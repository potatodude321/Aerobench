"""Study and optimisation driver (runs inside the project worker, ADR-0005/0009).

Each worker tick calls :func:`drive_all`, which for every running/paused study:

1. synchronises in-flight designs with their runs (completion, failure, bounded retries);
2. tells finished designs to the optimiser and checkpoints its state;
3. launches new designs while the concurrency limit allows (studies: next pending design;
   optimisation: asks the optimiser);
4. finishes the study when nothing is left to do.

The driver is idempotent: it keeps no state in memory between ticks, so a crash or a
restart of the worker resumes from the database (designs, runs, optimiser checkpoint).
Failures of individual designs are recorded as data and never stop the study.
"""

from __future__ import annotations

import math
import traceback
from typing import Any, Optional

from ..aero.forces import METRIC_INFO
from ..core.errors import AeroBenchError
from ..core.ids import short_id, utc_now_iso
from ..domain.exploration import (
    Constraint,
    ConstraintBound,
    OptimisationDefinition,
    Sense,
    StudyDefinition,
)
from ..domain.geometry import SizingBox
from ..domain.numerics import NumericsPreset
from ..domain.project import ProjectConfig
from ..jobs.manager import JobManager
from ..optimisation.algorithms import restore as restore_optimizer
from ..project.db import dec
from ..project.store import Project
from .design import baseline_extent, build_design
from .space import DesignSpace, values_key

ACTIVE_DESIGN = ("running",)
TERMINAL_DESIGN = ("completed", "failed", "cancelled", "skipped")
RUN_TERMINAL_FAIL = ("failed", "cancelled", "stopped", "interrupted")
CONVERGED = ("converged", "converged_oscillating")
RETRYABLE_CODES_PREFIX = ("mpi.", "internal", "environment.runner", "job.interrupted")

# Normalisation scales for constraint values by metric units (g is divided by max(|bound|, scale))
UNIT_SCALE = {"-": 0.01, "N": 10.0, "N·m": 10.0, "%": 1.0, "m²": 0.01}


def drive_all(project: Project, ctx) -> list[dict[str, Any]]:
    out = []
    for row in project.db.all("SELECT * FROM studies WHERE status IN ('running','paused','cancelling') ORDER BY created_at"):
        try:
            out.append(StudyRuntime(project, ctx, dec(row)).tick())
        except Exception as exc:  # noqa: BLE001 - one broken study must not stop the others
            project.db.log_event("study_error", row["id"], error=traceback.format_exc()[-3000:])
            project.db.update("studies", "id", row["id"], {"status": "failed", "message": f"Internal error: {exc}",
                                                           "ended_at": utc_now_iso(), "updated_at": utc_now_iso()})
            out.append({"study": row["id"], "error": str(exc)})
    return out


