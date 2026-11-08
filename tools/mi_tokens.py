"""備用的小米金鑰取出工具 (在你自己的電腦上執行；登入用 Xiaomi-cloud-tokens-extractor 的官方流程)。

原版工具只用「家庭」清單查裝置，有些帳號會顯示 No devices found；這支改用舊版的「全部裝置」清單，
並印出登入的帳號 ID，方便確認跟米家 App 是同一個帳號。

用法 (跟 token_extractor.py 放在同一個資料夾)：
  python mi_tokens.py
結果只顯示在你的螢幕上，不會傳到任何地方。TOKEN / BLE KEY 請不要截圖給別人。
"""
import json
import sys

try:
    import token_extractor as T
except ImportError:
    sys.exit("請把 token_extractor.py 放在同一個資料夾")

SERVERS = ["cn", "tw", "sg", "de", "us", "i2", "ru", "in"]
WANT = ("儲能", "冷氣", "溫濕度", "空調", "車庫")


def main():
    way = input("登入方式 p = 帳號密碼、q = QR code：").strip().lower()
    c = T.QrCodeXiaomiCloudConnector() if way == "q" else T.PasswordXiaomiCloudConnector()
    if not c.login():
        sys.exit("登入失敗")
    print(f"\n✅ 已登入，帳號 ID：{c.userId}（請對照米家 App「我的」裡的 ID）\n")
    total = 0
    for s in SERVERS:
        url = c.get_api_url(s) + "/home/device_list"
        r = c.execute_api_call_encrypted(url, {"data": json.dumps(
            {"getVirtualModel": True, "getHuamiDevices": 1, "get_split_device": False, "support_smart_home": True})})
        devs = ((r or {}).get("result") or {}).get("list") or []
        if not devs:
            print(f"伺服器 {s}：沒有裝置")
            continue
        print(f"伺服器 {s}：{len(devs)} 台裝置")
        total += len(devs)
        for d in devs:
            name = d.get("name", "")
            star = "★ " if any(k in name for k in WANT) else "  "
            print(f"{star}{name}  |  MODEL {d.get('model')}  |  IP {d.get('localip')}  |  MAC {d.get('mac')}  |  DID {d.get('did')}")
            if star.strip():
                print(f"     TOKEN   {d.get('token')}")
                if str(d.get("did", "")).startswith("blt."):
                    k = c.get_beaconkey(s, d["did"])
                    print(f"     BLE KEY {((k or {}).get('result') or {}).get('beaconkey')}")
    print(f"\n共 {total} 台。★ 是名稱含 儲能 / 冷氣 / 溫濕度 / 車庫 的裝置，只有它們會顯示 TOKEN / BLE KEY。")
    input("按 Enter 結束")


if __name__ == "__main__":
    main()
