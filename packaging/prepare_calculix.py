#!/usr/bin/env python3
"""Stage CalculiX (ccx 2.23, the structural solver of the aero-element workflow) for the
Windows installer.

    python packaging/prepare_calculix.py [--cache DIR] [--out DIR]

The Windows build comes from conda-forge (calculix feedstock, MinGW-w64/gfortran, SPOOLES
direct solver built in, OpenBLAS + ARPACK); every package is pinned by file name and SHA-256.
Only the executable and the run-time DLLs are kept:

    bundle/calculix/ccx.exe + *.dll         installed to <install>/resources/calculix
    bundle/licenses/calculix/...            licence texts of every package
    sources/calculix/ccx_2.23.src.tar.bz2   GPL Corresponding Source (upstream, dhondt.de)

Licences: CalculiX GPL-2.0-or-later (run as a separate program); ARPACK and OpenBLAS
BSD-3-Clause; GCC run-time libraries GPL-3.0 with the GCC Runtime Library Exception;
winpthreads MIT/BSD; Microsoft Visual C++ run-time redistributable licence (vc14_runtime).
The package's bundle.json entry is merged into packaging/build/bundle/bundle.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CCX_VERSION = "2.23"
CONDA = "https://api.anaconda.org/download/conda-forge/{name}/{version}/win-64/{file}"
PACKAGES = [
    # name, version, file, sha256
    ("calculix", "2.23", "calculix-2.23-pl5321h7abefab_8.conda", "a6c1e75c092893d73d0bb3902cba9db7e14ba3fe4d2f8cd74cdfbc1bc041d1d1"),
    ("arpack", "3.9.1", "arpack-3.9.1-nompi_h1f7371d_102.conda", "393a0f11fe53c9a4ce8e003f0f9816ce098179d4c06eacffd573afbb0f2e2f0c"),
    ("libgcc", "16.2.0", "libgcc-16.2.0-h110b43a_7.conda", "59feb6d16cba13a70f22dae70a44670eb2cc45e652e9a34a014814c2ca871132"),
    ("libwinpthread", "12.0.0.r4.gg4f2fc60ca", "libwinpthread-12.0.0.r4.gg4f2fc60ca-h11686cb_11.conda",
     "988c8fe5fca9e3510fd0b5671d67ae42c1fd632f8bd28f832a6735970eb69a36"),
    ("libgfortran5", "16.2.0", "libgfortran5-16.2.0-h7c9de5e_7.conda", "e9b86fe14cb9938abd08f0b1685b04457339f71d9f00783fe9258a1eb0229963"),
    ("libgfortran", "16.2.0", "libgfortran-16.2.0-h719f0c7_7.conda", "8e0d3445b5bdde22b6dfc2a7766b9336e448b240ff2015cd2cc73ca85f9b2130"),
    ("libblas", "3.11.0", "libblas-3.11.0-11_h0adab6e_openblas.conda", "64bf8d49bdd385193c68b56b0a11a256f1d7fa5711edfc9e9c7bcbf2ca25f636"),
    ("liblapack", "3.11.0", "liblapack-3.11.0-11_hd232482_openblas.conda", "0021c607d065b309d5fd19b48f0ebaf94ef3a680feecc3a728ab15900746efa8"),
    ("libstdcxx", "16.2.0", "libstdcxx-16.2.0-hae5796f_7.conda", "5984f81dcb289446fb3cdb0117c0680bded57eaa98da193f705400ec94fc71ae"),
    ("libgomp", "16.2.0", "libgomp-16.2.0-h8ee18e1_7.conda", "4b8a747f047d069ccd2a6cf2f4fdbc6b0c0e4dcd840998d1fe607b8c234e707b"),
    ("libopenblas", "0.3.34", "libopenblas-0.3.34-pthreads_h952cde7_2.conda", "138056f68cf8f92cb1830fe1ed950d70856cc840f5f4b2569f9e849cb109bf7e"),
    ("vc14_runtime", "14.51.36247", "vc14_runtime-14.51.36247-habf1de7_41.conda", "4e4cb599cdc41bf2109d1464c127b5bcbddf548ce3e322e612afb691338b48f8"),
]
SOURCE = ("https://www.dhondt.de/ccx_2.23.src.tar.bz2", "ccx_2.23.src.tar.bz2")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fetch(url: str, dest: Path, digest: str | None) -> Path:
    if not dest.exists() or (digest and sha256(dest) != digest):
        print(f"downloading {url}", flush=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(dest)
    if digest and sha256(dest) != digest:
        dest.unlink()
        raise SystemExit(f"SHA-256 mismatch for {dest.name}")
    return dest


def conda_members(path: Path):
    """(member name, bytes) of the package payload and its info (licences) in a .conda file."""
    import zstandard  # build-time dependency: pip install zstandard

    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not n.endswith(".tar.zst"):
                continue
            raw = zstandard.ZstdDecompressor().stream_reader(z.open(n)).read()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as t:
                for m in t.getmembers():
                    if m.isfile():
                        yield m.name, t.extractfile(m).read()


def pe_imports(data: bytes) -> list[str]:
    """Names of the DLLs a PE file imports (import directory; case as written in the file
    is matched case-insensitively against the staged names by the caller's dictionary)."""
    import struct

    pe = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (112 if magic == 0x20B else 96)
    imp_rva = struct.unpack_from("<I", data, dd + 8)[0]
    secs = [struct.unpack_from("<8sIIII", data, opt + struct.unpack_from("<H", data, pe + 20)[0] + 40 * i) for i in range(nsec)]

    def off(rva: int) -> int:
        for _, vsize, va, rsize, raw in secs:
            if va <= rva < va + max(vsize, rsize):
                return rva - va + raw
        raise ValueError("rva outside sections")

    out = []
    # export forwarders ("openblas.dll.dgemm_"): conda's libblas/liblapack only forward to OpenBLAS
    import re

    for m in re.finditer(rb"([A-Za-z0-9_\-]+\.dll)\.[A-Za-z_]", data):
        name = m.group(1).decode("ascii")
        if name not in out:
            out.append(name)
    if not imp_rva:
        return out
    p = off(imp_rva)
    while True:
        name_rva = struct.unpack_from("<I", data, p + 12)[0]
        if name_rva == 0:
            break
        q = off(name_rva)
        name = data[q:data.index(b"\0", q)].decode("ascii", "replace")
        if name not in out:
            out.append(name)
        p += 20
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(ROOT / "packaging" / "build" / "cache" / "calculix"))
    ap.add_argument("--out", default=str(ROOT / "packaging" / "build"))
    a = ap.parse_args()
    cache, out = Path(a.cache), Path(a.out)
    cache.mkdir(parents=True, exist_ok=True)
    dest = out / "bundle" / "calculix"
    lic = out / "bundle" / "licenses" / "calculix"
    for d in (dest, lic):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    pkgs = []
    staged: dict[str, bytes] = {}
    for name, version, file, digest in PACKAGES:
        p = fetch(CONDA.format(name=name, version=version, file=file), cache / file, digest)
        kept = []
        for member, data in conda_members(p):
            low = member.lower()
            if low.startswith(("library/bin/", "library/mingw-w64/bin/")) and (low.endswith(".dll") or low.endswith("ccx.exe")):
                staged[Path(member).name] = data
                kept.append(Path(member).name)
            elif low.startswith("info/licenses/") or low == "info/license.txt" or low.startswith("library/share/licenses/"):
                rel = Path(member).relative_to("info") if low.startswith("info/") else Path(member).relative_to("Library/share")
                target = lic / name / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        pkgs.append({"name": name, "version": version, "file": file, "sha256": digest, "kept": sorted(kept)})
    if "ccx.exe" not in staged:
        raise SystemExit("ccx.exe was not found in the calculix package")
    # only what ccx.exe actually loads (transitively); system DLLs come from Windows
    needed, todo = set(), ["ccx.exe"]
    while todo:
        n = todo.pop()
        if n in needed:
            continue
        needed.add(n)
        todo += [d for d in pe_imports(staged[n]) if d in staged and d not in needed]
    for n in sorted(needed):
        (dest / n).write_bytes(staged[n])
    for pk in pkgs:
        pk["kept"] = sorted(set(pk["kept"]) & needed)
    src_dir = out / "sources" / "calculix"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = fetch(SOURCE[0], src_dir / SOURCE[1], None)
    (src_dir / "SOURCE.txt").write_text(
        "CalculiX CrunchiX (ccx) 2.23 is free software under the GNU General Public License, version 2 or later.\n"
        f"Upstream source: {SOURCE[0]} (SHA-256 {sha256(src)}).\n"
        "The Windows executable shipped with AeroBench is the conda-forge build "
        f"{PACKAGES[0][2]} (recipe: https://github.com/conda-forge/calculix-feedstock).\n", encoding="utf-8")
    manifest_path = out / "bundle" / "bundle.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"components": {}}
    manifest.setdefault("components", {})["calculix"] = {
        "version": CCX_VERSION, "path": "calculix/ccx.exe", "packages": pkgs,
        "source": {"url": SOURCE[0], "sha256": sha256(src)},
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    size = sum(f.stat().st_size for f in dest.iterdir())
    print(f"CalculiX {CCX_VERSION}: {len(list(dest.iterdir()))} files, {size / 1e6:.1f} MB in {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
