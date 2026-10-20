"""2.5D 房屋能源流向動畫 (SVG + CSS)，以流動虛線表示電力方向與大小。"""

# 屋頂平面: A=山牆頂點, B=屋脊另一端, D=屋簷前緣
_A, _B, _D = (250, 150), (650, 112), (318, 252)


def _roof(u, v):
    return (_A[0] + u * (_B[0] - _A[0]) + v * (_D[0] - _A[0]),
            _A[1] + u * (_B[1] - _A[1]) + v * (_D[1] - _A[1]))


def _poly(points):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _panels():
    svg = []
    for i in range(4):
        u0, u1 = 0.30 + i * 0.165, 0.30 + i * 0.165 + 0.15
        v0, v1 = 0.12, 0.78
        svg.append(f'<polygon points="{_poly([_roof(u0, v0), _roof(u1, v0), _roof(u1, v1), _roof(u0, v1)])}" class="panel"/>')
        # 電池片格線
        for k in range(1, 3):
            u = u0 + (u1 - u0) * k / 3
            svg.append(f'<line x1="{_roof(u, v0)[0]:.1f}" y1="{_roof(u, v0)[1]:.1f}" x2="{_roof(u, v1)[0]:.1f}" y2="{_roof(u, v1)[1]:.1f}" class="cell"/>')
        for k in range(1, 6):
            v = v0 + (v1 - v0) * k / 6
            svg.append(f'<line x1="{_roof(u0, v)[0]:.1f}" y1="{_roof(u0, v)[1]:.1f}" x2="{_roof(u1, v)[0]:.1f}" y2="{_roof(u1, v)[1]:.1f}" class="cell"/>')
    return "".join(svg)


def _flow(path_d, kw, color, reverse=False):
    """底部軌道 + 流動虛線。功率越大流速越快，近乎 0 時只顯示灰色軌道。"""
    track = f'<path d="{path_d}" class="track"/>'
    if abs(kw) < 0.02:
        return track
    dur = max(0.35, 2.4 - abs(kw) * 0.35)
    direction = "reverse" if reverse else "normal"
    return track + (f'<path d="{path_d}" class="flow" style="stroke:{color};'
                    f'animation-duration:{dur:.2f}s;animation-direction:{direction};'
                    f'filter:drop-shadow(0 0 4px {color});"/>')


def _card(x, y, title, value, color, sub=""):
    sub_html = f'<span style="color:{color};margin-left:8px;">{sub}</span>' if sub else ""
    return (f'<div class="card" style="left:{x}%;top:{y}%;">'
            f'<div class="val">{value:.2f}<small> kW</small>{sub_html}</div>'
            f'<div class="ttl">{title}</div></div>')


def power_flow_html(pv, load, batt, soc, grid, night=False):
    """pv/load: kW；batt: kW (+充電/−放電)；grid: kW (+買電/−賣電)。"""
    sky = ("#0B1120", "#111827") if night else ("#1E3A5F", "#0F172A")
    window = "#FCD34D" if night else "#FDE68A"
    if night:
        stars = "".join(f'<circle cx="{x}" cy="{y}" r="1.2" fill="#E2E8F0" opacity="0.7"/>'
                        for x, y in [(60, 40), (140, 90), (330, 30), (420, 70), (520, 25), (720, 60), (600, 85), (880, 40), (40, 160)])
        sky_body = stars + '<circle cx="110" cy="70" r="22" fill="#F1F5F9"/><circle cx="121" cy="62" r="20" fill="{0}"/>'.format(sky[0])
    else:
        sky_body = ('<circle cx="110" cy="70" r="60" fill="#FDE68A" opacity="0.12"/>'
                    '<circle cx="110" cy="70" r="24" fill="#FBBF24" style="filter:drop-shadow(0 0 12px #FBBF24);"/>')
    pv_c, load_c, bat_c, grid_c = "#10B981", "#F97316", "#A78BFA", "#38BDF8"

    pv_path = f"M {_roof(0.63, 0.78)[0]:.0f} {_roof(0.63, 0.78)[1]:.0f} L 470 300"
    load_path = "M 452 312 L 300 340 L 262 340"
    bat_path = "M 480 324 L 480 372 L 552 372"
    grid_path = "M 815 150 L 815 304 L 508 304"

    batt_label = "電池充電" if batt > 0.02 else ("電池放電" if batt < -0.02 else "電池待機")
    grid_label = "電網購電" if grid > 0.02 else ("賣電" if grid < -0.02 else "電網")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body {{ margin:0; background:transparent; font-family:'Segoe UI',Roboto,'Noto Sans TC',sans-serif; }}
.wrap {{ position:relative; width:100%; max-width:960px; margin:0 auto; aspect-ratio: 900/470;
        border-radius:14px; overflow:hidden; border:1px solid #334155;
        background: radial-gradient(ellipse at 30% 0%, {sky[0]}, {sky[1]} 70%); }}
