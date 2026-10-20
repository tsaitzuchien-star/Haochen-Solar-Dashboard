import os
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ==========================================
# 1. 頁面基本設定與高階 CSS 樣式
# ==========================================
st.set_page_config(page_title="澔宸小窩 - 智慧能源戰情中心", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh")  # 5 分鐘刷新 (官方 API 資料約每 5 分鐘更新一次)

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header[data-testid="stHeader"] {background: transparent;}
.stApp {background-color: #0F172A; font-family: 'Segoe UI', Roboto, sans-serif;}

.metric-card {
    background: linear-gradient(145deg, #1E293B, #0F172A);
    border-radius: 12px;
    padding: 20px;
    border: 1px solid #334155;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    height: 100%;
    min-height: 290px;
    display: flex;
    flex-direction: column;
}
.glow-text-green { color: #10B981; text-shadow: 0 0 15px rgba(16, 185, 129, 0.4); font-weight: bold; }
.glow-text-orange { color: #F97316; text-shadow: 0 0 15px rgba(249, 115, 22, 0.4); font-weight: bold; }
.glow-text-purple { color: #A78BFA; text-shadow: 0 0 15px rgba(139, 92, 246, 0.4); font-weight: bold; }
.sub-text { color: #94A3B8; font-size: 14px; }
.section-title { color: #E2E8F0; font-size: 18px; font-weight: bold; margin: 10px 0; }
</style>
""", unsafe_allow_html=True)

TW_TZ = timezone(timedelta(hours=8))

# ==========================================
# 2. Growatt 官方 OpenAPI (Token 驗證，不需帳號密碼)
#    Token 請放在 Streamlit secrets 或環境變數 GROWATT_TOKEN，切勿寫進程式碼
# ==========================================
def get_secret(key, default=None):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


TOKEN = get_secret("GROWATT_TOKEN")
DEVICE_SN = get_secret("GROWATT_DEVICE_SN", "KQQ0N6200N")
PLANT_ID = get_secret("GROWATT_PLANT_ID", "9932849")
API_BASE = "https://openapi.growatt.com/"
DEVICE_TYPE = "sph-s"
CAPACITY_KWP = 10.68

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


def api_v1(endpoint, **params):
    r = requests.get(API_BASE + "v1/" + endpoint, headers={"token": TOKEN}, params=params, timeout=30)
    r.raise_for_status()
    res = r.json()
    if res.get("error_code") != 0:
        raise RuntimeError(f"{endpoint}: {res.get('error_msg')}")
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
    df = pd.DataFrame([{
        "time": datetime.fromtimestamp(x["calendar"] / 1000, TW_TZ).replace(tzinfo=None),
        "pv": x.get("ppv", 0) / 1000,
        "load": x.get("plocalLoadTotal", 0) / 1000,
        "charge": x.get("pcharge1", 0) / 1000,
        "discharge": x.get("pdischarge1", 0) / 1000,
        "buy": x.get("pacToUserTotal", 0) / 1000,
        "sell": x.get("pacToGridTotal", 0) / 1000,
        "soc": x.get("soc", 0),
    } for x in rows])
    return df.sort_values("time") if not df.empty else df


@st.cache_data(ttl=3600)
def fetch_monthly(year):
    d = api_v1("plant/energy", plant_id=PLANT_ID, time_unit="month",
               start_date=f"{year}-01-01", end_date=f"{year}-12-31")
    return pd.DataFrame([{"month": e["date"], "kwh": float(e["energy"])} for e in d.get("energys", [])])


try:
    s = fetch_realtime()
except Exception as e:
    st.error(f"⚠️ 連線異常: {e}")
    st.stop()

now_tw = datetime.now(TW_TZ)
last_update = datetime.fromtimestamp(s["calendar"] / 1000, TW_TZ)
stale = now_tw - last_update > timedelta(minutes=20)

f = lambda k: float(s.get(k) or 0)
pv_power = [f("ppv1") / 1000, f("ppv2") / 1000, f("ppv3") / 1000]
pv_volts = [f("vpv1"), f("vpv2"), f("vpv3")]
p_charge, p_discharge = f("pcharge1") / 1000, f("pdischarge1") / 1000
p_buy, p_sell = f("pacToUserTotal") / 1000, f("pacToGridTotal") / 1000
load_kw = f("plocalLoadTotal") / 1000
soc = int(f("soc"))

batt_status = "🔋 充電中" if p_charge > 0 else ("⚡ 放電中" if p_discharge > 0 else "💤 電池待機")
batt_power = p_charge if p_charge > 0 else p_discharge
if p_buy > 0:
    grid_status = "🔌 向台電買電"
elif p_sell > 0:
    grid_status = "📤 賣電至電網"
else:
    grid_status = "⚖️ 自給自足"

# ==========================================
# 3. 頂端標題與時間
# ==========================================
conn_color, conn_text = ("#F59E0B", "● 資料延遲") if stale else ("#10B981", "● 系統即時連線中")
st.markdown(f"""
<div style="display: flex; justify-content: space-between; align-items: flex-end; flex-wrap: wrap; gap: 10px; padding-bottom: 10px; border-bottom: 1px solid #334155; margin-bottom: 20px;">
    <div>
        <div style="color: #38BDF8; font-size: 32px; font-weight: 800; letter-spacing: 1px;">澔宸小窩 智慧能源戰情中心</div>
        <div style="color: #94A3B8; font-size: 14px; text-transform: uppercase; letter-spacing: 2px;">SPH 10000TL-HU Hybrid Microgrid Dashboard · {CAPACITY_KWP} kWp</div>
    </div>
    <div style="text-align: right;">
        <div style="color: {conn_color}; font-size: 14px; font-weight: bold;">{conn_text}</div>
        <div style="color: #F8FAFC; font-size: 20px; font-family: monospace;">資料時間 {last_update.strftime("%Y-%m-%d %H:%M")}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# 4. 三大核心指標
# ==========================================
col1, col2, col3 = st.columns([1.5, 1.5, 1])

with col1:
    st.markdown(f"""<div class="metric-card">
        <div>
            <div class="sub-text">🌞 今日太陽能總產出 (PV Yield)</div>
            <div style="display:flex; align-items:baseline; margin-top:10px;">
                <span class="glow-text-green" style="font-size: 70px; line-height: 1.1;">{f('epvToday'):.1f}</span>
                <span style="color:#64748B; font-size:20px; margin-left:10px;">kWh</span>
            </div>
        </div>
        <div style="margin-top:auto; border-top:1px dashed #334155; padding-top:15px; display:flex; justify-content:space-between;">
            <div><div class="sub-text">今日每 kWp 發電</div><div style="color:#F8FAFC; font-size:24px;">{f('epvToday') / CAPACITY_KWP:.2f} <span style="font-size:14px; color:#64748B;">h</span></div></div>
            <div style="text-align:right;"><div class="sub-text">建站總發電</div><div style="color:#F8FAFC; font-size:24px;">{f('epvTotal'):,.1f} <span style="font-size:14px; color:#64748B;">kWh</span></div></div>
        </div>
    </div>""", unsafe_allow_html=True)

with col2:
    st.markdown(f"""<div class="metric-card">
        <div>
            <div class="sub-text">🏠 家庭用電概況 (Local Load)</div>
            <div style="display:flex; align-items:baseline; margin-top:10px;">
                <span class="glow-text-orange" style="font-size: 70px; line-height: 1.1;">{load_kw:.2f}</span>
                <span style="color:#64748B; font-size:20px; margin-left:10px;">kW</span>
            </div>
        </div>
        <div style="margin-top:auto; border-top:1px dashed #334155; padding-top:15px; display:flex; justify-content:space-between;">
            <div><div class="sub-text">今日用電 / 買電</div><div style="color:#F8FAFC; font-size:24px;">{f('elocalLoadToday'):.1f} / {f('etoUserToday'):.1f} <span style="font-size:14px; color:#64748B;">kWh</span></div></div>
            <div style="text-align:right;"><div class="sub-text">市電狀態</div><div style="color:#38BDF8; font-size:20px;">{grid_status}</div></div>
        </div>
    </div>""", unsafe_allow_html=True)

with col3:
    st.markdown(f"""<div class="metric-card" style="align-items:center;">
        <div class="sub-text">🔋 儲能電池 (SOC)</div>
        <div style="position:relative; width:150px; height:150px; margin:15px auto; border-radius:50%;
             background: conic-gradient(#8B5CF6 {soc * 3.6}deg, #1E293B 0deg); box-shadow: 0 0 25px rgba(139,92,246,0.35);">
            <div style="position:absolute; inset:14px; border-radius:50%; background:#0F172A; display:flex; align-items:center; justify-content:center;">
                <span class="glow-text-purple" style="font-size:40px;">{soc}%</span>
            </div>
        </div>
        <div style="margin-top:auto; color:#E2E8F0; font-size:16px;">{batt_status} : <span style="color:#A78BFA; font-weight:bold;">{batt_power:.2f} kW</span></div>
        <div class="sub-text" style="margin-top:6px;">今日充 {f('echarge1Today'):.1f} / 放 {f('edischarge1Today'):.1f} kWh</div>
    </div>""", unsafe_allow_html=True)

# ==========================================
# 5. 即時能源流向
# ==========================================
st.markdown("<div class='section-title'>⚡ 即時能源流向 (Power Flow)</div>", unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns(4)


def mini_card(icon, title, value, unit, color, details):
    return f"""<div style="background:#1E293B; border-radius:8px; padding:15px; border-left:4px solid {color}; box-shadow: 0 2px 10px rgba(0,0,0,0.2);">
    <div style="color:#94A3B8; font-size:14px; display:flex; justify-content:space-between;"><span>{icon} {title}</span><span style="color:{color}; font-size:20px; font-weight:bold;">{value:.2f}<span style="font-size:12px; color:#64748B;"> {unit}</span></span></div>
    <div style="margin-top:10px; font-size:12px; color:#64748B;">{details}</div></div>"""


c1.markdown(mini_card("☀️", "太陽能總輸出", f("ppv") / 1000, "kW", "#10B981", "三組陣列即時發電功率總和"), unsafe_allow_html=True)
c2.markdown(mini_card("🔋", "電池端充放", batt_power, "kW", "#8B5CF6", f"電池溫度: {f('bmsBatteryTemp'):.1f} °C | {batt_status}"), unsafe_allow_html=True)
c3.markdown(mini_card("🏠", "負載端消耗", load_kw, "kW", "#F97316", "供應家中電器即時負載"), unsafe_allow_html=True)
c4.markdown(mini_card("⚡", "市電端", p_buy if p_buy > 0 else p_sell, "kW", "#38BDF8", f"狀態: {grid_status}"), unsafe_allow_html=True)

st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)

# ==========================================
# 6. 今日能源曲線 (官方 5 分鐘資料) 與各陣列發電
# ==========================================
chart_layout = dict(
    paper_bgcolor="#1E293B", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#94A3B8"),
    title_font=dict(color="#E2E8F0", size=16), title_x=0.02, title_y=0.96,
    height=380, margin=dict(l=50, r=50, t=70, b=40),
    legend=dict(orientation="h", y=1.0, yanchor="bottom", x=1, xanchor="right", font=dict(color="#FFFFFF")),
)
col_bar, col_line = st.columns([1, 2.5])

with col_bar:
    fig_bar = go.Figure(go.Bar(
        x=["PV1", "PV2", "PV3"], y=pv_power, marker_color=["#10B981", "#F59E0B", "#F97316"],
        text=[f"{v:.2f}" for v in pv_power], textposition="outside", textfont=dict(color="#fff", size=14),
    ))
    fig_bar.update_layout(**chart_layout, title="📊 即時各陣列發電 (kW)",
                          yaxis=dict(gridcolor="#334155", range=[0, max(max(pv_power) * 1.25, 1)]))
    st.plotly_chart(fig_bar, use_container_width=True, theme=None)

with col_line:
    try:
        curve = fetch_day_curve(now_tw.strftime("%Y-%m-%d"))
    except Exception as e:
        curve = pd.DataFrame()
        st.warning(f"今日曲線讀取失敗: {e}")
    fig_line = go.Figure()
    if not curve.empty:
        fig_line.add_trace(go.Scatter(x=curve["time"], y=curve["pv"], name="太陽能", line=dict(color="#10B981", width=2), fill="tozeroy", fillcolor="rgba(16,185,129,0.15)"))
        fig_line.add_trace(go.Scatter(x=curve["time"], y=curve["load"], name="負載", line=dict(color="#F97316", width=2)))
        fig_line.add_trace(go.Scatter(x=curve["time"], y=curve["charge"] - curve["discharge"], name="電池 (+充/−放)", line=dict(color="#8B5CF6", width=1.5)))
        fig_line.add_trace(go.Scatter(x=curve["time"], y=curve["buy"], name="買電", line=dict(color="#38BDF8", width=1.5, dash="dot")))
        fig_line.add_trace(go.Scatter(x=curve["time"], y=curve["soc"], name="SOC %", yaxis="y2", line=dict(color="#E2E8F0", width=1, dash="dash")))
    fig_line.update_layout(**chart_layout, title="📈 今日能源曲線 (每 5 分鐘)",
                           xaxis=dict(showgrid=False, tickformat="%H:%M"),
                           yaxis=dict(gridcolor="#334155", title="kW"),
                           yaxis2=dict(overlaying="y", side="right", range=[0, 105], tickvals=[0, 25, 50, 75, 100], showgrid=False, title="SOC %"))
    st.plotly_chart(fig_line, use_container_width=True, theme=None)

# ==========================================
# 6.5 限發分析：電池充滿且不賣電時，PV 會被壓到只供負載
# ==========================================
if not curve.empty:
    daylight = curve[(curve["time"].dt.hour >= 9) & (curve["time"].dt.hour < 15)]
    # 電池近滿、無充電、無賣電，且 PV 幾乎等於負載 → 判定為限發
    limited = daylight[(daylight["soc"] >= 95) & (daylight["charge"] < 0.1) & (daylight["sell"] < 0.05)
                       & (daylight["pv"] > 0) & ((daylight["pv"] - daylight["load"]).abs() < 0.5)]
    limited_hours = len(limited) * 5 / 60
    first_full = curve[curve["soc"] >= 97]["time"].min()
    st.markdown(f"""<div style="background:#1E293B; border-radius:8px; padding:15px; border-left:4px solid #F59E0B; margin-top:10px;">
        <div style="color:#F59E0B; font-weight:bold; font-size:16px;">⚠️ 今日限發觀測</div>
        <div style="color:#E2E8F0; font-size:14px; margin-top:6px;">
        電池首次充滿：<b>{first_full.strftime('%H:%M') if pd.notna(first_full) else '尚未充滿'}</b> ｜
        09–15 時受限發電時段：<b>{limited_hours:.1f} 小時</b> ｜ 今日賣電：<b>{f('etoGridToday'):.1f} kWh</b><br>
        <span style="color:#94A3B8;">系統設定為零饋電，電池充滿後 PV 只會發到等於家中負載，其餘日照無法利用。</span>
        </div></div>""", unsafe_allow_html=True)

# ==========================================
# 7. 每月發電量
# ==========================================
st.markdown("<div class='section-title' style='margin-top:20px;'>🗓️ 每月發電量</div>", unsafe_allow_html=True)
try:
    years = range(2024, now_tw.year + 1)
    monthly = pd.concat([fetch_monthly(y) for y in years], ignore_index=True)
    monthly = monthly[monthly["kwh"] > 0]
    monthly["kwh_per_kwp"] = monthly["kwh"] / CAPACITY_KWP
    fig_m = go.Figure(go.Bar(x=monthly["month"], y=monthly["kwh"], marker_color="#10B981",
                             customdata=monthly["kwh_per_kwp"],
                             hovertemplate="%{x}<br>%{y:.0f} kWh<br>%{customdata:.0f} kWh/kWp<extra></extra>"))
    fig_m.update_layout(**{**chart_layout, "height": 320}, title="每月 PV 發電 (kWh)",
                        xaxis=dict(type="category", showgrid=False), yaxis=dict(gridcolor="#334155"))
    st.plotly_chart(fig_m, use_container_width=True, theme=None)
except Exception as e:
    st.warning(f"月報讀取失敗: {e}")

# ==========================================
# 8. 系統核心運行參數
# ==========================================
st.markdown("<div class='section-title'>⚙️ 系統核心運行參數 (System Diagnostics)</div>", unsafe_allow_html=True)
diag1, diag2, diag3, diag4 = st.columns(4)


def diag_card(title, icon, color, rows):
    rows_html = "".join([f'<div style="display:flex; justify-content:space-between; margin-bottom:8px; font-size:14px;"><span style="color:#94A3B8;">{k}</span><span style="color:#F8FAFC; font-weight:500; font-family:monospace;">{v}</span></div>' for k, v in rows])
    return f"""<div style="background:#1E293B; border-radius:8px; padding:15px; border-top:3px solid {color}; border-left:1px solid #334155; border-right:1px solid #334155; border-bottom:1px solid #334155; box-shadow: 0 2px 10px rgba(0,0,0,0.2); height: 100%;">
    <div style="color:{color}; font-size:16px; font-weight:bold; border-bottom:1px solid #334155; padding-bottom:8px; margin-bottom:12px;">{icon} {title}</div>
    {rows_html}
    </div>"""


with diag1:
    st.markdown(diag_card("太陽能陣列 (MPPT)", "☀️", "#10B981", [
        (f"PV{i + 1} 電壓 / 功率", f"{pv_volts[i]:.1f} V / {pv_power[i] * 1000:.0f} W") for i in range(3)
    ]), unsafe_allow_html=True)

with diag2:
    st.markdown(diag_card("市電電網端 (Grid)", "⚡", "#38BDF8", [
        ("電網電壓 L1 / L2", f"{f('vac1'):.1f} / {f('vac2'):.1f} V"),
        ("電網頻率", f"{f('fac'):.2f} Hz"),
        ("累計買電 / 賣電", f"{f('etoUserTotal'):.0f} / {f('etoGridTotal'):.0f} kWh"),
    ]), unsafe_allow_html=True)

with diag3:
    st.markdown(diag_card("離網備用端 (EPS)", "🏠", "#F59E0B", [
        ("離網電壓", f"{f('upsVac1'):.1f} V"),
        ("離網頻率", f"{f('upsFac'):.2f} Hz"),
        ("離網輸出功率", f"{f('upsPac1'):.0f} W"),
    ]), unsafe_allow_html=True)

with diag4:
    charged, discharged = f("echarge1Total"), f("edischarge1Total")
    st.markdown(diag_card("主機與電池端 (System)", "🔋", "#8B5CF6", [
        ("電池電壓 / 溫度", f"{f('vbat'):.1f} V / {f('bmsBatteryTemp'):.1f} °C"),
        ("逆變器溫度", f"{f('invTemp'):.1f} °C"),
        ("累計充 / 放電", f"{charged:,.0f} / {discharged:,.0f} kWh"),
        ("累計放充比", f"{discharged / charged * 100:.0f} %" if charged else "-"),
    ]), unsafe_allow_html=True)

st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
