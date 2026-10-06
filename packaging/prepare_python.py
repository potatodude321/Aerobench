#!/usr/bin/env python3
"""Prepare the embedded Python runtime that ships inside the Windows installer.

Runs on Windows or Linux (cross-installs Windows wheels):

    python packaging/prepare_python.py --python-version 3.12.10 [--cad] [--sha256 <hash>]

Result (``packaging/build``):

    python/                    official embeddable CPython (amd64) with site-packages enabled
      Lib/site-packages/       aerobench engine + dependencies (win_amd64 wheels only)
    THIRD-PARTY-LICENSES.txt   licence texts of everything installed into the runtime
    wheels/                    the engine wheel that was installed

The download is verified against ``--sha256`` when given (recommended for release builds;
python.org publishes the digests on the release page).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def download(url: str, dest: Path, sha256: str | None) -> None:
    if not dest.exists():
        print(f"downloading {url}", flush=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if sha256:
        if digest.lower() != sha256.lower():
            dest.unlink()
            raise SystemExit(f"SHA-256 mismatch for {dest.name}: got {digest}, expected {sha256}")
        print(f"verified {dest.name} ({digest})")
    else:
        print(f"WARNING: {dest.name} not verified; SHA-256 = {digest}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-version", default="3.12.10")
    ap.add_argument("--sha256", default=None, help="expected SHA-256 of the embeddable zip")
    ap.add_argument("--cad", action="store_true", help="include OpenCASCADE (cadquery-ocp) for STEP/IGES import")
    ap.add_argument("--out", default=str(ROOT / "packaging" / "build"))
    args = ap.parse_args()

    out = Path(args.out)
    py_dir = out / "python"
    wheels = out / "wheels"
    cache = out / "cache"
    for d in (out, wheels, cache):
        d.mkdir(parents=True, exist_ok=True)
    if py_dir.exists():
        shutil.rmtree(py_dir)

    ver = args.python_version
    major_minor = "".join(ver.split(".")[:2])  # "312"
    zip_path = cache / f"python-{ver}-embed-amd64.zip"
    download(f"https://www.python.org/ftp/python/{ver}/python-{ver}-embed-amd64.zip", zip_path, args.sha256)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(py_dir)

    # enable site-packages in the embeddable distribution
    pth = py_dir / f"python{major_minor}._pth"
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() != "#import site"]
    for extra in ("Lib\\site-packages", "import site"):
        if extra not in lines:
            lines.append(extra)
    pth.write_text("\r\n".join(lines) + "\r\n")
    site = py_dir / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)

    # engine wheel (pure Python) + Windows wheels of every dependency
    for old in wheels.glob("aerobench-*.whl"):
        old.unlink()
    run([sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(wheels), str(ROOT / "engine")])
    engine_whl = next(wheels.glob("aerobench-*.whl"))
    # pip evaluates environment markers for the *running* interpreter, so Windows-only
    # dependencies of dependencies (colorama for click) are listed explicitly.
    reqs = [str(engine_whl), "colorama"] + (["cadquery-ocp>=7.7"] if args.cad else [])
    run([sys.executable, "-m", "pip", "install", "--no-compile", "--target", str(site), "--platform", "win_amd64",
         "--python-version", ".".join(ver.split(".")[:2]), "--implementation", "cp", "--only-binary=:all:", "--upgrade", *reqs])

    for stray in site.glob("*.whl"):
        stray.unlink()

    # licences
    run([sys.executable, str(ROOT / "packaging" / "licenses.py"), "--site-packages", str(site), "--node-modules",
         str(ROOT / "desktop" / "node_modules"), "--out", str(out / "THIRD-PARTY-LICENSES.txt")])
    print(f"embedded runtime ready in {py_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
