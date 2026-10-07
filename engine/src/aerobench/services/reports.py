"""Engineering reports: self-contained HTML (charts as inline SVG, images embedded), CSV
data and a JSON record; PDF is printed from the HTML by the desktop shell.

Reports state convergence and validity problems prominently and never invent values:
every number is read from the stored run records and OpenFOAM outputs.
"""

from __future__ import annotations

import base64
import shlex
import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, PackageLoader, select_autoescape

from ..aero.forces import METRIC_INFO
from ..core.errors import AeroBenchError, ErrorCategory
from ..core.fs import atomic_write_text
from ..core.ids import short_id, utc_now_iso
from ..domain.project import ProjectConfig
from ..project.db import dec
from ..project.store import Project
from ..reports import svg
from ..results.ingest import series_payload
from ..version import APP_DISPLAY_NAME, APP_VERSION
from .compare import compare_runs
from .runs import run_record

RUN_METRICS = ["Cd", "Cl", "Cl_front", "Cl_rear", "Cs", "Cm_pitch", "Cm_yaw", "Cm_roll", "CdA", "ClA", "drag_N", "lift_N",
               "downforce_N", "downforce_front_N", "downforce_rear_N", "side_N", "balance_front_pct", "efficiency"]
REPORT_PRESETS = ["cp_body", "centreline_cp", "rear_wake", "underfloor_velocity"]
CONV_BADGE = {"converged": ("b-ok", "converged"), "converged_oscillating": ("b-ok", "converged (bounded oscillation)"),
              "not_converged": ("b-warn", "not converged"), "diverged": ("b-bad", "diverged"), "insufficient_data": ("b-warn", "insufficient data")}

_env = Environment(loader=PackageLoader("aerobench.reports", "templates"), autoescape=select_autoescape(["html", "j2"]))


def _f(v: Any, digits: int = 4) -> str:
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            return "—"
        if v != 0 and (abs(v) >= 1e5 or abs(v) < 1e-3):
            return f"{v:.{min(digits, 3) - 1}e}"
        return f"{v:.{digits}g}" if abs(v) >= 1 else f"{v:.{digits}f}".rstrip("0").rstrip(".") if v else "0"
    return str(v)


def _metric_label(k: str) -> str:
    i = METRIC_INFO.get(k, {})
    u = i.get("units", "")
    return f"{k}" + (f" [{u}]" if u and u != "-" else "")


# ---------------------------------------------------------------------------- run sections


