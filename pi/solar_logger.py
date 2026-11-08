"""樹莓派常駐程式：同時讀逆變器 (CN5 RS485) 與 4 組 JK BMS (RS485C 旁聽)，
每分鐘把即時資料上傳到私人 Gist 給網站「🔋 儲能」分頁顯示，並在 SOC 與電壓有落差、電芯過低、壓差過大、
溫度過高時直接推播 LINE。

  python pi/solar_logger.py --setup   # 第一次：輸入 LINE / GitHub 金鑰，自動建立私人 Gist
  python pi/solar_logger.py           # 常駐執行 (install.sh 會設定成開機自動啟動)
  python pi/solar_logger.py --once    # 讀一次、印出結果後結束 (測試用)

安全：逆變器只用 Modbus 讀取指令 (功能碼 03/04)；BMS 那條只接收、完全不發送。
"""
import argparse
import getpass
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import threading
import time

import requests
import serial
from serial.tools import list_ports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]
import growatt_rs485_scan as inv      # noqa: E402  Modbus 讀取 (已在 10/31 實機驗證)
import jk_listen as jk                # noqa: E402  JK BMS 封包解讀 (已在 10/31 實機驗證)
import solar_core as C                # noqa: E402  SOC 與電壓落差判斷 (網站共用)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
STATE = os.path.join(HERE, "alert_state.json")
LOG_DIR = os.path.expanduser("~/solar_logs")
TW = dt.timezone(dt.timedelta(hours=8))
GIST_FILE = "solar_live.json"


# ---------------- 設定 ----------------
def load_config():
    if not os.path.exists(CONFIG):
        sys.exit("還沒設定，請先執行：python pi/solar_logger.py --setup")
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def setup():
    cfg = json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else {}
    print("直接按 Enter 保留目前的值")
    for key, label in (("line_token", "LINE Channel access token"),
                       ("github_token", "GitHub 金鑰 (只需要 Gists: Read and write 權限)")):
        v = getpass.getpass(f"{label}{' (已設定)' if cfg.get(key) else ''}（貼上後畫面不會顯示）：").strip()
        if v:
            cfg[key] = v
    if not cfg.get("gist_id"):
        r = requests.post("https://api.github.com/gists", timeout=20, headers=gh_headers(cfg),
                          json={"public": False, "description": "澔宸小窩即時資料 (樹莓派上傳)",
                                "files": {GIST_FILE: {"content": "{}"}}})
        r.raise_for_status()
        cfg["gist_id"] = r.json()["id"]
        print(f"✅ 已建立私人 Gist：{cfg['gist_id']}")
    cfg.setdefault("interval_s", 60)
    cfg.setdefault("inverter_port", "auto")
    cfg.setdefault("bms_port", "auto")
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(CONFIG, 0o600)
    print("\n✅ 設定完成。請到 Streamlit Cloud → App settings → Secrets 加上這兩行：")
    print(f'LIVE_GIST_ID = "{cfg["gist_id"]}"')
    print('GIST_TOKEN = "（另外建一把只有 Gists: Read-only 權限的金鑰貼在這裡）"')


def gh_headers(cfg):
    return {"Authorization": f"Bearer {cfg['github_token']}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"}


# ---------------- 序列埠：自動分辨哪個轉接器接逆變器、哪個接電池 ----------------
def usb_ports():
    return [p.device for p in list_ports.comports() if "USB" in p.device.upper() or "ACM" in p.device.upper()]


def open_inverter(port):
    """有回應 Modbus 的就是逆變器 (115200、站號 1)。"""
    try:
        s = serial.Serial(port, 115200, timeout=0.6)
    except serial.SerialException:
        return None
    if inv.read_regs(s, 1, inv.READ_INPUT, 0, 10) is not None:
        return s
    s.close()
    return None


