"""太陽能機房冷氣 (小米紅外線冷氣伴侶 sixwgh.acpartner.ym02) 區網控制：走小米 miIO 協定 (UDP 54321)，不經過雲端。

金鑰 (TOKEN) 與 IP 存在樹莓派的 pi/config.json → "ac": {"ip", "token", "model"} (用 tools/mi_setup.py 輸入)。

用法：
  .venv/bin/python tools/mi_ac.py info          # 讀裝置資訊 (只讀)
  .venv/bin/python tools/mi_ac.py spec          # 下載並列出這個型號的功能清單 (存成 pi/ac_spec.json)
  .venv/bin/python tools/mi_ac.py status        # 目前開關 / 模式 / 設定溫度 (只讀)
  .venv/bin/python tools/mi_ac.py on | off      # 開 / 關
  .venv/bin/python tools/mi_ac.py temp 26       # 設定溫度
  .venv/bin/python tools/mi_ac.py cool          # 切到冷氣模式

注意：紅外線是單向的，冷氣伴侶只記得「自己最後送出的指令」；有人用原廠遙控器操作時，這裡讀到的狀態會不準。
"""
import hashlib
import json
import os
import socket
import struct
import sys
import time

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(HERE, "pi", "config.json")
SPEC_CACHE = os.path.join(HERE, "pi", "ac_spec.json")


def md5(b):
    return hashlib.md5(b).digest()


