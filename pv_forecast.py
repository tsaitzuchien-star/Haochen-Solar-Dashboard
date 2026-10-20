"""明日發電預測：Open-Meteo ECMWF 短波輻射 → 東西坡板面日照 → 三組 PV 可發功率 → 電池充滿時間與限發估計。

參數由本站 2026/7~10 的實測資料推得 (電池每 1% 約 0.7 kWh、充電上限約 5 kW、早上 7 點電量中位數約 74%)，
系統效率 PR 以 430 個未限發小時的實測與 ECMWF 輻射比對校正為 0.70。
"""
import numpy as np
import pandas as pd
import requests

LAT, LON = 23.927693, 120.694682
TILT = 15.0                 # 屋頂坡度 (度)
PANEL_KWP = 0.445           # 單片容量 (24 片 = 10.68 kWp)
# 各組板面朝向片數：(東坡片數, 西坡片數)；正面朝東
STRING_PANELS = {"PV1": (0, 8), "PV2": (4, 4), "PV3": (8, 0)}
PR = 0.70                   # 系統效率：以 2026/7~10 未限發時段的實測與 ECMWF 輻射比對校正
TEMP_COEF = -0.0035         # 每 °C 功率變化
INVERTER_KW = 10.0
BATT_KWH_PER_PCT = 0.70
BATT_MAX_CHARGE_KW = 5.0
BATT_FULL_SOC = 99
# 實測各小時平均家中用電 (kW)
LOAD_PROFILE = {0: 0.91, 1: 0.80, 2: 0.74, 3: 0.68, 4: 0.66, 5: 0.65, 6: 0.65, 7: 0.75, 8: 0.74, 9: 0.89, 10: 1.30,
                11: 1.45, 12: 1.74, 13: 1.33, 14: 1.29, 15: 1.22, 16: 1.44, 17: 1.37, 18: 1.31, 19: 1.33, 20: 1.46,
                21: 1.53, 22: 1.53, 23: 1.10}

API = "https://api.open-meteo.com/v1/forecast"


def fetch_weather(days=2):
    """今天與明天的逐時預報 (ECMWF)。輻射為「前一小時平均」，時間標記在該小時結束時。"""
    r = requests.get(API, timeout=20, params={
        "latitude": LAT, "longitude": LON, "timezone": "Asia/Taipei", "forecast_days": days, "models": "ecmwf_ifs",
        "hourly": "shortwave_radiation,direct_radiation,diffuse_radiation,temperature_2m,cloud_cover,precipitation",
    })
    r.raise_for_status()
    h = r.json()["hourly"]
    df = pd.DataFrame(h).rename(columns={"shortwave_radiation": "ghi", "direct_radiation": "bhi",
                                         "diffuse_radiation": "dhi", "temperature_2m": "temp"})
    df["time"] = pd.to_datetime(df["time"])
    return df.fillna(0)


def _sun(times):
    """太陽天頂角與方位角 (度，方位角北=0 順時針)。"""
    t = pd.to_datetime(pd.Series(times))
    doy = t.dt.dayofyear.to_numpy()
    hours = (t.dt.hour + t.dt.minute / 60).to_numpy()
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365)
    b = 2 * np.pi * (doy - 81) / 364
    eot = 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)
    ha = np.radians(15 * (hours + (4 * (LON - 120) + eot) / 60 - 12))
    lat = np.radians(LAT)
    cos_z = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    zen = np.arccos(np.clip(cos_z, -1, 1))
    az = np.degrees(np.arctan2(np.sin(ha), np.cos(ha) * np.sin(lat) - np.tan(decl) * np.cos(lat))) + 180
    return np.degrees(zen), az


def plane_irradiance(df, azimuth):
    """水平面日照 → 斜面日照 (直射 + 等向散射 + 地面反射)。"""
    zen, saz = _sun(df["time"] - pd.Timedelta(minutes=30))  # 取該小時中點的太陽位置
    z, b = np.radians(zen), np.radians(TILT)
    cos_i = np.cos(z) * np.cos(b) + np.sin(z) * np.sin(b) * np.cos(np.radians(saz - azimuth))
    beam = np.where(np.cos(z) > 0.087, df["bhi"] * np.clip(cos_i, 0, None) / np.cos(z), 0)
    return beam + df["dhi"] * (1 + np.cos(b)) / 2 + df["ghi"] * 0.2 * (1 - np.cos(b)) / 2


def forecast(df=None, start_soc=74):
    """回傳 (逐時預測表, 每日摘要)。逐時表含三組 PV、總功率、家用、充電、限發 (kW = 該小時 kWh)。"""
    df = (fetch_weather() if df is None else df).copy()
    poa = {"E": plane_irradiance(df, 90), "W": plane_irradiance(df, 270)}
    for side in ("E", "W"):
        cell = df["temp"] + poa[side] * 0.03
        df[f"kw_{side}"] = PANEL_KWP * poa[side] / 1000 * PR * (1 + TEMP_COEF * (cell - 25))
    for name, (e, w) in STRING_PANELS.items():
        df[name] = (e * df["kw_E"] + w * df["kw_W"]).clip(lower=0)
    df["pv"] = df[list(STRING_PANELS)].sum(axis=1).clip(upper=INVERTER_KW)
    df["load"] = df["time"].dt.hour.map(lambda h: LOAD_PROFILE[(h - 1) % 24])  # 該小時 (前一小時區間) 的用電

    summary = {}
    out = []
    for day, g in df.groupby(df["time"].dt.date):
        soc, full_at = float(start_soc), None
        rows = []
        for r in g.itertuples():
            home = min(r.pv, r.load)
            surplus = r.pv - home
            room = max(BATT_FULL_SOC - soc, 0) * BATT_KWH_PER_PCT
            charge = min(surplus, BATT_MAX_CHARGE_KW, room)
            soc += charge / BATT_KWH_PER_PCT
            # 夜間電池供電 (太陽能不足的部分)
            soc -= max(r.load - r.pv, 0) / BATT_KWH_PER_PCT if r.time.hour >= 7 else 0
            if full_at is None and soc >= BATT_FULL_SOC - 0.5:
                full_at = r.time - pd.Timedelta(minutes=30)
            rows.append({"time": r.time, "pv_home": home, "pv_charge": charge, "lost": surplus - charge, "soc": soc})
        sim = pd.DataFrame(rows)
        out.append(sim)
        summary[day] = {
            "pv": float(g["pv"].sum()), **{n: float(g[n].sum()) for n in STRING_PANELS},
            "home": float(sim["pv_home"].sum()), "charge": float(sim["pv_charge"].sum()), "lost": float(sim["lost"].sum()),
            "full_at": full_at, "ghi": float(g["ghi"].sum() / 1000), "cloud": float(g.loc[g["ghi"] > 50, "cloud_cover"].mean() or 0),
            "rain": float(g["precipitation"].sum()),
            "best_hours": [t.hour - 1 for t in sim.loc[sim["lost"] > 0.5, "time"]],
        }
    return df.merge(pd.concat(out), on="time"), summary
