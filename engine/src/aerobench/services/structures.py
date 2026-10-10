"""Structural check of aero elements with CalculiX (ADR-0012).

Workflow: a finished CFD run → its wall loads (pressure and friction per face) → the same
surface as quadratic shells with the chosen sheet or composite sections → supports where the
part is attached → linear static analysis in CalculiX → deflection, stresses and reserve
factors (Tsai-Wu / maximum stress for plies, von Mises for metals and plastics).

``prepare`` builds the structural mesh and maps the loads (seconds; shown before solving).
``start`` writes the CalculiX deck and queues the solver as a job; the worker reads the
results when it finishes (``ingest_structure``).
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import atomic_write_text, sha256_json
from ..core.ids import check_id, short_id, utc_now_iso
from ..domain.project import ProjectConfig
from ..domain.structure import Material, StructureConfig
from ..jobs.manager import JobManager
from ..jobs.spec import JobStatus, ProgramStep
from ..project.db import dec
from ..project.store import Project
from ..structures.ccx import detect_calculix
from ..structures.deck import write_deck
from ..structures.loads import map_loads
from ..structures.materials import BUILTIN, all_materials, load_user_library, save_user_library
from ..structures.mesh import StructMesh, analysed_components, build_structural_mesh
from ..structures.results import assess, parse_dat, summarise
from .context import AppContext
from .runs import run_record

FORMAT = 5  # 2: loads on all S6 nodes; 4: edge division with flat quads; 5: total-force check for one part


def _err(code: str, msg: str, action: str = "", cat: ErrorCategory = ErrorCategory.CONFIGURATION) -> AeroBenchError:
    return AeroBenchError.make(code, cat, msg, action=action)


# ---------------------------------------------------------------------------- materials


def materials_payload(project: Optional[Project]) -> dict[str, Any]:
    return {"builtin": [m.model_dump(mode="json") for m in BUILTIN],
            "user": [m.model_dump(mode="json") for m in load_user_library()],
            "project": [m.model_dump(mode="json") for m in (project.config.structure.materials if project else [])]}


def save_user_material(data: dict[str, Any]) -> dict[str, Any]:
    m = Material.model_validate({**data, "builtin": False})
    if m.id in {b.id for b in BUILTIN}:
        raise _err("materials.builtin_id", f"'{m.id}' is the id of a built-in material; choose another id.")
    lib = [x for x in load_user_library() if x.id != m.id] + [m]
    save_user_library(lib)
    return m.model_dump(mode="json")


def delete_user_material(material_id: str) -> None:
    save_user_library([x for x in load_user_library() if x.id != material_id])


def set_structure(project: Project, data: dict[str, Any]) -> dict[str, Any]:
    st = StructureConfig.model_validate(data)
    project.update(lambda c: setattr(c, "structure", st))
    return project.config.structure.model_dump(mode="json")


# ---------------------------------------------------------------------------- inputs


def eligible_runs(project: Project) -> list[dict[str, Any]]:
    rows = project.db.all("SELECT id, label, status, created_at, metrics_json FROM runs WHERE status IN ('completed','stopped','interrupted') "
                          "ORDER BY created_at DESC")
    out = []
    for r in rows:
        m = json.loads(r["metrics_json"]) if r.get("metrics_json") else None
        out.append({"id": r["id"], "label": r["label"], "status": r["status"], "created_at": r["created_at"],
                    "Cd": ((m or {}).get("metrics") or {}).get("Cd"), "downforce_N": ((m or {}).get("metrics") or {}).get("downforce_N")})
    return out


def _load_case(project: Project, st: StructureConfig) -> tuple[dict[str, Any], ProjectConfig]:
    runs = eligible_runs(project)
    rid = st.run_id or (runs[0]["id"] if runs else None)
    if rid is None:
        raise _err("structure.no_run", "There is no finished flow simulation whose loads could be applied.",
                   "Run the flow simulation first (Run page).")
    r = run_record(project, check_id(rid, "run id"), include_snapshot=True)
    if r["status"] not in ("completed", "stopped", "interrupted"):
        raise _err("structure.run_not_ready", f"Run '{r['label']}' has no results yet.")
    snap = ProjectConfig.model_validate(r["config_snapshot"])
    cfg = snap.model_copy(update={"structure": st})  # the run's geometry and pose; today's structural settings
    return r, cfg


def load_scale(st: StructureConfig, run_speed: float) -> float:
    s = st.load_factor
    if st.design_speed:
        s *= (st.design_speed / run_speed) ** 2
    return float(s)


def _cfd_component_forces(project: Project, run: dict[str, Any], comps: list[str], cfg: ProjectConfig) -> Optional[np.ndarray]:
    """Total force of the parts from OpenFOAM's forces function objects (last iteration): one
    per part when the case has them, else the case total when the parts are all of the model."""
    from ..results.ingest import load_force_history

    case = project.root / run["path"]

    def last(fo: str) -> Optional[np.ndarray]:
        h = load_force_history(case, fo_name=fo, default_cofr=(0.0, 0.0, 0.0))
        return np.asarray(h.force[-1]) if h is not None and len(h.time) else None

    per = [last(f"forces_{c}") for c in comps]
    if all(f is not None for f in per):
        return np.sum(per, axis=0)
    if set(comps) == {c.id for c in cfg.geometry.components}:
        return last("forces_total")
    return None


def prepare(project: Project, ctx: AppContext, viz, st: Optional[StructureConfig] = None) -> dict[str, Any]:
    """Structural mesh + mapped loads for the current settings (cached). Returns the summary."""
    st = st or project.config.structure
    run, cfg = _load_case(project, st)
    key = sha256_json({"v": FORMAT, "run": run["id"], "iterations": run.get("iterations"), "components": analysed_components(cfg),
                       "model": st.model.value, "side": st.plate_side, "max": st.max_elements})[:20]
    cdir = project.cache_dir("structure", "prep", key)
    meta_p = cdir / "prepare.json"
    if meta_p.exists() and (cdir / "mesh.npz").exists():
        out = json.loads(meta_p.read_text())
        out["cached"] = True
        out["load_scale"] = load_scale(st, cfg.operating.speed)
        return out

    def decimate(surf, target):
        from ..geometry.trisurface import TriSurface

        p, f, r = viz.decimate(surf.points, surf.faces, surf.regions, target, cdir)
        return (TriSurface(p, f, r.astype(np.int32), list(surf.region_names)),
                f"The parts were coarsened from {surf.n_faces} to {len(f)} triangles for the structural model.")

    mesh = build_structural_mesh(project, cfg, decimate=decimate)
    loads_path, info = viz.surface_loads(project, run["id"], mesh.part_ids)
    with np.load(loads_path) as d:
        data = {k: d[k] for k in d.files}
    p_ref = float(cfg.operating.pressure or 0.0)
    mapped = map_loads(mesh, data, rho=cfg.physics.fluid.density, p_ref=p_ref)
    cfd = _cfd_component_forces(project, run, mesh.part_ids, cfg)
    np.savez(cdir / "mesh.npz", points=mesh.points, tris=mesh.tris, part=mesh.part, part_ids=np.array(json.dumps(mesh.part_ids)),
             closed=np.array(json.dumps(mesh.closed_parts)))
    np.savez(cdir / "loads.npz", nodal=mapped.nodal, traction=mapped.tri_traction, total=mapped.total)
    diff = None
    if cfd is not None and np.linalg.norm(cfd) > 0:
        diff = float(np.linalg.norm(mapped.total - cfd) / np.linalg.norm(cfd))
    out = {
        "key": key, "run_id": run["id"], "run_label": run["label"], "run_speed": cfg.operating.speed,
        "mesh": {**mesh.quality(), "model": mesh.model, "parts": mesh.part_ids, "closed_parts": mesh.closed_parts},
        "loads": {"total": mapped.total.tolist(), "pressure": mapped.pressure_total.tolist(), "viscous": mapped.viscous_total.tolist(),
                  "cfd_total": cfd.tolist() if cfd is not None else None, "difference": diff, "faces": mapped.faces,
                  "max_distance": mapped.max_distance, "correction": mapped.correction, "time": info.get("time")},
        "notes": mesh.notes + mapped.notes, "cached": False,
    }
    if diff is not None and diff > 0.03:
        out["notes"].append(f"The applied loads differ from the forces OpenFOAM integrated by {diff * 100:.1f} %; check that the run "
                            "finished and that the part's surface is the one simulated.")
    atomic_write_text(meta_p, json.dumps(out))
    out["load_scale"] = load_scale(st, cfg.operating.speed)
    return out


def _prep_data(project: Project, key: str) -> tuple[StructMesh, dict[str, np.ndarray]]:
    cdir = project.cache_dir("structure", "prep", check_id(key, "key"))
    with np.load(cdir / "mesh.npz") as d:
        mesh = StructMesh(points=d["points"], tris=d["tris"], part=d["part"], part_ids=json.loads(str(d["part_ids"])),
                          closed_parts=json.loads(str(d["closed"])))
    with np.load(cdir / "loads.npz") as d:
        loads = {k: d[k] for k in d.files}
    return mesh, loads


# ---------------------------------------------------------------------------- previews


def _abg(points: np.ndarray, tris: np.ndarray, scalars: np.ndarray, vectors: Optional[np.ndarray], meta: dict[str, Any]) -> tuple[dict, bytes]:
    from ..viz.payload import encode_mesh

    return encode_mesh(points.astype(np.float32), tris.astype(np.uint32), scalars=scalars.astype(np.float32),
                       vectors=vectors.astype(np.float32) if vectors is not None else None, meta=meta)


def _node_field(mesh: StructMesh, per_tri: np.ndarray, how: str = "mean") -> np.ndarray:
    n = len(mesh.points)
    if how == "min":
        out = np.full(n, np.inf)
        for a in range(3):
            np.minimum.at(out, mesh.tris[:, a], per_tri)
        return out
    if how == "max":
        out = np.full(n, -np.inf)
        for a in range(3):
            np.maximum.at(out, mesh.tris[:, a], per_tri)
        return out
    acc, cnt = np.zeros(n), np.zeros(n)
    for a in range(3):
        np.add.at(acc, mesh.tris[:, a], per_tri)
        np.add.at(cnt, mesh.tris[:, a], 1)
    return acc / np.maximum(cnt, 1)


def loads_preview(project: Project, ctx: AppContext, viz) -> tuple[dict, bytes]:
    """The structural surface coloured by the load normal to it (kPa, + pressing inwards)."""
    st = project.config.structure
    info = prepare(project, ctx, viz, st)
    mesh, loads = _prep_data(project, info["key"])
    n = mesh.normals()
    pn = -(loads["traction"] * n).sum(axis=1) / 1000.0 * info["load_scale"]  # + = pushing into the part
    from ..structures.deck import support_nodes

    held = {sp.id: int(len(support_nodes(mesh.points, sp.shape))) for sp in st.supports}
    meta = {"scalar_name": "Load normal to the surface (+ pressing in)", "scalar_units": "kPa", "prepare": info,
            "support_nodes": held}
    return _abg(mesh.points, mesh.tris, _node_field(mesh, pn), None, meta)


# ---------------------------------------------------------------------------- analysis


def start(project: Project, ctx: AppContext, viz, label: str = "") -> dict[str, Any]:
    cc = detect_calculix(getattr(getattr(ctx.settings, "calculix", None), "ccx", None))
    if not cc["available"]:
        raise _err("structure.no_ccx", cc["message"], cat=ErrorCategory.DEPENDENCY)
    st = project.config.structure
    info = prepare(project, ctx, viz, st)
    mesh, loads = _prep_data(project, info["key"])
    mats = all_materials(st.materials)
    sid = short_id("fea")
    rel = Path("structures") / sid
    sdir = project.root / rel
    sdir.mkdir(parents=True, exist_ok=False)
    scale = info["load_scale"]
    try:
        deck = write_deck(sdir / "model.inp", mesh, st, mats, loads["nodal"], scale)
        np.savez(sdir / "mesh.npz", points=mesh.points, tris=mesh.tris, part=mesh.part)
        used = {m for layers in deck.element_layers for m in layers}
        snapshot = {"structure": st.model_dump(mode="json"), "materials": {k: mats[k].model_dump(mode="json") for k in used},
                    "prepare": info, "deck": deck.as_dict(), "ccx": cc}
        atomic_write_text(sdir / "analysis.json", json.dumps(snapshot))
    except BaseException:
        shutil.rmtree(sdir, ignore_errors=True)  # nothing was queued: leave no half-written analysis behind
        raise
    cores = max(1, min(ctx.max_cores(), project.config.execution.n_procs or 1))
    env = {"OMP_NUM_THREADS": str(cores), "CCX_NPROC_STIFFNESS": str(cores), "CCX_NPROC_EQUATION_SOLVER": str(cores),
           "NUMBER_OF_CPUS": str(cores), "OPENBLAS_NUM_THREADS": "1"}
    step = ProgramStep(name="calculix", argv=[cc["ccx"], "-i", "model"], env=env, success_text="Job finished")
    jm = JobManager(project)
    job = jm.create("fea", sdir, [step], ctx.provider().serialise(), cores=cores, target_kind="structure", target_id=sid)
    project.db.insert("structure_runs", {"id": sid, "created_at": utc_now_iso(), "label": label or _default_label(st, info),
                                         "status": "queued", "job_id": job, "run_id": info["run_id"], "path": str(rel.as_posix()),
                                         "config_snapshot_json": json.dumps(snapshot), "summary_json": None, "message": None})
    from ..jobs.worker import ensure_worker_process

    ensure_worker_process(project.root)
    return {"id": sid, "job_id": job, "elements": int(mesh.n_tris), "mass": deck.mass}


def _default_label(st: StructureConfig, info: dict[str, Any]) -> str:
    secs = ", ".join(s.name for s in st.sections) or "no section"
    return f"{secs} @ {info['run_label']}" + (f" x{info['load_scale']:.2f}" if abs(info["load_scale"] - 1) > 1e-6 else "")


def ingest_structure(project: Project, sid: str) -> dict[str, Any]:
    row = dec(project.db.one("SELECT * FROM structure_runs WHERE id = ?", (sid,)))
    if row is None:
        return {}
    jm = JobManager(project)
    status = jm.refresh(row["job_id"])
    sdir = project.root / row["path"]
    snap = row["config_snapshot"]
    if status != JobStatus.COMPLETED or not (sdir / "model.dat").exists():
        msg = "Cancelled." if status == JobStatus.CANCELLED else _ccx_failure(sdir, jm, row["job_id"])
        project.db.update("structure_runs", "id", sid, {"status": "cancelled" if status == JobStatus.CANCELLED else "failed", "message": msg})
        return {"status": "failed", "message": msg}
    res = parse_dat(sdir / "model.dat")
    deck = snap["deck"]
    mats = {k: Material.model_validate(v) for k, v in snap["materials"].items()}
    st = StructureConfig.model_validate(snap["structure"])
    with np.load(sdir / "mesh.npz") as d:
        points, tris = d["points"], d["tris"]
    S = res.get("S:EALL")
    U = res.get("U:NCORNER")
    if S is None or U is None:
        msg = "CalculiX finished without writing stresses or displacements; see the log."
        project.db.update("structure_runs", "id", sid, {"status": "failed", "message": msg})
        return {"status": "failed", "message": msg}
    Uc = np.zeros((len(points), 3))
    Uc[U[:, 0].astype(np.int64) - 1] = U[:, 1:4]
    asm = assess(S, len(tris), deck["element_section"], deck["element_layers"], deck["section_kinds"], mats)
    cent = points[tris].mean(axis=1)
    summary = summarise(asm, Uc, cent, st.required_reserve, deck["mass"], res.get("RF:NSUP"), np.asarray(deck["applied_total"]),
                        np.asarray(deck["weight_vector"]), deck["element_section"], [s.name for s in st.sections],
                        held=np.asarray(deck.get("held_by_supports") or [0.0, 0.0, 0.0]), reactions_exact=deck.get("reactions_exact", True))
    summary["elements"] = int(len(tris))
    summary["load_scale"] = snap["prepare"].get("load_scale")
    summary["notes"] = []
    for name in deck.get("centred_sections") or []:
        summary["notes"].append(f"Section '{name}' is centred on the surface (half its thickness outside): it does not fit inside "
                                "the tightest curves of the part at its full thickness.")
    if summary["equilibrium_error"] is not None and summary["equilibrium_error"] > 1e-3:
        summary["notes"].append(f"The support reactions differ from the applied loads by {summary['equilibrium_error'] * 100:.2f} %.")
    big = summary["max_displacement"] > 0.2 * float(np.linalg.norm(points.max(0) - points.min(0)))
    if big:
        summary["notes"].append("The deflection is large compared with the part: the linear analysis is not valid "
                                               "for it, and the part is probably not held well enough (check the supports).")
    np.savez(sdir / "results.npz", U=Uc, rf=asm.element_rf, vm=asm.element_vm, layer=asm.element_layer, mode=asm.element_mode)
    atomic_write_text(sdir / "summary.json", json.dumps(summary))
    project.db.update("structure_runs", "id", sid, {"status": "completed", "summary_json": json.dumps(summary), "message": None})
    project.db.log_event("structure_ingested", sid, min_reserve=summary.get("min_reserve"))
    return summary


def _ccx_failure(sdir: Path, jm: JobManager, job_id: str) -> str:
    st = jm.state(job_id)
    text = ""
    if st and st.steps and st.steps[0].log:
        p = jm.project.job_dir(job_id) / st.steps[0].log
        if p.exists():
            text = p.read_text(errors="replace")[-20000:]
    for line in text.splitlines():
        if "*ERROR" in line:
            return "CalculiX stopped: " + line.strip().lstrip("*")
    if "zero pivot" in text.lower() or "singular" in text.lower():
        return "The structure can move freely (singular stiffness): the supports do not hold it in all directions."
    return "CalculiX did not finish; see its output in Logs."


def list_analyses(project: Project) -> list[dict[str, Any]]:
    rows = project.db.all("SELECT id, created_at, label, status, job_id, run_id, summary_json, message FROM structure_runs ORDER BY created_at DESC")
    out = []
    for r in rows:
        r = dec(r)
        out.append(r)
    return out


def result_payload(project: Project, sid: str, field: str = "reserve") -> tuple[dict, bytes]:
    row = dec(project.db.one("SELECT * FROM structure_runs WHERE id = ?", (check_id(sid, "analysis id"),)))
    if row is None or row["status"] != "completed":
        raise _err("structure.no_result", "This structural analysis has no results.")
    sdir = project.root / row["path"]
    with np.load(sdir / "mesh.npz") as d:
        mesh = StructMesh(points=d["points"], tris=d["tris"], part=d["part"], part_ids=[])
    with np.load(sdir / "results.npz") as d:
        U, rf, vm = d["U"], d["rf"], d["vm"]
    if field == "displacement":
        sc, name, units = np.linalg.norm(U, axis=1) * 1000.0, "Displacement", "mm"
    elif field == "stress":
        sc, name, units = _node_field(mesh, vm / 1e6, "max"), "Von Mises stress", "MPa"
    else:
        r = np.where(np.isfinite(rf), rf, 1e3)
        sc, name, units = _node_field(mesh, np.minimum(r, 1e3), "min"), "Reserve factor", "-"
    meta = {"scalar_name": name, "scalar_units": units, "summary": row["summary"],
            "length": float(np.linalg.norm(mesh.points.max(0) - mesh.points.min(0)))}
    return _abg(mesh.points, mesh.tris, sc, U, meta)


def status(project: Project, ctx: AppContext) -> dict[str, Any]:
    cfg = project.config
    return {"calculix": detect_calculix(), "runs": eligible_runs(project), "structure": cfg.structure.model_dump(mode="json"),
            "parts": [{"id": c.id, "name": c.name, "role": c.role.value} for c in cfg.geometry.components],
            "analysed": analysed_components(cfg) if cfg.geometry.components else [], "analyses": list_analyses(project)}


def support_presets(project: Project) -> list[dict[str, Any]]:
    """Typical attachments placed from the analysed parts' extent (posed as in the CFD case)."""
    cfg = project.config
    cfd = project.cfd_surface(cfg=cfg)
    from ..geometry.pose import posed_components

    parts, _, _ = posed_components(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
    comps = analysed_components(cfg)
    pts = np.vstack([parts[c].points for c in comps if parts[c].n_faces])
    lo, hi = pts.min(0), pts.max(0)
    span = hi - lo
    m = 0.02 * float(np.linalg.norm(span))
    ax = 1 if span[1] >= span[0] else 0  # the long horizontal direction (span of a wing)
    other = 1 - ax

    def box(a0, a1, o0=None, o1=None, z0=None, z1=None):
        mn, mx = lo - m, hi + m
        mn, mx = mn.copy(), mx.copy()
        mn[ax], mx[ax] = a0, a1
        if o0 is not None:
            mn[other], mx[other] = o0, o1
        if z0 is not None:
            mn[2], mx[2] = z0, z1
        return {"kind": "box", "min": [round(float(v), 4) for v in mn], "max": [round(float(v), 4) for v in mx]}

    w = 0.02 * span[ax]
    c0, c1 = lo[other] + 0.15 * span[other], lo[other] + 0.65 * span[other]  # the middle of the chord

    def mount(at: float) -> dict[str, Any]:
        # the lower surface there: from the lowest point of that patch up to a third of the local thickness
        win = pts[(np.abs(pts[:, ax] - at) <= w) & (pts[:, other] >= c0) & (pts[:, other] <= c1)]
        z_lo, z_hi = (float(win[:, 2].min()), float(win[:, 2].max())) if len(win) else (float(lo[2]), float(hi[2]))
        return box(at - w, at + w, c0, c1, z_lo - m, z_lo + 0.35 * (z_hi - z_lo))

    return [
        {"preset": "ends", "name": "Both ends (endplates)", "shapes": [box(lo[ax] - m, lo[ax] + 1.5 * w), box(hi[ax] - 1.5 * w, hi[ax] + m)]},
        {"preset": "pylons", "name": "Two mounts under the element at a quarter of the span",
         "shapes": [mount(lo[ax] + 0.25 * span[ax]), mount(hi[ax] - 0.25 * span[ax])]},
        {"preset": "centre", "name": "One central mount under the element", "shapes": [mount(0.5 * (lo[ax] + hi[ax]))]},
        {"preset": "rear_edge", "name": "Along the rear edge (bolted to the floor)", "shapes": [
            {"kind": "box", "min": [round(float(hi[0] - 0.08 * span[0]), 4), round(float(lo[1] - m), 4), round(float(lo[2] - m), 4)],
             "max": [round(float(hi[0] + m), 4), round(float(hi[1] + m), 4), round(float(hi[2] + m), 4)]}]},
    ]


def default_section(project: Project) -> dict[str, Any]:
    """A sensible first section: a 2 mm carbon twill skin (or plate) with 0° along the span."""
    cfg = project.config
    try:
        cfd = project.cfd_surface(cfg=cfg)
        lo, hi = cfd.bbox()
        zero = (0.0, 1.0, 0.0) if (hi[1] - lo[1]) >= (hi[0] - lo[0]) else (1.0, 0.0, 0.0)
    except Exception:  # noqa: BLE001
        zero = (0.0, 1.0, 0.0)
    plies = [{"material": "cfrp_twill_t300", "thickness": 0.00022, "angle": a} for a in (0, 45, 0, 45, 0)]
    return {"id": "skin", "name": "Carbon skin, 5 x twill", "components": [], "kind": "laminate", "plies": plies, "symmetric": False,
            "zero_direction": list(zero)}


def angle_between(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))))))
