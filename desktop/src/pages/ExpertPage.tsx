import { Alert, Badge, Button, Code, Group, NavLink, Paper, ScrollArea, Select, SegmentedControl, Stack, Table, Tabs, Text, Textarea, TextInput } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { notifyError, notifyOk } from "../components/notify";
import { KeyValue, SectionTitle } from "../components/ui";
import { pid, useApp } from "../state/store";

export function ExpertPage() {
  const { project, runs, environment } = useApp();
  const [tab, setTab] = useState<string | null>("generated");
  return (
    <div style={{ height: "100%", padding: 10, boxSizing: "border-box" }}>
      <Paper withBorder radius="md" p="sm" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <Tabs value={tab} onChange={setTab} style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <Tabs.List>
            <Tabs.Tab value="generated">Generated case (current setup)</Tabs.Tab>
            <Tabs.Tab value="browser">Case browser</Tabs.Tab>
            <Tabs.Tab value="overrides">Overrides</Tabs.Tab>
            <Tabs.Tab value="diff">Diff</Tabs.Tab>
            <Tabs.Tab value="commands">Commands</Tabs.Tab>
            <Tabs.Tab value="versions">Versions</Tabs.Tab>
          </Tabs.List>
          <div style={{ flex: 1, minHeight: 0, paddingTop: 8 }}>
            {tab === "generated" && <GeneratedPreview />}
            {tab === "browser" && <CaseBrowser runs={runs} />}
            {tab === "overrides" && <Overrides />}
            {tab === "diff" && <DiffView runs={runs} />}
            {tab === "commands" && <Commands runs={runs} />}
            {tab === "versions" && (
              <KeyValue
                rows={[
                  ["Project schema", String(project!.config.schema_version)],
                  ["Saved by app version", project!.config.app_version],
                  ["Solver target", `${project!.config.solver.fork} ${project!.config.solver.version}`],
                  ...((environment?.providers || []).map((p: any) => [`Provider ${p.id}`, `${p.fork || "?"} ${p.version || ""} ${p.project_dir || ""}`]) as [string, string][]),
                  ["ParaView", `${environment?.paraview?.version || "—"} ${environment?.paraview?.pvpython || ""}`],
                  ["Numerics preset", `${project!.config.numerics.preset} v${project!.config.numerics.preset_version}`],
                  ["Mesh preset", `${project!.config.mesh.preset} v${project!.config.mesh.preset_version}`],
                ]}
              />
            )}
          </div>
        </Tabs>
      </Paper>
    </div>
  );
}

function FileTreeView({ files, sel, onSel }: { files: string[]; sel: string | null; onSel: (f: string) => void }) {
  return (
    <ScrollArea h="100%">
      {files.map((f) => (
        <NavLink key={f} label={<span className="mono">{f}</span>} active={sel === f} onClick={() => onSel(f)} py={2} />
      ))}
    </ScrollArea>
  );
}

function GeneratedPreview() {
  const [stage, setStage] = useState("solve");
  const [data, setData] = useState<any>(null);
  const [sel, setSel] = useState<string | null>(null);
  useEffect(() => {
    api.get(`/api/p/${pid()}/expert/preview/${stage}`).then((d) => {
      setData(d);
      setSel(Object.keys(d.files)[0]);
    }).catch(notifyError);
  }, [stage]);
  return (
    <div style={{ display: "flex", gap: 8, height: "100%" }}>
      <Stack style={{ width: 300 }} gap={6}>
        <SegmentedControl size="xs" value={stage} onChange={setStage} data={[{ value: "mesh", label: "Mesh case" }, { value: "solve", label: "Solve case" }]} />
        {data && <FileTreeView files={Object.keys(data.files)} sel={sel} onSel={setSel} />}
      </Stack>
      <Stack style={{ flex: 1, minWidth: 0 }} gap={6}>
        {data?.overrides && (data.overrides.applied.length > 0 || data.overrides.conflicts.length > 0) && (
          <Alert variant="light" color={data.overrides.conflicts.length ? "yellow" : "blue"} p={6}>
            <Text size="xs">
              Overrides applied: {data.overrides.applied.join(", ") || "none"}. {data.overrides.conflicts.length > 0 && `Conflicts: ${data.overrides.conflicts.join("; ")}`}
            </Text>
          </Alert>
        )}
        <pre className="log-view" style={{ flex: 1, margin: 0 }}>
          {sel && data ? data.files[sel] : ""}
        </pre>
      </Stack>
    </div>
  );
}

