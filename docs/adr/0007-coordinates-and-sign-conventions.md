# ADR-0007 — Coordinate systems, force/moment sign conventions, aero balance

Status: Accepted (2026-10-06)

## CFD (tunnel) frame — what OpenFOAM sees

* `+x` = freestream direction at zero yaw (nose → tail). The vehicle nose points `−x`.
* `+z` = up, ground plane `z = 0` (after ride-height/contact treatment).
* `+y = z × x` → points to the **vehicle's right**.

Imported geometry is mapped into this frame by a persisted `GeometryTransform`
(unit scale, axis permutation/flip, translation) derived from the user's choice of the
geometry's forward/up axes. The transform is explicit and shown in the UI triad.

## Reported quantities (body axes by default)

All signs are fixed and labelled in the UI:

| Quantity | Definition (CFD frame) | Positive when |
|---|---|---|
| Drag D | `F·x̂` | force acts rearward |
| Lift L | `F·ẑ` | force acts upward |
| Downforce | `−L` | force acts downward |
| Side force S | `F·ŷ` | force acts to vehicle right |
| Pitching moment PM | `M·ŷ` | nose up |
| Yawing moment YM | `−M·ẑ` | nose to the right |
| Rolling moment RM | `−M·x̂` | right side down |

`F` = pressure + viscous force from OpenFOAM `forces`; `M` is re-referenced in the engine
from the function-object centre of rotation to the persisted moment reference point
using `M_P = M_O + (O − P) × F`.

Coefficients: `C = F/(q A_ref)`, `C_M = M/(q A_ref L_ref)`, `q = ½ ρ_ref U_ref²`,
`U_ref` = resultant freestream speed. Wind axes (drag along the freestream vector) are
available as an alternative and are always labelled.

## Axle loads and aero balance

Moment reference for axle loads: ground-level point midway between the axles on the
vehicle centreline. With wheelbase `WB` and PM (nose-up) about that point:

`L_front = L/2 + PM/WB`, `L_rear = L/2 − PM/WB` (equivalently `Cl_f = Cl/2 + Cm`,
`Cl_r = Cl/2 − Cm` with `L_ref = WB`).

Aero balance (front, %) = `DF_front / (DF_front + DF_rear) × 100`, `DF = −L`.
The balance is flagged *ill-conditioned* when total downforce ≤ 0 or when
`|DF_total| < 0.05 (|DF_front| + |DF_rear|)`. An ill-conditioned balance is reported as
*missing* (with the reason) everywhere a value would be compared, sorted, charted or
optimised — results, comparisons, studies, reports and CSV — because a ratio over a
near-zero or negative total (e.g. "231 % front") carries no engineering meaning. Front and
rear downforce remain available.

## Yaw

Yaw `β > 0` means the relative wind approaches from the vehicle's **right**:
flow direction `d = (cos β, −sin β, 0)`. See ADR-0008.

## Rules

* Reference area, length, density, velocity, moment point and axes are persisted with
  every run snapshot. After results exist they are never silently re-inferred; changing
  them recomputes coefficients from stored raw forces and records the change.
