import { Badge, Group, Paper, ScrollArea, Table, Text } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { notifyError } from "../components/notify";
import { DiagnosticList, StatusBadge } from "../components/ui";
import { pid, useApp } from "../state/store";

export function LogsPage() {
  const { jobs } = useApp();
  const [list, setList] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [job, setJob] = useState<any>(null);
  const [step, setStep] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [cmds, setCmds] = useState<any[]>([]);

  useEffect(() => {
    api.get(`/api/p/${pid()}/jobs`).then((l) => setList([...l].reverse())).catch(notifyError);
  }, [jobs.recent.map((j: any) => j.status).join()]);

  useEffect(() => {
    if (!sel) return;
    api.get(`/api/p/${pid()}/jobs/${sel}`).then((j) => {
      setJob(j);
      setStep(j.state?.step_index ?? 0);
    }).catch(notifyError);
    api.get(`/api/p/${pid()}/jobs/${sel}/commands`).then(setCmds).catch(() => setCmds([]));
  }, [sel]);

  useEffect(() => {
    if (!sel || step === null) return;
    api.get(`/api/p/${pid()}/jobs/${sel}/log?step=${step}&offset=0`).then((r) => setText(r.text)).catch(notifyError);
  }, [sel, step]);

  return (
    <div style={{ display: "flex", height: "100%", padding: 10, gap: 10, boxSizing: "border-box" }}>
      <Paper withBorder radius="md" style={{ width: 420, overflow: "hidden" }}>
        <ScrollArea h="100%" p="xs">
          <Table className="compact" highlightOnHover>
            <Table.Tbody>
              {list.map((j) => (
                <Table.Tr key={j.id} onClick={() => setSel(j.id)} style={{ cursor: "pointer", background: sel === j.id ? "rgba(34,184,207,0.08)" : undefined }}>
                  <Table.Td>
                    <Text size="xs" fw={600}>
                      {j.kind} → {j.target_id}
                    </Text>
                    <Text size="xs" c="dimmed" className="mono">
                      {j.id}
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    <StatusBadge status={j.status} size="xs" />
                  </Table.Td>
                  <Table.Td>
                    <Badge size="xs" variant="outline" style={{ flexShrink: 0 }} styles={{ label: { overflow: "visible", textOverflow: "clip" } }}>
                      {j.cores} core{j.cores > 1 ? "s" : ""}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </ScrollArea>
      </Paper>
      <Paper withBorder radius="md" p="sm" style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        {job ? (
          <>
            <Group gap={4} mb="xs">
              {job.state?.steps.map((s: any, i: number) => (
                <Badge key={i} size="sm" style={{ cursor: "pointer" }} variant={i === step ? "filled" : "light"}
                  color={s.status === "completed" ? "teal" : s.status === "failed" ? "red" : s.status === "running" ? "cyan" : "gray"} onClick={() => setStep(i)}>
                  {i}. {s.name}
                </Badge>
              ))}
            </Group>
            {step !== null && job.state?.steps[step]?.command && (
              <Text size="xs" className="mono" c="dimmed" mb={4}>
                $ {job.state.steps[step].command.join(" ")}
              </Text>
            )}
            <DiagnosticList items={step !== null ? job.state?.steps[step]?.diagnostics : []} />
            <pre className="log-view" style={{ flex: 1, margin: 0 }}>
              {text || "(no output)"}
            </pre>
            {cmds.length > 0 && (
              <Text size="xs" c="dimmed" mt={4}>
                {cmds.length} command(s) executed — full list in Expert → Commands.
              </Text>
            )}
          </>
        ) : (
          <Text c="dimmed">Select a job to see its steps and raw logs.</Text>
        )}
      </Paper>
    </div>
  );
}
