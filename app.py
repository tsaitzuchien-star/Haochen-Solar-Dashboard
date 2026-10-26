import importlib
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

import power_flow
import pv_forecast

# Streamlit Cloud 更新程式後只會重跑 app.py，已載入的模組可能還是舊版 (曾因此讀不到新欄位而 KeyError)；
# 每次重跑都重新載入這兩個小模組，確保 app.py 與它們同版
importlib.reload(power_flow)
importlib.reload(pv_forecast)
power_flow_html = power_flow.power_flow_html

# ==========================================
# 1. 頁面設定與樣式
#    全站顏色語意固定：綠=太陽能、橘=用電、紫=電池、藍=台電
# ==========================================
st.set_page_config(page_title="澔宸小窩 - 智慧能源", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh")  # 5 分鐘刷新 (官方資料約每 5 分鐘更新一次)

C_PV, C_LOAD, C_BAT, C_GRID = "#10B981", "#F97316", "#A78BFA", "#38BDF8"
# 人字屋頂、正面朝東：2026/10 起改為東西兩坡各 12 片一串 (PV1 = 西坡、PV3 = 東坡，PV2 停用)
# (逆變器輸入編號, 名稱, 說明, 顏色)；顏色與 3D 屋頂外框一致
STRINGS = [(1, "PV1", "西坡 12 片 · 下午較強", "#3B82F6"), (3, "PV3", "東坡 12 片 · 上午較強", "#F59E0B")]

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
.stApp {background-color: #0F172A; font-family: 'Segoe UI', Roboto, 'Noto Sans TC', sans-serif;}
.block-container {padding-top: 2.2rem; max-width: 1400px;}
.card {background:#1E293B; border:1px solid #334155; border-radius:12px; padding:14px 16px;}
.kpi-label {color:#94A3B8; font-size:13px;}
.kpi-value {color:#F8FAFC; font-size:28px; font-weight:700; line-height:1.25;}
.kpi-unit {color:#64748B; font-size:13px; font-weight:400; margin-left:3px;}
.kpi-sub {color:#64748B; font-size:12px; margin-top:2px;}
.section-title {color:#E2E8F0; font-size:17px; font-weight:700; margin:22px 0 10px;}
.chip {display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px; font-weight:600;}
.grid2 {display:grid; grid-template-columns:1fr 1fr; gap:10px;}
.grid3 {display:grid; grid-template-columns:repeat(3, 1fr); gap:10px;}
@media (max-width: 640px) {.grid3 {grid-template-columns:1fr;}}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p {color:#CBD5E1 !important;}
[data-testid="stRadio"] label p {color:#CBD5E1 !important;}
.stTabs [data-baseweb="tab-list"] {gap: 6px;}
.stTabs [data-baseweb="tab"] {background:#1E293B; border-radius:8px 8px 0 0; padding:8px 16px; color:#CBD5E1;}
</style>
""", unsafe_allow_html=True)

TW_TZ = timezone(timedelta(hours=8))

# ==========================================
# 2. Growatt 官方 OpenAPI (Token 驗證)
#    Token 請放在 Streamlit secrets 或環境變數 GROWATT_TOKEN，切勿寫進程式碼
# ==========================================
def get_secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


TOKEN = get_secret("GROWATT_TOKEN")
DEVICE_SN = get_secret("GROWATT_DEVICE_SN", "KQQ0N6200N")
API_BASE = "https://openapi.growatt.com/"
DEVICE_TYPE = "sph-s"
CAPACITY_KWP = 10.68
LAT, LON = 23.87, 120.85  # 南投

# 溫度判定門檻 (一般參考值，可依原廠規格調整)
INV_TEMP_LEVELS = [(60, "正常", "#2DD4BF"), (70, "偏高", "#F59E0B"), (math.inf, "過高", "#EF4444")]
BAT_TEMP_LEVELS = [(10, "低溫", "#60A5FA"), (35, "正常", "#2DD4BF"), (45, "偏高", "#F59E0B"), (math.inf, "過高", "#EF4444")]

if not TOKEN:
    st.error("⚠️ 尚未設定 GROWATT_TOKEN。請在 Streamlit Cloud → App settings → Secrets 加入：GROWATT_TOKEN = \"你的Token\"")
    st.stop()


def api_v4(endpoint, **data):
    r = requests.post(API_BASE + "v4/new-api/" + endpoint, headers={"token": TOKEN}, data=data, timeout=30)
    r.raise_for_status()
    res = r.json()
    if res.get("code") != 0:
        raise RuntimeError(f"{endpoint}: {res.get('message')}")
    return res["data"]


@st.cache_data(ttl=300)
def fetch_realtime():
    rows = api_v4("queryLastData", deviceSn=DEVICE_SN, deviceType=DEVICE_TYPE)[DEVICE_TYPE]
    if not rows:
        raise RuntimeError("查無即時資料")
    return rows[0]


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


@st.cache_data(ttl=600)
def fetch_day_curve(day):
    rows = api_v4("queryHistoricalData", deviceSn=DEVICE_SN, deviceType=DEVICE_TYPE, date=day)["datas"] or []
    g = lambda x, k: float(x.get(k) or 0)
    df = pd.DataFrame([{
        "time": datetime.fromtimestamp(x["calendar"] / 1000, TW_TZ).replace(tzinfo=None),
        "pv": g(x, "ppv") / 1000, "load": g(x, "plocalLoadTotal") / 1000,
        "charge": g(x, "pcharge1") / 1000, "discharge": g(x, "pdischarge1") / 1000,
        "buy": grid_buy_kw(g(x, "pacToUserTotal"), g(x, "plocalLoadTotal"), g(x, "ppv"), g(x, "pdischarge1"), g(x, "pcharge1")),
        "sell": g(x, "pacToGridTotal") / 1000,
        "soc": g(x, "soc"),
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
    return df


@st.cache_resource
def last_good():
    # 跨使用者共用的「最後一次成功資料」，API 限流 (FREQUENTLY_ACCESS) 時改顯示這份
    return {}


RETRY_COOLDOWN = timedelta(minutes=5)


def with_fallback(key, fn, *args):
    # 失敗後冷卻 5 分鐘再重試，避免每次刷新都打 API 而讓限流一直無法解除
    store = last_good()
    failed_at = store.get(f"{key}:failed_at")
    if failed_at and datetime.now(TW_TZ) - failed_at < RETRY_COOLDOWN:
        if key in store:
            return store[key]
        raise RuntimeError(store[f"{key}:error"])
    try:
        store[key] = fn(*args)
        store.pop(f"{key}:failed_at", None)
    except Exception as e:
        store[f"{key}:failed_at"], store[f"{key}:error"] = datetime.now(TW_TZ), str(e)
        if key not in store:
            raise
        st.toast("Growatt 暫時限流，顯示上一次資料", icon="⏳")
    return store[key]


def api_error_text(e):
    if "FREQUENTLY_ACCESS" in str(e):
        return "Growatt 查詢次數暫時達到上限，約 5 分鐘後會自動恢復"
    if "TOKEN_IS_INVALID" in str(e):
        return "Token 無效，請到 Streamlit → Settings → Secrets 更新 GROWATT_TOKEN"
    return f"資料讀取失敗: {e}"


# ==========================================
# 3. 分析：晴空模型估計「可發電量」，推算限發損失
# ==========================================
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


def temp_level(value, levels):
    for limit, label, color in levels:
        if value < limit:
            return label, color
    return levels[-1][1], levels[-1][2]


def sparkline_svg(values, color, w=240, h=42):
    v = [x for x in values if x > 0]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    span = max(hi - lo, 1.0)
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 4 - (x - lo) / span * (h - 8):.1f}" for i, x in enumerate(v))
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none" style="width:100%;height:{h}px;display:block;">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round"/></svg>')


# ==========================================
# 4. 取得資料
# ==========================================
try:
    s = with_fallback("realtime", fetch_realtime)
except Exception as e:
    st.error(f"⚠️ {api_error_text(e)}")
    st.stop()

now_tw = datetime.now(TW_TZ)
curve_error = None
try:
    curve = with_fallback(f"curve-{now_tw:%Y-%m-%d}", fetch_day_curve, now_tw.strftime("%Y-%m-%d"))
except Exception as e:
    curve, curve_error = pd.DataFrame(), e
ana = analyze_curve(curve)

f = lambda k: float(s.get(k) or 0)
last_update = datetime.fromtimestamp(s["calendar"] / 1000, TW_TZ)
age_min = max(int((now_tw - last_update).total_seconds() // 60), 0)
stale = age_min > 20
pv_kw, load_kw = f("ppv") / 1000, f("plocalLoadTotal") / 1000
p_charge, p_discharge = f("pcharge1") / 1000, f("pdischarge1") / 1000
p_buy = grid_buy_kw(f("pacToUserTotal"), f("plocalLoadTotal"), f("ppv"), f("pdischarge1"), f("pcharge1"))
# 台電供電：用今日購電累計每跳一格的間隔推算，並補進家中負載 (逆變器輸出不含台電直接供給的電)
if not curve.empty and "eto_user" in curve:
    now_naive = last_update.replace(tzinfo=None)
    hist = curve[curve["time"] < now_naive]
    rate = grid_rate_kw(list(hist["time"]) + [now_naive], list(hist["eto_user"]) + [f("etoUserToday")], now_naive)
    if solar_surplus(f("ppv"), f("pcharge1")):   # 太陽能正在幫電池充電：電有剩，台電一定已停止供電 (累計量要等跳格才會反應)
        rate = 0.0
    if rate > p_buy:
        load_kw += rate - p_buy
        p_buy = rate
p_sell = f("pacToGridTotal") / 1000
soc = int(f("soc"))
inv_temp = f("temp1") or f("invTemp")
bat_temp = f("bmsBatteryTemp")
str_kw = [(f(f"ppv{n}") or f(f"vpv{n}") * f(f"ipv{n}")) / 1000 for n, *_ in STRINGS]
# PV 電流：沒在發電 (電壓低於 50 V) 時，電流感測器有固定 0.2~0.3 A 的零點偏移，不是真的有電流，顯示為 0
ipv = lambda n: f(f"ipv{n}") if f(f"vpv{n}") >= 50 else 0.0
str_today = [f(f"epv{n}Today") for n, *_ in STRINGS]
is_limited = (pv_kw > 0.05 and soc >= 95 and p_charge < 0.1 and p_sell < 0.05
              and 8 <= last_update.hour < 16)

# ==========================================
# 5. 頂部狀態列
# ==========================================
if age_min < 1:
    age_text = "剛剛更新"
elif age_min < 60:
    age_text = f"{age_min} 分鐘前更新"
else:
    age_text = f"{age_min // 60} 小時 {age_min % 60} 分鐘前更新"
dot_color, dot_text = ("#F59E0B", "資料延遲") if stale else ("#22C55E", "正常")

# 狀態標籤放在標題同一行、資料時間放在副標題下方，手機上也不會錯位
st.markdown(f"""<div style="padding-bottom:12px; border-bottom:1px solid #334155; margin-bottom:16px;">
  <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
    <span style="color:#F8FAFC; font-size:26px; font-weight:800;">澔宸小窩</span>
    <span class="chip" style="background:{dot_color}22; color:{dot_color};">● {dot_text}</span>
  </div>
  <div style="color:#64748B; font-size:12px; letter-spacing:1px; margin-top:2px;">SPH 10000TL-HU · {CAPACITY_KWP} kWp · 南投</div>
  <div style="color:#94A3B8; font-size:12px; margin-top:6px; font-family:monospace;">
    資料 {last_update:%m/%d %H:%M}（{age_text}）· 網頁 {now_tw:%H:%M:%S}</div>
</div>""", unsafe_allow_html=True)

# ==========================================
# 6. 一句話狀態 + 用電建議
# ==========================================
if stale:
    status = f"⚠️ 已經 {age_text.replace('更新', '')}沒有收到新資料，請檢查採集器網路"
elif is_limited:
    status = "☀️ 電池已充滿，太陽能正被限制輸出 — 現在用電等於免費"
elif pv_kw > 0.05 and p_buy > 0.05:
    status = f"☀️ 太陽能 {pv_kw:.1f} kW 不夠用，台電即時補上 {p_buy:.1f} kW"
elif pv_kw > 0.05 and p_charge > 0.05:
    status = f"☀️ 太陽能供電中，同時為電池充電 {p_charge:.1f} kW（電量 {soc}%）"
elif pv_kw > 0.05:
    status = f"☀️ 太陽能供電中（{pv_kw:.1f} kW）"
elif p_buy > 0.05 and p_buy >= p_discharge:
    status = f"🪫 電池剩 {soc}%，改由台電即時供電 {p_buy:.1f} kW"
elif p_discharge > 0.05:
    status = f"🔋 電池供電中，剩餘 {soc}%，正在提供 {p_discharge:.1f} kW"
elif p_buy > 0.05:
    status = f"🔌 台電即時供電中（{p_buy:.1f} kW）"
elif soc < 95:   # 沒太陽能、電池也沒放電：家裡的電只可能來自台電 (購電累計還沒跳格，暫時算不出 kW)
    status = f"🪫 電池暫停放電（剩 {soc}%），家中用電由台電供應"
else:
    status = "💤 系統待機中"

st.markdown(f'<div style="color:#F1F5F9; font-size:19px; font-weight:600; margin:2px 0 12px;">{status}</div>',
            unsafe_allow_html=True)

# 電池低電壓提醒：這組電池電壓跌破約 48 V 時，BMS 會把電量直接歸零 (不論當時顯示幾 %)，
# 所以接近時提醒提高放電下限 (SOC) 讓台電接手
vbat = f("vbat")
# 只在電池「還在放電」時提醒：電池已停止放電 (台電接手或待機) 就沒有要調整的東西，上面的狀態句已說明
if 0 < vbat < 49 and not stale and p_discharge > 0.05:
    red = vbat < 48.5
    c = "#EF4444" if red else "#F59E0B"
    if vbat < 48:
        msg = f"電池電壓 {vbat:.1f} V，已低於 48 V，電量隨時可能直接歸零，請立即把放電下限 (SOC) 調高到目前電量 {soc}% 以上，讓台電接手供電"
    elif red:
        msg = f"電池電壓 {vbat:.1f} V，快要跌破 48 V，請立即把放電下限 (SOC) 調高到目前電量 {soc}% 附近，讓台電接手供電"
    else:
        msg = f"電池電壓 {vbat:.1f} V 偏低（電量 {soc}%），建議把放電下限 (SOC) 調高一些，避免跌破 48 V 電量歸零"
    st.markdown(f'<div style="background:{c}1F; border:1px solid {c}; color:{c}; border-radius:10px; padding:10px 14px; '
                f'font-size:15px; font-weight:600; margin:-4px 0 12px;">{"🔴" if red else "🟡"} {msg}</div>', unsafe_allow_html=True)

if is_limited and not stale:
    spare = f"，估計還有約 <b>{ana['lost_now']:.1f} kW</b> 用不到" if ana and ana["lost_now"] >= 0.3 else ""
    st.markdown(f"""<div class="card" style="border-color:#10B98166; background:linear-gradient(90deg,#064E3B66,#1E293B); margin-bottom:14px;">
      <div style="color:#6EE7B7; font-weight:700; font-size:15px;">💡 現在很適合用電{spare}</div>
      <div style="color:#CBD5E1; font-size:13px; margin-top:4px;">洗衣機、烘衣機、熱水器、電動車充電、冷氣預冷 —— 這段時間多用 1 度，就少浪費 1 度</div>
    </div>""", unsafe_allow_html=True)

# ==========================================
# 6.5 天氣預報 (預測區與 3D 場景共用)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_pv_forecast(soc7, version):
    return pv_forecast.forecast(soc7=soc7)


def today_soc7():
    """今天早上 7 點的電量：7 點後取 7:00~7:30 的第一筆實測，7 點前以目前電量扣掉到 7 點的預估用電。"""
    seven = now_tw.replace(hour=7, minute=0, second=0, microsecond=0, tzinfo=None)
    if now_tw.hour >= 7:
        after = curve[curve["time"] >= seven] if not curve.empty else curve
        if not after.empty and after["time"].iloc[0] <= seven + timedelta(minutes=30):
            return float(after["soc"].iloc[0]), "actual"
        return None, None
    left = sum(pv_forecast.LOAD_PROFILE[h] for h in range(now_tw.hour, 7)) - pv_forecast.LOAD_PROFILE[now_tw.hour] * now_tw.minute / 60
    return max(soc - left / pv_forecast.BATT_KWH_PER_PCT, pv_forecast.BATT_MIN_SOC), "estimate"


soc7_val, soc7_how = today_soc7()
fc_hourly, fc_days, fc_error = None, None, None
try:
    fc_hourly, fc_days = with_fallback(f"pv_forecast-v{pv_forecast.MODEL_VERSION}", fetch_pv_forecast,
                                       {now_tw.date(): round(soc7_val)} if soc7_val is not None else {},
                                       pv_forecast.MODEL_VERSION)
except Exception as e:
    fc_error = e


def current_weather():
    """3D 場景的天氣：降雨看 ECMWF 這一小時的預報；雲量白天優先用實測發電 ÷ 晴天應有發電判斷 (最接近現場)，
    電池滿被限發或天黑時改用預報的低 / 中 / 高雲。"""
    rain, eff = 0.0, 0.0
    if fc_hourly is not None:
        hr = fc_hourly[fc_hourly["time"] == pd.Timestamp(now_tw.replace(tzinfo=None)).floor("h") + pd.Timedelta(hours=1)]
        if not hr.empty:
            r = hr.iloc[0]
            rain = float(r.get("precipitation", 0) or 0)
            eff = max(float(r.get("cloud_cover_low", 0)), float(r.get("cloud_cover_mid", 0)), 0.35 * float(r.get("cloud_cover_high", 0)))
    shape = float(sun_shape([now_tw.replace(tzinfo=None)])[0])
    if shape > 0.15 and not is_limited and not stale:
        clear_kw = 0.8 * CAPACITY_KWP * shape
        eff = float(np.clip((1 - pv_kw / clear_kw) * 100, 0, 100))
    kind = "rain" if rain >= 0.3 else "overcast" if eff >= 70 else "cloudy" if eff >= 35 else "sunny"
    return {"kind": kind, "cloud": round(eff), "rain": round(rain, 1)}


# ==========================================
# 7. 主畫面：3D 能源流向 | 今日數字 + 溫度
# ==========================================
col_main, col_side = st.columns([1.55, 1], gap="medium")

with col_main:
    is_night = pv_kw < 0.05 and not (6 <= last_update.hour < 18)
    components.html(power_flow_html(pv=pv_kw, load=load_kw, batt=p_charge - p_discharge, soc=soc,
                                    grid=p_buy - p_sell, night=is_night,
                                    strings=[(f"{name} {where[:2]}", color, kw, f(f"vpv{n}"), ipv(n))
                                             for (n, name, where, color), kw in zip(STRINGS, str_kw)],
                                    battery={"v": f("vbat"), "temp": bat_temp or None},
                                    grid_today={"buy": f("etoUserToday"), "sell": f("etoGridToday")},
                                    hour=last_update.hour + last_update.minute / 60, weekend=last_update.weekday() >= 5, doy=last_update.timetuple().tm_yday,
                                    weather=current_weather()), height=520)


def kpi(label, value, unit, color, sub=""):
    return f"""<div class="card" style="border-top:3px solid {color}; height:100%;">
      <div class="kpi-label">{label}</div>
      <div class="kpi-value">{value}<span class="kpi-unit">{unit}</span></div>
      <div class="kpi-sub">{sub}</div></div>"""


def temp_card(title, value, levels, series, unit_color):
    if value <= 0:
        return f'<div class="card"><div class="kpi-label">{title}</div><div class="kpi-value">—</div></div>'
    label, color = temp_level(value, levels)
    peak = ""
    if series is not None and (series > 0).any():
        i = series.idxmax()
        peak = f"今日最高 {series.max():.1f}°C（{curve.loc[i, 'time']:%H:%M}）"
    # 單行輸出：sparkline 為空字串時若留下空白行，Markdown 會把後面的 HTML 當成程式碼區塊顯示
    return (f'<div class="card" style="height:100%;"><div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span class="kpi-label">{title}</span><span class="chip" style="background:{color}22; color:{color};">{label}</span></div>'
            f'<div class="kpi-value" style="color:{color};">{value:.1f}<span class="kpi-unit">°C</span></div>'
            f'{sparkline_svg(series.tolist() if series is not None else [], unit_color)}<div class="kpi-sub">{peak}</div></div>')


with col_side:
    self_rate = (1 - f("etoUserToday") / f("elocalLoadToday")) * 100 if f("elocalLoadToday") > 0 else 0
    lost_text = f"{ana['lost_kwh']:.1f}" if ana and ana["calibrated"] else "—"
    # 用 CSS grid 排版，手機上也維持兩欄
    st.markdown('<div class="grid2">'
                + kpi("☀️ 今日發電", f"{f('epvToday'):.1f}", "kWh", C_PV, f"{f('epvToday') / CAPACITY_KWP:.2f} kWh/kWp")
                + kpi("🏠 今日用電", f"{f('elocalLoadToday'):.1f}", "kWh", C_LOAD, f"買電 {f('etoUserToday'):.1f} kWh")
                + kpi("⚖️ 自給率", f"{self_rate:.0f}", "%", C_GRID, "不靠台電的用電比例")
                + kpi("🗑️ 浪費掉", lost_text, "kWh", "#F59E0B", "電池滿後被限發（晴天推估）")
                + "</div>", unsafe_allow_html=True)

    st.markdown("<div class='section-title' style='margin-top:16px;'>🌡️ 設備溫度</div>", unsafe_allow_html=True)
    st.markdown('<div class="grid2">'
                + temp_card("逆變器", inv_temp, INV_TEMP_LEVELS, curve["inv_temp"] if not curve.empty else None, "#94A3B8")
                + temp_card("電池", bat_temp, BAT_TEMP_LEVELS, curve["bat_temp"] if not curve.empty else None, C_BAT)
                + "</div>", unsafe_allow_html=True)

chart_layout = dict(
    paper_bgcolor="#1E293B", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8"),
    height=380, margin=dict(l=50, r=50, t=40, b=40), hovermode="x unified",
    legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, traceorder="normal", font=dict(color="#E2E8F0")),
)


# ==========================================
# 7.5 發電預測 (Open-Meteo ECMWF 短波輻射)
# ==========================================
st.markdown("<div class='section-title'>🌤️ 天氣與發電預測（ECMWF 短波輻射）</div>", unsafe_allow_html=True)
# 下午 2 點前預設看今天，之後看明天
day_opts = {"今天": now_tw.date(), "明天": (now_tw + timedelta(days=1)).date()}
pick = st.radio("預測日", list(day_opts), index=0 if now_tw.hour < 14 else 1, horizontal=True, label_visibility="collapsed")
tomorrow = day_opts[pick]
fd = fc_days.get(tomorrow) if fc_days else None
if fc_error is not None:
    st.markdown(f'<div class="card" style="color:#94A3B8;">⏳ 天氣預報暫時讀不到（{fc_error}），稍後會自動重試。</div>', unsafe_allow_html=True)

if fd:
    # 天空狀態看會擋光的雲：低雲、中雲照算，高空卷雲很薄只算三成 (總雲量 90% 但全是高雲時，實際仍接近晴天)
    lo, mi, hi = (fd.get(f"cloud_{k}", fd["cloud"]) for k in ("low", "mid", "high"))
    eff = max(lo, mi, hi * 0.35)
    thin = "（高空薄雲）" if hi >= 50 and eff < 65 else ""
    sky = ("☀️ 晴" if eff < 35 else "⛅ 多雲" if eff < 65 else "☁️ 陰") + thin
    sky += f"<span style='color:#64748B'> · 低/中/高雲 {lo:.0f}/{mi:.0f}/{hi:.0f}%</span>"
    rain = f"，預估降雨 {fd['rain']:.0f} mm" if fd["rain"] >= 1 else ""
    full_txt = (f"約 <b>{fd['full_at']:%H:%M}</b> 充滿" if fd["full_at"] is not None
                else f"最高約 <b>{fd['peak_soc']:.0f}%</b><span style='font-size:14px; color:#94A3B8;'>（{fd['peak_at']:%H:%M} 左右）</span>"
                if fd["peak_at"] is not None else f"<b>約 {fd['soc7']:.0f}%</b><span style='font-size:14px; color:#94A3B8;'>（發電剛好夠家裡用，沒有多餘的電可充）</span>")
    if tomorrow == now_tw.date() and ana and ana["full_time"] is not None and ana["full_time"].hour >= 8:
        full_txt = f"已於 <b>{ana['full_time']:%H:%M}</b> 充滿"
    soc7_txt = {
        "actual": f"以今早 7:00 實測電量 {fd['soc7']:.0f}% 估算",
        "carry": f"推估今晚用電後，7 點約剩 {fd['soc7']:.0f}%",
        "default": f"以早上 7 點電量 {fd['soc7']:.0f}%（歷史中位數）估算",
    }[fd["soc7_src"]]
    if fd["soc7_src"] == "actual" and soc7_how == "estimate":
        soc7_txt = f"以目前 {soc}% 扣掉清晨用電，推估 7 點約 {fd['soc7']:.0f}%"
    hrs = fd["best_hours"]
    best = (f"💡 {pick} <b>{hrs[0]}:00–{hrs[-1] + 1}:00</b> 太陽能用不完，適合安排洗衣、烘衣、熱水器、電動車充電"
            if hrs else f"{pick}預估太陽能大多會被家用與電池用完，沒有明顯浪費的時段")
    strings_txt = "　".join(f'<span style="color:{c}">{n}</span> {fd[n]:.1f}' for _, n, _, c in STRINGS)
    st.markdown(f"""<div class="card">
      <div style="display:flex; flex-wrap:wrap; gap:16px 36px; align-items:flex-end;">
        <div><div class="kpi-label">預估可發電量</div><div class="kpi-value" style="color:{C_PV};">{fd['pv']:.0f}<span class="kpi-unit">kWh</span></div>
          <div class="kpi-sub">{sky}{rain} · 日照 {fd['ghi']:.1f} kWh/m²</div></div>
        <div><div class="kpi-label">電池</div><div style="color:#F1F5F9; font-size:20px; margin-top:4px;">{full_txt}</div>
          <div class="kpi-sub">{soc7_txt}</div></div>
        <div><div class="kpi-label">預估浪費</div><div class="kpi-value" style="color:#F59E0B;">{fd['lost']:.0f}<span class="kpi-unit">kWh</span></div>
          <div class="kpi-sub">家用 {fd['home']:.0f} · 充電 {fd['charge']:.0f} kWh</div></div>
        <div><div class="kpi-label">東西兩組預估（kWh）</div><div style="color:#CBD5E1; font-size:14px; margin-top:8px;">{strings_txt}</div></div>
      </div>
      <div style="margin-top:12px; padding-top:10px; border-top:1px solid #334155; color:#E2E8F0; font-size:14px;">{best}</div>
    </div>""", unsafe_allow_html=True)

    t = fc_hourly[fc_hourly["time"].dt.date == tomorrow]
    t = t[(t["time"].dt.hour >= 6) & (t["time"].dt.hour <= 19)]
    tx = t["time"] - pd.Timedelta(minutes=30)
    ff = go.Figure()
    ff.add_trace(go.Bar(x=tx, y=t["pv_home"], name="供應家用", marker_color="#10B981"))
    ff.add_trace(go.Bar(x=tx, y=t["pv_charge"], name="充進電池", marker_color="#A78BFA"))
    ff.add_trace(go.Bar(x=tx, y=t["lost"].where(t["lost"] >= 0.05), name="浪費掉", marker=dict(
        color="rgba(245,158,11,0.35)", line=dict(color="#F59E0B", width=1), pattern=dict(shape="/", fgcolor="#F59E0B", size=6))))
    ff.add_trace(go.Scatter(x=tx, y=t["ghi"], name="短波輻射 W/m²", yaxis="y2", line=dict(color="#FDE68A", width=1.5, dash="dot")))
    ff.update_layout(**{**chart_layout, "height": 280, "margin": dict(l=50, r=50, t=40, b=40)}, barmode="stack", bargap=0.15,
                     xaxis=dict(showgrid=False, tickformat="%H時", dtick=2 * 3600000),
                     yaxis=dict(gridcolor="#334155", title="預估 kWh", rangemode="tozero"),
                     yaxis2=dict(overlaying="y", side="right", showgrid=False, title="W/m²", rangemode="tozero"))
    st.plotly_chart(ff, width="stretch", theme=None)
    st.markdown('<div style="color:#94A3B8; font-size:13px; margin-top:-6px;">資料來源：Open-Meteo ECMWF 逐時短波輻射預報（與中創需量預測同源），'
                "換算到東西坡板面後估算兩組發電；電池與用電依本站實測平均推估。依 7–10 月實測校正，逐時誤差中位數約 10%；午後雷陣雨或雲量多的日子誤差可能達 30% 以上。</div>",
                unsafe_allow_html=True)

# ==========================================
# 8. 今日：太陽能的電去了哪裡
# ==========================================
st.markdown("<div class='section-title'>📈 今天太陽能的電去了哪裡？</div>", unsafe_allow_html=True)
if ana is None or ana["df"]["pv"].sum() * 5 / 60 < 0.1:
    msg = api_error_text(curve_error) if curve_error else "今天太陽還沒出來，日出後這裡會顯示今天的發電去向；可先看上方的今天預測。"
    st.markdown(f'<div class="card" style="text-align:center; color:#94A3B8; padding:28px;">🌙 {msg}</div>', unsafe_allow_html=True)
else:
    c = ana["df"]
    full = ana["full_time"]
    to_kwh = lambda col: float(c[col].sum() * 5 / 60)
    parts = [("供應家用", to_kwh("pv_home"), "#10B981", ""), ("充進電池", to_kwh("pv_charge"), "#A78BFA", "")]
    if ana["calibrated"]:
        parts.append(("浪費掉（估計）", ana["lost_kwh"], "#F59E0B",
                      "background-image:repeating-linear-gradient(45deg, rgba(0,0,0,0.25) 0 4px, transparent 4px 9px);"))
    total = sum(v for _, v, _, _ in parts) or 1

    # (1) 一條比例長條：今天的太陽能分到哪裡
    segs = "".join(
        f'<div style="flex:{v:.3f}; background:{col}; {pat} display:flex; align-items:center; justify-content:center; '
        f'color:#0F172A; font-weight:700; font-size:13px; white-space:nowrap; overflow:hidden;">'
        f'{v / total * 100:.0f}%</div>' for _, v, col, pat in parts if v > 0.05)
    legend = "".join(
        f'<div style="display:flex; align-items:center; gap:6px;"><span style="width:12px; height:12px; border-radius:3px; background:{col}; {pat}"></span>'
        f'<span style="color:#CBD5E1;">{name}</span><b style="color:#F8FAFC;">{v:.1f} kWh</b></div>' for name, v, col, pat in parts)
    st.markdown(f"""<div class="card">
      <div style="display:flex; height:34px; border-radius:8px; overflow:hidden; gap:2px;">{segs}</div>
      <div style="display:flex; flex-wrap:wrap; gap:8px 22px; margin-top:12px; font-size:14px;">{legend}</div>
    </div>""", unsafe_allow_html=True)

    # (2) 每小時長條圖：只留三種顏色，標出電池充滿時間
    hourly = c.set_index("time")[["pv_home", "pv_charge", "lost"]].resample("1h").sum() * 5 / 60
    hourly = hourly[(hourly.index.hour >= 5) & (hourly.index.hour <= 19)]
    hx = hourly.index + pd.Timedelta(minutes=30)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=hx, y=hourly["pv_home"], name="供應家用", marker_color="#10B981"))
    fig.add_trace(go.Bar(x=hx, y=hourly["pv_charge"], name="充進電池", marker_color="#A78BFA"))
    if ana["calibrated"]:
        fig.add_trace(go.Bar(x=hx, y=hourly["lost"].where(hourly["lost"] >= 0.05), name="浪費掉（估計）", marker=dict(
            color="rgba(245,158,11,0.35)", line=dict(color="#F59E0B", width=1), pattern=dict(shape="/", fgcolor="#F59E0B", size=6))))
    if full is not None:
        fig.add_vline(x=full, line=dict(color="#E2E8F0", width=1, dash="dot"))
        fig.add_annotation(x=full, y=1, yref="paper", text=f"🔋 {full:%H:%M} 電池充滿", showarrow=False,
                           xanchor="left", yanchor="top", font=dict(color="#E2E8F0", size=12), bgcolor="rgba(15,23,42,0.7)")
    fig.update_layout(**{**chart_layout, "height": 300}, barmode="stack", bargap=0.15,
                      xaxis=dict(showgrid=False, tickformat="%H時", dtick=2 * 3600000),
                      yaxis=dict(gridcolor="#334155", title="每小時 kWh", rangemode="tozero"))
    st.plotly_chart(fig, width="stretch", theme=None)
    st.markdown(f"""<div style="color:#94A3B8; font-size:13px; margin-top:-6px;">
      每根長條是一小時的太陽能發電。電池充滿後，因為不能賣電，多出來的日照用不到（橘色斜線，以晴天推估，多雲時可能高估）。
      今日賣電 {f('etoGridToday'):.1f} kWh。</div>""", unsafe_allow_html=True)

# ==========================================
# 8.5 東西兩組太陽能板 (PV1 西坡 / PV3 東坡)
# ==========================================
st.markdown("<div class='section-title'>☀️ 東西兩組太陽能板</div>", unsafe_allow_html=True)
string_kwp = CAPACITY_KWP / len(STRINGS)
total_today = sum(str_today)
best = max(str_today) if str_today else 0
cards_html = ""
for i, (n, name, where, color) in enumerate(STRINGS):
    kw, today = str_kw[i], str_today[i]
    share = today / total_today * 100 if total_today > 0 else 0
    # 兩組朝向相反 (東坡 / 西坡)，即時功率差異屬正常；兩張卡片都標上資料時間，較強的一組標「最強」，較弱的標差多少
    other_kw = max(k for j, k in enumerate(str_kw) if j != i) if len(str_kw) > 1 else 0
    other = next(w[:2] for j, (_, _, w, _) in enumerate(STRINGS) if j != i) if len(STRINGS) > 1 else ""
    hm = f"{last_update:%H:%M}"
    if kw <= 0.05 and other_kw <= 0.05:
        note = f'<div class="kpi-sub" style="margin-top:4px;">🕐 {hm} 未發電</div>'
    elif kw >= other_kw:
        note = f'<div class="kpi-sub" style="margin-top:4px; color:#2DD4BF;">☀️ {hm} 發電最強</div>'
    else:
        note = f'<div class="kpi-sub" style="margin-top:4px;">🕐 {hm} 比{other}少 {(1 - kw / other_kw) * 100:.0f}%</div>'
    cards_html += f"""<div class="card" style="border-top:3px solid {color};">
      <div style="display:flex; justify-content:space-between; align-items:baseline;">
        <span style="color:{color}; font-weight:700;">{name}</span><span class="kpi-sub">{where}</span></div>
      <div class="kpi-value">{kw:.2f}<span class="kpi-unit">kW</span></div>
      <div class="kpi-sub">{f(f'vpv{n}'):.0f} V · {ipv(n):.1f} A</div>
      <div style="color:#CBD5E1; font-size:13px; margin-top:6px;">今日 <b>{today:.1f}</b> kWh（{share:.0f}%）</div>
      <div class="kpi-sub">{today / string_kwp:.2f} kWh/kWp</div>{note}</div>"""
st.markdown(f'<div class="grid2">{cards_html}</div>', unsafe_allow_html=True)

if not curve.empty and curve[["pv1", "pv2", "pv3"]].to_numpy().sum() > 0:
    # 每小時堆疊長條：總高度 = 該小時總發電，顏色看出東西兩組占比；改線當天 PV2 還有資料時另以灰色顯示
    hs = curve.set_index("time")[["pv1", "pv2", "pv3"]].resample("1h").sum() * 5 / 60
    hs = hs[(hs.index.hour >= 5) & (hs.index.hour <= 19)]
    hx = hs.index + pd.Timedelta(minutes=30)
    fh = go.Figure()
    for n, name, where, color in STRINGS:
        fh.add_trace(go.Bar(x=hx, y=hs[f"pv{n}"], name=f"{name} {where.split(' ')[0]}",
                            marker=dict(color=color, line=dict(color="#1E293B", width=1)),
                            hovertemplate=f"{name}：%{{y:.2f}} kWh<extra></extra>"))
    if hs["pv2"].sum() > 0.05:
        fh.add_trace(go.Bar(x=hx, y=hs["pv2"], name="PV2（已停用）", marker=dict(color="#64748B", line=dict(color="#1E293B", width=1)),
                            hovertemplate="PV2：%{y:.2f} kWh<extra></extra>"))
    fh.update_layout(**{**chart_layout, "height": 300, "margin": dict(l=50, r=20, t=70, b=40)}, barmode="stack", bargap=0.15,
                     title=dict(text="每小時發電（東西兩組堆疊）", font=dict(color="#E2E8F0", size=15), x=0.01, y=0.97),
                     xaxis=dict(showgrid=False, tickformat="%H時", dtick=2 * 3600000),
                     yaxis=dict(gridcolor="#334155", title="kWh", rangemode="tozero"))
    st.plotly_chart(fh, width="stretch", theme=None)

    # 每組一條橫向長條，切成上午 / 下午，直接看出朝向造成的差異
    noon = curve["time"].dt.hour < 12
    halves = [(float(curve.loc[noon, f"pv{n}"].sum() * 5 / 60), float(curve.loc[~noon, f"pv{n}"].sum() * 5 / 60)) for n, *_ in STRINGS]
    vmax = max(am + pm for am, pm in halves) or 1
    rows = ""
    for (_, name, where, color), (am, pm) in zip(STRINGS, halves):
        tot = am + pm
        am_pct = am / tot * 100 if tot > 0 else 0
        width = tot / vmax * 100
        rows += f"""<div style="display:grid; grid-template-columns:120px 1fr; gap:10px; align-items:center; margin:10px 0;">
          <div><div style="color:{color}; font-weight:700;">{name}</div><div class="kpi-sub">{where.split(" · ")[0]}</div></div>
          <div>
            <div style="display:flex; width:{width:.1f}%; height:26px; border-radius:6px; overflow:hidden; gap:2px;">
              <div style="flex:{am:.3f}; background:#E2E8F0; color:#1F2937; font-size:12px; font-weight:700; display:flex; align-items:center; padding-left:8px; white-space:nowrap; overflow:hidden;">上午 {am:.1f}</div>
              <div style="flex:{pm:.3f}; background:#64748B; color:#F8FAFC; font-size:12px; font-weight:700; display:flex; align-items:center; padding-left:8px; white-space:nowrap; overflow:hidden;">下午 {pm:.1f}</div>
            </div>
            <div class="kpi-sub" style="margin-top:3px;">共 {tot:.1f} kWh · 上午占 {am_pct:.0f}%</div>
          </div></div>"""
    full_t = ana["full_time"] if ana else None
    curtail_note = (f'<div style="color:#FBBF24; font-size:12px; margin-top:4px;">⚠️ 電池在 {full_t:%H:%M} 充滿後兩組一起被限發，'
                    "下午的數字會偏低；要比較朝向差異，請看電池未充滿的日子。</div>") if full_t is not None and full_t.hour < 15 else ""
    st.markdown(f"""<div class="card" style="margin-top:10px;">
      <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px;">
        <span style="color:#E2E8F0; font-weight:700;">上午 vs 下午發電（kWh）</span>
        <span style="font-size:12px; color:#94A3B8;"><span style="display:inline-block; width:10px; height:10px; background:#E2E8F0; border-radius:2px;"></span> 上午（12 點前）
        　<span style="display:inline-block; width:10px; height:10px; background:#64748B; border-radius:2px;"></span> 下午</span></div>
      {rows}{curtail_note}</div>""", unsafe_allow_html=True)

st.markdown('<div style="color:#94A3B8; font-size:13px; margin-top:10px;">屋頂為人字型、正面朝東，東西兩坡各 12 片串成一組（各約 5.34 kWp）：'
            "<b style='color:#F59E0B'>PV3 = 東坡</b>，上午日照直射發電較多；<b style='color:#3B82F6'>PV1 = 西坡</b>，下午較多；正午前後兩組會接近。"
            "PV2 輸入已停用（原本東西各 4 片混串，早晚會被較弱的一邊拖累）。"
            "若同一時段某組長期明顯低於往常，再檢查是否有遮蔭、落葉或髒污。電池充滿限發時兩組會一起被壓低。</div>",
            unsafe_allow_html=True)

# 下載逐 5 分鐘明細 (東西兩組電壓、電流、功率)，方便離線分析；Growatt 只保留最近 3 個月
with st.expander("📥 下載逐 5 分鐘明細（CSV）"):
    dl_day = st.date_input("日期", value=now_tw.date(), min_value=now_tw.date() - timedelta(days=89), max_value=now_tw.date())
    cols = {"time": "時間", "pv": "總發電kW", "pv1": "PV1西坡kW", "vpv1": "PV1電壓V", "ipv1": "PV1電流A",
            "pv3": "PV3東坡kW", "vpv3": "PV3電壓V", "ipv3": "PV3電流A",
            "pv2": "PV2kW(改線前東西混串)", "vpv2": "PV2電壓V", "ipv2": "PV2電流A", "load": "用電kW", "charge": "充電kW",
            "discharge": "放電kW", "buy": "買電kW", "sell": "賣電kW", "soc": "電池%"}
    try:
        dl = curve if dl_day == now_tw.date() else with_fallback(f"curve-{dl_day}", fetch_day_curve, dl_day.strftime("%Y-%m-%d"))
        out = dl[[c for c in cols if c in dl.columns]].rename(columns=cols)
        st.download_button(f"下載 {dl_day} 明細（{len(out)} 筆）", out.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"haochen_{dl_day}.csv", mime="text/csv", disabled=out.empty)
    except Exception as e:
        st.caption(api_error_text(e))

    # 一次下載一段日期：逐日向 Growatt 查詢 (每天一次，中間稍停避免被限流)，合併成一個 CSV
    st.markdown("<div style='height:10px'></div>**一次下載一段日期**", unsafe_allow_html=True)
    rng = st.date_input("日期區間", value=(now_tw.date() - timedelta(days=6), now_tw.date()),
                        min_value=now_tw.date() - timedelta(days=89), max_value=now_tw.date(), key="dl_range")
    if isinstance(rng, (tuple, list)) and len(rng) == 2:
        d0, d1 = rng
        days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
        st.caption(f"共 {len(days)} 天，每天約需 1–2 秒，下載期間請不要關閉頁面。")
        if st.button(f"準備 {d0:%m/%d}–{d1:%m/%d} 的 CSV"):
            import time
            parts, failed = [], []
            bar = st.progress(0.0)
            for i, day in enumerate(days):
                try:
                    df_day = curve if day == now_tw.date() else with_fallback(f"curve-{day}", fetch_day_curve, day.strftime("%Y-%m-%d"))
                    if not df_day.empty:
                        parts.append(df_day[[c for c in cols if c in df_day.columns]])
                except Exception:
                    failed.append(day)
                bar.progress((i + 1) / len(days), text=f"{day:%m/%d}（{i + 1}/{len(days)}）")
                time.sleep(1.0)
            bar.empty()
            merged = pd.concat(parts).rename(columns=cols) if parts else pd.DataFrame()
            st.session_state["dl_range_csv"] = (f"{d0}_{d1}", merged.to_csv(index=False).encode("utf-8-sig"), len(merged), failed)
        if st.session_state.get("dl_range_csv"):
            tag, data, n, failed = st.session_state["dl_range_csv"]
            st.download_button(f"下載 {tag.replace('_', ' 至 ')} 明細（{n} 筆）", data, file_name=f"haochen_{tag}.csv", mime="text/csv", disabled=n == 0)
            if failed:
                st.caption("以下日期查詢失敗（可能被 Growatt 限流，稍後再試）：" + "、".join(f"{d:%m/%d}" for d in failed))

# ==========================================
# 9. 分頁：設備與溫度 / 趨勢
# ==========================================
st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
tab_dev, tab_trend = st.tabs(["🌡️ 溫度與設備", "📊 趨勢（即將推出）"])

with tab_dev:
    if not curve.empty:
        ft = go.Figure()
        ft.add_hrect(y0=60, y1=70, fillcolor="#F59E0B", opacity=0.08, line_width=0)
        ft.add_hrect(y0=70, y1=90, fillcolor="#EF4444", opacity=0.08, line_width=0)
        ft.add_trace(go.Scatter(x=curve["time"], y=curve["inv_temp"].replace(0, np.nan), name="逆變器 (temp1)", line=dict(color="#E2E8F0", width=2)))
        ft.add_trace(go.Scatter(x=curve["time"], y=curve["inv_temp2"].replace(0, np.nan), name="逆變器 (temp2)", line=dict(color="#94A3B8", width=1.3, dash="dot")))
        ft.add_trace(go.Scatter(x=curve["time"], y=curve["bat_temp"].replace(0, np.nan), name="電池", line=dict(color=C_BAT, width=2)))
        ft.update_layout(**{**chart_layout, "height": 320}, xaxis=dict(showgrid=False, tickformat="%H:%M"),
                         yaxis=dict(gridcolor="#334155", title="°C", range=[15, 80]))
        st.plotly_chart(ft, width="stretch", theme=None)
        st.markdown('<div style="color:#94A3B8; font-size:13px;">' + "黃色區 60–70°C 偏高、紅色區 70°C 以上過高（逆變器）；電池（鋰鐵）10–35°C 為理想範圍。門檻為一般參考值，可依原廠規格調整。"
                   "逆變器夜間放電時溫度也會上升，屬正常現象。" + "</div>", unsafe_allow_html=True)

    def diag_card(title, color, rows):
        rows_html = "".join(f'<div style="display:flex; justify-content:space-between; margin-bottom:7px; font-size:13px;">'
                            f'<span style="color:#94A3B8;">{k}</span><span style="color:#F8FAFC; font-family:monospace;">{v}</span></div>' for k, v in rows)
        return (f'<div class="card" style="border-top:3px solid {color}; height:100%;">'
                f'<div style="color:{color}; font-weight:700; margin-bottom:10px;">{title}</div>{rows_html}</div>')

    d1, d2, d3 = st.columns(3)
    d1.markdown(diag_card("☀️ 太陽能陣列 (MPPT)", C_PV, [
        (f"PV{i} 電壓 / 功率", f"{f(f'vpv{i}'):.0f} V / {f(f'ppv{i}'):.0f} W") for i in (1, 2, 3)
    ]), unsafe_allow_html=True)
    d2.markdown(diag_card("🔋 電池", C_BAT, [
        ("電量 / 電壓", f"{soc} % / {f('vbat'):.1f} V"),
        ("目前狀態", f"充電 {p_charge * 1000:.0f} W" if p_charge > 0.02 else f"放電 {p_discharge * 1000:.0f} W" if p_discharge > 0.02 else "待機 0 W"),
        ("今日充 / 放電", f"{f('echarge1Today'):.1f} / {f('edischarge1Today'):.1f} kWh"),
        ("溫度", f"{bat_temp:.1f} °C"),
    ]), unsafe_allow_html=True)
    # 家裡的負載接在逆變器的備援 (EPS) 輸出：平常家中用電都經過這裡 (L1 + L2)；
    # 電池到放電下限時逆變器切到旁路，台電經內部繼電器直接供電給家裡，這裡就量不到 (接近 0)
    eps_w = f("upsPac1") + f("upsPac2")
    eps_txt = f"{eps_w:.0f} W" + ("（台電直供中）" if eps_w < 50 and p_buy > 0.05 else "")
    d3.markdown(diag_card("⚡ 市電 / 逆變器供電", C_GRID, [
        ("電網電壓 L1 / L2", f"{f('vac1'):.1f} / {f('vac2'):.1f} V"),
        ("電網頻率", f"{f('fac'):.2f} Hz"),
        ("逆變器送進家裡", eps_txt),
    ]), unsafe_allow_html=True)

with tab_trend:
    st.markdown("""<div class="card" style="color:#CBD5E1; line-height:1.8;">
      <b>📊 趨勢分析即將推出（第二階段）</b><br>
      Growatt 官方月報經驗證會少算約 3 成，且 5 分鐘明細只保留 3 個月。<br>
      第二階段將每天自動保存逆變器的正確數據，用來呈現每日 / 每月發電、用電、限發損失，以及設備健康檢查。
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
