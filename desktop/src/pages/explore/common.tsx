/** Shared pieces of the Studies and Optimise workspaces. */
import { ActionIcon, Badge, Button, Group, MultiSelect, NumberInput, Paper, Progress, ScrollArea, Select, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconDownload, IconPlayerPause, IconPlayerPlay, IconPlayerStop, IconPlus, IconTrash } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { bridge } from "../../api/bridge";
import { api } from "../../api/client";
import { fmt } from "../../components/ColorLegend";
import { notifyError, notifyInfo } from "../../components/notify";
import { StatusBadge } from "../../components/ui";
import { pid, useApp } from "../../state/store";

export interface TargetInfo {
  target: string;
  label: string;
  units: string;
  stage: string;
  kind: string;
  description: string;
  baseline: number | string | null;
  lower?: number;
  upper?: number;
}

export interface VariableDraft {
  id: string;
  target: string;
  kind: "continuous" | "integer" | "discrete";
  lower?: number;
  upper?: number;
  values?: (number | string)[];
  levels?: string; // comma-separated (studies)
}

export function useTargets(): TargetInfo[] {
  const [t, setT] = useState<TargetInfo[]>([]);
  const project = useApp((s) => s.project);
  useEffect(() => {
    api.get<TargetInfo[]>(`/api/p/${pid()}/studies/targets`).then(setT).catch(notifyError);
  }, [project?.config.parameters?.length]);
  return t;
}

export function useMetrics(): Record<string, { label: string; units: string; sign: string }> {
  const [m, setM] = useState<Record<string, any>>({});
  useEffect(() => {
    api.get("/api/metrics").then(setM).catch(notifyError);
  }, []);
  return m;
}

export function useRevisions(): { id: string; label: string; kind: string }[] {
  const [r, setR] = useState<any[]>([]);
  useEffect(() => {
    api.get(`/api/p/${pid()}/geometry`).then((g) => setR(g.revisions || [])).catch(() => setR([]));
  }, []);
  return r;
}

const slug = (s: string) => s.replace(/[^A-Za-z0-9_]/g, "_").replace(/^([0-9])/, "_$1");

export function defaultVariable(t: TargetInfo, existing: VariableDraft[]): VariableDraft {
  let id = slug(t.target.split(".").pop() || "x");
  while (existing.some((v) => v.id === id)) id += "_";
  if (t.kind === "discrete") return { id, target: t.target, kind: "discrete", values: t.baseline != null ? [t.baseline] : [] };
  const b = typeof t.baseline === "number" ? t.baseline : 0;
  const span: Record<string, [number, number]> = {
    "operating.speed": [b * 0.75, b * 1.25],
    "operating.yaw_deg": [-10, 10],
    "vehicle.ride_height_offset": [-0.02, 0.02],
    "vehicle.rake_deg": [-1, 1],
    "vehicle.front_ride_height": [-0.02, 0.02],
    "vehicle.rear_ride_height": [-0.02, 0.02],
    "mesh.base_cell_size": [b * 0.7, b * 1.3],
    "numerics.iterations": [Math.round(b * 0.5), Math.round(b * 2)],
  };
  const [lo, hi] = t.lower != null && t.upper != null ? [t.lower, t.upper] : span[t.target] || [b - 1, b + 1];
  return { id, target: t.target, kind: t.kind === "integer" ? "integer" : "continuous", lower: round(lo), upper: round(hi) };
}

const round = (x: number) => Math.round(x * 1e6) / 1e6;

export function toDefinitionVariables(vars: VariableDraft[]) {
  return vars.map((v) =>
    v.kind === "discrete"
      ? { id: v.id, target: v.target, kind: "discrete", values: v.values }
      : { id: v.id, target: v.target, kind: v.kind, lower: v.lower, upper: v.upper },
  );
}

export function parseLevels(vars: VariableDraft[]): Record<string, (number | string)[]> {
  const out: Record<string, (number | string)[]> = {};
  for (const v of vars) {
    if (!v.levels?.trim()) continue;
    out[v.id] = v.levels
      .split(/[,;\s]+/)
      .filter(Boolean)
      .map((x) => (v.kind === "discrete" ? x : parseFloat(x)))
      .filter((x) => typeof x === "string" || Number.isFinite(x));
  }
  return out;
}

