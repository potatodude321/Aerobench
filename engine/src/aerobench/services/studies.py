"""Study / optimisation / geometry-parameter services used by the API."""

from __future__ import annotations

import csv
import io
import math
from typing import Any, Optional, Union

import numpy as np

from ..aero.forces import METRIC_INFO
from ..core.errors import AeroBenchError, ErrorCategory
from ..core.ids import short_id, utc_now_iso
from ..domain.exploration import (
    DesignVariable,
    GeometryParameter,
    OptimisationDefinition,
    StudyDefinition,
    VariableKind,
)
from ..domain.project import ProjectConfig
from ..geometry.deform import apply_parameters, check_deformation, parameter_values_complete
from ..geometry.pose import place_in_cfd_frame, posed_components
from ..jobs.worker import ensure_worker_process
from ..optimisation.algorithms import create as create_optimizer
from ..project.db import dec
from ..project.store import Project
from ..studies.design import baseline_extent, build_design, frozen_transform
from ..studies.doe import generate
from ..studies.space import DesignSpace
from ..studies.targets import baseline_value, list_targets, target_info
from .context import AppContext

Definition = Union[StudyDefinition, OptimisationDefinition]


def _err(code: str, msg: str, action: str = "") -> AeroBenchError:
    return AeroBenchError.make(code, ErrorCategory.CONFIGURATION, msg, action=action)


# ---------------------------------------------------------------------------- definitions


def parse_definition(kind: str, data: dict[str, Any]) -> Definition:
    if kind == "study":
        return StudyDefinition.model_validate(data)
    if kind == "optimisation":
        return OptimisationDefinition.model_validate(data)
    raise _err("study.kind", f"Unknown study kind '{kind}'.")


