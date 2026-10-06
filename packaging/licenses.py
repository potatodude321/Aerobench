#!/usr/bin/env python3
"""Collect licence texts for everything shipped in the installer.

* Python: every ``*.dist-info`` in the embedded runtime's site-packages (``METADATA``
  licence fields and the files under ``licenses/`` or named LICENSE*/COPYING*/NOTICE*).
* JavaScript: production dependencies of the desktop app (``npm ls --omit=dev`` tree when
  available, otherwise the direct dependencies), with their LICENSE files.

Packages whose licence cannot be determined are listed at the top so a release cannot
silently ship an unknown licence.
"""

from __future__ import annotations

import argparse
import email
import json
import subprocess
from pathlib import Path

LICENSE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")


def python_packages(site: Path) -> list[dict]:
    out = []
    for di in sorted(site.glob("*.dist-info")):
        meta_p = di / "METADATA"
        if not meta_p.exists():
            continue
        meta = email.message_from_string(meta_p.read_text(encoding="utf-8", errors="replace"))
        lic = meta.get("License-Expression") or meta.get("License") or ""
        if not lic or len(lic) > 200:
            classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
            lic = "; ".join(classifiers) or (lic[:200] + "…" if lic else "")
        texts = []
        for f in sorted(di.rglob("*")):
            if f.is_file() and f.name.upper().startswith(LICENSE_NAMES):
                texts.append(f.read_text(encoding="utf-8", errors="replace"))
        out.append({"name": meta.get("Name"), "version": meta.get("Version"), "license": lic.strip(), "texts": texts})
    return out


def node_packages(nm: Path) -> list[dict]:
    names: set[str] = set()
    try:
        r = subprocess.run(["npm", "ls", "--omit=dev", "--all", "--json"], cwd=nm.parent, capture_output=True, text=True, timeout=300)
        tree = json.loads(r.stdout or "{}")

        def walk(deps: dict) -> None:
            for n, d in (deps or {}).items():
                if n not in names:
                    names.add(n)
                    walk(d.get("dependencies") or {})

        walk(tree.get("dependencies") or {})
    except Exception:  # noqa: BLE001 - fall back to direct dependencies
        pkg = json.loads((nm.parent / "package.json").read_text())
        names = set(pkg.get("dependencies", {}))
    names.add("electron")  # shipped runtime (dev dependency at build time)
    out = []
    for n in sorted(names):
        d = nm / n
        pj = d / "package.json"
        if not pj.exists():
            continue
        info = json.loads(pj.read_text(encoding="utf-8"))
        lic = info.get("license") or ""
        if not lic and isinstance(info.get("licenses"), list) and info["licenses"]:
            lic = info["licenses"][0].get("type", "")
        texts = [f.read_text(encoding="utf-8", errors="replace") for f in sorted(d.iterdir()) if f.is_file() and f.name.upper().startswith(LICENSE_NAMES)]
        out.append({"name": n, "version": info.get("version"), "license": lic if isinstance(lic, str) else str(lic), "texts": texts})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site-packages", required=True)
    ap.add_argument("--node-modules", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    py = python_packages(Path(a.site_packages))
    js = node_packages(Path(a.node_modules)) if Path(a.node_modules).exists() else []
    unknown = [f"{p['name']} {p['version']}" for p in py + js if not p["license"] and not p["texts"]]
    lines = ["THIRD-PARTY SOFTWARE LICENCES", "=" * 30, ""]
    lines += ["OpenFOAM, ParaView, Microsoft MPI and Gmsh are not included; they are installed separately by the user.", ""]
    if unknown:
        lines += ["LICENCE NOT DETERMINED (review before release):"] + [f"  - {u}" for u in unknown] + [""]
    for title, pkgs in (("Python runtime packages", py), ("Desktop (JavaScript) packages", js)):
        lines += [title, "-" * len(title)]
        lines += [f"{p['name']} {p['version']} — {p['license'] or 'see text'}" for p in pkgs]
        lines.append("")
    for p in py + js:
        for t in p["texts"]:
            lines += ["", "=" * 78, f"{p['name']} {p['version']}", "=" * 78, t.strip()]
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(py)} Python and {len(js)} JavaScript packages; {len(unknown)} without a determined licence -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
