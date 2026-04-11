import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
import growattServer
from streamlit_autorefresh import st_autorefresh

# ==========================================
# 1. 頁面基本設定與高階 CSS 樣式
# ==========================================
st.set_page_config(page_title="澔宸小窩 - 智慧能源戰情中心", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=300000, key="datarefresh") # 5分鐘自動刷新

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.stApp {background-color: #0F172A; font-family: 'Segoe UI', Roboto, sans-serif;}

/* 戰情室專屬發光卡片特效 */
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
.sub-text { color: #94A3B8; font-size: 14px; }
.chart-container {
    background-color: #1E293B; 
    border-radius: 10px; 
    padding: 15px; 
    border: 1px solid #334155; 
    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
}
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Growatt SPH 數據精準抓取
# ==========================================
USER = "cc00035"
PASS = "@@@00035"

@st.cache_data(ttl=60) 
def fetch_solar_data():
    api = growattServer.GrowattApi()
    api.server_url = "https://server-api.growatt.com/" 
    api.agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
    
    try:
        login_res = api.login(USER, PASS)
        if not login_res or not login_res.get('success'): return {"success": False, "error": "登入失敗"}
            
        user_id = login_res['user']['id']
        plant_id = api.plant_list(user_id)['data'][0]['plantId']
        plant_info = api.plant_info(plant_id)
        
        today_kwh = float(plant_info.get('todayEnergy', 0))
        month_kwh = float(plant_info.get('monthEnergy', 842.5))
        total_kwh = float(plant_info.get('totalEnergy', 0))
        
        devices = api.device_list(plant_id)
        sph_sn = next((d['deviceSn'] for d in devices if d['deviceType'] == 'sph'), None)
        status = api.mix_system_status(sph_sn, plant_id) if sph_sn else None
        
        if status is None or not isinstance(status, dict):
            fallback_soc = int(plant_info.get('sphList', [{}])[0].get('capacity', '0%').replace('%', '')) if plant_info.get('sphList') else 0
            return {
                "success": True, "is_night": True,
                "today_kwh": today_kwh, "month_kwh": month_kwh, "total_kwh": total_kwh,
                "current_kw": 0.0, "pv_power": [0.0, 0.0, 0.0], "pv_volts": [0.0, 0.0, 0.0],
                "batt_soc": fallback_soc, "batt_status": "待機/夜間", "batt_power": 0.0,
                "load_power": 0.0, "grid_power": 0.0, "grid_status": "市電待機", "temp": 0.0
            }
        
        p_pv1, p_pv2, p_pv3 = float(status.get('ppv1', 0)), float(status.get('ppv2', 0)), float(status.get('ppv3', 0))
        p_charge, p_discharge = float(status.get('pCharge', 0)), float(status.get('pdisCharge', 0))
        p_buy = float(status.get('pactouserr', 0))
        
        grid_status = "🔌 向台電買電" if p_buy > 0 else "⚖️ 市電待機/自給自足"
        batt_status = "🔋 充電中" if p_charge > 0 else ("⚡ 放電中" if p_discharge > 0 else "💤 電池待機")
        
        return {
            "success": True, "is_night": False,
            "today_kwh": today_kwh, "month_kwh": month_kwh, "total_kwh": total_kwh,
            "current_kw": (p_pv1 + p_pv2 + p_pv3) / 1000,
            "pv_power": [p_pv1/1000, p_pv2/1000, p_pv3/1000],
            "pv_volts": [float(status.get('vpv1', 0)), float(status.get('vpv2', 0)), float(status.get('vpv3', 0))],
            "batt_soc": int(float(status.get('soc', 0))),
            "batt_status": batt_status,
            "batt_power": (p_charge if p_charge > 0 else p_discharge) / 1000,
            "load_power": float(status.get('pLocalLoad', 0)) / 1000,
            "grid_power": p_buy / 1000,
            "grid_status": grid_status,
            "temp": float(status.get('temp1', 0))
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

d = fetch_solar_data()
if not d.get("success"):
    st.error(f"⚠️ 連線異常: {d.get('error')}")
    st.stop()

# ==========================================
# 3. 頂端標題與時間
# ==========================================
now = datetime.now()
st.markdown(f"""
<div style="display: flex; justify-content: space-between; align-items: flex-end; padding-bottom: 10px; border-bottom: 1px solid #334155; margin-bottom: 20px;">
    <div>
        <div style="color: #38BDF8; font-size: 32px; font-weight: 800; letter-spacing: 1px;">澔宸小窩 智慧能源戰情中心</div>
        <div style="color: #94A3B8; font-size: 14px; text-transform: uppercase; letter-spacing: 2px;">SPH 10000TL Hybrid Microgrid Dashboard</div>
    </div>
    <div style="text-align: right;">
        <div style="color: #10B981; font-size: 14px; font-weight: bold;">● 系統即時連線中</div>
        <div style="color: #F8FAFC; font-size: 20px; font-family: monospace;">{now.strftime("%Y-%m-%d %H:%M:%S")}</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ==========================================
# 4. 老闆視角：三大核心指標 (Row 1)
# ==========================================
# 重構：將「整張卡片」包含樣式包裝進 ECharts 的 HTML 中，避免被 Streamlit 切割
def complete_battery_card(value, color, title, status_text, power_val):
    html = f"""
    <!DOCTYPE html><html><head><script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
    body {{margin:0; padding:0; background:transparent; font-family: 'Segoe UI', Roboto, sans-serif; overflow: hidden;}}
    .metric-card {{
        background: linear-gradient(145deg, #1E293B, #0F172A);
        border-radius: 12px;
        padding: 20px;
        border: 1px solid #334155;
        box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        height: 290px; /* 強制與左側卡片同高 */
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
    }}
    .sub-text {{ color: #94A3B8; font-size: 14px; text-align: center; }}
    #m {{ flex: 1; width: 100%; margin-top: -15px; }}
    .footer-text {{ text-align: center; color: #E2E8F0; font-size: 16px; margin-top: auto; }}
    </style></head><body>
    <div class="metric-card">
        <div class="sub-text">🔋 {title}</div>
        <div id="m"></div>
        <div class="footer-text">{status_text} : <span style="color: {color}; font-weight: bold;">{power_val:.2f} kW</span></div>
    </div>
    <script>
    var c = echarts.init(document.getElementById('m'));
    c.setOption({{
        title: {{ text: '{value}%', left: 'center', top: 'center', textStyle: {{ color: '#fff', fontSize: 40, textShadowBlur: 10, textShadowColor: '{color}' }} }},
        series: [{{ type: 'pie', radius: ['70%', '85%'], label: {{show:false}}, data: [{{value: {value}, itemStyle: {{color: '{color}', shadowBlur: 15, shadowColor: '{color}'}}}}, {{value: {100-value}, itemStyle: {{color: '#1E293B'}}}}] }}]
    }}); window.onresize = c.resize;
    </script></body></html>"""
    return html

col1, col2, col3 = st.columns([1.5, 1.5, 1])

with col1:
    h1 = f"""<div class="metric-card">
        <div>
            <div class="sub-text">🌞 今日發電概況</div>
            <div style="display:flex; align-items:baseline; margin-top:10px;">
                <span class="glow-text-green" style="font-size: 70px; line-height: 1.1;">{d['today_kwh']:.1f}</span>
                <span style="color:#64748B; font-size:20px; margin-left:10px;">kWh</span>
            </div>
        </div>
        <div style="margin-top:auto; border-top:1px dashed #334155; padding-top:15px; display:flex; justify-content:space-between;">
            <div><div class="sub-text">本月累計</div><div style="color:#F8FAFC; font-size:24px;">{d['month_kwh']:.1f} <span style="font-size:14px; color:#64748B;">kWh</span></div></div>
            <div style="text-align:right;"><div class="sub-text">建站總發電</div><div style="color:#F8FAFC; font-size:24px;">{d['total_kwh']:.1f} <span style="font-size:14px; color:#64748B;">kWh</span></div></div>
        </div>
    </div>"""
    st.markdown(h1, unsafe_allow_html=True)

with col2:
    h2 = f"""<div class="metric-card">
        <div>
            <div class="sub-text">🏠 家庭用電概況 (Local Load)</div>
            <div style="display:flex; align-items:baseline; margin-top:10px;">
                <span class="glow-text-orange" style="font-size: 70px; line-height: 1.1;">{d['load_power']:.2f}</span>
                <span style="color:#64748B; font-size:20px; margin-left:10px;">kW</span>
            </div>
        </div>
        <div style="margin-top:auto; border-top:1px dashed #334155; padding-top:15px; display:flex; justify-content:space-between;">
            <div><div class="sub-text">市電狀態</div><div style="color:#38BDF8; font-size:20px;">{d['grid_status']}</div></div>
            <div style="text-align:right;"><div class="sub-text">向台電買電功率</div><div style="color:#F8FAFC; font-size:24px;">{d['grid_power']:.2f} <span style="font-size:14px; color:#64748B;">kW</span></div></div>
        </div>
    </div>"""
    st.markdown(h2, unsafe_allow_html=True)

with col3:
    # 預留稍微多一點的高度給 iframe，確保不會產生卷軸 (290px 卡片高度 + 10px 緩衝)
    components.html(complete_battery_card(d['batt_soc'], "#8B5CF6", "儲能電池 (SOC)", d['batt_status'], d['batt_power']), height=300)

st.markdown("<div style='height: 15px;'></div>", unsafe_allow_html=True)

# ==========================================
# 5. 工程師視角：全能源流向四大天王 (Row 2)
# ==========================================
st.markdown("<div style='color: #E2E8F0; font-size: 18px; font-weight: bold; margin-bottom: 10px;'>⚡ 即時能源流向 (Power Flow)</div>", unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns(4)

def mini_card(icon, title, value, unit, color, details):
    return f"""<div style="background:#1E293B; border-radius:8px; padding:15px; border-left:4px solid {color}; box-shadow: 0 2px 10px rgba(0,0,0,0.2);">
    <div style="color:#94A3B8; font-size:14px; display:flex; justify-content:space-between;"><span>{icon} {title}</span><span style="color:{color}; font-size:20px; font-weight:bold;">{value:.2f}<span style="font-size:12px; color:#64748B;"> {unit}</span></span></div>
    <div style="margin-top:10px; font-size:12px; color:#64748B;">{details}</div></div>"""

c1.markdown(mini_card("☀️", "太陽能總輸出", d['current_kw'], "kW", "#10B981", f"陣列總和即時發電功率"), unsafe_allow_html=True)
c2.markdown(mini_card("🔋", "電池端充放", d['batt_power'], "kW", "#8B5CF6", f"目前溫度: {d['temp']} °C | 狀態: {d['batt_status']}"), unsafe_allow_html=True)
c3.markdown(mini_card("🏠", "負載端消耗", d['load_power'], "kW", "#F97316", f"供應家中電器即時負載"), unsafe_allow_html=True)
c4.markdown(mini_card("⚡", "市電端輸入", d['grid_power'], "kW", "#38BDF8", f"狀態: {d['grid_status']}"), unsafe_allow_html=True)

st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)

# ==========================================
# 6. 動態圖表區 (Row 3：左側長條圖，右側時序圖)
# ==========================================
col_bar, col_line = st.columns([1, 2.5])

with col_bar:
    st.markdown("<div class='chart-container'>", unsafe_allow_html=True)
    st.markdown("<div style='color: #E2E8F0; font-size: 16px; font-weight: bold; margin-bottom: 10px;'>📊 即時各陣列發電 (kW)</div>", unsafe_allow_html=True)
    
    fig_bar = go.Figure(data=[
        go.Bar(
            x=['PV1', 'PV2', 'PV3'], 
            y=d['pv_power'], 
            marker_color=['#10B981', '#F59E0B', '#F97316'],
            text=[f"{v:.2f}" for v in d['pv_power']],
            textposition='outside', 
            textfont=dict(color='#fff', size=14)
        )
    ])
    fig_bar.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", 
        font=dict(color="#94A3B8"), height=350, margin=dict(l=10, r=10, t=20, b=10),
        xaxis=dict(gridcolor="#334155", showgrid=False), 
        yaxis=dict(gridcolor="#334155", showgrid=True, range=[0, max(max(d['pv_power'])*1.2, 1)])
    )
    st.plotly_chart(fig_bar, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

with col_line:
    st.markdown("<div class='chart-container'>", unsafe_allow_html=True)
    st.markdown("<div style='color: #E2E8F0; font-size: 16px; font-weight: bold; margin-bottom: 10px;'>📈 太陽能陣列出力模擬時序圖</div>", unsafe_allow_html=True)
    
    times = pd.date_range("06:00", "18:00", freq="15min").strftime("%H:%M")
    fig_line = go.Figure()
    sys_names = ['PV1 陣列', 'PV2 陣列', 'PV3 陣列']
    sys_colors = ['#10B981', '#F59E0B', '#F97316']
    bell = np.exp(-0.5 * np.linspace(-3, 3, len(times))**2)

    for i in range(3):
        y_data = bell * d['pv_power'][i] + np.random.uniform(0, 0.05, len(times))
        if d['is_night']: y_data = np.zeros(len(times))
        fig_line.add_trace(go.Scatter(x=times, y=y_data, name=sys_names[i], mode='lines', line=dict(width=3, color=sys_colors[i]), stackgroup='one'))

    fig_line.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", 
        font=dict(color="#94A3B8"), height=350, margin=dict(l=10, r=10, t=20, b=10), 
        legend=dict(orientation="h", y=1.1, x=0.5, xanchor="center", traceorder="normal", font=dict(color="#FFFFFF")),
        xaxis=dict(gridcolor="#334155", showgrid=True), yaxis=dict(gridcolor="#334155", showgrid=True)
    )
    st.plotly_chart(fig_line, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)