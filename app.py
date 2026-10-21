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

from power_flow import power_flow_html
import pv_forecast

# ==========================================
# 1. 頁面設定與樣式
#    全站顏色語意固定：綠=太陽能、橘=用電、紫=電池、藍=台電
# ==========================================
st.set_page_config(page_title="澔宸小窩 - 智慧能源", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh")  # 5 分鐘刷新 (官方資料約每 5 分鐘更新一次)

C_PV, C_LOAD, C_BAT, C_GRID = "#10B981", "#F97316", "#A78BFA", "#38BDF8"
# 人字屋頂、正面朝東：東西兩坡各 12 片，由西到東每 2 列 8 片為一組 (顏色與 3D 屋頂外框一致)
STRINGS = [("PV1", "西坡 8 片 · 下午較強", "#3B82F6"), ("PV2", "東西坡各 4 片 · 全天平均", "#14B8A6"),
           ("PV3", "東坡 8 片 · 上午較強", "#F59E0B")]

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


@st.cache_data(ttl=600)
def fetch_day_curve(day):
    rows = api_v4("queryHistoricalData", deviceSn=DEVICE_SN, deviceType=DEVICE_TYPE, date=day)["datas"] or []
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
    } for x in rows])
    return df.sort_values("time").reset_index(drop=True) if not df.empty else df


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
p_buy, p_sell = f("pacToUserTotal") / 1000, f("pacToGridTotal") / 1000
soc = int(f("soc"))
inv_temp = f("temp1") or f("invTemp")
bat_temp = f("bmsBatteryTemp")
str_kw = [(f(f"ppv{i}") or f(f"vpv{i}") * f(f"ipv{i}")) / 1000 for i in (1, 2, 3)]
str_today = [f(f"epv{i}Today") for i in (1, 2, 3)]
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
    status = f"☀️ 太陽能 {pv_kw:.1f} kW 不夠用，部分向台電購電 {p_buy:.1f} kW"
elif pv_kw > 0.05 and p_charge > 0.05:
    status = f"☀️ 太陽能供電中，同時為電池充電 {p_charge:.1f} kW（電量 {soc}%）"
elif pv_kw > 0.05:
    status = f"☀️ 太陽能供電中（{pv_kw:.1f} kW）"
elif p_discharge > 0.05:
    status = f"🔋 電池供電中，剩餘 {soc}%，正在提供 {p_discharge:.1f} kW"
elif p_buy > 0.05:
    status = f"🔌 向台電購電中（{p_buy:.1f} kW）"
else:
    status = "💤 系統待機中"

st.markdown(f'<div style="color:#F1F5F9; font-size:19px; font-weight:600; margin:2px 0 12px;">{status}</div>',
            unsafe_allow_html=True)

if is_limited and not stale:
    spare = f"，估計還有約 <b>{ana['lost_now']:.1f} kW</b> 用不到" if ana and ana["lost_now"] >= 0.3 else ""
    st.markdown(f"""<div class="card" style="border-color:#10B98166; background:linear-gradient(90deg,#064E3B66,#1E293B); margin-bottom:14px;">
      <div style="color:#6EE7B7; font-weight:700; font-size:15px;">💡 現在很適合用電{spare}</div>
      <div style="color:#CBD5E1; font-size:13px; margin-top:4px;">洗衣機、烘衣機、熱水器、電動車充電、冷氣預冷 —— 這段時間多用 1 度，就少浪費 1 度</div>
    </div>""", unsafe_allow_html=True)

# ==========================================
# 7. 主畫面：3D 能源流向 | 今日數字 + 溫度
# ==========================================
col_main, col_side = st.columns([1.55, 1], gap="medium")

with col_main:
    is_night = pv_kw < 0.05 and not (6 <= last_update.hour < 18)
    components.html(power_flow_html(pv=pv_kw, load=load_kw, batt=p_charge - p_discharge, soc=soc,
                                    grid=p_buy - p_sell, night=is_night, strings=str_kw), height=520)


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
@st.cache_data(ttl=3600)
def fetch_pv_forecast():
    return pv_forecast.forecast()


st.markdown("<div class='section-title'>🌤️ 天氣與發電預測（ECMWF 短波輻射）</div>", unsafe_allow_html=True)
# 下午 2 點前預設看今天，之後看明天
day_opts = {"今天": now_tw.date(), "明天": (now_tw + timedelta(days=1)).date()}
pick = st.radio("預測日", list(day_opts), index=0 if now_tw.hour < 14 else 1, horizontal=True, label_visibility="collapsed")
tomorrow = day_opts[pick]
try:
    fc_hourly, fc_days = with_fallback("pv_forecast", fetch_pv_forecast)
    fd = fc_days.get(tomorrow)
