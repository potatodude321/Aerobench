/**
 * Shape-change view: the baseline surface coloured by how far each point moves normal to the
 * surface (red outwards, blue inwards, grey unchanged) with a slider that blends between the
 * baseline and the changed shape, so it is easy to see which parts were morphed and how.
 */
import { Alert, Group, Slider, Stack, Text } from "@mantine/core";
import { Abg } from "../viewport/abg";
import { Viewer } from "../viewport/Viewer";
import { ColorLegend, fmt } from "./ColorLegend";

export const MORPH_ID = "morph";

/** Show a morph payload (positions + displacement vectors) at blend ``t``; returns the colour range. */
export function showMorph(v: Viewer, abg: Abg, t: number): [number, number] {
  v.clearResults();
  v.setGeometry(null);
  const st = abg.header.stats || {};
  const m = Math.max(st.max_outward_mm || 0, st.max_inward_mm || 0, 0.5);
  const range: [number, number] = [-m, m];
  v.setResult(MORPH_ID, abg, { colormap: "Cool to Warm", range });
  v.morphResult(MORPH_ID, t);
  return range;
}

export function MorphControls({
  header,
  range,
  t,
  onT,
  title = "Surface movement",
  compact = false,
}: {
  header: any | null;
  range: [number, number];
  t: number;
  onT: (t: number) => void;
  title?: string;
  /** small viewports: blend and figures stacked top-left */
  compact?: boolean;
}) {
  if (!header) return null;
  const st = header.stats || {};
  const chk = header.check;
  // compact: small viewports, whose toolbar takes the top right: narrow boxes at the top left
  const blendPos = compact ? { left: 12, top: 12, width: 200 } : { right: 12, bottom: 12, width: 300 };
  const statsPos = compact ? { left: 12, top: 74, width: 200 } : { right: 12, bottom: 80, width: 300 };
  return (
    <>
      <ColorLegend title={`${title}, + outwards`} colormap="Cool to Warm" range={range} units="mm" />
      <div className="ab-float" style={{ ...blendPos, padding: "8px 12px" }}>
        <Group justify="space-between" mb={2}>
          <Text size="xs" c="dimmed">
            Baseline
          </Text>
          <Text size="xs" fw={500}>
            {Math.round(t * 100)} %{compact ? "" : " of the change"}
          </Text>
          <Text size="xs" c="dimmed">
            Changed
          </Text>
        </Group>
        <Slider size="sm" min={0} max={1} step={0.01} value={t} onChange={onT} label={null} thumbLabel="Blend between the baseline and the changed shape" />
      </div>
      <div className="ab-float" style={{ ...statsPos, padding: "8px 10px" }}>
        <Stack gap={2}>
          <Text size="xs">
            {fmt(st.moved_area_pct, 1)} % of the surface moves, by up to {fmt(st.max_movement_mm, 1)} mm
          </Text>
          <Text size="xs" c="dimmed">
            Shape: up to {fmt(st.max_outward_mm, 1)} mm outwards and {fmt(st.max_inward_mm, 1)} mm inwards
          </Text>
          {(header.notes || []).map((n: string, i: number) => (
            <Text key={i} size="xs" c="yellow">
              {n}
            </Text>
          ))}
          {chk && chk.valid === false && (
            <Alert color="red" p={6} mt={4} title="Not a valid shape">
              {chk.errors.map((e: any, i: number) => (
                <Text key={i} size="xs">
                  {e.message}
                </Text>
              ))}
            </Alert>
          )}
          {chk && chk.valid === true && (
            <Text size="xs" c="teal">
              Valid shape: no folds, intersections or ground contact
            </Text>
          )}
          {(chk?.warnings || []).map((w: any, i: number) => (
            <Text key={i} size="xs" c="yellow">
              {w.message}
            </Text>
          ))}
        </Stack>
      </div>
    </>
  );
}