def baseline_values(cfg: ProjectConfig, variables: list[DesignVariable], project: Optional[Project] = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for v in variables:
        b = v.baseline if v.baseline is not None else baseline_value(cfg, v.target)
        if b is None and v.target == "mesh.base_cell_size" and project is not None:
            from .mesh import plan_mesh

            b = round(plan_mesh(project, cfg)[0].base_cell, 6)  # the preset's resolved background cell
        if b is None:
            raise _err("study.baseline_unknown", f"Variable '{v.id}' has no baseline value (target {v.target} is unset in the project).",
                       "Give the variable an explicit baseline.")
        out[v.id] = b
    return out


def validate_definition(project: Project, ctx: AppContext, kind: str, defn: Definition) -> list[dict[str, Any]]:
    """Problems that block starting the study (engineering + resource checks)."""
    cfg = project.config
    errs: list[dict[str, Any]] = []
    for v in defn.variables:
        try:
            target_info(v.target, cfg)
        except KeyError as exc:
            errs.append({"path": f"variables.{v.id}", "message": str(exc).strip("'")})
            continue
        if v.target == "geometry.variant":
            if v.kind != VariableKind.DISCRETE:
                errs.append({"path": f"variables.{v.id}", "message": "geometry.variant must be a discrete variable of revision ids"})
            else:
                known = {r["id"] for r in project.geometry_revisions()}
                bad = [x for x in v.values or [] if x not in known]
                if bad:
                    errs.append({"path": f"variables.{v.id}", "message": f"unknown geometry revision(s): {', '.join(map(str, bad))}"})
        if v.target.startswith("param."):
            p = next((x for x in cfg.parameters if x.id == v.target[6:]), None)
            if p is not None and v.kind != VariableKind.DISCRETE and (v.lower < p.lower - 1e-12 or v.upper > p.upper + 1e-12):  # type: ignore[operator]
                errs.append({"path": f"variables.{v.id}", "message": f"bounds exceed the parameter's range [{p.lower}, {p.upper}]"})
    try:
        DesignSpace(defn.variables)
    except ValueError as exc:
        errs.append({"path": "variables", "message": str(exc)})
    s = defn.scheduling
    maxc = ctx.max_cores()
    if s.concurrency * s.cores_per_run > maxc:
        errs.append({"path": "scheduling", "message": f"{s.concurrency} concurrent designs × {s.cores_per_run} cores exceed the {maxc} cores "
                     "available; reduce concurrency or cores per run (oversubscription would slow every run down)."})
    if isinstance(defn, OptimisationDefinition):
        for o in defn.objectives:
            if o.metric not in METRIC_INFO:
                errs.append({"path": "objectives", "message": f"unknown metric '{o.metric}'"})
            if o.aggregate == "single" and len(defn.conditions) > 1:
                errs.append({"path": "objectives", "message": f"objective '{o.metric}' is 'single' but {len(defn.conditions)} conditions are defined; "
                             "choose mean, max or min"})
        for c in defn.constraints:
            if c.metric not in METRIC_INFO:
                errs.append({"path": "constraints", "message": f"unknown metric '{c.metric}'"})
        vt = {v.target for v in defn.variables}
        for i, cond in enumerate(defn.conditions):
            for t in cond:
                if t in vt:
                    errs.append({"path": f"conditions.{i}", "message": f"'{t}' is both a design variable and a condition"})
                try:
                    target_info(t, cfg)
                except KeyError as exc:
                    errs.append({"path": f"conditions.{i}", "message": str(exc).strip("'")})
    else:
        for m in defn.metrics:
            if m not in METRIC_INFO:
                errs.append({"path": "metrics", "message": f"unknown metric '{m}'"})
    return errs


def _yaw_range(defn: Definition, cfg: ProjectConfig, designs: Optional[list[dict]] = None) -> Optional[tuple[float, float]]:
    yaws: list[float] = []
    for v in defn.variables:
        if v.target == "operating.yaw_deg":
            if v.kind == VariableKind.DISCRETE:
                yaws += [float(x) for x in v.values or []]
            else:
                yaws += [float(v.lower), float(v.upper)]  # type: ignore[arg-type]
    if isinstance(defn, OptimisationDefinition):
        yaws += [float(c["operating.yaw_deg"]) for c in defn.conditions if "operating.yaw_deg" in c]
    if not yaws:
        return None
    yaws.append(cfg.operating.yaw_deg)
    return (min(yaws), max(yaws))


def preview(project: Project, ctx: AppContext, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    defn = parse_definition(kind, data)
    errs = validate_definition(project, ctx, kind, defn)
    out: dict[str, Any] = {"errors": errs, "designs": [], "n_runs": 0}
    if errs:
        return out
    base = baseline_values(project.config, defn.variables, project)
    if isinstance(defn, StudyDefinition):
        try:
            designs = generate(defn, base)
        except ValueError as exc:
            out["errors"].append({"path": "definition", "message": str(exc)})
            return out
        out["designs"] = designs
        out["n_runs"] = len(designs)
    else:
        out["n_runs"] = (defn.budget) * max(1, len(defn.conditions))
        out["baseline"] = base
    out["targets"] = {v.id: target_info(v.target, project.config).__dict__ for v in defn.variables}
    yr = _yaw_range(defn, project.config)
    if yr and project.config.tunnel.design_yaw_range_deg is None:
        out["notes"] = [f"The tunnel and wake refinement will be sized for yaw {yr[0]:g}°…{yr[1]:g}° so every design shares one mesh per geometry."]
    return out


# ---------------------------------------------------------------------------- lifecycle


def start(project: Project, ctx: AppContext, kind: str, data: dict[str, Any], launch_worker: bool = True) -> dict[str, Any]:
    defn = parse_definition(kind, data)
    errs = validate_definition(project, ctx, kind, defn)
    if errs:
        raise AeroBenchError.make("study.invalid", ErrorCategory.CONFIGURATION, f"The {kind} definition has {len(errs)} problem(s).",
                                  details="\n".join(f"- {e['path']}: {e['message']}" for e in errs))
    cfg = project.config.model_copy(deep=True)
    if cfg.geometry.revision_id is None:
        raise _err("geometry.missing", "Import geometry before starting a study.")
    yr = _yaw_range(defn, cfg)
    if yr and cfg.tunnel.design_yaw_range_deg is None:
        cfg.tunnel.design_yaw_range_deg = yr
    base = baseline_values(cfg, defn.variables, project)
    space = DesignSpace(defn.variables)
    # keep the definition in the project (upsert by id)
    def keep(c: ProjectConfig) -> None:
        lst = c.studies if kind == "study" else c.optimisations
        lst[:] = [x for x in lst if x.id != defn.id] + [defn]  # type: ignore[list-item]

    project.update(keep)
    sid = short_id("study")
    now = utc_now_iso()
    sizing = baseline_extent(project, cfg)
    project.db.insert("studies", {
        "id": sid, "kind": kind, "name": defn.name, "definition_json": defn.model_dump(mode="json"), "status": "running",
        "created_at": now, "updated_at": now, "definition_id": defn.id, "base_config_json": cfg.to_json(), "started_at": now,
        "summary_json": {"sizing_box": sizing.model_dump(mode="json"), "baseline_values": base,
                         "yaw_range": list(yr) if yr else None},
    })
    rows = []
    if isinstance(defn, StudyDefinition):
        for i, d in enumerate(generate(defn, base)):
            rows.append({"id": short_id("design"), "study_id": sid, "idx": i, "origin": d["origin"], "values_json": d["values"],
                         "status": "pending", "attempts": 0, "run_ids_json": [], "created_at": now, "updated_at": now})
    else:
        in_bounds = not space.validate_values(base)
        x0 = space.to_unit(base).tolist() if in_bounds else None
        opt = create_optimizer(defn.algorithm.value, dim=space.dim, n_obj=len(defn.objectives), n_con=len(defn.constraints),
                               budget=defn.budget, batch_size=defn.batch_size, seed=defn.seed, init_samples=defn.initial_samples, x0=x0)
        opt.projector = space.project
        if defn.evaluate_baseline:
            tok = "baseline" if in_bounds else None
            if tok:
                opt.pending["baseline"] = list(x0)  # type: ignore[arg-type]  # counts against the budget
            rows.append({"id": short_id("design"), "study_id": sid, "idx": 0, "origin": "baseline", "values_json": base, "status": "pending",
                         "attempts": 0, "run_ids_json": [], "token": tok, "unit_json": x0, "created_at": now, "updated_at": now,
                         "notes_json": [] if in_bounds else ["The baseline lies outside the variable bounds; it is evaluated for reference only."]})
        project.db.insert("optimiser_state", {"study_id": sid, "algorithm": opt.name, "state_json": opt.state(), "updated_at": now})
    for r in rows:
        project.db.insert("designs", r)
    project.db.log_event("study_started", sid, kind=kind, definition=defn.id)
    if launch_worker:
        ensure_worker_process(project.root)
    return detail(project, sid)


def set_status(project: Project, sid: str, action: str, launch_worker: bool = True) -> dict[str, Any]:
    row = _row(project, sid)
    st = row["status"]
    if action == "pause":
        if st != "running":
            raise _err("study.state", f"Only a running study can be paused (it is {st}).")
        new = "paused"
    elif action == "resume":
        if st != "paused":
            raise _err("study.state", f"Only a paused study can be resumed (it is {st}).")
        new = "running"
    elif action == "cancel":
        if st in ("completed", "cancelled", "failed"):
            raise _err("study.state", f"The study is already {st}.")
        new = "cancelling"
    else:
        raise _err("study.action", f"Unknown action '{action}'.")
    project.db.update("studies", "id", sid, {"status": new, "updated_at": utc_now_iso()})
    project.db.log_event(f"study_{action}", sid)
    if launch_worker:
        ensure_worker_process(project.root)
    return detail(project, sid)


def _row(project: Project, sid: str) -> dict[str, Any]:
    r = project.db.one("SELECT * FROM studies WHERE id = ?", (sid,))
    if r is None:
        raise AeroBenchError.make("study.unknown", ErrorCategory.PROJECT, f"Unknown study {sid}.")
    return dec(r)


def list_studies(project: Project) -> list[dict[str, Any]]:
    out = []
    for r in project.db.all("SELECT id, kind, name, status, created_at, started_at, ended_at, definition_id, message FROM studies ORDER BY created_at DESC"):
        c = {x["status"]: x["n"] for x in project.db.all("SELECT status, COUNT(*) AS n FROM designs WHERE study_id = ? GROUP BY status", (r["id"],))}
        out.append({**r, "counts": c, "n_designs": sum(c.values())})
    return out


def detail(project: Project, sid: str) -> dict[str, Any]:
    row = _row(project, sid)
    row.pop("base_config", None)
    designs = [dec(d) for d in project.db.all("SELECT * FROM designs WHERE study_id = ? ORDER BY idx", (sid,))]
    out = {**row, "designs": designs}
    if row["kind"] == "optimisation":
        st = project.db.one("SELECT state_json, updated_at FROM optimiser_state WHERE study_id = ?", (sid,))
        if st:
            s = dec(st)["state"]
            out["optimiser"] = {"algorithm": s["algorithm"], "told": len(s["observations"]), "pending": len(s["pending"]),
                                "budget": s["budget"], "updated_at": st["updated_at"], "extra": _optimiser_extra(s)}
    out["history"] = history(row, designs)
    return out


def _optimiser_extra(s: dict) -> dict[str, Any]:
    e = s.get("extra") or {}
    if s["algorithm"] == "nsga2":
        return {"generation": e.get("generation"), "pop_size": e.get("pop_size")}
    if s["algorithm"] == "nelder_mead":
        return {"phase": e.get("phase"), "converged": e.get("converged")}
    return {}


def history(row: dict, designs: list[dict]) -> list[dict[str, Any]]:
    """Evaluation order with objective values and the best-so-far (feasible) for plotting."""
    if row["kind"] != "optimisation":
        return []
    defn = row["definition"]
    objs = defn.get("objectives", [])
    if len(objs) != 1:
        return []
    sign = -1 if objs[0].get("sense") == "maximize" else 1
    best = None
    out = []
    for d in sorted(designs, key=lambda x: x["idx"]):
        if d["status"] not in ("completed", "failed"):
            continue
        v = (d.get("objectives") or [{}])[0].get("value") if d["status"] == "completed" else None
        feas = bool(d.get("feasible"))
        if v is not None and feas and (best is None or sign * v < sign * best):
            best = v
        out.append({"idx": d["idx"], "design_id": d["id"], "value": v, "feasible": feas, "status": d["status"], "best": best})
    return out


def export_csv(project: Project, sid: str) -> str:
    row = _row(project, sid)
    designs = [dec(d) for d in project.db.all("SELECT * FROM designs WHERE study_id = ? ORDER BY idx", (sid,))]
    var_ids = [v["id"] for v in row["definition"]["variables"]]
    metric_keys = sorted({k for d in designs for k in ((d.get("metrics") or {}).get("aggregate") or {})})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["design", "idx", "origin", "status", "feasible", "converged"] + var_ids + [f"{m} [{METRIC_INFO.get(m, {}).get('units', '')}]".replace(" []", "") for m in metric_keys]
               + ["runs", "error"])
    for d in designs:
        agg = (d.get("metrics") or {}).get("aggregate") or {}
        conv = (d.get("convergence") or {}).get("all_converged")
        w.writerow([d["id"], d["idx"], d["origin"], d["status"], "" if d.get("feasible") is None else bool(d["feasible"]),
                    "" if conv is None else conv] + [d["values"].get(v) for v in var_ids]
                   + [_fmt(agg.get(m, {}).get("mean")) for m in metric_keys]
                   + [" ".join(r["run_id"] for r in d.get("run_ids") or []), (d.get("error") or {}).get("message", "")])
    return buf.getvalue()


def _fmt(v: Any) -> str:
    return "" if v is None or (isinstance(v, float) and not math.isfinite(v)) else repr(float(v))


def promote_design(project: Project, sid: str, design_id: str) -> dict[str, Any]:
    """Make a design the project's current configuration (geometry, pose, operating point)."""
    row = _row(project, sid)
    d = dec(project.db.one("SELECT * FROM designs WHERE id = ? AND study_id = ?", (design_id, sid)))
    if d is None:
        raise AeroBenchError.make("study.design_unknown", ErrorCategory.PROJECT, f"Unknown design {design_id}.")
    from ..domain.project import ProjectConfig as PC

    base = PC.model_validate(row["base_config"])
    defn: Any = OptimisationDefinition.model_validate(row["definition"]) if row["kind"] == "optimisation" else StudyDefinition.model_validate(row["definition"])
    assignments = {v.target: d["values"][v.id] for v in defn.variables}
    build = build_design(project, base, assignments)
    if not build.ok:
        raise _err("study.design_invalid", "The design cannot be applied: " + build.errors[0]["message"])
    new = build.cfg

    def mutate(c: ProjectConfig) -> None:
        c.geometry.revision_id = new.geometry.revision_id
        c.geometry.transform = new.geometry.transform
        c.geometry.sizing_box = None
        c.vehicle.wheels = new.vehicle.wheels
        c.vehicle.ride_height_offset = new.vehicle.ride_height_offset
        c.vehicle.rake_deg = new.vehicle.rake_deg
        c.operating = new.operating
        c.mesh.base_cell_size = new.mesh.base_cell_size
        c.numerics.iterations = new.numerics.iterations
        c.parameter_values = new.parameter_values

    project.update(mutate)
    project.db.log_event("design_promoted", design_id, study=sid)
    return {"applied": assignments, "geometry_revision_id": new.geometry.revision_id}


# ---------------------------------------------------------------------------- geometry parameters


def set_parameters(project: Project, params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parsed = [GeometryParameter.model_validate(p) for p in params]
    ids = [p.id for p in parsed]
    if len(ids) != len(set(ids)):
        raise _err("parameters.duplicate", "Geometry parameter ids must be unique.")
    comp_ids = {c.id for c in project.config.geometry.components}
    for p in parsed:
        bad = [c for c in p.components + p.frozen_components if c not in comp_ids]
        if bad:
            raise _err("parameters.component", f"Parameter '{p.id}' references unknown component(s): {', '.join(bad)}.")

    def mutate(c: ProjectConfig) -> None:
        c.parameters = parsed
        c.parameter_values = {k: v for k, v in c.parameter_values.items() if k in set(ids)}

    project.update(mutate)
    return [p.model_dump(mode="json") for p in parsed]


def parameter_preview(project: Project, values: dict[str, float], params: Optional[list[dict]] = None, check: bool = True,
                      max_faces: int = 400_000) -> tuple[dict[str, Any], bytes]:
    """Deformed, posed geometry with per-vertex displacement magnitude (mm) + validity check."""
    from ..geometry.repair import simplify_vertex_clustering
    from ..geometry.trisurface import TriSurface
    from ..viz.payload import encode_mesh

    cfg = project.config
    defs = [GeometryParameter.model_validate(p) for p in params] if params is not None else cfg.parameters
    full = parameter_values_complete(defs, values)
    tr = frozen_transform(project, cfg, cfg.geometry.revision_id)  # type: ignore[arg-type]
    cfd, _ = place_in_cfd_frame(project.raw_surface(), tr)
    res = apply_parameters(cfd, cfg.geometry.components, cfg.vehicle.wheels, defs, full)
    chk = check_deformation(cfd, res, defs, check_intersections=check, components=cfg.geometry.components).as_dict() if check else {"valid": None, "errors": [], "warnings": [], "stats": {}}
    vcfg = cfg.model_copy(deep=True)
    vcfg.vehicle.wheels = res.wheels
    disp_surf = TriSurface(np.linalg.norm(res.displacement, axis=1)[:, None].repeat(3, 1), cfd.faces, cfd.regions, cfd.region_names)
    parts, z0, _ = posed_components(res.surface, cfg.geometry.components, vcfg.vehicle, cfg.tunnel.contact_patch_fraction)
    dparts, _, _ = posed_components(disp_surf, cfg.geometry.components, vcfg.vehicle.model_copy(update={"rake_deg": 0.0, "ride_height_offset": 0.0}),
                                    cfg.tunnel.contact_patch_fraction)
    surfs, scal = [], []
    for cid, s in parts.items():
        surfs.append(TriSurface(s.points, s.faces, np.zeros(s.n_faces, np.int32), [cid]))
        scal.append(dparts[cid].points[:, 0])
    merged = TriSurface.merge(surfs)
    scalars = np.concatenate(scal) * 1000.0
    if merged.n_faces > max_faces:
        from scipy.spatial import cKDTree

        lo, hi = merged.bbox()
        cell = float(np.linalg.norm(hi - lo)) / math.sqrt(max_faces) * 0.9
        small, _ = simplify_vertex_clustering(merged, cell)
        _, j = cKDTree(merged.points).query(small.points)
        merged, scalars = small, scalars[j]
    header, blob = encode_mesh(merged.points.astype(np.float32), merged.faces.astype(np.uint32), scalars=scalars.astype(np.float32),
                               part_ids=merged.regions.astype(np.uint16), part_names=merged.region_names,
                               meta={"ground_z": z0, "scalar_name": "Displacement", "scalar_units": "mm", "check": chk})
    return header, blob


def targets(project: Project) -> list[dict[str, Any]]:
    return list_targets(project.config)
