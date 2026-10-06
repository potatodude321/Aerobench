"""Readers for OpenFOAM ``postProcessing`` ``.dat`` files (structured result ingestion).

Function objects write ``postProcessing/<name>/<startTime>/<file>.dat``.  A restarted run
creates a new ``<startTime>`` directory; :func:`read_series` merges them in time order,
later files overriding earlier ones for overlapping times.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class DatTable:
    columns: list[str]
    rows: list[list]  # raw parsed values (float or str)

    def column(self, name: str) -> np.ndarray:
        i = self.columns.index(name)
        return np.array([r[i] if i < len(r) else math.nan for r in self.rows], dtype=float)

    def numeric(self) -> dict[str, np.ndarray]:
        out = {}
        for i, c in enumerate(self.columns):
            try:
                out[c] = np.array([float(r[i]) for r in self.rows], dtype=float)
            except (ValueError, TypeError, IndexError):
                continue
        return out

    def __len__(self) -> int:
        return len(self.rows)


_SPLIT = re.compile(r"\s+")


def _header_columns(line: str) -> list[str]:
    s = line.lstrip("#").strip()
    if "\t" in s:
        cols = [c.strip() for c in re.split(r"\t+", s) if c.strip()]
        # vector groups such as "total_x total_y total_z" share a tab-separated cell
        out = []
        for c in cols:
            out.extend(c.split())
        cols = out
    elif "#" in s:  # e.g. "# Time          # Local         # Global"
        cols = [c.strip() for c in line.split("#") if c.strip()]
    else:
        cols = s.split()
    return cols


def _value(tok: str):
    try:
        return float(tok)
    except ValueError:
        return tok


def parse_dat_text(text: str) -> DatTable:
    columns: list[str] = []
    rows: list[list] = []
    last_header = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("#"):
            last_header = line
            continue
        if not columns and last_header is not None:
            columns = _header_columns(last_header)
        line = line.replace("(", " ").replace(")", " ")
        rows.append([_value(t) for t in _SPLIT.split(line.strip())])
    if not columns and last_header is not None:
        columns = _header_columns(last_header)
    return DatTable(columns, rows)


def time_dirs(fo_dir: Path) -> list[Path]:
    out = []
    if not fo_dir.is_dir():
        return out
    for p in fo_dir.iterdir():
        if p.is_dir():
            try:
                out.append((float(p.name), p))
            except ValueError:
                continue
    return [p for _, p in sorted(out)]


def read_series(fo_dir: Path, filename: str) -> Optional[DatTable]:
    """Merge ``<fo_dir>/<t>/<filename>`` (and ``<stem>_N.dat`` variants) in time order."""
    merged: dict[float, list] = {}
    columns: list[str] = []
    for d in time_dirs(Path(fo_dir)):
        cands = sorted(d.glob(Path(filename).stem + "*.dat"), key=lambda p: (len(p.name), p.name))
        for f in cands:
            if f.name != filename and not re.match(re.escape(Path(filename).stem) + r"_\d+\.dat$", f.name):
                continue
            t = parse_dat_text(f.read_text(errors="replace"))
            if t.columns:
                columns = columns or t.columns
            for r in t.rows:
                if r and isinstance(r[0], float):
                    merged[r[0]] = r
    if not merged:
        return None
    return DatTable(columns, [merged[k] for k in sorted(merged)])


@dataclass
class DatTail:
    """Incremental reader for a growing .dat file (live monitoring)."""

    path: Path
    offset: int = 0
    columns: list[str] = field(default_factory=list)
    _partial: str = ""
    _last_header: Optional[str] = None

    def poll(self) -> list[list]:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:  # truncated / replaced
            self.offset, self._partial, self.columns = 0, "", []
        if size == self.offset:
            return []
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read(size - self.offset).decode("utf-8", errors="replace")
        self.offset = size
        text = self._partial + chunk
        lines = text.split("\n")
        self._partial = lines.pop()  # incomplete last line
        rows = []
        for line in lines:
            if not line.strip():
                continue
            if line.startswith("#"):
                self._last_header = line
                continue
            if not self.columns and self._last_header:
                self.columns = _header_columns(self._last_header)
            line = line.replace("(", " ").replace(")", " ")
            rows.append([_value(t) for t in _SPLIT.split(line.strip())])
        return rows


def read_yplus(fo_dir: Path) -> dict[str, dict[str, float]]:
    """Latest y+ statistics per patch: {patch: {min, max, average, time}}."""
    t = read_series_rows(fo_dir, "yPlus.dat")
    out: dict[str, dict[str, float]] = {}
    for r in t:
        if len(r) >= 5 and isinstance(r[1], str):
            out[r[1]] = {"time": float(r[0]), "min": float(r[2]), "max": float(r[3]), "average": float(r[4])}
    return out


def read_series_rows(fo_dir: Path, filename: str) -> list[list]:
    rows: list[list] = []
    for d in time_dirs(Path(fo_dir)):
        f = d / filename
        if f.exists():
            rows.extend(parse_dat_text(f.read_text(errors="replace")).rows)
    return rows
