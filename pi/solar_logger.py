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


# ---------------- 診斷紀錄：把 print 的重要訊息留最近 40 行，跟著上傳 (網站「樹莓派」分頁、家人連結才看得到) ----------------
LOG_TAIL = []
AUTO_UPDATE_LOG = os.path.join(HERE, "auto_update.log")


class _Tee:
    def __init__(self, out):
        self.out, self.buf = out, ""

    def write(self, s):
        self.out.write(s)
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            # 每分鐘一行的例行「上傳✅」不記，只留其他訊息 (錯誤、自動控制、登入…)
            if line.strip() and not (len(line) > 9 and line[2] == ":" and " 上傳✅" in line[:16]):
                LOG_TAIL.append(f"{dt.datetime.now(TW):%m/%d %H:%M} {line.strip()[:200]}")
                del LOG_TAIL[:-40]
        return len(s)

    def flush(self):
        self.out.flush()


def _version():
    try:
        return subprocess.run(["git", "-C", ROOT, "log", "-1", "--format=%h %cd %s", "--date=format:%m/%d %H:%M"],
                              capture_output=True, text=True, timeout=5).stdout.strip()[:80]
    except (OSError, subprocess.SubprocessError):
        return None


VERSION = None


def snapshot(invd, listener):
    packs = [{k: v for k, v in p.items() if k != "t"} for p in sorted(listener.packs.values(), key=lambda p: p["addr"])
             if time.time() - p["t"] < 600]
    return {"time": dt.datetime.now(TW).isoformat(timespec="seconds"), "source": "樹莓派即時讀取",
            "inverter": invd, "packs": packs, "settings": listener.settings or {}, "pi": pi_diag(pi_stats())}


def pi_diag(stats):
    """樹莓派狀態再加上程式版本、自動更新紀錄、最近的重要訊息。"""
    au = (_read(AUTO_UPDATE_LOG, "") or "").strip().splitlines()
    return stats | {"commit": VERSION, "auto_update": au[-1][:120] if au else None, "log": list(LOG_TAIL)}


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
        try:                                              # 紀錄檔是 JSON (_read 只讀成文字，不能用)
            with open(CALIB_STATE) as f:
                self.st = json.load(f)
        except (OSError, ValueError):
            self.st = {}

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
        thermo = getattr(self, "thermo", None)
        if thermo:
            thermo.paused.set()
            thermo.idle.wait(15)
            time.sleep(5)                          # 讓藍牙晶片完全空出來再連 JK BMS
        try:
            r = subprocess.run([sys.executable, JK_BLE, "set-capacity", self.pack, str(self.cap), "--yes"],
                               capture_output=True, text=True, timeout=300)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") if isinstance(e.stdout, str) else ""
            out += "\n藍牙逾時"
        finally:
            if thermo:
                thermo.paused.clear()              # 恢復溫度計旁聽
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
        elif b is not None and n is not None and b < 95:
            # 寫入前就不是假滿 (例如之前已經校正過)：SOC 本來就依電壓算好了，不會再掉，不能當成「方法無效」
            self.line(f"ℹ️ 已重新寫入容量，電池 {self.pack} SOC {b}% → {n}%（寫入前就不是假滿，不需要再校正）。{charging}")
        elif b is not None and n is not None:
            self.st["disabled"] = True
            self.save()
            self.line(f"🟠 重新寫入容量後，電池 {self.pack} 的 SOC 還是 {n}%（寫入前 {b}%），沒有重新計算，"
                      "已停用自動校正。請告訴 Claude 換別的做法。")
        else:
            self.line(f"ℹ️ 已寫入，但 RS485 沒收到 {self.pack} 的資料，無法確認 SOC 是否重算。{charging}")

