import { Button, Group, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { IconPlayerStop } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { formatDuration, LiveLog } from "../components/LiveLog";
import { notifyError, notifyOk } from "../components/notify";
import { DiagnosticList, Hint, StatusBadge, StatusDot } from "../components/ui";
import { pid, useApp } from "../state/store";

const KIND_WORDS: Record<string, string> = { mesh: "Meshing", solve: "Simulation", fea: "Structural analysis" };
const ACTIVE = ["queued", "starting", "running", "stopping"];

/** Every job of the project with each step's output; running jobs stream live. */
export function LogsPage() {
  const { jobs, logJobId } = useApp();
  const [list, setList] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(logJobId);
  const [job, setJob] = useState<any>(null);
  const [step, setStep] = useState<number | null>(null);
  const [prog, setProg] = useState<any>(null);

  useEffect(() => {
    if (logJobId) setSel(logJobId);
  }, [logJobId]);

  const stamp = jobs.recent.map((j: any) => `${j.id}${j.status}`).join() + jobs.active.map((j: any) => j.id).join();
  useEffect(() => {
    api
      .get(`/api/p/${pid()}/jobs`)
      .then((l) => {
        const rows = [...l].reverse();
        setList(rows);
        setSel((s) => s ?? rows.find((r: any) => ACTIVE.includes(r.status))?.id ?? rows[0]?.id ?? null);
      })
      .catch(notifyError);
  }, [stamp]);

  const selRow = list.find((j) => j.id === sel);
  const running = !!selRow && ACTIVE.includes(selRow.status);
  const stepIndex = sel ? jobs.states[sel]?.step_index : undefined;

  useEffect(() => {
    if (!sel) return;
    let cancelled = false;
    api
      .get(`/api/p/${pid()}/jobs/${sel}`)
      .then((j) => {
        if (cancelled) return;
        setJob(j);
        setStep(null); // null: follow the running step
      })
      .catch(notifyError);
    return () => {
      cancelled = true;
    };
  }, [sel, selRow?.status, stepIndex]);

  useEffect(() => {
    if (!sel || !running) return setProg(null);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const r = await api.get(`/api/p/${pid()}/jobs/${sel}/progress`);
        if (!cancelled) setProg(r);
      } catch {
        /* try again */
      }
      if (!cancelled) timer = setTimeout(tick, 2000);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [sel, running]);

  const steps: any[] = job?.state?.steps || [];
  const shownStep = step ?? (running ? null : steps.length ? job.state.step_index : null);
  const followLive = running && step === null;
  const stepDiag = shownStep !== null ? steps[shownStep]?.diagnostics : steps[job?.state?.step_index ?? 0]?.diagnostics;

  const stop = async () => {
    if (!sel) return;
    try {
      await api.post(`/api/p/${pid()}/jobs/${sel}/stop`, { force: selRow?.kind !== "solve" });
      notifyOk(selRow?.kind === "solve" ? "Stop requested — the solver writes its current state and exits." : "Cancelling.");
    } catch (e) {
      notifyError(e);
    }
  };

  return (
    <div className="ab-page">
      <aside className="ab-inspector" style={{ width: 360, minWidth: 360 }}>
        <div style={{ padding: "18px 18px 8px" }}>
          <h1 className="ab-page-title">Logs</h1>
          <p className="ab-page-lede">Every meshing and simulation job, with the output of each step. Running jobs update live.</p>
        </div>
        <ScrollArea style={{ flex: 1 }} scrollbarSize={6}>
          <Stack gap={0} px={8} pb={16}>
            {list.length === 0 && <Hint style={{ padding: 10 }}>No jobs yet.</Hint>}
            {list.map((j) => (
              <button key={j.id} className="ab-step" aria-current={sel === j.id ? "page" : undefined} onClick={() => { setSel(j.id); setStep(null); }}
                style={{ height: "auto", padding: "7px 10px", gridTemplateColumns: "1fr auto", fontSize: 13 }}>
                <span style={{ minWidth: 0 }}>
                  <span style={{ display: "block", color: "var(--ab-text)" }}>
                    {KIND_WORDS[j.kind] || j.kind} <span style={{ color: "var(--ab-text-3)" }}>{j.target_id}</span>
                  </span>
                  <StatusBadge status={j.status} />
                </span>
                <span style={{ fontSize: 12, color: "var(--ab-text-3)" }}>
                  {j.cores} core{j.cores > 1 ? "s" : ""}
                </span>
              </button>
            ))}
          </Stack>
        </ScrollArea>
      </aside>
      <section className="ab-stage" style={{ padding: "14px 18px", gap: 10 }}>
        {job ? (
          <>
            <Group justify="space-between" wrap="nowrap">
              <Group gap={14} wrap="nowrap">
                <Text fw={600}>
                  {KIND_WORDS[selRow?.kind] || selRow?.kind} {selRow?.target_id}
                </Text>
                <StatusBadge status={selRow?.status} />
                {running && prog && (
                  <Text size="xs" c="dimmed">
                    {prog.phase || prog.step} · step {prog.step_index + 1} of {prog.step_count}
                    {prog.elapsed_seconds != null && ` · ${formatDuration(prog.elapsed_seconds)} so far`}
                  </Text>
                )}
              </Group>
              {running && (
                <Tooltip label={selRow?.kind === "solve" ? "The solver writes its current state, so the run can be resumed" : "Ends the job now"}>
                  <Button size="xs" variant="default" leftSection={<IconPlayerStop size={14} />} onClick={stop}>
                    {selRow?.kind === "solve" ? "Stop and save" : "Cancel"}
                  </Button>
                </Tooltip>
              )}
            </Group>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
              {running && (
                <Button size="compact-xs" variant={followLive ? "light" : "subtle"} color={followLive ? "ice" : "gray"} onClick={() => setStep(null)}>
                  <StatusDot tone="live" label="Live" />
                </Button>
              )}
              {steps.map((s: any, i: number) => (
                <Button key={i} size="compact-xs" variant={shownStep === i && !followLive ? "light" : "subtle"} color={shownStep === i && !followLive ? "ice" : "gray"}
                  onClick={() => setStep(i)} disabled={s.status === "pending"}>
                  <StatusDot
                    tone={s.status === "completed" ? "ok" : s.status === "running" ? "live" : s.status === "failed" ? "err" : s.status === "skipped" || s.status === "stopped" ? "warn" : "idle"}
                    label={s.name.replace(/_/g, " ")} />
                </Button>
              ))}
            </div>
            {shownStep !== null && steps[shownStep]?.command && (
              <Text size="xs" className="mono" c="dimmed">
                $ {steps[shownStep].command.join(" ")}
              </Text>
            )}
            <DiagnosticList items={stepDiag} />
            {sel && (
              <LiveLog key={`${sel}:${followLive ? "live" : shownStep}`} jobId={sel} live={followLive} step={followLive ? null : shownStep} style={{ flex: 1 }} />
            )}
          </>
        ) : (
          <Hint>Select a job to see its output.</Hint>
        )}
      </section>
    </div>
  );
}
