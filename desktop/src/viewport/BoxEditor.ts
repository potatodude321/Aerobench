/**
 * Editable boxes in the 3D view: click a box to select it; the selected box shows an arrow on
 * each face, pointing the way that face moves and coloured like its axis in the orientation
 * triad (x red, y green, z blue). Drag an arrow and that face follows the mouse along its axis,
 * with a guide line along the axis and the change shown live. Orbiting works as usual everywhere
 * else. The arrows keep the same size on screen however far the camera is.
 */
import * as THREE from "three";
import { AXIS_COLORS, type Viewer } from "./Viewer";

export interface EditableBox {
  id: string;
  min: number[];
  max: number[];
  color: number;
  /** keep the box symmetric about y = yc while dragging a y face */
  symmetricY?: number | null;
  /** cannot be resized (still selectable) */
  locked?: boolean;
}

const AXES = [new THREE.Vector3(1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, 0, 1)];
const ACTIVE = 0xffd27f;
const UP = new THREE.Vector3(0, 1, 0);

/** Arrow handle in screen pixels: a small foot on the face, a shaft and a head, along +y. */
const ARROW = { foot: 4, footH: 2, shaft: 2.1, head: 7.5, headL: 14, length: 40 };
/** Grab area around an arrow, in screen pixels (easier to hit than the thin arrow itself). */
const GRAB = { radius: 11, below: 9, beyond: 4 };

function arrowGeometry(): THREE.BufferGeometry {
  const { foot, footH, shaft, head, headL, length } = ARROW;
  const profile = [
    [0, 0], [foot, 0], [foot, footH], [shaft, footH],
    [shaft, length - headL], [head, length - headL], [0, length],
  ].map(([x, y]) => new THREE.Vector2(x, y));
  // flat shading keeps the foot, shaft and head crisp at this size
  return new THREE.LatheGeometry(profile, 24).toNonIndexed();
}

function grabGeometry(): THREE.BufferGeometry {
  const h = GRAB.below + ARROW.length + GRAB.beyond;
  const g = new THREE.CylinderGeometry(GRAB.radius, GRAB.radius, h, 12);
  g.translate(0, h / 2 - GRAB.below, 0);
  return g;
}

interface Drag {
  id: string;
  axis: number;
  side: 0 | 1;
  p0: THREE.Vector3;
  start: number;
  box: EditableBox;
}

export class BoxEditor {
  private group = new THREE.Group();
  private guide = new THREE.Group();
  private boxes: EditableBox[] = [];
  private selected: string | null = null;
  private handles: THREE.Mesh[] = [];
  private hovered: THREE.Mesh | null = null;
  private drag: Drag | null = null;
  private down: { x: number; y: number } | null = null;
  private raycaster = new THREE.Raycaster();
  private el: HTMLElement;
  private arrowGeo = arrowGeometry();
  private grabGeo = grabGeometry();
  onSelect?: (id: string | null) => void;
  onChange?: (id: string, min: number[], max: number[]) => void;
  onCommit?: (id: string, min: number[], max: number[]) => void;

  constructor(readonly v: Viewer) {
    this.arrowGeo.computeVertexNormals();
    this.group.name = "box-editor";
    this.guide.name = "box-editor-guide";
    v.scene.add(this.group, this.guide);
    (v as any).__boxEditor = this; // for automated interface tests
    this.el = v.renderer.domElement;
    this.el.addEventListener("pointerdown", this.onDown, true);
    this.el.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
    v.beforeRender.add(this.fitHandles);
  }

  dispose(): void {
    this.el.removeEventListener("pointerdown", this.onDown, true);
    this.el.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    this.v.beforeRender.delete(this.fitHandles);
    this.clear();
    this.showGuide(null);
    this.v.scene.remove(this.group, this.guide);
    this.arrowGeo.dispose();
    this.grabGeo.dispose();
    this.el.style.cursor = "";
    this.el.title = "";
  }

  setBoxes(boxes: EditableBox[], selected: string | null): void {
    if (this.drag) {
      // keep the box being dragged as the user moves it; take everything else
      const d = this.drag;
      boxes = boxes.map((b) => (b.id === d.id ? { ...b, min: d.box.min, max: d.box.max } : b));
    }
    this.boxes = boxes;
    this.selected = selected;
    this.rebuild();
  }

  /** Keep every arrow the same size on screen (runs before each frame). */
  private fitHandles = () => {
    for (const h of this.handles) h.scale.setScalar(this.v.worldPerPixel(h.position));
  };

