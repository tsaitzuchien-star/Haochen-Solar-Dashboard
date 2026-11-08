"""🏠 澔宸 AI 智慧小窩：4 層樓透天 3D 模型 (Three.js)，即時顯示哪些燈、冷氣、家電正在開。

資料由 home_map.build() 在網站伺服器端產生：沒用家人專用連結登入時，燈、鐵捲門、各房間冷氣都不會送到瀏覽器。
格局依照片 (正面 = +z = 馬路；面對房子時右手邊 = +x；樓梯是左邊中段的折返梯)：
  1F 車棚 (木條牆、透光屋頂、鐵捲門 + 右邊小門) → 客廳 (窗下書桌、右牆沙發、左牆電視) → 樓梯 / 右邊走道
     → 廚房餐廳 → 洗衣間 (左邊廁所)
  2F 前陽台 (左邊盡頭熱水器) → 主臥 (床頭靠左牆、後段衣櫃、左後廁所) → 樓梯口 → 書房
  3F 前陽台 → 3 樓房間 (床靠左牆、後段衣櫃) → 走道 (左邊浴室) → 樓梯口 → 視聽室 (右牆電視音響)
  4F 露台 (黃色矮牆) → 太陽能機房 (右牆逆變器、電池架、冷氣；左牆備用板) → 走道 → 健身室；屋頂太陽能板
"""
import html
import json

import home_map

_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
html, body { margin:0; padding:0; background:transparent; overflow:hidden;
  font-family:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif; }
#wrap { position:relative; width:100%; height:__H__px; border-radius:16px; overflow:hidden; border:1px solid #334155;
  background:__SKY__; }
