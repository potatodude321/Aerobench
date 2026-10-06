"""Vehicle placement in the CFD frame, ride height / rake, ground plane, case surfaces."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.geometry import ComponentRole, GeometryComponent, GeometryTransform
from ..domain.vehicle import VehicleConfig
from .io import write_stl
from .trisurface import TriSurface


def place_in_cfd_frame(raw: TriSurface, transform: GeometryTransform) -> tuple[TriSurface, GeometryTransform]:
    """Apply unit scale + axis mapping; if ``auto_place``, recompute the translation so the
    nose is at x=0, the lowest point at z=0 and the vehicle centred on y=0."""
    rot = transform.rotation() * transform.unit_scale
    pts = raw.points @ rot.T
    faces = raw.faces if np.linalg.det(rot) > 0 else raw.faces[:, ::-1]
    tmp = TriSurface(pts, faces, raw.regions, list(raw.region_names))
    if transform.auto_place:
        lo, hi = tmp.bbox()
        t = (-lo[0], -0.5 * (lo[1] + hi[1]), -lo[2])
        transform = transform.model_copy(update={"translation": tuple(float(x) for x in t)})
    tmp.points = tmp.points + np.asarray(transform.translation)
    return tmp, transform


def front_rear_to_offset_rake(delta_front: float, delta_rear: float, wheelbase: float) -> tuple[float, float]:
    """Convert front/rear ride-height changes to (offset at rear axle, rake deg nose-down)."""
    if wheelbase <= 0:
        raise ValueError("wheelbase must be positive")
    s = (delta_rear - delta_front) / wheelbase
    if abs(s) >= 1:
        raise ValueError("ride-height difference exceeds wheelbase")
    return delta_rear, math.degrees(math.asin(s))


def ground_z(vehicle: VehicleConfig, surf_cfd: TriSurface, contact_fraction: float) -> tuple[float, dict]:
    """Ground-plane height including the tyre contact-patch treatment (ADR-0008)."""
    info: dict = {}
    if vehicle.wheels:
        bottoms = [w.centre[2] - w.radius for w in vehicle.wheels]
        base = min(bottoms)
        delta = contact_fraction * min(w.radius for w in vehicle.wheels)
        info = {
            "wheel_bottom_min": base,
            "wheel_bottom_spread": max(bottoms) - base,
            "contact_raise": delta,
            "contact_patch_length": 2 * math.sqrt(max(2 * min(w.radius for w in vehicle.wheels) * delta, 0.0)),
        }
        return float(base + delta), info
    lo, _ = surf_cfd.bbox()
    info = {"note": "no wheels: ground at z=0 (geometry placed by the user / auto-placement)"}
    return 0.0 if lo[2] <= 1e-9 else float(min(0.0, lo[2])), info


def _rake_pivot(vehicle: VehicleConfig, surf: TriSurface, z0: float) -> np.ndarray:
    xr = vehicle.rear_axle_x
    if xr is None:
        lo, hi = surf.bbox()
        return np.array([hi[0], vehicle.centreline_y, lo[2]])
    return np.array([xr, vehicle.centreline_y, z0])


def pose_matrix(vehicle: VehicleConfig, pivot: np.ndarray) -> np.ndarray:
    """4x4 transform for body-attached parts: rake (nose-down +) about the pivot, then lift."""
    th = -math.radians(vehicle.rake_deg)  # rotation about +y by -rake => nose down
    c, s = math.cos(th), math.sin(th)
    R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    m = np.eye(4)
    m[:3, :3] = R
    m[:3, 3] = pivot - R @ pivot + np.array([0.0, 0.0, vehicle.ride_height_offset])
    return m


@dataclass
class CaseSurfaces:
    files: dict[str, Path]  # component id -> stl path
    ground_z: float
    ground_info: dict
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    component_bboxes: dict[str, tuple[list[float], list[float]]] = field(default_factory=dict)
    min_body_clearance: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


def component_surface(surf_cfd: TriSurface, comp: GeometryComponent) -> TriSurface:
    missing = [r for r in comp.source_regions if r not in surf_cfd.region_names]
    if missing:
        raise AeroBenchError.make(
            "geometry.region_missing",
            ErrorCategory.GEOMETRY,
            f"Component '{comp.name}' refers to region(s) not present in the geometry: {', '.join(missing)}.",
            action="Re-assign the component's regions in the Geometry page.",
        )
    sub = surf_cfd.subset_regions(comp.source_regions)
    sub.region_names = [comp.id] * len(sub.region_names)
    sub = TriSurface(sub.points, sub.faces, np.zeros(sub.n_faces, np.int32), [comp.id])
    return sub


def posed_components(surf_cfd: TriSurface, components: list[GeometryComponent], vehicle: VehicleConfig,
                     contact_fraction: float) -> tuple[dict[str, TriSurface], float, dict]:
    z0, info = ground_z(vehicle, surf_cfd, contact_fraction)
    pivot = _rake_pivot(vehicle, surf_cfd, z0)
    m = pose_matrix(vehicle, pivot)
    out: dict[str, TriSurface] = {}
    for comp in components:
        s = component_surface(surf_cfd, comp)
        if comp.role != ComponentRole.WHEEL and (vehicle.rake_deg != 0 or vehicle.ride_height_offset != 0):
            s = s.transformed(m)
        out[comp.id] = s
    return out, z0, info


def write_case_surfaces(surf_cfd: TriSurface, components: list[GeometryComponent], vehicle: VehicleConfig,
                        contact_fraction: float, out_dir: Path) -> CaseSurfaces:
    """Write one binary STL per component (patch name = component id) into ``out_dir``."""
    parts, z0, info = posed_components(surf_cfd, components, vehicle, contact_fraction)
    files: dict[str, Path] = {}
    los, his = [], []
    bboxes = {}
    min_clear = None
    warnings: list[str] = []
    for comp in components:
        s = parts[comp.id]
        if s.n_faces == 0:
            raise AeroBenchError.make("geometry.empty_component", ErrorCategory.GEOMETRY, f"Component '{comp.name}' has no triangles.")
        p = Path(out_dir) / f"{comp.id}.stl"
        write_stl(p, s, binary=True, solid_name=comp.id)
        files[comp.id] = p
        lo, hi = s.bbox()
        los.append(lo)
        his.append(hi)
        bboxes[comp.id] = (lo.tolist(), hi.tolist())
        if comp.role not in (ComponentRole.WHEEL, ComponentRole.STILT):
            clear = float(lo[2] - z0)
            min_clear = clear if min_clear is None else min(min_clear, clear)
    if min_clear is not None and min_clear <= 0:
        warnings.append(f"A body-attached part reaches {min_clear * 1000:.1f} mm below the ground plane; it will be cut by the floor.")
    lo = np.min(los, axis=0)
    hi = np.max(his, axis=0)
    return CaseSurfaces(files, z0, info, tuple(lo.tolist()), tuple(hi.tolist()), bboxes, min_clear, warnings)
