/**
 * Shape zones: the regions of the vehicle an optimisation may reshape. Everything outside the
 * zones keeps its imported shape. The viewport shows where change is allowed (freedom map)
 * or what a trial change does (movement heat map with a baseline ↔ changed slider).
 */
import { ActionIcon, Button, Group, Menu, MultiSelect, NumberInput, SegmentedControl, Select, Slider, Stack, Switch, Text, TextInput, Tooltip } from "@mantine/core";
import { IconDeviceFloppy, IconFocus2, IconPlus, IconTrash, IconWand } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import { ColorLegend, fmt } from "../../components/ColorLegend";
import { MirrorMenu } from "../../components/MirrorMenu";
import { MorphControls, showMorph } from "../../components/MorphView";
import { notifyError, notifyOk } from "../../components/notify";
import { ScrubField } from "../../components/ScrubField";
import { Hint, PageLayout, SectionTitle } from "../../components/ui";
import { pid, useApp } from "../../state/store";
import { decodeAbg } from "../../viewport/abg";
import { BoxEditor } from "../../viewport/BoxEditor";
import { Viewer } from "../../viewport/Viewer";
import { Viewport3D } from "../../viewport/Viewport3D";
import { Box3, Vector3 } from "three";

type Dir = "z" | "x" | "width";
interface Move { direction: Dir; max_mm: number; segments: number }
interface Zone { id: string; name: string; preset: string; box_min: number[]; box_max: number[]; blend: number; components: string[]; moves: Move[]; symmetric: boolean; enabled: boolean }

const DIR_LABEL: Record<Dir, string> = { z: "Up / down", x: "Fore / aft", width: "Wider / narrower" };
const SECTIONS = [
  { value: "1", label: "Whole zone" },
  { value: "2", label: "Front, rear" },
  { value: "3", label: "Front, mid, rear" },
];
const SECTION_WORDS: Record<number, string[]> = { 2: ["front", "rear"], 3: ["front", "middle", "rear"] };
const ZONE_COLORS = [0xe0b05c, 0x7fc4c9, 0xc98fd1, 0x9fcf7a, 0xe08a7a, 0x8fa8e0];

/** The geometry parameters a zone creates (mirrors aerobench.services.zones.zone_parameters). */
export function zoneVariables(z: Zone): { id: string; label: string; max: number }[] {
  if (!z.enabled) return [];
  const out: { id: string; label: string; max: number }[] = [];
  for (const m of z.moves) {
    for (let k = 0; k < m.segments; k++) {
      const id = `${z.id}_${m.direction}${m.segments === 1 ? "" : k + 1}`;
      const sec = m.segments === 1 ? "" : ` (${(SECTION_WORDS[m.segments] || [])[k] || `section ${k + 1}`})`;
      out.push({ id, label: `${DIR_LABEL[m.direction].toLowerCase()}${sec}`, max: m.max_mm });
    }
  }
  return out;
}

const slugId = (name: string, taken: string[]) => {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 24) || "zone";
  if (!/^[a-z]/.test(base)) base = "z_" + base;
  let id = base;
  for (let i = 2; taken.includes(id); i++) id = `${base}${i}`;
  return id;
};