#wrap canvas { display:block; cursor:grab; touch-action:none; }
.labels { position:absolute; inset:0; pointer-events:none; }
.tag { padding:3px 8px; border-radius:8px; font-size:11px; white-space:nowrap; pointer-events:none;
  background:rgba(15,23,42,.62); color:#CBD5E1; border:1px solid rgba(148,163,184,.25); }
.tag.on { color:#FDE68A; border-color:rgba(253,224,71,.55); background:rgba(15,23,42,.82); }
.tag.ac.on { color:#7DD3FC; border-color:rgba(125,211,252,.6); }
.tag.heat.on { color:#FDBA74; border-color:rgba(251,146,60,.6); }
.tag.gray { color:#64748B; }
.tag.sel { outline:2px solid #F8FAFC; }
.room { font-size:11px; color:rgba(226,232,240,.75); text-shadow:0 1px 3px #000; pointer-events:none; white-space:nowrap; }
.flabel { font-size:12px; font-weight:700; color:#E2E8F0; background:rgba(15,23,42,.6); padding:2px 8px; border-radius:8px;
  border:1px solid rgba(148,163,184,.3); pointer-events:none; white-space:nowrap; }
.flabel small { font-weight:400; color:#94A3B8; margin-left:4px; }
.top { position:absolute; left:12px; top:10px; right:12px; display:flex; justify-content:space-between; align-items:flex-start;
  gap:8px; pointer-events:none; }
.title { background:rgba(15,23,42,.66); border:1px solid rgba(148,163,184,.28); border-radius:12px; padding:8px 12px;
  color:#F8FAFC; backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px); max-width:62%; }
.title b { font-size:16px; }
.title .s { font-size:12px; color:#94A3B8; margin-top:2px; line-height:1.5; }
.title .s b { font-size:12px; color:#FDE68A; }
.title .w { color:#FCA5A5; }
.floors { display:flex; gap:4px; background:rgba(15,23,42,.66); padding:4px; border-radius:10px; pointer-events:auto; }
.floors button { border:0; background:transparent; color:#CBD5E1; font-size:13px; padding:6px 10px; border-radius:7px; cursor:pointer; }
.floors button.sel { background:#334155; color:#F8FAFC; font-weight:700; }
.chips { position:absolute; left:12px; right:12px; bottom:10px; display:flex; flex-wrap:wrap; gap:5px; max-height:70px;
  overflow:auto; pointer-events:auto; }
.chip { font-size:12px; padding:3px 9px; border-radius:999px; background:rgba(15,23,42,.72); color:#FDE68A;
  border:1px solid rgba(253,224,71,.4); cursor:pointer; white-space:nowrap; }
.chip.ac { color:#7DD3FC; border-color:rgba(125,211,252,.45); }
.chip.heat { color:#FDBA74; border-color:rgba(251,146,60,.45); }
.chip.lock { color:#94A3B8; border-color:rgba(148,163,184,.35); cursor:default; }
.info { position:absolute; right:12px; top:58px; width:220px; display:none; background:rgba(15,23,42,.86);
  border:1px solid rgba(148,163,184,.35); border-radius:12px; padding:10px 12px; color:#E2E8F0; font-size:13px; line-height:1.6;
  backdrop-filter:blur(8px); -webkit-backdrop-filter:blur(8px); }
.info b { font-size:15px; }
.info .x { position:absolute; right:8px; top:4px; cursor:pointer; color:#94A3B8; font-size:16px; }
.info .m { color:#94A3B8; font-size:12px; }
.hint { position:absolute; right:12px; bottom:84px; font-size:11px; color:rgba(226,232,240,.5); pointer-events:none; }
.fallback { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#94A3B8; }
@media (max-width:560px) { .title { max-width:100%; } .top { flex-direction:column; } .info { top:auto; bottom:84px; } }
</style></head><body>
<div id="wrap">
  <div class="top"><div class="title"><b>🏠 澔宸 AI 智慧小窩</b><div class="s" id="sum"></div></div>
    <div class="floors" id="floors"></div></div>
  <div class="info" id="info"><span class="x" id="ix">✕</span><div id="ib"></div></div>
  <div class="hint">拖曳旋轉 · 雙指縮放 · 點裝置看詳情</div>
  <div class="chips" id="chips"></div>
</div>
<script type="importmap">{"imports":{
  "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

const D = __DATA__;
const wrap = document.getElementById('wrap');
const HW = 3, FH = 3.0, SLAB = 0.2, CEIL = FH - 0.14, GAP = 1.7;
const hour = D.time ? parseInt(D.time.slice(11, 13), 10) : new Date().getHours();
const NIGHT = hour < 6 || hour >= 18;
const FLOOR_NAME = { 1: '車庫 · 客廳 · 廚房 · 洗衣間', 2: '陽台 · 主臥 · 書房', 3: '陽台 · 房間 · 浴室 · 視聽室', 4: '露台 · 太陽能機房 · 健身室' };

let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
} catch (e) {
  wrap.insertAdjacentHTML('beforeend', '<div class="fallback">這個瀏覽器不支援 3D 顯示</div>');
  throw e;
}
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(wrap.clientWidth, wrap.clientHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = NIGHT ? 0.95 : 1.05;
wrap.prepend(renderer.domElement);
const labelR = new CSS2DRenderer();
labelR.setSize(wrap.clientWidth, wrap.clientHeight);
labelR.domElement.className = 'labels';
wrap.insertBefore(labelR.domElement, renderer.domElement.nextSibling);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, wrap.clientWidth / wrap.clientHeight, 0.1, 300);
const NARROW = wrap.clientWidth < 600;
camera.position.set(24, 19, 30).multiplyScalar(NARROW ? 1.5 : 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true; controls.dampingFactor = 0.08;
controls.target.set(0, 9, -1.3);
controls.maxPolarAngle = Math.PI * 0.495; controls.minDistance = 5; controls.maxDistance = 70;

scene.add(new THREE.HemisphereLight(NIGHT ? '#7C8DB5' : '#E0F2FE', '#334155', NIGHT ? 0.75 : 1.15));
const sun = new THREE.DirectionalLight(NIGHT ? '#A5B4FC' : '#FFF4DE', NIGHT ? 0.45 : 1.7);
sun.position.set(12, 22, 14); scene.add(sun);
const fill = new THREE.DirectionalLight('#BFD7FF', 0.35); fill.position.set(-14, 8, -6); scene.add(fill);

// ---------- 小工具 ----------
const std = (c, o = {}) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.75, metalness: 0.05, ...o });
const basic = (c, o = {}) => new THREE.MeshBasicMaterial({ color: c, ...o });
function bx(parent, w, h, d, mat, x, y, z, ry = 0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat); m.position.set(x, y, z); m.rotation.y = ry; parent.add(m); return m;
}
function rbx(parent, w, h, d, mat, x, y, z, r = 0.04) {
  const m = new THREE.Mesh(new RoundedBoxGeometry(w, h, d, 2, r), mat); m.position.set(x, y, z); parent.add(m); return m;
}
function cyl(parent, rt, rb, h, mat, x, y, z, seg = 20) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), mat); m.position.set(x, y, z); parent.add(m); return m;
}
function glowTex(inner = 'rgba(255,230,170,1)') {
  const c = document.createElement('canvas'); c.width = c.height = 64; const g = c.getContext('2d');
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, inner); gr.addColorStop(0.35, inner.replace(',1)', ',.35)')); gr.addColorStop(1, 'rgba(0,0,0,0)');
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
}
const GLOW = glowTex(), GLOW_B = glowTex('rgba(150,220,255,1)'), GLOW_O = glowTex('rgba(255,170,90,1)');
function sprite(parent, tex, s, x, y, z, op = 1) {
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, opacity: op }));
  sp.scale.set(s, s, 1); sp.position.set(x, y, z); parent.add(sp); return sp;
}
function label(parent, cls, text, x, y, z) {
  const el = document.createElement('div'); el.className = cls; el.innerHTML = text;
  const o = new CSS2DObject(el); o.position.set(x, y, z); parent.add(o); return o;
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const M = {
  slab: std('#CBD5E1', { roughness: 0.9 }), slabEdge: std('#94A3B8'), pillar: std('#E2E8F0', { roughness: 0.6 }),
  wall: new THREE.MeshStandardMaterial({ color: '#E2E8F0', transparent: true, opacity: 0.12, depthWrite: false,
    side: THREE.DoubleSide, roughness: 0.4 }),
  glass: new THREE.MeshStandardMaterial({ color: '#7DD3FC', transparent: true, opacity: 0.14, depthWrite: false,
    side: THREE.DoubleSide, roughness: 0.1, metalness: 0.2 }),
  edge: new THREE.LineBasicMaterial({ color: '#CBD5E1', transparent: true, opacity: 0.45 }),
  white: std('#F8FAFC', { roughness: 0.5 }), dark: std('#1E293B'), wood: std('#A47148'), woodL: std('#C8A27A'),
  fabric: std('#64748B'), fabric2: std('#475569'), bed: std('#E2E8F0'), metal: std('#94A3B8', { metalness: 0.6, roughness: 0.35 }),
  black: std('#0F172A', { roughness: 0.4 }), plant: std('#16A34A'), pot: std('#B45309'), car: std('#2563EB', { metalness: 0.4, roughness: 0.3 }),
  tyre: std('#111827'), gray: std('#475569'), off: std('#334155'),
};
const coneMat = () => new THREE.MeshBasicMaterial({ color: '#FFD58A', transparent: true, opacity: NIGHT ? 0.17 : 0.1,
  blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide });

// ---------- 尺寸 (正面 = +z = 馬路；面對房子時右手邊 = +x) ----------
// 房子本體 z ZB ~ ZF；前面 1 樓是車棚 (到 ZC)；樓梯間在左邊中段 (x SX0 ~ SX1、z SZ0 ~ SZ1)，折返梯
const ZF = 3.0, ZB = -11.0, ZC = 8.4, BAL = 4.2;
const SX0 = -2.8, SX1 = -0.4, SZ0 = -6.0, SZ1 = -2.6;

// ---------- 地面 ----------
const ground = new THREE.Mesh(new THREE.CircleGeometry(32, 64), std(NIGHT ? '#1B2B24' : '#3F6B4E', { roughness: 1 }));
ground.rotation.x = -Math.PI / 2; ground.position.y = -0.12; scene.add(ground);
bx(scene, 9, 0.03, 5, std('#4B5563', { roughness: 1 }), 0, -0.1, ZC + 2.6);        // 門前馬路
for (const [x, z, s] of [[-7, 6, 1.2], [7.2, 4, 1], [-7.2, -6, 1.4], [7.4, -8, 1.1]]) {
  cyl(scene, 0.12, 0.15, 1.2 * s, M.wood, x, 0.5 * s, z, 8);
  const t = new THREE.Mesh(new THREE.IcosahedronGeometry(0.9 * s, 0), std('#15803D', { flatShading: true }));
  t.position.set(x, 1.6 * s, z); scene.add(t);
}

// ---------- 材質貼圖 ----------
function stripeTex(w, h, draw) {
  const c = document.createElement('canvas'); c.width = w; c.height = h; draw(c.getContext('2d'));
  const t = new THREE.CanvasTexture(c); t.colorSpace = THREE.SRGBColorSpace; return t;
}
const slatMat = std('#FFFFFF', { transparent: true, alphaTest: 0.5, side: THREE.DoubleSide, map: stripeTex(16, 128, x => {
  for (let j = 0; j < 128; j += 11) { x.fillStyle = '#6B4A32'; x.fillRect(0, j, 16, 8); }
}) });
const grilleTex = stripeTex(128, 128, x => {
  x.fillStyle = '#E5E7EB'; for (let i = 0; i < 128; i += 16) x.fillRect(i, 0, 3, 128); x.fillRect(0, 60, 128, 3);
});
grilleTex.wrapS = grilleTex.wrapT = THREE.RepeatWrapping;
const grilleMat = new THREE.MeshStandardMaterial({ map: grilleTex, transparent: true, alphaTest: 0.4, side: THREE.DoubleSide,
  metalness: 0.5, roughness: 0.4 });
{ const c = grilleTex.image.getContext('2d'); const d = c.getImageData(0, 0, 128, 128);
  for (let i = 3; i < d.data.length; i += 4) d.data[i] = d.data[i - 1] > 100 ? 255 : 0; c.putImageData(d, 0, 0); grilleTex.needsUpdate = true; }

// ---------- 樓層 ----------
const floors = {};
for (let f = 1; f <= 4; f++) {
  const g = new THREE.Group(); g.position.y = (f - 1) * (FH + GAP); scene.add(g);
  floors[f] = { g, f, rooms: {}, roomLabels: [], y: g.position.y };
}
const roof = new THREE.Group(); scene.add(roof);

function slab(g, hole, zf = ZF) {
  const y = -SLAB / 2, d = zf - ZB + 0.2, zc = (zf + ZB) / 2;
  if (!hole) { bx(g, 2 * HW + 0.2, SLAB, d, M.slab, 0, y, zc); return; }
  bx(g, HW + 0.1 - SX1, SLAB, d, M.slab, (SX1 + HW + 0.1) / 2, y, zc);
  bx(g, SX0 + HW + 0.1, SLAB, d, M.slab, (SX0 - HW - 0.1) / 2, y, zc);
  bx(g, SX1 - SX0, SLAB, zf + 0.1 - SZ1, M.slab, (SX0 + SX1) / 2, y, (zf + 0.1 + SZ1) / 2);
  bx(g, SX1 - SX0, SLAB, SZ0 - ZB + 0.1, M.slab, (SX0 + SX1) / 2, y, (SZ0 + ZB - 0.1) / 2);
}
function wall(g, x0, z0, x1, z1, mat = M.wall, h = CEIL, y0 = 0) {
  const len = Math.hypot(x1 - x0, z1 - z0);
  const m = new THREE.Mesh(new THREE.BoxGeometry(len, h, 0.08), mat);
  m.position.set((x0 + x1) / 2, y0 + h / 2, (z0 + z1) / 2); m.rotation.y = -Math.atan2(z1 - z0, x1 - x0);
  m.renderOrder = 2; g.add(m);
  if (mat !== grilleMat && mat !== slatMat) m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), M.edge));
  return m;
}
function rail(g, x0, z0, x1, z1) { wall(g, x0, z0, x1, z1, M.glass, 1.05); }
function balusters(g, x0, x1, z) {                 // 白色花瓶欄杆 + 鐵窗
  bx(g, x1 - x0, 0.12, 0.2, M.white, (x0 + x1) / 2, 0.95, z);
  for (let x = x0 + 0.15; x < x1; x += 0.3) cyl(g, 0.05, 0.07, 0.85, M.white, x, 0.45, z, 8);
  const gr = wall(g, x0, z, x1, z, grilleMat, CEIL - 1.05, 1.05);
  grilleMat.map.repeat.set(3, 1.2);
  return gr;
}
function room(f, name, x0, x1, z0, z1, color, { lift = 0, showLabel = true } = {}) {
  const fl = floors[f];
  const m = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0 - 0.04, 0.03, z1 - z0 - 0.04),
    std(color, { roughness: 0.85, emissive: '#FBBF24', emissiveIntensity: 0 }));
  m.position.set((x0 + x1) / 2, 0.015 + lift, (z0 + z1) / 2); fl.g.add(m);
  if (!fl.rooms[name]) fl.rooms[name] = { meshes: [], on: 0 };
  fl.rooms[name].meshes.push(m);
  if (showLabel) fl.roomLabels.push(label(fl.g, 'room', name, (x0 + x1) / 2, 0.15, (z0 + z1) / 2));
  return m;
}
function stairs(g) {                               // 折返梯：右半段往後爬到中間平台，左半段往前爬到上一層
  const n = 8, rise = FH / 2 / n, run = (SZ1 - SZ0 - 1.0) / n, xm = (SX0 + SX1) / 2;
  for (let i = 0; i < n; i++) {
    bx(g, SX1 - xm - 0.05, rise, run, M.woodL, (xm + SX1) / 2, (i + 0.5) * rise, SZ1 - (i + 0.5) * run);
    bx(g, xm - SX0 - 0.05, rise, run, M.woodL, (SX0 + xm) / 2, FH / 2 + (i + 0.5) * rise, SZ0 + 1.0 + (i + 0.5) * run);
  }
  bx(g, SX1 - SX0, 0.12, 1.0, M.woodL, xm, FH / 2 - 0.06, SZ0 + 0.5);            // 中間平台
  bx(g, 0.04, 0.9, SZ1 - SZ0 - 1.0, M.wood, xm, FH / 2 + 0.45, (SZ1 + SZ0 + 1.0) / 2);   // 中間扶手牆
}
function sides(g, zf, zb = ZB) {
  wall(g, -HW, zb, -HW, zf); wall(g, HW, zb, HW, zf); wall(g, -HW, zb, HW, zb);
  for (const [x, z] of [[-HW, zb], [HW, zb], [-HW, zf], [HW, zf]]) bx(g, 0.22, CEIL, 0.22, M.pillar, x, CEIL / 2, z);
}
function bed(g, x0, x1, z0, z1, head = '-x') {
  rbx(g, x1 - x0, 0.28, z1 - z0, M.bed, (x0 + x1) / 2, 0.15, (z0 + z1) / 2, 0.06);
  rbx(g, (x1 - x0) * 0.55, 0.06, z1 - z0 - 0.1, std('#93C5FD'), (x0 + x1) / 2 + (x1 - x0) * 0.15, 0.31, (z0 + z1) / 2, 0.03);
  if (head === '-x') bx(g, 0.12, 0.9, z1 - z0 + 0.2, M.white, x0 - 0.04, 0.45, (z0 + z1) / 2);
}

// ===== 1F：車棚 → 客廳 → 樓梯 / 走道 → 廚房餐廳 → 洗衣間、廁所 =====
{
  const f = 1, g = floors[f].g;
  slab(g, false); bx(g, 2 * HW + 0.2, SLAB, ZC - ZF, std('#9CA3AF', { roughness: 0.95 }), 0, -SLAB / 2, (ZF + ZC) / 2);
  sides(g, ZF);
  room(f, '車庫', -3, 3, ZF, ZC, '#6B7280'); room(f, '客廳', -3, 3, SZ1, ZF, '#D6D3D1');
  room(f, '樓梯', SX0, SX1, SZ0, SZ1, '#57534E', { showLabel: false }); room(f, '走道', SX1, 3, SZ0, SZ1, '#D6D3D1', { showLabel: false });
  room(f, '廚房 · 餐廳', -3, 3, -9.4, SZ0, '#E7E5E4'); room(f, '洗衣間', -1.4, 3, ZB, -9.4, '#CBD5E1');
  room(f, '廁所', -3, -1.4, ZB, -9.4, '#5B7C99');
  // 正面：左邊大窗 (鐵窗)、右邊大門
  wall(g, -3, ZF, -2.6, ZF); wall(g, -2.6, ZF, 0.3, ZF, M.glass); wall(g, 0.3, ZF, 1.3, ZF); wall(g, 2.3, ZF, 3, ZF);
  bx(g, 1.0, 2.1, 0.06, std('#374151'), 1.8, 1.05, ZF);                              // 大門
  wall(g, SX1, SZ0, SX1, SZ1);                                                        // 樓梯與走道之間的牆
  wall(g, -3, SZ0, SX1, SZ0); wall(g, SX1, SZ0, 0.6, SZ0); wall(g, 2.6, SZ0, 3, SZ0); // 廚房入口在右邊
  wall(g, -3, -9.4, 1.6, -9.4); wall(g, 2.6, -9.4, 3, -9.4);                          // 洗衣間門在右後
  wall(g, -1.4, ZB, -1.4, -10.5); wall(g, -1.4, -9.8, -1.4, -9.4);
  stairs(g);
  // 車棚：兩側木條牆、深色鋼樑透光屋頂、前面鐵捲門 + 右邊不鏽鋼小門
  for (const x of [-HW, HW]) { wall(g, x, ZF, x, ZC, slatMat, 1.9, 0.5); bx(g, 0.1, 0.5, ZC - ZF, std('#E7D7A8'), x, 0.25, (ZF + ZC) / 2); }
  const cy = CEIL - 0.05;
  for (let z = ZF + 0.3; z < ZC; z += 0.75) bx(g, 2 * HW, 0.12, 0.1, M.dark, 0, cy, z);
  for (const x of [-2.9, -1, 1, 2.9]) bx(g, 0.1, 0.12, ZC - ZF, M.dark, x, cy, (ZF + ZC) / 2);
  bx(g, 2 * HW, 0.02, ZC - ZF, new THREE.MeshStandardMaterial({ color: '#E0F2FE', transparent: true, opacity: 0.25, depthWrite: false }),
     0, cy + 0.08, (ZF + ZC) / 2);
  bx(g, 1.1, 2.3, 0.05, std('#D1D5DB', { metalness: 0.7, roughness: 0.3 }), 2.4, 1.15, ZC);   // 不鏽鋼小門
  // 車子、機車、腳踏車
  const car = new THREE.Group(); car.position.set(-0.6, 0, 5.7); g.add(car);
  rbx(car, 1.7, 0.7, 3.8, std('#F8FAFC', { metalness: 0.4, roughness: 0.3 }), 0, 0.6, 0, 0.2);
  rbx(car, 1.5, 0.5, 2.0, std('#1F2937', { metalness: 0.3, roughness: 0.15 }), 0, 1.13, -0.3, 0.15);
  for (const [x, z] of [[-0.85, 1.2], [0.85, 1.2], [-0.85, -1.2], [0.85, -1.2]]) {
    const w = cyl(car, 0.32, 0.32, 0.22, M.tyre, x, 0.32, z, 16); w.rotation.z = Math.PI / 2;
  }
  rbx(g, 0.5, 0.7, 1.4, std('#CBD5E1'), 2.3, 0.45, 5.4, 0.12);
  // 客廳：窗下兩張小朋友書桌、右牆沙發 + 按摩椅、左牆電視、門邊木櫃
  bx(g, 2.6, 0.05, 0.65, M.wood, -1.15, 0.75, 2.6); for (const x of [-2.4, 0.1]) bx(g, 0.05, 0.75, 0.6, M.dark, x, 0.375, 2.6);
  for (const x of [-1.8, -0.5]) rbx(g, 0.4, 0.45, 0.4, M.woodL, x, 0.23, 2.05);
  rbx(g, 0.9, 0.45, 2.2, std('#1F2937'), 2.5, 0.25, -1.0); rbx(g, 0.25, 0.75, 2.2, std('#111827'), 2.85, 0.45, -1.0);
  rbx(g, 0.8, 0.9, 1.3, std('#991B1B'), 2.45, 0.5, 0.9, 0.2);
  rbx(g, 0.6, 0.35, 0.9, M.white, 1.2, 0.2, -0.9);
  bx(g, 0.06, 0.9, 1.7, M.black, -2.9, 1.3, -0.9); bx(g, 0.45, 0.45, 2.2, M.wood, -2.72, 0.22, -0.9);
  bx(g, 0.45, 2.2, 1.4, M.woodL, 0.85, 1.1, 2.15);
  bx(g, 1.3, 1.1, 0.6, std('#4C0519'), -1.6, 0.55, -5.2);                             // 樓梯下鋼琴
  // 廚房：左牆冰箱烤箱高櫃、中間長餐桌、後面 L 型流理台、右牆家電檯
  bx(g, 0.65, 2.3, 2.6, M.white, -2.65, 1.15, -7.6); bx(g, 0.66, 1.85, 0.8, std('#CBD5E1', { metalness: 0.5 }), -2.64, 0.93, -6.8);
  bx(g, 1.0, 0.06, 2.0, M.white, -0.6, 0.75, -7.6);
  for (const z of [-8.2, -7.0]) for (const x of [-1.35, 0.15]) rbx(g, 0.4, 0.45, 0.4, M.wood, x, 0.23, z);
  bx(g, 3.2, 0.9, 0.6, std('#D1D5DB'), -0.6, 0.45, -9.05); bx(g, 3.2, 0.7, 0.35, M.white, -0.6, 2.0, -9.2);
  bx(g, 0.55, 0.9, 2.6, M.white, 2.7, 0.45, -7.6);
  for (const z of [-8.4, -7.4, -6.6]) cyl(g, 0.17, 0.17, 0.28, std('#F1F5F9'), 2.7, 1.05, z, 14);
  // 洗衣間：滾筒洗衣機、洗衣台、冷凍櫃、家電架；廁所
  rbx(g, 0.65, 0.95, 0.65, M.white, 2.55, 0.48, -10.6); bx(g, 0.7, 0.85, 0.5, std('#EAB308'), 1.7, 0.43, -10.7);
  bx(g, 0.65, 1.8, 0.65, M.white, 0.6, 0.9, -10.6); bx(g, 0.7, 1.5, 0.45, M.wood, -0.5, 0.75, -10.75);
  rbx(g, 0.4, 0.4, 0.55, M.white, -2.5, 0.2, -10.6); rbx(g, 0.3, 0.6, 0.25, M.white, -1.8, 0.7, -10.85);
}
// ===== 2F：前陽台 (熱水器) → 主臥 (後段衣櫃、廁所) → 樓梯口 → 書房 =====
{
  const f = 2, g = floors[f].g;
  slab(g, true); bx(g, 2 * HW + 0.2, SLAB, BAL - ZF, M.slab, 0, -SLAB / 2, (ZF + BAL) / 2);
  sides(g, ZF);
  room(f, '前陽台', -3, 3, ZF, BAL, '#92400E'); room(f, '主臥室', -3, 3, -1.0, ZF, '#B45309');
  room(f, '主臥廁所', -3, -0.6, SZ1, -1.0, '#5B7C99'); room(f, '主臥', -0.6, 3, SZ1, -1.0, '#B45309', { showLabel: false });
  room(f, '樓梯口', SX1, 3, SZ0, SZ1, '#A16207', { showLabel: false }); room(f, '書房', -3, 3, ZB, SZ0, '#A16207');
  wall(g, -3, ZF, 0.0, ZF); wall(g, 0.0, ZF, 2.4, ZF, M.glass); wall(g, 2.4, ZF, 3, ZF);
  balusters(g, -3, 3, BAL); wall(g, -3, ZF, -3, BAL, grilleMat); wall(g, 3, ZF, 3, BAL, grilleMat);
  wall(g, -3, -1.0, -0.6, -1.0); wall(g, -0.6, SZ1, -0.6, -2.2); wall(g, -0.6, -1.4, -0.6, -1.0);
  wall(g, -3, SZ1, SX1, SZ1); wall(g, SX1, SZ1, 0.6, SZ1); wall(g, 1.6, SZ1, 3, SZ1);
  rail(g, SX1, SZ0, SX1, SZ1);
  wall(g, -3, SZ0, 0.6, SZ0); wall(g, 1.6, SZ0, 3, SZ0);
  wall(g, -1.2, ZB, 1.2, ZB, M.glass);
  stairs(g);
  bed(g, -2.9, -0.9, 0.2, 2.5);
  bx(g, 2.0, 2.1, 0.6, M.woodL, -1.6, 1.05, -0.7);                                    // 衣櫃
  bx(g, 0.6, 0.45, 0.5, std('#BFDBFE'), 2.5, 0.23, 0.6); rbx(g, 0.35, 0.5, 0.35, M.white, 2.6, 0.25, 1.4);
  rbx(g, 0.4, 0.4, 0.55, M.white, -2.5, 0.2, -2.2); bx(g, 0.9, 1.9, 0.9, M.glass, -1.3, 0.95, -1.6);
  bx(g, 0.5, 0.7, 0.2, M.white, -2.9, 1.5, 3.7);                                      // 瓦斯熱水器
  bx(g, 1.6, 0.05, 0.7, M.woodL, 0.8, 0.75, -10.55); rbx(g, 0.45, 0.5, 0.45, M.fabric2, 0.8, 0.25, -9.9);
  bx(g, 1.6, 0.9, 0.4, M.white, -1.8, 0.45, -10.75);
  for (const [x, c] of [[-2.3, '#EAB308'], [-1.8, '#EA580C'], [-1.3, '#EAB308']]) bx(g, 0.4, 0.25, 0.3, std(c), x, 0.6, -10.6);
}
// ===== 3F：前陽台 → 3 樓房間 (床靠左牆、後段衣櫃) → 走道 (左邊浴室) → 樓梯口 → 視聽室 =====
{
  const f = 3, g = floors[f].g;
  slab(g, true); bx(g, 2 * HW + 0.2, SLAB, BAL - ZF, M.slab, 0, -SLAB / 2, (ZF + BAL) / 2);
  sides(g, ZF);
  room(f, '前陽台', -3, 3, ZF, BAL, '#92400E', { showLabel: false }); room(f, '3 樓房間', -3, 3, -0.4, ZF, '#B45309');
  room(f, '浴室', -3, 1.0, SZ1, -0.4, '#5B7C99'); room(f, '走道', 1.0, 3, SZ1, -0.4, '#A16207', { showLabel: false });
  room(f, '樓梯口', SX1, 3, SZ0, SZ1, '#A16207', { showLabel: false }); room(f, '視聽室', -3, 3, ZB, SZ0, '#C2410C');
  wall(g, -3, ZF, 3, ZF, M.glass);
  balusters(g, -3, 3, BAL); wall(g, -3, ZF, -3, BAL, grilleMat); wall(g, 3, ZF, 3, BAL, grilleMat);
  wall(g, -3, -0.4, 1.6, -0.4); wall(g, 2.6, -0.4, 3, -0.4);                         // 房間門在走道盡頭右邊
  wall(g, 1.0, SZ1, 1.0, -2.0); wall(g, 1.0, -1.2, 1.0, -0.4);                       // 浴室門開向走道
  wall(g, -3, SZ1, SX1, SZ1, M.wall); bx(g, 0.8, 0.5, 0.1, M.glass, -1.6, 2.0, SZ1);   // 浴室對樓梯間的霧面小窗
  rail(g, SX1, SZ0, SX1, SZ1);
  wall(g, -3, SZ0, 1.6, SZ0); wall(g, 2.6, SZ0, 3, SZ0);                             // 視聽室門在右邊
  wall(g, -1.2, ZB, 1.2, ZB, M.glass);
  stairs(g);
  bed(g, -2.9, -1.0, 0.6, 2.6);
  bx(g, 2.2, 2.1, 0.6, M.woodL, -1.6, 1.05, -0.05);                                   // 衣櫃
  rbx(g, 0.4, 0.4, 0.55, M.white, -2.5, 0.2, -2.1); bx(g, 0.5, 0.15, 0.4, M.white, -1.4, 0.8, -2.35);
  bx(g, 1.0, 1.9, 0.9, M.glass, 0.3, 0.95, -1.5);                                     // 淋浴間
  // 視聽室：右牆電視 + 落地喇叭、後牆窗戶、書桌、模型櫃
  bx(g, 0.06, 0.75, 1.3, M.black, 2.92, 1.4, -8.4); bx(g, 0.45, 0.5, 0.9, M.wood, 2.65, 0.25, -8.4);
  for (const z of [-9.4, -7.4]) bx(g, 0.3, 1.05, 0.3, std('#92400E'), 2.7, 0.53, z);
  bx(g, 1.3, 0.05, 0.6, M.woodL, -0.8, 0.75, -10.6); rbx(g, 0.5, 0.9, 0.5, M.black, -0.6, 0.45, -9.9);
  bx(g, 0.4, 0.75, 1.6, M.white, -2.75, 0.38, -7.6); bx(g, 0.45, 0.8, 1.2, M.black, -2.7, 0.4, -9.5);
  sprite(g, GLOW_B, 1.6, 2.85, 1.4, -8.4, NIGHT ? 0.3 : 0.12);
}
// ===== 4F：露台 (黃色矮牆) → 太陽能機房 (右牆逆變器、電池架、冷氣) → 走道 → 健身室 =====
const Z4 = 0.6;                                    // 4 樓室內的最前面 (再往前是露台)
{
  const f = 4, g = floors[f].g;
  slab(g, true, BAL); sides(g, Z4);
  room(f, '露台', -3, 3, Z4, BAL, '#E7E5E4'); room(f, '太陽能機房', -3, 3, SZ1, Z4, '#E5E7EB');
  room(f, '走道', SX1, 3, SZ0, SZ1, '#E5E7EB', { showLabel: false }); room(f, '健身室', -3, 3, ZB, SZ0, '#E5E7EB');
  wall(g, -3, Z4, -1.3, Z4); wall(g, -1.3, Z4, 1.3, Z4, grilleMat); wall(g, 1.3, Z4, 3, Z4);   // 鐵窗落地拉門
  bx(g, 2.6, 2.3, 0.03, M.glass, 0, 1.15, Z4 - 0.05);
  wall(g, -3, SZ1, SX1, SZ1); wall(g, 1.6, SZ1, 3, SZ1);                                      // 機房後面開口接走道
  rail(g, SX1, SZ0, SX1, SZ1);
  wall(g, -3, SZ0, 1.6, SZ0); wall(g, 2.6, SZ0, 3, SZ0);
  wall(g, -1.0, ZB, 1.0, ZB, M.glass);
  const para = std('#FDE68A', { roughness: 0.9 });                                           // 露台黃色矮牆
  bx(g, 2 * HW, 1.0, 0.18, para, 0, 0.5, BAL); for (const x of [-HW, HW]) bx(g, 0.18, 1.0, BAL - Z4, para, x, 0.5, (Z4 + BAL) / 2);
  bx(g, 2 * HW + 0.3, 0.08, 1.0, std('#E5E7EB', { metalness: 0.5 }), 0, CEIL, Z4 + 0.45);       // 拉門上方屋簷
  bx(g, 0.75, 0.55, 0.3, M.white, -2.1, 2.1, Z4 + 0.2);                                       // 冷氣室外機
  bx(g, 0.5, 0.8, 0.4, std('#D1D5DB', { metalness: 0.7 }), -2.7, 0.4, Z4 + 0.5);              // 洗手台
  // 機房：左牆靠著備用太陽能板與梯子、後段置物架
  for (let i = 0; i < 5; i++) { const p = bx(g, 0.04, 1.7, 1.05, std('#CBD5E1', { metalness: 0.4 }), 2.75 - i * 0.06, 0.85, -0.9);
    p.rotation.z = 0.12; }
  for (const y of [0.1, 0.7, 1.3, 1.8]) bx(g, 0.45, 0.04, 1.2, M.metal, -2.75, y, -2.0);   // 置物架
  bx(g, 0.35, 0.4, 0.5, std('#60A5FA'), -2.75, 1.5, -2.2);
  // 健身室：後牆窗簾、跑步機、紙箱、工作桌、小冰箱
  bx(g, 1.7, 1.6, 0.05, std('#9CA3AF'), 0, 1.5, ZB + 0.12);
  bx(g, 0.75, 1.5, 0.5, M.dark, -1.6, 0.75, -10.5);
  for (const [x, y] of [[-0.6, 0.3], [-0.6, 0.85], [-0.1, 0.3]]) bx(g, 0.55, 0.55, 0.5, std('#B45309'), x, y, -10.6);
  bx(g, 0.7, 0.75, 1.4, M.woodL, -2.6, 0.38, -6.9); bx(g, 0.65, 1.2, 0.6, std('#9CA3AF', { metalness: 0.4 }), 2.6, 0.6, -6.5);
}
// ===== 屋頂：人字屋頂 + 太陽能板 (前後兩坡各 12 片) =====
const panelMats = [];
{
  const zc = (Z4 + ZB) / 2, half = (Z4 - ZB) / 2, rise = 1.3, L = Math.hypot(half, rise), a = Math.atan2(rise, half);
  bx(roof, 2 * HW + 0.2, SLAB, Z4 - ZB + 0.2, M.slab, 0, -SLAB / 2, zc);
  const c = document.createElement('canvas'); c.width = 64; c.height = 128; const x = c.getContext('2d');
  x.fillStyle = '#1E3A8A'; x.fillRect(0, 0, 64, 128); x.strokeStyle = '#93C5FD'; x.lineWidth = 2;
  for (let i = 0; i <= 64; i += 16) { x.beginPath(); x.moveTo(i, 0); x.lineTo(i, 128); x.stroke(); }
  for (let j = 0; j <= 128; j += 21) { x.beginPath(); x.moveTo(0, j); x.lineTo(64, j); x.stroke(); }
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  for (const side of [1, -1]) {
    const s = new THREE.Group(); s.position.set(0, rise / 2 + 0.3, zc + side * half / 2); s.rotation.x = side * a; roof.add(s);
    bx(s, 2 * HW + 0.5, 0.08, L, std('#475569'), 0, 0, 0);
    const pm = std('#FFFFFF', { map: tex, metalness: 0.5, roughness: 0.25, emissive: '#38BDF8', emissiveIntensity: 0 });
    panelMats.push(pm);
    for (let i = 0; i < 6; i++) for (const zz of [-0.9, 0.9]) bx(s, 0.95, 0.05, 1.7, pm, -2.5 + i, 0.07, zz * side);
  }
  for (const sx of [-1, 1]) {
    const sh = new THREE.Shape(); sh.moveTo(-half, 0); sh.lineTo(half, 0); sh.lineTo(0, rise); sh.closePath();
    const m = new THREE.Mesh(new THREE.ShapeGeometry(sh), std('#94A3B8', { side: THREE.DoubleSide }));
    m.position.set(sx * (HW + 0.02), 0.3, zc); m.rotation.y = Math.PI / 2; roof.add(m);
  }
  for (const [x, z] of [[-HW, ZB], [HW, ZB], [-HW, Z4], [HW, Z4]]) bx(roof, 0.22, 0.3, 0.22, M.pillar, x, 0.15, z);
}

// ---------- 裝置 ----------
const devs = {};
const ST_TXT = { on: '開啟中', off: '關閉', offline: '離線', missing: '米家裡找不到', unknown: '狀態不明', nonet: '還沒聯網', locked: '🔒 家人登入後顯示' };
function reg(id, f, anchor, picks, extra = {}) {
  const s = D.slots[id] || { st: 'missing', n: id };
  const kind = D.kinds[id] || 'light';
  const el = document.createElement('div');
  const cls = kind === 'ac' ? 'ac' : kind === 'heater' ? 'heat' : '';
  el.className = `tag ${cls} ${s.st === 'on' ? 'on' : ['off', 'locked'].includes(s.st) ? '' : 'gray'}`;
  el.textContent = s.st === 'on' ? `${s.n}${s.t ? ' · ' + s.t : ''}` : `${s.n} · ${ST_TXT[s.st] || s.st}`;
  const tag = new CSS2DObject(el); tag.position.copy(anchor); floors[f].g.add(tag);
  const d = { id, f, s, kind, tag, el, on: s.st === 'on', tick: null, ...extra };
  for (const p of picks) p.traverse(o => { o.userData.dev = id; });
  devs[id] = d;
  return d;
}
function roomOn(f, name) { if (name && floors[f].rooms[name]) floors[f].rooms[name].on++; }
const dimmed = s => !s || !['on', 'off'].includes(s.st);

function light(id, f, x, z, roomName, { r = 1.3, y = CEIL - 0.03 } = {}) {
  const g = floors[f].g, s = D.slots[id];
  if (s && s.st === 'hide') return null;
  const on = s && s.st === 'on';
  const fix = cyl(g, 0.2, 0.22, 0.05, std(dimmed(s) ? '#64748B' : '#F1F5F9', { emissive: '#FFD58A', emissiveIntensity: on ? 2.4 : 0 }), x, y, z, 24);
  if (on) {
    const c = new THREE.Mesh(new THREE.ConeGeometry(r, y, 28, 1, true), coneMat()); c.position.set(x, y / 2, z); g.add(c);
    sprite(g, GLOW, 1.1, x, y - 0.1, z, 0.9);
    roomOn(f, roomName);
  }
  return reg(id, f, new THREE.Vector3(x, y + 0.25, z), [fix]);
}
function downlights(id, f, pts, roomName) {        // 一組嵌燈 (同一個開關)
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on', y = CEIL - 0.03;
  const mat = std(dimmed(s) ? '#64748B' : '#F1F5F9', { emissive: '#FFF1C1', emissiveIntensity: on ? 2.6 : 0 });
  const parts = pts.map(([x, z]) => {
    const m = cyl(g, 0.09, 0.09, 0.03, mat, x, y, z, 16);
    if (on) { const c = new THREE.Mesh(new THREE.ConeGeometry(0.7, y, 20, 1, true), coneMat()); c.position.set(x, y / 2, z); g.add(c);
      sprite(g, GLOW, 0.6, x, y - 0.08, z, 0.8); }
    return m;
  });
  if (on) roomOn(f, roomName);
  const [x, z] = pts[0];
  return reg(id, f, new THREE.Vector3(x, y + 0.25, z), parts);
}
function strip(id, f, x0, x1, z0, z1, roomName) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on', y = CEIL - 0.06, t = 0.05;
  const m = basic(on ? '#FCD34D' : dimmed(s) ? '#334155' : '#64748B');
  const parts = [bx(g, x1 - x0, t, t, m, (x0 + x1) / 2, y, z0), bx(g, x1 - x0, t, t, m, (x0 + x1) / 2, y, z1),
                 bx(g, t, t, z1 - z0, m, x0, y, (z0 + z1) / 2), bx(g, t, t, z1 - z0, m, x1, y, (z0 + z1) / 2)];
  if (on) {
    roomOn(f, roomName);
    for (const [px, pz] of [[(x0 + x1) / 2, z0], [(x0 + x1) / 2, z1], [x0, (z0 + z1) / 2], [x1, (z0 + z1) / 2]])
      sprite(g, GLOW, 1.4, px, y - 0.05, pz, 0.45);
  }
  return reg(id, f, new THREE.Vector3(x1 - 0.3, y + 0.2, z0 + 0.3), parts);
}
function nightLight(id, f, x, z, roomName) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const m = new THREE.Mesh(new THREE.SphereGeometry(0.08, 16, 10),
    std(dimmed(s) ? '#64748B' : '#FEF3C7', { emissive: '#FDBA74', emissiveIntensity: on ? 2.5 : 0 }));
  m.position.set(x, 0.62, z); g.add(m);
  if (on) { sprite(g, GLOW_O, 1.0, x, 0.65, z, 0.8); roomOn(f, roomName); }
  return reg(id, f, new THREE.Vector3(x, 1.0, z), [m]);
}
function lamp(id, f, x, z, dir = 1) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const grp = new THREE.Group(); grp.position.set(x, 0.78, z); g.add(grp);
  cyl(grp, 0.08, 0.09, 0.02, M.white, 0, 0.01, 0);
  cyl(grp, 0.012, 0.012, 0.42, M.white, 0, 0.21, 0, 6);
  const head = new THREE.Mesh(new THREE.BoxGeometry(0.32, 0.03, 0.06),
    std(dimmed(s) ? '#64748B' : '#F8FAFC', { emissive: '#FFF1C1', emissiveIntensity: on ? 2.6 : 0 }));
  head.position.set(0, 0.42, 0.08 * dir); grp.add(head);
  if (on) {
    const c = new THREE.Mesh(new THREE.ConeGeometry(0.3, 0.42, 20, 1, true), coneMat()); c.position.set(0, 0.21, 0.08 * dir); grp.add(c);
    sprite(grp, GLOW, 0.7, 0, 0.38, 0.08 * dir, 0.9);
  }
  return reg(id, f, new THREE.Vector3(x, 1.45, z), [grp]);
}
function ac(id, f, x, z, ry, y = CEIL - 0.42) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const grp = new THREE.Group(); grp.position.set(x, y, z); grp.rotation.y = ry; g.add(grp);
  rbx(grp, 0.95, 0.3, 0.24, std(dimmed(s) ? '#64748B' : '#F8FAFC', { roughness: 0.4 }), 0, 0, 0, 0.06);
  bx(grp, 0.4, 0.025, 0.01, basic(on ? '#38BDF8' : '#334155'), 0, -0.04, 0.125);
  let pts = null, life = null;
  if (on) {
    const n = 36; life = new Float32Array(n).map(() => Math.random());
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3));
    pts = new THREE.Points(geo, new THREE.PointsMaterial({ color: '#7DD3FC', size: 0.07, transparent: true, opacity: 0.85,
      depthWrite: false, blending: THREE.AdditiveBlending }));
    pts.userData.seed = new Float32Array(n).map(() => Math.random() - 0.5);
    grp.add(pts);
    sprite(grp, GLOW_B, 1.0, 0, -0.1, 0.25, 0.5);
  }
  const d = reg(id, f, new THREE.Vector3(x, y + 0.45, z), [grp]);
  if (pts) d.tick = (t, dt) => {
    const p = pts.geometry.attributes.position.array, seed = pts.userData.seed;
    for (let i = 0; i < life.length; i++) {
      life[i] += dt * 0.55; if (life[i] > 1) life[i] -= 1;
      const k = life[i];
      p[i * 3] = seed[i] * 0.85; p[i * 3 + 1] = -0.18 - k * 1.1; p[i * 3 + 2] = 0.15 + k * 1.3;
    }
    pts.geometry.attributes.position.needsUpdate = true;
  };
  return d;
}
function heater(id, f, x, z) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const tank = cyl(g, 0.3, 0.3, 1.1, std(dimmed(s) ? '#64748B' : '#E5E7EB', { metalness: 0.75, roughness: 0.2,
    emissive: '#F97316', emissiveIntensity: on ? 0.5 : 0 }), x, 0.75, z, 24);
  for (const [dx, dz] of [[-0.18, -0.18], [0.18, -0.18], [-0.18, 0.18], [0.18, 0.18]]) cyl(g, 0.02, 0.02, 0.2, M.metal, x + dx, 0.1, z + dz, 6);
  let steam = null;
  if (on) {
    sprite(g, GLOW_O, 1.6, x, 0.8, z, 0.6);
    steam = [];
    for (let i = 0; i < 6; i++) steam.push(sprite(g, glowTex('rgba(255,255,255,1)'), 0.3, x, 1.4, z, 0.4));
  }
  const d = reg(id, f, new THREE.Vector3(x, 1.8, z), [tank]);
  if (on) d.tick = (t) => {
    tank.material.emissiveIntensity = 0.35 + 0.25 * Math.sin(t * 3);
    steam.forEach((sp, i) => { const k = (t * 0.35 + i / steam.length) % 1;
      sp.position.set(x + Math.sin(t + i) * 0.08, 1.35 + k * 0.9, z); sp.material.opacity = 0.45 * (1 - k); sp.scale.setScalar(0.25 + k * 0.4); });
  };
  return d;
}
function garage(id, f, x0, x1, z) {
  const g = floors[f].g, s = D.slots[id];
  const open = s && s.st === 'on' ? Math.max(0.15, (s.pos ?? 100) / 100) : (s && typeof s.pos === 'number' ? s.pos / 100 : 0);
  const tex = stripeTex(16, 128, x => {
    x.fillStyle = dimmed(s) ? '#64748B' : '#D1D5DB'; x.fillRect(0, 0, 16, 128); x.fillStyle = 'rgba(15,23,42,.3)';
    for (let j = 0; j < 128; j += 6) x.fillRect(0, j, 16, 1.2);
  });
  const H = CEIL - 0.45, Wd = x1 - x0, xc = (x0 + x1) / 2;
  const door = new THREE.Mesh(new THREE.BoxGeometry(Wd, H, 0.05), std('#FFFFFF', { map: tex, metalness: 0.6, roughness: 0.4 }));
  door.position.set(xc, H / 2, z); g.add(door);
  bx(g, Wd + 0.2, 0.4, 0.4, std('#9CA3AF', { metalness: 0.6 }), xc, H + 0.2, z - 0.15);       // 捲門箱
  bx(g, 0.25, 0.2, 0.25, M.dark, x1 - 0.2, H + 0.05, z - 0.45);                                // 馬達
  const d = reg(id, f, new THREE.Vector3(xc, H + 0.7, z + 0.2), [door]);
  let cur = 0;
  d.tick = (t, dt) => {
    cur += (open - cur) * Math.min(1, dt * 1.5);
    const k = Math.max(0.02, 1 - cur);
    door.scale.y = k; door.position.y = H - (H * k) / 2;
  };
  return d;
}
function curtainZ(id, f, x0, x1, z) {               // 平行 x 軸的窗簾 (窗戶在 z 這面牆上)
  const g = floors[f].g, s = D.slots[id];
  const o = s && typeof s.pos === 'number' ? s.pos / 100 : s && s.st === 'on' ? 1 : 0;
  const len = x1 - x0, h = 1.9;
  const mat = std(dimmed(s) ? '#64748B' : '#F5F5F4', { side: THREE.DoubleSide, roughness: 1 });
  // 捲簾：開得越多捲得越上去
  const k = Math.max(0.05, 1 - o);
  const a = bx(g, len, h * k, 0.04, mat, (x0 + x1) / 2, 2.55 - (h * k) / 2, z - 0.12);
  bx(g, len + 0.1, 0.08, 0.08, M.white, (x0 + x1) / 2, 2.6, z - 0.12);
  return reg(id, f, new THREE.Vector3((x0 + x1) / 2, 2.9, z - 0.3), [a]);
}
function tv(id, f, x, z) {                         // 牆上電視 (面向 +x)：開著螢幕發藍光
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(1.55, 0.8), std('#0B1220', { emissive: '#60A5FA', emissiveIntensity: on ? 1.3 : 0 }));
  scr.position.set(x, 1.3, z); scr.rotation.y = Math.PI / 2; g.add(scr);
  if (on) sprite(g, GLOW_B, 2.0, x + 0.2, 1.3, z, NIGHT ? 0.45 : 0.25);
  return reg(id, f, new THREE.Vector3(x + 0.1, 2.0, z), [scr]);
}
function dish(id, f, x, z) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const box = bx(g, 0.58, 0.8, 0.6, std(dimmed(s) ? '#64748B' : '#E5E7EB', { metalness: 0.5, roughness: 0.3 }), x, 0.4, z);
  const led = bx(g, 0.12, 0.05, 0.02, basic(on ? '#22D3EE' : '#334155'), x, 0.72, z + 0.31);
  const d = reg(id, f, new THREE.Vector3(x, 1.25, z + 0.3), [box]);
  if (on) d.tick = t => { led.visible = Math.sin(t * 4) > -0.3; };
  return d;
}
function speaker(id, f, x, z, y = 0.6) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const sp = cyl(g, 0.06, 0.07, 0.16, std(dimmed(s) ? '#64748B' : '#1F2937'), x, y, z, 20);
  let ring = null;
  if (on) { ring = new THREE.Mesh(new THREE.TorusGeometry(0.12, 0.008, 6, 32), basic('#A78BFA', { transparent: true }));
    ring.rotation.x = Math.PI / 2; ring.position.set(x, y + 0.02, z); g.add(ring); }
  const d = reg(id, f, new THREE.Vector3(x, y + 0.4, z), [sp]);
  if (ring) d.tick = t => { const k = (t * 0.8) % 1; ring.scale.setScalar(1 + k * 3); ring.material.opacity = 1 - k; };
  return d;
}

