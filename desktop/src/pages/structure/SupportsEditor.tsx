/**
 * Supports: where the part is bolted or bonded to the car. Each is a box; the surface inside
 * it is held. A box that takes in a patch of the surface holds it like a bonded or bolted
 * flange; one that only touches a line of it clamps that line.
 */
import { ActionIcon, Button, Group, Menu, SimpleGrid, Stack, Switch, Text, TextInput, Tooltip } from "@mantine/core";
import { IconPlus, IconTrash } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import type { StructSupport, Vec3 } from "../../api/types";
import { Hint, NumField } from "../../components/ui";

export const SUPPORT_COLOR = 0xe0b05c;

function uniqueId(base: string, taken: string[]): string {
  let id = base;
  for (let i = 2; taken.includes(id); i++) id = `${base}${i}`;
  return id;
}

export function SupportsEditor({
  supports,
  presets,
  counts,
  onChange,
  bounds,
}: {
  supports: StructSupport[];
  presets: { preset: string; name: string; shapes: any[] }[];
  /** surface nodes held by each support (from the latest preview) */
  counts: Record<string, number>;
  onChange: (s: StructSupport[]) => void;
  /** extent of the analysed parts, for a new custom box */
  bounds: { min: Vec3; max: Vec3 } | null;
}) {
  const [sel, setSel] = useState(0);
  useEffect(() => {
    if (sel >= supports.length) setSel(Math.max(0, supports.length - 1));
  }, [supports.length, sel]);
  const s = supports[sel];
  const taken = supports.map((x) => x.id);

  const addPreset = (p: { preset: string; name: string; shapes: any[] }) => {
    const add = p.shapes.map((shape, k) => ({
      id: uniqueId(`${p.preset}${p.shapes.length > 1 ? k + 1 : ""}`.replace(/[^A-Za-z0-9_]/g, "_"), [...taken]),
      name: p.shapes.length > 1 ? `${p.name.split(" (")[0]}, ${k + 1}` : p.name.split(" (")[0],
      shape,
      enabled: true,
    }));
    // ids must stay unique within the batch as well
    const ids = [...taken];
    for (const a of add) {
      a.id = uniqueId(a.id, ids);
      ids.push(a.id);
    }
    onChange([...supports, ...add]);
    setSel(supports.length);
  };
  const addCustom = () => {
    const lo = bounds?.min ?? [0, 0, 0];
    const hi = bounds?.max ?? [0.1, 0.1, 0.1];
    const c = lo.map((v, i) => (v + hi[i]) / 2) as Vec3;
    const h = lo.map((v, i) => Math.max(0.01, (hi[i] - v) * 0.1)) as Vec3;
    onChange([...supports, { id: uniqueId("support", taken), name: "Support", enabled: true,
      shape: { kind: "box", min: c.map((v, i) => +(v - h[i]).toFixed(4)) as Vec3, max: c.map((v, i) => +(v + h[i]).toFixed(4)) as Vec3 } }]);
    setSel(supports.length);
  };
  const upd = (patch: Partial<StructSupport>) => onChange(supports.map((x, i) => (i === sel ? { ...x, ...patch } : x)));
  const setBox = (axis: number, end: 0 | 1, mm: number) => {
    if (!s || s.shape.kind !== "box") return;
    const mn = [...(s.shape.min as Vec3)] as Vec3;
    const mx = [...(s.shape.max as Vec3)] as Vec3;
    (end === 0 ? mn : mx)[axis] = mm / 1000;
    if (mn[axis] > mx[axis]) return;
    upd({ shape: { ...s.shape, min: mn, max: mx } });
  };

  return (
    <Stack gap={6}>
      <Group justify="flex-end">
        <Menu position="bottom-end" width={320}>
          <Menu.Target>
            <Button size="compact-xs" variant="subtle" leftSection={<IconPlus size={13} />}>
              Add support
            </Button>
          </Menu.Target>
          <Menu.Dropdown>
            {presets.map((p) => (
              <Menu.Item key={p.preset} onClick={() => addPreset(p)}>
                <Text size="sm">{p.name}</Text>
              </Menu.Item>
            ))}
            <Menu.Divider />
            <Menu.Item onClick={addCustom}>
              <Text size="sm">A box placed by hand</Text>
            </Menu.Item>
          </Menu.Dropdown>
        </Menu>
      </Group>
      {supports.length === 0 && <Hint>The part is not held anywhere yet. Add the places where it is bolted or bonded to the car.</Hint>}
      <Stack gap={2}>
        {supports.map((x, i) => (
          <div key={x.id} className="ab-step" aria-current={i === sel ? "page" : undefined} onClick={() => setSel(i)}
            style={{ height: "auto", padding: "5px 8px", gridTemplateColumns: "12px 1fr auto", cursor: "pointer", gap: 8 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, background: "#e0b05c", opacity: x.enabled ? 1 : 0.3 }} />
            <span style={{ minWidth: 0 }}>
              <Text size="sm" c={x.enabled ? undefined : "dimmed"} truncate>
                {x.name}
              </Text>
              <Text size="xs" c={counts[x.id] === 0 ? "var(--ab-err)" : "dimmed"} truncate>
                {counts[x.id] === undefined ? "box" : counts[x.id] === 0 ? "holds nothing: move it onto the part" : `holds ${counts[x.id]} surface points`}
              </Text>
            </span>
            <Switch size="xs" checked={x.enabled} onClick={(e) => e.stopPropagation()} aria-label={`Use support ${x.name}`}
              onChange={(e) => { const on = e.currentTarget.checked; onChange(supports.map((y, k) => (k === i ? { ...y, enabled: on } : y))); }} />
          </div>
        ))}
      </Stack>
      {s && s.shape.kind === "box" && (
        <Stack gap={6} mt={6}>
          <Group gap={6} wrap="nowrap" align="flex-end">
            <TextInput size="xs" label="Name" defaultValue={s.name} key={s.id} style={{ flex: 1 }}
              onBlur={(e) => { const v = e.currentTarget.value.trim(); if (v && v !== s.name) upd({ name: v }); }} />
            <Tooltip label="Remove this support">
              <ActionIcon size="md" variant="subtle" color="red" aria-label={`Remove support ${s.name}`} onClick={() => onChange(supports.filter((_, i) => i !== sel))}>
                <IconTrash size={15} />
              </ActionIcon>
            </Tooltip>
          </Group>
          <SimpleGrid cols={3} spacing={6}>
            {(["x", "y", "z"] as const).map((a, i) => (
              <NumField key={`lo${a}`} label={`${a} from`} units="mm" value={(s.shape.min as Vec3)[i] * 1000} decimals={1} onCommit={(v) => setBox(i, 0, v)} />
            ))}
            {(["x", "y", "z"] as const).map((a, i) => (
              <NumField key={`hi${a}`} label={`${a} to`} units="mm" value={(s.shape.max as Vec3)[i] * 1000} decimals={1} onCommit={(v) => setBox(i, 1, v)} />
            ))}
          </SimpleGrid>
          <Hint>Coordinates as in the flow simulation (x downstream, z up). Everything of the part inside the box is held.</Hint>
        </Stack>
      )}
      {s && s.shape.kind !== "box" && <Hint>This support is a {s.shape.kind}; edit it in the project file.</Hint>}
    </Stack>
  );
}
