"""儀表板與每日存檔共用的資料轉換與分析邏輯。"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

TW_TZ = timezone(timedelta(hours=8))
CAPACITY_KWP = 10.68
LAT, LON = 23.87, 120.85  # 南投


def rows_to_curve(rows):
    """Growatt queryHistoricalData 回傳的 datas → 5 分鐘資料表 (kW / %, °C)。"""
    g = lambda x, k: float(x.get(k) or 0)
    df = pd.DataFrame([{
        "time": datetime.fromtimestamp(x["calendar"] / 1000, TW_TZ).replace(tzinfo=None),
        "pv": g(x, "ppv") / 1000, "load": g(x, "plocalLoadTotal") / 1000,
        "charge": g(x, "pcharge1") / 1000, "discharge": g(x, "pdischarge1") / 1000,
        "buy": g(x, "pacToUserTotal") / 1000, "sell": g(x, "pacToGridTotal") / 1000,
        "soc": g(x, "soc"),
        # 逆變器溫度在 temp1/temp2 (歷史資料的 invTemp 恆為 0)
        "inv_temp": g(x, "temp1"), "inv_temp2": g(x, "temp2"), "bat_temp": g(x, "bmsBatteryTemp"),
        # 歷史資料的 ppv1~3 恆為 0，各組功率以電壓 x 電流計算
        **{f"pv{i}": g(x, f"vpv{i}") * g(x, f"ipv{i}") / 1000 for i in (1, 2, 3)},
        # 逆變器自己的當日累計 (kWh)，比積分更準
        **{k: g(x, k) for k in ("epvToday", "epv1Today", "epv2Today", "epv3Today", "elocalLoadToday",
                                "etoUserToday", "etoGridToday", "echarge1Today", "edischarge1Today")},
    } for x in rows])
    return df.sort_values("time").reset_index(drop=True) if not df.empty else df


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


DAILY_COLUMNS = ["date", "pv", "pv1", "pv2", "pv3", "load", "buy", "sell", "charge", "discharge",
                 "lost", "full_time", "soc_min", "soc_max", "inv_temp_max", "bat_temp_max", "points"]


def daily_summary(curve):
    """一天的 5 分鐘資料 → 每日摘要 (kWh)。"""
    day = curve["time"].iloc[0].date()
    ana = analyze_curve(curve)
    mx = lambda k: round(float(curve[k].max()), 2)
    # 部分機型的 epv1~3Today 可能為 0，改以電壓 x 電流積分
    strings = [mx(f"epv{i}Today") or round(float(curve[f"pv{i}"].sum() * 5 / 60), 2) for i in (1, 2, 3)]
    return {
        "date": day.isoformat(),
        "pv": mx("epvToday"), "pv1": strings[0], "pv2": strings[1], "pv3": strings[2],
        "load": mx("elocalLoadToday"), "buy": mx("etoUserToday"), "sell": mx("etoGridToday"),
        "charge": mx("echarge1Today"), "discharge": mx("edischarge1Today"),
        "lost": round(ana["lost_kwh"], 2) if ana and ana["calibrated"] else "",
        "full_time": ana["full_time"].strftime("%H:%M") if ana and ana["full_time"] is not None else "",
        "soc_min": int(curve["soc"].min()), "soc_max": int(curve["soc"].max()),
        "inv_temp_max": mx("inv_temp"), "bat_temp_max": mx("bat_temp"),
        "points": len(curve),
    }