class StudyRuntime:
    def __init__(self, project: Project, ctx, row: dict[str, Any]):
        self.project, self.ctx, self.row = project, ctx, row
        self.db = project.db
        self.kind = row["kind"]
        d = row["definition"]
        self.defn: Any = OptimisationDefinition.model_validate(d) if self.kind == "optimisation" else StudyDefinition.model_validate(d)
        self.base_cfg = ProjectConfig.model_validate(row["base_config"])
        self.space = DesignSpace(self.defn.variables)
        self.sched = self.defn.scheduling
        self.sizing: Optional[SizingBox] = None
        self.exhausted = False  # optimiser had nothing to propose with nothing in flight
        self.opt = None
        if self.kind == "optimisation":
            st = self.db.one("SELECT state_json FROM optimiser_state WHERE study_id = ?", (row["id"],))
            if st is None:
                raise RuntimeError("optimiser state missing")
            self.opt = restore_optimizer(dec(st)["state"])
            self.opt.projector = self.space.project

    # ------------------------------------------------------------------ helpers
    def designs(self, statuses: Optional[tuple[str, ...]] = None) -> list[dict[str, Any]]:
        if statuses:
            q = "SELECT * FROM designs WHERE study_id = ? AND status IN (%s) ORDER BY idx" % ",".join("?" * len(statuses))
            return [dec(r) for r in self.db.all(q, (self.row["id"], *statuses))]
        return [dec(r) for r in self.db.all("SELECT * FROM designs WHERE study_id = ? ORDER BY idx", (self.row["id"],))]

    def _update_design(self, did: str, changes: dict[str, Any]) -> None:
        changes["updated_at"] = utc_now_iso()
        self.db.update("designs", "id", did, changes)

    def _sizing(self) -> SizingBox:
        if self.sizing is None:
            sb = (self.row.get("summary") or {}).get("sizing_box")
            self.sizing = SizingBox.model_validate(sb) if sb else baseline_extent(self.project, self.base_cfg)
        return self.sizing

    def conditions(self) -> list[dict[str, float]]:
        if self.kind == "optimisation" and self.defn.conditions:
            return [dict(c) for c in self.defn.conditions]
        return [{}]

    def checkpoint(self) -> None:
        if self.opt is None:
            return
        self.db.execute("INSERT INTO optimiser_state(study_id, algorithm, state_json, updated_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(study_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
                        (self.row["id"], self.opt.name, _json(self.opt.state()), utc_now_iso()))

    # ------------------------------------------------------------------ tick
    def tick(self) -> dict[str, Any]:
        report: dict[str, Any] = {"study": self.row["id"], "completed": [], "failed": [], "launched": [], "retried": []}
        status = self.row["status"]
        if status == "cancelling":
            self._cancel_all()
            return report
        self._sync(report)
        if self.opt is not None:
            self._tell_finished()
        if status == "running":
            self._launch(report)
        self._maybe_finish()
        self.checkpoint()
        return report

    # ------------------------------------------------------------------ 1. synchronise
    def _sync(self, report: dict) -> None:
        for d in self.designs(ACTIVE_DESIGN):
            runs = d.get("run_ids") or []
            if not runs:
                self._fail(d, {"code": "design.no_runs", "message": "The design has no runs."})
                report["failed"].append(d["id"])
                continue
            per_cond: dict[int, dict] = {}
            for r in runs:
                per_cond[r["condition"]] = r  # last attempt wins
            states = {}
            for ci, r in per_cond.items():
                rr = self.db.one("SELECT id, status, metrics_json, convergence_json, diagnostics_json FROM runs WHERE id = ?", (r["run_id"],))
                states[ci] = dec(rr) if rr else {"status": "failed", "diagnostics": [{"code": "run.missing", "summary": "Run record missing."}]}
            pending = [ci for ci, s in states.items() if s["status"] not in ("completed",) + RUN_TERMINAL_FAIL]
            failed = [ci for ci, s in states.items() if s["status"] in RUN_TERMINAL_FAIL]
            if not self.sched.accept_unconverged:
                for ci, s in states.items():
                    if s["status"] == "completed" and (s.get("convergence") or {}).get("status") not in CONVERGED:
                        failed.append(ci)
                        s["diagnostics"] = (s.get("diagnostics") or []) + [{"code": "solver.not_converged",
                                                                             "summary": "The run did not meet the convergence criteria."}]
            if failed:
                ci = failed[0]
                if self._retry(d, ci, states[ci], report):
                    continue
                if pending:
                    # stop sibling runs: the design has failed anyway
                    for pci in pending:
                        self._stop_run(per_cond[pci]["run_id"])
                diag = (states[ci].get("diagnostics") or [{}])[0]
                self._fail(d, {"code": diag.get("code", "run.failed"), "message": diag.get("summary") or f"Run {per_cond[ci]['run_id']} failed.",
                               "run_id": per_cond[ci]["run_id"], "condition": ci})
                report["failed"].append(d["id"])
                continue
            if pending:
                continue
            if self._needs_baseline() and d["origin"] != "baseline" and self.baseline_metrics() is None:
                continue  # baseline-relative constraints: wait until the baseline is known
            self._complete(d, per_cond, states)
            report["completed"].append(d["id"])

    def _retry(self, d: dict, ci: int, st: dict, report: dict) -> bool:
        attempts = int(d.get("attempts") or 1)
        if attempts > self.sched.max_retries:
            return False
        diags = st.get("diagnostics") or []
        codes = [x.get("code", "") for x in diags]
        strategy = None
        if any(c.startswith(RETRYABLE_CODES_PREFIX) for c in codes) or st["status"] == "interrupted":
            strategy = "same"
        elif any(c == "solver.divergence" for c in codes):
            strategy = "robust"
        if strategy is None:
            return False
        build = self._build(d["values"], self.conditions()[ci])
        if not build.ok:
            return False
        cfg = build.cfg
        if strategy == "robust":
            if cfg.numerics.preset.value == "robust":
                return False
            cfg.numerics.preset = NumericsPreset.ROBUST
        try:
            rid = self._start_run(cfg, d, ci, attempts + 1)
        except AeroBenchError as exc:
            self.db.log_event("design_retry_failed", d["id"], error=exc.diagnostic.summary)
            return False
        runs = list(d.get("run_ids") or []) + [{"condition": ci, "run_id": rid, "attempt": attempts + 1, "strategy": strategy}]
        notes = list(d.get("notes") or []) + [f"Attempt {attempts + 1} for condition {ci + 1} ({strategy} retry) after {', '.join(codes) or st['status']}."]
        self._update_design(d["id"], {"run_ids_json": runs, "attempts": attempts + 1, "notes_json": notes})
        report["retried"].append(d["id"])
        return True

    def _complete(self, d: dict, per_cond: dict[int, dict], states: dict[int, dict]) -> None:
        conds = self.conditions()
        per = []
        conv_flags = []
        for ci in sorted(per_cond):
            m = (states[ci].get("metrics") or {}).get("metrics") or {}
            conv = (states[ci].get("convergence") or {}).get("status")
            conv_flags.append(conv)
            per.append({"condition": conds[ci], "run_id": per_cond[ci]["run_id"], "metrics": m, "convergence": conv})
        agg = aggregate_metrics(per)
        changes: dict[str, Any] = {"status": "completed", "metrics_json": {"conditions": per, "aggregate": agg},
                                   "convergence_json": {"all_converged": all(c in CONVERGED for c in conv_flags), "per_condition": conv_flags}}
        if self.kind == "optimisation":
            obj, cons, feasible = self.evaluate_objectives(per, own_baseline=per if d["origin"] == "baseline" else None)
            changes.update({"objectives_json": obj, "constraints_json": cons, "feasible": int(feasible)})
        self._update_design(d["id"], changes)

    def _fail(self, d: dict, error: dict) -> None:
        self._update_design(d["id"], {"status": "failed", "error_json": error, "feasible": 0})

    def _stop_run(self, run_id: str) -> None:
        r = self.db.one("SELECT job_id, status FROM runs WHERE id = ?", (run_id,))
        if r and r["job_id"] and r["status"] in ("queued", "running"):
            try:
                JobManager(self.project).request_stop(r["job_id"])
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ objectives
    def baseline_metrics(self) -> Optional[dict[str, Any]]:
        b = self.db.one("SELECT metrics_json, status FROM designs WHERE study_id = ? AND origin = 'baseline'", (self.row["id"],))
        if not b or b["status"] != "completed":
            return None
        return (dec(b).get("metrics") or {}).get("conditions")

    def _needs_baseline(self) -> bool:
        return self.kind == "optimisation" and any(c.bound != ConstraintBound.ABSOLUTE for c in self.defn.constraints)

    def evaluate_objectives(self, per: list[dict], own_baseline: Optional[list[dict]] = None) -> tuple[list[dict], list[dict], bool]:
        defn: OptimisationDefinition = self.defn
        objs = []
        for o in defn.objectives:
            vals = [c["metrics"].get(o.metric) for c in per]
            if any(v is None or not math.isfinite(v) for v in vals):
                objs.append({"metric": o.metric, "sense": o.sense.value, "value": None})
                continue
            v = {"single": vals[0], "mean": sum(vals) / len(vals), "max": max(vals), "min": min(vals)}[o.aggregate]
            objs.append({"metric": o.metric, "sense": o.sense.value, "value": float(v), "per_condition": vals})
        cons = []
        feasible = True
        base = own_baseline if own_baseline is not None else self.baseline_metrics()
        for c in defn.constraints:
            worst_g = -math.inf
            detail = []
            for ci, cond in enumerate(per):
                val = cond["metrics"].get(c.metric)
                bound = constraint_bound(c, None if base is None else (base[ci]["metrics"].get(c.metric) if ci < len(base) else None))
                if val is None or bound is None or not math.isfinite(val):
                    g = math.inf
                else:
                    scale = max(abs(bound), UNIT_SCALE.get(METRIC_INFO.get(c.metric, {}).get("units", "-"), 0.01))
                    g = ((val - bound) if c.op == "<=" else (bound - val)) / scale
                detail.append({"value": val, "bound": bound, "g": g if math.isfinite(g) else None})
                worst_g = max(worst_g, g)
            cons.append({"metric": c.metric, "op": c.op, "bound_kind": c.bound.value, "g": worst_g if math.isfinite(worst_g) else None,
                         "satisfied": bool(worst_g <= 0), "per_condition": detail})
            feasible = feasible and worst_g <= 0
        return objs, cons, feasible

    def _tell_finished(self) -> None:
        assert self.opt is not None
        for d in [x for x in self.designs(TERMINAL_DESIGN) if not x.get("told") and x.get("token")]:
            if d["token"] not in self.opt.pending and not any(o.token == d["token"] for o in self.opt.observations) and d["token"] != "baseline":
                self._update_design(d["id"], {"told": 1})
                continue
            if d["status"] == "completed":
                objs = d.get("objectives") or []
                f = [(-o["value"] if o["sense"] == Sense.MAXIMIZE.value else o["value"]) if o["value"] is not None else math.nan for o in objs]
                g = [c["g"] if c.get("g") is not None else math.nan for c in (d.get("constraints") or [])]
                failed = any(not math.isfinite(x) for x in f)
                self.opt.tell(d["token"], None if failed else f, g if self.opt.n_con else None, failed=failed, x=d.get("unit"))
            elif d["status"] == "cancelled":
                self.opt.forget_pending(d["token"])
            else:
                self.opt.tell(d["token"], failed=True, x=d.get("unit"))
            self._update_design(d["id"], {"told": 1})

    # ------------------------------------------------------------------ 3. launch
    def _active_count(self) -> int:
        return int(self.db.one("SELECT COUNT(*) AS n FROM designs WHERE study_id = ? AND status = 'running'", (self.row["id"],))["n"])

    def _launch(self, report: dict) -> None:
        free = self.sched.concurrency - self._active_count()
        if free <= 0:
            return
        if self.kind == "study":
            for d in self.designs(("pending",))[:free]:
                if self._launch_design(d):
                    report["launched"].append(d["id"])
            return
        # optimisation
        assert self.opt is not None
        needs_baseline = self._needs_baseline()
        base = self.db.one("SELECT status FROM designs WHERE study_id = ? AND origin = 'baseline'", (self.row["id"],))
        for d in self.designs(("pending",))[:free]:  # e.g. the baseline
            if self._launch_design(d):
                report["launched"].append(d["id"])
                free -= 1
        if needs_baseline and (base is None or base["status"] != "completed"):
            if base is not None and base["status"] in ("failed", "cancelled"):
                self.db.update("studies", "id", self.row["id"], {"status": "failed", "ended_at": utc_now_iso(), "updated_at": utc_now_iso(),
                                                                 "message": "The baseline design failed, so baseline-relative constraints cannot be evaluated."})
            return
        free = self.sched.concurrency - self._active_count()
        if free <= 0:
            return
        asked = self.opt.ask(min(free, max(1, self.defn.batch_size)))
        if not asked and self._active_count() == 0 and not self.opt.pending:
            self.exhausted = True
        n_idx = int(self.db.one("SELECT COALESCE(MAX(idx), -1) AS m FROM designs WHERE study_id = ?", (self.row["id"],))["m"]) + 1
        for tok, u in asked:
            values = self.space.from_unit(u)
            did = short_id("design")
            row = {"id": did, "study_id": self.row["id"], "idx": n_idx, "origin": "optimiser", "values_json": values, "status": "pending",
                   "attempts": 0, "run_ids_json": [], "token": tok, "unit_json": list(u), "created_at": utc_now_iso(), "updated_at": utc_now_iso()}
            n_idx += 1
            cached = self._identical_completed(values)
            if cached is not None:
                row.update({"status": cached["status"], "origin": "optimiser (repeat)", "metrics_json": cached.get("metrics"),
                            "objectives_json": cached.get("objectives"), "constraints_json": cached.get("constraints"),
                            "feasible": cached.get("feasible"), "error_json": cached.get("error"), "convergence_json": cached.get("convergence"),
                            "notes_json": [f"Identical to design {cached['id']}; its results were reused without a new run."]})
                self.db.insert("designs", row)
                continue
            self.db.insert("designs", row)
            if self._launch_design(dec(self.db.one("SELECT * FROM designs WHERE id = ?", (did,)))):
                report["launched"].append(did)
        self.checkpoint()

    def _identical_completed(self, values: dict) -> Optional[dict]:
        key = values_key(values)
        for d in self.designs(("completed", "failed")):
            if values_key(d["values"]) == key:
                return d
        return None

    def _build(self, values: dict, condition: dict):
        assignments = {}
        for v in self.defn.variables:
            assignments[v.target] = values[v.id]
        for t, val in condition.items():
            assignments.setdefault(t, val)
        return build_design(self.project, self.base_cfg, assignments, sizing=self._sizing())

    def _start_run(self, cfg: ProjectConfig, d: dict, ci: int, attempt: int) -> str:
        from ..services.runs import prepare_run

        cfg.execution.n_procs = self.sched.cores_per_run
        label = f"{self.row.get('name') or self.row['id']} #{d['idx']}" + (f" c{ci + 1}" if len(self.conditions()) > 1 else "")
        if attempt > 1:
            label += f" (attempt {attempt})"
        res = prepare_run(self.project, self.ctx, cfg, label=label, study_id=self.row["id"], design_id=d["id"])
        return res["run_id"]

    def _launch_design(self, d: dict) -> bool:
        runs = []
        geo_rev = None
        checks = None
        notes: list[str] = []
        for ci, cond in enumerate(self.conditions()):
            build = self._build(d["values"], cond)
            checks = build.geometry_check or checks
            notes.extend(build.notes)
            if not build.ok:
                for r in runs:
                    self._stop_run(r["run_id"])
                self._update_design(d["id"], {"status": "failed", "error_json": {"code": build.errors[0].get("code", "design.invalid"),
                                                                                  "message": build.errors[0]["message"], "all": build.errors},
                                              "geometry_check_json": checks, "attempts": 1, "feasible": 0, "notes_json": notes})
                return False
            geo_rev = build.geometry_revision_id
            try:
                rid = self._start_run(build.cfg, d, ci, 1)
            except AeroBenchError as exc:
                for r in runs:
                    self._stop_run(r["run_id"])
                self._update_design(d["id"], {"status": "failed", "error_json": {"code": exc.diagnostic.code, "message": exc.diagnostic.summary,
                                                                                  "details": exc.diagnostic.details}, "attempts": 1, "feasible": 0})
                return False
            runs.append({"condition": ci, "run_id": rid, "attempt": 1})
        self._update_design(d["id"], {"status": "running", "run_ids_json": runs, "attempts": 1, "geometry_revision_id": geo_rev,
                                      "geometry_check_json": checks, "notes_json": notes})
        return True

    # ------------------------------------------------------------------ 4. finish / cancel
    def _maybe_finish(self) -> None:
        if self.row["status"] != "running":
            return
        n_active = self._active_count()
        n_pending = int(self.db.one("SELECT COUNT(*) AS n FROM designs WHERE study_id = ? AND status = 'pending'", (self.row["id"],))["n"])
        if n_active or n_pending:
            return
        if self.opt is not None and not self.opt.done() and not self.exhausted:
            if self.opt.pending or self.opt.remaining() > 0:
                return
        summary = summarise(self)
        self.db.update("studies", "id", self.row["id"], {"status": "completed", "ended_at": utc_now_iso(), "updated_at": utc_now_iso(),
                                                         "summary_json": {**(self.row.get("summary") or {}), **summary}})
        self.db.log_event("study_completed", self.row["id"])

    def _cancel_all(self) -> None:
        for d in self.designs(("running", "pending")):
            for r in d.get("run_ids") or []:
                self._stop_run(r["run_id"])
            self._update_design(d["id"], {"status": "cancelled"})
            if self.opt is not None and d.get("token"):
                self.opt.forget_pending(d["token"])
        if self.opt is not None:
            for d in self.designs(("cancelled",)):
                if not d.get("told"):
                    self._update_design(d["id"], {"told": 1})
        self.checkpoint()
        summary = summarise(self)
        self.db.update("studies", "id", self.row["id"], {"status": "cancelled", "ended_at": utc_now_iso(), "updated_at": utc_now_iso(),
                                                         "summary_json": {**(self.row.get("summary") or {}), **summary}})


