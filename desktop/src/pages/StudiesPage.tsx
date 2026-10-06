import { Alert, Button, Group, MultiSelect, NumberInput, Paper, SegmentedControl, Select, Stack, Switch, Table, Tabs, Text, Textarea, TextInput } from "@mantine/core";
import { IconEye, IconPlayerPlay } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { axisStyle, EChart } from "../charts/EChart";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { EmptyState, PageLayout, SectionTitle } from "../components/ui";
import { pid, useApp } from "../state/store";
import {
  DesignTable,
  parseLevels,
  promoteDesign,
  SchedulingFields,
  StudyHeader,
  StudyList,
  toDefinitionVariables,
  useMetrics,
  useStudy,
  useStudyList,
  useTargets,
  VariableDraft,
  VariablesEditor,
} from "./explore/common";
import { ParametersPanel } from "./explore/ParametersPanel";

const KINDS = [
  { value: "one_factor", label: "One factor" },
  { value: "full_factorial", label: "Factorial" },
  { value: "latin_hypercube", label: "Latin hypercube" },
  { value: "table", label: "Table" },
];

const KIND_HELP: Record<string, string> = {
  one_factor: "Each variable is swept over its levels while the others stay at baseline (sensitivity sweeps, yaw sweeps).",
  full_factorial: "Every combination of the variables' levels (default 3 levels each).",
  latin_hypercube: "Space-filling random sample across all variables — good for response surfaces.",
  table: "Explicit list of designs (one row per design, columns = variable ids).",
};

export function StudiesPage() {
  const [tab, setTab] = useState<string | null>("studies");
  return (
    <Tabs value={tab} onChange={setTab} style={{ height: "100%", display: "flex", flexDirection: "column" }} keepMounted={false}>
      <Tabs.List px="sm">
        <Tabs.Tab value="studies">Studies</Tabs.Tab>
        <Tabs.Tab value="parameters">Geometry parameters</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="studies" style={{ flex: 1, minHeight: 0 }}>
        <StudiesWorkspace />
      </Tabs.Panel>
      <Tabs.Panel value="parameters" style={{ flex: 1, minHeight: 0 }}>
        <ParametersPanel />
      </Tabs.Panel>
    </Tabs>
  );
}

