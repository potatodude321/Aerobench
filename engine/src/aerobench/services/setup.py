"""Setup helpers: derived flow quantities shown next to inputs, presets, config patches."""

from __future__ import annotations

import math
from typing import Any

from ..aero.flow import dynamic_pressure, first_cell_height, inlet_turbulence, reynolds
from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.mesh import MeshPreset
from ..domain.numerics import NumericsPreset
from ..domain.project import ProjectConfig, validate_project
from ..domain.setup import DomainPreset, GroundMode, InletTurbulenceSpec, YawGroundMode
from ..domain.templates import apply_template, deep_merge
from ..foam.case.numerics import PRESETS
from ..project.store import Project


def derived_quantities(cfg: ProjectConfig) -> dict[str, Any]:
    op = cfg.operating
    fl = cfg.physics.fluid
    s = cfg.geometry.summary
    L = (s.bbox_max[0] - s.bbox_min[0]) if s else 4.5
    out: dict[str, Any] = {
        "reynolds_length": reynolds(op.speed, L, fl.kinematic_viscosity),
        "reynolds_ref": reynolds(op.speed, cfg.reference.length or L, fl.kinematic_viscosity),
        "dynamic_pressure_pa": dynamic_pressure(cfg.reference.density, cfg.reference.velocity or op.speed),
        "vehicle_length": L,
        "mach": op.speed / 343.0,
    }
    if out["mach"] > 0.3:
        out["mach_warning"] = "Mach > 0.3: incompressible results lose accuracy."
    it = cfg.physics.inlet_turbulence
    t = inlet_turbulence(op.speed, fl.kinematic_viscosity, it.intensity,
                         it.viscosity_ratio if it.spec == InletTurbulenceSpec.VISCOSITY_RATIO else None,
                         it.length_scale if it.spec == InletTurbulenceSpec.LENGTH_SCALE else None)
    out["turbulence"] = t.as_dict()
    ws = first_cell_height(cfg.mesh.layers.target_yplus, op.speed, L, fl.kinematic_viscosity, fl.density, cfg.mesh.layers.correlation)
    out["wall_spacing"] = ws.as_dict()
    b = math.radians(op.yaw_deg)
    out["freestream_vector"] = [op.speed * math.cos(b), -op.speed * math.sin(b), 0.0]
    if op.ground == GroundMode.MOVING:
        gv = op.speed * math.cos(b) if op.yaw_ground_mode == YawGroundMode.ROAD else op.speed
        out["ground_speed"] = gv
        out["wheels"] = [{"position": w.position.value, "radius": w.radius, "omega_rad_s": gv / w.radius,
                          "rpm": gv / w.radius * 60 / (2 * math.pi)} for w in cfg.vehicle.wheels]
    out["validation"] = [i.model_dump(mode="json") for i in validate_project(cfg)]
    return out


def patch_config(project: Project, patch: dict[str, Any]) -> ProjectConfig:
    """Deep-merge a partial JSON patch into the configuration (validated as a whole)."""
    forbidden = {"project_id", "schema_version", "created_at"}
    if forbidden & set(patch):
        raise AeroBenchError.make("config.readonly", ErrorCategory.CONFIGURATION, "Read-only fields cannot be changed.")
    ref_change = "reference" in patch and project.config.reference.locked and any(
        k in patch["reference"] for k in ("area", "length", "moment_centre", "density", "velocity", "axes"))
    if ref_change and not patch.get("reference", {}).get("confirm_after_results"):
        raise AeroBenchError.make(
            "reference.locked", ErrorCategory.CONFIGURATION,
            "Reference values are locked because results exist.",
            likely_cause="Changing reference area/length/moment centre changes every reported coefficient.",
            action="Confirm the change explicitly; existing runs keep their stored reference values and can be recomputed.")
    if "reference" in patch:
        patch = {**patch, "reference": {k: v for k, v in patch["reference"].items() if k != "confirm_after_results"}}
    data = deep_merge(project.config.to_json(), patch)
    new = ProjectConfig.model_validate(data)
    project.config = new
    project.save()
    return new


def apply_preset(project: Project, kind: str, value: str) -> ProjectConfig:
    cfg = project.config.model_copy(deep=True)
    if kind == "template":
        cfg = apply_template(cfg, value)
    elif kind == "tunnel":
        cfg.tunnel = cfg.tunnel.apply_preset(DomainPreset(value))
    elif kind == "mesh":
        cfg.mesh.preset = MeshPreset(value)
        cfg.mesh.base_cell_size = None
        cfg.mesh.surface = []
    elif kind == "numerics":
        cfg.numerics.preset = NumericsPreset(value)
    else:
        raise AeroBenchError.make("config.preset_kind", ErrorCategory.CONFIGURATION, f"Unknown preset kind '{kind}'.")
    project.config = ProjectConfig.model_validate(cfg.to_json())
    project.save()
    return project.config


def numerics_presets() -> dict[str, Any]:
    out = {}
    for ver, table in PRESETS.items():
        out[ver] = {k.value: v.__dict__ for k, v in table.items()}
    return out
