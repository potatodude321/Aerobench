import { Anchor, Button, Group, Stack } from "@mantine/core";
import { IconRefresh } from "@tabler/icons-react";
import { useState } from "react";
import { api } from "../api/client";
import { bridge } from "../api/bridge";
import { useApp } from "../state/store";
import { notifyError, notifyOk } from "./notify";
import { DiagnosticList, Hint, StatusDot } from "./ui";

/** What this computer can run: solver, MPI, ParaView, CAD import — as plain status lines. */
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
  if (!environment) return <Hint>Checking what is installed…</Hint>;
  const pv = environment.paraview || {};
  const hw = environment.hardware || {};
  const cad = environment.cad_import || [];
  return (
    <Stack gap={12}>
      {(environment.providers || []).map((p: any) => (
        <div key={p.id}>
          <StatusDot tone={p.available ? "ok" : "err"} label={<span style={{ color: "var(--ab-text)" }}>{p.label}</span>} />
          <Hint style={{ marginLeft: 13 }}>
            {p.available ? `${p.fork} ${p.version}, parallel runs ${p.mpi?.available ? `with ${p.mpi.kind}` : "not available"}` : "Not ready"}
          </Hint>
          {(p.problems || []).length > 0 && (
            <div style={{ marginTop: 6, marginLeft: 13 }}>
              <DiagnosticList items={p.problems} />
            </div>
          )}
          {bridge?.installMsmpi &&
            (p.problems || []).some((d: any) => ["environment.msmpi_missing", "environment.msmpi_old"].includes(d.code) && d.context?.bundled_installer) && (
              <Button size="xs" mt={6} ml={13} loading={mpiBusy} onClick={installMpi}>
                Install Microsoft MPI
              </Button>
            )}
        </div>
      ))}
      <div>
        <StatusDot tone={pv.available ? (pv.status === "ok" ? "ok" : "warn") : "err"}
          label={<span style={{ color: "var(--ab-text)" }}>ParaView {pv.version || ""}{pv.bundled ? " (included)" : ""}</span>} />
        <Hint style={{ marginLeft: 13 }}>
          {pv.message || (pv.available ? "Flow pictures and images for reports" : "Not found")}
          {!pv.available && (
            <>
              {" "}
              <Anchor size="xs" href="https://www.paraview.org/download/" target="_blank">
                paraview.org/download
              </Anchor>
            </>
          )}
        </Hint>
      </div>
      <div>
        <StatusDot tone={cad.length ? "ok" : "idle"} label={<span style={{ color: "var(--ab-text)" }}>STEP and IGES import</span>} />
        <Hint style={{ marginLeft: 13 }}>{cad.length ? `Through ${cad.join(", ")}` : "Not installed; STL, OBJ and PLY work"}</Hint>
      </div>
      <Group justify="space-between" wrap="nowrap">
        <Hint>
          {hw.physical_cores} cores, {hw.logical_cores} threads, {hw.memory_gb} GB memory
        </Hint>
        <Button size="compact-xs" variant="subtle" color="gray" leftSection={<IconRefresh size={13} />} loading={busy} onClick={refresh}>
          Check again
        </Button>
      </Group>
    </Stack>
  );
}
