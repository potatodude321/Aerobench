"""Automatic component identification (suggestions only — always user-overridable)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np

from ..domain.geometry import ComponentRole, GeometryComponent, WheelPosition
from ..domain.vehicle import Wheel
from ..core.fs import safe_name
from .inspect import face_components
from .trisurface import TriSurface

_ROLE_PATTERNS: list[tuple[ComponentRole, re.Pattern]] = [
    (ComponentRole.WHEEL, re.compile(r"wheel|tyre|tire|rim|hub|rad\b|reifen", re.I)),
    (ComponentRole.WING, re.compile(r"wing|aerofoil|airfoil|flap|element", re.I)),
    (ComponentRole.SPOILER, re.compile(r"spoiler|ducktail|lip", re.I)),
    (ComponentRole.SPLITTER, re.compile(r"splitter", re.I)),
    (ComponentRole.DIFFUSER, re.compile(r"diffuser", re.I)),
    (ComponentRole.FLOOR, re.compile(r"floor|underbody|undertray|plank", re.I)),
    (ComponentRole.MIRROR, re.compile(r"mirror", re.I)),
    (ComponentRole.COOLING_INLET, re.compile(r"inlet|intake|grille|grill", re.I)),
    (ComponentRole.COOLING_OUTLET, re.compile(r"outlet|exhaust|vent", re.I)),
    (ComponentRole.STILT, re.compile(r"stilt|strut|mount|support", re.I)),
    (ComponentRole.BODY, re.compile(r"body|chassis|shell|bodywork|car|vehicle", re.I)),
]

_POS_PATTERNS = {
    WheelPosition.FL: re.compile(r"(^|[^a-z])(fl|lf|front[_ -]?left|left[_ -]?front|vl)([^a-z]|$)", re.I),
    WheelPosition.FR: re.compile(r"(^|[^a-z])(fr|rf|front[_ -]?right|right[_ -]?front|vr)([^a-z]|$)", re.I),
    WheelPosition.RL: re.compile(r"(^|[^a-z])(rl|lr|rear[_ -]?left|left[_ -]?rear|hl)([^a-z]|$)", re.I),
    WheelPosition.RR: re.compile(r"(^|[^a-z])(rr|rear[_ -]?right|right[_ -]?rear|hr)([^a-z]|$)", re.I),
}


def role_from_name(name: str) -> Optional[ComponentRole]:
    for role, pat in _ROLE_PATTERNS:
        if pat.search(name):
            return role
    return None


def wheel_position_from_name(name: str) -> Optional[WheelPosition]:
    for pos, pat in _POS_PATTERNS.items():
        if pat.search(name):
            return pos
    return None


@dataclass
class WheelFit:
    centre: tuple[float, float, float]
    radius: float
    width: float
    axis: tuple[float, float, float]
    confidence: float

    def as_dict(self):
        return asdict(self)


def fit_wheel(points: np.ndarray, ground_z: Optional[float] = None) -> Optional[WheelFit]:
    """Fit a wheel to a point cloud (CFD frame).  Returns None if not wheel-like.

    A wheel has two similar extents (x, z ≈ diameter) and a smaller lateral width, and is
    roughly rotationally symmetric about a near-lateral axis.  The rolling radius is the
    95th-percentile radial distance from the fitted axis (robust to tread detail).
    """
    if len(points) < 12:
        return None
    c = points.mean(axis=0)
    cov = np.cov((points - c).T)
    w, v = np.linalg.eigh(cov)
    axis = v[:, 0]  # smallest variance direction for a disc-like body... check below
    # For a wheel (solid of revolution, width < diameter), the axis is the direction of
    # *smallest* extent.  Use extents along eigenvectors.
    proj = (points - c) @ v
    ext = proj.max(axis=0) - proj.min(axis=0)
    k = int(np.argmin(ext))
    axis = v[:, k]
    others = [i for i in range(3) if i != k]
    d1, d2 = ext[others[0]], ext[others[1]]
    if min(d1, d2) <= 0:
        return None
    roundness = min(d1, d2) / max(d1, d2)
    aspect = ext[k] / max(d1, d2)
    lateral = abs(axis[1])
    # centre: bbox centre in the plane normal to the axis
    mid = c + v[:, others[0]] * 0.5 * (proj[:, others[0]].max() + proj[:, others[0]].min()) + v[:, others[1]] * 0.5 * (
        proj[:, others[1]].max() + proj[:, others[1]].min()
    )
    mid = mid + axis * (0.5 * (proj[:, k].max() + proj[:, k].min()))
    rel = points - mid
    radial = rel - np.outer(rel @ axis, axis)
    r = np.linalg.norm(radial, axis=1)
    radius = float(np.percentile(r, 95))
    conf = float(np.clip(roundness, 0, 1) * np.clip(1.5 - aspect, 0, 1) * lateral)
    if ground_z is not None:
        touch = abs((mid[2] - radius) - ground_z) / max(radius, 1e-9)
        conf *= float(np.clip(1.2 - touch, 0, 1))
    ax = axis if axis[1] < 0 else -axis
    return WheelFit(tuple(float(x) for x in mid), radius, float(ext[k]), tuple(float(x) for x in ax), conf)


def _position_for(centre, all_centres) -> WheelPosition:
    xs = np.array([p[0] for p in all_centres])
    x_mid = 0.5 * (xs.min() + xs.max())
    front = centre[0] < x_mid  # nose at -x
    left = centre[1] < 0  # +y is vehicle right
    if front:
        return WheelPosition.FL if left else WheelPosition.FR
    return WheelPosition.RL if left else WheelPosition.RR


@dataclass
class IdentificationResult:
    components: list[GeometryComponent]
    wheels: list[Wheel]
    notes: list[str]


def identify_components(surf_cfd: TriSurface, ground_z: Optional[float] = None) -> IdentificationResult:
    """Suggest components and wheels for a surface already in the CFD frame.

    Strategy: (1) role from region names; (2) wheel detection by shape on wheel-named
    regions or, failing that, on connected components of unnamed regions in the lower
    corners; (3) everything else becomes body.
    """
    notes: list[str] = []
    lo, hi = surf_cfd.bbox()
    if ground_z is None:
        ground_z = float(lo[2])
    comps: list[GeometryComponent] = []
    wheels: list[Wheel] = []
    used_ids: set[str] = set()

    def uid(base: str) -> str:
        b = safe_name(base, "part")
        out, k = b, 1
        while out in used_ids:
            k += 1
            out = f"{b}_{k}"
        used_ids.add(out)
        return out

    wheel_candidates: list[tuple[str, WheelFit]] = []
    region_roles: dict[str, ComponentRole] = {}
    for ri, rname in enumerate(surf_cfd.region_names):
        mask = surf_cfd.regions == ri
        if not mask.any():
            continue
        role = role_from_name(rname)
        pts = surf_cfd.points[np.unique(surf_cfd.faces[mask])]
        if role == ComponentRole.WHEEL or role is None:
            fit = fit_wheel(pts, ground_z)
            if fit and (role == ComponentRole.WHEEL and fit.confidence > 0.2 or fit.confidence > 0.6):
                wheel_candidates.append((rname, fit))
                region_roles[rname] = ComponentRole.WHEEL
                continue
        region_roles[rname] = role or ComponentRole.BODY

    # single-region geometry: try connected components for wheels
    if not wheel_candidates and len(surf_cfd.region_names) == 1:
        n, labels = face_components(surf_cfd)
        if 4 <= n <= 64:
            notes.append("Wheels searched among disconnected parts of a single-region surface.")
            for c in range(n):
                mask = labels == c
                pts = surf_cfd.points[np.unique(surf_cfd.faces[mask])]
                fit = fit_wheel(pts, ground_z)
                if fit and fit.confidence > 0.6:
                    wheel_candidates.append((f"part{c}", fit))

    if wheel_candidates:
        centres = [f.centre for _, f in wheel_candidates]
        taken: set[WheelPosition] = set()
        for rname, fit in sorted(wheel_candidates, key=lambda t: -t[1].confidence):
            pos = wheel_position_from_name(rname) or _position_for(fit.centre, centres)
            if pos in taken:
                notes.append(f"Region '{rname}' looks like a wheel but position {pos.value} is already taken.")
                region_roles[rname] = ComponentRole.WHEEL
                continue
            taken.add(pos)
            cid = uid(f"wheel_{pos.value}")
            if rname in surf_cfd.region_names:
                comps.append(GeometryComponent(id=cid, name=f"Wheel {pos.value}", source_regions=[rname], role=ComponentRole.WHEEL,
                                               wheel_position=pos, suggested=True))
            else:
                notes.append(f"Wheel {pos.value} found as a disconnected part; split the surface by component to assign it.")
                cid = None
            wheels.append(Wheel(position=pos, component_id=cid, centre=fit.centre, axis=fit.axis, radius=fit.radius, width=fit.width,
                                source="auto", detection_confidence=round(fit.confidence, 3)))

    # group non-wheel regions by role
    by_role: dict[ComponentRole, list[str]] = {}
    for rname, role in region_roles.items():
        if role == ComponentRole.WHEEL and any(rname in c.source_regions for c in comps):
            continue
        if role == ComponentRole.WHEEL:
            role = ComponentRole.OTHER
        by_role.setdefault(role, []).append(rname)
    for role, regions in by_role.items():
        if role == ComponentRole.BODY:
            comps.insert(0, GeometryComponent(id=uid("body"), name="Body", source_regions=regions, role=role, suggested=True))
        else:
            for rname in regions:
                comps.append(GeometryComponent(id=uid(rname), name=rname, source_regions=[rname], role=role, suggested=True))
    order = {WheelPosition.FL: 0, WheelPosition.FR: 1, WheelPosition.RL: 2, WheelPosition.RR: 3}
    def comp_key(c: GeometryComponent):
        if c.role == ComponentRole.BODY:
            return (0, 0, c.id)
        if c.role == ComponentRole.WHEEL and c.wheel_position is not None:
            return (1, order[c.wheel_position], c.id)
        return (2, 0, c.id)
    comps.sort(key=comp_key)
    if len(wheels) not in (0, 4):
        notes.append(f"{len(wheels)} wheel(s) detected — check the wheel assignments.")
    return IdentificationResult(comps, sorted(wheels, key=lambda w: w.position.value), notes)
