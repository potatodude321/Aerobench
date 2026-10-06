"""Typed design space: engineering values <-> unit hypercube, with grid snapping."""

from __future__ import annotations

from typing import Any, Union

import numpy as np

from ..domain.exploration import DesignVariable, VariableKind

Value = Union[float, str]


class DesignSpace:
    def __init__(self, variables: list[DesignVariable]):
        if not variables:
            raise ValueError("a design space needs at least one variable")
        ids = [v.id for v in variables]
        if len(ids) != len(set(ids)):
            raise ValueError("design variable ids must be unique")
        targets = [v.target for v in variables]
        if len(targets) != len(set(targets)):
            raise ValueError("two design variables set the same target")
        self.variables = list(variables)

    @property
    def dim(self) -> int:
        return len(self.variables)

    # ------------------------------------------------------------------ conversions
    def to_unit(self, values: dict[str, Value]) -> np.ndarray:
        u = np.zeros(self.dim)
        for i, v in enumerate(self.variables):
            x = values[v.id]
            if v.kind == VariableKind.DISCRETE:
                vals = list(v.values or [])
                idx = _index_of(vals, x)
                u[i] = (idx + 0.5) / len(vals)
            else:
                u[i] = (float(x) - v.lower) / (v.upper - v.lower)  # type: ignore[operator]
        return np.clip(u, 0.0, 1.0)

    def from_unit(self, u: np.ndarray) -> dict[str, Value]:
        u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
        out: dict[str, Value] = {}
        for i, v in enumerate(self.variables):
            if v.kind == VariableKind.DISCRETE:
                vals = list(v.values or [])
                out[v.id] = vals[min(int(u[i] * len(vals)), len(vals) - 1)]
            elif v.kind == VariableKind.INTEGER:
                out[v.id] = float(int(round(v.lower + u[i] * (v.upper - v.lower))))  # type: ignore[operator]
            else:
                out[v.id] = float(v.lower + u[i] * (v.upper - v.lower))  # type: ignore[operator]
        return out

    def project(self, u: np.ndarray) -> np.ndarray:
        """Snap a unit point onto representable values (integers, discrete choices)."""
        return self.to_unit(self.from_unit(u))

    def validate_values(self, values: dict[str, Value]) -> list[str]:
        errs = []
        for v in self.variables:
            if v.id not in values:
                errs.append(f"missing value for '{v.id}'")
                continue
            x = values[v.id]
            if v.kind == VariableKind.DISCRETE:
                if _index_of(list(v.values or []), x, strict=False) < 0:
                    errs.append(f"'{v.id}' = {x!r} is not one of the allowed values")
            else:
                try:
                    xf = float(x)
                except (TypeError, ValueError):
                    errs.append(f"'{v.id}' = {x!r} is not a number")
                    continue
                if not (v.lower - 1e-12 <= xf <= v.upper + 1e-12):  # type: ignore[operator]
                    errs.append(f"'{v.id}' = {xf} is outside [{v.lower}, {v.upper}]")
                if v.kind == VariableKind.INTEGER and abs(xf - round(xf)) > 1e-9:
                    errs.append(f"'{v.id}' must be an integer")
        return errs

    def describe(self) -> list[dict[str, Any]]:
        return [v.model_dump(mode="json") for v in self.variables]


def _index_of(vals: list, x: Any, strict: bool = True) -> int:
    for i, v in enumerate(vals):
        if v == x:
            return i
        try:
            if float(v) == float(x):
                return i
        except (TypeError, ValueError):
            pass
    if strict:
        raise ValueError(f"{x!r} is not one of {vals}")
    return -1


def values_key(values: dict[str, Value]) -> str:
    """Canonical key for identical-design detection (12 significant digits)."""
    parts = []
    for k in sorted(values):
        v = values[k]
        parts.append(f"{k}={float(v):.12g}" if isinstance(v, (int, float)) else f"{k}={v}")
    return ";".join(parts)
