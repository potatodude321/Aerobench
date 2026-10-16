"""Geometry workflow: import, units/orientation, repair, components, derived values."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.geometry import AxisDir, ComponentRole, GeometryComponent, GeometrySummary
from ..domain.project import ProjectConfig
from ..domain.setup import AreaSource, MomentCentreSource
from ..domain.vehicle import VehicleCategory
from ..geometry import repair as repair_mod
from ..geometry.components import identify_components
from ..geometry.frontal_area import projected_area
from ..geometry.inspect import UNIT_CANDIDATES, inspect_surface, with_units
from ..geometry.io import read_surface
from ..geometry.pose import place_in_cfd_frame, posed_components
from ..geometry.trisurface import TriSurface
from ..project.store import Project


def import_geometry(
    project: Project,
    path: Path,
    units: str = "auto",
    forward_axis: Optional[str] = None,
    up_axis: Optional[str] = None,
    identify: bool = True,
    label: Optional[str] = None,
    category: Optional[str] = None,
) -> dict[str, Any]:
    path = Path(path)
    raw = read_surface(path)
    report = inspect_surface(raw, units_assumed="m")
    chosen = units
    auto_applied = False
    if units == "auto":
        L = max(report.extents)
        best = report.unit_suggestions[0]
        if 0.2 <= L <= 25.0:
            chosen = "m"
        else:
            chosen = best.unit
            auto_applied = True
    if chosen not in UNIT_CANDIDATES:
        raise AeroBenchError.make("geometry.units_unknown", ErrorCategory.GEOMETRY, f"Unknown unit '{chosen}'.")
    report = with_units(report, chosen)
    rid = project.add_geometry_revision(
        raw, kind="baseline", source_filename=path.name, source_bytes=path.read_bytes(), report=report.as_dict(),
        label=label or path.name, units=chosen,
    )

    def mutate(cfg: ProjectConfig) -> None:
        cfg.geometry.revision_id = rid
        cfg.geometry.transform = cfg.geometry.transform.model_copy(update={
            "unit_scale": UNIT_CANDIDATES[chosen],
            "forward_axis": AxisDir(forward_axis) if forward_axis else cfg.geometry.transform.forward_axis,
            "up_axis": AxisDir(up_axis) if up_axis else cfg.geometry.transform.up_axis,
            "auto_place": True,
        })
        if category:
            cfg.vehicle.category = VehicleCategory(category)
        cfg.reference.locked = False

    project.update(mutate)
    ident = identify_and_derive(project, identify=identify)
    return {
        "revision_id": rid,
        "report": report.as_dict(),
        "units": chosen,
        "units_auto_applied": auto_applied,
        "identification": ident,
    }


BODY_ROLES = (ComponentRole.BODY, ComponentRole.WING, ComponentRole.SPOILER, ComponentRole.SPLITTER, ComponentRole.DIFFUSER,
              ComponentRole.FLOOR, ComponentRole.MIRROR, ComponentRole.COOLING_INLET, ComponentRole.COOLING_OUTLET, ComponentRole.OTHER)


def _slug(text: str, taken: set[str]) -> str:
    import re

    base = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_") or "part"
    if not re.match(r"^[A-Za-z_]", base):
        base = "p_" + base
    base = base[:40]
    out, k = base, 2
    while out in taken:
        out = f"{base}_{k}"
        k += 1
    taken.add(out)
    return out


def _closed_and_volume(s: TriSurface) -> tuple[bool, float]:
    from ..geometry.fastops import unique_int_rows

    if not s.n_faces:
        return False, 0.0
    e = np.sort(np.concatenate([s.faces[:, [0, 1]], s.faces[:, [1, 2]], s.faces[:, [2, 0]]]), axis=1)
    _, cnt = unique_int_rows(e, return_counts=True)
    t = s.points[s.faces]
    vol = float(np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum()) / 6.0
    return bool(np.all(cnt == 2)), vol


def add_bodies(project: Project, paths: list[Path], as_: str = "parts", units: str = "same", role: str = "other",
               level: Optional[int] = None, names: Optional[list[str]] = None) -> dict[str, Any]:
    """Add bodies to the imported geometry, read as coming from the same source: the same units,
    axes and placement, so they sit where they were modelled relative to the parts already there.

    ``as_="parts"``: each file (or each named solid in it) becomes a part (a wall with forces),
    with ``role``. ``as_="refinement"``: each file must be a closed volume; the mesh is refined
    inside it to ``level`` (a refinement zone you modelled yourself)."""
    cfg = project.config
    if not cfg.geometry.revision_id:
        raise AeroBenchError.make("geometry.missing", ErrorCategory.GEOMETRY, "Import the main geometry first; added bodies are placed relative to it.")
    if as_ not in ("parts", "refinement"):
        raise AeroBenchError.make("geometry.add_kind", ErrorCategory.GEOMETRY, f"Unknown kind of body '{as_}'.")
    if units != "same" and units not in UNIT_CANDIDATES:
        raise AeroBenchError.make("geometry.units_unknown", ErrorCategory.GEOMETRY, f"Unknown unit '{units}'.")
    tr = cfg.geometry.transform
    # bring the new files' raw coordinates into the raw frame of the geometry already imported
    factor = 1.0 if units == "same" else UNIT_CANDIDATES[units] / tr.unit_scale
    current = project.raw_surface()
    _, placed = place_in_cfd_frame(current, tr)  # the translation the parts have now: kept, so nothing moves
    frozen = placed.model_copy(update={"auto_place": False})
    files = [Path(p) for p in paths]
    if not files:
        raise AeroBenchError.make("geometry.add_none", ErrorCategory.GEOMETRY, "Choose at least one file.")
    raws = []
    for i, f in enumerate(files):
        raw = read_surface(f)
        if raw.n_faces == 0:
            raise AeroBenchError.make("geometry.empty_file", ErrorCategory.GEOMETRY, f"'{f.name}' contains no triangles.")
        raws.append((f, TriSurface(raw.points * factor, raw.faces, raw.regions, list(raw.region_names)),
                     (names[i] if names and i < len(names) and names[i] else f.stem)))
    if as_ == "parts":
        r = ComponentRole(role)
        if r not in BODY_ROLES:
            raise AeroBenchError.make("geometry.add_role", ErrorCategory.GEOMETRY, f"Added parts cannot have the role '{role}'.",
                                      action="Wheels are found from the main geometry; add them there.")
        taken_regions = set(current.region_names)
        taken_ids = {c.id for c in cfg.geometry.components}
        new_surfs, new_comps, labels = [], [], []
        for f, raw, label in raws:
            used = sorted(set(np.unique(raw.regions).tolist()))
            rename = {}
            for k in used:
                rn = raw.region_names[k]
                stem = label if len(used) == 1 else f"{label}_{rn}"
                rename[k] = _slug(stem, taken_regions)
            names_new = list(raw.region_names)
            for k, v in rename.items():
                names_new[k] = v
            new_surfs.append(TriSurface(raw.points, raw.faces, raw.regions, names_new))
            for k, v in rename.items():
                pretty = label if len(used) == 1 else f"{label} {raw.region_names[k]}"
                new_comps.append(GeometryComponent(id=_slug(v, taken_ids), name=pretty, source_regions=[v], role=r))
            labels.append(f.name)
        merged = TriSurface.merge([current, *new_surfs])
        rev = project.geometry_revision(cfg.geometry.revision_id)
        report = inspect_surface(merged, units_assumed=rev.get("units") or "m")
        rid = project.add_geometry_revision(merged, kind="added", parent_id=cfg.geometry.revision_id,
                                            operations=(rev.get("operations") or []) + [{"op": "add_bodies", "files": labels, "units": units}],
                                            report=report.as_dict(), label=f"{rev.get('label')} + {', '.join(labels)}", units=rev.get("units"),
                                            source_filename=rev.get("source_filename"))

        def mutate(c: ProjectConfig) -> None:
            c.geometry.revision_id = rid
            c.geometry.transform = frozen
            c.geometry.components = list(c.geometry.components) + new_comps
            c.reference.locked = False

        project.update(mutate)
        from .element import refresh_element_pose

        refresh_element_pose(project)
        derived = refresh_derived(project)
        return {"kind": "parts", "revision_id": rid, "components": [c.model_dump(mode="json") for c in new_comps], "derived": derived}

    # refinement bodies
    from ..domain.mesh import RefinementRegion, RegionKind, SurfaceShape
    from .mesh import plan_mesh

    if level is None:
        try:
            plan, _, _ = plan_mesh(project)
            near = [r for r in plan.regions if r.kind in ("wake_near", "vehicle_box")]
            level = max((r.level for r in near), default=max((s.min_level for s in plan.surfaces), default=4) - 1)
        except Exception:  # noqa: BLE001 - a default only
            level = 4
    taken = {r.id for r in cfg.mesh.regions}
    regions, out = [], []
    for f, raw, label in raws:
        closed, _ = _closed_and_volume(raw)
        if not closed:
            raise AeroBenchError.make("geometry.refinement_open", ErrorCategory.GEOMETRY,
                                      f"'{f.name}' is not a closed volume, so it has no inside to refine.",
                                      action="Close the surface in your CAD tool (or Geometry, Repair, fill holes) and add it again.")
        # stored in the source frame (like the parts), so later changes of units or axes move it with them
        _, vol = _closed_and_volume(raw)
        faces = raw.faces if vol >= 0 else raw.faces[:, ::-1].copy()  # outward: the mesher needs inside and outside right
        body = TriSurface(raw.points, faces, np.zeros(len(faces), np.int32), ["refinement"])
        sha = project.store_surface(body)
        cfd = frozen.apply(body.points)
        lo, hi = cfd.min(axis=0), cfd.max(axis=0)
        rid = _slug(f"body_{label}", taken)
        reg = RefinementRegion(id=rid, name=label, kind=RegionKind.USER, level=int(level), auto=False,
                               shape=SurfaceShape(blob=sha, source_filename=f.name, bbox_min=tuple(lo.tolist()), bbox_max=tuple(hi.tolist()),
                                                  volume=abs(vol) * tr.unit_scale ** 3, triangles=body.n_faces))
        regions.append(reg)
        out.append(reg.model_dump(mode="json"))

    def mutate_r(c: ProjectConfig) -> None:
        c.mesh.regions = list(c.mesh.regions) + regions

    project.update(mutate_r)
    return {"kind": "refinement", "regions": out}


def identify_and_derive(project: Project, identify: bool = True) -> dict[str, Any]:
    """(Re)identify components/wheels and refresh summary + reference defaults."""
    cfd = project.cfd_surface()
    notes: list[str] = []
    if identify:
        res = identify_components(cfd)
        notes = res.notes

        def mutate(c: ProjectConfig) -> None:
            c.geometry.components = res.components
            c.vehicle.wheels = res.wheels
            if c.application == "aero_element":
                # an element alone has no wheels: round parts found in it are ordinary parts
                c.vehicle.wheels = []
                c.geometry.components = [comp.model_copy(update={"role": ComponentRole.OTHER, "wheel_position": None})
                                         if comp.role == ComponentRole.WHEEL else comp for comp in c.geometry.components]
            elif not res.wheels and any(comp.role == ComponentRole.STILT for comp in res.components):
                c.vehicle.category = VehicleCategory.BLUFF_BODY

        project.update(mutate, snapshot=False)
    from .element import refresh_element_pose

    refresh_element_pose(project)
    derived = refresh_derived(project)
    derived["notes"] = notes
    derived["components"] = [c.model_dump(mode="json") for c in project.config.geometry.components]
    derived["wheels"] = [w.model_dump(mode="json") for w in project.config.vehicle.wheels]
    return derived


def vehicle_extent(project: Project, cfg: Optional[ProjectConfig] = None) -> dict[str, Any]:
    cfg = cfg or project.config
    cfd = project.cfd_surface(cfg=cfg)
    parts, z0, ginfo = posed_components(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
    if not parts:
        lo, hi = cfd.bbox()
        return {"bbox_min": lo.tolist(), "bbox_max": hi.tolist(), "ground_z": 0.0, "ground": {}, "areas": {}, "clearance": None, "surface": cfd}
    merged = TriSurface.merge(list(parts.values()))
    lo, hi = merged.bbox()
    body_parts = [s for cid, s in parts.items() if cfg.geometry.component(cid).role not in (ComponentRole.WHEEL, ComponentRole.STILT)]
    clearance = None
    if body_parts:
        clearance = float(min(s.bbox()[0][2] for s in body_parts) - z0)
    areas = {cid: s.area() for cid, s in parts.items()}
    return {"bbox_min": lo.tolist(), "bbox_max": hi.tolist(), "ground_z": z0, "ground": ginfo, "areas": areas,
            "clearance": clearance, "surface": merged}


def refresh_derived(project: Project, resolution: int = 1000) -> dict[str, Any]:
    cfg = project.config
    ext = vehicle_extent(project)
    merged: TriSurface = ext["surface"]
    lo, hi = np.asarray(ext["bbox_min"]), np.asarray(ext["bbox_max"])
    # frontal area: silhouette above the ground plane, projected along +x
    z0 = ext["ground_z"]
    tri = merged.triangles()
    keep = tri[:, :, 2].max(axis=1) > z0
    pa = projected_area(merged, (1.0, 0.0, 0.0), resolution=resolution, face_mask=keep)
    # clip the part of the silhouette below the ground plane (tyre contact treatment)
    area = pa.area
    if ext["ground"].get("contact_raise"):
        h = pa.pixel_size
        v0 = pa.origin[1]
        rows = np.arange(pa.mask.shape[1]) * h + v0 + 0.5 * h
        area = float(pa.mask[:, rows > z0].sum()) * h * h
    # planform: the outline seen from above (reference area of wings and splitters)
    planform = projected_area(merged, (0.0, 0.0, 1.0), resolution=resolution).area
    wb = cfg.vehicle.wheelbase
    changes: list[str] = []
    if wb is not None and wb <= 0:
        # front wheels behind the rear ones: the wheel positions do not match the vehicle's
        # orientation (edited by hand, or the nose direction changed); never use a negative length
        changes.append("The front wheels are not ahead of the rear wheels; check the nose direction and the wheel "
                       "positions. The vehicle length is used as reference length meanwhile.")
        wb = None
    L = float(hi[0] - lo[0])
    out = {
        "bbox_min": lo.tolist(), "bbox_max": hi.tolist(), "length": L, "width": float(hi[1] - lo[1]),
        "height": float(hi[2] - z0), "ground_z": z0, "ground": ext["ground"], "frontal_area": area, "planform_area": planform,
        "frontal_area_uncertainty": pa.uncertainty, "frontal_area_pixel": pa.pixel_size, "wheelbase": wb,
        "body_clearance": ext["clearance"], "component_areas": ext["areas"],
    }

    def mutate(c: ProjectConfig) -> None:
        c.geometry.summary = GeometrySummary(
            bbox_min=tuple(lo.tolist()), bbox_max=tuple(hi.tolist()), triangle_count=merged.n_faces,
            surface_area=float(merged.area()), region_names=list(project.raw_surface().region_names),
        )
        ref = c.reference
        ref.area_computed = area
        ref.area_planform_computed = planform
        ref.area_computed_resolution = pa.pixel_size
        computed = planform if ref.area_source == AreaSource.COMPUTED_PLANFORM else area
        if ref.locked:
            if ref.area_source != AreaSource.MANUAL and ref.area and abs(ref.area - computed) > 1e-3 * ref.area:
                kind = "planform" if ref.area_source == AreaSource.COMPUTED_PLANFORM else "frontal"
                changes.append(f"Computed {kind} area is now {computed:.4f} m² but the reference area stays {ref.area:.4f} m² "
                               "because results exist (change it explicitly to recompute coefficients).")
            return
        if ref.area_source != AreaSource.MANUAL or ref.area is None:
            ref.area = computed
        if ref.length_source in ("wheelbase", "vehicle_length") or ref.length is None:
            if wb:
                ref.length = wb
                ref.length_source = "wheelbase"
            else:
                ref.length = L
                ref.length_source = "vehicle_length"
        if ref.moment_centre_source == MomentCentreSource.WHEELBASE_MID_GROUND and wb and c.vehicle.wheelbase_mid_ground(z0):
            ref.moment_centre = c.vehicle.wheelbase_mid_ground(z0)
        elif ref.moment_centre_source != MomentCentreSource.MANUAL or ref.moment_centre is None:
            ref.moment_centre = (float(0.5 * (lo[0] + hi[0])), c.vehicle.centreline_y, float(z0))
            if ref.moment_centre_source == MomentCentreSource.WHEELBASE_MID_GROUND:
                ref.moment_centre_source = MomentCentreSource.VEHICLE_CENTRE_GROUND
        ref.density = c.physics.fluid.density

    project.update(mutate, snapshot=False)
    out["reference_notices"] = changes
    return out


def set_units(project: Project, unit: str) -> dict[str, Any]:
    if unit not in UNIT_CANDIDATES:
        raise AeroBenchError.make("geometry.units_unknown", ErrorCategory.GEOMETRY, f"Unknown unit '{unit}'.")
    project.update(lambda c: setattr(c.geometry, "transform", c.geometry.transform.model_copy(update={"unit_scale": UNIT_CANDIDATES[unit],
                                                                                                    "auto_place": True})))
    return identify_and_derive(project, identify=True)


def set_orientation(project: Project, forward_axis: str, up_axis: str) -> dict[str, Any]:
    def mutate(c: ProjectConfig) -> None:
        c.geometry.transform = c.geometry.transform.model_copy(update={"forward_axis": AxisDir(forward_axis), "up_axis": AxisDir(up_axis),
                                                                       "auto_place": True})

    project.update(mutate)
    return identify_and_derive(project, identify=True)


def apply_repair(project: Project, steps: Optional[list[tuple[str, dict]]] = None) -> dict[str, Any]:
    rid = project.config.geometry.revision_id
    if rid is None:
        raise AeroBenchError.make("geometry.missing", ErrorCategory.GEOMETRY, "Import geometry before repairing it.")
    raw = project.raw_surface(rid)
    rev = project.geometry_revision(rid)
    fixed, records = repair_mod.run_pipeline(raw, steps)
    report = inspect_surface(fixed, units_assumed=rev.get("units") or "m")
    new_id = project.add_geometry_revision(fixed, kind="repaired", parent_id=rid, operations=(rev.get("operations") or []) + [r.as_dict() for r in records],
                                           report=report.as_dict(), label=f"{rev.get('label')} (repaired)", units=rev.get("units"),
                                           source_filename=rev.get("source_filename"))
    project.update(lambda c: setattr(c.geometry, "revision_id", new_id))
    regions_changed = set(fixed.region_names) != set(raw.region_names)
    ident = identify_and_derive(project, identify=regions_changed)
    return {"revision_id": new_id, "operations": [r.as_dict() for r in records], "report": report.as_dict(), "identification": ident}


def set_components(project: Project, components: list[dict], wheels: Optional[list[dict]] = None) -> dict[str, Any]:
    from ..domain.vehicle import Wheel

    comps = [GeometryComponent.model_validate(c) for c in components]
    regions = set(project.raw_surface().region_names)
    unknown = [r for c in comps for r in c.source_regions if r not in regions]
    if unknown:
        raise AeroBenchError.make("geometry.region_missing", ErrorCategory.GEOMETRY, f"Unknown region(s): {', '.join(unknown)}")
    unassigned = regions - {r for c in comps for r in c.source_regions}

    def mutate(c: ProjectConfig) -> None:
        c.geometry.components = comps
        if wheels is not None:
            c.vehicle.wheels = [Wheel.model_validate(w) for w in wheels]

    project.update(mutate)
    out = refresh_derived(project)
    out["unassigned_regions"] = sorted(unassigned)
    return out


def preview_payload(project: Project, max_faces: int = 400_000, cfg: Optional[ProjectConfig] = None, posed: bool = True) -> tuple[dict, bytes]:
    """Decimated CFD-frame geometry for the 3D viewport (binary .abg-style payload): posed as in
    the tunnel, or (``posed=False``) as imported, the frame shape zones and parameters use."""
    cfg = cfg or project.config
    cfd = project.cfd_surface(cfg=cfg)
    if not cfg.geometry.components:
        parts, z0 = {"all": cfd}, 0.0
    elif posed:
        parts, z0, _ = posed_components(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
    else:
        from .zones import _display_parts

        parts, z0 = _display_parts(cfd, cfg, posed=False)
    surfs = []
    names = []
    for cid, s in parts.items():
        surfs.append(TriSurface(s.points, s.faces, np.zeros(s.n_faces, np.int32), [cid]))
        names.append(cid)
    merged = TriSurface.merge(surfs)
    decimated = False
    if merged.n_faces > max_faces:
        lo, hi = merged.bbox()
        cell = float(np.linalg.norm(hi - lo)) / math.sqrt(max_faces) * 0.9
        merged, _ = repair_mod.simplify_vertex_clustering(merged, cell)
        decimated = True
    from ..viz.payload import encode_mesh

    header, blob = encode_mesh(merged.points.astype(np.float32), merged.faces.astype(np.uint32), part_ids=merged.regions.astype(np.uint16),
                               part_names=merged.region_names)
    header.update({"ground_z": z0, "decimated": decimated})
    return header, blob