def is_bms_bus(port, seconds=3):
    """只聽不說：電池並聯線上一直有主機在輪詢 (JK 封包 55 AA EB 90 或 Modbus 0x10 寫入)，逆變器那條沒輪詢時是安靜的。"""
    try:
        s = serial.Serial(port, 115200, timeout=0.3)
    except serial.SerialException:
        return False
    buf, t0 = bytearray(), time.time()
    while time.time() - t0 < seconds and len(buf) < 4000:
        buf += s.read(1024)                               # 只讀取
    s.close()
    return jk.HEAD in buf or b"\x10\x16\x20\x00\x01" in buf


def detect_ports(cfg, bms_known=None):
    """先用「旁聽」找出電池那條 (絕不對它送任何資料)，剩下的才用 Modbus 讀取指令確認是逆變器。
    bms_known：已經知道哪條是電池 (重新找逆變器時)，就不再碰它，避免跟旁聽搶資料、更不會對它送指令。"""
    ports = usb_ports()
    bp = bms_known or cfg.get("bms_port", "auto")
    if bp == "auto":
        bp = next((p for p in ports if is_bms_bus(p)), None)
    ip = cfg.get("inverter_port", "auto")
    candidates = [ip] if ip != "auto" else [p for p in ports if p != bp]
    inv_ser = None
    for p in candidates:
        if p != bp and (inv_ser := open_inverter(p)):
            break
    return inv_ser, bp


# ---------------- 逆變器 (input 暫存器對應：10/31 掃描實測) ----------------
def s32(v):
    return v - (1 << 32) if v >= 1 << 31 else v


def read_inverter(ser):
    r = {}
    for start in (0, 25, 50, 75, 1000, 1025, 1050):
        vals = inv.read_regs(ser, 1, inv.READ_INPUT, start, 25)
        if vals is None:
            return None
        r.update({start + i: v for i, v in enumerate(vals)})
        time.sleep(0.03)
    g = lambda a: r.get(a, 0)
    w = lambda a: s32((g(a) << 16) | g(a + 1)) / 10           # 32 位元功率 (W)，有正負
    e = lambda a: ((g(a) << 16) | g(a + 1)) / 10               # 32 位元電量 (kWh)
    return {
        "status": g(0), "pv_w": w(1),
        "pv1": {"v": g(3) / 10, "a": g(4) / 10, "w": w(5)}, "pv3": {"v": g(11) / 10, "a": g(12) / 10, "w": w(13)},
        "out_w": w(35), "fac": g(37) / 100, "vac1": g(38) / 10, "vac2": g(42) / 10,
        "load_l1_w": w(1031), "load_l2_w": w(1033), "load_w": w(1037),
        "buy_w": w(1021), "sell_w": w(1029), "dis_w": w(1009),
        "vbat": g(1013) / 10, "soc": g(1014), "inv_t": g(93) / 10, "ipm_t": g(94) / 10, "bat_t": g(1040) / 10,
        "eac_today": e(53), "epv_total": e(91), "buy_today": e(1044), "dis_today": e(1052),
    }


# ---------------- BMS 旁聽 (背景執行緒，只接收) ----------------
class BmsListener(threading.Thread):
    def __init__(self, port, avoid=lambda: None):
        super().__init__(daemon=True)
        self.port, self.packs, self.settings, self.last = port, {}, None, time.time()   # 開始旁聽的時間起算
        self.pack_settings = {}     # 每組電池最新一次的設定封包 (自動校正寫入後用來比對)
        self.avoid = avoid          # 回傳逆變器那個序列埠，重新找電池時跳過它

    def refind(self):
        """轉接器拔插或重新連線後名稱可能改變 (例如 ttyACM0 → ttyACM1)：只用旁聽重新找出電池那條。"""
        inv = self.avoid()
        for p in usb_ports():
            if p != inv and is_bms_bus(p):
                if p != self.port:
                    print(f"電池改到 {p}", flush=True)
                self.port = p
                return True
        return False

    def run(self):
        while True:
            try:
                ser = serial.Serial(self.port, 115200, timeout=0.3)
                buf, opened = bytearray(), time.time()
                while True:
                    buf += ser.read(4096)          # 只讀取，沒有任何 write
                    # 超過 60 秒沒收到任何電池封包：關掉重開序列埠 (轉接器偶爾會卡住)
                    if time.time() - max(self.last, opened) > 60:
                        print("60 秒沒收到電池資料，重新尋找電池序列埠", flush=True)
                        ser.close()
                        self.refind()
                        break
                    got, buf = jk.frames(buf)
                    for addr, f in got:
                        if f[4] == 0x02:
                            self.packs[addr] = {"addr": addr, **jk.decode_cells(f), "t": time.time()}
                            self.last = time.time()
                        elif f[4] == 0x01:
                            st = jk.decode_settings(f)
                            self.pack_settings[addr] = st
                            if addr == 0:
                                self.settings = st
            except (serial.SerialException, OSError) as e:
                print(f"BMS 序列埠錯誤：{e}，10 秒後重新尋找", flush=True)
                time.sleep(10)
                self.refind()


