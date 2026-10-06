"""Ask/tell optimiser protocol with JSON-serialisable state (ADR-0009).

All optimisers work in the unit hypercube ``[0, 1]^d``; the design space maps unit
coordinates to engineering values (and snaps integer/discrete variables).  Objectives are
*minimised* (the driver negates maximised objectives) and constraints are ``g(x) <= 0``
after normalisation.  A failed evaluation is data: it is told with ``failed=True`` and no
values.

Every optimiser can be checkpointed after any ``ask``/``tell`` with :meth:`state` and
rebuilt with :func:`restore`; a resumed optimiser continues exactly where the original
would have (its random generator state is part of the checkpoint).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

Projector = Callable[[np.ndarray], np.ndarray]


@dataclass
class Observation:
    token: str
    x: list[float]
    f: Optional[list[float]]
    g: Optional[list[float]]
    failed: bool

    def as_dict(self) -> dict[str, Any]:
        return {"token": self.token, "x": self.x, "f": self.f, "g": self.g, "failed": self.failed}


def total_violation(g: Optional[list[float]]) -> float:
    if g is None:
        return 0.0
    return float(sum(max(0.0, v) for v in g))


def dominates(a: np.ndarray, b: np.ndarray) -> bool:
    return bool(np.all(a <= b) and np.any(a < b))


def pareto_mask(F: np.ndarray) -> np.ndarray:
    """Non-dominated rows of F (minimisation)."""
    n = len(F)
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        if not keep[i]:
            continue
        dom = np.all(F <= F[i], axis=1) & np.any(F < F[i], axis=1)
        if dom.any():
            keep[i] = False
    return keep


class Optimizer(ABC):
    name = "base"
    multi_objective = False

    def __init__(self, dim: int, n_obj: int = 1, n_con: int = 0, budget: int = 30, batch_size: int = 1, seed: int = 0,
                 init_samples: Optional[int] = None, x0: Optional[list[float]] = None, options: Optional[dict[str, Any]] = None):
        if dim < 1:
            raise ValueError("the design space needs at least one variable")
        if n_obj < 1:
            raise ValueError("at least one objective is required")
        if n_obj > 1 and not self.multi_objective:
            raise ValueError(f"{self.name} is single-objective")
        self.dim = int(dim)
        self.n_obj = int(n_obj)
        self.n_con = int(n_con)
        self.budget = int(budget)
        self.batch_size = max(1, int(batch_size))
        self.seed = int(seed)
        self.init_samples = init_samples
        self.x0 = list(map(float, x0)) if x0 is not None else None
        self.options = dict(options or {})
        self.rng = np.random.default_rng(self.seed)
        self.observations: list[Observation] = []
        self.pending: dict[str, list[float]] = {}
        self.counter = 0
        self.projector: Optional[Projector] = None
        self._setup()

    # ------------------------------------------------------------------ to implement
    def _setup(self) -> None:
        """Initialise algorithm-specific state (called once, not on restore)."""

    @abstractmethod
    def _propose(self, n: int) -> list[np.ndarray]:
        """Return up to ``n`` new unit-cube points (may return fewer, e.g. while waiting)."""

    def _on_tell(self, obs: Observation) -> None:
        """Hook after an observation is recorded."""

    def _extra_state(self) -> dict[str, Any]:
        return {}

    def _load_extra(self, d: dict[str, Any]) -> None:
        pass

    # ------------------------------------------------------------------ protocol
    def project(self, x: np.ndarray) -> np.ndarray:
        x = np.clip(np.asarray(x, dtype=np.float64), 0.0, 1.0)
        return self.projector(x) if self.projector is not None else x

    @property
    def n_told(self) -> int:
        return len(self.observations)

    def remaining(self) -> int:
        return max(0, self.budget - self.n_told - len(self.pending))

    def done(self) -> bool:
        return self.remaining() == 0 and not self.pending

    def ask(self, n: int = 1) -> list[tuple[str, list[float]]]:
        n = min(int(n), self.remaining())
        if n <= 0:
            return []
        out = []
        for x in self._propose(n)[:n]:
            x = self.project(np.asarray(x))
            self.counter += 1
            tok = f"{self.name}-{self.counter}"
            self.pending[tok] = [float(v) for v in x]
            out.append((tok, self.pending[tok]))
        return out

    def tell(self, token: str, f: Optional[list[float]] = None, g: Optional[list[float]] = None, failed: bool = False,
             x: Optional[list[float]] = None) -> None:
        """Record an evaluation.  ``x`` must be given for points the optimiser did not ask
        for (e.g. the baseline design)."""
        if token in self.pending:
            asked_x = self.pending.pop(token)
            xv = [float(v) for v in x] if x is not None else asked_x
        elif x is not None:
            xv = [float(v) for v in x]
        else:
            raise KeyError(f"unknown evaluation token {token}")
        if any(o.token == token for o in self.observations):
            return  # idempotent: re-telling after a crash/resume is harmless
        if not failed:
            if f is None or len(f) != self.n_obj or not all(np.isfinite(f)):
                failed = True
            elif self.n_con and (g is None or len(g) != self.n_con or not all(np.isfinite(g))):
                failed = True
        obs = Observation(token, xv, None if failed else [float(v) for v in f], None if failed or g is None else [float(v) for v in g], failed)
        self.observations.append(obs)
        self._on_tell(obs)

    def forget_pending(self, token: str) -> None:
        """Drop an asked point that will never be evaluated (e.g. a cancelled design)."""
        self.pending.pop(token, None)

    # ------------------------------------------------------------------ results
    def arrays(self, include_failed: bool = False):
        obs = [o for o in self.observations if include_failed or not o.failed]
        X = np.array([o.x for o in obs]).reshape(-1, self.dim)
        F = np.array([o.f for o in obs if o.f is not None]).reshape(-1, self.n_obj) if not include_failed else None
        G = np.array([o.g if o.g is not None else [] for o in obs]).reshape(len(obs), -1) if self.n_con else np.zeros((len(obs), 0))
        return obs, X, F, G

    def feasible_observations(self) -> list[Observation]:
        return [o for o in self.observations if not o.failed and total_violation(o.g) <= 0.0]

    def best(self) -> Optional[Observation]:
        """Best feasible observation (single objective); None if nothing is feasible."""
        feas = self.feasible_observations()
        if not feas:
            return None
        return min(feas, key=lambda o: o.f[0])  # type: ignore[index]

    def pareto(self) -> list[Observation]:
        feas = self.feasible_observations()
        if not feas:
            return []
        F = np.array([o.f for o in feas])
        m = pareto_mask(F)
        return [o for o, k in zip(feas, m) if k]

    # ------------------------------------------------------------------ state
    def state(self) -> dict[str, Any]:
        return {
            "algorithm": self.name,
            "dim": self.dim, "n_obj": self.n_obj, "n_con": self.n_con, "budget": self.budget, "batch_size": self.batch_size,
            "seed": self.seed, "init_samples": self.init_samples, "x0": self.x0, "options": self.options,
            "rng": self.rng.bit_generator.state, "observations": [o.as_dict() for o in self.observations],
            "pending": self.pending, "counter": self.counter, "extra": self._extra_state(),
        }

    @classmethod
    def _restore(cls, d: dict[str, Any]) -> "Optimizer":
        obj = cls.__new__(cls)
        obj.dim, obj.n_obj, obj.n_con = d["dim"], d["n_obj"], d["n_con"]
        obj.budget, obj.batch_size, obj.seed = d["budget"], d["batch_size"], d["seed"]
        obj.init_samples, obj.x0, obj.options = d.get("init_samples"), d.get("x0"), dict(d.get("options") or {})
        obj.rng = np.random.default_rng()
        obj.rng.bit_generator.state = d["rng"]
        obj.observations = [Observation(**o) for o in d["observations"]]
        obj.pending = {k: list(v) for k, v in d["pending"].items()}
        obj.counter = d["counter"]
        obj.projector = None
        obj._load_extra(d.get("extra") or {})
        return obj


def lhs(rng: np.random.Generator, n: int, d: int) -> np.ndarray:
    """Latin hypercube sample with a few random restarts maximising the minimum distance."""
    best, best_score = None, -1.0
    for _ in range(8 if n <= 200 else 1):
        cut = (np.arange(n)[:, None] + rng.random((n, d))) / n
        for j in range(d):
            cut[:, j] = cut[rng.permutation(n), j]
        if n > 1:
            diff = cut[:, None, :] - cut[None, :, :]
            dist = np.sqrt((diff**2).sum(-1)) + np.eye(n) * 1e9
            score = float(dist.min())
        else:
            score = 0.0
        if score > best_score:
            best, best_score = cut, score
    return best  # type: ignore[return-value]
