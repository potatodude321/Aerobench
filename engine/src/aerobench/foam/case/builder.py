"""Project model → OpenFOAM mesh case and solve case (OpenCFD semantics via adapter)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import numpy as np

from ...aero.flow import InletTurbulenceValues, inlet_turbulence
from ...aero.forces import freestream_direction
from ...core.errors import AeroBenchError, ErrorCategory
from ...domain.geometry import ComponentRole, GeometryComponent
from ...domain.mesh import BoxShape, CylinderShape, LayerMode, RotatedBoxShape, SphereShape
from ...domain.project import ProjectConfig
from ...domain.setup import FarfieldBC, GroundMode, InletTurbulenceSpec, Symmetry, WallTreatment, YawGroundMode
from ...domain.vehicle import WheelRotation
from ...meshing.plan import MeshPlan
from ...version import GENERATOR_VERSION
from ..adapters.base import ForkAdapter
from ..ast import Dict, L, List, Str, T, Word, clean, dims, uniform, vec
from .generated import GeneratedCase, parse_extra_function_object
from .numerics import fv_schemes, fv_solution, preset_def, turbulence_fields

TUNNEL_PATCHES = ("inlet", "outlet", "ground", "top", "side_left", "side_right")
STOP_FO_NAME = "aerobenchStop"


@dataclass
class WheelBC:
    component_id: str
    origin: tuple[float, float, float]
    axis: tuple[float, float, float]
    omega: float
    note: str


@dataclass
class ResolvedCase:
    plan: MeshPlan
    components: list[GeometryComponent]
    tunnel_patches: list[str]
    vehicle_patches: list[str]
    patch_roles: dict[str, str]
    wheel_bcs: dict[str, WheelBC]
    u_inf: tuple[float, float, float]
    u_mag: float
    yaw_deg: float
    ground_velocity: Optional[tuple[float, float, float]]
    farfield: str
    turbulence: InletTurbulenceValues
    ref_area: float
    ref_length: float
    ref_density: float
    ref_velocity: float
    moment_centre: tuple[float, float, float]
    axle_reference: Optional[tuple[float, float, float]]
    wheelbase: Optional[float]
    n_procs: int
    model: str
    nu: float
    rho: float
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("plan")
        d["components"] = [c.id for c in self.components]
        d["turbulence"] = self.turbulence.as_dict()
        return d


def resolve_case(cfg: ProjectConfig, plan: MeshPlan) -> ResolvedCase:
    warnings: list[str] = []
    assumptions: list[str] = []
    comps = cfg.geometry.components
    vehicle_patches = [c.id for c in comps]
    roles = {c.id: c.role.value for c in comps}
    half = cfg.tunnel.symmetry == Symmetry.HALF
    tunnel = list(TUNNEL_PATCHES)
    if half:
        tunnel[tunnel.index("side_right")] = "symmetry"
    for t in tunnel:
        roles[t] = t

    op = cfg.operating
    d = freestream_direction(op.yaw_deg)
    u_inf = tuple(clean(x) for x in op.speed * d)
    # ground velocity
    ground_v = None
    if op.ground == GroundMode.MOVING:
        if op.yaw_ground_mode == YawGroundMode.ROAD:
            ground_v = (op.speed * math.cos(math.radians(op.yaw_deg)), 0.0, 0.0)
            assumptions.append("Moving ground: road mode — ground moves at U∞·cos β along the vehicle axis.")
        else:
            ground_v = u_inf
            assumptions.append("Moving ground: tunnel-belt mode — belt moves with the freestream vector.")
    else:
        assumptions.append("Stationary ground (no-slip).")
    ground_speed = float(np.linalg.norm(ground_v)) if ground_v else op.speed * math.cos(math.radians(op.yaw_deg))

    # wheels
    wheel_bcs: dict[str, WheelBC] = {}
    for w in cfg.vehicle.wheels:
        if not w.component_id:
            continue
        if not op.wheels_rotate or w.rotation == WheelRotation.STATIONARY:
            continue
        if w.rotation == WheelRotation.CUSTOM:
            if w.custom_omega is None:
                raise AeroBenchError.make("vehicle.wheel_omega", ErrorCategory.CONFIGURATION,
                                          f"Wheel {w.position.value} uses a custom speed but no angular velocity is set.")
            omega = float(w.custom_omega)
            note = "custom angular velocity"
        else:
            omega = ground_speed / w.radius
            note = f"ω = U_ground/R = {ground_speed:.4g}/{w.radius:.4g}"
        wheel_bcs[w.component_id] = WheelBC(w.component_id, tuple(w.centre), tuple(w.axis), omega, note)
    if wheel_bcs:
        assumptions.append("Wheel rotation modelled with rotatingWallVelocity (rotating-wall approximation; no MRF/sliding mesh).")

    # far-field strategy
    ff = cfg.tunnel.farfield_bc
    if ff == FarfieldBC.AUTO:
        ff = FarfieldBC.FREESTREAM if abs(op.yaw_deg) > 1e-9 else FarfieldBC.INLET_OUTLET_SLIP
    farfield = ff.value

    # turbulence
    it = cfg.physics.inlet_turbulence
    nu = cfg.physics.fluid.kinematic_viscosity
    turb = inlet_turbulence(
        op.speed,
        nu,
        it.intensity,
        it.viscosity_ratio if it.spec == InletTurbulenceSpec.VISCOSITY_RATIO else None,
        it.length_scale if it.spec == InletTurbulenceSpec.LENGTH_SCALE else None,
    )

    ref = cfg.reference
    if ref.area is None or ref.length is None or ref.moment_centre is None:
        raise AeroBenchError.make("reference.incomplete", ErrorCategory.CONFIGURATION,
                                  "Reference area, length and moment centre must be set before generating the case.")
    wb = cfg.vehicle.wheelbase
    axle_ref = cfg.vehicle.wheelbase_mid_ground(plan.ground_z)
    n_procs = max(1, cfg.execution.n_procs)
    return ResolvedCase(
        plan=plan,
        components=comps,
        tunnel_patches=tunnel,
        vehicle_patches=vehicle_patches,
        patch_roles=roles,
        wheel_bcs=wheel_bcs,
        u_inf=u_inf,
        u_mag=op.speed,
        yaw_deg=op.yaw_deg,
        ground_velocity=ground_v,
        farfield=farfield,
        turbulence=turb,
        ref_area=ref.area,
        ref_length=ref.length,
        ref_density=ref.density,
        ref_velocity=ref.velocity or op.speed,
        moment_centre=tuple(ref.moment_centre),
        axle_reference=axle_ref,
        wheelbase=wb,
        n_procs=n_procs,
        model=cfg.physics.turbulence_model.value,
        nu=nu,
        rho=cfg.physics.fluid.density,
        warnings=warnings,
        assumptions=assumptions,
    )


# =========================================================================== mesh case


def _control_dict_mesh(adapter: ForkAdapter, fmt: str) -> Dict:
    return Dict(
        application=adapter.application_name(),
        startFrom="startTime",
        startTime=0,
        stopAt="endTime",
        endTime=100,
        deltaT=1,
        writeControl="timeStep",
        writeInterval=100,
        purgeWrite=0,
        writeFormat=fmt,
        writePrecision=10,
        writeCompression=False,
        timeFormat="general",
        timePrecision=6,
        runTimeModifiable=False,
    )


def _block_mesh(plan: MeshPlan, tunnel_patches: list[str]) -> Dict:
    (x0, y0, z0), (x1, y1, z1) = plan.domain_min, plan.domain_max
    verts = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    d = Dict()
    d["scale"] = 1
    d["vertices"] = List([vec(v) for v in verts])
    nx, ny, nz = plan.n_cells_block
    d["blocks"] = List([Word("hex"), L(0, 1, 2, 3, 4, 5, 6, 7), L(nx, ny, nz), Word("simpleGrading"), L(1, 1, 1)])
    d["edges"] = List([])
    right_name = "symmetry" if "symmetry" in tunnel_patches else "side_right"
    faces = [
        ("inlet", "patch", [(0, 4, 7, 3)]),
        ("outlet", "patch", [(2, 6, 5, 1)]),
        ("ground", "wall", [(0, 3, 2, 1)]),
        ("top", "patch", [(4, 5, 6, 7)]),
        ("side_left", "patch", [(1, 5, 4, 0)]),
        (right_name, "symmetryPlane" if right_name == "symmetry" else "patch", [(3, 7, 6, 2)]),
    ]
    items = []
    for name, ptype, fl in faces:
        items.append(Word(name))
        items.append(Dict(type=ptype, faces=List([L(*f) for f in fl])))
    d["boundary"] = List(items)
    return d


def _shape_geometry(shape) -> Dict:
    if isinstance(shape, BoxShape):
        return Dict(type="box", min=vec(shape.min), max=vec(shape.max))
    if isinstance(shape, RotatedBoxShape):
        return Dict(type="rotatedBox", span=vec(shape.span), origin=vec(shape.origin), e1=vec(shape.e1), e3=vec(shape.e3))
    if isinstance(shape, CylinderShape):
        return Dict(type="cylinder", point1=vec(shape.point1), point2=vec(shape.point2), radius=clean(shape.radius))
    if isinstance(shape, SphereShape):
        return Dict(type="sphere", centre=vec(shape.centre), radius=clean(shape.radius))
    raise TypeError(type(shape))


def _shape_from_json(js: dict):
    kind = js["kind"]
    return {"box": BoxShape, "rotated_box": RotatedBoxShape, "cylinder": CylinderShape, "sphere": SphereShape}[kind](**js)


def _component_groups(c: GeometryComponent) -> list[str]:
    g = ["vehicle"]
    if c.role == ComponentRole.WHEEL:
        g.append("wheels")
    else:
        g.append("bodywork")
    return g


def _snappy(cfg: ProjectConfig, rc: ResolvedCase) -> Dict:
    plan = rc.plan
    m = cfg.mesh
    d = Dict()
    d["castellatedMesh"] = True
    d["snap"] = True
    do_layers = plan.layer_design is not None and any(s.n_layers > 0 for s in plan.surfaces)
    d["addLayers"] = bool(do_layers)

    geom = Dict()
    for c in rc.components:
        geom[f"{c.id}.stl"] = Dict(type="triSurfaceMesh", name=c.id)
    for r in plan.regions:
        geom[f"ref_{r.id}"] = _shape_geometry(_shape_from_json(r.shape))
    d["geometry"] = geom

    cast = Dict()
    cast["maxLocalCells"] = int(m.max_local_cells)
    cast["maxGlobalCells"] = int(m.max_global_cells)
    cast["minRefinementCells"] = 10
    cast["maxLoadUnbalance"] = 0.10
    cast["nCellsBetweenLevels"] = int(m.n_cells_between_levels)
    cast["features"] = List([Dict(file=Str(f"{s.component_id}.eMesh"), level=int(s.feature_level)) for s in plan.surfaces])
    rs = Dict()
    for s in plan.surfaces:
        comp = next(c for c in rc.components if c.id == s.component_id)
        rs[s.component_id] = Dict(
            level=L(int(s.min_level), int(s.max_level)),
            patchInfo=Dict(type="wall", inGroups=L(*_component_groups(comp))),
        )
    cast["refinementSurfaces"] = rs
    cast["resolveFeatureAngle"] = float(m.resolve_feature_angle)
    rr = Dict()
    for r in plan.regions:
        rr[f"ref_{r.id}"] = Dict(mode="inside", levels=List([L(1e15, int(r.level))]))
    if plan.distance_levels:
        for c in rc.components:
            if c.role == ComponentRole.STILT:
                continue
            rr[c.id] = Dict(mode="distance", levels=List([L(clean(dist), int(lv)) for dist, lv in plan.distance_levels]))
    cast["refinementRegions"] = rr
    cast["locationInMesh"] = vec(plan.location_in_mesh)
    cast["allowFreeStandingZoneFaces"] = True
    d["castellatedMeshControls"] = cast

    d["snapControls"] = Dict(
        nSmoothPatch=3,
        tolerance=float(m.snap_tolerance),
        nSolveIter=int(m.n_snap_solve_iter),
        nRelaxIter=5,
        nFeatureSnapIter=int(m.n_feature_snap_iter),
        implicitFeatureSnap=False,
        explicitFeatureSnap=True,
        multiRegionFeatureSnap=False,
    )

    lay = Dict()
    ld = plan.layer_design
    if ld is not None:
        lc = m.layers
        layers = Dict()
        for s in plan.surfaces:
            if s.n_layers > 0:
                layers[s.component_id] = Dict(nSurfaceLayers=int(s.n_layers))
        if lc.ground_layers and rc.ground_velocity is not None:
            layers["ground"] = Dict(nSurfaceLayers=int(max(1, ld.n_layers // 2)))
        if ld.mode == "absolute":
            lay["relativeSizes"] = False
            lay["thicknessModel"] = "firstAndExpansion"
            lay["firstLayerThickness"] = clean(ld.first_layer, 6)
            lay["expansionRatio"] = float(ld.expansion_ratio)
            lay["minThickness"] = clean(max(0.1 * ld.total_thickness, ld.first_layer * 0.5), 6)
        else:
            lay["relativeSizes"] = True
            lay["thicknessModel"] = "finalAndExpansion"
            lay["finalLayerThickness"] = float(ld.final_relative)
            lay["expansionRatio"] = float(ld.expansion_ratio)
            lay["minThickness"] = float(m.layers.min_thickness_relative)
        lay["layers"] = layers
        lay["nGrow"] = 0
        lay["featureAngle"] = float(lc.feature_angle)
        lay["slipFeatureAngle"] = 30.0
        lay["nRelaxIter"] = 5
        lay["nSmoothSurfaceNormals"] = 1
        lay["nSmoothNormals"] = 3
        lay["nSmoothThickness"] = 10
        lay["maxFaceThicknessRatio"] = 0.5
        lay["maxThicknessToMedialRatio"] = 0.3
        lay["minMedialAxisAngle"] = 90.0
        lay["nBufferCellsNoExtrude"] = 0
        lay["nLayerIter"] = int(lc.n_layer_iter)
        lay["nRelaxedIter"] = 20
    else:
        lay["relativeSizes"] = True
        lay["layers"] = Dict()
        lay["expansionRatio"] = 1.0
        lay["finalLayerThickness"] = 0.3
        lay["minThickness"] = 0.1
    d["addLayersControls"] = lay

    q = m.quality
    mq = Dict(
        maxNonOrtho=float(q.max_non_ortho),
        maxBoundarySkewness=float(q.max_boundary_skewness),
        maxInternalSkewness=float(q.max_internal_skewness),
        maxConcave=float(q.max_concave),
        minVol=float(q.min_vol),
        minTetQuality=float(q.min_tet_quality),
        minArea=-1,
        minTwist=float(q.min_twist),
        minDeterminant=float(q.min_determinant),
        minFaceWeight=float(q.min_face_weight),
        minVolRatio=float(q.min_vol_ratio),
        minTriangleTwist=-1,
        nSmoothScale=int(q.n_smooth_scale),
        errorReduction=float(q.error_reduction),
        relaxed=Dict(maxNonOrtho=float(q.relaxed_max_non_ortho)),
    )
    d["meshQualityControls"] = mq
    d["writeFlags"] = L("scalarLevels", "layerSets", "layerFields")
    d["mergeTolerance"] = 1e-6
    return d


def _feature_extract(cfg: ProjectConfig, rc: ResolvedCase) -> Dict:
    d = Dict()
    for c in rc.components:
        d[f"{c.id}.stl"] = Dict(
            extractionMethod="extractFromSurface",
            includedAngle=float(cfg.mesh.feature_included_angle),
            subsetFeatures=Dict(nonManifoldEdges=False, openEdges=True),
            writeObj=False,
        )
    return d


def _decompose(n: int, method: str) -> Dict:
    d = Dict(numberOfSubdomains=int(n), method=method)
    return d


def _minimal_schemes() -> Dict:
    return Dict(
        ddtSchemes=Dict(default="steadyState"),
        gradSchemes=Dict(default=T("Gauss", "linear")),
        divSchemes=Dict(default="none"),
        laplacianSchemes=Dict(default=T("Gauss", "linear", "corrected")),
        interpolationSchemes=Dict(default="linear"),
        snGradSchemes=Dict(default="corrected"),
        wallDist=Dict(method="meshWave"),
    )


def build_mesh_case(cfg: ProjectConfig, rc: ResolvedCase, adapter: ForkAdapter) -> GeneratedCase:
    gc = GeneratedCase(stage="mesh")
    fmt = cfg.execution.write_format
    gc.add("system/controlDict", _control_dict_mesh(adapter, fmt), location="system")
    gc.add("system/blockMeshDict", _block_mesh(rc.plan, rc.tunnel_patches), location="system")
    gc.add("system/surfaceFeatureExtractDict", _feature_extract(cfg, rc), location="system")
    snappy = _snappy(cfg, rc)
    gc.add("system/snappyHexMeshDict", snappy, location="system")
    gc.add("system/decomposeParDict", _decompose(rc.n_procs, cfg.execution.decomposition.value), location="system")
    gc.add("system/fvSchemes", _minimal_schemes(), location="system")
    gc.add("system/fvSolution", Dict(), location="system")
    gc.add("system/meshQualityDict", snappy["meshQualityControls"].copy(), location="system")
    gc.apply_expert(cfg.expert)
    gc.meta = {"generator": GENERATOR_VERSION, "adapter": adapter.describe(), "stage": "mesh"}
    return gc


# =========================================================================== solve case


def _patch_bcs(rc: ResolvedCase, model: str, cfg: ProjectConfig) -> dict[str, dict[str, Dict]]:
    """Boundary conditions for every field on every patch."""
    t = rc.turbulence
    fields = ["U", "p", "nut"] + turbulence_fields(model)
    wf = cfg.physics.wall_treatment == WallTreatment.WALL_FUNCTIONS
    out: dict[str, dict[str, Dict]] = {f: {} for f in fields}
    free = {"k": t.k, "omega": t.omega, "epsilon": t.epsilon, "nuTilda": t.nu_tilda}

    def far(field_name: str, kind: str) -> Dict:
        if rc.farfield == "freestream":
            if field_name == "U":
                return Dict(type="freestreamVelocity", freestreamValue=uniform(rc.u_inf))
            if field_name == "p":
                return Dict(type="freestreamPressure", freestreamValue=uniform(cfg.operating.pressure))
            if field_name == "nut":
                return Dict(type="calculated", value=uniform(0))
            return Dict(type="inletOutlet", inletValue=uniform(free[field_name]), value=uniform(free[field_name]))
        # classic inlet / outlet / slip
        if kind == "inlet":
            if field_name == "U":
                return Dict(type="fixedValue", value=uniform(rc.u_inf))
            if field_name == "p":
                return Dict(type="zeroGradient")
            if field_name == "nut":
                return Dict(type="calculated", value=uniform(0))
            return Dict(type="fixedValue", value=uniform(free[field_name]))
        if kind == "outlet":
            if field_name == "U":
                return Dict(type="inletOutlet", inletValue=uniform((0, 0, 0)), value=uniform(rc.u_inf))
            if field_name == "p":
                return Dict(type="fixedValue", value=uniform(cfg.operating.pressure))
            if field_name == "nut":
                return Dict(type="calculated", value=uniform(0))
            return Dict(type="inletOutlet", inletValue=uniform(free[field_name]), value=uniform(free[field_name]))
        # side / top
        return Dict(type="slip") if field_name != "nut" else Dict(type="calculated", value=uniform(0))

    def wall(field_name: str, velocity: Optional[Dict]) -> Dict:
        if field_name == "U":
            return velocity if velocity is not None else Dict(type="noSlip")
        if field_name == "p":
            return Dict(type="zeroGradient")
        if field_name == "nut":
            if model == "laminar":
                return Dict(type="calculated", value=uniform(0))
            return Dict(type="nutUSpaldingWallFunction", value=uniform(0)) if wf or model == "SpalartAllmaras" else Dict(
                type="nutLowReWallFunction", value=uniform(0))
        if field_name == "k":
            return Dict(type="kqRWallFunction", value=uniform(t.k)) if wf else Dict(type="fixedValue", value=uniform(1e-10))
        if field_name == "omega":
            return Dict(type="omegaWallFunction", value=uniform(t.omega))
        if field_name == "epsilon":
            return Dict(type="epsilonWallFunction", value=uniform(t.epsilon))
        if field_name == "nuTilda":
            return Dict(type="fixedValue", value=uniform(0))
        raise KeyError(field_name)

    for f in fields:
        for patch in rc.tunnel_patches:
            if patch in ("inlet", "outlet"):
                out[f][patch] = far(f, patch)
            elif patch in ("top", "side_left", "side_right"):
                out[f][patch] = far(f, "side")
            elif patch == "symmetry":
                out[f][patch] = Dict(type="symmetryPlane")
            elif patch == "ground":
                gv = None
                if rc.ground_velocity is not None:
                    gv = Dict(type="fixedValue", value=uniform(rc.ground_velocity))
                out[f][patch] = wall(f, gv)
        for c in rc.components:
            vel = None
            wb = rc.wheel_bcs.get(c.id)
            if wb is not None:
                vel = Dict(type="rotatingWallVelocity", origin=vec(wb.origin), axis=vec(wb.axis), omega=clean(wb.omega),
                           value=uniform((0, 0, 0)))
            out[f][c.id] = wall(f, vel)
    return out


FIELD_META = {
    "U": ("volVectorField", dims(0, 1, -1, 0, 0, 0, 0)),
    "p": ("volScalarField", dims(0, 2, -2, 0, 0, 0, 0)),
    "nut": ("volScalarField", dims(0, 2, -1, 0, 0, 0, 0)),
    "k": ("volScalarField", dims(0, 2, -2, 0, 0, 0, 0)),
    "omega": ("volScalarField", dims(0, 0, -1, 0, 0, 0, 0)),
    "epsilon": ("volScalarField", dims(0, 2, -3, 0, 0, 0, 0)),
    "nuTilda": ("volScalarField", dims(0, 2, -1, 0, 0, 0, 0)),
}


def _field_files(rc: ResolvedCase, cfg: ProjectConfig) -> dict[str, tuple[str, Dict]]:
    model = rc.model
    bcs = _patch_bcs(rc, model, cfg)
    t = rc.turbulence
    internal = {
        "U": uniform(rc.u_inf),
        "p": uniform(cfg.operating.pressure),
        "nut": uniform(0),
        "k": uniform(t.k),
        "omega": uniform(t.omega),
        "epsilon": uniform(t.epsilon),
        "nuTilda": uniform(t.nu_tilda),
    }
    out = {}
    for f, patches in bcs.items():
        cls, dm = FIELD_META[f]
        body = Dict()
        body["dimensions"] = dm
        body["internalField"] = internal[f]
        bf = Dict()
        for patch, bc in patches.items():
            bf[patch] = bc
        body["boundaryField"] = bf
        out[f"0/{f}"] = (cls, body)
    return out


def _function_objects(cfg: ProjectConfig, rc: ResolvedCase, adapter: ForkAdapter) -> Dict:
    fos = Dict()
    obs = cfg.observables
    rho = rc.rho
    centre = rc.moment_centre
    fos["forces_total"] = adapter.forces_fo(rc.vehicle_patches, rho, centre)
    if obs.component_forces and len(rc.components) > 1:
        for c in rc.components:
            if c.include_in_forces:
                fos[f"forces_{c.id}"] = adapter.forces_fo([c.id], rho, centre)
    # native coefficient cross-check: body axes, CofR at the axle reference if available
    cofr = rc.axle_reference or centre
    lref = rc.wheelbase or rc.ref_length
    fos["coeffs_total"] = adapter.force_coeffs_fo(rc.vehicle_patches, rho, cofr, (1, 0, 0), (0, 0, 1), rc.ref_velocity, lref, rc.ref_area)
    turb = turbulence_fields(rc.model)
    fos["solverInfo"] = adapter.solver_info_fo(["U", "p"] + turb)
    if obs.continuity:
        fos["continuityError"] = adapter.continuity_fo()
    if obs.yplus and rc.model != "laminar":
        fos["yPlus"] = adapter.yplus_fo()
    if obs.wall_shear_stress:
        fos["wallShearStress"] = adapter.wall_shear_fo(rc.vehicle_patches)
    if obs.pressure_coefficient:
        fos["Cp"] = adapter.pressure_coeff_fo(rc.u_inf)
    if obs.vorticity:
        fos["vorticity"] = adapter.vorticity_fo()
    if obs.q_criterion:
        fos["Q"] = adapter.q_fo()
    for pr in obs.probes:
        fos[f"probes_{pr.name}"] = adapter.probes_fo(pr.points, pr.fields)
    for name, text in cfg.expert.extra_function_objects.items():
        fos[name] = parse_extra_function_object(name, text)
    fos[STOP_FO_NAME] = adapter.abort_fo()
    return fos


def _control_dict_solve(cfg: ProjectConfig, rc: ResolvedCase, adapter: ForkAdapter, end_time: int) -> Dict:
    n = cfg.numerics
    wi = n.write_interval or min(500, end_time)
    return Dict(
        application=adapter.application_name(),
        startFrom="latestTime",
        startTime=0,
        stopAt="endTime",
        endTime=int(end_time),
        deltaT=1,
        writeControl="timeStep",
        writeInterval=int(wi),
        purgeWrite=int(cfg.execution.purge_write),
        writeFormat=cfg.execution.write_format,
        writePrecision=8,
        writeCompression=False,
        timeFormat="general",
        timePrecision=8,
        runTimeModifiable=True,
        functions=_function_objects(cfg, rc, adapter),
    )


def build_solve_case(cfg: ProjectConfig, rc: ResolvedCase, adapter: ForkAdapter) -> GeneratedCase:
    gc = GeneratedCase(stage="solve")
    n = cfg.numerics
    model = rc.model
    p = preset_def(n)
    n_first = n.first_order_iterations if n.first_order_iterations is not None else p.first_order_iterations
    n_first = min(n_first, max(0, n.iterations - 1))
    path, body = adapter.transport_file(rc.nu)
    gc.add(path, body, location="constant")
    path, body = adapter.turbulence_file(model)
    gc.add(path, body, location="constant")
    for rel, (cls, body) in _field_files(rc, cfg).items():
        gc.add(rel, body, cls=cls, location="0")
    gc.add("system/controlDict", _control_dict_solve(cfg, rc, adapter, n.iterations), location="system")
    gc.add("system/fvSchemes", fv_schemes(n, model), location="system")
    gc.add("system/fvSolution", fv_solution(n, model), location="system")
    gc.add("system/decomposeParDict", _decompose(rc.n_procs, cfg.execution.decomposition.value), location="system")
    if n_first > 0:
        gc.add("system/fvSchemes.firstOrder", fv_schemes(n, model, first_order=True), location="system", object_name="fvSchemes")
    gc.apply_expert(cfg.expert)
    gc.meta = {
        "generator": GENERATOR_VERSION,
        "adapter": adapter.describe(),
        "stage": "solve",
        "first_order_iterations": n_first,
        "iterations": n.iterations,
        "numerics_preset": {"preset": n.preset.value, "version": n.preset_version, "description": p.description},
    }
    return gc


FORBIDDEN_DYNAMIC = ("#codeStream", "#calc", "coded", "codedFixedValue", "codedMixed", "#{")


def assert_no_dynamic_code(gc: GeneratedCase) -> None:
    for path, text in gc.render_all().items():
        for tok in FORBIDDEN_DYNAMIC:
            if tok in text:
                raise AeroBenchError.make(
                    "case.dynamic_code",
                    ErrorCategory.CONFIGURATION,
                    f"Generated file {path} contains dynamic code ('{tok}'), which the native Windows build cannot compile.",
                    action="Remove the expert override that introduced it, or use the WSL provider.",
                )
