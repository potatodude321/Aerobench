"""OpenFOAM dictionary abstract syntax tree (ADR-0006).

Values are Python scalars (``int``, ``float``, ``bool``) or AST nodes.  ``Dict`` keeps
insertion order and may interleave ``Directive`` items (``#include`` ...) with entries.
Entry values made of several tokens (``bounded Gauss linearUpwindV grad(U)``) are
``Tokens``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Union

Scalar = Union[int, float, bool]

_WORD_RE = re.compile(r"^[A-Za-z_$#:][^\s;{}\[\]\"]*$")


@dataclass(frozen=True)
class Word:
    value: str

    def __post_init__(self):
        v = self.value
        if v.startswith("${") and v.endswith("}"):
            return  # macro / inline expression ${...} / ${{...}}
        if not v or any(c.isspace() for c in v) or any(c in v for c in ';{}"'):
            raise ValueError(f"invalid OpenFOAM word: {v!r}")


@dataclass(frozen=True)
class Str:
    value: str


@dataclass(frozen=True)
class Dimensions:
    exps: tuple[float, ...]  # 7 SI exponents [kg m s K mol A cd]

    def __post_init__(self):
        if len(self.exps) not in (5, 7):
            raise ValueError("dimensions need 5 or 7 exponents")


@dataclass(frozen=True)
class Raw:
    """Verbatim text (e.g. ``#codeStream`` blocks from imported cases)."""

    text: str


@dataclass
class List:
    items: list[Any] = field(default_factory=list)


@dataclass
class Tokens:
    """A multi-token entry value, e.g. ``uniform (0 0 0)`` or ``Gauss linear``."""

    items: list[Any] = field(default_factory=list)


@dataclass
class Directive:
    name: str  # e.g. "#include", "#includeEtc", "#includeFunc", "#remove"
    arg: Any = None


@dataclass
class Entry:
    key: str
    value: Any


class Dict:
    """Ordered OpenFOAM dictionary."""

    __slots__ = ("items",)

    def __init__(self, entries: Any = None, **kw: Any):
        self.items: list[Union[Entry, Directive]] = []
        if entries:
            pairs = entries.items() if isinstance(entries, dict) else entries
            for k, v in pairs:
                self[k] = v
        for k, v in kw.items():
            self[k] = v

    # -- mapping protocol -----------------------------------------------------------
    def _find(self, key: str) -> int:
        for i, it in enumerate(self.items):
            if isinstance(it, Entry) and it.key == key:
                return i
        return -1

    def __contains__(self, key: str) -> bool:
        return self._find(key) >= 0

    def __getitem__(self, key: str) -> Any:
        i = self._find(key)
        if i < 0:
            raise KeyError(key)
        return self.items[i].value  # type: ignore[union-attr]

    def get(self, key: str, default: Any = None) -> Any:
        i = self._find(key)
        return default if i < 0 else self.items[i].value  # type: ignore[union-attr]

    def __setitem__(self, key: str, value: Any) -> None:
        value = coerce(value)
        i = self._find(key)
        if i >= 0:
            self.items[i] = Entry(key, value)
        else:
            self.items.append(Entry(key, value))

    def __delitem__(self, key: str) -> None:
        i = self._find(key)
        if i < 0:
            raise KeyError(key)
        del self.items[i]

    def keys(self) -> list[str]:
        return [it.key for it in self.items if isinstance(it, Entry)]

    def entries(self) -> Iterator[Entry]:
        return (it for it in self.items if isinstance(it, Entry))

    def add_directive(self, name: str, arg: Any = None) -> None:
        self.items.append(Directive(name, arg))

    def __len__(self) -> int:
        return len(self.keys())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Dict) and self.items == other.items

    def __repr__(self) -> str:
        return f"Dict({', '.join(self.keys())})"

    # -- key paths ('/'-separated) ---------------------------------------------------
    def get_path(self, path: str) -> Any:
        node: Any = self
        for part in _split_path(path):
            if not isinstance(node, Dict) or part not in node:
                raise KeyError(path)
            node = node[part]
        return node

    def has_path(self, path: str) -> bool:
        try:
            self.get_path(path)
            return True
        except KeyError:
            return False

    def set_path(self, path: str, value: Any, create: bool = True) -> None:
        parts = _split_path(path)
        node: Any = self
        for part in parts[:-1]:
            if part not in node:
                if not create:
                    raise KeyError(path)
                node[part] = Dict()
            node = node[part]
            if not isinstance(node, Dict):
                raise KeyError(f"{path}: '{part}' is not a dictionary")
        node[parts[-1]] = value

    def delete_path(self, path: str) -> None:
        parts = _split_path(path)
        node: Any = self
        for part in parts[:-1]:
            node = node[part]
            if not isinstance(node, Dict):
                raise KeyError(path)
        del node[parts[-1]]

    def copy(self) -> "Dict":
        return deep_copy(self)


def _split_path(path: str) -> list[str]:
    # Allow quoted regex keys containing '/' by escaping as '\/'
    parts, cur, esc = [], [], False
    for ch in path:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "/":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    if any(p == "" for p in parts):
        raise KeyError(f"invalid key path {path!r}")
    return parts


