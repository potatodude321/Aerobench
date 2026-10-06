import { Button, Card, Container, Grid, Group, Select, Stack, Text, TextInput, Title, UnstyledButton } from "@mantine/core";
import { IconFolderOpen, IconPlus } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { bridge } from "../api/bridge";
import { api } from "../api/client";
import type { ProjectPayload } from "../api/types";
import { EnvironmentCard } from "../components/EnvironmentCard";
import { notifyError } from "../components/notify";
import { useApp } from "../state/store";

export function Welcome() {
  const { openProjectPayload } = useApp();
  const [templates, setTemplates] = useState<any[]>([]);
  const [recent, setRecent] = useState<any[]>([]);
  const [name, setName] = useState("New vehicle study");
  const [folder, setFolder] = useState("");
  const [template, setTemplate] = useState<string | null>("generic_road_car");
  const [openPath, setOpenPath] = useState("");
  const [busy, setBusy] = useState(false);
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
    const p = await bridge?.openDirectory({ title: "Choose a folder for the new project", create: true });
    if (p) setFolder(p);
  };

  const create = async () => {
    setBusy(true);
    try {
      const p = await api.post<ProjectPayload>("/api/projects", { parent: folder, name, template_id: template });
      openProjectPayload(p);
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const open = async (path?: string) => {
    let p = path || openPath;
    if (!p && bridge) p = (await bridge.openDirectory({ title: "Open project folder" })) || "";
    if (!p) return;
    setBusy(true);
    try {
      openProjectPayload(await api.post<ProjectPayload>("/api/projects/open", { path: p }));
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };

  const tpl = templates.find((t) => t.id === template);
  return (
    <Container size="lg" py="xl" style={{ height: "100%", overflow: "auto" }}>
      <Title order={2} mb={4}>
        Automotive external aerodynamics
      </Title>
      <Text c="dimmed" mb="lg">
        Import a vehicle, build a virtual wind tunnel, mesh, solve with OpenFOAM and explore results with ParaView — without editing dictionaries.
      </Text>
      <Grid gap="md">
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Card withBorder radius="md" p="lg">
            <Text fw={700} mb="sm">
              New project
            </Text>
            <Stack gap="sm">
              <TextInput label="Project name" value={name} onChange={(e) => setName(e.currentTarget.value)} />
              <Group align="flex-end" gap="xs">
                <TextInput style={{ flex: 1 }} label="Location (parent folder)" value={folder} onChange={(e) => setFolder(e.currentTarget.value)} placeholder={"E:\\Aero projects"} description={target ? `Project folder: ${target}` : undefined} />
                {bridge && (
                  <Button variant="default" onClick={pickFolder}>
                    Browse…
                  </Button>
                )}
              </Group>
              <Select
                label="Template"
                data={templates.map((t) => ({ value: t.id, label: t.label }))}
                value={template}
                onChange={setTemplate}
                description={tpl?.description}
              />
              <Group justify="flex-end">
                <Button leftSection={<IconPlus size={16} />} onClick={create} loading={busy} disabled={!folder || !name}>
                  Create project
                </Button>
              </Group>
            </Stack>
          </Card>
          <Card withBorder radius="md" p="lg" mt="md">
            <Text fw={700} mb="sm">
              Open project
            </Text>
            <Group align="flex-end" gap="xs">
              <TextInput style={{ flex: 1 }} label="Project folder" value={openPath} onChange={(e) => setOpenPath(e.currentTarget.value)} />
              <Button variant="default" leftSection={<IconFolderOpen size={16} />} onClick={() => open()} loading={busy}>
                Open
              </Button>
            </Group>
            {recent.length > 0 && (
              <Stack gap={2} mt="md">
                <Text size="xs" c="dimmed" fw={700} tt="uppercase">
                  Recent
                </Text>
                {recent.map((r) => (
                  <UnstyledButton key={r.path} onClick={() => open(r.path)} disabled={!r.exists} p={6} style={{ borderRadius: 6 }}>
                    <Text size="sm" fw={600} c={r.exists ? undefined : "dimmed"}>
                      {r.name}
                    </Text>
                    <Text size="xs" c="dimmed" className="mono">
                      {r.path}
                      {!r.exists && " (missing)"}
                    </Text>
                  </UnstyledButton>
                ))}
              </Stack>
            )}
          </Card>
        </Grid.Col>
        <Grid.Col span={{ base: 12, md: 5 }}>
          <EnvironmentCard />
        </Grid.Col>
      </Grid>
    </Container>
  );
}
