"""樹莓派常駐程式：同時讀逆變器 (CN5 RS485) 與 4 組 JK BMS (RS485C 旁聽)，
每分鐘把即時資料上傳到私人 Gist 給網站「🔋 儲能」分頁顯示，並在 SOC 與電壓有落差、電芯過低、壓差過大、
溫度過高時直接推播 LINE。

  python pi/solar_logger.py --setup   # 第一次：輸入 LINE / GitHub 金鑰，自動建立私人 Gist
  python pi/solar_logger.py           # 常駐執行 (install.sh 會設定成開機自動啟動)
  python pi/solar_logger.py --once    # 讀一次、印出結果後結束 (測試用)

安全：逆變器只用 Modbus 讀取指令 (功能碼 03/04)；BMS 那條只接收、完全不發送。
"""
import argparse
import csv
import datetime as dt
import json
import os
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
        v = input(f"{label}{' (已設定)' if cfg.get(key) else ''}：").strip()
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


def detect_ports(cfg):
    """先用「旁聽」找出電池那條 (絕不對它送任何資料)，剩下的才用 Modbus 讀取指令確認是逆變器。"""
    ports = usb_ports()
    bp = cfg.get("bms_port", "auto")
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
    def __init__(self, port):
        super().__init__(daemon=True)
        self.port, self.packs, self.settings, self.last = port, {}, None, time.time()   # 開始旁聽的時間起算

    def run(self):
        while True:
            try:
                ser = serial.Serial(self.port, 115200, timeout=0.3)
                buf = bytearray()
                while True:
                    buf += ser.read(4096)          # 只讀取，沒有任何 write
                    got, buf = jk.frames(buf)
                    for addr, f in got:
                        if f[4] == 0x02:
                            self.packs[addr] = {"addr": addr, **jk.decode_cells(f), "t": time.time()}
                            self.last = time.time()
                        elif f[4] == 0x01 and addr == 0:
                            self.settings = jk.decode_settings(f)
            except (serial.SerialException, OSError) as e:
                print(f"BMS 序列埠錯誤：{e}，10 秒後重試", flush=True)
                time.sleep(10)


# ---------------- 警示 ----------------
COOLDOWN_H = {"socv": 6, "socv_red": 2, "lowcell": 2, "delta": 12, "temp": 3, "socgap": 12, "alarm": 3,
              "bms_lost": 6, "inv_lost": 6}


def check_alerts(invd, packs, bms_age):
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
        alarm = [p for p in packs if p.get("alarm")]
        if alarm:
            out.append(("alarm", "red", "BMS 告警：" + "、".join(f"#{p['addr']} 代碼 {p['alarm']:#06x}" for p in alarm)
                                        + "，請打開 JK App 查看"))
    if bms_age > 300:
        out.append(("bms_lost", "orange", f"已經 {bms_age / 60:.0f} 分鐘沒收到電池 BMS 資料，請檢查電池 RS485C 的網路線"))
    if invd is None:
        out.append(("inv_lost", "orange", "讀不到逆變器 (CN5) 的資料，請檢查網路線或轉接器"))
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
    with open(STATE, "w") as f:
        json.dump(state, f)


# ---------------- 上傳與記錄 ----------------
def snapshot(invd, listener):
    packs = [{k: v for k, v in p.items() if k != "t"} for p in sorted(listener.packs.values(), key=lambda p: p["addr"])
             if time.time() - p["t"] < 600]
    return {"time": dt.datetime.now(TW).isoformat(timespec="seconds"), "source": "樹莓派即時讀取",
            "inverter": invd, "packs": packs, "settings": listener.settings or {}}


def upload(cfg, snap):
    try:
        r = requests.patch(f"https://api.github.com/gists/{cfg['gist_id']}", timeout=20, headers=gh_headers(cfg),
                           json={"files": {GIST_FILE: {"content": json.dumps(snap, ensure_ascii=False)}}})
        return r.status_code == 200
    except requests.RequestException:
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


# ---------------- 主程式 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setup", action="store_true")
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()
    if a.setup:
        return setup()
    cfg = load_config()
    inv_ser, bms_port = detect_ports(cfg)
    print(f"逆變器：{inv_ser.port if inv_ser else '找不到'}　電池：{bms_port or '找不到'}", flush=True)
    listener = BmsListener(bms_port) if bms_port else None
    if listener:
        listener.start()
    if a.once:
        time.sleep(15)                                  # 等 BMS 輪完一圈
    while True:
        t0 = time.time()
        invd = None
        if inv_ser is None:
            inv_ser, _ = detect_ports(cfg)
        if inv_ser:
            try:
                invd = read_inverter(inv_ser)
            except (serial.SerialException, OSError):
                inv_ser = None
        snap = snapshot(invd, listener) if listener else {"time": dt.datetime.now(TW).isoformat(timespec="seconds"),
                                                          "inverter": invd, "packs": [], "settings": {}}
        alerts = check_alerts(invd, snap["packs"], time.time() - listener.last if listener else 0)
        if a.once:
            print(json.dumps(snap, ensure_ascii=False, indent=1)[:3000])
            print("警示：", alerts or "無")
            return
        ok = upload(cfg, snap)
        log_csv(snap)
        notify(cfg, alerts)
        print(f"{snap['time'][11:19]} 上傳{'✅' if ok else '❌'}  PV {invd['pv_w'] if invd else '-'} W  "
              f"SOC {invd['soc'] if invd else '-'}%  電池 {len(snap['packs'])} 組  警示 {len(alerts)}", flush=True)
        time.sleep(max(5, cfg.get("interval_s", 60) - (time.time() - t0)))


if __name__ == "__main__":
    main()