# ---------------- 米家溫濕度計 (藍牙旁聽) 與 機房冷氣自動控制 ----------------
class MiThermo(threading.Thread):
    """背景一直聽米家溫濕度計的藍牙廣播 (約每分鐘一筆，加密)，保留每顆最新的溫度 / 濕度 / 電量。只接收，不連線。"""

    def __init__(self, keys):
        super().__init__(daemon=True)
        self.keys = {k.upper(): v for k, v in keys.items()}
        self.data = {}
        self.fallback = None        # 藍牙收不到時改用小米雲端 (HomeWatch.sensor)，參數是 MAC
        self.paused = threading.Event()      # 藍牙要連 JK BMS (自動校正) 時暫停掃描：一邊掃描一邊連線，樹莓派內建藍牙容易漏收封包
        self.idle = threading.Event()        # 掃描已經真的停下來
        self.idle.set()

    def latest(self, mac, max_age=900):
        d = self.data.get((mac or "").upper())
        if d and time.time() - d.get("t", 0) < max_age:
            return d
        return self.fallback(mac) if self.fallback else None

    def run(self):
        import asyncio
        import mi_ble
        from bleak import BleakScanner

        def cb(dev, adv):
            mac = dev.address.upper()
            raw = adv.service_data.get(mi_ble.MI_UUID)
            if not raw or mac not in self.keys:
                return
            try:
                vals = mi_ble.decode_objects(mi_ble.decrypt(raw, mac, self.keys[mac]))
            except Exception:                      # noqa: BLE001  沒有內容的訊號、或封包不完整
                return
            if vals:
                d = self.data.setdefault(mac, {})
                d.update(vals)
                # 這款 (MJWSD05MMC) 溫度沒變就不重送，只送濕度 / 電量；有收到任何一筆就代表溫度計還在、上次的溫度仍有效
                if "temp" in d:
                    d["t"] = time.time()

        async def loop():
            while True:
                if self.paused.is_set():
                    self.idle.set()
                    await asyncio.sleep(1)
                    continue
                try:
                    self.idle.clear()
                    async with BleakScanner(cb):
                        # 每 30 分鐘重開一次掃描；超過 10 分鐘一筆都沒收到 (藍牙卡住) 就提早重開；要暫停時馬上停
                        t0 = time.time()
                        while time.time() - t0 < 1800 and not self.paused.is_set():
                            await asyncio.sleep(1)
                            if int(time.time() - t0) % 60:
                                continue
                            last = max([d.get("t", 0) for d in self.data.values()] or [0])
                            if time.time() - max(last, t0) > 600:
                                print("溫度計 10 分鐘沒有訊號，重新開始藍牙掃描", flush=True)
                                break
                except Exception as e:             # noqa: BLE001  藍牙偶爾被佔用：等一下重來
                    print(f"溫度計旁聽錯誤：{e}", flush=True)
                    await asyncio.sleep(30)
                finally:
                    if self.paused.is_set():
                        self.idle.set()

        asyncio.run(loop())


AC_STATE = os.path.join(HERE, "ac_state.json")


