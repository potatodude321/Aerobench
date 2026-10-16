"""Resolve the mesh configuration into a concrete, inspectable mesh plan.

The plan contains every number that ends up in ``blockMeshDict`` and
``snappyHexMeshDict``: domain bounds snapped to whole background cells, base cell, levels,
refinement volumes, layer design, ``locationInMesh`` and a cell-count estimate.  The UI
shows the plan *before* meshing (BUILD_SPEC §8.4: estimates, not promises).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

from ..domain.geometry import ComponentRole
from ..domain.mesh import (
    BoxShape,
    CylinderShape,
    LayerMode,
    MeshConfig,
    MeshPreset,
    RefinementRegion,
    RegionKind,
    RotatedBoxShape,
    SphereShape,
    SurfaceShape,
)
from ..domain.project import ProjectConfig, effective_yaw_range
from ..domain.setup import Symmetry
from .layers import LayerDesign, design_layers, relative_layers

# surface cell = L / divisor ; base = L / 10 (snapped to a power-of-two multiple of s)
PRESET_SURFACE_DIVISOR = {MeshPreset.FAST: 160.0, MeshPreset.BALANCED: 320.0, MeshPreset.HIGH_ACCURACY: 640.0}
FINE_ROLES = {ComponentRole.WING, ComponentRole.SPOILER, ComponentRole.SPLITTER, ComponentRole.MIRROR}


@dataclass
class SurfacePlan:
    component_id: str
    role: str
    min_level: int
    max_level: int
    feature_level: int
    cell_size: float
    layers: bool
    n_layers: int = 0


@dataclass
class RegionPlan:
    id: str
    name: str
    kind: str
    level: int
    cell_size: float
    shape: dict
    auto: bool
    volume: float


@dataclass
class MeshPlan:
    base_cell: float
    body_level: int
    body_cell: float
    domain_min: tuple[float, float, float]
    domain_max: tuple[float, float, float]
    n_cells_block: tuple[int, int, int]
    ground_z: float
    location_in_mesh: tuple[float, float, float]
    surfaces: list[SurfacePlan]
    regions: list[RegionPlan]
    distance_levels: list[tuple[float, int]]
    layer_design: Optional[LayerDesign]
    estimate_cells: int
    estimate_range: tuple[int, int]
    estimate_memory_gb: tuple[float, float]
    blockage_ratio: Optional[float]
    vehicle_bbox_min: tuple[float, float, float]
    vehicle_bbox_max: tuple[float, float, float]
    yaw_range: tuple[float, float]
    half_model: bool
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def _level_for(base: float, size: float) -> int:
    return max(0, int(round(math.log2(base / size))))


def _box(lo, hi) -> BoxShape:
    return BoxShape(min=tuple(float(x) for x in lo), max=tuple(float(x) for x in hi))


def _shape_volume(shape) -> float:
    if isinstance(shape, BoxShape):
        return float(np.prod(np.maximum(np.subtract(shape.max, shape.min), 0)))
    if isinstance(shape, RotatedBoxShape):
        return float(np.prod(shape.span))
    if isinstance(shape, CylinderShape):
        return math.pi * shape.radius**2 * float(np.linalg.norm(np.subtract(shape.point2, shape.point1)))
    if isinstance(shape, SphereShape):
        return 4.0 / 3.0 * math.pi * shape.radius**3
    if isinstance(shape, SurfaceShape):
        return float(shape.volume)
    return 0.0


def _shape_bounds(shape: dict) -> tuple[np.ndarray, np.ndarray]:
    k = shape["kind"]
    if k == "box":
        return np.asarray(shape["min"], float), np.asarray(shape["max"], float)
    if k == "surface":
        return np.asarray(shape["bbox_min"], float), np.asarray(shape["bbox_max"], float)
    if k == "sphere":
        c, r = np.asarray(shape["centre"], float), float(shape["radius"])
        return c - r, c + r
    if k == "cylinder":
        p1, p2, r = np.asarray(shape["point1"], float), np.asarray(shape["point2"], float), float(shape["radius"])
        return np.minimum(p1, p2) - r, np.maximum(p1, p2) + r
    if k == "rotated_box":
        o, sp = np.asarray(shape["origin"], float), np.asarray(shape["span"], float)
        e1 = np.asarray(shape["e1"], float) / np.linalg.norm(shape["e1"])
        e3 = np.asarray(shape["e3"], float) / np.linalg.norm(shape["e3"])
        e2 = np.cross(e3, e1)
        corners = np.array([o + a * sp[0] * e1 + b * sp[1] * e2 + c * sp[2] * e3 for a in (0, 1) for b in (0, 1) for c in (0, 1)])
        return corners.min(0), corners.max(0)
    raise ValueError(k)


def _inside(shape: dict, p: np.ndarray) -> np.ndarray:
    k = shape["kind"]
    if k in ("box", "surface"):  # a modelled volume counts by its bounding box here
        lo, hi = _shape_bounds(shape)
        return np.all((p >= lo) & (p <= hi), axis=1)
    if k == "sphere":
        return np.linalg.norm(p - np.asarray(shape["centre"], float), axis=1) <= float(shape["radius"])
    if k == "cylinder":
        a, b = np.asarray(shape["point1"], float), np.asarray(shape["point2"], float)
        ab = b - a
        t = (p - a) @ ab / max(float(ab @ ab), 1e-300)
        d = np.linalg.norm(p - a - np.outer(t, ab), axis=1)
        return (t >= 0) & (t <= 1) & (d <= float(shape["radius"]))
    if k == "rotated_box":
        o, sp = np.asarray(shape["origin"], float), np.asarray(shape["span"], float)
        e1 = np.asarray(shape["e1"], float) / np.linalg.norm(shape["e1"])
        e3 = np.asarray(shape["e3"], float) / np.linalg.norm(shape["e3"])
        e2 = np.cross(e3, e1)
        q = p - o
        u = np.stack([q @ e1, q @ e2, q @ e3], axis=1)
        return np.all((u >= 0) & (u <= sp), axis=1)
    return np.zeros(len(p), dtype=bool)


def exclusive_volumes(regions: list["RegionPlan"], n: int = 24) -> list[float]:
    """Volume of each refinement region that is not inside a finer one (or an equally fine one
    listed before it): where regions overlap, the mesher refines to the finest level, so the
    cells there are counted once. Sampled on a grid over each region's own bounds."""
    out = []
    shapes = [r.shape for r in regions]
    for i, r in enumerate(regions):
        try:
            lo, hi = _shape_bounds(shapes[i])
        except (KeyError, ValueError):
            out.append(r.volume)
            continue
        g = [(lo[a] + (np.arange(n) + 0.5) / n * (hi[a] - lo[a])) for a in range(3)]
        pts = np.stack(np.meshgrid(*g, indexing="ij"), axis=-1).reshape(-1, 3)
        own = _inside(shapes[i], pts)
        if not own.any():
            out.append(0.0 if r.volume == 0 else r.volume)
            continue
        covered = np.zeros(len(pts), dtype=bool)
        for j, o in enumerate(regions):
            if j == i or not (o.level > r.level or (o.level == r.level and j < i)):
                continue
            covered |= _inside(shapes[j], pts)
        out.append(r.volume * float((own & ~covered).sum()) / float(own.sum()))
    return out