export function VariablesEditor({ vars, onChange, targets, showLevels }: { vars: VariableDraft[]; onChange: (v: VariableDraft[]) => void; targets: TargetInfo[]; showLevels?: boolean }) {
  const revisions = useRevisions();
  const used = new Set(vars.map((v) => v.target));
  const add = (target: string | null) => {
    const t = targets.find((x) => x.target === target);
    if (t) onChange([...vars, defaultVariable(t, vars)]);
  };
  const upd = (i: number, patch: Partial<VariableDraft>) => onChange(vars.map((v, k) => (k === i ? { ...v, ...patch } : v)));
  return (
    <Stack gap={6}>
      {vars.map((v, i) => {
        const t = targets.find((x) => x.target === v.target);
        return (
          <Paper key={i} p={8} withBorder radius="sm">
            <Group justify="space-between" mb={4} wrap="nowrap">
              <Tooltip label={t?.description || v.target} multiline w={300}>
                <Text size="xs" fw={600} truncate>
                  {t?.label || v.target} {t?.units ? <Text span c="dimmed" size="xs">[{t.units}]</Text> : null}
                </Text>
              </Tooltip>
              <ActionIcon size="sm" variant="subtle" color="red" onClick={() => onChange(vars.filter((_, k) => k !== i))}>
                <IconTrash size={14} />
              </ActionIcon>
            </Group>
            <Group gap={6} grow align="flex-end">
              <TextInput size="xs" label="Id" value={v.id} onChange={(e) => upd(i, { id: slug(e.currentTarget.value) })} />
              {v.kind === "discrete" ? (
                v.target === "geometry.variant" ? (
                  <MultiSelect size="xs" label="Geometry revisions" data={revisions.map((r) => ({ value: r.id, label: `${r.label || r.id} (${r.kind})` }))}
                    value={(v.values || []).map(String)} onChange={(x) => upd(i, { values: x })} />
                ) : (
                  <TextInput size="xs" label="Values" value={(v.values || []).join(", ")} onChange={(e) => upd(i, { values: e.currentTarget.value.split(/[,;]+/).map((s) => s.trim()).filter(Boolean) })} />
                )
              ) : (
                <>
                  <NumberInput size="xs" label="Lower" value={v.lower} onChange={(x) => upd(i, { lower: Number(x) })} decimalScale={6} hideControls />
                  <NumberInput size="xs" label="Upper" value={v.upper} onChange={(x) => upd(i, { upper: Number(x) })} decimalScale={6} hideControls />
                </>
              )}
            </Group>
            {showLevels && (
              <TextInput mt={4} size="xs" label="Levels (optional)" description="Comma-separated; default: evenly spaced" placeholder={v.kind === "discrete" ? "all values" : "e.g. -10, -5, 0, 5, 10"}
                value={v.levels || ""} onChange={(e) => upd(i, { levels: e.currentTarget.value })} />
            )}
            <Text size="xs" c="dimmed" mt={2}>
              Baseline: {t?.baseline == null ? "—" : typeof t.baseline === "number" ? fmt(t.baseline, 4) : String(t.baseline)} · invalidates {t?.stage}
            </Text>
          </Paper>
        );
      })}
      <Select size="xs" placeholder="Add a variable…" leftSection={<IconPlus size={14} />} value={null} onChange={add} searchable
        data={targets.filter((t) => !used.has(t.target)).map((t) => ({ value: t.target, label: `${t.label}${t.units ? ` [${t.units}]` : ""}` }))} />
    </Stack>
  );
}

export function useStudy(sid: string | null): [any | null, () => void] {
  const [d, setD] = useState<any | null>(null);
  const load = useCallback(() => {
    if (!sid) return setD(null);
    api.get(`/api/p/${pid()}/studies/${sid}`).then(setD).catch(notifyError);
  }, [sid]);
  useEffect(() => {
    load();
  }, [load]);
  useEffect(() => {
    if (!d || !["running", "paused", "cancelling"].includes(d.status)) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [d?.status, load]);
  return [d, load];
}

export function useStudyList(kind: "study" | "optimisation"): [any[], () => void] {
  const [list, setList] = useState<any[]>([]);
  const load = useCallback(() => {
    api.get<any[]>(`/api/p/${pid()}/studies`).then((l) => setList(l.filter((x) => x.kind === kind))).catch(notifyError);
  }, [kind]);
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);
  return [list, load];
}