except Exception as e:
    fd = None
    st.markdown(f'<div class="card" style="color:#94A3B8;">⏳ 天氣預報暫時讀不到（{e}），稍後會自動重試。</div>', unsafe_allow_html=True)

if fd:
    sky = "☀️ 晴" if fd["cloud"] < 30 else ("⛅ 多雲" if fd["cloud"] < 70 else "☁️ 陰")
    rain = f"，預估降雨 {fd['rain']:.0f} mm" if fd["rain"] >= 1 else ""
    full_txt = f"約 <b>{fd['full_at']:%H:%M}</b> 充滿" if fd["full_at"] is not None else "<b>不會充滿</b>"
    hrs = fd["best_hours"]
    best = (f"💡 {pick} <b>{hrs[0]}:00–{hrs[-1] + 1}:00</b> 太陽能用不完，適合安排洗衣、烘衣、熱水器、電動車充電"
            if hrs else f"{pick}預估太陽能大多會被家用與電池用完，沒有明顯浪費的時段")
    strings_txt = "　".join(f'<span style="color:{c}">{n}</span> {fd[n]:.1f}' for n, _, c in STRINGS)
    st.markdown(f"""<div class="card">
      <div style="display:flex; flex-wrap:wrap; gap:16px 36px; align-items:flex-end;">
        <div><div class="kpi-label">預估可發電量</div><div class="kpi-value" style="color:{C_PV};">{fd['pv']:.0f}<span class="kpi-unit">kWh</span></div>
          <div class="kpi-sub">{sky}{rain} · 日照 {fd['ghi']:.1f} kWh/m²</div></div>
        <div><div class="kpi-label">電池</div><div style="color:#F1F5F9; font-size:20px; margin-top:4px;">{full_txt}</div>
          <div class="kpi-sub">以早上 7 點電量 74% 估算</div></div>
        <div><div class="kpi-label">預估浪費</div><div class="kpi-value" style="color:#F59E0B;">{fd['lost']:.0f}<span class="kpi-unit">kWh</span></div>
          <div class="kpi-sub">家用 {fd['home']:.0f} · 充電 {fd['charge']:.0f} kWh</div></div>
        <div><div class="kpi-label">三組預估（kWh）</div><div style="color:#CBD5E1; font-size:14px; margin-top:8px;">{strings_txt}</div></div>
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
                "換算到東西坡板面後估算三組發電；電池與用電依本站實測平均推估。依 7–10 月實測校正，逐時誤差中位數約 10%；午後雷陣雨或雲量多的日子誤差可能達 30% 以上。</div>",
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
# 8.5 三組太陽能板 (PV1 / PV2 / PV3)
# ==========================================
st.markdown("<div class='section-title'>☀️ 三組太陽能板</div>", unsafe_allow_html=True)
string_kwp = CAPACITY_KWP / 3
total_today = sum(str_today)
best = max(str_today) if str_today else 0
cards_html = ""
for i, (name, where, color) in enumerate(STRINGS):
    kw, today = str_kw[i], str_today[i]
    share = today / total_today * 100 if total_today > 0 else 0
    # 三組朝向不同 (東坡 / 西坡 / 各半)，即時功率差異屬正常；只標示目前最強的一組
    note = ('<div class="kpi-sub" style="margin-top:4px; color:#2DD4BF;">☀️ 目前發電最強</div>'
            if kw > 0.05 and kw == max(str_kw) else "")
    cards_html += f"""<div class="card" style="border-top:3px solid {color};">
      <div style="display:flex; justify-content:space-between; align-items:baseline;">
        <span style="color:{color}; font-weight:700;">{name}</span><span class="kpi-sub">{where}</span></div>
      <div class="kpi-value">{kw:.2f}<span class="kpi-unit">kW</span></div>
      <div class="kpi-sub">{f(f'vpv{i + 1}'):.0f} V · {f(f'ipv{i + 1}'):.1f} A</div>
      <div style="color:#CBD5E1; font-size:13px; margin-top:6px;">今日 <b>{today:.1f}</b> kWh（{share:.0f}%）</div>
      <div class="kpi-sub">{today / string_kwp:.2f} kWh/kWp</div>{note}</div>"""
st.markdown(f'<div class="grid3">{cards_html}</div>', unsafe_allow_html=True)

if not curve.empty and curve[["pv1", "pv2", "pv3"]].to_numpy().sum() > 0:
    # 每小時三組堆疊長條：總高度 = 該小時總發電，顏色看出各組占比
    hs = curve.set_index("time")[["pv1", "pv2", "pv3"]].resample("1h").sum() * 5 / 60
    hs = hs[(hs.index.hour >= 5) & (hs.index.hour <= 19)]
    hx = hs.index + pd.Timedelta(minutes=30)
    fh = go.Figure()
    for i, (name, where, color) in enumerate(STRINGS):
        fh.add_trace(go.Bar(x=hx, y=hs[f"pv{i + 1}"], name=f"{name} {['西坡', '屋脊兩側', '東坡'][i]}",
                            marker=dict(color=color, line=dict(color="#1E293B", width=1)),
                            hovertemplate=f"{name}：%{{y:.2f}} kWh<extra></extra>"))
    fh.update_layout(**{**chart_layout, "height": 300, "margin": dict(l=50, r=20, t=70, b=40)}, barmode="stack", bargap=0.15,
                     title=dict(text="每小時發電（三組堆疊）", font=dict(color="#E2E8F0", size=15), x=0.01, y=0.97),
                     xaxis=dict(showgrid=False, tickformat="%H時", dtick=2 * 3600000),
                     yaxis=dict(gridcolor="#334155", title="kWh", rangemode="tozero"))
    st.plotly_chart(fh, width="stretch", theme=None)

    # 每組一條橫向長條，切成上午 / 下午，直接看出朝向造成的差異
    noon = curve["time"].dt.hour < 12
    halves = [(float(curve.loc[noon, f"pv{i}"].sum() * 5 / 60), float(curve.loc[~noon, f"pv{i}"].sum() * 5 / 60)) for i in (1, 2, 3)]
    vmax = max(am + pm for am, pm in halves) or 1
    rows = ""
    for (name, where, color), (am, pm) in zip(STRINGS, halves):
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
    curtail_note = (f'<div style="color:#FBBF24; font-size:12px; margin-top:4px;">⚠️ 電池在 {full_t:%H:%M} 充滿後三組一起被限發，'
                    "下午的數字會偏低；要比較朝向差異，請看電池未充滿的日子。</div>") if full_t is not None and full_t.hour < 15 else ""
    st.markdown(f"""<div class="card" style="margin-top:10px;">
      <div style="display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px;">
        <span style="color:#E2E8F0; font-weight:700;">上午 vs 下午發電（kWh）</span>
        <span style="font-size:12px; color:#94A3B8;"><span style="display:inline-block; width:10px; height:10px; background:#E2E8F0; border-radius:2px;"></span> 上午（12 點前）
        　<span style="display:inline-block; width:10px; height:10px; background:#64748B; border-radius:2px;"></span> 下午</span></div>
      {rows}{curtail_note}</div>""", unsafe_allow_html=True)

st.markdown('<div style="color:#94A3B8; font-size:13px; margin-top:10px;">屋頂為人字型、正面朝東，三組容量相同（各 8 片、約 3.56 kWp）但朝向不同：'
            "<b style='color:#F59E0B'>PV3 在東坡</b>，上午日照直射發電較多；<b style='color:#3B82F6'>PV1 在西坡</b>，下午較多；"
            "<b style='color:#14B8A6'>PV2 東西坡各半</b>，全天較平均。正午前後三組會接近。"
            "若同一時段某組長期明顯低於往常，再檢查是否有遮蔭、落葉或髒污。電池充滿限發時三組會一起被壓低。</div>",
            unsafe_allow_html=True)

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
        ("今日充 / 放電", f"{f('echarge1Today'):.1f} / {f('edischarge1Today'):.1f} kWh"),
        ("溫度", f"{bat_temp:.1f} °C"),
    ]), unsafe_allow_html=True)
    d3.markdown(diag_card("⚡ 市電 / 離網", C_GRID, [
        ("電網電壓 L1 / L2", f"{f('vac1'):.1f} / {f('vac2'):.1f} V"),
        ("電網頻率", f"{f('fac'):.2f} Hz"),
        ("離網輸出", f"{f('upsVac1'):.0f} V / {f('upsPac1'):.0f} W"),
        # 電表原始讀數，用來確認「電網 0 kW」是真的沒買電，還是欄位沒資料
        ("即時買電 / 賣電", f"{f('pacToUserTotal'):.0f} / {f('pacToGridTotal'):.0f} W"),
        ("今日買電 / 賣電", f"{f('etoUserToday'):.1f} / {f('etoGridToday'):.1f} kWh"),
        ("累計買電 / 賣電", f"{f('etoUserTotal'):.0f} / {f('etoGridTotal'):.0f} kWh"),
    ]), unsafe_allow_html=True)

with tab_trend:
    st.markdown("""<div class="card" style="color:#CBD5E1; line-height:1.8;">
      <b>📊 趨勢分析即將推出（第二階段）</b><br>
      Growatt 官方月報經驗證會少算約 3 成，且 5 分鐘明細只保留 3 個月。<br>
      第二階段將每天自動保存逆變器的正確數據，用來呈現每日 / 每月發電、用電、限發損失，以及設備健康檢查。
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