# ---------------- 警示 ----------------
COOLDOWN_H = {"fakefull": 20, "socv": 6, "socv_red": 2, "lowcell": 2, "delta": 12, "temp": 3, "socgap": 12, "alarm": 3,
              "bms_lost": 6, "inv_lost": 6, "pi_hot": 3}


_prev_soc = {}                                         # 每組上一次的 SOC，用來抓「剛到 100%」那一刻


def check_alerts(invd, packs, bms_age, inv_fail_s=0):
    out = []                                            # (key, 紅/橘, 訊息)
    if invd:
        sv = C.soc_voltage_check(invd["soc"], invd["vbat"], max(invd["dis_w"], 0) / 1000)
        if sv:
            out.append(("socv_red" if sv["level"] == "red" else "socv", sv["level"],
                        f"SOC 與電壓有落差\n{sv['msg']}。\n👉 {sv['action']}。"))
    if packs:
        cells = [c for p in packs for c in p["cells"]]
        avg = sum(cells) / len(cells)
        low = min(packs, key=lambda p: min(p["cells"]))
        if min(low["cells"]) < 3.00 and low["curr"] < 0:
            out.append(("lowcell", "red", f"電池 #{low['addr']} 有電芯只剩 {min(low['cells']):.3f} V（快到 2.90 V 校正點），"
                                          "請把逆變器放電下限 SOC 調高，讓台電接手"))
        limit = 0.080 if avg >= 3.38 else 0.030
        for p in packs:
            if p["delta"] > limit:
                out.append(("delta", "orange", f"電池 #{p['addr']} 壓差 {p['delta'] * 1000:.0f} mV（目前門檻 {limit * 1000:.0f} mV），"
                                               "請留意是否有電芯不平衡"))
                break
        hot = max(packs, key=lambda p: max(p["t1"], p["t2"], p["mos_t"]))
        t = max(hot["t1"], hot["t2"], hot["mos_t"])
        if t >= 45:
            out.append(("temp", "red" if t >= 52 else "orange", f"電池 #{hot['addr']} 溫度 {t:.1f}°C 偏高，請檢查通風"))
        socs = [p["soc"] for p in packs]
        if max(socs) - min(socs) >= 15:
            lo = min(packs, key=lambda p: p["soc"])
            out.append(("socgap", "orange", f"4 組電池電量不一致：#{lo['addr']} 顯示 {lo['soc']}%，其他組最高 {max(socs)}%"
                                            "（電壓相同，實際電量應該一樣）。下次充滿到 3.45 V 會自動校正，期間逆變器顯示的平均電量會偏低"))
        # SOC 剛變成 100% 的那一刻看最高單體：≥ 3.44 V 是電壓校正 (真的滿)；
        # 明顯低於 3.44 V 就是計數先到 340 Ah 頂 (假滿)，逆變器會提早停充、校正也沒發生
        for p in packs:
            prev = _prev_soc.get(p["addr"])
            _prev_soc[p["addr"]] = p["soc"]
            if prev is not None and prev < 100 <= p["soc"] and max(p["cells"]) < 3.43:
                out.append(("fakefull", "orange", f"電池 #{p['addr']} 顯示 100%，但最高單體只有 {max(p['cells']):.3f} V"
                                                  "（真的充滿要 3.45 V），是 BMS 計數先到頂、不是真的滿。"
                                                  "如果逆變器停止充電，樹莓派會在 9–14 點自動用藍牙校正"))
                break
        alarm = [p for p in packs if p.get("alarm")]
        if alarm:
            out.append(("alarm", "red", "BMS 告警：" + "、".join(f"#{p['addr']} 代碼 {p['alarm']:#06x}" for p in alarm)
                                        + "，請打開 JK App 查看"))
    if bms_age > 300:
        out.append(("bms_lost", "orange", f"已經 {bms_age / 60:.0f} 分鐘沒收到電池 BMS 資料，請檢查電池 RS485C 的網路線"))
    pt = pi_stats().get("cpu_temp")
    if pt and pt >= 80:
        out.append(("pi_hot", "orange", f"樹莓派 CPU 溫度 {pt:.0f}°C 偏高 (85°C 會降速)，請檢查散熱風扇與擺放位置的通風"))
    if invd is None and inv_fail_s >= 120:     # 偶爾一兩次讀不到不算，連續 2 分鐘以上才通知
        out.append(("inv_lost", "orange", f"已經 {inv_fail_s / 60:.0f} 分鐘讀不到逆變器 (CN5) 的資料，請檢查網路線或轉接器"))
    return out


