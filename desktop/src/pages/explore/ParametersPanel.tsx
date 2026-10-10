/** Geometry parameters: rigid moves, FFD and RBF morphs of selected components, with a live
 *  deformed preview coloured by displacement and the engine's validity check. */
import { ActionIcon, Badge, Button, Group, MultiSelect, NumberInput, Paper, SegmentedControl, Slider, Stack, Switch, Text, TextInput } from "@mantine/core";
import { IconDeviceFloppy, IconPlus, IconRefresh, IconTrash } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { fmt } from "../../components/ColorLegend";
import { MorphControls, showMorph } from "../../components/MorphView";
import { notifyError, notifyInfo } from "../../components/notify";
import { PageLayout, SectionTitle } from "../../components/ui";
import { pid, useApp } from "../../state/store";
import { decodeAbg } from "../../viewport/abg";
import { Viewer } from "../../viewport/Viewer";
import { Viewport3D } from "../../viewport/Viewport3D";

type Kind = "translate" | "rotate" | "ffd" | "rbf";

const DEFAULTS: Record<Kind, any> = {
  translate: { kind: "translate", direction: [0, 0, 1] },
  rotate: { kind: "rotate", pivot: [0, 0, 0], axis: [0, 1, 0] },
  ffd: { kind: "ffd", box_min: [0, -1, 0], box_max: [1, 1, 1], divisions: [3, 2, 2], moves: [[[1, 1, 1], [0, 0, -0.05]]], frozen_boundary: true },
  rbf: { kind: "rbf", handles: [[[0, 0, 0], [0, 0, 0.05]]], fixed_points: [], support_radius: 0.5, basis: "wendland_c2" },
};

const HELP: Record<Kind, string> = {
  translate: "Rigid translation: displacement = (value − baseline) × direction (metres per unit).",
  rotate: "Rigid rotation about the pivot and axis by (value − baseline) degrees (e.g. wing angle of attack).",
  ffd: "Free-form deformation: a Bernstein lattice over the box; listed control points move by (value − baseline) × direction. Outer lattice faces stay fixed so the morph blends smoothly into the untouched surface.",
  rbf: "Radial-basis morph: handles move by (value − baseline) × displacement, fixed points stay; Wendland C2 has compact support (radius), thin-plate is global.",
};

function Vec({ label, value, onChange }: { label: string; value: number[]; onChange: (v: number[]) => void }) {
  return (
    <Group gap={4} grow align="flex-end">
      {["x", "y", "z"].map((a, i) => (
        <NumberInput key={a} size="xs" label={i === 0 ? label : undefined} placeholder={a} leftSection={<Text size="xs" c="dimmed">{a}</Text>} value={value[i]}
          decimalScale={6} hideControls onChange={(x) => onChange(value.map((v, k) => (k === i ? Number(x) || 0 : v)))} />
      ))}
    </Group>
  );
}

