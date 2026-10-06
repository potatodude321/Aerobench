"""Optimiser registry (ask/tell protocol, ADR-0009)."""

from __future__ import annotations

from typing import Any

from .base import Observation, Optimizer, pareto_mask, total_violation
from .gp_bo import GPBO
from .nelder_mead import NelderMead
from .nsga2 import NSGA2
from .space_filling import SpaceFilling

ALGORITHMS: dict[str, type[Optimizer]] = {c.name: c for c in (SpaceFilling, GPBO, NSGA2, NelderMead)}


def create(name: str, **kw: Any) -> Optimizer:
    try:
        cls = ALGORITHMS[name]
    except KeyError:
        raise ValueError(f"unknown optimisation algorithm '{name}'") from None
    return cls(**kw)


def restore(state: dict[str, Any]) -> Optimizer:
    return ALGORITHMS[state["algorithm"]]._restore(state)


__all__ = ["ALGORITHMS", "Observation", "Optimizer", "create", "pareto_mask", "restore", "total_violation"]
