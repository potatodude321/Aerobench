/**
 * Imperative three.js viewer (z-up, CFD frame: x downstream, y vehicle right, z up).
 *
 * Layers: geometry (vehicle parts), overlays (tunnel, refinement volumes, wheels, seed
 * point) and results (ParaView-extracted surfaces, slices, iso-surfaces, streamlines).
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Abg } from "./abg";
import { PART_COLORS, sampleColormap } from "./colormaps";

export interface CameraState {
  position: [number, number, number];
  target: [number, number, number];
  up: [number, number, number];
  ortho?: boolean;
}

export interface ResultStyle {
  colormap: string;
  range: [number, number];
  opacity?: number;
  visible?: boolean;
  lineWidth?: number;
}

interface ResultEntry {
  abg: Abg;
  object: THREE.Object3D;
  style: ResultStyle;
}

export class Viewer {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  private triadScene = new THREE.Scene();
  private triadCam = new THREE.OrthographicCamera(-1.6, 1.6, 1.6, -1.6, 0.1, 10);
  private geometry = new THREE.Group();
  private overlays = new THREE.Group();
  private results = new THREE.Group();
  private resultMap = new Map<string, ResultEntry>();
  private raf = 0;
  private dirty = true;
  private ro: ResizeObserver;
  private lastCamEmit = 0;
  onCameraChange?: (c: CameraState) => void;
  private suppressEmit = false;
  partMeshes: { name: string; mesh: THREE.Mesh }[] = [];

  constructor(private host: HTMLElement, background = 0x14171d) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(background, 1);
    this.renderer.autoClear = false;
    host.appendChild(this.renderer.domElement);
    this.renderer.domElement.style.display = "block";
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 5000);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(-6, -7, 4);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.15;
    this.controls.screenSpacePanning = true;
    this.controls.addEventListener("change", () => {
      this.dirty = true;
      this.emitCamera();
    });
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.4));
    const dir = new THREE.DirectionalLight(0xffffff, 1.2);
    dir.position.set(-1, -1, 2);
    this.camera.add(dir);
    this.scene.add(this.camera);
    this.scene.add(this.geometry, this.overlays, this.results);
    this.buildTriad();
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(host);
    this.resize();
    const loop = () => {
      this.raf = requestAnimationFrame(loop);
      const changed = this.controls.update();
      if (changed || this.dirty) {
        this.dirty = false;
        this.render();
      }
    };
    loop();
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.ro.disconnect();
    this.controls.dispose();
    this.clearGroup(this.geometry);
    this.clearGroup(this.overlays);
    this.clearGroup(this.results);
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }

  private emitCamera(): void {
    if (this.suppressEmit || !this.onCameraChange) return;
    const now = performance.now();
    if (now - this.lastCamEmit < 30) return;
    this.lastCamEmit = now;
    this.onCameraChange(this.getCamera());
  }

  private resize(): void {
    const w = Math.max(1, this.host.clientWidth);
    const h = Math.max(1, this.host.clientHeight);
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = w + "px";
    this.renderer.domElement.style.height = h + "px";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.dirty = true;
  }

  private render(): void {
    const r = this.renderer;
    const w = this.host.clientWidth;
    const h = this.host.clientHeight;
    r.setViewport(0, 0, w, h);
    r.setScissorTest(false);
    r.clear();
    r.render(this.scene, this.camera);
    // orientation triad (bottom-left)
    const s = 90;
    r.setViewport(8, 8, s, s);
    r.setScissor(8, 8, s, s);
    r.setScissorTest(true);
    r.clearDepth();
    const d = new THREE.Vector3().subVectors(this.camera.position, this.controls.target).normalize().multiplyScalar(4);
    this.triadCam.position.copy(d);
    this.triadCam.up.copy(this.camera.up);
    this.triadCam.lookAt(0, 0, 0);
    r.render(this.triadScene, this.triadCam);
    r.setScissorTest(false);
  }

  requestRender(): void {
    this.dirty = true;
  }

  private buildTriad(): void {
    const mk = (dir: THREE.Vector3, color: number, label: string) => {
      const arrow = new THREE.ArrowHelper(dir, new THREE.Vector3(), 1.0, color, 0.28, 0.16);
      this.triadScene.add(arrow);
      const sp = this.textSprite(label, color);
      sp.position.copy(dir.clone().multiplyScalar(1.35));
      sp.scale.set(0.5, 0.5, 1);
      this.triadScene.add(sp);
    };
    mk(new THREE.Vector3(1, 0, 0), 0xff6b6b, "x");
    mk(new THREE.Vector3(0, 1, 0), 0x51cf66, "y");
    mk(new THREE.Vector3(0, 0, 1), 0x4dabf7, "z");
  }

  textSprite(text: string, color = 0xffffff, size = 64): THREE.Sprite {
    const c = document.createElement("canvas");
    c.width = c.height = 128;
    const g = c.getContext("2d")!;
    g.font = `bold ${size}px sans-serif`;
    g.fillStyle = "#" + color.toString(16).padStart(6, "0");
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, 64, 64);
    const tex = new THREE.CanvasTexture(c);
    return new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
  }

  private clearGroup(g: THREE.Group): void {
    for (const c of [...g.children]) {
      g.remove(c);
      c.traverse((o: any) => {
        o.geometry?.dispose?.();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m: any) => m.dispose?.());
      });
    }
    this.dirty = true;
  }

  // ------------------------------------------------------------------ vehicle geometry
  setGeometry(abg: Abg | null, opts: { opacity?: number; colorByPart?: boolean; hidden?: string[] } = {}): void {
    this.clearGroup(this.geometry);
    this.partMeshes = [];
    if (!abg || !abg.indices) return;
    const names = abg.header.part_names || ["part"];
    const tri = abg.indices;
    const nTri = tri.length / 3;
    // split triangles by part so parts can be hidden / highlighted individually
    const byPart: number[][] = names.map(() => []);
    for (let t = 0; t < nTri; t++) byPart[abg.partIds ? abg.partIds[t] : 0].push(t);
    names.forEach((name, pi) => {
      const tris = byPart[pi];
      if (!tris.length) return;
      const pos = new Float32Array(tris.length * 9);
      let k = 0;
      for (const t of tris) {
        for (let v = 0; v < 3; v++) {
          const idx = tri[3 * t + v] * 3;
          pos[k++] = abg.positions[idx];
          pos[k++] = abg.positions[idx + 1];
          pos[k++] = abg.positions[idx + 2];
        }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
      g.computeVertexNormals();
      const color = opts.colorByPart === false ? 0xb8c0cc : name.startsWith("wheel") ? 0x3b3f46 : PART_COLORS[pi % PART_COLORS.length];
      const m = new THREE.MeshStandardMaterial({
        color,
        metalness: 0.15,
        roughness: 0.55,
        transparent: (opts.opacity ?? 1) < 1,
        opacity: opts.opacity ?? 1,
        side: THREE.DoubleSide,
        flatShading: false,
      });
      const mesh = new THREE.Mesh(g, m);
      mesh.name = name;
      mesh.visible = !(opts.hidden || []).includes(name);
      this.geometry.add(mesh);
      this.partMeshes.push({ name, mesh });
    });
    this.dirty = true;
  }

  setGeometryVisible(v: boolean): void {
    this.geometry.visible = v;
    this.dirty = true;
  }

  setGeometryOpacity(o: number): void {
    for (const { mesh } of this.partMeshes) {
      const m = mesh.material as THREE.MeshStandardMaterial;
      m.opacity = o;
      m.transparent = o < 1;
      m.depthWrite = o >= 1;
    }
    this.dirty = true;
  }

  highlightPart(name: string | null): void {
    for (const { name: n, mesh } of this.partMeshes) {
      const m = mesh.material as THREE.MeshStandardMaterial;
      m.emissive = new THREE.Color(n === name ? 0x335577 : 0x000000);
    }
    this.dirty = true;
  }

  // ------------------------------------------------------------------ overlays
  clearOverlays(): void {
    this.clearGroup(this.overlays);
  }

  addBox(min: number[], max: number[], color = 0x4dabf7, opacity = 0.08, edges = true, name = "box"): void {
    const size = new THREE.Vector3(max[0] - min[0], max[1] - min[1], max[2] - min[2]);
    const g = new THREE.BoxGeometry(size.x, size.y, size.z);
    const centre = new THREE.Vector3((max[0] + min[0]) / 2, (max[1] + min[1]) / 2, (max[2] + min[2]) / 2);
    if (opacity > 0) {
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide }));
      m.position.copy(centre);
      m.name = name;
      this.overlays.add(m);
    }
    if (edges) {
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.8 }));
      e.position.copy(centre);
      this.overlays.add(e);
    }
    this.dirty = true;
  }

  addRotatedBox(origin: number[], span: number[], e1: number[], e3: number[], color = 0x4dabf7, opacity = 0.08): void {
    const x = new THREE.Vector3(...(e1 as [number, number, number])).normalize();
    const z = new THREE.Vector3(...(e3 as [number, number, number])).normalize();
    const y = new THREE.Vector3().crossVectors(z, x);
    const basis = new THREE.Matrix4().makeBasis(x, y, z);
    const g = new THREE.BoxGeometry(span[0], span[1], span[2]);
    g.translate(span[0] / 2, span[1] / 2, span[2] / 2);
    const grp = new THREE.Group();
    grp.add(new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide })));
    grp.add(new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color })));
    grp.applyMatrix4(basis);
    grp.position.set(origin[0], origin[1], origin[2]);
    this.overlays.add(grp);
    this.dirty = true;
  }

  addCylinder(p1: number[], p2: number[], radius: number, color = 0xf59f00, opacity = 0.1): void {
    const a = new THREE.Vector3(...(p1 as [number, number, number]));
    const b = new THREE.Vector3(...(p2 as [number, number, number]));
    const len = a.distanceTo(b);
    const g = new THREE.CylinderGeometry(radius, radius, len, 40, 1, false);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide }));
    m.position.copy(a.clone().add(b).multiplyScalar(0.5));
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), b.clone().sub(a).normalize());
    this.overlays.add(m);
    const e = new THREE.LineSegments(new THREE.EdgesGeometry(g, 30), new THREE.LineBasicMaterial({ color }));
    e.position.copy(m.position);
    e.quaternion.copy(m.quaternion);
    this.overlays.add(e);
    this.dirty = true;
  }

  addGround(min: number[], max: number[], z: number): void {
    const sx = max[0] - min[0];
    const sy = max[1] - min[1];
    const g = new THREE.PlaneGeometry(sx, sy);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0x2b313b, transparent: true, opacity: 0.55, side: THREE.DoubleSide, depthWrite: false }));
    m.position.set((max[0] + min[0]) / 2, (max[1] + min[1]) / 2, z - 1e-4);
    this.overlays.add(m);
    const step = Math.pow(10, Math.floor(Math.log10(Math.max(sx, sy) / 10)));
    const grid = new THREE.GridHelper(Math.ceil(Math.max(sx, sy) / step) * step, Math.ceil(Math.max(sx, sy) / step), 0x3a4250, 0x262c35);
    grid.rotation.x = Math.PI / 2;
    grid.position.set((max[0] + min[0]) / 2, (max[1] + min[1]) / 2, z);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.5;
    this.overlays.add(grid);
    this.dirty = true;
  }

  addPoint(p: number[], color = 0xff922b, size = 0.05): void {
    const m = new THREE.Mesh(new THREE.SphereGeometry(size, 16, 12), new THREE.MeshBasicMaterial({ color }));
    m.position.set(p[0], p[1], p[2]);
    this.overlays.add(m);
    this.dirty = true;
  }

  addArrow(origin: number[], dir: number[], length: number, color = 0x4dabf7): void {
    const d = new THREE.Vector3(...(dir as [number, number, number])).normalize();
    const a = new THREE.ArrowHelper(d, new THREE.Vector3(...(origin as [number, number, number])), length, color, length * 0.18, length * 0.09);
    this.overlays.add(a);
    this.dirty = true;
  }

  addLabel(text: string, p: number[], color = 0xffffff, scale = 0.4): void {
    const s = this.textSprite(text, color, 40);
    s.position.set(p[0], p[1], p[2]);
    s.scale.set(scale, scale, 1);
    this.overlays.add(s);
    this.dirty = true;
  }

  // ------------------------------------------------------------------ results
  setResult(id: string, abg: Abg, style: ResultStyle): void {
    this.removeResult(id);
    let obj: THREE.Object3D;
    if (abg.lines) {
      // packed polylines: count, ids..., count, ids...
      const segs: number[] = [];
      const L = abg.lines;
      let i = 0;
      while (i < L.length) {
        const n = L[i++];
        for (let k = 0; k < n - 1; k++) segs.push(L[i + k], L[i + k + 1]);
        i += n;
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(abg.positions, 3));
      g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(abg.header.n_points * 3), 3));
      g.setIndex(segs);
      obj = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: (style.opacity ?? 1) < 1, opacity: style.opacity ?? 1 }));
    } else {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(abg.positions, 3));
      g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(abg.header.n_points * 3), 3));
      if (abg.indices) g.setIndex(new THREE.BufferAttribute(abg.indices, 1));
      g.computeVertexNormals();
      obj = new THREE.Mesh(
        g,
        new THREE.MeshStandardMaterial({
          vertexColors: true,
          side: THREE.DoubleSide,
          metalness: 0.0,
          roughness: 0.75,
          transparent: (style.opacity ?? 1) < 1,
          opacity: style.opacity ?? 1,
          polygonOffset: true,
          polygonOffsetFactor: 1,
          polygonOffsetUnits: 1,
        }),
      );
    }
    obj.name = id;
    obj.visible = style.visible ?? true;
    this.results.add(obj);
    this.resultMap.set(id, { abg, object: obj, style });
    this.recolor(id, style);
  }

  recolor(id: string, style: ResultStyle): void {
    const e = this.resultMap.get(id);
    if (!e) return;
    e.style = style;
    const geo = (e.object as THREE.Mesh).geometry as THREE.BufferGeometry;
    const col = geo.getAttribute("color") as THREE.BufferAttribute;
    const arr = col.array as Float32Array;
    const s = e.abg.scalars;
    const [lo, hi] = style.range;
    const span = hi - lo || 1;
    for (let i = 0; i < e.abg.header.n_points; i++) {
      const v = s ? s[i] : 0;
      if (!Number.isFinite(v)) {
        arr[3 * i] = arr[3 * i + 1] = arr[3 * i + 2] = 0.4;
      } else sampleColormap(style.colormap, (v - lo) / span, arr, 3 * i);
    }
    col.needsUpdate = true;
    const mat = (e.object as THREE.Mesh).material as THREE.Material;
    mat.opacity = style.opacity ?? 1;
    mat.transparent = (style.opacity ?? 1) < 1;
    e.object.visible = style.visible ?? true;
    this.dirty = true;
  }

  removeResult(id: string): void {
    const e = this.resultMap.get(id);
    if (!e) return;
    this.results.remove(e.object);
    e.object.traverse((o: any) => {
      o.geometry?.dispose?.();
      o.material?.dispose?.();
    });
    this.resultMap.delete(id);
    this.dirty = true;
  }

  clearResults(): void {
    for (const id of [...this.resultMap.keys()]) this.removeResult(id);
  }

  resultIds(): string[] {
    return [...this.resultMap.keys()];
  }

  // ------------------------------------------------------------------ camera
  bounds(): THREE.Box3 {
    const b = new THREE.Box3();
    b.expandByObject(this.geometry);
    if (b.isEmpty()) b.expandByObject(this.results);
    if (b.isEmpty()) b.expandByObject(this.overlays);
    if (b.isEmpty()) b.set(new THREE.Vector3(-1, -1, 0), new THREE.Vector3(1, 1, 1));
    return b;
  }

  view(kind: "iso" | "front" | "rear" | "side" | "top" | "bottom" | "iso_rear", box?: THREE.Box3): void {
    const b = box || this.bounds();
    const c = b.getCenter(new THREE.Vector3());
    const r = b.getSize(new THREE.Vector3()).length() * 0.5;
    const dist = r / Math.sin((this.camera.fov * Math.PI) / 360) * 1.05;
    const dirs: Record<string, [number, number, number]> = {
      iso: [-0.75, -0.9, 0.55],
      iso_rear: [0.8, -0.9, 0.5],
      front: [-1, 0, 0.0001],
      rear: [1, 0, 0.0001],
      side: [0, -1, 0.0001],
      top: [0.0001, 0, 1],
      bottom: [0.0001, 0, -1],
    };
    const d = new THREE.Vector3(...dirs[kind]).normalize();
    this.controls.target.copy(c);
    this.camera.position.copy(c.clone().add(d.multiplyScalar(dist)));
    this.camera.up.set(0, 0, 1);
    this.camera.near = Math.max(0.001, dist / 1000);
    this.camera.far = dist * 100;
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.dirty = true;
    this.emitCamera();
  }

  getCamera(): CameraState {
    const p = this.camera.position;
    const t = this.controls.target;
    const u = this.camera.up;
    return { position: [p.x, p.y, p.z], target: [t.x, t.y, t.z], up: [u.x, u.y, u.z] };
  }

  setCamera(c: CameraState): void {
    this.suppressEmit = true;
    this.camera.position.set(...c.position);
    this.controls.target.set(...c.target);
    this.camera.up.set(...c.up);
    this.camera.updateProjectionMatrix();
    this.controls.update();
    this.suppressEmit = false;
    this.dirty = true;
  }

  snapshot(): string {
    this.render();
    return this.renderer.domElement.toDataURL("image/png");
  }
}
