import { ErrorBoundary } from "./components/ErrorBoundary";
import { Center, Loader, Stack, Switch, Text, Tooltip } from "@mantine/core";
import {
  IconArrowsDiff,
  IconCode,
  IconFileAnalytics,
  IconFolder,
  IconSettings,
  IconTable,
  IconTargetArrow,
  IconTerminal2,
} from "@tabler/icons-react";
import { useEffect } from "react";
import { api, events, initEngine } from "./api/client";
import { bridge } from "./api/bridge";
import type { ProjectPayload } from "./api/types";
import { notifyError, notifyOk } from "./components/notify";
import { StatusDot } from "./components/ui";
import { Section, useApp } from "./state/store";
import { ComparePage } from "./pages/ComparePage";
import { ExpertPage } from "./pages/ExpertPage";
import { GeometryPage } from "./pages/GeometryPage";
import { LogsPage } from "./pages/LogsPage";
import { MeshPage } from "./pages/MeshPage";
import { OptimisePage } from "./pages/OptimisePage";
import { PhysicsPage } from "./pages/PhysicsPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ReportPage } from "./pages/ReportPage";
import { ResultsPage } from "./pages/ResultsPage";
import { RunPage } from "./pages/RunPage";
import { SettingsPage } from "./pages/SettingsPage";
import { StudiesPage } from "./pages/StudiesPage";
import { TunnelPage } from "./pages/TunnelPage";
import { Welcome } from "./pages/Welcome";

/** The simulation workflow, in the order it is done. */
const WORKFLOW: { id: Section; label: string }[] = [
  { id: "geometry", label: "Geometry" },
  { id: "tunnel", label: "Tunnel" },
  { id: "mesh", label: "Mesh" },
  { id: "physics", label: "Physics" },
  { id: "run", label: "Run" },
  { id: "results", label: "Results" },
];
const ANALYSIS: { id: Section; label: string; icon: any }[] = [
  { id: "compare", label: "Compare", icon: IconArrowsDiff },
  { id: "studies", label: "Studies", icon: IconTable },
  { id: "optimise", label: "Optimise", icon: IconTargetArrow },
  { id: "report", label: "Report", icon: IconFileAnalytics },
];
const PROJECT: { id: Section; label: string; icon: any }[] = [
  { id: "project", label: "Project", icon: IconFolder },
  { id: "logs", label: "Logs", icon: IconTerminal2 },
  { id: "expert", label: "Expert", icon: IconCode },
  { id: "settings", label: "Settings", icon: IconSettings },
];

function SectionContent({ section }: { section: Section }) {
  switch (section) {
    case "geometry":
      return <GeometryPage />;
    case "tunnel":
      return <TunnelPage />;
    case "mesh":
      return <MeshPage />;
    case "physics":
      return <PhysicsPage />;
    case "run":
      return <RunPage />;
    case "results":
      return <ResultsPage />;
    case "compare":
      return <ComparePage />;
    case "studies":
      return <StudiesPage />;
    case "optimise":
      return <OptimisePage />;
    case "report":
      return <ReportPage />;
    case "project":
      return <ProjectPage />;
    case "logs":
      return <LogsPage />;
    case "expert":
      return <ExpertPage />;
    case "settings":
      return <SettingsPage />;
  }
}

type StepState = "done" | "stale" | "running" | "issue" | "todo";

