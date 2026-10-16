/**
 * A number field whose label can be dragged: press on the label (or the arrows beside it) and
 * move left or right, and the value follows with the change shown live; release to keep it.
 * Shift drags ten times faster, Alt ten times finer. Typing works as in any number field.
 */
import { NumberInput, Text } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

export function ScrubField({
  label,
  value,
  onChange,
  onCommit,
  step,
  decimals = 3,
  units,
  min,
  max,
  pixelsPerStep = 4,
  w,
}: {
  label: string;
  value: number;
  /** live, while dragging (and on typing) */
  onChange: (v: number) => void;
  /** when the drag ends or a typed value is entered; defaults to onChange */
  onCommit?: (v: number) => void;
  step: number;
  decimals?: number;
  units?: string;
  min?: number;
  max?: number;
  pixelsPerStep?: number;
  w?: number | string;
}) {
  const [text, setText] = useState<string | number>(value);
  const [dragging, setDragging] = useState(false);
  const start = useRef<{ x: number; v: number; last: number } | null>(null);
  useEffect(() => {
    if (!dragging) setText(Number(value.toFixed(decimals)));
  }, [value, decimals, dragging]);

  const clamp = (v: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, v));
  const round = (v: number) => Number((Math.round(v / step) * step).toFixed(decimals));

  const down = (e: React.PointerEvent) => {
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    start.current = { x: e.clientX, v: value, last: value };
    setDragging(true);
  };
  const move = (e: React.PointerEvent) => {
    const s = start.current;
    if (!s) return;
    const f = e.shiftKey ? 10 : e.altKey ? 0.1 : 1;
    const v = clamp(round(s.v + ((e.clientX - s.x) / pixelsPerStep) * step * f));
    if (v !== s.last) {
      s.last = v;
      setText(v);
      onChange(v);
    }
  };
  const up = (e: React.PointerEvent) => {
    const s = start.current;
    start.current = null;
    setDragging(false);
    try {
      (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    if (s && s.last !== s.v) (onCommit ?? onChange)(s.last);
  };
  const commitTyped = () => {
    const n = typeof text === "number" ? text : parseFloat(String(text));
    if (Number.isFinite(n) && n !== value) (onCommit ?? onChange)(clamp(n));
  };

  return (
    <NumberInput
      size="xs"
      w={w}
      label={
        <span className="ab-scrub" onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
          title="Drag left or right to change (Shift: faster, Alt: finer)" data-dragging={dragging || undefined}>
          {label} <span aria-hidden className="ab-scrub-arrows">⇔</span>
        </span>
      }
      value={text}
      onChange={setText}
      onBlur={commitTyped}
      onKeyDown={(e) => e.key === "Enter" && commitTyped()}
      step={step}
      decimalScale={decimals}
      min={min}
      max={max}
      rightSection={units ? <Text size="xs" c="dimmed" pr={8}>{units}</Text> : undefined}
      rightSectionWidth={units ? Math.max(30, units.length * 8 + 14) : undefined}
    />
  );
}
