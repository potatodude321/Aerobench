# ADR-0008 — Yaw, moving ground and wheel rotation modelling

Status: Accepted (2026-10-06)

## Decision

* **Yaw by rotating the inflow, not the vehicle.** The tunnel stays aligned with the
  vehicle; the freestream vector is `U∞ (cos β, −sin β, 0)`. All far-field faces
  (inlet, outlet, sides, top) use `freestreamVelocity`/`freestreamPressure` when
  `β ≠ 0` (they switch between inflow and outflow locally). At `β = 0` the classic
  set (fixed-velocity inlet, fixed-pressure outlet with `inletOutlet` U, slip sides/top)
  is used. Wake refinement boxes become `rotatedBox` aligned with the flow.
  Consequence: a yaw sweep can reuse one mesh when the domain/wake boxes are sized for the
  sweep's maximum yaw (the study planner does this automatically).
* **Ground** modes: `moving` (default for automotive), `stationary` (noSlip).
  Moving-ground velocity under yaw has two explicit interpretations:
  * `road` (default): ground moves at the vehicle speed along the vehicle axis,
    `U_g = U∞ cos β x̂` (pure crosswind on a road).
  * `tunnel_belt`: belt along the flow direction at `U∞` (turntable wind tunnel).
* **Wheels**: `rotatingWallVelocity` with `origin` = wheel centre, `axis = −ŷ`
  (so the contact patch moves with the ground), `ω = |U_g| / R_rolling`. OpenFOAM's
  definition (`Up = ω (â × r)`, wall-normal component removed) was verified in the
  v2606 source. This is the **rotating-wall approximation**; it is labelled as such.
  MRF and sliding-mesh (AMI) are separate, explicitly-labelled advanced options and are
  never presented as equivalent.
* **Tyre/ground contact**: line contacts are meshing poison. Default treatment raises the
  tunnel floor into the tyres by `δ = 1 %` of the rolling radius (configurable), creating a
  finite contact patch of length `2√(2Rδ)`. The effective ride-height change (`−δ`) is
  displayed and recorded.
* **Symmetry**: half-car models use a `symmetryPlane` at `y = 0`; any `β ≠ 0` with
  half-car symmetry is a validation **error**.
