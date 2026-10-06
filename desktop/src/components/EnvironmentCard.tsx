import { Alert, Anchor, Badge, Button, Card, Group, Stack, Text } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useState } from "react";
import { api } from "../api/client";
import { useApp } from "../state/store";
import { notifyError } from "./notify";
import { DiagnosticList } from "./ui";

export function EnvironmentCard() {
  const { environment, setEnvironment } = useApp();
  const [busy, setBusy] = useState(false);
  const refresh = async () => {
    setBusy(true);
    try {
      setEnvironment(await api.get("/api/environment?refresh=true"));
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };
  if (!environment)
    return (
      <Card withBorder>
        <Text size="sm" c="dimmed">
          Checking environment…
        </Text>
      </Card>
    );
  const pv = environment.paraview || {};
  const hw = environment.hardware || {};
  return (
    <Card withBorder radius="md" p="md">
      <Group justify="space-between" mb="xs">
        <Text fw={700}>Environment</Text>
        <Button size="xs" variant="subtle" leftSection={<IconRefresh size={14} />} loading={busy} onClick={refresh}>
          Re-detect
        </Button>
      </Group>
      <Stack gap="xs">
        {(environment.providers || []).map((p: any) => (
          <div key={p.id}>
            <Group justify="space-between">
              <Text size="sm" fw={600}>
                {p.label}
              </Text>
              <Badge color={p.available ? "teal" : "red"} variant="light">
                {p.available ? "ready" : "unavailable"}
              </Badge>
            </Group>
            {p.available && (
              <Text size="xs" c="dimmed">
                {p.fork} {p.version} · MPI: {p.mpi?.available ? p.mpi.kind : "none"} · {p.project_dir}
              </Text>
            )}
            <DiagnosticList items={p.problems} />
          </div>
        ))}
        <div>
          <Group justify="space-between">
            <Text size="sm" fw={600}>
              ParaView
            </Text>
            <Badge color={pv.available ? (pv.status === "ok" ? "teal" : "yellow") : "red"} variant="light">
              {pv.version || (pv.available ? "found" : "missing")}
            </Badge>
          </Group>
          {pv.message && (
            <Alert color={pv.available ? "yellow" : "red"} variant="light" p={6} mt={4}>
              <Text size="xs">
                {pv.message}{" "}
                {!pv.available && (
                  <Anchor size="xs" href="https://www.paraview.org/download/" target="_blank">
                    paraview.org/download
                  </Anchor>
                )}
              </Text>
            </Alert>
          )}
        </div>
        <Text size="xs" c="dimmed">
          {hw.physical_cores} physical cores · {hw.logical_cores} threads · {hw.memory_gb} GB RAM · {hw.os}
        </Text>
      </Stack>
    </Card>
  );
}
