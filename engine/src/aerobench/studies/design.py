"""From design values to a runnable project configuration.

A design is evaluated against a *frozen* base configuration (the project as it was when
the study started).  Applying a design:

1. sets operating/pose/mesh/numerics targets;
2. builds the design geometry when geometry parameters (or a variant) differ from the
   baseline: the base surface is placed in the CFD frame with the baseline's resolved
   transform (``auto_place`` disabled, so a longer tail does not shift the car), deformed,
   validated, and stored as a ``parameterised`` geometry revision in the CFD frame (the
   design then uses an identity transform).  Identical inputs reuse the same revision;
3. freezes the domain/refinement sizing to the baseline vehicle extents for every design
   whose geometry or pose differs, so meshes differ only where the vehicle differs.

Reference values (area, length) stay those of the baseline: coefficients of different
designs are therefore directly comparable (a design's own frontal area is reported
separately for information).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional


from ..core.errors import AeroBenchError
from ..domain.geometry import AxisDir, GeometryTransform, SizingBox
from ..domain.project import ProjectConfig, validate_project
from ..geometry.deform import apply_parameters, check_deformation, parameter_values_complete
from ..geometry.inspect import inspect_surface
from ..geometry.pose import place_in_cfd_frame, posed_components
from ..geometry.trisurface import TriSurface
from ..project.store import Project
from .targets import apply_targets

IDENTITY = GeometryTransform(unit_scale=1.0, forward_axis=AxisDir.NX, up_axis=AxisDir.PZ, translation=(0.0, 0.0, 0.0), auto_place=False)


@dataclass
class DesignBuild:
    cfg: ProjectConfig
    geometry_revision_id: Optional[str]
    geometry_check: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def baseline_extent(project: Project, cfg: ProjectConfig) -> SizingBox:
    from ..services.geometry import vehicle_extent

    ext = vehicle_extent(project, cfg)
    return SizingBox(bbox_min=tuple(ext["bbox_min"]), bbox_max=tuple(ext["bbox_max"]), clearance=ext["clearance"], source="baseline")


def frozen_transform(project: Project, cfg: ProjectConfig, revision_id: str) -> GeometryTransform:
    """The baseline's resolved transform with auto-placement switched off."""
    if not cfg.geometry.transform.auto_place:
        return cfg.geometry.transform
    _, resolved = place_in_cfd_frame(project.raw_surface(cfg.geometry.revision_id), cfg.geometry.transform)
    return resolved.model_copy(update={"auto_place": False})


