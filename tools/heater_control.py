"""電熱水器 (eWeLink / 易微聯 WiFi 開關) 控制測試：區網直接控制，失敗時改用 eWeLink 雲端。

第一次使用 (只要做一次)：
  1. 到 https://dev.ewelink.cc 註冊「個人開發者」，建立一個應用，
     Redirect URL 填 http://127.0.0.1:8000/redirectUrl ，取得 APPID 與 APP SECRET
  2. pip install requests cryptography zeroconf
  3. python heater_control.py setup
     → 輸入 APPID / APP SECRET，瀏覽器會打開 eWeLink 登入頁，用你平常的 eWeLink 帳號登入
     → 程式自動找到「儲熱電熱水器」，把通訊金鑰存在本機的 heater_config.json (不會上傳到任何地方)

之後：
  python heater_control.py status   # 查目前開關狀態
  python heater_control.py on       # 開
  python heater_control.py off      # 關

安全：heater_config.json 裡有你的登入憑證與開關金鑰，請不要傳給別人或上傳。
"""
import base64
import hashlib
import hmac
import http.server
import json
import os
import random
import string
import sys
import threading
import time
import urllib.parse
import webbrowser

try:
    import requests
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    sys.exit("請先安裝：pip install requests cryptography zeroconf")

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heater_config.json")
REDIRECT = "http://127.0.0.1:8000/redirectUrl"
NAME_HINT = "熱水器"


# ---------- 設定檔 ----------
def load():
    if not os.path.exists(CONFIG):
        sys.exit("還沒設定，請先執行：python heater_control.py setup")
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def save(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


# ---------- eWeLink 雲端 (CoolKit Open API v2) ----------
def sign(msg, secret):
    buf = json.dumps(msg, separators=(",", ":"), ensure_ascii=False) if isinstance(msg, dict) else msg
    return base64.b64encode(hmac.new(secret.encode(), buf.encode(), hashlib.sha256).digest()).decode()


def nonce():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def api_base(region):
    return f"https://{region}-apia.coolkit.{'cn' if region in ('cn', 'test') else 'cc'}"


def cloud(cfg, method, path, body=None, params=None, retry=True):
    headers = {"Authorization": f"Bearer {cfg['at']}", "X-CK-Appid": cfg["appid"], "X-CK-Nonce": nonce(),
               "Content-Type": "application/json"}
    r = requests.request(method, api_base(cfg["region"]) + path, headers=headers, timeout=15,
                         data=json.dumps(body, separators=(",", ":")) if body is not None else None, params=params)
    res = r.json()
    if res.get("error") in (401, 402) and retry:      # access token 過期：用 refresh token 換新的
        refresh(cfg)
        return cloud(cfg, method, path, body, params, retry=False)
    if res.get("error") != 0:
        raise RuntimeError(f"eWeLink 雲端回應錯誤 {res.get('error')}：{res.get('msg')}")
    return res.get("data") or {}


def refresh(cfg):
    body = {"rt": cfg["rt"]}
    r = requests.post(api_base(cfg["region"]) + "/v2/user/refresh", timeout=15,
                      data=json.dumps(body, separators=(",", ":")),
                      headers={"X-CK-Appid": cfg["appid"], "Authorization": f"Sign {sign(body, cfg['secret'])}",
                               "Content-Type": "application/json", "X-CK-Nonce": nonce()}).json()
    if r.get("error") != 0:
        sys.exit("登入已失效，請重新執行：python heater_control.py setup")
    cfg["at"], cfg["rt"] = r["data"]["at"], r["data"]["rt"]
    save(cfg)


def oauth_login(appid, secret):
    """打開 eWeLink 官方登入頁，在本機 127.0.0.1:8000 接回授權碼。"""
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>✅ 登入成功，可以關掉這個頁面，回到命令提示字元</h2>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 8000), Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    seq = str(int(time.time() * 1000))
    params = {"clientId": appid, "redirectUrl": REDIRECT, "grantType": "authorization_code",
              "state": nonce(), "nonce": nonce(), "seq": seq, "authorization": sign(f"{appid}_{seq}", secret)}
    url = "https://c2ccdn.coolkit.cc/oauth/index.html?" + urllib.parse.urlencode(params)
    print("正在打開 eWeLink 登入頁…（沒有自動打開的話，請複製這個網址到瀏覽器）\n" + url)
    webbrowser.open(url)
    for _ in range(600):
        if "code" in got:
            break
        time.sleep(0.5)
    srv.server_close()
    if "code" not in got:
        sys.exit("等太久沒有登入，請重新執行 setup")
    region = got.get("region", "as")
    body = {"redirectUrl": REDIRECT, "code": got["code"], "grantType": "authorization_code"}
    r = requests.post(api_base(region) + "/v2/user/oauth/token", timeout=15,
                      data=json.dumps(body, separators=(",", ":")),
                      headers={"X-CK-Appid": appid, "Authorization": f"Sign {sign(body, secret)}",
                               "Content-Type": "application/json", "X-CK-Nonce": nonce()}).json()
    if r.get("error") != 0:
        sys.exit(f"取得登入憑證失敗：{r.get('error')} {r.get('msg')}")
    d = r["data"]
    return {"appid": appid, "secret": secret, "region": region,
            "at": d.get("accessToken") or d.get("at"), "rt": d.get("refreshToken") or d.get("rt")}


