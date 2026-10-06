"""Flow calculations: air properties, Reynolds number, y+ / first-cell height, inlet turbulence.

Every function documents its correlation and validity range so the UI can show the
assumptions (BUILD_SPEC §8.3).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

R_AIR = 287.058  # J/(kg K), specific gas constant of dry air
SUTHERLAND_MU0 = 1.716e-5  # Pa s at T0
SUTHERLAND_T0 = 273.15  # K
SUTHERLAND_S = 110.4  # K
C_MU = 0.09


def air_properties(temperature_c: float = 20.0, pressure_pa: float = 101325.0) -> dict[str, float]:
    """Dry-air density (ideal gas) and viscosity (Sutherland's law, valid ~100–1900 K)."""
    t = temperature_c + 273.15
    if t <= 0:
        raise ValueError("temperature below absolute zero")
    rho = pressure_pa / (R_AIR * t)
    mu = SUTHERLAND_MU0 * (t / SUTHERLAND_T0) ** 1.5 * (SUTHERLAND_T0 + SUTHERLAND_S) / (t + SUTHERLAND_S)
    return {"density": rho, "dynamic_viscosity": mu, "kinematic_viscosity": mu / rho, "temperature_k": t}


def reynolds(speed: float, length: float, nu: float) -> float:
    if nu <= 0:
        raise ValueError("viscosity must be positive")
    return speed * length / nu


def dynamic_pressure(rho: float, speed: float) -> float:
    return 0.5 * rho * speed * speed


def skin_friction(re_x: float, correlation: str = "schlichting_1979") -> float:
    """Turbulent flat-plate local skin-friction coefficient.

    * ``schlichting_1979``: ``Cf = (2 log10 Re_x − 0.65)^−2.3``, valid Re_x < 1e9.
    * ``power_law_1_7``: ``Cf = 0.0592 Re_x^−1/5``, valid 5e5 < Re_x < 1e7.
    """
    if re_x <= 1.0:
        raise ValueError("Reynolds number too small for a turbulent correlation")
    if correlation == "schlichting_1979":
        return (2.0 * math.log10(re_x) - 0.65) ** -2.3
    if correlation == "power_law_1_7":
        return 0.0592 * re_x ** -0.2
    raise ValueError(f"unknown correlation {correlation!r}")


@dataclass(frozen=True)
class WallSpacing:
    target_yplus: float
    reynolds: float
    cf: float
    wall_shear_stress: float  # Pa
    friction_velocity: float  # m/s
    first_cell_centre_height: float  # m (y at which y+ = target)
    first_cell_height: float  # m (cell thickness = 2 * centre height)
    correlation: str
    assumptions: str

    def as_dict(self) -> dict:
        return asdict(self)


def first_cell_height(
    target_yplus: float,
    speed: float,
    length: float,
    nu: float,
    rho: float = 1.204,
    correlation: str = "schlichting_1979",
) -> WallSpacing:
    """Estimate the wall-adjacent cell thickness for a target y+.

    Uses the flat-plate skin friction at ``Re_L`` (conservative w.r.t. most of the body,
    which sees higher local Cf near the front).  The first cell *centre* sits at ``y``,
    so the cell thickness is ``2y``.  Real y+ varies over a vehicle by a factor of 3–10;
    the estimate targets the body-averaged value.
    """
    if target_yplus <= 0 or speed <= 0 or length <= 0:
        raise ValueError("target y+, speed and length must be positive")
    re = reynolds(speed, length, nu)
    cf = skin_friction(re, correlation)
    tau = 0.5 * cf * rho * speed * speed
    u_tau = math.sqrt(tau / rho)
    y = target_yplus * nu / u_tau
    return WallSpacing(
        target_yplus=target_yplus,
        reynolds=re,
        cf=cf,
        wall_shear_stress=tau,
        friction_velocity=u_tau,
        first_cell_centre_height=y,
        first_cell_height=2.0 * y,
        correlation=correlation,
        assumptions=(
            f"Turbulent flat plate, {correlation} correlation evaluated at Re_L={re:.3g} "
            f"(L={length:.3g} m, U={speed:.3g} m/s, nu={nu:.3g} m²/s); cell thickness = 2 × centroid height."
        ),
    )


def yplus_from_height(height: float, speed: float, length: float, nu: float, correlation: str = "schlichting_1979") -> float:
    """Inverse of :func:`first_cell_height` for a cell of thickness ``height``."""
    re = reynolds(speed, length, nu)
    cf = skin_friction(re, correlation)
    u_tau = speed * math.sqrt(0.5 * cf)
    return (0.5 * height) * u_tau / nu


@dataclass(frozen=True)
class InletTurbulenceValues:
    k: float
    omega: float
    epsilon: float
    nut: float
    nu_tilda: float
    intensity: float
    length_scale: float
    viscosity_ratio: float

    def as_dict(self) -> dict:
        return asdict(self)


def inlet_turbulence(
    speed: float,
    nu: float,
    intensity: float = 0.01,
    viscosity_ratio: float | None = 10.0,
    length_scale: float | None = None,
) -> InletTurbulenceValues:
    """Freestream turbulence quantities.

    ``k = 1.5 (I U)²``.  Either the eddy-viscosity ratio ``νt/ν`` (default, robust for
    external aero) or a turbulent length scale ``ℓ`` (``ω = √k / (Cμ^¼ ℓ)``) closes ω.
    ``ε = Cμ k ω``; ``ν̃`` for Spalart–Allmaras is set to ``3–5 ν`` scaled by the ratio
    (we use ``ν̃ = max(3ν, νt)``).
    """
    if speed <= 0 or nu <= 0 or intensity <= 0:
        raise ValueError("speed, viscosity and intensity must be positive")
    k = 1.5 * (intensity * speed) ** 2
    if length_scale is not None:
        omega = math.sqrt(k) / (C_MU ** 0.25 * length_scale)
        nut = k / omega
        ratio = nut / nu
    else:
        if viscosity_ratio is None or viscosity_ratio <= 0:
            raise ValueError("viscosity_ratio must be positive")
        nut = viscosity_ratio * nu
        omega = k / nut
        ratio = viscosity_ratio
        length_scale = math.sqrt(k) / (C_MU ** 0.25 * omega)
    eps = C_MU * k * omega
    return InletTurbulenceValues(
        k=k,
        omega=omega,
        epsilon=eps,
        nut=nut,
        nu_tilda=max(3.0 * nu, nut),
        intensity=intensity,
        length_scale=length_scale,
        viscosity_ratio=ratio,
    )
