"""澔宸 AI 智慧小窩：把樹莓派上傳的米家 / eWeLink 裝置對應到 3D 房子裡的位置 (網站與 tools/mi_cloud.py 共用)。

裝置用「米家 App 裡的名稱 + 房間名稱」比對關鍵字；名稱改了或對錯位置，只要改下面的 SLOTS 規則。
  rules：每一組裡任一個關鍵字出現就算符合，所有組都要符合。
  ex   ：出現這些字就不算 (避免「2 樓廁所燈」被當成 1 樓廁所)。
  pub  ：沒登入的訪客也看得到 (只放本來就公開的機房冷氣、熱水器)。
裝置資料格式 (live["home"]["devices"])：
  {"n": 名稱, "r": 房間, "m": 型號, "o": 在線, "k": 類型, "on": True/False/None, "t": 狀態文字, "pos": 開啟 %, "sw": [各路開關]}
"""
from datetime import datetime, timedelta, timezone

TW = timezone(timedelta(hours=8))
AC_KW = ["冷氣", "空調"]

# (id, 樓層, 顯示名稱, 類型, rules, ex)
SLOTS = [
    ("garage_door", 1, "車庫鐵捲門", "garage", [["鐵捲", "捲門", "車庫門"]], []),
    ("f1_living_ac", 1, "客廳冷氣", "ac", [["客廳"]], []),
    ("f1_tv", 1, "客廳電視", "tv", [["電視"]], ["視聽"]),
    ("f1_flood", 1, "客廳投光燈", "light", [["投光"]], []),
    ("f1_ceiling", 1, "客廳吸頂燈", "light", [["吸頂"]], ["2樓", "3樓", "2F", "3F"]),
    ("f2_bed_indirect", 2, "主臥間接照明", "light", [["間接"], ["2樓", "2F", "房間", "臥"]], ["客廳"]),
    ("f1_indirect", 1, "客廳電燈 (間接照明)", "light", [["間接"]], ["2樓", "2F", "3樓", "3F"]),
    ("f1_kitchen", 1, "餐廳電燈", "light", [["廚房", "餐廳"], ["燈"]], []),
    ("f1_dining2", 1, "1 樓樓梯電燈 (餐廳開關)", "light", None, []),
    ("f1_dining3", 1, "1 樓走道電燈", "light", None, []),
    ("f1_laundry", 1, "洗衣間燈", "light", [["洗衣"], ["燈"]], []),
    ("f1_wc_back", 1, "後面廁所燈", "light", None, []),                 # 只在 ALT 比對 (見下面)
    ("f2_wc", 2, "主臥廁所燈", "light", [["2樓", "2F", "主臥"], ["廁所", "浴室"]], []),
    ("f1_wc", 1, "1 樓廁所燈", "light", [["廁所", "浴室"]], ["2樓", "3樓", "2F", "3F", "後"]),
    ("f1_lamp_chen", 1, "宸宸檯燈", "lamp", [["宸宸"]], []),
    ("f1_lamp_hao", 1, "澔澔檯燈", "lamp", [["澔澔"]], []),
    ("stair2", 2, "樓梯電燈", "light", [["樓梯"]], ["1樓", "2樓", "3樓", "1F", "2F", "3F"]),
    ("f1_stair", 1, "1 樓樓梯燈", "light", [["樓梯"]], ["2樓", "3樓", "2F", "3F"]),
    ("f2_curtain", 2, "主臥窗簾", "curtain", [["窗簾"]], []),
    ("f1_dish", 1, "洗碗廚餘機", "dish", [["洗碗", "廚餘"]], []),
    ("f2_bed_ac", 2, "主臥冷氣", "ac", [["2樓", "2F", "主臥"], ["房間", "臥"]], []),
    ("f2_study_ac", 2, "2 樓書房冷氣", "ac", [["書房"]], []),
    ("f2_night", 2, "主臥小夜燈", "light", [["夜燈"]], []),
    ("f2_main", 2, "主臥主照明", "light", [["主照明", "主燈"]], []),
    ("f2_hall", 2, "2 樓走道照明", "light", [["走道", "走廊"]], []),
    ("f2_hall2", 2, "2 樓走道照明 (第 2 路)", "light", None, []),
    ("f2_lamp", 2, "2 樓書房檯燈", "lamp", [["檯燈", "台燈"]], ["宸", "澔"]),
    ("f1_speaker", 1, "客廳小愛同學", "speaker", [["小愛", "音箱"], ["客廳", "1樓", "1F"]], []),
    ("f2_speaker", 2, "主臥小愛同學", "speaker", [["小愛", "音箱"]], ["客廳", "1樓", "1F"]),
    ("f3_bed_ac", 3, "3 樓房間冷氣", "ac", [["3樓", "3F"], ["房間", "臥"]], []),
    ("f3_av_ac", 3, "3 樓視聽室冷氣", "ac", [["視聽", "影音"]], []),
]
# 多鍵牆壁開關：米家裡一個開關 (例如 3 鍵的「客廳」) 有好幾路，每一路是一盞燈 → 依序對到這些位置 (第 1 路 = 最左邊)。
# 依米家 App 按鍵名稱確認過的：客廳 = 投光燈 / 吸頂燈 / 電燈；餐廳 = 餐廳電燈 / 樓梯電燈 / 走道電燈。
# (名稱關鍵字, 排除, [第 1 路, 第 2 路, ...])；只套用在讀得到 2 路以上開關的裝置。
GANGS = [
    ([["2樓房間照明", "房間照明"]], [], ["f2_night", "f2_main", "f2_bed_indirect"]),
    ([["走道照明", "走廊"]], ["3樓", "3F"], ["f2_hall", "f2_hall2"]),
    ([["餐廳", "廚房"]], AC_KW + ["洗碗", "廚餘"], ["f1_kitchen", "f1_dining2", "f1_dining3"]),
    ([["客廳"]], AC_KW + ["小愛", "音箱", "窗簾", "檯燈"], ["f1_flood", "f1_ceiling", "f1_indirect"]),
]
LABEL = {}                                           # slot id → 顯示名稱 (下面填)

