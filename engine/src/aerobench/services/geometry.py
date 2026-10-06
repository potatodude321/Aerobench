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
from ..geometry.inspect import UNIT_CANDIDATES, inspect_surface
from ..geometry.io import read_surface
from ..geometry.pose import posed_components
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
    report = inspect_surface(raw, units_assumed=chosen)
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
            if not res.wheels and any(comp.role == ComponentRole.STILT for comp in res.components):
                c.vehicle.category = VehicleCategory.BLUFF_BODY

        project.update(mutate, snapshot=False)
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
    wb = cfg.vehicle.wheelbase
    L = float(hi[0] - lo[0])
    out = {
        "bbox_min": lo.tolist(), "bbox_max": hi.tolist(), "length": L, "width": float(hi[1] - lo[1]),
        "height": float(hi[2] - z0), "ground_z": z0, "ground": ext["ground"], "frontal_area": area,
        "frontal_area_uncertainty": pa.uncertainty, "frontal_area_pixel": pa.pixel_size, "wheelbase": wb,
        "body_clearance": ext["clearance"], "component_areas": ext["areas"],
    }
    changes: list[str] = []

    def mutate(c: ProjectConfig) -> None:
        c.geometry.summary = GeometrySummary(
            bbox_min=tuple(lo.tolist()), bbox_max=tuple(hi.tolist()), triangle_count=merged.n_faces,
            surface_area=float(merged.area()), region_names=list(project.raw_surface().region_names),
        )
        ref = c.reference
        ref.area_computed = area
        ref.area_computed_resolution = pa.pixel_size
        if ref.locked:
            if ref.area_source == AreaSource.COMPUTED_FRONTAL and ref.area and abs(ref.area - area) > 1e-3 * ref.area:
                changes.append(f"Computed frontal area is now {area:.4f} m² but the reference area stays {ref.area:.4f} m² "
                               "because results exist (change it explicitly to recompute coefficients).")
            return
        if ref.area_source == AreaSource.COMPUTED_FRONTAL or ref.area is None:
            ref.area = area
        if ref.length_source in ("wheelbase", "vehicle_length") or ref.length is None:
            if wb:
                ref.length = wb
                ref.length_source = "wheelbase"
            else:
                ref.length = L
                ref.length_source = "vehicle_length"
        if ref.moment_centre_source == MomentCentreSource.WHEELBASE_MID_GROUND and c.vehicle.wheelbase_mid_ground(z0):
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
    project.update(lambda c: setattr(c.geometry, "transform", c.geometry.transform.model_copy(update={"unit_scale": UNIT_CANDIDATES[unit]})))
    return identify_and_derive(project, identify=True)


def set_orientation(project: Project, forward_axis: str, up_axis: str) -> dict[str, Any]:
    def mutate(c: ProjectConfig) -> None:
        c.geometry.transform = c.geometry.transform.model_copy(update={"forward_axis": AxisDir(forward_axis), "up_axis": AxisDir(up_axis)})

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


def preview_payload(project: Project, max_faces: int = 400_000, cfg: Optional[ProjectConfig] = None) -> tuple[dict, bytes]:
    """Decimated, posed CFD-frame geometry for the 3D viewport (binary .abg-style payload)."""
    cfg = cfg or project.config
    cfd = project.cfd_surface(cfg=cfg)
    parts, z0, _ = posed_components(cfd, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction) if cfg.geometry.components else ({"all": cfd}, 0.0, {})
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