export function ParametersPanel() {
  const project = useApp((s) => s.project)!;
  const { refreshProject } = useApp();
  const comps = project.config.geometry.components;
  const [params, setParams] = useState<any[]>([]);
  const [sel, setSel] = useState(0);
  const [values, setValues] = useState<Record<string, number>>({});
  const [morph, setMorph] = useState<{ header: any; range: [number, number] } | null>(null);
  const [t, setT] = useState(1);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [withCheck, setWithCheck] = useState(true);
  const viewer = useRef<Viewer | null>(null);
  const framed = useRef(false);

  const onViewer = (v: Viewer) => {
    viewer.current = v;
    api.binary(`/api/p/${pid()}/geometry/preview.abg`).then((buf) => {
      v.setGeometry(decodeAbg(buf), { colorByPart: true });
      v.view("iso");
      framed.current = true;
    }).catch(() => undefined);
  };

  useEffect(() => {
    api.get(`/api/p/${pid()}/parameters`).then((r) => {
      setParams(r.parameters);
      setValues(Object.fromEntries(r.parameters.map((p: any) => [p.id, p.baseline])));
    }).catch(notifyError);
  }, []);

  const p = params[sel];
  const upd = (patch: any) => {
    setParams((ps) => ps.map((x, i) => (i === sel ? { ...x, ...patch } : x)));
    setDirty(true);
  };
  const updDef = (patch: any) => upd({ deformation: { ...p.deformation, ...patch } });

  const add = () => {
    const ext = project.config.geometry.summary;
    const lo = ext?.bbox_min || [0, -1, 0];
    const hi = ext?.bbox_max || [4.5, 1, 1.4];
    let id = "p" + (params.length + 1);
    while (params.some((x) => x.id === id)) id += "_";
    const body = comps.find((c) => c.role === "body")?.id;
    setParams([...params, { id, name: `Parameter ${params.length + 1}`, units: "m", lower: -0.05, upper: 0.05, baseline: 0, components: body ? [body] : [],
      frozen_components: [], max_displacement: 0.1, symmetric_about_centreline: false,
      deformation: { ...DEFAULTS.ffd, box_min: [lo[0] + 0.75 * (hi[0] - lo[0]), lo[1] - 0.01, lo[2]], box_max: [hi[0] + 0.01, hi[1] + 0.01, hi[2] + 0.01] } }]);
    setValues((v) => ({ ...v, [id]: 0 }));
    setSel(params.length);
    setDirty(true);
  };

  const save = async () => {
    try {
      await api.put(`/api/p/${pid()}/parameters`, { parameters: params });
      setDirty(false);
      await refreshProject();
      notifyInfo("Geometry parameters saved. They can now be used as study and optimisation variables (param.<id>).");
    } catch (e) {
      notifyError(e);
    }
  };

  const doPreview = async () => {
    if (!viewer.current) return;
    setBusy(true);
    try {
      const buf = await api.postBinary(`/api/p/${pid()}/parameters/preview.abg`, { values, parameters: params, check: withCheck });
      const abg = decodeAbg(buf);
      const v = viewer.current;
      setMorph({ header: abg.header, range: showMorph(v, abg, t) });
      if (!framed.current) {
        v.view("iso");
        framed.current = true;
      }
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const side = (
    <Stack gap="xs">
      <SectionTitle right={<Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} onClick={add}>Add</Button>}>Parameters</SectionTitle>
      {!params.length && <Text size="xs" c="dimmed">No geometry parameters yet. Parameters morph or move components; studies and optimisations use them as variables.</Text>}
      <Stack gap={2}>
        {params.map((x, i) => (
          <Group key={i} justify="space-between" p={4} style={{ cursor: "pointer", borderRadius: 4, background: i === sel ? "var(--ab-accent-dim)" : undefined }} onClick={() => setSel(i)}>
            <Text size="xs" fw={600}>{x.name} <Text span size="xs" c="dimmed" className="mono">param.{x.id}</Text></Text>
            <Badge size="xs" variant="outline">{x.deformation.kind}</Badge>
          </Group>
        ))}
      </Stack>
      {p && p.deformation.kind === "zone" && (
        <Paper withBorder p={8}>
          <Text size="xs">
            Created by the shape zone <b>{p.deformation.zone}</b>: change it on the Optimise page, Shape zones tab.
          </Text>
        </Paper>
      )}
      {p && p.deformation.kind !== "zone" && (
        <Paper withBorder p={8}>
          <Stack gap={6}>
            <Group grow gap={6}>
              <TextInput size="xs" label="Id" value={p.id} onChange={(e) => upd({ id: e.currentTarget.value.replace(/[^A-Za-z0-9_]/g, "_") })} />
              <TextInput size="xs" label="Name" value={p.name} onChange={(e) => upd({ name: e.currentTarget.value })} />
              <TextInput size="xs" label="Units" value={p.units} onChange={(e) => upd({ units: e.currentTarget.value })} />
            </Group>
            <SegmentedControl size="xs" fullWidth value={p.deformation.kind} data={[{ value: "translate", label: "Move" }, { value: "rotate", label: "Rotate" }, { value: "ffd", label: "FFD" }, { value: "rbf", label: "RBF" }]}
              onChange={(k) => upd({ deformation: { ...DEFAULTS[k as Kind] } })} />
            <Text size="xs" c="dimmed">{HELP[p.deformation.kind as Kind]}</Text>
            <MultiSelect size="xs" label="Affected components" description="Empty = all non-wheel components" data={comps.map((c) => ({ value: c.id, label: `${c.name} (${c.role})` }))}
              value={p.components} onChange={(x) => upd({ components: x })} />
            <MultiSelect size="xs" label="Frozen components" data={comps.map((c) => ({ value: c.id, label: c.name }))} value={p.frozen_components} onChange={(x) => upd({ frozen_components: x })} />
            <Group grow gap={6}>
              <NumberInput size="xs" label="Lower" value={p.lower} decimalScale={6} hideControls onChange={(x) => upd({ lower: Number(x) })} />
              <NumberInput size="xs" label="Baseline" value={p.baseline} decimalScale={6} hideControls onChange={(x) => upd({ baseline: Number(x) })} />
              <NumberInput size="xs" label="Upper" value={p.upper} decimalScale={6} hideControls onChange={(x) => upd({ upper: Number(x) })} />
              <NumberInput size="xs" label="Max move [m]" value={p.max_displacement ?? ""} decimalScale={4} hideControls onChange={(x) => upd({ max_displacement: x === "" ? null : Number(x) })} />
            </Group>
            {p.deformation.kind === "translate" && <Vec label="Direction per unit [m]" value={p.deformation.direction} onChange={(v) => updDef({ direction: v })} />}
            {p.deformation.kind === "rotate" && (
              <>
                <Vec label="Pivot [m]" value={p.deformation.pivot} onChange={(v) => updDef({ pivot: v })} />
                <Vec label="Axis" value={p.deformation.axis} onChange={(v) => updDef({ axis: v })} />
              </>
            )}
            {p.deformation.kind === "ffd" && (
              <>
                <Vec label="Lattice box min [m]" value={p.deformation.box_min} onChange={(v) => updDef({ box_min: v })} />
                <Vec label="Lattice box max [m]" value={p.deformation.box_max} onChange={(v) => updDef({ box_max: v })} />
                <Group gap={4} grow>
                  {[0, 1, 2].map((i) => (
                    <NumberInput key={i} size="xs" label={i === 0 ? "Divisions (x, y, z)" : undefined} min={1} max={8} value={p.deformation.divisions[i]}
                      onChange={(x) => updDef({ divisions: p.deformation.divisions.map((d: number, k: number) => (k === i ? Number(x) || 1 : d)) })} />
                  ))}
                </Group>
                <Text size="xs" fw={600}>Moved control points (i, j, k) → direction per unit [m]</Text>
                {p.deformation.moves.map((mv: any, mi: number) => (
                  <Group key={mi} gap={4} wrap="nowrap">
                    {[0, 1, 2].map((a) => (
                      <NumberInput key={a} size="xs" w={46} min={0} value={mv[0][a]} hideControls
                        onChange={(x) => updDef({ moves: p.deformation.moves.map((m: any, k: number) => (k === mi ? [m[0].map((c: number, j: number) => (j === a ? Number(x) || 0 : c)), m[1]] : m)) })} />
                    ))}
                    <Text size="xs">→</Text>
                    {[0, 1, 2].map((a) => (
                      <NumberInput key={"d" + a} size="xs" w={70} value={mv[1][a]} decimalScale={4} hideControls
                        onChange={(x) => updDef({ moves: p.deformation.moves.map((m: any, k: number) => (k === mi ? [m[0], m[1].map((c: number, j: number) => (j === a ? Number(x) || 0 : c))] : m)) })} />
                    ))}
                    <ActionIcon size="sm" variant="subtle" color="red" onClick={() => updDef({ moves: p.deformation.moves.filter((_: any, k: number) => k !== mi) })}><IconTrash size={12} /></ActionIcon>
                  </Group>
                ))}
                <Button size="compact-xs" variant="subtle" onClick={() => updDef({ moves: [...p.deformation.moves, [[1, 1, 1], [0, 0, 0]]] })}>Add control point</Button>
                <Text size="xs" c="dimmed">Interior indices run 1…divisions−1; boundary points are frozen.</Text>
              </>
            )}
            {p.deformation.kind === "rbf" && (
              <>
                <NumberInput size="xs" label="Support radius [m]" value={p.deformation.support_radius} decimalScale={4} hideControls onChange={(x) => updDef({ support_radius: Number(x) || 0.1 })} />
                <SegmentedControl size="xs" value={p.deformation.basis} data={[{ value: "wendland_c2", label: "Wendland C2 (local)" }, { value: "thin_plate", label: "Thin plate (global)" }]} onChange={(b) => updDef({ basis: b })} />
                {p.deformation.handles.map((h: any, hi: number) => (
                  <Paper key={hi} p={4} withBorder>
                    <Vec label={`Handle ${hi + 1} point [m]`} value={h[0]} onChange={(v) => updDef({ handles: p.deformation.handles.map((x: any, k: number) => (k === hi ? [v, x[1]] : x)) })} />
                    <Vec label="Displacement per unit [m]" value={h[1]} onChange={(v) => updDef({ handles: p.deformation.handles.map((x: any, k: number) => (k === hi ? [x[0], v] : x)) })} />
                  </Paper>
                ))}
                <Button size="compact-xs" variant="subtle" onClick={() => updDef({ handles: [...p.deformation.handles, [[0, 0, 0], [0, 0, 0]]] })}>Add handle</Button>
              </>
            )}
            <Switch size="xs" label="Mirror-symmetric about the centreline" checked={p.symmetric_about_centreline} onChange={(e) => upd({ symmetric_about_centreline: e.currentTarget.checked })} />
            <Button size="xs" color="red" variant="subtle" leftSection={<IconTrash size={14} />} onClick={() => { setParams(params.filter((_, i) => i !== sel)); setSel(0); setDirty(true); }}>
              Remove parameter
            </Button>
          </Stack>
        </Paper>
      )}
      <Group grow>
        <Button size="xs" leftSection={<IconDeviceFloppy size={14} />} onClick={save} disabled={!dirty}>Save parameters</Button>
      </Group>
      {params.length > 0 && (
        <>
          <SectionTitle>Preview values</SectionTitle>
          {params.map((x) => (
            <Stack key={x.id} gap={0}>
              <Group justify="space-between">
                <Text size="xs">{x.name}</Text>
                <Text size="xs" className="kpi-value">{fmt(values[x.id] ?? x.baseline, 4)} {x.units}</Text>
              </Group>
              <Slider size="sm" min={x.lower} max={x.upper} step={(x.upper - x.lower) / 100 || 0.01} value={values[x.id] ?? x.baseline}
                onChange={(v) => setValues((s) => ({ ...s, [x.id]: v }))} label={null} />
            </Stack>
          ))}
          <Switch size="xs" label="Run the full validity check (self-intersection, enclosure)" checked={withCheck} onChange={(e) => setWithCheck(e.currentTarget.checked)} />
          <Button size="xs" variant="default" leftSection={<IconRefresh size={14} />} onClick={doPreview} loading={busy}>Preview deformed geometry</Button>
        </>
      )}
    </Stack>
  );

  return (
    <PageLayout side={side} sideWidth={460} pad>
      <div style={{ position: "relative", height: "100%" }}>
        <Viewport3D onReady={onViewer}>
          {morph && (
            <MorphControls header={morph.header} range={morph.range} t={t}
              onT={(x) => { setT(x); viewer.current?.morphResult("morph", x); }} />
          )}
        </Viewport3D>
      </div>
    </PageLayout>
  );
}

