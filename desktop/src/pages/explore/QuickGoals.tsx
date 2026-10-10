/**
 * One-click optimisation goals: each row says what may change, what must not get worse and
 * how long it takes, and starts a complete optimisation with one click (the engine picks the
 * variables, constraints, budget and scheduling; see services/quick_optimise.py).
 */
import { Button, Group, Menu, Stack, Text, Tooltip } from "@mantine/core";
import { IconPlayerPlay, IconTargetArrow } from "@tabler/icons-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import { notifyError, notifyOk } from "../../components/notify";
import { Hint } from "../../components/ui";
import { pid, useApp } from "../../state/store";

interface Goal {
  goal: string;
  title: string;
  available: boolean;
  reason?: string;
  objective?: string;
  changes?: string[];
  keeps?: string[];
  designs?: number;
  concurrent?: number;
  estimate_seconds?: number | null;
  zones_added?: string[];
  notes?: string[];
  busy?: boolean;
  active_study_id?: string | null;
}

export function duration(s: number): string {
  const m = Math.max(1, Math.round(s / 60));
  if (m < 60) return `about ${m} min`;
  const h = Math.floor(m / 60);
  const r = Math.round((m - 60 * h) / 5) * 5;
  return `about ${h} h${r ? ` ${r} min` : ""}`;
}

function summarise(items: string[], max = 3): string {
  if (items.length <= max) return items.join("; ");
  return `${items.slice(0, max).join("; ")} and ${items.length - max} more`;
}

export function QuickGoals({ onStarted, refreshKey }: { onStarted: (studyId: string) => void; refreshKey?: unknown }) {
  const project = useApp((s) => s.project)!;
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const element = project.config.application === "aero_element";

  const load = useCallback(() => {
    api.get<Goal[]>(`/api/p/${pid()}/optimise/quick`).then(setGoals).catch(() => setGoals([]));
  }, []);
  useEffect(load, [load, refreshKey, project.config.zones?.length, project.config.geometry.revision_id]);

  const start = async (g: Goal) => {
    setBusy(g.goal);
    try {
      const d = await api.post(`/api/p/${pid()}/optimise/quick`, { goal: g.goal });
      notifyOk(`${d.definition?.budget ?? g.designs} designs; each is a full flow simulation. Progress and the best design so far show on the right.`,
        `${g.title}: optimisation started`);
      onStarted(d.id);
      await useApp.getState().refreshProject();
      load();
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(null);
    }
  };

  if (goals === null) return null;
  return (
    <Stack gap={10}>
      <Hint>
        Pick a goal and AeroBench sets up the rest: what may change ({element ? "the angle and height of the element" : "your shape zones, or typical ones it places"}),
        what must not get worse, and how many designs to try.
      </Hint>
      {goals.map((g) => (
        <div key={g.goal} className="ab-quick-goal" data-disabled={!g.available || undefined}>
          <Group justify="space-between" wrap="nowrap" align="flex-start" gap={10}>
            <div style={{ minWidth: 0 }}>
              <Text size="sm" fw={600}>
                {g.title}
              </Text>
              {g.available ? (
                <>
                  <Text size="xs" c="dimmed">
                    Changes: {summarise(g.changes || [])}
                  </Text>
                  {(g.keeps || []).length > 0 && (
                    <Text size="xs" c="dimmed">
                      Keeps: {(g.keeps || []).join("; ")}
                    </Text>
                  )}
                  <Text size="xs" c="dimmed">
                    {g.designs} designs{(g.concurrent || 1) > 1 ? `, ${g.concurrent} at a time` : ""}
                    {g.estimate_seconds ? `, ${duration(g.estimate_seconds)}` : " (time shown after a first run)"}
                  </Text>
                  {(g.notes || []).map((n, i) => (
                    <Text key={i} size="xs" c="var(--ab-text-3)">
                      {n}
                    </Text>
                  ))}
                </>
              ) : (
                <Text size="xs" c="var(--ab-text-3)">
                  {g.reason}
                </Text>
              )}
            </div>
            {g.active_study_id ? (
              <Button size="xs" variant="subtle" onClick={() => onStarted(g.active_study_id!)} style={{ flexShrink: 0 }} aria-label={`Show: ${g.title}`}>
                Running
              </Button>
            ) : (
              <Tooltip label="Another optimisation is running: this one shares the cores with it" disabled={!g.busy || !g.available}>
                <Button size="xs" variant="light" leftSection={<IconPlayerPlay size={13} />} disabled={!g.available || (!!busy && busy !== g.goal)}
                  loading={busy === g.goal} onClick={() => start(g)} aria-label={`Start: ${g.title}`} style={{ flexShrink: 0 }}>
                  Start
                </Button>
              </Tooltip>
            )}
          </Group>
        </div>
      ))}
    </Stack>
  );
}

/** "Optimise" menu for other pages (Results): the same goals, started in one click, then the
 * Optimise page opens on the new optimisation. */
export function OptimiseMenu() {
  const [goals, setGoals] = useState<Goal[] | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.get<Goal[]>(`/api/p/${pid()}/optimise/quick`).then(setGoals).catch(() => setGoals([]));
  const start = async (g: Goal) => {
    setBusy(true);
    try {
      await api.post(`/api/p/${pid()}/optimise/quick`, { goal: g.goal });
      notifyOk(`${g.designs} designs; each is a full flow simulation.`, `${g.title}: optimisation started`);
      await useApp.getState().refreshProject();
      useApp.getState().setSection("optimise");
    } catch (e) {
      notifyError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Menu position="bottom-end" width={330} onOpen={load}>
      <Menu.Target>
        <Button size="xs" variant="default" leftSection={<IconTargetArrow size={14} />} loading={busy}>
          Optimise
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Label>Optimise the current setup</Menu.Label>
        {goals === null && <Menu.Item disabled>Loading…</Menu.Item>}
        {(goals || []).map((g) => (
          <Menu.Item key={g.goal} disabled={!g.available || !!g.active_study_id} onClick={() => start(g)}>
            <Text size="sm">{g.title}{g.active_study_id ? " (running)" : ""}</Text>
            <Text size="xs" c="dimmed">
              {g.available
                ? `${g.designs} designs${g.estimate_seconds ? `, ${duration(g.estimate_seconds)}` : ""}${(g.keeps || []).length ? `; keeps ${(g.keeps || []).join("; ")}` : ""}`
                : g.reason}
            </Text>
          </Menu.Item>
        ))}
      </Menu.Dropdown>
    </Menu>
  );
}
