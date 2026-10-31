"""RS485 監聽工具：只接收、完全不發送 (不會干擾電池之間或電池與逆變器的通訊)。

用途：接在電池主機的 RS485C (並聯通訊的空孔)，聽主機和其他電池之間的對話，
判斷通訊速率與格式，並把原始資料存檔給 Claude 解讀。

用法：
  python rs485_sniff.py              # 自動找轉接器，依序試 9600 / 19200 / 38400 / 115200 鮑率，每種聽 8 秒
  python rs485_sniff.py --port COM5  # 指定序列埠
"""
import argparse
import datetime as dt
import json
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    sys.exit("請先安裝：pip install pyserial")

BAUDS = [9600, 19200, 38400, 115200]
LISTEN_S = 8


def crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def modbus_frames(buf):
    """在原始位元組裡找 CRC 正確的 Modbus RTU 封包 (4–256 bytes)，回傳數量。"""
    n, i = 0, 0
    while i < len(buf) - 4:
        for L in range(4, min(256, len(buf) - i) + 1):
            f = buf[i:i + L]
            if crc16(f[:-2]) == f[-2] | (f[-1] << 8) and f[1] in (1, 2, 3, 4, 5, 6, 15, 16, 0x83, 0x84):
                n += 1
                i += L - 1
                break
        i += 1
    return n


def find_port():
    ports = list(list_ports.comports())
    for p in ports:
        print(f"  找到序列埠：{p.device}  {p.description}")
    if not ports:
        sys.exit("找不到 USB 轉 RS485 轉接器")
    pick = [p for p in ports if any(k in f"{p.description} {p.manufacturer}".upper()
                                    for k in ("FTDI", "FT232", "CH34", "CP210", "USB SERIAL", "RS485"))]
    return (pick or ports)[0].device


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    a = ap.parse_args()
    port = a.port or find_port()
    print(f"監聽 {port}（只接收，不會送出任何資料）")
    result = {"time": dt.datetime.now().isoformat(timespec="seconds"), "port": port, "captures": {}}
    for baud in BAUDS:
        ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.2)
        ser.reset_input_buffer()
        buf, t0 = bytearray(), time.time()
        while time.time() - t0 < LISTEN_S:
            buf += ser.read(4096)          # 只讀取；整個程式沒有任何 write
        ser.close()
        ascii_frames = buf.count(b"~")    # Pylontech 類 ASCII 協定以 ~ 開頭
        mb = modbus_frames(bytes(buf[:20000]))
        print(f"  {baud:>6} 鮑率：收到 {len(buf):>6} bytes，Modbus 封包 {mb}，~ 開頭封包 {ascii_frames}"
              + (f"，前段：{buf[:24].hex(' ')}" if buf else ""))
        result["captures"][str(baud)] = {"bytes": len(buf), "modbus_frames": mb, "ascii_frames": ascii_frames,
                                         "hex": buf[:60000].hex()}
    fn = f"rs485_sniff_{dt.datetime.now():%Y%m%d_%H%M}.json"
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(result, f)
    best = max(result["captures"].items(), key=lambda kv: kv[1]["modbus_frames"] * 100 + kv[1]["ascii_frames"] * 100)
    if best[1]["bytes"] == 0:
        print("\n⚠️ 完全沒有收到資料：這組腳位可能不是 RS485，換下一組腳位再試")
    elif best[1]["modbus_frames"] or best[1]["ascii_frames"]:
        print(f"\n✅ 在 {best[0]} 鮑率聽到完整封包！")
    else:
        print("\n🟡 有收到資料但還認不出格式，可能是 A/B 接反，或要換鮑率")
    print(f"已存成 {fn}，請把這個檔案傳給 Claude")


if __name__ == "__main__":
    main()
