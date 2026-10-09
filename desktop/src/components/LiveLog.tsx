import { ActionIcon, Button, Drawer, Group, Text, Tooltip } from "@mantine/core";
import { IconArrowBarToDown, IconCopy, IconExternalLink } from "@tabler/icons-react";
import { CSSProperties, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { pid, useApp } from "../state/store";

const MAX_CHARS = 400_000;

/**
 * Output of a job as it is written. Polls the engine once a second for what was added to
 * the running step's log (each view reads independently, so several can show the same job);
 * when the job moves to its next step the view continues with that step from its start.
 * Follows the end of the output until the user scrolls up; "Follow" jumps back. With
 * ``step`` the complete log of that step is shown once (finished steps).
 */
export function LiveLog({ jobId, step, style, live = true }: { jobId: string; step?: number | null; style?: CSSProperties; live?: boolean }) {
  const [text, setText] = useState("");
  const [follow, setFollow] = useState(true);
  const ref = useRef<HTMLPreElement>(null);

  useEffect(() => {
    setText("");
    setFollow(true);
    let cancelled = false;
    if (!live || (step !== undefined && step !== null)) {
      api
        .get(`/api/p/${pid()}/jobs/${jobId}/log?${step !== undefined && step !== null ? `step=${step}&` : ""}offset=0`)
        .then((r) => !cancelled && setText(r.text || ""))
        .catch(() => !cancelled && setText(""));
      return () => {
        cancelled = true;
      };
    }
    let reading: number | null = null;
    let offset = -1;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const q = reading === null ? `offset=-1` : `following=${reading}&offset=${offset}`;
        const r = await api.get(`/api/p/${pid()}/jobs/${jobId}/log?${q}`);
        if (cancelled) return;
        if (r.step_changed || (reading !== null && r.step !== reading)) {
          setText((t) => (t ? t + `\n──────── ${r.log ? String(r.log).replace(/^\d+-|\.log$/g, "") : "next step"} ────────\n` : "") + (r.text || ""));
        } else if (r.text) {
          setText((t) => (t + r.text).slice(-MAX_CHARS));
        }
        reading = r.step ?? reading;
        offset = r.offset ?? offset;
      } catch {
        /* engine busy or job folder gone: try again */
      }
      if (!cancelled) timer = setTimeout(tick, 1000);
    };
    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId, step, live]);

  useEffect(() => {
    const el = ref.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [text, follow]);

  const onScroll = () => {
    const el = ref.current;
    if (!el) return;
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atEnd !== follow) setFollow(atEnd);
  };

  return (
    <div style={{ position: "relative", display: "flex", flexDirection: "column", minHeight: 0, ...style }}>
      <pre ref={ref} className="log-view" onScroll={onScroll} style={{ flex: 1, margin: 0, minHeight: 120 }}>
        {text || (live ? "Waiting for output…" : "(no output)")}
      </pre>
      <Group gap={2} style={{ position: "absolute", right: 14, top: 8 }}>
        {!follow && (
          <Button size="compact-xs" variant="default" leftSection={<IconArrowBarToDown size={13} />} onClick={() => setFollow(true)}>
            Follow
          </Button>
        )}
        <Tooltip label="Copy the output">
          <ActionIcon size="sm" variant="subtle" color="gray" onClick={() => navigator.clipboard?.writeText(text)} aria-label="Copy">
            <IconCopy size={14} />
          </ActionIcon>
        </Tooltip>
      </Group>
    </div>
  );
}

/** A side panel with a job's live output, opened from any page; links to the Logs page. */
export function LogDrawer({ jobId, title, opened, onClose }: { jobId: string | null; title: string; opened: boolean; onClose: () => void }) {
  const { openLog } = useApp();
  return (
    <Drawer opened={opened && !!jobId} onClose={onClose} position="right" size={760} title={<Text fw={600}>{title}</Text>}
      styles={{ body: { height: "calc(100% - 60px)", display: "flex", flexDirection: "column" }, content: { background: "var(--ab-surface)" }, header: { background: "var(--ab-surface)" } }}>
      {jobId && (
        <>
          <LiveLog jobId={jobId} style={{ flex: 1 }} />
          <Group justify="flex-end" mt={8}>
            <Button size="xs" variant="subtle" rightSection={<IconExternalLink size={13} />} onClick={() => { onClose(); openLog(jobId); }}>
              All steps in Logs
            </Button>
          </Group>
        </>
      )}
    </Drawer>
  );
}

export function formatDuration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "—";
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h} h ${m} min`;
  if (m) return `${m} min ${sec < 10 ? "0" : ""}${sec} s`;
  return `${sec} s`;
}