function CaseBrowser({ runs }: { runs: any[] }) {
  const [runId, setRunId] = useState<string | null>(runs[0]?.id || null);
  const [tree, setTree] = useState<any>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [file, setFile] = useState<any>(null);
  useEffect(() => {
    if (!runId) return;
    api.get(`/api/p/${pid()}/expert/tree/run/${runId}`).then(setTree).catch(notifyError);
  }, [runId]);
  useEffect(() => {
    if (!sel || !runId) return;
    api.get(`/api/p/${pid()}/expert/file/run/${runId}?path=${encodeURIComponent(sel)}`).then(setFile).catch(notifyError);
  }, [sel, runId]);
  return (
    <div style={{ display: "flex", gap: 8, height: "100%" }}>
      <Stack style={{ width: 320 }} gap={6}>
        <Select size="xs" data={runs.map((r) => ({ value: r.id, label: `${r.label} (${r.id})` }))} value={runId} onChange={setRunId} />
        {tree && (
          <>
            <Text size="xs" c="dimmed" className="mono">
              {tree.case}
            </Text>
            <Text size="xs" c="dimmed">
              Time directories: {tree.times.join(", ") || "none"} {tree.processors.length ? `· ${tree.processors.length} processor dirs` : ""}
            </Text>
            <FileTreeView files={tree.files.map((f: any) => f.path)} sel={sel} onSel={setSel} />
          </>
        )}
      </Stack>
      <pre className="log-view" style={{ flex: 1, margin: 0 }}>
        {file?.too_large ? `(file too large to display: ${file.size} bytes)` : file?.text || ""}
      </pre>
    </div>
  );
}

