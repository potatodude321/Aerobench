"""Live progress of meshing and solver jobs: current step, elapsed time, iterations,
time per iteration, estimated time to completion and convergence measured so far.

Everything is read from files the runner and OpenFOAM write anyway (job state, step logs,
``postProcessing``), so it works for jobs started by an earlier session or by the setup.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..domain.project import ProjectConfig
from ..jobs.manager import JobManager
from ..project.db import dec
from ..project.store import Project

TAIL_BYTES = 512_000
SOLVER_STEPS = ("solve_first_order", "solve")
_TIME = re.compile(r"^Time = ([0-9.eE+-]+)\s*$", re.M)
_CLOCK = re.compile(r"ExecutionTime = ([0-9.eE+-]+) s\s+ClockTime = ([0-9.eE+-]+) s")
# snappyHexMesh phase banners, in order
_SNAPPY_PHASES = (
    ("Refinement phase", "Refining the background mesh"),
    ("Surface refinement iteration", "Refining near the surfaces"),
    ("Shell refinement iteration", "Refining the refinement boxes"),
    ("Removing mesh beyond surface intersections", "Removing cells inside the vehicle"),
    ("Snapping to features", "Snapping to the surface"),
    ("Morph iteration", "Snapping to the surface"),
    ("Shrinking and moving mesh", "Adding boundary layers"),
    ("Layer addition iteration", "Adding boundary layers"),
    ("Checking final mesh", "Checking the mesh"),
)


def _iso(t: Optional[str]) -> Optional[float]:
    if not t:
        return None
    try:
        return datetime.fromisoformat(t).timestamp()
    except ValueError:
        return None


def _tail(path: Path, n: int = TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def solver_timing(log_text: str) -> dict[str, Any]:
    """Current iteration and wall seconds per iteration from an OpenFOAM solver log.

    Each iteration prints ``Time = k`` and, at its end, ``ExecutionTime = … ClockTime = …``.
    The rate is the clock time over the last (up to) 30 iterations, so it follows changes
    such as the switch to second-order schemes."""
    pairs: list[tuple[float, float, float]] = []  # (iteration, execution time, clock time)
    cur_iter: Optional[float] = None
    times = [(m.start(), float(m.group(1))) for m in _TIME.finditer(log_text)]
    clocks = [(m.start(), float(m.group(1)), float(m.group(2))) for m in _CLOCK.finditer(log_text)]
    ti = 0
    for cpos, exe, clock in clocks:
        while ti < len(times) and times[ti][0] < cpos:
            cur_iter = times[ti][1]
            ti += 1
        if cur_iter is not None:
            pairs.append((cur_iter, exe, clock))
    last_iter = times[-1][1] if times else None
    out: dict[str, Any] = {"iteration": last_iter, "seconds_per_iteration": None}
    if len(pairs) >= 2:
        recent = pairs[-31:]
        di = recent[-1][0] - recent[0][0]
        dc = recent[-1][2] - recent[0][2]
        de = recent[-1][1] - recent[0][1]
        if di > 0:
            # ClockTime has a resolution of 1 s: use it once it spans enough time
            rate = dc / di if dc >= 5 else de / di
            if rate >= 0:
                out["seconds_per_iteration"] = rate
    return out


def snappy_phase(log_text: str) -> Optional[str]:
    best, best_pos = None, -1
    for marker, label in _SNAPPY_PHASES:
        p = log_text.rfind(marker)
        if p > best_pos:
            best, best_pos = label, p
    return best


_conv_cache: dict[str, tuple[tuple, dict]] = {}


def live_convergence(case_dir: Path, cfg: ProjectConfig, ground_z: float) -> Optional[dict[str, Any]]:
    """The run's convergence criteria evaluated on the iterations so far (cached by the
    size and age of the force and residual files)."""
    from ..monitoring.convergence import assess
    from ..results.ingest import coefficient_series, load_force_history, reference_spec, residual_series

    pp = case_dir / "postProcessing"
    files = sorted(pp.rglob("*.dat")) if pp.exists() else []
    key = tuple((str(f), f.stat().st_size, f.stat().st_mtime_ns) for f in files)
    hit = _conv_cache.get(str(case_dir))
    if hit and hit[0] == key:
        return hit[1]
    ref = reference_spec(cfg, ground_z)
    h = load_force_history(case_dir, default_cofr=ref.moment_centre)
    if h is None or len(h.time) == 0:
        return None
    series = coefficient_series(h, ref)
    res = residual_series(case_dir)
    a = assess(h.time, {k: series[k] for k in ("Cd", "Cl", "Cl_front", "Cl_rear") if np.all(np.isfinite(series[k]))},
               {k: v for k, v in res.items() if k != "Time"}, None, None, cfg.numerics.averaging_fraction, cfg.numerics.iterations)
    d = a.as_dict()
    d["met"] = sum(1 for c in a.criteria if c.passed)
    d["total"] = sum(1 for c in a.criteria if c.passed is not None)
    _conv_cache[str(case_dir)] = (key, d)
    if len(_conv_cache) > 64:
        _conv_cache.pop(next(iter(_conv_cache)))
    return d


def job_progress(project: Project, job_id: str) -> dict[str, Any]:
    jm = JobManager(project)
    row = jm.row(job_id)
    st = jm.state(job_id)
    now = time.time()
    out: dict[str, Any] = {"job_id": job_id, "kind": row["kind"] if row else None, "status": row["status"] if row else None}
    if st is None:
        return out
    started = _iso(st.started_at)
    ended = _iso(st.ended_at)
    out["elapsed_seconds"] = ((ended or now) - started) if started else None
    out["step_index"] = st.step_index
    out["step_count"] = len(st.steps)
    step = st.steps[st.step_index] if 0 <= st.step_index < len(st.steps) else None
    out["step"] = step.name if step else None
    out["step_elapsed_seconds"] = (now - _iso(step.started_at)) if step and step.started_at and step.status == "running" else None
    if step and step.log:
        text = _tail(project.job_dir(job_id) / step.log, 64_000)
        if step.name == "snappyHexMesh":
            out["phase"] = snappy_phase(text)
    return out


def run_progress(project: Project, run_id: str) -> dict[str, Any]:
    """Progress of a run: job steps, iteration k of N, seconds per iteration, ETA and the
    convergence criteria on the iterations so far."""
    r = dec(project.db.one("SELECT * FROM runs WHERE id = ?", (run_id,)))
    if r is None:
        return {}
    cfg = ProjectConfig.model_validate(r["config_snapshot"])
    out: dict[str, Any] = {"run_id": run_id, "status": r["status"], "target_iterations": cfg.numerics.iterations,
                           "stop_when_converged": cfg.numerics.stop_when_converged}
    if not r.get("job_id"):
        return out
    jm = JobManager(project)
    out["job"] = job_progress(project, r["job_id"])
    row = jm.row(r["job_id"])
    if row and row.get("depends_on"):
        dep = jm.row(row["depends_on"])
        if dep and dep["status"] in ("queued", "starting", "running"):
            out["waiting_for"] = {"kind": dep["kind"], "job_id": dep["id"], "progress": job_progress(project, dep["id"])}
    st = jm.state(r["job_id"])
    if st is not None:
        steps = [s for s in st.steps if s.name in SOLVER_STEPS and s.log]
        active = next((s for s in reversed(steps) if s.status in ("running", "completed", "stopped", "failed")), None)
        if active is not None:
            t = solver_timing(_tail(project.job_dir(r["job_id"]) / active.log))
            out.update({"iteration": t["iteration"], "seconds_per_iteration": t["seconds_per_iteration"]})
            it, spi = t["iteration"], t["seconds_per_iteration"]
            if it is not None and spi and r["status"] in ("running", "starting", "stopping") and active.status == "running":
                out["eta_seconds"] = max(0.0, (cfg.numerics.iterations - it) * spi)
        if r["status"] in ("running", "starting", "stopping", "stopped", "interrupted"):
            try:
                ground = (r.get("resolved") or {}).get("ground_z", 0.0)
                out["convergence"] = live_convergence(project.root / r["path"], cfg, ground)
            except Exception:  # noqa: BLE001 - partially written files: try again on the next poll
                out["convergence"] = None
    return out
