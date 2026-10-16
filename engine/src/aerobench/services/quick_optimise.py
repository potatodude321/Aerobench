"""One-click optimisation goals: "Lower drag", "More downforce", "Better efficiency".

A goal turns into a complete optimisation definition without any setup:

* **What may change.** A vehicle changes shape inside its enabled shape zones. Without any,
  the goal places its own (drag: rear taper, roof trailing edge, diffuser; downforce and
  efficiency: front splitter, rear deck, diffuser) — one variable each, so the search stays
  short; they appear under Shape zones and can be adjusted. An aero element changes its
  angle (±5°) and, over the ground, its height (half to twice today's), plus its enabled
  shape zones.
* **What must not get worse.** Lower drag: lift may rise by at most 0.02 in Cl. More
  downforce: drag may rise by at most 5 % (vehicles) or 10 % (elements), and a vehicle that
  makes downforce keeps its aero balance within 2 points. Better efficiency (downforce /
  drag) is offered only when the vehicle makes downforce.
* **How much.** Bayesian optimisation (one objective, constraints): a space-filling start of
  max(5, 2d + 1) designs, then max(6, 2d) guided designs, plus the baseline (needed for the
  "relative to today" constraints), at most 30. Designs run one after another on the
  project's cores, or several at once when the cores allow it.

The time estimate uses the most recent finished run of the project (meshing and solving),
so it is shown only once something has run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.exploration import OptimisationDefinition, ShapeZone
from ..domain.project import ProjectConfig
from ..domain.setup import ElementEnvironment
from ..project.store import Project
from . import studies as ssvc
from . import zones as zsvc
from .context import AppContext

GOALS = ("drag", "downforce", "efficiency")

TITLES = {"drag": "Lower drag", "downforce": "More downforce", "efficiency": "Better efficiency"}

# zones a goal places on a vehicle that has none (preset id, move direction, largest move in mm)
GOAL_ZONES: dict[str, list[tuple[str, str, float]]] = {
    "drag": [("boat_tail", "width", 30.0), ("roof_tail", "z", 30.0), ("diffuser", "z", 25.0)],
    "downforce": [("front_splitter", "z", 20.0), ("rear_deck", "z", 30.0), ("diffuser", "z", 25.0)],
    "efficiency": [("front_splitter", "z", 20.0), ("rear_deck", "z", 30.0), ("diffuser", "z", 25.0)],
}

MAX_BUDGET = 30


def _err(code: str, msg: str, action: str = "") -> AeroBenchError:
    return AeroBenchError.make(code, ErrorCategory.CONFIGURATION, msg, action=action)


@dataclass
class Plan:
    goal: str
    title: str
    available: bool = True
    reason: str = ""
    definition: Optional[OptimisationDefinition] = None
    zones_to_add: list[dict[str, Any]] = field(default_factory=list)
    zones_to_enable: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)  # plain-language list of what may change
    keeps: list[str] = field(default_factory=list)  # plain-language constraints
    objective: str = ""
    designs: int = 0
    concurrent: int = 1
    seconds_per_design: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        est = None
        if self.seconds_per_design and self.designs:
            waves = -(-self.designs // max(1, self.concurrent))
            est = waves * self.seconds_per_design
        return {"goal": self.goal, "title": self.title, "available": self.available, "reason": self.reason, "objective": self.objective,
                "changes": self.changes, "keeps": self.keeps, "designs": self.designs, "concurrent": self.concurrent,
                "estimate_seconds": est, "zones_added": [z["name"] for z in self.zones_to_add], "notes": self.notes,
                "definition": self.definition.model_dump(mode="json") if self.definition else None}


# ---------------------------------------------------------------------------- what we know from earlier runs


def _latest_run(project: Project) -> Optional[dict[str, Any]]:
    """The most recent finished run with forces (a design of a study counts too)."""
    row = project.db.one("SELECT id, metrics_json, wall_seconds, mesh_revision_id, n_procs FROM runs WHERE status = 'completed' "
                         "AND metrics_json IS NOT NULL ORDER BY created_at DESC LIMIT 1")
    if row is None:
        return None
    m = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
    return {"id": row["id"], "metrics": (m or {}).get("metrics") or {}, "wall_seconds": row["wall_seconds"],
            "mesh_revision_id": row["mesh_revision_id"], "n_procs": row["n_procs"]}


def _seconds(a: Optional[str], b: Optional[str]) -> Optional[float]:
    try:
        return (datetime.fromisoformat(str(b).replace("Z", "+00:00")) - datetime.fromisoformat(str(a).replace("Z", "+00:00"))).total_seconds()
    except (TypeError, ValueError):
        return None


def _seconds_per_design(project: Project, run: Optional[dict[str, Any]]) -> Optional[float]:
    """Meshing plus solving of the latest run: every shape change needs both."""
    if run is None or not run.get("wall_seconds"):
        return None
    total = float(run["wall_seconds"])
    if run.get("mesh_revision_id"):
        j = project.db.one("SELECT started_at, ended_at FROM jobs WHERE target_kind = 'mesh' AND target_id = ? AND status = 'completed' "
                           "ORDER BY created_at DESC LIMIT 1", (run["mesh_revision_id"],))
        if j is not None:
            total += max(0.0, _seconds(j["started_at"], j["ended_at"]) or 0.0)
    return total


# ---------------------------------------------------------------------------- variables


def _zone_variables(cfg: ProjectConfig, zones: list[ShapeZone]) -> list[dict[str, Any]]:
    params = {p.id: p for p in cfg.parameters}
    out = []
    for z in zones:
        if not z.enabled:
            continue
        for pid in zsvc.zone_parameter_ids(z):
            p = params.get(pid)
            lo, hi = (p.lower, p.upper) if p is not None else (-_move_max(z, pid), _move_max(z, pid))
            out.append({"id": pid, "target": f"param.{pid}", "kind": "continuous", "lower": lo, "upper": hi, "baseline": 0.0, "units": "mm",
                        "label": p.name if p is not None else f"{z.name}: {pid}"})
    return out


def _move_max(z: ShapeZone, pid: str) -> float:
    for mv in z.moves:
        if pid.startswith(f"{z.id}_{mv.direction}"):
            return mv.max_mm
    return 20.0


def _goal_zones(project: Project, goal: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Zones the goal adds (and existing disabled zones of the same kind it turns on)."""
    existing = list(project.config.zones)
    by_preset = {z.preset: z for z in existing}
    presets = {p["preset"]: p for p in zsvc.presets(project)}
    add, enable = [], []
    taken = {z.id for z in existing} | {p.id for p in project.config.parameters}
    for key, direction, mm in GOAL_ZONES[goal]:
        if key in by_preset:
            enable.append(by_preset[key].id)
            continue
        pr = presets[key]
        zid = key
        k = 2
        while zid in taken or any(pid.startswith(zid + "_") for pid in taken):
            zid = f"{key}{k}"
            k += 1
        taken.add(zid)
        add.append({"id": zid, "name": pr["name"], "preset": key, "box_min": pr["box_min"], "box_max": pr["box_max"], "blend": pr["blend"],
                    "moves": [{"direction": direction, "max_mm": mm, "segments": 1}], "symmetric": True, "enabled": True})
    return add, enable


