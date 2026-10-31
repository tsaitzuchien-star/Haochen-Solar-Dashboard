"""JK BMS (BASEN GREEN 48300 電池) 並聯通訊監聽：只接收、完全不發送。

接在電池主機的 RS485C (黃線旁邊的空孔)，網路線第 1 腳 → B-、第 2 腳 → A+，115200 鮑率。
主機會輪流詢問每組電池，這支程式只是「旁聽」並解讀每組電池的回覆：
每顆電芯電壓、電流、溫度、SOC、剩餘容量、累計循環容量、SOH、BMS 設定值。

用法：
  python jk_listen.py              # 即時顯示 (Ctrl+C 結束)
  python jk_listen.py --csv        # 同時把每次讀到的資料存到 jk_log_日期.csv
"""
import argparse
import csv
import datetime as dt
import struct
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("請先安裝：pip install pyserial")

HEAD = b"\x55\xaa\xeb\x90"
FRAME = 300


def u16(f, o): return struct.unpack_from("<H", f, o)[0]
def i16(f, o): return struct.unpack_from("<h", f, o)[0]
def u32(f, o): return struct.unpack_from("<I", f, o)[0]
def i32(f, o): return struct.unpack_from("<i", f, o)[0]


def decode_cells(f):
    """JK02 (32 串版) 即時資料封包 (類型 0x02)。"""
    cells = [u16(f, 6 + 2 * n) / 1000 for n in range(16)]
    return {
        "cells": cells, "avg": u16(f, 74) / 1000, "delta": u16(f, 76) / 1000,
        "max_cell": f[78] + 1, "min_cell": f[79] + 1,
        "mos_t": i16(f, 144) / 10, "volt": u32(f, 150) / 1000, "power": u32(f, 154) / 1000,
        "curr": i32(f, 158) / 1000,          # 正 = 充電，負 = 放電
        "t1": i16(f, 162) / 10, "t2": i16(f, 164) / 10,
        "alarm": u16(f, 166), "bal_curr": i16(f, 170) / 1000, "bal": f[172],
        "soc": f[173], "remain_ah": u32(f, 174) / 1000, "full_ah": u32(f, 178) / 1000,
        "cycles": u32(f, 182), "cycle_ah": u32(f, 186), "soh": f[190],
        "runtime_h": u32(f, 194) / 3600, "chg_on": f[198], "dis_on": f[199],
    }


SETTING_NAMES = ["智慧休眠電壓", "單體欠壓保護", "欠壓恢復", "單體過壓保護", "過壓恢復", "均衡觸發壓差",
                 "SOC-100% 電壓", "SOC-0% 電壓", "建議充電電壓 RCV", "浮充電壓 RFV", "自動關機電壓",
                 "最大充電電流", "充電過流延遲", "充電過流恢復", "最大放電電流", "放電過流延遲", "放電過流恢復",
                 "短路恢復", "最大均衡電流", "充電過溫保護", "充電過溫恢復", "放電過溫保護", "放電過溫恢復",
                 "充電低溫保護", "充電低溫恢復", "MOS 過溫保護", "MOS 過溫恢復", "電芯數", "充電開關", "放電開關",
                 "均衡開關", "標稱容量", "短路保護延遲", "均衡起始電壓"]


def decode_settings(f):
    return {name: i32(f, 6 + 4 * n) for n, name in enumerate(SETTING_NAMES)}


def frames(buf):
    """從緩衝區切出完整、校驗正確的封包；回傳 (封包清單, 剩下的緩衝區)。
    每個封包後面緊接著該電池的 Modbus 回覆 (第 1 個 byte = 電池地址)。"""
    out = []
    while True:
        j = buf.find(HEAD)
        if j < 0 or len(buf) < j + FRAME + 1:
            return out, buf[max(j, 0):] if j >= 0 else buf[-3:]
        f = bytes(buf[j:j + FRAME])
        addr = buf[j + FRAME]
        if sum(f[:-1]) & 0xFF == f[-1]:
            out.append((addr, f))
        buf = buf[j + 4:]


def find_port():
    ports = list(list_ports.comports())
    if not ports:
        sys.exit("找不到 USB 轉 RS485 轉接器")
    pick = [p for p in ports if any(k in f"{p.description} {p.manufacturer}".upper()
                                    for k in ("FTDI", "FT232", "CH34", "CP210", "USB SERIAL", "RS485"))]
    return (pick or ports)[0].device


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--csv", action="store_true")
    a = ap.parse_args()
    port = a.port or find_port()
    ser = serial.Serial(port, 115200, timeout=0.2)
    print(f"旁聽 {port} 115200（只接收，不會送出任何資料），Ctrl+C 結束\n")
    writer = None
    if a.csv:
        fn = f"jk_log_{dt.datetime.now():%Y%m%d_%H%M}.csv"
        fh = open(fn, "w", newline="", encoding="utf-8-sig")
        writer = csv.writer(fh)
        writer.writerow(["時間", "電池地址", "電壓V", "電流A", "SOC%", "剩餘Ah", "最高單體V", "最低單體V", "壓差mV",
                         "溫度1", "溫度2", "MOS溫度", "累計循環Ah", "SOH%", "告警"] + [f"電芯{n}" for n in range(1, 17)])
        print(f"同時記錄到 {fn}")
    buf, shown_settings = bytearray(), set()
    try:
        while True:
            buf += ser.read(4096)          # 只讀取；整個程式沒有任何 write
            got, buf = frames(buf)
            for addr, f in got:
                now = dt.datetime.now()
                if f[4] == 0x02:
                    d = decode_cells(f)
                    print(f"{now:%H:%M:%S} 電池{addr}  {d['volt']:.2f}V  {d['curr']:+6.1f}A  SOC {d['soc']:3d}%  "
                          f"剩 {d['remain_ah']:.0f}/{d['full_ah']:.0f}Ah  單體 {min(d['cells']):.3f}–{max(d['cells']):.3f}V "
                          f"(差 {d['delta'] * 1000:.0f}mV)  溫度 {d['t1']:.1f}/{d['t2']:.1f}°C  MOS {d['mos_t']:.1f}°C  "
                          f"累計 {d['cycle_ah']:,}Ah  SOH {d['soh']}%" + (f"  ⚠️告警 {d['alarm']:#06x}" if d["alarm"] else ""))
                    if writer:
                        writer.writerow([now.isoformat(timespec="seconds"), addr, d["volt"], d["curr"], d["soc"],
                                         d["remain_ah"], max(d["cells"]), min(d["cells"]), round(d["delta"] * 1000),
                                         d["t1"], d["t2"], d["mos_t"], d["cycle_ah"], d["soh"], d["alarm"]] + d["cells"])
                        fh.flush()
                elif f[4] == 0x01 and addr not in shown_settings:
                    shown_settings.add(addr)
                    s = decode_settings(f)
                    print(f"         電池{addr} 設定：" + "、".join(f"{k} {v}" for k, v in list(s.items())[:12]) + " …")
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
