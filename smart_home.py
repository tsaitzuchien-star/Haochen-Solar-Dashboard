"""🏠 澔宸智慧 AI 家庭：儲能室環境、戶外溫度、電池溫度與機房冷氣 (樹莓派每分鐘上傳的 climate 欄位)。

這裡只顯示環境與冷氣；燈、鐵捲門等「看得出家裡有沒有人」的裝置在上方 3D 小窩 (house_3d.py)，
只有用家人專用連結開啟時才會顯示。網頁上不能操作任何裝置。
資料格式 (live["climate"])：
  {"indoor": {temp, hum, batt, age_s} | None, "outdoor": {...} | None, "cell_max": °C | None,
   "ac": {on, mode, mode_name, temp, fan, auto_on, enabled, since, on_room, off_room, target, on_cell} | None}
"""
import html
from datetime import datetime, timezone, timedelta

TW = timezone(timedelta(hours=8))


def _age_txt(s):
    if s is None:
        return ""
    return "剛剛" if s < 90 else f"{s / 60:.0f} 分鐘前" if s < 3600 else f"{s / 3600:.1f} 小時前"


def _tile(icon, title, temp, hum, sub, color, extra=""):
    t = f"{temp:.1f}" if temp is not None else "—"
    h = f'<span class="hum">💧 {hum:.0f}%</span>' if hum is not None else ""
    return (f'<div class="tile"><div class="th"><span>{icon} {title}</span>{h}</div>'
            f'<div class="tv" style="color:{color}">{t}<small>°C</small></div>'
            f'<div class="ts">{sub}</div>{extra}</div>')


def _room_color(t, lo, hi):
    if t is None:
        return "#94A3B8"
    return "#60A5FA" if t <= lo else "#34D399" if t < hi else "#F59E0B" if t < hi + 2 else "#EF4444"


