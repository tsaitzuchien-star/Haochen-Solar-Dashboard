"""🏠 澔宸 AI 智慧小窩：4 層樓透天 3D 模型 (Three.js)，即時顯示哪些燈、冷氣、家電正在開。

資料由 home_map.build() 在網站伺服器端產生：沒用家人專用連結登入時，燈、鐵捲門、各房間冷氣都不會送到瀏覽器。
格局 (正面 = +z = 車庫鐵捲門那一面；樓梯在左側中段)：
  1F 車庫 → 客廳 → 樓梯 / 廚房餐廳 → 洗衣間、後面廁所
  2F 前陽台 (熱水器) → 主臥室 (含廁所) → 走道 → 書房
  3F 3 樓房間 → 浴室 / 走道 → 視聽室
  4F 露台 → 太陽能機房 → 走道 → 健身室；屋頂太陽能板
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
const FLOOR_NAME = { 1: '車庫 · 客廳 · 廚房', 2: '主臥 · 書房 · 陽台', 3: '房間 · 視聽室', 4: '機房 · 健身室 · 露台' };

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
controls.target.set(0, 9, 0);
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

// ---------- 地面 ----------
const ground = new THREE.Mesh(new THREE.CircleGeometry(30, 64), std(NIGHT ? '#1B2B24' : '#3F6B4E', { roughness: 1 }));
ground.rotation.x = -Math.PI / 2; ground.position.y = -0.12; scene.add(ground);
bx(scene, 5.8, 0.03, 6, std('#6B7280', { roughness: 1 }), 0, -0.1, 11);          // 車道
for (const [x, z, s] of [[-6.5, 6, 1.2], [6.8, 3, 1], [-6.8, -6, 1.4], [7, -7, 1.1]]) {
  cyl(scene, 0.12, 0.15, 1.2 * s, M.wood, x, 0.5 * s, z, 8);
  const t = new THREE.Mesh(new THREE.IcosahedronGeometry(0.9 * s, 0), std('#15803D', { flatShading: true }));
  t.position.set(x, 1.6 * s, z); scene.add(t);
}

// ---------- 樓層 ----------
const floors = {};
for (let f = 1; f <= 4; f++) {
  const g = new THREE.Group(); g.position.y = (f - 1) * (FH + GAP); scene.add(g);
  floors[f] = { g, f, rooms: {}, roomLabels: [], y: g.position.y };
}
const roof = new THREE.Group(); scene.add(roof);

function slab(g, hole) {
  const y = -SLAB / 2;
  if (!hole) { bx(g, 2 * HW + 0.2, SLAB, 16.2, M.slab, 0, y, 0); return; }
  bx(g, 4.7, SLAB, 16.2, M.slab, 0.75, y, 0);                       // x -1.6 ~ 3.1
  bx(g, 1.5, SLAB, 9.5, M.slab, -2.35, y, 3.35);                     // 樓梯洞前面
  bx(g, 1.5, SLAB, 3.5, M.slab, -2.35, y, -6.35);                    // 樓梯洞後面
}
function wall(g, x0, z0, x1, z1, mat = M.wall, h = CEIL) {
  const len = Math.hypot(x1 - x0, z1 - z0);
  const m = new THREE.Mesh(new THREE.BoxGeometry(len, h, 0.08), mat);
  m.position.set((x0 + x1) / 2, h / 2, (z0 + z1) / 2); m.rotation.y = -Math.atan2(z1 - z0, x1 - x0);
  m.renderOrder = 2; g.add(m);
  m.add(new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry), M.edge));
  return m;
}
function rail(g, x0, z0, x1, z1) { wall(g, x0, z0, x1, z1, M.glass, 1.05); }
function room(f, name, x0, x1, z0, z1, color, { lift = 0, showLabel = true } = {}) {
  const fl = floors[f];
  const m = new THREE.Mesh(new THREE.BoxGeometry(x1 - x0 - 0.04, 0.03, z1 - z0 - 0.04),
    std(color, { roughness: 0.85, emissive: '#FBBF24', emissiveIntensity: 0 }));
  m.position.set((x0 + x1) / 2, 0.015 + lift, (z0 + z1) / 2); fl.g.add(m);
  fl.rooms[name] = { mesh: m, on: 0 };
  if (showLabel) fl.roomLabels.push(label(fl.g, 'room', name, (x0 + x1) / 2, 0.15, (z0 + z1) / 2));
  return m;
}
function stairs(g) {
  const n = 13, rise = FH / n, run = 3.2 / n;
  for (let i = 0; i < n; i++) bx(g, 1.3, rise, run, M.woodL, -2.3, (i + 0.5) * rise, -1.4 - (i + 0.5) * run);
  bx(g, 0.04, 1, 3.3, M.metal, -1.62, FH / 2 + 0.5, -3.0).rotation.x = Math.atan2(FH, 3.2);
}
function sides(g, zf) {
  wall(g, -HW, -8, -HW, zf); wall(g, HW, -8, HW, zf); wall(g, -HW, -8, HW, -8);
  for (const [x, z] of [[-HW, -8], [HW, -8], [-HW, zf], [HW, zf]]) bx(g, 0.22, CEIL, 0.22, M.pillar, x, CEIL / 2, z);
}

// ===== 1F =====
{
  const f = 1, g = floors[f].g;
  slab(g, false); sides(g, 8);
  room(f, '車庫', -3, 3, 3, 8, '#4B5563'); room(f, '客廳', -3, 3, -1.4, 3, '#8B6B4A');
  room(f, '樓梯', -3, -1.6, -4.6, -1.4, '#57534E', { showLabel: false }); room(f, '廁所', -3, -1.6, -5.6, -4.6, '#5B7C99');
  room(f, '廚房 · 餐廳', -1.6, 3, -5.6, -1.4, '#7C6F64'); room(f, '洗衣間', -3, 0.6, -8, -5.6, '#5B6B7A');
  room(f, '後面廁所', 0.6, 3, -8, -5.6, '#5B7C99');
  wall(g, -3, 3, 0.4, 3); wall(g, 1.4, 3, 3, 3);
  wall(g, -3, -4.6, -1.6, -4.6); wall(g, -1.6, -4.6, -1.6, -5.6);
  wall(g, -3, -5.6, -1.9, -5.6); wall(g, -1.0, -5.6, 3, -5.6); wall(g, 0.6, -5.6, 0.6, -8);
  stairs(g);
  // 車子
  const car = new THREE.Group(); car.position.set(0, 0, 5.4); g.add(car);
  rbx(car, 1.8, 0.7, 4.2, M.car, 0, 0.6, 0, 0.18); rbx(car, 1.6, 0.55, 2.2, std('#BFDBFE', { metalness: 0.3, roughness: 0.15 }), 0, 1.15, -0.2, 0.15);
  for (const [x, z] of [[-0.9, 1.3], [0.9, 1.3], [-0.9, -1.3], [0.9, -1.3]]) {
    const w = cyl(car, 0.34, 0.34, 0.24, M.tyre, x, 0.34, z, 16); w.rotation.z = Math.PI / 2;
  }
  // 客廳：沙發、茶几、電視、小朋友書桌
  rbx(g, 0.9, 0.45, 2.4, M.fabric, -2.4, 0.25, 0.9); rbx(g, 0.25, 0.75, 2.4, M.fabric2, -2.82, 0.45, 0.9);
  rbx(g, 0.7, 0.35, 1.1, M.woodL, -1.2, 0.2, 0.9);
  bx(g, 0.06, 0.8, 1.6, M.black, 2.9, 1.4, 1.2); bx(g, 0.45, 0.4, 2.0, M.wood, 2.7, 0.2, 1.2);
  bx(g, 2.2, 0.05, 0.6, M.woodL, 1.5, 0.75, -0.95); for (const x of [0.5, 2.5]) bx(g, 0.05, 0.75, 0.55, M.woodL, x, 0.375, -0.95);
  for (const x of [1.0, 2.0]) rbx(g, 0.4, 0.45, 0.4, M.fabric2, x, 0.23, -0.4);
  // 廚房餐廳
  bx(g, 0.65, 0.9, 3.4, M.white, 2.65, 0.45, -3.6); bx(g, 0.66, 0.04, 3.4, std('#1F2937'), 2.65, 0.92, -3.6);
  bx(g, 0.4, 0.7, 3.4, M.white, 2.78, 2.1, -3.6);
  bx(g, 1.0, 0.05, 1.6, M.wood, 0.3, 0.75, -3.4); for (const z of [-3.9, -2.9]) for (const x of [-0.45, 1.05]) rbx(g, 0.4, 0.45, 0.4, M.fabric, x, 0.23, z);
  bx(g, 0.7, 1.9, 0.7, std('#CBD5E1', { metalness: 0.4 }), 2.6, 0.95, -5.1);    // 冰箱
  // 廁所、洗衣間 (洗衣機、洗衣台、冷凍櫃、家電櫃)
  rbx(g, 0.4, 0.4, 0.55, M.white, -2.6, 0.2, -5.25); rbx(g, 0.4, 0.4, 0.55, M.white, 2.6, 0.2, -7.5);
  rbx(g, 0.6, 0.85, 0.6, M.white, -2.6, 0.43, -7.6); cyl(g, 0.2, 0.2, 0.02, M.black, -2.6, 0.62, -7.29).rotation.x = Math.PI / 2;
  bx(g, 0.8, 0.85, 0.55, M.white, -1.7, 0.43, -7.65); rbx(g, 0.7, 0.9, 0.65, std('#E5E7EB'), -0.8, 0.45, -7.6);
  bx(g, 0.9, 1.9, 0.5, M.wood, 0.1, 0.95, -7.7);
}
// ===== 2F =====
{
  const f = 2, g = floors[f].g;
  slab(g, true); sides(g, 6.8);
  room(f, '前陽台', -3, 3, 6.8, 8, '#6B7280'); room(f, '主臥室', -3, 3, 0, 6.8, '#8B6B4A');
  room(f, '主臥廁所', 1.2, 3, 0, 2.4, '#5B7C99', { lift: 0.006 }); room(f, '走道', -1.6, 3, -4.6, 0, '#57534E');
  room(f, '書房', -3, 3, -8, -4.6, '#7A6A55');
  wall(g, -3, 6.8, 3, 6.8, M.glass);
  wall(g, 1.2, 2.4, 3, 2.4); wall(g, 1.2, 1.2, 1.2, 2.4);
  wall(g, -1.6, 0, 0.2, 0); wall(g, 1.2, 0, 3, 0);
  wall(g, -3, -4.6, -0.2, -4.6); wall(g, 0.8, -4.6, 3, -4.6);
  rail(g, -3, 8, 3, 8); rail(g, -3, 6.8, -3, 8); rail(g, 3, 6.8, 3, 8);
  stairs(g);
  rbx(g, 2.0, 0.5, 1.9, M.bed, -2.0, 0.3, 3.9); rbx(g, 0.15, 0.9, 1.9, M.wood, -2.95, 0.5, 3.9);
  rbx(g, 1.2, 0.1, 1.8, std('#93C5FD'), -1.6, 0.6, 3.9);
  for (const z of [2.6, 5.2]) bx(g, 0.45, 0.5, 0.4, M.wood, -2.7, 0.25, z);
  bx(g, 0.6, 2.0, 1.8, M.wood, 2.65, 1.0, 4.6);                                 // 衣櫃
  rbx(g, 0.4, 0.4, 0.55, M.white, 2.6, 0.2, 0.4);
  bx(g, 1.8, 0.05, 0.6, M.woodL, 1.9, 0.75, -7.6); rbx(g, 0.45, 0.5, 0.45, M.fabric2, 1.9, 0.25, -7.0);
  bx(g, 1.6, 2.0, 0.35, M.wood, -2.0, 1.0, -7.8);                               // 書櫃
  for (const [x, z] of [[-2.6, 7.6], [-1.9, 7.6]]) { cyl(g, 0.18, 0.14, 0.3, M.pot, x, 0.15, z); cyl(g, 0.22, 0.05, 0.5, M.plant, x, 0.55, z, 7); }
}
// ===== 3F =====
{
  const f = 3, g = floors[f].g;
  slab(g, true); sides(g, 8);
  room(f, '3 樓房間', -3, 3, 1.2, 8, '#8B6B4A'); room(f, '浴室', 0.6, 3, -2.6, 1.2, '#5B7C99');
  room(f, '走道', -1.6, 0.6, -4.6, 1.2, '#57534E', { showLabel: false }); room(f, '走道', 0.6, 3, -4.6, -2.6, '#57534E', { showLabel: false });
  room(f, '視聽室', -3, 3, -8, -4.6, '#4C4F6B');
  wall(g, -3, 8, 3, 8, M.glass);
  wall(g, -3, 1.2, -0.6, 1.2); wall(g, 0.4, 1.2, 3, 1.2);
  wall(g, 0.6, -2.6, 0.6, -1.6); wall(g, 0.6, -0.8, 0.6, 1.2); wall(g, 0.6, -2.6, 3, -2.6);
  wall(g, -3, -4.6, -0.2, -4.6); wall(g, 0.8, -4.6, 3, -4.6);
  stairs(g);
  rbx(g, 1.6, 0.5, 2.0, M.bed, -2.1, 0.3, 5.0); rbx(g, 0.15, 0.9, 1.6, M.wood, -2.1, 0.5, 6.05).rotation.y = Math.PI / 2;
  bx(g, 0.6, 2.0, 1.6, M.wood, 2.65, 1.0, 6.8);
  rbx(g, 0.75, 0.5, 1.6, M.white, 2.5, 0.25, -0.5, 0.15); rbx(g, 0.4, 0.4, 0.55, M.white, 2.6, 0.2, -2.1);
  rbx(g, 2.6, 0.45, 0.9, std('#7F1D1D'), 0, 0.25, -5.4); rbx(g, 2.6, 0.7, 0.2, std('#7F1D1D'), 0, 0.5, -4.95);
  bx(g, 2.8, 1.5, 0.06, M.black, 0, 1.5, -7.9);
  sprite(g, GLOW_B, 2.4, 0, 1.5, -7.75, NIGHT ? 0.35 : 0.15);
}
// ===== 4F =====
{
  const f = 4, g = floors[f].g;
  slab(g, true); sides(g, 3);
  room(f, '露台', -3, 3, 3, 8, '#6B7280'); room(f, '太陽能機房', -3, 3, -1.4, 3, '#3F4C5A');
  room(f, '走道', -1.6, 3, -4.6, -1.4, '#57534E', { showLabel: false }); room(f, '健身室', -3, 3, -8, -4.6, '#4C5D73');
  wall(g, -3, 3, -0.2, 3, M.glass); wall(g, 0.8, 3, 3, 3, M.glass);
  wall(g, -1.6, -1.4, 0.4, -1.4); wall(g, 1.4, -1.4, 3, -1.4);
  wall(g, -3, -4.6, -0.2, -4.6); wall(g, 0.8, -4.6, 3, -4.6);
  rail(g, -3, 8, 3, 8); rail(g, -3, 3, -3, 8); rail(g, 3, 3, 3, 8);
  rbx(g, 0.8, 0.9, 1.9, M.dark, 1.8, 0.45, -6.6); bx(g, 0.7, 0.05, 1.2, M.black, 1.8, 0.95, -6.8);
  bx(g, 1.2, 0.8, 0.4, M.metal, -2.0, 0.4, -7.7);
  for (const [x, z] of [[-2.4, 7.4], [2.4, 7.4], [2.4, 4.0]]) { cyl(g, 0.25, 0.2, 0.4, M.pot, x, 0.2, z); cyl(g, 0.3, 0.06, 0.8, M.plant, x, 0.8, z, 7); }
  bx(g, 1.8, 0.05, 0.8, M.woodL, -1.2, 0.75, 5.5);
}
// ===== 屋頂：人字屋頂 + 太陽能板 (前後兩坡) =====
const panelMats = [];
{
  bx(roof, 2 * HW + 0.2, SLAB, 11.2, M.slab, 0, -SLAB / 2, -2.5);
  const half = 5.6, rise = 1.3, L = Math.hypot(half, rise), a = Math.atan2(rise, half);
  const c = document.createElement('canvas'); c.width = 64; c.height = 128; const x = c.getContext('2d');
  x.fillStyle = '#1E3A8A'; x.fillRect(0, 0, 64, 128); x.strokeStyle = '#93C5FD'; x.lineWidth = 2;
  for (let i = 0; i <= 64; i += 16) { x.beginPath(); x.moveTo(i, 0); x.lineTo(i, 128); x.stroke(); }
  for (let j = 0; j <= 128; j += 21) { x.beginPath(); x.moveTo(0, j); x.lineTo(64, j); x.stroke(); }
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  for (const side of [1, -1]) {
    const s = new THREE.Group(); s.position.set(0, rise / 2 + 0.3, -2.5 + side * half / 2); s.rotation.x = side * a; roof.add(s);
    bx(s, 2 * HW + 0.5, 0.08, L, std('#475569'), 0, 0, 0);
    const pm = std('#FFFFFF', { map: tex, metalness: 0.5, roughness: 0.25, emissive: '#38BDF8', emissiveIntensity: 0 });
    panelMats.push(pm);
    for (let i = 0; i < 6; i++) for (const zz of [-0.9, 0.9]) bx(s, 0.95, 0.05, 1.7, pm, -2.5 + i, 0.07, zz * side);
  }
  for (const sx of [-1, 1]) {
    const sh = new THREE.Shape(); sh.moveTo(-half, 0); sh.lineTo(half, 0); sh.lineTo(0, rise); sh.closePath();
    const m = new THREE.Mesh(new THREE.ShapeGeometry(sh), std('#94A3B8', { side: THREE.DoubleSide }));
    m.position.set(sx * (HW + 0.02), 0.3, -2.5); m.rotation.y = Math.PI / 2; roof.add(m);
  }
  for (const [x, z] of [[-HW, -8], [HW, -8], [-HW, 3], [HW, 3]]) bx(roof, 0.22, 0.3, 0.22, M.pillar, x, 0.15, z);
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
  const on = s && s.st === 'on';
  const fix = cyl(g, 0.2, 0.22, 0.05, std(dimmed(s) ? '#64748B' : '#F1F5F9', { emissive: '#FFD58A', emissiveIntensity: on ? 2.4 : 0 }), x, y, z, 24);
  if (on) {
    const c = new THREE.Mesh(new THREE.ConeGeometry(r, y, 28, 1, true), coneMat()); c.position.set(x, y / 2, z); g.add(c);
    sprite(g, GLOW, 1.1, x, y - 0.1, z, 0.9);
    roomOn(f, roomName);
  }
  return reg(id, f, new THREE.Vector3(x, y + 0.25, z), [fix]);
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
  const arm = cyl(grp, 0.012, 0.012, 0.42, M.white, 0, 0.21, 0, 6);
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
  const tank = cyl(g, 0.32, 0.32, 1.3, std(dimmed(s) ? '#64748B' : '#F1F5F9', { emissive: '#F97316', emissiveIntensity: on ? 0.5 : 0 }), x, 0.85, z, 24);
  cyl(g, 0.33, 0.33, 0.08, M.metal, x, 1.52, z, 24); bx(g, 0.5, 0.2, 0.5, M.gray, x, 0.1, z);
  let steam = null;
  if (on) {
    sprite(g, GLOW_O, 1.6, x, 0.9, z, 0.6);
    steam = [];
    for (let i = 0; i < 6; i++) steam.push(sprite(g, glowTex('rgba(255,255,255,1)'), 0.3, x, 1.6, z, 0.4));
  }
  const d = reg(id, f, new THREE.Vector3(x, 2.0, z), [tank]);
  if (on) d.tick = (t) => {
    tank.material.emissiveIntensity = 0.35 + 0.25 * Math.sin(t * 3);
    steam.forEach((sp, i) => { const k = (t * 0.35 + i / steam.length) % 1;
      sp.position.set(x + Math.sin(t + i) * 0.08, 1.6 + k * 0.9, z); sp.material.opacity = 0.45 * (1 - k); sp.scale.setScalar(0.25 + k * 0.4); });
  };
  return d;
}
function garage(id, f) {
  const g = floors[f].g, s = D.slots[id];
  const open = s && s.st === 'on' ? Math.max(0.15, (s.pos ?? 100) / 100) : (s && typeof s.pos === 'number' ? s.pos / 100 : 0);
  const c = document.createElement('canvas'); c.width = 16; c.height = 128; const x = c.getContext('2d');
  x.fillStyle = dimmed(s) ? '#64748B' : '#CBD5E1'; x.fillRect(0, 0, 16, 128); x.fillStyle = 'rgba(15,23,42,.35)';
  for (let j = 0; j < 128; j += 8) x.fillRect(0, j, 16, 1.5);
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace;
  const H = CEIL - 0.3, Wd = 5.6;
  const door = new THREE.Mesh(new THREE.BoxGeometry(Wd, H, 0.06), std('#FFFFFF', { map: tex, metalness: 0.4, roughness: 0.5 }));
  door.position.set(0, H / 2, 8); g.add(door);
  bx(g, Wd + 0.2, 0.3, 0.35, M.gray, 0, H + 0.15, 8);
  const d = reg(id, f, new THREE.Vector3(0, H + 0.6, 8.3), [door]);
  let cur = 0;
  d.tick = (t, dt) => {
    cur += (open - cur) * Math.min(1, dt * 1.5);
    const k = Math.max(0.02, 1 - cur);
    door.scale.y = k; door.position.y = H - (H * k) / 2;
  };
  return d;
}
function curtain(id, f, x, z0, z1) {
  const g = floors[f].g, s = D.slots[id];
  const o = s && typeof s.pos === 'number' ? s.pos / 100 : s && s.st === 'on' ? 1 : 0;
  const len = z1 - z0, h = 2.3;
  bx(g, 0.03, h, len, M.glass, x - 0.02, 0.2 + h / 2, (z0 + z1) / 2);
  const mat = std(dimmed(s) ? '#64748B' : '#E7D8C3', { side: THREE.DoubleSide, roughness: 1 });
  const w = (len / 2) * (1 - 0.78 * o);
  const a = bx(g, 0.05, h, w, mat, x + 0.06, 0.2 + h / 2, z0 + w / 2), b = bx(g, 0.05, h, w, mat, x + 0.06, 0.2 + h / 2, z1 - w / 2);
  return reg(id, f, new THREE.Vector3(x + 0.2, h + 0.5, (z0 + z1) / 2), [a, b]);
}
function dish(id, f, x, z) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const box = bx(g, 0.6, 0.8, 0.58, std(dimmed(s) ? '#64748B' : '#E5E7EB', { metalness: 0.5, roughness: 0.3 }), x, 0.4, z);
  const led = bx(g, 0.02, 0.05, 0.12, basic(on ? '#22D3EE' : '#334155'), x - 0.31, 0.72, z);
  const d = reg(id, f, new THREE.Vector3(x - 0.3, 1.25, z), [box]);
  if (on) d.tick = t => { led.visible = Math.sin(t * 4) > -0.3; };
  return d;
}
function speaker(id, f, x, z) {
  const g = floors[f].g, s = D.slots[id], on = s && s.st === 'on';
  const sp = cyl(g, 0.07, 0.08, 0.2, std(dimmed(s) ? '#64748B' : '#E2E8F0'), x, 0.6, z, 20);
  let ring = null;
  if (on) { ring = new THREE.Mesh(new THREE.TorusGeometry(0.12, 0.008, 6, 32), basic('#A78BFA', { transparent: true }));
    ring.rotation.x = Math.PI / 2; ring.position.set(x, 0.62, z); g.add(ring); }
  const d = reg(id, f, new THREE.Vector3(x, 1.0, z), [sp]);
  if (ring) d.tick = t => { const k = (t * 0.8) % 1; ring.scale.setScalar(1 + k * 3); ring.material.opacity = 1 - k; };
  return d;
}

// 1F
garage('garage_door', 1);
ac('f1_living_ac', 1, -1.4, 2.88, Math.PI);
light('f1_ceiling', 1, 0, 0.8, '客廳', { r: 1.8 });
light('f1_flood', 1, 1.9, 2.3, '客廳', { r: 1.1 });
strip('f1_indirect', 1, -2.8, 2.8, -1.2, 2.8, '客廳');
curtain('f1_curtain', 1, -2.94, -0.2, 2.6);
lamp('f1_lamp_chen', 1, 1.0, -1.1); lamp('f1_lamp_hao', 1, 2.0, -1.1);
light('f1_kitchen', 1, 0.7, -3.5, '廚房 · 餐廳', { r: 1.6 });
dish('f1_dish', 1, 2.62, -4.2);
light('f1_wc', 1, -2.3, -5.1, '廁所', { r: 0.6 });
light('f1_laundry', 1, -1.2, -6.8, '洗衣間', { r: 1.2 });
light('f1_wc_back', 1, 1.8, -6.8, '後面廁所', { r: 0.9 });
light('f1_stair', 1, -2.3, -2.2, '樓梯', { r: 0.7 });
// 2F
heater('f2_heater', 2, 2.2, 7.4);
ac('f2_bed_ac', 2, 2.9, 2.9, -Math.PI / 2);
light('f2_main', 2, 0, 3.6, '主臥室', { r: 1.8 });
strip('f2_bed_indirect', 2, -2.8, 2.8, 2.6, 6.6, '主臥室');
nightLight('f2_night', 2, -2.7, 5.2, '主臥室');
speaker('f2_speaker', 2, -2.7, 2.6);
light('f2_wc', 2, 2.1, 1.2, '主臥廁所', { r: 0.8 });
light('f2_hall', 2, 0.7, -2.4, '走道', { r: 1.0 });
ac('f2_study_ac', 2, -1.0, -7.88, 0);
lamp('f2_lamp', 2, 2.5, -7.65);
// 3F
ac('f3_bed_ac', 3, 2.9, 4.4, -Math.PI / 2);
ac('f3_av_ac', 3, -2.88, -6.3, Math.PI / 2);
// 4F
ac('f4_solar_ac', 4, 2.88, 0.4, -Math.PI / 2);
ac('f4_gym_ac', 4, -2.88, -6.3, Math.PI / 2);

// 4F 機房：4 組電池 + 逆變器 (公開資訊)
const E = D.energy || {};
{
  const g = floors[4].g;
  const packs = (E.packs && E.packs.length ? E.packs : [E.soc, E.soc, E.soc, E.soc]).slice(0, 4);
  packs.forEach((soc, i) => {
    const z = 2.3 - i * 0.95;
    rbx(g, 0.6, 1.5, 0.75, std('#1F2937', { metalness: 0.3 }), -2.55, 0.75, z, 0.05);
    const k = typeof soc === 'number' ? Math.max(0.03, soc / 100) : 0;
    const col = soc >= 60 ? '#22C55E' : soc >= 30 ? '#EAB308' : '#EF4444';
    bx(g, 0.02, 1.2, 0.08, basic('#0F172A'), -2.24, 0.75, z);
    if (k) bx(g, 0.025, 1.2 * k, 0.08, basic(col), -2.235, 0.15 + 0.6 * k, z);
  });
  const pvOn = (E.pv || 0) > 50;
  bx(g, 0.22, 0.85, 0.6, M.white, 2.85, 1.5, 1.9);
  bx(g, 0.01, 0.18, 0.3, basic(pvOn ? '#34D399' : '#334155'), 2.735, 1.7, 1.9);
  if (pvOn) sprite(g, glowTex('rgba(110,231,183,1)'), 0.6, 2.7, 1.7, 1.9, 0.5);
  const parts = [];
  if (typeof E.pv === 'number') parts.push(`☀️ ${(E.pv / 1000).toFixed(1)} kW`);
  if (typeof E.soc === 'number') parts.push(`🔋 ${E.soc}%` + (E.packs && E.packs.length > 1 ? `（${E.packs.map(v => v ?? '—').join(' / ')}）` : ''));
  if (typeof E.load === 'number') parts.push(`🏠 ${(E.load / 1000).toFixed(1)} kW`);
  if (parts.length) floors[4].energy = label(g, 'tag on', parts.join(' · '), -2.2, 2.1, 0.9);
  panelMats.forEach(m => { m.emissiveIntensity = Math.min(0.55, (E.pv || 0) / 12000); });
}

// 房間亮燈 → 地板染暖色
for (const fl of Object.values(floors)) for (const r of Object.values(fl.rooms))
  if (r.on) r.mesh.material.emissiveIntensity = NIGHT ? 0.45 : 0.25;

// 樓層名稱
for (const fl of Object.values(floors))
  fl.flabel = label(fl.g, 'flabel', `${fl.f}F<small>${FLOOR_NAME[fl.f]}</small>`, -HW - 0.4, 1.4, 8.3);

// ---------- 上方摘要、樓層按鈕、開啟中清單 ----------
const onList = Object.values(devs).filter(d => d.on).sort((a, b) => a.f - b.f);
const sum = document.getElementById('sum');
const T = D.temps || {};
const tparts = [];
if (typeof T.out === 'number') tparts.push(`戶外 ${T.out.toFixed(1)}°C`);
if (typeof T.room === 'number') tparts.push(`儲能室 ${T.room.toFixed(1)}°C`);
if (D.authed) {
  let s = `<b>${onList.length}</b> 台開啟中` + (tparts.length ? ' · ' + tparts.join(' · ') : '');
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
let camGoal = null, tgtGoal = new THREE.Vector3(0, 9, 0);
function setFocus(f) {
  focus = f;
  fb.querySelectorAll('button').forEach(b => b.classList.toggle('sel', +b.dataset.f === f));
  tgtGoal = f ? new THREE.Vector3(0, (f - 1) * FH + 0.8, 0.5) : new THREE.Vector3(0, 9, 0);
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
    if (fl.energy) fl.energy.visible = !focus ? !NARROW : focus === 4;
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