export function StudyList({ list, selected, onSelect }: { list: any[]; selected: string | null; onSelect: (id: string) => void }) {
  if (!list.length) return <Text size="xs" c="dimmed">Nothing started yet.</Text>;
  return (
    <Table className="compact" highlightOnHover>
      <Table.Tbody>
        {list.map((s) => {
          const done = (s.counts.completed || 0) + (s.counts.failed || 0) + (s.counts.cancelled || 0) + (s.counts.skipped || 0);
          return (
            <Table.Tr key={s.id} onClick={() => onSelect(s.id)} style={{ cursor: "pointer", background: selected === s.id ? "var(--ab-accent-dim)" : undefined }}>
              <Table.Td>
                <Text size="xs" fw={600}>{s.name}</Text>
                <Text size="xs" c="dimmed" className="mono">{s.id}</Text>
              </Table.Td>
              <Table.Td w={90}><StatusBadge status={s.status} size="xs" /></Table.Td>
              <Table.Td w={70}><Text size="xs" ta="right">{done}/{s.n_designs}</Text></Table.Td>
            </Table.Tr>
          );
        })}
      </Table.Tbody>
    </Table>
  );
}

export function StudyHeader({ d, reload }: { d: any; reload: () => void }) {
  const { setSection } = useApp();
  const counts: Record<string, number> = {};
  for (const x of d.designs) counts[x.status] = (counts[x.status] || 0) + 1;
  const total = d.kind === "optimisation" ? d.definition.budget : d.designs.length;
  const finished = (counts.completed || 0) + (counts.failed || 0) + (counts.cancelled || 0);
  const act = async (a: string) => {
    try {
      await api.post(`/api/p/${pid()}/studies/${d.id}/${a}`);
      reload();
    } catch (e) {
      notifyError(e);
    }
  };
  const exportCsv = async () => {
    try {
      const text = await api.get<string>(`/api/p/${pid()}/studies/${d.id}/export.csv`);
      const blob = new Blob([text], { type: "text/csv" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${d.name || d.id}.csv`;
      a.click();
    } catch (e) {
      notifyError(e);
    }
  };
  return (
    <Stack gap={6}>
      <Group justify="space-between">
        <Group gap="xs">
          <Text fw={700}>{d.name}</Text>
          <StatusBadge status={d.status} />
          {d.optimiser && (
            <Badge variant="outline" size="sm">
              {d.optimiser.algorithm} · {d.optimiser.told} told · {d.optimiser.pending} pending
              {d.optimiser.extra?.generation != null ? ` · gen ${d.optimiser.extra.generation}` : ""}
            </Badge>
          )}
        </Group>
        <Group gap={6}>
          {d.status === "running" && <Button size="xs" variant="default" leftSection={<IconPlayerPause size={14} />} onClick={() => act("pause")}>Pause</Button>}
          {d.status === "paused" && <Button size="xs" variant="default" leftSection={<IconPlayerPlay size={14} />} onClick={() => act("resume")}>Resume</Button>}
          {["running", "paused"].includes(d.status) && (
            <Button size="xs" color="red" variant="light" leftSection={<IconPlayerStop size={14} />} onClick={() => act("cancel")}>Cancel</Button>
          )}
          <Button size="xs" variant="default" leftSection={<IconDownload size={14} />} onClick={exportCsv}>CSV</Button>
          <Button size="xs" variant="default" onClick={() => setSection("report")}>Report…</Button>
        </Group>
      </Group>
      <Progress value={total ? (100 * finished) / total : 0} size="sm" />
      <Text size="xs" c="dimmed">
        {Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(" · ") || "no designs yet"}
        {d.summary?.unconverged ? ` · ${d.summary.unconverged} not converged` : ""}
        {d.message ? ` · ${d.message}` : ""}
      </Text>
    </Stack>
  );
}

export function DesignTable({ d, metrics, highlight, onPromote }: { d: any; metrics: string[]; highlight?: Set<string>; onPromote?: (id: string) => void }) {
  const { selectRun, setSection, refreshRuns } = useApp();
  const vars: string[] = d.definition.variables.map((v: any) => v.id);
  const openRun = async (rid: string) => {
    await refreshRuns();
    selectRun(rid);
    setSection("results");
  };
  return (
    <ScrollArea h="100%" type="auto">
      <Table className="compact" stickyHeader highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>#</Table.Th>
            <Table.Th>Status</Table.Th>
            {vars.map((v) => <Table.Th key={v} ta="right">{v}</Table.Th>)}
            {metrics.map((m) => <Table.Th key={m} ta="right">{m}</Table.Th>)}
            {d.kind === "optimisation" && <Table.Th>Feasible</Table.Th>}
            <Table.Th>Runs / note</Table.Th>
            {onPromote && <Table.Th />}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {d.designs.map((x: any) => {
            const agg = x.metrics?.aggregate || {};
            const unconv = x.status === "completed" && !x.convergence?.all_converged;
            return (
              <Table.Tr key={x.id} style={{ background: highlight?.has(x.id) ? "rgba(81,207,102,0.08)" : undefined }}>
                <Table.Td>
                  <Text size="xs">{x.idx}{x.origin === "baseline" ? " (baseline)" : ""}</Text>
                </Table.Td>
                <Table.Td><StatusBadge status={x.status} size="xs" /></Table.Td>
                {vars.map((v) => (
                  <Table.Td key={v} ta="right"><Text size="xs" className="kpi-value">{typeof x.values[v] === "number" ? fmt(x.values[v], 4) : String(x.values[v])}</Text></Table.Td>
                ))}
                {metrics.map((m) => (
                  <Table.Td key={m} ta="right">
                    <Text size="xs" className="kpi-value" c={unconv ? "yellow" : undefined}>{fmt(agg[m]?.mean, 4)}</Text>
                  </Table.Td>
                ))}
                {d.kind === "optimisation" && (
                  <Table.Td>{x.status === "completed" ? <Badge size="xs" color={x.feasible ? "teal" : "orange"} variant="light">{x.feasible ? "yes" : "no"}</Badge> : null}</Table.Td>
                )}
                <Table.Td>
                  {x.status === "failed" ? (
                    <Tooltip label={x.error?.message || ""} multiline w={360}>
                      <Text size="xs" c="red" lineClamp={1} maw={260}>{x.error?.message}</Text>
                    </Tooltip>
                  ) : (
                    <Group gap={4}>
                      {(x.run_ids || []).map((r: any) => (
                        <Badge key={r.run_id} size="xs" variant="outline" style={{ cursor: "pointer" }} onClick={() => openRun(r.run_id)}>
                          run{(x.run_ids.length > 1 ? ` c${r.condition + 1}` : "") + (r.attempt > 1 ? ` #${r.attempt}` : "")}
                        </Badge>
                      ))}
                      {unconv && <Text size="xs" c="yellow">not converged</Text>}
                      {(x.notes || []).length > 0 && (
                        <Tooltip label={(x.notes || []).join("\n")} multiline w={360}><Text size="xs" c="dimmed">notes</Text></Tooltip>
                      )}
                    </Group>
                  )}
                </Table.Td>
                {onPromote && (
                  <Table.Td>
                    {x.status === "completed" && (
                      <Button size="compact-xs" variant="subtle" onClick={() => onPromote(x.id)}>Apply</Button>
                    )}
                  </Table.Td>
                )}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </ScrollArea>
  );
}