# ---------------------------------------------------------------------------- shared helpers


def constraint_bound(c: Constraint, baseline_value: Optional[float]) -> Optional[float]:
    if c.bound == ConstraintBound.ABSOLUTE:
        return c.value
    if baseline_value is None or not math.isfinite(baseline_value):
        return None
    if c.bound == ConstraintBound.BASELINE_DELTA:
        return baseline_value + c.value
    return baseline_value * c.value


def aggregate_metrics(per: list[dict]) -> dict[str, dict[str, float]]:
    keys = sorted({k for c in per for k in c["metrics"]})
    out = {}
    for k in keys:
        vals = [c["metrics"].get(k) for c in per]
        vals = [v for v in vals if isinstance(v, (int, float)) and math.isfinite(v)]
        if not vals:
            continue
        out[k] = {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals), "n": len(vals)}
    return out


def summarise(rt: StudyRuntime) -> dict[str, Any]:
    ds = rt.designs()
    counts: dict[str, int] = {}
    for d in ds:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    unconverged = sum(1 for d in ds if d["status"] == "completed" and not (d.get("convergence") or {}).get("all_converged", False))
    out: dict[str, Any] = {"counts": counts, "n_designs": len(ds), "unconverged": unconverged}
    gci_out = mesh_sensitivity(rt, ds)
    if gci_out:
        out["gci"] = gci_out
    if rt.kind == "optimisation":
        defn: OptimisationDefinition = rt.defn
        feas = [d for d in ds if d["status"] == "completed" and d.get("feasible")]
        if len(defn.objectives) == 1 and feas:
            o = defn.objectives[0]
            sign = -1 if o.sense == Sense.MAXIMIZE else 1
            best = min(feas, key=lambda d: sign * d["objectives"][0]["value"])
            out["best_design_id"] = best["id"]
            out["best_value"] = best["objectives"][0]["value"]
        elif feas:
            import numpy as np

            from ..optimisation.algorithms import pareto_mask

            signs = [(-1 if o.sense == Sense.MAXIMIZE else 1) for o in defn.objectives]
            F = np.array([[s * ob["value"] for s, ob in zip(signs, d["objectives"])] for d in feas])
            mask = pareto_mask(F)
            out["pareto_design_ids"] = [d["id"] for d, k in zip(feas, mask) if k]
        out["infeasible"] = sum(1 for d in ds if d["status"] == "completed" and not d.get("feasible"))
    return out


