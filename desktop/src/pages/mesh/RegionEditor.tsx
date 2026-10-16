/**
 * Refinement boxes of the mesh: the automatic ones (level and on/off) and your own boxes and
 * modelled volumes. Your boxes are edited in the 3D view (click a box, drag a face handle) or
 * with the fields, whose labels can be dragged too; the change shows at once. A box can be
 * mirrored to the other side of a plane. Where boxes overlap the finer level applies; a box
 * entirely inside a finer one changes nothing, and the list says so.
 */
import { ActionIcon, Button, Checkbox, Group, NumberInput, Stack, Switch, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MeshPlan } from "../../api/types";
import { fmt } from "../../components/ColorLegend";
import { MirrorMenu } from "../../components/MirrorMenu";
import { ScrubField } from "../../components/ScrubField";
import { Hint, SectionTitle } from "../../components/ui";
import { BoxEditor, type EditableBox } from "../../viewport/BoxEditor";
import { LEVEL_COLORS, levelColorCss } from "../../viewport/colormaps";
import type { Viewer } from "../../viewport/Viewer";

type Region = { id: string; name: string; kind: string; level: number; shape: any; auto: boolean; enabled: boolean; cell_size?: number };
const AX = ["x", "y", "z"] as const;

/** Bounding box of a region's shape (for overlaps). */
function bounds(s: any): [number[], number[]] | null {
  if (s.kind === "box") return [s.min, s.max];
  if (s.kind === "surface") return [s.bbox_min, s.bbox_max];
  if (s.kind === "cylinder") {
    const lo = [0, 1, 2].map((a) => Math.min(s.point1[a], s.point2[a]) - s.radius);
    const hi = [0, 1, 2].map((a) => Math.max(s.point1[a], s.point2[a]) + s.radius);
    return [lo, hi];
  }
  if (s.kind === "sphere") return [s.centre.map((c: number) => c - s.radius), s.centre.map((c: number) => c + s.radius)];
  return null;
}

/** How each region relates to the others where they overlap (boxes exact, other shapes by their bounds). */
export function overlapNotes(regions: Region[]): Record<string, string> {
  const out: Record<string, string> = {};
  const on = regions.filter((r) => r.enabled);
  for (const r of on) {
    const b = bounds(r.shape);
    if (!b) continue;
    const vol = b[0].reduce((p, _, a) => p * Math.max(0, b[1][a] - b[0][a]), 1);
    let inside: Region | null = null;
    const finer: string[] = [];
    for (const o of on) {
      if (o.id === r.id) continue;
      const c = bounds(o.shape);
      if (!c) continue;
      const lo = [0, 1, 2].map((a) => Math.max(b[0][a], c[0][a]));
      const hi = [0, 1, 2].map((a) => Math.min(b[1][a], c[1][a]));
      const ov = lo.reduce((p, _, a) => p * Math.max(0, hi[a] - lo[a]), 1);
      if (ov <= 0) continue;
      const exact = r.shape.kind === "box" && o.shape.kind === "box";
      // equally fine: the one listed first does the refining (as the cell estimate counts it)
      const finerOrFirst = o.level > r.level || (o.level === r.level && on.indexOf(o) < on.indexOf(r));
      if (exact && ov >= vol * (1 - 1e-9) && finerOrFirst && (!inside || o.level > inside.level)) inside = o;
      else if (o.level > r.level) finer.push(o.name);
    }
    if (inside) out[r.id] = `No effect: it lies inside “${inside.name}”, which is at least as fine (level ${inside.level}).`;
    // the automatic boxes are nested by design: only your own say where a finer one takes over
    else if (finer.length && !r.auto) out[r.id] = `Where it overlaps ${finer.slice(0, 2).map((n) => `“${n}”`).join(" and ")}${finer.length > 2 ? ` and ${finer.length - 2} more` : ""}, the finer level there applies.`;
  }
  return out;
}