  private clear(): void {
    for (const o of [...this.group.children]) {
      this.group.remove(o);
      o.traverse((x: any) => {
        if (x.geometry !== this.arrowGeo && x.geometry !== this.grabGeo) x.geometry?.dispose?.();
        x.material?.dispose?.();
      });
    }
    this.handles = [];
    this.hovered = null;
  }

  private sceneSize(): number {
    const b = this.v.bounds();
    const s = b.isEmpty() ? 1 : b.getSize(new THREE.Vector3()).length();
    return Math.max(s, 1e-3);
  }

  private makeHandle(boxId: string, axis: number, side: 0 | 1, at: THREE.Vector3): THREE.Mesh {
    const active = !!this.drag && this.drag.id === boxId && this.drag.axis === axis && this.drag.side === side;
    const color = active ? ACTIVE : AXIS_COLORS[axis];
    // shaded where it can be seen, and a faint copy only where the part hides it
    const h = new THREE.Mesh(this.arrowGeo, new THREE.MeshLambertMaterial({ color, emissive: color, emissiveIntensity: 0.5 }));
    h.renderOrder = 11;
    const ghost = new THREE.Mesh(this.arrowGeo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.55, depthFunc: THREE.GreaterDepth, depthWrite: false }));
    ghost.renderOrder = 12;
    const grab = new THREE.Mesh(this.grabGeo, new THREE.MeshBasicMaterial({ visible: false, side: THREE.DoubleSide }));
    h.add(ghost, grab);
    h.position.copy(at);
    h.quaternion.setFromUnitVectors(UP, AXES[axis].clone().multiplyScalar(side === 0 ? -1 : 1));
    h.scale.setScalar(this.v.worldPerPixel(at));
    h.userData = { handle: true, axis, side, boxId };
    return h;
  }

  private rebuild(): void {
    this.clear();
    for (const b of this.boxes) {
      const sel = b.id === this.selected;
      const size = new THREE.Vector3(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]).max(new THREE.Vector3(1e-6, 1e-6, 1e-6));
      const c = new THREE.Vector3((b.max[0] + b.min[0]) / 2, (b.max[1] + b.min[1]) / 2, (b.max[2] + b.min[2]) / 2);
      const g = new THREE.BoxGeometry(size.x, size.y, size.z);
      const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: b.color, transparent: true, opacity: sel ? 0.13 : 0.04, depthWrite: false, side: THREE.DoubleSide }));
      m.position.copy(c);
      m.userData = { boxId: b.id };
      this.group.add(m);
      const e = new THREE.LineSegments(new THREE.EdgesGeometry(g), new THREE.LineBasicMaterial({ color: b.color, transparent: true, opacity: sel ? 1 : 0.55 }));
      e.position.copy(c);
      this.group.add(e);
      if (!sel || b.locked) continue;
      for (let a = 0; a < 3; a++) {
        for (const side of [0, 1] as const) {
          const p = c.clone();
          p.setComponent(a, side === 0 ? b.min[a] : b.max[a]);
          const h = this.makeHandle(b.id, a, side, p);
          this.group.add(h);
          h.updateMatrixWorld(true); // hit-testable before the next frame is drawn
          this.handles.push(h);
        }
      }
    }
    this.v.requestRender();
  }

  /** A line along the axis being dragged, through the face's starting point. */
  private showGuide(d: Drag | null): void {
    for (const o of [...this.guide.children]) {
      this.guide.remove(o);
      (o as THREE.Line).geometry.dispose();
      ((o as THREE.Line).material as THREE.Material).dispose();
    }
    if (d) {
      const L = 2 * this.sceneSize();
      const u = AXES[d.axis];
      const g = new THREE.BufferGeometry().setFromPoints([d.p0.clone().addScaledVector(u, -L), d.p0.clone().addScaledVector(u, L)]);
      const line = new THREE.Line(g, new THREE.LineDashedMaterial({ color: AXIS_COLORS[d.axis], dashSize: L / 200, gapSize: L / 300, transparent: true, opacity: 0.7, depthTest: false }));
      line.computeLineDistances();
      line.renderOrder = 9;
      this.guide.add(line);
    }
    this.v.requestRender();
  }

  private ray(e: PointerEvent): THREE.Ray {
    const r = this.el.getBoundingClientRect();
    const ndc = new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.v.camera);
    return this.raycaster.ray;
  }

  private hitHandle(e: PointerEvent): THREE.Mesh | null {
    this.ray(e);
    const hit = this.raycaster.intersectObjects(this.handles, true)[0];
    let o: THREE.Object3D | null = hit?.object ?? null;
    while (o && !o.userData.handle) o = o.parent;
    return (o as THREE.Mesh) || null;
  }

  private paint(h: THREE.Mesh, color: number): void {
    const solid = h.material as THREE.MeshLambertMaterial;
    solid.color.set(color);
    solid.emissive.set(color);
    ((h.children[0] as THREE.Mesh).material as THREE.MeshBasicMaterial).color.set(color);
  }

  private onDown = (e: PointerEvent) => {
    if (e.button !== 0) return;
    const h = this.hitHandle(e);
    if (h) {
      const { axis, side, boxId } = h.userData;
      const box = this.boxes.find((b) => b.id === boxId);
      if (!box) return;
      // the drag is ours: the camera must not orbit (its handler is on the same element)
      e.stopImmediatePropagation();
      e.preventDefault();
      this.v.controls.enabled = false;
      this.drag = { id: boxId, axis, side, p0: h.position.clone(), start: (side === 0 ? box.min : box.max)[axis],
        box: { ...box, min: [...box.min], max: [...box.max] } };
      this.el.setPointerCapture?.(e.pointerId);
      this.el.style.cursor = "grabbing";
      this.el.title = "";
      this.paint(h, ACTIVE);
      this.showGuide(this.drag);
      return;
    }
    this.down = { x: e.clientX, y: e.clientY };
  };

  private onMove = (e: PointerEvent) => {
    if (this.drag) {
      const d = this.drag;
      const ray = this.ray(e);
      // closest point of the face's axis line to the mouse ray
      const u = AXES[d.axis];
      const w0 = d.p0.clone().sub(ray.origin);
      const b = u.dot(ray.direction);
      const den = 1 - b * b;
      if (Math.abs(den) < 1e-6) return;
      const t = (b * ray.direction.dot(w0) - u.dot(w0)) / den;
      const S = this.sceneSize();
      const eps = 1e-3 * S;
      let val = Math.round((d.start + t) * 1000) / 1000; // whole millimetres
      const mn = [...d.box.min];
      const mx = [...d.box.max];
      if (d.side === 0) val = Math.min(val, mx[d.axis] - eps);
      else val = Math.max(val, mn[d.axis] + eps);
      (d.side === 0 ? mn : mx)[d.axis] = val;
      if (d.axis === 1 && d.box.symmetricY != null) {
        const yc = d.box.symmetricY;
        const a = Math.max(Math.abs(val - yc), eps);
        mn[1] = yc - a;
        mx[1] = yc + a;
      }
      d.box.min = mn;
      d.box.max = mx;
      this.boxes = this.boxes.map((x) => (x.id === d.id ? { ...x, min: mn, max: mx } : x));
      this.rebuild();
      this.onChange?.(d.id, mn, mx);
      return;
    }
    // hover feedback on the arrows
    if (!this.handles.length) return;
    const h = this.hitHandle(e);
    if (h !== this.hovered) {
      if (this.hovered) this.paint(this.hovered, AXIS_COLORS[this.hovered.userData.axis]);
      if (h) this.paint(h, ACTIVE);
      this.hovered = h;
      this.el.style.cursor = h ? "grab" : "";
      this.el.title = h ? `Drag to move this face along ${"xyz"[h.userData.axis]}` : "";
      this.v.requestRender();
    }
  };

  private onUp = (e: PointerEvent) => {
    if (this.drag) {
      const d = this.drag;
      this.drag = null;
      this.v.controls.enabled = true;
      this.el.style.cursor = "";
      this.showGuide(null);
      this.rebuild();
      this.onCommit?.(d.id, d.box.min, d.box.max);
      return;
    }
    const s = this.down;
    this.down = null;
    if (!s || Math.hypot(e.clientX - s.x, e.clientY - s.y) > 4 || e.target !== this.el) return;
    // a click (no orbit): select the nearest box under the mouse
    this.ray(e);
    const meshes = this.group.children.filter((o) => (o as any).isMesh && o.userData.boxId && !o.userData.handle);
    const hit = this.raycaster.intersectObjects(meshes, false)[0];
    if (hit) this.onSelect?.(hit.object.userData.boxId);
  };
}
