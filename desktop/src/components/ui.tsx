/** Shared UI building blocks. */
import { Alert, Badge, Box, Card, Collapse, Group, NumberInput, Paper, ScrollArea, Stack, Text, Title, Tooltip, UnstyledButton } from "@mantine/core";
import { IconAlertTriangle, IconChevronDown, IconChevronRight, IconCircleCheck, IconInfoCircle, IconX } from "@tabler/icons-react";
import { ReactNode, useEffect, useState } from "react";
import type { Diagnostic } from "../api/client";
import type { ValidationIssue } from "../api/types";
import { fmt } from "./ColorLegend";

export function PageLayout({ side, children, sideWidth = 420 }: { side: ReactNode; children: ReactNode; sideWidth?: number }) {
  return (
    <div style={{ display: "flex", height: "100%", gap: 10, padding: 10, boxSizing: "border-box" }}>
      <Paper withBorder radius="md" style={{ width: sideWidth, minWidth: sideWidth, height: "100%", overflow: "hidden", display: "flex", flexDirection: "column" }}>
        <ScrollArea style={{ flex: 1 }} offsetScrollbars>
          <Box p="md">{side}</Box>
        </ScrollArea>
      </Paper>
      <div style={{ flex: 1, minWidth: 0, height: "100%", display: "flex", flexDirection: "column", gap: 10 }}>{children}</div>
    </div>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <Group justify="space-between" mt="md" mb={6}>
      <Text size="xs" fw={700} tt="uppercase" c="dimmed" style={{ letterSpacing: 0.6 }}>
        {children}
      </Text>
      {right}
    </Group>
  );
}

export function PageTitle({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <Group justify="space-between" align="flex-start" mb="xs">
      <div>
        <Title order={3}>{title}</Title>
        {subtitle && (
          <Text size="sm" c="dimmed">
            {subtitle}
          </Text>
        )}
      </div>
      {right}
    </Group>
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
      rightSection={units ? <Text size="xs" c="dimmed" pr={6}>{units}</Text> : undefined}
      rightSectionWidth={units ? Math.max(30, units.length * 8 + 12) : undefined}
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

export function Kpi({ label, value, units, digits = 3, sub, color, sign }: { label: string; value: number | null | undefined; units?: string; digits?: number; sub?: ReactNode; color?: string; sign?: string }) {
  return (
    <Card withBorder radius="md" p="sm" style={{ minWidth: 150, flex: 1 }}>
      <Tooltip label={sign || label} disabled={!sign}>
        <Text size="xs" c="dimmed" fw={600} tt="uppercase">
          {label}
        </Text>
      </Tooltip>
      <Group gap={4} align="baseline">
        <Text fz={26} fw={700} className="kpi-value" c={color}>
          {fmt(value, digits)}
        </Text>
        {units && (
          <Text size="sm" c="dimmed">
            {units}
          </Text>
        )}
      </Group>
      {sub && (
        <Text size="xs" c="dimmed">
          {sub}
        </Text>
      )}
    </Card>
  );
}

const sevColor = { error: "red", warning: "yellow", info: "blue" } as const;

export function DiagnosticCard({ d }: { d: Diagnostic }) {
  const [open, setOpen] = useState(false);
  const Icon = d.severity === "error" ? IconX : d.severity === "warning" ? IconAlertTriangle : IconInfoCircle;
  return (
    <Alert color={sevColor[d.severity] || "gray"} variant="light" icon={<Icon size={18} />} title={d.summary} p="xs">
      {d.likely_cause && (
        <Text size="xs">
          <b>Likely cause:</b> {d.likely_cause}
        </Text>
      )}
      {d.action && (
        <Text size="xs">
          <b>What to do:</b> {d.action}
        </Text>
      )}
      {d.details && (
        <>
          <UnstyledButton onClick={() => setOpen((o) => !o)} mt={4}>
            <Group gap={2}>
              {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
              <Text size="xs" c="dimmed">
                Raw details
              </Text>
            </Group>
          </UnstyledButton>
          <Collapse expanded={open}>
            <pre className="log-view" style={{ maxHeight: 260, marginTop: 4 }}>
              {d.details}
            </pre>
          </Collapse>
        </>
      )}
    </Alert>
  );
}

export function DiagnosticList({ items, empty }: { items: Diagnostic[] | undefined | null; empty?: string }) {
  if (!items || items.length === 0)
    return empty ? (
      <Group gap={6}>
        <IconCircleCheck size={16} color="var(--mantine-color-teal-5)" />
        <Text size="sm" c="dimmed">
          {empty}
        </Text>
      </Group>
    ) : null;
  const order = { error: 0, warning: 1, info: 2 } as Record<string, number>;
  return (
    <Stack gap={6}>
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
    <Stack gap={4}>
      {list.map((i, k) => (
        <Alert key={k} color={i.severity === "error" ? "red" : i.severity === "warning" ? "yellow" : "blue"} variant="light" p={6}>
          <Text size="xs">
            <b>{i.path}</b>: {i.message}
          </Text>
        </Alert>
      ))}
    </Stack>
  );
}

const statusColor: Record<string, string> = {
  completed: "teal",
  running: "cyan",
  starting: "cyan",
  stopping: "orange",
  queued: "gray",
  failed: "red",
  stopped: "orange",
  interrupted: "red",
  cancelled: "gray",
  converged: "teal",
  converged_oscillating: "lime",
  not_converged: "yellow",
  diverged: "red",
  insufficient_data: "gray",
};

export function StatusBadge({ status, size = "sm" }: { status: string | null | undefined; size?: string }) {
  if (!status) return null;
  return (
    <Badge color={statusColor[status] || "gray"} variant="light" size={size} style={{ flexShrink: 0 }} styles={{ label: { overflow: "visible", textOverflow: "clip" } }}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

export function KeyValue({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <Stack gap={2}>
      {rows.map(([k, v]) => (
        <Group key={k} justify="space-between" wrap="nowrap">
          <Text size="xs" c="dimmed">
            {k}
          </Text>
          <Text size="xs" fw={500} ta="right" className="kpi-value">
            {v}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Stack align="center" justify="center" h="100%" gap="xs" p="xl">
      <Title order={4} c="dimmed">
        {title}
      </Title>
      {children}
    </Stack>
  );
}