// 1F
garage('garage_door', 1, -3, 1.8, ZC);
ac('f1_living_ac', 1, -1.15, ZF - 0.14, Math.PI);
light('f1_ceiling', 1, 0.2, -0.1, '客廳', { r: 1.8 });
downlights('f1_flood', 1, [[-1.6, 1.6], [1.6, 1.6], [-1.6, -1.6], [1.6, -1.6]], '客廳');
strip('f1_indirect', 1, -2.8, 2.8, -2.4, 2.8, '客廳');
tv('f1_tv', 1, -2.86, -0.9);
lamp('f1_lamp_chen', 1, -1.9, 2.75, -1); lamp('f1_lamp_hao', 1, -0.5, 2.75, -1);
light('f1_stair', 1, -1.6, -4.3, '樓梯', { r: 0.8 });
light('f1_kitchen', 1, 0.3, -7.6, '廚房 · 餐廳', { r: 1.8 });
light('f1_dining2', 1, -2.2, -5.6, '樓梯', { r: 0.8 });          // 餐廳開關第 2 路：樓梯電燈
light('f1_dining3', 1, 1.3, -4.3, '走道', { r: 1.0 });           // 餐廳開關第 3 路：走道電燈 (樓梯旁通往廚房)
dish('f1_dish', 1, 0.4, -9.05);
speaker('f1_speaker', 1, -2.7, 0.35, 0.53);
light('f1_laundry', 1, 1.0, -10.2, '洗衣間', { r: 1.1 });
light('f1_wc', 1, -2.2, -9.9, '廁所', { r: 0.55 });
light('f1_wc_back', 1, -2.2, -10.6, '廁所', { r: 0.55 });
// 2F
heater('f2_heater', 2, -2.55, 3.65);
ac('f2_bed_ac', 2, 1.2, ZF - 0.14, Math.PI);
downlights('f2_main', 2, [[-1.4, 0.4], [1.2, 0.4], [-1.4, 2.2], [1.2, 2.2]], '主臥室');
strip('f2_bed_indirect', 2, -2.8, 2.8, -0.8, 2.8, '主臥室');
nightLight('f2_night', 2, -2.75, 2.75, '主臥室');
curtainZ('f2_curtain', 2, -0.1, 2.5, ZF);
light('stair2', 2, -2.2, -5.6, '樓梯口', { r: 0.8 });
speaker('f2_speaker', 2, -2.9, 1.0, 0.98);
light('f2_wc', 2, -1.8, -1.8, '主臥廁所', { r: 0.8 });
light('f2_hall', 2, 0.5, -1.8, '主臥', { r: 0.7 });           // 「2樓走道照明」= 主臥衣櫃旁的走道 (樓梯口沒有米家開關)
light('f2_hall2', 2, 2.2, -1.8, '主臥', { r: 0.7 });
ac('f2_study_ac', 2, 1.6, ZB + 0.14, 0);
lamp('f2_lamp', 2, 1.3, -10.75);
// 3F
ac('f3_bed_ac', 3, 0.9, ZF - 0.14, Math.PI);
ac('f3_av_ac', 3, 1.7, ZB + 0.14, 0);
// 4F
ac('f4_solar_ac', 4, -2.88, -0.7, Math.PI / 2, CEIL - 0.35);
ac('f4_gym_ac', 4, -2.88, -9.2, Math.PI / 2);