function StudiesWorkspace() {
  const { environment, refreshProject } = useApp();
  const targets = useTargets();
  const metricInfo = useMetrics();
  const [list, reloadList] = useStudyList("study");
  const [sel, setSel] = useState<string | null>(null);
  const [d, reload] = useStudy(sel ?? list[0]?.id ?? null);

  const [name, setName] = useState("Yaw sweep");
  const [kind, setKind] = useState("one_factor");
  const [vars, setVars] = useState<VariableDraft[]>([]);
  const [nSamples, setNSamples] = useState<number>(10);
  const [seed, setSeed] = useState<number>(0);
  const [table, setTable] = useState("");
  const [metrics, setMetrics] = useState<string[]>(["Cd", "Cl", "Cl_front", "Cl_rear", "balance_front_pct"]);
  const [includeBaseline, setIncludeBaseline] = useState(true);
  const [sched, setSched] = useState({ concurrency: 1, cores_per_run: Math.max(1, environment?.max_cores || 1), max_retries: 1, accept_unconverged: true });
  const [preview, setPreview] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);

  const definition = () => {
    const d: any = {
      id: name.replace(/[^A-Za-z0-9_-]/g, "_").replace(/^[^A-Za-z_]/, "s") || "study",
      name,
      kind,
      variables: toDefinitionVariables(vars),
      levels: parseLevels(vars),
      n_samples: nSamples,
      seed,
      include_baseline: includeBaseline,
      metrics,
      scheduling: sched,
    };
    if (kind === "table") {
      const lines = table.trim().split(/\r?\n/).filter(Boolean);
      const head = (lines[0] || "").split(/[,;\t]/).map((s) => s.trim());
      d.table = lines.slice(1).map((l) => {
        const cells = l.split(/[,;\t]/).map((s) => s.trim());
        const row: Record<string, number | string> = {};
        head.forEach((h, i) => {
          const v = vars.find((x) => x.id === h);
          if (v && cells[i] !== undefined && cells[i] !== "") row[h] = v.kind === "discrete" ? cells[i] : parseFloat(cells[i]);
        });
        return row;
      });
    }
    return d;
  };

  const doPreview = async () => {
    setBusy(true);
    try {
      setPreview(await api.post(`/api/p/${pid()}/studies/preview`, { kind: "study", definition: definition() }));
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };
  const start = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/api/p/${pid()}/studies`, { kind: "study", definition: definition() });
      setSel(r.id);
      setPreview(null);
      reloadList();
      await refreshProject();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const side = (
    <Stack gap="xs">
      <SectionTitle>New study</SectionTitle>
      <TextInput size="xs" label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
      <SegmentedControl size="xs" fullWidth data={KINDS} value={kind} onChange={setKind} />
      <Text size="xs" c="dimmed">{KIND_HELP[kind]}</Text>
      <SectionTitle>Variables</SectionTitle>
      <VariablesEditor vars={vars} onChange={setVars} targets={targets} showLevels={kind === "one_factor" || kind === "full_factorial"} />
      {kind === "latin_hypercube" && (
        <Group grow gap={6}>
          <NumberInput size="xs" label="Samples" min={1} max={1000} value={nSamples} onChange={(x) => setNSamples(Number(x) || 1)} />
          <NumberInput size="xs" label="Seed" value={seed} onChange={(x) => setSeed(Number(x) || 0)} />
        </Group>
      )}
      {kind === "table" && (
        <Textarea size="xs" label="Designs (CSV)" autosize minRows={4} placeholder={vars.map((v) => v.id).join(",") + "\n…"} value={table}
          onChange={(e) => setTable(e.currentTarget.value)} description="First row: variable ids. Missing columns use the baseline." styles={{ input: { fontFamily: "monospace" } }} />
      )}
      <Switch size="xs" label="Include the baseline design" checked={includeBaseline} onChange={(e) => setIncludeBaseline(e.currentTarget.checked)} />
      <MultiSelect size="xs" label="Reported metrics" data={Object.keys(metricInfo).map((k) => ({ value: k, label: `${k} — ${metricInfo[k].label}` }))} value={metrics} onChange={setMetrics} searchable />
      <SectionTitle>Scheduling</SectionTitle>
      <SchedulingFields value={sched} onChange={setSched} maxCores={environment?.max_cores || 1} />
      <Switch size="xs" label="Use runs that did not meet the convergence criteria (flagged)" checked={sched.accept_unconverged}
        onChange={(e) => setSched({ ...sched, accept_unconverged: e.currentTarget.checked })} />
      <Group grow>
        <Button size="xs" variant="default" leftSection={<IconEye size={14} />} onClick={doPreview} loading={busy} disabled={!vars.length}>Preview</Button>
        <Button size="xs" leftSection={<IconPlayerPlay size={14} />} onClick={start} loading={busy} disabled={!vars.length}>Start</Button>
      </Group>
      {preview && (
        <Paper withBorder p={8}>
          {preview.errors?.length ? (
            <Alert color="red" p={6} title="Cannot start">
              {preview.errors.map((e: any, i: number) => <Text key={i} size="xs">{e.path}: {e.message}</Text>)}
            </Alert>
          ) : (
            <Stack gap={4}>
              <Text size="xs" fw={600}>{preview.designs.length} designs → {preview.n_runs} runs</Text>
              {(preview.notes || []).map((n: string, i: number) => <Text key={i} size="xs" c="dimmed">{n}</Text>)}
              <Table className="compact">
                <Table.Tbody>
                  {preview.designs.slice(0, 30).map((x: any, i: number) => (
                    <Table.Tr key={i}>
                      <Table.Td><Text size="xs">{i}</Text></Table.Td>
                      <Table.Td><Text size="xs" c="dimmed">{x.origin}</Text></Table.Td>
                      <Table.Td><Text size="xs">{Object.entries(x.values).map(([k, v]) => `${k}=${typeof v === "number" ? fmt(v, 4) : v}`).join(", ")}</Text></Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              {preview.designs.length > 30 && <Text size="xs" c="dimmed">…and {preview.designs.length - 30} more</Text>}
            </Stack>
          )}
        </Paper>
      )}
      <SectionTitle>Studies in this project</SectionTitle>
      <StudyList list={list} selected={d?.id ?? null} onSelect={setSel} />
    </Stack>
  );

  return (
    <PageLayout side={side} sideWidth={440}>
      {d ? (
        <Stack gap="sm" p="sm" h="100%">
          <StudyHeader d={d} reload={reload} />
          <StudyChart d={d} />
          <div style={{ flex: 1, minHeight: 0 }}>
            <DesignTable d={d} metrics={d.definition.metrics || ["Cd", "Cl"]} onPromote={(id) => promoteDesign(d.id, id, refreshProject)} />
          </div>
        </Stack>
      ) : (
        <EmptyState title="No study selected">
          Define variables on the left — speed, yaw, ride height, rake, geometry parameters or alternative geometries — preview the designs and start. Studies run in
          the background; closing the application does not stop them.
        </EmptyState>
      )}
    </PageLayout>
  );
}

function StudyChart({ d }: { d: any }) {
  const vars: any[] = d.definition.variables;
  const metrics: string[] = d.definition.metrics || ["Cd"];
  const [xv, setXv] = useState<string | null>(vars[0]?.id || null);
  const [m, setM] = useState<string | null>(metrics[0] || null);
  const option = useMemo(() => {
    if (!xv || !m) return null;
    const ok = d.designs.filter((x: any) => x.status === "completed" && typeof x.values[xv] === "number");
    const pts = ok.map((x: any) => [x.values[xv], x.metrics?.aggregate?.[m]?.mean, x.idx, x.convergence?.all_converged]).sort((a: any, b: any) => a[0] - b[0]);
    const oneFactor = d.definition.kind === "one_factor";
    const onAxis = oneFactor
      ? pts.filter((p: any) => {
          const des = d.designs.find((x: any) => x.idx === p[2]);
          return vars.every((v) => v.id === xv || des.values[v.id] === (d.summary?.baseline_values || {})[v.id]);
        })
      : pts;
    return {
      tooltip: { trigger: "item", formatter: (p: any) => `#${p.data[2]}: ${xv}=${fmt(p.data[0], 4)}<br/>${m}=${fmt(p.data[1], 5)}${p.data[3] === false ? "<br/>not converged" : ""}` },
      xAxis: { type: "value", name: xv, nameLocation: "middle", nameGap: 24, scale: true, ...axisStyle },
      yAxis: { type: "value", name: m, scale: true, ...axisStyle },
      series: [
        { type: oneFactor ? "line" : "scatter", data: onAxis, symbolSize: 8, itemStyle: { color: "#22b8cf" } },
        ...(oneFactor ? [] : []),
      ],
    };
  }, [d, xv, m]);
  if (!vars.length) return null;
  return (
    <Paper withBorder p={8}>
      <Group gap="xs" mb={4}>
        <Select size="xs" w={180} data={vars.filter((v) => v.kind !== "discrete").map((v) => v.id)} value={xv} onChange={setXv} />
        <Text size="xs" c="dimmed">vs</Text>
        <Select size="xs" w={180} data={metrics} value={m} onChange={setM} />
      </Group>
      {option ? <EChart option={option as any} height={220} /> : null}
    </Paper>
  );
}