def send_line(cfg, text):
    if not cfg.get("line_token"):
        return False
    try:
        r = requests.post("https://api.line.me/v2/bot/message/broadcast", timeout=15,
                          headers={"Authorization": f"Bearer {cfg['line_token']}"},
                          json={"messages": [{"type": "text", "text": text}]})
        return r.status_code == 200
    except requests.RequestException:
        return False


def notify(cfg, alerts):
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    now = time.time()
    for key, level, msg in alerts:
        if now - state.get(key, 0) < COOLDOWN_H[key] * 3600:
            continue
        text = f"{'🔴' if level == 'red' else '🟠'} {msg}\n（樹莓派即時偵測 {dt.datetime.now(TW):%H:%M}）"
        if send_line(cfg, text):
            state[key] = now
            print("已推播：" + msg.splitlines()[0], flush=True)
    # 連線類的警示恢復時也通知一次
    now_keys = {k for k, _, _ in alerts}
    for key, msg in (("inv_lost", "✅ 逆變器 (CN5) 已經恢復連線，資料正常讀取中"),
                     ("bms_lost", "✅ 電池 BMS 已經恢復連線，資料正常讀取中")):
        if state.get(f"{key}_active") and key not in now_keys:
            if send_line(cfg, f"{msg}\n（樹莓派即時偵測 {dt.datetime.now(TW):%H:%M}）"):
                state[f"{key}_active"] = False
                state.pop(key, None)       # 之後再斷線可以馬上通知
                print("已推播：" + msg, flush=True)
        elif key in now_keys and state.get(key, 0) > now - 60:
            state[f"{key}_active"] = True
    with open(STATE, "w") as f:
        json.dump(state, f)


# ---------------- 上傳與記錄 ----------------
# ---------------- 樹莓派本身的狀態 (只讀系統檔案，不需要額外套件) ----------------
_cpu_prev = None


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def pi_stats():
    global _cpu_prev
    out = {}
    t = _read("/sys/class/thermal/thermal_zone0/temp")
    if t:
        out["cpu_temp"] = round(int(t) / 1000, 1)
    # CPU 使用率：兩次讀 /proc/stat 的差
    f = (_read("/proc/stat", "") or "").splitlines()[0].split()[1:]
    if f:
        vals = list(map(int, f))
        idle, total = vals[3] + vals[4], sum(vals)
        if _cpu_prev:
            dt_, di = total - _cpu_prev[1], idle - _cpu_prev[0]
            out["cpu_pct"] = round(100 * (1 - di / dt_), 1) if dt_ else 0.0
        _cpu_prev = (idle, total)
    mem = {}
    for line in (_read("/proc/meminfo", "") or "").splitlines():
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0])
    if mem.get("MemTotal"):
        out["ram_total_mb"] = round(mem["MemTotal"] / 1024)
        out["ram_used_mb"] = round((mem["MemTotal"] - mem.get("MemAvailable", 0)) / 1024)
    try:
        import shutil
        du = shutil.disk_usage("/")
        out["disk_used_gb"], out["disk_total_gb"] = round(du.used / 1e9, 1), round(du.total / 1e9, 1)
    except OSError:
        pass
    up = _read("/proc/uptime")
    if up:
        out["uptime_h"] = round(float(up.split()[0]) / 3600, 1)
    out["load1"] = round(os.getloadavg()[0], 2)
    import glob
    for fan in glob.glob("/sys/devices/platform/cooling_fan/hwmon/*/fan1_input"):
        out["fan_rpm"] = int(_read(fan, "0") or 0)
    thr = _read("/sys/devices/platform/soc/soc:firmware/get_throttled") or ""
    try:
        import subprocess
        thr = thr or subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        out["throttled"] = int(thr.strip().split("=")[-1], 16) if thr.strip() else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return out


