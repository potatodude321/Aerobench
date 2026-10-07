"""Force/moment reduction to automotive quantities (ADR-0007).

Inputs are raw OpenFOAM ``forces`` outputs (N, N·m for incompressible runs once
multiplied by ρ_ref) in the CFD frame about a known centre.  Everything else —
coefficients, axle loads, aero balance — is computed here with explicit, persisted
reference values and sign conventions.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Optional

import numpy as np

X = np.array([1.0, 0.0, 0.0])
Y = np.array([0.0, 1.0, 0.0])
Z = np.array([0.0, 0.0, 1.0])


def transfer_moment(moment_o, force, origin_o, point_p) -> np.ndarray:
    """Moment about P given moment about O: ``M_P = M_O + (O − P) × F``."""
    return np.asarray(moment_o, float) + np.cross(np.asarray(origin_o, float) - np.asarray(point_p, float), np.asarray(force, float))


def freestream_direction(yaw_deg: float) -> np.ndarray:
    """Unit flow direction for yaw β (β > 0: wind from the vehicle's right)."""
    b = math.radians(yaw_deg)
    return np.array([math.cos(b), -math.sin(b), 0.0])


@dataclass
class ReferenceSpec:
    area: float
    length: float
    density: float
    velocity: float
    moment_centre: tuple[float, float, float]
    axes: str = "body"  # "body" | "wind"
    yaw_deg: float = 0.0
    wheelbase: Optional[float] = None
    axle_reference: Optional[tuple[float, float, float]] = None  # ground point midway between axles
    half_model: bool = False

    @property
    def q(self) -> float:
        return 0.5 * self.density * self.velocity**2


@dataclass
class AeroResult:
    """Automotive force quantities with explicit signs (see ADR-0007)."""

    drag: float
    lift: float
    side: float
    pitch: float  # nose-up +
    yaw: float  # nose-right +
    roll: float  # right-side-down +
    cd: float
    cl: float
    cs: float
    cm_pitch: float
    cm_yaw: float
    cm_roll: float
    downforce: float
    cda: float
    cla: float
    lift_front: Optional[float] = None
    lift_rear: Optional[float] = None
    cl_front: Optional[float] = None
    cl_rear: Optional[float] = None
    downforce_front: Optional[float] = None
    downforce_rear: Optional[float] = None
    balance_front_pct: Optional[float] = None
    balance_ill_conditioned: bool = False
    efficiency: Optional[float] = None  # downforce / drag
    axes: str = "body"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _axes(axes: str, yaw_deg: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (drag_dir, side_dir(right), up) unit vectors in the CFD frame."""
    if axes == "wind":
        d = freestream_direction(yaw_deg)
        right = np.cross(Z, d)  # z × d: for d = +x gives +y (vehicle right)
        return d, right, Z
    return X, Y, Z


def reduce_forces(force, moment, moment_origin, ref: ReferenceSpec) -> AeroResult:
    """Reduce total force/moment (CFD frame) to automotive quantities.

    ``force``/``moment`` must already be dimensional (N, N·m).  ``moment`` is about
    ``moment_origin``.  For half models forces/moments are doubled (symmetric
    quantities only); side force, yaw and roll moments are reported as 0 with a note.
    """
    F = np.asarray(force, float)
    M0 = np.asarray(moment, float)
    notes: list[str] = []
    if ref.area <= 0 or ref.length <= 0 or ref.velocity <= 0 or ref.density <= 0:
        raise ValueError("reference values must be positive")

    M = transfer_moment(M0, F, moment_origin, ref.moment_centre)
    dd, right, up = _axes(ref.axes, ref.yaw_deg)

    drag = float(F @ dd)
    lift = float(F @ up)
    side = float(F @ right)
    pitch = float(M @ right)  # positive rotation about +right lifts the nose (ADR-0007)
    yaw_m = float(-(M @ up))
    roll = float(-(M @ np.cross(right, up)))  # cross(right, up) = rearward axis; roll about forward = -that

    if ref.half_model:
        drag, lift, pitch = 2 * drag, 2 * lift, 2 * pitch
        side = yaw_m = roll = 0.0
        notes.append("half-model: drag/lift/pitch doubled; side force, yaw and roll not available")

    q = ref.q
    qa = q * ref.area
    res = AeroResult(
        drag=drag,
        lift=lift,
        side=side,
        pitch=pitch,
        yaw=yaw_m,
        roll=roll,
        cd=drag / qa,
        cl=lift / qa,
        cs=side / qa,
        cm_pitch=pitch / (qa * ref.length),
        cm_yaw=yaw_m / (qa * ref.length),
        cm_roll=roll / (qa * ref.length),
        downforce=-lift,
        cda=drag / q,
        cla=lift / q,
        axes=ref.axes,
        notes=notes,
    )
    if abs(drag) > 0:
        res.efficiency = -lift / drag

    if ref.wheelbase and ref.axle_reference is not None:
        # pitching moment about the ground point midway between axles (independent of the
        # reporting moment centre)
        # Axle loads are physical body-axis quantities whatever the reporting axes.
        M_ax = transfer_moment(M0, F, moment_origin, ref.axle_reference)
        pm = float(M_ax @ Y)
        if ref.half_model:
            pm *= 2
        lf = lift / 2.0 + pm / ref.wheelbase
        lr = lift / 2.0 - pm / ref.wheelbase
        res.lift_front, res.lift_rear = lf, lr
        res.cl_front, res.cl_rear = lf / qa, lr / qa
        res.downforce_front, res.downforce_rear = -lf, -lr
        df_total = -(lf + lr)
        denom = abs(lf) + abs(lr)
        if df_total <= 0 or (denom > 0 and abs(df_total) < 0.05 * denom):
            res.balance_ill_conditioned = True
            notes.append("aero balance ill-conditioned: total downforce is ≤ 0 or small relative to axle loads")
        if abs(df_total) > 0:
            res.balance_front_pct = 100.0 * (-lf) / df_total
    return res


def metrics_dict(res: AeroResult) -> dict[str, Optional[float]]:
    """Flatten to the metric names used by studies/optimisation."""
    return {
        "Cd": res.cd,
        "Cl": res.cl,
        "Cs": res.cs,
        "Cm_pitch": res.cm_pitch,
        "Cm_yaw": res.cm_yaw,
        "Cm_roll": res.cm_roll,
        "Cl_front": res.cl_front,
        "Cl_rear": res.cl_rear,
        "drag_N": res.drag,
        "lift_N": res.lift,
        "downforce_N": res.downforce,
        "downforce_front_N": res.downforce_front,
        "downforce_rear_N": res.downforce_rear,
        "side_N": res.side,
        "pitch_Nm": res.pitch,
        "yaw_Nm": res.yaw,
        "roll_Nm": res.roll,
        "CdA": res.cda,
        "ClA": res.cla,
        # An ill-conditioned balance (net lift, or downforce small against the axle loads)
        # is not a number anyone should compare, sort or optimise: it is reported as missing
        # (the AeroResult keeps the raw ratio and the flag for diagnostics).
        "balance_front_pct": None if res.balance_ill_conditioned else res.balance_front_pct,
        "efficiency": res.efficiency,
    }


METRIC_INFO: dict[str, dict[str, str]] = {
    "Cd": {"label": "Drag coefficient", "units": "-", "sign": "+ rearward"},
    "Cl": {"label": "Lift coefficient", "units": "-", "sign": "+ up"},
    "Cs": {"label": "Side-force coefficient", "units": "-", "sign": "+ to vehicle right"},
    "Cm_pitch": {"label": "Pitching-moment coefficient", "units": "-", "sign": "+ nose up"},
    "Cm_yaw": {"label": "Yawing-moment coefficient", "units": "-", "sign": "+ nose right"},
    "Cm_roll": {"label": "Rolling-moment coefficient", "units": "-", "sign": "+ right side down"},
    "Cl_front": {"label": "Front-axle lift coefficient", "units": "-", "sign": "+ up"},
    "Cl_rear": {"label": "Rear-axle lift coefficient", "units": "-", "sign": "+ up"},
    "drag_N": {"label": "Drag", "units": "N", "sign": "+ rearward"},
    "lift_N": {"label": "Lift", "units": "N", "sign": "+ up"},
    "downforce_N": {"label": "Downforce", "units": "N", "sign": "+ down"},
    "downforce_front_N": {"label": "Front downforce", "units": "N", "sign": "+ down"},
    "downforce_rear_N": {"label": "Rear downforce", "units": "N", "sign": "+ down"},
    "side_N": {"label": "Side force", "units": "N", "sign": "+ to vehicle right"},
    "pitch_Nm": {"label": "Pitching moment", "units": "N·m", "sign": "+ nose up"},
    "yaw_Nm": {"label": "Yawing moment", "units": "N·m", "sign": "+ nose right"},
    "roll_Nm": {"label": "Rolling moment", "units": "N·m", "sign": "+ right side down"},
    "CdA": {"label": "Drag area", "units": "m²", "sign": ""},
    "ClA": {"label": "Lift area", "units": "m²", "sign": "+ up"},
    "balance_front_pct": {"label": "Aero balance (front)", "units": "%",
                          "sign": "front DF / total DF; undefined (missing) when the vehicle makes net lift or near-zero downforce"},
    "efficiency": {"label": "Downforce / drag", "units": "-", "sign": ""},
}