def _run_sections(project: Project, r: dict[str, Any], viz, images: bool) -> tuple[list[dict], list[dict]]:
    cfg = ProjectConfig.model_validate(r["config_snapshot"])
    res = r.get("metrics") or {}
    m = res.get("metrics") or {}
    conv = r.get("convergence") or {}
    callouts: list[dict] = []
    if r["status"] != "completed":
        callouts.append({"level": "bad", "text": f"Run {r['id']} is {r['status']}; results below are incomplete or absent."})
    cs = conv.get("status")
    if cs in ("not_converged", "insufficient_data"):
        callouts.append({"level": "", "text": f"Run {r['id']} did not meet the convergence criteria: {conv.get('summary', '')}"})
    elif cs == "diverged":
        callouts.append({"level": "bad", "text": f"Run {r['id']} diverged; its force values are not physically meaningful."})
    for d in r.get("diagnostics") or []:
        callouts.append({"level": "bad" if d.get("severity", "error") == "error" else "", "text": f"{d.get('summary')} {d.get('action') or ''}"})

    mesh = dec(project.db.one("SELECT * FROM mesh_revisions WHERE id = ?", (r.get("mesh_revision_id"),))) or {}
    geo = dec(project.db.one("SELECT * FROM geometry_revisions WHERE id = ?", (r.get("geometry_revision_id"),))) or {}
    resolved = r.get("resolved") or {}
    ref = res.get("reference") or {}
    op = cfg.operating
    sections: list[dict] = []

    # --- summary
    kv = [("Run", f"{r.get('label')} ({r['id']})"), ("Status", r["status"]),
          ("Convergence", CONV_BADGE.get(cs, ("", cs or "—"))[1]),
          ("Speed / yaw", f"{op.speed:g} m/s ({op.speed * 3.6:.1f} km/h) / {op.yaw_deg:g}°"),
          ("Ground / wheels", f"{op.ground.value} ground, wheels {'rotating' if op.wheels_rotate else 'stationary'}"),
          ("Iterations (averaging window)", f"{res.get('iterations', '—')} ({_f((res.get('averaging_window') or [None])[0], 5)}–"
                                            f"{_f((res.get('averaging_window') or [None, None])[1], 5)})"),
          ("Wall time", f"{(r.get('wall_seconds') or 0) / 60:.1f} min on {r.get('n_procs')} core(s)")]
    key_rows = []
    for k in ("Cd", "Cl", "Cl_front", "Cl_rear", "balance_front_pct", "CdA", "drag_N", "downforce_N"):
        if k in m:
            key_rows.append([METRIC_INFO.get(k, {}).get("label", k), _metric_label(k), _f(m[k], 5), METRIC_INFO.get(k, {}).get("sign", "")])
    sections.append({"title": "Summary", "blocks": [{"kind": "kv", "rows": kv},
                                                    {"kind": "table", "title": "Key results", "header": ["Quantity", "Symbol", "Value", "Sign convention"],
                                                     "rows": key_rows, "num_cols": [2]}]})
    if m.get("balance_front_pct") is None and m.get("downforce_front_N") is not None:
        sections[-1]["blocks"].append({"kind": "callout", "level": "", "text": "The aero balance (front share of total downforce) is undefined "
                                       "for this run: the vehicle makes net lift or near-zero downforce, so the ratio has no engineering "
                                       "meaning. Use the front and rear downforce values instead."})
    elif m.get("balance_front_pct") is not None and abs(m.get("downforce_N") or 0) < 0.05 * abs(m.get("drag_N") or 1):
        sections[-1]["blocks"].append({"kind": "callout", "level": "", "text": "Total downforce is small compared with drag; treat the aero "
                                       "balance (front share of total downforce) with care."})

    # --- full results
    rows = [[METRIC_INFO.get(k, {}).get("label", k), _metric_label(k), _f(m.get(k), 5), _f((res.get("metrics_last") or {}).get(k), 5),
             METRIC_INFO.get(k, {}).get("sign", "")] for k in RUN_METRICS if k in m]
    blocks: list[dict] = [{"kind": "table", "header": ["Quantity", "Symbol", "Window mean", "Last iteration", "Sign convention"], "rows": rows,
                           "num_cols": [2, 3]}]
    pv = res.get("pressure_viscous")
    if pv:
        blocks.append({"kind": "kv", "title": "Drag decomposition", "rows": [("Pressure drag Cd,p", _f(pv["Cd_pressure"], 5)),
                                                                             ("Viscous drag Cd,v", _f(pv["Cd_viscous"], 5))]})
    comps = res.get("components") or {}
    if comps:
        crow = []
        for cid, c in comps.items():
            cm = c.get("metrics") or c
            crow.append([cid, _f(cm.get("Cd"), 4), _f(cm.get("Cl"), 4), _f(cm.get("Cs"), 4), _f(cm.get("drag_N"), 4),
                         _f(cm.get("downforce_N"), 4), _f(cm.get("side_N"), 4)])
        blocks.append({"kind": "table", "title": "Per-component forces",
                       "header": ["Component", "Cd", "Cl", "Cs", "Drag [N]", "Downforce [N]", "Side force [N]"], "rows": crow,
                       "num_cols": [1, 2, 3, 4, 5, 6], "caption": "Component coefficients use the vehicle reference values; they sum to the totals."})
    blocks.append({"kind": "kv", "title": "Reference values", "rows": [
        ("Area", f"{_f(ref.get('area'), 5)} m²"), ("Length", f"{_f(ref.get('length'), 5)} m"),
        ("Density", f"{_f(ref.get('density'), 5)} kg/m³"), ("Velocity", f"{_f(ref.get('velocity'), 5)} m/s"),
        ("Dynamic pressure", f"{_f(ref.get('q'), 5)} Pa"), ("Moment centre", ", ".join(_f(x, 4) for x in ref.get("moment_centre") or []) + " m"),
        ("Wheelbase", f"{_f(ref.get('wheelbase'), 5)} m"), ("Axes", ref.get("axes", "—"))]})
    sections.append({"title": "Aerodynamic results", "blocks": blocks})

    # --- convergence
    cblocks: list[dict] = []
    try:
        ser = series_payload(project.root / r["path"], cfg, resolved.get("ground_z", 0.0), max_points=1500)
    except Exception:  # noqa: BLE001 - charts are optional, the table below is not
        ser = {}
    if ser.get("residuals"):
        rs = ser["residuals"]
        t = rs.get("time") or []
        cblocks.append({"kind": "svg", "svg": svg.line_chart({k: (t, v) for k, v in rs.items() if k != "time"}, "Initial residuals",
                                                             "iteration", "residual", logy=True),
                        "caption": "Initial residuals per solved field (log scale)."})
    if ser.get("forces"):
        fs = ser["forces"]
        t = fs.get("time") or []
        win = res.get("averaging_window")
        cblocks.append({"kind": "svg", "svg": svg.line_chart({k: (t, fs[k]) for k in ("Cd", "Cl", "Cl_front", "Cl_rear") if k in fs},
                                                             "Force coefficients", "iteration", "coefficient", band=tuple(win) if win else None),
                        "caption": "Shaded band: averaging window used for the reported values."})
    crit = conv.get("criteria") or []
    if crit:
        cblocks.append({"kind": "table", "title": "Convergence criteria (criteria version " + str(conv.get("criteria_version", "")) + ")",
                        "header": ["Criterion", "Value", "Threshold", "Result"],
                        "rows": [[c["name"], _f(c.get("value"), 4), _f(c.get("threshold"), 4),
                                  "pass" if c.get("passed") else ("—" if c.get("passed") is None else "open")] for c in crit], "num_cols": [1, 2]})
    if conv.get("summary"):
        cblocks.insert(0, {"kind": "text", "text": conv["summary"]})
    sections.append({"title": "Convergence", "blocks": cblocks or [{"kind": "text", "text": "No convergence data recorded."}]})

    # --- flow visualisation
    if images:
        iblock = _preset_images(project, r["id"], viz)
        sections.append({"title": "Flow visualisation", "blocks": iblock})

    # --- setup
    t = cfg.tunnel
    q = mesh.get("quality") or {}
    setup = [{"kind": "kv", "title": "Geometry", "rows": [
        ("Revision", f"{geo.get('label') or ''} ({geo.get('id')}, {geo.get('kind')})"), ("Triangles", _f(geo.get("n_faces"), 9)),
        ("Components", ", ".join(f"{c.id} ({c.role.value})" for c in cfg.geometry.components)),
        ("Wheels", "; ".join(f"{w.position.value}: r={w.radius:.3f} m, {w.rotation.value}" for w in cfg.vehicle.wheels) or "none"),
        ("Ride-height offset / rake", f"{cfg.vehicle.ride_height_offset * 1000:.1f} mm / {cfg.vehicle.rake_deg:g}°")]},
        {"kind": "kv", "title": "Virtual wind tunnel", "rows": [
            ("Extents (vehicle lengths)", f"upstream {t.upstream:g}, downstream {t.downstream:g}, lateral {t.lateral:g}, top {t.top:g}"),
            ("Blockage ratio", "—" if (mesh.get("plan") or {}).get("blockage_ratio") is None else f"{(mesh.get('plan') or {})['blockage_ratio'] * 100:.2f} %"), ("Ground", f"{op.ground.value} (yaw mode {op.yaw_ground_mode.value})"),
            ("Symmetry", t.symmetry.value), ("Far field", str(t.farfield_bc.value)),
            ("Design yaw range", "—" if t.design_yaw_range_deg is None else f"{t.design_yaw_range_deg[0]:g}° … {t.design_yaw_range_deg[1]:g}°")]},
        {"kind": "kv", "title": "Mesh", "rows": [
            ("Mesh revision", f"{mesh.get('id', '—')}"), ("Cells", _f(mesh.get("cells"), 9)),
            ("Max non-orthogonality", _f(q.get("max_non_ortho"), 4)), ("Max skewness", _f(q.get("max_skewness"), 4)),
            ("Failed checks", _f(q.get("failed_checks"), 3)), ("Preset", str(cfg.mesh.preset.value))]},
        {"kind": "kv", "title": "Physics and numerics", "rows": [
            ("Fluid", f"ρ = {cfg.physics.fluid.density:g} kg/m³, ν = {cfg.physics.fluid.kinematic_viscosity:.4g} m²/s"),
            ("Turbulence", f"{cfg.physics.turbulence_model.value} ({cfg.physics.wall_treatment.value})"),
            ("Numerics preset", f"{cfg.numerics.preset.value} (v{cfg.numerics.preset_version})"),
            ("Iterations", f"{cfg.numerics.iterations} (averaging the final {cfg.numerics.averaging_fraction * 100:.0f}%)")]}]
    sections.append({"title": "Setup", "blocks": setup})

    # --- provenance
    prov = [("Solver", f"OpenFOAM {r.get('foam_version')} ({r.get('foam_fork')}) via provider '{r.get('provider')}'"),
            ("ParaView", r.get("paraview_version") or "—"), ("Application", f"{APP_DISPLAY_NAME} {r.get('app_version')}"),
            ("Configuration hash", r.get("config_hash", "")[:16]), ("Solve-stage hash", (r.get("solve_hash") or "")[:16]),
            ("Run folder", r.get("path")), ("Started / ended", f"{r.get('started_at')} / {r.get('ended_at')}"),
            ("Hardware", _hw(r.get("hardware") or {}))]
    if r.get("reproduces_run_id"):
        prov.append(("Reproduces", r["reproduces_run_id"]))
    pblocks: list[dict] = [{"kind": "kv", "rows": prov}]
    ov = (resolved.get("overrides") or {})
    if any(ov.values()) if isinstance(ov, dict) else False:
        pblocks.append({"kind": "callout", "level": "info", "text": "Expert overrides were applied to this run: " + json.dumps(ov)[:600]})
    try:
        from ..jobs.manager import JobManager

        cmds = JobManager(project).commands(r["job_id"]) if r.get("job_id") else []
        if cmds:
            pblocks.append({"kind": "pre", "title": "Executed commands", "text": "\n".join(f"[{c.get('step')}] " + shlex.join(c.get("argv") or []) for c in cmds)})
    except Exception:  # noqa: BLE001
        pass
    sections.append({"title": "Provenance", "blocks": pblocks})
    return sections, callouts


