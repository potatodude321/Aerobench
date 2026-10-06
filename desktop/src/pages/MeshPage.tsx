import { Alert, Badge, Button, Checkbox, Group, NumberInput, Paper, Progress, SegmentedControl, SimpleGrid, Stack, Table, Text, Tooltip } from "@mantine/core";
import { IconGridDots, IconRefresh } from "@tabler/icons-react";
import { useCallback, useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { api } from "../api/client";
import type { MeshPlan } from "../api/types";
import { fmt } from "../components/ColorLegend";
import { notifyError, notifyOk } from "../components/notify";
import { DiagnosticList, KeyValue, NumField, PageLayout, SectionTitle, StatusBadge } from "../components/ui";
import { pid, useApp } from "../state/store";
import { decodeAbg } from "../viewport/abg";
import { Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";
import { usePlan } from "./TunnelPage";

const LEVEL_COLORS = [0x495057, 0x5c7cfa, 0x339af0, 0x22b8cf, 0x20c997, 0x94d82d, 0xfcc419, 0xff922b, 0xff6b6b, 0xf06595];

export function drawPlan(v: Viewer, plan: MeshPlan, opts: { regions?: boolean; domain?: boolean; hiddenRegions?: string[] } = {}) {
  v.clearOverlays();
  const [x0, y0, z0] = plan.domain_min;
  const [x1, y1, z1] = plan.domain_max;
  if (opts.domain !== false) {
    v.addBox([x0, y0, z0], [x1, y1, z1], 0x868e96, 0.0, true);
    v.addGround([x0, y0, z0], [x1, y1, z1], plan.ground_z);
    const L = plan.vehicle_bbox_max[0] - plan.vehicle_bbox_min[0];
    v.addArrow([x0 + 0.3 * L, 0.5 * (y0 + y1), 0.5 * (z0 + z1)], [Math.cos((plan.yaw_range[0] * Math.PI) / 180), -Math.sin((plan.yaw_range[0] * Math.PI) / 180), 0], L, 0x4dabf7);
  }
  if (opts.regions) {
    for (const r of plan.regions) {
      if (opts.hiddenRegions?.includes(r.id)) continue;
      const col = LEVEL_COLORS[Math.min(r.level, LEVEL_COLORS.length - 1)];
      const s = r.shape;
      if (s.kind === "box") v.addBox(s.min, s.max, col, 0.05, true, r.id);
      else if (s.kind === "cylinder") v.addCylinder(s.point1, s.point2, s.radius, col, 0.12);
      else if (s.kind === "rotated_box") v.addRotatedBox(s.origin, s.span, s.e1, s.e3, col, 0.05);
    }
    v.addPoint(plan.location_in_mesh, 0xff922b, Math.max(0.03, plan.base_cell * 0.15));
  }
}

export function MeshPage() {
  const { project, patchConfig, geometryVersion, jobs, refreshProject } = useApp();
  const cfg = project!.config;
  const m = cfg.mesh;
  const [plan, extra, loading] = usePlan();
  const [meshes, setMeshes] = useState<any[]>([]);
  const [current, setCurrent] = useState<any>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [cores, setCores] = useState<number>(cfg.execution.n_procs);
  const viewer = useRef<Viewer | null>(null);
  const [geomLoaded, setGeomLoaded] = useState(0);

  const loadMeshes = useCallback(async () => {
    try {
      const list = await api.get(`/api/p/${pid()}/meshes`);
      setMeshes(list);
      const latest = list[0];
      setCurrent(latest ? await api.get(`/api/p/${pid()}/meshes/${latest.id}`) : null);
    } catch (e) {
      notifyError(e);
    }
  }, []);

  useEffect(() => {
    loadMeshes();
  }, [loadMeshes, jobs.recent.map((j: any) => j.status).join()]);

  useEffect(() => {
    const v = viewer.current;
    if (!v || !cfg.geometry.revision_id) return;
    api.binary(`/api/p/${pid()}/geometry/preview.abg`).then((b) => {
      v.setGeometry(decodeAbg(b));
      setGeomLoaded((x) => x + 1);
    }).catch(notifyError);
  }, [cfg.geometry.revision_id, geometryVersion]);

  useEffect(() => {
    const v = viewer.current;
    if (!v || !plan) return;
    drawPlan(v, plan, { regions: true, domain: true, hiddenRegions: hidden });
    const lo = plan.vehicle_bbox_min;
    const hi = plan.vehicle_bbox_max;
    const L = hi[0] - lo[0];
    v.view("iso", new THREE.Box3(new THREE.Vector3(lo[0] - 0.3 * L, lo[1] - 0.5 * L, lo[2]), new THREE.Vector3(hi[0] + 1.5 * L, hi[1] + 0.5 * L, hi[2] + 0.3 * L)));
  }, [plan, hidden, geomLoaded]);

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

  const setRegionLevel = (id: string, level: number | null, enabled?: boolean) => {
    if (!plan) return;
    const r = plan.regions.find((x) => x.id === id)!;
    const existing = (m.regions || []).filter((x: any) => x.id !== id);
    const entry = { id, name: r.name, kind: r.kind, shape: r.shape, level: level ?? r.level, enabled: enabled ?? true, auto: r.auto };
    patchConfig({ mesh: { regions: [...existing, entry] } });
  };

  const generate = async (force = false) => {
    setBusy(true);
    try {
      const r = await api.post(`/api/p/${pid()}/meshes`, { force, nprocs: cores });
      notifyOk(r.reused ? `An identical mesh already exists (${r.mesh_id}).` : `Meshing queued (${r.mesh_id}).`);
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
  const meshJob = current && jobs.states[current.job_id];
  const coverage = current?.snappy?.layers || [];
  const q = current?.quality;

  const side = (
    <Stack gap="xs">
      <SectionTitle right={loading && <Badge size="xs" variant="light">updating…</Badge>}>Mesh preset</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={m.preset} onChange={setPreset}
        data={[{ value: "fast", label: "Fast" }, { value: "balanced", label: "Balanced" }, { value: "high_accuracy", label: "High accuracy" }, { value: "custom", label: "Custom" }]} />
      {plan && (
        <Paper withBorder p="xs">
          <KeyValue
            rows={[
              ["Background cell (level 0)", `${fmt(plan.base_cell * 1000, 1)} mm`],
              ["Body surface cell", `${fmt(plan.body_cell * 1000, 2)} mm (level ${plan.body_level})`],
              ["Estimated cells", `${(plan.estimate_cells / 1e6).toFixed(2)} M  (${(plan.estimate_range[0] / 1e6).toFixed(1)}–${(plan.estimate_range[1] / 1e6).toFixed(1)} M)`],
              ["Estimated memory", `${plan.estimate_memory_gb[0]}–${plan.estimate_memory_gb[1]} GB`],
            ]}
          />
          <Text size="xs" c="dimmed" mt={4}>
            Estimates only — the real count is known after snappyHexMesh.
          </Text>
        </Paper>
      )}
      {m.preset === "custom" && (
        <NumField label="Background cell size" units="m" value={m.base_cell_size ?? plan?.base_cell} decimals={4} onCommit={(v) => patchConfig({ mesh: { base_cell_size: v } })} />
      )}

      <SectionTitle>Surface refinement</SectionTitle>
      <Table className="compact">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Component</Table.Th>
            <Table.Th>Min</Table.Th>
            <Table.Th>Max</Table.Th>
            <Table.Th>Cell</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {plan?.surfaces.map((s) => (
            <Table.Tr key={s.component_id}>
              <Table.Td>
                <Text size="xs" className="mono">
                  {s.component_id}
                </Text>
              </Table.Td>
              <Table.Td>
                <NumberInput size="xs" w={56} min={0} max={12} value={s.min_level} onChange={(v) => typeof v === "number" && toCustomSurface(s.component_id, v, s.max_level)} />
              </Table.Td>
              <Table.Td>
                <NumberInput size="xs" w={56} min={0} max={12} value={s.max_level} onChange={(v) => typeof v === "number" && toCustomSurface(s.component_id, s.min_level, v)} />
              </Table.Td>
              <Table.Td>
                <Text size="xs">{fmt(s.cell_size * 1000, 1)} mm</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>

      <SectionTitle>Refinement volumes</SectionTitle>
      <Table className="compact">
        <Table.Tbody>
          {plan?.regions.map((r) => (
            <Table.Tr key={r.id}>
              <Table.Td>
                <Checkbox size="xs" checked={!hidden.includes(r.id)} onChange={(e) => setHidden((h) => (e.currentTarget.checked ? h.filter((x) => x !== r.id) : [...h, r.id]))} />
              </Table.Td>
              <Table.Td>
                <Text size="xs">{r.name}</Text>
                <Text size="xs" c="dimmed">
                  {r.kind}
                </Text>
              </Table.Td>
              <Table.Td>
                <NumberInput size="xs" w={56} min={0} max={12} value={r.level} onChange={(v) => typeof v === "number" && setRegionLevel(r.id, v)} />
              </Table.Td>
              <Table.Td>
                <Text size="xs">{fmt(r.cell_size * 1000, 0)} mm</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {plan && plan.distance_levels.length > 0 && (
        <Text size="xs" c="dimmed">
          Plus near-wall bands: {plan.distance_levels.map(([d, l]) => `level ${l} within ${fmt(d * 1000, 0)} mm`).join(", ")}.
        </Text>
      )}

      <SectionTitle>Boundary layer</SectionTitle>
      <SegmentedControl size="xs" fullWidth value={lay.mode} onChange={(v) => patchConfig({ mesh: { layers: { mode: v } } })}
        data={[{ value: "target_yplus", label: "Target y+" }, { value: "relative", label: "Relative" }, { value: "none", label: "No layers" }]} />
      <SimpleGrid cols={3} spacing="xs">
        {lay.mode === "target_yplus" && (
          <NumField label="Target y+" value={lay.target_yplus} decimals={1} onCommit={(v) => patchConfig({ mesh: { layers: { target_yplus: v } } })} />
        )}
        <NumField label="Expansion" value={lay.expansion_ratio} decimals={3} onCommit={(v) => patchConfig({ mesh: { layers: { expansion_ratio: v } } })} />
        <NumField label={lay.mode === "relative" ? "Layers" : "Layers (auto)"} value={lay.n_layers ?? ld?.n_layers} decimals={0}
          onCommit={(v) => patchConfig({ mesh: { layers: { n_layers: Math.round(v) } } })} />
        {lay.mode !== "none" && (
          <NumField label="Final / surface cell" value={lay.final_relative} decimals={2} onCommit={(v) => patchConfig({ mesh: { layers: { final_relative: v } } })} />
        )}
      </SimpleGrid>
      {ld && (
        <Paper withBorder p="xs">
          <KeyValue
            rows={[
              ["First layer", `${fmt(ld.first_layer * 1000, 3)} mm`],
              ["Layers × ratio", `${ld.n_layers} × ${fmt(ld.expansion_ratio, 2)}`],
              ["Final layer", `${fmt(ld.final_layer * 1000, 2)} mm (${fmt(ld.final_relative, 2)}× cell)`],
              ["Total thickness", `${fmt(ld.total_thickness * 1000, 2)} mm`],
              ["y+ estimate", fmt(ld.yplus_estimate, 1)],
            ]}
          />
          <Text size="xs" c="dimmed" mt={4}>
            {ld.assumptions}
          </Text>
          {ld.warnings?.map((w: string, i: number) => (
            <Alert key={i} color="yellow" variant="light" p={4} mt={4}>
              <Text size="xs">{w}</Text>
            </Alert>
          ))}
        </Paper>
      )}

      <SectionTitle>Generate</SectionTitle>
      <Group align="flex-end" gap="xs">
        <NumberInput size="xs" w={90} label="Cores" min={1} max={256} value={cores} onChange={(v) => typeof v === "number" && setCores(v)} />
        <Button size="xs" leftSection={<IconGridDots size={14} />} loading={busy} onClick={() => generate(false)} disabled={!plan}>
          Generate mesh
        </Button>
        <Tooltip label="Re-mesh even if an identical mesh exists">
          <Button size="xs" variant="subtle" leftSection={<IconRefresh size={14} />} onClick={() => generate(true)}>
            Force
          </Button>
        </Tooltip>
      </Group>
      {project!.staleness.mesh.stale && (
        <Alert color="orange" variant="light" p={6}>
          <Text size="xs">The latest mesh no longer matches the current setup (geometry, tunnel or mesh settings changed). It is kept, but a new mesh is needed.</Text>
        </Alert>
      )}
      {extra?.existing && (
        <Text size="xs" c="dimmed">
          Matching mesh: {extra.existing.id} ({extra.existing.status})
        </Text>
      )}
    </Stack>
  );

  return (
    <PageLayout side={side}>
      <Paper withBorder radius="md" style={{ flex: 1, minHeight: 300, overflow: "hidden" }}>
        <Viewport3D onReady={(v) => (viewer.current = v)} />
      </Paper>
      {current && (
        <Paper withBorder radius="md" p="sm" style={{ maxHeight: "38%", overflow: "auto" }}>
          <Group justify="space-between" mb={6}>
            <Group gap="xs">
              <Text fw={700} size="sm">
                Mesh {current.id}
              </Text>
              <StatusBadge status={current.status} />
              {current.cells && <Badge variant="light">{(current.cells / 1e6).toFixed(3)} M cells</Badge>}
            </Group>
            <Text size="xs" c="dimmed">
              {current.provider} · OpenFOAM {current.foam_version}
            </Text>
          </Group>
          {meshJob && !["completed", "failed"].includes(meshJob.status) && (
            <Stack gap={4} mb="xs">
              <Progress value={(100 * meshJob.step_index) / Math.max(1, meshJob.steps.length)} animated />
              <Text size="xs" c="dimmed">
                {meshJob.steps[meshJob.step_index]?.name} ({meshJob.step_index + 1}/{meshJob.steps.length})
              </Text>
            </Stack>
          )}
          <SimpleGrid cols={{ base: 1, lg: 3 }} spacing="sm">
            <div>
              <Text size="xs" fw={700} mb={4}>
                Quality (checkMesh)
              </Text>
              {q ? (
                <KeyValue
                  rows={[
                    ["Cells", q.cells?.toLocaleString()],
                    ["Max non-orthogonality", `${fmt(q.max_non_ortho, 1)}° (avg ${fmt(q.avg_non_ortho, 1)}°)`],
                    ["Max skewness", fmt(q.max_skewness, 2)],
                    ["Max aspect ratio", fmt(q.max_aspect_ratio, 1)],
                    ["Failed checks", q.failed_checks],
                    ["Regions", q.n_regions],
                  ]}
                />
              ) : (
                <Text size="xs" c="dimmed">
                  Not available yet.
                </Text>
              )}
            </div>
            <div>
              <Text size="xs" fw={700} mb={4}>
                Layer coverage {current.snappy?.overall_layer_coverage != null && `— ${(current.snappy.overall_layer_coverage * 100).toFixed(0)}% overall`}
              </Text>
              <Table className="compact">
                <Table.Tbody>
                  {coverage.map((l: any) => (
                    <Table.Tr key={l.patch}>
                      <Table.Td>
                        <Text size="xs" className="mono">
                          {l.patch}
                        </Text>
                      </Table.Td>
                      <Table.Td w={120}>
                        <Progress value={Math.min(100, l.coverage * 100)} color={l.coverage > 0.7 ? "teal" : l.coverage > 0.4 ? "yellow" : "red"} />
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs">
                          {fmt(l.mesh_layers, 2)}/{l.target_layers}
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
            <div>
              <Text size="xs" fw={700} mb={4}>
                Findings
              </Text>
              <DiagnosticList items={(current.diagnostics || []).filter((d: any) => typeof d === "object")} empty="No mesh problems detected." />
            </div>
          </SimpleGrid>
          {meshes.length > 1 && (
            <Text size="xs" c="dimmed" mt="xs">
              {meshes.length} meshes in this project; earlier meshes are kept for the runs that used them.
            </Text>
          )}
        </Paper>
      )}
    </PageLayout>
  );
}
