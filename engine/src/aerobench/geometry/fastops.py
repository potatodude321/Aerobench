"""Fast drop-in replacements for ``np.unique(..., axis=0)`` on large meshes.

``np.unique`` with ``axis=0`` sorts rows through a structured view, which is several times
slower than sorting one integer key per row. Both functions here return exactly what
``np.unique(rows, axis=0, ...)`` returns (same unique rows in the same lexicographic order,
same index/inverse/count arrays), so callers keep their semantics:

* integer rows are packed into one int64 key per row whose order equals the rows'
  lexicographic order (when the value ranges fit in 63 bits; otherwise the numpy path);
* float rows (vertex welding) are grouped by a 64-bit hash of their bit patterns, every
  group is verified to contain identical rows (any collision falls back to the numpy path),
  and the unique rows are then sorted lexicographically.
"""

from __future__ import annotations

import numpy as np

_MAX_KEY = 1 << 62


def _int_keys(rows: np.ndarray) -> np.ndarray | None:
    rows = np.asarray(rows)
    if rows.ndim != 2 or rows.shape[0] == 0 or not np.issubdtype(rows.dtype, np.integer):
        return None
    lo = rows.min(axis=0).astype(np.int64)
    hi = rows.max(axis=0).astype(np.int64)
    span = [int(h) - int(l) + 1 for l, h in zip(lo, hi)]
    total = 1
    for s in span:
        total *= s
    if total >= _MAX_KEY:
        return None
    key = np.zeros(rows.shape[0], np.int64)
    for j, s in enumerate(span):
        key *= s
        key += rows[:, j].astype(np.int64) - lo[j]
    return key


def unique_int_rows(rows: np.ndarray, return_index: bool = False, return_inverse: bool = False,
                    return_counts: bool = False):
    """``np.unique(rows, axis=0, ...)`` for integer arrays, via one packed key per row."""
    rows = np.asarray(rows)
    key = _int_keys(rows)
    if key is None:
        return np.unique(rows, axis=0, return_index=return_index, return_inverse=return_inverse,
                         return_counts=return_counts)
    res = np.unique(key, return_index=True, return_inverse=return_inverse, return_counts=return_counts)
    first = res[1]
    out = [rows[first]]
    if return_index:
        out.append(first)
    k = 2
    if return_inverse:
        out.append(res[k].reshape(-1))
        k += 1
    if return_counts:
        out.append(res[k])
    return out[0] if len(out) == 1 else tuple(out)


def weld_float_rows(rows: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``np.unique(rows, axis=0, return_inverse=True)`` for finite float64 (N, 3) rows."""
    rows = np.ascontiguousarray(rows, dtype=np.float64)
    if rows.shape[0] == 0:
        return rows.reshape(0, rows.shape[1]), np.zeros(0, np.int64)
    rows = rows + 0.0  # -0.0 -> +0.0: numpy's comparison treats them as equal as well
    b = rows.view(np.uint64)
    h = np.zeros(len(rows), np.uint64)
    mults = (np.uint64(0x9E3779B97F4A7C15), np.uint64(0xBF58476D1CE4E5B9), np.uint64(0x94D049BB133111EB))
    with np.errstate(over="ignore"):
        for j in range(b.shape[1]):
            x = b[:, j] ^ (b[:, j] >> np.uint64(31))
            h ^= x * mults[j % 3]
            h = (h << np.uint64(23)) | (h >> np.uint64(41))
    _, first, inv = np.unique(h, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    rep = rows[first]
    if not np.array_equal(rep[inv], rows):
        return np.unique(rows, axis=0, return_inverse=True)  # hash collision (practically never)
    order = np.lexsort(rep.T[::-1])
    rank = np.empty_like(order)
    rank[order] = np.arange(len(order))
    return rep[order], rank[inv]