def automotive_regions(
    cfg: ProjectConfig,
    lo: np.ndarray,
    hi: np.ndarray,
    z0: float,
    n: int,
    body_clearance: Optional[float],
    yaw_range: tuple[float, float],
) -> list[RefinementRegion]:
    """Automotive refinement volumes, sized from vehicle dimensions."""
    L = hi[0] - lo[0]
    W = hi[1] - lo[1]
    yc = 0.5 * (lo[1] + hi[1])
    top = hi[2]
    preset = cfg.mesh.preset
    hi_acc = preset == MeshPreset.HIGH_ACCURACY
    s_lo, s_hi = min(yaw_range), max(yaw_range)
    # wake drifts to -y for beta > 0 (flow direction (cos b, -sin b, 0))
    def widen(dist):
        left = max(0.0, math.sin(math.radians(s_hi))) * dist
        right = max(0.0, -math.sin(math.radians(s_lo))) * dist
        return left, right

    regs: list[RefinementRegion] = []

    def add(rid, name, kind, shape, level):
        lvl = max(0, level)
        regs.append(RefinementRegion(id=rid, name=name, kind=kind, shape=shape, level=lvl, auto=True))

    pad = 0.1 * L
    l1, r1 = widen(L * 0.3)
    add("vehicle_box", "Near-vehicle box", RegionKind.VEHICLE_BOX,
        _box((lo[0] - pad, lo[1] - pad - l1, z0), (hi[0] + 0.3 * L, hi[1] + pad + r1, top + pad)), n - 3)
    wake_near_len = (1.5 if hi_acc else 1.0) * L
    l2, r2 = widen(wake_near_len)
    add("wake_near", "Near wake", RegionKind.WAKE_NEAR,
        _box((hi[0] - 0.2 * L, lo[1] - 0.05 * L - l2, z0), (hi[0] + wake_near_len, hi[1] + 0.05 * L + r2, top + 0.05 * L)),
        n - (1 if hi_acc else 2))
    wake_far_len = 3.0 * L
    l3, r3 = widen(wake_far_len)
    add("wake_far", "Far wake", RegionKind.WAKE_FAR,
        _box((hi[0] - 0.1 * L, lo[1] - 0.3 * L - l3, z0), (hi[0] + wake_far_len, hi[1] + 0.3 * L + r3, top + 0.3 * L)), n - 4)
    add("front", "Front stagnation", RegionKind.FRONT,
        _box((lo[0] - 0.2 * L, lo[1], z0), (lo[0] + 0.05 * L, hi[1], top)), n - 2)
    if body_clearance is not None and body_clearance > 0:
        add("underbody", "Underbody", RegionKind.UNDERBODY,
            _box((lo[0], yc - 0.47 * W, z0), (hi[0], yc + 0.47 * W, z0 + 1.5 * body_clearance)), n - 1)
    for w in cfg.vehicle.wheels:
        c = np.asarray(w.centre)
        half = 0.5 * (w.width or 0.25 * w.radius * 2) + 0.05 * w.radius * 2
        ax = np.asarray(w.axis)
        add(f"wheel_{w.position.value}", f"Wheel {w.position.value}", RegionKind.WHEEL,
            CylinderShape(point1=tuple((c - ax * half).tolist()), point2=tuple((c + ax * half).tolist()), radius=1.2 * w.radius), n - 1)
    return regs


