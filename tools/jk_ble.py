"""JK BMS 藍牙工具 (跟 JK App 走同一條藍牙路)：讀取版本 / 電芯 / 設定，必要時重新寫入電池容量。

用法 (在樹莓派上，先把手機 JK App 完全關掉；JK 藍牙同時只能連一台)：
  .venv/bin/python tools/jk_ble.py scan                    # 列出附近的 JK BMS
  .venv/bin/python tools/jk_ble.py read 000-00             # 讀取 #0 的版本、電芯、設定 (只讀)
  .venv/bin/python tools/jk_ble.py set-capacity 003-03 340 # 重新寫入容量 340 Ah (會先問你確認)

安全：
  - 「read」只送出 JK App 也會送的查詢指令，不改任何設定。
  - 「set-capacity」只寫「標稱容量」這一個值，只接受 300–360 Ah；
    寫入前後各讀一次全部 34 項設定比對，除了容量以外有任何一項改變就大聲警告。
"""
import argparse
import asyncio
import os
import struct
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("請先安裝：.venv/bin/pip install bleak")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jk_listen import SETTING_NAMES, decode_cells, decode_settings  # noqa: E402  (同一種 JK02 封包格式)

SERVICE = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR = "0000ffe1-0000-1000-8000-00805f9b34fb"
HEAD = b"\x55\xaa\xeb\x90"
FRAME = 300
CMD_DEVICE_INFO, CMD_CELL_INFO = 0x97, 0x96
REG_CAPACITY = 0x20                      # 設定封包第 32 項「標稱容量」(mAh)，寫入位址 = 項次
CAP_IDX = SETTING_NAMES.index("標稱容量")
assert REG_CAPACITY == CAP_IDX + 1


def command(reg, value=0, length=0):
    """JK02 藍牙指令：AA 55 90 EB | 位址 | 長度 | 4 bytes 數值 (小端) | 9 個 0 | 前 19 bytes 加總。"""
    f = bytearray(20)
    f[0:4] = b"\xaa\x55\x90\xeb"
    f[4], f[5] = reg, length
    f[6:10] = struct.pack("<I", value)
    f[19] = sum(f[:19]) & 0xFF
    return bytes(f)


def ascii_at(f, off, n):
    return bytes(f[off:off + n]).split(b"\x00")[0].decode("ascii", "replace").strip()


def decode_info(f):
    return {"型號": ascii_at(f, 6, 16), "硬體版本": ascii_at(f, 22, 8), "軟體版本": ascii_at(f, 30, 8),
            "開機次數": struct.unpack_from("<I", f, 42)[0], "藍牙名稱": ascii_at(f, 46, 16),
            "生產日期": ascii_at(f, 78, 8), "序號": ascii_at(f, 86, 11)}


class Jk:
    """收藍牙通知、拼成 300 bytes 的完整封包，依類型 (0x01 設定、0x02 電芯、0x03 版本) 存最新一包。"""

    def __init__(self, client):
        self.c, self.buf, self.frames = client, bytearray(), {}
        self.event = asyncio.Event()

    def on_data(self, _, data):
        self.buf += data
        while True:
            j = self.buf.find(HEAD)
            if j < 0:
                del self.buf[:-3]
                return
            if len(self.buf) < j + FRAME:
                del self.buf[:j]
                return
            f = bytes(self.buf[j:j + FRAME])
            if sum(f[:-1]) & 0xFF == f[-1]:
                self.frames[f[4]] = f
                self.event.set()
                del self.buf[:j + FRAME]
            else:
                del self.buf[:j + 4]

    async def ask(self, cmd, want, timeout=10):
        for t in want:
            self.frames.pop(t, None)
        await self.c.write_gatt_char(CHAR, cmd, response=False)
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout
        while not all(t in self.frames for t in want):
            self.event.clear()
            left = end - loop.time()
            if left <= 0:
                return False
            try:
                await asyncio.wait_for(self.event.wait(), left)
            except asyncio.TimeoutError:
                return False
        return True

    async def read_all(self):
        info = await self.ask(command(CMD_DEVICE_INFO), [0x03], 6)
        ok = await self.ask(command(CMD_CELL_INFO), [0x01, 0x02], 12)
        if not ok:
            raise RuntimeError("沒收到電芯 / 設定資料 (手機 App 是不是還連著？)")
        return (decode_info(self.frames[0x03]) if info else None,
                decode_cells(self.frames[0x02]), decode_settings(self.frames[0x01]))