// 4F 機房：右牆的逆變器、電箱、4 組電池抽屜架 (公開資訊)
const E = D.energy || {};
{
  const g = floors[4].g;
  const packs = (E.packs && E.packs.length ? E.packs : [E.soc, E.soc, E.soc, E.soc]).slice(0, 4);
  const RZ = -0.7;                                 // 電池架位置
  for (const x of [-2.92, -2.38]) for (const z of [RZ - 0.42, RZ + 0.42]) bx(g, 0.04, 1.5, 0.04, M.metal, x, 0.75, z);
  packs.forEach((soc, i) => {
    const y = 0.22 + i * 0.36;
    bx(g, 0.5, 0.28, 0.78, M.white, -2.65, y, RZ);
    const k = typeof soc === 'number' ? Math.max(0.03, soc / 100) : 0;
    const col = soc >= 60 ? '#22C55E' : soc >= 30 ? '#EAB308' : '#EF4444';
    bx(g, 0.01, 0.05, 0.6, basic('#0F172A'), -2.395, y + 0.06, RZ);
    if (k) bx(g, 0.012, 0.05, 0.6 * k, basic(col), -2.392, y + 0.06, RZ - 0.3 + 0.3 * k);
  });
  const pvOn = (E.pv || 0) > 50;
  bx(g, 0.2, 0.75, 0.5, M.white, -2.88, 1.75, 0.0);                                            // 逆變器
  bx(g, 0.01, 0.12, 0.22, basic(pvOn ? '#34D399' : '#334155'), -2.775, 1.85, 0.0);
  if (pvOn) sprite(g, glowTex('rgba(110,231,183,1)'), 0.6, -2.7, 1.85, 0.0, 0.5);
  bx(g, 0.12, 0.4, 0.35, std('#F1F5F9'), -2.92, 1.5, -0.4); bx(g, 0.12, 0.3, 0.3, std('#F1F5F9'), -2.92, 0.9, 0.0);   // 電箱
  const parts = [];
  if (typeof E.pv === 'number') parts.push(`☀️ ${(E.pv / 1000).toFixed(1)} kW`);
  if (typeof E.soc === 'number') parts.push(`🔋 ${E.soc}%` + (E.packs && E.packs.length > 1 ? `（${E.packs.map(v => v ?? '—').join(' / ')}）` : ''));
  if (typeof E.load === 'number') parts.push(`🏠 ${(E.load / 1000).toFixed(1)} kW`);
  window.ENERGY_TXT = parts.join(' · ');
  panelMats.forEach(m => { m.emissiveIntensity = Math.min(0.55, (E.pv || 0) / 12000); });
}

