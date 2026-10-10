/**
 * Structural check of an aero element: the wall loads of a finished flow simulation applied to
 * the same surface as thin shells (CalculiX), with sheet or composite sections, supports where
 * the part is attached, and a failure check of every ply or sheet.
 */
import { ActionIcon, Alert, Badge, Button, Group, Loader, Menu, MultiSelect, SegmentedControl, Select, SimpleGrid, Slider, Stack, Switch, Text, Tooltip } from "@mantine/core";
import { IconBooks, IconPlayerPlay, IconPlus, IconTerminal2 } from "@tabler/icons-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, EngineError } from "../../api/client";
import type { Diagnostic } from "../../api/client";
import type { Material, StructSection, StructSupport, StructureAnalysis, StructureConfig, Vec3 } from "../../api/types";
import { ColorLegend, fmt } from "../../components/ColorLegend";
import { notifyError, notifyOk } from "../../components/notify";
import { DiagnosticCard, Hint, KeyValue, NumField, PageLayout, SectionTitle } from "../../components/ui";
import { pid, useApp } from "../../state/store";
import { Abg, decodeAbg } from "../../viewport/abg";
import { setReserveColormap } from "../../viewport/colormaps";
import { Viewer } from "../../viewport/Viewer";
import { Viewport3D } from "../../viewport/Viewport3D";
import { MaterialLibrary } from "./MaterialLibrary";
import { SectionEditor } from "./SectionEditor";
import { SUPPORT_COLOR, SupportsEditor } from "./SupportsEditor";

interface Status {
  calculix: { available: boolean; version: string | null; message: string };
  runs: { id: string; label: string; status: string; created_at: string; Cd: number | null; downforce_N: number | null }[];
  structure: StructureConfig;
  parts: { id: string; name: string; role: string }[];
  analysed: string[];
  analyses: StructureAnalysis[];
  materials: { builtin: Material[]; user: Material[]; project: Material[] };
}

type View = "loads" | "reserve" | "displacement" | "stress";
const FIELD: Record<Exclude<View, "loads">, string> = { reserve: "reserve", displacement: "displacement", stress: "stress" };
const LAYER = "structure";

function diag(e: unknown): Diagnostic {
  if (e instanceof EngineError) return e.diagnostic;
  return { code: "ui", category: "internal", severity: "error", summary: String((e as Error)?.message || e) };
}