def resolve_mesh_plan(
    cfg: ProjectConfig,
    vehicle_bbox_min,
    vehicle_bbox_max,
    ground_z: float,
    component_areas: Optional[dict[str, float]] = None,
    body_clearance: Optional[float] = None,
    frontal_area: Optional[float] = None,
) -> MeshPlan:
    lo = np.asarray(vehicle_bbox_min, float)
    hi = np.asarray(vehicle_bbox_max, float)
    lo[2] = max(lo[2], ground_z)
    L = float(hi[0] - lo[0])
    if L <= 0:
        raise ValueError("vehicle has zero length")
    m: MeshConfig = cfg.mesh
    notes: list[str] = []
    warnings: list[str] = []

    # -------------------------------------------------------- base and body cell
    if m.preset != MeshPreset.CUSTOM:
        s_target = L / PRESET_SURFACE_DIVISOR[m.preset]
        n = max(1, int(round(math.log2((L / 10.0) / s_target))))
        base = s_target * 2**n if m.base_cell_size is None else m.base_cell_size
        if m.base_cell_size is not None:
            n = _level_for(base, s_target)
    else:
        base = m.base_cell_size or L / 10.0
        body_ref = next((sr for sr in m.surface if sr.role == ComponentRole.BODY), None)
        n = body_ref.min_level if body_ref else 4
    s = base / 2**n
    curv = 0 if m.preset == MeshPreset.FAST else 1

    # -------------------------------------------------------- domain
    yaw_rng = effective_yaw_range(cfg)
    t = cfg.tunnel
    half = t.symmetry == Symmetry.HALF
    max_side = max(abs(math.sin(math.radians(yaw_rng[0]))), abs(math.sin(math.radians(yaw_rng[1]))))
    x_min = lo[0] - t.upstream * L
    x_max = hi[0] + t.downstream * L
    y_min = lo[1] - t.lateral * L - max_side * t.downstream * L * 0.5
    y_max = hi[1] + t.lateral * L + max_side * t.downstream * L * 0.5
    z_min = ground_z
    z_max = hi[2] + t.top * L
    if half:
        y_max = cfg.vehicle.centreline_y
    nx = int(math.ceil((x_max - x_min) / base))
    ny = int(math.ceil((y_max - y_min) / base))
    nz = int(math.ceil((z_max - z_min) / base))
    x_max = x_min + nx * base
    if half:
        y_min = y_max - ny * base
    else:
        extra = ny * base - (y_max - y_min)
        y_min -= extra / 2
        y_max = y_min + ny * base
    z_max = z_min + nz * base
    blockage = None
    if frontal_area:
        blockage = frontal_area / ((y_max - y_min) * (z_max - z_min)) * (0.5 if half else 1.0)
        if blockage > 0.02:
            warnings.append(f"Blockage ratio {blockage * 100:.1f}% exceeds 2%; enlarge the lateral/top clearance.")
        elif blockage > 0.01:
            notes.append(f"Blockage ratio {blockage * 100:.1f}% (between 1% and 2%).")

    # -------------------------------------------------------- surfaces
    surfaces: list[SurfacePlan] = []
    overrides = {cid: sr for sr in m.surface for cid in sr.component_ids}
    role_over = {sr.role: sr for sr in m.surface if sr.role is not None and not sr.component_ids}
    for comp in cfg.geometry.components:
        sr = overrides.get(comp.id) or role_over.get(comp.role)
        if sr is not None:
            mn, mx = sr.min_level, max(sr.max_level, sr.min_level)
            fl = sr.feature_level if sr.feature_level is not None else mx
            lay = sr.layers
        else:
            extra = 1 if comp.role in FINE_ROLES else 0
            mn = n + extra
            mx = n + extra + curv
            if comp.role == ComponentRole.STILT:
                mx = mn
            fl = mx
            lay = comp.role not in (ComponentRole.STILT,)
        surfaces.append(SurfacePlan(comp.id, comp.role.value, mn, mx, fl, base / 2**mn, lay))

    # -------------------------------------------------------- regions
    regions: list[RefinementRegion] = []
    if m.auto_regions:
        regions.extend(automotive_regions(cfg, lo, hi, ground_z, n, body_clearance, yaw_rng))
    regions.extend(r for r in m.regions if not r.auto)
    # user overrides of auto regions (same id, auto=False replaces)
    by_id: dict[str, RefinementRegion] = {}
    for r in regions:
        by_id[r.id] = r
    for r in m.regions:
        if r.auto and r.id in by_id:
            # user tweaked level / enabled flag of an auto region: keep geometry from automation
            by_id[r.id] = by_id[r.id].model_copy(update={"level": r.level, "enabled": r.enabled})
    region_plans = [
        RegionPlan(r.id, r.name, r.kind.value, r.level, base / 2**r.level, r.shape.model_dump(mode="json"), r.auto, _shape_volume(r.shape))
        for r in by_id.values()
        if r.enabled
    ]
    distance_levels: list[tuple[float, int]] = []
    if m.preset == MeshPreset.BALANCED:
        distance_levels = [(0.02 * L, n - 1), (0.06 * L, n - 2)]
    elif m.preset == MeshPreset.HIGH_ACCURACY:
        distance_levels = [(0.015 * L, n - 1), (0.05 * L, n - 2)]
    distance_levels = [(d, lv) for d, lv in distance_levels if lv >= 1]

    # -------------------------------------------------------- layers
    lc = m.layers
    ld: Optional[LayerDesign] = None
    nu = cfg.physics.fluid.kinematic_viscosity
    if lc.mode == LayerMode.TARGET_YPLUS:
        ld = design_layers(lc.target_yplus, cfg.operating.speed, L, nu, s, lc.expansion_ratio, lc.final_relative, lc.n_layers)
        warnings.extend(ld.warnings)
    elif lc.mode == LayerMode.RELATIVE:
        ld = relative_layers(lc.n_layers or 3, lc.expansion_ratio, lc.final_relative, s, cfg.operating.speed, L, nu)
    if ld is not None:
        for sp in surfaces:
            sp.n_layers = ld.n_layers if sp.layers else 0

    # -------------------------------------------------------- location in mesh
    loc = (x_min + 1.5313 * base, (y_min + 1.4721 * base) if half else (y_max - 1.4721 * base), z_max - 1.3917 * base)

    # -------------------------------------------------------- estimate
    domain_vol = (x_max - x_min) * (y_max - y_min) * (z_max - z_min)
    est = domain_vol / base**3
    # refinement volumes: each point counts once, at the finest level of the volumes it lies in
    # (overlapping and nested boxes are not counted twice)
    for r, vol in zip(region_plans, exclusive_volumes(region_plans)):
        est += vol / r.cell_size**3 * 0.875
    areas = component_areas or {}
    for sp in surfaces:
        A = areas.get(sp.component_id)
        if A is None:
            continue
        sc = base / 2 ** (0.5 * (sp.min_level + sp.max_level))
        if half:
            A *= 0.5
        surf_cells = A / sc**2
        est += surf_cells * 2 * m.n_cells_between_levels * 0.6 + surf_cells * sp.n_layers
    for d, lv in distance_levels:
        A_tot = sum(areas.values()) * (0.5 if half else 1.0)
        est += A_tot * d / (base / 2**lv) ** 3 * 0.5
    est_i = int(est)
    mem = (round(est_i * 1.0e-6 * 0.6, 2), round(est_i * 1.0e-6 * 1.8, 2))
    if est_i > m.max_global_cells:
        warnings.append(f"Estimated {est_i / 1e6:.1f} M cells exceeds maxGlobalCells ({m.max_global_cells / 1e6:.0f} M).")
    return MeshPlan(
        base_cell=base,
        body_level=n,
        body_cell=s,
        domain_min=(x_min, y_min, z_min),
        domain_max=(x_max, y_max, z_max),
        n_cells_block=(nx, ny, nz),
        ground_z=ground_z,
        location_in_mesh=tuple(float(v) for v in loc),
        surfaces=surfaces,
        regions=region_plans,
        distance_levels=distance_levels,
        layer_design=ld,
        estimate_cells=est_i,
        estimate_range=(int(est_i * 0.5), int(est_i * 2.0)),
        estimate_memory_gb=mem,
        blockage_ratio=blockage,
        vehicle_bbox_min=tuple(lo.tolist()),
        vehicle_bbox_max=tuple(hi.tolist()),
        yaw_range=yaw_rng,
        half_model=half,
        notes=notes,
        warnings=warnings,
    )
