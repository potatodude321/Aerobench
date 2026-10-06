"""Shared domain primitives."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

Vec3 = tuple[float, float, float]
PositiveFloat = Annotated[float, Field(gt=0)]
NonNegFloat = Annotated[float, Field(ge=0)]


class Model(BaseModel):
    """Base for all persisted domain models: strict about unknown keys, validated on assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


class AxisDir(str, Enum):
    PX = "+x"
    NX = "-x"
    PY = "+y"
    NY = "-y"
    PZ = "+z"
    NZ = "-z"

    def vector(self) -> np.ndarray:
        idx = "xyz".index(self.value[1])
        v = np.zeros(3)
        v[idx] = 1.0 if self.value[0] == "+" else -1.0
        return v


def unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(a))
    if n == 0.0:
        raise ValueError("zero-length vector")
    return a / n
