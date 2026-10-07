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

# Native libraries vendored inside binary wheels (delvewheel ``*.libs`` folders) carry no
# dist-info of their own.  Each known library is listed with the licence it is used under
# (where a library is dual-licensed, the permissive option); an unknown DLL is reported as
# undetermined so a release cannot ship it unnoticed.
NATIVE_LIBS = [
    ("TK", "Open CASCADE Technology (OCCT)", "LGPL-2.1 with the Open CASCADE Exception 1.0. Dynamically loaded; the DLLs may be "
     "replaced by the user. Source code: https://github.com/Open-Cascade-SAS/OCCT (release matching the bundled OCP version)."),
    ("FreeImage", "FreeImage", "FreeImage Public License 1.0 (used under FIPL; dual-licensed with GPL)."),
    ("raw", "LibRaw", "LGPL-2.1 (dual-licensed with CDDL-1.0); dynamically loaded. Source: https://www.libraw.org."),
    ("OpenEXR", "OpenEXR", "BSD-3-Clause"), ("Iex", "OpenEXR Iex", "BSD-3-Clause"), ("IlmThread", "OpenEXR IlmThread", "BSD-3-Clause"),
    ("Imath", "Imath", "BSD-3-Clause"),
    ("freetype", "FreeType", "FreeType License (used under FTL; dual-licensed with GPL-2.0). Portions of this software are "
     "copyright (c) The FreeType Project (www.freetype.org). All rights reserved."),
    ("jpeg", "libjpeg", "IJG License. This software is based in part on the work of the Independent JPEG Group."),
    ("lcms2", "Little CMS", "MIT"), ("liblzma", "XZ Utils liblzma", "0BSD / public domain"), ("libpng", "libpng", "PNG Reference Library License v2"),
    ("libwebp", "libwebp", "BSD-3-Clause"), ("libsharpyuv", "libwebp sharpyuv", "BSD-3-Clause"),
    ("openjp2", "OpenJPEG", "BSD-2-Clause"), ("openjph", "OpenJPH", "BSD-2-Clause"), ("tiff", "LibTIFF", "libtiff License (BSD-like)"),
    ("Lerc", "LERC", "Apache-2.0"), ("deflate", "libdeflate", "MIT"), ("zlib", "zlib", "zlib License"), ("zstd", "Zstandard", "BSD-3-Clause"),
    ("libscipy_openblas", "OpenBLAS (scipy-openblas build)", "BSD-3-Clause; the full texts of OpenBLAS and its compiler runtime "
     "(GCC Runtime Library Exception) are part of the numpy/scipy licence files below."),
    ("msvcp140", "Microsoft Visual C++ runtime", "Microsoft Visual C++ Redistributable (distributable code)"),
    ("vcruntime140", "Microsoft Visual C++ runtime", "Microsoft Visual C++ Redistributable (distributable code)"),
    ("vcomp140", "Microsoft OpenMP runtime", "Microsoft Visual C++ Redistributable (distributable code)"),
]


def native_libraries(site: Path) -> list[dict]:
    import re

    out: dict[str, dict] = {}
    for libdir in sorted(site.glob("*.libs")):
        for f in sorted(libdir.iterdir()):
            if f.suffix.lower() not in (".dll", ".so", ".dylib") and ".so." not in f.name:
                continue
            stem = re.sub(r"-[0-9a-f]{32}$", "", f.name.split(".")[0])
            hit = next((n for n in NATIVE_LIBS if stem.startswith(n[0])), None)
            key = hit[1] if hit else stem
            entry = out.setdefault(key, {"name": key, "license": hit[2] if hit else "", "dirs": [], "files": 0})
            entry["files"] += 1
            if libdir.name not in entry["dirs"]:
                entry["dirs"].append(libdir.name)
    for e in out.values():
        e["bundled_in"] = ", ".join(e.pop("dirs"))
    return list(out.values())


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
    ap.add_argument("--bundle", default=None, help="packaging/build/bundle (bundled applications) if present")
    a = ap.parse_args()
    py = python_packages(Path(a.site_packages))
    native = native_libraries(Path(a.site_packages))
    js = node_packages(Path(a.node_modules)) if Path(a.node_modules).exists() else []
    unknown = [f"{p['name']} {p['version']}" for p in py + js if not p["license"] and not p["texts"]]
    unknown += [f"native library {n['name']} (in {n['bundled_in']})" for n in native if not n["license"]]
    lines = ["THIRD-PARTY SOFTWARE LICENCES", "=" * 30, ""]
    bundle = Path(a.bundle) if a.bundle and (Path(a.bundle) / "bundle.json").is_file() else None
    if bundle is not None:
        comp = json.loads((bundle / "bundle.json").read_text(encoding="utf-8")).get("components", {})
        lines += ["Bundled applications (separate programs, run as external processes)",
                  "-------------------------------------------------------------------"]
        if "openfoam" in comp:
            lines += [f"OpenFOAM {comp['openfoam']['version']} for Windows (OpenCFD Ltd) — GPL-3.0-or-later. Unmodified binaries; "
                      "licence text in resources\\licenses\\OpenFOAM\\GPL-3.0.txt. Source code: see resources\\licenses\\SOURCE-OFFER.txt."]
        if "paraview" in comp:
            lines += [f"ParaView {comp['paraview']['version']} (Kitware) — BSD-3-Clause; the licences of ParaView and every library it "
                      f"bundles (including Qt 6, LGPL-3.0) are in resources\\{comp['paraview']['path']}\\share\\licenses."]
        if "msmpi" in comp:
            lines += [f"Microsoft MPI {comp['msmpi']['version']} redistributable installer — MIT licence "
                      "(resources\\licenses\\MS-MPI); installed system-wide by the setup."]
        lines.append("")
        for rel in ("licenses/SOURCE-OFFER.txt", "licenses/MS-MPI/LICENSE.txt", "licenses/MS-MPI/COPYRIGHT-mpich.txt", "licenses/OpenFOAM/GPL-3.0.txt"):
            f = bundle / rel
            if f.is_file():
                lines += ["=" * 78, rel, "=" * 78, f.read_text(encoding="utf-8", errors="replace").strip(), ""]
    else:
        lines += ["OpenFOAM, ParaView, Microsoft MPI and Gmsh are not included; they are installed separately by the user.", ""]
    if unknown:
        lines += ["LICENCE NOT DETERMINED (review before release):"] + [f"  - {u}" for u in unknown] + [""]
    for title, pkgs in (("Python runtime packages", py), ("Desktop (JavaScript) packages", js)):
        lines += [title, "-" * len(title)]
        lines += [f"{p['name']} {p['version']} — {p['license'] or 'see text'}" for p in pkgs]
        lines.append("")
        if title.startswith("Python") and native:
            sub_title = "Native libraries bundled inside Python wheels"
            lines += [sub_title, "-" * len(sub_title)]
            lines += [f"{n['name']} (in {n['bundled_in']}, {n['files']} file(s)) — {n['license'] or 'NOT DETERMINED'}" for n in native]
            lines.append("")
    for p in py + js:
        for t in p["texts"]:
            lines += ["", "=" * 78, f"{p['name']} {p['version']}", "=" * 78, t.strip()]
    Path(a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(py)} Python and {len(js)} JavaScript packages; {len(unknown)} without a determined licence -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
