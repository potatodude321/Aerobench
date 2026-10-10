import {
  ActionIcon,
  Button,
  Group,
  Loader,
  NumberInput,
  ScrollArea,
  Select,
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
import { notifyError, notifyInfo, notifyOk } from "../components/notify";
import { OptimiseMenu } from "./explore/QuickGoals";
import { DiagnosticList, EmptyState, Hint, KeyValue, Kpi, Readouts, SectionTitle, StatusBadge } from "../components/ui";
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
  /** share of points without a value (shown grey) */
  missing?: number;
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
  // a view without values (grey) must never pass silently
  const sc = abg.scalars;
  if (sc && sc.length) {
    let finite = 0;
    for (let i = 0; i < sc.length; i++) if (Number.isFinite(sc[i])) finite++;
    layer.missing = 1 - finite / sc.length;
  }
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

  // re-read when the selected run's results change (finished, resumed, re-read from the case)
  const sel = runs.find((r) => r.id === runId);
  const runStamp = sel ? `${sel.status}|${sel.iterations}|${sel.ended_at}|${sel.metrics?.Cd}` : "";
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
  }, [runId, runStamp]);

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
      if ((layer.missing ?? 0) > 0.05)
        notifyInfo(`${Math.round((layer.missing ?? 0) * 100)} % of “${p.label}” has no ${layer.field} values and is shown grey. Re-read the results from the case on the Run page, or open the run in ParaView to check the files.`, "Incomplete field");
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
    let target = `${project!.root}/${run.path}/case.foam`;
    try {
      target = (await api.post(`/api/p/${pid()}/runs/${run.id}/viz/paraview-gui`)).script;
    } catch {
      /* fall back to opening the case file directly */
    }
    const res = await bridge.openParaView(exe, target);
    if (!res.ok) notifyError(new Error(res.error));
    else notifyOk("ParaView opened with this case.");
  };

  if (!completed.length) return <EmptyState title="No results yet">Run a simulation to see aerodynamic results here.</EmptyState>;
  const m = run?.metrics?.metrics || {};
  const res = run?.metrics?.result || {};
  const conv = run?.convergence;
  const comps = run?.metrics?.components || {};
  const op = run?.operating || {};
  const isElement = useApp.getState().project?.config.application === "aero_element";
  const active = layers.find((l) => l.id === activeLayer) || layers[layers.length - 1];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "10px 18px", background: "var(--ab-surface)" }}>
        <Group gap={16} wrap="nowrap">
          <h1 className="ab-page-title" style={{ fontSize: 19 }}>
            Results
          </h1>
          <Select size="xs" w={380} value={runId} onChange={(v) => selectRun(v)} data={completed.map((r) => ({ value: r.id, label: `${r.label} — ${r.id}` }))} />
          {run && <StatusBadge status={run.status} />}
          {conv && <StatusBadge status={conv.status} />}
        </Group>
        <Group gap={8} wrap="nowrap">
          {run && <OptimiseMenu />}
          {bridge && vizStatus?.paraview && (
            <Button size="xs" variant="default" leftSection={<IconExternalLink size={14} />} onClick={openInParaView}>
              Open in ParaView
            </Button>
          )}
        </Group>
      </div>
      {run && (
        <Readouts>
          <Kpi label="Drag coefficient Cd" value={m.Cd} digits={4} sub={`CdA ${fmt(m.CdA, 3)} m², drag ${fmt(m.drag_N, 1)} N`} sign="Positive rearward" />
          <Kpi label="Lift coefficient Cl" value={m.Cl} digits={4} sub={`Downforce ${fmt(m.downforce_N, 1)} N`} sign="Positive up; downforce = −lift" />
          {isElement ? (
            <>
              <Kpi label="Downforce" value={m.downforce_N} units="N" digits={1} sub={`Drag ${fmt(m.drag_N, 1)} N`} />
              <Kpi label="Efficiency" value={m.efficiency} digits={2} sub="Downforce ÷ drag" />
            </>
          ) : (
            <>
              <Kpi label="Front downforce" value={m.downforce_front_N} units="N" digits={1} sub={`Cl front ${fmt(m.Cl_front, 4)}`} />
              <Kpi label="Rear downforce" value={m.downforce_rear_N} units="N" digits={1} sub={`Cl rear ${fmt(m.Cl_rear, 4)}`} />
              <Kpi label="Aero balance, front" value={m.balance_front_pct} units="%" digits={1}
                color={res.balance_ill_conditioned ? "var(--ab-warn)" : undefined}
                sub={res.balance_ill_conditioned ? "Not meaningful: the car makes net lift" : "Front ÷ total downforce"} />
            </>
          )}
          <Kpi label="Side force Cs" value={m.Cs} digits={4} sub={`Yaw moment ${fmt(m.yaw_Nm, 1)} N·m`} />
        </Readouts>
      )}
      <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
        <div style={{ flex: 1, position: "relative", minWidth: 0, display: "flex" }}>
          <Viewport3D onReady={(v) => (viewer.current = v)}>
            {active && <ColorLegend title={fieldInfo[active.field]?.label || active.field} colormap={active.colormap} range={active.range} units={active.units} />}
            {loadingPreset && (
              <div className="ab-viewport-note">
                <Loader size={12} color="ice" />
                ParaView is preparing the view…
              </div>
            )}
          </Viewport3D>
        </div>
        <aside className="ab-inspector" style={{ width: 360, borderRight: 0, borderLeft: "1px solid var(--ab-line)" }}>
          <Tabs defaultValue="viz" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
            <Tabs.List px={10}>
              <Tabs.Tab value="viz">Views</Tabs.Tab>
              <Tabs.Tab value="summary">Summary</Tabs.Tab>
              <Tabs.Tab value="history">History</Tabs.Tab>
            </Tabs.List>
            <ScrollArea style={{ flex: 1 }} scrollbarSize={6}>
              <div style={{ padding: "4px 16px 24px" }}>
                <Tabs.Panel value="viz">
                  {!vizStatus?.available && (
                    <Hint style={{ color: "var(--ab-warn)", marginTop: 12 }}>{vizStatus?.message || "ParaView is not available, so flow fields cannot be shown."}</Hint>
                  )}
                  <SectionTitle first>Standard views</SectionTitle>
                  <Stack gap={0}>
                    {presets.map((p) => {
                      const on = layers.some((l) => l.id === p.id);
                      return (
                        <Group key={p.id} justify="space-between" wrap="nowrap" gap={4}>
                          <Button size="compact-sm" variant="subtle" color={on ? "ice" : "gray"} onClick={() => addPreset(p)} loading={loadingPreset === p.id}
                            disabled={!vizStatus?.available} style={{ flex: 1, fontWeight: on ? 600 : 400 }} justify="flex-start">
                            {p.label}
                          </Button>
                          {(p.op === "surface" || p.op === "slice") && (
                            <Tooltip label="Render a high-quality image with ParaView">
                              <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => paraviewShot(p.id)} disabled={!vizStatus?.available} aria-label="ParaView image">
                                <IconCamera size={14} />
                              </ActionIcon>
                            </Tooltip>
                          )}
                        </Group>
                      );
                    })}
                  </Stack>
                  <SectionTitle>Shown</SectionTitle>
                  {layers.length === 0 && <Hint>Pick a view above.</Hint>}
                  <Stack gap={6}>
                    {layers.map((l) => (
                      <div key={l.id} onClick={() => setActiveLayer(l.id)}
                        style={{ borderLeft: `2px solid ${active?.id === l.id ? "var(--ab-accent)" : "var(--ab-line)"}`, padding: "2px 0 4px 10px", cursor: "pointer" }}>
                        <Group justify="space-between" wrap="nowrap">
                          <Text size="sm" fw={active?.id === l.id ? 600 : 400} lineClamp={1}>
                            {l.label}
                          </Text>
                          <Group gap={2} wrap="nowrap">
                            <ActionIcon size="sm" variant="subtle" color="gray" onClick={(e) => { e.stopPropagation(); updateLayer(l.id, { visible: !l.visible }); }} aria-label={l.visible ? "Hide" : "Show"}>
                              {l.visible ? <IconEye size={14} /> : <IconEyeOff size={14} />}
                            </ActionIcon>
                            <ActionIcon size="sm" variant="subtle" color="gray" onClick={(e) => { e.stopPropagation(); removeLayer(l.id); }} aria-label="Remove">
                              <IconTrash size={14} />
                            </ActionIcon>
                          </Group>
                        </Group>
                        {(l.missing ?? 0) > 0.05 && <Hint style={{ color: "var(--ab-warn)" }}>{Math.round((l.missing ?? 0) * 100)} % without values (grey)</Hint>}
                        {active?.id === l.id && (
                          <Stack gap={6} mt={6}>
                            <Select size="xs" label="Colour map" data={COLORMAPS} value={l.colormap} onChange={(c) => c && updateLayer(l.id, { colormap: c })} />
                            <Group grow gap={6}>
                              <NumberInput size="xs" label="From" value={l.range[0]} decimalScale={4} onChange={(v) => typeof v === "number" && updateLayer(l.id, { range: [v, l.range[1]] })} />
                              <NumberInput size="xs" label="To" value={l.range[1]} decimalScale={4} onChange={(v) => typeof v === "number" && updateLayer(l.id, { range: [l.range[0], v] })} />
                            </Group>
                            <Group justify="space-between">
                              <Hint>
                                Data range {fmt(l.dataRange[0], 3)} to {fmt(l.dataRange[1], 3)}
                              </Hint>
                              <Button size="compact-xs" variant="subtle" onClick={() => updateLayer(l.id, { range: l.dataRange })}>
                                Fit to data
                              </Button>
                            </Group>
                            <Text size="xs" c="dimmed">
                              Opacity
                            </Text>
                            <Slider size="xs" min={0.1} max={1} step={0.05} value={l.opacity} onChange={(v) => updateLayer(l.id, { opacity: v })} label={(v) => `${Math.round(v * 100)} %`} />
                          </Stack>
                        )}
                      </div>
                    ))}
                  </Stack>
                  {shot && (
                    <>
                      <SectionTitle right={<Button size="compact-xs" variant="subtle" onClick={() => setShot(null)}>Close</Button>}>ParaView image</SectionTitle>
                      <img src={shot} style={{ width: "100%", borderRadius: 4 }} alt="ParaView rendering" />
                    </>
                  )}
                </Tabs.Panel>
                <Tabs.Panel value="summary">
                  {run && (
                    <>
                      <SectionTitle first>Convergence</SectionTitle>
                      <Text size="xs">{conv?.summary}</Text>
                      <Hint style={{ marginTop: 4 }}>Values are averaged over iterations {run.metrics?.averaging_window?.map((x: number) => fmt(x, 0)).join("–")}.</Hint>
                      <SectionTitle>Condition</SectionTitle>
                      <KeyValue rows={[["Speed", `${fmt(op.speed, 2)} m/s`], ["Yaw", `${fmt(op.yaw_deg, 2)}°`], ["Ground", op.ground], ["Wheels rotating", op.wheels_rotate ? "Yes" : "No"]]} />
                      <SectionTitle>Reference values</SectionTitle>
                      <KeyValue rows={[
                        ["Area", `${fmt(run.metrics?.reference?.area, 4)} m²`],
                        ["Length", `${fmt(run.metrics?.reference?.length, 4)} m`],
                        ["Dynamic pressure q", `${fmt(run.metrics?.reference?.q, 1)} Pa`],
                        ["Axes", run.metrics?.reference?.axes],
                      ]} />
                      <SectionTitle>Per part</SectionTitle>
                      <Table className="compact" withRowBorders={false}>
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th>Part</Table.Th>
                            <Table.Th>Cd</Table.Th>
                            <Table.Th>Cl</Table.Th>
                            <Table.Th>Downforce</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {Object.entries(comps).map(([k, c]: any) => (
                            <Table.Tr key={k}>
                              <Table.Td>{k}</Table.Td>
                              <Table.Td>{fmt(c.Cd, 4)}</Table.Td>
                              <Table.Td>{fmt(c.Cl, 4)}</Table.Td>
                              <Table.Td>{fmt(c.downforce_N, 1)} N</Table.Td>
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                      {run.metrics?.pressure_viscous && (
                        <Hint style={{ marginTop: 6 }}>
                          Drag from pressure {fmt(run.metrics.pressure_viscous.Cd_pressure, 4)}, from friction {fmt(run.metrics.pressure_viscous.Cd_viscous, 4)}
                        </Hint>
                      )}
                      <SectionTitle>y+ on the walls</SectionTitle>
                      <Table className="compact" withRowBorders={false}>
                        <Table.Tbody>
                          {Object.entries(run.metrics?.yplus || {}).filter(([k]) => k !== "ground").map(([k, v]: any) => (
                            <Table.Tr key={k}>
                              <Table.Td>{k}</Table.Td>
                              <Table.Td>mean {fmt(v.average, 1)}</Table.Td>
                              <Table.Td>max {fmt(v.max, 0)}</Table.Td>
                            </Table.Tr>
                          ))}
                        </Table.Tbody>
                      </Table>
                      <div style={{ marginTop: 12 }}>
                        <DiagnosticList items={run.diagnostics} />
                      </div>
                    </>
                  )}
                </Tabs.Panel>
                <Tabs.Panel value="history">
                  <SectionTitle first>Coefficients</SectionTitle>
                  <CoefficientChart series={series} keys={["Cd", "Cl"]} window={run?.metrics?.averaging_window} height={220} />
                  <CoefficientChart series={series} keys={["balance_front_pct"]} window={run?.metrics?.averaging_window} height={180} />
                  <SectionTitle>Residuals</SectionTitle>
                  <ResidualChart series={series} height={220} />
                  <Hint style={{ marginTop: 6 }}>The figures at the top are averages over the shaded window.</Hint>
                </Tabs.Panel>
              </div>
            </ScrollArea>
          </Tabs>
        </aside>
      </div>
    </div>
  );
}
