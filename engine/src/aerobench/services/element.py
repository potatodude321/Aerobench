"""Aero element studied alone (application 'aero_element'): its pose over the ground.

The element keeps the vehicle pose machinery: the angle is the rake (rotation about the span
axis at the trailing edge, + = trailing edge up) and the ride-height offset lifts it so that
its lowest point sits at the requested clearance — in ground effect the user's value, in free
stream several chords, where the moving ground (no boundary layer) has no measurable effect.
"""

from __future__ import annotations

from typing import Any, Optional

from ..core.errors import AeroBenchError, ErrorCategory
from ..domain.project import ProjectConfig
from ..domain.setup import ElementEnvironment, GroundMode
from ..geometry.pose import posed_components
from ..project.store import Project

APPLICATION = "aero_element"


def is_element(cfg: ProjectConfig) -> bool:
    return cfg.application == APPLICATION


def _apply(project: Project, c: ProjectConfig) -> dict[str, Any]:
    e = c.element
    c.vehicle.rake_deg = float(e.angle_deg)
    c.operating.ground = GroundMode.MOVING
    c.operating.wheels_rotate = False
    if not c.geometry.revision_id:
        return {}
    cfd = project.cfd_surface(cfg=c)
    lo, hi = cfd.bbox()
    chord = float(hi[0] - lo[0])
    target = e.ground_clearance if e.environment == ElementEnvironment.GROUND_EFFECT else e.free_stream_chords * chord
    flat = c.vehicle.model_copy(update={"ride_height_offset": 0.0})
    parts, z0, _ = posed_components(cfd, c.geometry.components, flat, c.tunnel.contact_patch_fraction)
    zmin = min(float(s.bbox()[0][2]) for s in parts.values() if s.n_faces)
    c.vehicle.ride_height_offset = round(target - (zmin - z0), 6)
    return {"chord": chord, "clearance": target, "ride_height_offset": c.vehicle.ride_height_offset}


def apply_element_setup(project: Project, environment: Optional[str] = None, ground_clearance: Optional[float] = None,
                        angle_deg: Optional[float] = None, free_stream_chords: Optional[float] = None) -> dict[str, Any]:
    """Store the element setup and derive the pose from it."""
    if not is_element(project.config):
        raise AeroBenchError.make("element.not_element", ErrorCategory.CONFIGURATION,
                                  "This project studies a whole vehicle, not an aero element on its own.")
    upd = {k: v for k, v in {"environment": environment, "ground_clearance": ground_clearance, "angle_deg": angle_deg,
                             "free_stream_chords": free_stream_chords}.items() if v is not None}
    out: dict[str, Any] = {}

    def mutate(c: ProjectConfig) -> None:
        c.element = c.element.model_validate({**c.element.model_dump(), **upd})
        out.update(_apply(project, c))

    project.update(mutate)
    return {"element": project.config.element.model_dump(mode="json"), **out}


def refresh_element_pose(project: Project) -> None:
    """After a geometry change: keep the clearance and angle the user asked for."""
    if is_element(project.config) and project.config.geometry.revision_id:
        project.update(lambda c: _apply(project, c))