def _hw(h: dict) -> str:
    parts = [h.get("processor"), f"{h.get('physical_cores', '?')} physical cores" if h else None,
             f"{h.get('memory_gb', '?')} GB RAM" if h else None, h.get("os")]
    return ", ".join(str(p) for p in parts if p)


def _preset_images(project: Project, run_id: str, viz) -> list[dict]:
    if viz is None:
        return [{"kind": "text", "text": "Flow images were not requested."}]
    try:
        avail = viz.available()
    except Exception:  # noqa: BLE001
        avail = {"available": False}
    if not avail.get("available"):
        return [{"kind": "callout", "level": "", "text": "ParaView was not available when this report was generated, so no flow images are included."}]
    items = []
    errors = []
    try:
        presets = {p["id"]: p for p in viz.presets(project, run_id)}
    except Exception as exc:  # noqa: BLE001
        return [{"kind": "callout", "level": "", "text": f"Flow images unavailable: {exc}"}]
    for pid in REPORT_PRESETS:
        if pid not in presets:
            continue
        try:
            path = viz.preset_screenshot(project, run_id, pid, size=(1400, 800))
            if path and Path(path).exists():
                items.append({"src": "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode(), "caption": presets[pid]["label"]})
        except Exception as exc:  # noqa: BLE001 - one failed image must not fail the report
            errors.append(f"{presets[pid]['label']}: {exc}")
    out: list[dict] = [{"kind": "images", "pics": items}] if items else []
    if errors:
        out.append({"kind": "callout", "level": "", "text": "Some images could not be rendered: " + "; ".join(errors)[:800]})
    return out or [{"kind": "text", "text": "No flow images could be produced for this run."}]


# ---------------------------------------------------------------------------- compare / study


def _compare_sections(project: Project, run_ids: list[str], baseline: Optional[str]) -> tuple[list[dict], list[dict], list[list[Any]]]:
    cmp = compare_runs(project, run_ids, baseline)
    callouts = [{"level": "", "text": w} for w in cmp["warnings"]]
    header = ["Run", "Convergence", "Cells"] + [_metric_label(m["id"]) for m in cmp["metrics"]]
    rows, drows = [], []
    csv_rows = [["run_id", "label", "baseline", "convergence", "cells"] + [m["id"] for m in cmp["metrics"]] +
                [f"delta_{m['id']}" for m in cmp["metrics"]]]
    for t in cmp["runs"]:
        rows.append([f"{t['label']}{' (baseline)' if t['baseline'] else ''}", CONV_BADGE.get(t["convergence"], ("", t["convergence"] or "—"))[1],
                     _f(t["cells"], 9)] + [_f(t["metrics"][m["id"]]["value"], 5) for m in cmp["metrics"]])
        if not t["baseline"]:
            drows.append([t["label"]] + [_delta(t["metrics"][m["id"]], m["percent_meaningful"]) for m in cmp["metrics"]])
        csv_rows.append([t["run_id"], t["label"], t["baseline"], t["convergence"], t["cells"]] +
                        [t["metrics"][m["id"]]["value"] for m in cmp["metrics"]] + [t["metrics"][m["id"]]["delta"] for m in cmp["metrics"]])
    blocks: list[dict] = [{"kind": "table", "header": header, "rows": rows, "num_cols": list(range(2, len(header)))}]
    if drows:
        blocks.append({"kind": "table", "title": "Differences to the baseline", "header": ["Run"] + [_metric_label(m["id"]) for m in cmp["metrics"]],
                       "rows": drows, "num_cols": list(range(1, len(cmp["metrics"]) + 1)),
                       "caption": "Δ = run − baseline. Percentages are shown only where meaningful (not for quantities that change sign). "
                                  "1 drag count = 0.001 Cd."})
    coeffs = [m["id"] for m in cmp["metrics"] if m["id"] in ("Cd", "Cl", "Cl_front", "Cl_rear")]
    blocks.append({"kind": "svg", "svg": svg.bar_chart(coeffs, {t["label"]: [t["metrics"][k]["value"] for k in coeffs] for t in cmp["runs"]},
                                                       "Coefficients", "coefficient")})
    if not cmp["field_difference_available"] and cmp.get("field_difference_note"):
        blocks.append({"kind": "text", "text": cmp["field_difference_note"]})
    return [{"title": "Comparison", "blocks": blocks}], callouts, csv_rows


def _delta(v: dict, pct_ok: bool) -> str:
    d = v.get("delta")
    if d is None:
        return "—"
    s = f"{d:+.4g}"
    if pct_ok and v.get("percent") is not None:
        s += f" ({v['percent']:+.1f}%)"
    return s


def _study_sections(project: Project, sid: str) -> tuple[list[dict], list[dict], list[list[Any]], str]:
    from .studies import detail, export_csv

    st = detail(project, sid)
    defn = st["definition"]
    designs = st["designs"]
    callouts: list[dict] = []
    counts = (st.get("summary") or {}).get("counts") or {}
    if st["status"] not in ("completed",):
        callouts.append({"level": "", "text": f"The {st['kind']} is {st['status']}; the tables show the designs evaluated so far."})
    n_fail = sum(1 for d in designs if d["status"] == "failed")
    if n_fail:
        callouts.append({"level": "", "text": f"{n_fail} design(s) failed; they are listed with their reasons and excluded from rankings."})
    unconv = sum(1 for d in designs if d["status"] == "completed" and not (d.get("convergence") or {}).get("all_converged", False))
    if unconv:
        callouts.append({"level": "", "text": f"{unconv} completed design(s) did not meet the convergence criteria; their values are uncertain."})
    var_ids = [v["id"] for v in defn["variables"]]
    metrics = [o["metric"] for o in defn.get("objectives", [])] + [c["metric"] for c in defn.get("constraints", [])] if st["kind"] == "optimisation" \
        else list(defn.get("metrics") or ["Cd", "Cl"])
    metrics = list(dict.fromkeys(metrics))
    header = ["#", "Origin", "Status"] + var_ids + [_metric_label(m) for m in metrics] + (["Feasible"] if st["kind"] == "optimisation" else []) + ["Note"]
    rows = []
    for d in designs:
        agg = (d.get("metrics") or {}).get("aggregate") or {}
        note = (d.get("error") or {}).get("message", "") if d["status"] == "failed" else \
            ("not converged" if d["status"] == "completed" and not (d.get("convergence") or {}).get("all_converged", False) else "")
        rows.append([d["idx"], d["origin"], d["status"]] + [_f(d["values"].get(v), 5) for v in var_ids] +
                    [_f(agg.get(m, {}).get("mean"), 5) for m in metrics] +
                    ([("yes" if d.get("feasible") else "no") if d["status"] == "completed" else "—"] if st["kind"] == "optimisation" else []) + [note])
    kv = [("Kind", st["kind"]), ("Status", st["status"]), ("Designs", ", ".join(f"{k}: {v}" for k, v in counts.items()) or str(len(designs))),
          ("Variables", "; ".join(f"{v['id']} → {v['target']} [{v.get('lower')}, {v.get('upper')}]" if v.get("kind") != "discrete"
                                  else f"{v['id']} → {v['target']} {v.get('values')}" for v in defn["variables"]))]
    if st["kind"] == "optimisation":
        kv += [("Algorithm", defn.get("algorithm")), ("Budget", defn.get("budget")),
               ("Objectives", "; ".join(f"{o['sense']} {o['metric']} ({o.get('aggregate', 'single')})" for o in defn["objectives"])),
               ("Constraints", "; ".join(f"{c['metric']} {c['op']} {c['value']} ({c.get('bound', 'absolute')})" for c in defn.get("constraints", [])) or "none")]
        if defn.get("conditions"):
            kv.append(("Conditions", "; ".join(json.dumps(c) for c in defn["conditions"])))
    blocks: list[dict] = [{"kind": "kv", "rows": kv}]
    summ = st.get("summary") or {}
    if summ.get("best_design_id"):
        b = next((d for d in designs if d["id"] == summ["best_design_id"]), None)
        if b:
            blocks.append({"kind": "callout", "level": "info", "text": f"Best feasible design: #{b['idx']} with "
                           + ", ".join(f"{k} = {_f(v, 5)}" for k, v in b["values"].items()) + f" → {defn['objectives'][0]['metric']} = {_f(summ.get('best_value'), 5)}"})
    hist = st.get("history") or []
    if hist:
        xs = [h["idx"] for h in hist]
        blocks.append({"kind": "svg", "svg": svg.line_chart({"evaluated": (xs, [h["value"] for h in hist]), "best feasible": (xs, [h["best"] for h in hist])},
                                                            "Optimisation history", "design #", defn["objectives"][0]["metric"],
                                                            markers={"evaluated"})})
    if st["kind"] == "optimisation" and len(defn.get("objectives", [])) == 2:
        o1, o2 = defn["objectives"][0]["metric"], defn["objectives"][1]["metric"]
        pts = [((d.get("objectives") or [{}, {}])[0].get("value"), (d.get("objectives") or [{}, {}])[1].get("value"), d["id"]) for d in designs
               if d["status"] == "completed"]
        blocks.append({"kind": "svg", "svg": svg.scatter_chart(pts, set(summ.get("pareto_design_ids") or []), "Objective space (red: Pareto front)",
                                                               o1, o2, connect_highlight=True)})
    if st["kind"] == "study" and len(var_ids) == 1 and metrics:
        v = var_ids[0]
        ok = [d for d in designs if d["status"] == "completed"]
        try:
            series = {m: ([float(d["values"][v]) for d in ok], [((d.get("metrics") or {}).get("aggregate") or {}).get(m, {}).get("mean") for d in ok])
                      for m in metrics[:4]}
            series = {m: tuple(zip(*sorted(zip(*xy)))) if xy[0] else xy for m, xy in series.items()}
            blocks.append({"kind": "svg", "svg": svg.line_chart(series, f"Response to {v}", v, "value")})
        except (TypeError, ValueError):
            pass
    g = summ.get("gci")
    if g:
        grow = [[m, r["status"], _f(r.get("order"), 3), _f(r.get("extrapolated"), 5),
                 "—" if r.get("gci_fine") is None else f"{r['gci_fine'] * 100:.2f} %", _f(r.get("asymptotic_ratio"), 3), r.get("note", "")]
                for m, r in g["results"].items()]
        blocks.append({"kind": "table", "title": "Mesh sensitivity — grid convergence index (Celik et al. 2008)",
                       "header": ["Quantity", "Convergence", "Observed order", "Extrapolated", "GCI (fine)", "Asymptotic ratio", "Note"],
                       "rows": grow, "num_cols": [2, 3, 4, 5],
                       "caption": "Three finest meshes; GCI with safety factor 1.25. An asymptotic ratio near 1 indicates the asymptotic range."})
    blocks.append({"kind": "table", "title": "Designs", "header": header, "rows": rows, "num_cols": [0] + list(range(3, 3 + len(var_ids) + len(metrics)))})
    csv_text = export_csv(project, sid)
    return [{"title": "Design exploration: " + (st.get("name") or sid), "blocks": blocks}], callouts, [], csv_text


# ---------------------------------------------------------------------------- entry points


def create_report(project: Project, ctx, spec: dict[str, Any], viz=None) -> dict[str, Any]:
    kind = spec.get("kind")
    rid = short_id("report")
    out_dir = project.root / "reports" / rid
    out_dir.mkdir(parents=True, exist_ok=True)
    callouts: list[dict] = []
    sections: list[dict] = []
    csv_rows: list[list[Any]] = []
    csv_text: Optional[str] = None
    title = spec.get("title")
    subtitle = ""
    try:
        if kind == "run":
            if not spec.get("run_ids"):
                raise AeroBenchError.make("report.no_run", ErrorCategory.CONFIGURATION, "Choose a run for the report.")
            r = run_record(project, spec["run_ids"][0], include_snapshot=True)
            sections, callouts = _run_sections(project, r, viz, bool(spec.get("images", True)))
            title = title or f"Aerodynamic run report — {r.get('label')}"
            subtitle = f"Run {r['id']}"
            m = (r.get("metrics") or {}).get("metrics") or {}
            csv_rows = [["metric", "label", "units", "value", "sign_convention"]] + [
                [k, METRIC_INFO.get(k, {}).get("label", k), METRIC_INFO.get(k, {}).get("units", ""), v, METRIC_INFO.get(k, {}).get("sign", "")]
                for k, v in m.items()]
        elif kind == "compare":
            ids = spec.get("run_ids") or []
            if len(ids) < 2:
                raise AeroBenchError.make("report.compare_runs", ErrorCategory.CONFIGURATION, "Choose at least two runs to compare.")
            sections, callouts, csv_rows = _compare_sections(project, ids, spec.get("baseline"))
            title = title or "Run comparison"
            subtitle = f"{len(ids)} runs, baseline {spec.get('baseline') or ids[0]}"
            if spec.get("images", True) and viz is not None:
                base = spec.get("baseline") or ids[0]
                for run_id in ids[:4]:
                    r = run_record(project, run_id)
                    imgs = _preset_images_one(project, run_id, viz, "cp_body", f"{r.get('label')}: Cp body map")
                    if imgs:
                        sections[0]["blocks"].append(imgs)
                _ = base
        elif kind == "study":
            if not spec.get("study_id"):
                raise AeroBenchError.make("report.no_study", ErrorCategory.CONFIGURATION, "Choose a study for the report.")
            sections, callouts, csv_rows, csv_text = _study_sections(project, spec["study_id"])
            title = title or "Design exploration report"
            subtitle = f"Study {spec['study_id']}"
        else:
            raise AeroBenchError.make("report.kind", ErrorCategory.CONFIGURATION, f"Unknown report kind '{kind}'.")
        allowed = spec.get("sections")
        if allowed:
            sections = [s for s in sections if s["title"] in allowed or s["title"].split(":")[0] in allowed]
        html = _env.get_template("report.html.j2").render(
            title=title, subtitle=subtitle, project={"name": project.config.name}, generated_at=utc_now_iso(), app_name=APP_DISPLAY_NAME,
            app_version=APP_VERSION, callouts=callouts, sections=sections, notes=spec.get("notes") or "")
        atomic_write_text(out_dir / "report.html", html)
        if csv_text is None:
            buf = io.StringIO()
            csv.writer(buf).writerows(csv_rows)
            csv_text = buf.getvalue()
        atomic_write_text(out_dir / "data.csv", csv_text)
        atomic_write_text(out_dir / "spec.json", json.dumps(spec, indent=2, default=str))
        status, message = "completed", None
    except AeroBenchError:
        raise
    except Exception as exc:  # noqa: BLE001 - record the failure, keep the raw reason
        status, message = "failed", f"{type(exc).__name__}: {exc}"
        atomic_write_text(out_dir / "error.txt", message)
    project.db.insert("reports", {"id": rid, "created_at": utc_now_iso(), "title": title, "kind": kind, "spec_json": spec,
                                  "path": str(out_dir.relative_to(project.root)), "status": status, "message": message})
    if status != "completed":
        raise AeroBenchError.make("report.failed", ErrorCategory.INTERNAL, "The report could not be generated.", details=message)
    return {"id": rid, "title": title, "status": status, "files": ["report.html", "data.csv"], "path": str(out_dir)}


def _preset_images_one(project: Project, run_id: str, viz, preset: str, caption: str) -> Optional[dict]:
    try:
        if not viz.available().get("available"):
            return None
        p = viz.preset_screenshot(project, run_id, preset, size=(1200, 700))
        if p and Path(p).exists():
            return {"kind": "images", "pics": [{"src": "data:image/png;base64," + base64.b64encode(Path(p).read_bytes()).decode(), "caption": caption}]}
    except Exception:  # noqa: BLE001
        return None
    return None


def list_reports(project: Project) -> list[dict[str, Any]]:
    return [dec(r) for r in project.db.all("SELECT * FROM reports ORDER BY created_at DESC")]


def report_file(project: Project, rid: str, name: str) -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise AeroBenchError.make("report.file", ErrorCategory.PROJECT, "Invalid file name.")
    r = project.db.one("SELECT path FROM reports WHERE id = ?", (rid,))
    if r is None:
        raise AeroBenchError.make("report.unknown", ErrorCategory.PROJECT, f"Unknown report {rid}.")
    p = project.root / r["path"] / name
    if not p.exists():
        raise AeroBenchError.make("report.file_missing", ErrorCategory.PROJECT, f"{name} does not exist for report {rid}.")
    return p