export function ZonesPanel({ onUse }: { onUse?: (paramIds: string[]) => void }) {
  const project = useApp((s) => s.project)!;
  const { refreshProject } = useApp();
  const comps = project.config.geometry.components.filter((c: any) => c.role !== "wheel");
  const yc = project.config.vehicle.centreline_y || 0;
  const [zones, setZones] = useState<Zone[]>([]);
  const [presets, setPresets] = useState<any[]>([]);
  const [sel, setSel] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [mode, setMode] = useState<"freedom" | "change">("freedom");
  const [values, setValues] = useState<Record<string, number>>({});
  const [t, setT] = useState(1);
  const [morph, setMorph] = useState<{ header: any; range: [number, number] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [hasViewer, setHasViewer] = useState(false);
  const [coverage, setCoverage] = useState<Record<string, number>>({});
  const element = project.config.application === "aero_element";
  const viewer = useRef<Viewer | null>(null);
  const editor = useRef<BoxEditor | null>(null);
  const framed = useRef(false);
  const reqSeq = useRef(0);

  useEffect(() => {
    api.get(`/api/p/${pid()}/zones`).then((r) => { setZones(r.zones); setReady(true); }).catch(notifyError);
    api.get(`/api/p/${pid()}/zones/presets`).then((r) => setPresets(r.presets)).catch(() => setPresets([]));
  }, []);

  const z = zones[sel];
  const vars = useMemo(() => zones.flatMap((x) => zoneVariables(x).map((v) => ({ ...v, zone: x.name }))), [zones]);

  const upd = (patch: Partial<Zone>) => {
    setZones((zs) => zs.map((x, i) => (i === sel ? { ...x, ...patch } : x)));
    setDirty(true);
  };

  // ---------------------------------------------------------------- viewport
  /** Most zone volume behind the middle of the vehicle: view it from the rear. */
  const rearFocused = () => {
    const v = viewer.current;
    if (!v || !zones.length) return false;
    const b = v.bounds();
    const mid = (b.min.x + b.max.x) / 2;
    let rear = 0;
    let front = 0;
    for (const x of zones) {
      const vol = (x.box_max[0] - x.box_min[0]) * (x.box_max[2] - x.box_min[2]);
      if ((x.box_min[0] + x.box_max[0]) / 2 > mid) rear += vol;
      else front += vol;
    }
    return rear > front;
  };

  /** Turn the view towards a zone (from behind for zones in the rear half). */
  const focusZone = (x: Zone | undefined) => {
    const v = viewer.current;
    if (!v || !x) return;
    const b = v.bounds();
    const rear = (x.box_min[0] + x.box_max[0]) / 2 > (b.min.x + b.max.x) / 2;
    const pad = 0.6;
    const box = new Box3(new Vector3(x.box_min[0] - pad, x.box_min[1] - pad, x.box_min[2] - pad), new Vector3(x.box_max[0] + pad, x.box_max[1] + pad, x.box_max[2] + pad));
    v.view(rear ? "iso_rear" : "iso", box);
  };

  /** Boxes (editable: click one, drag its face handles) and their names. */
  const drawBoxes = () => {
    const v = viewer.current;
    if (!v) return;
    v.clearOverlays();
    zones.forEach((x, i) => {
      if (!x.enabled) return;
      const c = ZONE_COLORS[i % ZONE_COLORS.length];
      v.addLabel(x.name, [(x.box_min[0] + x.box_max[0]) / 2, (x.box_min[1] + x.box_max[1]) / 2, x.box_max[2] + 0.08], c, 0.32);
    });
    const ed = editor.current;
    if (!ed) return;
    ed.onSelect = (id) => {
      const i = zones.findIndex((x) => x.id === id);
      if (i >= 0) setSel(i);
    };
    const move = (id: string, mn: number[], mx: number[]) => {
      setZones((zs) => zs.map((x) => (x.id === id ? { ...x, box_min: mn, box_max: mx } : x)));
      setDirty(true);
    };
    ed.onChange = move;
    ed.onCommit = move;
    ed.setBoxes(zones.flatMap((x, i) => (x.enabled ? [{ id: x.id, min: x.box_min, max: x.box_max, color: ZONE_COLORS[i % ZONE_COLORS.length],
      symmetricY: x.symmetric ? yc : null }] : [])), zones[sel]?.id ?? null);
  };

  const loadFreedom = async () => {
    const v = viewer.current;
    if (!v) return;
    const seq = ++reqSeq.current;
    try {
      const buf = await api.postBinary(`/api/p/${pid()}/zones/freedom.abg`, { zones });
      if (seq !== reqSeq.current) return;
      v.clearResults();
      v.setGeometry(null);
      const abg = decodeAbg(buf);
      setCoverage(Object.fromEntries((abg.header.coverage || []).map((c: any) => [c.id, c.area_pct])));
      v.setResult("freedom", abg, { colormap: "Shape freedom", range: [0, 100] });
      if (!framed.current) {
        v.view(rearFocused() ? "iso_rear" : "iso");
        framed.current = true;
      }
      setMorph(null);
    } catch (e) {
      if (seq === reqSeq.current) notifyError(e);
    }
  };

  const loadChange = async (vals = values) => {
    const v = viewer.current;
    if (!v) return;
    const seq = ++reqSeq.current;
    setBusy(true);
    try {
      const buf = await api.postBinary(`/api/p/${pid()}/zones/preview.abg`, { zones, values: vals, check: true });
      if (seq !== reqSeq.current) return;
      const abg = decodeAbg(buf);
      const range = showMorph(v, abg, t);
      setMorph({ header: abg.header, range });
      if (!framed.current) {
        v.view(rearFocused() ? "iso_rear" : "iso");
        framed.current = true;
      }
    } catch (e) {
      if (seq === reqSeq.current) notifyError(e);
    } finally {
      if (seq === reqSeq.current) setBusy(false);
    }
  };

  // zones edited: redraw the boxes at once, the coloured surface after a short pause
  useEffect(() => {
    if (!ready || !hasViewer) return;
    drawBoxes();
    const h = setTimeout(() => (mode === "freedom" ? loadFreedom() : loadChange()), framed.current ? 450 : 0);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zones, mode, ready, hasViewer]);
  useEffect(drawBoxes, [sel]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => editor.current?.dispose(), []);

  const onViewer = (v: Viewer) => {
    viewer.current = v;
    editor.current?.dispose();
    editor.current = new BoxEditor(v);
    setHasViewer(true);
    // the surface first, so the view can be framed on it once the zones are known
    // the parts as imported: zones are defined on the part itself, before it is posed in the tunnel
    api.binary(`/api/p/${pid()}/geometry/preview.abg?posed=false`).then((buf) => {
      if (!v.resultIds().length) v.setGeometry(decodeAbg(buf), { colorByPart: false });
    }).catch(() => undefined);
  };

  const blend = (x: number) => {
    setT(x);
    viewer.current?.morphResult("morph", x);
  };

  // ---------------------------------------------------------------- edits
  const addPreset = (p: any) => {
    const id = slugId(p.preset === "custom" ? "zone" : p.preset, zones.map((x) => x.id));
    const zn: Zone = { id, name: p.name, preset: p.preset, box_min: p.box_min, box_max: p.box_max, blend: p.blend, components: [], moves: p.moves, symmetric: true, enabled: true };
    setZones([...zones, zn]);
    setSel(zones.length);
    setDirty(true);
    focusZone(zn);
  };

  const save = async () => {
    try {
      const r = await api.put(`/api/p/${pid()}/zones`, { zones });
      setZones(r.zones);
      setDirty(false);
      await refreshProject();
      notifyOk(`${r.parameters.length} shape variable${r.parameters.length === 1 ? "" : "s"} available for studies and optimisations.`, "Zones saved");
    } catch (e) {
      notifyError(e);
    }
  };

  /** Zones whose boxes overlap (on the same parts): their changes add up there. */
  const overlaps = useMemo(() => {
    const out: Record<string, string[]> = {};
    const on = zones.filter((x) => x.enabled);
    for (const a of on) {
      for (const b of on) {
        if (a.id >= b.id) continue;
        const shared = !a.components.length || !b.components.length || a.components.some((c) => b.components.includes(c));
        const hit = [0, 1, 2].every((k) => Math.min(a.box_max[k], b.box_max[k]) > Math.max(a.box_min[k], b.box_min[k]));
        if (shared && hit) {
          (out[a.id] ||= []).push(b.name);
          (out[b.id] ||= []).push(a.name);
        }
      }
    }
    return out;
  }, [zones]);

  const mirrorZone = (axis: number, plane: number) => {
    if (!z) return;
    const mn = [...z.box_min];
    const mx = [...z.box_max];
    mn[axis] = 2 * plane - z.box_max[axis];
    mx[axis] = 2 * plane - z.box_min[axis];
    const id = slugId(`${z.id}_mirror`, zones.map((x) => x.id));
    // a box mirrored across the centreline of a symmetric zone is the zone itself: the copy is one-sided
    setZones([...zones, { ...z, id, name: `${z.name} (mirrored)`, box_min: mn, box_max: mx, symmetric: axis === 1 ? false : z.symmetric }]);
    setSel(zones.length);
    setDirty(true);
  };

  const setBox = (axis: number, end: 0 | 1, val: number) => {
    const mn = [...z.box_min];
    const mx = [...z.box_max];
    // a face never passes the opposite one
    if (end === 0) mn[axis] = Math.min(val, mx[axis] - 0.001);
    else mx[axis] = Math.max(val, mn[axis] + 0.001);
    upd({ box_min: mn, box_max: mx });
  };
  const halfWidth = z ? Math.max(Math.abs(z.box_min[1] - yc), Math.abs(z.box_max[1] - yc)) : 0;

  const tryVariables = (
    <Stack gap={0} mt={8} style={{ maxHeight: 360, overflowY: "auto", paddingRight: 4 }}>
      <Group justify="space-between">
        <Text size="xs" fw={500}>
          Shape variables
        </Text>
        <Button size="compact-xs" variant="subtle" onClick={() => { setValues({}); loadChange({}); }}>
          Reset
        </Button>
      </Group>
      {vars.length === 0 && <Text size="xs" c="dimmed">Add a zone first.</Text>}
      {vars.map((v) => (
        <Stack key={v.id} gap={0} mt={6}>
          <Group justify="space-between" wrap="nowrap">
            <Text size="xs" truncate>
              {v.zone}: {v.label}
            </Text>
            <Text size="xs" className="kpi-value">
              {fmt(values[v.id] ?? 0, 1)} mm
            </Text>
          </Group>
          <Slider size="sm" min={-v.max} max={v.max} step={Math.max(0.5, v.max / 60)} value={values[v.id] ?? 0} label={null}
            onChange={(x) => setValues((s) => ({ ...s, [v.id]: x }))}
            onChangeEnd={(x) => {
              const nv = { ...values, [v.id]: x };
              setValues(nv);
              loadChange(nv);
            }} />
        </Stack>
      ))}
    </Stack>
  );

  const side = (
    <Stack gap={0}>
      <Hint>
        Mark the parts of the {element ? "element" : "vehicle"} the optimiser may reshape. Everything outside the zones keeps its imported shape exactly; inside, changes fade out
        smoothly towards the zone's edges.{element ? " Zones are drawn on the element as imported, before its angle and height are applied." : ""}
      </Hint>
      <SectionTitle
        right={
          <Menu position="bottom-end" width={300}>
            <Menu.Target>
              <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={13} />}>
                Add zone
              </Button>
            </Menu.Target>
            <Menu.Dropdown>
              {presets.map((p) => (
                <Menu.Item key={p.preset} onClick={() => addPreset(p)}>
                  <Text size="sm">{p.name}</Text>
                  <Text size="xs" c="dimmed">
                    {p.description}
                  </Text>
                </Menu.Item>
              ))}
            </Menu.Dropdown>
          </Menu>
        }
      >
        Zones
      </SectionTitle>
      {zones.length === 0 && <Hint>No zones yet. Add one from the list of typical regions; adjust its box afterwards.</Hint>}
      <Stack gap={2}>
        {zones.map((x, i) => (
          <div key={x.id} className="ab-step" aria-current={i === sel ? "page" : undefined} onClick={() => setSel(i)}
            style={{ height: "auto", padding: "6px 8px", gridTemplateColumns: "12px 1fr auto", cursor: "pointer", gap: 8 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: `#${ZONE_COLORS[i % ZONE_COLORS.length].toString(16).padStart(6, "0")}`, opacity: x.enabled ? 1 : 0.3 }} />
            <span style={{ minWidth: 0 }}>
              <Text size="sm" c={x.enabled ? undefined : "dimmed"} truncate>
                {x.name}
              </Text>
              <Text size="xs" c="dimmed" truncate>
                {x.moves.map((m) => `${DIR_LABEL[m.direction].toLowerCase()} ±${m.max_mm} mm`).join(", ")}
              </Text>
              {x.enabled && overlaps[x.id] && (
                <Text size="xs" c="yellow" truncate>
                  Overlaps {overlaps[x.id].map((n) => `“${n}”`).join(", ")}: the changes add up there
                </Text>
              )}
              {x.enabled && coverage[x.id] !== undefined && (
                <Text size="xs" c={coverage[x.id] > 0 ? "dimmed" : "red"} truncate>
                  {coverage[x.id] > 0 ? `Covers ${fmt(coverage[x.id], coverage[x.id] < 1 ? 2 : 0)} % of the surface` : "Its box misses the part: nothing would change"}
                </Text>
              )}
            </span>
            <Switch size="xs" checked={x.enabled} onClick={(e) => e.stopPropagation()} aria-label="Use this zone"
              onChange={(e) => { const on = e.currentTarget.checked; setZones((zs) => zs.map((y, k) => (k === i ? { ...y, enabled: on } : y))); setDirty(true); }} />
          </div>
        ))}
      </Stack>

      {z && (
        <>
          <SectionTitle right={
            <Group gap={2}>
            <Tooltip label="Turn the view towards this zone">
              <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => focusZone(z)} aria-label="Show zone">
                <IconFocus2 size={14} />
              </ActionIcon>
            </Tooltip>
            <MirrorMenu defaults={[z.box_max[0], yc, z.box_min[2]]} onMirror={mirrorZone} label="Mirror a copy of this zone" />
            <Tooltip label="Remove this zone">
              <ActionIcon size="sm" variant="subtle" color="red" onClick={() => { setZones(zones.filter((_, i) => i !== sel)); setSel(0); setDirty(true); }} aria-label="Remove zone">
                <IconTrash size={14} />
              </ActionIcon>
            </Tooltip>
            </Group>
          }>
            {z.name}
          </SectionTitle>
          <Stack gap={8}>
            <TextInput size="xs" label="Name" value={z.name} onChange={(e) => upd({ name: e.currentTarget.value })} />
            <Text size="xs" fw={500}>
              {element ? "Box (metres, as imported: x along the chord, y along the span, z up)" : "Box (metres, vehicle frame: nose at the front, x rearwards, z up)"}
            </Text>
            <Text size="xs" c="dimmed">
              Drag the white handles on the box in the view, or drag a field's label left and right.
            </Text>
            <Group gap={6} grow>
              <ScrubField label="Front x" value={z.box_min[0]} step={0.005} decimals={3} units="m" onChange={(v) => setBox(0, 0, v)} />
              <ScrubField label="Rear x" value={z.box_max[0]} step={0.005} decimals={3} units="m" onChange={(v) => setBox(0, 1, v)} />
            </Group>
            <Group gap={6} grow>
              <ScrubField label="Bottom z" value={z.box_min[2]} step={0.005} decimals={3} units="m" onChange={(v) => setBox(2, 0, v)} />
              <ScrubField label="Top z" value={z.box_max[2]} step={0.005} decimals={3} units="m" onChange={(v) => setBox(2, 1, v)} />
            </Group>
            {z.symmetric ? (
              <ScrubField label="Half width from the centreline" value={halfWidth} step={0.005} decimals={3} units="m" min={0.01}
                onChange={(v) => { const a = Math.max(0.01, v); upd({ box_min: [z.box_min[0], yc - a, z.box_min[2]], box_max: [z.box_max[0], yc + a, z.box_max[2]] }); }} />
            ) : (
              <Group gap={6} grow>
                <ScrubField label="Right y" value={z.box_min[1]} step={0.005} decimals={3} units="m" onChange={(v) => setBox(1, 0, v)} />
                <ScrubField label="Left y" value={z.box_max[1]} step={0.005} decimals={3} units="m" onChange={(v) => setBox(1, 1, v)} />
              </Group>
            )}
            <NumberInput size="xs" label="Fade-out distance" description="Inside the box edges the change fades smoothly to zero over this distance"
              value={Math.round(z.blend * 1000)} min={5} step={10} rightSection={<Text size="xs" c="dimmed" pr={8}>mm</Text>}
              onChange={(v) => upd({ blend: Math.max(0.005, (Number(v) || 5) / 1000) })} />
            <MultiSelect size="xs" label="Parts" placeholder="All parts except wheels" data={comps.map((c: any) => ({ value: c.id, label: c.name }))}
              value={z.components} onChange={(x) => upd({ components: x })} clearable />
            <Switch size="xs" label="Mirror-symmetric about the centreline" checked={z.symmetric} onChange={(e) => upd({ symmetric: e.currentTarget.checked })} />
            <Text size="xs" fw={500} mt={4}>
              Changes the optimiser may make
            </Text>
            {z.moves.map((m, mi) => (
              <Group key={mi} gap={6} wrap="nowrap" align="flex-end">
                <Select size="xs" w={140} label={mi === 0 ? "Direction" : undefined} value={m.direction} allowDeselect={false}
                  data={(Object.keys(DIR_LABEL) as Dir[]).filter((d) => d === m.direction || !z.moves.some((o) => o.direction === d)).map((d) => ({ value: d, label: DIR_LABEL[d] }))}
                  onChange={(d) => d && upd({ moves: z.moves.map((o, k) => (k === mi ? { ...o, direction: d as Dir } : o)) })} />
                <NumberInput size="xs" w={78} label={mi === 0 ? "Up to ±" : undefined} value={m.max_mm} min={1} max={1000} rightSection={<Text size="xs" c="dimmed" pr={6}>mm</Text>}
                  onChange={(v) => upd({ moves: z.moves.map((o, k) => (k === mi ? { ...o, max_mm: Math.max(1, Number(v) || 1) } : o)) })} />
                <Select size="xs" style={{ flex: 1 }} label={mi === 0 ? "Sections" : undefined} value={String(m.segments)} data={SECTIONS} allowDeselect={false}
                  onChange={(s) => s && upd({ moves: z.moves.map((o, k) => (k === mi ? { ...o, segments: Number(s) } : o)) })} />
                <ActionIcon size="sm" variant="subtle" color="red" mb={4} onClick={() => upd({ moves: z.moves.filter((_, k) => k !== mi) })} aria-label="Remove change" disabled={z.moves.length === 1}>
                  <IconTrash size={13} />
                </ActionIcon>
              </Group>
            ))}
            {z.moves.length < 3 && (
              <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} style={{ alignSelf: "flex-start" }}
                onClick={() => {
                  const d = (Object.keys(DIR_LABEL) as Dir[]).find((x) => !z.moves.some((o) => o.direction === x))!;
                  upd({ moves: [...z.moves, { direction: d, max_mm: 20, segments: 1 }] });
                }}>
                Add a change
              </Button>
            )}
            <Hint>
              Sections split the zone from front to rear into parts that move independently — two sections let the optimiser tilt a surface (for example a
              diffuser angle) instead of only lifting it.
            </Hint>
          </Stack>
        </>
      )}
    </Stack>
  );

  const footer = (
    <Group gap={8} grow>
      <Button leftSection={<IconDeviceFloppy size={15} />} onClick={save} disabled={!dirty}>
        Save zones
      </Button>
      {onUse && (
        <Tooltip label={dirty ? "Save the zones first" : "Start a new optimisation with these shape variables"}>
          <Button variant="default" leftSection={<IconWand size={15} />} disabled={dirty || !vars.length} onClick={() => onUse(vars.map((v) => v.id))}>
            Optimise these
          </Button>
        </Tooltip>
      )}
    </Group>
  );

  return (
    <PageLayout side={side} sideWidth={440} footer={footer}>
      <Viewport3D onReady={onViewer}>
        <div className="ab-float" style={{ left: 12, top: 12, padding: 10, width: 320 }}>
          <SegmentedControl size="xs" fullWidth value={mode} onChange={(m) => setMode(m as any)}
            data={[{ value: "freedom", label: "Where it can change" }, { value: "change", label: "Trial change" }]} />
          <Text size="xs" c="dimmed" mt={6}>
            {mode === "freedom"
              ? "Coloured: how strongly the zones may move the surface. Grey parts never change."
              : busy
                ? "Computing the changed shape…"
                : "Set the variables the optimiser will explore; colour shows the movement normal to the surface."}
          </Text>
          {mode === "change" && tryVariables}
        </div>
        {mode === "freedom" && <ColorLegend title="Shape freedom" colormap="Shape freedom" range={[0, 100]} units="%" />}
        {mode === "change" && morph && <MorphControls header={morph.header} range={morph.range} t={t} onT={blend} />}
      </Viewport3D>
    </PageLayout>
  );
}