svg {{ position:absolute; inset:0; width:100%; height:100%; }}
.panel {{ fill:#1E3A8A; stroke:#93C5FD; stroke-width:1.2; }}
.cell {{ stroke:#3B5BA9; stroke-width:0.6; }}
.track {{ fill:none; stroke:#1F2937; stroke-width:5; stroke-linecap:round; stroke-linejoin:round; }}
.flow {{ fill:none; stroke-width:4; stroke-linecap:round; stroke-linejoin:round; stroke-dasharray:6 12;
        animation-name:dash; animation-timing-function:linear; animation-iteration-count:infinite; }}
@keyframes dash {{ to {{ stroke-dashoffset:-36; }} }}
@media (prefers-reduced-motion: reduce) {{ .flow {{ animation:none; stroke-dasharray:none; }} }}
.card {{ position:absolute; transform:translate(-50%,-50%); padding:8px 14px; border-radius:10px;
        background:rgba(15,23,42,0.72); border:1px solid rgba(148,163,184,0.25); backdrop-filter:blur(6px);
        color:#F8FAFC; white-space:nowrap; box-shadow:0 4px 14px rgba(0,0,0,0.35); }}
.val {{ font-size:clamp(14px,2.4vw,22px); font-weight:700; }}
.val small {{ font-size:0.6em; font-weight:400; color:#CBD5E1; }}
.ttl {{ font-size:clamp(10px,1.4vw,13px); color:#94A3B8; margin-top:2px; }}
</style></head><body><div class="wrap">
<svg viewBox="0 0 900 470" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="side" x1="0" x2="1"><stop offset="0" stop-color="#B9A588"/><stop offset="1" stop-color="#8F7C63"/></linearGradient>
    <linearGradient id="front" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#E7DCC8"/><stop offset="1" stop-color="#C9B99D"/></linearGradient>
    <linearGradient id="roofg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#475569"/><stop offset="1" stop-color="#334155"/></linearGradient>
    <radialGradient id="glow"><stop offset="0" stop-color="{window}" stop-opacity="0.55"/><stop offset="1" stop-color="{window}" stop-opacity="0"/></radialGradient>
  </defs>
  {sky_body}
  <ellipse cx="470" cy="420" rx="400" ry="38" fill="#000" opacity="0.35"/>
  <!-- 側牆 -->
  <polygon points="{_poly([(305, 250), (705, 212), (705, 372), (305, 412)])}" fill="url(#side)"/>
  <!-- 山牆 -->
  <polygon points="{_poly([(185, 232), (250, 150), (305, 250), (305, 412), (185, 388)])}" fill="url(#front)"/>
  <polygon points="{_poly([(205, 262), (282, 276), (282, 400), (205, 384)])}" fill="{window}" opacity="0.85"/>
  <line x1="243" y1="269" x2="243" y2="392" stroke="#7C6A50" stroke-width="3"/>
  <line x1="205" y1="320" x2="282" y2="334" stroke="#7C6A50" stroke-width="3"/>
  <ellipse cx="243" cy="330" rx="90" ry="70" fill="url(#glow)"/>
  <!-- 側牆窗戶 -->
  <polygon points="{_poly([(560, 250), (640, 243), (640, 300), (560, 307)])}" fill="{window}" opacity="0.7"/>
  <!-- 屋頂 -->
  <polygon points="{_poly([_A, _B, (722, 210), _D])}" fill="url(#roofg)" stroke="#1E293B" stroke-width="2"/>
  <polygon points="{_poly([(175, 236), _A, (318, 252), (312, 258), (250, 160), (182, 242)])}" fill="#E2E8F0" opacity="0.9"/>
  {_panels()}
  <!-- 電線桿 -->
  <line x1="815" y1="95" x2="815" y2="410" stroke="#64748B" stroke-width="7"/>
  <line x1="785" y1="112" x2="845" y2="112" stroke="#64748B" stroke-width="5"/>
  <path d="M 785 112 Q 740 160 705 230" fill="none" stroke="#475569" stroke-width="1.5"/>
  <!-- 能源流 -->
  {_flow(pv_path, pv, pv_c)}
  {_flow(load_path, load, load_c)}
  {_flow(bat_path, batt, bat_c, reverse=batt < 0)}
  {_flow(grid_path, grid, grid_c, reverse=grid < 0)}
  <!-- 逆變器 -->
  <rect x="452" y="282" width="56" height="42" rx="5" fill="#F8FAFC" stroke="#CBD5E1"/>
  <circle cx="480" cy="303" r="7" fill="#1E293B"/>
  <circle cx="500" cy="290" r="2.5" fill="#22C55E"/>
  <!-- 電池櫃 -->
  <rect x="552" y="340" width="48" height="68" rx="5" fill="#F1F5F9" stroke="#CBD5E1"/>
  <rect x="558" y="346" width="8" height="56" rx="2" fill="#CBD5E1"/>
  <rect x="558" y="{346 + 56 * (1 - soc / 100):.1f}" width="8" height="{56 * soc / 100:.1f}" rx="2" fill="{bat_c}"/>
  <circle cx="586" cy="356" r="2.5" fill="#64748B"/>
</svg>
{_card(70, 11, "光伏", pv, pv_c)}
{_card(15, 52, "載入 (家中負載)", load, load_c)}
{_card(80, 82, batt_label, abs(batt), bat_c, f"{soc}%")}
{_card(91, 11, grid_label, abs(grid), grid_c)}
</div></body></html>"""
