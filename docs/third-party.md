# Third-party components and data

AeroBench is proprietary. It **does not link to** OpenFOAM, ParaView, MPI, CalculiX or Gmsh:
they run as separate programs that exchange files and pipes with AeroBench (aggregation, not a
combined work). The Windows installer **redistributes** OpenFOAM, ParaView, CalculiX and the
Microsoft MPI installer unmodified (ParaView minus two optional parts), which carries
obligations listed below. Libraries that are linked into AeroBench are permissively licensed (or LGPL,
dynamically loaded). This page is an engineering inventory, not legal advice — have the
distribution reviewed before release.

## External programs (run as processes)

| Program | Licence | Shipped? | Use |
|---|---|---|---|
| OpenFOAM v2606 for Windows (OpenCFD MinGW build) | GPL-3.0-or-later | **yes**, in the installer (`resources\openfoam`) | Meshing and solving |
| Microsoft MPI 10.1.3 | MIT; "installers for MS-MPI may be redistributed with your own applications" | **yes**, its installer (`resources\redist`), run silently by the setup | Required by the Windows OpenFOAM build |
| ParaView 6.2.0 / pvpython (Kitware) | BSD-3-Clause + licences of its bundled libraries (incl. Qt 6, LGPL-3.0) | **yes** (`resources\ParaView-6.2.0`) | Post-processing sidecar, images, *Open in ParaView* |
| CalculiX CrunchiX 2.23 (G. Dhondt), conda-forge Windows build | GPL-2.0-or-later; runtime DLLs see below | **yes** (`resources\calculix`) | Structural check of aero elements (linear static shells) |
| OpenFOAM on Linux, Open MPI | GPL-3.0 / BSD-3-Clause-style | no (user-installed) | Alternative execution environments |
| Gmsh (optional) | GPL-2.0-or-later | no | Alternative CAD tessellation, executed as an external program only |

### Obligations that come with shipping them

* **OpenFOAM (GPL-3.0)** — the installer carries the GPL text
  (`resources\licenses\OpenFOAM\GPL-3.0.txt`) and `SOURCE-OFFER.txt`. The Corresponding
  Source (`OpenFOAM-v2606.tgz`, `ThirdParty-v2606.tar.gz`, as published by OpenCFD, SHA-256
  pinned in `packaging/prepare_thirdparty.py`) is produced by the build in
  `packaging\build\sources` and **must be published next to every installer download**
  (GPL-3.0 §6). The MinGW runtime libraries in the same folder (GCC runtime with the GCC
  Runtime Library Exception; GMP, MPFR, MPC under LGPL-3.0; FFTW under GPL-2.0-or-later) are
  covered by the written offer in `SOURCE-OFFER.txt` with their upstream locations.
* **ParaView** — redistributed with its `share\licenses` folder intact; Qt 6 is LGPL-3.0
  (dynamically linked, replaceable DLLs; sources at download.qt.io, referenced in the offer).
  Removed from Kitware's package: the NVIDIA IndeX plugin and its 290 MB library (needs an
  NVIDIA IndeX licence and GPU) and the `uv` tool. Identical duplicate DLLs are removed from
  the package and recreated as hard links at install time, restoring Kitware's layout.
* **CalculiX (GPL-2.0-or-later)** — `ccx.exe` is the conda-forge build (package and every
  runtime DLL pinned by SHA-256 in `packaging/prepare_calculix.py`), with SPOOLES (public
  domain) linked in. The installer carries the licence texts of every package in
  `resources\licenses\calculix\<package>`. The build writes the Corresponding Source to
  `packaging\build\sources\calculix` (upstream `ccx_2.23.src.tar.bz2`, SHA-256 pinned, and
  `SOURCE.txt` naming the conda-forge recipe used for the Windows build); **publish it next to
  every installer download** (GPL-2.0 §3). Runtime DLLs shipped with it: ARPACK-NG
  (BSD-3-Clause), OpenBLAS with its BLAS/LAPACK interfaces (BSD-3-Clause), the GCC runtime
  (libgcc_s, libgfortran, libgomp: GPL-3.0 with the GCC Runtime Library Exception;
  libquadmath: LGPL-2.1-or-later, dynamically loaded) and winpthreads (MIT and BSD-style).
* **Microsoft MPI** — the official redistributable installer, MIT licence and MPICH
  copyright in `resources\licenses\MS-MPI`. MS-MPI remains installed when AeroBench is
  uninstalled (shared system component).

