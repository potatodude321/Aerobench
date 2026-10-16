"""Shape zones: regions of the vehicle an optimisation or study may reshape.

A zone is a box in the vehicle's CFD frame (metres, nose towards -x, +z up, unposed) with one
or more moves (raise/lower, fore/aft, wider/narrower). Each move becomes one geometry
parameter per section (``param.<zone>_<dir>[k]``, values in mm) whose deformation fades
smoothly to zero inside the box faces: the rest of the vehicle is never morphed.

Also: the morph preview used by the zone editor, the parameter editor and optimisation
results (base surface + per-vertex displacement, coloured by the movement normal to the
surface so growth and shrinkage read at a glance), and the freedom map showing where the
zones allow change.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.exploration import GeometryParameter, ShapeZone, ZoneDef
from ..domain.geometry import ComponentRole
from ..domain.project import ProjectConfig
from ..geometry.deform import affected_component_ids, apply_parameters, check_deformation, component_vertex_masks, parameter_values_complete, zone_window
from ..geometry.pose import component_surface, ground_z, posed_components
from ..geometry.trisurface import TriSurface
from ..project.db import dec
from ..project.store import Project

DIR_WORDS = {"z": "up / down", "x": "fore / aft", "width": "wider / narrower"}
SECTION_WORDS = {2: ["front", "rear"], 3: ["front", "middle", "rear"]}


def _err(code: str, msg: str, action: str = "") -> AeroBenchError:
    return AeroBenchError.make(code, ErrorCategory.CONFIGURATION, msg, action=action)


# ---------------------------------------------------------------------------- extents & presets


def body_extent(project: Project, cfg: Optional[ProjectConfig] = None) -> dict[str, Any]:
    """Bounding box of the non-wheel parts in the unposed CFD frame (the frame zones use)."""
    cfg = cfg or project.config
    if not cfg.geometry.revision_id:
        raise _err("zones.no_geometry", "Import the vehicle first.")
    cfd = project.cfd_surface(cfg=cfg)
    masks = component_vertex_masks(cfd, cfg.geometry.components)
    body = np.zeros(cfd.n_points, dtype=bool)
    for c in cfg.geometry.components:
        if c.role not in (ComponentRole.WHEEL, ComponentRole.STILT):
            body |= masks[c.id]
    pts = cfd.points[body] if body.any() else cfd.points
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    yc = cfg.vehicle.centreline_y
    half_width = float(max(np.abs(pts[:, 1] - yc).max(), 1e-3))
    return {"lo": lo, "hi": hi, "half_width": half_width, "centreline_y": yc, "surface": cfd}


def _element_presets(e: dict[str, Any]) -> list[dict[str, Any]]:
    """Typical zones of a wing or flap (chord along x, span along y): boxes from its extent.

    The boxes reach well past the element above, below, at its ends and beyond its leading and
    trailing edges (farther than the fade-out), so upper and lower surfaces move together and
    the edges move fully; the change fades out only towards the inside of the chord."""
    lo, hi, yc, hw = e["lo"], e["hi"], e["centreline_y"], e["half_width"]
    C, H = float(hi[0] - lo[0]), float(hi[2] - lo[2])
    blend = round(0.15 * C, 3)
    m = 0.05 * max(C, H) + blend
    out = m / C  # beyond an edge, in chords
    ylo, yhi = yc - hw - m, yc + hw + m
    mm = lambda f: float(max(2, round(f * C * 1000)))  # noqa: E731 - a share of the chord, in whole mm

    def box(x0: float, x1: float, y0: float = ylo, y1: float = yhi) -> dict:
        return {"box_min": [round(lo[0] + x0 * C, 4), round(y0, 4), round(lo[2] - m, 4)],
                "box_max": [round(lo[0] + x1 * C, 4), round(y1, 4), round(hi[2] + m, 4)]}

    tip = 0.25 * hw
    return [
        {"preset": "leading_edge", "name": "Leading edge", "description": "Front of the element: droop or raise the nose (camber at the front).",
         **box(-out, 0.35), "blend": blend, "moves": [{"direction": "z", "max_mm": mm(0.02), "segments": 1}]},
        {"preset": "trailing_edge", "name": "Trailing edge", "description": "Rear of the element: flap-like camber, Gurney-like lip height.",
         **box(0.6, 1 + out), "blend": blend, "moves": [{"direction": "z", "max_mm": mm(0.03), "segments": 1}]},
        {"preset": "camber", "name": "Mid-chord camber", "description": "Middle of the chord: more or less curvature.",
         **box(0.2, 0.8), "blend": blend, "moves": [{"direction": "z", "max_mm": mm(0.015), "segments": 1}]},
        {"preset": "chord", "name": "Chord length", "description": "Trailing edge moved fore or aft: a longer or shorter element.",
         **box(0.7, 1 + out), "blend": blend, "moves": [{"direction": "x", "max_mm": mm(0.04), "segments": 1}]},
        {"preset": "tips", "name": "Tips (twist)", "description": "Outer ends of the span, both sides: wash-in or wash-out.",
         **box(-out, 1 + out, yc + hw - tip, yhi), "blend": round(0.5 * tip, 3), "moves": [{"direction": "z", "max_mm": mm(0.03), "segments": 2}]},
        {"preset": "custom", "name": "Custom zone", "description": "A box you place yourself.",
         **box(0.3, 0.7), "blend": blend, "moves": [{"direction": "z", "max_mm": mm(0.015), "segments": 1}]},
    ]


def presets(project: Project) -> list[dict[str, Any]]:
    """Typical zones placed on this vehicle or element (boxes from its extent, unposed frame)."""
    e = body_extent(project)
    if project.config.application == "aero_element":
        return _element_presets(e)
    lo, hi, yc, hw = e["lo"], e["hi"], e["centreline_y"], e["half_width"]
    L, H = float(hi[0] - lo[0]), float(hi[2] - lo[2])
    m = 0.05 * max(L, H)  # margin beyond the surface so the box faces are clear of it
    ylo, yhi = yc - hw - m, yc + hw + m

    def box(x0: float, x1: float, z0: float, z1: float, y0: float = ylo, y1: float = yhi) -> dict:
        return {"box_min": [round(lo[0] + x0 * L, 4), round(y0, 4), round(lo[2] + z0 * H, 4)],
                "box_max": [round(lo[0] + x1 * L, 4), round(y1, 4), round(lo[2] + z1 * H, 4)]}

    blend = round(0.04 * L, 3)
    return [
        {"preset": "front_splitter", "name": "Front splitter", "description": "Lower front edge: lip height and length.",
         **box(-0.04, 0.14, -0.06, 0.30), "blend": blend, "moves": [{"direction": "z", "max_mm": 25, "segments": 1}, {"direction": "x", "max_mm": 30, "segments": 1}]},
        {"preset": "bonnet", "name": "Bonnet and windscreen", "description": "Upper front: bonnet line and screen rake.",
         **box(0.10, 0.45, 0.45, 1.06), "blend": blend, "moves": [{"direction": "z", "max_mm": 30, "segments": 2}]},
        {"preset": "roof_tail", "name": "Roof trailing edge", "description": "Roof drop towards the rear: wake size and rear lift.",
         **box(0.50, 0.90, 0.70, 1.06), "blend": blend, "moves": [{"direction": "z", "max_mm": 40, "segments": 2}]},
        {"preset": "rear_deck", "name": "Rear deck / spoiler lip", "description": "Boot lid and spoiler lip height.",
         **box(0.78, 1.04, 0.45, 0.85), "blend": blend, "moves": [{"direction": "z", "max_mm": 30, "segments": 2}, {"direction": "x", "max_mm": 30, "segments": 1}]},
        {"preset": "diffuser", "name": "Rear diffuser", "description": "Underbody exit: diffuser kick-up angle.",
         **box(0.72, 1.04, -0.06, 0.32), "blend": blend, "moves": [{"direction": "z", "max_mm": 30, "segments": 2}]},
        {"preset": "boat_tail", "name": "Rear taper (boat-tailing)", "description": "Narrowing of the rear corners: base pressure and drag.",
         **box(0.72, 1.04, -0.06, 1.06), "blend": blend, "moves": [{"direction": "width", "max_mm": 40, "segments": 2}]},
        {"preset": "side_skirts", "name": "Side skirts", "description": "Sill height and width between the wheels.",
         **box(0.25, 0.75, -0.06, 0.28), "blend": blend, "moves": [{"direction": "z", "max_mm": 25, "segments": 1}, {"direction": "width", "max_mm": 20, "segments": 1}]},
        {"preset": "custom", "name": "Custom zone", "description": "A box you place yourself.",
         **box(0.40, 0.60, -0.06, 1.06), "blend": blend, "moves": [{"direction": "z", "max_mm": 30, "segments": 1}]},
    ]


# ---------------------------------------------------------------------------- zone -> parameters


def zone_parameter_ids(z: ShapeZone) -> list[str]:
    out = []
    for mv in z.moves:
        base = f"{z.id}_{mv.direction}"
        out += [base] if mv.segments == 1 else [f"{base}{k + 1}" for k in range(mv.segments)]
    return out


def zone_parameters(z: ShapeZone, half_width: float) -> list[GeometryParameter]:
    if not z.enabled:
        return []
    out = []
    for mv in z.moves:
        for k in range(mv.segments):
            pid = f"{z.id}_{mv.direction}" + ("" if mv.segments == 1 else str(k + 1))
            sec = "" if mv.segments == 1 else " (" + (SECTION_WORDS.get(mv.segments, [f"section {i + 1}" for i in range(mv.segments)])[k]) + ")"
            out.append(GeometryParameter(
                id=pid, name=f"{z.name}: {DIR_WORDS[mv.direction]}{sec}", units="mm", baseline=0.0,
                lower=-mv.max_mm, upper=mv.max_mm, step=None, components=list(z.components), frozen_components=[],
                deformation=ZoneDef(zone=z.id, box_min=z.box_min, box_max=z.box_max, blend=z.blend, direction=mv.direction,
                                    segments=mv.segments, segment=k, half_width=half_width),
                # one section moves at most its own value; the margin absorbs float rounding
                max_displacement=mv.max_mm / 1000.0 * 1.001, symmetric_about_centreline=z.symmetric,
            ))
    return out


def _symmetric_box(z: ShapeZone, yc: float) -> ShapeZone:
    a = max(abs(z.box_min[1] - yc), abs(z.box_max[1] - yc))
    return z.model_copy(update={"box_min": (z.box_min[0], yc - a, z.box_min[2]), "box_max": (z.box_max[0], yc + a, z.box_max[2])})


def set_zones(project: Project, zones: list[dict[str, Any]]) -> dict[str, Any]:
    """Store the zones and regenerate their parameters (hand-made parameters are kept)."""
    parsed = [ShapeZone.model_validate(z) for z in zones]
    ids = [z.id for z in parsed]
    if len(ids) != len(set(ids)):
        raise _err("zones.duplicate", "Each zone needs its own id.")
    cfg = project.config
    comp_ids = {c.id for c in cfg.geometry.components}
    for z in parsed:
        bad = [c for c in z.components if c not in comp_ids]
        if bad:
            raise _err("zones.component", f"Zone '{z.name}' refers to unknown part(s): {', '.join(bad)}.")
        wheels = [c for c in z.components if cfg.geometry.component(c).role == ComponentRole.WHEEL]
        if wheels:
            raise _err("zones.wheel", f"Zone '{z.name}' includes a wheel; wheels can only be moved rigidly.",
                       "Leave wheels out of the zone's parts (they are excluded by default).")
    e = body_extent(project, cfg) if parsed else None
    if e is not None:
        parsed = [_symmetric_box(z, e["centreline_y"]) if z.symmetric else z for z in parsed]
    manual = [p for p in cfg.parameters if p.deformation.kind != "zone"]
    generated = [p for z in parsed for p in zone_parameters(z, e["half_width"] if e else 1.0)]
    clash = {p.id for p in manual} & {p.id for p in generated}
    if clash:
        raise _err("zones.id_clash", f"Zone parameter id(s) {', '.join(sorted(clash))} are already used by other geometry parameters.",
                   "Rename the zone.")

    def mutate(c: ProjectConfig) -> None:
        c.zones = parsed
        c.parameters = manual + generated
        keep = {p.id for p in c.parameters}
        c.parameter_values = {k: v for k, v in c.parameter_values.items() if k in keep}

    project.update(mutate)
    return {"zones": [z.model_dump(mode="json") for z in parsed], "parameters": [p.model_dump(mode="json") for p in generated]}


def parameters_with_zones(project: Project, zones: list[dict[str, Any]]) -> list[GeometryParameter]:
    """The project's hand-made parameters plus those of (unsaved) ``zones``."""
    cfg = project.config
    parsed = [ShapeZone.model_validate(z) for z in zones]
    e = body_extent(project, cfg) if parsed else None
    if e is not None:
        parsed = [_symmetric_box(z, e["centreline_y"]) if z.symmetric else z for z in parsed]
    return [p for p in cfg.parameters if p.deformation.kind != "zone"] + [p for z in parsed for p in zone_parameters(z, e["half_width"] if e else 1.0)]