def mesh_sensitivity(rt: StudyRuntime, ds: list[dict]) -> Optional[dict[str, Any]]:
    """GCI for studies that vary only the background cell size (three finest completed meshes)."""
    if rt.kind != "study":
        return None
    var = [v for v in rt.defn.variables if v.target == "mesh.base_cell_size"]
    if len(var) != 1 or len(rt.defn.variables) != 1:
        return None
    vid = var[0].id
    done = sorted((d for d in ds if d["status"] == "completed"), key=lambda d: float(d["values"][vid]))
    sizes = []
    for d in done:
        if not sizes or abs(float(d["values"][vid]) - sizes[-1][0]) > 1e-12:
            sizes.append((float(d["values"][vid]), d))
    if len(sizes) < 3:
        return None
    from ..validation.gci import gci

    trio = sizes[:3]
    res = {}
    for m in rt.defn.metrics:
        vals = [((d.get("metrics") or {}).get("aggregate") or {}).get(m, {}).get("mean") for _, d in trio]
        if any(v is None for v in vals):
            continue
        res[m] = gci([h for h, _ in trio], vals).as_dict()
    return {"variable": vid, "designs": [d["id"] for _, d in trio], "results": res} if res else None


def _json(v: Any) -> str:
    import json

    return json.dumps(v, allow_nan=False, default=str)
