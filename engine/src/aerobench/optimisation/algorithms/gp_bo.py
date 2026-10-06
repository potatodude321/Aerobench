"""Constrained Bayesian optimisation with Gaussian processes.

* Initial design: Latin hypercube (``init_samples``, default ``max(5, 2d+1)``).
* Surrogates: one GP for the objective, one per constraint, and — once any evaluation has
  failed — one for a failure indicator (+1 failed / -1 succeeded).
* Acquisition: expected improvement over the best *feasible* value, multiplied by the
  probability that every constraint holds and the probability that the evaluation
  succeeds.  Before any feasible point exists the probability of feasibility alone is
  maximised.
* Batches and asynchronous evaluation: points that are asked but not yet told are added as
  fantasies at the surrogate mean (Kriging believer), so concurrent proposals spread out.
* The acquisition is maximised over a dense random candidate set plus local L-BFGS-B
  refinement of the best candidates, then snapped to the discrete grid of the space.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from ..gp import GP
from .base import Observation, Optimizer, lhs, total_violation


class GPBO(Optimizer):
    name = "gp_bo"
    multi_objective = False

    def _setup(self) -> None:
        n0 = self.init_samples or max(5, 2 * self.dim + 1)
        n0 = int(min(n0, self.budget))
        self.init_design = lhs(self.rng, n0, self.dim).tolist() if n0 > 0 else []
        self.init_index = 0
        self.theta: dict[str, Any] = {}

    def _extra_state(self) -> dict[str, Any]:
        return {"init_design": self.init_design, "init_index": self.init_index, "theta": self.theta}

    def _load_extra(self, d: dict[str, Any]) -> None:
        self.init_design = d["init_design"]
        self.init_index = d["init_index"]
        self.theta = d.get("theta") or {}

    # ------------------------------------------------------------------ proposal
    def _propose(self, n: int) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        while len(out) < n and self.init_index < len(self.init_design):
            out.append(np.asarray(self.init_design[self.init_index]))
            self.init_index += 1
        if len(out) == n:
            return out
        ok = [o for o in self.observations if not o.failed]
        if len(ok) < 2:
            # not enough information for a surrogate: space-filling fallback
            while len(out) < n:
                out.append(self.rng.random(self.dim))
            return out
        models = self._fit_models(ok)
        pend = [np.asarray(x) for x in self.pending.values()] + out
        for _ in range(n - len(out)):
            x = self._maximise(models, pend)
            out.append(x)
            pend.append(x)
        return out

    def _fit_models(self, ok: list[Observation]) -> dict[str, Any]:
        X = np.array([o.x for o in ok])
        y = np.array([o.f[0] for o in ok])  # type: ignore[index]
        m: dict[str, Any] = {"f": GP(X, y, self.rng, self._theta("f"))}
        self.theta["f"] = m["f"].theta.tolist()
        m["g"] = []
        for j in range(self.n_con):
            gj = np.array([o.g[j] for o in ok])  # type: ignore[index]
            gp = GP(X, gj, self.rng, self._theta(f"g{j}"))
            self.theta[f"g{j}"] = gp.theta.tolist()
            m["g"].append(gp)
        if any(o.failed for o in self.observations):
            Xa = np.array([o.x for o in self.observations])
            lab = np.array([1.0 if o.failed else -1.0 for o in self.observations])
            gp = GP(Xa, lab, self.rng, self._theta("fail"))
            self.theta["fail"] = gp.theta.tolist()
            m["fail"] = gp
        feas = [o.f[0] for o in ok if total_violation(o.g) <= 0]  # type: ignore[index]
        m["best"] = min(feas) if feas else None
        return m

    def _theta(self, key: str) -> Optional[np.ndarray]:
        t = self.theta.get(key)
        return np.asarray(t) if t is not None and len(t) == self.dim + 2 else None

    def _acq(self, models: dict[str, Any], X: np.ndarray) -> np.ndarray:
        X = np.atleast_2d(X)
        pof = np.ones(len(X))
        for gp in models["g"]:
            mu, var = gp.predict(X)
            pof *= norm.cdf(-mu / np.sqrt(var + 1e-12))
        if "fail" in models:
            mu, var = models["fail"].predict(X)
            pof *= norm.cdf(-mu / np.sqrt(var + 1e-12))
        best = models["best"]
        if best is None:
            return pof
        mu, var = models["f"].predict(X)
        sd = np.sqrt(var)
        z = (best - mu) / np.maximum(sd, 1e-12)
        ei = (best - mu) * norm.cdf(z) + sd * norm.pdf(z)
        ei = np.where(sd > 1e-12, ei, np.maximum(best - mu, 0.0))
        return ei * pof

    def _maximise(self, models: dict[str, Any], pending: list[np.ndarray]) -> np.ndarray:
        if pending:
            P = np.array(pending)
            fm = dict(models)
            fm["f"] = models["f"].with_fantasies(P, models["f"].predict(P)[0])
            fm["g"] = [gp.with_fantasies(P, gp.predict(P)[0]) for gp in models["g"]]
            if "fail" in models:
                fm["fail"] = models["fail"].with_fantasies(P, models["fail"].predict(P)[0])
            models = fm
        n_cand = int(self.options.get("n_candidates", min(4000, 400 * self.dim)))
        C = self.rng.random((n_cand, self.dim))
        # local candidates around the incumbents
        ok = [o for o in self.observations if not o.failed]
        if ok:
            incumbents = sorted(ok, key=lambda o: (total_violation(o.g), o.f[0]))[:5]  # type: ignore[index]
            loc = np.vstack([np.asarray(o.x) + 0.05 * self.rng.standard_normal((n_cand // 10, self.dim)) for o in incumbents])
            C = np.vstack([C, np.clip(loc, 0, 1)])
        C = np.array([self.project(c) for c in C])
        a = self._acq(models, C)
        top = np.argsort(-a)[:5]
        best_x, best_a = C[top[0]], a[top[0]]
        for i in top:
            r = minimize(lambda x: -float(self._acq(models, x)[0]), C[i], method="L-BFGS-B", bounds=[(0.0, 1.0)] * self.dim,
                         options={"maxiter": 50})
            xp = self.project(r.x)
            ap = float(self._acq(models, xp)[0])
            if ap > best_a:
                best_x, best_a = xp, ap
        # never propose an exact duplicate of an evaluated or pending point
        seen = [np.asarray(o.x) for o in self.observations] + list(pending)
        if seen and min(np.abs(best_x - s).max() for s in seen) < 1e-9:
            order = np.argsort(-a)
            for i in order:
                c = C[i]
                if min(np.abs(c - s).max() for s in seen) >= 1e-9:
                    return c
            return self.project(self.rng.random(self.dim))
        return best_x
