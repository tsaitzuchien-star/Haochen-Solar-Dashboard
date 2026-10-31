"""Growatt SPH 10000TL-HU-US：透過 CN5 (Computer) RS485 讀取逆變器所有暫存器 (只讀，不會修改任何設定)。

接線 (CN5 RJ45，T568B 色碼)：
  第 1 腳 (橘白) → 轉接器 B-
  第 2 腳 (橘)   → 轉接器 A+
  其他腳不接 (GND 先不接)

用法：
  pip install pyserial
  python growatt_rs485_scan.py            # 自動找轉接器、自動試鮑率與站號，掃描完存成 growatt_scan_日期.json
  python growatt_rs485_scan.py --port COM5 # 指定序列埠 (Windows 看裝置管理員；Mac 是 /dev/tty.usbserial-xxxx)
  python growatt_rs485_scan.py --live      # 掃描後每 5 秒顯示一次重點數值 (Ctrl+C 結束)

安全：程式只使用 Modbus 功能碼 03 / 04 (讀取)，沒有任何寫入指令。
"""
import argparse
import datetime as dt
import json
import struct
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("請先安裝：pip install pyserial")

READ_HOLDING, READ_INPUT = 3, 4      # 只允許這兩個讀取功能碼
# Growatt 協定常見的暫存器區段 (不同機型用不同區段，全部試一遍)
RANGES = {
    READ_INPUT: [(0, 125), (1000, 125), (2000, 125), (3000, 250)],
    READ_HOLDING: [(0, 125), (1000, 125), (3000, 125)],
}
CHUNK = 25


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


def read_regs(ser, unit, func, start, count):
    """讀取 count 個暫存器；失敗回傳 None。"""
    assert func in (READ_HOLDING, READ_INPUT)
    req = struct.pack(">BBHH", unit, func, start, count)
    ser.reset_input_buffer()
    ser.write(req + crc16(req))
    need = 5 + 2 * count
    resp = ser.read(need)
    if len(resp) >= 5 and resp[1] == func | 0x80 and crc16(resp[:3]) == resp[3:5]:
        return None   # 例外回應：這段暫存器不存在
    if len(resp) != need or crc16(resp[:-2]) != resp[-2:] or resp[0] != unit or resp[1] != func:
        return None
    return list(struct.unpack(f">{count}H", resp[3:-2]))


def find_port():
    ports = list(list_ports.comports())
    for p in ports:
        print(f"  找到序列埠：{p.device}  {p.description}")
    pick = [p for p in ports if any(k in f"{p.description} {p.manufacturer}".upper()
                                    for k in ("FTDI", "FT232", "CH34", "CP210", "USB SERIAL", "USB-SERIAL", "RS485"))]
    if not (pick or ports):
        sys.exit("找不到 USB 轉 RS485 轉接器，請確認有插好 (Windows 可在裝置管理員 → 連接埠查看)")
    return (pick or ports)[0].device


def connect(port, bauds, units):
    for baud in bauds:
        ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.6)
        for unit in units:
            for func in (READ_INPUT, READ_HOLDING):
                if read_regs(ser, unit, func, 0, 10) is not None:
                    print(f"✅ 連線成功：{port}  鮑率 {baud}  站號 {unit}")
                    return ser, unit
        ser.close()
        print(f"  鮑率 {baud} 沒有回應")
    sys.exit("❌ 逆變器沒有回應：請檢查 A+/B- 是否接反 (對調再試)、RJ45 有沒有插緊、是否插在 CN5: Computer")


def scan(ser, unit):
    out = {}
    for func, ranges in RANGES.items():
        name = "input" if func == READ_INPUT else "holding"
        for start, length in ranges:
            got = 0
            for s in range(start, start + length, CHUNK):
                vals = read_regs(ser, unit, func, s, min(CHUNK, start + length - s))
                time.sleep(0.05)
                if vals is None:
                    continue
                got += len(vals)
                for i, v in enumerate(vals):
                    out[f"{name}:{s + i}"] = v
            print(f"  {name} {start}–{start + length - 1}：讀到 {got} 個")
    return out


def u32(r, kind, a):
    return (r.get(f"{kind}:{a}", 0) << 16) | r.get(f"{kind}:{a + 1}", 0)


def live(ser, unit):
    # SPH 10000TL-HU-US 實測對應 (2026-10-31 掃描)：input 0–124 逆變器、1000–1124 儲能 / BMS
    print("\n每 5 秒更新 (Ctrl+C 結束)")
    print("時間      PV西   PV東   輸出   負載L1/L2    充電   放電  逆變器充電A  BMS電流  電池V  SOC  逆變器°C 電池°C")
    while True:
        r = {}
        for start in (0, 25, 50, 75, 1000, 1025, 1050, 1075, 1100):
            vals = read_regs(ser, unit, READ_INPUT, start, 25)
            if vals:
                r.update({f"input:{start + i}": v for i, v in enumerate(vals)})
        g = lambda a: r.get(f"input:{a}", 0)
        w = lambda a: u32(r, "input", a) / 10
        vb = g(1013) / 10
        print(f"{dt.datetime.now():%H:%M:%S}  {w(5):5.0f}  {w(13):5.0f}  {w(35):5.0f}  {w(1031):5.0f}/{w(1033):<5.0f}  "
              f"{w(1011):5.0f}  {w(1009):5.0f}  {w(1011) / vb if vb else 0:9.1f}A  {g(1088) / 100:6.2f}A  "
              f"{vb:5.1f}  {g(1014):3d}%  {g(93) / 10:6.1f}  {g(1040) / 10:5.1f}")
        time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--baud", type=int)
    ap.add_argument("--unit", type=int)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    port = a.port or find_port()
    ser, unit = connect(port, [a.baud] if a.baud else [115200, 9600, 38400], [a.unit] if a.unit else [1, 2, 3])
    print("開始掃描 (約 1 分鐘)…")
    regs = scan(ser, unit)
    fn = f"growatt_scan_{dt.datetime.now():%Y%m%d_%H%M}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump({"time": dt.datetime.now().isoformat(timespec="seconds"), "port": port,
                   "baud": ser.baudrate, "unit": unit, "registers": regs}, f, indent=1)
    print(f"\n✅ 完成：共 {len(regs)} 個暫存器，已存成 {fn}，請把這個檔案傳給 Claude")
    if a.live:
        live(ser, unit)


if __name__ == "__main__":
    main()
