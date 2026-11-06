import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { STLLoader } from "three/addons/loaders/STLLoader.js";

const loader = new STLLoader();
const cache = new Map();

function load(url) {
  if (!cache.has(url)) {
    cache.set(
      url,
      new Promise((resolve, reject) =>
        loader.load(url, (g) => { g.computeVertexNormals(); resolve(g); }, undefined, reject)
      )
    );
  }
  return cache.get(url);
}

export class Viewer {
  constructor(el) {
    this.el = el;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 1, 5000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xb8b2a6, 1.6));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(200, 400, 300);
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0xffffff, 0.6);
    rim.position.set(-300, 100, -200);
    this.scene.add(rim);
    this.root = new THREE.Group();
    this.scene.add(this.root);
    this.ring = null;
    new ResizeObserver(() => this.resize()).observe(el);
    this.resize();
    const tick = () => {
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      requestAnimationFrame(tick);
    };
    tick();
  }

  resize() {
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  clear() {
    this.root.clear();
    this.root.rotation.set(0, 0, 0);
    this.ring = null;
  }

  frame(obj, dir = new THREE.Vector3(0.9, 0.55, 1.1)) {
    const box = new THREE.Box3().setFromObject(obj);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const r = size.length() / 2;
    const dist = r / Math.sin((this.camera.fov * Math.PI) / 360) * 0.95;
    this.camera.position.copy(center).add(dir.normalize().multiplyScalar(dist));
    this.camera.near = dist / 50;
    this.camera.far = dist * 20;
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
  }

  material(color, opts = {}) {
    return new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.02, ...opts });
  }

  async showArm({ arm, gauntlet, fit_ring, hand = [] }, color) {
    this.clear();
    const [ga, gg, gr, ...gh] = await Promise.all([load(arm), load(gauntlet), load(fit_ring), ...hand.map((h) => load(h.url))]);
    const group = new THREE.Group();
    const armMesh = new THREE.Mesh(ga, this.material(0xe2ad8c, { transparent: true, opacity: 0.42, depthWrite: false, roughness: 0.8 }));
    const gMesh = new THREE.Mesh(gg, this.material(color));
    this.ring = new THREE.Mesh(gr, this.material(color, { transparent: true, opacity: 0.9 }));
    this.ring.visible = false;
    const device = new THREE.Group();
    device.add(gMesh);
    gh.forEach((g, i) => device.add(new THREE.Mesh(g, this.material(hand[i].color))));
    group.add(device, armMesh, this.ring);
    group.rotation.y = Math.PI / 2; // arm axis (z) -> screen x
    this.root.add(group);
    this.root.updateMatrixWorld(true);
    // 10 mm grid just under the device, with 50 mm major lines
    const box = new THREE.Box3().setFromObject(device);
    const c = box.getCenter(new THREE.Vector3());
    const span = Math.ceil(Math.max(box.max.x - box.min.x, box.max.z - box.min.z) / 50) * 50 + 100;
    const minor = new THREE.GridHelper(span, span / 10, 0xcfcac0, 0xe3dfd7);
    const major = new THREE.GridHelper(span, span / 50, 0x9d978c, 0x9d978c);
    for (const g of [minor, major]) {
      g.position.set(Math.round(c.x / 10) * 10, box.min.y - 6, Math.round(c.z / 10) * 10);
      g.material.transparent = true;
      g.material.opacity = g === major ? 0.55 : 0.7;
      this.root.add(g);
    }
    major.position.y += 0.05;
    this.frame(device, new THREE.Vector3(0.3, 0.75, 1.0));
  }

  async showDemo(parts) {
    this.clear();
    const geoms = await Promise.all(parts.map((p) => load(p.url)));
    const device = new THREE.Group();
    geoms.forEach((g, i) => device.add(new THREE.Mesh(g, this.material(parts[i].color))));
    const pivot = new THREE.Group();
    device.rotation.y = Math.PI / 2;
    pivot.add(device);
    this.root.add(pivot);
    this.root.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(pivot);
    const c = box.getCenter(new THREE.Vector3());
    device.position.sub(c); // spin around the middle of the device
    this.root.updateMatrixWorld(true);
    const b2 = new THREE.Box3().setFromObject(pivot);
    const grid = new THREE.GridHelper(400, 40, 0xd4cfc5, 0xe7e3db);
    grid.position.y = b2.min.y - 8;
    grid.material.transparent = true;
    grid.material.opacity = 0.8;
    this.root.add(grid);
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 1.6;
    this.controls.enableZoom = false;
    this.controls.enablePan = false;
    this.renderer.domElement.addEventListener("pointerdown", () => (this.controls.autoRotate = false), { once: true });
    this.frame(pivot, new THREE.Vector3(0.6, 0.5, 1.0));
    this.camera.position.multiplyScalar(1.0);
  }

  setRing(on) {
    if (this.ring) this.ring.visible = on;
  }

  async showPlate(items, bed) {
    this.clear();
    const group = new THREE.Group();
    const plate = new THREE.Mesh(
      new THREE.BoxGeometry(bed[0], bed[1], 2),
      new THREE.MeshStandardMaterial({ color: 0x2a2a30, roughness: 0.9 })
    );
    plate.position.set(bed[0] / 2, bed[1] / 2, -1.05);
    group.add(plate);
    const grid = new THREE.GridHelper(bed[0], bed[0] / 20, 0x55555f, 0x3c3c44);
    grid.rotation.x = Math.PI / 2;
    grid.position.set(bed[0] / 2, bed[1] / 2, 0.05);
    group.add(grid);
    const geoms = await Promise.all(items.map((it) => load(it.file)));
    items.forEach((it, i) => {
      const m = new THREE.Mesh(geoms[i], this.material(it.color));
      m.position.set(it.offset[0], it.offset[1], it.offset[2]);
      group.add(m);
    });
    group.rotation.x = -Math.PI / 2; // print z -> up
    this.root.add(group);
    this.frame(group, new THREE.Vector3(0.2, 1.1, 0.9));
  }
}