async def find(target):
    if target.count(":") == 5:
        return target
    print(f"尋找 {target} …")
    dev = await BleakScanner.find_device_by_name(target, timeout=20)
    if not dev:
        sys.exit(f"找不到 {target}。請確認手機 JK App 已完全關掉 (從背景滑掉)")
    return dev


async def scan():
    found = await BleakScanner.discover(timeout=20, return_adv=True)
    rows = [(adv.local_name or d.name or "?", d.address, adv.rssi) for d, adv in found.values()
            if SERVICE in [u.lower() for u in adv.service_uuids]]
    for name, addr, rssi in sorted(rows):
        print(f"  {name:10s} {addr}  訊號 {rssi} dBm")
    print(f"共找到 {len(rows)} 組 JK BMS")


def show(info, d, s):
    if info:
        print("版本：" + "、".join(f"{k} {v}" for k, v in info.items()))
    print(f"電池：{d['volt']:.2f} V  {d['curr']:+.1f} A  SOC {d['soc']}%  剩 {d['remain_ah']:.1f}/{d['full_ah']:.1f} Ah  "
          f"單體 {min(d['cells']):.3f}–{max(d['cells']):.3f} V  溫度 {d['t1']:.1f}/{d['t2']:.1f}°C")
    print(f"標稱容量設定：{s['標稱容量'] / 1000:.0f} Ah   SOC-100% 電壓：{s['SOC-100% 電壓'] / 1000:.3f} V")


async def run(args):
    if args.cmd == "scan":
        return await scan()
    dev = await find(args.name)
    async with BleakClient(dev, timeout=20) as client:
        jk = Jk(client)
        await client.start_notify(CHAR, jk.on_data)
        info, d, s = await jk.read_all()
        show(info, d, s)
        if args.cmd == "read":
            if args.all:
                print("全部設定：" + "、".join(f"{k} {v}" for k, v in s.items()))
            return
        ah = args.ah
        if not 300 <= ah <= 360:
            sys.exit("容量只接受 300–360 Ah")
        if not args.yes and input(f"\n確定要把 {args.name} 的標稱容量寫成 {ah} Ah？(輸入 yes)：").strip() != "yes":
            sys.exit("已取消，沒有寫入")
        await client.write_gatt_char(CHAR, command(REG_CAPACITY, ah * 1000, 4), response=False)
        print(f"已送出：標稱容量 {ah} Ah，3 秒後讀回確認 …")
        await asyncio.sleep(3)
        _, d2, s2 = await jk.read_all()
        changed = {k: (s[k], s2[k]) for k in s if s[k] != s2[k] and k != "標稱容量"}
        if changed:
            print("⚠️⚠️ 警告：以下設定也被改到了，請立刻用 JK App 改回原值：")
            for k, (a, b) in changed.items():
                print(f"   {k}：{a} → {b}")
        if s2["標稱容量"] != ah * 1000:
            print(f"❌ 讀回的容量是 {s2['標稱容量'] / 1000:.0f} Ah，寫入沒有生效")
        elif not changed:
            print(f"✅ 容量確認為 {ah} Ah，其他 33 項設定都沒變")
        print(f"SOC：{d['soc']}% ({d['remain_ah']:.1f} Ah) → {d2['soc']}% ({d2['remain_ah']:.1f} Ah)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    r = sub.add_parser("read")
    r.add_argument("name", help="藍牙名稱 (例如 000-00) 或 MAC 位址")
    r.add_argument("--all", action="store_true", help="列出全部 34 項設定")
    w = sub.add_parser("set-capacity")
    w.add_argument("name")
    w.add_argument("ah", type=int)
    w.add_argument("--yes", action="store_true", help="不再詢問確認 (給自動化用)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
