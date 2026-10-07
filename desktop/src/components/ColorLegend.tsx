import { Group, Text } from "@mantine/core";
import { colormapCss } from "../viewport/colormaps";

export function fmt(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e5)) return v.toExponential(2);
  return v.toFixed(digits);
}

export function ColorLegend({ title, colormap, range, units }: { title: string; colormap: string; range: [number, number]; units?: string }) {
  return (
    <div className="ab-float" style={{ left: 108, bottom: 12, width: 280, padding: "8px 10px" }}>
      <Text size="xs" fw={500} mb={5}>
        {title} {units && units !== "-" ? <span style={{ color: "var(--ab-text-3)" }}>({units})</span> : ""}
      </Text>
      <div style={{ height: 10, borderRadius: 2, background: colormapCss(colormap) }} />
      <Group justify="space-between" mt={2}>
        <Text size="xs" c="dimmed">
          {fmt(range[0], 2)}
        </Text>
        <Text size="xs" c="dimmed">
          {fmt((range[0] + range[1]) / 2, 2)}
        </Text>
        <Text size="xs" c="dimmed">
          {fmt(range[1], 2)}
        </Text>
      </Group>
    </div>
  );
}
