/**
 * Material library: the built-in metals, plastics and composite plies, and the user's own
 * materials (kept for every project on this computer). Composite plies are entered with the
 * lamina constants and strengths of a data sheet; the engine checks that the constants are
 * physically possible before saving.
 */
import { ActionIcon, Alert, Button, Group, Modal, ScrollArea, SegmentedControl, SimpleGrid, Stack, Table, Tabs, Text, TextInput, Tooltip } from "@mantine/core";
import { IconCopy, IconPencil, IconPlus, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { api, EngineError } from "../../api/client";
import type { Material } from "../../api/types";
import { fmt } from "../../components/ColorLegend";
import { notifyError, notifyOk } from "../../components/notify";
import { Hint, NumField } from "../../components/ui";

const GPa = 1e9;
const MPa = 1e6;

type Draft = Partial<Material> & { kind: "isotropic" | "lamina" };

const EMPTY: Record<string, Draft> = {
  isotropic: { kind: "isotropic", name: "", density: 2700, E: 70 * GPa, nu: 0.33, yield_strength: 250 * MPa, ultimate_strength: 300 * MPa, source: "" },
  lamina: { kind: "lamina", name: "", density: 1550, E1: 130 * GPa, E2: 9 * GPa, G12: 4.5 * GPa, nu12: 0.3, Xt: 2000 * MPa, Xc: 1200 * MPa, Yt: 50 * MPa,
    Yc: 200 * MPa, S12: 80 * MPa, ply_thickness: 0.00015, source: "" },
};

function slug(name: string, taken: string[]): string {
  let base = name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || "material";
  if (!/^[a-z]/.test(base)) base = "m_" + base;
  let id = base;
  for (let i = 2; taken.includes(id); i++) id = `${base}_${i}`;
  return id;
}

function MaterialForm({ draft, onChange }: { draft: Draft; onChange: (d: Draft) => void }) {
  const d = draft;
  const set = (k: keyof Material, scale = 1) => (v: number) => onChange({ ...d, [k]: v * scale });
  const g = (k: keyof Material, scale = 1) => (d[k] == null ? null : (d[k] as number) / scale);
  return (
    <Stack gap={8}>
      <TextInput size="xs" label="Name" value={d.name || ""} onChange={(e) => onChange({ ...d, name: e.currentTarget.value })} placeholder="e.g. Supplier prepreg 200 gsm" />
      {d.kind === "isotropic" ? (
        <SimpleGrid cols={3} spacing={6}>
          <NumField label="Young's modulus" units="GPa" value={g("E", GPa)} decimals={2} onCommit={set("E", GPa)} />
          <NumField label="Poisson's ratio" value={g("nu")} decimals={3} onCommit={set("nu")} />
          <NumField label="Density" units="kg/m³" value={g("density")} decimals={0} onCommit={set("density")} />
          <NumField label="Yield strength" units="MPa" value={g("yield_strength", MPa)} decimals={1} onCommit={set("yield_strength", MPa)} />
          <NumField label="Ultimate strength" units="MPa" value={g("ultimate_strength", MPa)} decimals={1} onCommit={set("ultimate_strength", MPa)} />
        </SimpleGrid>
      ) : (
        <>
          <Text size="xs" fw={500}>
            Stiffness of one ply (1 = along the fibres, 2 = across)
          </Text>
          <SimpleGrid cols={4} spacing={6}>
            <NumField label="E1" units="GPa" value={g("E1", GPa)} decimals={2} onCommit={set("E1", GPa)} />
            <NumField label="E2" units="GPa" value={g("E2", GPa)} decimals={2} onCommit={set("E2", GPa)} />
            <NumField label="G12" units="GPa" value={g("G12", GPa)} decimals={2} onCommit={set("G12", GPa)} />
            <NumField label="ν12" value={g("nu12")} decimals={3} onCommit={set("nu12")} />
          </SimpleGrid>
          <Text size="xs" fw={500}>
            Strengths of one ply
          </Text>
          <SimpleGrid cols={5} spacing={6}>
            <NumField label="Xt" units="MPa" value={g("Xt", MPa)} decimals={0} onCommit={set("Xt", MPa)} description="fibre tension" />
            <NumField label="Xc" units="MPa" value={g("Xc", MPa)} decimals={0} onCommit={set("Xc", MPa)} description="fibre compr." />
            <NumField label="Yt" units="MPa" value={g("Yt", MPa)} decimals={0} onCommit={set("Yt", MPa)} description="matrix tension" />
            <NumField label="Yc" units="MPa" value={g("Yc", MPa)} decimals={0} onCommit={set("Yc", MPa)} description="matrix compr." />
            <NumField label="S12" units="MPa" value={g("S12", MPa)} decimals={0} onCommit={set("S12", MPa)} description="shear" />
          </SimpleGrid>
          <SimpleGrid cols={3} spacing={6}>
            <NumField label="Cured ply thickness" units="mm" value={g("ply_thickness", 1e-3)} decimals={3} onCommit={set("ply_thickness", 1e-3)} />
            <NumField label="Density" units="kg/m³" value={g("density")} decimals={0} onCommit={set("density")} />
          </SimpleGrid>
          <Hint>Compressive strengths are entered as positive numbers. Out-of-plane constants default to a transversely isotropic ply.</Hint>
        </>
      )}
      <TextInput size="xs" label="Source" value={d.source || ""} onChange={(e) => onChange({ ...d, source: e.currentTarget.value })} placeholder="Data sheet, test report…" />
    </Stack>
  );
}

export function MaterialLibrary({ opened, onClose, builtin, user, onSaved }: {
  opened: boolean;
  onClose: () => void;
  builtin: Material[];
  user: Material[];
  onSaved: () => void;
}) {
  const [tab, setTab] = useState<string | null>("lamina");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const all = [...builtin, ...user];

  const start = (d: Draft, id: string | null) => {
    setDraft(d);
    setEditing(id);
    setError(null);
  };
  const save = async () => {
    if (!draft || !draft.name?.trim()) {
      setError("Give the material a name.");
      return;
    }
    const id = editing || slug(draft.name, all.map((m) => m.id));
    setSaving(true);
    try {
      await api.put(`/api/materials/${id}`, { ...draft, id, name: draft.name.trim() });
      notifyOk(`${draft.name} is in your library.`, editing ? "Material updated" : "Material added");
      setDraft(null);
      setEditing(null);
      onSaved();
    } catch (e) {
      if (e instanceof EngineError) {
        const errs = (e.diagnostic as any).errors as { msg: string }[] | undefined;
        setError(errs?.length ? errs.map((x) => x.msg.replace(/^Value error, /, "")).join(" ") : e.diagnostic.summary);
      } else notifyError(e);
    } finally {
      setSaving(false);
    }
  };
  const del = async (m: Material) => {
    try {
      await api.del(`/api/materials/${m.id}`);
      notifyOk(`${m.name} was removed from your library.`);
      onSaved();
    } catch (e) {
      notifyError(e);
    }
  };

  const rows = (kind: "isotropic" | "lamina") =>
    all.filter((m) => m.kind === kind).map((m) => (
      <Table.Tr key={m.id}>
        <Table.Td>
          <Text size="xs">{m.name}</Text>
          <Text size="xs" c="dimmed" truncate maw={260}>
            {m.builtin ? m.source : m.source || "your material"}
          </Text>
        </Table.Td>
        {kind === "isotropic" ? (
          <>
            <Table.Td className="num">{fmt((m.E || 0) / GPa, 1)}</Table.Td>
            <Table.Td className="num">{fmt((m.yield_strength || 0) / MPa, 0)}</Table.Td>
            <Table.Td className="num">{fmt((m.ultimate_strength || 0) / MPa, 0)}</Table.Td>
          </>
        ) : (
          <>
            <Table.Td className="num">{fmt((m.E1 || 0) / GPa, 0)} / {fmt((m.E2 || 0) / GPa, 1)}</Table.Td>
            <Table.Td className="num">{fmt((m.Xt || 0) / MPa, 0)} / {fmt((m.Xc || 0) / MPa, 0)}</Table.Td>
            <Table.Td className="num">{fmt((m.ply_thickness || 0) * 1000, 2)}</Table.Td>
          </>
        )}
        <Table.Td className="num">{fmt(m.density, 0)}</Table.Td>
        <Table.Td>
          <Group gap={0} wrap="nowrap" justify="flex-end">
            <Tooltip label="Start a new material from this one">
              <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`Copy ${m.name}`}
                onClick={() => start({ ...m, name: `${m.name} (copy)`, builtin: false, source: m.source }, null)}>
                <IconCopy size={13} />
              </ActionIcon>
            </Tooltip>
            {!m.builtin && (
              <>
                <ActionIcon size="sm" variant="subtle" color="gray" aria-label={`Edit ${m.name}`} onClick={() => start({ ...m }, m.id)}>
                  <IconPencil size={13} />
                </ActionIcon>
                <ActionIcon size="sm" variant="subtle" color="red" aria-label={`Delete ${m.name}`} onClick={() => del(m)}>
                  <IconTrash size={13} />
                </ActionIcon>
              </>
            )}
          </Group>
        </Table.Td>
      </Table.Tr>
    ));

  return (
    <Modal opened={opened} onClose={onClose} title="Materials" size={760} centered>
      {draft ? (
        <Stack gap={10}>
          <Group justify="space-between">
            <Text fw={500}>{editing ? `Edit ${draft.name}` : "New material"}</Text>
            {!editing && (
              <SegmentedControl size="xs" value={draft.kind} onChange={(k) => setDraft({ ...EMPTY[k], name: draft.name, source: draft.source })}
                data={[{ value: "lamina", label: "Composite ply" }, { value: "isotropic", label: "Metal or plastic" }]} />
            )}
          </Group>
          <MaterialForm draft={draft} onChange={setDraft} />
          {error && (
            <Alert color="red" p={8}>
              <Text size="xs">{error}</Text>
            </Alert>
          )}
          <Group justify="flex-end" gap={6}>
            <Button size="xs" variant="default" onClick={() => setDraft(null)}>
              Cancel
            </Button>
            <Button size="xs" loading={saving} onClick={save}>
              {editing ? "Save changes" : "Add to my library"}
            </Button>
          </Group>
        </Stack>
      ) : (
        <Stack gap={8}>
          <Group justify="space-between">
            <Hint>Built-in values are typical data-sheet values. Your own materials are kept on this computer for every project.</Hint>
            <Button size="xs" variant="default" leftSection={<IconPlus size={13} />} onClick={() => start({ ...EMPTY[tab === "isotropic" ? "isotropic" : "lamina"] }, null)}>
              New material
            </Button>
          </Group>
          <Tabs value={tab} onChange={setTab}>
            <Tabs.List>
              <Tabs.Tab value="lamina">Composite plies</Tabs.Tab>
              <Tabs.Tab value="isotropic">Metals and plastics</Tabs.Tab>
            </Tabs.List>
          </Tabs>
          <ScrollArea h={420}>
            <Table className="compact" stickyHeader>
              <Table.Thead>
                {tab === "isotropic" ? (
                  <Table.Tr>
                    <Table.Th>Material</Table.Th>
                    <Table.Th>E (GPa)</Table.Th>
                    <Table.Th>Yield (MPa)</Table.Th>
                    <Table.Th>Ultimate (MPa)</Table.Th>
                    <Table.Th>kg/m³</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                ) : (
                  <Table.Tr>
                    <Table.Th>Ply</Table.Th>
                    <Table.Th>E1 / E2 (GPa)</Table.Th>
                    <Table.Th>Xt / Xc (MPa)</Table.Th>
                    <Table.Th>Ply (mm)</Table.Th>
                    <Table.Th>kg/m³</Table.Th>
                    <Table.Th />
                  </Table.Tr>
                )}
              </Table.Thead>
              <Table.Tbody>{rows(tab === "isotropic" ? "isotropic" : "lamina")}</Table.Tbody>
            </Table>
          </ScrollArea>
        </Stack>
      )}
    </Modal>
  );
}