## Engine (Python) dependencies

| Package | Licence |
|---|---|
| Pydantic | MIT |
| NumPy | BSD-3-Clause |
| SciPy | BSD-3-Clause |
| FastAPI | MIT |
| Starlette | BSD-3-Clause |
| Uvicorn | BSD-3-Clause |
| psutil | BSD-3-Clause |
| Jinja2 | BSD-3-Clause |
| python-multipart | Apache-2.0 |
| opentelemetry-api (required by FastAPI) | Apache-2.0 — API only; no SDK or exporter is shipped and it is disabled at start-up (`OTEL_SDK_DISABLED=true`), so nothing is sent anywhere |
| cadquery-ocp-novtk ("cad" extra; shipped in the Windows installer) | Apache-2.0 bindings to Open CASCADE Technology (LGPL-2.1 with OCCT exception; dynamically loaded) |
| pytest, httpx, ruff (development only) | MIT / BSD-3-Clause / MIT |

The packaged application uses the official embeddable CPython distribution (PSF licence).

Binary wheels vendor native libraries (`*.libs` folders) without their own metadata:
OpenBLAS in NumPy/SciPy and, in the CAD component, Open CASCADE Technology (LGPL-2.1 with
the OCCT exception, dynamically loaded and replaceable) with its image/font dependencies
(FreeImage under the FreeImage Public License, LibRaw under LGPL-2.1, FreeType under the
FTL, and BSD/MIT/zlib-style libraries). `packaging/licenses.py` lists every vendored DLL with
the licence it is used under in `THIRD-PARTY-LICENSES.txt`, and reports an unknown DLL as
"not determined" so it cannot ship unnoticed.

## Desktop dependencies

| Package | Licence |
|---|---|
| Electron (includes Chromium, Node.js) | MIT (+ bundled licences) |
| React, React DOM | MIT |
| Mantine (core, hooks, notifications) | MIT |
| @tabler/icons-react | MIT |
| three.js | MIT |
| Apache ECharts | Apache-2.0 |
| zustand | MIT |
| Vite, TypeScript (build only) | MIT / Apache-2.0 |
| electron-builder (build only) | MIT |
| Playwright (tests only) | Apache-2.0 |

A full, version-pinned licence report is generated at packaging time
(`packaging/licenses.py` → `THIRD-PARTY-LICENSES.txt` in the installer).

## Algorithms and published methods

| Method | Source | Implementation |
|---|---|---|
| Grid convergence index | Celik et al., *J. Fluids Eng.* 130 (2008) 078001; Roache (1994) | `validation/gci.py`, tested against the paper's example |
| NSGA-II | Deb et al., *IEEE TEC* 6 (2002) 182–197 | own implementation (`optimisation/algorithms/nsga2.py`) |
| Gaussian-process Bayesian optimisation, expected improvement with probability of feasibility | Jones et al. (1998); Gardner et al. (2014) | own implementation (`optimisation/gp.py`, `gp_bo.py`) |
| Nelder–Mead with adaptive coefficients | Gao & Han (2012) | own implementation |
| Free-form deformation (Bernstein) | Sederberg & Parry (1986) | `geometry/deform.py` |
| Wendland C2 radial basis | Wendland (1995) | `geometry/deform.py` |
| Triangle/segment intersection | Möller & Trumbore (1997) | `geometry/deform.py` |
| Flat-plate skin friction for first-cell height | Schlichting (1979) correlation | `meshing/layers.py` |

## Geometry and reference data

* **Synthetic vehicles** used in tests and demos (`geometry/fixtures.py`) are generated by
  AeroBench; no third-party shapes are shipped.
* **Ahmed body** (validation benchmark) is generated from its published dimensions (Ahmed,
  Ramm & Faltin, SAE 840300, 1984). Reference drag values are cited with their source in
  `validation/benchmarks.py`.
* **DrivAer** (TU München) is a widely used realistic validation model. Its CAD data is
  provided by TUM *on request*; the terms are set by TUM and are not published on the
  geometry page. **AeroBench does not include, download or redistribute DrivAer data.**
  Users who obtain it import it like any other geometry and remain responsible for complying
  with TUM's conditions (including any restriction on commercial use or redistribution).
  Derived datasets (e.g. DrivAerNet, DrivAerML) carry their own licences, which must be checked
  separately before use.
