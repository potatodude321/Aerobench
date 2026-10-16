/**
 * Add bodies to the imported geometry: more files from the same CAD model (same units, axes and
 * origin, so they land where they were modelled), either as parts of the vehicle or element
 * (walls with their own forces) or as refinement zones you modelled yourself (closed volumes the
 * mesh is refined inside).
 */
import { Button, FileButton, Group, Modal, NumberInput, SegmentedControl, Select, Stack, Text, TextInput } from "@mantine/core";
import { IconFilePlus, IconUpload } from "@tabler/icons-react";
import { useState } from "react";
import { bridge, isDesktop } from "../api/bridge";
import { api } from "../api/client";
import { pid } from "../state/store";
import { notifyError, notifyOk } from "./notify";
import { Hint } from "./ui";

const ROLES = [
  { value: "other", label: "Part" },
  { value: "body", label: "Bodywork" },
  { value: "wing", label: "Wing" },
  { value: "spoiler", label: "Spoiler" },
  { value: "splitter", label: "Splitter" },
  { value: "diffuser", label: "Diffuser" },
  { value: "floor", label: "Floor" },
  { value: "mirror", label: "Mirror" },
  { value: "cooling_inlet", label: "Cooling inlet" },
  { value: "cooling_outlet", label: "Cooling outlet" },
];
const UNITS = ["mm", "cm", "m", "in", "ft"];
const EXT = ["stl", "obj", "ply", "step", "stp", "iges", "igs"];

interface Picked {
  name: string;
  path?: string;
  file?: File;
}

export function AddBodies({ unit, onAdded }: { unit: string; onAdded: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<Picked[]>([]);
  const [kind, setKind] = useState<"parts" | "refinement">("parts");
  const [role, setRole] = useState("other");
  const [units, setUnits] = useState("same");
  const [level, setLevel] = useState<number | "">("");
  const [busy, setBusy] = useState(false);

  const stem = (n: string) => n.replace(/^.*[\\/]/, "").replace(/\.[^.]+$/, "");
  const pick = async () => {
    if (bridge) {
      const paths = await bridge.openFiles({ title: "Add bodies from the same model", filters: [{ name: "Geometry", extensions: EXT }] });
      if (!paths.length) return;
      setFiles(paths.map((p) => ({ name: stem(p), path: p })));
      setOpen(true);
    }
  };
  const picked = (fs: File[]) => {
    if (!fs.length) return;
    setFiles(fs.map((f) => ({ name: stem(f.name), file: f })));
    setOpen(true);
  };

  const add = async () => {
    setBusy(true);
    try {
      let r: any;
      if (files.every((f) => f.path)) {
        r = await api.post(`/api/p/${pid()}/geometry/bodies`, {
          paths: files.map((f) => f.path), kind, units, role, level: level === "" ? null : level, names: files.map((f) => f.name),
        });
      } else {
        r = await api.uploadMany(`/api/p/${pid()}/geometry/bodies/upload`, files.map((f) => f.file!),
          { kind, units, role, ...(level === "" ? {} : { level: String(level) }) });
      }
      const n = kind === "parts" ? (r.components || []).length : (r.regions || []).length;
      notifyOk(kind === "parts" ? `${n} part${n === 1 ? "" : "s"} added; set their roles below if needed.`
        : `${n} refinement zone${n === 1 ? "" : "s"} added; see them on the Mesh page.`, "Bodies added");
      setOpen(false);
      setFiles([]);
      await onAdded();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {isDesktop ? (
        <Button size="xs" variant="default" leftSection={<IconFilePlus size={14} />} onClick={pick} fullWidth>
          Add bodies from the same model…
        </Button>
      ) : (
        <FileButton onChange={picked} accept={EXT.map((e) => `.${e}`).join(",")} multiple>
          {(props) => (
            <Button {...props} size="xs" variant="default" leftSection={<IconUpload size={14} />} fullWidth>
              Add bodies from the same model…
            </Button>
          )}
        </FileButton>
      )}
      <Modal opened={open} onClose={() => setOpen(false)} title="Add bodies" size="lg">
        <Stack gap={10}>
          <Hint>
            Bodies exported from the same CAD model keep their position relative to what is already imported: they are read with the same units, axes and origin.
          </Hint>
          <SegmentedControl fullWidth value={kind} onChange={(v) => setKind(v as any)}
            data={[{ value: "parts", label: "Parts (walls with forces)" }, { value: "refinement", label: "Refinement zones (closed volumes)" }]} />
          <Text size="xs" c="dimmed">
            {kind === "parts"
              ? "Each file becomes a part of the model (a file with several named solids gives one part each). The flow sees it as a wall and its forces are reported."
              : "Each file must be a closed volume you modelled around a region of interest. The flow does not see it; the mesh is refined everywhere inside it."}
          </Text>
          <Stack gap={4}>
            {files.map((f, i) => (
              <TextInput key={i} size="xs" label={i === 0 ? "Names" : undefined} value={f.name}
                onChange={(e) => { const v = e.currentTarget.value; setFiles((fs) => fs.map((x, k) => (k === i ? { ...x, name: v } : x))); }} />
            ))}
          </Stack>
          <Group grow align="flex-end">
            {kind === "parts" ? (
              <Select size="xs" label="What they are" data={ROLES} value={role} onChange={(v) => v && setRole(v)} allowDeselect={false} />
            ) : (
              <NumberInput size="xs" label="Refinement level" description="Empty: like the region around the vehicle" min={0} max={12} value={level}
                onChange={(v) => setLevel(v === "" ? "" : Number(v))} />
            )}
            <Select size="xs" label="Units in the files" allowDeselect={false} value={units} onChange={(v) => v && setUnits(v)}
              data={[{ value: "same", label: `Same as the geometry (${unit})` }, ...UNITS.filter((u) => u !== unit).map((u) => ({ value: u, label: u }))]} />
          </Group>
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={add} loading={busy} disabled={!files.length || files.some((f) => !f.name.trim())}>
              Add {files.length} {kind === "parts" ? (files.length === 1 ? "part" : "parts") : files.length === 1 ? "zone" : "zones"}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </>
  );
}