def setup():
    appid = input("APPID：").strip()
    secret = input("APP SECRET：").strip()
    cfg = oauth_login(appid, secret)
    things = cloud(cfg, "GET", "/v2/device/thing", params={"num": 100}).get("thingList", [])
    devs = [t["itemData"] for t in things if t.get("itemType") in (1, 2)]
    if not devs:
        sys.exit("這個帳號底下找不到任何裝置")
    for i, dv in enumerate(devs):
        print(f"  [{i}] {dv.get('name')}  ({dv.get('deviceid')})")
    pick = [dv for dv in devs if NAME_HINT in (dv.get("name") or "")]
    dev = pick[0] if len(pick) == 1 else devs[int(input("請輸入熱水器的編號：").strip())]
    params = dev.get("params") or {}
    cfg.update(deviceid=dev["deviceid"], devicekey=dev.get("devicekey", ""), name=dev.get("name"),
               multi="switches" in params and "switch" not in params)
    save(cfg)
    print(f"\n✅ 設定完成：{dev.get('name')}（{dev['deviceid']}），目前 {current_cloud(cfg)}")
    print("可以試試：python heater_control.py off ，再 python heater_control.py on")


# ---------- 區網直接控制 (需要 devicekey，AES-128-CBC，金鑰 = MD5(devicekey)) ----------
def find_ip(deviceid, wait=6):
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return None
    found = {}

    class L:
        def add_service(self, zc, typ, name):
            if deviceid in name:
                info = zc.get_service_info(typ, name)
                if info and info.parsed_addresses():
                    found["host"] = f"{info.parsed_addresses()[0]}:{info.port or 8081}"

        update_service = add_service

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    ServiceBrowser(zc, "_ewelink._tcp.local.", L())
    for _ in range(wait * 10):
        if found:
            break
        time.sleep(0.1)
    zc.close()
    return found.get("host")


def lan_switch(cfg, on):
    if not cfg.get("devicekey"):
        return False
    host = cfg.get("host") or find_ip(cfg["deviceid"])
    if not host:
        return False
    data = ({"switches": [{"switch": "on" if on else "off", "outlet": 0}]} if cfg.get("multi")
            else {"switch": "on" if on else "off"})
    plain = json.dumps(data, separators=(",", ":")).encode()
    key = hashlib.md5(cfg["devicekey"].encode()).digest()
    iv = os.urandom(16)
    padder = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(padder.update(plain) + padder.finalize()) + enc.finalize()
    payload = {"sequence": str(int(time.time() * 1000)), "deviceid": cfg["deviceid"], "selfApikey": "123",
               "encrypt": True, "iv": base64.b64encode(iv).decode(), "data": base64.b64encode(ct).decode()}
    path = "switches" if cfg.get("multi") else "switch"
    try:
        r = requests.post(f"http://{host}/zeroconf/{path}", json=payload, timeout=5,
                          headers={"Connection": "close"}).json()
    except (requests.RequestException, ValueError):
        cfg.pop("host", None)
        return False
    if r.get("error") == 0:
        if cfg.get("host") != host:
            cfg["host"] = host
            save(cfg)
        return True
    return False


# ---------- 開 / 關 / 狀態 ----------
def current_cloud(cfg):
    p = cloud(cfg, "GET", "/v2/device/thing/status",
              params={"type": 1, "id": cfg["deviceid"], "params": "switch|switches"}).get("params", {})
    sw = p.get("switch") or (p.get("switches") or [{}])[0].get("switch")
    return {"on": "開啟 🔥", "off": "關閉"}.get(sw, f"未知（{p}）")


def set_switch(on):
    cfg = load()
    word = "開啟" if on else "關閉"
    if lan_switch(cfg, on):
        print(f"✅ 已{word}（區網直接控制）")
        return
    params = ({"switches": [{"switch": "on" if on else "off", "outlet": 0}]} if cfg.get("multi")
              else {"switch": "on" if on else "off"})
    cloud(cfg, "POST", "/v2/device/thing/status", {"type": 1, "id": cfg["deviceid"], "params": params})
    print(f"✅ 已{word}（經由 eWeLink 雲端；區網控制沒成功，可能是筆電跟開關不在同一個 WiFi）")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "setup":
        setup()
    elif cmd in ("on", "off"):
        set_switch(cmd == "on")
    elif cmd == "status":
        print("熱水器目前：" + current_cloud(load()))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
