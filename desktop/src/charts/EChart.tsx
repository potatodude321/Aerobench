import * as echarts from "echarts";
import { useEffect, useRef } from "react";

export const CHART_COLORS = ["#4dabf7", "#ff922b", "#51cf66", "#f06595", "#cc5de8", "#fcc419", "#22b8cf", "#94d82d", "#ff6b6b", "#845ef7"];

const base: echarts.EChartsOption = {
  backgroundColor: "transparent",
  textStyle: { color: "#c1c7d0", fontFamily: "Inter, system-ui, sans-serif" },
  color: CHART_COLORS,
  animation: false,
  grid: { left: 60, right: 20, top: 52, bottom: 40, containLabel: false },
  tooltip: { trigger: "axis", backgroundColor: "rgba(20,23,29,0.95)", borderColor: "#333a45", textStyle: { color: "#e9ecef" } },
  legend: { textStyle: { color: "#c1c7d0" }, top: 0, type: "scroll" },
};

const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);

/** Option-level merge onto the shared theme; component objects (legend, grid, tooltip…) are
 *  merged one level deep so a page that sets ``legend.data`` keeps the theme's placement. */
function mergeOption(option: echarts.EChartsOption): echarts.EChartsOption {
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(option)) {
    const b = (base as Record<string, unknown>)[k];
    out[k] = isObj(b) && isObj(v) ? { ...b, ...v } : v;
  }
  return out as echarts.EChartsOption;
}

export function EChart({ option, height = 260, onClick }: { option: echarts.EChartsOption; height?: number | string; onClick?: (p: any) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    chart.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => chart.current?.resize());
    ro.observe(ref.current);
    return () => {
      ro.disconnect();
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(mergeOption(option), { notMerge: true, lazyUpdate: true });
  }, [option]);

  useEffect(() => {
    const c = chart.current;
    if (!c || !onClick) return;
    c.on("click", onClick);
    return () => {
      c.off("click", onClick);
    };
  }, [onClick]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}

export const axisStyle = {
  axisLine: { lineStyle: { color: "#4a5260" } },
  splitLine: { lineStyle: { color: "#262c35" } },
  axisLabel: { color: "#9aa3ad" },
  nameTextStyle: { color: "#9aa3ad" },
};
