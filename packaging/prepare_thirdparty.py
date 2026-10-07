#!/usr/bin/env python3
"""Stage the third-party applications that ship inside the Windows installer.

    python packaging/prepare_thirdparty.py [--cache DIR] [--out DIR] [--no-paraview]

Downloads (pinned by SHA-256, cached), unpacks and trims:

* **OpenFOAM v2606** for Windows (OpenCFD MinGW build, GPL-3.0): the ``OpenFOAM-v2606``
  tree from the official installer — executables, libraries and ``etc`` — without the
  MSYS2 terminal environment and the tutorials (not used by AeroBench, ~0.5 GB).
* **Microsoft MPI 10.1.3** redistributable installer (MIT; "installers for MS-MPI may be
  redistributed with your own applications"), run silently by the AeroBench setup.
* **ParaView 6.2.0** (Kitware binaries, BSD-3-Clause + bundled third-party licences).
  The Windows ZIP stores ~1000 DLLs up to four times (``bin`` and three Python package
  folders), 2.2 GB of identical copies.  Copies are removed here and recreated as NTFS
  hard links by the setup (``tools/restore_links.py``), so the installed tree is exactly
  Kitware's layout.  Also removed: the bundled ``uv`` package manager and the NVIDIA IndeX
  plugin (290 MB, needs an NVIDIA IndeX licence and GPU; its plugin entry is dropped).

Results (``--out``, default ``packaging/build``):

    bundle/                 copied by electron-builder into <install>/resources
      openfoam/OpenFOAM-v2606/...
      ParaView-6.2.0/...
      redist/msmpisetup.exe
      tools/restore_links.py, tools/paraview-links.json
      licenses/...          OpenFOAM GPL text, MS-MPI licence, source offer
      bundle.json           components, versions and source hashes
    sources/                GPL Corresponding Source, published next to the installer
      OpenFOAM-v2606.tgz, ThirdParty-v2606.tar.gz, SOURCE-OFFER.txt

Unpacking the OpenFOAM installer (NSIS) needs the full 7-Zip (``7z``; on Windows
"C:\\Program Files\\7-Zip\\7z.exe"); the 7za that ships with electron-builder cannot read
NSIS archives.
"""

from __future__ import annotations

import argparse
import filecmp
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OPENFOAM_VERSION = "v2606"
PARAVIEW_VERSION = "6.2.0"
MSMPI_VERSION = "10.1.3 (10.1.12498.52)"

DOWNLOADS = {
    "openfoam_installer": {
        "file": "OpenFOAM-v2606-windows-mingw.exe",
        "url": "https://dl.openfoam.com/source/v2606/OpenFOAM-v2606-windows-mingw.exe",
        "sha256": "020321d58b42e1d2ad0b616cdde17f92503608edd902388fb8c73e94e9cb1e25",
    },
    "openfoam_source": {
        "file": "OpenFOAM-v2606.tgz",
        "url": "https://dl.openfoam.com/source/v2606/OpenFOAM-v2606.tgz",
        "sha256": "2a1310e3ed192cc4c521e1d22dcc176f57bec61160c878dc4348f21d6672294d",
    },
    "thirdparty_source": {
        "file": "ThirdParty-v2606.tar.gz",
        "url": "https://dl.openfoam.com/source/v2606/ThirdParty-v2606.tar.gz",
        "sha256": "3c7ccd88c5698a9c77a636b01f26e35d7042f15f14c2364cfd2d403030bf3f4a",
    },
    "msmpi": {
        "file": "msmpisetup-10.1.3.exe",
        "url": "https://download.microsoft.com/download/7/2/7/72731ebb-b63c-4170-ade7-836966263a8f/msmpisetup.exe",
        "sha256": "47443829114d8d8670f77af98939fe876d33eceb35d0ce4e0e85efeec4d87213",
    },
    "paraview": {
        "file": "ParaView-6.2.0-Windows-Python3.12-msvc2017-AMD64.zip",
        "url": "https://www.paraview.org/files/v6.2/ParaView-6.2.0-Windows-Python3.12-msvc2017-AMD64.zip",
        "sha256": "f5533d36ac974a4c7597194bd9f516749b37a14b2643d37c52a95c70ad0f1fe9",
    },
}

