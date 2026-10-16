/**
 * Editable boxes in the 3D view: click a box to select it; the selected box shows a handle on
 * each face; drag a handle and that face follows the mouse along its axis, with the change
 * shown live. Orbiting works as usual everywhere else.
 */
import * as THREE from "three";
import type { Viewer } from "./Viewer";

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

export class BoxEditor {
  private group = new THREE.Group();
  private boxes: EditableBox[] = [];
  private selected: string | null = null;
  private handles: THREE.Mesh[] = [];
  private hovered: THREE.Mesh | null = null;
  private drag: { id: string; axis: number; side: 0 | 1; p0: THREE.Vector3; start: number; box: EditableBox } | null = null;
  private down: { x: number; y: number } | null = null;
  private raycaster = new THREE.Raycaster();
  private el: HTMLElement;
  onSelect?: (id: string | null) => void;
  onChange?: (id: string, min: number[], max: number[]) => void;
  onCommit?: (id: string, min: number[], max: number[]) => void;

  constructor(readonly v: Viewer) {
    this.group.name = "box-editor";
    v.scene.add(this.group);
    (v as any).__boxEditor = this; // for automated interface tests
    this.el = v.renderer.domElement;
    this.el.addEventListener("pointerdown", this.onDown, true);
    this.el.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
  }

  dispose(): void {
    this.el.removeEventListener("pointerdown", this.onDown, true);
    this.el.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
    this.clear();
    this.v.scene.remove(this.group);
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

  private clear(): void {
    for (const o of [...this.group.children]) {
      this.group.remove(o);
      o.traverse((x: any) => {
        x.geometry?.dispose?.();
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

  private rebuild(): void {
    this.clear();
    const S = this.sceneSize();
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
      const hs = Math.min(Math.max(0.05 * size.length(), 0.006 * S), 0.03 * S);
      for (let a = 0; a < 3; a++) {
        for (const side of [0, 1] as const) {
          const p = c.clone();
          p.setComponent(a, side === 0 ? b.min[a] : b.max[a]);
          const h = new THREE.Mesh(new THREE.BoxGeometry(hs, hs, hs), new THREE.MeshBasicMaterial({ color: 0xffffff, depthTest: false, transparent: true, opacity: 0.9 }));
          h.position.copy(p);
          h.renderOrder = 10;
          h.userData = { handle: true, axis: a, side, boxId: b.id };
          this.group.add(h);
          this.handles.push(h);
        }
      }
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
    const hit = this.raycaster.intersectObjects(this.handles, false)[0];
    return (hit?.object as THREE.Mesh) || null;
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
    // hover feedback on handles
    if (!this.handles.length) return;
    const h = this.hitHandle(e);
    if (h !== this.hovered) {
      if (this.hovered) (this.hovered.material as THREE.MeshBasicMaterial).color.set(0xffffff);
      if (h) (h.material as THREE.MeshBasicMaterial).color.set(0xffd27f);
      this.hovered = h;
      this.el.style.cursor = h ? "grab" : "";
      this.v.requestRender();
    }
  };

  private onUp = (e: PointerEvent) => {
    if (this.drag) {
      const d = this.drag;
      this.drag = null;
      this.v.controls.enabled = true;
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