def _cache_key(base_rev: str, transform: GeometryTransform, cfg: ProjectConfig, values: dict[str, float]) -> str:
    payload = {
        "base": base_rev, "transform": transform.model_dump(mode="json"),
        "components": [c.model_dump(mode="json") for c in cfg.geometry.components],
        "wheels": [w.model_dump(mode="json") for w in cfg.vehicle.wheels],
        "centreline_y": cfg.vehicle.centreline_y,
        "parameters": [p.model_dump(mode="json") for p in cfg.parameters if p.id in values],
        "values": {k: round(float(v), 12) for k, v in sorted(values.items())},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_design(project: Project, base_cfg: ProjectConfig, assignments: dict[str, Any], sizing: Optional[SizingBox] = None,
                 check_intersections: bool = True) -> DesignBuild:
    cfg = base_cfg.model_copy(deep=True)
    try:
        side = apply_targets(cfg, assignments)
    except (KeyError, ValueError) as exc:
        return DesignBuild(cfg, None, errors=[{"code": "design.invalid_value", "message": str(exc)}])
    out = DesignBuild(cfg, cfg.geometry.revision_id)
    geometry_changed = side["pose_changed"]
    base_rev = base_cfg.geometry.revision_id
    if base_rev is None:
        out.errors.append({"code": "geometry.missing", "message": "The base configuration has no geometry."})
        return out

    # ---- geometry variant (alternative revision in the same source frame)
    if side["variant"] is not None and side["variant"] != base_rev:
        try:
            project.geometry_revision(side["variant"])
        except AeroBenchError:
            out.errors.append({"code": "geometry.variant_missing", "message": f"Unknown geometry revision '{side['variant']}'."})
            return out
        cfg.geometry.transform = frozen_transform(project, base_cfg, base_rev)
        cfg.geometry.revision_id = side["variant"]
        regions = set(project.raw_surface(side["variant"]).region_names)
        missing = sorted({r for c in cfg.geometry.components for r in c.source_regions} - regions)
        if missing:
            out.errors.append({"code": "geometry.variant_regions", "message": "The variant lacks region(s) used by the component "
                               f"assignment: {', '.join(missing)}. Variants must keep the baseline's region names."})
            return out
        geometry_changed = True
        out.geometry_revision_id = side["variant"]

    # ---- geometry parameters
    pv = side["param_values"]
    if pv:
        try:
            full = parameter_values_complete(cfg.parameters, {**cfg.parameter_values, **pv})
        except ValueError as exc:
            out.errors.append({"code": "design.invalid_value", "message": str(exc)})
            return out
        cfg.parameter_values = full
        if any(abs(full[p.id] - p.baseline) > 0 for p in cfg.parameters):
            src_rev = cfg.geometry.revision_id
            transform = cfg.geometry.transform if not cfg.geometry.transform.auto_place else frozen_transform(project, cfg, src_rev)  # type: ignore[arg-type]
            key = _cache_key(src_rev, transform, cfg, full)  # type: ignore[arg-type]
            row = project.db.one("SELECT id, parameters_json FROM geometry_revisions WHERE kind = 'parameterised' AND "
                                 "json_extract(parameters_json, '$.cache_key') = ?", (key,))
            if row is not None:
                params = json.loads(row["parameters_json"])
                out.geometry_check = params.get("check")
                if not (out.geometry_check or {}).get("valid", True):
                    out.errors.extend(out.geometry_check.get("errors", []))  # type: ignore[union-attr]
                    return out
                from ..domain.vehicle import Wheel

                cfg.vehicle.wheels = [Wheel.model_validate(w) for w in params["wheels"]]
                cfg.geometry.revision_id = row["id"]
                cfg.geometry.transform = IDENTITY
                out.geometry_revision_id = row["id"]
                out.notes.append("Reused an identical parameterised geometry.")
            else:
                cfd, _ = place_in_cfd_frame(project.raw_surface(src_rev), transform)
                try:
                    res = apply_parameters(cfd, cfg.geometry.components, cfg.vehicle.wheels, cfg.parameters, full, cfg.vehicle.centreline_y)
                except ValueError as exc:
                    out.errors.append({"code": "geometry.parameter_invalid", "message": str(exc)})
                    return out
                cfg.vehicle.wheels = res.wheels
                chk = check_deformation(cfd, res, cfg.parameters, check_intersections=check_intersections, components=cfg.geometry.components)
                # ground contact is judged on the posed vehicle
                parts, z0, _ = posed_components(res.surface, cfg.geometry.components, cfg.vehicle, cfg.tunnel.contact_patch_fraction)
                body_min = [s.bbox()[0][2] for cid, s in parts.items()
                            if cfg.geometry.component(cid).role.value not in ("wheel", "stilt") and s.n_faces]
                if body_min and min(body_min) <= z0:
                    chk.errors.append({"code": "geometry.ground_contact",
                                       "message": f"The deformed body reaches the ground plane ({(min(body_min) - z0) * 1000:.1f} mm)."})
                    chk.valid = False
                out.geometry_check = chk.as_dict()
                report = inspect_surface(res.surface, units_assumed="m").as_dict()
                rev_id = project.add_geometry_revision(
                    res.surface, kind="parameterised", parent_id=src_rev, label="Parameterised: " + ", ".join(f"{k}={v:g}" for k, v in sorted(pv.items())),
                    units="m", report=report,
                    parameters={"cache_key": key, "values": full, "frame": "cfd", "check": out.geometry_check,
                                "wheels": [w.model_dump(mode="json") for w in res.wheels],
                                "definitions": [p.model_dump(mode="json") for p in cfg.parameters]},
                )
                if not chk.valid:
                    out.errors.extend(chk.errors)
                    return out
                cfg.geometry.revision_id = rev_id
                cfg.geometry.transform = IDENTITY
                out.geometry_revision_id = rev_id
            geometry_changed = True

    if geometry_changed:
        cfg.geometry.sizing_box = sizing or baseline_extent(project, base_cfg)
        # yaw studies: keep one mesh for the whole yaw range is handled by the study (tunnel.design_yaw_range_deg)
    issues = validate_project(cfg, "solve")
    errs = [i for i in issues if i.severity.value == "error"]
    if errs:
        out.errors.extend({"code": i.code or "config.invalid", "message": f"{i.path}: {i.message}"} for i in errs)
    return out


def design_frontal_area(project: Project, cfg: ProjectConfig) -> Optional[float]:
    """Projected frontal area of a design's posed vehicle (information only)."""
    from ..geometry.frontal_area import projected_area
    from ..services.geometry import vehicle_extent

    try:
        ext = vehicle_extent(project, cfg)
        surf: TriSurface = ext["surface"]
        return float(projected_area(surf, direction=(1.0, 0.0, 0.0), resolution=600).area)
    except Exception:  # noqa: BLE001 - informational only
        return None
