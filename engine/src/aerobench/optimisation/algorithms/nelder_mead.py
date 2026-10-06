"""Nelder–Mead simplex search (adaptive coefficients, Gao & Han 2012) in ask/tell form.

A derivative-free *local* refinement method, typically started from the baseline or from
the best design of a previous study.  Constraints are handled with an exact L1 penalty on
the normalised violations; failed evaluations are worse than any finite value.  Trial
points are clipped to the bounds and snapped to the design grid.  The search stops when
the simplex has shrunk below ``xtol`` (unit-cube units) and the function spread is below
``ftol``, or when the budget is used.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import numpy as np

from .base import Observation, Optimizer, total_violation

# A failed evaluation is worse than any real value; kept finite so the state stays valid JSON.
FAILED_VALUE = 1e300


class NelderMead(Optimizer):
    name = "nelder_mead"
    multi_objective = False

    def _setup(self) -> None:
        n = self.dim
        self.alpha = 1.0
        self.gamma = 1.0 + 2.0 / n
        self.rho = 0.75 - 1.0 / (2 * n)
        self.sigma = 1.0 - 1.0 / n if n > 1 else 0.5
        self.penalty = float(self.options.get("penalty", 1e3))
        self.xtol = float(self.options.get("xtol", 1e-3))
        self.ftol = float(self.options.get("ftol", 1e-6))
        step = float(self.options.get("step", 0.1))
        x0 = np.clip(np.asarray(self.x0 if self.x0 is not None else [0.5] * n, dtype=float), 0, 1)
        verts = [x0]
        for i in range(n):
            v = x0.copy()
            v[i] = v[i] + step if v[i] + step <= 1.0 else v[i] - step
            verts.append(v)
        self.phase = "init"
        self.queue = [v.tolist() for v in verts]
        self.waiting: list[str] = []
        self.simplex: list[list[Any]] = []  # [x, fpen]
        self.trial: dict[str, Any] = {}
        self.converged = False

    def _extra_state(self) -> dict[str, Any]:
        keys = ("alpha", "gamma", "rho", "sigma", "penalty", "xtol", "ftol", "phase", "queue", "waiting", "simplex", "trial", "converged")
        return {k: getattr(self, k) for k in keys}

    def _load_extra(self, d: dict[str, Any]) -> None:
        for k, v in d.items():
            setattr(self, k, v)

    # ------------------------------------------------------------------ helpers
    def fpen(self, o: Observation) -> float:
        if o.failed or o.f is None:
            return FAILED_VALUE
        return float(o.f[0]) + self.penalty * total_violation(o.g)

    def remaining(self) -> int:
        return 0 if self.converged else super().remaining()

    def _lookup(self, x: np.ndarray) -> Optional[Observation]:
        for o in self.observations:
            if np.abs(np.asarray(o.x) - x).max() < 1e-12:
                return o
        return None

    def ask(self, n: int = 1):  # type: ignore[override]
        out = super().ask(n)
        self.waiting.extend(t for t, _ in out)
        return out

    def forget_pending(self, token: str) -> None:
        # the simplex needs every vertex: a point that will never be evaluated counts as failed
        if token in self.pending:
            self.tell(token, failed=True)

    def _propose(self, n: int) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for _ in range(10_000):
            if len(out) >= n or self.converged:
                break
            if not self.queue:
                if out or self.waiting:
                    break
                self._advance()
                if not self.queue:
                    break
                continue
            px = self.project(np.asarray(self.queue[0]))
            self.queue.pop(0)
            known = self._lookup(px)
            if known is None and self.phase in ("init", "shrink"):
                # the same point is already being evaluated (e.g. the baseline): wait for it
                same = next((t for t, x in self.pending.items() if t not in self.waiting and np.abs(np.asarray(x) - px).max() < 1e-12), None)
                if same is not None:
                    self.waiting.append(same)
                    continue
            if known is not None:
                # already evaluated (baseline, clipped/snapped duplicate): reuse, no budget spent
                if self.phase in ("init", "shrink"):
                    self.simplex.append([px.tolist(), self.fpen(known)])
                else:
                    self.trial["f"] = self.fpen(known)
                    self.trial["x"] = px.tolist()
                continue
            out.append(px)
        return out

    def _on_tell(self, obs: Observation) -> None:
        if obs.token not in self.waiting:
            return
        self.waiting.remove(obs.token)
        if self.phase in ("init", "shrink"):
            self.simplex.append([obs.x, self.fpen(obs)])
        else:
            self.trial["f"] = self.fpen(obs)
            self.trial["x"] = obs.x
        if not self.waiting and not self.queue:
            self._advance()

    # ------------------------------------------------------------------ state machine
    def _sorted(self):
        self.simplex.sort(key=lambda v: v[1])
        return [np.asarray(v[0]) for v in self.simplex], [v[1] for v in self.simplex]

    def _check_converged(self, X, F) -> bool:
        diam = max(np.abs(x - X[0]).max() for x in X[1:]) if len(X) > 1 else 0.0
        finite = [f for f in F if f < FAILED_VALUE]
        spread = (max(finite) - min(finite)) if len(finite) == len(F) else math.inf
        return diam < self.xtol and spread < self.ftol

    def _advance(self) -> None:
        if self.converged:
            return
        n = self.dim
        if self.phase in ("init", "shrink"):
            if len(self.simplex) < n + 1:
                return
            self.phase = "iterate"
        X, F = self._sorted()
        if self.phase == "iterate":
            if self._check_converged(X, F):
                self.converged = True
                return
            c = np.mean(X[:-1], axis=0)
            xr = c + self.alpha * (c - X[-1])
            self.trial = {"stage": "reflect", "c": c.tolist(), "xr": None, "fr": None}
            self.phase = "reflect"
            self.queue = [np.clip(xr, 0, 1).tolist()]
            return
        stage, fx = self.phase, self.trial.get("f", FAILED_VALUE)
        c = np.asarray(self.trial["c"])
        fb, fsw, fw = F[0], F[-2], F[-1]
        if stage == "reflect":
            xr = np.asarray(self.trial["x"])
            if fb <= fx < fsw:
                self._replace_worst(xr, fx)
            elif fx < fb:
                self.trial.update({"xr": xr.tolist(), "fr": fx})
                self.phase = "expand"
                self.queue = [np.clip(c + self.gamma * (xr - c), 0, 1).tolist()]
            elif fx < fw:
                self.trial.update({"xr": xr.tolist(), "fr": fx})
                self.phase = "contract_out"
                self.queue = [np.clip(c + self.rho * (xr - c), 0, 1).tolist()]
            else:
                self.trial.update({"xr": xr.tolist(), "fr": fx})
                self.phase = "contract_in"
                self.queue = [np.clip(c - self.rho * (c - X[-1]), 0, 1).tolist()]
            return
        if stage == "expand":
            xe = np.asarray(self.trial["x"])
            if fx < self.trial["fr"]:
                self._replace_worst(xe, fx)
            else:
                self._replace_worst(np.asarray(self.trial["xr"]), self.trial["fr"])
            return
        if stage == "contract_out":
            if fx <= self.trial["fr"]:
                self._replace_worst(np.asarray(self.trial["x"]), fx)
            else:
                self._shrink(X)
            return
        if stage == "contract_in":
            if fx < fw:
                self._replace_worst(np.asarray(self.trial["x"]), fx)
            else:
                self._shrink(X)
            return

    def _replace_worst(self, x: np.ndarray, f: float) -> None:
        self.simplex[-1] = [list(map(float, x)), float(f)]
        self.phase = "iterate"
        self._advance()

    def _shrink(self, X: list[np.ndarray]) -> None:
        best = X[0]
        keep = self.simplex[0]
        self.simplex = [keep]
        self.phase = "shrink"
        self.queue = [np.clip(best + self.sigma * (x - best), 0, 1).tolist() for x in X[1:]]