# ---------------------------------------------------------------------------- previews


def _display_parts(surface: TriSurface, cfg: ProjectConfig, posed: bool, vehicle=None) -> tuple[dict[str, TriSurface], Optional[float]]:
    """The parts as the viewport shows them: posed as in the tunnel, or in the frame shape zones
    and parameters are defined in (the part as imported; an element before its angle and height)."""
    vehicle = vehicle or cfg.vehicle
    if posed:
        parts, z0, _ = posed_components(surface, cfg.geometry.components, vehicle, cfg.tunnel.contact_patch_fraction)
        return parts, z0
    parts = {c.id: component_surface(surface, c) for c in cfg.geometry.components}
    if cfg.application == "aero_element":
        return parts, None  # the ground is only defined once the element is placed
    z0, _ = ground_z(vehicle, surface, cfg.tunnel.contact_patch_fraction)
    return parts, z0


def _vertex_normals(s: TriSurface) -> np.ndarray:
    """Area-weighted vertex normals, oriented outwards for closed parts (by signed volume)."""
    tri = s.points[s.faces]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    vn = np.zeros_like(s.points)
    for k in range(3):
        np.add.at(vn, s.faces[:, k], fn)
    vol = float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum()) / 6.0
    if vol < 0:
        vn = -vn
    n = np.linalg.norm(vn, axis=1)
    return vn / np.where(n > 0, n, 1.0)[:, None]


