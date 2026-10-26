import { Button, Group, Select, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { IconFolderOpen, IconPlus } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { bridge } from "../api/bridge";
import { api } from "../api/client";
import type { ProjectPayload } from "../api/types";
import { EnvironmentCard } from "../components/EnvironmentCard";
import { notifyError } from "../components/notify";
import { Hint, SectionTitle } from "../components/ui";
import { useApp } from "../state/store";
import { REPO_URL } from "../about";

/** The launch window's motif, still: a car in profile with the air passing over it. */
function FlowMark() {
  return (
    <svg viewBox="0 0 600 124" width="520" height="108" aria-hidden="true" style={{ display: "block", marginLeft: -6 }}>
      <g fill="none" strokeLinecap="round">
        <path d="M0,22 C160,22 240,12 300,10 C380,8 460,16 600,20" stroke="#8db7e0" strokeOpacity="0.22" strokeWidth="1.2" />
        <path d="M0,40 C150,40 230,26 300,20 C360,16 430,30 600,38" stroke="#8db7e0" strokeOpacity="0.32" strokeWidth="1.2" />
        <path d="M0,58 C110,58 180,46 260,36 C290,30 330,26 360,28 C400,32 470,52 600,56" stroke="#8db7e0" strokeOpacity="0.45" strokeWidth="1.2" />
        <path d="M0,76 C90,76 130,70 175,66 C230,60 262,40 312,36 C360,32 392,48 440,58 C480,64 540,72 600,74" stroke="#8db7e0" strokeOpacity="0.7" strokeWidth="1.3" />
        <path d="M140,98 C142,88 152,80 175,78 L240,72 C262,58 284,48 312,46 C344,44 370,48 394,62 L440,68 C452,70 456,80 456,98 Z" stroke="#6c7580" strokeWidth="1.5" />
        <circle cx="192" cy="100" r="13" stroke="#6c7580" strokeWidth="1.5" />
        <circle cx="408" cy="100" r="13" stroke="#6c7580" strokeWidth="1.5" />
        <line x1="0" y1="113.5" x2="600" y2="113.5" stroke="#2d333c" />
      </g>
    </svg>
  );
}

export function Welcome() {
  const { openProjectPayload } = useApp();
  const [templates, setTemplates] = useState<any[]>([]);
  const [recent, setRecent] = useState<any[]>([]);
  const [name, setName] = useState("New vehicle study");
  const [folder, setFolder] = useState("");
  const [template, setTemplate] = useState<string | null>("generic_road_car");
  const [openPath, setOpenPath] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [target, setTarget] = useState("");

  useEffect(() => {
    api.get("/api/templates").then(setTemplates).catch(notifyError);
    api.get("/api/projects/recent").then(setRecent).catch(notifyError);
    api.get<{ parent: string }>("/api/projects/defaults").then((d) => setFolder((f) => f || d.parent)).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!folder || !name.trim()) return setTarget("");
    const t = setTimeout(() => {
      api.get<{ folder_name: string }>(`/api/projects/defaults?name=${encodeURIComponent(name)}`)
        .then((d) => setTarget(folder.replace(/[\\/]+$/, "") + (folder.includes("\\") ? "\\" : "/") + d.folder_name))
        .catch(() => setTarget(""));
    }, 250);
    return () => clearTimeout(t);
  }, [folder, name]);

  const pickFolder = async () => {
    const p = await bridge?.openDirectory({ title: "Choose where to keep the new project", create: true });
    if (p) setFolder(p);
  };

  const create = async () => {
    setBusy("create");
    try {
      const p = await api.post<ProjectPayload>("/api/projects", { parent: folder, name, template_id: template });
      openProjectPayload(p);
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  const demo = async (kind: "ahmed" | "wing") => {
    setBusy(`demo-${kind}`);
    try {
      openProjectPayload(await api.post<ProjectPayload>("/api/projects/demo", { kind, parent: folder || null }));
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  const open = async (path?: string) => {
    let p = path || openPath;
    if (!p && bridge) p = (await bridge.openDirectory({ title: "Open a project folder" })) || "";
    if (!p) return;
    setBusy(p);
    try {
      openProjectPayload(await api.post<ProjectPayload>("/api/projects/open", { path: p }));
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  const tpl = templates.find((t) => t.id === template);
  return (
    <div style={{ height: "100%", overflow: "auto" }}>
      <div style={{ maxWidth: 1080, margin: "0 auto", padding: "56px 40px 40px" }}>
        <div style={{ fontFamily: "var(--ab-font-num)", fontWeight: 600, fontSize: 40, lineHeight: 1, letterSpacing: 0.2 }}>AeroBench</div>
        <div style={{ color: "var(--ab-text-2)", fontSize: 15, marginTop: 8 }}>Vehicle external aerodynamics, from CAD to drag and downforce.</div>
        <div style={{ borderLeft: "2px solid var(--ab-warn)", paddingLeft: 12, marginTop: 18, maxWidth: 720, fontSize: 13, lineHeight: 1.5, color: "var(--ab-text-2)" }}>
          <span style={{ color: "var(--ab-text)" }}>Early, experimental software.</span> Many features are only lightly tested: runs may fail
          and results may be wrong, so check numbers that matter another way. Please{" "}
          <a href={`${REPO_URL}/issues`} target="_blank" rel="noreferrer" style={{ color: "var(--ab-accent)" }}>
            report problems and ideas
          </a>
          .
        </div>
        <div style={{ marginTop: 22 }}>
          <FlowMark />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", columnGap: 64, marginTop: 18 }}>
          <section>
            <SectionTitle first>Recent projects</SectionTitle>
            {recent.length === 0 && <Hint>Projects you open or create appear here.</Hint>}
            <Stack gap={0}>
              {recent.map((r) => (
                <UnstyledButton key={r.path} onClick={() => open(r.path)} disabled={!r.exists || !!busy} className="ab-recent"
                  style={{ padding: "9px 10px", borderRadius: 4, opacity: r.exists ? 1 : 0.5 }}>
                  <Group justify="space-between" wrap="nowrap" gap={12}>
                    <Text size="md" fw={500} truncate>
                      {r.name}
                    </Text>
                    <Text size="xs" c="var(--ab-text-3)" style={{ whiteSpace: "nowrap" }}>
                      {!r.exists ? "Not found" : r.modified ? new Date(r.modified).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""}
                    </Text>
                  </Group>
                  <Text size="xs" c="var(--ab-text-3)" truncate>
                    {r.path}
                  </Text>
                </UnstyledButton>
              ))}
            </Stack>
            <Group gap={8} mt={14} align="flex-end" wrap="nowrap">
              {!bridge && <TextInput style={{ flex: 1 }} size="xs" placeholder="Project folder" value={openPath} onChange={(e) => setOpenPath(e.currentTarget.value)} />}
              <Button variant="default" leftSection={<IconFolderOpen size={16} />} onClick={() => open()} loading={busy === openPath && !!openPath}>
                Open another project…
              </Button>
            </Group>
          </section>

          <section>
            <SectionTitle first>New project</SectionTitle>
            <Stack gap={12}>
              <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
              <Group align="flex-end" gap={8} wrap="nowrap">
                <TextInput style={{ flex: 1 }} label="Keep it in" value={folder} onChange={(e) => setFolder(e.currentTarget.value)} placeholder={"E:\\Aero projects"}
                  description={target ? `The project folder will be ${target}` : undefined} inputWrapperOrder={["label", "input", "description"]} />
                {bridge && (
                  <Button variant="default" onClick={pickFolder}>
                    Browse…
                  </Button>
                )}
              </Group>
              <Select label="Start from" data={templates.map((t) => ({ value: t.id, label: t.label }))} value={template} onChange={setTemplate}
                description={tpl?.description} inputWrapperOrder={["label", "input", "description"]} />
              <Group justify="flex-end">
                <Button leftSection={<IconPlus size={16} />} onClick={create} loading={busy === "create"} disabled={!folder || !name}>
                  Create project
                </Button>
              </Group>
            </Stack>

            <SectionTitle>Or try a demo</SectionTitle>
            <Stack gap={14}>
              {([
                ["ahmed", "Ahmed body", "The classic reference body, ready to run: mesh refinement regions and shape zones are set up, and a guide on each page walks through the workflow. A first run takes a few minutes."],
                ["wing", "Front wing", "A wing in ground effect studied on its own, then checked for strength: its loads go onto a carbon skin held by two mounts, and CalculiX checks every ply."],
              ] as const).map(([kind, title, text]) => (
                <Group key={kind} justify="space-between" wrap="nowrap" gap={16} align="flex-start">
                  <div style={{ minWidth: 0 }}>
                    <Text fw={500}>{title}</Text>
                    <Text size="xs" c="var(--ab-text-3)">
                      {text}
                    </Text>
                  </div>
                  <Button variant="default" onClick={() => demo(kind)} loading={busy === `demo-${kind}`} disabled={!!busy && busy !== `demo-${kind}`}
                    style={{ flexShrink: 0 }} aria-label={`Open the ${title.toLowerCase()} demo`}>
                    Open the demo
                  </Button>
                </Group>
              ))}
            </Stack>

            <SectionTitle>This computer</SectionTitle>
            <EnvironmentCard />
          </section>
        </div>
      </div>
    </div>
  );
}
