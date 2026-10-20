import math
import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

from power_flow import power_flow_html
from solar_core import CAPACITY_KWP, TW_TZ, analyze_curve, rows_to_curve

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
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p, [data-testid="stRadio"] label p {color:#CBD5E1 !important;}
.stTabs [data-baseweb="tab-list"] {gap: 6px;}
.stTabs [data-baseweb="tab"] {background:#1E293B; border-radius:8px 8px 0 0; padding:8px 16px; color:#CBD5E1;}
</style>
""", unsafe_allow_html=True)


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
    return rows_to_curve(api_v4("queryHistoricalData", deviceSn=DEVICE_SN, deviceType=DEVICE_TYPE, date=day)["datas"] or [])


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
# 3. 分析 (限發估計等核心邏輯在 solar_core.py，與每日存檔共用)
# ==========================================
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
tab_dev, tab_trend = st.tabs(["🌡️ 溫度與設備", "📊 趨勢"])

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
    daily_path = Path(__file__).parent / "data" / "daily.csv"
    if not daily_path.exists():
        st.markdown("""<div class="card" style="color:#CBD5E1; line-height:1.8;">
          <b>📊 尚未有存檔資料</b><br>每日自動存檔（GitHub Actions）第一次執行後，這裡會出現每日與每月趨勢。</div>""",
                    unsafe_allow_html=True)
    else:
        dd = pd.read_csv(daily_path, parse_dates=["date"]).sort_values("date")
        rng = st.radio("期間", ["最近 30 天", "最近 90 天", "全部"], horizontal=True, label_visibility="collapsed")
        view = dd if rng == "全部" else dd[dd["date"] > dd["date"].max() - pd.Timedelta(days=30 if rng == "最近 30 天" else 90)]

        def stacked(df, x, title, xfmt, height=320):
            fig = go.Figure()
            for i, (name, _, color) in enumerate(STRINGS):
                fig.add_trace(go.Bar(x=x, y=df[f"pv{i + 1}"], name=f"{name} {['西坡', '屋脊兩側', '東坡'][i]}",
                                     marker=dict(color=color, line=dict(color="#1E293B", width=1)),
                                     hovertemplate=f"{name}：%{{y:.1f}} kWh<extra></extra>"))
            fig.update_layout(**{**chart_layout, "height": height, "margin": dict(l=50, r=20, t=70, b=40)},
                              barmode="stack", bargap=0.18,
                              title=dict(text=title, font=dict(color="#E2E8F0", size=15), x=0.01, y=0.97),
                              xaxis=dict(showgrid=False, tickformat=xfmt), yaxis=dict(gridcolor="#334155", title="kWh", rangemode="tozero"))
            return fig

        # (B) 每日三組堆疊
        st.plotly_chart(stacked(view, view["date"], "每日發電（三組堆疊）", "%m/%d"), width="stretch", theme=None)

        # 月份版本 + 月摘要
        dd["month"] = dd["date"].dt.to_period("M")
        m = dd.groupby("month").agg(days=("date", "count"), pv=("pv", "sum"), pv1=("pv1", "sum"), pv2=("pv2", "sum"),
                                     pv3=("pv3", "sum"), load=("load", "sum"), buy=("buy", "sum"),
                                     lost=("lost", lambda x: pd.to_numeric(x, errors="coerce").sum())).reset_index()
        multi_year = m["month"].dt.year.nunique() > 1
        m["label"] = [(f"{p.year % 100}/{p.month}月" if multi_year else f"{p.month}月") + ("" if n >= p.days_in_month else f"·{n}天")
                      for p, n in zip(m["month"], m["days"])]
        st.plotly_chart(stacked(m, m["label"], "每月發電（三組堆疊）", None), width="stretch", theme=None)

        rows_html = "".join(
            f"<tr style='border-bottom:1px solid #26334a;'><td style='text-align:left; padding:6px 0;'>{r.label}</td><td>{r.pv:,.0f}</td><td>{r.pv / CAPACITY_KWP:,.0f}</td><td>{r.load:,.0f}</td><td>{r.buy:,.0f}</td>"
            f"<td>{(1 - r.buy / r.load) * 100 if r.load else 0:.0f}%</td><td>{r.lost:,.0f}</td></tr>" for r in m.itertuples())
        st.markdown(f"""<div class="card" style="overflow-x:auto;">
          <div style="color:#E2E8F0; font-weight:700; margin-bottom:8px;">每月摘要（kWh）</div>
          <table style="width:100%; border-collapse:collapse; font-size:13px; color:#CBD5E1; text-align:right; white-space:nowrap;">
            <tr style="color:#94A3B8; border-bottom:1px solid #334155;"><th style="text-align:left;">月份</th><th>發電</th><th>每kWp</th>
              <th>用電</th><th>買電</th><th>自給</th><th>浪費*</th></tr>
            {rows_html}</table>
          <div class="kpi-sub" style="margin-top:8px;">資料來自逆變器自身的每日累計（比 Growatt 官方月報準確），每天凌晨自動存檔。
            未滿整月的月份會標示天數。*浪費為電池充滿後被限發的估計值。</div></div>""", unsafe_allow_html=True)

st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
