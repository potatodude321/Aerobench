import { ActionIcon, Button, Group, Menu, NumberInput, Progress, Select, SimpleGrid, Stack, Table, Tabs, Text, TextInput, Tooltip } from "@mantine/core";
import { IconDots, IconPlayerPlay, IconPlayerStop, IconRefresh, IconRepeat } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { api, events } from "../api/client";
import { CoefficientChart, ResidualChart } from "../charts/RunCharts";
import { fmt } from "../components/ColorLegend";
import { notifyError, notifyOk } from "../components/notify";
import { DiagnosticList, EmptyState, Hint, KeyValue, PageLayout, SectionTitle, StatusBadge, StatusDot, ValidationList } from "../components/ui";
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
    <>
      <SectionTitle first>Where it runs</SectionTitle>
      <Select size="xs" label="Solver" data={providers} value={cfg.execution.provider_id || environment?.default_provider || null}
        onChange={(v) => patchConfig({ execution: { provider_id: v } })} />
      <SimpleGrid cols={2} spacing="xs" mt={8}>
        <NumberInput size="xs" label="Cores" min={1} max={maxCores} value={cfg.execution.n_procs}
          onChange={(v) => typeof v === "number" && patchConfig({ execution: { n_procs: Math.max(1, Math.min(maxCores, v)) } })}
          description={`${maxCores} available`} />
        <Select size="xs" label="Result files" data={[{ value: "binary", label: "Binary (smaller)" }, { value: "ascii", label: "Text" }]} value={cfg.execution.write_format}
          onChange={(v) => v && patchConfig({ execution: { write_format: v } })} />
      </SimpleGrid>

      <SectionTitle>New run</SectionTitle>
      <TextInput size="xs" label="Name (optional)" value={label} onChange={(e) => setLabel(e.currentTarget.value)} placeholder={`U=${cfg.operating.speed} m/s, yaw=${cfg.operating.yaw_deg}°`} />
      <ValidationList issues={project!.validation} filter={(i) => i.severity === "error"} />
      <Hint style={{ marginTop: 8 }}>
        {project!.staleness.mesh.stale || !project!.staleness.mesh.current ? "The vehicle is meshed first, then solved. " : "The existing mesh is reused. "}
        The run continues in the background if AeroBench is closed.
      </Hint>

      <SectionTitle right={<ActionIcon size="sm" variant="subtle" color="gray" onClick={refreshRuns} aria-label="Refresh"><IconRefresh size={14} /></ActionIcon>}>Runs</SectionTitle>
      {runs.length === 0 && <Hint>No runs yet.</Hint>}
      <Stack gap={0}>
        {runs.map((r) => (
          <button key={r.id} className="ab-step" aria-current={focus === r.id ? "page" : undefined} onClick={() => setFocus(r.id)}
            style={{ height: "auto", padding: "7px 10px", gridTemplateColumns: "1fr auto", fontSize: 13 }}>
            <span style={{ minWidth: 0 }}>
              <span style={{ display: "block", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", color: "var(--ab-text)" }}>{r.label}</span>
              <StatusBadge status={r.status} />
            </span>
            <span className="num" style={{ fontSize: 14, color: "var(--ab-text-2)" }}>{r.metrics ? `Cd ${fmt(r.metrics.Cd, 3)}` : ""}</span>
          </button>
        ))}
      </Stack>
    </>
  );

  return (
    <PageLayout
      side={side}
      sideWidth={380}
      title="Run"
      lede="Solve the flow with the current setup and follow it as it converges."
      footer={
        <Button fullWidth leftSection={<IconPlayerPlay size={16} />} onClick={start} loading={busy} disabled={errors.length > 0}>
          Run simulation
        </Button>
      }
    >
      {focus ? (
        <RunMonitor runId={focus} jobs={jobs} onResults={() => { selectRun(focus); setSection("results"); }} />
      ) : (
        <EmptyState title="No runs yet">Check the setup, then press “Run simulation”.</EmptyState>
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

  const stepWord = (n: string) => n.replace(/_/g, " ");
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={{ padding: "16px 22px 12px", borderBottom: "1px solid var(--ab-line)", background: "var(--ab-surface)" }}>
        <Group justify="space-between" wrap="nowrap">
          <Group gap={16} wrap="nowrap" style={{ minWidth: 0 }}>
            <h2 className="ab-page-title" style={{ fontSize: 19, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {run.label}
            </h2>
            <StatusBadge status={run.status} />
            {run.convergence && <StatusBadge status={run.convergence.status} />}
          </Group>
          <Group gap={6} wrap="nowrap">
            {live && (
              <>
                <Button size="xs" variant="default" leftSection={<IconPlayerStop size={14} />} onClick={() => stop(false)}>
                  Stop and save
                </Button>
                <Tooltip label="End the solver at once, without saving the current state">
                  <Button size="xs" color="red" variant="subtle" onClick={() => stop(true)}>
                    Kill
                  </Button>
                </Tooltip>
              </>
            )}
            {!live && run.status !== "completed" && (
              <Button size="xs" variant="default" leftSection={<IconPlayerPlay size={14} />} onClick={resume}>
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
                <ActionIcon variant="subtle" color="gray" aria-label="More">
                  <IconDots size={16} />
                </ActionIcon>
              </Menu.Target>
              <Menu.Dropdown>
                <Menu.Item leftSection={<IconRepeat size={14} />} onClick={reproduce}>
                  Run again with identical settings
                </Menu.Item>
                {run.status === "completed" && (
                  <Menu.Item leftSection={<IconPlayerPlay size={14} />} onClick={resume}>
                    Continue for 500 more iterations
                  </Menu.Item>
                )}
                {!live && (
                  <Menu.Item leftSection={<IconRepeat size={14} />} onClick={reingest}>
                    Read the results from the case again
                  </Menu.Item>
                )}
              </Menu.Dropdown>
            </Menu>
          </Group>
        </Group>
        <Progress value={pct} mt={12} size="xs" animated={live} color={run.status === "failed" ? "red" : undefined} />
        <div style={{ display: "flex", flexWrap: "wrap", columnGap: 14, rowGap: 4, marginTop: 8 }}>
          {steps.map((s: any, i: number) => (
            <StatusDot key={i}
              tone={s.status === "completed" ? "ok" : s.status === "running" ? "live" : s.status === "failed" ? "err" : s.status === "skipped" ? "warn" : "idle"}
              label={<span style={{ color: s.status === "running" ? "var(--ab-text)" : undefined }}>{stepWord(s.name)}</span>} />
          ))}
        </div>
      </div>
      <Tabs value={tab} onChange={setTab} style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <Tabs.List px={14}>
          <Tabs.Tab value="monitor">Convergence</Tabs.Tab>
          <Tabs.Tab value="log">Solver output</Tabs.Tab>
          <Tabs.Tab value="details">Details</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="monitor" style={{ flex: 1, overflow: "auto", padding: "14px 22px" }}>
          <SimpleGrid cols={{ base: 1, xl: 2 }} spacing="lg">
            <div>
              <Text size="sm" fw={600}>
                Residuals
              </Text>
              <ResidualChart series={series} height={300} />
            </div>
            <div>
              <Text size="sm" fw={600}>
                Force coefficients
              </Text>
              <CoefficientChart series={series} keys={["Cd", "Cl", "Cl_front", "Cl_rear"]} window={run.metrics?.averaging_window} height={300} />
            </div>
          </SimpleGrid>
          {run.convergence && (
            <div style={{ marginTop: 14, maxWidth: 900 }}>
              <SectionTitle>{run.convergence.summary}</SectionTitle>
              <Table className="compact" withRowBorders={false}>
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Criterion</Table.Th>
                    <Table.Th>Value</Table.Th>
                    <Table.Th>Limit</Table.Th>
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
                      <Table.Td>{c.passed === null ? "" : c.passed ? <StatusDot tone="ok" label="Met" /> : <StatusDot tone="warn" label="Not yet" />}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </div>
          )}
          <div style={{ marginTop: 14, maxWidth: 900 }}>
            <DiagnosticList items={run.diagnostics} />
          </div>
        </Tabs.Panel>
        <Tabs.Panel value="log" style={{ flex: 1, minHeight: 0, padding: "12px 22px" }}>
          <pre ref={logRef} className="log-view" style={{ height: "100%", maxHeight: "calc(100vh - 260px)", margin: 0 }}>
            {log || "Waiting for output…"}
          </pre>
        </Tabs.Panel>
        <Tabs.Panel value="details" style={{ padding: "14px 22px", maxWidth: 640 }}>
          <KeyValue
            rows={[
              ["Run", run.id],
              ["Mesh", run.mesh_revision_id],
              ["Geometry version", run.geometry_revision_id],
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
    </div>
  );
}