# ------------------------------------------------------------------------- construction helpers


def coerce(value: Any) -> Any:
    """Convert convenient Python values to AST values."""
    if isinstance(value, (Dict, List, Tokens, Word, Str, Dimensions, Raw)):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite number in dictionary")
        return value
    if isinstance(value, str):
        return Word(value) if _is_word(value) else Str(value)
    if isinstance(value, tuple):
        return List([coerce(v) for v in value])
    if isinstance(value, list):
        return List([coerce(v) for v in value])
    if isinstance(value, dict):
        return Dict(value)
    if hasattr(value, "tolist"):
        return coerce(value.tolist())
    raise TypeError(f"cannot convert {type(value).__name__} to an OpenFOAM value")


def _is_word(s: str) -> bool:
    return bool(_WORD_RE.match(s)) and _balanced(s)


def _balanced(s: str) -> bool:
    d = 0
    for c in s:
        if c == "(":
            d += 1
        elif c == ")":
            d -= 1
            if d < 0:
                return False
    return d == 0


def W(s: str) -> Word:
    return Word(s)


def S(s: str) -> Str:
    return Str(s)


def T(*items: Any) -> Tokens:
    """Multi-token value: T('uniform', (0, 0, 0)) -> ``uniform (0 0 0)``."""
    return Tokens([coerce(i) for i in items])


def L(*items: Any) -> List:
    return List([coerce(i) for i in items])


def clean(x: float, digits: int = 12) -> float:
    """Round computed values to ``digits`` significant figures (removes binary noise such
    as 0.31999999999999995 so generated dictionaries are readable and stable)."""
    x = float(x)
    if x == 0.0 or not math.isfinite(x):
        return 0.0 if x == 0.0 else x
    return float(f"{x:.{digits}g}")


def vec(v) -> List:
    return List([clean(x) for x in v])


def uniform(value: Any) -> Tokens:
    if isinstance(value, (list, tuple)) or hasattr(value, "tolist"):
        return Tokens([Word("uniform"), vec(value)])
    if isinstance(value, float):
        value = clean(value)
    return Tokens([Word("uniform"), coerce(value)])


def dims(*e: float) -> Dimensions:
    return Dimensions(tuple(e))


def deep_copy(node: Any) -> Any:
    if isinstance(node, Dict):
        d = Dict()
        for it in node.items:
            if isinstance(it, Entry):
                d.items.append(Entry(it.key, deep_copy(it.value)))
            else:
                d.items.append(Directive(it.name, deep_copy(it.arg)))
        return d
    if isinstance(node, List):
        return List([deep_copy(i) for i in node.items])
    if isinstance(node, Tokens):
        return Tokens([deep_copy(i) for i in node.items])
    return node


# ------------------------------------------------------------------------- JSON encoding


def to_json(node: Any) -> Any:
    if isinstance(node, bool) or isinstance(node, (int, float)) or node is None:
        return node
    if isinstance(node, Word):
        return {"type": "word", "value": node.value}
    if isinstance(node, Str):
        return {"type": "string", "value": node.value}
    if isinstance(node, Dimensions):
        return {"type": "dims", "value": list(node.exps)}
    if isinstance(node, Raw):
        return {"type": "raw", "text": node.text}
    if isinstance(node, List):
        return {"type": "list", "items": [to_json(i) for i in node.items]}
    if isinstance(node, Tokens):
        return {"type": "tokens", "items": [to_json(i) for i in node.items]}
    if isinstance(node, Dict):
        out = []
        for it in node.items:
            if isinstance(it, Entry):
                out.append({"key": it.key, "value": to_json(it.value)})
            else:
                out.append({"directive": it.name, "arg": to_json(it.arg)})
        return {"type": "dict", "entries": out}
    raise TypeError(type(node))


def from_json(obj: Any) -> Any:
    """Inverse of :func:`to_json`; also accepts plain JSON (str/list/dict/number)."""
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return coerce(obj)
    if isinstance(obj, list):
        return List([from_json(i) for i in obj])
    if isinstance(obj, dict):
        t = obj.get("type")
        if t == "word":
            return Word(obj["value"])
        if t == "string":
            return Str(obj["value"])
        if t == "dims":
            return Dimensions(tuple(obj["value"]))
        if t == "raw":
            return Raw(obj["text"])
        if t == "list":
            return List([from_json(i) for i in obj["items"]])
        if t == "tokens":
            return Tokens([from_json(i) for i in obj["items"]])
        if t == "dict":
            d = Dict()
            for e in obj["entries"]:
                if "directive" in e:
                    d.items.append(Directive(e["directive"], from_json(e.get("arg"))))
                else:
                    d.items.append(Entry(e["key"], from_json(e["value"])))
            return d
        return Dict({k: from_json(v) for k, v in obj.items()})
    raise TypeError(type(obj))


def walk(node: Any):
    """Yield every node in the tree (depth-first)."""
    yield node
    if isinstance(node, Dict):
        for it in node.items:
            if isinstance(it, Entry):
                yield from walk(it.value)
            else:
                yield it
                if it.arg is not None:
                    yield from walk(it.arg)
    elif isinstance(node, (List, Tokens)):
        for i in node.items:
            yield from walk(i)
