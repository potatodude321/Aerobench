import * as echarts from "echarts";
import { useEffect, useRef } from "react";

export const CHART_COLORS = ["#8db7e0", "#d6a24a", "#74b98f", "#c98bb0", "#a99ad6", "#d9c27a", "#6fb0ae", "#e08a7a", "#9aa3ae", "#b5c77d"];

const base: echarts.EChartsOption = {
  backgroundColor: "transparent",
  textStyle: { color: "#a1a9b4", fontFamily: "Barlow, 'Segoe UI', system-ui, sans-serif", fontSize: 12 },
  color: CHART_COLORS,
  animation: false,
  grid: { left: 60, right: 20, top: 52, bottom: 40, containLabel: false },
  tooltip: { trigger: "axis", backgroundColor: "rgba(27,31,37,0.96)", borderColor: "#2d333c", textStyle: { color: "#e3e6ea", fontFamily: "Barlow, sans-serif" } },
  legend: { textStyle: { color: "#a1a9b4" }, top: 0, type: "scroll", icon: "roundRect", itemWidth: 12, itemHeight: 3 },
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
  axisLine: { lineStyle: { color: "#39404a" } },
  splitLine: { lineStyle: { color: "#242930" } },
  axisLabel: { color: "#8b939e" },
  nameTextStyle: { color: "#6c7580" },
};