def _one_mpi_job(ctx: AppContext, cfg: ProjectConfig) -> bool:
    try:
        return bool(getattr(ctx.provider(cfg.execution.provider_id), "one_mpi_job_at_a_time", False))
    except AeroBenchError:
        return False


def _describe_move(var: dict[str, Any]) -> str:
    return f"{var['label']} (±{max(abs(var['lower']), abs(var['upper'])):g} mm)"


# ---------------------------------------------------------------------------- plans


def plan(project: Project, ctx: AppContext, goal: str) -> Plan:
    if goal not in GOALS:
        raise _err("optimise.goal", f"Unknown goal '{goal}'.")
    cfg = project.config
    pl = Plan(goal=goal, title=TITLES[goal])
    if not cfg.geometry.revision_id:
        pl.available, pl.reason = False, "Import the geometry first."
        return pl
    element = cfg.application == "aero_element"
    run = _latest_run(project)
    base = (run or {}).get("metrics") or {}
    downforce = base.get("downforce_N")
    makes_downforce = downforce is not None and downforce > 0

    # ---- variables
    variables: list[dict[str, Any]] = []
    if element:
        e = cfg.element
        a0 = float(e.angle_deg)
        variables.append({"id": "angle", "target": "element.angle_deg", "kind": "continuous", "lower": max(-45.0, a0 - 5.0),
                          "upper": min(45.0, a0 + 5.0), "baseline": a0, "units": "°", "label": "Angle, trailing edge up"})
        pl.changes.append(f"angle {max(-45.0, a0 - 5.0):g}° to {min(45.0, a0 + 5.0):g}° (today {a0:g}°)")
        if e.environment == ElementEnvironment.GROUND_EFFECT:
            h0 = float(e.ground_clearance)
            lo, hi = round(max(0.5 * h0, 0.005), 4), round(min(2.0 * h0, 10.0), 4)
            variables.append({"id": "height", "target": "element.ground_clearance", "kind": "continuous", "lower": lo, "upper": hi,
                              "baseline": h0, "units": "m", "label": "Height over the ground"})
            pl.changes.append(f"height over the ground {lo * 1000:.0f} to {hi * 1000:.0f} mm (today {h0 * 1000:.0f} mm)")
        zone_vars = _zone_variables(cfg, list(cfg.zones))
        variables += zone_vars
        pl.changes += [_describe_move(v) for v in zone_vars]
    else:
        zone_vars = _zone_variables(cfg, list(cfg.zones))
        if zone_vars:
            pl.notes.append("Uses your shape zones.")
        else:
            try:
                pl.zones_to_add, pl.zones_to_enable = _goal_zones(project, goal)
            except AeroBenchError as exc:
                pl.available, pl.reason = False, exc.diagnostic.summary
                return pl
            future = [ShapeZone.model_validate(z) for z in pl.zones_to_add]
            future += [z.model_copy(update={"enabled": True}) for z in cfg.zones if z.id in pl.zones_to_enable]
            zone_vars = _zone_variables(cfg, future)
            if pl.zones_to_add:
                pl.notes.append("Places shape zones (" + ", ".join(z["name"] for z in pl.zones_to_add) + "); adjust them under Shape zones.")
        variables += zone_vars
        pl.changes += [_describe_move(v) for v in zone_vars]
    if not variables:
        pl.available, pl.reason = False, "Nothing to change: add a shape zone first."
        return pl

    # ---- objective and constraints
    constraints: list[dict[str, Any]] = []
    if goal == "drag":
        objective = {"metric": "Cd", "sense": "minimize"}
        pl.objective = "Lowest drag coefficient"
        constraints.append({"metric": "Cl", "op": "<=", "value": 0.02, "bound": "baseline_delta"})
        pl.keeps.append("lift up by at most 0.02 in Cl, so downforce is not traded away")
    elif goal == "downforce":
        objective = {"metric": "Cl", "sense": "minimize"}
        pl.objective = "Most downforce (lowest lift coefficient)"
        ratio = 1.10 if element else 1.05
        constraints.append({"metric": "Cd", "op": "<=", "value": ratio, "bound": "baseline_ratio"})
        pl.keeps.append(f"drag up by at most {round((ratio - 1) * 100)}\u00a0%")
        if not element and makes_downforce and base.get("balance_front_pct") is not None and cfg.vehicle.wheelbase:
            constraints += [{"metric": "balance_front_pct", "op": ">=", "value": -2.0, "bound": "baseline_delta"},
                            {"metric": "balance_front_pct", "op": "<=", "value": 2.0, "bound": "baseline_delta"}]
            pl.keeps.append("aero balance within 2 points of today")
    else:
        if not element and run is not None and not makes_downforce:
            pl.available = False
            pl.reason = "The vehicle makes lift, not downforce, so downforce/drag is not a useful target. Try More downforce first."
            return pl
        objective = {"metric": "efficiency", "sense": "maximize"}
        pl.objective = "Highest downforce / drag"
        if run is None and not element:
            pl.notes.append("Meaningful only if the vehicle makes downforce; the first design (today's shape) shows it.")

    # ---- budget and scheduling
    d = len(variables)
    n0 = max(5, 2 * d + 1)
    budget = min(MAX_BUDGET, n0 + max(6, 2 * d) + 1)
    if d > 8:
        pl.notes.append(f"{d} variables: the search is short for so many; consider fewer zones.")
    cores = max(1, min(cfg.execution.n_procs or 1, ctx.max_cores()))
    concurrent = max(1, min(4, ctx.max_cores() // cores))
    if cores > 1 and concurrent > 1 and _one_mpi_job(ctx, cfg):
        concurrent = 1  # parallel runs cannot share this computer (Microsoft MPI): one design at a time, all cores
    pl.designs, pl.concurrent = budget, concurrent
    pl.seconds_per_design = _seconds_per_design(project, run)
    if run is not None and run.get("n_procs") and run["n_procs"] != cores and pl.seconds_per_design:
        pl.seconds_per_design *= run["n_procs"] / cores
    pl.definition = OptimisationDefinition.model_validate({
        "id": f"quick_{goal}", "name": TITLES[goal], "variables": variables, "objectives": [objective | {"aggregate": "single"}],
        "constraints": constraints, "algorithm": "gp_bo", "budget": budget, "batch_size": concurrent, "initial_samples": n0, "seed": 0,
        "evaluate_baseline": True, "scheduling": {"concurrency": concurrent, "cores_per_run": cores, "max_retries": 1, "accept_unconverged": True},
    })
    return pl


def plans(project: Project, ctx: AppContext) -> list[dict[str, Any]]:
    out = []
    for g in GOALS:
        try:
            out.append(plan(project, ctx, g).as_dict())
        except AeroBenchError as exc:
            out.append({"goal": g, "title": TITLES[g], "available": False, "reason": exc.diagnostic.summary})
    active = project.db.all("SELECT id, definition_id, status FROM studies WHERE kind = 'optimisation' AND "
                            "status IN ('running', 'paused', 'cancelling') ORDER BY created_at DESC")
    for p in out:
        mine = next((r for r in active if r["definition_id"] == f"quick_{p['goal']}"), None)
        # the goal's own optimisation is open, not started twice; another one shares the cores
        p["active_study_id"] = mine["id"] if mine else None
        p["busy"] = any(r["definition_id"] != f"quick_{p['goal']}" and r["status"] == "running" for r in active)
    return out


def start(project: Project, ctx: AppContext, goal: str, launch_worker: bool = True) -> dict[str, Any]:
    open_ = project.db.one("SELECT id FROM studies WHERE definition_id = ? AND status IN ('running', 'paused', 'cancelling')", (f"quick_{goal}",))
    if open_ is not None:
        raise _err("optimise.already_running", f"{TITLES[goal]} is already running ({open_['id']}).",
                   "Open it on the Optimise page, or cancel it before starting it again.")
    pl = plan(project, ctx, goal)
    if not pl.available or pl.definition is None:
        raise _err("optimise.unavailable", f"{pl.title} cannot start: {pl.reason}")
    if pl.zones_to_add or pl.zones_to_enable:
        zones = [z.model_dump(mode="json") | ({"enabled": True} if z.id in pl.zones_to_enable else {}) for z in project.config.zones]
        zsvc.set_zones(project, zones + pl.zones_to_add)
        # the zones' parameters now exist: plan again so the variables match them exactly
        pl = plan(project, ctx, goal)
        assert pl.definition is not None
    return ssvc.start(project, ctx, "optimisation", pl.definition.model_dump(mode="json"), launch_worker=launch_worker)