export async function promoteDesign(sid: string, designId: string, refreshProject: () => Promise<void>) {
  try {
    const r = await api.post(`/api/p/${pid()}/studies/${sid}/designs/${designId}/promote`);
    await refreshProject();
    notifyInfo(`Applied ${Object.entries(r.applied).map(([k, v]) => `${k} = ${v}`).join(", ")} to the project. Mesh and results are now stale until re-run.`, "Design applied");
  } catch (e) {
    notifyError(e);
  }
}

export function SchedulingFields({ value, onChange, maxCores }: { value: any; onChange: (v: any) => void; maxCores: number }) {
  const over = value.concurrency * value.cores_per_run > maxCores;
  return (
    <Stack gap={4}>
      <Group grow gap={6}>
        <NumberInput size="xs" label="Concurrent designs" min={1} value={value.concurrency} onChange={(x) => onChange({ ...value, concurrency: Number(x) || 1 })} />
        <NumberInput size="xs" label="Cores per run" min={1} value={value.cores_per_run} onChange={(x) => onChange({ ...value, cores_per_run: Number(x) || 1 })} />
        <NumberInput size="xs" label="Retries" min={0} max={5} value={value.max_retries} onChange={(x) => onChange({ ...value, max_retries: Number(x) || 0 })} />
      </Group>
      <Text size="xs" c={over ? "red" : "dimmed"}>
        {value.concurrency * value.cores_per_run} of {maxCores} cores{over ? " — exceeds the available cores (would be refused)" : ""}. Retries apply to
        transient failures (interrupted, MPI) and to divergence (re-run with Robust numerics).
      </Text>
    </Stack>
  );
}

export { bridge };