class AcAuto:
    """儲能室太熱就開冷氣、降溫後關掉。
    開：室溫 ≥ ac_on_room (預設 30°C)，或電芯 ≥ ac_on_cell (預設 35°C)。不是冷氣模式才切 Cool；預設不改設定溫度。
    關：不管是自動開還是手動 (米家 / 遙控器) 開的，室溫 ≤ ac_off_room (預設 26.5°C) 持續 10 分鐘、電芯 < 32°C 就關；
        自動開的另外要開滿 20 分鐘。config.json 設 "ac_off_manual": false 可改回「只關自動開的」。
    開了 20 分鐘室溫沒有下降 → 再送一次開 + LINE 提醒 (紅外線是單向的，可能沒被冷氣收到)。"""

    def __init__(self, cfg, thermo):
        self.cfg, self.thermo = cfg, thermo
        self.on_room = float(cfg.get("ac_on_room", 30))
        self.off_room = float(cfg.get("ac_off_room", 26.5))
        self.on_cell = float(cfg.get("ac_on_cell", 35))
        # 自動開機時的設定溫度：預設不改 (維持冷氣伴侶記得的設定，例如米家 App 設的 23°C)；config 設 "ac_target": 25 才會改
        self.target = float(cfg["ac_target"]) if cfg.get("ac_target") is not None else None
        self.enabled = bool(cfg.get("ac_auto", True) and cfg.get("ac", {}).get("token"))
        self.off_manual = bool(cfg.get("ac_off_manual", True))
        self.ac, self.status, self.status_t, self.cool_since = None, {}, 0, None
        try:
            with open(AC_STATE) as f:
                self.st = json.load(f)
        except (OSError, ValueError):
            self.st = {}

    def save(self):
        with open(AC_STATE, "w") as f:
            json.dump(self.st, f)

    def dev(self):
        if self.ac is None:
            import mi_ac
            self.ac = mi_ac.Ac(self.cfg)
        return self.ac

    def read_status(self, force=False):
        if force or time.time() - self.status_t > 300:
            try:
                self.status = self.dev().get()
                self.status_t = time.time()
            except Exception as e:                 # noqa: BLE001
                print(f"讀不到冷氣伴侶：{e}", flush=True)
        return self.status

    def mode_name(self, v):
        try:
            for m in (self.dev().p["mode"] or {}).get("values") or []:
                if m["value"] == v:
                    return m["description"]
        except Exception:                          # noqa: BLE001
            pass
        return None

    def line(self, text):
        print(text.splitlines()[0], flush=True)
        send_line(self.cfg, text + f"\n（樹莓派冷氣自動控制 {dt.datetime.now(TW):%H:%M}）")

    def climate(self, packs):
        """給網站用的摘要 (一併上傳)。"""
        ind = self.thermo.latest(self.cfg.get("mi_indoor")) if self.thermo else None
        out = self.thermo.latest(self.cfg.get("mi_outdoor")) if self.thermo else None
        cells = [max(p["t1"], p["t2"]) for p in packs] if packs else []
        pick = lambda d: {k: d.get(k) for k in ("temp", "hum", "batt", "src")} | {"age_s": round(time.time() - d["t"])} if d else None
        return {"indoor": pick(ind), "outdoor": pick(out), "cell_max": max(cells) if cells else None,
                "ac": {"on": self.status.get("on"), "mode": self.status.get("mode"), "temp": self.status.get("temp"),
                       "mode_name": self.mode_name(self.status.get("mode")), "fan": self.status.get("fan"),
                       "since": self.st.get("changed"), "on_room": self.on_room, "off_room": self.off_room,
                       "target": self.target, "on_cell": self.on_cell,
                       "auto_on": bool(self.st.get("auto_on")), "enabled": self.enabled, "off_manual": self.off_manual} if self.status else None}

    def tick(self, packs):
        if not self.enabled:
            return
        ind = self.thermo.latest(self.cfg.get("mi_indoor")) if self.thermo else None
        room = ind.get("temp") if ind else None
        cell = max(max(p["t1"], p["t2"]) for p in packs) if packs else None
        if room is None and cell is None:
            return
        st = self.read_status()
        if not st:
            return
        is_on = bool(st.get("on"))
        hot = (room is not None and room >= self.on_room) or (cell is not None and cell >= self.on_cell)
        now = time.time()
        why = f"儲能室 {room if room is not None else '?'}°C、電芯最高 {cell if cell is not None else '?'}°C"
        if hot and not is_on and now - self.st.get("changed", 0) >= 600:
            try:
                ac = self.dev()
                mv = ac.mode_value("cool")
                kw = {"mode": mv} if mv is not None and st.get("mode") != mv else {}   # 不是冷氣模式才切 Cool
                if self.target is not None:
                    kw["temp"] = self.target
                if kw:
                    ac.set(**kw)
                ac.set(on=True)
                self.st.update(auto_on=True, changed=now, on_room=room)
                self.save()
                self.read_status(force=True)
                t_set = self.target if self.target is not None else st.get("temp")
                self.line(f"❄️ {why}，已自動開冷氣（Cool" + (f" {t_set:g}°C" if isinstance(t_set, (int, float)) else "") + "）。")
            except Exception as e:                 # noqa: BLE001
                self.line(f"🟠 {why}，想自動開冷氣但冷氣伴侶沒有回應：{e}")
                self.st["changed"] = now
                self.save()
            return
        if is_on and (self.st.get("auto_on") or self.off_manual):
            # 自動開的要開滿 20 分鐘才關；手動開的只看室溫有沒有降到門檻以下並維持 10 分鐘
            on_for = now - self.st.get("changed", now) if self.st.get("auto_on") else 1e9
            # 開了 20 分鐘室溫沒降：可能紅外線沒被收到，再送一次並提醒一次
            if (self.st.get("auto_on") and room is not None and self.st.get("on_room") is not None and on_for >= 1200
                    and not self.st.get("rechecked")
                    and room >= self.st["on_room"] - 0.3):
                try:
                    self.dev().set(on=True)
                except Exception:                  # noqa: BLE001
                    pass
                self.st["rechecked"] = True
                self.save()
                self.line(f"🟠 冷氣自動開了 20 分鐘，儲能室還是 {room}°C，已再送一次開機指令，請確認冷氣有沒有在運轉。")
            cool = room is not None and room <= self.off_room and (cell is None or cell < self.on_cell - 3)
            self.cool_since = (self.cool_since or now) if cool else None
            if cool and now - self.cool_since >= 600 and on_for >= 1200:
                try:
                    was_auto = bool(self.st.get("auto_on"))
                    self.dev().set(on=False)
                    self.st = {"auto_on": False, "changed": now}
                    self.save()
                    self.read_status(force=True)
                    self.cool_since = None
                    self.line(f"✅ {why}，已降溫，自動關冷氣{'' if was_auto else '（這次是手動開的）'}。")
                except Exception as e:             # noqa: BLE001
                    print(f"自動關冷氣失敗：{e}", flush=True)
        elif not is_on and self.st.get("auto_on"):
            self.st = {"auto_on": False, "changed": now}     # 有人自己關掉了：不再當成自動開的
            self.save()


