"""Design-of-experiments generators for studies (one-factor sweeps, full factorial,
Latin hypercube, explicit tables)."""

from __future__ import annotations

import itertools
from typing import Any, Union

import numpy as np

from ..domain.exploration import DesignVariable, StudyDefinition, StudyKind, VariableKind
from ..optimisation.algorithms.base import lhs
from .space import DesignSpace, values_key

Value = Union[float, str]

MAX_DESIGNS = 1000


def default_levels(v: DesignVariable, n: int) -> list[Value]:
    if v.kind == VariableKind.DISCRETE:
        return list(v.values or [])
    pts = np.linspace(v.lower, v.upper, n)  # type: ignore[arg-type]
    if v.kind == VariableKind.INTEGER:
        return sorted({float(round(p)) for p in pts})
    return [float(p) for p in pts]


def levels_for(defn: StudyDefinition, v: DesignVariable, n_default: int) -> list[Value]:
    lv = defn.levels.get(v.id)
    return list(lv) if lv else default_levels(v, n_default)


def generate(defn: StudyDefinition, baseline: dict[str, Value]) -> list[dict[str, Any]]:
    """Designs as ``{"values": {...}, "origin": ...}``; the baseline first when requested.
    Identical designs are generated once."""
    space = DesignSpace(defn.variables)
    designs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(values: dict[str, Value], origin: str) -> None:
        errs = space.validate_values(values)
        if errs:
            raise ValueError("; ".join(errs))
        k = values_key(values)
        if k in seen:
            return
        seen.add(k)
        designs.append({"values": dict(values), "origin": origin})

    if defn.include_baseline:
        add(baseline, "baseline")
    if defn.kind == StudyKind.ONE_FACTOR:
        for v in defn.variables:
            for lv in levels_for(defn, v, 5):
                add({**baseline, v.id: lv}, "doe")
    elif defn.kind == StudyKind.FULL_FACTORIAL:
        grids = [levels_for(defn, v, 3) for v in defn.variables]
        n = int(np.prod([len(g) for g in grids]))
        if n > MAX_DESIGNS:
            raise ValueError(f"the full factorial has {n} designs (limit {MAX_DESIGNS}); use fewer levels or a Latin hypercube")
        for combo in itertools.product(*grids):
            add({v.id: x for v, x in zip(defn.variables, combo)}, "doe")
    elif defn.kind == StudyKind.LATIN_HYPERCUBE:
        if defn.n_samples > MAX_DESIGNS:
            raise ValueError(f"at most {MAX_DESIGNS} samples")
        rng = np.random.default_rng(defn.seed)
        for u in lhs(rng, defn.n_samples, space.dim):
            add(space.from_unit(u), "doe")
    elif defn.kind == StudyKind.TABLE:
        if not defn.table:
            raise ValueError("a table study needs at least one row")
        for i, row in enumerate(defn.table):
            missing = [v.id for v in defn.variables if v.id not in row]
            vals = {**baseline, **{k: row[k] for k in row if k in {v.id for v in defn.variables}}}
            unknown = [k for k in row if k not in {v.id for v in defn.variables}]
            if unknown:
                raise ValueError(f"table row {i + 1}: unknown column(s) {', '.join(unknown)}")
            if missing and any(baseline.get(m) is None for m in missing):
                raise ValueError(f"table row {i + 1}: missing value(s) for {', '.join(missing)}")
            add(vals, "table")
    if len(designs) > MAX_DESIGNS:
        raise ValueError(f"{len(designs)} designs exceed the limit of {MAX_DESIGNS}")
    return designs
