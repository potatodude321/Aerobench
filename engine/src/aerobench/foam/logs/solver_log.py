"""Incremental OpenFOAM solver-log parser (residuals, continuity, timing, errors)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Optional

_TIME = re.compile(r"^Time = ([-+\deE.]+)\s*$")
_SOLVE = re.compile(
    r"^(\w+):\s+Solving for (\w+), Initial residual = ([^,]+), Final residual = ([^,]+), No Iterations (\d+)"
)
_CONT = re.compile(r"time step continuity errors : sum local = ([^,]+), global = ([^,]+), cumulative = (\S+)")
_EXEC = re.compile(r"^ExecutionTime = ([\d.eE+-]+) s\s+ClockTime = ([\d.eE+-]+) s")
_BOUNDING = re.compile(r"^bounding (\w+), min: (\S+) max: (\S+) average: (\S+)")
_FATAL_START = re.compile(r"--> FOAM FATAL (IO )?ERROR")
_FATAL_END = re.compile(r"FOAM (exiting|aborting)")


def _f(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return math.nan


@dataclass
class IterationRecord:
    time: float
    residuals: dict[str, float] = field(default_factory=dict)  # initial residuals (first solve per field)
    final: dict[str, float] = field(default_factory=dict)
    iterations: dict[str, int] = field(default_factory=dict)
    continuity: Optional[tuple[float, float, float]] = None
    exec_time: Optional[float] = None
    clock_time: Optional[float] = None


@dataclass
class SolverLogState:
    records: list[IterationRecord] = field(default_factory=list)
    fatal: Optional[str] = None
    bounding_counts: dict[str, int] = field(default_factory=dict)
    finished: bool = False
    nan_detected: bool = False
    warnings: int = 0
    _cur: Optional[IterationRecord] = None
    _in_fatal: bool = False
    _fatal_lines: list[str] = field(default_factory=list)
    _partial: str = ""

    def feed(self, chunk: str) -> list[IterationRecord]:
        """Feed new log text; returns completed iteration records."""
        text = self._partial + chunk
        lines = text.split("\n")
        self._partial = lines.pop()
        done: list[IterationRecord] = []
        for raw in lines:
            line = raw.rstrip("\r")
            if self._in_fatal:
                self._fatal_lines.append(line)
                if _FATAL_END.search(line):
                    self._in_fatal = False
                    self.fatal = "\n".join(self._fatal_lines).strip()
                continue
            if _FATAL_START.search(line):
                self._in_fatal = True
                self._fatal_lines = [line]
                continue
            m = _TIME.match(line)
            if m:
                if self._cur is not None:
                    done.append(self._cur)
                    self.records.append(self._cur)
                self._cur = IterationRecord(time=_f(m.group(1)))
                continue
            if self._cur is None:
                continue
            m = _SOLVE.search(line)
            if m:
                fld = m.group(2)
                ini, fin = _f(m.group(3)), _f(m.group(4))
                if math.isnan(ini) or math.isnan(fin) or "nan" in line.lower():
                    self.nan_detected = True
                if fld not in self._cur.residuals:  # first solve of the field in this iteration
                    self._cur.residuals[fld] = ini
                    self._cur.iterations[fld] = int(m.group(5))
                self._cur.final[fld] = fin
                continue
            m = _CONT.search(line)
            if m:
                self._cur.continuity = (_f(m.group(1)), _f(m.group(2)), _f(m.group(3)))
                continue
            m = _EXEC.match(line)
            if m:
                self._cur.exec_time = _f(m.group(1))
                self._cur.clock_time = _f(m.group(2))
                continue
            m = _BOUNDING.match(line)
            if m:
                self.bounding_counts[m.group(1)] = self.bounding_counts.get(m.group(1), 0) + 1
                continue
            if line.startswith("--> FOAM Warning"):
                self.warnings += 1
            if line.strip() == "End":
                self.finished = True
                if self._cur is not None:
                    done.append(self._cur)
                    self.records.append(self._cur)
                    self._cur = None
        return done

    def flush(self) -> None:
        if self._cur is not None:
            self.records.append(self._cur)
            self._cur = None
        if self._in_fatal and self._fatal_lines:
            self.fatal = "\n".join(self._fatal_lines).strip()


def parse_solver_log(text: str) -> SolverLogState:
    st = SolverLogState()
    st.feed(text + "\n")
    st.flush()
    return st
