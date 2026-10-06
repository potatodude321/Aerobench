"""Run comparison with deltas and validity checks (BUILD_SPEC §19)."""

from __future__ import annotations

from typing import Any, Optional

from ..aero.forces import METRIC_INFO
from ..core.errors import AeroBenchError, ErrorCategory
from ..project.store import Project
from .runs import run_record

DEFAULT_METRICS = ["Cd", "Cl", "Cl_front", "Cl_rear", "balance_front_pct", "Cs", "Cm_pitch", "Cm_yaw", "CdA", "drag_N",
                   "downforce_N", "efficiency"]
# percentage deltas are misleading for quantities that cross zero
NO_PERCENT = {"Cl", "Cl_front", "Cl_rear", "Cs", "Cm_pitch", "Cm_yaw", "Cm_roll", "downforce_N", "balance_front_pct", "efficiency"}


def compare_runs(project: Project, run_ids: list[str], baseline: Optional[str] = None, metrics: Optional[list[str]] = None) -> dict[str, Any]:
    if not run_ids:
        raise AeroBenchError.make("compare.empty", ErrorCategory.CONFIGURATION, "Select at least one run to compare.")
    baseline = baseline or run_ids[0]
    if baseline not in run_ids:
        run_ids = [baseline] + run_ids
    metrics = metrics or DEFAULT_METRICS
    rows = {rid: run_record(project, rid, include_snapshot=True) for rid in run_ids}
    base = rows[baseline]
    base_m = ((base.get("metrics") or {}).get("metrics")) or {}
    warnings: list[str] = []
    table = []
    for rid in run_ids:
        r = rows[rid]
        m = ((r.get("metrics") or {}).get("metrics")) or {}
        vals = {}
        for k in metrics:
            v, b = m.get(k), base_m.get(k)
            d = None if v is None or b is None else v - b
            pct = None
            if d is not None and k not in NO_PERCENT and b not in (0, None) and abs(b) > 1e-9:
                pct = 100.0 * d / abs(b)
            vals[k] = {"value": v, "delta": d, "percent": pct}
        snap = r.get("config_snapshot") or {}
        mesh = project.db.one("SELECT cells FROM mesh_revisions WHERE id = ?", (r.get("mesh_revision_id"),)) or {}
        table.append({
            "run_id": rid, "label": r.get("label"), "status": r.get("status"), "baseline": rid == baseline,
            "metrics": vals, "operating": r.get("operating"), "reference": r.get("reference"),
            "mesh_revision_id": r.get("mesh_revision_id"), "geometry_revision_id": r.get("geometry_revision_id"),
            "cells": mesh.get("cells"), "wall_seconds": r.get("wall_seconds"), "iterations": r.get("iterations"),
            "convergence": (r.get("convergence") or {}).get("status"), "n_procs": r.get("n_procs"),
            "numerics": (snap.get("numerics") or {}).get("preset"), "mesh_preset": (snap.get("mesh") or {}).get("preset"),
        })
    # validity notes
    b_ref = base.get("reference") or {}
    for t in table[1:] if table and table[0]["baseline"] else table:
        if t["baseline"]:
            continue
        ref = t["reference"] or {}
        if any(ref.get(k) != b_ref.get(k) for k in ("area", "length", "moment_centre")):
            warnings.append(f"{t['run_id']}: different reference values — compare forces (N) and CdA rather than coefficients.")
        op, bop = t["operating"] or {}, base.get("operating") or {}
        diff = [k for k in ("speed", "yaw_deg", "ground") if op.get(k) != bop.get(k)]
        if diff:
            warnings.append(f"{t['run_id']}: operating condition differs ({', '.join(diff)}).")
        if t["convergence"] in ("not_converged", "diverged", "insufficient_data"):
            warnings.append(f"{t['run_id']}: run is {t['convergence'].replace('_', ' ')} — deltas may not be meaningful.")
    same_mesh = len({t["mesh_revision_id"] for t in table}) == 1
    return {
        "baseline": baseline,
        "metrics": [{"id": k, **METRIC_INFO.get(k, {"label": k, "units": "", "sign": ""}), "percent_meaningful": k not in NO_PERCENT}
                    for k in metrics],
        "runs": table,
        "warnings": warnings,
        "field_difference_available": same_mesh,
        "field_difference_note": None if same_mesh else
        "Field difference maps need identical meshes; these runs use different meshes, so only side-by-side views are offered.",
    }
