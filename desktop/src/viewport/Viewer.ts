/**
 * Imperative three.js viewer (z-up, CFD frame: x downstream, y vehicle right, z up).
 *
 * Layers: geometry (vehicle parts), overlays (tunnel, refinement volumes, wheels, seed
 * point) and results (ParaView-extracted surfaces, slices, iso-surfaces, streamlines).
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Abg } from "./abg";
import { LEVEL_COLORS, PART_COLORS, WHEEL_COLOR, sampleColormap } from "./colormaps";

export const VIEWPORT_BG = 0x111418;

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
  /** multiplies the scalars before colouring (morph blending) */
  scalarScale?: number;
}

export interface MeshLayerStyle {
  /** surface: fill only; edges: fill with cell edges; wireframe: edges only */
  mode: "surface" | "edges" | "wireframe";
  colorBy?: "solid" | "level";
  color?: number;
  wireColor?: number;
  visible?: boolean;
  /** cut away by the section plane (surfaces), unlike the cut itself */
  clip?: boolean;
}

interface MeshLayerEntry {
  group: THREE.Group;
  fill: THREE.Mesh | null;
  edges: THREE.LineSegments | null;
  style: MeshLayerStyle;
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
  private meshGroup = new THREE.Group();
  private resultMap = new Map<string, ResultEntry>();
  private meshLayers = new Map<string, MeshLayerEntry>();
  private raf = 0;
  private dirty = true;
  private ro: ResizeObserver;
  private lastCamEmit = 0;
  onCameraChange?: (c: CameraState) => void;
  private suppressEmit = false;
  /** One material per vehicle part (all parts share one indexed geometry). */
  parts: { name: string; material: THREE.MeshStandardMaterial }[] = [];
  private geometryBase: THREE.Box3 | null = null;
  /** Section plane of a mesh cut: surfaces on the camera's side of it are cut away. */
  private section: THREE.Plane | null = null;

  constructor(private host: HTMLElement, background = VIEWPORT_BG) {
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
    this.geometry.matrixAutoUpdate = false;
    this.scene.add(this.geometry, this.overlays, this.results, this.meshGroup);
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
    this.clearGroup(this.meshGroup);
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
    if (this.section && this.section.distanceToPoint(this.camera.position) > 0) this.section.negate();
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
    mk(new THREE.Vector3(1, 0, 0), 0xd98282, "x");
    mk(new THREE.Vector3(0, 1, 0), 0x86bf98, "y");
    mk(new THREE.Vector3(0, 0, 1), 0x8db7e0, "z");
  }