/** Where each workflow stage stands, for the markers in the rail. */
function stepStates(project: ProjectPayload, activeJobs: any[], hasResults: boolean): Record<string, { state: StepState; note?: string; tip: string }> {
  const st = project.staleness;
  const hasGeo = !!project.config.geometry.revision_id;
  const errs = (re: RegExp) => project.validation.filter((v) => v.severity === "error" && re.test(v.path)).length;
  const meshJobs = activeJobs.filter((j) => j.kind === "mesh").length;
  const runJobs = activeJobs.filter((j) => j.kind !== "mesh").length;
  const out: Record<string, { state: StepState; note?: string; tip: string }> = {};
  out.geometry = hasGeo ? { state: "done", tip: "Vehicle imported" } : { state: "todo", note: "Start here", tip: "Import the vehicle surface" };
  const t = errs(/^tunnel/);
  out.tunnel = !hasGeo ? { state: "todo", tip: "Set up after importing the vehicle" } : t ? { state: "issue", note: `${t} issue${t > 1 ? "s" : ""}`, tip: "Setup issues" } : { state: "done", tip: "Domain set" };
  const m = st.mesh.state;
  if (meshJobs || m === "running") out.mesh = { state: "running", note: "Meshing", tip: "A mesh for the current settings is being generated" };
  else if (m === "current") out.mesh = { state: "done", tip: "A mesh exists for the current settings" };
  else if (m === "failed") out.mesh = { state: "issue", note: "Failed", tip: "Meshing with the current settings failed; see the Mesh page" };
  else if (m === "stale") out.mesh = { state: "stale", note: "Not meshed", tip: "No mesh exists for the current settings yet; the next run meshes first" };
  else out.mesh = { state: "todo", tip: "Not meshed yet (a run meshes automatically)" };
  const ph = errs(/^(physics|operating|numerics)/);
  out.physics = !hasGeo ? { state: "todo", tip: "Set up after importing the vehicle" } : ph ? { state: "issue", note: `${ph} issue${ph > 1 ? "s" : ""}`, tip: "Setup issues" } : { state: "done", tip: "Physics set" };
  const r = st.solve.state;
  if (runJobs) out.run = { state: "running", note: `${runJobs} running`, tip: "Simulations in progress" };
  else if (r === "current") out.run = { state: "done", tip: "A finished run exists for the current settings" };
  else if (r === "stopped") out.run = { state: "done", note: "Stopped", tip: "The run for the current settings was stopped; it can be resumed" };
  else if (r === "failed") out.run = { state: "issue", note: "Failed", tip: "The run for the current settings failed; see the Run page" };
  else if (r === "stale") out.run = { state: "stale", note: "Not run", tip: "Nothing has been run with the current settings yet" };
  else out.run = { state: "todo", tip: "No run yet" };
  if (!hasResults) out.results = { state: "todo", tip: "No results yet" };
  else if (r === "stale" || r === "failed") out.results = { state: "stale", note: "Earlier settings", tip: "The results shown are from runs with other settings" };
  else out.results = { state: "done", tip: "Results available" };
  return out;
}

