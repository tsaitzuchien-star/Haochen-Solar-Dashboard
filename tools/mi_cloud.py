"""小米雲端 (米家) 讀取全部裝置的開關狀態：給「澔宸 AI 智慧小窩」3D 圖用。只讀，不會控制任何裝置。

為什麼用雲端：其他樓層的裝置在 192.168.1.x / 192.168.0.x，跟樹莓派 (192.168.100.x) 不同網段，區網 miIO 連不到。

登入：第一次用米家 App 掃 QR code (不用輸入小米密碼)。之後用小米發的長效憑證 (passToken) 自動續期，
存在樹莓派的 pi/mi_cloud.json (權限 600、不上傳)。樹莓派常駐程式發現需要登入時，會自動把 QR code 連結用 LINE 傳給你。

用法 (在樹莓派上)：
  .venv/bin/python tools/mi_cloud.py login     # 手動登入一次 (印出 QR code 連結，用米家 App 掃)
  .venv/bin/python tools/mi_cloud.py list      # 列出全部裝置、房間、在不在線，以及對應到 3D 圖的哪個位置
  .venv/bin/python tools/mi_cloud.py status    # 讀一次所有裝置的狀態 (跟上傳到網站的內容一樣)
  .venv/bin/python tools/mi_cloud.py spec 型號  # 看某個型號的功能清單 (除錯用)
  .venv/bin/python tools/mi_cloud.py blekey    # 從雲端更新藍牙溫濕度計的 BLE KEY (存進 pi/config.json，不顯示)
"""
import base64
import hashlib
import json
import os
import random
import string
import sys
import time
import urllib.parse

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION = os.path.join(ROOT, "pi", "mi_cloud.json")
SPECS = os.path.join(ROOT, "pi", "miot_specs.json")
ACCOUNT = "https://account.xiaomi.com"

# 3D 圖會用到的服務 (依優先順序決定裝置類型) 與屬性；其他 (電量、指示燈、童鎖…) 不讀
SERVICES = ["air-conditioner", "ir-aircondition-control", "water-heater", "heater", "light", "curtain",
            "window-opener", "motor-controller", "garage-door", "door", "dishwasher", "washer", "speaker",
            "play-control", "television", "fan", "air-purifier", "switch", "outlet", "plug",
            "temperature-humidity-sensor"]
PROPS = ["on", "brightness", "mode", "target-temperature", "status", "current-position", "target-position",
         "playing-state", "left-time", "fault", "temperature", "relative-humidity"]
IDLE = {"idle", "off", "standby", "sleep", "stop", "stopped", "close", "closed", "pause", "paused", "complete",
        "completed", "done", "wait", "waiting", "none", "normal", "no fault", "no faults", "unknown"}


class AuthError(RuntimeError):
    pass


def _json(text):
    return json.loads(text.replace("&&&START&&&", "", 1))


def _rc4(key, data):
    """小米 API 用的 RC4 (先丟掉前 1024 bytes 的金鑰流)。純 Python，不用額外套件。"""
    s, j = list(range(256)), 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) & 255
        s[i], s[j] = s[j], s[i]
    i = j = 0
    out = bytearray()
    for n in range(1024 + len(data)):
        i = (i + 1) & 255
        j = (j + s[i]) & 255
        s[i], s[j] = s[j], s[i]
        if n >= 1024:
            out.append(data[n - 1024] ^ s[(s[i] + s[j]) & 255])
    return bytes(out)


def _b64(b):
    return base64.b64encode(b).decode()


