"""LINE 通知：GitHub Actions 每 30 分鐘執行一次，有狀況或到了固定時間才發訊息 (LINE 官方帳號廣播給所有好友)。

- 異常 (隨時)：逆變器超過 30 分鐘沒回傳、放電中電壓低於 48.5 V、顯示 100% 但電壓不到 54 V (需要校正)
- 每天早上 6:30–8:30：今日發電預測與用電建議、東西兩串比例異常
- 每天晚上 19:30–21:30：預估今晚電池撐不到天亮時才提醒
- 每月 1 日早上：上個月報告
同一種通知在冷卻時間內不重發，每天最多 MAX_PER_DAY 則；發送紀錄存在 data/notify_state.json。

用法：GROWATT_TOKEN=... LINE_TOKEN=... python scripts/notify.py [--test]
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pv_forecast  # noqa: E402
import solar_core as C  # noqa: E402

clean = lambda v: (v or "").strip().strip('"').strip("'").strip() or None
GROWATT_TOKEN = clean(os.environ.get("GROWATT_TOKEN"))
LINE_TOKEN = clean(os.environ.get("LINE_TOKEN"))
DEVICE_SN = os.environ.get("GROWATT_DEVICE_SN", "KQQ0N6200N")
FLOOR = int(os.environ.get("BATT_FLOOR") or 35)        # 放電下限 (%)，可在 GitHub 的 Variables 設定 BATT_FLOOR
SITE = "https://haochen-solar-dashboard.streamlit.app/"
STATE = ROOT / "data" / "notify_state.json"
MAX_PER_DAY = 6
COOLDOWN_H = {"offline": 6, "lowv": 6, "calib": 24}


def growatt(endpoint, **data):
    r = requests.post(f"https://openapi.growatt.com/v4/new-api/{endpoint}", headers={"token": GROWATT_TOKEN},
                      data={"deviceSn": DEVICE_SN, "deviceType": "sph-s", **data}, timeout=30)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(d.get("message"))
    return d["data"]


def line_send(text):
    text = re.sub(r"<[^>]+>", "", text)
    if not LINE_TOKEN:
        print("::error::沒有 LINE_TOKEN：請到 GitHub → Settings → Secrets and variables → Actions 新增 LINE_TOKEN")
        print(text)
        return False
    r = requests.post("https://api.line.me/v2/bot/message/broadcast", timeout=30,
                      headers={"Authorization": f"Bearer {LINE_TOKEN}"},
                      json={"messages": [{"type": "text", "text": text[:4900]}]})
    if r.status_code != 200:
        print(f"::error::LINE 發送失敗 {r.status_code}: {r.text[:200]}")
        return False
    print("已發送：", text.splitlines()[0])
    return True


def yesterday_review(now):
    """昨天回顧 (每日存檔 00:40 寫入)：發電、預測誤差、自給率、省下的電費。"""
    try:
        daily = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"])
        fc = pd.read_csv(ROOT / "data" / "forecast.csv", parse_dates=["date"])
    except Exception as e:
        print(f"::warning::讀不到存檔：{e}")
        return ""
    y = pd.Timestamp(now.date()) - pd.Timedelta(days=1)
    row = daily[(daily["date"] == y) & (daily["source"] != "plant")]
    if row.empty:
        return ""
    r = row.iloc[0]
    txt = f"\n📊 昨天發電 {r['pv']:.1f} 度"
    p = fc[fc["date"] == y]
    if len(p) and r["pv"] > 0:
        txt += f"（預測 {p.iloc[0]['pv']:.1f}，誤差 {abs(p.iloc[0]['pv'] / r['pv'] - 1) * 100:.0f}%）"
    # 省下的錢：本月至今的累進電費省下金額 ÷ 本月太陽能供應的度數 = 每度省多少，再乘上昨天太陽能供應的度數
    m = daily[(daily["source"] != "plant") & (daily["date"].dt.to_period("M") == y.to_period("M")) & (daily["date"] <= y)]
    own = m["load"].sum() - m["buy"].sum()
    if own > 0 and r["load"] > 0:
        saved = C.monthly_savings(m["load"].sum(), m["buy"].sum(), y.month, len(m), y.days_in_month)[0]
        self_r = (1 - r["buy"] / r["load"]) * 100
        txt += f"\n用電 {r['load']:.1f} 度，自給率 {self_r:.0f}%，省下約 {saved / own * (r['load'] - r['buy']):.0f} 元"
    return txt


def main():
    now = dt.datetime.now(C.TW_TZ)
    today = now.date().isoformat()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    sent_today = state.get("sent_today", {})
    if sent_today.get("date") != today:
        sent_today = {"date": today, "n": 0}
    msgs = []   # (key, text)

    if "--test" in sys.argv:
        ok = line_send(f"✅ 澔宸小窩太陽能通知設定成功！\n之後有異常、每天早上的發電預測、月報都會傳到這裡。\n{SITE}")
        sys.exit(0 if ok else 1)

    def due(key, hours):
        last = state.get("last", {}).get(key)
        return last is None or now - dt.datetime.fromisoformat(last) >= dt.timedelta(hours=hours)

    # 即時資料：異常偵測
    rt = None
    if GROWATT_TOKEN:
        try:
            rt = growatt("queryLastData")["sph-s"][0]
        except Exception as e:
            print(f"::warning::Growatt 即時資料讀取失敗：{e}")
    f = lambda k: float((rt or {}).get(k) or 0)
    if rt:
        age = (now - dt.datetime.fromtimestamp(rt["calendar"] / 1000, C.TW_TZ)).total_seconds() / 60
        soc, vbat, dis = int(f("soc")), f("vbat"), f("pdischarge1") / 1000
        if age > 30 and due("offline", COOLDOWN_H["offline"]):
            msgs.append(("offline", f"⚠️ 逆變器已經 {age:.0f} 分鐘沒有回傳資料，請檢查逆變器與採集器網路。\n{SITE}"))
        if 0 < vbat < 48.5 and dis > 0.05 and due("lowv", COOLDOWN_H["lowv"]):
            msgs.append(("lowv", f"🔴 電池電壓 {vbat:.1f} V（顯示 {soc}%），接近 48 V 會直接歸零。"
                                 f"建議把放電下限調高到 {soc}% 左右，讓台電接手。"))
        if soc >= 99 and 0 < vbat < 54 and dis < 0.3 and due("calib", COOLDOWN_H["calib"]):
            msgs.append(("calib", f"🔧 電池顯示 {soc}%，但電壓只有 {vbat:.1f} V（真正充滿約 55 V），BMS 電量偏高，建議校正讓太陽能繼續充。"))

    # 預報 (早上、晚上才需要)
    morning = dt.time(6, 30) <= now.time() < dt.time(8, 30) and state.get("morning") != today
    evening = dt.time(19, 30) <= now.time() < dt.time(21, 30) and state.get("evening") != today
    hourly, days = (pv_forecast.forecast(soc7={now.date(): f("soc")} if rt else {})
                    if morning or evening else (None, None))
    day_load = sum(pv_forecast.LOAD_PROFILE.values())

    if morning:
        s = days.get(now.date())
        if s:
            full = s.get("full_at")
            batt = (f"電池目前 {f('soc'):.0f}%" + ("" if f("soc") >= 98
                    else f"，預估 {full:%H:%M} 充滿" if full is not None and full.hour >= 7
                    else f"，白天最高約 {s['peak_soc']:.0f}%")) if rt else ""
            lines = [f"☀️ 早安！今天預估發電約 {s['pv']:.0f} 度（西 {s['PV1']:.0f}／東 {s['PV3']:.0f}）",
                     batt, C.day_advice(s, "今天", day_load), yesterday_review(now)]
            try:
                daily = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"])
                fc = pd.read_csv(ROOT / "data" / "forecast.csv", parse_dates=["date"])
                al = C.string_ratio_alerts(daily[daily["source"] != "plant"], fc)
                al = al[al["date"] >= daily["date"].max() - pd.Timedelta(days=1)]
                if len(al) >= 2:
                    lines.append("🩺 最近兩天東西兩串發電比例跟預測差超過 15%，可能有遮蔭、髒污或接頭鬆脫。")
            except Exception as e:
                print(f"::warning::兩串比例檢查失敗：{e}")
            msgs.append(("morning", "\n".join(x for x in lines if x)))

    if evening and rt:
        out = C.battery_outlook(pd.Timestamp(now.replace(tzinfo=None)), f("soc"), FLOOR, hourly)
        if out["empty_at"] is not None:
            msgs.append(("evening", f"🌙 今晚電池預計 {out['empty_at']:%H:%M} 降到放電下限 {FLOOR}%，"
                                    f"之後到天亮約需台電供應 {out['grid_kwh']:.1f} 度（台電會自動接手，不用處理）。"))
        else:
            state["evening"] = today   # 撐得到就不發訊息，今天不再檢查

    # 月報：每月 1 日早上
    if now.day == 1 and now.hour >= 8 and state.get("monthly") != now.strftime("%Y-%m"):
        try:
            daily = pd.read_csv(ROOT / "data" / "daily.csv", parse_dates=["date"])
            prev = (now.replace(day=1) - dt.timedelta(days=1))
            m = daily[(daily["source"] != "plant") & (daily["date"].dt.year == prev.year) & (daily["date"].dt.month == prev.month)]
            if len(m):
                saved = C.monthly_savings(m["load"].sum(), m["buy"].sum(), prev.month, len(m), prev.day)[0]
                normal = C.CLIMATE_DAILY[prev.month]
                avg = m["pv"].mean()
                self_r = (1 - m["buy"].sum() / m["load"].sum()) * 100
                msgs.append(("monthly", f"📋 {prev.month} 月報告\n發電 {m['pv'].sum():,.0f} 度（平均每天 {avg:.1f} 度，"
                                        f"比正常年{'好' if avg >= normal else '少'} {abs(avg / normal - 1) * 100:.0f}%）\n"
                                        f"用電 {m['load'].sum():,.0f} 度，自給率 {self_r:.0f}%\n省下電費約 {saved:,.0f} 元\n{SITE}"))
        except Exception as e:
            print(f"::warning::月報失敗：{e}")

    for key, text in msgs:
        if sent_today["n"] >= MAX_PER_DAY:
            print(f"今天已發 {MAX_PER_DAY} 則，略過：{key}")
            continue
        if line_send(text):
            sent_today["n"] += 1
            state.setdefault("last", {})[key] = now.isoformat(timespec="minutes")
            if key in ("morning", "evening"):
                state[key] = today
            if key == "monthly":
                state["monthly"] = now.strftime("%Y-%m")
    if not msgs:
        print("沒有需要通知的事")
    state["sent_today"] = sent_today
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    if not LINE_TOKEN:
        sys.exit(1)


if __name__ == "__main__":
    main()
