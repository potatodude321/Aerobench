"""Space-filling exploration: a Latin-hypercube design of the full budget, evaluated in order.

The whole sample is generated up front from the seed, so the design is reproducible and a
resumed study continues with exactly the points it would have evaluated."""

from __future__ import annotations

from typing import Any

import numpy as np

from .base import Optimizer, lhs


class SpaceFilling(Optimizer):
    name = "space_filling"
    multi_objective = True

    def _setup(self) -> None:
        self.sample = lhs(self.rng, max(1, self.budget), self.dim).tolist()
        self.next_index = 0

    def _propose(self, n: int) -> list[np.ndarray]:
        out = []
        while len(out) < n and self.next_index < len(self.sample):
            out.append(np.asarray(self.sample[self.next_index]))
            self.next_index += 1
        return out

    def _extra_state(self) -> dict[str, Any]:
        return {"sample": self.sample, "next_index": self.next_index}

    def _load_extra(self, d: dict[str, Any]) -> None:
        self.sample = d["sample"]
        self.next_index = d["next_index"]
