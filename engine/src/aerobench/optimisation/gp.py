"""Gaussian-process regression (Matérn-5/2, ARD) on the unit hypercube.

Small, dependency-light implementation sized for design optimisation (tens to a few
hundred evaluations).  Targets are standardised; hyperparameters (length-scales, signal
variance, noise) maximise the log marginal likelihood with multi-start L-BFGS-B.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy.linalg import cho_factor, cho_solve
from scipy.optimize import minimize

SQRT5 = math.sqrt(5.0)


def matern52(X1: np.ndarray, X2: np.ndarray, ls: np.ndarray, s2: float) -> np.ndarray:
    d = (X1[:, None, :] - X2[None, :, :]) / ls
    r = np.sqrt(np.maximum((d * d).sum(-1), 0.0))
    return s2 * (1.0 + SQRT5 * r + 5.0 / 3.0 * r * r) * np.exp(-SQRT5 * r)


class GP:
    LS_BOUNDS = (math.log(0.02), math.log(20.0))
    S2_BOUNDS = (math.log(0.05), math.log(20.0))
    NOISE_BOUNDS = (math.log(1e-6), math.log(0.2))

    def __init__(self, X: np.ndarray, y: np.ndarray, rng: Optional[np.random.Generator] = None,
                 theta: Optional[np.ndarray] = None, fit: bool = True, restarts: int = 3):
        self.X = np.asarray(X, dtype=np.float64).reshape(len(y), -1)
        y = np.asarray(y, dtype=np.float64).reshape(-1)
        self.mu = float(y.mean()) if len(y) else 0.0
        sd = float(y.std()) if len(y) > 1 else 1.0
        self.sd = sd if sd > 1e-12 else 1.0
        self.y = (y - self.mu) / self.sd
        self.d = self.X.shape[1]
        self.rng = rng or np.random.default_rng(0)
        if theta is None:
            theta = np.concatenate([np.full(self.d, math.log(0.3)), [0.0, math.log(1e-3)]])
        self.theta = np.asarray(theta, dtype=np.float64)
        if fit and len(self.y) >= 2:
            self._fit(restarts)
        self._factor()

    # ------------------------------------------------------------------ hyperparameters
    def _unpack(self, th: np.ndarray):
        return np.exp(th[: self.d]), float(np.exp(th[self.d])), float(np.exp(th[self.d + 1]))

    def _nlml(self, th: np.ndarray) -> float:
        ls, s2, nz = self._unpack(th)
        K = matern52(self.X, self.X, ls, s2) + (nz + 1e-10) * np.eye(len(self.y))
        try:
            c = cho_factor(K, lower=True, check_finite=False)
        except np.linalg.LinAlgError:
            return 1e10
        a = cho_solve(c, self.y, check_finite=False)
        return float(0.5 * self.y @ a + np.log(np.diag(c[0])).sum() + 0.5 * len(self.y) * math.log(2 * math.pi))

    def _fit(self, restarts: int) -> None:
        bounds = [self.LS_BOUNDS] * self.d + [self.S2_BOUNDS, self.NOISE_BOUNDS]
        starts = [self.theta]
        for _ in range(restarts):
            starts.append(np.array([self.rng.uniform(lo, hi) for lo, hi in bounds]))
        best, best_v = self.theta, self._nlml(self.theta)
        for s in starts:
            try:
                r = minimize(self._nlml, s, method="L-BFGS-B", bounds=bounds, options={"maxiter": 150})
            except Exception:  # noqa: BLE001 - a failed restart is not fatal
                continue
            if np.isfinite(r.fun) and r.fun < best_v:
                best, best_v = r.x, float(r.fun)
        self.theta = np.asarray(best)

    def _factor(self) -> None:
        ls, s2, nz = self._unpack(self.theta)
        self.ls, self.s2, self.noise = ls, s2, nz
        K = matern52(self.X, self.X, ls, s2) + (nz + 1e-10) * np.eye(len(self.y))
        jitter = 0.0
        for _ in range(6):
            try:
                self.chol = cho_factor(K + jitter * np.eye(len(K)), lower=True, check_finite=False)
                break
            except np.linalg.LinAlgError:
                jitter = 1e-8 if jitter == 0 else jitter * 10
        else:  # pragma: no cover - pathological inputs
            raise np.linalg.LinAlgError("GP covariance is not positive definite")
        self.alpha = cho_solve(self.chol, self.y, check_finite=False)

    # ------------------------------------------------------------------ prediction
    def predict(self, Xs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Mean and variance in the original target units (latent, noise-free)."""
        Xs = np.atleast_2d(Xs)
        if len(self.y) == 0:
            return np.full(len(Xs), self.mu), np.full(len(Xs), self.sd**2)
        Ks = matern52(Xs, self.X, self.ls, self.s2)
        m = Ks @ self.alpha
        v = cho_solve(self.chol, Ks.T, check_finite=False)
        var = np.maximum(self.s2 - (Ks * v.T).sum(1), 1e-12)
        return m * self.sd + self.mu, var * self.sd**2

    def with_fantasies(self, Xf: np.ndarray, yf: np.ndarray) -> "GP":
        """Same hyperparameters, extra (fantasised) observations — Kriging believer."""
        X = np.vstack([self.X, np.atleast_2d(Xf)])
        y = np.concatenate([self.y * self.sd + self.mu, np.asarray(yf).reshape(-1)])
        g = GP.__new__(GP)
        g.X, g.d, g.rng, g.theta = X, self.d, self.rng, self.theta
        g.mu, g.sd = self.mu, self.sd
        g.y = (y - g.mu) / g.sd
        g._factor()
        return g
