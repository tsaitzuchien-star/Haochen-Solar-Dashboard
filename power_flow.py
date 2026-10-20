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
function addBattery(g, x, y) {  // y 為電池中心高度
  mesh(new RoundedBoxGeometry(0.8, 1.4, 0.38, 4, 0.05), M.white, [x, y, 0.22], null, g);
  for (let i = 1; i < 4; i++) mesh(box(0.78, 0.012, 0.01), std('#CBD5E1', 0.6), [x, y - 0.7 + i * 0.35, 0.41], null, g, false);
  const h = 1.1 * Math.max(D.soc, 3) / 100;
  mesh(box(0.06, 1.1, 0.01), std('#CBD5E1', 0.6), [x - 0.28, y, 0.415], null, g, false);
  mesh(box(0.06, h, 0.012), glowMat('#8B5CF6', 1.6), [x - 0.28, y - 0.55 + h / 2, 0.42], null, g, false);
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
const XW = W / 2 + 0.07;   // 右側牆面外側
const ZW = DP / 2 + 0.07;  // 正面牆面外側
const pvDrop = [V(3.4, eaveY + 0.35, DP / 2 + 0.1), V(3.4, eaveY - 0.05, DP / 2 + OVER - 0.05)];
let P;  // 管線與標籤位置

if (LAYOUT === 'front') {
  // 正面只留門窗；右側牆後段 (木紋) 中央由前往後：逆變器 (電池在正下方) → 約 2 m 後為配電盤。
  // 太陽能沿二樓牆頂走到逆變器正上方後垂直往下；家用電由逆變器水平直達配電盤；台電由右後方電線桿架空接到二樓牆面再下到配電盤
  const S = frame([W / 2, 0, 0], Math.PI / 2);
  // 設備組置中於木紋牆：IX = 逆變器/電池、PX = 配電盤 (牆面局部座標，往後為正)
  const WOOD_C = DP / 4 - 0.3, IX = WOOD_C - 1.1, PX = WOOD_C + 1.1, LX = WOOD_C;
  addInverter(S, IX, Y0 + 2.5); addBattery(S, IX, Y0 + 0.7);
  addPanel(S, PX, Y0 + 2.5);
  const pole = [8.6, 0, -6.6]; addPole(pole);
  const service = at(S, PX, Y0 + 3.9, 0.07);
  if (NIGHT) {  // 設備區壁燈：照亮逆變器、電池與配電盤
    mesh(box(0.3, 0.08, 0.14), M.trim, at(S, LX, Y0 + 3.45, 0.07).toArray());
    mesh(box(0.26, 0.02, 0.1), glowMat('#FFE7B0', 3), at(S, LX, Y0 + 3.4, 0.08).toArray(), null, scene, false);
    const lamp = new THREE.PointLight('#FFE2A8', 9, 6, 2); lamp.position.copy(at(S, LX, Y0 + 3.2, 0.8)); scene.add(lamp);
  }
  const wire = sagWire(V(pole[0] - 0.6, Y0 + 7.0, pole[2] + 0.35), service, 1.0);
  P = {
    pv: poly([V(3.9, eaveY + 0.35, DP / 2 + 0.1), V(3.9, eaveY - 0.05, DP / 2 + OVER - 0.05), V(3.9, TOP - 0.3, ZW), V(XW, TOP - 0.3, ZW),
              at(S, IX, TOP - 0.3, 0.07), at(S, IX, Y0 + 2.5 + INV_H / 2 + 0.05, 0.07)]),
    load: poly([at(S, IX + INV_W / 2, Y0 + 2.5, 0.07), at(S, PX - 0.35, Y0 + 2.5, 0.07)]),
    bat: poly([at(S, IX, Y0 + 2.5 - INV_H / 2 - 0.05, 0.14), at(S, IX, Y0 + 1.4, 0.14)]),
    grid: chain(wire, poly([service, at(S, PX, Y0 + 2.98, 0.07)])),
    batCard: [7.6, Y0 + 0.2, -0.6], gridCard: [pole[0], Y0 + 8.0, pole[2]],
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

// ---------- 能源流 ----------
const C = { pv: '#10B981', load: '#F97316', bat: '#A78BFA', grid: '#38BDF8' };
const flows = [
  { kw: D.pv, color: C.pv, rev: false, curve: P.pv },
  { kw: D.load, color: C.load, rev: false, curve: P.load },
  { kw: Math.abs(D.batt), color: C.bat, rev: D.batt < 0, curve: P.bat },
  { kw: Math.abs(D.grid), color: C.grid, rev: D.grid < 0, curve: P.grid },
];

const glowTex = canvasTex(64, 64, (g, w, h) => {
  const r = g.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, w / 2);
  r.addColorStop(0, 'rgba(255,255,255,1)'); r.addColorStop(0.25, 'rgba(255,255,255,0.75)');
  r.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = r; g.fillRect(0, 0, w, h);
});

for (const f of flows) {
  const active = f.kw >= 0.02;
  const len = f.curve.getLength();
  mesh(new THREE.TubeGeometry(f.curve, Math.ceil(len * 20), 0.045, 8), active
    ? std(f.color, 0.45, 0.2, { emissive: new THREE.Color(f.color), emissiveIntensity: 0.25 })
    : M.track, null, null, scene, false);
  f.dots = [];
  if (!active) continue;
  f.len = len;
  f.speed = 0.2 + Math.min(f.kw, 10) * 0.05;   // 每秒移動距離 (m)，緩慢流動較有質感
  const n = Math.max(3, Math.round(len / 0.6));
  const sMat = new THREE.SpriteMaterial({ map: glowTex, color: f.color, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true });
  const cMat = new THREE.MeshBasicMaterial({ color: new THREE.Color(f.color).lerp(new THREE.Color('#ffffff'), 0.55) });
  for (let i = 0; i < n; i++) {
    const s = new THREE.Sprite(sMat); s.scale.setScalar(0.42); scene.add(s);
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
  card(gridLabel, Math.abs(D.grid), C.grid, '', P.gridCard),
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
const tmp = new THREE.Vector3();
function tick() {
  const t = clock.getElapsedTime();
  for (const f of flows) for (const d of f.dots) {
    let u = (d.phase + t * f.speed / f.len) % 1;
    if (f.rev) u = 1 - u;
    f.curve.getPointAt(u, tmp);
    d.s.position.copy(tmp); d.c.position.copy(tmp);
  }
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
tick();
</script>
<script>
// 瀏覽器不支援 WebGL 時的提示
setTimeout(() => { if (!document.querySelector('#wrap canvas')) {
  const d = document.createElement('div'); d.className = 'fallback'; d.textContent = '3D 畫面載入失敗，請確認瀏覽器支援 WebGL';
  document.getElementById('wrap').appendChild(d); } }, 6000);
</script>
</body></html>"""


def power_flow_html(pv, load, batt, soc, grid, night=False, layout="front", strings=None):
    """pv/load: kW；batt: kW (+充電/−放電)；grid: kW (+買電/−賣電)；strings: [PV1, PV2, PV3] kW。"""
    data = {"pv": round(pv, 3), "load": round(load, 3), "batt": round(batt, 3),
            "soc": int(soc), "grid": round(grid, 3), "night": bool(night), "layout": layout,
            "strings": [round(x, 3) for x in strings] if strings else []}
    sky = ("linear-gradient(180deg,#070B18 0%,#111a33 55%,#1E293B 100%)" if night
           else "linear-gradient(180deg,#5D8FC4 0%,#A9C7E3 55%,#D7E3EE 100%)")
    return (_TEMPLATE.replace("__DATA__", json.dumps(data))
            .replace("__SKY__", sky).replace("__STARS__", "1" if night else "0"))