# ParaView items that are removed (not merely deduplicated)
PARAVIEW_REMOVE = ["bin/uv.exe", "bin/libnvindex.dll", "bin/paraview-6.2/plugins/pvNVIDIAIndeX", "kernels_nvidia_index"]
PARAVIEW_REMOVED_PLUGINS = ["pvNVIDIAIndeX"]


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(key: str, cache: Path) -> Path:
    d = DOWNLOADS[key]
    dest = cache / d["file"]
    if not dest.exists():
        log(f"downloading {d['url']}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(d["url"], timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        tmp.replace(dest)
    digest = sha256(dest)
    if digest != d["sha256"]:
        bad = dest.with_suffix(dest.suffix + ".bad")
        dest.replace(bad)
        raise SystemExit(f"SHA-256 mismatch for {dest.name}: got {digest}, expected {d['sha256']} (moved to {bad.name})")
    log(f"verified {dest.name}")
    return dest


def find_7z() -> str:
    for name in ("7z", "7z.exe"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if Path(p).exists():
            return p
    raise SystemExit("7-Zip (7z) is required to unpack the OpenFOAM installer; install it from https://www.7-zip.org")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


# ---------------------------------------------------------------------------- OpenFOAM
def stage_openfoam(installer: Path, dest_root: Path, licenses: Path) -> dict:
    seven = find_7z()
    tree = f"msys64/home/ofuser/OpenFOAM/OpenFOAM-{OPENFOAM_VERSION}"
    with tempfile.TemporaryDirectory(dir=dest_root.parent) as td:
        td = Path(td)
        run([seven, "x", "-y", f"-o{td}", str(installer), "msys64.7z", "licenses", "README.txt"])
        run([seven, "x", "-y", f"-o{td}", str(td / "msys64.7z"), f"{tree}/*", f"-x!{tree}/tutorials"])
        src = td / tree
        bin_dir = src / "platforms" / "win64MingwDPInt32Opt" / "bin"
        for required in ("simpleFoam.exe", "snappyHexMesh.exe", "blockMesh.exe", "libOpenFOAM.dll", "libPstream.dll"):
            if not (bin_dir / required).exists():
                raise SystemExit(f"unexpected OpenFOAM installer layout: {required} missing in {bin_dir}")
        if not filecmp.cmp(bin_dir / "libPstream.dll", bin_dir / "libPstream.dll-msmpi", shallow=False):
            raise SystemExit("libPstream.dll is not the MS-MPI variant: the bundled MPI setup would not match")
        target = dest_root / f"OpenFOAM-{OPENFOAM_VERSION}"
        if target.exists():
            shutil.rmtree(target)
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(target))
        (licenses / "OpenFOAM").mkdir(parents=True, exist_ok=True)
        shutil.copy2(td / "licenses" / "License.txt", licenses / "OpenFOAM" / "GPL-3.0.txt")
        shutil.copy2(td / "README.txt", licenses / "OpenFOAM" / "README-OpenCFD-Windows.txt")
    n_exe = len(list((target / "platforms" / "win64MingwDPInt32Opt" / "bin").glob("*.exe")))
    return {"version": OPENFOAM_VERSION, "path": f"openfoam/OpenFOAM-{OPENFOAM_VERSION}", "executables": n_exe,
            "build": "OpenCFD win64MingwDPInt32Opt (MinGW cross-compiled), libPstream linked to MS-MPI"}


# ---------------------------------------------------------------------------- ParaView
def stage_paraview(zip_path: Path, dest_root: Path, tools: Path) -> dict:
    target = dest_root / f"ParaView-{PARAVIEW_VERSION}"
    if target.exists():
        shutil.rmtree(target)
    with tempfile.TemporaryDirectory(dir=dest_root) as td:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(td)
        tops = [p for p in Path(td).iterdir() if p.is_dir()]
        if len(tops) != 1:
            raise SystemExit("unexpected ParaView archive layout")
        shutil.move(str(tops[0]), str(target))
    removed = 0
    for rel in PARAVIEW_REMOVE:
        p = target / rel
        if p.is_dir():
            removed += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            shutil.rmtree(p)
        elif p.exists():
            removed += p.stat().st_size
            p.unlink()
    xml = target / "bin" / "paraview-6.2" / "plugins" / "paraview.plugins.xml"
    if xml.exists():
        text = xml.read_text(encoding="utf-8")
        for name in PARAVIEW_REMOVED_PLUGINS:
            text = re.sub(rf'\s*<Plugin name="{re.escape(name)}"[^>]*/>', "", text)
            text = re.sub(rf'\s*<Plugin name="{re.escape(name)}"[^>]*>.*?</Plugin>', "", text, flags=re.S)
        xml.write_text(text, encoding="utf-8")
    # deduplicate: copies of bin/*.dll anywhere below bin/Lib become hard links at install time
    bin_dlls = {p.name.lower(): p for p in (target / "bin").glob("*.dll")}
    links: list[list[str]] = []
    saved = 0
    for p in sorted((target / "bin" / "Lib").rglob("*.dll")):
        src = bin_dlls.get(p.name.lower())
        if src is None or src.stat().st_size != p.stat().st_size or not filecmp.cmp(src, p, shallow=False):
            continue
        links.append([p.relative_to(target).as_posix(), src.relative_to(target).as_posix()])
        saved += p.stat().st_size
        p.unlink()
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "paraview-links.json").write_text(json.dumps({"root": target.name, "links": links}, indent=0), encoding="utf-8")
    for required in ("bin/pvpython.exe", "bin/paraview.exe", "share/licenses"):
        if not (target / required).exists():
            raise SystemExit(f"unexpected ParaView layout: {required} missing")
    return {"version": PARAVIEW_VERSION, "path": target.name, "hardlinks": len(links), "dedup_bytes": saved, "removed_bytes": removed,
            "removed": PARAVIEW_REMOVE}


