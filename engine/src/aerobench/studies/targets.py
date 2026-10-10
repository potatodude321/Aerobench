"""Configuration targets that design variables and operating conditions may set.

Every target has a documented meaning, units and the pipeline stage it invalidates, so a
study can tell which designs share meshes and which need new geometry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional, Union

from ..domain.project import ProjectConfig

Value = Union[float, str]


@dataclass(frozen=True)
class TargetInfo:
    target: str
    label: str
    units: str
    stage: str  # geometry | mesh | solve
    kind: str = "continuous"  # continuous | integer | discrete
    description: str = ""


STATIC_TARGETS: dict[str, TargetInfo] = {t.target: t for t in [
    TargetInfo("operating.speed", "Free-stream speed", "m/s", "solve", description="U∞; the reference velocity follows unless set explicitly."),
    TargetInfo("operating.yaw_deg", "Yaw angle", "°", "solve",
               description="β > 0: wind from the vehicle's right. Studies size one mesh for the whole yaw range."),
    TargetInfo("vehicle.ride_height_offset", "Ride-height offset", "m", "geometry",
               description="Vertical offset of the body (wheels stay on the ground)."),
    TargetInfo("vehicle.rake_deg", "Rake", "°", "geometry", description="Pitch about the rear-axle ground contact; + = nose down."),
    TargetInfo("vehicle.front_ride_height", "Front ride-height change", "m", "geometry",
               description="Change of the body height at the front axle relative to the baseline pose."),
    TargetInfo("vehicle.rear_ride_height", "Rear ride-height change", "m", "geometry",
               description="Change of the body height at the rear axle relative to the baseline pose."),
    TargetInfo("element.angle_deg", "Element angle", "°", "geometry",
               description="Aero element studied alone: rotation about its span, + = trailing edge up; its clearance is kept."),
    TargetInfo("element.ground_clearance", "Element height over the ground", "m", "geometry",
               description="Aero element in ground effect: height of its lowest point above the ground."),
    TargetInfo("mesh.base_cell_size", "Background cell size", "m", "mesh", description="Level-0 cell edge length (mesh sensitivity)."),
    TargetInfo("numerics.iterations", "Iterations", "", "solve", kind="integer"),
    TargetInfo("geometry.variant", "Geometry variant", "", "geometry", kind="discrete",
               description="Alternative geometry revision (e.g. imported mirror or spoiler options)."),
]}


def target_info(target: str, cfg: Optional[ProjectConfig] = None) -> TargetInfo:
    if target in STATIC_TARGETS:
        return STATIC_TARGETS[target]
    if target.startswith("param."):
        pid = target[6:]
        p = next((x for x in (cfg.parameters if cfg else []) if x.id == pid), None)
        if cfg is not None and p is None:
            raise KeyError(f"unknown geometry parameter '{pid}'")
        return TargetInfo(target, p.name if p else pid, p.units if p else "", "geometry",
                          description="Geometry parameter (rigid move or morph of selected components).")
    raise KeyError(f"unknown study target '{target}'")


ELEMENT_TARGETS = ("element.angle_deg", "element.ground_clearance")


def list_targets(cfg: ProjectConfig) -> list[dict[str, Any]]:
    element = cfg.application == "aero_element"
    # an element's pose is its angle and height; the vehicle pose targets apply to vehicles
    hidden = ("vehicle.front_ride_height", "vehicle.rear_ride_height", "vehicle.rake_deg", "vehicle.ride_height_offset") if element else ELEMENT_TARGETS
    out = [t.__dict__ | {"baseline": baseline_value(cfg, t.target)} for t in STATIC_TARGETS.values() if t.target not in hidden]
    for p in cfg.parameters:
        t = target_info(f"param.{p.id}", cfg)
        out.append(t.__dict__ | {"baseline": p.baseline, "lower": p.lower, "upper": p.upper})
    return out


def baseline_value(cfg: ProjectConfig, target: str) -> Optional[Value]:
    if target == "operating.speed":
        return cfg.operating.speed
    if target == "operating.yaw_deg":
        return cfg.operating.yaw_deg
    if target == "vehicle.ride_height_offset":
        return cfg.vehicle.ride_height_offset
    if target == "vehicle.rake_deg":
        return cfg.vehicle.rake_deg
    if target in ("vehicle.front_ride_height", "vehicle.rear_ride_height"):
        return 0.0
    if target == "element.angle_deg":
        return cfg.element.angle_deg
    if target == "element.ground_clearance":
        return cfg.element.ground_clearance
    if target == "mesh.base_cell_size":
        return cfg.mesh.base_cell_size
    if target == "numerics.iterations":
        return cfg.numerics.iterations
    if target == "geometry.variant":
        return cfg.geometry.revision_id
    if target.startswith("param."):
        p = next((x for x in cfg.parameters if x.id == target[6:]), None)
        return None if p is None else cfg.parameter_values.get(p.id, p.baseline)
    raise KeyError(target)


def apply_targets(cfg: ProjectConfig, assignments: dict[str, Value]) -> dict[str, Any]:
    """Apply ``target -> value`` to ``cfg`` in place.  Returns side information
    (geometry-parameter values and variant revision) for the geometry stage."""
    side: dict[str, Any] = {"param_values": {}, "variant": None, "pose_changed": False, "element_pose": False}
    dfront = assignments.get("vehicle.front_ride_height")
    drear = assignments.get("vehicle.rear_ride_height")
    for t, v in assignments.items():
        if t == "operating.speed":
            cfg.operating.speed = float(v)
        elif t == "operating.yaw_deg":
            cfg.operating.yaw_deg = float(v)
        elif t == "vehicle.ride_height_offset":
            cfg.vehicle.ride_height_offset = float(v)
            side["pose_changed"] = True
        elif t == "vehicle.rake_deg":
            cfg.vehicle.rake_deg = float(v)
            side["pose_changed"] = True
        elif t in ("vehicle.front_ride_height", "vehicle.rear_ride_height"):
            pass  # handled jointly below
        elif t in ELEMENT_TARGETS:
            if cfg.application != "aero_element":
                raise ValueError(f"{t} applies to aero element projects only")
            if t == "element.angle_deg":
                cfg.element.angle_deg = float(v)
            else:
                if float(v) <= 0:
                    raise ValueError("the element's height over the ground must be positive")
                cfg.element.ground_clearance = float(v)
            side["element_pose"] = True
            side["pose_changed"] = True
        elif t == "mesh.base_cell_size":
            cfg.mesh.base_cell_size = float(v)
        elif t == "numerics.iterations":
            cfg.numerics.iterations = int(round(float(v)))
        elif t == "geometry.variant":
            side["variant"] = str(v)
        elif t.startswith("param."):
            side["param_values"][t[6:]] = float(v)
        else:
            raise KeyError(f"unknown study target '{t}'")
    if dfront is not None or drear is not None:
        wb = cfg.vehicle.wheelbase
        if not wb:
            raise ValueError("front/rear ride-height variables need both axles (wheels) to be defined")
        # baseline pose expressed as front/rear changes: rear = offset, front = offset - WB sin(rake)
        r0 = cfg.vehicle.ride_height_offset
        f0 = r0 - wb * math.sin(math.radians(cfg.vehicle.rake_deg))
        fr = f0 + float(dfront or 0.0)
        rr = r0 + float(drear or 0.0)
        s = (rr - fr) / wb
        if abs(s) >= 1:
            raise ValueError("front/rear ride-height difference exceeds the wheelbase")
        cfg.vehicle.ride_height_offset = rr
        cfg.vehicle.rake_deg = math.degrees(math.asin(s))
        side["pose_changed"] = True
    return side
