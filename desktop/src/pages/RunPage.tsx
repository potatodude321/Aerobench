import { ActionIcon, Alert, Badge, Button, Group, Menu, NumberInput, Paper, Progress, Select, SimpleGrid, Stack, Table, Tabs, Text, TextInput, Tooltip } from "@mantine/core";
import { IconDots, IconPlayerPlay, IconPlayerStop, IconRefresh, IconRepeat } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { api, events } from "../api/client";
import { CoefficientChart, ResidualChart } from "../charts/RunCharts";
import { fmt } from "../components/ColorLegend";
import { notifyError, notifyOk } from "../components/notify";
import { DiagnosticList, KeyValue, PageLayout, SectionTitle, StatusBadge, ValidationList } from "../components/ui";
import { pid, useApp } from "../state/store";

function duration(s: number | null | undefined): string {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
}

export function RunPage() {
  const { project, runs, refreshRuns, jobs, environment, patchConfig, selectRun, setSection } = useApp();
  const cfg = project!.config;
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const errors = project!.validation.filter((v) => v.severity === "error");
  const providers = (environment?.providers || []).map((p: any) => ({ value: p.id, label: `${p.label}${p.available ? "" : " (unavailable)"}` }));
  const maxCores = environment?.max_cores || 1;

  const active = runs.filter((r) => ["queued", "running", "starting", "stopping"].includes(r.status));
  useEffect(() => {
    if (!focus && active.length) setFocus(active[0].id);
    if (!focus && !active.length && runs.length) setFocus(runs[0].id);
  }, [runs.length, active.length]);

  const start = async () => {
    setBusy(true);
    try {
      const r = await api.post(`/api/p/${pid()}/runs`, { label: label || undefined });
      notifyOk(r.mesh_job_id ? "Meshing first, then solving." : "Run queued.", `Run ${r.run_id}`);
      setFocus(r.run_id);
      setLabel("");
      await refreshRuns();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const side = (
    <Stack gap="xs">
      <SectionTitle>Execution</SectionTitle>
      <Select size="xs" label="Solver environment" data={providers} value={cfg.execution.provider_id || environment?.default_provider || null}
        onChange={(v) => patchConfig({ execution: { provider_id: v } })} />
      <Group grow>
        <NumberInput size="xs" label="Cores" min={1} max={maxCores} value={cfg.execution.n_procs}
          onChange={(v) => typeof v === "number" && patchConfig({ execution: { n_procs: Math.max(1, Math.min(maxCores, v)) } })}
          description={`${maxCores} available for runs`} />
        <Select size="xs" label="Write format" data={["binary", "ascii"]} value={cfg.execution.write_format}
          onChange={(v) => v && patchConfig({ execution: { write_format: v } })} />
      </Group>
      <SectionTitle>Start</SectionTitle>
      <TextInput size="xs" label="Run label (optional)" value={label} onChange={(e) => setLabel(e.currentTarget.value)} placeholder={`U=${cfg.operating.speed} m/s, yaw=${cfg.operating.yaw_deg}°`} />
      <ValidationList issues={project!.validation} filter={(i) => i.severity === "error"} />
      <Button leftSection={<IconPlayerPlay size={16} />} onClick={start} loading={busy} disabled={errors.length > 0}>
        Run simulation
      </Button>
      <Text size="xs" c="dimmed">
        The case is generated from the current setup, meshed if no matching mesh exists, then solved by a background process. Closing the
        application does not stop it.
      </Text>
      {project!.staleness.mesh.stale && (
        <Alert color="blue" variant="light" p={6}>
          <Text size="xs">A new mesh will be generated first (setup changed since the last mesh).</Text>
        </Alert>
      )}

      <SectionTitle right={<ActionIcon size="sm" variant="subtle" onClick={refreshRuns}><IconRefresh size={14} /></ActionIcon>}>Runs</SectionTitle>
      <Table className="compact" highlightOnHover>
        <Table.Tbody>
          {runs.map((r) => (
            <Table.Tr key={r.id} onClick={() => setFocus(r.id)} style={{ cursor: "pointer", background: focus === r.id ? "rgba(34,184,207,0.08)" : undefined }}>
              <Table.Td>
                <Text size="xs" fw={600} lineClamp={1}>
                  {r.label}
                </Text>
                <Text size="xs" c="dimmed" className="mono">
                  {r.id}
                </Text>
              </Table.Td>
              <Table.Td>
                <StatusBadge status={r.status} size="xs" />
              </Table.Td>
              <Table.Td>
                <Text size="xs">{r.metrics ? `Cd ${fmt(r.metrics.Cd, 3)}` : ""}</Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );

  return (
    <PageLayout side={side} sideWidth={380}>
      {focus ? (
        <RunMonitor runId={focus} jobs={jobs} onResults={() => { selectRun(focus); setSection("results"); }} />
      ) : (
        <Paper withBorder p="xl" style={{ flex: 1 }}>
          <Text c="dimmed">No runs yet. Configure the setup and press “Run simulation”.</Text>
        </Paper>
      )}
    </PageLayout>
  );
}

function RunMonitor({ runId, jobs, onResults }: { runId: string; jobs: any; onResults: () => void }) {
  const { refreshRuns } = useApp();
  const [run, setRun] = useState<any>(null);
  const [series, setSeries] = useState<any>(null);
  const [log, setLog] = useState("");
  const [tab, setTab] = useState<string | null>("monitor");
  const logRef = useRef<HTMLPreElement>(null);
  const live = run && ["queued", "running", "starting", "stopping"].includes(run.status);

  const load = async () => {
    try {
      const r = await api.get(`/api/p/${pid()}/runs/${runId}`);
      setRun(r);
      setSeries(await api.get(`/api/p/${pid()}/runs/${runId}/series?max_points=3000`));
    } catch (e) {
      notifyError(e);
    }
  };

  useEffect(() => {
    setLog("");
    load();
  }, [runId]);

  // poll while live
  useEffect(() => {
    if (!live) return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [live, runId]);

  // reload when job status changes
  const jobStatus = run?.job_id && jobs.states[run.job_id]?.status;
  useEffect(() => {
    if (run) load();
  }, [jobStatus, jobs.recent.map((j: any) => j.status).join()]);

  // live log via WebSocket
  useEffect(() => {
    if (!run?.job_id) return;
    const jid = run.job_id;
    events.subscribeLog(jid);
    const off = events.on((m) => {
      if (m.type === "log" && m.job_id === jid) setLog((l) => (l + m.text).slice(-200000));
      if (m.type === "log_step" && m.job_id === jid) setLog((l) => l + `\n──────── step ${m.step} ────────\n`);
    });
    return () => {
      off();
      events.unsubscribeLog(jid);
    };
  }, [run?.job_id]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  if (!run) return null;
  const st = run.job;
  const steps = st?.steps || [];
  const doneSteps = steps.filter((s: any) => ["completed", "skipped"].includes(s.status)).length;
  const lastIter = series?.forces?.time?.at(-1);
  const pct = run.status === "completed" ? 100 : lastIter ? Math.min(99, (100 * lastIter) / (run.metrics?.iterations || run.iterations || 1)) : (100 * doneSteps) / Math.max(1, steps.length);

  const stop = async (force: boolean) => {
    try {
      await api.post(`/api/p/${pid()}/runs/${runId}/stop`, { force });
      notifyOk(force ? "Killing the solver." : "Stop requested — the solver will write the current state and exit.");
      load();
    } catch (e) {
      notifyError(e);
    }
  };
  const resume = async () => {
    try {
      await api.post(`/api/p/${pid()}/runs/${runId}/resume`, { extra_iterations: 500 });
      notifyOk("Resuming from the latest saved iteration (+500).");
      refreshRuns();
      load();
    } catch (e) {
      notifyError(e);
    }
  };
  const reingest = async () => {
    try {
      const r = await api.post(`/api/p/${pid()}/runs/${runId}/reingest`);
      notifyOk(`Results read again from the case files — run is ${r.status}.`);
      refreshRuns();
      load();
    } catch (e) {
      notifyError(e);
    }
  };
  const reproduce = async () => {
    try {
      const r = await api.post(`/api/p/${pid()}/runs/${runId}/reproduce`);
      notifyOk(`New run ${r.run_id} with the identical frozen configuration.`);
      refreshRuns();
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <>
      <Paper withBorder radius="md" p="sm">
        <Group justify="space-between">
          <Group gap="xs">
            <Text fw={700}>{run.label}</Text>
            <StatusBadge status={run.status} />
            {run.convergence && <StatusBadge status={run.convergence.status} />}
          </Group>
          <Group gap="xs">
            {live && (
              <>
                <Button size="xs" color="orange" variant="light" leftSection={<IconPlayerStop size={14} />} onClick={() => stop(false)}>
                  Stop & save
                </Button>
                <Tooltip label="Kill immediately without writing">
                  <Button size="xs" color="red" variant="subtle" onClick={() => stop(true)}>
                    Kill
                  </Button>
                </Tooltip>
              </>
            )}
            {!live && run.status !== "completed" && (
              <Button size="xs" variant="light" leftSection={<IconPlayerPlay size={14} />} onClick={resume}>
                Resume
              </Button>
            )}
            {run.status === "completed" && (
              <Button size="xs" onClick={onResults}>
                Open results
              </Button>
            )}
            <Menu position="bottom-end">
              <Menu.Target>
                <ActionIcon variant="subtle">
                  <IconDots size={16} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconRepeat size={14} />} onClick={reproduce}>
                  Reproduce run
                </Menu.Item>
                {run.status === "completed" && (
                  <Menu.Item leftSection={<IconPlayerPlay size={14} />} onClick={resume}>
                    Continue +500 iterations
                  </Menu.Item>
                )}
                {!live && (
                  <Menu.Item leftSection={<IconRepeat size={14} />} onClick={reingest}>
                    Re-read results from the case
                  </Menu.Item>
                )}
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
        <Progress value={pct} mt="xs" animated={live} />
        <Group gap={6} mt={6} wrap="wrap">
          {steps.map((s: any, i: number) => (
            <Badge key={i} size="xs" variant={s.status === "running" ? "filled" : "light"}
              color={s.status === "completed" ? "teal" : s.status === "running" ? "cyan" : s.status === "failed" ? "red" : s.status === "skipped" ? "yellow" : "gray"}>
              {s.name}
            </Badge>
          ))}
        </Group>
      </Paper>
      <Paper withBorder radius="md" p="sm" style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <Tabs value={tab} onChange={setTab} style={{ flex: 1, display: "flex", flexDirection: "column" }}>
          <Tabs.List>
            <Tabs.Tab value="monitor">Convergence</Tabs.Tab>
            <Tabs.Tab value="log">Solver log</Tabs.Tab>
            <Tabs.Tab value="details">Details</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="monitor" pt="xs" style={{ flex: 1, overflow: "auto" }}>
            <SimpleGrid cols={{ base: 1, xl: 2 }}>
              <div>
                <Text size="xs" fw={700}>
                  Residuals
                </Text>
                <ResidualChart series={series} height={300} />
              </div>
              <div>
                <Text size="xs" fw={700}>
                  Force coefficients
                </Text>
                <CoefficientChart series={series} keys={["Cd", "Cl", "Cl_front", "Cl_rear"]} window={run.metrics?.averaging_window} height={300} />
              </div>
            </SimpleGrid>
            {run.convergence && (
              <Paper withBorder p="xs" mt="xs">
                <Text size="sm" fw={600}>
                  {run.convergence.summary}
                </Text>
                <Table className="compact" mt={4}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Criterion</Table.Th>
                      <Table.Th>Value</Table.Th>
                      <Table.Th>Threshold</Table.Th>
                      <Table.Th />
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {run.convergence.criteria.map((c: any, i: number) => (
                      <Table.Tr key={i}>
                        <Table.Td>{c.name}</Table.Td>
                        <Table.Td>{fmt(c.value, 4)}</Table.Td>
                        <Table.Td>
                          {fmt(c.threshold, 4)} {c.note}
                        </Table.Td>
                        <Table.Td>{c.passed === null ? "" : c.passed ? <Badge size="xs" color="teal">pass</Badge> : <Badge size="xs" color="yellow">open</Badge>}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Paper>
            )}
            <DiagnosticList items={run.diagnostics} />
          </Tabs.Panel>
          <Tabs.Panel value="log" pt="xs" style={{ flex: 1, minHeight: 0 }}>
            <pre ref={logRef} className="log-view" style={{ height: "100%", maxHeight: "60vh", margin: 0 }}>
              {log || "Waiting for output…"}
            </pre>
          </Tabs.Panel>
          <Tabs.Panel value="details" pt="xs">
            <KeyValue
              rows={[
                ["Run id", run.id],
                ["Mesh", run.mesh_revision_id],
                ["Geometry revision", run.geometry_revision_id],
                ["Solver", `${run.foam_fork} ${run.foam_version} via ${run.provider}`],
                ["Cores", run.n_procs],
                ["Iterations", run.iterations ?? "—"],
                ["Wall time", duration(run.wall_seconds)],
                ["Configuration hash", <span className="mono">{run.config_hash?.slice(0, 16)}</span>],
                ["Started", run.started_at || "—"],
                ["Ended", run.ended_at || "—"],
              ]}
            />
          </Tabs.Panel>
        </Tabs>
      </Paper>
    </>
  );
}
