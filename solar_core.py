"""儀表板與每日存檔共用的資料轉換與分析邏輯 (不依賴 Streamlit)。"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

TW_TZ = timezone(timedelta(hours=8))
CAPACITY_KWP = 10.68
LAT, LON = 23.87, 120.85  # 南投


def grid_buy_kw(meter_w, load_w, pv_w, dis_w, chg_w):
    """能量平衡推算的購電 (kW)：逆變器輸出 − 太陽能 − 電池放電 + 電池充電；0.1 kW 以下視為量測誤差。
    注意 plocalLoadTotal 是「逆變器輸出」，台電直接供給家裡的電不算在內，所以這個值只抓得到經過逆變器的少量購電，
    台電接手供電時要靠 grid_rate_kw 從購電累計量推算。"""
    if meter_w > 0:
        return meter_w / 1000
    bal = (load_w - pv_w - dis_w + chg_w) / 1000
    return bal if bal > 0.1 else 0.0


def solar_surplus(pv_w, chg_w):
    """太陽能正在幫電池充電 (充電 ≥ 0.1 kW 且太陽能大於充電量)：代表太陽能已足夠家用還有剩，台電不可能在供電。
    (時間電價設定的台電充電只在電量低於 30% 時發生，那時太陽能會小於充電量，不會被誤判)"""
    return chg_w >= 100 and pv_w > chg_w


def grid_rate_kw(times, eto, now_t):
    """由今日購電累計 (etoUserToday，每 0.1 kWh 跳一格) 推算目前的台電供電功率 (kW)。
    這台的即時購電欄位與 CT 欄位恆為 0，這是唯一可靠的來源。做法是量「每跳一格之間隔多久」：
    取最近一次跳格，往回找到至少差 0.2 kWh 且相隔 15 分鐘以上 (最多回看 60 分鐘) 的跳格，平均功率 = 度數差 ÷ 時間差；
    如果最近一次跳格之後已經過了比預期更久，代表用電變小，功率再往下修；超過預期間隔兩倍就視為台電已停止供電。"""
    steps = [(times[i], eto[i]) for i in range(1, len(eto)) if eto[i] > eto[i - 1] + 1e-6]
    if not steps:
        return 0.0
    t_last, e_last = steps[-1]
    since = (now_t - t_last).total_seconds() / 3600
    if since > 1:
        return 0.0
    rate = 0.0
    for t_k, e_k in reversed(steps[:-1]):
        hrs = (t_last - t_k).total_seconds() / 3600
        if hrs > 1:
            break
        if hrs > 0:
            rate = (e_last - e_k) / hrs
        if e_last - e_k >= 0.2 - 1e-6 and hrs >= 0.25:   # 至少 0.2 kWh 且跨 15 分鐘，平均掉 0.1 kWh 一格的跳動
            break
    if rate and since > 2 * 0.1 / rate:      # 超過平常兩倍的時間都沒再跳格：台電已經停止供電
        return 0.0
    if rate and since > 0.1 / rate:          # 該跳格的時間到了卻還沒跳：用電變小了
        rate = 0.1 / since
    return rate


def rows_to_curve(rows):
    """Growatt queryHistoricalData 的 datas → 5 分鐘資料表 (kW / % / V / °C)，並補上台電供電、充電功率與電量跳動的修正。"""
    g = lambda x, k: float(x.get(k) or 0)
    df = pd.DataFrame([{
        "time": datetime.fromtimestamp(x["calendar"] / 1000, TW_TZ).replace(tzinfo=None),
        "pv": g(x, "ppv") / 1000, "load": g(x, "plocalLoadTotal") / 1000,
        "charge": g(x, "pcharge1") / 1000, "discharge": g(x, "pdischarge1") / 1000,
        "buy": grid_buy_kw(g(x, "pacToUserTotal"), g(x, "plocalLoadTotal"), g(x, "ppv"), g(x, "pdischarge1"), g(x, "pcharge1")),
        "sell": g(x, "pacToGridTotal") / 1000,
        "soc": g(x, "soc"), "vbat": g(x, "vbat"),
        # 逆變器溫度在 temp1/temp2 (歷史資料的 invTemp 恆為 0)
        "inv_temp": g(x, "temp1"), "inv_temp2": g(x, "temp2"), "bat_temp": g(x, "bmsBatteryTemp"),
        # 歷史資料的 ppv1~3 恆為 0，各組功率以電壓 x 電流計算
        **{f"pv{i}": g(x, f"vpv{i}") * g(x, f"ipv{i}") / 1000 for i in (1, 2, 3)},
        **{f"{k}{i}": g(x, f"{k}{i}") for k in ("vpv", "ipv") for i in (1, 2, 3)},
        "eto_user": g(x, "etoUserToday"),
    } for x in rows])
    if df.empty:
        return df
    df = df.sort_values("time").reset_index(drop=True)
    # 台電供電改由購電累計量推算，家中負載 = 逆變器輸出 + 台電
    t, e = list(df["time"]), list(df["eto_user"])
    rate = pd.Series([grid_rate_kw(t[:i + 1], e[:i + 1], t[i]) for i in range(len(df))])
    rate[[solar_surplus(pv * 1000, ch * 1000) for pv, ch in zip(df["pv"], df["charge"])]] = 0.0
    extra = (rate - df["buy"]).clip(lower=0)
    df["buy"] += extra
    df["load"] += extra
    df["soc_raw"], df["soc"] = df["soc"], clean_soc(df["soc"])
    df["charge"] = [solar_charge_kw(*r) for r in zip(df["charge"], df["discharge"], df["pv"], df["load"] - extra, df["sell"])]
    return df


def solar_charge_kw(charge, discharge, pv, inv_load, sell):
    """電池充電功率：Growatt 的 pcharge1 穩定充電時只算到主機那一組電池的電流 (約 4 組合計的 1/4；
    2026-10-31 RS485 實測：BMS 4 組合計 84 A ≈ 4.5 kW，逆變器同時只報 20 A ≈ 1.1 kW)，
    但電就只有家用、賣電、電池三個去處，所以太陽能扣掉逆變器供給家裡與賣電的部分 x 0.92 (實測轉換效率上限) 就是電池至少收到的電。
    電池在放電時不套用。"""
    if discharge > 0.05 or pv < 0.1:
        return charge
    return max(charge, (pv - inv_load - sell) * 0.92)


def clean_soc(soc):
    """去掉電量讀數的瞬間跳動 (跳一兩筆又回來，例如 60→68→68→59)：與前後 5 筆中位數差超過 4% 的點改用中位數。
    真正的階梯變化 (手動校正、BMS 歸零) 會連續維持，中位數會跟著變，不受影響。"""
    med = soc.rolling(5, center=True, min_periods=1).median()
    return soc.where((soc - med).abs() <= 4, med)


def soc_jumps(df):
    """電量一次跳 8% 以上 (5 分鐘內不可能充放這麼多電)，回傳 (時間, 前, 後, 種類)：
    往上跳 → "full"：最高那顆電芯碰到 SOC-100% 校正電壓 (3.45 V)，BMS 把累積誤差校正回 100%；
    往下跳、原本 85% 以上 / 正在充電 / 白天有太陽能 → "manual"：在 BMS App 寫入容量，BMS 用電壓重估電量；
    其他往下跳 (夜間放電中) → "bms"：BMS 自己重新估算 (例如碰到 SOC-0% 校正電壓 2.90 V)。"""
    d = df["soc"].diff()
    out = []
    for i in d[d.abs() >= 8].index:
        a, b = df["soc"][i - 1], df["soc"][i]
        if b > a:
            kind = "full"
        elif a >= 85 or max(df["charge"][i - 1], df["charge"][i]) > 0.3 or df["pv"][i] > 0.5:
            kind = "manual"
        else:
            kind = "bms"
        out.append((df["time"][i], a, b, kind))
    return out


def sun_shape(times):
    """各時間點的晴空日照形狀 (0~1)，以太陽高度角近似。"""
    t = pd.to_datetime(pd.Series(times))
    doy = t.dt.dayofyear.to_numpy()
    hours = (t.dt.hour + t.dt.minute / 60).to_numpy()
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    b = 2 * np.pi * (doy - 81) / 364
    eot = 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)  # 分鐘
    solar_time = hours + (4 * (LON - 120) + eot) / 60
    ha = np.radians(15 * (solar_time - 12))
    lat = np.radians(LAT)
    sin_el = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    return np.clip(sin_el, 0, None) ** 1.2


def analyze_curve(curve):
    """回傳限發判定、可發電量估計與今日損失。以晴天推估，多雲時會高估。"""
    if curve.empty:
        return None
    c = curve.copy()
    c["shape"] = sun_shape(c["time"])
    hour = c["time"].dt.hour
    c["limited"] = ((hour >= 8) & (hour < 16) & (c["soc"] >= 95) & (c["charge"] < 0.1)
                    & (c["sell"] < 0.05) & (c["pv"] > 0.05))
    # 用未限發、太陽夠高的時段校正系統可發功率
    ref = c[~c["limited"] & (c["shape"] > 0.35) & (c["pv"] > 0.3)]
    k = float(np.quantile(ref["pv"] / ref["shape"], 0.75)) if len(ref) >= 6 else np.nan
    k = min(k, CAPACITY_KWP) if not np.isnan(k) else np.nan
    c["potential"] = c["shape"] * k if not np.isnan(k) else c["pv"]
    c["lost"] = np.where(c["limited"], (c["potential"] - c["pv"]).clip(lower=0), 0.0)
    c["pv_home"] = np.minimum(c["pv"], c["load"])
    c["pv_charge"] = np.clip(np.minimum(c["charge"], c["pv"] - c["pv_home"]), 0, None)
    full = c[c["soc"] >= 97]["time"]
    return {
        "df": c,
        "lost_kwh": float(c["lost"].sum() * 5 / 60),
        "lost_now": float(c["lost"].iloc[-1]) if c["limited"].iloc[-1] else 0.0,
        "full_time": full.min() if not full.empty else None,
        "calibrated": not np.isnan(k),
    }


# 下載 / 存檔用的 CSV 欄位 (內部名稱 → 中文欄名)
CSV_COLS = {"time": "時間", "pv": "總發電kW", "pv1": "PV1西坡kW", "vpv1": "PV1電壓V", "ipv1": "PV1電流A",
            "pv3": "PV3東坡kW", "vpv3": "PV3電壓V", "ipv3": "PV3電流A",
            "pv2": "PV2kW(改線前東西混串)", "vpv2": "PV2電壓V", "ipv2": "PV2電流A", "load": "用電kW", "charge": "充電kW",
            "discharge": "放電kW", "buy": "買電kW", "sell": "賣電kW", "soc": "電池%", "soc_raw": "電池%(原始讀數)", "vbat": "電池電壓V"}

# 每日摘要 (data/daily.csv) 的欄位；kWh 以 5 分鐘資料積分
DAILY_COLUMNS = ["date", "pv", "pv1", "pv2", "pv3", "load", "buy", "sell", "charge", "discharge", "lost",
                 "peak_kw", "soc_min", "soc_max", "vbat_min", "points", "source",
                 "vmp1", "vmp3", "vmp_diff", "voc1", "voc3", "vmax"]


def daily_summary(curve, source="growatt"):
    """一天的 5 分鐘資料 → 每日摘要 (kWh 以實際時間間隔積分，缺資料的空檔不補)。"""
    c = curve.sort_values("time").reset_index(drop=True)
    hrs = c["time"].diff().dt.total_seconds().div(3600).clip(upper=0.25).fillna(5 / 60)
    kwh = lambda col: round(float((c[col] * hrs).sum()), 2) if col in c else ""
    ana = analyze_curve(c)
    vb = c["vbat"][c["vbat"] > 30] if "vbat" in c else pd.Series(dtype=float)
    return {
        "date": c["time"].iloc[0].date().isoformat(),
        **{k: kwh(k) for k in ("pv", "pv1", "pv2", "pv3", "load", "buy", "sell", "charge", "discharge")},
        "lost": round(ana["lost_kwh"], 2) if ana and ana["calibrated"] else "",
        "peak_kw": round(float(c["pv"].max()), 2),
        "soc_min": int(c["soc"].min()), "soc_max": int(c["soc"].max()),
        "vbat_min": round(float(vb.min()), 1) if len(vb) else "",
        "points": len(c), "source": source,
        **string_health(c),
    }


# 改線後配置在這裡的「正常年」：過去 10 年 (2016–2025) ERA5 天氣套用東西兩坡模型、以 7–10 月實測校正，各月平均每天發電 (kWh)
CLIMATE_DAILY = {1: 28.2, 2: 32.7, 3: 38.1, 4: 41.8, 5: 40.4, 6: 39.3, 7: 39.7, 8: 36.5, 9: 35.8, 10: 32.6, 11: 28.8, 12: 26.2}

# 電池：4 組 EVE LF314 16 串，BMS 標稱容量 323 Ah (2026-10-31 RS485 讀 JK BMS 確認)
BATT_PACKS, BATT_AH, BATT_NOM_V = 4, 323, 51.2
BATT_KWH_PER_PCT = BATT_PACKS * BATT_AH * BATT_NOM_V / 1000 / 100   # ≈ 0.66 kWh (整組約 66 kWh)
DISCHARGE_EFF = 0.95         # 電池 → 逆變器 → 家裡
CHARGE_EFF = 0.92            # 太陽能 → 電池 (實測)
MAX_CHARGE_KW = 5.0


def battery_outlook(now, soc, floor, hourly, load_scale=1.0):
    """從現在模擬到明天早上太陽能接手，估計電池會不會降到放電下限。

    hourly: 逐時預測表 (time = 該小時結束、pv / load 為該小時平均 kW，load 是平常的用電曲線)。
    回傳 {"empty_at": 降到放電下限的時間或 None, "until": 模擬結束 (太陽能足夠家用的時間), "soc_end", "soc_min", "grid_kwh": 下限後需台電供應}。"""
    soc_now, soc_min, empty_at, grid_kwh, until, hours = float(soc), float(soc), None, 0.0, None, 0
    # 要撐到的那個早上：中午以前 (例如過了午夜) 是今天早上，否則是明天早上
    tomorrow = now.normalize() if now.hour < 12 else (now + pd.Timedelta(days=1)).normalize()
    for r in hourly[hourly["time"] > now].itertuples():
        start = r.time - pd.Timedelta(hours=1)
        frac = min(1.0, (r.time - now).total_seconds() / 3600)
        # 「最近用電比平常高 / 低」只反映眼前 (例如正在開冷氣)：前 2 小時照比例，之後 4 小時內慢慢回到平常
        k = max(0.0, min(1.0, (hours - 2) / 4))
        load = r.load * (load_scale * (1 - k) + k)
        # 明天早上太陽能已經夠家裡用：電池任務結束
        if start >= tomorrow + pd.Timedelta(hours=5) and r.pv >= load:
            until = start
            break
        hours += 1
        net = (load - r.pv) * frac
        if net > 0:
            need = net / DISCHARGE_EFF / BATT_KWH_PER_PCT
            avail = max(soc_now - floor, 0)
            if need > avail:
                if empty_at is None:
                    empty_at = start + pd.Timedelta(hours=(1 - frac) + frac * avail / need)
                grid_kwh += (need - avail) * BATT_KWH_PER_PCT * DISCHARGE_EFF
            soc_now -= min(need, avail)
        else:
            soc_now = min(100.0, soc_now + min(-net * CHARGE_EFF, MAX_CHARGE_KW * frac) / BATT_KWH_PER_PCT)
        soc_min = min(soc_min, soc_now)
        if start >= tomorrow + pd.Timedelta(hours=12):
            break
    return {"empty_at": empty_at, "until": until, "soc_end": soc_now, "soc_min": soc_min, "grid_kwh": grid_kwh, "hours": hours}


# 台電表燈非營業用 (累進，2025/10/1 起)：每月級距上限 (度) 與每度電價；兩個月抄表一次時級距加倍。
# 6/1–9/30 為夏月。實際帳單 95 度、102 度都收 200 元 → 每期 (兩個月) 最低 200 元
TARIFF_TIERS = [120, 330, 500, 700, 1000, float("inf")]
TARIFF_RATES = {"summer": [1.78, 2.55, 3.80, 5.14, 6.44, 8.86], "other": [1.78, 2.26, 3.13, 4.24, 5.27, 7.03]}
MIN_BILL_PER_MONTH = 100


def tiered_cost(kwh, month):
    """一個月用 kwh 度的累進電費 (元，不含最低電費)。"""
    rates = TARIFF_RATES["summer" if 6 <= month <= 9 else "other"]
    cost, low = 0.0, 0
    for top, rate in zip(TARIFF_TIERS, rates):
        if kwh <= low:
            break
        cost += (min(kwh, top) - low) * rate
        low = top
    return cost


def monthly_savings(load_kwh, buy_kwh, month, days, month_days):
    """這個月 (可能還沒過完) 太陽能省下的電費：沒有太陽能時全部向台電買的電費 − 實際電費。
    沒過完的月份先依天數換算成整月再計算級距，最後按比例換回來。回傳 (省下元, 沒太陽能的電費, 實際電費)。"""
    k = month_days / max(days, 1)
    without = tiered_cost(load_kwh * k, month)
    actual = max(tiered_cost(buy_kwh * k, month), MIN_BILL_PER_MONTH)
    return (without - actual) / k, without / k, actual / k


def string_ratio_alerts(daily, forecasts, tol=0.15, min_kwh=15):
    """東西兩串比例異常：改線後每天的 PV1 / PV3 實測比 ÷ 當天預測比，偏離超過 tol 且發電夠多的日子。
    回傳 DataFrame (date, ratio = 實測比 / 預測比)。"""
    if forecasts.empty:
        return pd.DataFrame(columns=["date", "ratio"])
    d = daily.merge(forecasts[["date", "pv1", "pv3"]], on="date", suffixes=("", "_fc"))
    d = d[(d["date"] >= pd.Timestamp("2026-10-22")) & (d["pv"] >= min_kwh) & (d["pv3"] > 0) & (d["pv3_fc"] > 0)]
    d = d.assign(ratio=(d["pv1"] / d["pv3"]) / (d["pv1_fc"] / d["pv3_fc"]))
    return d.loc[(d["ratio"] - 1).abs() > tol, ["date", "ratio"]]


def day_advice(s, label, day_load):
    """依某天的發電預測摘要 (pv_forecast.forecast 的 summary) 給用電建議 (含 <b> 標記)。day_load: 家裡一天約用幾度。"""
    hrs = s["best_hours"]
    if hrs:
        best = (f"💡 {label} <b>{hrs[0]}:00–{hrs[-1] + 1}:00</b> 電池會充滿、太陽能用不完（約浪費 {s['lost']:.0f} 度）："
                "適合安排洗衣、烘衣、熱水器、冷氣預冷，或電動車充電")
    elif s["pv"] >= day_load + 8:
        best = (f"☀️ {label}發電約 {s['pv']:.0f} 度，比家裡一天用電（約 {day_load:.0f} 度）多：大電器盡量排在 <b>10:00–14:00</b> 太陽最強時使用，"
                "直接用太陽能，不經過電池，損耗最少")
    elif s["pv"] >= day_load * 0.6:
        best = (f"⛅ {label}發電約 {s['pv']:.0f} 度，大約只夠白天用加上補一點電池：洗衣、熱水器等集中在 <b>11:00–13:00</b>，"
                "其他時間避免同時開多個大電器")
    else:
        short = day_load - s["pv"] * 0.9
        best = (f"🌧️ {label}發電只有約 {s['pv']:.0f} 度，家裡一天約用 {day_load:.0f} 度，差的約 <b>{short:.0f} 度</b>會先用電池，"
                "電池到放電下限後台電自動接手。你家是累進電價（沒有離峰優惠），<b>不建議先用台電幫電池充電</b>"
                "（多轉一手約損耗 15%）；能延後的大電器（烘衣、洗碗機）可以等晴天再用")
    return best


INSTALL_DATE = datetime(2024, 11, 19).date()   # 開始發電 (Growatt 電站第一天有資料)


def runtime(today):
    """從啟用到今天的 (年, 月, 日, 總天數)。"""
    from dateutil.relativedelta import relativedelta
    r = relativedelta(today, INSTALL_DATE)
    return r.years, r.months, r.days, (today - INSTALL_DATE).days


# 原廠即時資料的故障 / 警告碼：正常時全部是 0
# (sysFaultWord* 平常就有非 0 的固定值，看起來是字元編碼，不是故障，不列入)
FAULT_KEYS = {"faultCode": "故障碼", "faultBitCode": "故障位元", "systemFault": "系統故障",
              "systemWarn": "系統警告", "warnCode": "警告碼", "warnCode1": "警告碼1", "warnCode2": "警告碼2", "warnCode3": "警告碼3"}


def fault_codes(rt):
    """回傳非 0 的故障 / 警告碼 [(欄位, 中文名稱, 數值)]；全部正常時是空 list。"""
    out = []
    for k, name in FAULT_KEYS.items():
        try:
            v = int(float(rt.get(k) or 0))
        except (TypeError, ValueError):
            continue
        if v:
            out.append((k, name, v))
    return out


def lifetime_stats(rt, bat_kwh=66.0):
    """原廠累計總量 (安裝至今) → 自給率、電池等效循環次數、來回效率；欄位沒有值時回傳 None。"""
    g = lambda k: float(rt.get(k) or 0)
    pv, buy, sell = g("epvTotal"), g("etoUserTotal"), g("etoGridTotal")
    # 用電 = 逆變器累計輸出 + 台電；原廠 elocalLoadTotal 會多算，只在沒有 eacTotal 時備用
    load = g("eacTotal") + buy if g("eacTotal") > 0 else g("elocalLoadTotal")
    chg, dis = g("echarge1Total"), g("edischarge1Total")
    if pv <= 0 or load <= 0:
        return None
    return {"pv": pv, "load": load, "buy": buy, "sell": sell, "charge": chg, "discharge": dis,
            "self_rate": 1 - buy / load,
            "cycles": dis / bat_kwh,
            "round_trip": dis / chg if chg > 0 else None,
            "bat_loss": chg - dis}


# 元晶 TSEC TS60-BMH-445 H1 銘牌 (STC)；PV1 西坡、PV3 東坡各 12 片串聯，每片 3 顆旁路二極體
PANEL = {"voc": 41.05, "isc": 13.56, "vmp": 34.01, "imp": 13.11, "n": 12, "diodes": 3}
INV_MAX_DC_V = 525          # 逆變器最大輸入電壓
INV_MPPT_MIN_V = 150        # 啟動電壓 / MPPT 下限


def string_health(curve):
    """5 分鐘資料 → 兩串板子的工作點檢查 (逆變器沒有 I-V 掃描，用 MPPT 工作點近似)。

    vmp1 / vmp3：中午 10:30–13:30 兩串都有明顯電流時的工作電壓中位數。兩串同樣 12 片、同時間溫度接近，
      電壓應該幾乎一樣；一顆旁路二極體導通 (局部遮蔭或故障) 少約 1/36 ≈ 2.8%，整片沒作用少約 1/12 ≈ 8%。
    voc1 / voc3：清晨電流還是零點偏移 (≤ 0.5 A) 時的最高電壓，接近開路電壓 (日照弱所以比銘牌低，看自己的長期趨勢)。
    vmax：全天最高 PV 電壓 (不能超過逆變器 525 V)。"""
    out = {k: "" for k in ("vmp1", "vmp3", "vmp_diff", "voc1", "voc3", "vmax")}
    if curve is None or curve.empty or "vpv1" not in curve:
        return out
    c = curve
    h = c["time"].dt.hour + c["time"].dt.minute / 60
    noon = c[(h >= 10.5) & (h <= 13.5) & (c["ipv1"] > 3) & (c["ipv3"] > 3)]
    if len(noon) >= 6:
        v1, v3 = float(noon["vpv1"].median()), float(noon["vpv3"].median())
        out.update(vmp1=round(v1, 1), vmp3=round(v3, 1), vmp_diff=round((v1 / v3 - 1) * 100, 1))
    dawn = c[(h < 9) & (h >= 4)]
    for i in (1, 3):
        v = dawn[dawn[f"ipv{i}"] <= 0.5][f"vpv{i}"]
        if len(v) and v.max() > INV_MPPT_MIN_V:
            out[f"voc{i}"] = round(float(v.max()), 1)
    out["vmax"] = round(float(c[["vpv1", "vpv3"]].max().max()), 1)
    return out


def panel_alerts(daily, tol=5.0):
    """最近兩天中午兩串工作電壓都差超過 tol%：可能有一片板子、接頭或旁路二極體出問題。回傳說明文字或 None。"""
    if "vmp_diff" not in daily:
        return None
    d = daily.dropna(subset=["vmp_diff"]).sort_values("date").tail(2)
    if len(d) < 2 or not (d["vmp_diff"].abs() > tol).all():
        return None
    low = "西坡 PV1" if d["vmp_diff"].iloc[-1] < 0 else "東坡 PV3"
    pct = abs(float(d["vmp_diff"].iloc[-1]))
    n = pct / (100 / PANEL["n"])
    return (f"{low} 中午工作電壓連續兩天比另一串低 {pct:.1f}%（約 {n:.1f} 片板子的電壓），"
            "可能有一片板子、接頭或旁路二極體異常，或有固定遮蔭")


# 電池 BMS 人工檢查紀錄 (筆電接電池 RS485C 旁聽 JK BMS)；樹莓派長期記錄上線後改成自動
BMS_CHECK = {
    "date": "2026-10-31",
    "packs": 4, "cells": 64,
    "delta_mv_max": 5,              # 單體最大壓差
    "temp_range": (28.3, 30.7),     # 電芯 / MOS 溫度範圍 °C
    "soh": 100,
    "charge_a": (19.5, 22.4),       # 中午充電時各組電流 (4 組合計約 84 A)
    "settings": "4 組一致：充電 2–55°C、放電 ≤60°C、RCV 3.50 V、SOC-100% 3.45 V、均衡 3.40 V 起",
}


# 電熱水器 (鑫司 KS-30S：4 kW、113 公升) 的加熱時機：中午電池快滿、太陽能有剩時加熱，等於把多的太陽能存成熱水
HEATER_KW = 4.0


def heater_advice(hour, soc, pv_kw, load_kw, lost_now=0.0):
    """回傳 (等級, 文字)。等級：go / keep / stop / topup / wait / night。
    load_kw 含熱水器；用電比平常多 ≥ 3.7 kW 視為熱水器正在加熱。lost_now = 電池滿了被限發、其實還發得出來的功率。"""
    running = load_kw >= HEATER_KW * 0.85 + 0.3
    base = load_kw - HEATER_KW if running else load_kw
    surplus = pv_kw + lost_now - base           # 不算熱水器時，太陽能比家裡用電多出來的
    if not 9 <= hour < 16:
        return "night", "太陽能不夠，先不要開（需要熱水請手動，或等明天中午）"
    if running:
        if soc >= 85 and surplus >= HEATER_KW * 0.5:
            return "keep", f"正在加熱，太陽能多 {surplus:.1f} kW 夠用，繼續"
        return "stop", f"正在加熱但太陽能只多 {max(surplus, 0):.1f} kW，大部分在用電池，建議關掉"
    if soc >= 95 and surplus >= 3.0:
        return "go", f"現在適合加熱：電池 {soc}%、太陽能多 {surplus:.1f} kW"
    if hour >= 15 and soc >= 80:
        return "topup", f"陰天補熱時機：電池還有 {soc}%，今天太陽能不多，現在加熱最划算"
    need = "電池還沒充到 95%" if soc < 95 else f"太陽能只多 {max(surplus, 0):.1f} kW"
    return "wait", f"先等等：{need}（目標：電池 ≥ 95% 且太陽能多 3 kW 以上）"
