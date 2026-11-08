"""把小米裝置的金鑰存進樹莓派的 pi/config.json (只存在樹莓派上，不會上傳；檔案權限 600)。

金鑰用 Xiaomi-cloud-tokens-extractor 在你自己的電腦上取得，這支程式只負責「輸入並存起來」：
  .venv/bin/python tools/mi_setup.py
輸入時畫面不會顯示 (跟輸入密碼一樣)。
"""
import getpass
import json
import os
import re

CONFIG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pi", "config.json")


def ask_hex(prompt, n):
    while True:
        v = getpass.getpass(prompt).strip().lower()
        if not v:
            return ""
        if re.fullmatch(f"[0-9a-f]{{{n}}}", v):
            return v
        print(f"格式不對：應該是 {n} 個 0–9、a–f 的字元，再輸入一次 (直接按 Enter 跳過)")


def main():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    keys = cfg.setdefault("mi_keys", {})
    print("① 儲能室溫濕度計 (A4:C1:38:AA:7B:F5) 的 BLE KEY")
    k = ask_hex("   BLE KEY (32 字元)：", 32)
    if k:
        keys["A4:C1:38:AA:7B:F5"] = k
    mac = input("② 戶外 (車庫) 溫濕度計的 MAC (例如 A4:C1:38:xx:xx:xx，沒有就按 Enter)：").strip().upper()
    if mac:
        k = ask_hex("   它的 BLE KEY (32 字元)：", 32)
        if k:
            keys[mac] = k
            cfg["mi_outdoor"] = mac
    print("③ 太陽能機房冷氣 (紅外線遙控，IP 192.168.100.216)")
    t = ask_hex("   TOKEN (32 字元)：", 32)
    if t:
        cfg["ac"] = {"ip": input("   IP (直接 Enter = 192.168.100.216)：").strip() or "192.168.100.216",
                     "token": t, "model": input("   MODEL (例如 xiaomi.ir.xxx，可留空)：").strip()}
    cfg["mi_indoor"] = "A4:C1:38:AA:7B:F5"
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.chmod(CONFIG, 0o600)
    print(f"\n✅ 已存到 {CONFIG}（溫度計 {len(keys)} 顆、冷氣 {'有' if 'ac' in cfg else '沒有'}）")
    print("接著測試：.venv/bin/python tools/mi_ble.py read")


if __name__ == "__main__":
    main()