// 房間亮燈 → 地板染暖色
for (const fl of Object.values(floors)) for (const r of Object.values(fl.rooms))
  if (r.on) r.meshes.forEach(m => { m.material.emissiveIntensity = NIGHT ? 0.45 : 0.25; });

// 樓層名稱
for (const fl of Object.values(floors))
  fl.flabel = label(fl.g, 'flabel', `${fl.f}F<small>${FLOOR_NAME[fl.f]}</small>`, -HW - 0.4, 1.4, fl.f === 1 ? ZC : ZF);

// ---------- 上方摘要、樓層按鈕、開啟中清單 ----------
const onList = Object.values(devs).filter(d => d.on).sort((a, b) => a.f - b.f);
const sum = document.getElementById('sum');
const T = D.temps || {};
const tparts = [];
if (typeof T.out === 'number') tparts.push(`戶外 ${T.out.toFixed(1)}°C`);
if (typeof T.room === 'number') tparts.push(`儲能室 ${T.room.toFixed(1)}°C`);
if (window.ENERGY_TXT) tparts.unshift(window.ENERGY_TXT);
if (D.authed) {
  let s = `<b>${onList.length}</b> 台開啟中` + (tparts.length ? '<br>' + tparts.join(' · ') : '');
  if (D.login === 'need') s += '<br><span class="w">⚠️ 小米帳號需要登入，已用 LINE 傳 QR code 給你</span>';
  else if (D.age == null) s += '<br><span class="w">樹莓派還沒上傳家電資料（更新程式後約 1 分鐘出現）</span>';
  else if (D.age > 300) s += `<br><span class="w">家電資料 ${Math.round(D.age / 60)} 分鐘沒更新</span>`;
  sum.innerHTML = s;
} else {
  sum.innerHTML = '🔒 訪客模式：只顯示機房冷氣、熱水器與太陽能' + (tparts.length ? '<br>' + tparts.join(' · ') : '');
}
const chips = document.getElementById('chips');
for (const d of onList) {
  const c = document.createElement('span');
  c.className = 'chip ' + (d.kind === 'ac' ? 'ac' : d.kind === 'heater' ? 'heat' : '');
  c.textContent = `${d.f}F ${d.s.n}`; c.onclick = () => { setFocus(d.f); select(d.id); };
  chips.appendChild(c);
}
if (!D.authed) chips.insertAdjacentHTML('beforeend', '<span class="chip lock">🔒 燈、鐵捲門、各房間冷氣：家人用 LINE 收到的專用連結開啟</span>');
else if (!onList.length) chips.insertAdjacentHTML('beforeend', '<span class="chip lock">目前沒有開著的燈或家電</span>');

