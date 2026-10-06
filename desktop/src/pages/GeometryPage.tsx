import { Alert, Badge, Button, Checkbox, FileButton, Group, MultiSelect, Paper, Select, SimpleGrid, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconDownload, IconFileImport, IconUpload, IconRefresh, IconTool, IconWand } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { bridge, isDesktop } from "../api/bridge";
import { api } from "../api/client";
import type { GeometryComponent, Wheel } from "../api/types";
import { fmt } from "../components/ColorLegend";
import { notifyError, notifyInfo, notifyOk } from "../components/notify";
import { DiagnosticList, KeyValue, NumField, PageLayout, SectionTitle, StatusBadge } from "../components/ui";
import { pid, useApp } from "../state/store";
import { decodeAbg } from "../viewport/abg";
import { Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";

const ROLES = ["body", "wheel", "wing", "spoiler", "splitter", "diffuser", "floor", "mirror", "cooling_inlet", "cooling_outlet", "stilt", "other"];
const AXES = ["+x", "-x", "+y", "-y", "+z", "-z"];
const UNITS = ["m", "mm", "cm", "in", "ft"];

export function GeometryPage() {
  const { project, refreshProject, geometryVersion, bumpGeometry, patchConfig } = useApp();
  const [geo, setGeo] = useState<any>(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [comps, setComps] = useState<GeometryComponent[]>([]);
  const [wheels, setWheels] = useState<Wheel[]>([]);
  const [dirty, setDirty] = useState(false);
  const [highlight, setHighlight] = useState<string | null>(null);
  const viewer = useRef<Viewer | null>(null);
  const cfg = project!.config;

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

  // viewport content
  useEffect(() => {
    const v = viewer.current;
    if (!v || !cfg.geometry.revision_id) return;
    let cancelled = false;
    api
      .binary(`/api/p/${pid()}/geometry/preview.abg`)
      .then((buf) => {
        if (cancelled) return;
        const abg = decodeAbg(buf);
        v.setGeometry(abg);
        v.clearOverlays();
        const s = cfg.geometry.summary;
        if (s) {
          const L = s.bbox_max[0] - s.bbox_min[0];
          const z0 = abg.header.ground_z ?? 0;
          v.addGround([s.bbox_min[0] - 0.4 * L, s.bbox_min[1] - 0.6 * L, 0], [s.bbox_max[0] + 0.6 * L, s.bbox_max[1] + 0.6 * L, 0], z0);
          v.addArrow([s.bbox_min[0] - 0.35 * L, s.bbox_max[1] + 0.25 * L, z0 + 0.05], [1, 0, 0], 0.25 * L, 0x4dabf7);
          v.addLabel("flow", [s.bbox_min[0] - 0.2 * L, s.bbox_max[1] + 0.3 * L, z0 + 0.15 * L], 0x4dabf7, 0.3 * L / 1.5);
        }
        for (const w of cfg.vehicle.wheels) {
          const c = w.centre;
          const a = w.axis;
          const h = (w.width || 0.2) * 0.8;
          v.addCylinder([c[0] - a[0] * h, c[1] - a[1] * h, c[2] - a[2] * h], [c[0] + a[0] * h, c[1] + a[1] * h, c[2] + a[2] * h], w.radius * 1.02, 0xf59f00, 0.0);
          v.addLabel(w.position, [c[0], c[1] + (c[1] < 0 ? -1 : 1) * 0.35, c[2] + w.radius * 1.3], 0xf59f00, 0.3);
        }
        if (cfg.reference.moment_centre) v.addPoint(cfg.reference.moment_centre, 0xff6b6b, 0.04);
        v.view("iso");
      })
      .catch(notifyError);
    return () => {
      cancelled = true;
    };
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

  const importFile = async () => {
    let p = path;
    if (!p && bridge)
      p =
        (await bridge.openFile({
          title: "Import vehicle geometry",
          filters: [{ name: "Geometry", extensions: ["stl", "obj", "ply", "step", "stp", "iges", "igs"] }],
        })) || "";
    if (!p) return;
    const r = await run("import", () => api.post(`/api/p/${pid()}/geometry/import`, { path: p }));
    if (r?.units_auto_applied) notifyInfo(`The file was interpreted as ${r.units} (a vehicle in metres would have been implausible). Change it below if needed.`, "Units corrected");
  };

  const upload = async (f: File | null) => {
    if (!f) return;
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

  const side = (
    <Stack gap="xs">
      <SectionTitle>Import</SectionTitle>
      <Group gap="xs" align="flex-end">
        <TextInput style={{ flex: 1 }} size="xs" placeholder={isDesktop ? "Browse or paste a path…" : "Path on the engine machine"} value={path} onChange={(e) => setPath(e.currentTarget.value)} />
        <Button size="xs" leftSection={<IconFileImport size={14} />} onClick={importFile} loading={busy === "import"}>
          Import
        </Button>
      </Group>
      {!isDesktop && (
        <FileButton onChange={upload} accept=".stl,.obj,.ply">
          {(props) => (
            <Button {...props} size="xs" variant="default" leftSection={<IconUpload size={14} />} loading={busy === "import"}>
              Upload a file from this computer…
            </Button>
          )}
        </FileButton>
      )}
      <Text size="xs" c="dimmed">
        STL (ASCII multi-solid or binary), OBJ (groups), PLY. STEP/IGES are tessellated through the CAD adapter.
      </Text>

      {rev && (
        <>
          <SectionTitle right={<Badge variant="light">{rev.kind}</Badge>}>Current geometry</SectionTitle>
          {geo.revisions.length > 1 && (
            <Select
              size="xs"
              label="Revision / variant"
              value={rev.id}
              data={geo.revisions.map((r: any) => ({ value: r.id, label: `${r.label} · ${r.kind} · ${r.created_at.slice(0, 16).replace("T", " ")}` }))}
              onChange={(v) => v && run("activate", () => api.post(`/api/p/${pid()}/geometry/activate`, { revision_id: v }), "Revision activated")}
            />
          )}
          <KeyValue
            rows={[
              ["Source", rev.source_filename || "—"],
              ["Triangles", report?.n_faces?.toLocaleString()],
              ["Regions", regions.length],
              ["Length × width × height", s ? `${fmt(s.bbox_max[0] - s.bbox_min[0])} × ${fmt(s.bbox_max[1] - s.bbox_min[1])} × ${fmt(s.bbox_max[2] - s.bbox_min[2])} m` : "—"],
              ["Surface area", s ? `${fmt(s.surface_area)} m²` : "—"],
              ["Closed / outward", report ? `${report.closed ? "yes" : "no"} / ${report.outward_normals === null ? "n/a" : report.outward_normals ? "yes" : "no"}` : "—"],
            ]}
          />
          <SimpleGrid cols={3} spacing="xs">
            <Select
              size="xs"
              label="File units"
              data={UNITS}
              value={UNITS.find((u) => Math.abs(({ m: 1, mm: 1e-3, cm: 1e-2, in: 0.0254, ft: 0.3048 } as any)[u] - cfg.geometry.transform.unit_scale) < 1e-12) || "m"}
              onChange={(u) => u && run("units", () => api.post(`/api/p/${pid()}/geometry/units`, { unit: u }), `Units set to ${u}`)}
            />
            <Tooltip label="Direction the vehicle nose points in the file">
              <Select size="xs" label="Nose points" data={AXES} value={cfg.geometry.transform.forward_axis}
                onChange={(a) => a && run("orient", () => api.post(`/api/p/${pid()}/geometry/orientation`, { forward_axis: a, up_axis: cfg.geometry.transform.up_axis }))} />
            </Tooltip>
            <Select size="xs" label="Up" data={AXES} value={cfg.geometry.transform.up_axis}
              onChange={(a) => a && run("orient", () => api.post(`/api/p/${pid()}/geometry/orientation`, { forward_axis: cfg.geometry.transform.forward_axis, up_axis: a }))} />
          </SimpleGrid>

          <SectionTitle
            right={
              <Button size="compact-xs" variant="light" leftSection={<IconTool size={12} />} loading={busy === "repair"}
                onClick={() => run("repair", () => api.post(`/api/p/${pid()}/geometry/repair`, {}), "Repaired copy created (original kept)")}>
                Repair
              </Button>
            }
          >
            Diagnostics
          </SectionTitle>
          <DiagnosticList items={report?.diagnostics} empty="No geometry problems detected." />
          {(rev.operations || []).length > 0 && (
            <Paper withBorder p={6}>
              <Text size="xs" fw={600} mb={2}>
                Repair history
              </Text>
              {(rev.operations || []).map((o: any, i: number) => (
                <Text key={i} size="xs" c="dimmed">
                  {i + 1}. {o.summary}
                </Text>
              ))}
            </Paper>
          )}

          <SectionTitle
            right={
              <Group gap={4}>
                <Button size="compact-xs" variant="subtle" leftSection={<IconWand size={12} />} loading={busy === "identify"}
                  onClick={() => run("identify", () => api.post(`/api/p/${pid()}/geometry/identify`), "Components re-identified")}>
                  Auto-identify
                </Button>
                <Button size="compact-xs" disabled={!dirty} loading={busy === "comps"}
                  onClick={() => run("comps", () => api.post(`/api/p/${pid()}/geometry/components`, { components: comps, wheels }), "Components saved")}>
                  Save
                </Button>
              </Group>
            }
          >
            Components
          </SectionTitle>
          <Table className="compact" withRowBorders={false}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Patch</Table.Th>
                <Table.Th>Role</Table.Th>
                <Table.Th>Regions</Table.Th>
                <Table.Th>F</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {comps.map((c, i) => (
                <Table.Tr key={c.id} onMouseEnter={() => setHighlight(c.id)} onMouseLeave={() => setHighlight(null)}>
                  <Table.Td>
                    <Text size="xs" className="mono">
                      {c.id}
                    </Text>
                    {c.suggested && <Badge size="xs" variant="outline">auto</Badge>}
                  </Table.Td>
                  <Table.Td>
                    <Group gap={2} wrap="nowrap">
                      <Select size="xs" w={100} data={ROLES} value={c.role} onChange={(r) => r && updateComp(i, { role: r, wheel_position: r === "wheel" ? c.wheel_position : null })} />
                      {c.role === "wheel" && (
                        <Select size="xs" w={62} data={["FL", "FR", "RL", "RR"]} value={c.wheel_position} onChange={(p) => updateComp(i, { wheel_position: p as any })} />
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>
                    <MultiSelect size="xs" w={130} data={regions} value={c.source_regions} onChange={(v) => updateComp(i, { source_regions: v })} />
                  </Table.Td>
                  <Table.Td>
                    <Tooltip label="Report forces for this component">
                      <Checkbox size="xs" checked={c.include_in_forces} onChange={(e) => updateComp(i, { include_in_forces: e.currentTarget.checked })} />
                    </Tooltip>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <SectionTitle>Wheels (CFD frame, metres)</SectionTitle>
          {wheels.length === 0 && (
            <Alert color="yellow" variant="light" p="xs">
              <Text size="xs">No wheels detected. Without wheels the axle loads, aero balance and wheel rotation are unavailable.</Text>
            </Alert>
          )}
          {wheels.map((w, i) => (
            <Paper key={w.position} withBorder p={6}>
              <Group justify="space-between" mb={4}>
                <Text size="xs" fw={700}>
                  {w.position}
                </Text>
                <Group gap={4}>
                  {w.source === "auto" && (
                    <Badge size="xs" variant="light">
                      detected {w.detection_confidence !== null ? `${Math.round((w.detection_confidence || 0) * 100)}%` : ""}
                    </Badge>
                  )}
                  <Select size="xs" w={120} data={["vehicle_speed", "custom", "stationary"]} value={w.rotation} onChange={(r) => r && updateWheel(i, { rotation: r as any })} />
                </Group>
              </Group>
              <SimpleGrid cols={4} spacing={4}>
                <NumField label="x" value={w.centre[0]} onCommit={(v) => updateWheel(i, { centre: [v, w.centre[1], w.centre[2]] })} />
                <NumField label="y" value={w.centre[1]} onCommit={(v) => updateWheel(i, { centre: [w.centre[0], v, w.centre[2]] })} />
                <NumField label="z" value={w.centre[2]} onCommit={(v) => updateWheel(i, { centre: [w.centre[0], w.centre[1], v] })} />
                <NumField label="R" value={w.radius} onCommit={(v) => updateWheel(i, { radius: v })} />
              </SimpleGrid>
            </Paper>
          ))}

          <SectionTitle>Vehicle pose</SectionTitle>
          <SimpleGrid cols={2} spacing="xs">
            <NumField label="Ride-height offset" units="m" value={cfg.vehicle.ride_height_offset} decimals={4}
              onCommit={(v) => patchConfig({ vehicle: { ride_height_offset: v } }).then(bumpGeometry)} description="Body moves, wheels stay" />
            <NumField label="Rake (nose down +)" units="deg" value={cfg.vehicle.rake_deg} decimals={3}
              onCommit={(v) => patchConfig({ vehicle: { rake_deg: v } }).then(bumpGeometry)} description="About rear contact" />
          </SimpleGrid>

          <SectionTitle>Reference values</SectionTitle>
          <ReferenceEditor />
          <Group mt="sm">
            <Button size="xs" variant="default" leftSection={<IconDownload size={14} />}
              onClick={async () => {
                const out = await bridge?.saveFile({ title: "Export posed geometry", defaultPath: "vehicle.stl", filters: [{ name: "STL", extensions: ["stl"] }] });
                if (out) run("export", () => api.post(`/api/p/${pid()}/geometry/export`, { path: out, format: "stl" }), `Exported ${out}`);
              }}>
              Export STL
            </Button>
            <Button size="xs" variant="subtle" leftSection={<IconRefresh size={14} />} onClick={() => run("derive", () => api.post(`/api/p/${pid()}/geometry/derive`))}>
              Recompute derived values
            </Button>
          </Group>
        </>
      )}
    </Stack>
  );

  return (
    <PageLayout side={side}>
      <Paper withBorder radius="md" style={{ flex: 1, overflow: "hidden" }}>
        {cfg.geometry.revision_id ? (
          <Viewport3D onReady={(v) => (viewer.current = v)} />
        ) : (
          <Stack align="center" justify="center" h="100%">
            <Text c="dimmed">Import a vehicle surface to begin.</Text>
            <StatusBadge status="queued" />
          </Stack>
        )}
      </Paper>
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
  return (
    <Stack gap={4}>
      {ref.locked && (
        <Alert color="orange" variant="light" p={6}>
          <Text size="xs">Results exist: reference values are locked so coefficients stay comparable.</Text>
          <Checkbox size="xs" mt={4} label="I understand — allow changing reference values" checked={confirm} onChange={(e) => setConfirm(e.currentTarget.checked)} />
        </Alert>
      )}
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Reference area" units="m²" value={ref.area} decimals={5} disabled={ref.locked && !confirm}
          description={ref.area_computed ? `frontal ${fmt(ref.area_computed, 4)} m² (${ref.area_source === "manual" ? "manual override" : "computed"})` : undefined}
          onCommit={(v) => commit({ area: v, area_source: "manual" })} />
        <NumField label="Reference length" units="m" value={ref.length} decimals={4} disabled={ref.locked && !confirm}
          description={ref.length_source} onCommit={(v) => commit({ length: v, length_source: "manual" })} />
        <NumField label="Density" units="kg/m³" value={ref.density} decimals={4} disabled={ref.locked && !confirm} onCommit={(v) => commit({ density: v })} />
        <Select size="xs" label="Force axes" data={[{ value: "body", label: "Body (vehicle)" }, { value: "wind", label: "Wind" }]} value={ref.axes}
          disabled={ref.locked && !confirm} onChange={(v) => v && commit({ axes: v })} />
      </SimpleGrid>
      <Text size="xs" c="dimmed">
        Moment centre ({ref.moment_centre_source.replace(/_/g, " ")}): {ref.moment_centre ? ref.moment_centre.map((x) => fmt(x, 3)).join(", ") : "—"}
      </Text>
      {ref.area_source === "manual" && !ref.locked && (
        <Button size="compact-xs" variant="subtle" onClick={() => commit({ area_source: "computed_frontal", area: ref.area_computed })}>
          Use computed frontal area
        </Button>
      )}
    </Stack>
  );
}
