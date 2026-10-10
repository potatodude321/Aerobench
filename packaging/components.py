"""Large parts of an AeroBench installation that rarely change between versions — the Python
runtime, OpenFOAM and ParaView — installed and updated as separate components.

The build (``packaging/make_components.py``) packs each one into its own archive and writes a
manifest of its files. The setup embeds the archives and, when it updates an existing
installation, replaces a component only if it differs from the installed one; unchanged
components (hundreds of megabytes to gigabytes) are kept as they are.

Run by the setup with the installation's own Python (standard library only)::

    python -I components.py verify [--quick] <resources-folder> <component>

``--quick``  every file of the manifest exists with the recorded size (seconds; used for a
             component whose marker says it is this version, and after extraction).
full         additionally, every file has the recorded SHA-256 and the folder holds nothing
             else apart from compiled Python caches and files the setup itself creates (used
             to adopt a component installed by a version without markers).

Exit codes: 0 identical, 1 different or incomplete, 2 cannot check (no manifest, bad input).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

FORMAT = 1
MARKER = ".aerobench-component"
CHUNK = 1 << 20


def manifest_path(resources: Path, name: str) -> Path:
    return resources / "tools" / "components" / f"{name}.json"


def is_ignored(rel: str) -> bool:
    """Files that never belong to a component's content: Python byte-code caches (compiled at
    install time) and the setup's marker."""
    parts = rel.split("/")
    return "__pycache__" in parts or parts[-1] == MARKER or rel.endswith((".pyc", ".pyo"))


def iter_files(root: Path) -> Iterator[tuple[str, Path]]:
    """(relative path with '/' separators, path) of every regular file under root, sorted."""
    out: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            if not is_ignored(rel):
                out.append((rel, p))
    out.sort(key=lambda t: t[0])
    yield from out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def component_id(name: str, directory: str, files: Iterable[list]) -> str:
    """Content identity: name, folder, and every file's path, size and hash."""
    h = hashlib.sha256()
    h.update(json.dumps([FORMAT, name, directory], separators=(",", ":")).encode())
    for rel, size, digest in files:
        h.update(f"\n{rel}\0{size}\0{digest}".encode())
    return h.hexdigest()[:20]


def verify(resources: Path, name: str, quick: bool) -> tuple[int, list[str]]:
    mp = manifest_path(resources, name)
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return 2, [f"no readable manifest {mp}: {exc}"]
    if man.get("format") != FORMAT:
        return 2, [f"unsupported manifest format {man.get('format')}"]
    root = resources / man["dir"]
    if not root.is_dir():
        return 1, [f"{root} is missing"]
    problems: list[str] = []
    # sizes first: a different version is almost always found here, without reading any data
    for rel, size, _ in man["files"]:
        try:
            st = (root / rel).stat()
        except OSError:
            problems.append(f"missing {rel}")
        else:
            if st.st_size != size:
                problems.append(f"size of {rel}: {st.st_size} instead of {size}")
        if len(problems) >= 20:
            return 1, problems
    if problems or quick:
        return (1 if problems else 0), problems
    known = {rel for rel, _, _ in man["files"]}
    allowed = set(man.get("allowed_extra", []))
    for rel, _ in iter_files(root):
        if rel not in known and rel not in allowed:
            problems.append(f"not part of this version: {rel}")
            if len(problems) >= 20:
                return 1, problems
    # hashing releases the GIL: a few threads keep a fast disk busy
    from concurrent.futures import ThreadPoolExecutor

    files = man["files"]
    with ThreadPoolExecutor(max_workers=min(8, (os.cpu_count() or 2))) as pool:
        for (rel, _, digest), got in zip(files, pool.map(lambda f: sha256_file(root / f[0]), files)):
            if got != digest:
                problems.append(f"content of {rel} differs")
                if len(problems) >= 20:
                    break
    return (1 if problems else 0), problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if len(args) != 3 or args[0] != "verify" or flags - {"--quick"}:
        print(__doc__, file=sys.stderr)
        return 2
    _, resources, name = args
    try:
        code, problems = verify(Path(resources), name, "--quick" in flags)
    except Exception as exc:  # noqa: BLE001 - the setup then installs the component afresh
        print(f"{name}: could not be checked: {exc}")
        return 2
    if code == 0:
        print(f"{name}: identical to this version")
    else:
        print(f"{name}: " + ("differs from this version" if code == 1 else "cannot be checked"))
        for p in problems[:10]:
            print("  " + p)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