def snapshot(invd, listener):
    packs = [{k: v for k, v in p.items() if k != "t"} for p in sorted(listener.packs.values(), key=lambda p: p["addr"])
             if time.time() - p["t"] < 600]
    return {"time": dt.datetime.now(TW).isoformat(timespec="seconds"), "source": "樹莓派即時讀取",
            "inverter": invd, "packs": packs, "settings": listener.settings or {}, "pi": pi_stats()}


_upload_wait_until = 0.0
_rate_left = "?"          # GitHub 這一小時還剩多少次 (每次上傳的回應都會附上)


def upload(cfg, snap):
    """上傳到私人 Gist；失敗時印出原因。GitHub 限流 (403/429) 時暫停一段時間再試，避免越傳越被擋。"""
    global _upload_wait_until
    if time.time() < _upload_wait_until:
        return False
    try:
        r = requests.patch(f"https://api.github.com/gists/{cfg['gist_id']}", timeout=20, headers=gh_headers(cfg),
                           json={"files": {GIST_FILE: {"content": json.dumps(snap, ensure_ascii=False)}}})
    except requests.RequestException as e:
        print(f"上傳失敗：網路錯誤 {e}", flush=True)
        return False
    global _rate_left
    _rate_left = r.headers.get("X-RateLimit-Remaining", _rate_left)
    if r.status_code == 200:
        return True
    msg = (r.text or "")[:160].replace("\n", " ")
    print(f"上傳失敗：HTTP {r.status_code} 類別={r.headers.get('X-RateLimit-Resource')} "
          f"已用={r.headers.get('X-RateLimit-Used')} {msg}", flush=True)
    if r.status_code in (403, 429):
        retry = int(r.headers.get("Retry-After") or 0)
        reset = int(r.headers.get("X-RateLimit-Reset") or 0)
        _upload_wait_until = time.time() + max(retry, reset - time.time() if reset else 0, 120)
        print(f"GitHub 限流，暫停上傳 {(_upload_wait_until - time.time()) / 60:.0f} 分鐘", flush=True)
    return False


def log_csv(snap):
    os.makedirs(LOG_DIR, exist_ok=True)
    fn = os.path.join(LOG_DIR, f"{snap['time'][:10]}.csv")
    new = not os.path.exists(fn)
    i = snap["inverter"] or {}
    with open(fn, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["時間", "PV西W", "PV西V", "PV西A", "PV東W", "PV東V", "PV東A", "輸出W", "負載L1W", "負載L2W",
                        "買電W", "放電W", "逆變器SOC", "電池V", "逆變器°C"]
                       + [f"#{n}{k}" for n in range(4) for k in ("SOC", "電流A", "最低V", "最高V", "溫度")])
        row = [snap["time"], i.get("pv1", {}).get("w"), i.get("pv1", {}).get("v"), i.get("pv1", {}).get("a"),
               i.get("pv3", {}).get("w"), i.get("pv3", {}).get("v"), i.get("pv3", {}).get("a"), i.get("out_w"),
               i.get("load_l1_w"), i.get("load_l2_w"), i.get("buy_w"), i.get("dis_w"), i.get("soc"), i.get("vbat"),
               i.get("inv_t")]
        by = {p["addr"]: p for p in snap["packs"]}
        for n in range(4):
            p = by.get(n)
            row += ([p["soc"], p["curr"], min(p["cells"]), max(p["cells"]), max(p["t1"], p["t2"])] if p else [""] * 5)
        w.writerow(row)


