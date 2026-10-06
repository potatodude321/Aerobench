import { ActionIcon, Alert, Button, Group, NumberInput, Paper, Select, SimpleGrid, Stack, Switch, Text, TextInput } from "@mantine/core";
import { IconEye, IconPlayerPlay, IconPlus, IconTrash } from "@tabler/icons-react";
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { axisStyle, EChart } from "../charts/EChart";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { EmptyState, PageLayout, SectionTitle } from "../components/ui";
import { pid, useApp } from "../state/store";
import {
  DesignTable,
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

const ALGOS = [
  { value: "gp_bo", label: "Bayesian (Gaussian process)", help: "Sample-efficient global search for one objective with constraints; the default for expensive CFD." },
  { value: "nsga2", label: "NSGA-II (multi-objective)", help: "Evolutionary search for a Pareto front of two or more objectives; needs a larger budget (≈ 8–10 × population)." },
  { value: "nelder_mead", label: "Nelder–Mead (local)", help: "Derivative-free local refinement from the baseline; constraints by penalty." },
  { value: "space_filling", label: "Space-filling (Latin hypercube)", help: "No adaptation: an evenly spread sample of the budget, useful as a first look or for surrogate building." },
];

interface Objective { metric: string; sense: "minimize" | "maximize"; aggregate: "single" | "mean" | "max" | "min" }
interface Constraint { metric: string; op: ">=" | "<="; value: number; bound: "absolute" | "baseline_delta" | "baseline_ratio" }

export function OptimisePage() {
  const { environment, refreshProject } = useApp();
  const targets = useTargets();
  const metricInfo = useMetrics();
  const [list, reloadList] = useStudyList("optimisation");
  const [sel, setSel] = useState<string | null>(null);
  const [d, reload] = useStudy(sel ?? list[0]?.id ?? null);

  const [name, setName] = useState("Minimise drag");
  const [algorithm, setAlgorithm] = useState("gp_bo");
  const [vars, setVars] = useState<VariableDraft[]>([]);
  const [objs, setObjs] = useState<Objective[]>([{ metric: "Cd", sense: "minimize", aggregate: "single" }]);
  const [cons, setCons] = useState<Constraint[]>([]);
  const [conditions, setConditions] = useState<string>("");
  const [budget, setBudget] = useState(20);
  const [batch, setBatch] = useState(1);
  const [init, setInit] = useState<number | "">("");
  const [seed, setSeed] = useState(0);
  const [baseline, setBaseline] = useState(true);
  const [sched, setSched] = useState({ concurrency: 1, cores_per_run: Math.max(1, environment?.max_cores || 1), max_retries: 1, accept_unconverged: true });
  const [preview, setPreview] = useState<any | null>(null);
  const [busy, setBusy] = useState(false);
  const metricOpts = Object.keys(metricInfo).map((k) => ({ value: k, label: `${k} — ${metricInfo[k].label}` }));
  const multi = objs.length > 1;

  const parsedConditions = (): Record<string, number>[] => {
    // "yaw: 0, 5, 10" -> [{operating.yaw_deg: 0}, …]
    const t = conditions.trim();
    if (!t) return [];
    const vals = t.split(/[,;\s]+/).map(Number).filter(Number.isFinite);
    return vals.map((v) => ({ "operating.yaw_deg": v }));
  };

  const definition = () => ({
    id: name.replace(/[^A-Za-z0-9_-]/g, "_").replace(/^[^A-Za-z_]/, "o") || "optimisation",
    name,
    algorithm,
    variables: toDefinitionVariables(vars),
    objectives: objs.map((o) => ({ ...o, aggregate: parsedConditions().length > 1 && o.aggregate === "single" ? "mean" : o.aggregate })),
    constraints: cons,
    budget,
    batch_size: batch,
    initial_samples: init === "" ? null : init,
    seed,
    evaluate_baseline: baseline || cons.some((c) => c.bound !== "absolute"),
    conditions: parsedConditions(),
    scheduling: sched,
  });

  const run = async (kind: "preview" | "start") => {
    setBusy(true);
    try {
      if (kind === "preview") setPreview(await api.post(`/api/p/${pid()}/studies/preview`, { kind: "optimisation", definition: definition() }));
      else {
        const r = await api.post(`/api/p/${pid()}/studies`, { kind: "optimisation", definition: definition() });
        setSel(r.id);
        setPreview(null);
        reloadList();
        await refreshProject();
      }
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const side = (
    <Stack gap="xs">
      <SectionTitle>New optimisation</SectionTitle>
      <TextInput size="xs" label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
      <Select size="xs" label="Algorithm" data={ALGOS.map(({ value, label }) => ({ value, label }))} value={algorithm} onChange={(v) => v && setAlgorithm(v)} />
      <Text size="xs" c="dimmed">{ALGOS.find((a) => a.value === algorithm)?.help}</Text>
      {multi && ["gp_bo", "nelder_mead"].includes(algorithm) && <Alert color="orange" p={6}>Several objectives need NSGA-II or space-filling.</Alert>}
      <SectionTitle>Design variables</SectionTitle>
      <VariablesEditor vars={vars} onChange={setVars} targets={targets} />
      <SectionTitle right={<ActionIcon size="sm" variant="subtle" onClick={() => setObjs([...objs, { metric: "Cl", sense: "minimize", aggregate: "single" }])}><IconPlus size={14} /></ActionIcon>}>Objectives</SectionTitle>
      {objs.map((o, i) => (
        <Group key={i} gap={4} wrap="nowrap">
          <Select size="xs" style={{ flex: 1 }} data={[{ value: "minimize", label: "Minimise" }, { value: "maximize", label: "Maximise" }]} value={o.sense}
            onChange={(v) => setObjs(objs.map((x, k) => (k === i ? { ...x, sense: v as any } : x)))} w={110} />
          <Select size="xs" style={{ flex: 2 }} data={metricOpts} value={o.metric} searchable onChange={(v) => v && setObjs(objs.map((x, k) => (k === i ? { ...x, metric: v } : x)))} />
          {parsedConditions().length > 1 && (
            <Select size="xs" w={80} data={["mean", "max", "min"]} value={o.aggregate === "single" ? "mean" : o.aggregate}
              onChange={(v) => setObjs(objs.map((x, k) => (k === i ? { ...x, aggregate: v as any } : x)))} />
          )}
          {objs.length > 1 && <ActionIcon size="sm" variant="subtle" color="red" onClick={() => setObjs(objs.filter((_, k) => k !== i))}><IconTrash size={12} /></ActionIcon>}
        </Group>
      ))}
      <Text size="xs" c="dimmed">Sign conventions: lift + up, so “more downforce” = minimise Cl; balance in % front.</Text>
      <SectionTitle right={<ActionIcon size="sm" variant="subtle" onClick={() => setCons([...cons, { metric: "Cl", op: "<=", value: 0, bound: "baseline_delta" }])}><IconPlus size={14} /></ActionIcon>}>
        Constraints
      </SectionTitle>
      {cons.map((c, i) => (
        <Group key={i} gap={4} wrap="nowrap">
          <Select size="xs" style={{ flex: 2 }} data={metricOpts} value={c.metric} searchable onChange={(v) => v && setCons(cons.map((x, k) => (k === i ? { ...x, metric: v } : x)))} />
          <Select size="xs" w={60} data={["<=", ">="]} value={c.op} onChange={(v) => setCons(cons.map((x, k) => (k === i ? { ...x, op: v as any } : x)))} />
          <NumberInput size="xs" w={80} value={c.value} decimalScale={5} hideControls onChange={(v) => setCons(cons.map((x, k) => (k === i ? { ...x, value: Number(v) } : x)))} />
          <Select size="xs" w={120} data={[{ value: "absolute", label: "absolute" }, { value: "baseline_delta", label: "baseline +" }, { value: "baseline_ratio", label: "baseline ×" }]}
            value={c.bound} onChange={(v) => setCons(cons.map((x, k) => (k === i ? { ...x, bound: v as any } : x)))} />
          <ActionIcon size="sm" variant="subtle" color="red" onClick={() => setCons(cons.filter((_, k) => k !== i))}><IconTrash size={12} /></ActionIcon>
        </Group>
      ))}
      <TextInput size="xs" label="Evaluate at yaw angles (optional)" description="e.g. 0, 5 — each design runs at every angle; objectives aggregate across them" value={conditions}
        onChange={(e) => setConditions(e.currentTarget.value)} placeholder="current operating point only" />
      <SectionTitle>Budget</SectionTitle>
      <SimpleGrid cols={4} spacing={6}>
        <NumberInput size="xs" label="Designs" min={2} value={budget} onChange={(v) => setBudget(Number(v) || 2)} />
        <NumberInput size="xs" label="Batch" min={1} value={batch} onChange={(v) => setBatch(Number(v) || 1)} />
        <NumberInput size="xs" label="Initial" placeholder="auto" min={1} value={init} onChange={(v) => setInit(v === "" ? "" : Number(v))} />
        <NumberInput size="xs" label="Seed" value={seed} onChange={(v) => setSeed(Number(v) || 0)} />
      </SimpleGrid>
      <Switch size="xs" label="Evaluate the baseline first" checked={baseline || cons.some((c) => c.bound !== "absolute")} disabled={cons.some((c) => c.bound !== "absolute")}
        onChange={(e) => setBaseline(e.currentTarget.checked)} />
      <SectionTitle>Scheduling</SectionTitle>
      <SchedulingFields value={sched} onChange={setSched} maxCores={environment?.max_cores || 1} />
      <Group grow>
        <Button size="xs" variant="default" leftSection={<IconEye size={14} />} onClick={() => run("preview")} loading={busy} disabled={!vars.length}>Check</Button>
        <Button size="xs" leftSection={<IconPlayerPlay size={14} />} onClick={() => run("start")} loading={busy} disabled={!vars.length}>Start</Button>
      </Group>
      {preview && (
        preview.errors?.length ? (
          <Alert color="red" p={6} title="Cannot start">{preview.errors.map((e: any, i: number) => <Text key={i} size="xs">{e.path}: {e.message}</Text>)}</Alert>
        ) : (
          <Alert color="teal" p={6}>
            <Text size="xs">Up to {preview.n_runs} runs. Baseline: {Object.entries(preview.baseline || {}).map(([k, v]) => `${k}=${typeof v === "number" ? fmt(v, 4) : v}`).join(", ")}</Text>
            {(preview.notes || []).map((n: string, i: number) => <Text key={i} size="xs">{n}</Text>)}
          </Alert>
        )
      )}
      <SectionTitle>Optimisations in this project</SectionTitle>
      <StudyList list={list} selected={d?.id ?? null} onSelect={setSel} />
    </Stack>
  );

  return (
    <PageLayout side={side} sideWidth={460}>
      {d ? <OptimisationView d={d} reload={reload} onPromote={(id) => promoteDesign(d.id, id, refreshProject)} /> : (
        <EmptyState title="No optimisation selected">
          Choose design variables (operating point, pose or geometry parameters), objectives and constraints. Every evaluation is a full mesh + RANS run;
          failed designs are recorded and the optimiser learns from them. Progress is checkpointed after every design, so the search resumes after a restart.
        </EmptyState>
      )}
    </PageLayout>
  );
}

function OptimisationView({ d, reload, onPromote }: { d: any; reload: () => void; onPromote: (id: string) => void }) {
  const objs = d.definition.objectives;
  const metrics = Array.from(new Set([...objs.map((o: any) => o.metric), ...d.definition.constraints.map((c: any) => c.metric)])) as string[];
  const pareto = new Set<string>(d.summary?.pareto_design_ids || []);
  if (d.summary?.best_design_id) pareto.add(d.summary.best_design_id);
  const chart = useMemo(() => {
    if (objs.length === 1) {
      const h = d.history || [];
      return {
        legend: { data: ["evaluated", "best feasible"] },
        tooltip: { trigger: "axis" },
        xAxis: { type: "value", name: "design #", nameLocation: "middle", nameGap: 24, ...axisStyle },
        yAxis: { type: "value", name: objs[0].metric, scale: true, ...axisStyle },
        series: [
          { name: "evaluated", type: "scatter", data: h.filter((x: any) => x.value != null).map((x: any) => [x.idx, x.value]), symbolSize: 7,
            itemStyle: { color: (p: any) => (h.find((x: any) => x.idx === p.data[0])?.feasible ? "#22b8cf" : "#ff922b") } },
          { name: "best feasible", type: "line", step: "end", data: h.filter((x: any) => x.best != null).map((x: any) => [x.idx, x.best]), showSymbol: false, lineStyle: { color: "#51cf66" } },
        ],
      };
    }
    const done = d.designs.filter((x: any) => x.status === "completed" && x.objectives?.length >= 2);
    const pt = (x: any) => [x.objectives[0].value, x.objectives[1].value, x.idx];
    return {
      legend: { data: ["designs", "Pareto front"] },
      tooltip: { trigger: "item", formatter: (p: any) => `#${p.data[2]}: ${objs[0].metric}=${fmt(p.data[0], 4)}, ${objs[1].metric}=${fmt(p.data[1], 4)}` },
      xAxis: { type: "value", name: `${objs[0].metric} (${objs[0].sense})`, nameLocation: "middle", nameGap: 24, scale: true, ...axisStyle },
      yAxis: { type: "value", name: `${objs[1].metric} (${objs[1].sense})`, scale: true, ...axisStyle },
      series: [
        { name: "designs", type: "scatter", data: done.filter((x: any) => !pareto.has(x.id)).map(pt), symbolSize: 7, itemStyle: { color: "#4dabf7", opacity: 0.6 } },
        { name: "Pareto front", type: "line", data: done.filter((x: any) => pareto.has(x.id)).map(pt).sort((a: any, b: any) => a[0] - b[0]), symbolSize: 10,
          itemStyle: { color: "#51cf66" }, lineStyle: { type: "dashed" } },
      ],
    };
  }, [d]);
  const best = d.designs.find((x: any) => x.id === d.summary?.best_design_id);
  return (
    <Stack gap="sm" p="sm" h="100%">
      <StudyHeader d={d} reload={reload} />
      {best && (
        <Paper withBorder p={8}>
          <Group justify="space-between">
            <Text size="sm">
              Best feasible: design #{best.idx} — {Object.entries(best.values).map(([k, v]) => `${k} = ${typeof v === "number" ? fmt(v, 4) : v}`).join(", ")} →{" "}
              <b>{objs[0].metric} = {fmt(d.summary.best_value, 5)}</b>
            </Text>
            <Button size="compact-xs" onClick={() => onPromote(best.id)}>Apply to project</Button>
          </Group>
        </Paper>
      )}
      <Paper withBorder p={8}>
        <EChart option={chart as any} height={240} />
      </Paper>
      <div style={{ flex: 1, minHeight: 0 }}>
        <DesignTable d={d} metrics={metrics} highlight={pareto} onPromote={onPromote} />
      </div>
    </Stack>
  );
}