def _encode(parts_base: dict[str, TriSurface], scalars: dict[str, np.ndarray], vectors: Optional[dict[str, np.ndarray]],
            meta: dict[str, Any], max_faces: int) -> tuple[dict, bytes]:
    from ..geometry.repair import simplify_vertex_clustering
    from ..viz.payload import encode_mesh

    surfs, sc, vec = [], [], []
    for cid, s in parts_base.items():
        if s.n_faces == 0:
            continue
        surfs.append(TriSurface(s.points, s.faces, np.zeros(s.n_faces, np.int32), [cid]))
        sc.append(scalars[cid])
        vec.append(vectors[cid] if vectors is not None else np.zeros_like(s.points))
    merged = TriSurface.merge(surfs)
    scal = np.concatenate(sc)
    vecs = np.concatenate(vec)
    if merged.n_faces > max_faces:
        from scipy.spatial import cKDTree

        lo, hi = merged.bbox()
        cell = float(np.linalg.norm(hi - lo)) / math.sqrt(max_faces) * 0.9
        small, _ = simplify_vertex_clustering(merged, cell)
        _, j = cKDTree(merged.points).query(small.points)
        merged, scal, vecs = small, scal[j], vecs[j]
    return encode_mesh(merged.points.astype(np.float32), merged.faces.astype(np.uint32), scalars=scal.astype(np.float32),
                       vectors=vecs.astype(np.float32) if vectors is not None else None,
                       part_ids=merged.regions.astype(np.uint16), part_names=merged.region_names, meta=meta)