let focus = 0;
const fb = document.getElementById('floors');
for (const [k, txt] of [[0, '全部'], [1, '1F'], [2, '2F'], [3, '3F'], [4, '4F']]) {
  const b = document.createElement('button'); b.textContent = txt; b.dataset.f = k; b.onclick = () => setFocus(k); fb.appendChild(b);
}
let camGoal = null, tgtGoal = new THREE.Vector3(0, 9, -1.3);
function setFocus(f) {
  focus = f;
  fb.querySelectorAll('button').forEach(b => b.classList.toggle('sel', +b.dataset.f === f));
  tgtGoal = f ? new THREE.Vector3(0, (f - 1) * FH + 0.8, f === 1 ? -1.3 : -3.5) : new THREE.Vector3(0, 9, -1.3);
  const dir = camera.position.clone().sub(controls.target).normalize();
  if (f) { dir.y = Math.max(dir.y, 0.85); dir.normalize(); }
  camGoal = tgtGoal.clone().add(dir.multiplyScalar((f ? 19 : 41) * (NARROW ? 1.5 : 1)));
  updateTags();
}
controls.addEventListener('start', () => { camGoal = null; });

function updateTags() {
  for (const d of Object.values(devs)) {
    // 全部：只標開著的 (手機太擠就不標，看下方清單)；單一樓層：只標這一層，關著的也標
    d.tag.visible = focus ? (focus === d.f && d.s.st !== 'locked') : (d.on && !NARROW);
  }
  for (const fl of Object.values(floors)) {
    fl.roomLabels.forEach(l => { l.visible = focus === fl.f; });
    fl.flabel.visible = !focus && !NARROW;
  }
}

