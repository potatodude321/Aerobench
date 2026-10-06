"""Filesystem helpers: atomic writes, hashing, safe names."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (temp file + fsync + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        # os.replace is atomic on POSIX and on Windows (same volume).  On Windows a reader
        # holding the file open without FILE_SHARE_DELETE can make replace fail; retry.
        _replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _replace_with_retry(src: str, dst: Path, attempts: int = 20) -> None:
    import time

    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05 * (i + 1))


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def canonical_json(obj: Any) -> str:
    """Canonical JSON used for hashing: sorted keys, no whitespace, finite floats."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def atomic_write_json(path: Path, obj: Any, indent: int = 2) -> None:
    atomic_write_text(path, json.dumps(obj, indent=indent, sort_keys=False, ensure_ascii=False) + "\n")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_json(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_tree(root: Path, include: Iterable[str] | None = None) -> str:
    """Stable hash of a directory tree (relative paths + content)."""
    root = Path(root)
    h = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file())
    for p in files:
        rel = p.relative_to(root).as_posix()
        if include is not None and not any(Path(rel).match(pat) for pat in include):
            continue
        h.update(rel.encode())
        h.update(b"\0")
        h.update(sha256_file(p).encode())
        h.update(b"\n")
    return h.hexdigest()


_FOLDER_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def project_folder_name(name: str) -> str:
    """Folder name for a new project.  Characters invalid on Windows are replaced; on
    Linux/macOS whitespace is also replaced because OpenFOAM there rejects spaces in paths
    (the native Windows build accepts them)."""
    s = _FOLDER_BAD.sub("_", name.strip()).rstrip(". ")
    if os.name != "nt":
        s = re.sub(r"\s+", "_", s)
    return s or "project"


def safe_name(name: str, fallback: str = "item") -> str:
    """OpenFOAM-safe identifier (word): letters, digits, underscore; must not start with digit."""
    s = _SAFE.sub("_", name.strip()).strip("._-")
    s = s.replace("-", "_").replace(".", "_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = f"{fallback}_{s}"
    return s


def link_or_copy(src: Path, dst: Path) -> str:
    """Hard-link ``src`` to ``dst`` when possible (same volume), otherwise copy.

    Returns "link" or "copy".
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def free_disk_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free