function Overrides() {
  const { project, patchConfig } = useApp();
  const ex = project!.config.expert;
  const [file, setFile] = useState("system/fvSolution");
  const [key, setKey] = useState("SIMPLE/nNonOrthogonalCorrectors");
  const [value, setValue] = useState("1");
  const [stage, setStage] = useState("solve");
  const [foName, setFoName] = useState("");
  const [foBody, setFoBody] = useState("type surfaces;\nlibs (sampling);\n");
  const add = async () => {
    let v: any = value;
    try {
      v = JSON.parse(value);
    } catch {
      /* treat as word/string */
    }
    if (await patchConfig({ expert: { patches: [...ex.patches, { file, key_path: key, value: v, op: "set", stage }] } })) notifyOk("Override saved — it is re-applied every time the case is generated.");
  };
  return (
    <ScrollArea h="100%">
      <Text size="sm" mb="xs">
        Overrides are structured patches applied after generation. They survive regeneration; if a key no longer exists the conflict is reported rather than silently dropped.
      </Text>
      <Table className="compact">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Stage</Table.Th>
            <Table.Th>File</Table.Th>
            <Table.Th>Key path</Table.Th>
            <Table.Th>Operation</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {ex.patches.map((p: any, i: number) => (
            <Table.Tr key={i}>
              <Table.Td>{p.stage}</Table.Td>
              <Table.Td className="mono">{p.file}</Table.Td>
              <Table.Td className="mono">{p.key_path}</Table.Td>
              <Table.Td>
                <Code>{p.op === "delete" ? "delete" : JSON.stringify(p.value)}</Code>
              </Table.Td>
              <Table.Td>
                <Button size="compact-xs" color="red" variant="subtle" onClick={() => patchConfig({ expert: { patches: ex.patches.filter((_: any, k: number) => k !== i) } })}>
                  remove
                </Button>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <SectionTitle>Add dictionary override</SectionTitle>
      <Group align="flex-end" gap="xs">
        <Select size="xs" w={90} label="Stage" data={["solve", "mesh"]} value={stage} onChange={(v) => v && setStage(v)} />
        <TextInput size="xs" label="File" value={file} onChange={(e) => setFile(e.currentTarget.value)} />
        <TextInput size="xs" label="Key path" value={key} onChange={(e) => setKey(e.currentTarget.value)} w={280} />
        <TextInput size="xs" label="Value (JSON or word)" value={value} onChange={(e) => setValue(e.currentTarget.value)} />
        <Button size="xs" onClick={add}>
          Add
        </Button>
      </Group>
      <SectionTitle>Custom function object</SectionTitle>
      <Group align="flex-end" gap="xs">
        <TextInput size="xs" label="Name" value={foName} onChange={(e) => setFoName(e.currentTarget.value)} />
        <Button size="xs" disabled={!foName}
          onClick={() => patchConfig({ expert: { extra_function_objects: { ...ex.extra_function_objects, [foName]: foBody } } }).then((ok) => ok && notifyOk(`Function object ${foName} added`))}>
          Save function object
        </Button>
      </Group>
      <Textarea mt={4} autosize minRows={5} className="mono" value={foBody} onChange={(e) => setFoBody(e.currentTarget.value)} />
      {Object.keys(ex.extra_function_objects).length > 0 && (
        <Group gap={4} mt={4}>
          {Object.keys(ex.extra_function_objects).map((k) => (
            <Badge key={k} variant="light">
              {k}
            </Badge>
          ))}
        </Group>
      )}
    </ScrollArea>
  );
}

function DiffView({ runs }: { runs: any[] }) {
  const [a, setA] = useState<string | null>(runs[1]?.id || null);
  const [b, setB] = useState<string | null>(runs[0]?.id || null);
  const [file, setFile] = useState("system/fvSchemes");
  const [text, setText] = useState("");
  const [cur, setCur] = useState<Record<string, string> | null>(null);
  return (
    <Stack h="100%" gap={6}>
      <Group align="flex-end" gap="xs">
        <Select size="xs" label="Run A" data={runs.map((r) => ({ value: r.id, label: r.label }))} value={a} onChange={setA} />
        <Select size="xs" label="Run B" data={runs.map((r) => ({ value: r.id, label: r.label }))} value={b} onChange={setB} />
        <TextInput size="xs" label="File" value={file} onChange={(e) => setFile(e.currentTarget.value)} />
        <Button size="xs" disabled={!a || !b} onClick={() => api.get(`/api/p/${pid()}/expert/diff?run_a=${a}&run_b=${b}&path=${encodeURIComponent(file)}`).then(setText).catch(notifyError)}>
          Diff runs
        </Button>
        <Button size="xs" variant="light" disabled={!b} onClick={() => api.get(`/api/p/${pid()}/expert/diff-current/${b}`).then(setCur).catch(notifyError)}>
          Run B vs current setup
        </Button>
      </Group>
      <pre className="log-view" style={{ flex: 1, margin: 0 }}>
        {cur ? Object.values(cur).join("\n") || "(identical)" : text}
      </pre>
    </Stack>
  );
}

function Commands({ runs }: { runs: any[] }) {
  const [runId, setRunId] = useState<string | null>(runs[0]?.id || null);
  const [cmds, setCmds] = useState<any[]>([]);
  useEffect(() => {
    const r = runs.find((x) => x.id === runId);
    if (!r?.job_id) return;
    api.get(`/api/p/${pid()}/jobs/${r.job_id}/commands`).then(setCmds).catch(notifyError);
  }, [runId]);
  return (
    <Stack h="100%" gap={6}>
      <Select size="xs" w={400} data={runs.map((r) => ({ value: r.id, label: `${r.label} (${r.id})` }))} value={runId} onChange={setRunId} />
      <pre className="log-view" style={{ flex: 1, margin: 0 }}>
        {cmds.map((c) => `[${c.t}] (${c.step})  cwd=${c.cwd}\n$ ${c.argv.join(" ")}`).join("\n\n")}
      </pre>
    </Stack>
  );
}
