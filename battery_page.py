"""🔋 儲能分頁：4 組 JK BMS 電池的總覽、各組電量、單體電壓熱圖、溫度與保護設定。

資料格式 (data/bms_snapshot.json，之後由樹莓派即時更新同樣的格式)：
  {"time": ISO 時間, "source": 說明, "packs": [{addr, cells[16], avg, delta, volt, curr, soc, remain_ah, full_ah,
   t1, t2, mos_t, soh, runtime_h, alarm, bal, chg_on, dis_on, ...}], "settings": {中文名稱: 原始值}}
"""
import html
import json
import os
from datetime import datetime

NOM_V = 51.2           # 磷酸鐵鋰 16 串標稱電壓
FLOOR = 20             # 逆變器放電下限 SOC %


def load_snapshot(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bms_snapshot.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _soc_color(soc):
    return "#EF4444" if soc < 25 else "#F59E0B" if soc < 45 else "#34D399"


def _cell_color(dev_mv):
    """單體電壓跟全部平均的差 (mV) → 顏色：差越多越偏暖色。"""
    a = min(abs(dev_mv) / 15, 1)
    if dev_mv >= 0:   # 偏高：綠 → 橘
        r, g, b = int(52 + (245 - 52) * a), int(211 + (158 - 211) * a), int(153 + (11 - 153) * a)
    else:             # 偏低：綠 → 藍
        r, g, b = int(52 + (96 - 52) * a), int(211 + (165 - 211) * a), int(153 + (250 - 153) * a)
    return f"rgb({r},{g},{b})"


def _battery_icon(soc, color, charging):
    """純 CSS 電池圖示 (st.html 會濾掉 SVG)。"""
    bolt = '<div class="bolt">⚡</div>' if charging else ""
    return (f'<div class="batt"><div class="cap"></div><div class="body">'
            f'<div class="fill" style="height:{soc}%;background:{color}"></div>{bolt}</div></div>')


def render(snap, live_soc=None):
    """回傳完整的 HTML 字串 (給 st.components.v1.html 用)。"""
    if not snap or not snap.get("packs"):
        return "<div style='color:#94A3B8;font-family:sans-serif;padding:20px'>還沒有電池 BMS 資料</div>"
    packs = sorted(snap["packs"], key=lambda p: p["addr"])
    n = len(packs)
    total_ah = sum(p["full_ah"] for p in packs)
    remain_ah = sum(p["remain_ah"] for p in packs)
    soc = remain_ah / total_ah * 100 if total_ah else 0
    stored = remain_ah * NOM_V / 1000
    full_kwh = total_ah * NOM_V / 1000
    curr = sum(p["curr"] for p in packs)
    volt = sum(p["volt"] for p in packs) / n
    power = curr * volt / 1000
    cells = [c for p in packs for c in p["cells"]]
    avg_cell = sum(cells) / len(cells)
    delta_all = (max(cells) - min(cells)) * 1000
    delta_pack = max(p["delta"] for p in packs) * 1000
    temps = [t for p in packs for t in (p["t1"], p["t2"])]
    mos = [p["mos_t"] for p in packs]
    alarms = [p for p in packs if p.get("alarm")]
    t = datetime.fromisoformat(snap["time"])
    state = ("充電中" if curr > 1 else "放電中" if curr < -1 else "待機")
    state_c = "#34D399" if curr > 1 else "#60A5FA" if curr < -1 else "#94A3B8"
    ok = not alarms and delta_pack <= 30 and max(temps) < 45
    chip = (f'<span class="chip ok">● 全部正常</span>' if ok else
            f'<span class="chip warn">● 需要注意</span>')
    to_full = (100 - soc) / 100 * full_kwh
    to_floor = max(soc - FLOOR, 0) / 100 * full_kwh

    # 各組卡片
    pack_cards = []
    for p in packs:
        c = _soc_color(p["soc"])
        name = "主機" if p["addr"] == 0 else f"從機 {p['addr']}"
        pc = p["curr"]
        cur_txt = f'<span style="color:{"#34D399" if pc > 0.5 else "#60A5FA" if pc < -0.5 else "#94A3B8"}">{pc:+.1f} A</span>'
        pack_cards.append(f'''<div class="pack">
          <div class="pack-head"><span class="addr">#{p["addr"]}</span><span class="pname">{name}</span></div>
          <div class="pack-body">
            {_battery_icon(p["soc"], c, pc > 0.5)}
            <div class="pack-stats">
              <div class="big" style="color:{c}">{p["soc"]}<small>%</small></div>
              <div class="sub">{p["remain_ah"]:.0f} / {p["full_ah"]:.0f} Ah</div>
              <div class="row"><span>電壓</span><b>{p["volt"]:.2f} V</b></div>
              <div class="row"><span>電流</span><b>{cur_txt}</b></div>
              <div class="row"><span>壓差</span><b>{p["delta"] * 1000:.0f} mV</b></div>
              <div class="row"><span>溫度</span><b>{max(p["t1"], p["t2"]):.1f}°C</b></div>
              <div class="row"><span>MOS</span><b>{p["mos_t"]:.1f}°C</b></div>
              <div class="row"><span>SOH</span><b>{p["soh"]}%</b></div>
            </div>
          </div>
        </div>''')

    # 單體電壓熱圖 (每組一列 16 格)
    heat_rows = []
    for p in packs:
        mx, mn = max(p["cells"]), min(p["cells"])
        imx, imn = p["cells"].index(mx), p["cells"].index(mn)   # 只標第一顆最高、最低
        tds = "".join(
            f'<div class="cell" style="background:{_cell_color((v - avg_cell) * 1000)}" '
            f'title="#{p["addr"]} 第 {i + 1} 顆：{v:.3f} V（{(v - avg_cell) * 1000:+.0f} mV）">'
            f'{"▲" if i == imx and mx != mn else "▼" if i == imn and mx != mn else ""}<span>{v * 1000 - 3000:.0f}</span></div>'
            for i, v in enumerate(p["cells"]))
        heat_rows.append(f'<div class="hrow"><div class="hlabel">#{p["addr"]}</div>{tds}'
                         f'<div class="hend">{p["delta"] * 1000:.0f} mV</div></div>')
    head_nums = "".join(f'<div class="hnum">{i}</div>' for i in range(1, 17))

    # 保護設定
    s = snap.get("settings", {})
    v = lambda k, d=1000: s.get(k, 0) / d
    settings_rows = [
        ("充滿 / 浮充", f"{v('建議充電電壓 RCV'):.2f} V → {v('浮充電壓 RFV'):.2f} V"),
        ("SOC 校正點", f"100% @ {v('SOC-100% 電壓'):.2f} V · 0% @ {v('SOC-0% 電壓'):.2f} V"),
        ("單體過壓 / 欠壓", f"{v('單體過壓保護'):.2f} V / {v('單體欠壓保護'):.2f} V"),
        ("均衡", f"{v('均衡起始電壓'):.2f} V 起 · 壓差 {s.get('均衡觸發壓差', 0)} mV · 最大 {v('最大均衡電流'):.0f} A"),
        ("充電溫度", f"{v('充電低溫保護', 10):.0f}–{v('充電過溫保護', 10):.0f}°C"),
        ("放電溫度", f"≤ {v('放電過溫保護', 10):.0f}°C · MOS ≤ {v('MOS 過溫保護', 10):.0f}°C"),
        ("每組電流上限", f"充 {v('最大充電電流'):.0f} A · 放 {v('最大放電電流'):.0f} A"),
    ]
    settings_html = "".join(f'<div class="srow"><span>{a}</span><b>{b}</b></div>' for a, b in settings_rows)
    runtime = "、".join(f"#{p['addr']} {p['runtime_h'] / 24:.0f} 天" for p in packs)
    note_live = (f"<span class='muted'>（逆變器目前顯示 {live_soc}%）</span>" if live_soc is not None else "")

    # 直接用 st.html 插進頁面 (不用 iframe，高度自動)；所有樣式都限定在 .bp 底下，不影響網站其他部分
    return f'''<style>
    .bp {{ --card:#1E293B; --line:#334155; --txt:#E2E8F0; --mut:#94A3B8; --dim:#64748B;
           color:var(--txt); font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif; }}
    .bp, .bp * {{ box-sizing:border-box; }}
    .bp .wrap {{ display:flex; flex-direction:column; gap:14px; }}
    .bp .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:18px 20px; }}
    .bp .hero {{ display:grid; grid-template-columns:minmax(220px,300px) 1fr; gap:22px; align-items:center;
                background:linear-gradient(135deg,#1E1B4B 0%,#1E293B 55%); border-color:#4C1D95; }}
    .bp .ring {{ position:relative; width:100%; max-width:240px; aspect-ratio:1; margin:auto; border-radius:50%;
                box-shadow:0 0 40px #34D39922; }}
    .bp .ring .mid {{ position:absolute; inset:11%; border-radius:50%; background:#18173A; display:flex; flex-direction:column;
                     align-items:center; justify-content:center; }}
    .bp .ring .tick {{ position:absolute; left:50%; top:0; width:2px; height:50%; margin-left:-1px; transform-origin:50% 100%; }}
    .bp .ring .tick::before {{ content:""; position:absolute; top:0; left:0; width:2px; height:11%; background:#F8FAFC; opacity:0.7; }}
    .bp .ring .flr {{ color:var(--dim); font-size:11px; margin-top:4px; }}
    .bp .ring .pct {{ font-size:52px; font-weight:800; line-height:1; }}
    .bp .ring .pct small {{ font-size:22px; color:var(--mut); }}
    .bp .ring .kwh {{ color:var(--mut); font-size:14px; margin-top:6px; }}
    .bp .title {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:20px; font-weight:700; }}
    .bp .chip {{ font-size:12px; padding:3px 10px; border-radius:999px; font-weight:600; }}
    .bp .chip.ok {{ background:#064E3B; color:#6EE7B7; }}
    .bp .chip.warn {{ background:#78350F; color:#FCD34D; }}
    .bp .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-top:14px; }}
    .bp .kpi {{ background:#0F172A99; border:1px solid var(--line); border-radius:12px; padding:10px 12px; }}
    .bp .kpi .l {{ color:var(--mut); font-size:12px; }}
    .bp .kpi .v {{ font-size:22px; font-weight:700; margin-top:2px; }}
    .bp .kpi .v small {{ font-size:12px; color:var(--mut); font-weight:500; margin-left:2px; }}
    .bp .packs {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; }}
    .bp .pack {{ position:relative; background:var(--card); border:1px solid var(--line); border-radius:16px; padding:14px; }}
    .bp .pack-head {{ display:flex; align-items:center; gap:8px; margin-bottom:8px; }}
    .bp .addr {{ background:#4C1D95; color:#DDD6FE; font-weight:700; font-size:12px; padding:2px 8px; border-radius:6px; }}
    .bp .pname {{ color:var(--mut); font-size:13px; }}
    .bp .pack-body {{ display:flex; gap:12px; align-items:center; }}
    .bp .batt {{ width:54px; flex:none; display:flex; flex-direction:column; align-items:center; }}
    .bp .batt .cap {{ width:40%; height:7px; background:#475569; border-radius:3px 3px 0 0; }}
    .bp .batt .body {{ position:relative; width:100%; height:112px; border:3px solid #475569; border-radius:10px;
                      background:#0B1220; padding:4px; display:flex; flex-direction:column; justify-content:flex-end; overflow:hidden; }}
    .bp .batt .fill {{ width:100%; border-radius:5px; box-shadow:inset 0 4px 0 #ffffff2e; }}
    .bp .batt .bolt {{ position:absolute; inset:0; display:flex; align-items:center; justify-content:center; font-size:22px;
                      animation:bpulse 1.6s ease-in-out infinite; }}
    @keyframes bpulse {{ 0%,100% {{ opacity:0.35; }} 50% {{ opacity:1; }} }}
    .bp .pack-stats {{ flex:1; min-width:0; }}
    .bp .big {{ font-size:34px; font-weight:800; line-height:1; }}
    .bp .big small {{ font-size:16px; }}
    .bp .sub {{ color:var(--dim); font-size:12px; margin:2px 0 6px; }}
    .bp .row, .bp .srow {{ display:flex; justify-content:space-between; font-size:13px; padding:2px 0; gap:8px; }}
    .bp .row span, .bp .srow span {{ color:var(--mut); }}
    .bp .row b, .bp .srow b {{ font-weight:600; text-align:right; }}
    .bp h3 {{ margin:0 0 4px; font-size:16px; color:var(--txt); }}
    .bp .muted {{ color:var(--dim); font-size:12px; font-weight:400; }}
    .bp .heat {{ overflow-x:auto; padding-bottom:4px; }}
    .bp .hrow {{ display:grid; grid-template-columns:34px repeat(16,minmax(30px,1fr)) 52px; gap:3px; margin-top:3px; min-width:620px; }}
    .bp .hlabel, .bp .hend, .bp .hnum {{ font-size:11px; color:var(--mut); display:flex; align-items:center; justify-content:center; }}
    .bp .cell {{ border-radius:6px; height:38px; display:flex; flex-direction:column; align-items:center; justify-content:center;
                color:#0F172A; font-size:10px; font-weight:700; line-height:1.1; }}
    .bp .cell span {{ font-size:11px; }}
    .bp .legend {{ display:flex; align-items:center; gap:8px; font-size:11px; color:var(--mut); margin-top:10px; flex-wrap:wrap; }}
    .bp .grad {{ width:160px; height:8px; border-radius:4px;
                background:linear-gradient(90deg,rgb(96,165,250),rgb(52,211,153),rgb(245,158,11)); }}
    .bp .two {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:14px; }}
    .bp .bar {{ height:10px; border-radius:5px; background:#0F172A; overflow:hidden; margin:6px 0 2px; }}
    .bp .bar i {{ display:block; height:100%; }}
    @media (max-width:640px) {{
      .bp .hero {{ grid-template-columns:1fr; }}
      .bp .ring {{ max-width:200px; }}
      .bp .packs {{ grid-template-columns:1fr 1fr; gap:8px; }}
      .bp .pack {{ padding:10px; }}
      .bp .pack-body {{ display:block; }}
      .bp .batt {{ position:absolute; top:42px; left:10px; width:28px; }}
      .bp .batt .body {{ height:58px; border-width:2px; padding:2px; border-radius:6px; }}
      .bp .batt .bolt {{ font-size:13px; }}
      .bp .pack-stats .big, .bp .pack-stats .sub {{ margin-left:36px; }}
      .bp .pack-stats .big {{ font-size:28px; }}
      .bp .pack-stats .sub {{ margin-bottom:18px; }}
      .bp .row {{ font-size:12px; }}
    }}
    </style><div class="bp"><div class="wrap">

    <div class="card hero">
      <div class="ring" style="background:conic-gradient({_soc_color(soc)} 0 {soc:.1f}%, #0B1220 {soc:.1f}% 100%)">
        <div class="tick" style="transform:rotate({FLOOR * 3.6:.0f}deg)"></div>
        <div class="mid"><div class="pct">{soc:.0f}<small>%</small></div>
          <div class="kwh">{stored:.1f} / {full_kwh:.0f} 度</div><div class="flr">白線 = {FLOOR}% 放電下限</div></div>
      </div>
      <div>
        <div class="title">🔋 儲能系統 {chip}</div>
        <div class="muted" style="margin-top:4px">{n} 組 EVE LF314 16 串 · JK BMS 並聯 · {total_ah:,.0f} Ah {note_live}</div>
        <div class="kpis">
          <div class="kpi"><div class="l">目前狀態</div><div class="v" style="color:{state_c}">{state}</div></div>
          <div class="kpi"><div class="l">合計電流 / 功率</div><div class="v">{curr:+.1f}<small>A</small> {power:+.2f}<small>kW</small></div></div>
          <div class="kpi"><div class="l">還差多少充滿</div><div class="v">{to_full:.1f}<small>度</small></div></div>
          <div class="kpi"><div class="l">距離 {FLOOR}% 還剩</div><div class="v">{to_floor:.1f}<small>度</small></div></div>
          <div class="kpi"><div class="l">平均單體電壓</div><div class="v">{avg_cell:.3f}<small>V</small></div></div>
          <div class="kpi"><div class="l">最大壓差（組內）</div><div class="v">{delta_pack:.0f}<small>mV</small></div></div>
          <div class="kpi"><div class="l">電芯溫度</div><div class="v">{min(temps):.0f}–{max(temps):.0f}<small>°C</small></div></div>
          <div class="kpi"><div class="l">健康度 SOH</div><div class="v">{min(p["soh"] for p in packs)}<small>%</small></div></div>
        </div>
      </div>
    </div>

    <div class="packs">{"".join(pack_cards)}</div>

    <div class="card">
      <h3>單體電壓 <span class="muted">64 顆電芯，數字是 3.xxx V 的小數（例如 345 = 3.345 V）· ▲ 最高 ▼ 最低</span></h3>
      <div class="heat"><div class="hrow"><div></div>{head_nums}<div class="hnum">壓差</div></div>{"".join(heat_rows)}</div>
      <div class="legend">比平均低 <div class="grad"></div> 比平均高 · 全部 64 顆最高最低差 {delta_all:.0f} mV（組內最大 {delta_pack:.0f} mV，30 mV 以內都算很平均）</div>
    </div>

    <div class="two">
      <div class="card">
        <h3>溫度</h3>
        {"".join(f'<div class="row"><span>#{p["addr"]} 電芯 / MOS</span><b>{p["t1"]:.1f} · {p["t2"]:.1f} / {p["mos_t"]:.1f}°C</b></div>'
                 f'<div class="bar"><i style="width:{min(max(p["t1"], p["t2"]) / 55 * 100, 100):.0f}%;background:linear-gradient(90deg,#34D399,#F59E0B)"></i></div>'
                 for p in packs)}
        <div class="muted" style="margin-top:6px">長條滿格 = 55°C 充電過溫保護</div>
      </div>
      <div class="card">
        <h3>BMS 保護設定 <span class="muted">4 組一致</span></h3>
        {settings_html}
        <div class="muted" style="margin-top:6px">運轉時間：{runtime}</div>
      </div>
    </div>

    <div class="muted" style="text-align:center">資料時間 {t:%Y/%m/%d %H:%M} · {html.escape(snap.get("source", ""))} · 樹莓派上線後改成即時更新</div>
    </div></div>'''
