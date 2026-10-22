"""3D 房屋能源流向 (Three.js 即時渲染)。

光點沿管線流動表示電力方向，流速隨功率變化；可用滑鼠/手指拖曳旋轉視角。
"""
import json

_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
html, body { margin:0; padding:0; background:transparent; overflow:hidden;
  font-family:'Segoe UI',Roboto,'Noto Sans TC',sans-serif; }
#wrap { position:relative; width:100%; height:500px; border-radius:14px; overflow:hidden;
  border:1px solid #334155; background:__SKY__; }
#wrap canvas { display:block; cursor:grab; }
#wrap canvas:active { cursor:grabbing; }
.stars { position:absolute; inset:0; pointer-events:none; background-image:
  radial-gradient(1.2px 1.2px at 8% 12%, #fff, transparent), radial-gradient(1px 1px at 22% 30%, #cbd5e1, transparent),
  radial-gradient(1.4px 1.4px at 37% 8%, #fff, transparent), radial-gradient(1px 1px at 55% 22%, #e2e8f0, transparent),
  radial-gradient(1.2px 1.2px at 68% 6%, #fff, transparent), radial-gradient(1px 1px at 83% 18%, #cbd5e1, transparent),
  radial-gradient(1.3px 1.3px at 93% 9%, #fff, transparent), radial-gradient(1px 1px at 47% 35%, #94a3b8, transparent);
  opacity:__STARS__; }
.card { position:absolute; left:0; top:0; padding:8px 14px; border-radius:12px; pointer-events:none;
  background:rgba(15,23,42,0.62); border:1px solid rgba(148,163,184,0.28);
  backdrop-filter:blur(10px); -webkit-backdrop-filter:blur(10px);
  color:#F8FAFC; white-space:nowrap; box-shadow:0 6px 20px rgba(0,0,0,0.35); will-change:transform; }
.card .v { font-size:20px; font-weight:700; letter-spacing:0.3px; }
.card .v small { font-size:12px; font-weight:400; color:#CBD5E1; margin-left:2px; }
.card .t { font-size:12px; color:#94A3B8; margin-top:1px; }
.card .strings { display:flex; gap:10px; margin-top:5px; padding-top:5px; border-top:1px solid rgba(148,163,184,0.25); font-size:11px; color:#94A3B8; }
.card .strings i { display:inline-block; width:7px; height:7px; border-radius:2px; margin-right:4px; }
.card .strings b { color:#F1F5F9; font-weight:600; }
.card .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }
.hint { position:absolute; right:12px; bottom:10px; font-size:11px; color:rgba(226,232,240,0.55); pointer-events:none; }
.tag { position:absolute; left:0; top:0; padding:3px 8px; border-radius:8px; font-size:11px; pointer-events:none; white-space:nowrap;
  background:rgba(15,23,42,0.55); color:#94A3B8; border:1px solid rgba(148,163,184,0.2); }
.tag.on { color:#FDE68A; border-color:rgba(253,230,138,0.5); background:rgba(15,23,42,0.75); }
.mode { position:absolute; left:12px; bottom:10px; display:flex; gap:4px; background:rgba(15,23,42,0.6); padding:3px; border-radius:10px; }
.mode button { border:0; background:transparent; color:#CBD5E1; font-size:12px; padding:5px 10px; border-radius:7px; cursor:pointer; }
.mode button.sel { background:#334155; color:#F8FAFC; }
.fallback { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; color:#94A3B8; }
</style>
<script type="importmap">{"imports":{
  "three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body>
<div id="wrap"><div class="stars"></div><div class="hint">拖曳可旋轉視角</div></div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';

const D = __DATA__;
const NIGHT = D.night;
const wrap = document.getElementById('wrap');

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = NIGHT ? 1.0 : 1.05;
wrap.prepend(renderer.domElement);

const scene = new THREE.Scene();
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
const ENV = NIGHT ? 0.12 : 0.75;

const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 200);
// 依設備配置調整預設視角，讓設備正面朝向鏡頭
const CAM = { front: [25.0, 12.5, 19.5], door: [15.5, 10.5, 18.5], side: [20.5, 10.0, 10.0], yard: [17.5, 10.2, 15.5] }[D.layout || 'front'];
camera.position.set(...CAM);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(1.0, 3.4, -0.3);
controls.enableDamping = true;
controls.enablePan = false;
controls.minDistance = 18;
controls.maxDistance = 50;
controls.maxPolarAngle = 1.38;

// ---------- 材質 ----------
const std = (color, roughness = 0.8, metalness = 0, extra = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness, metalness, envMapIntensity: ENV, ...extra });

function canvasTex(w, h, draw, repeat) {
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  draw(c.getContext('2d'), w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  if (repeat) { t.wrapS = t.wrapT = THREE.RepeatWrapping; t.repeat.set(...repeat); }
  return t;
}

const panelTex = canvasTex(256, 416, (g, w, h) => {
  g.fillStyle = '#0B1733'; g.fillRect(0, 0, w, h);
  const cols = 6, rows = 10;
  for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
    const x = i * w / cols, y = j * h / rows;
    const grd = g.createLinearGradient(x, y, x + w / cols, y + h / rows);
    grd.addColorStop(0, '#16306A'); grd.addColorStop(1, '#0E2150');
    g.fillStyle = grd; g.fillRect(x + 2, y + 2, w / cols - 4, h / rows - 4);
  }
  g.strokeStyle = 'rgba(180,200,240,0.35)'; g.lineWidth = 1;
  for (let i = 1; i < 3; i++) { g.beginPath(); g.moveTo(i * w / 3, 0); g.lineTo(i * w / 3, h); g.stroke(); }
});
const woodTex = canvasTex(256, 256, (g, w, h) => {
  g.fillStyle = '#8A5A3B'; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 16; i++) {
    const x = i * w / 16;
    g.fillStyle = i % 2 ? '#94633F' : '#7E5234'; g.fillRect(x, 0, w / 16 - 2, h);
    g.fillStyle = 'rgba(40,20,10,0.55)'; g.fillRect(x + w / 16 - 2, 0, 2, h);
    for (let k = 0; k < 6; k++) { g.fillStyle = 'rgba(60,30,15,0.15)'; g.fillRect(x + Math.random() * 12, Math.random() * h, 1, 30 + Math.random() * 60); }
  }
}, [2, 1]);
// 側牆炭灰木條 (讓橘色等能量管線更醒目)
const slatTex = canvasTex(256, 256, (g, w, h) => {
  g.fillStyle = '#3A3F47'; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 16; i++) {
    const x = i * w / 16;
    g.fillStyle = i % 2 ? '#434952' : '#373C44'; g.fillRect(x, 0, w / 16 - 2, h);
    g.fillStyle = 'rgba(10,12,16,0.6)'; g.fillRect(x + w / 16 - 2, 0, 2, h);
    for (let k = 0; k < 6; k++) { g.fillStyle = 'rgba(255,255,255,0.04)'; g.fillRect(x + Math.random() * 12, Math.random() * h, 1, 30 + Math.random() * 60); }
  }
}, [2, 1]);
const roofTex = canvasTex(256, 64, (g, w, h) => {
  g.fillStyle = '#2E333B'; g.fillRect(0, 0, w, h);
  for (let i = 0; i < 16; i++) { g.fillStyle = 'rgba(255,255,255,0.06)'; g.fillRect(i * w / 16, 0, 2, h); }
}, [4, 1]);

const M = {
  wall: std('#EEEAE3', 0.92),
  wood: std('#FFFFFF', 0.75, 0, { map: woodTex }),
  slat: std('#FFFFFF', 0.7, 0.05, { map: slatTex }),
  roof: std('#FFFFFF', 0.5, 0.45, { map: roofTex }),
  trim: std('#22262D', 0.5, 0.4),
  frame: std('#1C2026', 0.4, 0.6),
  glass: NIGHT
    ? std('#0E1822', 0.06, 0.9, { emissive: new THREE.Color('#FFC873'), emissiveIntensity: 1.7, envMapIntensity: 0.2 })
    : std('#36597C', 0.04, 0.65, { emissive: new THREE.Color('#7FA9D1'), emissiveIntensity: 0.3, envMapIntensity: 1.6 }),
  panel: std('#FFFFFF', 0.2, 0.55, { map: panelTex, envMapIntensity: NIGHT ? 0.2 : 1.5 }),
  alu: std('#C9CED6', 0.35, 0.85),
  white: std('#F5F6F8', 0.35, 0.05),
  dark: std('#111827', 0.4, 0.2),
  concrete: std('#CFCBC4', 0.95),
  grass: std('#5E8C4A', 0.95),
  path: std('#B8B2A8', 0.9),
  trunk: std('#6B4A33', 0.9),
  leaf: std('#4E7F3D', 0.85, 0, { flatShading: true }),
  leaf2: std('#5F9447', 0.85, 0, { flatShading: true }),
  pole: std('#8D8A84', 0.85),
  track: std('#64748B', 0.5, 0.3),   // 無電流時的管線，夜間也看得見
};

function mesh(geo, mat, pos, rot, parent = scene, cast = true) {
  const m = new THREE.Mesh(geo, mat);
  if (pos) m.position.set(...pos);
  if (rot) m.rotation.set(...rot);
  m.castShadow = cast; m.receiveShadow = true;
  parent.add(m);
  return m;
}
const box = (w, h, d) => new THREE.BoxGeometry(w, h, d);

// ---------- 地台與庭園 ----------
const Y0 = 0.3;
mesh(new THREE.CylinderGeometry(12.6, 12.9, 0.3, 96), M.concrete, [0, 0.15, 0], null, scene, false);
mesh(new THREE.CylinderGeometry(12.3, 12.3, 0.04, 96), M.grass, [0, Y0 + 0.01, 0], null, scene, false);
mesh(box(3.2, 0.05, 5.0), M.path, [6.8, Y0 + 0.03, 8.4], [0, -0.35, 0], scene, false);
mesh(box(9.6, 0.06, 1.6), M.path, [0, Y0 + 0.04, 6.65], null, scene, false);

function tree(x, z, s = 1) {
  mesh(new THREE.CylinderGeometry(0.1 * s, 0.14 * s, 1.4 * s, 8), M.trunk, [x, Y0 + 0.7 * s, z]);
  mesh(new THREE.IcosahedronGeometry(1.0 * s, 1), M.leaf, [x, Y0 + 1.9 * s, z]);
  mesh(new THREE.IcosahedronGeometry(0.7 * s, 1), M.leaf2, [x + 0.4 * s, Y0 + 2.5 * s, z - 0.2 * s]);
}
tree(-7.6, 6.4, 1.1); tree(-7.2, -6.8, 1.5); tree(-1.6, -9.6, 1.1); tree(9.4, -6.4, 1.0); tree(-8.8, 0.8, 0.8);
for (const [x, z, s] of [[-3.6, 6.25, 0.45], [-2.4, 6.3, 0.38], [-1.0, 6.25, 0.42], [5.2, 5.0, 0.4]])
  mesh(new THREE.IcosahedronGeometry(s, 1), M.leaf2, [x, Y0 + s * 0.8, z]);

// ---------- 房屋主體 (兩層樓) ----------
const W = 8.4, FL = 3.1, H = FL * 2, DP = 11.5, RISE = 1.6, OVER = 0.45;  // 緩坡人字屋頂
const TOP = Y0 + H;
const F2 = Y0 + FL;  // 二樓樓板高度
mesh(box(W, H, DP), M.wall, [0, Y0 + H / 2, 0]);
mesh(box(W + 0.1, 0.25, DP + 0.1), M.trim, [0, Y0 + 0.12, 0]);
mesh(box(W + 0.12, 0.16, DP + 0.12), M.trim, [0, F2, 0]);  // 樓層飾帶

// 山牆
const gShape = new THREE.Shape();
gShape.moveTo(-DP / 2, 0); gShape.lineTo(DP / 2, 0); gShape.lineTo(0, RISE); gShape.closePath();
const gGeo = new THREE.ExtrudeGeometry(gShape, { depth: 0.3, bevelEnabled: false });
for (const sx of [1, -1]) mesh(gGeo, M.wall, [sx > 0 ? W / 2 - 0.3 : -W / 2, TOP, 0], [0, Math.PI / 2, 0]);

// 屋頂 (兩片斜板 + 屋脊)
const slope = Math.atan(RISE / (DP / 2));
const halfRun = DP / 2 + OVER;
const L = halfRun / Math.cos(slope);
const roofs = {};
for (const s of [1, -1]) {
  const g = new THREE.Group();
  g.position.set(0, TOP + RISE - (halfRun / 2) * Math.tan(slope) + 0.12, s * halfRun / 2);
  g.rotation.x = s * slope;
  scene.add(g);
  mesh(box(W + 0.9, 0.16, L), M.roof, [0, 0, 0], null, g);
  mesh(box(W + 0.95, 0.22, 0.1), M.trim, [0, -0.02, s * L / 2], null, g);
  roofs[s] = g;
}
mesh(box(W + 0.95, 0.14, 0.32), M.trim, [0, TOP + RISE + 0.22, 0]);

// 太陽能板：人字屋頂兩坡各 12 片 (沿屋脊 4 片 [2+2，中間走道] x 往屋簷 3 片)。正面 (+z) 朝東。
// 由西到東共 6 列，每 2 列一組：PV1 = 西坡下方 2 列；PV2 = 緊鄰屋脊的兩列 (東、西坡各一)；PV3 = 東坡下方 2 列
const PW = 1.12, PL = 1.9, GAP = 0.05, ALONG = 4, AISLE = 0.45;  // 直式長板，長邊順坡
const STRING_COLORS = ['#3B82F6', '#14B8A6', '#F59E0B'];  // PV1 藍、PV2 青綠、PV3 金黃
// 沿屋脊 4 排分成 2 + 2，中間留支架走道
const xs = [-1.5, -0.5, 0.5, 1.5].map(i => i * (PW + GAP) + Math.sign(i) * AISLE / 2);
const ridgeZ = -L / 2 + 0.2;  // 屋面局部座標：靠屋脊端
// 兩坡的屋面局部 z 方向相反：東坡 (roofs[1]) 屋脊在 -z，西坡 (roofs[-1]) 屋脊在 +z
const toward = (roof, z) => (roof === roofs[-1] ? -z : z);
function panelAt(roof, x, k) {  // k = 0 緊鄰屋脊, 2 最靠屋簷
  const z = toward(roof, ridgeZ + PL / 2 + k * (PL + GAP));
  mesh(box(PW + 0.03, 0.05, PL + 0.03), M.alu, [x, 0.11, z], null, roof);
  const pm = mesh(new THREE.PlaneGeometry(PW, PL), M.panel, [x, 0.14, z], [-Math.PI / 2, 0, 0], roof);
  pm.castShadow = false;
}
for (const s of [1, -1]) for (const x of xs) for (let k = 0; k < 3; k++) panelAt(roofs[s], x, k);
// 各組以彩色外框標示
function outline(roof, k0, k1, color) {
  const xa = xs[0] - PW / 2 - 0.05, xb = xs[ALONG - 1] + PW / 2 + 0.05;
  const za = toward(roof, ridgeZ + k0 * (PL + GAP) - 0.05), zb = toward(roof, ridgeZ + (k1 + 1) * (PL + GAP) - GAP + 0.05);
  const m = std(color, 0.4, 0, { emissive: new THREE.Color(color), emissiveIntensity: NIGHT ? 0.5 : 0.9 });
  const t = 0.035;
  mesh(box(xb - xa, 0.02, t), m, [(xa + xb) / 2, 0.165, za], null, roof, false);
  mesh(box(xb - xa, 0.02, t), m, [(xa + xb) / 2, 0.165, zb], null, roof, false);
  mesh(box(t, 0.02, Math.abs(zb - za)), m, [xa, 0.165, (za + zb) / 2], null, roof, false);
  mesh(box(t, 0.02, Math.abs(zb - za)), m, [xb, 0.165, (za + zb) / 2], null, roof, false);
}
outline(roofs[1], 1, 2, STRING_COLORS[2]);   // PV3 東坡
outline(roofs[-1], 1, 2, STRING_COLORS[0]);  // PV1 西坡
outline(roofs[1], 0, 0, STRING_COLORS[1]);   // PV2 東半
outline(roofs[-1], 0, 0, STRING_COLORS[1]);  // PV2 西半

// 地面方位指標 (正面 +z 朝東 → 北為 +x)
const compassTex = canvasTex(256, 256, (g, w, h) => {
  g.clearRect(0, 0, w, h);
  g.fillStyle = 'rgba(15,23,42,0.75)'; g.beginPath(); g.arc(w / 2, h / 2, 118, 0, Math.PI * 2); g.fill();
  g.strokeStyle = 'rgba(226,232,240,0.6)'; g.lineWidth = 3; g.stroke();
  g.fillStyle = '#EF4444'; g.beginPath(); g.moveTo(w - 40, h / 2); g.lineTo(w / 2, h / 2 - 16); g.lineTo(w / 2, h / 2 + 16); g.fill();
  g.fillStyle = '#CBD5E1'; g.beginPath(); g.moveTo(40, h / 2); g.lineTo(w / 2, h / 2 - 16); g.lineTo(w / 2, h / 2 + 16); g.fill();
  g.font = 'bold 34px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillStyle = '#F8FAFC';
  g.fillText('北', w - 22, h / 2 - 40); g.fillText('東', w / 2, h - 30); g.fillText('南', 22, h / 2 - 40); g.fillText('西', w / 2, 30);
});
mesh(new THREE.PlaneGeometry(2.0, 2.0), new THREE.MeshBasicMaterial({ map: compassTex, transparent: true }),
     [-6.6, Y0 + 0.06, 8.6], [-Math.PI / 2, 0, 0], scene, false);

// 窗戶
function windowAt(w, h, pos, rotY = 0) {
  const g = new THREE.Group(); g.position.set(...pos); g.rotation.y = rotY; scene.add(g);
  mesh(new THREE.PlaneGeometry(w, h), M.glass, [0, 0, 0.02], null, g, false);
  const t = 0.07;
  mesh(box(w + t * 2, t, 0.1), M.frame, [0, h / 2 + t / 2, 0.04], null, g);
  mesh(box(w + t * 2, t, 0.1), M.frame, [0, -h / 2 - t / 2, 0.04], null, g);
  mesh(box(t, h, 0.1), M.frame, [-w / 2 - t / 2, 0, 0.04], null, g);
  mesh(box(t, h, 0.1), M.frame, [w / 2 + t / 2, 0, 0.04], null, g);
  if (w > 1.6) mesh(box(0.05, h, 0.08), M.frame, [0, 0, 0.04], null, g);
  return g;
}
// 前牆 (+z)
windowAt(1.5, 1.7, [-2.6, Y0 + 1.75, DP / 2]);
windowAt(1.5, 1.7, [-0.8, Y0 + 1.75, DP / 2]);
mesh(box(1.1, 2.3, 0.08), M.wood, [0.7, Y0 + 1.15, DP / 2 + 0.03]);
mesh(new THREE.SphereGeometry(0.04, 12, 8), M.alu, [1.1, Y0 + 1.15, DP / 2 + 0.09]);
// 側面 (+x) 木紋外牆 + 落地窗 + 山牆三角窗
mesh(box(0.08, H, DP / 2 + 0.6), M.slat, [W / 2 + 0.04, Y0 + H / 2, -DP / 4 + 0.3]);
windowAt(2.6, 2.6, [W / 2 + 0.01, Y0 + 1.4, 3.0], Math.PI / 2);
windowAt(2.0, 1.4, [W / 2 + 0.01, F2 + 1.5, 3.0], Math.PI / 2);
// 二樓正面：玻璃欄杆陽台 + 落地窗，右側窗
windowAt(2.6, 2.3, [-1.6, F2 + 1.3, DP / 2]);
windowAt(1.4, 1.4, [2.3, F2 + 1.55, DP / 2]);
const railGlass = std('#BFD9EE', 0.05, 0.1, { transparent: true, opacity: NIGHT ? 0.18 : 0.35, envMapIntensity: 1.2 });
mesh(box(3.6, 0.18, 1.2), M.white, [-1.6, F2, DP / 2 + 0.6]);
mesh(box(3.6, 1.0, 0.03), railGlass, [-1.6, F2 + 0.6, DP / 2 + 1.18], null, scene, false);
for (const sx of [-1, 1]) mesh(box(0.03, 1.0, 1.18), railGlass, [-1.6 + sx * 1.785, F2 + 0.6, DP / 2 + 0.6], null, scene, false);
mesh(box(3.64, 0.05, 0.07), M.trim, [-1.6, F2 + 1.12, DP / 2 + 1.18]);
const triShape = new THREE.Shape();
triShape.moveTo(-1.8, 0); triShape.lineTo(1.8, 0); triShape.lineTo(0, 0.7); triShape.closePath();
mesh(new THREE.ShapeGeometry(triShape), M.glass, [W / 2 + 0.02, TOP + 0.15, 0], [0, Math.PI / 2, 0], scene, false);
// 後側窗
for (const z of [-3, 0, 3]) { windowAt(1.4, 1.5, [-W / 2 - 0.01, Y0 + 1.8, z], -Math.PI / 2); windowAt(1.4, 1.5, [-W / 2 - 0.01, F2 + 1.5, z], -Math.PI / 2); }

// ---------- 設備配置 ----------
// layout: front=設備在正面 / side=集中在右側牆 / yard=側院獨立設備區 (配電箱在牆上，以地下管線連接)
const LAYOUT = D.layout || 'front';
const V = (x, y, z) => new THREE.Vector3(x, y, z);
function frame(pos, rotY) {
  const g = new THREE.Group(); g.position.set(...pos); g.rotation.y = rotY;
  scene.add(g); g.updateMatrixWorld(true); return g;
}
const at = (g, x, y, z) => g.localToWorld(V(x, y, z));
const glowMat = (c, i) => std(c, 0.3, 0, { emissive: new THREE.Color(c), emissiveIntensity: i });

// 各設備以「牆面座標」建立：x 沿牆、y 向上、z 朝外
function addPanel(g, x, y) {  // 配電盤 (淺色、約 70 x 95 cm，夜間也看得見)
  mesh(new RoundedBoxGeometry(0.7, 0.95, 0.18, 3, 0.04), std('#E5E7EB', 0.4, 0.15), [x, y, 0.1], null, g);
  mesh(box(0.6, 0.01, 0.01), std('#9CA3AF', 0.6), [x, y + 0.2, 0.195], null, g, false);
  mesh(box(0.01, 0.6, 0.01), std('#9CA3AF', 0.6), [x + 0.2, y - 0.1, 0.195], null, g, false);
  mesh(new THREE.SphereGeometry(0.028, 10, 8), glowMat('#22C55E', 2.5), [x - 0.22, y + 0.34, 0.2], null, g, false);
  mesh(new THREE.SphereGeometry(0.028, 10, 8), glowMat('#F97316', D.load > 0.02 ? 2.5 : 0.2), [x - 0.12, y + 0.34, 0.2], null, g, false);
}
// 逆變器：仿 Growatt SPH 直立機型 (白色機身、GROWATT 標誌、黑色橢圓面板 + 藍色 LCD、下方檢修蓋板)
const INV_W = 0.62, INV_H = 1.05, INV_D = 0.24;
const invFaceTex = canvasTex(372, 630, (g, w, h) => {
  g.clearRect(0, 0, w, h);
  g.font = 'bold 38px Arial, sans-serif'; g.textBaseline = 'alphabetic';
  g.fillStyle = '#6DBE45'; g.fillText('G', 34, 92);
  g.fillStyle = '#4B5563'; g.fillText('ROWATT', 34 + g.measureText('G').width, 92);
  const cx = w / 2, cy = h * 0.44, rw = 96, rh = 44;  // 橢圓面板
  g.fillStyle = '#0B0F14'; g.strokeStyle = '#9CA3AF'; g.lineWidth = 3;
  g.beginPath(); g.roundRect(cx - rw, cy - rh, rw * 2, rh * 2, rh); g.fill(); g.stroke();
  g.strokeStyle = '#C7CCD3'; g.lineWidth = 2;  // 檢修蓋板分隔線
  g.beginPath(); g.moveTo(0, h * 0.62); g.lineTo(w, h * 0.62); g.stroke();
  g.fillStyle = '#F9FAFB'; g.strokeStyle = '#9CA3AF'; g.lineWidth = 2;
  for (const [sx, sy] of [[0.24, 0.645], [0.76, 0.645], [0.04, 0.72], [0.96, 0.72], [0.04, 0.9], [0.96, 0.9], [0.24, 0.975], [0.76, 0.975]]) {
    g.beginPath(); g.arc(sx * w, sy * h, 7, 0, Math.PI * 2); g.fill(); g.stroke();
  }
});
const invFaceMat = new THREE.MeshStandardMaterial({ map: invFaceTex, transparent: true, roughness: 0.4, envMapIntensity: ENV });
function addInverter(g, x, y) {
  const zf = 0.02 + INV_D;  // 正面位置
  mesh(new RoundedBoxGeometry(INV_W, INV_H, INV_D, 4, 0.05), M.white, [x, y, 0.02 + INV_D / 2], null, g);
  mesh(new THREE.PlaneGeometry(INV_W, INV_H), invFaceMat, [x, y, zf + 0.002], null, g, false);
  // 藍色 LCD 與三顆指示燈 (夜間會發光)
  const cy = y + INV_H * (0.5 - 0.44);
  mesh(new THREE.PlaneGeometry(0.17, 0.105), glowMat('#4F7FD0', NIGHT ? 1.4 : 0.5), [x - 0.035, cy, zf + 0.004], null, g, false);
  ['#22C55E', '#22C55E', '#EF4444'].forEach((c, i) =>
    mesh(new THREE.SphereGeometry(0.009, 8, 6), glowMat(c, i < 2 ? 3 : 0.4), [x + 0.105, cy + 0.035 - i * 0.03, zf + 0.006], null, g, false));
  // 頂部掛架、左側直流開關、底部接線端子
  mesh(box(INV_W * 0.82, 0.05, 0.05), M.alu, [x, y + INV_H / 2 + 0.02, 0.05], null, g);
  mesh(box(0.035, 0.1, 0.06), M.dark, [x - INV_W / 2 - 0.015, y - 0.12, 0.14], null, g);
  for (const dx of [-0.22, -0.11, -0.02, 0.07, 0.16, 0.23])
    mesh(new THREE.CylinderGeometry(0.025, 0.025, 0.05, 10), M.dark, [x + dx, y - INV_H / 2 - 0.02, 0.14], null, g);
}
function addBattery(g, x, y, cells = 4) {  // y 為電池中心高度；每格模組高 0.35 m
  const BH = cells * 0.35, bar = BH - 0.3;
  mesh(new RoundedBoxGeometry(0.8, BH, 0.38, 4, 0.05), M.white, [x, y, 0.22], null, g);
  for (let i = 1; i < cells; i++) mesh(box(0.78, 0.012, 0.01), std('#CBD5E1', 0.6), [x, y - BH / 2 + i * 0.35, 0.41], null, g, false);
  const h = bar * Math.max(D.soc, 3) / 100;
  mesh(box(0.06, bar, 0.01), std('#CBD5E1', 0.6), [x - 0.28, y, 0.415], null, g, false);
  mesh(box(0.06, h, 0.012), glowMat('#8B5CF6', 1.6), [x - 0.28, y - bar / 2 + h / 2, 0.42], null, g, false);
}
function addPole(pos) {
  mesh(new THREE.CylinderGeometry(0.11, 0.15, 7.4, 12), M.pole, [pos[0], Y0 + 3.7, pos[2]]);
  mesh(box(1.8, 0.12, 0.12), M.pole, [pos[0], Y0 + 6.9, pos[2]], [0, 0.5, 0]);
  for (const dx of [-0.7, 0.7]) mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.18, 8), M.white, [pos[0] + dx * Math.cos(0.5), Y0 + 7.05, pos[2] - dx * Math.sin(0.5)]);
}
function sagWire(a, b, sag = 1.3) {
  const mid = a.clone().add(b).multiplyScalar(0.5); mid.y -= sag;
  const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
  mesh(new THREE.TubeGeometry(curve, 40, 0.018, 6), M.dark, null, null, scene, false);
  return curve;
}
function poly(pts) {
  const cp = new THREE.CurvePath();
  for (let i = 0; i < pts.length - 1; i++) cp.add(new THREE.LineCurve3(pts[i], pts[i + 1]));
  return cp;
}
function chain(...curves) {
  const cp = new THREE.CurvePath();
  for (const c of curves) for (const k of (c.curves || [c])) cp.add(k);
  return cp;
}

const eaveY = TOP + RISE - halfRun * Math.tan(slope) + 0.05;
const roofAt = z => TOP + RISE + 0.32 - Math.abs(z) * Math.tan(slope);  // 屋面 (板面) 在 z 處的高度
const XW = W / 2 + 0.07;   // 右側牆面外側
const ZW = DP / 2 + 0.07;  // 正面牆面外側
const pvDrop = [V(3.4, eaveY + 0.35, DP / 2 + 0.1), V(3.4, eaveY - 0.05, DP / 2 + OVER - 0.05)];
let P;  // 管線與標籤位置

if (LAYOUT === 'front') {
  // 正面只留門窗；右側牆後段 (木紋) 中央由前往後：逆變器 (電池在正下方) → 約 2 m 後為配電盤。
  // 太陽能由屋頂板陣邊緣沿屋面拉到逆變器正上方，再一直線垂直往下；家用電由逆變器水平直達配電盤；台電由右後方電線桿架空接到二樓牆面再下到配電盤
  const S = frame([W / 2, 0, 0], Math.PI / 2);
  // 設備組置中於木紋牆：IX = 逆變器/電池、PX = 配電盤 (牆面局部座標，往後為正)
  const WOOD_C = DP / 4 - 0.3, IX = WOOD_C - 1.1, PX = WOOD_C + 1.1, LX = WOOD_C;
  // 逆變器與配電盤上緣都貼齊二樓樓板底 (EQ_TOP)；EQ_Y = 逆變器中心，PY = 配電盤中心
  const EQ_TOP = F2 - 0.05, EQ_Y = EQ_TOP - INV_H / 2, PY = EQ_TOP - 0.475;
  // 電池 3 格 (1.05 m) 貼地，與逆變器底部相距約 1.2 m
  addInverter(S, IX, EQ_Y); addBattery(S, IX, Y0 + 0.525, 3);
  addPanel(S, PX, PY);
  const pole = [8.6, 0, -6.6]; addPole(pole);
  // 配電盤上緣分兩邊：靠後 (近電線桿) 接台電進線，靠前接往屋內的主幹線，兩條線平行不重疊
  const IN_X = PX + 0.22, OUT_X = PX - 0.22;
  const service = at(S, IN_X, EQ_Y + 1.4, 0.07);
  if (NIGHT) {  // 設備區壁燈：照亮逆變器、電池與配電盤
    mesh(box(0.3, 0.08, 0.14), M.trim, at(S, LX, EQ_Y + 0.95, 0.07).toArray());
    mesh(box(0.26, 0.02, 0.1), glowMat('#FFE7B0', 3), at(S, LX, EQ_Y + 0.9, 0.08).toArray(), null, scene, false);
    const lamp = new THREE.PointLight('#FFE2A8', 9, 6, 2); lamp.position.copy(at(S, LX, EQ_Y + 0.7, 0.8)); scene.add(lamp);
  }
  const wire = sagWire(V(pole[0] - 0.6, Y0 + 7.0, pole[2] + 0.35), service, 1.0);
  P = {
    pv: poly([V(xs[ALONG - 1] + PW / 2, roofAt(-IX), -IX), V(XW, roofAt(-IX), -IX), at(S, IX, EQ_Y + INV_H / 2 + 0.05, 0.07)]),
    load: poly([at(S, IX + INV_W / 2, EQ_Y, 0.07), at(S, PX - 0.35, EQ_Y, 0.07)]),
    bat: poly([at(S, IX, EQ_Y - INV_H / 2 - 0.05, 0.14), at(S, IX, Y0 + 1.05, 0.14)]),
    grid: chain(wire, poly([service, at(S, IN_X, PY + 0.48, 0.07)])),
    batCard: [7.6, Y0 + 0.2, -0.6], gridCard: [pole[0], Y0 + 8.0, pole[2]],
    // 主幹線由配電盤上緣往上穿過樓板，在二樓地板面的高度穿牆進屋，貼著二樓地板走到中央分電點 (家中微電網起點)；
    // 放在樓板上面而不是下面，從上方的視角才不會被樓板擋住
    panelOut: [at(S, OUT_X, EQ_TOP - 0.05, 0.07), at(S, OUT_X, F2 + 0.13, 0.07)],
    panelIn: at(S, OUT_X, F2 + 0.13, -0.25),
  };
} else if (LAYOUT === 'door') {
  // 與 front 相同，但配電盤在正面大門右手邊；家用電從逆變器沿落地窗上緣繞到正面，台電走地下管線進配電盤底部
  const S = frame([W / 2, 0, 0], Math.PI / 2);
  const F = frame([0, 0, DP / 2], 0);
  addInverter(S, 0.95, Y0 + 2.5); addBattery(S, 0.95, Y0 + 0.7);
  addPanel(F, 1.9, Y0 + 1.85);
  const pole = [7.6, 0, -6.4]; addPole(pole);
  const g0 = Y0 + 0.08;
  P = {
    pv: poly([V(3.9, eaveY + 0.35, DP / 2 + 0.1), V(3.9, eaveY - 0.05, DP / 2 + OVER - 0.05), V(3.9, TOP - 0.3, ZW), V(XW, TOP - 0.3, ZW),
              at(S, 0.95, TOP - 0.3, 0.07), at(S, 0.95, Y0 + 2.91, 0.07)]),
    load: poly([at(S, 0.59, Y0 + 2.5, 0.07), at(S, 0.4, Y0 + 2.5, 0.07), at(S, 0.4, Y0 + 2.92, 0.07), V(XW, Y0 + 2.92, ZW),
                V(1.9, Y0 + 2.92, ZW), V(1.9, Y0 + 2.18, ZW)]),
    bat: poly([at(S, 0.95, Y0 + 2.09, 0.1), at(S, 0.95, Y0 + 1.4, 0.1)]),
    grid: poly([V(pole[0], Y0 + 2.6, pole[2]), V(pole[0], g0, pole[2]), V(W / 2 + 0.7, g0, ZW + 0.5), V(1.9, g0, ZW + 0.5), V(1.9, g0, ZW), V(1.9, Y0 + 1.52, ZW)]),
    batCard: [7.0, Y0 + 0.2, 0.6], gridCard: [pole[0], Y0 + 8.0, pole[2]],
  };
} else if (LAYOUT === 'side') {
  // 右側牆 (木紋段)：配電箱、逆變器、下方電池；台電由電線桿架空接入牆頂，再沿管線下到配電箱
  const S = frame([W / 2, 0, 0], Math.PI / 2);
  addPanel(S, 0.85, Y0 + 1.85); addInverter(S, 1.75, Y0 + 2.15); addBattery(S, 1.75, Y0 + 0.7);
  const pole = [8.8, 0, -3.8]; addPole(pole);
  const service = at(S, 0.85, Y0 + 3.05, 0.07);
  const wire = sagWire(V(pole[0] - 0.6, Y0 + 7.0, pole[2] + 0.35), service, 1.2);
  P = {
    pv: poly([...pvDrop, V(XW, eaveY - 0.05, DP / 2 + OVER - 0.05), V(XW, Y0 + 2.95, DP / 2 - 0.05),
              at(S, 1.75, Y0 + 2.95, 0.07), at(S, 1.75, Y0 + 2.56, 0.07)]),
    load: poly([at(S, 1.39, Y0 + 2.0, 0.07), at(S, 1.15, Y0 + 2.0, 0.07), at(S, 1.15, Y0 + 1.3, 0.07),
                at(S, 0.85, Y0 + 1.3, 0.07), at(S, 0.85, Y0 + 1.52, 0.07)]),
    bat: poly([at(S, 1.95, Y0 + 1.74, 0.1), at(S, 1.95, Y0 + 1.4, 0.15)]),
    grid: chain(wire, poly([service, at(S, 0.85, Y0 + 2.18, 0.07)])),
    batCard: [W / 2 + 2.6, Y0 + 0.2, -0.6], gridCard: [pole[0], Y0 + 8.0, pole[2]],
  };
} else {
  // 側院設備區：水泥平台 + 背板 + 雨遮；配電箱在右側牆，兩者以地下管線連接
  const Y = frame([6.6, 0, -1.3], 0);
  mesh(box(2.5, 0.12, 1.4), M.concrete, [0, Y0 + 0.06, 0.45], null, Y);
  mesh(box(2.3, 2.7, 0.08), std('#4B5563', 0.8), [0, Y0 + 1.47, -0.04], null, Y);
  mesh(box(2.7, 0.07, 1.45), M.trim, [0, Y0 + 2.95, 0.5], [0.08, 0, 0], Y);
  for (const x of [-1.2, 1.2]) mesh(new THREE.CylinderGeometry(0.035, 0.035, 2.85, 8), M.trim, [x, Y0 + 1.5, 1.1], null, Y);
  addInverter(Y, -0.5, Y0 + 1.85); addBattery(Y, 0.55, Y0 + 0.82);
  const S = frame([W / 2, 0, 0], Math.PI / 2);
  addPanel(S, 1.3, Y0 + 1.7);
  const pole = [9.0, 0, -4.4]; addPole(pole);
  const service = at(S, 1.3, Y0 + 3.05, 0.07);
  const wire = sagWire(V(pole[0] - 0.6, Y0 + 7.0, pole[2] + 0.35), service, 1.2);
  const g0 = Y0 + 0.08;
  P = {
    pv: poly([...pvDrop, V(XW, eaveY - 0.05, DP / 2 + OVER - 0.05), V(XW, eaveY - 0.05, DP / 2 + 0.07), V(XW, g0, DP / 2 + 0.07),
              at(Y, -0.75, g0, 0.6), at(Y, -0.75, Y0 + 0.13, 0.08), at(Y, -0.75, Y0 + 2.45, 0.08), at(Y, -0.5, Y0 + 2.45, 0.08), at(Y, -0.5, Y0 + 2.26, 0.08)]),
    load: poly([at(Y, -0.86, Y0 + 1.6, 0.1), at(Y, -1.0, Y0 + 1.6, 0.1), at(Y, -1.0, g0, 0.1), at(S, 1.3, g0, 0.07), at(S, 1.3, Y0 + 1.37, 0.07)]),
    bat: poly([at(Y, -0.14, Y0 + 1.7, 0.14), at(Y, 0.55, Y0 + 1.7, 0.14), at(Y, 0.55, Y0 + 1.52, 0.22)]),
    grid: chain(wire, poly([service, at(S, 1.3, Y0 + 2.03, 0.07)])),
    batCard: [8.6, Y0 + 0.1, 0.8], gridCard: [pole[0], Y0 + 8.0, pole[2]],
  };
}

// ---------- 光源 ----------
if (!NIGHT) {
  scene.add(new THREE.HemisphereLight('#DCEBFF', '#5B6B4E', 0.9));
  const sun = new THREE.DirectionalLight('#FFF1DC', 3.0);
  sun.position.set(9, 16, 10);
  Object.assign(sun.shadow.camera, { left: -14, right: 14, top: 14, bottom: -14, near: 1, far: 50 });
  sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -0.0004; sun.shadow.radius = 4;
  sun.castShadow = true; scene.add(sun);
  const fill = new THREE.DirectionalLight('#BFD7FF', 0.5); fill.position.set(-10, 6, -4); scene.add(fill);
} else {
  scene.add(new THREE.HemisphereLight('#3B5BA9', '#0B1120', 0.45));
  const moon = new THREE.DirectionalLight('#A9BFFF', 0.7);
  moon.position.set(-8, 14, 9);
  Object.assign(moon.shadow.camera, { left: -14, right: 14, top: 14, bottom: -14, near: 1, far: 50 });
  moon.shadow.mapSize.set(1024, 1024); moon.shadow.radius = 6;
  moon.castShadow = true; scene.add(moon);
  // 室內燈光從窗戶灑出
  for (const p of [[-3.0, Y0 + 1.6, DP / 2 + 0.8], [-1.1, Y0 + 1.6, DP / 2 + 0.8], [W / 2 + 0.9, Y0 + 1.4, 3.0], [0.7, Y0 + 2.6, DP / 2 + 0.5],
                   [-1.6, F2 + 1.4, DP / 2 + 1.6], [2.3, F2 + 1.5, DP / 2 + 0.8]]) {
    const l = new THREE.PointLight('#FFC66E', 6, 7, 2); l.position.set(...p); scene.add(l);
  }
}

// ---------- 室內透視：溫馨科技風住宅 (家人與電器依時段示意，非實測分路用電) ----------
// 1F：前段 客廳(左) / 餐廳(右)；後段 廚房(右)、洗衣(左)、樓梯(左側)；2F 後陽台放儲熱式熱水器
// 2F：前段 兒童房(左) / 主臥(右)；後段 浴室(右)、閱讀角(左)
const HOUR = D.hour ?? 12, WEEKEND = !!D.weekend;
const inRange = (a, b) => HOUR >= a && HOUR < b;
const SLEEP = HOUR >= 23 || HOUR < 6;
const ACT = {
  lights: (HOUR >= 17 || HOUR < 6.5 || NIGHT) && !SLEEP,
  kitchen: inRange(6, 7.5) || inRange(11, 12.5) || inRange(17, 19),
  tv: inRange(19, 23) || (WEEKEND && inRange(13, 17)),
  study: (!WEEKEND && inRange(18.5, 22)) || (WEEKEND && inRange(9, 11)),
  heater: inRange(9, 15) && D.pv > 0.8,   // 白天用太陽能加熱洗澡熱水
  vacuum: inRange(9, 15),
  laundry: inRange(9, 11.5) || (WEEKEND && inRange(14, 16)),
  dehumid: inRange(8, 18),
};
const interior = new THREE.Group(); scene.add(interior);
const iMat = (c, r = 0.8, extra = {}) => std(c, r, 0, extra);
const glow = (c, i) => std(c, 0.3, 0, { emissive: new THREE.Color(c), emissiveIntensity: i });
const IN = (geo, mat, pos, rot) => mesh(geo, mat, pos, rot, interior);
const anim = [];
const F1 = Y0 + 0.31, F2f = F2 + 0.09, C1 = Y0 + FL - 0.05, C2 = TOP - 0.05;

// 材質：木地板、溫暖布料、深色科技面板
const floorTex = canvasTex(256, 256, (g, w, h) => {
  for (let i = 0; i < 8; i++) { g.fillStyle = ['#C8A27A', '#BF9870', '#CDA982', '#C29C74'][i % 4]; g.fillRect(0, i * h / 8, w, h / 8 - 2);
    g.fillStyle = 'rgba(80,50,20,0.35)'; g.fillRect((i * 97) % w, i * h / 8, 2, h / 8); }
}, [3, 4]);
const MI = {
  floor: std('#FFFFFF', 0.7, 0, { map: floorTex }), floor2: std('#FFFFFF', 0.7, 0, { map: floorTex }),
  part: iMat('#F3EEE6', 0.9, { transparent: true, opacity: 0.45 }),
  glassPart: std('#CFE6F5', 0.1, 0.1, { transparent: true, opacity: 0.25 }),
  fabric: iMat('#8C9A8B', 0.95), fabric2: iMat('#C9B79C', 0.95), cushion: iMat('#E8C07A', 0.9),
  oak: iMat('#B98B5E', 0.6), walnut: iMat('#6B4A33', 0.6), white: iMat('#F5F5F2', 0.45),
  dark: iMat('#1F242C', 0.4), metal: std('#A7AEB8', 0.35, 0.7), rug: iMat('#D9CBB4', 1), rug2: iMat('#9FB3C8', 1),
  plant: iMat('#4E8B4A', 0.8, { flatShading: true }), pot: iMat('#D7CFC4', 0.7), sheet: iMat('#EEF2F7', 0.9), blanket: iMat('#7C9CC4', 0.9),
};

// 樓板與隔間
IN(box(W - 0.3, 0.06, DP - 0.3), MI.floor, [0, F1 - 0.03, 0]);
IN(box(W - 0.3, 0.14, DP - 0.3), MI.floor2, [0, F2 + 0.02, 0]);
const wallZ = (x1, x2, z, y0, h, mat = MI.part) => IN(box(x2 - x1, h, 0.08), mat, [(x1 + x2) / 2, y0 + h / 2, z]);
const wallX = (z1, z2, x, y0, h, mat = MI.part) => IN(box(0.08, h, z2 - z1), mat, [x, y0 + h / 2, (z1 + z2) / 2]);
wallZ(-2.6, -0.2, -0.9, F1, FL - 0.35); wallZ(1.6, 4.05, -0.9, F1, FL - 0.35, MI.glassPart);   // 1F 前後分區 (廚房玻璃隔間)
wallZ(-4.05, -0.3, 0.9, F2f, FL - 0.2); wallZ(0.9, 4.05, 0.9, F2f, FL - 0.2);                 // 2F 前後分區
wallX(0.9, 5.6, 0.3, F2f, FL - 0.2);                                                           // 兒童房 / 主臥
wallX(-5.6, 0.9, 0.9, F2f, FL - 0.2, MI.glassPart);                                            // 浴室玻璃

// 天花板間接照明 (LED 燈條)：傍晚後暖光，白天微亮
const stripMat = glow('#FFD9A0', ACT.lights ? 2.2 : 0.25);
const accentMat = glow('#5EEAD4', ACT.lights ? 1.6 : 0.6);
function strip(x1, z1, x2, z2, y, mat = stripMat) {
  const len = Math.hypot(x2 - x1, z2 - z1);
  IN(box(x1 === x2 ? 0.04 : len, 0.03, z1 === z2 ? 0.04 : len), mat, [(x1 + x2) / 2, y, (z1 + z2) / 2]);
}
for (const y of [C1 - 0.08, C2 - 0.08]) {
  strip(-3.95, 5.45, 3.95, 5.45, y); strip(-3.95, -5.45, 3.95, -5.45, y);
  strip(-3.95, -5.45, -3.95, 5.45, y); strip(3.95, -5.45, 3.95, 5.45, y);
}
if (ACT.lights) for (const [x, y, z] of [[-2.2, C1 - 0.3, 3.4], [2.5, C1 - 0.3, 3.3], [2.3, C1 - 0.3, -3.3], [-2.0, C2 - 0.3, 3.3], [2.3, C2 - 0.3, 3.3], [2.4, C2 - 0.3, -2.5]]) {
  const l = new THREE.PointLight('#FFE3B8', NIGHT ? 6 : 3, 6.5, 2); l.position.set(x, y, z); interior.add(l);
}

function person(x, y, z, shirt, scale = 1, rotY = 0, sit = false) {
  const g = new THREE.Group(); g.position.set(x, y, z); g.rotation.y = rotY; g.scale.setScalar(scale); interior.add(g);
  mesh(new THREE.CapsuleGeometry(0.2, sit ? 0.35 : 0.55, 6, 12), iMat(shirt, 0.7), [0, sit ? 0.4 : 0.5, 0], null, g);
  mesh(new THREE.SphereGeometry(0.17, 16, 12), iMat('#F1C9A5', 0.6), [0, sit ? 0.88 : 1.05, 0], null, g);
  mesh(new THREE.SphereGeometry(0.175, 16, 12, 0, Math.PI * 2, 0, Math.PI / 2), iMat('#2B2118', 0.8), [0, sit ? 0.92 : 1.09, -0.01], null, g);
  return g;
}
function plant(x, y, z, s = 1) {
  IN(new THREE.CylinderGeometry(0.16 * s, 0.12 * s, 0.32 * s, 16), MI.pot, [x, y + 0.16 * s, z]);
  IN(new THREE.IcosahedronGeometry(0.3 * s, 1), MI.plant, [x, y + 0.55 * s, z]);
}

// ===== 1F 客廳 (前左) =====
IN(box(3.0, 0.02, 2.2), MI.rug, [-2.2, F1 + 0.01, 3.6]);
IN(box(3.4, 2.5, 0.12), iMat('#2A2F37', 0.6), [-2.2, F1 + 1.25, 1.75]);                         // 電視牆
IN(box(3.2, 0.03, 0.03), accentMat, [-2.2, F1 + 0.42, 1.83]);                                    // 電視櫃下藍綠燈條
IN(box(2.6, 0.4, 0.45), MI.walnut, [-2.2, F1 + 0.2, 2.05]);
const tv = IN(box(1.9, 1.05, 0.05), ACT.tv && !SLEEP ? glow('#7CB7FF', 1.3) : MI.dark, [-2.2, F1 + 1.35, 1.83]);
IN(box(2.6, 0.42, 0.9), MI.fabric, [-2.2, F1 + 0.21, 4.75]); IN(box(2.6, 0.5, 0.22), MI.fabric, [-2.2, F1 + 0.6, 5.1]);
IN(box(0.9, 0.42, 1.6), MI.fabric, [-3.45, F1 + 0.21, 4.2]);                                     // L 型沙發
for (const dx of [-0.8, 0.1]) IN(box(0.45, 0.35, 0.15), MI.cushion, [-2.2 + dx, F1 + 0.6, 4.95]);
IN(new THREE.CylinderGeometry(0.45, 0.45, 0.35, 32), MI.oak, [-2.2, F1 + 0.18, 3.55]);           // 圓茶几
plant(-3.7, F1, 2.3, 1.2);
IN(new THREE.CylinderGeometry(0.02, 0.02, 1.5, 8), MI.metal, [-0.9, F1 + 0.75, 5.0]);            // 立燈
IN(new THREE.SphereGeometry(0.16, 16, 12), ACT.lights ? glow('#FFD49A', 2.5) : MI.white, [-0.9, F1 + 1.55, 5.0]);
if (ACT.tv && !SLEEP) {
  anim.push(t => { tv.material.emissiveIntensity = 1.0 + 0.4 * Math.sin(t * 7) * Math.sin(t * 2.3); });
}

// ===== 1F 餐廳 (前右) =====
IN(box(1.8, 0.05, 0.95), MI.oak, [2.5, F1 + 0.75, 3.3]);
for (const dx of [-0.75, 0.75]) IN(box(0.06, 0.72, 0.8), MI.walnut, [2.5 + dx, F1 + 0.37, 3.3]);
for (const [dx, dz] of [[-0.45, -0.75], [0.45, -0.75], [-0.45, 0.75], [0.45, 0.75]]) {
  IN(box(0.42, 0.06, 0.42), MI.fabric2, [2.5 + dx, F1 + 0.45, 3.3 + dz]);
  IN(box(0.42, 0.45, 0.05), MI.fabric2, [2.5 + dx, F1 + 0.7, 3.3 + dz * 1.28]);
}
for (const dx of [-0.45, 0.45]) {                                                                // 吊燈
  IN(new THREE.CylinderGeometry(0.005, 0.005, 0.9, 6), MI.dark, [2.5 + dx, C1 - 0.45, 3.3]);
  IN(new THREE.ConeGeometry(0.18, 0.2, 20, 1, true), ACT.lights ? glow('#FFD49A', 2.2) : MI.dark, [2.5 + dx, C1 - 0.95, 3.3]);
}
plant(3.7, F1, 5.0, 1.0);

// ===== 1F 廚房 (後右，玻璃隔間) =====
const KZ = -5.15;
IN(box(3.4, 0.9, 0.6), MI.white, [2.1, F1 + 0.45, KZ]);
IN(box(3.4, 0.04, 0.62), iMat('#3F4650', 0.3), [2.1, F1 + 0.92, KZ]);
IN(box(3.4, 0.6, 0.35), MI.white, [2.1, F1 + 2.15, KZ - 0.1]);                                  // 吊櫃
IN(box(3.3, 0.02, 0.02), accentMat, [2.1, F1 + 1.83, KZ + 0.05]);                                // 櫃下燈條
IN(box(0.85, 1.9, 0.7), iMat('#D1D5DB', 0.3, { metalness: 0.4 }), [-0.2, F1 + 0.95, -5.0]);       // 冰箱 (流理台左側，不擋配電盤下來的線)
IN(box(0.06, 0.6, 0.02), accentMat, [0.12, F1 + 1.2, -4.64]);
IN(box(1.8, 0.9, 0.8), MI.oak, [1.9, F1 + 0.45, -3.4]);                                          // 中島
IN(box(1.82, 0.04, 0.82), iMat('#ECECEC', 0.25), [1.9, F1 + 0.92, -3.4]);
IN(box(0.8, 0.4, 0.5), MI.metal, [2.1, F1 + 1.95, KZ + 0.05]);                                   // 排油煙機
IN(box(0.6, 0.02, 0.3), ACT.kitchen && !SLEEP ? glow('#FFE7B0', 2) : MI.dark, [2.1, F1 + 1.74, KZ + 0.1]);
IN(new THREE.CylinderGeometry(0.17, 0.17, 0.27, 20), iMat('#E5E1DA', 0.4), [0.9, F1 + 1.08, KZ]);  // 電鍋
IN(box(0.3, 0.34, 0.3), MI.dark, [3.2, F1 + 1.11, KZ]);                                          // 氣炸鍋
IN(new THREE.CylinderGeometry(0.2, 0.18, 0.15, 20), MI.metal, [2.1, F1 + 1.02, KZ]);              // 炒鍋
for (const dx of [-0.5, 0.5]) IN(box(0.36, 0.7, 0.36), MI.walnut, [1.9 + dx, F1 + 0.35, -2.7]);  // 吧台椅
if (ACT.kitchen && !SLEEP) {
  const mom = person(2.1, F1, -4.45, '#C2557A', 1.3, Math.PI);
  anim.push(t => { mom.position.x = 2.1 + 0.35 * Math.sin(t * 0.7); });
  const steamTex = glowTex0();
  for (const [sx, sy] of [[0.9, F1 + 1.25], [2.1, F1 + 1.12], [3.2, F1 + 1.3]]) for (let i = 0; i < 4; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: steamTex, color: '#FFFFFF', transparent: true, depthWrite: false })); interior.add(sp);
    anim.push(t => { const k = (t * 0.5 + i / 4) % 1; sp.position.set(sx + 0.05 * Math.sin(t * 3 + i), sy + k * 0.6, KZ);
                     sp.material.opacity = 0.45 * (1 - k); sp.scale.setScalar(0.15 + k * 0.25); });
  }
}

// ===== 1F 洗衣 (後左)、樓梯 =====
IN(box(0.62, 0.85, 0.6), MI.white, [-2.1, F1 + 0.43, -5.0]);                                     // 洗衣機
const drum = IN(new THREE.CircleGeometry(0.2, 24), ACT.laundry ? glow('#38BDF8', 0.8) : iMat('#334155', 0.2), [-2.1, F1 + 0.45, -4.68]);
if (ACT.laundry) anim.push(t => { drum.rotation.z = t * 6; drum.material.emissiveIntensity = 0.6 + 0.3 * Math.sin(t * 6); });
IN(box(0.62, 0.85, 0.6), MI.white, [-1.4, F1 + 0.43, -5.0]);                                     // 烘衣機
IN(box(0.3, 0.06, 0.2), MI.dark, [-1.2, F1 + 1.2, -0.84]);                                       // Wi-Fi 路由器
for (let i = 0; i < 3; i++) { const led = IN(new THREE.SphereGeometry(0.012, 8, 6), glow('#22C55E', 2), [-1.3 + i * 0.05, F1 + 1.235, -0.73]);
  anim.push(t => { led.material.emissiveIntensity = (Math.sin(t * 9 + i * 2) > 0) ? 2.5 : 0.4; }); }
IN(new THREE.CircleGeometry(0.2, 24), iMat('#334155', 0.2), [-2.1, F1 + 0.45, -4.69]);
for (let i = 0; i < 13; i++) {                                                                    // 樓梯 (沿左牆往後上樓)
  const h = (F2 - F1 + 0.1) / 13;
  IN(box(1.0, 0.06, 0.32), MI.oak, [-3.5, F1 + h * (i + 1), -1.2 - i * 0.3]);
  IN(box(1.0, 0.02, 0.02), accentMat, [-3.5, F1 + h * (i + 1) - 0.05, -1.05 - i * 0.3]);           // 踏階燈
}
IN(box(0.04, 0.9, 4.0), MI.glassPart, [-2.98, F1 + 1.6, -3.0]);                                  // 玻璃扶手

// 智慧能源面板 (走道牆上)：電池電量環 + 發電長條，與實際數據連動
const smartTex = canvasTex(256, 160, (g, w, h) => {
  g.fillStyle = '#0B1220'; g.fillRect(0, 0, w, h);
  g.lineWidth = 14; g.strokeStyle = '#1E293B'; g.beginPath(); g.arc(70, 80, 46, 0, Math.PI * 2); g.stroke();
  g.strokeStyle = '#A78BFA'; g.beginPath(); g.arc(70, 80, 46, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * D.soc / 100); g.stroke();
  const bars = [[D.pv, '#10B981'], [D.load, '#F97316'], [Math.abs(D.grid), '#38BDF8']];
  bars.forEach(([v, c], i) => { g.fillStyle = '#1E293B'; g.fillRect(140, 38 + i * 32, 96, 16);
    g.fillStyle = c; g.fillRect(140, 38 + i * 32, 96 * Math.min(v / 8, 1), 16); });
});
IN(box(0.62, 0.42, 0.03), MI.dark, [-1.2, F1 + 1.5, -0.85]);
IN(new THREE.PlaneGeometry(0.56, 0.36), new THREE.MeshBasicMaterial({ map: smartTex }), [-1.2, F1 + 1.5, -0.83]);

// 掃地機器人 (白天在客廳餐廳巡航)
if (ACT.vacuum) {
  const bot = new THREE.Group(); interior.add(bot);
  mesh(new THREE.CylinderGeometry(0.18, 0.18, 0.08, 24), MI.white, [0, 0.04, 0], null, bot);
  mesh(new THREE.CylinderGeometry(0.05, 0.05, 0.02, 16), accentMat, [0, 0.09, 0.06], null, bot);
  anim.push(t => { const a = t * 0.35; bot.position.set(0.2 + 2.8 * Math.cos(a), F1, 3.0 + 1.0 * Math.sin(2 * a)); bot.rotation.y = -a; });
}

// ===== 2F 兒童房 (前左) =====
IN(box(2.4, 0.02, 1.6), MI.rug2, [-1.9, F2f + 0.01, 3.4]);
IN(box(2.6, 0.06, 0.7), MI.oak, [-1.9, F2f + 0.75, 1.35]);                                     // 長書桌 (靠隔間)
for (const dx of [-1.25, 1.25]) IN(box(0.06, 0.75, 0.65), MI.walnut, [-1.9 + dx, F2f + 0.37, 1.35]);
IN(box(2.6, 0.03, 0.25), MI.oak, [-1.9, F2f + 1.5, 1.05]);                                    // 層板與書
for (let i = 0; i < 9; i++) IN(box(0.06, 0.24, 0.18), iMat(['#EF4444', '#3B82F6', '#F59E0B', '#10B981'][i % 4], 0.8), [-2.9 + i * 0.22, F2f + 1.64, 1.05]);
const study = ACT.study && !SLEEP;
for (const dx of [-0.6, 0.6]) {                                                                 // 檯燈
  IN(new THREE.CylinderGeometry(0.015, 0.015, 0.4, 8), MI.dark, [-1.9 + dx - 0.35, F2f + 0.98, 1.2]);
  IN(new THREE.ConeGeometry(0.12, 0.14, 16, 1, true), study ? glow('#FFE7A0', 2.5) : MI.metal, [-1.9 + dx - 0.35, F2f + 1.18, 1.25]);
  if (study) { const l = new THREE.PointLight('#FFE2A8', 2.5, 2.5, 2); l.position.set(-1.9 + dx - 0.35, F2f + 1.05, 1.4); interior.add(l); }
}
for (const dz of [0, 1]) {                                                                       // 上下舖
  IN(box(1.0, 0.25, 2.0), MI.white, [-3.45, F2f + 0.3 + dz * 1.3, 4.4]);
  IN(box(0.95, 0.12, 1.9), MI.blanket, [-3.45, F2f + 0.48 + dz * 1.3, 4.4]);
}
for (const [dx, dz] of [[-0.5, -1], [0.5, -1], [-0.5, 1], [0.5, 1]]) IN(box(0.05, 1.8, 0.05), MI.oak, [-3.45 + dx, F2f + 0.9, 4.4 + dz]);

// ===== 2F 主臥 (前右) =====
IN(box(1.9, 0.35, 2.1), MI.white, [2.4, F2f + 0.18, 3.6]);
IN(box(1.85, 0.15, 1.6), MI.sheet, [2.4, F2f + 0.42, 3.8]);
IN(box(1.85, 0.08, 0.9), MI.fabric2, [2.4, F2f + 0.5, 4.2]);
IN(box(2.1, 1.0, 0.1), iMat('#8B6F55', 0.8), [2.4, F2f + 0.7, 2.5]);                              // 床頭板
IN(box(1.9, 0.03, 0.03), stripMat, [2.4, F2f + 1.22, 2.56]);
for (const dx of [-1.25, 1.25]) {
  IN(box(0.45, 0.45, 0.4), MI.walnut, [2.4 + dx, F2f + 0.23, 2.75]);
  IN(new THREE.SphereGeometry(0.1, 16, 12), ACT.lights || SLEEP ? glow('#FFD49A', SLEEP ? 0.8 : 2) : MI.white, [2.4 + dx, F2f + 0.6, 2.75]);
}
plant(3.7, F2f, 5.2, 0.9);
const purifier = IN(box(0.35, 0.65, 0.35), MI.white, [3.7, F2f + 0.33, 1.5]);                     // 空氣清淨機 (常時)
IN(new THREE.CircleGeometry(0.1, 20), glow('#5EEAD4', 1.2), [3.7, F2f + 0.6, 1.68]);

// ===== 2F 浴室 (後右)、閱讀角 (後左) =====
IN(box(1.7, 0.55, 0.8), MI.white, [2.6, F2f + 0.28, -4.8]);
IN(box(1.5, 0.05, 0.6), iMat('#A5D8F0', 0.1, { transparent: true, opacity: 0.8 }), [2.6, F2f + 0.5, -4.8]);
IN(box(1.0, 0.8, 0.5), MI.oak, [1.6, F2f + 0.4, -2.0]);
IN(box(0.8, 0.6, 0.03), glow('#E0F2FE', ACT.lights ? 0.9 : 0.25), [1.6, F2f + 1.5, -2.25]);       // 發光鏡
IN(box(0.8, 1.9, 0.35), MI.oak, [-3.6, F2f + 0.95, -2.5]);                                      // 書櫃
for (let r = 0; r < 4; r++) for (let i = 0; i < 4; i++) IN(box(0.05, 0.28, 0.2), iMat(['#94A3B8', '#C084FC', '#FB923C', '#22D3EE'][(r + i) % 4], 0.8), [-3.6, F2f + 0.3 + r * 0.45, -2.75 + i * 0.12]);
IN(new THREE.SphereGeometry(0.45, 20, 14, 0, Math.PI * 2, 0, Math.PI / 1.6), MI.cushion, [-2.3, F2f + 0.2, -3.6]);  // 懶骨頭
plant(-1.2, F2f, -4.8, 1.1);
// 2F 後陽台 (浴室外)：落地玻璃門 + 玻璃欄杆，放儲熱式電熱水器 (屋外，外觀與透視模式都看得到)
const OUT = (geo, mat, pos, rot) => mesh(geo, mat, pos, rot, scene);
const BZ = -DP / 2 - 0.65;   // 陽台中心 (後牆外 1.3 m 深)
OUT(box(2.8, 0.12, 1.3), M.concrete, [2.5, F2 + 0.03, BZ]);
for (const x of [1.3, 3.7]) OUT(box(0.1, 0.35, 1.1), M.concrete, [x, F2 - 0.2, BZ + 0.05]);   // 托架
const railMat = std('#CFE6F5', 0.1, 0.1, { transparent: true, opacity: 0.3, depthWrite: false });
OUT(box(2.8, 1.0, 0.03), railMat, [2.5, F2f + 0.5, BZ - 0.64]);
for (const x of [1.12, 3.88]) OUT(box(0.03, 1.0, 1.3), railMat, [x, F2f + 0.5, BZ]);
OUT(box(2.84, 0.04, 0.06), M.trim, [2.5, F2f + 1.02, BZ - 0.64]);
for (const x of [1.12, 3.88]) OUT(box(0.06, 0.04, 1.3), M.trim, [x, F2f + 1.02, BZ]);
OUT(box(1.0, 2.1, 0.04), M.glass, [1.8, F2f + 1.05, -DP / 2 - 0.02]);                         // 落地門
// 儲熱式電熱水器：白天有太陽能時加熱 (水由下往上變橘紅、加熱管發光、氣泡翻滾、頂部冒蒸氣)；
// 傍晚到洗澡前為保溫的熱水；洗完澡後到隔天早上為冷水
const WH = [3.2, F2f, BZ - 0.15];
const HEATING = ACT.heater, HOT = !HEATING && inRange(15, 22);
OUT(new THREE.CylinderGeometry(0.36, 0.36, 1.5, 28), std('#F1F5F9', 0.35, 0, { transparent: true, opacity: 0.45, depthWrite: false }), [WH[0], WH[1] + 0.85, WH[2]]);
// 水體：透明淺藍；加熱與否用氣泡、水面翻滾、加熱管與溫度表表現
const SURF = WH[1] + 1.45;
const water = OUT(new THREE.CylinderGeometry(0.31, 0.31, 1.3, 28),
  std('#93D5F5', 0.05, 0, { transparent: true, opacity: 0.55, depthWrite: false, emissive: new THREE.Color('#38BDF8'), emissiveIntensity: 0.25 }),
  [WH[0], WH[1] + 0.8, WH[2]]);
const surface = OUT(new THREE.CircleGeometry(0.305, 28), std('#E0F2FE', 0.05, 0, { transparent: true, opacity: 0.7, depthWrite: false }),
  [WH[0], SURF + 0.002, WH[2]], [-Math.PI / 2, 0, 0]);
for (const dy of [0.12, 1.58]) OUT(new THREE.CylinderGeometry(0.37, 0.37, 0.05, 28), MI.metal, [WH[0], WH[1] + dy, WH[2]]);
const heatRing = OUT(new THREE.TorusGeometry(0.38, 0.03, 8, 32), HEATING ? glow('#F97316', 2.5) : iMat('#94A3B8'), [WH[0], WH[1] + 0.35, WH[2]], [Math.PI / 2, 0, 0]);
// 桶底 U 型加熱管
const coil = OUT(new THREE.TorusGeometry(0.16, 0.025, 8, 24, Math.PI * 1.6), HEATING ? glow('#EF4444', 3) : iMat('#9CA3AF', 0.4), [WH[0], WH[1] + 0.26, WH[2]], [Math.PI / 2, 0, 0]);
// 側面溫度顯示 (朝向屋子右側，從預設視角看得到)
const tempTex = canvasTex(128, 64, () => {});
const tempScreen = OUT(new THREE.PlaneGeometry(0.36, 0.18), new THREE.MeshBasicMaterial({ map: tempTex }), [WH[0] + 0.37, WH[1] + 1.15, WH[2]], [0, Math.PI / 2, 0]);
function drawTemp(v) {
  const g = tempTex.image.getContext('2d');
  g.fillStyle = '#0B1220'; g.fillRect(0, 0, 128, 64);
  g.fillStyle = HEATING ? '#FB923C' : HOT ? '#FDBA74' : '#93C5FD';
  g.font = 'bold 40px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(`${Math.round(v)}°C`, 64, 34); tempTex.needsUpdate = true;
}
drawTemp(HEATING ? 45 : HOT ? 65 : 38);
if (HEATING) anim.push(t => {
  heatRing.material.emissiveIntensity = 1.5 + 1.2 * (0.5 + 0.5 * Math.sin(t * 2.5));
  coil.material.emissiveIntensity = 2.2 + 1.2 * Math.sin(t * 6);
  surface.position.y = SURF + 0.002 + 0.008 * Math.sin(t * 11);   // 水面輕微翻騰
  const k = 0.5 + 0.5 * Math.sin(t * 0.4);
  if (Math.floor(t * 4) !== drawTemp.last) { drawTemp.last = Math.floor(t * 4); drawTemp(45 + 20 * k); }
});
if (HEATING || HOT) {
  // 氣泡：從桶底加熱管冒出、邊晃邊往上，愈上面愈大；加熱中又多又快，保溫時零星幾顆
  const bubbleMat = std('#FFFFFF', 0.05, 0, { transparent: true, opacity: 0.85, emissive: new THREE.Color('#F0F9FF'), emissiveIntensity: 0.5 });
  const NB = HEATING ? 34 : 6;
  for (let i = 0; i < NB; i++) {
    const b = OUT(new THREE.SphereGeometry(0.016 + (i % 3) * 0.007, 10, 8), bubbleMat, [WH[0], WH[1], WH[2]]);
    const a = i * 2.4, r = 0.04 + (i * 0.37 % 1) * 0.24, sp = (HEATING ? 0.45 : 0.15) + (i * 0.61 % 1) * 0.3;
    anim.push(t => {
      const u = (t * sp + i / NB) % 1;
      b.position.set(WH[0] + r * Math.cos(a) + 0.025 * Math.sin(t * 8 + i), WH[1] + 0.3 + u * (SURF - WH[1] - 0.3), WH[2] + r * Math.sin(a) + 0.025 * Math.cos(t * 7 + i));
      b.scale.setScalar(0.6 + u * 1.0);
    });
  }
}
if (HEATING) {
  // 水面滾泡：小水泡在水面鼓起後破掉
  const domeMat = std('#F0F9FF', 0.05, 0, { transparent: true, opacity: 0.8, depthWrite: false });
  for (let i = 0; i < 16; i++) {
    const d = OUT(new THREE.SphereGeometry(0.045, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2), domeMat, [WH[0], SURF, WH[2]]);
    const a = i * 2.39, r = 0.05 + (i * 0.53 % 1) * 0.22, sp = 1.2 + (i * 0.71 % 1) * 1.3;
    d.position.set(WH[0] + r * Math.cos(a), SURF, WH[2] + r * Math.sin(a));
    anim.push(t => {
      const u = (t * sp + i / 16) % 1;
      d.scale.setScalar(u < 0.85 ? 0.3 + u * 0.9 : 0.001);   // 慢慢鼓起，最後瞬間破掉
    });
  }
}
if (HEATING || HOT) {
  // 頂部洩壓閥冒蒸氣
  OUT(new THREE.CylinderGeometry(0.03, 0.03, 0.18, 10), MI.metal, [WH[0] - 0.12, WH[1] + 1.69, WH[2]]);
  const steamTex = glowTex0();
  for (let i = 0; i < 7; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: steamTex, color: '#FFFFFF', transparent: true, depthWrite: false })); scene.add(sp);
    anim.push(t => {
      const k = (t * (HEATING ? 0.45 : 0.25) + i / 7) % 1;
      sp.position.set(WH[0] - 0.12 + 0.08 * Math.sin(t * 2 + i), WH[1] + 1.8 + k * 0.9, WH[2] + 0.06 * Math.cos(t * 1.7 + i));
      sp.material.opacity = (HEATING ? 0.7 : 0.35) * (1 - k); sp.scale.setScalar(0.12 + k * 0.4);
    });
  }
}
// 2F 除濕機 (兒童房，白天運轉)
IN(box(0.38, 0.6, 0.28), MI.white, [-0.4, F2f + 0.3, 5.0]);
const dh = IN(box(0.25, 0.04, 0.02), ACT.dehumid ? glow('#38BDF8', 1.5) : MI.dark, [-0.4, F2f + 0.5, 5.15]);
if (ACT.dehumid) anim.push(t => { dh.material.emissiveIntensity = 1 + 0.6 * Math.sin(t * 2); });



// ===== 家人作息：爸爸、媽媽、兩個兒子依時段出現在不同位置 (示意) =====
const DAD = '#2F6FB0', MOM = '#C2557A', SON1 = '#16A34A', SON2 = '#EA580C';
const sit = (x, y, z, c, sc, rot) => person(x, y, z, c, sc, rot, true);
function lie(x, y, z, c, sc) { const p = person(x, y, z, c, sc, 0); p.rotation.x = -Math.PI / 2; return p; }
function screen(x, y, z, rotY = 0) {   // 筆電 / 平板發光螢幕
  const m = IN(box(0.32, 0.22, 0.02), glow('#93C5FD', 1.4), [x, y, z], [0, rotY, 0]);
  anim.push(t => { m.material.emissiveIntensity = 1.1 + 0.3 * Math.sin(t * 3); });
}
const eat = inRange(6, 7.5) || inRange(12, 13) || inRange(17.5, 19);

// 爸爸
if (SLEEP) lie(2.05, F2f + 0.62, 3.9, DAD, 1.2);
else if (eat) sit(2.05, F1 + 0.25, 4.05, DAD, 1.3, Math.PI);
else if (HOUR >= 19) sit(-1.6, F1 + 0.2, 4.75, DAD, 1.35, Math.PI);                    // 客廳看電視
else { const d = sit(-2.3, F2f + 0.25, -3.35, DAD, 1.3, Math.PI); screen(-2.3, F2f + 0.95, -3.85); }  // 閱讀角用筆電工作

// 媽媽 (下廚時段已在廚房)
if (SLEEP) lie(2.75, F2f + 0.62, 3.9, MOM, 1.15);
else if (!(ACT.kitchen && !SLEEP)) {
  if (eat) sit(2.95, F1 + 0.25, 2.55, MOM, 1.25, 0);
  else if (HOUR >= 19) sit(-2.6, F1 + 0.2, 4.75, MOM, 1.3, Math.PI);                  // 陪爸爸看電視
  else { sit(2.95, F1 + 0.25, 2.55, MOM, 1.25, 0); screen(2.95, F1 + 0.83, 2.95, Math.PI); }  // 餐桌用平板
}

// 兩個兒子
if (SLEEP || HOUR >= 22) { lie(-3.45, F2f + 0.62, 4.4, SON1, 0.9); lie(-3.45, F2f + 1.92, 4.4, SON2, 0.85); }
else if (ACT.study) {
  [sit(-2.5, F2f + 0.15, 1.95, SON1, 1.0, Math.PI), sit(-1.3, F2f + 0.15, 1.95, SON2, 0.95, Math.PI)]
    .forEach((k, i) => anim.push(t => { k.rotation.x = 0.18 + 0.05 * Math.sin(t * 2 + i * 1.7); }));   // 低頭寫功課
} else if (eat) { sit(2.95, F1 + 0.25, 4.05, SON1, 1.0, Math.PI); sit(2.05, F1 + 0.25, 2.55, SON2, 0.95, 0); }
else if (inRange(13, 17.5) || HOUR >= 18.5) {                                          // 客廳地毯：一個玩平板、一個玩球
  sit(-2.7, F1 - 0.05, 3.3, SON1, 0.95, Math.PI / 2); screen(-2.35, F1 + 0.42, 3.3, Math.PI / 2);
  const s2 = person(-1.6, F1, 3.0, SON2, 0.9, -Math.PI / 2);
  const ball = IN(new THREE.SphereGeometry(0.1, 16, 12), iMat('#EF4444', 0.5), [-1.2, F1 + 0.1, 3.0]);
  anim.push(t => { const k = Math.abs(Math.sin(t * 3)); ball.position.y = F1 + 0.1 + k * 0.8; s2.position.y = F1 + 0.08 * k; });
} else {                                                                           // 兒童房：一個在下舖看書、一個在地毯堆積木
  sit(-3.45, F2f + 0.45, 4.4, SON1, 0.95, Math.PI / 2); screen(-3.1, F2f + 1.0, 4.4, Math.PI / 2);
  const s2 = sit(-1.8, F2f + 0.0, 3.5, SON2, 0.9, Math.PI);
  for (let i = 0; i < 5; i++) IN(box(0.12, 0.12, 0.12), iMat(['#EF4444', '#3B82F6', '#F59E0B', '#10B981', '#A855F7'][i], 0.6), [-1.8 + (i % 3 - 1) * 0.15, F2f + 0.06 + Math.floor(i / 3) * 0.12, 3.05]);
  anim.push(t => { s2.rotation.x = 0.15 + 0.08 * Math.sin(t * 1.5); });
}

// ===== 家中微電網：配電盤 → 各房間電器，金色 = 再生能源 (太陽能/電池)，紅色 = 台電 =====
// (綠色已代表屋頂太陽能管線，室內改用暖金色，與紅色台電電力明顯區隔)
const greenShare = D.load > 0.02 ? Math.min(1, Math.max(0, 1 - Math.max(D.grid, 0) / D.load)) : 1;
const G_COL = new THREE.Color('#FBBF24'), R_COL = new THREE.Color('#EF4444');
const wireCol = R_COL.clone().lerp(G_COL, greenShare);
const hub = P.panelIn || V(W / 2 - 0.25, F1 + 2.6, -3.7);
// 配電盤先往屋內走到中央分電點 (避開外牆上的逆變器與綠色太陽能管線)，再垂直下到各樓板，沿中央走道分流到各電器
// to = 插座端點；base = 電器在地板上的接點 (x, z)
const CX = 0.7;
const junction = V(CX, hub.y, hub.z);
const loads = [
  { on: true, base: [-0.2, -4.55], to: V(-0.2, F1 + 0.35, -4.62) },                    // 冰箱 (常時)
  { on: true, base: [-1.2, -0.8], to: V(-1.2, F1 + 1.25, -0.8) },                      // 智慧能源面板、Wi-Fi 路由器 (常時)
  { on: ACT.kitchen && !SLEEP, base: [2.1, -4.8], to: V(2.1, F1 + 0.95, -4.8) },       // 廚房流理台 (排油煙機、電鍋、氣炸鍋)
  { on: ACT.laundry, base: [-1.75, -4.65], to: V(-1.75, F1 + 0.3, -4.65) },            // 洗衣機、烘衣機
  { on: ACT.tv && !SLEEP, base: [-2.2, 1.9], to: V(-2.2, F1 + 0.42, 1.9) },            // 電視櫃
  { on: ACT.lights, base: [-0.9, 5.0], to: V(-0.9, F1 + 1.45, 5.0) },                  // 客廳立燈
  { on: ACT.lights, base: [2.5, 3.3], to: V(2.5, F1 + 0.78, 3.3) },                    // 餐桌 (吊燈)
  { on: ACT.vacuum, base: [0.2, 5.3], to: V(0.2, F1 + 0.12, 5.3) },                    // 掃地機器人充電座
  { on: ACT.heater, base: [2.7, -DP / 2 + 0.2], to: V(2.7, F2f + 0.2, -DP / 2), up: true },  // 2F 後陽台儲熱式熱水器 (到後牆，屋外段另外畫)
  { on: true, base: [3.7, 1.5], to: V(3.7, F2f + 0.3, 1.5), up: true },               // 2F 空氣清淨機 (常時)
  { on: ACT.dehumid, base: [-0.4, 5.0], to: V(-0.4, F2f + 0.3, 5.0), up: true },       // 2F 除濕機
  { on: ACT.study && !SLEEP, base: [-0.65, 1.35], to: V(-0.65, F2f + 0.78, 1.35), up: true },  // 兒童房書桌檯燈
  { on: ACT.lights, base: [3.65, 2.75], to: V(3.65, F2f + 0.45, 2.75), up: true },     // 主臥床頭燈
];
const wireMat = std(wireCol.getStyle(), 0.4, 0, { emissive: wireCol, emissiveIntensity: 0.8 });
// 未使用中的分路只淡淡顯示
const offMat = std('#CBD5E1', 0.6, 0, { transparent: true, opacity: 0.12, depthWrite: false });
const glowT = glowTex0();
const dotMats = [G_COL, R_COL].map(c => new THREE.SpriteMaterial({ map: glowT, color: c, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
const totalOn = loads.filter(l => l.on).length || 1;
loads.forEach((L, k) => {
  const fy = (L.up ? F2f : F1) + 0.015 + k * 0.002;      // 貼地，每條略微錯開避免重疊閃爍
  const sx = CX - (k % 4) * 0.05;                          // 中央走道的走線並排
  const pts = [hub, junction, V(sx, hub.y, hub.z), V(sx, fy, hub.z), V(sx, fy, L.base[1]), V(L.base[0], fy, L.base[1]), L.to];
  const curve = poly(pts.filter((p, i) => i === 0 || p.distanceTo(pts[i - 1]) > 0.01));
  IN(new THREE.TubeGeometry(curve, Math.ceil(curve.getLength() * 12), 0.011, 6), L.on ? wireMat : offMat);
  if (!L.on) return;
  const len = curve.getLength(), n = Math.max(6, Math.round(len / 0.2));
  const speed = 0.5 + Math.min(D.load / totalOn, 2) * 0.6;
  for (let i = 0; i < n; i++) {
    // 依再生能源比例決定每顆光點顏色 (例如 70% 金、30% 紅)
    const sp = new THREE.Sprite(dotMats[((i * 0.618) % 1) < greenShare ? 0 : 1]); interior.add(sp);
    anim.push(t => {
      const u = (i / n + t * speed / len) % 1;
      curve.getPointAt(u, sp.position);
      sp.scale.setScalar(0.11 * Math.min(1, u / 0.06, (1 - u) / 0.06));
    });
  }
});
// 主幹線：配電盤穿牆進屋到中央分電點，比分路粗，接上屋外逆變器 → 配電盤那條線
if (P.panelOut) {
  const trunk = poly([...P.panelOut, hub, junction]);
  const len = trunk.getLength(), n = Math.round(len / 0.18);
  IN(new THREE.TubeGeometry(trunk, 40, 0.024, 8), wireMat);
  for (let i = 0; i < n; i++) {
    const sp = new THREE.Sprite(dotMats[((i * 0.618) % 1) < greenShare ? 0 : 1]); interior.add(sp);
    anim.push(t => {
      const u = (i / n + t * (0.6 + Math.min(D.load, 6) * 0.15) / len) % 1;
      trunk.getPointAt(u, sp.position); sp.scale.setScalar(0.17);
    });
  }
}
// 熱水器電源線 (屋外段，外觀模式也看得到)：從後牆穿出，沿陽台地面接到熱水器側邊的接線盒
{
  const y0 = F2f + 0.03, zw = -DP / 2 - 0.04;
  const cable = poly([V(2.7, F2f + 0.2, zw), V(2.7, y0, zw), V(2.7, y0, WH[2]), V(WH[0] - 0.36, y0, WH[2]), V(WH[0] - 0.36, F2f + 0.35, WH[2])]);
  mesh(new THREE.TubeGeometry(cable, 40, 0.014, 6), ACT.heater ? wireMat : M.dark, null, null, scene, false);
  mesh(box(0.06, 0.14, 0.12), MI.white, [WH[0] - 0.39, F2f + 0.4, WH[2]], null, scene, false);
  if (ACT.heater) {
    const len = cable.getLength(), n = Math.round(len / 0.16);
    for (let i = 0; i < n; i++) {
      const sp = new THREE.Sprite(dotMats[((i * 0.618) % 1) < greenShare ? 0 : 1]); scene.add(sp);
      anim.push(t => { cable.getPointAt((i / n + t * 0.9 / len) % 1, sp.position); sp.scale.setScalar(0.12); });
    }
  }
}
for (const [p, r] of [[hub, 0.05], [junction, 0.08]])   // 配電盤入口與中央分電點
  IN(new THREE.SphereGeometry(r, 16, 12), std(wireCol.getStyle(), 0.3, 0, { emissive: wireCol, emissiveIntensity: 2 }), p.toArray());

// 透視模式：房屋外殼半透明
const SHELL = [M.wall, M.slat, M.roof, M.trim, M.frame, M.glass, M.panel, M.alu, M.wood];
function setXray(on) {
  for (const m of SHELL) { m.transparent = on; m.opacity = on ? (m === M.panel ? 0.55 : m === M.roof ? 0.22 : 0.16) : 1; m.depthWrite = !on; m.needsUpdate = true; }
  interior.visible = on;
  document.querySelectorAll('.mode button').forEach(b => b.classList.toggle('sel', (b.dataset.m === 'x') === on));
  try { localStorage.setItem('pf_xray', on ? '1' : '0'); } catch (e) {}
}
let xray = true;
try { xray = localStorage.getItem('pf_xray') !== '0'; } catch (e) {}
// 透視模式預設鏡頭拉近，看得到家人
if (xray) camera.position.lerp(controls.target, 0.25);
const modeEl = document.createElement('div'); modeEl.className = 'mode';
modeEl.innerHTML = '<button data-m="o" title="外觀">🏠</button><button data-m="x" title="透視">🔍</button>';
wrap.appendChild(modeEl);
modeEl.querySelectorAll('button').forEach(b => b.onclick = () => setXray(b.dataset.m === 'x'));

// ---------- 能源流 ----------
const C = { pv: '#10B981', load: '#F97316', bat: '#A78BFA', grid: '#38BDF8' };
const flows = [
  { kw: D.pv, color: C.pv, rev: false, curve: P.pv },
  { kw: D.load, color: C.load, rev: false, curve: P.load },
  { kw: Math.abs(D.batt), color: C.bat, rev: D.batt < 0, curve: P.bat },
  { kw: Math.abs(D.grid), color: C.grid, rev: D.grid < 0, curve: P.grid },
];

const glowTex = glowTex0();
function glowTex0() { if (glowTex0.t) return glowTex0.t; return glowTex0.t = canvasTex(64, 64, (g, w, h) => {
  const r = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(0.25, 'rgba(255,255,255,0.75)');
  r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, w, h);
}); }

for (const f of flows) {
  const active = f.kw >= 0.02;
  const len = f.curve.getLength();
  mesh(new THREE.TubeGeometry(f.curve, Math.ceil(len * 20), 0.045, 8), active
    ? std(f.color, 0.45, 0.2, { emissive: new THREE.Color(f.color), emissiveIntensity: 0.25 })
    : M.track, null, null, scene, false);
  f.dots = [];
  if (!active) continue;
  // 方向箭頭：沿管線每約 0.5 m 一個圓錐，跟著電流流動並指向電流方向
  const arrowMat = new THREE.MeshStandardMaterial({ color: f.color, emissive: new THREE.Color(f.color), emissiveIntensity: 0.9, roughness: 0.4 });
  // 箭頭大小一致；短管線 (如逆變器→電池) 間距縮為約 0.25 m，才放得下兩個以上
  // 短管線 (逆變器→電池) 箭頭放大，才看得清楚
  const arrowGeo = len < 1.5 ? new THREE.ConeGeometry(0.125, 0.285, 16) : new THREE.ConeGeometry(0.11, 0.26, 16);
  const na = Math.max(1, Math.round(len / (len < 1.5 ? 0.3 : 0.5)));
  f.arrows = [];
  for (let i = 0; i < na; i++) {
    const cone = new THREE.Mesh(arrowGeo, arrowMat);
    scene.add(cone);
    f.arrows.push({ m: cone, phase: (i + 0.5) / na });  // 位置與方向在動畫迴圈中隨電流移動
  }
  f.len = len;
  f.speed = 0.2 + Math.min(f.kw, 10) * 0.05;   // 每秒移動距離 (m)，緩慢流動較有質感
  // 所有管線都用密集的小光點，看起來像連續水流
  const n = Math.max(6, Math.round(len / 0.17));
  f.size = 0.28; f.core = 0.6;
  const sMat = new THREE.SpriteMaterial({ map: glowTex, color: f.color, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true });
  const cMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(f.color).lerp(new THREE.Color('#ffffff'), 0.55) });
  for (let i = 0; i < n; i++) {
    const s = new THREE.Sprite(sMat); s.scale.setScalar(f.size); scene.add(s);
    const c = new THREE.Mesh(new THREE.SphereGeometry(0.055, 12, 8), cMat); scene.add(c);
    f.dots.push({ s, c, phase: i / n });
  }
}

// ---------- 標籤 ----------
function card(label, kw, color, extra, anchor, foot = '') {
  const el = document.createElement('div'); el.className = 'card';
  el.innerHTML = `<div class="v">${kw.toFixed(2)}<small>kW</small>${extra || ''}</div><div class="t"><span class="dot" style="background:${color};box-shadow:0 0 8px ${color}"></span>${label}</div>${foot}`;
  wrap.appendChild(el);
  return { el, anchor: V(...anchor) };
}
// 光伏卡片下方列出三組功率，顏色對應屋頂外框
const stringRows = (D.strings || []).length ? '<div class="strings">' + D.strings.map((kw, i) =>
  `<span><i style="background:${STRING_COLORS[i]}"></i>PV${i + 1}${['西', '中', '東'][i]} <b>${kw.toFixed(2)}</b></span>`).join('') + '</div>' : '';
const battLabel = D.batt > 0.02 ? '電池充電' : (D.batt < -0.02 ? '電池放電' : '電池待機');
const gridLabel = D.grid > 0.02 ? '電網購電' : (D.grid < -0.02 ? '賣電至電網' : '電網');
const cards = [
  card('光伏', D.pv, C.pv, '', [0.4, TOP + RISE + 1.4, 1.6], stringRows),
  card('載入 (家中負載)', D.load, C.load, '', [-5.0, Y0 + 0.1, DP / 2 + 1.2]),
  card(battLabel, Math.abs(D.batt), C.bat, `<span style="color:${C.bat};font-size:16px;margin-left:10px">${D.soc}%</span>`, P.batCard),
  card(gridLabel, Math.abs(D.grid), C.grid, '', P.gridCard,
       D.grid_today ? `<div class="strings"><span>今日購電 <b>${D.grid_today.buy.toFixed(1)}</b> kWh</span>`
         + (D.grid_today.sell > 0.05 ? `<span>賣電 <b>${D.grid_today.sell.toFixed(1)}</b></span>` : '') + '</div>' : ''),
];

// ---------- 迴圈 ----------
function resize() {
  const w = wrap.clientWidth, h = wrap.clientHeight;
  renderer.setSize(w, h); camera.aspect = w / h;
  camera.fov = w < 600 ? 42 : 30;
  camera.updateProjectionMatrix();
}
window.addEventListener('resize', resize); resize();

const clock = new THREE.Clock();
const ARROW_SPEED = 2.5;
const tmp = new THREE.Vector3(), tan = new THREE.Vector3(), UP = new THREE.Vector3(0, 1, 0);
function tick() {
  const t = clock.getElapsedTime();
  for (const f of flows) for (const d of f.dots) {
    let u = (d.phase + t * f.speed / f.len) % 1;
    if (f.rev) u = 1 - u;
    f.curve.getPointAt(u, tmp);
    d.s.position.copy(tmp); d.c.position.copy(tmp);
    // 兩端淡入淡出，光點不會在端點突然出現或消失 (避免看起來在跳)
    const fade = Math.min(1, u / 0.12, (1 - u) / 0.12);
    d.s.scale.setScalar(f.size * fade); d.c.scale.setScalar(f.core * fade);
  }
  // 箭頭與光點同速沿管線移動，並轉向電流方向
  for (const f of flows) for (const a of f.arrows || []) {
    let u = (a.phase + t * f.speed * ARROW_SPEED / f.len) % 1;  // 箭頭比光點快，方向感更明顯
    if (f.rev) u = 1 - u;
    f.curve.getPointAt(u, tmp);
    a.m.position.copy(tmp);
    tan.copy(f.curve.getTangentAt(u)).normalize();
    if (f.rev) tan.negate();
    a.m.quaternion.setFromUnitVectors(UP, tan);
    a.m.scale.setScalar(Math.min(1, u / 0.1, (1 - u) / 0.1));
  }
  for (const a of anim) a(t);
  controls.update();
  renderer.render(scene, camera);
  const w = wrap.clientWidth, h = wrap.clientHeight;
  for (const c of cards) {
    tmp.copy(c.anchor).project(camera);
    const x = (tmp.x * 0.5 + 0.5) * w, y = (-tmp.y * 0.5 + 0.5) * h;
    const cw = c.el.offsetWidth, ch = c.el.offsetHeight;
    const cx = Math.min(Math.max(x - cw / 2, 8), w - cw - 8), cy = Math.min(Math.max(y - ch / 2, 8), h - ch - 8);
    c.el.style.transform = `translate(${cx}px, ${cy}px)`;
  }
  requestAnimationFrame(tick);
}
setXray(xray);
tick();
</script>
<script>
// 瀏覽器不支援 WebGL 時的提示
setTimeout(() => { if (!document.querySelector('#wrap canvas')) {
  const d = document.createElement('div'); d.className = 'fallback'; d.textContent = '3D 畫面載入失敗，請確認瀏覽器支援 WebGL';
  document.getElementById('wrap').appendChild(d); } }, 6000);
</script>
</body></html>"""


def power_flow_html(pv, load, batt, soc, grid, night=False, layout="front", strings=None, grid_today=None, hour=12.0, weekend=False):
    """pv/load: kW；batt: kW (+充電/−放電)；grid: kW (+買電/−賣電)；strings: [PV1, PV2, PV3] kW；grid_today: {"buy","sell"} 今日買賣電 kWh。"""
    data = {"pv": round(pv, 3), "load": round(load, 3), "batt": round(batt, 3),
            "soc": int(soc), "grid": round(grid, 3), "night": bool(night), "layout": layout,
            "strings": [round(x, 3) for x in strings] if strings else [],
            "grid_today": grid_today, "hour": round(hour, 2), "weekend": bool(weekend)}
    sky = ("linear-gradient(180deg,#070B18 0%,#111a33 55%,#1E293B 100%)" if night
           else "linear-gradient(180deg,#5D8FC4 0%,#A9C7E3 55%,#D7E3EE 100%)")
    return (_TEMPLATE.replace("__DATA__", json.dumps(data))
            .replace("__SKY__", sky).replace("__STARS__", "1" if night else "0"))
