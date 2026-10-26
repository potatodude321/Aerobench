import { Button, Container, Group, NumberInput, Paper, Stack, Switch, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { AboutPanel } from "../components/AboutPanel";
import { EnvironmentCard } from "../components/EnvironmentCard";
import { SectionTitle } from "../components/ui";
import { notifyError, notifyOk } from "../components/notify";
import { useApp } from "../state/store";

export function SettingsPage() {
  const { setEnvironment } = useApp();
  const [s, setS] = useState<any>(null);
  useEffect(() => {
    api.get("/api/settings").then(setS).catch(notifyError);
  }, []);
  if (!s) return null;
  const save = async () => {
    try {
      setS(await api.put("/api/settings", s));
      setEnvironment(await api.get("/api/environment"));
      notifyOk("Settings saved and environment re-detected.");
    } catch (e) {
      notifyError(e);
    }
  };
  const native = s.providers.find((p: any) => p.kind === "native-windows")?.config?.root || "";
  return (
    <Container size="md" py="md" style={{ height: "100%", overflow: "auto" }}>
      <Title order={3} mb="md">
        Settings
      </Title>
      <Stack>
        <div>
          <SectionTitle first>This computer</SectionTitle>
          <EnvironmentCard />
        </div>
        <Paper withBorder p="md" radius="md">
          <Text fw={700} mb="xs">
            Solver
          </Text>
          <TextInput label="OpenFOAM for Windows install root (optional)" description="Leave empty to auto-detect %APPDATA%\Keysight-OpenCFD\OpenFOAM\v2606"
            value={native}
            onChange={(e) => {
              const root = e.currentTarget.value;
              const others = s.providers.filter((p: any) => p.kind !== "native-windows");
              setS({ ...s, providers: root ? [{ id: "native", kind: "native-windows", config: { root } }, ...others] : others });
            }} />
          <Group grow mt="xs">
            <NumberInput label="Maximum cores used by this application" value={s.max_cores ?? ""} placeholder="all physical cores" min={1}
              onChange={(v) => setS({ ...s, max_cores: typeof v === "number" ? v : null })} />
            <NumberInput label="Background worker idle timeout (s)" value={s.worker_idle_timeout_s} min={10}
              onChange={(v) => typeof v === "number" && setS({ ...s, worker_idle_timeout_s: v })} />
          </Group>
          <Switch mt="sm" label="Allow experimental fork adapters (OpenFOAM Foundation v13/v14 — not validated)" checked={s.allow_experimental_adapters}
            onChange={(e) => setS({ ...s, allow_experimental_adapters: e.currentTarget.checked })} />
        </Paper>
        <Paper withBorder p="md" radius="md">
          <Text fw={700} mb="xs">
            ParaView
          </Text>
          <TextInput label="pvpython executable (optional)" placeholder="auto-detect" value={s.paraview?.pvpython || ""}
            onChange={(e) => setS({ ...s, paraview: { ...s.paraview, pvpython: e.currentTarget.value || null } })} />
        </Paper>
        <Group justify="flex-end">
          <Button onClick={save}>Save settings</Button>
        </Group>
        <AboutPanel />
      </Stack>
    </Container>
  );
}
