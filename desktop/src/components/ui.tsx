/** Shared UI building blocks (see styles/app.css for the visual system). */
import { Collapse, NumberInput, ScrollArea, Stack, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { IconChevronDown, IconChevronRight } from "@tabler/icons-react";
import { CSSProperties, ReactNode, useEffect, useState } from "react";
import type { Diagnostic } from "../api/client";
import type { ValidationIssue } from "../api/types";
import { fmt } from "./ColorLegend";

/**
 * Inspector on the left (settings, scrolls), stage on the right (usually the 3D view, edge to
 * edge). ``title``/``lede`` head the inspector; ``footer`` stays visible at its bottom (the
 * page's main action).
 */
export function PageLayout({
  side,
  children,
  sideWidth = 400,
  title,
  lede,
  footer,
  pad = false,
}: {
  side: ReactNode;
  children: ReactNode;
  sideWidth?: number;
  title?: ReactNode;
  lede?: ReactNode;
  footer?: ReactNode;
  pad?: boolean;
}) {
  return (
    <div className="ab-page">
      <aside className="ab-inspector" style={{ width: sideWidth, minWidth: sideWidth }}>
        <ScrollArea style={{ flex: 1 }} scrollbarSize={6}>
          <div className="ab-inspector-body">
            {title && <h1 className="ab-page-title">{title}</h1>}
            {lede && <p className="ab-page-lede">{lede}</p>}
            {side}
          </div>
        </ScrollArea>
        {footer && <div style={{ borderTop: "1px solid var(--ab-line)", padding: "12px 18px" }}>{footer}</div>}
      </aside>
      <section className="ab-stage" style={pad ? { padding: 14, gap: 12, overflow: "auto" } : undefined}>
        {children}
      </section>
    </div>
  );
}

/** A titled group of settings: sentence-case heading over a hairline, actions on the right. */
export function SectionTitle({ children, right, first }: { children: ReactNode; right?: ReactNode; first?: boolean }) {
  return (
    <div className="ab-section-head" style={{ marginTop: first ? 18 : 24 }}>
      <h2 className="ab-section-title">{children}</h2>
      {right && <div style={{ display: "flex", alignItems: "center", gap: 4 }}>{right}</div>}
    </div>
  );
}

export function PageTitle({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12, gap: 12 }}>
      <div>
        <h1 className="ab-page-title">{title}</h1>
        {subtitle && <p className="ab-page-lede">{subtitle}</p>}
      </div>
      {right}
    </div>
  );
}

export function Hint({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div className="ab-hint" style={style}>
      {children}
    </div>
  );
}

/** Numeric input that commits on blur/enter (avoids writing config on every keystroke). */
export function NumField({
  label,
  value,
  onCommit,
  units,
  min,
  max,
  step,
  decimals = 4,
  description,
  disabled,
  w,
}: {
  label: string;
  value: number | null | undefined;
  onCommit: (v: number) => void;
  units?: string;
  min?: number;
  max?: number;
  step?: number;
  decimals?: number;
  description?: string;
  disabled?: boolean;
  w?: number | string;
}) {
  const [v, setV] = useState<string | number>(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  const commit = () => {
    const n = typeof v === "number" ? v : parseFloat(String(v));
    if (Number.isFinite(n) && n !== value) onCommit(n);
  };
  return (
    <NumberInput
      label={label}
      description={description}
      value={v}
      onChange={setV}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
      rightSection={units ? <Text size="xs" c="dimmed" pr={8}>{units}</Text> : undefined}
      rightSectionWidth={units ? Math.max(30, units.length * 8 + 14) : undefined}
      min={min}
      max={max}
      step={step}
      decimalScale={decimals}
      disabled={disabled}
      size="xs"
      w={w}
      hideControls
    />
  );
}

/** One number read at a glance; part of a ``Readouts`` strip (no card around it). */
export function Kpi({ label, value, units, digits = 3, sub, color, sign }: { label: string; value: number | null | undefined; units?: string; digits?: number; sub?: ReactNode; color?: string; sign?: string }) {
  return (
    <div className="ab-readout">
      <Tooltip label={sign} disabled={!sign}>
        <div className="ab-readout-label">{label}</div>
      </Tooltip>
      <div className="ab-readout-value" style={color ? { color } : undefined}>
        {fmt(value, digits)}
        {units && <span className="ab-readout-unit">{units}</span>}
      </div>
      {sub && <div className="ab-readout-sub">{sub}</div>}
    </div>
  );
}

export function Readouts({ children }: { children: ReactNode }) {
  return <div className="ab-readouts">{children}</div>;
}

const sevVar: Record<string, string> = { error: "var(--ab-err)", warning: "var(--ab-warn)", info: "var(--ab-accent)" };

export function DiagnosticCard({ d }: { d: Diagnostic }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ab-diag" style={{ ["--sev" as string]: sevVar[d.severity] || "var(--ab-text-3)" }}>
      <div className="ab-diag-title">{d.summary}</div>
      {(d.likely_cause || d.action) && (
        <div className="ab-diag-body">
          {d.likely_cause && <div>{d.likely_cause}</div>}
          {d.action && <div style={{ color: "var(--ab-text)" }}>{d.action}</div>}
        </div>
      )}
      {d.details && (
        <>
          <UnstyledButton onClick={() => setOpen((o) => !o)} mt={4} style={{ display: "flex", alignItems: "center", gap: 2 }}>
            {open ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
            <Text size="xs" c="dimmed">
              Technical details
            </Text>
          </UnstyledButton>
          <Collapse expanded={open}>
            <pre className="log-view" style={{ maxHeight: 260, marginTop: 4 }}>
              {d.details}
            </pre>
          </Collapse>
        </>
      )}
    </div>
  );
}

export function DiagnosticList({ items, empty }: { items: Diagnostic[] | undefined | null; empty?: string }) {
  if (!items || items.length === 0) return empty ? <StatusDot tone="ok" label={empty} /> : null;
  const order = { error: 0, warning: 1, info: 2 } as Record<string, number>;
  return (
    <Stack gap={10}>
      {[...items].sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3)).map((d, i) => (
        <DiagnosticCard key={i} d={d} />
      ))}
    </Stack>
  );
}

