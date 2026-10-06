import { useMemo } from "react";
import { axisStyle, EChart } from "./EChart";

export function ResidualChart({ series, height = 260 }: { series: any; height?: number }) {
  const option = useMemo(() => {
    const r = series?.residuals;
    if (!r) return { title: { text: "No residuals yet", textStyle: { color: "#7c8592", fontSize: 12 }, left: "center", top: "middle" } };
    const keys = Object.keys(r).filter((k) => k !== "time");
    return {
      tooltip: { trigger: "axis", valueFormatter: (v: number) => (v == null ? "—" : Number(v).toExponential(2)) },
      legend: { data: keys },
      xAxis: { type: "value", name: "iteration", nameLocation: "middle", nameGap: 24, min: "dataMin", ...axisStyle },
      yAxis: { type: "log", name: "initial residual", ...axisStyle, axisLabel: { ...axisStyle.axisLabel, formatter: (v: number) => v.toExponential(0) } },
      dataZoom: [{ type: "inside" }],
      series: keys.map((k) => ({ name: k, type: "line", showSymbol: false, lineStyle: { width: 1.2 }, data: r.time.map((t: number, i: number) => [t, r[k][i]]) })),
    };
  }, [series]);
  return <EChart option={option as any} height={height} />;
}

export function CoefficientChart({ series, keys = ["Cd", "Cl"], window, height = 260 }: { series: any; keys?: string[]; window?: [number, number] | null; height?: number }) {
  const option = useMemo(() => {
    const f = series?.forces;
    if (!f) return { title: { text: "No force data yet", textStyle: { color: "#7c8592", fontSize: 12 }, left: "center", top: "middle" } };
    const present = keys.filter((k) => f[k]);
    return {
      tooltip: { trigger: "axis", valueFormatter: (v: number) => (v == null ? "—" : Number(v).toFixed(4)) },
      legend: { data: present },
      xAxis: { type: "value", name: "iteration", nameLocation: "middle", nameGap: 24, min: "dataMin", ...axisStyle },
      yAxis: { type: "value", scale: true, ...axisStyle },
      dataZoom: [{ type: "inside" }],
      series: present.map((k, idx) => ({
        name: k,
        type: "line",
        showSymbol: false,
        lineStyle: { width: 1.4 },
        data: f.time.map((t: number, i: number) => [t, f[k][i]]),
        markArea:
          idx === 0 && window
            ? { silent: true, itemStyle: { color: "rgba(34,184,207,0.08)" }, label: { color: "#7fd3e0", textBorderWidth: 0, fontSize: 11 }, data: [[{ xAxis: window[0], name: "averaging window" }, { xAxis: window[1] }]] }
            : undefined,
      })),
    };
  }, [series, keys.join(), window?.[0], window?.[1]]);
  return <EChart option={option as any} height={height} />;
}