# ---------------- 假滿自動校正 (藍牙重新寫入容量) ----------------
CALIB_STATE = os.path.join(HERE, "calib_state.json")
JK_BLE = os.path.join(ROOT, "tools", "jk_ble.py")
CALIB_TEST = os.path.join(HERE, "calib_test")         # touch 這個檔案 → 下一輪立刻照真實流程跑一次 (不看條件、不算今天的次數)


class AutoCalib:
    """BMS 計數先到 340 Ah 頂、電壓還沒到 3.45 V 就顯示滿 → 逆變器提早停充。
    條件連續 10 分鐘成立就用藍牙對一組從機重新寫入容量 340 Ah (跟手機 JK App 手動做的一樣)，
    讓 SOC 重算、逆變器繼續充到 3.45 V，4 組一起真正校正。
    安全：一天最多成功 1 次、嘗試 3 次；寫入工具會比對全部 34 項設定，有其他設定被改到或寫了沒效果就自動停用並通知。"""
    AVG_CELL = 3.36      # 真的充滿後休息平均仍有 3.38 V 以上；假滿 (11/6、11/8) 平均只有 3.34 V
    MAX_CELL = 3.40      # 任何一顆到 3.40 V 以上就可能真的接近滿，不動作
    HOLD_S = 600
    VERIFY_S = 300

    def __init__(self, cfg, listener=None):
        self.cfg, self.listener = cfg, listener
        self.pack = cfg.get("calib_pack", "003-03")       # 用從機 #3，不碰負責 CAN 通訊的主機 #0
        self.cap = int(cfg.get("calib_ah", 340))
        self.since, self.thread, self.pending = None, None, None
        self.addr = int(self.pack[-1])
        self.before = None                                # 寫入前 (RS485 看到的) #3 SOC 與設定
        self.st = _read(CALIB_STATE, {}) or {}

    def save(self):
        with open(CALIB_STATE, "w") as f:
            json.dump(self.st, f)

    def line(self, text):
        print(text.splitlines()[0], flush=True)
        send_line(self.cfg, text + f"\n（樹莓派自動校正 {dt.datetime.now(TW):%H:%M}）")

    def tick(self, invd, packs):
        if not self.cfg.get("auto_calib", True) or self.st.get("disabled"):
            return
        if self.thread and self.thread.is_alive():
            return
        if self.pending and time.time() - self.pending["t"] >= self.VERIFY_S:
            self.verify(packs)
        now = dt.datetime.now(TW)
        today = str(now.date())
        if os.path.exists(CALIB_TEST):
            os.remove(CALIB_TEST)
            cells = [c for p in packs for c in p["cells"]]
            why = (f"測試：SOC {invd['soc'] if invd else '?'}%、最高單體 {max(cells):.3f} V" if cells else "測試")
            self.remember(packs)
            self.thread = threading.Thread(target=self.write, args=(why, None), daemon=True)
            self.thread.start()
            return
        if self.st.get("done") == today or (self.st.get("tries_date") == today and self.st.get("tries", 0) >= 3):
            self.since = None
            return
        cells = [c for p in packs for c in p["cells"]]
        if invd is None or len(packs) < 4 or not cells or invd["soc"] < 99 or not 9 <= now.hour < 14:
            self.since = None
            return
        # 有沒有太陽看組串電壓：電池滿了逆變器會限發，太陽能功率只剩家裡用的量，不能用功率判斷
        sun = max(invd["pv1"]["v"], invd["pv3"]["v"]) >= 300
        avg, mx, chg = sum(cells) / len(cells), max(cells), sum(p["curr"] for p in packs)
        checks = {"平均單體<3.36": avg < self.AVG_CELL, "最高單體<3.40": mx < self.MAX_CELL,
                  "沒在充電": chg < 3, "有太陽": sun}
        cond = all(checks.values())
        if now.minute % 5 == 0 or cond != bool(self.since):
            print(f"假滿檢查：SOC {invd['soc']}% 平均 {avg:.3f} V 最高 {mx:.3f} V 電流 {chg:+.1f} A "
                  f"組串 {invd['pv1']['v']:.0f}/{invd['pv3']['v']:.0f} V → "
                  + "、".join(f"{k}{'✓' if v else '✗'}" for k, v in checks.items())
                  + (f"（已持續 {(time.time() - self.since) / 60:.0f} 分）" if self.since else ""), flush=True)
        if not cond:
            self.since = None
            return
        self.since = self.since or time.time()
        if time.time() - self.since < self.HOLD_S:
            return
        self.since = None
        if self.st.get("tries_date") != today:
            self.st.update(tries_date=today, tries=0)
        self.st["tries"] += 1
        self.save()
        why = f"SOC {invd['soc']}%、單體平均只有 {avg:.3f} V（最高 {mx:.3f} V）、逆變器已停充 10 分鐘"
        self.remember(packs)
        self.thread = threading.Thread(target=self.write, args=(why, today), daemon=True)
        self.thread.start()

    def remember(self, packs):
        me = next((p for p in packs if p["addr"] == self.addr), None)
        sets = (self.listener.pack_settings.get(self.addr) if self.listener else None)
        self.before = {"soc": me["soc"] if me else None, "settings": dict(sets) if sets else None}

    def write(self, why, today):
        try:
            r = subprocess.run([sys.executable, JK_BLE, "set-capacity", self.pack, str(self.cap), "--yes"],
                               capture_output=True, text=True, timeout=300)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") if isinstance(e.stdout, str) else ""
            out += "\n藍牙逾時"
        if "⚠️⚠️" in out:
            self.st["disabled"] = True
            self.save()
            self.line("🔴 自動校正寫入後，BMS 有其他設定被改到，已停用自動校正！請打開 JK App 檢查 "
                      f"{self.pack}，並告訴 Claude。\n" + out[-600:])
        elif "已送出" in out:                           # 寫入有送出 (藍牙讀回不一定成功，改用 RS485 確認)
            if today:                                  # 測試不算今天的次數
                self.st["done"] = today
                self.save()
            self.pending = {"t": time.time(), "before": self.before}
            self.line(f"🔧 偵測到假滿（{why}），已自動用藍牙對電池 {self.pack} 重新寫入容量 {self.cap} Ah。\n"
                      f"5 分鐘後用 RS485 確認 SOC 是否重算、設定是否正常，並回報充電狀況。")
        else:
            print("自動校正這次沒成功：" + out.strip()[-300:], flush=True)
            if today is None:
                self.line("🟠 測試：藍牙寫入沒有成功（可能手機 JK App 正連著電池）。\n最後訊息：" + out.strip()[-200:])
            elif self.st.get("tries", 0) >= 3:
                self.line(f"🟠 偵測到假滿（{why}），但今天 3 次藍牙寫入都沒成功（可能手機 JK App 正連著電池）。"
                          "可以用 JK App 手動對任一組重新寫入容量 340 Ah。\n最後訊息：" + out.strip()[-200:])

    def verify(self, packs):
        before = self.pending.get("before") or {}
        self.pending = None
        me = next((p for p in packs if p["addr"] == self.addr), None)
        # 設定比對 (RS485 旁聽)：除了容量，其他 33 項都不該變
        now_set = self.listener.pack_settings.get(self.addr) if self.listener else None
        old_set = before.get("settings")
        if old_set and now_set:
            changed = [f"{k} {old_set[k]}→{now_set[k]}" for k in old_set
                       if k != "標稱容量" and now_set.get(k) != old_set[k]]
            if changed:
                self.st["disabled"] = True
                self.save()
                self.line(f"🔴 電池 {self.pack} 寫入後有其他設定改變：{'、'.join(changed)}。"
                          "已停用自動校正，請打開 JK App 改回原值並告訴 Claude。")
                return
        set_note = "其他設定都沒變" if (old_set and now_set) else "設定比對資料不足"
        chg = sum(p["curr"] for p in packs)
        charging = f"電池合計 {chg:+.0f} A，" + ("逆變器已恢復充電，充到最高單體 3.45 V 時 4 組會自動校正到 100%。"
                                                if chg >= 5 else "目前沒在充電（太陽能有多的電時就會繼續充）。")
        b, n = before.get("soc"), (me["soc"] if me else None)
        if b is not None and n is not None and n <= b - 3:
            self.line(f"✅ 自動校正有效：電池 {self.pack} SOC {b}% → {n}%（依電壓重新計算），{set_note}。\n{charging}")
        elif b is not None and n is not None:
            self.st["disabled"] = True
            self.save()
            self.line(f"🟠 重新寫入容量後，電池 {self.pack} 的 SOC 還是 {n}%（寫入前 {b}%），沒有重新計算，"
                      "已停用自動校正。請告訴 Claude 換別的做法。")
        else:
            self.line(f"ℹ️ 已寫入，但 RS485 沒收到 {self.pack} 的資料，無法確認 SOC 是否重算。{charging}")