  /** A text label; the canvas widens to fit long text (``userData.aspect`` = width / height). */
  textSprite(text: string, color = 0xffffff, size = 64): THREE.Sprite {
    const c = document.createElement("canvas");
    const font = `600 ${size}px Barlow, "Segoe UI", sans-serif`;
    const probe = c.getContext("2d")!;
    probe.font = font;
    c.width = Math.max(128, Math.ceil(probe.measureText(text).width) + 24);
    c.height = 128;
    const g = c.getContext("2d")!; // resizing reset the context state
    g.font = font;
    g.fillStyle = "#" + color.toString(16).padStart(6, "0");
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, c.width / 2, 64);
    const tex = new THREE.CanvasTexture(c);
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true }));
    sp.userData.aspect = c.width / c.height;
    return sp;
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
  /**
   * Vehicle parts as ONE indexed geometry whose triangles are sorted by part (a counting sort
   * over typed arrays) with one draw group and material per part: no per-triangle JavaScript
   * objects, no normal computation (flat shading from screen-space derivatives). A 400k
   * triangle model is ready in a few milliseconds instead of freezing the window.
   */
  setGeometry(abg: Abg | null, opts: { opacity?: number; colorByPart?: boolean; hidden?: string[] } = {}): void {
    this.clearGroup(this.geometry);
    this.parts = [];
    this.geometry.matrix.identity();
    this.geometry.matrixWorldNeedsUpdate = true;
    this.geometryBase = null;
    if (!abg || !abg.indices) return;
    const names = abg.header.part_names?.length ? abg.header.part_names : ["part"];
    const src = abg.indices;
    const nTri = src.length / 3;
    const pids = abg.partIds;
    const nParts = names.length;
    const counts = new Uint32Array(nParts);
    for (let t = 0; t < nTri; t++) counts[pids ? Math.min(pids[t], nParts - 1) : 0]++;
    const starts = new Uint32Array(nParts);
    let acc = 0;
    for (let p = 0; p < nParts; p++) {
      starts[p] = acc;
      acc += counts[p];
    }
    const cursor = starts.slice();
    const sorted = new Uint32Array(src.length);
    for (let t = 0; t < nTri; t++) {
      const p = pids ? Math.min(pids[t], nParts - 1) : 0;
      const d = 3 * cursor[p]++;
      sorted[d] = src[3 * t];
      sorted[d + 1] = src[3 * t + 1];
      sorted[d + 2] = src[3 * t + 2];
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(abg.positions, 3));
    g.setIndex(new THREE.BufferAttribute(sorted, 1));
    const materials: THREE.MeshStandardMaterial[] = [];
    names.forEach((name, pi) => {
      const color = opts.colorByPart === false ? 0xaab2bd : name.startsWith("wheel") ? WHEEL_COLOR : PART_COLORS[pi % PART_COLORS.length];
      const m = new THREE.MeshStandardMaterial({
        color,
        metalness: 0.1,
        roughness: 0.62,
        transparent: (opts.opacity ?? 1) < 1,
        opacity: opts.opacity ?? 1,
        side: THREE.DoubleSide,
        flatShading: true,
      });
      m.visible = !(opts.hidden || []).includes(name);
      materials.push(m);
      if (counts[pi]) g.addGroup(3 * starts[pi], 3 * counts[pi], pi);
      this.parts.push({ name, material: m });
    });
    g.computeBoundingBox();
    g.computeBoundingSphere();
    const mesh = new THREE.Mesh(g, materials);
    mesh.name = "vehicle";
    this.geometry.add(mesh);
    this.geometryBase = g.boundingBox!.clone();
    if (this.section) this.applySection();
    this.dirty = true;
  }

  setGeometryVisible(v: boolean): void {
    this.geometry.visible = v;
    this.dirty = true;
  }

  setGeometryOpacity(o: number): void {
    for (const { material: m } of this.parts) {
      m.opacity = o;
      m.transparent = o < 1;
      m.depthWrite = o >= 1;
    }
    this.dirty = true;
  }

  setPartVisible(name: string, v: boolean): void {
    for (const p of this.parts) if (p.name === name) p.material.visible = v;
    this.dirty = true;
  }

  highlightPart(name: string | null): void {
    for (const { name: n, material: m } of this.parts) m.emissive = new THREE.Color(n === name ? 0x2c4a66 : 0x000000);
    this.dirty = true;
  }

  /**
   * Show the vehicle re-oriented/re-scaled at once, before the engine has recomputed the
   * posed geometry: ``linear`` maps the displayed (old) CFD frame to the new one; the result
   * is moved so its nose, centreline and lowest point stay where they were (the engine's
   * automatic placement does the same). ``null`` removes the preview transform.
   */
  previewGeometryTransform(linear: THREE.Matrix3 | null): void {
    const base = this.geometryBase;
    if (!linear || !base) {
      this.geometry.matrix.identity();
    } else {
      const m4 = new THREE.Matrix4().setFromMatrix3(linear);
      const box = base.clone().applyMatrix4(m4);
      const t = new THREE.Vector3(base.min.x - box.min.x, 0.5 * (base.min.y + base.max.y) - 0.5 * (box.min.y + box.max.y), base.min.z - box.min.z);
      this.geometry.matrix.copy(new THREE.Matrix4().makeTranslation(t.x, t.y, t.z).multiply(m4));
    }
    this.geometry.matrixWorldNeedsUpdate = true;
    this.dirty = true;
  }

  // ------------------------------------------------------------------ overlays
  clearOverlays(): void {
    this.clearGroup(this.overlays);
  }

  addBox(min: number[], max: number[], color = 0x8db7e0, opacity = 0.08, edges = true, name = "box"): void {
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

  addRotatedBox(origin: number[], span: number[], e1: number[], e3: number[], color = 0x8db7e0, opacity = 0.08): void {
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

  addCylinder(p1: number[], p2: number[], radius: number, color = 0x8db7e0, opacity = 0.1): void {
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
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0x1c2027, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false }));
    m.position.set((max[0] + min[0]) / 2, (max[1] + min[1]) / 2, z - 1e-4);
    this.overlays.add(m);
    const step = Math.pow(10, Math.floor(Math.log10(Math.max(sx, sy) / 10)));
    const grid = new THREE.GridHelper(Math.ceil(Math.max(sx, sy) / step) * step, Math.ceil(Math.max(sx, sy) / step), 0x2c323b, 0x23282f);
    grid.rotation.x = Math.PI / 2;
    grid.position.set((max[0] + min[0]) / 2, (max[1] + min[1]) / 2, z);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.6;
    this.overlays.add(grid);
    this.dirty = true;
  }

  addPoint(p: number[], color = 0xd6a24a, size = 0.05): void {
    const m = new THREE.Mesh(new THREE.SphereGeometry(size, 16, 12), new THREE.MeshBasicMaterial({ color }));
    m.position.set(p[0], p[1], p[2]);
    this.overlays.add(m);
    this.dirty = true;
  }

  addArrow(origin: number[], dir: number[], length: number, color = 0x8db7e0): void {
    const d = new THREE.Vector3(...(dir as [number, number, number])).normalize();
    const a = new THREE.ArrowHelper(d, new THREE.Vector3(...(origin as [number, number, number])), length, color, length * 0.18, length * 0.09);
    this.overlays.add(a);
    this.dirty = true;
  }

  addLabel(text: string, p: number[], color = 0xffffff, scale = 0.4, depthTest = false): void {
    const s = this.textSprite(text, color, 40);
    (s.material as THREE.SpriteMaterial).depthTest = depthTest;
    s.position.set(p[0], p[1], p[2]);
    s.scale.set(scale * (s.userData.aspect || 1), scale, 1);
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
      // a payload with displacement vectors is morphed in place: keep its base positions intact
      g.setAttribute("position", new THREE.BufferAttribute(abg.vectors ? Float32Array.from(abg.positions) : abg.positions, 3));
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
    const k = style.scalarScale ?? 1;
    for (let i = 0; i < e.abg.header.n_points; i++) {
      const v = s ? s[i] * k : 0;
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

  /**
   * Blend a result that carries per-vertex displacement vectors between its base shape (t = 0)
   * and the changed shape (t = 1); the colours follow the same fraction of the change.
   */
  morphResult(id: string, t: number): void {
    const e = this.resultMap.get(id);
    if (!e || !e.abg.vectors) return;
    const geo = (e.object as THREE.Mesh).geometry as THREE.BufferGeometry;
    const pos = geo.getAttribute("position") as THREE.BufferAttribute;
    const arr = pos.array as Float32Array;
    const base = e.abg.positions;
    const vec = e.abg.vectors;
    for (let i = 0; i < arr.length; i++) arr[i] = base[i] + t * vec[i];
    pos.needsUpdate = true;
    geo.computeVertexNormals();
    geo.computeBoundingSphere();
    this.recolor(id, { ...e.style, scalarScale: t });
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

  // ------------------------------------------------------------------ generated-mesh layers
  /**
   * A view of the generated mesh: polygons (fill) plus every cell edge (wireframe), sharing
   * one position buffer. Slices are coloured per cell by refinement level with the same
   * colours as the refinement boxes, so the two can be compared directly.
   */
  setMeshLayer(id: string, abg: Abg, style: MeshLayerStyle): void {
    this.removeMeshLayer(id);
    const grp = new THREE.Group();
    grp.name = id;
    const pos = new THREE.BufferAttribute(abg.positions, 3);
    let fill: THREE.Mesh | null = null;
    if (abg.indices && abg.indices.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", pos);
      g.setIndex(new THREE.BufferAttribute(abg.indices, 1));
      const byLevel = style.colorBy === "level" && abg.scalars;
      if (byLevel) {
        const n = abg.header.n_points;
        const col = new Float32Array(3 * n);
        const c = new THREE.Color();
        const s = abg.scalars!;
        for (let i = 0; i < n; i++) {
          const lv = Number.isFinite(s[i]) ? Math.max(0, Math.min(LEVEL_COLORS.length - 1, Math.round(s[i]))) : 0;
          c.setHex(LEVEL_COLORS[lv]);
          col[3 * i] = c.r;
          col[3 * i + 1] = c.g;
          col[3 * i + 2] = c.b;
        }
        g.setAttribute("color", new THREE.BufferAttribute(col, 3));
      }
      const common = { side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 };
      const mat = byLevel
        ? new THREE.MeshBasicMaterial({ vertexColors: true, ...common })
        : new THREE.MeshStandardMaterial({ color: style.color ?? 0x9aa3ae, metalness: 0.05, roughness: 0.7, flatShading: true, ...common });
      fill = new THREE.Mesh(g, mat);
      grp.add(fill);
    }
    let edges: THREE.LineSegments | null = null;
    if (abg.edges && abg.edges.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", pos);
      g.setIndex(new THREE.BufferAttribute(abg.edges, 1));
      edges = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0x0b0d10, transparent: true, opacity: 0.6 }));
      grp.add(edges);
    }
    this.meshGroup.add(grp);
    this.meshLayers.set(id, { group: grp, fill, edges, style });
    this.styleMeshLayer(id, style);
    if (this.section) this.applySection();
  }

  styleMeshLayer(id: string, style: Partial<MeshLayerStyle>): void {
    const e = this.meshLayers.get(id);
    if (!e) return;
    e.style = { ...e.style, ...style };
    const st = e.style;
    e.group.visible = st.visible !== false;
    const wire = st.mode === "wireframe";
    if (e.fill) e.fill.visible = !wire;
    if (e.edges) {
      e.edges.visible = st.mode !== "surface";
      const m = e.edges.material as THREE.LineBasicMaterial;
      // light lines when they are all there is, dark lines over a lit surface
      m.color.setHex(wire ? (st.wireColor ?? 0xb7c0cb) : 0x0b0d10);
      m.opacity = wire ? 0.75 : st.colorBy === "level" ? 0.45 : 0.6;
      m.needsUpdate = true;
    }
    this.dirty = true;
  }

  removeMeshLayer(id: string): void {
    const e = this.meshLayers.get(id);
    if (!e) return;
    this.meshGroup.remove(e.group);
    e.group.traverse((o: any) => {
      o.geometry?.dispose?.();
      o.material?.dispose?.();
    });
    this.meshLayers.delete(id);
    this.dirty = true;
  }

  clearMeshLayers(): void {
    for (const id of [...this.meshLayers.keys()]) this.removeMeshLayer(id);
  }

  hasMeshLayer(id: string): boolean {
    return this.meshLayers.has(id);
  }

  /**
   * Cut the vehicle surfaces (and mesh layers marked ``clip``) along a plane, removing the
   * half facing the camera, so a mesh cut through the volume can be seen around the body.
   */
  setSectionClip(normal: number[] | null, point?: number[]): void {
    if (!normal || !point) {
      this.section = null;
      this.renderer.localClippingEnabled = false;
    } else {
      this.section = new THREE.Plane().setFromNormalAndCoplanarPoint(new THREE.Vector3(...(normal as [number, number, number])).normalize(), new THREE.Vector3(...(point as [number, number, number])));
      this.renderer.localClippingEnabled = true;
    }
    this.applySection();
  }

  private applySection(): void {
    const planes = this.section ? [this.section] : [];
    for (const { material } of this.parts) {
      material.clippingPlanes = planes;
      material.needsUpdate = true;
    }
    for (const e of this.meshLayers.values()) {
      const clip = e.style.clip ? planes : [];
      for (const o of [e.fill, e.edges]) {
        if (!o) continue;
        const m = o.material as THREE.Material;
        m.clippingPlanes = clip;
        m.needsUpdate = true;
      }
    }
    this.dirty = true;
  }

  setOverlaysVisible(v: boolean): void {
    this.overlays.visible = v;
    this.dirty = true;
  }

  // ------------------------------------------------------------------ camera
  bounds(): THREE.Box3 {
    const b = new THREE.Box3();
    b.expandByObject(this.geometry);
    if (b.isEmpty()) b.expandByObject(this.meshGroup);
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
