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

# ==========================================
# 1. 頁面設定與樣式
#    全站顏色語意固定：綠=太陽能、橘=用電、紫=電池、藍=台電
# ==========================================
st.set_page_config(page_title="澔宸小窩 - 智慧能源", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh")  # 5 分鐘刷新 (官方資料約每 5 分鐘更新一次)

C_PV, C_LOAD, C_BAT, C_GRID = "#10B981", "#F97316", "#A78BFA", "#38BDF8"
# 屋頂 4 x 6 共 24 片，由左至右每 2 列 8 片為一組 (顏色與 3D 屋頂外框一致)
STRINGS = [("PV1", "左側 8 片", "#10B981"), ("PV2", "中間 8 片", "#2DD4BF"), ("PV3", "右側 8 片", "#A3E635")]

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

st.markdown(f"""
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;
     padding-bottom:12px; border-bottom:1px solid #334155; margin-bottom:16px;">
  <div>
    <div style="color:#F8FAFC; font-size:26px; font-weight:800;">澔宸小窩</div>
    <div style="color:#64748B; font-size:12px; letter-spacing:1px;">SPH 10000TL-HU · {CAPACITY_KWP} kWp · 南投</div>
  </div>
  <div style="text-align:right;">
    <span class="chip" style="background:{dot_color}22; color:{dot_color};">● {dot_text}</span>
    <div style="color:#94A3B8; font-size:12px; margin-top:4px; font-family:monospace;">
      資料 {last_update:%m/%d %H:%M}（{age_text}）· 網頁 {now_tw:%H:%M:%S}</div>
  </div>
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
    return f"""<div class="card" style="height:100%;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span class="kpi-label">{title}</span>
        <span class="chip" style="background:{color}22; color:{color};">{label}</span></div>
      <div class="kpi-value" style="color:{color};">{value:.1f}<span class="kpi-unit">°C</span></div>
      {sparkline_svg(series.tolist() if series is not None else [], unit_color)}
      <div class="kpi-sub">{peak}</div></div>"""


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

# ==========================================
# 8. 今日：太陽能的電去了哪裡
# ==========================================
st.markdown("<div class='section-title'>📈 今天太陽能的電去了哪裡？</div>", unsafe_allow_html=True)
chart_layout = dict(
    paper_bgcolor="#1E293B", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8"),
    height=380, margin=dict(l=50, r=50, t=40, b=40), hovermode="x unified",
    legend=dict(orientation="h", y=1.02, yanchor="bottom", x=0, traceorder="normal", font=dict(color="#E2E8F0")),
)
if ana is None:
    st.markdown(f'<div class="card" style="text-align:center; color:#94A3B8; padding:40px;">⏳ {api_error_text(curve_error) if curve_error else "今天還沒有資料"}</div>',
                unsafe_allow_html=True)
else:
    c = ana["df"]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=c["time"], y=c["pv_home"], name="供應家用", stackgroup="pv", line=dict(width=0), fillcolor="rgba(16,185,129,0.75)"))
    fig.add_trace(go.Scatter(x=c["time"], y=c["pv_charge"], name="充進電池", stackgroup="pv", line=dict(width=0), fillcolor="rgba(167,139,250,0.7)"))
    if ana["calibrated"]:
        fig.add_trace(go.Scatter(x=c["time"], y=c["lost"], name="被限發（估計）", stackgroup="pv", line=dict(width=0),
                                 fillcolor="rgba(245,158,11,0.35)", fillpattern=dict(shape="/", fgcolor="#F59E0B", size=6)))
    fig.add_trace(go.Scatter(x=c["time"], y=c["load"], name="家中用電", line=dict(color=C_LOAD, width=1.6)))
    fig.add_trace(go.Scatter(x=c["time"], y=c["soc"], name="電池 SOC", yaxis="y2", line=dict(color="#E2E8F0", width=1.2, dash="dot")))
    fig.update_layout(**chart_layout,
                      xaxis=dict(showgrid=False, tickformat="%H:%M"),
                      yaxis=dict(gridcolor="#334155", title="kW", rangemode="tozero"),
                      yaxis2=dict(overlaying="y", side="right", range=[0, 105], tickvals=[0, 25, 50, 75, 100], showgrid=False, title="SOC %"))
    st.plotly_chart(fig, width="stretch", theme=None)
    full = ana["full_time"]
    st.markdown(f"""<div style="color:#94A3B8; font-size:13px; margin-top:-6px;">
      電池首次充滿：<b style="color:#E2E8F0">{full:%H:%M}</b>　｜　今日賣電：<b style="color:#E2E8F0">{f('etoGridToday'):.1f} kWh</b>　｜
      系統為零饋電設定，電池充滿後太陽能只會發到等於家中用電；斜線區為以晴天推估的損失，多雲時可能高估。</div>"""
                if full is not None else
                '<div style="color:#94A3B8; font-size:13px; margin-top:-6px;">電池今天尚未充滿。</div>', unsafe_allow_html=True)

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
    gap = (1 - today / best) * 100 if best >= 1 else 0
    # 白天只顯示差距供參考；15 點後全天資料較完整，差距仍超過 15% 才提醒
    if gap >= 15 and last_update.hour >= 15:
        note = f'<div style="color:#F59E0B; font-size:12px; margin-top:4px;">⚠️ 今日比最佳組低 {gap:.0f}%，可能有遮蔭或髒污</div>'
    elif gap >= 3:
        note = f'<div class="kpi-sub" style="margin-top:4px;">比最佳組低 {gap:.0f}%</div>'
    else:
        note = '<div class="kpi-sub" style="margin-top:4px; color:#2DD4BF;">今日最佳</div>' if best >= 1 else ""
    cards_html += f"""<div class="card" style="border-top:3px solid {color};">
      <div style="display:flex; justify-content:space-between; align-items:baseline;">
        <span style="color:{color}; font-weight:700;">{name}</span><span class="kpi-sub">{where}</span></div>
      <div class="kpi-value">{kw:.2f}<span class="kpi-unit">kW</span></div>
      <div class="kpi-sub">{f(f'vpv{i + 1}'):.0f} V · {f(f'ipv{i + 1}'):.1f} A</div>
      <div style="color:#CBD5E1; font-size:13px; margin-top:6px;">今日 <b>{today:.1f}</b> kWh（{share:.0f}%）</div>
      <div class="kpi-sub">{today / string_kwp:.2f} kWh/kWp</div>{note}</div>"""
st.markdown(f'<div class="grid3">{cards_html}</div>', unsafe_allow_html=True)

if not curve.empty and curve[["pv1", "pv2", "pv3"]].to_numpy().sum() > 0:
    fs = go.Figure()
    for i, (name, where, color) in enumerate(STRINGS):
        fs.add_trace(go.Scatter(x=curve["time"], y=curve[f"pv{i + 1}"], name=f"{name}（{where}）", line=dict(color=color, width=2)))
    fs.update_layout(**{**chart_layout, "height": 300}, xaxis=dict(showgrid=False, tickformat="%H:%M"),
                     yaxis=dict(gridcolor="#334155", title="kW", rangemode="tozero"))
    st.plotly_chart(fs, width="stretch", theme=None)
st.markdown('<div style="color:#94A3B8; font-size:13px; margin-top:-6px;">三組容量相同（各 8 片、約 3.56 kWp），'
            "正常情況下發電量應接近；某一組長期偏低時，可檢查是否有遮蔭、落葉或髒污。電池充滿限發時三組會一起被壓低。</div>",
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
    ]), unsafe_allow_html=True)

with tab_trend:
    st.markdown("""<div class="card" style="color:#CBD5E1; line-height:1.8;">
      <b>📊 趨勢分析即將推出（第二階段）</b><br>
      Growatt 官方月報經驗證會少算約 3 成，且 5 分鐘明細只保留 3 個月。<br>
      第二階段將每天自動保存逆變器的正確數據，用來呈現每日 / 每月發電、用電、限發損失，以及設備健康檢查。
    </div>""", unsafe_allow_html=True)

st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