# 第二輪才比的規則 (依順序)：1 樓只有洗衣間旁那一間廁所。
# 米家裡沒有「洗衣間燈」時，「後面廁所燈」其實就是洗衣間那盞 → 放到洗衣間；有「洗衣間燈」時才當成廁所的第二盞燈。
ALT = [
    ("f1_laundry", [["後面", "後"], ["廁所", "浴室"]], ["2樓", "3樓", "2F", "3F"]),
    ("f1_wc_back", [["後面", "後"], ["廁所", "浴室"]], ["2樓", "3樓", "2F", "3F"]),
]
# 不在米家 (資料從樹莓派的冷氣 / 熱水器自動控制來)，或還沒聯網的
EXTRA = [
    ("f4_solar_ac", 4, "太陽能機房冷氣", "ac"),
    ("f2_heater", 2, "儲熱電熱水器", "heater"),
    ("f4_gym_ac", 4, "健身室冷氣", "ac"),
]
LABEL.update({s[0]: s[2] for s in SLOTS})
PUBLIC = {"f4_solar_ac", "f2_heater"}
NO_NET = {"f4_gym_ac": "還沒聯網"}
SOLAR_AC_KW = [["儲能", "機房", "太陽能"], AC_KW]   # 4 樓機房冷氣伴侶在米家裡的名稱 (不重複列在其他裝置)

_CN = str.maketrans({"一": "1", "二": "2", "三": "3", "四": "4"})


def norm(s):
    return (s or "").translate(_CN).replace(" ", "").upper()


def is_ac(d):
    """冷氣：米家名稱常常只有房間名 (例如「2樓書房」)，所以看裝置類型 / 型號 (冷氣伴侶)。"""
    m = str(d.get("m") or "").lower()
    return (d.get("k") in ("air-conditioner", "ir-aircondition-control") or "acpartner" in m or ".aircondition" in m
            or any(k in (d.get("n") or "") for k in AC_KW))


HIDE = ["網關", "溫濕度", "WRITING"]                  # 不列在「其他裝置」(網關、藍牙溫濕度計另外顯示)


def _hit(text, rules, ex=()):
    return all(any(norm(k) in text for k in grp) for grp in rules) and not any(norm(k) in text for k in ex)


def match(devices):
    """回傳 ({slot_id: device}, [沒對到的裝置])；每台裝置只對一個位置，依 SLOTS 順序先比名稱、再比名稱 + 房間。"""
    left = list(devices or [])
    got = {}
    for rules, ex, ids in GANGS:
        for d in left:
            sw = d.get("sw") or []
            if len(sw) > 1 and _hit(norm(d.get("n")), rules, ex):
                for i, sid in enumerate(ids[:len(sw)]):
                    if sid not in got:
                        got[sid] = d | {"on": sw[i] if d.get("o") is not False else None, "sw": None,
                                        "n": LABEL.get(sid, sid).split(" (")[0],
                                        "t": f"「{d.get('n')}」第 {i + 1} 路"}
                left.remove(d)
                break
    names = [norm(d.get("n")) for d in left]
    dup = {n for n in names if names.count(n) > 1}       # 同名的 (例如兩個「間接照明」) 只能靠房間分辨
    for use_room in (False, True):
        for sid, _f, _l, kind, rules, ex in SLOTS:
            if sid in got or rules is None:
                continue
            for d in left:
                if not use_room and norm(d.get("n")) in dup:
                    continue
                text = norm(d.get("n")) + ("|" + norm(d.get("r")) if use_room else "")
                if (kind == "ac") != is_ac(d):           # 冷氣位置只收冷氣，其他位置不收冷氣
                    continue
                if _hit(text, rules, ex):
                    got[sid] = d
                    left.remove(d)
                    break
    for sid, rules, ex in ALT:
        if sid in got:
            continue
        for d in left:
            if _hit(norm(d.get("n")) + "|" + norm(d.get("r")), rules, ex):
                got[sid] = d
                left.remove(d)
                break
    left = [d for d in left if not (is_ac(d) and _hit(norm(d.get("n")), [SOLAR_AC_KW[0]]))
            and not any(norm(k) in norm(d.get("n")) for k in HIDE)]
    return got, left