function sectionId(name: string, taken: string[]): string {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 30) || "section";
  if (!/^[a-z]/.test(base)) base = "s_" + base;
  let id = base;
  for (let i = 2; taken.includes(id); i++) id = `${base}${i}`;
  return id;
}

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function StructurePage() {
  const project = useApp((s) => s.project)!;
  const jobs = useApp((s) => s.jobs);
  const { openLog, setSection, refreshProject } = useApp();
  const [status, setStatus] = useState<Status | null>(null);
  const [st, setSt] = useState<StructureConfig | null>(null);
  const [presets, setPresets] = useState<{ supports: any[]; section: StructSection } | null>(null);
  const [prep, setPrep] = useState<any | null>(null);
  const [prepError, setPrepError] = useState<Diagnostic | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [view, setView] = useState<View>("loads");
  const [selId, setSelId] = useState<string | null>(null);
  const [deform, setDeform] = useState(0.5);
  const [shown, setShown] = useState<{ header: any; range: [number, number]; colormap: string } | null>(null);
  const [libOpen, setLibOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [hasViewer, setHasViewer] = useState(false);
  /** a check started from this page: show its reserve factors as soon as it is done */
  const awaiting = useRef<string | null>(null);
  const viewer = useRef<Viewer | null>(null);
  const abgRef = useRef<Abg | null>(null);
  const framed = useRef(false);
  const seq = useRef(0);

  // ---------------------------------------------------------------- data
  const loadStatus = useCallback(async () => {
    try {
      const s = await api.get<Status>(`/api/p/${pid()}/structure`);
      setStatus(s);
      setSt(s.structure);
      return s;
    } catch (e) {
      notifyError(e);
      return null;
    }
  }, []);

  useEffect(() => {
    loadStatus();
    api.get(`/api/p/${pid()}/structure/presets`).then(setPresets).catch(() => setPresets(null));
  }, [loadStatus, project.config.geometry.revision_id]);

  // a structural job started, finished or was read: refresh the list of checks
  const feaSig = JSON.stringify(
    [...jobs.active, ...jobs.recent].filter((j: any) => j.kind === "fea").map((j: any) => [j.id, j.status, j.ingested]),
  );
  useEffect(() => {
    if (status) loadStatus().then(() => refreshProject());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feaSig]);

  const analyses = status?.analyses ?? [];
  useEffect(() => {
    if (selId && analyses.some((a) => a.id === selId)) return;
    const done = analyses.find((a) => a.status === "completed");
    setSelId(done?.id ?? null);
  }, [analyses, selId]);
  const sel = analyses.find((a) => a.id === selId) ?? null;
  useEffect(() => {
    if (sel && awaiting.current === sel.id && sel.status === "completed") {
      awaiting.current = null;
      setView("reserve");
    }
  }, [sel?.id, sel?.status]);

  const materials = useMemo(() => {
    if (!status) return [];
    const m = new Map<string, Material>();
    for (const x of [...status.materials.builtin, ...status.materials.user, ...(st?.materials ?? [])]) m.set(x.id, x);
    return [...m.values()];
  }, [status, st?.materials]);
  const parts = (status?.parts ?? []).filter((p) => p.role !== "wheel" && p.role !== "stilt").map((p) => ({ value: p.id, label: p.name }));

  const save = async (next: StructureConfig) => {
    const prev = st;
    setSt(next);
    try {
      const saved = await api.put<StructureConfig>(`/api/p/${pid()}/structure`, { structure: next });
      setSt(saved);
    } catch (e) {
      notifyError(e);
      setSt(prev);
    }
  };
  const upd = (patch: Partial<StructureConfig>) => st && save({ ...st, ...patch });

  // ---------------------------------------------------------------- loads preview
  const prepKey = st ? JSON.stringify([st.run_id, st.components, st.model, st.plate_side, st.max_elements, st.design_speed, st.load_factor, st.supports]) : "";
  const runAvailable = (status?.runs.length ?? 0) > 0;

  const loadPreview = useCallback(async () => {
    const v = viewer.current;
    if (!v) return;
    const my = ++seq.current;
    setPreparing(true);
    setPrepError(null);
    try {
      const buf = await api.binary(`/api/p/${pid()}/structure/loads.abg`);
      if (my !== seq.current) return;
      const abg = decodeAbg(buf);
      abgRef.current = abg;
      setPrep(abg.header.prepare);
      setCounts(abg.header.support_nodes || {});
      const [lo, hi] = abg.header.scalar_range || [0, 1];
      const m = Math.max(Math.abs(lo), Math.abs(hi), 1e-6);
      const range: [number, number] = [-m, m];
      v.clearResults();
      v.setGeometry(null);
      v.setResult(LAYER, abg, { colormap: "Cool to Warm", range });
      setShown({ header: abg.header, range, colormap: "Cool to Warm" });
      if (!framed.current) {
        v.view("iso");
        framed.current = true;
      }
    } catch (e) {
      if (my === seq.current) {
        setPrepError(diag(e));
        setPrep(null);
      }
    } finally {
      if (my === seq.current) setPreparing(false);
    }
  }, []);

  const loadResult = useCallback(async (a: StructureAnalysis, field: Exclude<View, "loads">) => {
    const v = viewer.current;
    if (!v) return;
    const my = ++seq.current;
    setPreparing(true);
    try {
      const buf = await api.binary(`/api/p/${pid()}/structure/analyses/${a.id}/result.abg?field=${FIELD[field]}`);
      if (my !== seq.current) return;
      const abg = decodeAbg(buf);
      abgRef.current = abg;
      const req = a.summary?.required_reserve ?? 1.5;
      let range: [number, number];
      let colormap: string;
      if (field === "reserve") {
        setReserveColormap(req);
        range = [0, 2 * req];
        colormap = "Reserve";
      } else if (field === "stress" && abg.scalars) {
        // peaks right at the supports would wash out the rest: the colours end at the 99th percentile
        const v = Array.from(abg.scalars).filter(Number.isFinite).sort((x, y) => x - y);
        const p99 = v.length ? v[Math.floor(0.99 * (v.length - 1))] : 1;
        range = [0, Math.max(p99, 1e-9)];
        colormap = "Inferno (matplotlib)";
      } else {
        range = [0, Math.max(abg.header.scalar_range?.[1] ?? 1, 1e-9)];
        colormap = "Viridis (matplotlib)";
      }
      v.clearResults();
      v.setGeometry(null);
      v.setResult(LAYER, abg, { colormap, range });
      setShown({ header: abg.header, range, colormap });
      if (!framed.current) {
        v.view("iso");
        framed.current = true;
      }
    } catch (e) {
      if (my === seq.current) notifyError(e);
    } finally {
      if (my === seq.current) setPreparing(false);
    }
  }, []);

  // what the viewport shows
  useEffect(() => {
    if (!hasViewer || !st) return;
    if (view === "loads") {
      if (!runAvailable) return;
      const t = setTimeout(loadPreview, 350);
      return () => clearTimeout(t);
    }
    if (sel && sel.status === "completed") loadResult(sel, view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasViewer, view, prepKey, runAvailable, selId, sel?.status]);

  /** Exaggeration of the deflection at full slider: the largest movement shown as 5 % of the part. */
  const exaggeration = useMemo(() => {
    const h = shown?.header;
    if (!h || view === "loads" || !h.summary) return 1;
    const L = h.length || 1;
    const u = h.summary.max_displacement || 0;
    return u > 0 ? Math.max(1, (0.05 * L) / u) : 1;
  }, [shown, view]);

  useEffect(() => {
    const v = viewer.current;
    if (!v || view === "loads" || !abgRef.current?.vectors) return;
    v.morphResult(LAYER, deform * exaggeration, false);
  }, [deform, exaggeration, shown, view]);

  // supports drawn as boxes on top of whatever is shown
  useEffect(() => {
    const v = viewer.current;
    if (!v || !st) return;
    v.clearOverlays();
    for (const s of st.supports) {
      if (!s.enabled || s.shape.kind !== "box") continue;
      const mn = s.shape.min as Vec3;
      const mx = s.shape.max as Vec3;
      v.addBox(mn, mx, SUPPORT_COLOR, 0.12, true, `support-${s.id}`);
      v.addLabel(s.name, [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, mn[2] - 0.03], SUPPORT_COLOR, 0.05);
    }
  }, [st?.supports, hasViewer, shown]);

  // ---------------------------------------------------------------- actions
  const setUpStart = async () => {
    if (!st || !presets) return;
    const mounts = presets.supports.find((p) => p.preset === "pylons") || presets.supports[0];
    const supports: StructSupport[] = (mounts?.shapes || []).map((shape: any, k: number) => ({ id: `mount${k + 1}`, name: `Mount ${k + 1}`, shape, enabled: true }));
    await save({ ...st, sections: st.sections.length ? st.sections : [presets.section], supports: st.supports.length ? st.supports : supports });
  };

  const addSection = (kind: "laminate" | "sheet") => {
    if (!st) return;
    const name = kind === "sheet" ? "Aluminium sheet" : "Carbon laminate";
    const base: StructSection = kind === "sheet"
      ? { id: "", name, components: [], kind: "sheet", material: "al_6061_t6", thickness: 0.002, plies: [], symmetric: false, zero_direction: [0, 1, 0] }
      : { ...(presets?.section ?? { components: [], kind: "laminate", plies: [{ material: "cfrp_twill_t300", thickness: 0.00022, angle: 0 }], symmetric: false, zero_direction: [0, 1, 0] }), id: "", name } as StructSection;
    // a second section applies to named parts only (one section may cover "all the rest")
    const others = st.sections.length > 0;
    const sec = { ...base, id: sectionId(name, st.sections.map((s) => s.id)), components: others && parts.length ? [parts[0].value] : [] };
    save({ ...st, sections: [...st.sections, sec] });
  };

  const start = async () => {
    setStarting(true);
    try {
      const r = await api.post(`/api/p/${pid()}/structure/analyses`, { label: "" });
      notifyOk(`${r.elements.toLocaleString()} shell elements, ${fmt(r.mass, 2)} kg of structure. CalculiX is solving.`, "Structural check started");
      awaiting.current = r.id;
      await loadStatus();
      setSelId(r.id);
    } catch (e) {
      notifyError(e);
    } finally {
      setStarting(false);
    }
  };

  if (!status || !st) {
    return (
      <PageLayout side={<Loader size="sm" color="ice" />} title="Structure">
        <div style={{ flex: 1, background: "var(--ab-viewport)" }} />
      </PageLayout>
    );
  }

  const ccxOk = status.calculix.available;
  const enabledSupports = st.supports.filter((s) => s.enabled);
  const blockers = [
    !ccxOk && "CalculiX is not available.",
    !runAvailable && "Run a flow simulation first: its loads are applied.",
    !st.sections.length && "Add a section (what the part is made of).",
    !enabledSupports.length && "Add a support (where the part is held).",
  ].filter(Boolean) as string[];
  const running = analyses.filter((a) => ["queued", "running"].includes(a.status));
  const sum = view !== "loads" && sel?.status === "completed" ? sel.summary : null;
  const req = st.required_reserve;
  const runOptions = status.runs.map((r) => ({
    value: r.id,
    label: `${r.label}${r.downforce_N != null ? `, ${fmt(r.downforce_N, 0)} N downforce` : r.Cd != null ? `, Cd ${fmt(r.Cd, 3)}` : ""}`,
  }));
  const loadsTotal = prep?.loads?.total as number[] | undefined;
  const scale = prep?.load_scale ?? 1;

  const side = (
    <Stack gap={0}>
      {!ccxOk && (
        <Alert color="yellow" p={10} mt={4}>
          <Text size="xs">{status.calculix.message}</Text>
        </Alert>
      )}
      {st.sections.length === 0 && st.supports.length === 0 && presets && (
        <div className="ab-diag" style={{ ["--sev" as string]: "var(--ab-accent)", marginTop: 10 }}>
          <div className="ab-diag-body" style={{ marginTop: 0 }}>
            Start from a 1.1 mm carbon twill skin held by two mounts under the element, then adjust both to your design.
          </div>
          <Button size="xs" mt={8} variant="light" onClick={setUpStart}>
            Set up a starting point
          </Button>
        </div>
      )}

      <SectionTitle first>Loads</SectionTitle>
      <Stack gap={8}>
        <Select size="xs" label="From the flow simulation" data={runOptions} value={st.run_id ?? status.runs[0]?.id ?? null}
          placeholder="No finished simulation yet" onChange={(v) => upd({ run_id: v })} allowDeselect={false} />
        <SimpleGrid cols={2} spacing="xs">
          <NumField label="At speed" units="m/s" value={st.design_speed ?? undefined} decimals={2} min={0.1}
            description={st.design_speed ? "loads scale with speed squared" : "as simulated"} onCommit={(v) => upd({ design_speed: v > 0 ? v : null })} />
          <NumField label="Load factor" value={st.load_factor} decimals={2} min={0.1} max={20}
            description="kerbs, bumps, gusts" onCommit={(v) => v > 0 && upd({ load_factor: v })} />
        </SimpleGrid>
        <Switch size="xs" label="Include the part's own weight" checked={st.self_weight} onChange={(e) => upd({ self_weight: e.currentTarget.checked })} />
        {loadsTotal && (
          <KeyValue rows={[
            ["Applied force", `${fmt(Math.hypot(...loadsTotal) * scale, 1)} N (${fmt(-loadsTotal[2] * scale, 1)} N down, ${fmt(loadsTotal[0] * scale, 1)} N drag)`],
            ["Against OpenFOAM's integral", prep.loads.difference != null ? `${fmt(prep.loads.difference * 100, 2)} % apart` : "not available"],
            ["Shell elements", `${(prep.mesh?.triangles ?? 0).toLocaleString()}`],
          ]} />
        )}
        {prepError && <DiagnosticCard d={prepError} />}
        {(prep?.notes || []).map((n: string, i: number) => (
          <Hint key={i}>{n}</Hint>
        ))}
      </Stack>

      <SectionTitle>Parts and model</SectionTitle>
      <Stack gap={8}>
        <MultiSelect size="xs" label="Parts checked" data={parts} value={st.components} placeholder={st.components.length ? undefined : "All parts"}
          onChange={(v) => upd({ components: v })} clearable />
        <SegmentedControl size="xs" fullWidth value={st.model} onChange={(v) => upd({ model: v as any })}
          data={[{ value: "skin", label: "Skin (hollow or foam core)" }, { value: "plate", label: "Plate (one face of a thin part)" }]} />
        {st.model === "plate" && (
          <Select size="xs" label="The plate is the part's" value={st.plate_side} onChange={(v) => v && upd({ plate_side: v as any })}
            data={[{ value: "upper", label: "Upper face, material below it" }, { value: "lower", label: "Lower face, material above it" }]} />
        )}
        <Hint>
          {st.model === "skin"
            ? "The whole outer surface is a shell with the section's thickness inside it: wings, flaps, hollow or foam-cored mouldings."
            : "Only one face of the part carries the section, as a flat plate under it: splitters, endplates, floors."}
        </Hint>
      </Stack>

      <SectionTitle right={
        <Group gap={2}>
          <Button size="compact-xs" variant="subtle" leftSection={<IconBooks size={13} />} onClick={() => setLibOpen(true)}>
            Materials
          </Button>
          <Menu position="bottom-end">
            <Menu.Target>
              <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={13} />}>
                Add
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              <Menu.Item onClick={() => addSection("laminate")}>Composite laminate</Menu.Item>
              <Menu.Item onClick={() => addSection("sheet")}>Sheet of metal or plastic</Menu.Item>
            </Menu.Dropdown>
          </Menu>
        </Group>
      }>
        Sections
      </SectionTitle>
      <Stack gap={14}>
        {st.sections.length === 0 && <Hint>What the part is made of: a laminate of plies, or a sheet of one material.</Hint>}
        {st.sections.map((s, i) => (
          <SectionEditor key={s.id} section={s} materials={materials} parts={parts}
            onChange={(ns) => save({ ...st, sections: st.sections.map((x, k) => (k === i ? ns : x)) })}
            onRemove={() => save({ ...st, sections: st.sections.filter((_, k) => k !== i) })} />
        ))}
      </Stack>

      <SectionTitle>Supports</SectionTitle>
      <SupportsEditor supports={st.supports} presets={presets?.supports ?? []} counts={counts} bounds={null}
        onChange={(sp) => save({ ...st, supports: sp })} />

      <SectionTitle>Pass criterion</SectionTitle>
      <SimpleGrid cols={2} spacing="xs">
        <NumField label="Required reserve factor" value={req} decimals={2} min={1} max={10} onCommit={(v) => v >= 1 && upd({ required_reserve: v })} />
        <NumField label="Element limit" value={st.max_elements} decimals={0} min={500} max={400000} step={1000}
          onCommit={(v) => v >= 500 && upd({ max_elements: Math.round(v) })} />
      </SimpleGrid>
      <Hint style={{ marginTop: 6 }}>
        The reserve factor is how many times larger all loads could be before the first ply or the material fails (Tsai-Wu and maximum stress for plies, yield for
        metals and plastics).
      </Hint>

      <SectionTitle>Checks</SectionTitle>
      {analyses.length === 0 && <Hint>No structural check yet.</Hint>}
      <Stack gap={2}>
        {analyses.map((a) => {
          const s = a.summary;
          const tone = a.status === "completed" ? (s?.passed ? "var(--ab-ok)" : "var(--ab-err)") : a.status === "failed" ? "var(--ab-err)" : "var(--ab-text-3)";
          return (
            <div key={a.id} className="ab-step" aria-current={a.id === selId ? "page" : undefined}
              onClick={() => { setSelId(a.id); if (a.status === "completed" && view === "loads") setView("reserve"); }}
              style={{ height: "auto", padding: "5px 8px", gridTemplateColumns: "1fr auto auto", cursor: "pointer", gap: 8 }}>
              <span style={{ minWidth: 0 }}>
                <Text size="sm" truncate>
                  {a.label}
                </Text>
                <Text size="xs" c="dimmed" truncate>
                  {when(a.created_at)}
                  {a.status === "failed" && a.message ? `: ${a.message}` : ""}
                </Text>
              </span>
              <Text size="xs" className="num" style={{ color: tone, whiteSpace: "nowrap" }}>
                {a.status === "completed" ? `reserve ${fmt(s?.min_reserve, 2)}` : a.status === "queued" ? "waiting" : a.status}
              </Text>
              <Tooltip label="CalculiX output">
                <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`Log of ${a.label}`} onClick={(e) => { e.stopPropagation(); openLog(a.job_id); }}>
                  <IconTerminal2 size={14} />
                </ActionIcon>
              </Tooltip>
            </div>
          );
        })}
      </Stack>
    </Stack>
  );

  const footer = (
    <Stack gap={6}>
      <Tooltip label={blockers.join(" ")} disabled={!blockers.length} multiline w={300}>
        <Button fullWidth leftSection={<IconPlayerPlay size={15} />} loading={starting} disabled={blockers.length > 0} onClick={start}>
          Run the structural check
        </Button>
      </Tooltip>
      {running.length > 0 && (
        <Text size="xs" c="dimmed" ta="center">
          {running.length} check{running.length > 1 ? "s" : ""} in progress
        </Text>
      )}
      {!runAvailable && (
        <Button size="xs" variant="subtle" onClick={() => setSection("run")}>
          Go to the flow simulation
        </Button>
      )}
    </Stack>
  );

  const resultViews = [
    { value: "loads", label: "Loads" },
    { value: "reserve", label: "Reserve factor", disabled: !sel || sel.status !== "completed" },
    { value: "displacement", label: "Deflection", disabled: !sel || sel.status !== "completed" },
    { value: "stress", label: "Stress", disabled: !sel || sel.status !== "completed" },
  ];

  return (
    <PageLayout side={side} sideWidth={430} title="Structure"
      lede="Check that the part holds under the loads of a flow simulation: the same surface as thin shells in CalculiX, with your layup and supports."
      footer={footer}>
      <Viewport3D onReady={(v) => { viewer.current = v; setHasViewer(true); }}>
        <div className="ab-float" style={{ left: 12, top: 12, padding: 6 }}>
          <SegmentedControl size="xs" value={view} onChange={(v) => setView(v as View)} data={resultViews} />
        </div>
        {preparing && (
          <div className="ab-viewport-note">
            <Loader size={12} color="ice" />
            {view === "loads" ? "Preparing the shell model and mapping the loads…" : "Loading the result…"}
          </div>
        )}
        {view === "loads" && !runAvailable && (
          <div className="ab-viewport-note">No finished flow simulation yet: its loads are what the structure carries.</div>
        )}
        {shown && (view === "loads" ? prep : sum) && (
          <ColorLegend
            title={view === "loads" ? "Load on the surface, + pressing in" : view === "reserve" ? `Reserve factor (required ${fmt(req, 2)})`
              : view === "displacement" ? "Deflection" : `Von Mises stress, largest through the thickness; up to ${fmt(shown.header.scalar_range?.[1], 1)}`}
            colormap={shown.colormap} range={shown.range} units={view === "loads" ? "kPa" : view === "displacement" ? "mm" : view === "stress" ? "MPa" : undefined} />
        )}
        {sum && view !== "loads" && (
          <>
            <div className="ab-float" style={{ right: 12, top: 60, width: 300, padding: "10px 12px" }}>
              <Group justify="space-between" mb={6}>
                <Text size="sm" fw={600}>
                  {sum.passed ? "Holds" : (sum.min_reserve ?? 0) < 1 ? "Fails" : "Below the required reserve"}
                </Text>
                <Badge size="sm" variant="light" color={sum.passed ? "teal" : "red"}>
                  reserve {fmt(sum.min_reserve, 2)}
                </Badge>
              </Group>
              <KeyValue rows={[
                ["Weakest point", sum.critical ? `${sum.critical.section}${sum.critical.ply ? `, ply ${sum.critical.ply}` : ""}: ${sum.critical.mode}` : "—"],
                ["Largest deflection", `${fmt((sum.max_displacement ?? 0) * 1000, 2)} mm`],
                ["Mass of the structure", `${fmt(sum.mass, 3)} kg`],
                ["Shell elements", `${(sum.elements ?? 0).toLocaleString()}`],
                ["Reactions balance the loads", sum.equilibrium_error != null ? `within ${fmt(sum.equilibrium_error * 100, 3)} %` : "not checked (clamped edges)"],
              ]} />
              {(sum.notes || []).map((n: string, i: number) => (
                <Text key={i} size="xs" c="yellow" mt={6}>
                  {n}
                </Text>
              ))}
            </div>
            {abgRef.current?.vectors && (
              <div className="ab-float" style={{ right: 12, bottom: 12, width: 300, padding: "8px 12px" }}>
                <Group justify="space-between" mb={2}>
                  <Text size="xs" c="dimmed">
                    Unloaded
                  </Text>
                  <Text size="xs" fw={500}>
                    Deflection × {fmt(deform * exaggeration, deform * exaggeration < 10 ? 1 : 0)}
                  </Text>
                </Group>
                <Slider size="sm" min={0} max={1} step={0.01} value={deform} onChange={setDeform} label={null} thumbLabel="Exaggeration of the deflection" />
              </div>
            )}
          </>
        )}
        {view !== "loads" && sel && sel.status !== "completed" && (
          <div className="ab-viewport-note">
            {sel.status === "failed" ? `This check did not finish: ${sel.message || "see its log"}` : "This check is still running."}
          </div>
        )}
      </Viewport3D>
      <MaterialLibrary opened={libOpen} onClose={() => setLibOpen(false)} builtin={status.materials.builtin} user={status.materials.user}
        onSaved={() => loadStatus()} />
    </PageLayout>
  );
}