// ---------- 點裝置看詳情 ----------
const info = document.getElementById('info'), ib = document.getElementById('ib');
document.getElementById('ix').onclick = () => { info.style.display = 'none'; Object.values(devs).forEach(d => d.el.classList.remove('sel')); };
function select(id) {
  const d = devs[id]; if (!d) return;
  Object.values(devs).forEach(x => x.el.classList.toggle('sel', x.id === id));
  const s = d.s;
  let extra = '';
  if (s.st === 'missing') extra = '<div class="m">米家裡沒有找到名稱符合的裝置（名稱可能不一樣，在樹莓派執行 tools/mi_cloud.py list 查看）</div>';
  if (s.sw && s.sw.length > 1) extra += `<div class="m">各路開關：${s.sw.map((v, i) => `${i + 1} ${v ? '開' : '關'}`).join('、')}</div>`;
  ib.innerHTML = `<b>${esc(s.n)}</b><div class="m">${d.f}F${s.r ? ' · ' + esc(s.r) : ''}</div>` +
    `<div>${ST_TXT[s.st] || esc(s.st)}${s.t ? ' · ' + esc(s.t) : ''}</div>${extra}`;
  info.style.display = 'block';
  d.tag.visible = true;
}
const ray = new THREE.Raycaster(), ptr = new THREE.Vector2();
let down = null;
renderer.domElement.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', e => {
  if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 6) return;
  const r = renderer.domElement.getBoundingClientRect();
  ptr.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(ptr, camera);
  const hits = ray.intersectObjects(scene.children, true).filter(h => h.object.visible && h.object.userData.dev);
  for (const h of hits) {
    let o = h.object, vis = true;
    while (o) { if (!o.visible) vis = false; o = o.parent; }
    if (vis) { select(h.object.userData.dev); return; }
  }
});

