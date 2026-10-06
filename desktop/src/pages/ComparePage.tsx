import { Alert, Badge, Button, Group, MultiSelect, Paper, ScrollArea, Select, SimpleGrid, Stack, Switch, Table, Text } from "@mantine/core";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { axisStyle, EChart } from "../charts/EChart";
import { fmt } from "../components/ColorLegend";
import { notifyError } from "../components/notify";
import { EmptyState, StatusBadge } from "../components/ui";
import { pid, useApp } from "../state/store";
import { CameraState, Viewer } from "../viewport/Viewer";
import { Viewport3D } from "../viewport/Viewport3D";
import { loadLayer, useFieldInfo } from "./ResultsPage";

export function ComparePage() {
  const { runs, compareRunIds } = useApp();
  const completed = runs.filter((r) => r.status === "completed" && r.metrics);
  const [ids, setIds] = useState<string[]>(compareRunIds.length ? compareRunIds : completed.slice(0, 2).map((r) => r.id));
  const [baseline, setBaseline] = useState<string | null>(ids[0] || null);
  const [data, setData] = useState<any>(null);
  const [scene, setScene] = useState("cp_body");
  const [sync, setSync] = useState(true);
  const viewers = useRef<Record<string, Viewer>>({});
  const fieldInfo = useFieldInfo();

  useEffect(() => {
    if (!ids.length) return setData(null);
    api.post(`/api/p/${pid()}/compare`, { run_ids: ids, baseline: baseline && ids.includes(baseline) ? baseline : ids[0] }).then(setData).catch(notifyError);
  }, [ids.join(), baseline]);

  const onCamera = (src: string) => (c: CameraState) => {
    if (!sync) return;
    for (const [id, v] of Object.entries(viewers.current)) if (id !== src) v.setCamera(c);
  };

  const loadScenes = async () => {
    for (const id of ids.slice(0, 3)) {
      const v = viewers.current[id];
      if (!v) continue;
      try {
        const presets = await api.get(`/api/p/${pid()}/runs/${id}/viz/presets`);
        const p = presets.find((x: any) => x.id === scene);
        if (!p) continue;
        v.clearResults();
        await loadLayer(v, id, p, fieldInfo);
        v.view("iso");
      } catch (e) {
        notifyError(e);
      }
    }
  };

  const chart = useMemo(() => {
    if (!data) return null;
    const keys = ["Cd", "Cl", "Cl_front", "Cl_rear"];
    return {
      legend: { data: data.runs.map((r: any) => r.label) },
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: keys, ...axisStyle },
      yAxis: { type: "value", ...axisStyle },
      series: data.runs.map((r: any) => ({ name: r.label, type: "bar", data: keys.map((k) => r.metrics[k]?.value ?? null) })),
    };
  }, [data]);

  if (completed.length < 1) return <EmptyState title="Nothing to compare yet">Complete at least two runs.</EmptyState>;
  const shown = ids.slice(0, 3);
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", padding: 10, gap: 10, boxSizing: "border-box" }}>
      <Group align="flex-end">
        <MultiSelect size="xs" label="Runs" w={560} data={completed.map((r) => ({ value: r.id, label: `${r.label} (${r.id})` }))} value={ids} onChange={setIds} />
        <Select size="xs" label="Baseline" w={260} data={ids.map((i) => ({ value: i, label: completed.find((r) => r.id === i)?.label || i }))} value={baseline} onChange={setBaseline} />
      </Group>
      {data && (
        <Paper withBorder radius="md" p="sm">
          <ScrollArea>
            <Table className="compact" striped>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Run</Table.Th>
                  {data.metrics.map((m: any) => (
                    <Table.Th key={m.id} title={`${m.label}${m.sign ? ` (${m.sign})` : ""}`}>
                      {/^C[a-z]/.test(m.id) && !m.id.endsWith("A") ? m.id.replace(/_/g, " ") : m.label}
                      {m.units && m.units !== "-" ? ` [${m.units}]` : ""}
                    </Table.Th>
                  ))}
                  <Table.Th>Cells</Table.Th>
                  <Table.Th>Convergence</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {data.runs.map((r: any) => (
                  <Table.Tr key={r.run_id}>
                    <Table.Td>
                      <Group gap={4}>
                        <Text size="xs" fw={600}>
                          {r.label}
                        </Text>
                        {r.baseline && <Badge size="xs">baseline</Badge>}
                      </Group>
                    </Table.Td>
                    {data.metrics.map((m: any) => {
                      const v = r.metrics[m.id];
                      return (
                        <Table.Td key={m.id}>
                          <Text size="xs">{fmt(v?.value, m.id.endsWith("_N") ? 1 : 4)}</Text>
                          {!r.baseline && v?.delta != null && (
                            <Text size="xs" c={v.delta > 0 ? "orange" : "teal"}>
                              {v.delta > 0 ? "+" : ""}
                              {fmt(v.delta, m.id.endsWith("_N") ? 1 : 4)}
                              {v.percent != null ? ` (${v.percent > 0 ? "+" : ""}${v.percent.toFixed(1)}%)` : ""}
                            </Text>
                          )}
                        </Table.Td>
                      );
                    })}
                    <Table.Td>{r.cells ? `${(r.cells / 1e6).toFixed(2)} M` : "—"}</Table.Td>
                    <Table.Td>
                      <StatusBadge status={r.convergence} size="xs" />
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </ScrollArea>
          <Text size="xs" c="dimmed" mt={4}>
            Delta = run − baseline. Percentages are omitted for quantities that change sign (lift, balance, moments). Drag counts: 1 count = 0.001 Cd.
          </Text>
          {data.warnings.map((w: string, i: number) => (
            <Alert key={i} color="yellow" variant="light" p={4} mt={4}>
              <Text size="xs">{w}</Text>
            </Alert>
          ))}
        </Paper>
      )}
      <SimpleGrid cols={2} spacing="sm" style={{ flex: 1, minHeight: 0 }}>
        <Paper withBorder radius="md" p="sm">{chart && <EChart option={chart as any} height="100%" />}</Paper>
        <Paper withBorder radius="md" p="sm" style={{ display: "flex", flexDirection: "column" }}>
          <Group justify="space-between" mb={6}>
            <Group gap="xs">
              <Select size="xs" w={240} value={scene} onChange={(v) => v && setScene(v)}
                data={[
                  { value: "cp_body", label: "Cp body map" },
                  { value: "centreline_cp", label: "Centreline pressure" },
                  { value: "rear_wake", label: "Rear wake plane" },
                  { value: "underfloor_velocity", label: "Underfloor velocity" },
                  { value: "yplus_body", label: "y+ body map" },
                ]} />
              <Button size="xs" onClick={loadScenes}>
                Show side by side
              </Button>
            </Group>
            <Switch size="xs" label="Sync cameras" checked={sync} onChange={(e) => setSync(e.currentTarget.checked)} />
          </Group>
          <SimpleGrid cols={Math.max(1, shown.length)} spacing={6} style={{ flex: 1 }}>
            {shown.map((id) => (
              <Stack key={id} gap={2} style={{ minHeight: 0 }}>
                <Text size="xs" fw={600} lineClamp={1}>
                  {completed.find((r) => r.id === id)?.label}
                </Text>
                <div style={{ flex: 1, minHeight: 200 }}>
                  <Viewport3D toolbar={false} onReady={(v) => (viewers.current[id] = v)} onCamera={onCamera(id)} />
                </div>
              </Stack>
            ))}
          </SimpleGrid>
          {data && !data.field_difference_available && (
            <Text size="xs" c="dimmed" mt={4}>
              {data.field_difference_note}
            </Text>
          )}
        </Paper>
      </SimpleGrid>
    </div>
  );
}