# ---------------- 主程式 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.setup:
        return setup()
    # 測試讀一次 (--once) 不需要金鑰，方便先檢查接線
    cfg = load_config() if not (a.once and not os.path.exists(CONFIG)) else {}
    inv_ser, bms_port = detect_ports(cfg)
    print(f"逆變器：{inv_ser.port if inv_ser else '找不到'}　電池：{bms_port or '找不到'}", flush=True)
    listener = BmsListener(bms_port, avoid=lambda: inv_ser.port if inv_ser else None) if bms_port else None
    if listener:
        listener.start()
    if a.once:
        time.sleep(15)                                  # 等 BMS 輪完一圈
    inv_fail_since = None
    calib = AutoCalib(cfg, listener) if not a.once else None
    while True:
        t0 = time.time()
        invd = None
        if inv_ser is None:
            inv_ser, _ = detect_ports(cfg, bms_known=bms_port)
        for _try in range(3):                       # 讀不到先重試，再不行就關掉重新開序列埠
            if not inv_ser:
                break
            try:
                invd = read_inverter(inv_ser)
            except (serial.SerialException, OSError):
                invd = None
            if invd:
                break
            time.sleep(1)
            if _try == 1:
                try:
                    inv_ser.close()
                except Exception:
                    pass
                inv_ser = open_inverter(inv_ser.port)
        if invd:
            inv_fail_since = None
        else:
            inv_fail_since = inv_fail_since or time.time()
            print("讀不到逆變器，重試中", flush=True)
        snap = snapshot(invd, listener) if listener else {"time": dt.datetime.now(TW).isoformat(timespec="seconds"),
                                                          "inverter": invd, "packs": [], "settings": {}}
        alerts = check_alerts(invd, snap["packs"], time.time() - listener.last if listener else 0,
                              time.time() - inv_fail_since if inv_fail_since else 0)
        if a.once:
            print(json.dumps(snap, ensure_ascii=False, indent=1)[:3000])
            print("警示：", alerts or "無")
            return
        ok = upload(cfg, snap)
        log_csv(snap)
        notify(cfg, alerts)
        try:
            calib.tick(invd, snap["packs"])
        except Exception as e:                       # 自動校正出錯不能影響記錄與上傳
            print(f"自動校正錯誤：{e}", flush=True)
        print(f"{snap['time'][11:19]} 上傳{'✅' if ok else '❌'}  PV {invd['pv_w'] if invd else '-'} W  "
              f"SOC {invd['soc'] if invd else '-'}%  電池 {len(snap['packs'])} 組  警示 {len(alerts)}  GitHub 剩 {_rate_left}", flush=True)
        # 每 60 秒讀一次並上傳：GitHub 對頻繁修改同一個 Gist 有限制 (15 秒跑了約 3 小時就被擋，60 秒時一直正常)
        time.sleep(max(5, 60 - (time.time() - t0)))


if __name__ == "__main__":
    main()