# ---------------------------------------------------------------------------- sources (GPL)
SOURCE_OFFER = """AeroBench {app_version} — source code of the GPL software it ships
=====================================================================

The AeroBench installer contains OpenFOAM {of} for Windows, built by OpenCFD Ltd and
redistributed unmodified. OpenFOAM is free software under the GNU General Public License
version 3 (see resources\\licenses\\OpenFOAM\\GPL-3.0.txt in the installation). AeroBench runs
OpenFOAM as separate programs and exchanges only files and process I/O with it.

The Corresponding Source is distributed together with every AeroBench installer:

  OpenFOAM-{of}.tgz        SHA-256 {of_sha}
  ThirdParty-{of}.tar.gz   SHA-256 {tp_sha}

These are the source archives OpenCFD publishes for this release
(https://dl.openfoam.com/source/{of}/). The Windows build cross-compiles them with MinGW-w64
(configuration in OpenFOAM-{of}/etc-mingw and bin/tools/createMingwRuntime of the source). The MinGW runtime libraries
in the same folder are: GCC runtime (libgcc_s_seh-1, libstdc++-6: GPL-3.0 with the GCC Runtime
Library Exception), winpthreads (MIT/BSD), GMP 6.1.1, MPFR 3.1.4, MPC 1.0.2 (LGPL-3.0),
FFTW 3.3.10 (GPL-2.0-or-later). Their upstream sources are at https://gmplib.org,
https://www.mpfr.org, https://www.multiprecision.org, https://www.fftw.org and
https://gcc.gnu.org; on written request we provide these sources on a physical medium
for no more than the cost of distribution, for three years after the last distribution
of this version.

ParaView {pv} (BSD-3-Clause) is redistributed unmodified apart from removing the NVIDIA
IndeX plugin and the uv tool; the licences of ParaView and of the libraries it bundles,
including Qt 6 (LGPL-3.0) whose sources are available from https://download.qt.io, are in
resources\\ParaView-{pv}\\share\\licenses.

Microsoft MPI {mpi} is redistributed under its redistribution terms (MIT licence; see
resources\\licenses\\MS-MPI).
"""


def stage_sources(cache: Path, out: Path, app_version: str) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    res = {}
    for key in ("openfoam_source", "thirdparty_source"):
        src = fetch(key, cache)
        dst = out / src.name
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dst)
        res[key] = {"file": src.name, "sha256": DOWNLOADS[key]["sha256"]}
    text = SOURCE_OFFER.format(app_version=app_version, of=OPENFOAM_VERSION, pv=PARAVIEW_VERSION, mpi=MSMPI_VERSION,
                               of_sha=DOWNLOADS["openfoam_source"]["sha256"], tp_sha=DOWNLOADS["thirdparty_source"]["sha256"])
    (out / "SOURCE-OFFER.txt").write_text(text, encoding="utf-8")
    return res, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(ROOT / "packaging" / "cache"))
    ap.add_argument("--out", default=str(ROOT / "packaging" / "build"))
    ap.add_argument("--no-paraview", action="store_true", help="leave ParaView out (development builds only)")
    a = ap.parse_args()
    cache, out = Path(a.cache), Path(a.out)
    cache.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "engine" / "src"))
    from aerobench.version import APP_VERSION  # noqa: E402

    bundle = out / "bundle"
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / "redist").mkdir(parents=True)
    licenses = bundle / "licenses"
    tools = bundle / "tools"

    manifest: dict = {"app_version": APP_VERSION, "components": {}}
    manifest["components"]["openfoam"] = stage_openfoam(fetch("openfoam_installer", cache), bundle / "openfoam", licenses)
    msmpi = fetch("msmpi", cache)
    shutil.copy2(msmpi, bundle / "redist" / "msmpisetup.exe")
    (licenses / "MS-MPI").mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "packaging" / "licenses" / "MS-MPI-LICENSE.txt", licenses / "MS-MPI" / "LICENSE.txt")
    shutil.copy2(ROOT / "packaging" / "licenses" / "MS-MPI-COPYRIGHT-mpich.txt", licenses / "MS-MPI" / "COPYRIGHT-mpich.txt")
    manifest["components"]["msmpi"] = {"version": MSMPI_VERSION, "path": "redist/msmpisetup.exe", "sha256": DOWNLOADS["msmpi"]["sha256"],
                                       "install": "silent (-unattend) by the AeroBench setup when no MS-MPI is installed"}
    if not a.no_paraview:
        manifest["components"]["paraview"] = stage_paraview(fetch("paraview", cache), bundle, tools)
    tools.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "packaging" / "restore_links.py", tools / "restore_links.py")
    shutil.copy2(ROOT / "packaging" / "running.py", tools / "running.py")
    srcs, offer = stage_sources(cache, out / "sources", APP_VERSION)
    (licenses / "SOURCE-OFFER.txt").write_text(offer, encoding="utf-8")
    manifest["sources"] = srcs
    manifest["downloads"] = {k: {"url": v["url"], "sha256": v["sha256"]} for k, v in DOWNLOADS.items()}
    (bundle / "bundle.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    size = sum(f.stat().st_size for f in bundle.rglob("*") if f.is_file())
    log(f"bundle ready in {bundle} ({size / 1e9:.2f} GB before installer compression)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