def morph_preview(project: Project, cfg: ProjectConfig, values: dict[str, float], defs: Optional[list[GeometryParameter]] = None,
                  check: bool = True, max_faces: int = 400_000, posed: bool = True) -> tuple[dict, bytes]:
    """The posed baseline surface with each vertex's displacement for ``values``.

    positions: baseline; vectors: displacement (m) — the deformed shape is positions +
    vectors, and positions + t·vectors blends between them; scalars: movement normal to the
    surface in mm (+ outwards, − inwards). The header carries the validity check and
    statistics (largest movement, share of the surface area that moves)."""
    defs = cfg.parameters if defs is None else defs
    full = parameter_values_complete(defs, values)
    cfd = project.cfd_surface(cfg=cfg)
    res = apply_parameters(cfd, cfg.geometry.components, cfg.vehicle.wheels, defs, full, cfg.vehicle.centreline_y)
    if check:
        chk = check_deformation(cfd, res, defs, check_intersections=True, components=cfg.geometry.components).as_dict()
    else:
        chk = {"valid": None, "errors": [], "warnings": [], "stats": {}}
    moved = cfg.vehicle.model_copy(update={"wheels": res.wheels})
    base, _ = _display_parts(cfd, cfg, posed)
    dfm, z0 = _display_parts(res.surface, cfg, posed, vehicle=moved)
    scal, vecs = {}, {}
    area_all = area_moved = 0.0
    for cid, b in base.items():
        d = dfm[cid].points - b.points
        vecs[cid] = d
        scal[cid] = (d * _vertex_normals(b)).sum(axis=1) * 1000.0
        if b.n_faces:
            fa = 0.5 * np.linalg.norm(np.cross(b.points[b.faces[:, 1]] - b.points[b.faces[:, 0]], b.points[b.faces[:, 2]] - b.points[b.faces[:, 0]]), axis=1)
            mv = (np.linalg.norm(d, axis=1)[b.faces] > 1e-4).any(axis=1)  # > 0.1 mm
            area_all += float(fa.sum())
            area_moved += float(fa[mv].sum())
    allv = np.concatenate(list(scal.values())) if scal else np.zeros(1)
    mags = np.concatenate([np.linalg.norm(v, axis=1) for v in vecs.values()]) if vecs else np.zeros(1)
    requested = {k: v for k, v in full.items() if any(d.id == k and abs(v - d.baseline) > 1e-12 for d in defs)}
    notes = []
    if requested and mags.max() < 1e-4:
        notes.append("Nothing moves: no part of the surface lies inside the zones (or boxes) of the variables you changed. "
                     "Check where their boxes are against the part.")
    elif mags.max() >= 1e-4 and max(float(allv.max()), float(-allv.min())) < 0.2 * float(mags.max() * 1000.0):
        notes.append("The surface moves mostly along itself (for example a fore/aft change of a flat roof), so the shape hardly changes.")
    meta = {"ground_z": z0, "scalar_name": "Surface movement (+ outwards)", "scalar_units": "mm", "check": chk, "posed": posed,
            "stats": {"max_movement_mm": float(mags.max() * 1000.0), "max_outward_mm": float(allv.max()), "max_inward_mm": float(-allv.min()),
                      "moved_area_pct": 100.0 * area_moved / area_all if area_all else 0.0},
            "notes": notes, "values": full}
    return _encode(base, scal, vecs, meta, max_faces)


