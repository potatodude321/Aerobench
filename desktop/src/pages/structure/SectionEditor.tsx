/**
 * One section of the structure: a sheet of one isotropic material, or a laminate of plies
 * listed from the outer (aerodynamic) surface inwards.
 */
import { ActionIcon, Group, MultiSelect, SegmentedControl, Select, Stack, Switch, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import type { Material, Ply, StructSection, Vec3 } from "../../api/types";
import { fmt } from "../../components/ColorLegend";
import { Hint, NumField } from "../../components/ui";

const DIRECTIONS: { value: string; label: string; v: Vec3 }[] = [
  { value: "y", label: "Along the span (y)", v: [0, 1, 0] },
  { value: "x", label: "Along the flow (x)", v: [1, 0, 0] },
  { value: "z", label: "Vertical (z)", v: [0, 0, 1] },
];

function dirKey(v: Vec3): string {
  const a = v.map(Math.abs);
  const i = a.indexOf(Math.max(...a));
  return ["x", "y", "z"][i];
}

export function stackOf(s: StructSection): Ply[] {
  return s.symmetric ? [...s.plies, ...[...s.plies].reverse()] : s.plies;
}

export function sectionThickness(s: StructSection): number {
  return s.kind === "sheet" ? s.thickness || 0 : stackOf(s).reduce((t, p) => t + p.thickness, 0);
}

export function sectionMass(s: StructSection, mats: Record<string, Material>): number | null {
  if (s.kind === "sheet") {
    const m = mats[s.material || ""];
    return m ? (s.thickness || 0) * m.density : null;
  }
  let sum = 0;
  for (const p of stackOf(s)) {
    const m = mats[p.material];
    if (!m) return null;
    sum += p.thickness * m.density;
  }
  return sum;
}

export function SectionEditor({
  section,
  materials,
  parts,
  onChange,
  onRemove,
}: {
  section: StructSection;
  materials: Material[];
  parts: { value: string; label: string }[];
  onChange: (s: StructSection) => void;
  onRemove: () => void;
}) {
  const s = section;
  const byId = Object.fromEntries(materials.map((m) => [m.id, m]));
  const [name, setName] = useState(s.name);
  useEffect(() => setName(s.name), [s.name]);
  const metals = materials.filter((m) => m.kind === "isotropic").map((m) => ({ value: m.id, label: m.name }));
  const plyData = [
    { group: "Composite plies", items: materials.filter((m) => m.kind === "lamina").map((m) => ({ value: m.id, label: m.name })) },
    { group: "Metals and plastics", items: metals },
  ];
  const upd = (patch: Partial<StructSection>) => onChange({ ...s, ...patch });
  const setPly = (i: number, patch: Partial<Ply>) => upd({ plies: s.plies.map((p, k) => (k === i ? { ...p, ...patch } : p)) });
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= s.plies.length) return;
    const pl = [...s.plies];
    [pl[i], pl[j]] = [pl[j], pl[i]];
    upd({ plies: pl });
  };
  const addPly = () => {
    const last = s.plies[s.plies.length - 1];
    const mat = last?.material || materials.find((m) => m.kind === "lamina")?.id || "";
    const t = last?.thickness || byId[mat]?.ply_thickness || 0.0002;
    upd({ plies: [...s.plies, { material: mat, thickness: t, angle: last ? (last.angle === 0 ? 45 : 0) : 0 }] });
  };
  const toKind = (k: string) => {
    if (k === s.kind) return;
    if (k === "sheet") upd({ kind: "sheet", material: s.material || metals[0]?.value || null, thickness: s.thickness || 0.002 });
    else upd({ kind: "laminate", plies: s.plies.length ? s.plies : [{ material: "cfrp_twill_t300", thickness: 0.00022, angle: 0 }] });
  };
  const t = sectionThickness(s);
  const mass = sectionMass(s, byId);

  return (
    <Stack gap={8} className="ab-struct-section">
      <Group gap={6} wrap="nowrap" align="flex-end">
        <TextInput size="xs" label="Name" value={name} style={{ flex: 1 }} onChange={(e) => setName(e.currentTarget.value)}
          onBlur={() => name.trim() && name !== s.name && upd({ name: name.trim() })} />
        <Tooltip label="Remove this section">
          <ActionIcon size="md" variant="subtle" color="red" onClick={onRemove} aria-label={`Remove section ${s.name}`}>
            <IconTrash size={15} />
          </ActionIcon>
        </Tooltip>
      </Group>
      <MultiSelect size="xs" label="Parts" data={parts} value={s.components} placeholder={s.components.length ? undefined : "Every analysed part not named in another section"}
        onChange={(v) => upd({ components: v })} clearable />
      <SegmentedControl size="xs" fullWidth value={s.kind} onChange={toKind}
        data={[{ value: "laminate", label: "Composite laminate" }, { value: "sheet", label: "Sheet (metal or plastic)" }]} />
      {s.kind === "sheet" ? (
        <Group gap={6} grow>
          <Select size="xs" label="Material" data={metals} value={s.material || null} onChange={(v) => v && upd({ material: v })} searchable />
          <NumField label="Thickness" units="mm" value={(s.thickness || 0) * 1000} decimals={3} min={0.01}
            onCommit={(v) => v > 0 && upd({ thickness: v / 1000 })} />
        </Group>
      ) : (
        <>
          <Select size="xs" label="0° fibre direction" data={DIRECTIONS.map(({ value, label }) => ({ value, label }))} value={dirKey(s.zero_direction)}
            onChange={(v) => v && upd({ zero_direction: DIRECTIONS.find((d) => d.value === v)!.v })} />
          <Table className="compact ab-ply-table" verticalSpacing={2} horizontalSpacing={4}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th style={{ width: 22 }} />
                <Table.Th>Ply material</Table.Th>
                <Table.Th style={{ width: 74 }}>mm</Table.Th>
                <Table.Th style={{ width: 58 }}>Angle</Table.Th>
                <Table.Th style={{ width: 66 }} />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {s.plies.map((p, i) => (
                <Table.Tr key={i}>
                  <Table.Td>
                    <Text size="xs" c="dimmed" className="num">
                      {i + 1}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <Select size="xs" data={plyData} value={p.material} onChange={(v) => v && setPly(i, { material: v, thickness: byId[v]?.ply_thickness || p.thickness })}
                      searchable comboboxProps={{ width: 280, position: "bottom-start" }} aria-label={`Material of ply ${i + 1}`} />
                  </Table.Td>
                  <Table.Td>
                    <NumField label="" value={p.thickness * 1000} decimals={3} min={0.01} onCommit={(v) => v > 0 && setPly(i, { thickness: v / 1000 })} />
                  </Table.Td>
                  <Table.Td>
                    <NumField label="" value={p.angle} decimals={1} min={-90} max={90} onCommit={(v) => setPly(i, { angle: Math.max(-90, Math.min(90, v)) })} />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={0} wrap="nowrap">
                      <ActionIcon size="sm" variant="subtle" color="gray" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move ply outwards">
                        <IconArrowUp size={13} />
                      </ActionIcon>
                      <ActionIcon size="sm" variant="subtle" color="gray" disabled={i === s.plies.length - 1} onClick={() => move(i, 1)} aria-label="Move ply inwards">
                        <IconArrowDown size={13} />
                      </ActionIcon>
                      <ActionIcon size="sm" variant="subtle" color="red" disabled={s.plies.length === 1} onClick={() => upd({ plies: s.plies.filter((_, k) => k !== i) })}
                        aria-label={`Remove ply ${i + 1}`}>
                        <IconTrash size={13} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Group justify="space-between">
            <ActionIcon.Group>
              <Tooltip label="Add a ply on the inside">
                <ActionIcon size="sm" variant="default" onClick={addPly} aria-label="Add ply">
                  <IconPlus size={13} />
                </ActionIcon>
              </Tooltip>
            </ActionIcon.Group>
            <Switch size="xs" label="Mirror about the mid-plane" checked={s.symmetric} onChange={(e) => upd({ symmetric: e.currentTarget.checked })} />
          </Group>
          <Hint>
            Ply 1 is on the outer surface. Angles turn from the 0° direction about the outward normal.
            {s.symmetric ? ` The list is the outer half: ${s.plies.length * 2} plies in all.` : ""}
          </Hint>
        </>
      )}
      <Text size="xs" c="dimmed">
        {fmt(t * 1000, 2)} mm thick, {mass != null ? `${fmt(mass, 2)} kg/m²` : "unknown material"}
      </Text>
    </Stack>
  );
}
