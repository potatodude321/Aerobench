"""Validation benchmarks (BUILD_SPEC validation ladder; docs/validation.md).

Each benchmark builds a complete project from a geometry the application can generate
itself (no third-party CAD is redistributed), runs it through the normal pipeline and
compares the results with published measurements.  Results are written next to the project
as JSON so a validation record can be archived; nothing is tuned to hit a reference.

Reference values carry their source.  Where experiments disagree, the reference is a range.
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from ..core.ids import utc_now_iso
from ..domain.geometry import ComponentRole
from ..domain.mesh import LayerConfig, LayerMode, MeshConfig, MeshPreset, SurfaceRefinement
from ..domain.project import ProjectConfig
from ..domain.setup import AreaSource, GroundMode
from ..domain.vehicle import VehicleCategory
from ..version import APP_VERSION


@dataclass
class Reference:
    quantity: str
    value: Optional[float] = None
    low: Optional[float] = None
    high: Optional[float] = None
    source: str = ""
    notes: str = ""


@dataclass
class BenchmarkCase:
    id: str
    name: str
    description: str
    geometry: Callable[[], Any]
    configure: Callable[[ProjectConfig, str], None]
    references: list[Reference]
    acceptance: dict[str, tuple[float, float]] = field(default_factory=dict)  # quantity -> (lo, hi) band
    qualitative: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "description": self.description, "references": [asdict(r) for r in self.references],
                "acceptance": {k: list(v) for k, v in self.acceptance.items()}, "qualitative": self.qualitative, "caveats": self.caveats}


# ---------------------------------------------------------------------------- Ahmed body

AHMED_LEVELS = {
    # background cell, body surface level (min, max), wake/near-body refinement handled by the automatic regions
    "coarse": (0.16, (4, 5)),
    "medium": (0.12, (5, 6)),
    "fine": (0.08, (5, 6)),
}


def _ahmed_geometry(slant: float):
    from ..geometry.fixtures import ahmed_body

    return lambda: ahmed_body(slant_deg=slant, stilts=True)


def _ahmed_configure(cfg: ProjectConfig, level: str) -> None:
    base, (lo, hi) = AHMED_LEVELS[level]
    cfg.vehicle.category = VehicleCategory.BLUFF_BODY
    cfg.operating.speed = 40.0
    cfg.operating.yaw_deg = 0.0
    cfg.operating.ground = GroundMode.STATIONARY
    cfg.operating.wheels_rotate = False
    # Experiments: open-jet/closed test section with a ground plate; a generous domain keeps
    # blockage below 1% so no correction is applied.
    cfg.tunnel = cfg.tunnel.model_copy(update={"upstream": 2.5, "downstream": 5.0, "lateral": 2.0, "top": 2.0})
    cfg.mesh = MeshConfig(
        preset=MeshPreset.CUSTOM, base_cell_size=base, auto_regions=True,
        surface=[SurfaceRefinement(role=ComponentRole.BODY, min_level=lo, max_level=hi, feature_level=hi),
                 SurfaceRefinement(role=ComponentRole.STILT, min_level=lo, max_level=hi)],
        layers=LayerConfig(mode=LayerMode.RELATIVE, n_layers=3, expansion_ratio=1.2, final_relative=0.5),
    )
    cfg.numerics.iterations = {"coarse": 1200, "medium": 2000, "fine": 3000}[level]
    # reference area: the body's frontal area W x H (stilts excluded), as in the experiments
    cfg.reference.area = 0.389 * 0.288
    cfg.reference.area_source = AreaSource.MANUAL
    cfg.reference.length = 1.044
    cfg.reference.length_source = "manual"


BENCHMARKS: dict[str, BenchmarkCase] = {}


def _register(c: BenchmarkCase) -> None:
    BENCHMARKS[c.id] = c


_register(BenchmarkCase(
    id="ahmed25",
    name="Ahmed body, 25° slant",
    description="Canonical bluff-body validation (Re_L ≈ 2.8e6, 40 m/s, stationary ground, stilts). The 25° slant sits just below the "
                "critical angle with a partially attached slant flow — a demanding case for steady RANS.",
    geometry=_ahmed_geometry(25.0),
    configure=_ahmed_configure,
    references=[
        Reference("Cd", value=0.285, source="Ahmed, Ramm & Faltin, SAE 840300 (1984)", notes="Original measurement"),
        Reference("Cd", value=0.299, source="Meile et al. (2011), 25° slant at 40 m/s", notes="Later measurement in a different facility"),
    ],
    acceptance={"Cd": (0.25, 0.34)},
    caveats=["Steady k-ω SST is known to over-predict the 25° slant separation (higher Cd); results within the band are consistent with "
             "published RANS studies, not proof of accuracy for every configuration.",
             "Stilt positions in the generated geometry are approximate; stilt drag is a few percent of the total."],
))

_register(BenchmarkCase(
    id="ahmed35",
    name="Ahmed body, 35° slant",
    description="Above the critical slant angle (~30°) the slant flow separates fully and drag drops below the 25° value; checks that "
                "the solver reproduces the regime change (run together with ahmed25).",
    geometry=_ahmed_geometry(35.0),
    configure=_ahmed_configure,
    references=[Reference("Cd", source="Ahmed, Ramm & Faltin, SAE 840300 (1984)",
                          notes="Qualitative: Cd(35°) < Cd(25°) (drag falls above the critical angle)")],
    qualitative=["Cd(35°) < Cd(25°) when both are run at the same level"],
))


def list_benchmarks() -> list[dict[str, Any]]:
    return [c.describe() for c in BENCHMARKS.values()]


# ---------------------------------------------------------------------------- running


def build_benchmark_project(case_id: str, root: Path, level: str = "medium", cores: Optional[int] = None):
    from ..geometry.io import write_stl
    from ..project.store import Project
    from ..services.geometry import import_geometry, refresh_derived

    case = BENCHMARKS[case_id]
    if level not in AHMED_LEVELS:
        raise ValueError(f"unknown level '{level}' (coarse, medium, fine)")
    p = Project.create(root, f"Benchmark {case.name} ({level})")
    with tempfile.TemporaryDirectory() as td:
        stl = Path(td) / f"{case_id}.stl"
        write_stl(stl, case.geometry())
        import_geometry(p, stl, units="m", label=f"{case.name} (generated)")

    def mutate(c: ProjectConfig) -> None:
        case.configure(c, level)
        if cores:
            c.execution.n_procs = cores
        c.notes = f"Validation benchmark {case_id} at level {level}; generated by {APP_VERSION}."

    p.update(mutate)
    refresh_derived(p)
    p.update(lambda c: case.configure(c, level))  # re-apply the manual reference after derivation
    return p


def evaluate(case_id: str, run: dict[str, Any]) -> dict[str, Any]:
    case = BENCHMARKS[case_id]
    m = ((run.get("metrics") or {}).get("metrics")) or {}
    conv = (run.get("convergence") or {}).get("status")
    checks = []
    for q, (lo, hi) in case.acceptance.items():
        v = m.get(q)
        ok = v is not None and math.isfinite(v) and lo <= v <= hi
        checks.append({"quantity": q, "value": v, "band": [lo, hi], "within_band": ok})
    comparisons = []
    for r in case.references:
        v = m.get(r.quantity)
        if r.value is not None and v is not None:
            comparisons.append({"quantity": r.quantity, "value": v, "reference": r.value, "source": r.source,
                                "difference_pct": 100.0 * (v - r.value) / abs(r.value)})
    status = "not_run" if run.get("status") != "completed" else ("unconverged" if conv not in ("converged", "converged_oscillating")
                                                                    else ("pass" if all(c["within_band"] for c in checks) else "outside_band"))
    return {"case": case_id, "status": status, "convergence": conv, "metrics": m, "checks": checks, "comparisons": comparisons,
            "qualitative": case.qualitative, "caveats": case.caveats}


def run_benchmark(case_id: str, out_dir: Path, level: str = "medium", cores: Optional[int] = None, ctx=None,
                  progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Build, mesh, solve and evaluate a benchmark synchronously (CLI / CI use)."""
    from ..jobs.worker import Worker
    from ..services.context import AppContext
    from ..services.runs import prepare_run, run_record

    ctx = ctx or AppContext()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir / f"{case_id}_{level}_{time.strftime('%Y%m%d-%H%M%S')}"
    t0 = time.time()
    p = build_benchmark_project(case_id, root, level, cores)
    res = prepare_run(p, ctx, label=f"{case_id} {level}")
    w = Worker(p, ctx, launch_inline=True)
    last = ""
    while True:
        w.tick()
        r = p.db.one("SELECT status FROM runs WHERE id = ?", (res["run_id"],))
        if progress and r and r["status"] != last:
            progress(f"{case_id}: run {r['status']}")
            last = r["status"]
        if not w.has_work():
            break
        time.sleep(1.0)
    run = run_record(p, res["run_id"])
    ev = evaluate(case_id, run)
    mesh = p.db.one("SELECT cells FROM mesh_revisions WHERE id = ?", (run.get("mesh_revision_id"),)) or {}
    ev.update({"level": level, "project": str(root), "run_id": res["run_id"], "cells": mesh.get("cells"), "wall_seconds": time.time() - t0,
               "app_version": APP_VERSION, "foam_version": run.get("foam_version"), "provider": run.get("provider"), "date": utc_now_iso(),
               "hardware": run.get("hardware")})
    (root / "validation").mkdir(exist_ok=True)
    (root / "validation" / f"{case_id}.json").write_text(json.dumps(ev, indent=2, default=str))
    p.close()
    return ev