def freedom_preview(project: Project, zones: Optional[list[dict[str, Any]]] = None, max_faces: int = 400_000,
                    posed: bool = False) -> tuple[dict, bytes]:
    """Where the zones allow change: per vertex, the strongest zone weight (0–100 %), and how
    much of the surface each zone covers (a box that misses the part covers nothing)."""
    cfg = project.config
    zs = [ShapeZone.model_validate(z) for z in zones] if zones is not None else cfg.zones
    cfd = project.cfd_surface(cfg=cfg)
    masks = component_vertex_masks(cfd, cfg.geometry.components)
    w = np.zeros(cfd.n_points)
    tri = cfd.points[cfd.faces]
    fa = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    total = float(fa.sum()) or 1.0
    coverage = []
    for z in zs:
        if not z.enabled:
            continue
        aff = affected_component_ids(GeometryParameter(id="z", name="z", lower=-1, upper=1, components=list(z.components),
                                                       deformation=ZoneDef(zone=z.id, box_min=z.box_min, box_max=z.box_max)), cfg.geometry.components)
        sel = np.zeros(cfd.n_points, dtype=bool)
        for c in aff:
            if c in masks:
                sel |= masks[c]
        keep = np.zeros(cfd.n_points, dtype=bool)
        for cid, m in masks.items():
            if cid not in aff:
                keep |= m
        sel &= ~keep  # vertices shared with untouched parts are held (as in the deformation)
        zw = np.zeros(cfd.n_points)
        if sel.any():
            zw[sel] = zone_window(cfd.points[sel], np.asarray(z.box_min), np.asarray(z.box_max), z.blend)
            w = np.maximum(w, zw)
        covered = float(fa[(zw[cfd.faces] > 1e-6).any(axis=1)].sum())
        coverage.append({"id": z.id, "name": z.name, "area_pct": 100.0 * covered / total})
    field = TriSurface(np.repeat(w[:, None], 3, axis=1), cfd.faces, cfd.regions, cfd.region_names)
    base, z0 = _display_parts(cfd, cfg, posed)
    flat = cfg.vehicle.model_copy(update={"rake_deg": 0.0, "ride_height_offset": 0.0})
    wparts, _ = _display_parts(field, cfg, posed, vehicle=flat)
    scal = {cid: wparts[cid].points[:, 0] * 100.0 for cid in base}
    return _encode(base, scal, None, {"ground_z": z0, "scalar_name": "Shape freedom", "scalar_units": "%", "coverage": coverage,
                                      "posed": posed}, max_faces)


def design_morph(project: Project, study_id: str, design_id: str) -> tuple[dict, bytes]:
    """Morph preview of an evaluated study/optimisation design against its study's baseline."""
    s = dec(project.db.one("SELECT * FROM studies WHERE id = ?", (study_id,)))
    d = dec(project.db.one("SELECT * FROM designs WHERE id = ? AND study_id = ?", (design_id, study_id)))
    if s is None or d is None:
        raise _err("studies.unknown_design", "Unknown study or design.")
    base_cfg = ProjectConfig.model_validate(s["base_config"]) if s.get("base_config") else project.config
    variables = (s.get("definition") or {}).get("variables", [])
    values = d.get("values") or {}
    pv = {v["target"][6:]: float(values[v["id"]]) for v in variables
          if str(v.get("target", "")).startswith("param.") and v["id"] in values and values[v["id"]] is not None}
    if not pv:
        raise _err("studies.no_shape", "This design does not change the shape.", "Shape changes come from geometry parameters or shape zones.")
    return morph_preview(project, base_cfg, pv, check=False)