HEATER_STATE = os.path.join(HERE, "heater_state.json")


class HeaterAuto:
    """熱水器 (eWeLink 開關) 自動控制：用掉電池滿了之後會被限發浪費的太陽能。
    開：9:00–15:00、逆變器 SOC ≥ 98%、有太陽 (組串電壓 ≥ 300 V)、熱水器關著、距離上次自動關機 ≥ 30 分鐘。
    關 (只關自動開的)：電池放電 > 1.5 kW 持續 5 分鐘，或 SOC < 90%，或過了保底結束時間。
    「真的在加熱」= 開關開著而且家裡用電 ≥ 3.2 kW (4 kW 熱水器 + 家用)；水熱了溫控會自己斷電，用電就掉下來 (保溫中)。
    這台熱水器約 1 小時就能從冷水加熱完畢。
    保底：15:00 時今天「真的加熱」不到 50 分鐘 (不論誰開的)，強制開 1 小時到 16:00，確保晚上有熱水。"""
    ON_SOC, OFF_SOC, OFF_DIS_KW = 98, 90, 1.5
    MIN_MINUTES, HEAT_LOAD_KW, FALLBACK_END = 50, 3.2, 16.0

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.get("heater_auto", True)) and os.path.exists(os.path.join(ROOT, "tools", "heater_config.json"))
        self.on, self.status_t, self.dis_since, self.last_tick, self.heating = None, 0, None, time.time(), False
        try:
            with open(HEATER_STATE) as f:
                self.st = json.load(f)
        except (OSError, ValueError):
            self.st = {}

    def save(self):
        with open(HEATER_STATE, "w") as f:
            json.dump(self.st, f)

    def line(self, text):
        print(text.splitlines()[0], flush=True)
        send_line(self.cfg, text + f"\n（樹莓派熱水器自動控制 {dt.datetime.now(TW):%H:%M}）")

    def _h(self):
        import heater_control
        return heater_control

    def read(self, force=False):
        if force or time.time() - self.status_t > 300:
            try:
                h = self._h()
                self.on = h.current_cloud(h.load()).startswith("開")
                self.status_t = time.time()
            except (Exception, SystemExit) as e:  # noqa: BLE001  雲端偶爾失敗 / 登入過期 (sys.exit)
                print(f"讀不到熱水器狀態：{e}", flush=True)
        return self.on

    def switch(self, on):
        try:
            self._h().set_switch(on)
            self.on, self.status_t = on, time.time()
            return True
        except (Exception, SystemExit) as e:      # noqa: BLE001
            self.line(f"🟠 想{'開' if on else '關'}熱水器但 eWeLink 沒有回應：{e}")
            return False

    def _away_dates(self):
        try:
            with open(os.path.join(HERE, "heater_away.json"), encoding="utf-8") as f:
                return set(json.load(f).get("dates") or [])
        except (OSError, ValueError):
            return set()

    def summary(self):
        return {"on": self.on, "heating": self.heating, "auto_on": bool(self.st.get("auto_on")), "mode": self.st.get("mode"),
                "minutes_today": round(self.st.get("minutes", 0)), "enabled": self.enabled, "away": getattr(self, "away", False)}

    def tick(self, invd):
        if not self.enabled:
            return
        now_dt = dt.datetime.now(TW)
        today, now = str(now_dt.date()), time.time()
        if self.st.get("date") != today:
            self.st = {"date": today, "minutes": 0, "auto_on": False}
            self.save()
        on = self.read()
        if on is None:
            return
        self.away = today in self._away_dates()
        if self.away:                                 # 外出日 (pi/heater_away.json)：不自動加熱；自動開著的關掉
            if on and self.st.get("auto_on") and self.switch(False):
                self.st.update(auto_on=False, mode=None, changed=now)
                self.line("🧳 今天外出，已關掉自動開的熱水器，今天不再自動加熱。")
            self.save()
            return
        load_kw = (invd.get("load_w") or 0) / 1000 if invd else 0
        self.heating = bool(on and load_kw >= self.HEAT_LOAD_KW)
        if self.heating:                              # 今天真的加熱了多久 (不論誰開的；保溫斷電時不算)
            self.st["minutes"] = self.st.get("minutes", 0) + min(now - self.last_tick, 120) / 60
        self.last_tick = now
        hm = now_dt.hour + now_dt.minute / 60
        soc = invd["soc"] if invd else None
        dis_kw = max(invd.get("dis_w", 0), 0) / 1000 if invd else 0
        sun = invd and max(invd["pv1"]["v"], invd["pv3"]["v"]) >= 300
        # 保底：15:00–16:30 加熱不到 60 分鐘就強制開
        if 15 <= hm < self.FALLBACK_END and not on and self.st["minutes"] < self.MIN_MINUTES and not self.st.get("fallback_done"):
            self.st["fallback_done"] = True           # 一天只保底一次 (中途被手動關掉就不再開)
            if self.switch(True):
                self.st.update(auto_on=True, mode="fallback", changed=now)
                self.line(f"🔥 今天熱水器只加熱了 {self.st['minutes']:.0f} 分鐘（陰天電池沒充滿），保底加熱 1 小時到 16:00，確保晚上有熱水。")
            self.save()
            return
        if not on:
            if self.st.get("auto_on"):                # 有人自己關掉了
                self.st.update(auto_on=False, mode=None)
            if (9 <= hm < 15 and soc is not None and soc >= self.ON_SOC and sun
                    and now - self.st.get("changed", 0) >= 1800):
                if self.switch(True):
                    self.st.update(auto_on=True, mode="surplus", changed=now)
                    self.line(f"🔥 電池 {soc}% 快滿了、太陽能用不完，已自動開熱水器，把多的電存成熱水。")
            self.save()
            return
        if not self.st.get("auto_on"):                # 手動 / 排程開的：不管它
            self.save()
            return
        # 自動開的：該關了嗎
        self.dis_since = (self.dis_since or now) if dis_kw > self.OFF_DIS_KW else None
        why = None
        if hm >= self.FALLBACK_END and self.st.get("mode") == "fallback":
            why = "保底加熱 1 小時完成"
        elif hm >= 16.5:
            why = "已經 16:30"
        elif self.st.get("mode") == "surplus" and soc is not None and soc < self.OFF_SOC:
            why = f"電池降到 {soc}%"
        elif self.st.get("mode") == "surplus" and self.dis_since and now - self.dis_since >= 300:
            why = f"太陽能不夠、電池放電 {dis_kw:.1f} kW 已經 5 分鐘"
        if why and self.switch(False):
            self.line(f"✅ {why}，已自動關熱水器（今天共加熱 {self.st['minutes']:.0f} 分鐘）。")
            self.st.update(auto_on=False, mode=None, changed=now)
            self.dis_since = None
        self.save()