export function RegionEditor({
  plan,
  regions: cfgRegions,
  patch,
  viewer,
  viewerReady,
  active,
  hidden,
  setHidden,
  centreY,
}: {
  plan: MeshPlan | null;
  regions: any[];
  patch: (regions: any[]) => void;
  viewer: React.MutableRefObject<Viewer | null>;
  viewerReady: number;
  active: boolean;
  hidden: string[];
  setHidden: (f: (h: string[]) => string[]) => void;
  centreY: number;
}) {
  const [sel, setSel] = useState<string | null>(null);
  const [live, setLive] = useState<{ id: string; min: number[]; max: number[] } | null>(null);
  const editor = useRef<BoxEditor | null>(null);

  // everything the plan resolves (enabled) plus your own regions that are switched off
  const list: Region[] = useMemo(() => {
    const fromPlan: Region[] = (plan?.regions || []).map((r) => ({ ...r, enabled: true }));
    const ids = new Set(fromPlan.map((r) => r.id));
    // switched-off regions (your own, or automatic ones you switched off) are not in the plan
    const off: Region[] = (cfgRegions || []).filter((r: any) => !ids.has(r.id) && !r.enabled).map((r: any) => ({ ...r, enabled: false }));
    return [...fromPlan, ...off].map((r) => (live && r.id === live.id ? { ...r, shape: { ...r.shape, min: live.min, max: live.max } } : r));
  }, [plan, cfgRegions, live]);
  const editable = (r: Region) => !r.auto && r.shape.kind === "box";
  const notes = useMemo(() => overlapNotes(list), [list]);
  const cur = list.find((r) => r.id === sel) || null;

  /** Write one of your regions back (keeping the others as they are). */
  const save = (id: string, change: (r: any) => any) => {
    const own = (cfgRegions || []).map((r: any) => (r.id === id ? change(r) : r));
    if (!own.some((r: any) => r.id === id)) {
      // an automatic region: store an override of its level / switch
      const r = list.find((x) => x.id === id)!;
      own.push(change({ id, name: r.name, kind: r.kind, shape: r.shape, level: r.level, enabled: r.enabled, auto: r.auto }));
    }
    patch(own);
  };

  // ---------------------------------------------------------------- 3D editing
  useEffect(() => {
    const v = viewer.current;
    if (!v) return;
    const ed = new BoxEditor(v);
    editor.current = ed;
    return () => {
      ed.dispose();
      editor.current = null;
    };
  }, [viewerReady, viewer]);
  useEffect(() => {
    const ed = editor.current;
    if (!ed) return;
    ed.onSelect = (id) => setSel(id);
    ed.onChange = (id, min, max) => setLive({ id, min, max });
    ed.onCommit = (id, min, max) => {
      save(id, (r: any) => ({ ...r, shape: { ...r.shape, min, max } }));
      setTimeout(() => setLive(null), 0);
    };
    const boxes: EditableBox[] = active
      ? list.filter((r) => editable(r) && r.enabled && !hidden.includes(r.id)).map((r) => ({
          id: r.id, min: r.shape.min, max: r.shape.max, color: LEVEL_COLORS[Math.min(r.level, LEVEL_COLORS.length - 1)],
        }))
      : [];
    ed.setBoxes(boxes, sel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, sel, active, hidden, viewerReady]);

  // ---------------------------------------------------------------- edits
  const addBox = () => {
    if (!plan) return;
    const lo = plan.vehicle_bbox_min;
    const hi = plan.vehicle_bbox_max;
    const L = hi[0] - lo[0];
    const taken = new Set(list.map((r) => r.id));
    let k = 1;
    while (taken.has(`user_box_${k}`)) k++;
    const near = plan.regions.find((r) => r.kind === "wake_near");
    const level = near ? near.level : Math.max(0, plan.body_level - 1);
    const id = `user_box_${k}`;
    const r = { id, name: `Box ${k}`, kind: "user", auto: false, enabled: true, level,
      shape: { kind: "box", min: [hi[0] - 0.25 * L, lo[1], lo[2]], max: [hi[0] + 0.25 * L, hi[1], hi[2]] } };
    patch([...(cfgRegions || []), r]);
    setSel(id);
  };
  const setBox = (axis: number, end: 0 | 1, val: number, commit: boolean) => {
    if (!cur) return;
    const mn = [...cur.shape.min];
    const mx = [...cur.shape.max];
    (end === 0 ? mn : mx)[axis] = val;
    if (end === 0) mn[axis] = Math.min(mn[axis], mx[axis] - 1e-3);
    else mx[axis] = Math.max(mx[axis], mn[axis] + 1e-3);
    if (commit) {
      save(cur.id, (r: any) => ({ ...r, shape: { ...r.shape, min: mn, max: mx } }));
      setTimeout(() => setLive(null), 0);
    } else setLive({ id: cur.id, min: mn, max: mx });
  };
  const mirror = (axis: number, plane: number) => {
    if (!cur) return;
    const taken = new Set(list.map((r) => r.id));
    let id = `${cur.id}_mirror`;
    for (let k = 2; taken.has(id); k++) id = `${cur.id}_mirror${k}`;
    const mn = [...cur.shape.min];
    const mx = [...cur.shape.max];
    mn[axis] = 2 * plane - cur.shape.max[axis];
    mx[axis] = 2 * plane - cur.shape.min[axis];
    patch([...(cfgRegions || []), { id, name: `${cur.name} (mirrored)`, kind: "user", auto: false, enabled: true, level: cur.level,
      shape: { kind: "box", min: mn, max: mx } }]);
    setSel(id);
  };
  const remove = (id: string) => {
    patch((cfgRegions || []).filter((r: any) => r.id !== id));
    if (sel === id) setSel(null);
  };

  return (
    <>
      <SectionTitle right={
        <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={12} />} onClick={addBox} disabled={!plan}>
          Add box
        </Button>
      }>
        Refinement boxes
      </SectionTitle>
      <Table className="compact" withRowBorders={false}>
        <Table.Tbody>
          {list.map((r) => (
            <Table.Tr key={r.id} style={{ background: r.id === sel ? "var(--ab-surface-2)" : undefined, cursor: "pointer" }} onClick={() => setSel(r.id)}>
              <Table.Td w={28} onClick={(e) => e.stopPropagation()}>
                <Tooltip label="Show in the planning view">
                  <Checkbox size="xs" checked={!hidden.includes(r.id)} onChange={(e) => { const on = e.currentTarget.checked; setHidden((h) => (on ? h.filter((x) => x !== r.id) : [...h, r.id])); }} />
                </Tooltip>
              </Table.Td>
              <Table.Td>
                <Group gap={6} wrap="nowrap">
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: levelColorCss(r.level), flexShrink: 0, opacity: r.enabled ? 1 : 0.3 }} />
                  <Text size="xs" c={r.enabled ? undefined : "dimmed"}>
                    {r.name}
                    {r.shape.kind === "surface" ? " (modelled)" : ""}
                  </Text>
                </Group>
                {notes[r.id] && (
                  <Text size="xs" c={notes[r.id].startsWith("No effect") ? "yellow" : "dimmed"} style={{ lineHeight: 1.3 }}>
                    {notes[r.id]}
                  </Text>
                )}
              </Table.Td>
              <Table.Td onClick={(e) => e.stopPropagation()}>
                <NumberInput size="xs" w={58} min={0} max={12} value={r.level} onChange={(v) => typeof v === "number" && save(r.id, (x: any) => ({ ...x, level: v }))} />
              </Table.Td>
              <Table.Td>
                <Text size="xs">{r.cell_size ? `${fmt(r.cell_size * 1000, 0)} mm` : ""}</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {cur && (
        <Stack gap={8} mt={8} p={8} style={{ border: "1px solid var(--ab-line)", borderRadius: 6 }}>
          <Group justify="space-between" wrap="nowrap">
            {cur.auto ? (
              <Text size="sm" fw={500}>{cur.name}</Text>
            ) : (
              <TextInput key={cur.id} size="xs" style={{ flex: 1 }} defaultValue={cur.name}
                onBlur={(e) => { const v = e.currentTarget.value.trim(); if (v && v !== cur.name) save(cur.id, (x: any) => ({ ...x, name: v })); }}
                onKeyDown={(e) => e.key === "Enter" && (e.currentTarget as HTMLInputElement).blur()} />
            )}
            <Group gap={2} wrap="nowrap">
              <Switch size="xs" checked={cur.enabled} label="On" onChange={(e) => { const on = e.currentTarget.checked; save(cur.id, (x: any) => ({ ...x, enabled: on })); }} />
              {editable(cur) && (
                <MirrorMenu defaults={[cur.shape.max[0], centreY, cur.shape.min[2]]} onMirror={mirror} label="Mirror a copy of this box" />
              )}
              {!cur.auto && (
                <Tooltip label="Remove this box">
                  <ActionIcon size="sm" variant="subtle" color="red" onClick={() => remove(cur.id)} aria-label="Remove box">
                    <IconTrash size={14} />
                  </ActionIcon>
                </Tooltip>
              )}
            </Group>
          </Group>
          {editable(cur) ? (
            <>
              <Hint>Drag the white handles on the box in the view, or drag a field's label left and right.</Hint>
              {AX.map((a, i) => (
                <Group key={a} gap={6} grow>
                  <ScrubField label={`${a} from`} value={cur.shape.min[i]} step={0.005} decimals={3} units="m"
                    onChange={(v) => setBox(i, 0, v, false)} onCommit={(v) => setBox(i, 0, v, true)} />
                  <ScrubField label={`${a} to`} value={cur.shape.max[i]} step={0.005} decimals={3} units="m"
                    onChange={(v) => setBox(i, 1, v, false)} onCommit={(v) => setBox(i, 1, v, true)} />
                </Group>
              ))}
            </>
          ) : cur.auto ? (
            <Hint>Placed automatically from the vehicle's size; you can change its level or switch it off. Add a box of your own for a region of your choice.</Hint>
          ) : cur.shape.kind === "surface" ? (
            <Hint>A volume you modelled ({cur.shape.source_filename}); the mesh is refined everywhere inside it. Its shape comes from the file.</Hint>
          ) : null}
        </Stack>
      )}
    </>
  );
}