export function ValidationList({ issues, filter }: { issues: ValidationIssue[]; filter?: (i: ValidationIssue) => boolean }) {
  const list = filter ? issues.filter(filter) : issues;
  if (!list.length) return null;
  return (
    <Stack gap={8} mt={6}>
      {list.map((i, k) => (
        <div key={k} className="ab-diag" style={{ ["--sev" as string]: sevVar[i.severity] || "var(--ab-text-3)" }}>
          <div className="ab-diag-body" style={{ color: "var(--ab-text)", marginTop: 0 }}>
            {i.message}
          </div>
          <div className="ab-hint">{i.path}</div>
        </div>
      ))}
    </Stack>
  );
}

type Tone = "ok" | "warn" | "err" | "live" | "idle" | "accent";
const toneVar: Record<Tone, string> = {
  ok: "var(--ab-ok)",
  warn: "var(--ab-warn)",
  err: "var(--ab-err)",
  live: "var(--ab-accent)",
  accent: "var(--ab-accent)",
  idle: "var(--ab-text-3)",
};

const statusTone: Record<string, Tone> = {
  completed: "ok",
  converged: "ok",
  converged_oscillating: "ok",
  running: "live",
  starting: "live",
  stopping: "warn",
  queued: "idle",
  failed: "err",
  stopped: "warn",
  interrupted: "err",
  cancelled: "idle",
  not_converged: "warn",
  diverged: "err",
  insufficient_data: "idle",
};

const statusWords: Record<string, string> = {
  converged_oscillating: "converged (oscillating)",
  not_converged: "not converged",
  insufficient_data: "too few iterations to judge",
};

export function StatusDot({ tone, label, title }: { tone: Tone; label: ReactNode; title?: string }) {
  return (
    <span className="ab-status" title={title} data-live={tone === "live" ? "true" : undefined} style={{ ["--dot" as string]: toneVar[tone] }}>
      {label}
    </span>
  );
}

/** Job/run/convergence status as a coloured dot and a plain word. */
export function StatusBadge({ status }: { status: string | null | undefined; size?: string }) {
  if (!status) return null;
  const word = statusWords[status] || status.replace(/_/g, " ");
  return <StatusDot tone={statusTone[status] || "idle"} label={word.charAt(0).toUpperCase() + word.slice(1)} />;
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="ab-facts" style={{ margin: 0 }}>
      {rows.map(([k, v]) => (
        <div key={k} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Stack align="center" justify="center" h="100%" gap={6} p="xl">
      <div className="ab-page-title" style={{ fontSize: 19, color: "var(--ab-text-2)" }}>
        {title}
      </div>
      {children && <div className="ab-hint" style={{ fontSize: 13, textAlign: "center", maxWidth: 420 }}>{children}</div>}
    </Stack>
  );
}