class Miio:
    """最精簡的 miIO 客戶端：hello 取得裝置 ID 與時間戳，之後 AES-128-CBC 加密送 JSON 指令。"""

    def __init__(self, ip, token, timeout=5):
        self.ip, self.token, self.timeout = ip, bytes.fromhex(token), timeout
        self.key = md5(self.token)
        self.iv = md5(self.key + self.token)
        self.did, self.stamp, self.t0, self.id = None, 0, 0, int(time.time()) % 10000

    def _sock(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(self.timeout)
        return s

    def hello(self):
        pkt = bytes.fromhex("21310020") + b"\xff" * 28
        with self._sock() as s:
            s.sendto(pkt, (self.ip, 54321))
            data, _ = s.recvfrom(1024)
        self.did, self.stamp = struct.unpack(">II", data[8:16])
        self.t0 = time.time()

    def _enc(self, raw):
        p = padding.PKCS7(128).padder()
        e = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return e.update(p.update(raw) + p.finalize()) + e.finalize()

    def _dec(self, raw):
        d = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        u = padding.PKCS7(128).unpadder()
        return u.update(d.update(raw) + d.finalize()) + u.finalize()

    def send(self, method, params=None, retries=3):
        last = None
        for _ in range(retries):
            try:
                if self.did is None or time.time() - self.t0 > 60:
                    self.hello()
                self.id += 1
                body = self._enc(json.dumps({"id": self.id, "method": method, "params": params or []},
                                            separators=(",", ":")).encode())
                stamp = self.stamp + int(time.time() - self.t0)
                head = struct.pack(">HHIII", 0x2131, 32 + len(body), 0, self.did, stamp)
                pkt = head + md5(head + self.token + body) + body
                with self._sock() as s:
                    s.sendto(pkt, (self.ip, 54321))
                    data, _ = s.recvfrom(4096)
                res = json.loads(self._dec(data[32:]).rstrip(b"\x00").decode())
                if "error" in res:
                    raise RuntimeError(f"裝置回應錯誤：{res['error']}")
                return res.get("result")
            except (socket.timeout, OSError, ValueError) as e:
                last = e
                self.did = None
                time.sleep(1)
        raise RuntimeError(f"冷氣伴侶沒有回應 ({last})：IP 或 TOKEN 不對，或不在同一個網路")


# ---------- MIoT 功能清單 (從 miot-spec.org 下載，找出開關 / 模式 / 溫度 / 風速的 siid、piid) ----------
def load_spec(model, refresh=False):
    if not refresh and os.path.exists(SPEC_CACHE):
        with open(SPEC_CACHE, encoding="utf-8") as f:
            sp = json.load(f)
        if sp.get("model") == model:
            return sp
    inst = requests.get("https://miot-spec.org/miot-spec-v2/instances?status=released", timeout=30).json()["instances"]
    cand = [i for i in inst if i["model"] == model]
    if not cand:
        inst = requests.get("https://miot-spec.org/miot-spec-v2/instances?status=all", timeout=30).json()["instances"]
        cand = [i for i in inst if i["model"] == model]
    if not cand:
        raise RuntimeError(f"miot-spec.org 找不到 {model}")
    typ = max(cand, key=lambda i: i.get("version", 0))["type"]
    full = requests.get(f"https://miot-spec.org/miot-spec-v2/instance?type={typ}", timeout=30).json()
    props = {}
    for s in full.get("services", []):
        sname = s["type"].split(":")[3]
        for p in s.get("properties", []):
            pname = p["type"].split(":")[3]
            props[f"{sname}.{pname}"] = {"siid": s["iid"], "piid": p["iid"], "access": p.get("access", []),
                                         "values": p.get("value-list"), "range": p.get("value-range"),
                                         "desc": p.get("description", "")}
    sp = {"model": model, "type": typ, "props": props}
    with open(SPEC_CACHE, "w", encoding="utf-8") as f:
        json.dump(sp, f, ensure_ascii=False, indent=1)
    return sp


def find(sp, *names):
    for n in names:
        for k, v in sp["props"].items():
            if k.endswith("." + n) and k.split(".")[0] in ("air-conditioner", "ir-aircondition-control", "fan-control"):
                return v
    for n in names:
        for k, v in sp["props"].items():
            if k.endswith("." + n):
                return v
    return None


class Ac:
    def __init__(self, cfg=None):
        if cfg is None:
            with open(CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
        ac = cfg.get("ac") or {}
        if not ac.get("token"):
            raise RuntimeError("pi/config.json 還沒有冷氣的 TOKEN (先執行 tools/mi_setup.py)")
        self.dev = Miio(ac.get("ip", "192.168.100.216"), ac["token"])
        self.model = ac.get("model") or "sixwgh.acpartner.ym02"
        self.sp = load_spec(self.model)
        self.p = {k: find(self.sp, *v) for k, v in {"on": ["on"], "mode": ["mode"],
                                                    "temp": ["target-temperature"], "fan": ["fan-level"]}.items()}

    def get(self):
        keys = [k for k, v in self.p.items() if v]
        res = self.dev.send("get_properties", [{"did": k, "siid": self.p[k]["siid"], "piid": self.p[k]["piid"]} for k in keys])
        return {r["did"]: r.get("value") for r in res if r.get("code", 0) == 0}

    def set(self, **kw):
        params = [{"did": k, "siid": self.p[k]["siid"], "piid": self.p[k]["piid"], "value": v} for k, v in kw.items()]
        res = self.dev.send("set_properties", params)
        bad = [r for r in res if r.get("code", 0) != 0]
        if bad:
            raise RuntimeError(f"設定失敗：{bad}")
        return True

    def mode_value(self, name):
        for v in (self.p["mode"] or {}).get("values") or []:
            if v["description"].lower() == name:
                return v["value"]
        return None


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "info":
        with open(CONFIG, encoding="utf-8") as f:
            ac = json.load(f)["ac"]
        print(json.dumps(Miio(ac["ip"], ac["token"]).send("miIO.info"), ensure_ascii=False, indent=1)[:1500])
        return
    if cmd == "spec":
        with open(CONFIG, encoding="utf-8") as f:
            model = json.load(f)["ac"].get("model") or "sixwgh.acpartner.ym02"
        sp = load_spec(model, refresh=True)
        print(f"{model} → {sp['type']}")
        for k, v in sp["props"].items():
            extra = v["values"] or v["range"] or ""
            print(f"  {k:45s} siid {v['siid']} piid {v['piid']}  {','.join(v['access'])}  {extra}")
        return
    ac = Ac()
    if cmd == "status":
        st = ac.get()
        modes = {v["value"]: v["description"] for v in (ac.p["mode"] or {}).get("values") or []}
        print(f"開關 {'開' if st.get('on') else '關'}  模式 {modes.get(st.get('mode'), st.get('mode'))}  "
              f"設定溫度 {st.get('temp')}°C  風速 {st.get('fan')}")
    elif cmd in ("on", "off"):
        ac.set(on=cmd == "on")
        print(f"✅ 已送出：{'開' if cmd == 'on' else '關'}")
    elif cmd == "temp" and len(sys.argv) > 2:
        ac.set(temp=float(sys.argv[2]))
        print(f"✅ 已設定 {sys.argv[2]}°C")
    elif cmd == "cool":
        v = ac.mode_value("cool")
        if v is None:
            sys.exit("這個型號的功能清單裡找不到 Cool 模式")
        ac.set(mode=v)
        print("✅ 已切到冷氣模式")
    else:
        print(__doc__)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit(f"❌ {e}")