export default function App() {
  const { engineReady, engineError, project, section, setSection, expertMode, setExpert, setEngineReady, setEnvironment, setJobs, jobs, runs } = useApp();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await initEngine();
        await api.get("/api/health");
        if (cancelled) return;
        setEngineReady(true);
        events.connect();
        api.get("/api/environment").then(setEnvironment).catch(notifyError);
      } catch (e) {
        setEngineReady(false, String((e as Error).message || e));
      }
    })();
    bridge?.onEngineExit((info) => setEngineReady(false, `The engine stopped (code ${info.code}).\n${info.log.join("\n")}`));
    const off = events.on((msg) => {
      if (msg.type === "jobs") {
        const prev = useApp.getState().jobs;
        setJobs({ active: msg.active, recent: msg.recent, states: msg.states });
        const changed = msg.recent.some((r: any) => {
          const before = prev.recent.find((x: any) => x.id === r.id);
          return !before || before.status !== r.status || before.ingested !== r.ingested;
        });
        // results are read a moment after a job ends ("ingested"): only then are they complete
        const ready = msg.recent.filter((r: any) => {
          const before = prev.recent.find((x: any) => x.id === r.id);
          return before && !before.ingested && r.ingested;
        });
        if (changed || msg.active.length !== prev.active.length) {
          const st = useApp.getState();
          st.refreshRuns().then(() => {
            for (const r of ready) {
              if (r.kind === "solve") {
                const run = useApp.getState().runs.find((x) => x.id === r.target_id);
                if (run?.metrics) notifyOk(`Cd ${run.metrics.Cd?.toFixed(4) ?? "—"} — open the Results page to explore it.`, `Results ready: ${run.label}`);
                else if (run && ["failed", "cancelled"].includes(run.status)) notifyError(new Error("See the Run page for the reason."), `Run ${run.label} did not finish`);
              } else if (r.kind === "mesh" && r.status === "completed") {
                notifyOk("Open the Mesh page to inspect it.", "Mesh ready");
              }
            }
          });
          st.refreshProject();
        }
      }
    });
    return () => {
      cancelled = true;
      off();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!engineReady) {
    return (
      <Center h="100%">
        <Stack align="center" gap={8} maw={720} p="xl">
          {engineError ? (
            <>
              <div className="ab-page-title" style={{ color: "var(--ab-err)" }}>
                The engine is not reachable
              </div>
              <Text size="xs" c="dimmed" style={{ whiteSpace: "pre-wrap" }}>
                {engineError}
              </Text>
            </>
          ) : (
            <>
              <Loader size="sm" color="ice" />
              <Text c="dimmed">Opening the workspace…</Text>
            </>
          )}
        </Stack>
      </Center>
    );
  }

  if (!project) return <Welcome />;

  const running = jobs.active.length;
  const hasResults = runs.some((r) => ["completed", "stopped", "interrupted"].includes(r.status) && r.metrics);
  const states = stepStates(project, jobs.active, hasResults);
  const errors = project.validation.filter((v) => v.severity === "error");

  return (
    <div className="ab-shell">
      <header className="ab-header">
        <div style={{ display: "flex", alignItems: "baseline", minWidth: 0 }}>
          <span className="ab-wordmark">AeroBench</span>
          <span className="ab-crumb">/</span>
          <Tooltip label={project.root}>
            <span style={{ fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{project.config.name}</span>
          </Tooltip>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          {errors.length > 0 && (
            <Tooltip label={errors.map((v) => v.message).join("\n")} w={380}>
              <span>
                <StatusDot tone="err" label={`${errors.length} setup issue${errors.length > 1 ? "s" : ""}`} />
              </span>
            </Tooltip>
          )}
          <StatusDot tone={running ? "live" : "idle"} label={running ? `${running} job${running > 1 ? "s" : ""} running` : "Idle"} />
          <Switch size="xs" label="Expert" checked={expertMode} onChange={(e) => setExpert(e.currentTarget.checked)} />
        </div>
      </header>
      <nav className="ab-rail" aria-label="Sections">
        <div className="ab-rail-group">Workflow</div>
        <div className="ab-seq">
          {WORKFLOW.map((s, i) => {
            const st = states[s.id];
            return (
              <Tooltip key={s.id} label={st.tip} position="right" openDelay={500} disabled={!st.note}>
                <button className="ab-step" data-state={st.state} aria-current={section === s.id ? "page" : undefined} onClick={() => setSection(s.id)}>
                  <span className="ab-step-no">{i + 1}</span>
                  <span>{s.label}</span>
                  <span className="ab-step-note" style={st.state === "issue" ? { color: "var(--ab-err)" } : undefined}>
                    {st.note}
                  </span>
                </button>
              </Tooltip>
            );
          })}
        </div>
        <div className="ab-rail-group" style={{ marginTop: 22 }}>
          Analysis
        </div>
        <div className="ab-plain">
          {ANALYSIS.map((s) => (
            <button key={s.id} className="ab-step" aria-current={section === s.id ? "page" : undefined} onClick={() => setSection(s.id)}>
              <s.icon size={16} stroke={1.6} />
              <span>{s.label}</span>
              <span />
            </button>
          ))}
        </div>
        <div style={{ flex: 1 }} />
        <div className="ab-plain">
          {PROJECT.filter((s) => s.id !== "expert" || expertMode).map((s) => (
            <button key={s.id} className="ab-step" aria-current={section === s.id ? "page" : undefined} onClick={() => setSection(s.id)}>
              <s.icon size={16} stroke={1.6} />
              <span>{s.label}</span>
              <span />
            </button>
          ))}
        </div>
      </nav>
      <main className="ab-main">
        <ErrorBoundary resetKey={section}>
          <SectionContent section={section} />
        </ErrorBoundary>
      </main>
    </div>
  );
}