class MiCloud:
    def __init__(self, path=SESSION):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.s = json.load(f)
        except (OSError, ValueError):
            self.s = {}
        self.s.setdefault("server", "cn")
        self.s.setdefault("agent", "".join(random.choice("ABCDE") for _ in range(13)))
        self.s.setdefault("device_id", "".join(random.choice(string.ascii_lowercase) for _ in range(16)))
        self.ua = f"Android-7.1.1-1.0.0-ONEPLUS A3010-136-{self.s['agent']} APP/xiaomi.smarthome APPV/62830"
        self.http = requests.Session()
        self._rooms, self._rooms_t, self._specs = {}, 0, None
        self._instances = None

    # ---------- 登入 ----------
    def logged_in(self):
        return bool(self.s.get("service_token") and self.s.get("ssecurity"))

    def save(self):
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.s, f)
        os.chmod(self.path, 0o600)

    def _acct(self):
        h = requests.Session()
        h.headers["User-Agent"] = self.ua
        h.cookies.update({"sdkVersion": "accountsdk-18.8.15", "deviceId": self.s["device_id"]})
        return h

    def _finish(self, h, location, nonce=None):
        """打開登入成功後給的網址，拿到 API 用的 serviceToken。"""
        if nonce and "clientSign" not in location:
            sign = _b64(hashlib.sha1(f"nonce={nonce}&{self.s['ssecurity']}".encode()).digest())
            location += ("&" if "?" in location else "?") + "clientSign=" + urllib.parse.quote(sign)
        h.get(location, timeout=20)
        tok = next((c.value for c in h.cookies if c.name == "serviceToken"), None)
        if not tok:
            raise AuthError("登入最後一步沒有拿到 serviceToken")
        self.s["service_token"] = tok
        self.s["login_t"] = time.time()
        self.save()
        return True

    def qr_start(self):
        """開始 QR code 登入：回傳 {"qr": 圖片網址, "url": 手機直接開的網址, "lp": ..., "timeout": 秒}。"""
        h = self._acct()
        r = h.get(f"{ACCOUNT}/longPolling/loginUrl", timeout=20, params={
            "_qrsize": "480", "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue", "callback": "https://sts.api.io.mi.com/sts",
            "_hasLogo": "false", "sid": "xiaomiio", "serviceParam": "", "_locale": "zh_TW",
            "_dc": str(int(time.time() * 1000))})
        j = _json(r.text)
        if "qr" not in j:
            raise AuthError(f"小米沒有給 QR code：{str(j)[:200]}")
        return {"qr": j["qr"], "url": j.get("loginUrl"), "lp": j["lp"], "timeout": int(j.get("timeout") or 300), "h": h}

    def qr_wait(self, q):
        """等米家 App 掃描 (最多 q["timeout"] 秒)。成功回傳 True。"""
        h, end = q["h"], time.time() + max(q["timeout"], 120)
        while time.time() < end:
            try:
                r = h.get(q["lp"], timeout=15)
            except requests.exceptions.Timeout:
                continue
            except requests.RequestException:
                time.sleep(3)
                continue
            if r.status_code != 200:
                time.sleep(2)
                continue
            j = _json(r.text)
            if not j.get("ssecurity"):
                continue
            self.s.update(user_id=str(j["userId"]), ssecurity=j["ssecurity"], pass_token=j.get("passToken"),
                          c_user_id=j.get("cUserId"))
            return self._finish(h, j["location"], j.get("nonce"))
        return False

    def refresh(self):
        """用長效憑證 passToken 換新的 serviceToken (不用再掃 QR code)。失敗回傳 False。"""
        if not (self.s.get("pass_token") and self.s.get("user_id")):
            return False
        h = self._acct()
        h.cookies.update({"userId": self.s["user_id"], "passToken": self.s["pass_token"]})
        try:
            r = h.get(f"{ACCOUNT}/pass/serviceLogin", params={"sid": "xiaomiio", "_json": "true"}, timeout=20)
            j = _json(r.text)
        except (requests.RequestException, ValueError) as e:
            print(f"小米續期連線失敗：{e}", flush=True)
            return False
        if j.get("code") != 0 or not j.get("ssecurity") or not j.get("location"):
            return False
        self.s["ssecurity"] = j["ssecurity"]
        if j.get("passToken"):
            self.s["pass_token"] = j["passToken"]
        try:
            return self._finish(h, j["location"], j.get("nonce"))
        except (AuthError, requests.RequestException) as e:
            print(f"小米續期失敗：{e}", flush=True)
            return False

    # ---------- 加密 API ----------
    @property
    def base(self):
        s = self.s["server"]
        return "https://api.io.mi.com/app" if s == "cn" else f"https://{s}.api.io.mi.com/app"

    @staticmethod
    def _sig(method, url, snonce, params):
        parts = [method, url.split("com", 1)[1].replace("/app/", "/")] + [f"{k}={v}" for k, v in params.items()] + [snonce]
        return _b64(hashlib.sha1("&".join(parts).encode()).digest())

    def api(self, path, data, _retry=True):
        if not self.logged_in():
            raise AuthError("還沒登入小米帳號")
        url = self.base + path
        nonce = _b64(os.urandom(8) + (int(time.time() * 1000) // 60000).to_bytes(4, "big"))
        key = hashlib.sha256(base64.b64decode(self.s["ssecurity"]) + base64.b64decode(nonce)).digest()
        snonce = _b64(key)
        params = {"data": json.dumps(data, separators=(",", ":"))}
        params["rc4_hash__"] = self._sig("POST", url, snonce, params)
        params = {k: _b64(_rc4(key, v.encode())) for k, v in params.items()}
        params.update(signature=self._sig("POST", url, snonce, params), ssecurity=self.s["ssecurity"], _nonce=nonce)
        tok = self.s["service_token"]
        r = self.http.post(url, data=params, timeout=25, headers={
            "User-Agent": self.ua, "Accept-Encoding": "identity", "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2", "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4"},
            cookies={"userId": self.s["user_id"], "yetAnotherServiceToken": tok, "serviceToken": tok,
                     "locale": "zh_TW", "timezone": "GMT+08:00", "is_daylight": "0", "dst_offset": "0",
                     "channel": "MI_APP_STORE"})
        if r.status_code in (401, 403):
            if _retry and self.refresh():
                return self.api(path, data, False)
            raise AuthError(f"小米登入已失效 (HTTP {r.status_code})")
        r.raise_for_status()
        try:
            j = json.loads(_rc4(key, base64.b64decode(r.text)).decode())
        except (ValueError, UnicodeDecodeError):
            j = json.loads(r.text)
        code = j.get("code", 0)
        if code in (2, 3) or "auth" in str(j.get("message", "")).lower():
            if _retry and self.refresh():
                return self.api(path, data, False)
            raise AuthError(f"小米登入已失效：{j.get('message')}")
        if code != 0:
            raise RuntimeError(f"小米 API {path} 回應錯誤：{code} {j.get('message')}")
        return j.get("result")

    def devices(self):
        r = self.api("/home/device_list", {"getVirtualModel": True, "getHuamiDevices": 1, "get_split_device": False,
                                           "support_smart_home": True})
        return (r or {}).get("list") or []

    def rooms(self):
        """{did: 房間名稱}，一小時查一次。"""
        if time.time() - self._rooms_t < 3600:
            return self._rooms
        try:
            r = self.api("/v2/homeroom/gethome", {"fg": True, "fetch_share": True, "fetch_share_dev": True,
                                                  "limit": 300, "app_ver": 7})
            out = {}
            for hm in (r or {}).get("homelist") or []:
                for rm in hm.get("roomlist") or []:
                    for did in rm.get("dids") or []:
                        out[str(did)] = rm.get("name")
            self._rooms, self._rooms_t = out, time.time()
        except (RuntimeError, requests.RequestException, ValueError) as e:
            print(f"讀不到米家房間清單：{e}", flush=True)
            self._rooms_t = time.time() - 3000          # 10 分鐘後再試
        return self._rooms

    def get_props(self, params):
        out = []
        for i in range(0, len(params), 30):
            out += self.api("/miotspec/prop/get", {"params": params[i:i + 30]}) or []
        return out

    # ---------- 型號功能清單 (miot-spec.org，存在 pi/miot_specs.json) ----------
    def spec(self, model):
        if self._specs is None:
            try:
                with open(SPECS, encoding="utf-8") as f:
                    self._specs = json.load(f)
            except (OSError, ValueError):
                self._specs = {}
        sp = self._specs.get(model)
        if sp and (sp.get("svcs") or time.time() - sp.get("t", 0) < 7 * 86400):
            return sp
        try:
            if self._instances is None:
                self._instances = requests.get("https://miot-spec.org/miot-spec-v2/instances?status=all",
                                               timeout=60).json()["instances"]
            cand = [i for i in self._instances if i["model"] == model]
            svcs = []
            if cand:
                best = max(cand, key=lambda i: (i.get("status") == "released", i.get("version", 0)))
                full = requests.get(f"https://miot-spec.org/miot-spec-v2/instance?type={best['type']}", timeout=30).json()
                for s in full.get("services", []):
                    sname = s["type"].split(":")[3]
                    if sname not in SERVICES:
                        continue
                    props = {}
                    for p in s.get("properties", []):
                        pname = p["type"].split(":")[3]
                        if pname in PROPS and "read" in p.get("access", []):
                            props[pname] = {"piid": p["iid"], "unit": p.get("unit"),
                                            "vl": {str(v["value"]): v["description"] for v in p.get("value-list") or []}}
                    if props:
                        svcs.append({"n": sname, "siid": s["iid"], "p": props})
            sp = {"svcs": svcs, "t": time.time()}
        except (requests.RequestException, ValueError, KeyError) as e:
            print(f"下載 {model} 功能清單失敗：{e}", flush=True)
            return {"svcs": []}
        self._specs[model] = sp
        with open(SPECS, "w", encoding="utf-8") as f:
            json.dump(self._specs, f, ensure_ascii=False)
        return sp

    # ---------- 全部裝置狀態 ----------
    def read_states(self):
        devs = self.devices()
        rooms = self.rooms()
        plan, req = [], []
        for d in devs:
            did, model = str(d.get("did")), d.get("model") or ""
            svcs = []
            # 藍牙裝置只讀溫濕度計 (經網關上傳到雲端，給藍牙收不到時備援)
            if (not did.startswith("blt.") or "sensor_ht" in model) and d.get("isOnline"):
                svcs = sorted(self.spec(model).get("svcs") or [], key=lambda s: SERVICES.index(s["n"]))
            picks = []
            count = {}
            for s in svcs:
                count[s["n"]] = count.get(s["n"], 0) + 1
            seen = {}
            for s in svcs:
                seen[s["n"]] = seen.get(s["n"], 0) + 1
                tag = s["n"] + (f"#{seen[s['n']]}" if count[s["n"]] > 1 else "")
                for pname, p in s["p"].items():
                    if len(picks) < 12:
                        picks.append((f"{tag}.{pname}", s["siid"], p["piid"], p))
                        req.append({"did": did, "siid": s["siid"], "piid": p["piid"]})
            plan.append((d, did, svcs[0]["n"] if svcs else None, picks))
        vals = {}
        if req:
            for r in self.get_props(req):
                if r.get("code", 0) == 0 and "value" in r:
                    vals[(str(r["did"]), r["siid"], r["piid"])] = r["value"]
        out = []
        for d, did, kind, picks in plan:
            v = {}
            for key, siid, piid, p in picks:
                if (did, siid, piid) in vals:
                    x = vals[(did, siid, piid)]
                    v[key] = p["vl"].get(str(x), x) if p["vl"] and not isinstance(x, bool) else x
            out.append(summarize(d, rooms.get(did), kind, v))
        return out


def summarize(d, room, kind, v):
    """把讀到的屬性整理成網站要的格式 (不含 did、TOKEN、IP)。"""
    ons = [x for k, x in v.items() if k.endswith(".on") and isinstance(x, bool)]
    on, pos, txt = None, None, []
    if ons:
        on = any(ons)
    # 判斷「開著」的優先順序：開關 → 開啟位置 (窗簾 / 鐵捲門) → 播放狀態 → 運轉狀態
    for want in ("current-position", "playing-state", "status"):
        for k, x in v.items():
            if k.split(".")[-1] != want:
                continue
            if want == "current-position" and isinstance(x, (int, float)):
                pos = x
                if on is None:
                    on = x > 2
            elif want == "playing-state" and on is None:
                on = str(x).lower() in ("playing", "play", "1")
            elif want == "status" and on is None and isinstance(x, str):
                on = x.strip().lower() not in IDLE
    for k, x in v.items():
        name = k.split(".")[-1]
        if name == "brightness" and isinstance(x, (int, float)) and on:
            txt.append(f"亮度 {x:g}%")
        elif name == "mode" and isinstance(x, str) and on and not k.startswith(("switch", "outlet", "plug")):   # 開關的 mode 是接線模式
            txt.append(x)
        elif name == "target-temperature" and isinstance(x, (int, float)) and on:
            txt.append(f"設定 {x:g}°C")
        elif name == "current-position" and isinstance(x, (int, float)):
            txt.append(f"開 {x:g}%")
        elif name in ("status", "playing-state") and isinstance(x, str):
            txt.append(x)
        elif name == "left-time" and isinstance(x, (int, float)) and x:
            txt.append(f"剩 {x:g} 分")
    rec = {"n": d.get("name"), "r": room, "m": d.get("model"), "o": bool(d.get("isOnline")), "k": kind,
           "on": on, "t": " · ".join(dict.fromkeys(txt))[:60]}
    if pos is not None:
        rec["pos"] = pos
    temp = next((x for k, x in v.items() if k.endswith(".temperature") and isinstance(x, (int, float))), None)
    hum = next((x for k, x in v.items() if k.endswith(".relative-humidity") and isinstance(x, (int, float))), None)
    if temp is not None:
        rec |= {"temp": round(temp, 1), "hum": hum, "t": f"{temp:.1f}°C" + (f" {hum:.0f}%" if hum is not None else "")}
    if len(ons) > 1:
        rec["sw"] = ons
    return rec


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    mc = MiCloud()
    if cmd == "login":
        q = mc.qr_start()
        print("用電腦 (或另一支手機) 打開這個網址，會看到 QR code，再用米家 App 右上角「+」→「掃一掃」掃描：")
        print(f"  {q['qr']}")
        if q.get("url"):
            print(f"只有一支手機的話，也可以直接用手機瀏覽器打開：\n  {q['url']}")
        print(f"等待掃描中（{q['timeout']} 秒內有效）…")
        print("✅ 登入成功，已存到 pi/mi_cloud.json" if mc.qr_wait(q) else "❌ 逾時，請再執行一次")
        return
    if cmd == "blekey":
        # 從小米雲端抓藍牙溫濕度計最新的 BLE KEY，直接存進 pi/config.json (畫面上不顯示金鑰)
        if not mc.logged_in() and not mc.refresh():
            sys.exit("還沒登入：先執行 .venv/bin/python tools/mi_cloud.py login")
        cfg_path = os.path.join(ROOT, "pi", "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        keys = cfg.setdefault("mi_keys", {})
        n = 0
        for d in mc.devices():
            did, mac = str(d.get("did")), str(d.get("mac") or "").upper()
            if not did.startswith("blt.") or "sensor_ht" not in str(d.get("model")) or not mac:
                continue
            r = mc.api("/v2/device/blt_get_beaconkey", {"did": did, "pdid": 1}) or {}
            k = str(r.get("beaconkey") or "").lower()
            if len(k) != 32:
                print(f"⚠️ {d.get('name')}：雲端沒有給金鑰")
                continue
            changed = keys.get(mac) != k
            keys[mac] = k
            name = d.get("name") or ""
            if "儲能" in name:
                cfg["mi_indoor"] = mac
            elif "車庫" in name:
                cfg["mi_outdoor"] = mac
            n += 1
            print(f"✅ {name}（{mac}）{'金鑰已更新（原本的不一樣）' if changed else '金鑰沒變'}")
        fd = os.open(cfg_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        print(f"\n共 {n} 顆。接著執行：sudo systemctl restart solar-logger")
        return
    if cmd == "spec" and len(sys.argv) > 2:
        print(json.dumps(mc.spec(sys.argv[2]), ensure_ascii=False, indent=1))
        return
    if not mc.logged_in() and not mc.refresh():
        sys.exit("還沒登入：先執行 .venv/bin/python tools/mi_cloud.py login")
    if cmd == "list":
        sys.path.insert(0, ROOT)
        import home_map
        recs = mc.read_states()                      # 跟上傳到網站的一樣 (含多鍵開關各路、裝置類型)
        got, _left = home_map.match(recs)
        label = {s[0]: s[2] for s in home_map.SLOTS}
        where = {}
        for sid, d in got.items():                   # 多鍵開關的每一路是複製出來的，用名稱 + 房間找回原本那台
            g = d.get("g") or ""
            key = (g.split("「")[1].split("」")[0] if "」第" in g else d.get("n"), d.get("r"))
            where.setdefault(key, []).append(label[sid] + (f"（第 {g.split('第 ')[1]}）" if "」第" in g else ""))
        print(f"伺服器 {mc.s['server']}：{len(recs)} 台\n")
        for d in sorted(recs, key=lambda d: (d["r"] or "", d["n"] or "")):
            to = where.get((d["n"], d["r"]))
            sw = f"  |  {len(d['sw'])} 路開關" if d.get("sw") else ""
            print(f"{'🟢' if d['o'] else '⚫'} {d['n']}  |  房間 {d['r'] or '—'}  |  {d['m']}{sw}  →  "
                  f"{('3D：' + '、'.join(to)) if to else '（列在網站「其他裝置」）' if any(d is x for x in _left) else '（不顯示：網關、感應器、無線按鈕，或機房冷氣另外顯示）'}")
        miss = [label[s[0]] for s in home_map.SLOTS if s[0] not in got and s[4] is not None]
        if miss:
            print("\n3D 圖上還沒對到裝置的位置：" + "、".join(miss))
    elif cmd == "status":
        t0 = time.time()
        for r in mc.read_states():
            st = "離線" if not r["o"] else "開" if r["on"] else "關" if r["on"] is False else "—"
            print(f"{st:3s} {r['n']}  ({r['r'] or '—'} · {r['k'] or r['m']})  {r['t']}")
        print(f"\n讀取花了 {time.time() - t0:.1f} 秒")
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except (AuthError, RuntimeError) as e:
        sys.exit(f"❌ {e}")
