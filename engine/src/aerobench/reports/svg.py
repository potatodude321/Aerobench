"""Minimal, dependency-free SVG charts for self-contained reports (print friendly)."""

from __future__ import annotations

import math
from html import escape
from typing import Optional, Sequence

PALETTE = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if not math.isfinite(lo) or not math.isfinite(hi):
        return []
    if hi == lo:
        hi = lo + (abs(lo) or 1.0) * 0.1
        lo = lo - (abs(lo) or 1.0) * 0.1
    raw = (hi - lo) / max(n, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = min((s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw), default=raw)
    start = math.floor(lo / step) * step
    out = []
    v = start
    while v <= hi + step * 1e-9:
        if v >= lo - step * 1e-9:
            out.append(round(v, 12))
        v += step
    return out


def _fmt(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e4 or a < 1e-3:
        return f"{v:.0e}" if a < 1e-3 or a >= 1e5 else f"{v:.0f}"
    return f"{v:.4g}"


class _Frame:
    def __init__(self, width: int, height: int, xlo: float, xhi: float, ylo: float, yhi: float, logy: bool = False,
                 left: int = 64, right: int = 16, top: int = 30, bottom: int = 44):
        self.w, self.h = width, height
        self.l, self.r, self.t, self.b = left, right, top, bottom
        self.logy = logy
        if logy:
            ylo, yhi = math.log10(max(ylo, 1e-300)), math.log10(max(yhi, 1e-300))
        if xhi == xlo:
            xhi = xlo + 1
        if yhi == ylo:
            yhi = ylo + (abs(ylo) or 1) * 0.1
            ylo = ylo - (abs(ylo) or 1) * 0.1
        self.xlo, self.xhi, self.ylo, self.yhi = xlo, xhi, ylo, yhi

    def x(self, v: float) -> float:
        return self.l + (v - self.xlo) / (self.xhi - self.xlo) * (self.w - self.l - self.r)

    def y(self, v: float) -> float:
        if self.logy:
            v = math.log10(max(v, 1e-300))
        return self.h - self.b - (v - self.ylo) / (self.yhi - self.ylo) * (self.h - self.t - self.b)


def _axes(fr: _Frame, xlabel: str, ylabel: str, title: str, xticks: Optional[Sequence[tuple[float, str]]] = None) -> list[str]:
    s = [f'<rect x="{fr.l}" y="{fr.t}" width="{fr.w - fr.l - fr.r}" height="{fr.h - fr.t - fr.b}" fill="none" stroke="#999" stroke-width="0.8"/>']
    if fr.logy:
        for e in range(math.floor(fr.ylo), math.ceil(fr.yhi) + 1):
            if fr.ylo - 1e-9 <= e <= fr.yhi + 1e-9:
                yy = fr.y(10.0**e)
                s.append(f'<line x1="{fr.l}" x2="{fr.w - fr.r}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e5e5e5"/>')
                s.append(f'<text x="{fr.l - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="10">1e{e}</text>')
    else:
        for t in _nice_ticks(fr.ylo, fr.yhi):
            yy = fr.y(t)
            s.append(f'<line x1="{fr.l}" x2="{fr.w - fr.r}" y1="{yy:.1f}" y2="{yy:.1f}" stroke="#e5e5e5"/>')
            s.append(f'<text x="{fr.l - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="10">{_fmt(t)}</text>')
    ticks = xticks if xticks is not None else [(t, _fmt(t)) for t in _nice_ticks(fr.xlo, fr.xhi, 6)]
    for t, lab in ticks:
        xx = fr.x(t)
        s.append(f'<line x1="{xx:.1f}" x2="{xx:.1f}" y1="{fr.h - fr.b}" y2="{fr.h - fr.b + 4}" stroke="#999"/>')
        s.append(f'<text x="{xx:.1f}" y="{fr.h - fr.b + 16}" text-anchor="middle" font-size="10">{escape(lab)}</text>')
    s.append(f'<text x="{(fr.l + fr.w - fr.r) / 2:.1f}" y="{fr.h - 6}" text-anchor="middle" font-size="11">{escape(xlabel)}</text>')
    s.append(f'<text transform="translate(14,{(fr.t + fr.h - fr.b) / 2:.1f}) rotate(-90)" text-anchor="middle" font-size="11">{escape(ylabel)}</text>')
    if title:
        s.append(f'<text x="{fr.l}" y="18" font-size="12" font-weight="600">{escape(title)}</text>')
    return s


def _legend(fr: _Frame, names: list[str]) -> list[str]:
    out = []
    x = fr.w - fr.r - 4
    for i, n in reversed(list(enumerate(names))):
        w = 7 * len(n) + 22
        x -= w
        c = PALETTE[i % len(PALETTE)]
        out.append(f'<rect x="{x:.0f}" y="8" width="12" height="3" fill="{c}"/><text x="{x + 16:.0f}" y="13" font-size="10">{escape(n)}</text>')
    return out


def _wrap(width: int, height: int, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" '
            f'style="max-width:{width}px;font-family:Helvetica,Arial,sans-serif" role="img">' + "".join(body) + "</svg>")


def line_chart(series: dict[str, tuple[Sequence[float], Sequence[float]]], title: str = "", xlabel: str = "", ylabel: str = "",
               logy: bool = False, width: int = 640, height: int = 280, band: Optional[tuple[float, float]] = None,
               ylim: Optional[tuple[float, float]] = None) -> str:
    pts = {k: [(float(x), float(y)) for x, y in zip(*v) if y is not None and math.isfinite(float(y)) and (not logy or float(y) > 0)]
           for k, v in series.items()}
    pts = {k: v for k, v in pts.items() if v}
    if not pts:
        return _wrap(width, 60, [f'<text x="10" y="30" font-size="12">{escape(title)}: no data</text>'])
    xs = [p[0] for v in pts.values() for p in v]
    ys = [p[1] for v in pts.values() for p in v]
    ylo, yhi = ylim if ylim else (min(ys), max(ys))
    if not ylim and not logy:
        pad = (yhi - ylo) * 0.05 or abs(yhi) * 0.05 or 1.0
        ylo, yhi = ylo - pad, yhi + pad
    fr = _Frame(width, height, min(xs), max(xs), ylo, yhi, logy)
    body = []
    if band:
        x0, x1 = fr.x(max(band[0], fr.xlo)), fr.x(min(band[1], fr.xhi))
        body.append(f'<rect x="{x0:.1f}" y="{fr.t}" width="{max(x1 - x0, 0):.1f}" height="{fr.h - fr.t - fr.b}" fill="#1f77b4" opacity="0.08"/>')
    body += _axes(fr, xlabel, ylabel, title)
    for i, (name, p) in enumerate(pts.items()):
        if ylim:
            p = [(x, min(max(y, ylo), yhi)) for x, y in p]
        d = " ".join(f"{'M' if j == 0 else 'L'}{fr.x(x):.1f},{fr.y(y):.1f}" for j, (x, y) in enumerate(p))
        body.append(f'<path d="{d}" fill="none" stroke="{PALETTE[i % len(PALETTE)]}" stroke-width="1.3"/>')
    if len(pts) > 1:
        body += _legend(fr, list(pts))
    return _wrap(width, height, body)


def bar_chart(categories: list[str], series: dict[str, list[Optional[float]]], title: str = "", ylabel: str = "",
              width: int = 640, height: int = 280) -> str:
    vals = [v for s in series.values() for v in s if v is not None and math.isfinite(v)]
    if not vals:
        return _wrap(width, 60, [f'<text x="10" y="30" font-size="12">{escape(title)}: no data</text>'])
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    pad = (hi - lo) * 0.08 or 1.0
    fr = _Frame(width, height, 0, len(categories), lo - (pad if lo < 0 else 0), hi + pad)
    body = _axes(fr, "", ylabel, title, xticks=[(i + 0.5, c) for i, c in enumerate(categories)])
    y0 = fr.y(0.0)
    body.append(f'<line x1="{fr.l}" x2="{fr.w - fr.r}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="#555"/>')
    n = max(len(series), 1)
    bw = 0.8 / n
    for si, (name, vs) in enumerate(series.items()):
        for ci, v in enumerate(vs):
            if v is None or not math.isfinite(v):
                continue
            x0 = fr.x(ci + 0.1 + si * bw)
            x1 = fr.x(ci + 0.1 + (si + 1) * bw)
            yv = fr.y(v)
            body.append(f'<rect x="{x0:.1f}" y="{min(y0, yv):.1f}" width="{max(x1 - x0 - 1, 1):.1f}" height="{abs(yv - y0):.1f}" '
                        f'fill="{PALETTE[si % len(PALETTE)]}"><title>{escape(name)}: {v:.5g}</title></rect>')
    if len(series) > 1:
        body += _legend(fr, list(series))
    return _wrap(width, height, body)


def scatter_chart(points: list[tuple[float, float, str]], highlight: Optional[set[str]] = None, title: str = "", xlabel: str = "",
                  ylabel: str = "", width: int = 520, height: int = 360, connect_highlight: bool = False) -> str:
    pts = [(x, y, k) for x, y, k in points if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)]
    if not pts:
        return _wrap(width, 60, [f'<text x="10" y="30" font-size="12">{escape(title)}: no data</text>'])
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    px = (max(xs) - min(xs)) * 0.06 or abs(xs[0]) * 0.05 or 1
    py = (max(ys) - min(ys)) * 0.06 or abs(ys[0]) * 0.05 or 1
    fr = _Frame(width, height, min(xs) - px, max(xs) + px, min(ys) - py, max(ys) + py)
    body = _axes(fr, xlabel, ylabel, title)
    hl = highlight or set()
    if connect_highlight:
        h = sorted([(x, y) for x, y, k in pts if k in hl])
        if len(h) > 1:
            d = " ".join(f"{'M' if j == 0 else 'L'}{fr.x(x):.1f},{fr.y(y):.1f}" for j, (x, y) in enumerate(h))
            body.append(f'<path d="{d}" fill="none" stroke="#d62728" stroke-width="1" stroke-dasharray="4 3"/>')
    for x, y, k in pts:
        c = "#d62728" if k in hl else "#1f77b4"
        r = 4.5 if k in hl else 3.2
        body.append(f'<circle cx="{fr.x(x):.1f}" cy="{fr.y(y):.1f}" r="{r}" fill="{c}" fill-opacity="0.8"><title>{escape(k)}</title></circle>')
    return _wrap(width, height, body)