// ---------- 動畫 ----------
const clock = new THREE.Clock();
function resize() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  renderer.setSize(w, h); labelR.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(wrap);
setFocus(0); camGoal = null;
function loop() {
  const dt = Math.min(clock.getDelta(), 0.1), t = clock.elapsedTime;
  for (const fl of Object.values(floors)) {
    const goal = (fl.f - 1) * (FH + (focus ? 0 : GAP));
    fl.g.position.y += (goal - fl.g.position.y) * Math.min(1, dt * 5);
    fl.g.visible = !focus || fl.f <= focus;
  }
  roof.position.y = floors[4].g.position.y + FH + (focus ? 0 : GAP * 0.6);
  roof.visible = !focus;
  for (const d of Object.values(devs)) if (d.tick) d.tick(t, dt);
  controls.target.lerp(tgtGoal, Math.min(1, dt * 4));
  if (camGoal) { camera.position.lerp(camGoal, Math.min(1, dt * 3)); if (camera.position.distanceTo(camGoal) < 0.05) camGoal = null; }
  controls.update();
  renderer.render(scene, camera);
  labelR.render(scene, camera);
  requestAnimationFrame(loop);
}
loop();
</script></body></html>"""


def render(live, authed, height=620):
    state = home_map.build(live, authed)
    hour = int((state.get("time") or "T12")[11:13] or 12) if state.get("time") else 12
    night = hour < 6 or hour >= 18
    sky = ("linear-gradient(180deg,#0B1022 0%,#1E293B 100%)" if night
           else "linear-gradient(180deg,#7DB7E8 0%,#CFE6F7 70%,#E2EEF7 100%)")
    return (_TEMPLATE.replace("__DATA__", json.dumps(state, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__H__", str(height)).replace("__SKY__", sky))


def others_html(live, authed):
    """米家裡有、但 3D 圖上沒有對應位置的裝置 (只有家人登入時顯示)。"""
    if not authed:
        return ""
    st = home_map.build(live, True)
    rows = st.get("others") or []
    if not rows:
        return ""
    icon = {"on": "🟡", "off": "⚪", "offline": "⚫", "unknown": "⚪"}
    items = "".join(
        f'<span style="display:inline-block;margin:3px 6px 3px 0;padding:3px 10px;border-radius:999px;background:#0F172A;'
        f'border:1px solid #334155;font-size:12px;color:{"#FDE68A" if r["st"] == "on" else "#94A3B8"}">'
        f'{icon.get(r["st"], "⚪")} {html.escape(r.get("n") or "")}'
        f'{" · " + html.escape(r["t"]) if r.get("t") and r["st"] == "on" else ""}'
        f'{" · 離線" if r["st"] == "offline" else ""}</span>' for r in rows)
    return (f'<div style="background:#1E293B;border:1px solid #334155;border-radius:14px;padding:10px 14px;margin:10px 0;'
            f'font-family:\'Noto Sans TC\',sans-serif"><div style="color:#94A3B8;font-size:13px;margin-bottom:4px">'
            f'其他米家 / eWeLink 裝置（3D 圖上還沒有位置）</div>{items}</div>')
