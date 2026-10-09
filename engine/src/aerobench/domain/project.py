"""Project configuration root, validation and stage hashing (staleness)."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from ..core.errors import Severity, ValidationIssue
from ..core.fs import sha256_json
from ..core.ids import new_uuid, utc_now_iso
from ..version import APP_VERSION, GENERATOR_VERSION
from .common import Model
from .execution import ExecutionConfig, SolverTarget
from .expert import ExpertConfig
from .exploration import GeometryParameter, OptimisationDefinition, StudyDefinition
from .geometry import ComponentRole, GeometryConfig
from .mesh import MeshConfig
from .numerics import NumericsConfig
from .observables import ObservablesConfig
from .setup import (
    OperatingCondition,
    PhysicsConfig,
    ReferenceValues,
    Symmetry,
    TunnelConfig,
    TurbulenceModel,
    WallTreatment,
)
from .vehicle import VehicleCategory, VehicleConfig

SCHEMA_VERSION = 1


class ProjectConfig(Model):
    schema_version: int = SCHEMA_VERSION
    project_id: str = Field(default_factory=new_uuid)
    name: str = "Untitled project"
    created_at: str = Field(default_factory=utc_now_iso)
    modified_at: str = Field(default_factory=utc_now_iso)
    app_version: str = APP_VERSION
    application: str = "automotive_external_aero"
    template_id: Optional[str] = None
    solver: SolverTarget = Field(default_factory=SolverTarget)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    geometry: GeometryConfig = Field(default_factory=GeometryConfig)
    vehicle: VehicleConfig = Field(default_factory=VehicleConfig)
    reference: ReferenceValues = Field(default_factory=ReferenceValues)
    operating: OperatingCondition = Field(default_factory=OperatingCondition)
    tunnel: TunnelConfig = Field(default_factory=TunnelConfig)
    mesh: MeshConfig = Field(default_factory=MeshConfig)
    physics: PhysicsConfig = Field(default_factory=PhysicsConfig)
    numerics: NumericsConfig = Field(default_factory=NumericsConfig)
    observables: ObservablesConfig = Field(default_factory=ObservablesConfig)
    expert: ExpertConfig = Field(default_factory=ExpertConfig)
    parameters: list[GeometryParameter] = Field(default_factory=list)
    parameter_values: dict[str, float] = Field(default_factory=dict, description="Current values of geometry parameters")
    studies: list[StudyDefinition] = Field(default_factory=list)
    optimisations: list[OptimisationDefinition] = Field(default_factory=list)
    notes: str = ""
    tags: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------ helpers
    def touch(self) -> None:
        self.modified_at = utc_now_iso()
        self.app_version = APP_VERSION

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ProjectConfig":
        from .migrations import migrate

        return cls.model_validate(migrate(data))


# ---------------------------------------------------------------------------- stage hashes


HASH_SCHEME = 2


def _dump(model: Any) -> Any:
    if isinstance(model, Model):
        return model.model_dump(mode="json")
    return model


def _canon(model: Any) -> Any:
    """Settings as they affect a result. Fields left at their default are omitted, so a new
    release that adds a setting (with a default that keeps the old behaviour) does not make
    every existing mesh and run look out of date."""
    if isinstance(model, Model):
        return model.model_dump(mode="json", exclude_defaults=True)
    return model


# Which fields of a part and of a wheel shape the *mesh*; the rest only affect the solve.
_COMPONENT_MESH_FIELDS = ("id", "source_regions", "role", "wheel_position")
_WHEEL_MESH_FIELDS = ("position", "component_id", "centre", "axis", "radius", "width")


def _pick(model: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    d = model.model_dump(mode="json")
    return {k: d[k] for k in fields if k in d}


def geometry_stage_input(cfg: ProjectConfig) -> dict[str, Any]:
    out = {
        "revision": cfg.geometry.revision_id,
        "transform": _canon(cfg.geometry.transform),
        # names, "suggested" flags and force reporting do not change the surface or the mesh
        "components": [_pick(c, _COMPONENT_MESH_FIELDS) for c in cfg.geometry.components],
        "vehicle_pose": {"ride_height_offset": cfg.vehicle.ride_height_offset, "rake_deg": cfg.vehicle.rake_deg},
    }
    # Geometry parameters are applied by creating a new geometry revision, so the revision id
    # already captures their effect; definitions alone (e.g. a parameter just added) must not
    # invalidate existing meshes.
    if cfg.geometry.sizing_box is not None:
        out["sizing_box"] = _canon(cfg.geometry.sizing_box)
    return out


def effective_yaw_range(cfg: ProjectConfig) -> tuple[float, float]:
    if cfg.tunnel.design_yaw_range_deg is not None:
        lo, hi = cfg.tunnel.design_yaw_range_deg
        lo, hi = min(lo, hi, cfg.operating.yaw_deg), max(lo, hi, cfg.operating.yaw_deg)
        return (float(lo), float(hi))
    return (cfg.operating.yaw_deg, cfg.operating.yaw_deg)


def mesh_stage_input(cfg: ProjectConfig) -> dict[str, Any]:
    tunnel = _canon(cfg.tunnel)
    tunnel.pop("farfield_bc", None)  # a boundary condition, not geometry
    return {
        "scheme": HASH_SCHEME,
        "generator": GENERATOR_VERSION,
        "geometry": geometry_stage_input(cfg),
        "wheels": [_pick(w, _WHEEL_MESH_FIELDS) for w in cfg.vehicle.wheels],
        "tunnel": tunnel,
        "yaw_range": effective_yaw_range(cfg),
        "mesh": _canon(cfg.mesh),
        "solver": _canon(cfg.solver),
        # layer design depends on the flow speed / viscosity through y+
        "layer_flow": (
            {"speed": cfg.operating.speed, "nu": cfg.physics.fluid.kinematic_viscosity}
            if cfg.mesh.layers.mode.value == "target_yplus"
            else None
        ),
    }


def solve_stage_input(cfg: ProjectConfig) -> dict[str, Any]:
    return {
        "mesh": mesh_stage_input(cfg),
        "operating": _canon(cfg.operating),
        "tunnel_bc": cfg.tunnel.farfield_bc.value,
        "wheel_rotation": [(w.position.value, w.rotation.value, w.custom_omega) for w in cfg.vehicle.wheels],
        "forces_from": [c.id for c in cfg.geometry.components if c.include_in_forces],
        "physics": _canon(cfg.physics),
        "numerics": _canon(cfg.numerics),
        "observables": _canon(cfg.observables),
        "expert": _canon(cfg.expert),
        "execution": {"write_format": cfg.execution.write_format},
    }


def derived_stage_input(cfg: ProjectConfig) -> dict[str, Any]:
    ref = _canon(cfg.reference)
    ref.pop("locked", None)
    return {"solve": solve_stage_input(cfg), "reference": ref}


def stage_hashes(cfg: ProjectConfig) -> dict[str, str]:
    return {
        "geometry": sha256_json(geometry_stage_input(cfg)),
        "mesh": sha256_json(mesh_stage_input(cfg)),
        "solve": sha256_json(solve_stage_input(cfg)),
        "derived": sha256_json(derived_stage_input(cfg)),
    }


# --- scheme 1 (until 0.1.0 build 2): kept only to verify stored hashes before re-keying them
def _v1_geometry(cfg: ProjectConfig) -> dict[str, Any]:
    out = {
        "revision": cfg.geometry.revision_id,
        "transform": _dump(cfg.geometry.transform),
        "components": [_dump(c) for c in cfg.geometry.components],
        "vehicle_pose": {"ride_height_offset": cfg.vehicle.ride_height_offset, "rake_deg": cfg.vehicle.rake_deg},
    }
    if cfg.geometry.sizing_box is not None:
        out["sizing_box"] = _dump(cfg.geometry.sizing_box)
    return out


def v1_mesh_stage_input(cfg: ProjectConfig) -> dict[str, Any]:
    return {
        "generator": GENERATOR_VERSION,
        "geometry": _v1_geometry(cfg),
        "wheels": [_dump(w) for w in cfg.vehicle.wheels],
        "tunnel": {k: v for k, v in _dump(cfg.tunnel).items() if k != "farfield_bc"},
        "yaw_range": effective_yaw_range(cfg),
        "mesh": _dump(cfg.mesh),
        "solver": _dump(cfg.solver),
        "layer_flow": (
            {"speed": cfg.operating.speed, "nu": cfg.physics.fluid.kinematic_viscosity}
            if cfg.mesh.layers.mode.value == "target_yplus"
            else None
        ),
    }


def v1_solve_stage_input(cfg: ProjectConfig) -> dict[str, Any]:
    return {
        "mesh": v1_mesh_stage_input(cfg),
        "operating": _dump(cfg.operating),
        "tunnel_bc": cfg.tunnel.farfield_bc.value,
        "physics": _dump(cfg.physics),
        "numerics": _dump(cfg.numerics),
        "observables": _dump(cfg.observables),
        "expert": _dump(cfg.expert),
        "execution": {"write_format": cfg.execution.write_format},
    }


# ---------------------------------------------------------------------------- validation


def validate_project(cfg: ProjectConfig, for_stage: str = "solve") -> list[ValidationIssue]:
    """Engineering validation.  Errors block generation; warnings are displayed."""
    issues: list[ValidationIssue] = []

    def err(path: str, msg: str, code: str = "") -> None:
        issues.append(ValidationIssue(path=path, severity=Severity.ERROR, message=msg, code=code))

    def warn(path: str, msg: str, code: str = "") -> None:
        issues.append(ValidationIssue(path=path, severity=Severity.WARNING, message=msg, code=code))

    if cfg.geometry.revision_id is None:
        err("geometry", "No geometry has been imported.", "geometry.missing")
    if not cfg.geometry.components:
        err("geometry.components", "No geometry components are defined.", "geometry.no_components")
    if cfg.geometry.summary is None:
        err("geometry.summary", "Geometry has not been measured (re-import or refresh).", "geometry.unmeasured")

    # symmetry vs yaw (BUILD_SPEC §6.3)
    if cfg.tunnel.symmetry == Symmetry.HALF:
        lo, hi = effective_yaw_range(cfg)
        if abs(lo) > 1e-9 or abs(hi) > 1e-9:
            err("tunnel.symmetry", "Half-car symmetry cannot be combined with non-zero yaw.", "tunnel.symmetry_yaw")
        warn(
            "tunnel.symmetry",
            "Half-car symmetry: the plane is placed at the vehicle centreline; side force, yaw and roll "
            "moments are not available and forces are doubled for reporting.",
            "tunnel.half_car",
        )

    # wheels
    wheel_components = cfg.geometry.by_role(ComponentRole.WHEEL)
    if cfg.vehicle.category != VehicleCategory.BLUFF_BODY:
        if not cfg.vehicle.wheels:
            warn("vehicle.wheels", "No wheels are defined: axle loads, aero balance and wheel rotation are unavailable.", "vehicle.no_wheels")
        elif len(cfg.vehicle.wheels) not in (2, 4):
            warn("vehicle.wheels", f"{len(cfg.vehicle.wheels)} wheels defined; axle loads assume front/rear axles.")
        for w in cfg.vehicle.wheels:
            if w.component_id is None and cfg.operating.wheels_rotate:
                warn(f"vehicle.wheels.{w.position.value}", "Wheel has no surface component; it cannot rotate.", "vehicle.wheel_no_surface")
            elif w.component_id is not None:
                try:
                    cfg.geometry.component(w.component_id)
                except KeyError:
                    err(f"vehicle.wheels.{w.position.value}", f"Unknown component '{w.component_id}'.")
        if wheel_components and not cfg.vehicle.wheels:
            warn("vehicle.wheels", "Wheel components exist but no wheel centres/radii are defined.")
        wb = cfg.vehicle.wheelbase
        if wb is not None and wb <= 0:
            err("vehicle.wheels", "Front wheels must be ahead of rear wheels (nose points to -x).", "vehicle.axle_order")

    # reference values
    ref = cfg.reference
    if ref.area is None:
        err("reference.area", "Reference area is not set (compute frontal area or enter a value).", "reference.area")
    if ref.length is None:
        err("reference.length", "Reference length is not set.", "reference.length")
    if ref.moment_centre is None:
        err("reference.moment_centre", "Moment reference point is not set.", "reference.moment_centre")

    # physics
    if cfg.physics.turbulence_model == TurbulenceModel.GEKO:
        warn("physics.turbulence_model", "GEKO is new in OpenFOAM v2606 and is treated as an advanced option.")
    if cfg.physics.turbulence_model == TurbulenceModel.LAMINAR and cfg.operating.speed > 1:
        warn("physics.turbulence_model", "Laminar flow at automotive Reynolds numbers is not physical.")
    if cfg.physics.wall_treatment == WallTreatment.LOW_RE and cfg.mesh.layers.target_yplus > 5:
        warn("mesh.layers.target_yplus", "Low-Re wall treatment needs y+ ≈ 1; target y+ is high.")
    if cfg.physics.wall_treatment == WallTreatment.WALL_FUNCTIONS and cfg.mesh.layers.target_yplus < 15:
        warn("mesh.layers.target_yplus", "Wall functions are intended for y+ ≳ 30; target is in the buffer layer.")

    # tunnel
    if cfg.tunnel.upstream < 1.5:
        warn("tunnel.upstream", "Upstream extent below 1.5 vehicle lengths may affect stagnation pressure.")
    if cfg.tunnel.downstream < 3:
        warn("tunnel.downstream", "Downstream extent below 3 vehicle lengths may truncate the wake.")

    # execution
    if cfg.execution.n_procs > 1 and not cfg.mesh.parallel:
        warn("mesh.parallel", "Meshing will run serially although multiple cores are requested.")

    return issues
