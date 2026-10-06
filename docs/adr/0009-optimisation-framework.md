# ADR-0009 — Design-exploration framework (studies, DOE, optimisation)

Status: Accepted (2026-10-06)

## Decision

* A **design** is a point in a typed design space (continuous, integer, discrete/
  categorical variables with units and bounds). Evaluating a design means: derive the
  project configuration (operating point, geometry parameters, mesh/solver settings) →
  geometry revision (if geometry parameters changed) → mesh (reused when the mesh input
  hash matches) → solve → metrics.
* Studies (one-factor, full factorial, Latin hypercube, CSV/table, custom list) and
  optimisers share the same evaluator, scheduler and run database.
* Optimisers implement an **ask/tell** protocol with a JSON-serialisable state, so a
  study can be checkpointed after every tell and resumed after a crash:
  * `space_filling` (LHS / Sobol / random) — baseline;
  * `gp_bo` — Gaussian-process Bayesian optimisation (Matérn-5/2, expected improvement,
    probability-of-feasibility weighting for constraints), own implementation on
    numpy/scipy;
  * `nsga2` — constrained multi-objective evolutionary optimiser, own implementation;
  * `nelder_mead` — derivative-free local refinement (ask/tell wrapper);
  * Optional adapters (e.g. Optuna, if installed) behind the same protocol.
* **Failures are data.** A meshing/solver/validity failure produces an evaluation with
  `status=failed` and a classified reason. Optimisers receive it via `tell_failure`;
  GP-BO models infeasibility, NSGA-II treats it as maximally infeasible. The loop never
  crashes because a design failed. Retries are bounded and recorded.
* Constraints are `g(x) ≤ 0` after normalisation; objectives are minimised internally
  (maximisation is negated, always displayed in natural sense).
* Scheduler allocates explicit core budgets: `concurrency × cores_per_run ≤ budget`;
  oversubscription is refused.
* Adjoint shape optimisation is out of scope until its full chain is validated.
