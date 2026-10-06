import { AppShell, Badge, Box, Center, Group, Loader, NavLink, Stack, Switch, Text, Tooltip } from "@mantine/core";
import {
  IconArrowsDiff,
  IconAtom2,
  IconCar,
  IconChartAreaLine,
  IconCode,
  IconFileAnalytics,
  IconFolder,
  IconGridDots,
  IconPlayerPlay,
  IconSettings,
  IconTable,
  IconTargetArrow,
  IconTerminal2,
  IconWind,
} from "@tabler/icons-react";
import { useEffect } from "react";
import { api, events, initEngine } from "./api/client";
import { bridge } from "./api/bridge";
import { notifyError } from "./components/notify";
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

const PRIMARY: { id: Section; label: string; icon: any }[] = [
  { id: "geometry", label: "Geometry", icon: IconCar },
  { id: "tunnel", label: "Tunnel", icon: IconWind },
  { id: "mesh", label: "Mesh", icon: IconGridDots },
  { id: "physics", label: "Physics", icon: IconAtom2 },
  { id: "run", label: "Run", icon: IconPlayerPlay },
  { id: "results", label: "Results", icon: IconChartAreaLine },
  { id: "compare", label: "Compare", icon: IconArrowsDiff },
  { id: "studies", label: "Studies", icon: IconTable },
  { id: "optimise", label: "Optimise", icon: IconTargetArrow },
  { id: "report", label: "Report", icon: IconFileAnalytics },
];
const SECONDARY: { id: Section; label: string; icon: any }[] = [
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

export default function App() {
  const { engineReady, engineError, project, section, setSection, expertMode, setExpert, setEngineReady, setEnvironment, setJobs, jobs } = useApp();

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
        const finished = msg.recent.some((r: any) => {
          const before = prev.recent.find((x: any) => x.id === r.id);
          return before && before.status !== r.status;
        });
        if (finished || msg.active.length !== prev.active.length) {
          useApp.getState().refreshRuns();
          useApp.getState().refreshProject();
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
        <Stack align="center">
          {engineError ? (
            <>
              <Text c="red" fw={600}>
                The engine is not reachable
              </Text>
              <Text size="xs" c="dimmed" maw={700} style={{ whiteSpace: "pre-wrap" }}>
                {engineError}
              </Text>
            </>
          ) : (
            <>
              <Loader />
              <Text c="dimmed">Starting engine…</Text>
            </>
          )}
        </Stack>
      </Center>
    );
  }

  if (!project) return <Welcome />;

  const st = project.staleness;
  const running = jobs.active.length;
  const badge = (id: Section) => {
    if (id === "geometry" && !project.config.geometry.revision_id) return <Badge size="xs" color="yellow">import</Badge>;
    if (id === "mesh" && st.mesh.stale) return <Badge size="xs" color="orange">stale</Badge>;
    if (id === "run" && running) return <Badge size="xs" color="cyan">{running}</Badge>;
    if (id === "results" && st.solve.stale) return <Badge size="xs" color="orange">stale</Badge>;
    return null;
  };
  const errors = project.validation.filter((v) => v.severity === "error").length;

  return (
    <AppShell header={{ height: 46 }} navbar={{ width: 190, breakpoint: 0 }} padding={0}>
      <AppShell.Header style={{ background: "#111419", borderColor: "#232831" }}>
        <Group h="100%" px="md" justify="space-between">
          <Group gap="sm">
            <Text fw={800} c="cyan.4" style={{ letterSpacing: 0.5 }}>
              AeroBench
            </Text>
            <Text c="dimmed" size="sm">
              /
            </Text>
            <Text fw={600} size="sm">
              {project.config.name}
            </Text>
            <Text c="dimmed" size="xs" className="mono">
              {project.root}
            </Text>
          </Group>
          <Group gap="md">
            {errors > 0 && (
              <Tooltip label={project.validation.filter((v) => v.severity === "error").map((v) => v.message).join("\n")} multiline w={360}>
                <Badge color="red" variant="light">
                  {errors} setup issue{errors > 1 ? "s" : ""}
                </Badge>
              </Tooltip>
            )}
            <Badge variant="dot" color={running ? "cyan" : "gray"}>
              {running ? `${running} job${running > 1 ? "s" : ""} running` : "idle"}
            </Badge>
            <Switch size="xs" label="Expert" checked={expertMode} onChange={(e) => setExpert(e.currentTarget.checked)} />
          </Group>
        </Group>
      </AppShell.Header>
      <AppShell.Navbar p={6} style={{ background: "#111419", borderColor: "#232831" }}>
        <Stack gap={2} h="100%">
          {PRIMARY.map((s) => (
            <NavLink
              key={s.id}
              active={section === s.id}
              label={s.label}
              leftSection={<s.icon size={18} stroke={1.6} />}
              rightSection={badge(s.id)}
              onClick={() => setSection(s.id)}
              variant="light"
              style={{ borderRadius: 6 }}
            />
          ))}
          <Box style={{ flex: 1 }} />
          {SECONDARY.filter((s) => s.id !== "expert" || expertMode).map((s) => (
            <NavLink
              key={s.id}
              active={section === s.id}
              label={s.label}
              leftSection={<s.icon size={17} stroke={1.6} />}
              onClick={() => setSection(s.id)}
              variant="light"
              style={{ borderRadius: 6 }}
            />
          ))}
        </Stack>
      </AppShell.Navbar>
      <AppShell.Main style={{ height: "100vh", paddingTop: 46, background: "#0c0e12" }}>
        <SectionContent section={section} />
      </AppShell.Main>
    </AppShell>
  );
}