def _ac_txt(mode, temp):
    m = {"cool": "冷氣", "heat": "暖氣", "dry": "除濕", "fan": "送風", "auto": "自動"}.get(str(mode or "").lower(), mode or "")
    return " · ".join(x for x in (m, f"設定 {temp:g}°C" if isinstance(temp, (int, float)) else "") if x)


def build(live, authed):
    """給 3D 房子用的狀態 (網站伺服器端產生；沒登入時不含任何非公開裝置的資料)。"""
    live = live or {}
    home = live.get("home") or {}
    clim = live.get("climate") or {}
    slots, others = {}, []
    if authed:
        got, left = match(home.get("devices"))
        for sid, _f, label, kind, _r, _e in SLOTS:
            d = got.get(sid)
            if not d:
                slots[sid] = {"st": "hide" if _r is None else "missing", "n": label}   # 只在第二輪比的沒對到就不畫
                continue
            st = "offline" if d.get("o") is False else "on" if d.get("on") else "off" if d.get("on") is False else "unknown"
            slots[sid] = {"st": st, "n": d.get("n") or label, "r": d.get("r"), "t": d.get("t") or "",
                          "pos": d.get("pos"), "sw": d.get("sw")}
            # 鐵捲門控制器 (米家只有 上 / 停 / 下 三個按鈕)：按鈕只是一下下的脈衝，讀不到門現在是開還是關；
            # 有開啟位置 (pos) 或「開門中 / 關門中」狀態時才顯示，否則標成狀態不明，不亂猜
            if kind == "garage" and st != "offline" and d.get("pos") is None:
                moving = any(w in str(d.get("t")).lower() for w in ("opening", "closing", "rising", "falling", "上升", "下降"))
                slots[sid] |= {"st": "on" if moving else "unknown", "sw": None,
                               "t": "門在動" if moving else "讀不到門開著還是關著（米家只有上 / 停 / 下按鈕）"}
        others = [{"n": d.get("n"), "r": d.get("r"), "st": "offline" if d.get("o") is False else
                   "on" if d.get("on") else "off" if d.get("on") is False else "unknown", "t": d.get("t") or ""}
                  for d in left]
    else:
        for sid, _f, label, _k, _r, _e in SLOTS:
            slots[sid] = {"st": "hide" if _r is None else "locked", "n": label}
    ac = clim.get("ac")
    slots["f4_solar_ac"] = ({"st": "on" if ac.get("on") else "off", "n": "太陽能機房冷氣",
                             "t": _ac_txt(ac.get("mode_name"), ac.get("temp")) + (" · 自動控制" if ac.get("auto_on") else "")}
                            if ac else {"st": "missing", "n": "太陽能機房冷氣"})
    hw = clim.get("heater")
    slots["f2_heater"] = ({"st": "on" if hw.get("on") else "off", "n": "儲熱電熱水器",
                           "t": ("加熱中" if hw.get("heating") else "保溫中") if hw.get("on") else
                           f"今天加熱 {hw.get('minutes_today', 0):.0f} 分鐘"}
                          if hw else {"st": "missing", "n": "儲熱電熱水器"})
    for sid, why in NO_NET.items():
        slots[sid] = {"st": "nonet", "n": next(e[2] for e in EXTRA if e[0] == sid), "t": why}
    inv = live.get("inverter") or {}
    ind, out = clim.get("indoor") or {}, clim.get("outdoor") or {}
    age = None
    if home.get("t"):
        age = round(datetime.now(TW).timestamp() - home["t"])
    return {
        "authed": bool(authed),
        "time": live.get("time"),
        "slots": slots,
        "others": others,
        "login": home.get("login") if authed else None,
        "age": age if authed else None,
        "energy": {"pv": inv.get("pv_w"), "soc": inv.get("soc"), "load": inv.get("load_w"),
                   "packs": [p.get("soc") for p in live.get("packs") or []]},
        "temps": {"room": ind.get("temp"), "out": out.get("temp"), "out_h": out.get("hum")},
        "floors": {sid: f for sid, f, *_ in SLOTS} | {e[0]: e[1] for e in EXTRA},
        "kinds": {sid: k for sid, _f, _l, k, *_ in SLOTS} | {e[0]: e[3] for e in EXTRA},
    }