def render(live):
    c = (live or {}).get("climate")
    if not c:
        return ("<div style='color:#94A3B8;font-family:sans-serif;padding:18px;background:#1E293B;border-radius:14px'>"
                "還沒有收到環境資料（樹莓派更新到有溫濕度計與冷氣控制的版本後就會出現）。</div>")
    ac = c.get("ac") or {}
    lo, hi = ac.get("off_room", 26.5), ac.get("on_room", 29)
    on_cell = ac.get("on_cell", 35)
    ind, out = c.get("indoor"), c.get("outdoor")
    room = ind.get("temp") if ind else None
    cell = c.get("cell_max")
    t_live = datetime.fromisoformat(live["time"])
    stale = (datetime.now(TW) - t_live).total_seconds() > 300

    # ---- 三個溫度 ----
    rc = _room_color(room, lo, hi)
    # 室溫在「關 lo ↔ 開 hi」之間的位置 (左右各留 3°C)
    bar = ""
    if room is not None:
        x0, x1 = lo - 3, hi + 3
        pos = lambda v: max(0, min(100, (v - x0) / (x1 - x0) * 100))
        bar = (f'<div class="scale"><div class="zone" style="left:{pos(lo):.0f}%;width:{pos(hi) - pos(lo):.0f}%"></div>'
               f'<div class="mark" style="left:{pos(room):.0f}%;background:{rc}"></div></div>'
               f'<div class="scl"><span style="left:{pos(lo):.0f}%">{lo:g}° 關</span>'
               f'<span style="left:{pos(hi):.0f}%">{hi:g}° 開</span></div>')
    tiles = [
        _tile("🔋", "儲能室", room, ind.get("hum") if ind else None,
              (f"{_age_txt(ind.get('age_s'))}更新" + (f" · 溫度計電量 {ind['batt']}%" if ind.get("batt") else "")) if ind
              else "收不到溫濕度計", rc, bar),
        _tile("🌤️", "戶外（車庫）", out.get("temp") if out else None, out.get("hum") if out else None,
              f"{_age_txt(out.get('age_s'))}更新" if out else "離樹莓派太遠，暫時收不到", "#E2E8F0"),
        _tile("🔥", "電池最高溫", cell, None,
              f"{on_cell:g}°C 以上會強制開冷氣 · 55°C 停止充電" if cell is not None else "沒有電池資料",
              "#34D399" if cell is None or cell < 35 else "#F59E0B" if cell < 45 else "#EF4444"),
    ]

    # ---- 冷氣 ----
    if ac:
        on = bool(ac.get("on"))
        who = "自動開啟" if ac.get("auto_on") else "手動開啟（不會被自動關掉）" if on else "等室溫到門檻會自動開"
        since = ac.get("since")
        since_txt = f" · {datetime.fromtimestamp(since, TW):%m/%d %H:%M} 起" if since and on and ac.get("auto_on") else ""
        fan = {0: "自動", 1: "低", 2: "中", 3: "高"}.get(ac.get("fan"), ac.get("fan"))
        state = (f'<div class="acstate {"on" if on else "off"}"><div class="fanicon{" spin" if on else ""}">✻</div>'
                 f'<div><div class="acbig">{"運轉中" if on else "關機"}</div>'
                 f'<div class="ts">{html.escape(str(ac.get("mode_name") or ac.get("mode") or ""))} · 設定 {ac.get("temp", "—")}°C · 風速 {fan}'
                 f'<br>{who}{since_txt}</div></div></div>')
        # 智慧判斷：用跟樹莓派一樣的規則，說明現在為什麼開 / 關
        if not ac.get("enabled"):
            why = "自動控制目前停用。"
        elif room is None and cell is None:
            why = "收不到儲能室溫度與電池溫度，暫時不自動控制。"
        elif (room is not None and room >= hi) or (cell is not None and cell >= on_cell):
            why = (f"儲能室 {room}°C" if room is not None and room >= hi else f"電池 {cell}°C") + \
                  ("，已超過門檻，冷氣運轉中降溫。" if on else "，已超過門檻，樹莓派會在 1 分鐘內開冷氣。")
        elif on and ac.get("auto_on"):
            why = (f"儲能室 {room}°C，降到 {lo:g}°C 以下並維持 10 分鐘後會自動關機。" if room is not None
                   else "冷氣是自動開的，等室溫降下來會自動關。")
        elif on:
            why = f"冷氣是手動開的，自動控制不會關它；儲能室 {room}°C。"
        else:
            why = f"儲能室 {room}°C，在 {hi:g}°C 以下，不需要開冷氣。" if room is not None else "目前不需要開冷氣。"
        rules = (f'<div class="rules"><span>❄️ 室溫 ≥ {hi:g}°C 或電池 ≥ {on_cell:g}°C → 開（Cool {ac.get("target", 25):g}°C）</span>'
                 f'<span>✅ 自動開的冷氣，室溫 ≤ {lo:g}°C 持續 10 分鐘 → 關</span>'
                 f'<span>📱 每次開關都用 LINE 通知</span></div>')
        ac_html = (f'<div class="card"><h3>❄️ 太陽能機房冷氣 <span class="muted">Panasonic · 小米冷氣伴侶</span></h3>'
                   f'{state}<div class="ai">🤖 {why}</div>{rules}</div>')
    else:
        ac_html = '<div class="card"><h3>❄️ 太陽能機房冷氣</h3><div class="ts">讀不到冷氣伴侶</div></div>'

    # ---- 熱水器 ----
    hw = c.get("heater")
    if hw:
        on = bool(hw.get("on"))
        mode = {"surplus": "電池滿了、自動用多的太陽能加熱", "fallback": "陰天保底加熱 1 小時（到 16:00）"}.get(hw.get("mode"))
        heating = hw.get("heating")
        who = mode if hw.get("auto_on") and mode else "手動或排程開啟（不會被自動關掉）" if on else "等電池充滿會自動開"
        mins = hw.get("minutes_today", 0)
        hw_html = (f'<div class="card" style="margin-top:12px"><h3>🔥 儲熱電熱水器 <span class="muted">4 kW · eWeLink 開關</span></h3>'
                   f'<div class="acstate {"on" if on else "off"}"><div class="fanicon heat{" glow" if on else ""}">♨</div>'
                   f'<div><div class="acbig">{("加熱中" if heating else "保溫中") if on else "關閉"}</div>'
                   f'<div class="ts">{who}{"（水已經熱了，溫控自動斷電）" if on and heating is False else ""}'
                   f'<br>今天已加熱 {mins:.0f} 分鐘（約 {mins / 60 * 4:.1f} 度電 · 約 60 分鐘可從冷水加熱滿）</div></div></div>'
                   f'<div class="rules"><span>🔥 9:00–15:00 電池 ≥ 98% 且有太陽 → 開（把會被限發的電存成熱水）</span>'
                   f'<span>✅ 電池放電 > 1.5 kW 持續 5 分鐘或電池 < 90% → 關</span>'
                   f'<span>🌧️ 15:00 時今天加熱不到 50 分鐘 → 保底加熱 1 小時到 16:00</span></div></div>')
    else:
        hw_html = ""

    warn = (f'<div class="warn">⚠️ 樹莓派 {_age_txt((datetime.now(TW) - t_live).total_seconds())}之後就沒有新資料，以下是最後一次的數值</div>'
            if stale else "")
    return f'''<style>
    .sh {{ color:#E2E8F0; font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif; }}
    .sh * {{ box-sizing:border-box; }}
    .sh .hero {{ background:linear-gradient(135deg,#0C4A6E 0%,#1E293B 60%); border:1px solid #0E7490; border-radius:16px;
                padding:16px 20px; margin-bottom:12px; }}
    .sh .hero b {{ font-size:20px; }}
    .sh .hero .muted {{ display:block; margin-top:4px; }}
    .sh .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:12px; margin-bottom:12px; }}
    .sh .tile, .sh .card {{ background:#1E293B; border:1px solid #334155; border-radius:16px; padding:14px 16px; }}
    .sh .th {{ display:flex; justify-content:space-between; color:#94A3B8; font-size:13px; }}
    .sh .hum {{ color:#7DD3FC; }}
    .sh .tv {{ font-size:38px; font-weight:800; line-height:1.1; margin-top:4px; }}
    .sh .tv small {{ font-size:16px; color:#94A3B8; font-weight:500; margin-left:2px; }}
    .sh .ts {{ color:#64748B; font-size:12px; margin-top:4px; line-height:1.6; }}
    .sh .scale {{ position:relative; height:8px; border-radius:4px; margin-top:10px;
                 background:linear-gradient(90deg,#3B82F6,#34D399 40%,#34D399 60%,#F59E0B 80%,#EF4444); opacity:.9; }}
    .sh .zone {{ position:absolute; top:-3px; bottom:-3px; border:1px dashed #E2E8F099; border-radius:4px; }}
    .sh .mark {{ position:absolute; top:-5px; width:6px; height:18px; margin-left:-3px; border-radius:3px; border:2px solid #0F172A; }}
    .sh .scl {{ position:relative; height:16px; color:#64748B; font-size:11px; margin-top:4px; }}
    .sh .scl span {{ position:absolute; transform:translateX(-50%); white-space:nowrap; }}
    .sh h3 {{ margin:0 0 10px; font-size:16px; }}
    .sh .muted {{ color:#64748B; font-size:12px; font-weight:400; }}
    .sh .acstate {{ display:flex; gap:14px; align-items:center; }}
    .sh .fanicon {{ width:56px; height:56px; flex:none; border-radius:50%; display:flex; align-items:center; justify-content:center;
                   font-size:34px; background:#0F172A; color:#475569; }}
    .sh .acstate.on .fanicon {{ color:#38BDF8; box-shadow:0 0 18px #38BDF855; }}
    .sh .acstate.on .heat {{ color:#F97316; box-shadow:0 0 18px #F9731666; }}
    .sh .glow {{ animation:shglow 1.8s ease-in-out infinite; }}
    @keyframes shglow {{ 50% {{ box-shadow:0 0 28px #F97316AA; }} }}
    .sh .acstate.on .heat + div .acbig {{ color:#FB923C; }}
    .sh .spin {{ animation:shspin 2.4s linear infinite; }}
    @keyframes shspin {{ to {{ transform:rotate(360deg); }} }}
    .sh .acbig {{ font-size:24px; font-weight:800; }}
    .sh .acstate.on .acbig {{ color:#38BDF8; }}
    .sh .acstate.off .acbig {{ color:#94A3B8; }}
    .sh .ai {{ margin-top:12px; background:#0F172A; border:1px solid #334155; border-radius:12px; padding:10px 12px;
              font-size:14px; line-height:1.6; }}
    .sh .rules {{ display:flex; flex-direction:column; gap:3px; margin-top:10px; color:#94A3B8; font-size:12px; }}
    .sh .warn {{ background:#78350F; color:#FDE68A; border-radius:10px; padding:8px 12px; margin-bottom:10px; font-size:13px; }}
    </style><div class="sh">
    <div class="hero"><b>🏠 澔宸智慧 AI 家庭</b>
      <span class="muted">樹莓派每分鐘讀取米家溫濕度計（藍牙）、冷氣伴侶（區網）與熱水器開關（eWeLink），自動開關機房冷氣與熱水器 · 網頁上只能查看，不能操作</span></div>
    {warn}
    <div class="tiles">{"".join(tiles)}</div>
    {ac_html}
    {hw_html}
    <div class="ts" style="text-align:center;margin-top:10px">資料時間 {t_live:%Y/%m/%d %H:%M:%S}</div>
    </div>'''
