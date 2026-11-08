"""米家藍牙溫濕度計旁聽：只接收廣播，不連線、不配對，也不影響手機米家 App。

米家溫濕度計 3 (MJWSD05MMC) 每隔幾秒用藍牙廣播溫濕度 (服務代碼 FE95，MiBeacon 格式)，資料是加密的，
要有該裝置的 bindkey (從小米帳號取出) 才解得開。

用法 (樹莓派)：
  .venv/bin/python tools/mi_ble.py scan            # 列出附近的小米藍牙裝置 (不需要金鑰)
  .venv/bin/python tools/mi_ble.py read            # 用 pi/config.json 裡的 mi_keys 解出溫濕度
"""
import argparse
import asyncio
import json
import os
import struct
import sys
import time

try:
    from bleak import BleakScanner
except ImportError:
    sys.exit("請先安裝：.venv/bin/pip install bleak")

MI_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi", "config.json")


def parse_header(data):
    """MiBeacon 標頭：(是否加密, 產品代碼, 封包序號, 資料開始位置)。"""
    fc, pid, cnt = struct.unpack_from("<HHB", data, 0)
    i = 5
    if fc & 0x10:            # 有附 MAC
        i += 6
    if fc & 0x20:            # 有附 capability
        cap = data[i]
        i += 1
        if cap & 0x20:       # io capability
            i += 2
    return bool(fc & 0x08), pid, cnt, i, fc


def decrypt(data, mac, key_hex):
    """MiBeacon v4/v5 AES-CCM 解密 (nonce = 反轉 MAC + 產品代碼 + 序號 + 3 bytes 擴充序號，tag 4 bytes)。"""
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    enc, pid, cnt, i, fc = parse_header(data)
    if not enc:
        return data[i:]
    mac_rev = bytes.fromhex(mac.replace(":", ""))[::-1]
    ext, mic = data[-7:-4], data[-4:]
    nonce = mac_rev + data[2:4] + data[4:5] + ext
    return AESCCM(bytes.fromhex(key_hex), tag_length=4).decrypt(nonce, data[i:-7] + mic, b"\x11")


def decode_objects(p):
    """解出溫度 / 濕度 / 電量 (支援舊式 0x10xx 與新式 0x4Cxx 物件)。"""
    out, i = {}, 0
    while i + 3 <= len(p):
        oid, ln = struct.unpack_from("<HB", p, i)
        v = p[i + 3:i + 3 + ln]
        i += 3 + ln
        if oid == 0x1004 and ln == 2:
            out["temp"] = struct.unpack("<h", v)[0] / 10
        elif oid == 0x1006 and ln == 2:
            out["hum"] = struct.unpack("<H", v)[0] / 10
        elif oid == 0x100D and ln == 4:
            t, h = struct.unpack("<hH", v)
            out.update(temp=t / 10, hum=h / 10)
        elif oid == 0x100A and ln >= 1:
            out["batt"] = v[0]
        elif oid == 0x4C01 and ln == 4:
            out["temp"] = round(struct.unpack("<f", v)[0], 1)
        elif oid == 0x4C02 and ln == 1:
            out["hum"] = v[0]
        elif oid == 0x4C08 and ln == 4:
            out["hum"] = round(struct.unpack("<f", v)[0], 1)
        elif oid == 0x4803 and ln == 1:
            out["batt"] = v[0]
    return out


async def scan(seconds=25):
    seen = {}

    def cb(dev, adv):
        d = adv.service_data.get(MI_UUID)
        if d and len(d) >= 5:
            enc, pid, _, i, _ = parse_header(d)
            s = seen.setdefault(dev.address, {"enc": 0, "plain": 0, "vals": {}, "name": ""})
            s.update(rssi=adv.rssi, pid=pid)
            s["name"] = adv.local_name or dev.name or s["name"]
            s["enc" if enc else "plain"] += 1
            if not enc:
                s["vals"].update(decode_objects(d[i:]))

    async with BleakScanner(cb):
        print(f"聆聽 {seconds} 秒 …", flush=True)
        await asyncio.sleep(seconds)
    for mac, s in sorted(seen.items(), key=lambda x: -x[1]["rssi"]):
        vals = "  未加密資料：" + json.dumps(s["vals"], ensure_ascii=False) if s["vals"] else ""
        print(f"  {mac}  訊號 {s['rssi']} dBm  產品代碼 0x{s['pid']:04X}  加密 {s['enc']} 次 / 未加密 {s['plain']} 次"
              f"  {s['name']}{vals}")
    print(f"共 {len(seen)} 個小米藍牙裝置" if seen else "沒聽到任何小米藍牙廣播")


async def read(keys, seconds=60):
    got = {}

    def cb(dev, adv):
        d = adv.service_data.get(MI_UUID)
        mac = dev.address.upper()
        if not d or mac not in keys:
            return
        try:
            vals = decode_objects(decrypt(d, mac, keys[mac]))
        except Exception:                      # noqa: BLE001  金鑰不對或封包不完整
            return
        if vals:
            got.setdefault(mac, {}).update(vals, t=time.time())

    async with BleakScanner(cb):
        end = time.time() + seconds
        while time.time() < end and not all("temp" in got.get(m, {}) and "hum" in got.get(m, {}) for m in keys):
            await asyncio.sleep(1)
    for mac in keys:
        v = got.get(mac)
        print(f"  {mac}  " + (f"{v.get('temp', '?')} °C  {v.get('hum', '?')} %  電量 {v.get('batt', '?')}%" if v
                              else "沒收到 (金鑰不對或太遠)"))
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "read"])
    a = ap.parse_args()
    if a.cmd == "scan":
        return asyncio.run(scan())
    try:
        with open(CONFIG, encoding="utf-8") as f:
            keys = {k.upper(): v for k, v in json.load(f).get("mi_keys", {}).items()}
    except (OSError, ValueError):
        keys = {}
    if not keys:
        sys.exit("pi/config.json 還沒有 mi_keys (溫度計的藍牙金鑰)")
    asyncio.run(read(keys))


if __name__ == "__main__":
    main()
