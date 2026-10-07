"""Recreate ParaView's duplicate DLLs after installation (run by the AeroBench setup).

    python -I restore_links.py <resources-folder>

``tools/paraview-links.json`` lists ``[copy, original]`` pairs relative to the ParaView
folder: identical files that the build removed to keep the installer small.  Each copy is
recreated as an NTFS hard link to the original (no extra disk space; same bytes, so
Kitware's layout is restored exactly) or, where hard links are not possible (e.g. FAT/exFAT
or network drives), as a real copy.  Idempotent: correct existing files are kept, so a
repair install can run it again.  Standard library only; exit code 0 = complete.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path


def same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def restore(resources: Path) -> tuple[int, int, list[str]]:
    spec = json.loads((resources / "tools" / "paraview-links.json").read_text(encoding="utf-8"))
    root = (resources / spec["root"]).resolve()
    linked = copied = 0
    errors: list[str] = []
    for rel_copy, rel_orig in spec["links"]:
        dst, src = (root / rel_copy).resolve(), (root / rel_orig).resolve()
        if root not in dst.parents or root not in src.parents:
            errors.append(f"refusing path outside ParaView: {rel_copy} -> {rel_orig}")
            continue
        if not src.is_file():
            errors.append(f"missing original {rel_orig}")
            continue
        if dst.exists():
            if same_file(dst, src) or dst.stat().st_size == src.stat().st_size:
                continue
            dst.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
            linked += 1
        except OSError:
            try:
                shutil.copy2(src, dst)
                copied += 1
            except OSError as exc:
                errors.append(f"{rel_copy}: {exc}")
    return linked, copied, errors


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: restore_links.py <resources-folder>", file=sys.stderr)
        return 64
    resources = Path(argv[0])
    if not (resources / "tools" / "paraview-links.json").is_file():
        print("nothing to restore (no ParaView in this installation)")
        return 0
    linked, copied, errors = restore(resources)
    report = f"ParaView files restored: {linked} hard links, {copied} copies, {len(errors)} errors"
    print(report)
    for e in errors[:50]:
        print("  " + e)
    try:
        (resources / "tools" / "restore_links.log").write_text(report + "\n" + "\n".join(errors) + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
