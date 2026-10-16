import { Button, Checkbox, Group, Loader, NumberInput, Progress, SegmentedControl, SimpleGrid, Slider, Stack, Switch, Table, Text, Tooltip } from "@mantine/core";
import { IconGridDots, IconRefresh, IconTerminal2 } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { api } from "../api/client";
import type { MeshPlan } from "../api/types";
import { fmt } from "../components/ColorLegend";
import { formatDuration, LogDrawer } from "../components/LiveLog";
import { notifyError, notifyOk } from "../components/notify";
import { DiagnosticList, Hint, KeyValue, NumField, PageLayout, SectionTitle, StatusBadge, StatusDot } from "../components/ui";
import { pid, useApp } from "../state/store";
import { decodeAbg, type Abg } from "../viewport/abg";
import { LEVEL_COLORS, levelColorCss } from "../viewport/colormaps";
import { Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";
import { RegionEditor } from "./mesh/RegionEditor";
import { usePlan } from "./TunnelPage";

/** Modelled refinement bodies as the mesher places them (fetched once per placement). */
const bodyCache = new Map<string, Promise<Abg>>();
function refinementBody(id: string, shape: any): Promise<Abg> {
  const key = `${pid()}:${id}:${shape.blob}:${shape.bbox_min.join(",")}:${shape.bbox_max.join(",")}`;
  let p = bodyCache.get(key);
  if (!p) {
    p = api.binary(`/api/p/${pid()}/geometry/refinement/${id}.abg`).then(decodeAbg);
    p.catch(() => bodyCache.delete(key));
    bodyCache.set(key, p);
  }
  return p;
}

export function drawPlan(v: Viewer, plan: MeshPlan, opts: { regions?: boolean; domain?: boolean; hiddenRegions?: string[] } = {}) {
  v.clearOverlays();
  const gen = ((v as any).__planGen = ((v as any).__planGen || 0) + 1);
  const [x0, y0, z0] = plan.domain_min;
  const [x1, y1, z1] = plan.domain_max;
  if (opts.domain !== false) {
    v.addBox([x0, y0, z0], [x1, y1, z1], 0x59616c, 0.0, true);
    v.addGround([x0, y0, z0], [x1, y1, z1], plan.ground_z);
    const L = plan.vehicle_bbox_max[0] - plan.vehicle_bbox_min[0];
    v.addArrow([x0 + 0.3 * L, 0.5 * (y0 + y1), 0.5 * (z0 + z1)], [Math.cos((plan.yaw_range[0] * Math.PI) / 180), -Math.sin((plan.yaw_range[0] * Math.PI) / 180), 0], L, 0x8db7e0);
  }
  if (opts.regions) {
    for (const r of plan.regions) {
      if (opts.hiddenRegions?.includes(r.id)) continue;
      const col = LEVEL_COLORS[Math.min(r.level, LEVEL_COLORS.length - 1)];
      const s = r.shape;
      if (s.kind === "box") v.addBox(s.min, s.max, col, 0.05, true, r.id);
      else if (s.kind === "cylinder") v.addCylinder(s.point1, s.point2, s.radius, col, 0.1);
      else if (s.kind === "rotated_box") v.addRotatedBox(s.origin, s.span, s.e1, s.e3, col, 0.05);
      else if (s.kind === "surface") {
        refinementBody(r.id, s).then((abg) => {
          if ((v as any).__planGen === gen) v.addMeshOverlay(abg, col, 0.06, r.id);
        }).catch(() => v.addBox(s.bbox_min, s.bbox_max, col, 0.0, true, r.id));
      }
    }
    v.addPoint(plan.location_in_mesh, 0xd6a24a, Math.max(0.03, plan.base_cell * 0.15));
  }
}

type Cut = "off" | "y" | "x" | "z";
const CUT_AXIS: Record<Exclude<Cut, "off">, number> = { x: 0, y: 1, z: 2 };
const CUT_NORMAL: Record<Exclude<Cut, "off">, number[]> = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };

interface MeshView {
  mesh_id: string;
  status: string;
  patches: { vehicle: string[]; ground: string[]; domain: string[] };
  plan: MeshPlan | null;
  cells: number | null;
}

interface Show {
  vehicle: boolean;
  ground: boolean;
  boxes: boolean;
  domain: boolean;
  wireframe: boolean;
}

