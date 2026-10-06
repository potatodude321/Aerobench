"""NSGA-II (Deb et al., 2002) with constraint domination, in ask/tell form.

* Generation 0 is a Latin hypercube of ``pop_size`` points; each later generation is
  produced by binary tournament (rank, then crowding), simulated binary crossover and
  polynomial mutation in the unit cube, snapped to the design grid.
* Constraint domination: feasible beats infeasible; between infeasible designs the smaller
  total violation wins; failed evaluations are infinitely infeasible.
* Evaluation is asynchronous within a generation: ``ask`` hands out the members of the
  current generation; when they are all told, selection runs and the next generation is
  produced.  ``ask`` returns nothing while the generation is still being evaluated.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .base import Optimizer, lhs, total_violation


def constrained_sort(F: np.ndarray, V: np.ndarray) -> list[list[int]]:
    """Non-dominated fronts with constraint domination.  ``V`` = total violation (inf = failed)."""
    n = len(F)
    S: list[list[int]] = [[] for _ in range(n)]
    cnt = np.zeros(n, dtype=int)

    def cdom(i: int, j: int) -> bool:
        vi, vj = V[i], V[j]
        if vi <= 0 and vj > 0:
            return True
        if vi > 0 and vj > 0:
            return vi < vj
        if vi > 0 and vj <= 0:
            return False
        return bool(np.all(F[i] <= F[j]) and np.any(F[i] < F[j]))

    fronts: list[list[int]] = [[]]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            if cdom(i, j):
                S[i].append(j)
            elif cdom(j, i):
                cnt[i] += 1
        if cnt[i] == 0:
            fronts[0].append(i)
    k = 0
    while fronts[k]:
        nxt = []
        for i in fronts[k]:
            for j in S[i]:
                cnt[j] -= 1
                if cnt[j] == 0:
                    nxt.append(j)
        k += 1
        fronts.append(nxt)
    return fronts[:-1]


def crowding(F: np.ndarray, idx: list[int]) -> dict[int, float]:
    d = {i: 0.0 for i in idx}
    if len(idx) <= 2:
        return {i: math.inf for i in idx}
    sub = F[idx]
    for m in range(F.shape[1]):
        order = np.argsort(sub[:, m], kind="stable")
        lo, hi = sub[order[0], m], sub[order[-1], m]
        d[idx[order[0]]] = d[idx[order[-1]]] = math.inf
        if not np.isfinite(hi - lo) or hi - lo <= 0:
            continue
        for k in range(1, len(order) - 1):
            d[idx[order[k]]] += (sub[order[k + 1], m] - sub[order[k - 1], m]) / (hi - lo)
    return d


class NSGA2(Optimizer):
    name = "nsga2"
    multi_objective = True

    def _setup(self) -> None:
        pop = int(self.options.get("pop_size") or max(8, min(40, 4 * self.dim)))
        pop += pop % 2
        if self.budget < 2 * pop:
            pop = max(4, (self.budget // 2) // 2 * 2)
        self.pop_size = pop
        self.eta_c = float(self.options.get("eta_c", 15.0))
        self.eta_m = float(self.options.get("eta_m", 20.0))
        self.p_c = float(self.options.get("p_c", 0.9))
        self.generation = 0
        self.queue: list[list[float]] = lhs(self.rng, pop, self.dim).tolist()
        self.gen_tokens: list[str] = []
        self.parents: list[str] = []  # tokens of the current parent population

    def _extra_state(self) -> dict[str, Any]:
        return {"pop_size": self.pop_size, "eta_c": self.eta_c, "eta_m": self.eta_m, "p_c": self.p_c, "generation": self.generation,
                "queue": self.queue, "gen_tokens": self.gen_tokens, "parents": self.parents}

    def _load_extra(self, d: dict[str, Any]) -> None:
        for k, v in d.items():
            setattr(self, k, v)

    # ------------------------------------------------------------------ ask
    def ask(self, n: int = 1):  # type: ignore[override]
        out = super().ask(n)
        self.gen_tokens.extend(t for t, _ in out)
        return out

    def forget_pending(self, token: str) -> None:
        super().forget_pending(token)
        if token in self.gen_tokens:
            self.gen_tokens.remove(token)

    def _propose(self, n: int) -> list[np.ndarray]:
        if not self.queue and self._generation_complete():
            self._advance()
        out = []
        while self.queue and len(out) < n:
            out.append(np.asarray(self.queue.pop(0)))
        return out

    def _generation_complete(self) -> bool:
        told = {o.token for o in self.observations}
        return bool(self.gen_tokens) and all(t in told for t in self.gen_tokens)

    def _obs_by_token(self):
        return {o.token: o for o in self.observations}

    def _advance(self) -> None:
        obs = self._obs_by_token()
        pool = [t for t in dict.fromkeys(self.parents + self.gen_tokens) if t in obs]
        # baseline / externally told designs join the first selection
        if self.generation == 0:
            pool += [o.token for o in self.observations if o.token not in pool]
        self.parents = self._select(pool, obs, self.pop_size)
        self.generation += 1
        self.gen_tokens = []
        self.queue = [x.tolist() for x in self._offspring(self.parents, obs)]

    def _matrices(self, tokens: list[str], obs) -> tuple[np.ndarray, np.ndarray]:
        F = np.array([obs[t].f if obs[t].f is not None else [math.inf] * self.n_obj for t in tokens], dtype=float)
        V = np.array([math.inf if obs[t].failed else total_violation(obs[t].g) for t in tokens], dtype=float)
        F = np.where(np.isfinite(F), F, 1e300)
        return F, V

    def _select(self, pool: list[str], obs, k: int) -> list[str]:
        F, V = self._matrices(pool, obs)
        fronts = constrained_sort(F, V)
        chosen: list[int] = []
        self._rank: dict[str, int] = {}
        self._crowd: dict[str, float] = {}
        for r, fr in enumerate(fronts):
            cd = crowding(F, fr)
            for i in fr:
                self._rank[pool[i]] = r
                self._crowd[pool[i]] = cd[i]
            if len(chosen) + len(fr) <= k:
                chosen.extend(fr)
            else:
                rest = sorted(fr, key=lambda i: -cd[i])[: k - len(chosen)]
                chosen.extend(rest)
                break
        return [pool[i] for i in chosen]

    def _tournament(self, parents: list[str]) -> str:
        a, b = self.rng.choice(len(parents), 2, replace=len(parents) < 2)
        ta, tb = parents[a], parents[b]
        ra, rb = self._rank.get(ta, 0), self._rank.get(tb, 0)
        if ra != rb:
            return ta if ra < rb else tb
        return ta if self._crowd.get(ta, 0) >= self._crowd.get(tb, 0) else tb

    def _sbx(self, x1: np.ndarray, x2: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        c1, c2 = x1.copy(), x2.copy()
        if self.rng.random() > self.p_c:
            return c1, c2
        for i in range(self.dim):
            if self.rng.random() > 0.5 or abs(x1[i] - x2[i]) < 1e-14:
                continue
            y1, y2 = min(x1[i], x2[i]), max(x1[i], x2[i])
            u = self.rng.random()
            beta = 1.0 + 2.0 * min(y1, 1.0 - y2) / (y2 - y1)
            alpha = 2.0 - beta ** -(self.eta_c + 1.0)
            bq = (u * alpha) ** (1.0 / (self.eta_c + 1.0)) if u <= 1.0 / alpha else (1.0 / (2.0 - u * alpha)) ** (1.0 / (self.eta_c + 1.0))
            a, b = 0.5 * ((y1 + y2) - bq * (y2 - y1)), 0.5 * ((y1 + y2) + bq * (y2 - y1))
            if self.rng.random() < 0.5:
                a, b = b, a
            c1[i], c2[i] = a, b
        return np.clip(c1, 0, 1), np.clip(c2, 0, 1)

    def _mutate(self, x: np.ndarray) -> np.ndarray:
        y = x.copy()
        pm = 1.0 / self.dim
        for i in range(self.dim):
            if self.rng.random() >= pm:
                continue
            u = self.rng.random()
            d1, d2 = y[i], 1.0 - y[i]
            mp = 1.0 / (self.eta_m + 1.0)
            if u < 0.5:
                dq = (2 * u + (1 - 2 * u) * (1 - d1) ** (self.eta_m + 1)) ** mp - 1
            else:
                dq = 1 - (2 * (1 - u) + 2 * (u - 0.5) * (1 - d2) ** (self.eta_m + 1)) ** mp
            y[i] = y[i] + dq
        return np.clip(y, 0, 1)

    def _offspring(self, parents: list[str], obs) -> list[np.ndarray]:
        seen = {tuple(np.round(self.project(np.asarray(o.x)), 12)) for o in self.observations}
        kids: list[np.ndarray] = []
        guard = 0
        while len(kids) < self.pop_size and guard < 50 * self.pop_size:
            guard += 1
            p1 = np.asarray(obs[self._tournament(parents)].x)
            p2 = np.asarray(obs[self._tournament(parents)].x)
            for c in self._sbx(p1, p2):
                c = self.project(self._mutate(c))
                key = tuple(np.round(c, 12))
                if key in seen and guard < 40 * self.pop_size:
                    continue  # avoid re-evaluating an identical design
                seen.add(key)
                kids.append(c)
                if len(kids) >= self.pop_size:
                    break
        return kids