HOME_KEY = os.path.join(HERE, "home_key.json")
HOME_STATE = os.path.join(HERE, "home_state.json")


class HomeWatch(threading.Thread):
    """「澔宸 AI 智慧小窩」3D 圖的資料：背景每 60 秒用小米雲端讀全部米家裝置，每 5 分鐘讀 eWeLink 裝置。
    結果跟著每分鐘的上傳一起送到 Gist (snap["home"])，不會增加上傳次數。只讀，不控制。
    - 小米需要登入時：自動用 LINE 傳 QR code 連結 (米家 App 掃一下就好；12 小時內最多傳一次)。
    - 網站家人專用連結：第一次執行時產生隨機鑰匙 (pi/home_key.json，不上傳)，用 LINE 傳連結；
      Gist 只放鑰匙的 SHA-256，網站比對後才顯示燈、鐵捲門等「看得出有沒有人在家」的裝置。"""

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.devices, self.ew, self.t, self.ew_t = None, [], 0, 0
        self.login = "starting"
        self.key_sha = self._key()
        try:
            with open(HOME_STATE) as f:
                self.st = json.load(f)
        except (OSError, ValueError):
            self.st = {}

    def _save(self):
        with open(HOME_STATE, "w") as f:
            json.dump(self.st, f)

    def _key(self):
        import hashlib
        import secrets
        try:
            with open(HOME_KEY) as f:
                k = json.load(f)
        except (OSError, ValueError):
            k = {}
        if not k.get("key"):
            k = {"key": secrets.token_urlsafe(24), "sent": False}
        if not k.get("sent"):
            url = self.cfg.get("site_url", "https://haochen-solar-dashboard.streamlit.app/")
            if send_line(self.cfg, "🏠 澔宸 AI 智慧小窩 家人專用連結（打開就能看到家裡全部燈與家電的 3D 即時狀態，"
                                   f"請加入書籤，不要分享給外人）：\n{url}?k={k['key']}"):
                k["sent"] = True
        fd = os.open(HOME_KEY, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(k, f)
        return hashlib.sha256(k["key"].encode()).hexdigest()

    def _login(self, mc):
        if mc.refresh():
            self.login = "ok"
            return True
        self.login = "need"
        if time.time() - self.st.get("qr_sent", 0) < 12 * 3600:
            return False
        q = mc.qr_start()
        msg = ("🔑 樹莓派需要登入小米帳號，才能在網站 3D 小窩顯示家裡燈與冷氣的狀態（只讀取、不會控制）。\n"
               f"用電腦打開這個網址會出現 QR code，用米家 App 右上角「+」→「掃一掃」掃描：\n{q['qr']}")
        if q.get("url"):
            msg += f"\n\n只有手機的話，直接用手機瀏覽器打開：\n{q['url']}"
        msg += f"\n\n{q['timeout'] // 60} 分鐘內有效；沒掃到的話 12 小時後會再傳一次。"
        send_line(self.cfg, msg)
        self.st["qr_sent"] = time.time()
        self._save()
        if mc.qr_wait(q):
            self.login = "ok"
            send_line(self.cfg, "✅ 小米帳號登入成功，網站 3D 小窩開始顯示家電狀態（之後會自動續期）。")
            return True
        return False

    def _ewelink(self):
        try:
            import heater_control as h
            hc = h.load()
            things = h.cloud(hc, "GET", "/v2/device/thing", params={"num": 100}).get("thingList", [])
        except (Exception, SystemExit) as e:          # noqa: BLE001
            print(f"讀不到 eWeLink 裝置清單：{e}", flush=True)
            return
        out = []
        for t in things:
            d = t.get("itemData") or {}
            if not d.get("deviceid") or d.get("deviceid") == hc.get("deviceid"):   # 熱水器另外顯示
                continue
            p = d.get("params") or {}
            sws = [s.get("switch") == "on" for s in p.get("switches") or []]
            on = (p.get("switch") == "on") if "switch" in p else (any(sws) if sws else None)
            out.append({"n": d.get("name"), "r": None, "m": "eWeLink", "o": bool(d.get("online")), "k": "switch",
                        "on": on, "t": "", "src": "ew"} | ({"sw": sws} if len(sws) > 1 else {}))
        self.ew = out

    def run(self):
        import mi_cloud
        mc = mi_cloud.MiCloud()
        while True:
            t0 = time.time()
            try:
                if not mc.logged_in() and not self._login(mc):
                    pass
                else:
                    self.devices, self.t, self.login = mc.read_states(), time.time(), "ok"
            except mi_cloud.AuthError as e:
                print(f"小米雲端：{e}", flush=True)
                mc.s.pop("service_token", None)
                try:
                    self._login(mc)
                except Exception as e2:               # noqa: BLE001
                    print(f"小米登入失敗：{e2}", flush=True)
            except Exception as e:                    # noqa: BLE001  網路偶爾失敗：下一輪再試
                print(f"讀取米家裝置失敗：{e}", flush=True)
            time.sleep(max(10, 60 - (time.time() - t0)))

    def ewelink_tick(self):
        """在主迴圈呼叫 (跟熱水器控制同一個執行緒，避免兩邊同時換 eWeLink 登入憑證)。"""
        if os.path.exists(os.path.join(ROOT, "tools", "heater_config.json")) and time.time() - self.ew_t > 300:
            self.ew_t = time.time()
            self._ewelink()

    def sensor(self, mac):
        """藍牙收不到溫濕度計時的備援：從小米雲端 (經網關) 讀到的溫濕度。儲能室 = 名稱含「儲能」，戶外 = 含「車庫」。"""
        if not mac or not self.devices or time.time() - self.t > 600:
            return None
        kw = "儲能" if mac.upper() == str(self.cfg.get("mi_indoor", "")).upper() else \
             "車庫" if mac.upper() == str(self.cfg.get("mi_outdoor", "")).upper() else None
        for d in self.devices if kw else []:
            if kw in (d.get("n") or "") and d.get("temp") is not None:
                return {"temp": d["temp"], "hum": d.get("hum"), "batt": None, "t": self.t, "src": "cloud"}
        return None

    def summary(self):
        return {"key_sha": self.key_sha, "login": self.login, "t": round(self.t) if self.t else None,
                "devices": (self.devices or []) + self.ew}


# ---------------- 主程式 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.setup:
        return setup()
    global VERSION
    VERSION = _version()
    if not a.once:
        sys.stdout = _Tee(sys.stdout)
        print(f"樹莓派程式啟動，版本 {VERSION}", flush=True)
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
    thermo = MiThermo(cfg["mi_keys"]) if cfg.get("mi_keys") and not a.once else None
    if thermo:
        thermo.start()
    if calib:
        calib.thermo = thermo
    acauto = AcAuto(cfg, thermo) if not a.once else None
    heater = HeaterAuto(cfg) if not a.once else None
    home = HomeWatch(cfg) if not a.once and cfg.get("home_watch", True) else None
    if home:
        home.start()
        if thermo:
            thermo.fallback = home.sensor
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
                                                          "inverter": invd, "packs": [], "settings": {},
                                                          "pi": pi_diag(pi_stats())}
        alerts = check_alerts(invd, snap["packs"], time.time() - listener.last if listener else 0,
                              time.time() - inv_fail_since if inv_fail_since else 0)
        if a.once:
            print(json.dumps(snap, ensure_ascii=False, indent=1)[:3000])
            print("警示：", alerts or "無")
            return
        if acauto:
            try:
                acauto.tick(snap["packs"])
                snap["climate"] = acauto.climate(snap["packs"])
            except Exception as e:                   # 冷氣控制出錯不能影響記錄與上傳
                print(f"冷氣自動控制錯誤：{e}", flush=True)
        if heater and heater.enabled:
            try:
                heater.tick(invd)
                snap.setdefault("climate", {})["heater"] = heater.summary()
            except Exception as e:                   # 熱水器控制出錯不能影響記錄與上傳
                print(f"熱水器自動控制錯誤：{e}", flush=True)
        if home:
            try:
                home.ewelink_tick()
            except Exception as e:                   # noqa: BLE001
                print(f"eWeLink 裝置讀取錯誤：{e}", flush=True)
            snap["home"] = home.summary()
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