function LevelLegend({ levels, base }: { levels: number[]; base: number }) {
  if (!levels.length) return null;
  return (
    <div className="ab-float" style={{ left: 12, bottom: 12, padding: "8px 10px" }}>
      <Text size="xs" c="dimmed" mb={4}>
        Refinement level of the cut cells
      </Text>
      <Group gap={10}>
        {levels.map((l) => (
          <Group key={l} gap={5} wrap="nowrap">
            <span style={{ width: 10, height: 10, borderRadius: 2, background: levelColorCss(l), display: "inline-block" }} />
            <Text size="xs">
              {l}
              <span style={{ color: "var(--ab-text-3)" }}> · {fmt((base / 2 ** l) * 1000, base / 2 ** l < 0.01 ? 1 : 0)} mm</span>
            </Text>
          </Group>
        ))}
      </Group>
    </div>
  );
}

export function MeshPage() {
  const { project, patchConfig, geometryVersion, jobs, refreshProject, setSection, environment, meshView, setMeshView } = useApp();
  const cfg = project!.config;
  const m = cfg.mesh;
  const [plan, extra, loading] = usePlan();
  const [meshes, setMeshes] = useState<any[]>([]);
  const [current, setCurrent] = useState<any>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const maxCores = environment?.max_cores || 1;
  const cores = Math.max(1, Math.min(maxCores, cfg.execution.n_procs));
  const viewer = useRef<Viewer | null>(null);
  const [geomLoaded, setGeomLoaded] = useState(0);
  const [viewerReady, setViewerReady] = useState(0);
  const framedFor = useRef<string>("");
  // view settings live in the store, so they survive leaving and re-opening the page
  const mode: "plan" | "mesh" = meshView.mode ?? "plan";
  const setMode = (v: "plan" | "mesh") => setMeshView({ mode: v, modeChosen: true });
  const show: Show = meshView.show ?? { vehicle: true, ground: false, boxes: false, domain: true, wireframe: false };
  const setShow = (v: Show) => setMeshView({ show: v });
  const cut: Cut = meshView.cut ?? "off";
  const setCut = (v: Cut) => setMeshView({ cut: v });
  const cutPos: Record<string, number> = meshView.cutPos ?? {};
  const setCutPos = (f: (p: Record<string, number>) => Record<string, number>) => setMeshView({ cutPos: f(useApp.getState().meshView.cutPos ?? {}) });
  const [view, setView] = useState<MeshView | null>(null);
  const [layerBusy, setLayerBusy] = useState<string | null>(null);
  const [levels, setLevels] = useState<number[]>([]);
  const [logOpen, setLogOpen] = useState(false);
  const [progress, setProgress] = useState<any>(null);
  const st = project!.staleness.mesh;

  // the mesh shown: the one for the current settings when there is one, else the newest
  const shownId: string | null = st.matching?.id ?? meshes[0]?.id ?? null;
  const loadMeshes = useCallback(async () => {
    try {
      setMeshes(await api.get(`/api/p/${pid()}/meshes`));
    } catch (e) {
      notifyError(e);
    }
  }, []);

  useEffect(() => {
    loadMeshes();
  }, [loadMeshes, jobs.recent.map((j: any) => `${j.status}${j.ingested}`).join()]);

  const meshRowStamp = meshes.find((x) => x.id === shownId)?.status;
  useEffect(() => {
    if (!shownId) return setCurrent(null);
    let cancelled = false;
    api.get(`/api/p/${pid()}/meshes/${shownId}`).then((r) => !cancelled && setCurrent(r)).catch(notifyError);
    return () => {
      cancelled = true;
    };
  }, [shownId, meshRowStamp, jobs.recent.map((j: any) => `${j.status}${j.ingested}`).join()]);

  const viewable = !!current && ["completed", "failed"].includes(current.status) && !!current.cells;
  // open on the generated mesh once one exists, unless the user picked the planning view
  useEffect(() => {
    if (viewable && !meshView.modeChosen && mode !== "mesh") setMeshView({ mode: "mesh" });
    if (!viewable && mode === "mesh") setMeshView({ mode: "plan" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewable, current?.id]);

  // live progress of a meshing job
  const meshJob = current && jobs.states[current.job_id];
  const meshRunning = !!meshJob && !["completed", "failed", "stopped", "cancelled", "interrupted"].includes(meshJob.status);
  useEffect(() => {
    if (!meshRunning || !current?.job_id) return setProgress(null);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api.get(`/api/p/${pid()}/jobs/${current.job_id}/progress`);
        if (!cancelled) setProgress(r);
      } catch {
        /* try again */
      }
      if (!cancelled) timer = setTimeout(tick, 2000);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [meshRunning, current?.job_id]);

  const cancelMeshing = async () => {
    if (!current?.job_id) return;
    try {
      await api.post(`/api/p/${pid()}/jobs/${current.job_id}/stop`, { force: true });
      notifyOk("Meshing is being cancelled.");
    } catch (e) {
      notifyError(e);
    }
  };

  // vehicle surface (the triangulated CAD) in both modes
  useEffect(() => {
    const v = viewer.current;
    if (!v || !cfg.geometry.revision_id) return;
    api.binary(`/api/p/${pid()}/geometry/preview.abg`).then((b) => {
      v.setGeometry(decodeAbg(b));
      setGeomLoaded((x) => x + 1);
    }).catch(notifyError);
  }, [cfg.geometry.revision_id, geometryVersion]);

  // ---------------------------------------------------------------- planning view
  useEffect(() => {
    const v = viewer.current;
    if (!v || !plan || mode !== "plan") return;
    v.clearMeshLayers();
    v.setSectionClip(null);
    v.setGeometryVisible(true);
    // your own boxes are drawn (and edited) by the region editor
    const own = plan.regions.filter((r) => !r.auto && r.shape.kind === "box").map((r) => r.id);
    drawPlan(v, plan, { regions: true, domain: true, hiddenRegions: [...hidden, ...own] });
    const lo = plan.vehicle_bbox_min;
    const hi = plan.vehicle_bbox_max;
    const key = `${lo.join(",")}|${hi.join(",")}|${geomLoaded}`;
    if (framedFor.current !== key) {
      // frame the view when the vehicle changes, not when a box is edited
      framedFor.current = key;
      const L = hi[0] - lo[0];
      v.view("iso", new THREE.Box3(new THREE.Vector3(lo[0] - 0.3 * L, lo[1] - 0.5 * L, lo[2]), new THREE.Vector3(hi[0] + 1.5 * L, hi[1] + 0.5 * L, hi[2] + 0.3 * L)));
    }
  }, [plan, hidden, geomLoaded, mode]);

  // ---------------------------------------------------------------- generated mesh view
  const meshId = viewable ? current.id : null;
  const [opening, setOpening] = useState(false);
  useEffect(() => {
    if (mode !== "mesh" || !meshId) return;
    let cancelled = false;
    setView(null);
    setOpening(true);
    // nothing of the planning view may linger while the mesh is being opened
    viewer.current?.clearOverlays();
    viewer.current?.setGeometryVisible(false);
    api
      .get<MeshView>(`/api/p/${pid()}/meshes/${meshId}/view`)
      .then((vw) => {
        if (cancelled) return;
        setView(vw);
        const p = vw.plan;
        if (p) {
          const lo = p.vehicle_bbox_min;
          const hi = p.vehicle_bbox_max;
          // keep positions the user chose (clamped into this mesh's tunnel); fill in the rest
          const d0 = p.domain_min;
          const d1 = p.domain_max;
          const clamp = (v: number | undefined, a: number, fb: number) => (v === undefined ? fb : Math.min(d1[a], Math.max(d0[a], v)));
          setCutPos((prev) => ({
            x: clamp(prev.x, 0, 0.5 * (lo[0] + hi[0])),
            y: clamp(prev.y, 1, p.half_model ? lo[1] + 0.25 * (hi[1] - lo[1]) : 0.5 * (lo[1] + hi[1])),
            z: clamp(prev.z, 2, lo[2] + 0.3 * (hi[2] - lo[2])),
          }));
        }
      })
      .catch((e) => {
        if (cancelled) return;
        notifyError(e, "The generated mesh cannot be shown");
        viewer.current?.setGeometryVisible(true);
      })
      .finally(() => !cancelled && setOpening(false));
    return () => {
      cancelled = true;
    };
  }, [mode, meshId]);

  const fetchLayer = useCallback(
    async (id: string, op: string, params: any) => {
      const res = await api.post(`/api/p/${pid()}/meshes/${meshId}/viz/extract`, { op, params });
      const buf = await api.binary(`/api/p/${pid()}/meshes/${meshId}/viz/payload/${res.key}`);
      return { id, abg: decodeAbg(buf) };
    },
    [meshId],
  );

  const patchStyle = (which: "vehicle" | "ground") => ({
    mode: show.wireframe ? ("wireframe" as const) : ("edges" as const),
    colorBy: "solid" as const,
    color: which === "vehicle" ? 0xa3acb7 : 0x59616c,
    clip: true,
    visible: show[which],
  });

  // patch layers (vehicle, ground): fetched once per mesh, then only restyled
  useEffect(() => {
    const v = viewer.current;
    if (!v || mode !== "mesh" || !view) return;
    v.setGeometryVisible(false);
    let cancelled = false;
    (async () => {
      for (const which of ["vehicle", "ground"] as const) {
        const id = `mesh:${view.mesh_id}:${which}`;
        const patches = view.patches[which];
        if (!show[which] || !patches.length) {
          if (v.hasMeshLayer(id)) v.styleMeshLayer(id, { visible: false });
          continue;
        }
        if (!v.hasMeshLayer(id)) {
          setLayerBusy(which === "vehicle" ? "Loading the vehicle surface mesh…" : "Loading the ground mesh…");
          try {
            const { abg } = await fetchLayer(id, "mesh_patches", { patches });
            if (cancelled) return;
            v.setMeshLayer(id, abg, patchStyle(which));
          } catch (e) {
            if (!cancelled) notifyError(e, "Could not show the surface mesh");
          } finally {
            if (!cancelled) setLayerBusy(null);
          }
        } else v.styleMeshLayer(id, patchStyle(which));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, view, show.vehicle, show.ground, show.wireframe]);

  // overlays in mesh mode: refinement boxes and domain as they were for this mesh
  useEffect(() => {
    const v = viewer.current;
    if (!v || mode !== "mesh" || !view?.plan) return;
    v.clearOverlays();
    drawPlan(v, view.plan, { regions: show.boxes, domain: show.domain });
  }, [mode, view, show.boxes, show.domain]);

  // camera when the mesh view opens
  useEffect(() => {
    const v = viewer.current;
    if (!v || mode !== "mesh" || !view?.plan) return;
    const lo = view.plan.vehicle_bbox_min;
    const hi = view.plan.vehicle_bbox_max;
    const L = hi[0] - lo[0];
    v.view("iso", new THREE.Box3(new THREE.Vector3(lo[0] - 0.1 * L, lo[1] - 0.15 * L, lo[2]), new THREE.Vector3(hi[0] + 0.2 * L, hi[1] + 0.15 * L, hi[2] + 0.1 * L)));
  }, [mode, view?.mesh_id]);

  // the cut through the volume mesh (debounced while the slider moves; cached by the engine)
  const cutReq = useRef(0);
  useEffect(() => {
    const v = viewer.current;
    if (!v || mode !== "mesh" || !view) return;
    const id = `mesh:${view.mesh_id}:cut`;
    if (cut === "off") {
      v.removeMeshLayer(id);
      v.setSectionClip(null);
      setLevels([]);
      return;
    }
    const axis = CUT_AXIS[cut];
    const pos = cutPos[cut] ?? 0;
    const origin = [0, 0, 0];
    origin[axis] = pos;
    v.setSectionClip(CUT_NORMAL[cut], origin);
    const my = ++cutReq.current;
    const t = setTimeout(async () => {
      setLayerBusy("Cutting through the volume mesh…");
      try {
        const { abg } = await fetchLayer(id, "mesh_slice", { origin, normal: CUT_NORMAL[cut] });
        if (my !== cutReq.current) return;
        v.setMeshLayer(id, abg, { mode: show.wireframe ? "wireframe" : "edges", colorBy: "level" });
        setLevels(abg.header.levels || []);
      } catch (e) {
        if (my === cutReq.current) notifyError(e, "Could not cut the mesh here");
      } finally {
        if (my === cutReq.current) setLayerBusy(null);
      }
    }, 280);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, view, cut, cutPos[cut === "off" ? "x" : cut]]);

  useEffect(() => {
    const v = viewer.current;
    if (!v || !view) return;
    v.styleMeshLayer(`mesh:${view.mesh_id}:cut`, { mode: show.wireframe ? "wireframe" : "edges" });
  }, [show.wireframe, view]);

  const chooseCut = (c: Cut) => {
    setCut(c);
    const v = viewer.current;
    if (!v || !view?.plan || c === "off") return;
    const lo = view.plan.vehicle_bbox_min;
    const hi = view.plan.vehicle_bbox_max;
    const L = hi[0] - lo[0];
    const box = new THREE.Box3(new THREE.Vector3(lo[0] - 0.5 * L, lo[1] - 0.2 * L, lo[2]), new THREE.Vector3(hi[0] + 1.0 * L, hi[1] + 0.2 * L, hi[2] + 0.4 * L));
    v.view(c === "y" ? "side" : c === "x" ? "rear" : "top", box);
  };

  // leaving the page or the mode: restore the plain vehicle
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    if (mode === "plan") {
      v.setGeometryVisible(true);
      setCut("off");
    }
  }, [mode]);

  const setPreset = async (p: string) => {
    try {
      await api.post(`/api/p/${pid()}/presets/mesh/${p}`);
      await refreshProject();
    } catch (e) {
      notifyError(e);
    }
  };

  const toCustomSurface = (compId: string, minL: number, maxL: number) => {
    if (!plan) return;
    // freeze the current plan into an explicit custom surface list, then change one entry
    const surface = plan.surfaces.map((s) => ({
      component_ids: [s.component_id],
      min_level: s.component_id === compId ? minL : s.min_level,
      max_level: s.component_id === compId ? Math.max(minL, maxL) : s.max_level,
      feature_level: s.component_id === compId ? Math.max(minL, maxL) : s.feature_level,
      layers: s.layers,
    }));
    patchConfig({ mesh: { preset: "custom", base_cell_size: plan.base_cell, surface } });
  };

  const generate = async (force = false) => {
    setBusy(true);
    try {
      const r = await api.post(`/api/p/${pid()}/meshes`, { force, nprocs: cores });
      setMeshView({ modeChosen: false });
      notifyOk(r.reused ? "This mesh already exists for the current settings." : "Meshing started.");
      await loadMeshes();
      await refreshProject();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const ld = plan?.layer_design;
  const lay = m.layers || {};
  const coverage = current?.snappy?.layers || [];
  const q = current?.quality;
  const forCurrent = !!current && current.id === st.matching?.id;
  const viewPlan = mode === "mesh" ? view?.plan : plan;
  const span = useMemo(() => {
    const p = viewPlan;
    if (!p || cut === "off") return null;
    const a = CUT_AXIS[cut];
    return [p.domain_min[a], p.domain_max[a]];
  }, [viewPlan, cut]);

  const latest = current && (
    <>
      <SectionTitle first right={<StatusBadge status={meshRunning ? meshJob.status : current.status} />}>
        {forCurrent ? "Mesh for these settings" : "Most recent mesh"}
      </SectionTitle>
      {meshRunning && (
        <Stack gap={4} mb={10}>
          <Progress value={(100 * (meshJob.step_index + 0.5)) / Math.max(1, meshJob.steps.length)} animated />
          <Group justify="space-between" wrap="nowrap">
            <Text size="xs" c="dimmed">
              {progress?.phase || meshJob.steps[meshJob.step_index]?.name} · step {meshJob.step_index + 1} of {meshJob.steps.length}
              {progress?.elapsed_seconds != null && ` · ${formatDuration(progress.elapsed_seconds)} so far`}
            </Text>
            <Button size="compact-xs" color="red" variant="subtle" onClick={cancelMeshing}>
              Cancel
            </Button>
          </Group>
        </Stack>
      )}
      {current.job_id && (
        <Button size="compact-xs" variant="subtle" mb={8} leftSection={<IconTerminal2 size={13} />} onClick={() => setLogOpen(true)}>
          {meshRunning ? "Show meshing output (live)" : "Show meshing output"}
        </Button>
      )}
      {!forCurrent && st.state === "stale" && (
        <Hint style={{ color: "var(--ab-warn)", marginBottom: 8 }}>
          No mesh exists for the current settings yet; this one was made with other settings and is kept for the runs that used it. Generate a mesh, or start a run, to mesh with the current settings.
        </Hint>
      )}
      {forCurrent && current.status === "failed" && (
        <Hint style={{ color: "var(--ab-err)", marginBottom: 8 }}>Meshing with the current settings failed. The findings below say why; the meshing output has the details.</Hint>
      )}
      {forCurrent && ["stopped", "cancelled"].includes(current.status) && (
        <Hint style={{ marginBottom: 8 }}>Meshing was cancelled. Generate the mesh again when ready.</Hint>
      )}
      {q ? (
        <KeyValue
          rows={[
            ["Cells", q.cells?.toLocaleString()],
            ["Worst non-orthogonality", `${fmt(q.max_non_ortho, 1)}°  (mean ${fmt(q.avg_non_ortho, 1)}°)`],
            ["Worst skewness", fmt(q.max_skewness, 2)],
            ["Largest aspect ratio", fmt(q.max_aspect_ratio, 1)],
            ["Failed quality checks", q.failed_checks],
            ["Boundary layer coverage", current.snappy?.overall_layer_coverage != null ? `${(current.snappy.overall_layer_coverage * 100).toFixed(0)} %` : "—"],
          ]}
        />
      ) : (
        <Hint>Quality figures appear when meshing has finished.</Hint>
      )}
      {coverage.length > 0 && (
        <Stack gap={4} mt={10}>
          {coverage.map((l: any) => (
            <Group key={l.patch} gap={8} wrap="nowrap">
              <Text size="xs" w={92} truncate>
                {l.patch}
              </Text>
              <Progress style={{ flex: 1 }} size="xs" value={Math.min(100, l.coverage * 100)} color={l.coverage > 0.7 ? "teal" : l.coverage > 0.4 ? "yellow" : "red"} />
              <Text size="xs" c="dimmed" w={58} ta="right">
                {fmt(l.mesh_layers, 1)} of {l.target_layers}
              </Text>
            </Group>
          ))}
        </Stack>
      )}
      {(current.diagnostics || []).filter((d: any) => typeof d === "object").length > 0 && (
        <div style={{ marginTop: 12 }}>
          <DiagnosticList items={(current.diagnostics || []).filter((d: any) => typeof d === "object")} />
        </div>
      )}
      {meshes.length > 1 && <Hint style={{ marginTop: 8 }}>{meshes.length} meshes in this project; earlier ones are kept for the runs that used them.</Hint>}
    </>
  );

  const side = (
    <>
      {latest}
      <SectionTitle first={!current} right={loading ? <Loader size={12} /> : undefined}>
        Mesh density
      </SectionTitle>
      <SegmentedControl size="xs" fullWidth value={m.preset} onChange={setPreset}
        data={[{ value: "fast", label: "Fast" }, { value: "balanced", label: "Balanced" }, { value: "high_accuracy", label: "High accuracy" }, { value: "custom", label: "Custom" }]} />
      {plan && (
        <div style={{ marginTop: 10 }}>
          <KeyValue
            rows={[
              ["Background cell (level 0)", `${fmt(plan.base_cell * 1000, 1)} mm`],
              ["Cells on the body", `${fmt(plan.body_cell * 1000, 2)} mm (level ${plan.body_level})`],
              ["Expected cells", `${(plan.estimate_cells / 1e6).toFixed(2)} M  (${(plan.estimate_range[0] / 1e6).toFixed(1)}–${(plan.estimate_range[1] / 1e6).toFixed(1)} M)`],
              ["Expected memory", `${plan.estimate_memory_gb[0]}–${plan.estimate_memory_gb[1]} GB`],
            ]}
          />
          <Hint style={{ marginTop: 4 }}>Estimates; the real count is known after meshing.</Hint>
        </div>
      )}
      {m.preset === "custom" && (
        <NumField label="Background cell size" units="m" value={m.base_cell_size ?? plan?.base_cell} decimals={4} onCommit={(v) => patchConfig({ mesh: { base_cell_size: v } })} />
      )}

      <SectionTitle>Surface refinement</SectionTitle>
      <Table className="compact" withRowBorders={false}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Part</Table.Th>
            <Table.Th>Min level</Table.Th>
            <Table.Th>Max level</Table.Th>
            <Table.Th>Cell</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {plan?.surfaces.map((s) => (
            <Table.Tr key={s.component_id}>
              <Table.Td>
                <Text size="xs">{s.component_id}</Text>
              </Table.Td>
              <Table.Td>
                <NumberInput size="xs" w={58} min={0} max={12} value={s.min_level} onChange={(v) => typeof v === "number" && toCustomSurface(s.component_id, v, s.max_level)} />
              </Table.Td>
              <Table.Td>
                <NumberInput size="xs" w={58} min={0} max={12} value={s.max_level} onChange={(v) => typeof v === "number" && toCustomSurface(s.component_id, s.min_level, v)} />
              </Table.Td>
              <Table.Td>
                <Text size="xs">{fmt(s.cell_size * 1000, 1)} mm</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <RegionEditor plan={plan} regions={m.regions || []} patch={(regions) => patchConfig({ mesh: { regions } })} viewer={viewer}
        viewerReady={viewerReady} active={mode === "plan"} hidden={hidden} setHidden={setHidden} centreY={cfg.vehicle.centreline_y || 0} />
      {plan && plan.distance_levels.length > 0 && (
        <Hint style={{ marginTop: 6 }}>Also near the walls: {plan.distance_levels.map(([d, l]) => `level ${l} within ${fmt(d * 1000, 0)} mm`).join(", ")}.</Hint>
      )}

      <SectionTitle>Boundary layer</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={lay.mode} onChange={(v) => patchConfig({ mesh: { layers: { mode: v } } })}
        data={[{ value: "target_yplus", label: "Target y+" }, { value: "relative", label: "Relative" }, { value: "none", label: "No layers" }]} />
      <SimpleGrid cols={3} spacing="xs" mt={8}>
        {lay.mode === "target_yplus" && (
          <NumField label="Target y+" value={lay.target_yplus} decimals={1} onCommit={(v) => patchConfig({ mesh: { layers: { target_yplus: v } } })} />
        )}
        <NumField label="Growth ratio" value={lay.expansion_ratio} decimals={3} onCommit={(v) => patchConfig({ mesh: { layers: { expansion_ratio: v } } })} />
        <NumField label={lay.mode === "relative" ? "Layers" : "Layers (auto)"} value={lay.n_layers ?? ld?.n_layers} decimals={0}
          onCommit={(v) => patchConfig({ mesh: { layers: { n_layers: Math.round(v) } } })} />
        {lay.mode !== "none" && (
          <NumField label="Last layer / cell" value={lay.final_relative} decimals={2} onCommit={(v) => patchConfig({ mesh: { layers: { final_relative: v } } })} />
        )}
      </SimpleGrid>
      {ld && (
        <div style={{ marginTop: 10 }}>
          <KeyValue
            rows={[
              ["First layer", `${fmt(ld.first_layer * 1000, 3)} mm`],
              ["Layers × growth", `${ld.n_layers} × ${fmt(ld.expansion_ratio, 2)}`],
              ["Last layer", `${fmt(ld.final_layer * 1000, 2)} mm (${fmt(ld.final_relative, 2)} × cell)`],
              ["Total thickness", `${fmt(ld.total_thickness * 1000, 2)} mm`],
              ["Expected y+", fmt(ld.yplus_estimate, 1)],
            ]}
          />
          <Hint style={{ marginTop: 4 }}>{ld.assumptions}</Hint>
          {ld.warnings?.map((w: string, i: number) => (
            <Hint key={i} style={{ color: "var(--ab-warn)", marginTop: 4 }}>
              {w}
            </Hint>
          ))}
        </div>
      )}
      {extra?.existing && extra.existing.status !== "completed" && (
        <Hint style={{ marginTop: 10 }}>A mesh for these settings is {extra.existing.status}.</Hint>
      )}
    </>
  );

  const footer = (
    <Stack gap={8}>
      <Group gap={8} align="flex-end" wrap="nowrap">
        <NumberInput size="xs" w={78} label="Cores" min={1} max={maxCores} value={cores}
          onChange={(v) => typeof v === "number" && v >= 1 && patchConfig({ execution: { n_procs: Math.max(1, Math.min(maxCores, Math.round(v))) } })} />
        <Button style={{ flex: 1 }} leftSection={<IconGridDots size={15} />} loading={busy} onClick={() => generate(false)} disabled={!plan || (meshRunning && forCurrent)}>
          {meshRunning && forCurrent ? "Meshing…" : "Generate mesh"}
        </Button>
        <Tooltip label="Mesh again even if a mesh for these settings exists">
          <Button variant="default" px={10} onClick={() => generate(true)} aria-label="Mesh again" disabled={meshRunning && forCurrent}>
            <IconRefresh size={15} />
          </Button>
        </Tooltip>
      </Group>
      <Button variant="subtle" size="xs" onClick={() => setSection("physics")}>
        Continue to physics
      </Button>
    </Stack>
  );

  return (
    <PageLayout side={side} title="Mesh" lede="Set how fine the mesh is near the vehicle and in its wake, then generate it and inspect the cells." footer={footer}>
      <Viewport3D onReady={(v) => { viewer.current = v; setViewerReady((x) => x + 1); }}>
        <div className="ab-float" style={{ left: 12, top: 12, padding: 10, width: 258 }}>
          <SegmentedControl size="xs" fullWidth value={mode} onChange={(v) => setMode(v as any)}
            data={[{ value: "plan", label: "Planned" }, { value: "mesh", label: "Generated mesh", disabled: !viewable }]} />
          {mode === "plan" && <Hint style={{ marginTop: 8 }}>Refinement boxes, coloured by level, inside the tunnel. {viewable ? "" : "Generate the mesh to inspect its cells."}</Hint>}
          {mode === "mesh" && (
            <Stack gap={8} mt={10}>
              <SimpleGrid cols={2} spacing={6} verticalSpacing={6}>
                <Checkbox size="xs" label="Vehicle surface" checked={show.vehicle} onChange={(e) => setShow({ ...show, vehicle: e.currentTarget.checked })} />
                <Checkbox size="xs" label="Ground" checked={show.ground} onChange={(e) => setShow({ ...show, ground: e.currentTarget.checked })} />
                <Checkbox size="xs" label="Refinement boxes" checked={show.boxes} onChange={(e) => setShow({ ...show, boxes: e.currentTarget.checked })} />
                <Checkbox size="xs" label="Tunnel outline" checked={show.domain} onChange={(e) => setShow({ ...show, domain: e.currentTarget.checked })} />
              </SimpleGrid>
              <Switch size="xs" label="Wireframe only" checked={show.wireframe} onChange={(e) => setShow({ ...show, wireframe: e.currentTarget.checked })} />
              <div>
                <Text size="xs" c="dimmed" fw={500} mb={4}>
                  Cut through the volume
                </Text>
                <SegmentedControl size="xs" fullWidth value={cut} onChange={(v) => chooseCut(v as Cut)}
                  data={[{ value: "off", label: "None" }, { value: "y", label: "Lengthwise" }, { value: "x", label: "Across" }, { value: "z", label: "Level" }]} />
              </div>
              {cut !== "off" && span && (
                <div>
                  <Group justify="space-between" mb={2}>
                    <Text size="xs" c="dimmed">
                      Position ({cut})
                    </Text>
                    <Text size="xs" className="num">
                      {fmt(cutPos[cut], 3)} m
                    </Text>
                  </Group>
                  <Slider size="sm" min={span[0]} max={span[1]} step={(span[1] - span[0]) / 400} value={cutPos[cut] ?? 0} label={null}
                    onChange={(x) => setCutPos((p) => ({ ...p, [cut]: x }))} />
                </div>
              )}
            </Stack>
          )}
        </div>
        {(layerBusy || (mode === "mesh" && opening)) && (
          <div className="ab-viewport-note" style={{ left: "auto", right: 12, top: 56 }}>
            <Loader size={12} color="ice" />
            {opening ? "Opening the mesh…" : layerBusy}
          </div>
        )}
        {mode === "mesh" && cut !== "off" && view?.plan && <LevelLegend levels={levels} base={view.plan.base_cell} />}
        {mode === "mesh" && current?.cells && (
          <div className="ab-float" style={{ right: 12, bottom: 12, padding: "6px 10px" }}>
            <StatusDot tone={current.status === "completed" ? "ok" : "warn"} label={`${(current.cells / 1e6).toFixed(2)} M cells`} />
          </div>
        )}
      </Viewport3D>
      <LogDrawer jobId={current?.job_id ?? null} title={`Meshing output — ${current?.id ?? ""}`} opened={logOpen} onClose={() => setLogOpen(false)} />
    </PageLayout>
  );
}
