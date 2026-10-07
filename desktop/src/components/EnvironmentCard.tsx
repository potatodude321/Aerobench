import { Alert, Anchor, Badge, Button, Card, Group, Stack, Text } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useState } from "react";
import { api } from "../api/client";
import { bridge } from "../api/bridge";
import { useApp } from "../state/store";
import { notifyError, notifyOk } from "./notify";
import { DiagnosticList } from "./ui";

export function EnvironmentCard() {
  const { environment, setEnvironment } = useApp();
  const [busy, setBusy] = useState(false);
  const [mpiBusy, setMpiBusy] = useState(false);
  const installMpi = async () => {
    if (!bridge) return;
    setMpiBusy(true);
    try {
      const r = await bridge.installMsmpi();
      if (r.ok) notifyOk("Microsoft MPI installed.");
      else notifyError(new Error(r.error || "Microsoft MPI could not be installed."));
      setEnvironment(await api.get("/api/environment?refresh=true"));
    } catch (e) {
      notifyError(e);
    } finally {
      setMpiBusy(false);
    }
  };
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
            {bridge?.installMsmpi &&
              (p.problems || []).some((d: any) => ["environment.msmpi_missing", "environment.msmpi_old"].includes(d.code) && d.context?.bundled_installer) && (
                <Button size="xs" mt={4} loading={mpiBusy} onClick={installMpi}>
                  Install Microsoft MPI
                </Button>
              )}
          </div>
        ))}
        <div>
          <Group justify="space-between">
            <Text size="sm" fw={600}>
              ParaView
            </Text>
            <Badge color={pv.available ? (pv.status === "ok" ? "teal" : "yellow") : "red"} variant="light">
              {pv.version || (pv.available ? "found" : "missing")}
              {pv.bundled ? " · included" : ""}
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
        <Group justify="space-between">
          <Text size="sm" fw={600}>
            CAD import (STEP/IGES)
          </Text>
          <Badge color={(environment.cad_import || []).length ? "teal" : "gray"} variant="light">
            {(environment.cad_import || []).length ? environment.cad_import.join(", ") : "not installed"}
          </Badge>
        </Group>
        <Text size="xs" c="dimmed">
          {hw.physical_cores} physical cores · {hw.logical_cores} threads · {hw.memory_gb} GB RAM · {hw.os}
        </Text>
      </Stack>
    </Card>
  );
}
