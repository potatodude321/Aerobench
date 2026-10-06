import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  NumberInput,
  Paper,
  ScrollArea,
  Select,
  SimpleGrid,
  Slider,
  Stack,
  Table,
  Tabs,
  Text,
  Tooltip,
} from "@mantine/core";
import { IconCamera, IconExternalLink, IconEye, IconEyeOff, IconTrash } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { bridge } from "../api/bridge";
import { api } from "../api/client";
import { CoefficientChart, ResidualChart } from "../charts/RunCharts";
import { ColorLegend, fmt } from "../components/ColorLegend";
import { notifyError, notifyOk } from "../components/notify";
import { DiagnosticList, EmptyState, KeyValue, Kpi, SectionTitle, StatusBadge } from "../components/ui";
import { pid, useApp } from "../state/store";
import { decodeAbg } from "../viewport/abg";
import { COLORMAPS } from "../viewport/colormaps";
import { Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";

export interface Layer {
  id: string;
  label: string;
  op: string;
  params: any;
  field: string;
  colormap: string;
  range: [number, number];
  dataRange: [number, number];
  visible: boolean;
  opacity: number;
  units?: string;
}

export function useFieldInfo() {
  const [info, setInfo] = useState<Record<string, any>>({});
  useEffect(() => {
    api.get(`/api/p/${pid()}/viz/fields`).then(setInfo).catch(() => undefined);
  }, []);
  return info;
}

/** Load a preset/extraction into a viewer; returns the layer description. */
export async function loadLayer(viewer: Viewer, runId: string, preset: any, fieldInfo: Record<string, any>, idSuffix = ""): Promise<Layer> {
  const res = await api.post(`/api/p/${pid()}/runs/${runId}/viz/extract`, { op: preset.op, params: preset.params });
  const buf = await api.binary(`/api/p/${pid()}/runs/${runId}/viz/payload/${res.key}`);
  const abg = decodeAbg(buf);
  const field = preset.params.field || "U";
  const fi = fieldInfo[field] || {};
  const dataRange = (abg.header.scalar_range || [0, 1]) as [number, number];
  const range = (preset.range || fi.range || dataRange) as [number, number];
  const layer: Layer = {
    id: preset.id + idSuffix,
    label: preset.label,
    op: preset.op,
    params: preset.params,
    field,
    colormap: fi.colormap || "Cool to Warm",
    range,
    dataRange,
    visible: true,
    opacity: 1,
    units: fi.units,
  };
  viewer.setResult(layer.id, abg, { colormap: layer.colormap, range: layer.range, opacity: 1 });
  return layer;
}

export function ResultsPage() {
  const { runs, selectedRunId, selectRun, project } = useApp();
  const completed = runs.filter((r) => ["completed", "stopped", "interrupted"].includes(r.status) && r.metrics);
  const runId = selectedRunId && completed.find((r) => r.id === selectedRunId) ? selectedRunId : completed[0]?.id;
  const [run, setRun] = useState<any>(null);
  const [series, setSeries] = useState<any>(null);
  const [presets, setPresets] = useState<any[]>([]);
  const [layers, setLayers] = useState<Layer[]>([]);
  const [loadingPreset, setLoadingPreset] = useState<string | null>(null);
  const [vizStatus, setVizStatus] = useState<any>(null);
  const [activeLayer, setActiveLayer] = useState<string | null>(null);
  const [shot, setShot] = useState<string | null>(null);
  const viewer = useRef<Viewer | null>(null);
  const fieldInfo = useFieldInfo();

  useEffect(() => {
    api.get(`/api/p/${pid()}/viz/status`).then(setVizStatus).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!runId) return;
    setRun(null);
    setLayers([]);
    viewer.current?.clearResults();
    (async () => {
      try {
        const r = await api.get(`/api/p/${pid()}/runs/${runId}`);
        setRun(r);
        setSeries(await api.get(`/api/p/${pid()}/runs/${runId}/series`));
        const ps = await api.get(`/api/p/${pid()}/runs/${runId}/viz/presets`).catch(() => []);
        setPresets(ps);
      } catch (e) {
        notifyError(e);
      }
    })();
  }, [runId]);

  // default scene: Cp on the body
  useEffect(() => {
    if (!run || !presets.length || !viewer.current || layers.length || !vizStatus?.available) return;
    const cp = presets.find((p) => p.id === "cp_body");
    if (cp) addPreset(cp, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, presets, vizStatus]);

  const addPreset = async (p: any, fit = false) => {
    if (!viewer.current || !runId) return;
    setLoadingPreset(p.id);
    try {
      const layer = await loadLayer(viewer.current, runId, p, fieldInfo);
      setLayers((ls) => [...ls.filter((l) => l.id !== layer.id), layer]);
      setActiveLayer(layer.id);
      if (fit) viewer.current.view("iso");
    } catch (e) {
      notifyError(e, `Could not create “${p.label}”`);
    } finally {
      setLoadingPreset(null);
    }
  };

  const updateLayer = (id: string, patch: Partial<Layer>) => {
    setLayers((ls) =>
      ls.map((l) => {
        if (l.id !== id) return l;
        const n = { ...l, ...patch };
        viewer.current?.recolor(id, { colormap: n.colormap, range: n.range, opacity: n.opacity, visible: n.visible });
        return n;
      }),
    );
  };
  const removeLayer = (id: string) => {
    viewer.current?.removeResult(id);
    setLayers((ls) => ls.filter((l) => l.id !== id));
  };

  const paraviewShot = async (presetId: string) => {
    try {
      const r = await api.post(`/api/p/${pid()}/runs/${runId}/viz/screenshot`, { preset_id: presetId, size: [1600, 900] });
      setShot(api.imageUrl(`/api/p/${pid()}/runs/${runId}/viz/image/${r.name}`));
    } catch (e) {
      notifyError(e);
    }
  };

  const openInParaView = async () => {
    if (!bridge || !run) return;
    const exe = vizStatus?.paraview;
    const res = await bridge.openParaView(exe, `${project!.root}/${run.path}/case.foam`);
    if (!res.ok) notifyError(new Error(res.error));
    else notifyOk("ParaView opened with this case.");
  };

  if (!completed.length) return <EmptyState title="No results yet">Run a simulation to see aerodynamic results here.</EmptyState>;
  const m = run?.metrics?.metrics || {};
  const res = run?.metrics?.result || {};
  const conv = run?.convergence;
  const comps = run?.metrics?.components || {};
  const op = run?.operating || {};
  const active = layers.find((l) => l.id === activeLayer) || layers[layers.length - 1];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", padding: 10, gap: 10, boxSizing: "border-box" }}>
      <Group justify="space-between">
        <Group gap="xs">
          <Select size="xs" w={420} value={runId} onChange={(v) => selectRun(v)} data={completed.map((r) => ({ value: r.id, label: `${r.label} — ${r.id}` }))} />
          {run && <StatusBadge status={run.status} />}
          {conv && <StatusBadge status={conv.status} />}
        </Group>
        <Group gap="xs">
          {bridge && vizStatus?.paraview && (
            <Button size="xs" variant="default" leftSection={<IconExternalLink size={14} />} onClick={openInParaView}>
              Open in ParaView
            </Button>
          )}
        </Group>
      </Group>
      {run && (
        <SimpleGrid cols={{ base: 3, lg: 6 }} spacing="xs">
          <Kpi label="Cd" value={m.Cd} digits={4} sub={`CdA ${fmt(m.CdA, 3)} m² · ${fmt(m.drag_N, 1)} N`} sign="Drag coefficient, + rearward" />
          <Kpi label="Cl" value={m.Cl} digits={4} sub={`Downforce ${fmt(m.downforce_N, 1)} N`} sign="Lift coefficient, + up (downforce = −lift)" />
          <Kpi label="Front downforce" value={m.downforce_front_N} units="N" digits={1} sub={`Cl front ${fmt(m.Cl_front, 4)}`} />
          <Kpi label="Rear downforce" value={m.downforce_rear_N} units="N" digits={1} sub={`Cl rear ${fmt(m.Cl_rear, 4)}`} />
          <Kpi label="Aero balance (front)" value={m.balance_front_pct} units="%" digits={1}
            color={res.balance_ill_conditioned ? "yellow" : undefined}
            sub={res.balance_ill_conditioned ? "ill-conditioned: net lift" : "front DF / total DF"} />
          <Kpi label="Side force Cs" value={m.Cs} digits={4} sub={`Yaw moment ${fmt(m.yaw_Nm, 1)} N·m`} />
        </SimpleGrid>
      )}
      <div style={{ flex: 1, display: "flex", gap: 10, minHeight: 0 }}>
        <Paper withBorder radius="md" style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          <Viewport3D onReady={(v) => (viewer.current = v)}>
            {active && <ColorLegend title={fieldInfo[active.field]?.label || active.field} colormap={active.colormap} range={active.range} units={active.units} />}
            {loadingPreset && (
              <Group style={{ position: "absolute", top: 12, left: 12 }} gap={6}>
                <Loader size="xs" />
                <Text size="xs">ParaView is processing…</Text>
              </Group>
            )}
          </Viewport3D>
        </Paper>
        <Paper withBorder radius="md" style={{ width: 360, display: "flex", flexDirection: "column" }}>
          <Tabs defaultValue="viz" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <Tabs.List>
              <Tabs.Tab value="viz">Visualise</Tabs.Tab>
              <Tabs.Tab value="summary">Summary</Tabs.Tab>
              <Tabs.Tab value="history">History</Tabs.Tab>
            </Tabs.List>
            <ScrollArea style={{ flex: 1 }} p="sm">
              <Tabs.Panel value="viz">
                {!vizStatus?.available && (
                  <Alert color="yellow" variant="light" p="xs" mb="xs">
                    <Text size="xs">{vizStatus?.message || "ParaView is not available; install ParaView 6.2 for field visualisation."}</Text>
                  </Alert>
                )}
                <SectionTitle>Automotive views</SectionTitle>
                <Stack gap={4}>
                  {presets.map((p) => (
                    <Group key={p.id} justify="space-between" wrap="nowrap">
                      <Button size="compact-xs" variant={layers.some((l) => l.id === p.id) ? "light" : "subtle"} onClick={() => addPreset(p)}
                        loading={loadingPreset === p.id} disabled={!vizStatus?.available} style={{ flex: 1 }} justify="flex-start">
                        {p.label}
                      </Button>
                      {(p.op === "surface" || p.op === "slice") && (
                        <Tooltip label="High-quality image rendered by ParaView">
                          <ActionIcon size="sm" variant="subtle" onClick={() => paraviewShot(p.id)} disabled={!vizStatus?.available}>
                            <IconCamera size={14} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Group>
                  ))}
                </Stack>
                <SectionTitle>Layers</SectionTitle>
                <Stack gap={6}>
                  {layers.map((l) => (
                    <Paper key={l.id} withBorder p={6} onClick={() => setActiveLayer(l.id)} style={{ borderColor: active?.id === l.id ? "var(--mantine-color-cyan-7)" : undefined }}>
                      <Group justify="space-between" wrap="nowrap">
                        <Text size="xs" fw={600} lineClamp={1}>
                          {l.label}
                        </Text>
                        <Group gap={2} wrap="nowrap">
                          <ActionIcon size="sm" variant="subtle" onClick={() => updateLayer(l.id, { visible: !l.visible })}>
                            {l.visible ? <IconEye size={14} /> : <IconEyeOff size={14} />}
                          </ActionIcon>
                          <ActionIcon size="sm" variant="subtle" color="red" onClick={() => removeLayer(l.id)}>
                            <IconTrash size={14} />
                          </ActionIcon>
                        </Group>
                      </Group>
                      {active?.id === l.id && (
                        <Stack gap={4} mt={4}>
                          <Select size="xs" data={COLORMAPS} value={l.colormap} onChange={(c) => c && updateLayer(l.id, { colormap: c })} />
                          <Group grow gap={4}>
                            <NumberInput size="xs" label="min" value={l.range[0]} decimalScale={4} onChange={(v) => typeof v === "number" && updateLayer(l.id, { range: [v, l.range[1]] })} />
                            <NumberInput size="xs" label="max" value={l.range[1]} decimalScale={4} onChange={(v) => typeof v === "number" && updateLayer(l.id, { range: [l.range[0], v] })} />
                          </Group>
                          <Group justify="space-between">
                            <Text size="xs" c="dimmed">
                              data {fmt(l.dataRange[0], 3)} … {fmt(l.dataRange[1], 3)}
                            </Text>
                            <Button size="compact-xs" variant="subtle" onClick={() => updateLayer(l.id, { range: l.dataRange })}>
                              fit
                            </Button>
                          </Group>
                          <Text size="xs">Opacity</Text>
                          <Slider size="xs" min={0.1} max={1} step={0.05} value={l.opacity} onChange={(v) => updateLayer(l.id, { opacity: v })} />
                        </Stack>
                      )}
                    </Paper>
                  ))}
                </Stack>
                {shot && (
                  <>
                    <SectionTitle right={<Button size="compact-xs" variant="subtle" onClick={() => setShot(null)}>close</Button>}>ParaView render</SectionTitle>
                    <img src={shot} style={{ width: "100%", borderRadius: 6 }} />
                  </>
                )}
              </Tabs.Panel>
              <Tabs.Panel value="summary">
                {run && (
                  <Stack gap="xs">
                    <SectionTitle>Convergence</SectionTitle>
                    <Text size="xs">{conv?.summary}</Text>
                    <Text size="xs" c="dimmed">
                      Averaged over iterations {run.metrics?.averaging_window?.map((x: number) => fmt(x, 0)).join("–")}
                    </Text>
                    <SectionTitle>Operating condition</SectionTitle>
                    <KeyValue rows={[["Speed", `${fmt(op.speed, 2)} m/s`], ["Yaw", `${fmt(op.yaw_deg, 2)}°`], ["Ground", op.ground], ["Wheels rotating", op.wheels_rotate ? "yes" : "no"]]} />
                    <SectionTitle>Reference</SectionTitle>
                    <KeyValue rows={[
                      ["Area", `${fmt(run.metrics?.reference?.area, 4)} m²`],
                      ["Length", `${fmt(run.metrics?.reference?.length, 4)} m`],
                      ["q", `${fmt(run.metrics?.reference?.q, 1)} Pa`],
                      ["Axes", run.metrics?.reference?.axes],
                    ]} />
                    <SectionTitle>Components</SectionTitle>
                    <Table className="compact">
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>Part</Table.Th>
                          <Table.Th>Cd</Table.Th>
                          <Table.Th>Cl</Table.Th>
                          <Table.Th>DF [N]</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {Object.entries(comps).map(([k, c]: any) => (
                          <Table.Tr key={k}>
                            <Table.Td>{k}</Table.Td>
                            <Table.Td>{fmt(c.Cd, 4)}</Table.Td>
                            <Table.Td>{fmt(c.Cl, 4)}</Table.Td>
                            <Table.Td>{fmt(c.downforce_N, 1)}</Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                    {run.metrics?.pressure_viscous && (
                      <Text size="xs" c="dimmed">
                        Drag split: pressure {fmt(run.metrics.pressure_viscous.Cd_pressure, 4)}, viscous {fmt(run.metrics.pressure_viscous.Cd_viscous, 4)}
                      </Text>
                    )}
                    <SectionTitle>y+ (wall patches)</SectionTitle>
                    <Table className="compact">
                      <Table.Tbody>
                        {Object.entries(run.metrics?.yplus || {}).filter(([k]) => k !== "ground").map(([k, v]: any) => (
                          <Table.Tr key={k}>
                            <Table.Td>{k}</Table.Td>
                            <Table.Td>avg {fmt(v.average, 1)}</Table.Td>
                            <Table.Td>max {fmt(v.max, 0)}</Table.Td>
                          </Table.Tr>
                        ))}
                      </Table.Tbody>
                    </Table>
                    <DiagnosticList items={run.diagnostics} />
                  </Stack>
                )}
              </Tabs.Panel>
              <Tabs.Panel value="history">
                <Text size="xs" fw={700}>
                  Coefficients
                </Text>
                <CoefficientChart series={series} keys={["Cd", "Cl"]} window={run?.metrics?.averaging_window} height={220} />
                <CoefficientChart series={series} keys={["balance_front_pct"]} window={run?.metrics?.averaging_window} height={180} />
                <Text size="xs" fw={700}>
                  Residuals
                </Text>
                <ResidualChart series={series} height={220} />
                <Badge size="xs" variant="outline" mt="xs">
                  dashboard values are averages over the shaded window
                </Badge>
              </Tabs.Panel>
            </ScrollArea>
          </Tabs>
        </Paper>
      </div>
    </div>
  );
}
