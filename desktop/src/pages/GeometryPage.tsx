import { Button, Checkbox, FileButton, Group, Loader, MultiSelect, SegmentedControl, Select, SimpleGrid, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconArrowRight, IconDownload, IconFileImport, IconRefresh, IconTool, IconUpload, IconWand } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { bridge, isDesktop } from "../api/bridge";
import { api } from "../api/client";
import type { GeometryComponent, Wheel } from "../api/types";
import { fmt } from "../components/ColorLegend";
import { notifyError, notifyInfo, notifyOk } from "../components/notify";
import { DiagnosticList, Hint, KeyValue, NumField, PageLayout, SectionTitle, StatusDot } from "../components/ui";
import { pid, useApp } from "../state/store";
import { decodeAbg } from "../viewport/abg";
import { Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";

const ROLES = ["body", "wheel", "wing", "spoiler", "splitter", "diffuser", "floor", "mirror", "cooling_inlet", "cooling_outlet", "stilt", "other"];
const AXES = ["+x", "-x", "+y", "-y", "+z", "-z"] as const;
const UNIT_SCALE: Record<string, number> = { m: 1, mm: 1e-3, cm: 1e-2, in: 0.0254, ft: 0.3048 };
const AXV: Record<string, [number, number, number]> = { "+x": [1, 0, 0], "-x": [-1, 0, 0], "+y": [0, 1, 0], "-y": [0, -1, 0], "+z": [0, 0, 1], "-z": [0, 0, -1] };
const axisLabel = (a: string) => a.replace("-", "−");
const sameAxis = (a: string, b: string) => a[1] === b[1];

/** Source-to-CFD rotation, as the engine builds it: nose to −x, up to +z, right to +y. */
function rotation(forward: string, up: string): THREE.Matrix3 {
  const f = new THREE.Vector3(...AXV[forward]);
  const u = new THREE.Vector3(...AXV[up]);
  const r = new THREE.Vector3().crossVectors(f, u);
  const src = new THREE.Matrix3().set(f.x, r.x, u.x, f.y, r.y, u.y, f.z, r.z, u.z);
  const dst = new THREE.Matrix3().set(-1, 0, 0, 0, 1, 0, 0, 0, 1);
  return dst.multiply(src.transpose());
}

interface Pose {
  forward: string;
  up: string;
  scale: number;
}

/** Linear map from the displayed CFD geometry (pose a) to the one pose b will produce. */
function poseDelta(a: Pose, b: Pose): THREE.Matrix3 {
  const ra = rotation(a.forward, a.up);
  const rb = rotation(b.forward, b.up);
  return rb.multiply(ra.transpose()).multiplyScalar(b.scale / a.scale);
}

export function GeometryPage() {
  const { project, refreshProject, geometryVersion, bumpGeometry, patchConfig, setSection } = useApp();
  const [geo, setGeo] = useState<any>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [comps, setComps] = useState<GeometryComponent[]>([]);
  const [wheels, setWheels] = useState<Wheel[]>([]);
  const [dirty, setDirty] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [pose, setPose] = useState<Pose | null>(null); // what the user asked for (may be in flight)
  const [pending, setPending] = useState(false);
  const viewer = useRef<Viewer | null>(null);
  const shown = useRef<Pose | null>(null); // pose of the geometry currently in the viewport
  const fitted = useRef<number[] | null>(null); // vehicle size the camera was last fitted to
  const seq = useRef(0);
  const cfg = project!.config;
  const isElement = cfg.application === "aero_element";
  const tr = cfg.geometry.transform;
  const current: Pose = pose ?? { forward: tr.forward_axis, up: tr.up_axis, scale: tr.unit_scale };

  const load = useCallback(async () => {
    try {
      const g = await api.get(`/api/p/${pid()}/geometry`);
      setGeo(g);
      setComps(g.components);
      setWheels(g.wheels);
      setDirty(false);
    } catch (e) {
      notifyError(e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, geometryVersion]);

  // viewport content: the posed vehicle and what was derived from it
  useEffect(() => {
    const v = viewer.current;
    if (!v || !cfg.geometry.revision_id) return;
    let cancelled = false;
    const asLoaded: Pose = { forward: tr.forward_axis, up: tr.up_axis, scale: tr.unit_scale };
    api
      .binary(`/api/p/${pid()}/geometry/preview.abg`)
      .then((buf) => {
        if (cancelled) return;
        const abg = decodeAbg(buf);
        const first = !shown.current;
        v.setGeometry(abg);
        shown.current = asLoaded;
        v.clearOverlays();
        const s = cfg.geometry.summary;
        if (s) {
          const L = s.bbox_max[0] - s.bbox_min[0];
          const z0 = abg.header.ground_z ?? 0;
          v.addGround([s.bbox_min[0] - 0.4 * L, s.bbox_min[1] - 0.6 * L, 0], [s.bbox_max[0] + 0.6 * L, s.bbox_max[1] + 0.6 * L, 0], z0);
          v.addArrow([s.bbox_min[0] - 0.38 * L, s.bbox_max[1] + 0.28 * L, z0 + 0.02], [1, 0, 0], 0.22 * L, 0x8db7e0);
          v.addLabel("flow", [s.bbox_min[0] - 0.27 * L, s.bbox_max[1] + 0.36 * L, z0 + 0.04], 0x8db7e0, (0.16 * L) / 1.5);
        }
        for (const w of cfg.vehicle.wheels) {
          const c = w.centre;
          const a = w.axis;
          const h = (w.width || 0.2) * 0.55;
          v.addCylinder([c[0] - a[0] * h, c[1] - a[1] * h, c[2] - a[2] * h], [c[0] + a[0] * h, c[1] + a[1] * h, c[2] + a[2] * h], w.radius * 1.03, 0x8db7e0, 0.0);
          v.addLabel(w.position, [c[0], c[1] + (c[1] < 0 ? -1 : 1) * (w.width || 0.2), c[2] + w.radius * 1.35], 0xa1a9b4, 0.24, true);
        }
        if (cfg.reference.moment_centre) v.addPoint(cfg.reference.moment_centre, 0xd6a24a, 0.035);
        // refit the camera only when the vehicle's size changed (import, orientation, units),
        // not after every edit of a wheel or the ride height
        const size = v.bounds().getSize(new THREE.Vector3()).toArray();
        const prev = fitted.current;
        if (first || !prev || size.some((x, k) => Math.abs(x - prev[k]) > 0.02 * Math.max(...prev))) {
          v.view("iso");
          fitted.current = size;
        }
      })
      .catch(notifyError);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg.geometry.revision_id, geometryVersion, project?.staleness.hashes.geometry, cfg.vehicle.wheels, cfg.reference.moment_centre, cfg.geometry.summary]);

  useEffect(() => viewer.current?.highlightPart(highlight), [highlight]);

  const run = async (key: string, fn: () => Promise<any>, ok?: string) => {
    setBusy(key);
    try {
      const r = await fn();
      await refreshProject();
      await load();
      bumpGeometry();
      if (ok) notifyOk(ok);
      return r;
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  /**
   * Orientation and units: the viewport shows the result at once (a rigid re-orientation of
   * what is displayed); the engine then re-identifies wheels and recomputes reference values
   * in the background and the exact geometry replaces the preview. Rapid changes are applied
   * in order; only the last one reloads the view.
   */
  const changePose = async (next: Pose, request: () => Promise<any>, label: string) => {
    const my = ++seq.current;
    setPose(next);
    setPending(true);
    const v = viewer.current;
    if (v && shown.current) {
      v.previewGeometryTransform(poseDelta(shown.current, next));
      v.clearOverlays();
      v.view("iso");
      fitted.current = v.bounds().getSize(new THREE.Vector3()).toArray();
    }
    try {
      const r = await request();
      if (my !== seq.current) return;
      const notes: string[] = [...(r?.notes || []), ...(r?.reference_notices || [])].filter((n: string) => /named as the|not ahead/.test(n));
      if (notes.length) notifyInfo(notes[0], "Check the orientation");
      await refreshProject();
      await load();
      bumpGeometry();
    } catch (e) {
      if (my !== seq.current) return;
      notifyError(e, `Could not change the ${label}`);
      viewer.current?.previewGeometryTransform(null);
      bumpGeometry();
    } finally {
      if (my === seq.current) {
        setPose(null);
        setPending(false);
      }
    }
  };

  const setOrientation = (forward: string, up: string) => {
    let f = forward;
    let u = up;
    if (sameAxis(f, u)) {
      // the nose cannot point along the up axis: keep the choice just made, move the other
      if (forward !== current.forward) u = f[1] === "z" ? "+y" : "+z";
      else f = u[1] === "x" ? "-y" : "-x";
    }
    if (f === current.forward && u === current.up) return;
    changePose({ ...current, forward: f, up: u }, () => api.post(`/api/p/${pid()}/geometry/orientation`, { forward_axis: f, up_axis: u }), "orientation");
  };
  const setUnits = (unit: string) => {
    if (Math.abs(UNIT_SCALE[unit] - current.scale) < 1e-12) return;
    changePose({ ...current, scale: UNIT_SCALE[unit] }, () => api.post(`/api/p/${pid()}/geometry/units`, { unit }), "units");
  };

  const importFile = async (typed?: string) => {
    let p = (typed ?? "").trim();
    if (!p && bridge)
      p =
        (await bridge.openFile({
          title: "Import vehicle geometry",
          filters: [{ name: "Geometry", extensions: ["stl", "obj", "ply", "step", "stp", "iges", "igs"] }],
        })) || "";
    if (!p) return;
    shown.current = null;
    const r = await run("import", () => api.post(`/api/p/${pid()}/geometry/import`, { path: p }));
    if (r?.units_auto_applied) notifyInfo(`The file was read as ${r.units}: in metres the vehicle would have had an implausible size. Change the units below if needed.`, "Units corrected");
  };

  const upload = async (f: File | null) => {
    if (!f) return;
    shown.current = null;
    await run("import", () => api.upload(`/api/p/${pid()}/geometry/upload`, f));
  };

  const rev = geo?.current;
  const report = rev?.report;
  const s = cfg.geometry.summary;
  const regions: string[] = rev?.region_names || [];
  const updateComp = (i: number, patch: Partial<GeometryComponent>) => {
    setComps((c) => c.map((x, k) => (k === i ? { ...x, ...patch, suggested: false } : x)));
    setDirty(true);
  };
  const updateWheel = (i: number, patch: Partial<Wheel>) => {
    setWheels((w) => w.map((x, k) => (k === i ? { ...x, ...patch, source: "manual" } : x)));
    setDirty(true);
  };
  const unit = Object.keys(UNIT_SCALE).find((u) => Math.abs(UNIT_SCALE[u] - current.scale) < 1e-12) || "m";

  const importControls = (
    <Stack gap={8}>
      {isDesktop && (
        <Button size="sm" leftSection={<IconFileImport size={16} />} onClick={() => importFile()} loading={busy === "import"} fullWidth variant={rev ? "default" : "filled"}>
          {rev ? "Import another file…" : "Import geometry…"}
        </Button>
      )}
      <TextInput size="xs" placeholder={isDesktop ? "Or paste a file path and press Enter" : "Path of the file on the engine machine, then Enter"} value={path}
        onChange={(e) => setPath(e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && path.trim()) {
            importFile(path);
            setPath("");
          }
        }}
        rightSection={!isDesktop ? (
          <Button size="compact-xs" variant="subtle" onClick={() => path.trim() && importFile(path)} disabled={!path.trim()}>
            Import
          </Button>
        ) : undefined}
        rightSectionWidth={!isDesktop ? 64 : undefined} />
      {!isDesktop && (
        <FileButton onChange={upload} accept=".stl,.obj,.ply">
          {(props) => (
            <Button {...props} size="xs" variant="default" leftSection={<IconUpload size={14} />} loading={busy === "import"}>
              Upload a file from this computer…
            </Button>
          )}
        </FileButton>
      )}
      <Hint>STL (binary or ASCII with several solids), OBJ, PLY, STEP and IGES. Parts named in the file (body, wheels, wings…) are kept apart.</Hint>
    </Stack>
  );

  const side = (
    <>
      {!rev && <div style={{ marginTop: 20 }}>{importControls}</div>}
      {rev && (
        <>
          <SectionTitle right={busy === "import" ? <Loader size={14} /> : undefined}>{isElement ? "Element" : "Vehicle"}</SectionTitle>
          {geo.revisions.length > 1 && (
            <Select
              size="xs"
              mb={10}
              label="Version"
              value={rev.id}
              data={geo.revisions.map((r: any) => ({ value: r.id, label: `${r.label} — ${r.kind}, ${r.created_at.slice(0, 16).replace("T", " ")}` }))}
              onChange={(v) => v && run("activate", () => api.post(`/api/p/${pid()}/geometry/activate`, { revision_id: v }), "Version activated")}
            />
          )}
          <KeyValue
            rows={[
              ["File", rev.source_filename || "—"],
              ["Size (L × W × H)", s ? `${fmt(s.bbox_max[0] - s.bbox_min[0], 3)} × ${fmt(s.bbox_max[1] - s.bbox_min[1], 3)} × ${fmt(s.bbox_max[2] - s.bbox_min[2], 3)} m` : "—"],
              ["Triangles", report?.n_faces?.toLocaleString()],
              ["Surface area", s ? `${fmt(s.surface_area, 3)} m²` : "—"],
              ["Watertight", report ? (report.closed ? "Yes" : "No") : "—"],
            ]}
          />
          <div style={{ marginTop: 12 }}>{importControls}</div>

          <SectionTitle right={pending ? <StatusDot tone="live" label="Updating" /> : undefined}>Orientation</SectionTitle>
          <Stack gap={10}>
            <div>
              <Text size="xs" c="dimmed" fw={500} mb={4}>
                The nose points to
              </Text>
              <SegmentedControl size="xs" fullWidth value={current.forward} onChange={(a) => setOrientation(a, current.up)}
                data={AXES.map((a) => ({ value: a, label: axisLabel(a) }))} />
            </div>
            <div>
              <Text size="xs" c="dimmed" fw={500} mb={4}>
                Up is
              </Text>
              <SegmentedControl size="xs" fullWidth value={current.up} onChange={(a) => setOrientation(current.forward, a)}
                data={AXES.map((a) => ({ value: a, label: axisLabel(a) }))} />
            </div>
            <div>
              <Text size="xs" c="dimmed" fw={500} mb={4}>
                File units
              </Text>
              <SegmentedControl size="xs" fullWidth value={unit} onChange={setUnits} data={Object.keys(UNIT_SCALE)} />
            </div>
            <Hint>Directions as written in the file. The view turns at once; wheels and reference values follow a moment later. Air flows along +x.</Hint>
          </Stack>

          <SectionTitle
            right={
              <Button size="compact-xs" variant="subtle" leftSection={<IconTool size={13} />} loading={busy === "repair"}
                onClick={() => run("repair", () => api.post(`/api/p/${pid()}/geometry/repair`, {}), "Repaired copy created; the original is kept")}>
                Repair
              </Button>
            }
          >
            Checks
          </SectionTitle>
          <DiagnosticList items={report?.diagnostics} empty="No problems found in the surface." />
          {(rev.operations || []).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <Text size="xs" c="dimmed" mb={2}>
                Repairs applied to this version
              </Text>
              {(rev.operations || []).map((o: any, i: number) => (
                <Text key={i} size="xs">
                  {i + 1}. {o.summary}
                </Text>
              ))}
            </div>
          )}

          <SectionTitle
            right={
              <>
                <Button size="compact-xs" variant="subtle" leftSection={<IconWand size={13} />} loading={busy === "identify"}
                  onClick={() => run("identify", () => api.post(`/api/p/${pid()}/geometry/identify`), "Parts identified again")}>
                  Identify again
                </Button>
                <Button size="compact-xs" disabled={!dirty} loading={busy === "comps"}
                  onClick={() => run("comps", () => api.post(`/api/p/${pid()}/geometry/components`, { components: comps, wheels }), "Parts saved")}>
                  Save
                </Button>
              </>
            }
          >
            Parts
          </SectionTitle>
          <Table className="compact" withRowBorders={false} verticalSpacing={2}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Part</Table.Th>
                <Table.Th>Role</Table.Th>
                <Table.Th>From regions</Table.Th>
                <Table.Th>
                  <Tooltip label="Report forces for this part">
                    <span>Forces</span>
                  </Tooltip>
                </Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {comps.map((c, i) => (
                <Table.Tr key={c.id} onMouseEnter={() => setHighlight(c.id)} onMouseLeave={() => setHighlight(null)}>
                  <Table.Td style={{ maxWidth: 96 }}>
                    <Text size="xs" truncate>
                      {c.id}
                    </Text>
                    {c.suggested && (
                      <Text size="xs" c="var(--ab-text-3)" lh={1.1}>
                        suggested
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      <Select size="xs" w={c.role === "wheel" ? 82 : 128} data={ROLES} value={c.role}
                        onChange={(r) => r && updateComp(i, { role: r, wheel_position: r === "wheel" ? c.wheel_position : null })} />
                      {c.role === "wheel" && (
                        <Select size="xs" w={60} data={["FL", "FR", "RL", "RR"]} value={c.wheel_position} onChange={(p) => updateComp(i, { wheel_position: p as any })} />
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <MultiSelect size="xs" w={112} data={regions} value={c.source_regions} onChange={(v) => updateComp(i, { source_regions: v })} />
                  </Table.Td>
                  <Table.Td>
                    <Checkbox size="xs" checked={c.include_in_forces} onChange={(e) => updateComp(i, { include_in_forces: e.currentTarget.checked })} />
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          {!isElement && <>
          <SectionTitle>Wheels</SectionTitle>
          {wheels.length === 0 && (
            <DiagnosticList
              items={[{ code: "w", category: "geometry", severity: "warning", summary: "No wheels found.", likely_cause: "Without wheels there are no axle loads, no aero balance and no wheel rotation.", action: "Check the nose and up directions, or set a part's role to wheel and save." } as any]}
            />
          )}
          <Stack gap={12}>
            {wheels.map((w, i) => (
              <div key={w.position}>
                <Group justify="space-between" mb={4} wrap="nowrap">
                  <Group gap={8}>
                    <Text size="sm" fw={600}>
                      {w.position}
                    </Text>
                    <Text size="xs" c="var(--ab-text-3)">
                      {w.source === "auto" ? `found automatically${w.detection_confidence != null ? `, ${Math.round((w.detection_confidence || 0) * 100)} % fit` : ""}` : "set by hand"}
                    </Text>
                  </Group>
                  <Select size="xs" w={128} data={[{ value: "vehicle_speed", label: "Rolls with road" }, { value: "custom", label: "Custom speed" }, { value: "stationary", label: "Not rotating" }]}
                    value={w.rotation} onChange={(r) => r && updateWheel(i, { rotation: r as any })} />
                </Group>
                <SimpleGrid cols={4} spacing={6}>
                  <NumField label="x" units="m" value={w.centre[0]} onCommit={(v) => updateWheel(i, { centre: [v, w.centre[1], w.centre[2]] })} />
                  <NumField label="y" units="m" value={w.centre[1]} onCommit={(v) => updateWheel(i, { centre: [w.centre[0], v, w.centre[2]] })} />
                  <NumField label="z" units="m" value={w.centre[2]} onCommit={(v) => updateWheel(i, { centre: [w.centre[0], w.centre[1], v] })} />
                  <NumField label="Radius" units="m" value={w.radius} onCommit={(v) => updateWheel(i, { radius: v })} />
                </SimpleGrid>
              </div>
            ))}
          </Stack>

          <SectionTitle>Ride height and rake</SectionTitle>
          <SimpleGrid cols={2} spacing="xs">
            <NumField label="Ride-height change" units="m" value={cfg.vehicle.ride_height_offset} decimals={4}
              onCommit={(v) => patchConfig({ vehicle: { ride_height_offset: v } }).then(bumpGeometry)} description="Body moves, wheels stay" />
            <NumField label="Rake, nose down" units="°" value={cfg.vehicle.rake_deg} decimals={3}
              onCommit={(v) => patchConfig({ vehicle: { rake_deg: v } }).then(bumpGeometry)} description="About the rear contact" />
          </SimpleGrid>
          </>}
          {isElement && (
            <>
              <SectionTitle>Position</SectionTitle>
              <Hint>The element's height over the ground and its angle are set on the Tunnel page.</Hint>
            </>
          )}

          <SectionTitle>Reference values</SectionTitle>
          <ReferenceEditor />

          <SectionTitle>Export</SectionTitle>
          <Group gap={6}>
            <Button size="xs" variant="default" leftSection={<IconDownload size={14} />}
              onClick={async () => {
                const out = await bridge?.saveFile({ title: "Export the posed vehicle", defaultPath: "vehicle.stl", filters: [{ name: "STL", extensions: ["stl"] }] });
                if (out) run("export", () => api.post(`/api/p/${pid()}/geometry/export`, { path: out, format: "stl" }), `Exported ${out}`);
              }}>
              Export STL
            </Button>
            <Button size="xs" variant="subtle" leftSection={<IconRefresh size={14} />} loading={busy === "derive"} onClick={() => run("derive", () => api.post(`/api/p/${pid()}/geometry/derive`))}>
              Recompute reference values
            </Button>
          </Group>
        </>
      )}
    </>
  );

  return (
    <PageLayout
      side={side}
      title="Geometry"
      lede={isElement ? "Import the element (wing, flap, splitter), check that it faces the right way, and review its parts."
        : "Import the vehicle, check that it faces the right way, and review the parts and wheels found in it."}
      footer={rev ? (
        <Button fullWidth variant="default" rightSection={<IconArrowRight size={15} />} onClick={() => setSection("tunnel")}>
          Continue to the tunnel
        </Button>
      ) : undefined}
    >
      {cfg.geometry.revision_id ? (
        <Viewport3D onReady={(v) => (viewer.current = v)}>
          {pending && (
            <div className="ab-viewport-note">
              <Loader size={12} color="ice" />
              Finding wheels and recomputing reference values…
            </div>
          )}
        </Viewport3D>
      ) : (
        <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", background: "var(--ab-viewport)" }}>
          <Stack align="center" gap={6} maw={360}>
            <div className="ab-page-title" style={{ fontSize: 19, color: "var(--ab-text-2)" }}>
              No vehicle yet
            </div>
            <Text size="sm" c="dimmed" ta="center">
              Import a surface file to start. Everything else in the workflow is set up from it.
            </Text>
          </Stack>
        </div>
      )}
    </PageLayout>
  );
}

function ReferenceEditor() {
  const { project, patchConfig } = useApp();
  const ref = project!.config.reference;
  const [confirm, setConfirm] = useState(false);
  const commit = async (patch: Record<string, unknown>) => {
    const p = ref.locked ? { ...patch, confirm_after_results: confirm } : patch;
    try {
      await patchConfig({ reference: p });
    } catch (e) {
      notifyError(e);
    }
  };
  const locked = ref.locked && !confirm;
  return (
    <Stack gap={8}>
      {ref.locked && (
        <div className="ab-diag" style={{ ["--sev" as string]: "var(--ab-warn)" }}>
          <div className="ab-diag-body" style={{ marginTop: 0 }}>
            Results exist, so the reference values are locked to keep coefficients comparable.
          </div>
          <Checkbox size="xs" mt={6} label="Allow changing them anyway" checked={confirm} onChange={(e) => setConfirm(e.currentTarget.checked)} />
        </div>
      )}
      <SegmentedControl size="xs" fullWidth disabled={locked} value={ref.area_source}
        onChange={(v) => {
          if (v === "computed_frontal") commit({ area_source: v, area: ref.area_computed });
          else if (v === "computed_planform") commit({ area_source: v, area: ref.area_planform_computed });
        }}
        data={[
          { value: "computed_frontal", label: "Frontal area" },
          { value: "computed_planform", label: "Planform (from above)" },
          { value: "manual", label: "Set by hand", disabled: true },
        ]} />
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Area" units="m²" value={ref.area} decimals={5} disabled={locked}
          description={[ref.area_computed != null && `frontal ${fmt(ref.area_computed, 4)}`, ref.area_planform_computed != null && `planform ${fmt(ref.area_planform_computed, 4)}`]
            .filter(Boolean).join(", ") || undefined}
          onCommit={(v) => commit({ area: v, area_source: "manual" })} />
        <NumField label="Length" units="m" value={ref.length} decimals={4} disabled={locked}
          description={ref.length_source === "wheelbase" ? "Wheelbase" : ref.length_source === "vehicle_length" ? "Vehicle length" : "Set by hand"}
          onCommit={(v) => commit({ length: v, length_source: "manual" })} />
        <NumField label="Air density" units="kg/m³" value={ref.density} decimals={4} disabled={locked} onCommit={(v) => commit({ density: v })} />
        <Select size="xs" label="Force axes" data={[{ value: "body", label: "Vehicle (body)" }, { value: "wind", label: "Wind" }]} value={ref.axes}
          disabled={locked} onChange={(v) => v && commit({ axes: v })} />
      </SimpleGrid>
      <Hint>
        Moment centre ({ref.moment_centre_source.replace(/_/g, " ")}): {ref.moment_centre ? ref.moment_centre.map((x) => fmt(x, 3)).join(", ") : "—"} m
      </Hint>
      <Hint>Wings and splitters are usually compared on their planform area; vehicles on their frontal area.</Hint>
    </Stack>
  );
}
