"""The *Windows* OpenFOAM that ships in the installer, driven by the real native-Windows
provider, end to end — run under Wine on Linux build machines.

What this proves about the shipped bundle and ``NativeWindowsProvider``: the bundled layout
is recognised, the environment the provider builds (PATH, WM_PROJECT_DIR, FOAM_ETC,
MPI_BUFFER_SIZE) is sufficient, MS-MPI's ``msmpi.dll`` resolves, executables run with the case
as working directory, and the full serial pipeline (surfaceFeatureExtract, blockMesh,
snappyHexMesh with layers, checkMesh, potentialFoam, simpleFoam with the first-order switch,
graceful stop) produces results AeroBench ingests.

Not covered here (Wine cannot run MS-MPI's process manager, error 1747): parallel runs.

Requires: ``packaging/build/bundle`` (``python packaging/prepare_thirdparty.py``), wine, and
MS-MPI installed in the Wine prefix (``wine msmpisetup.exe -unattend``).
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

import pytest

from aerobench.execution import registry
from aerobench.execution.native_windows import NativeWindowsProvider
from aerobench.execution.provider import CommandSpec
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.jobs.manager import JobManager
from aerobench.jobs.worker import Worker  # noqa: F401  (used by _drain)
from aerobench.project.store import Project
from aerobench.services import geometry as geo
from aerobench.services.context import AppContext
from aerobench.services.runs import prepare_run, run_record
from aerobench.settings import AppSettings

from .conftest import coarse_mesh

REPO = Path(__file__).resolve().parents[3]
BUNDLE = REPO / "packaging" / "build" / "bundle"
WINE = shutil.which("wine") or ("/tmp/claude-0/winebin/wine" if Path("/tmp/claude-0/winebin/wine").exists() else None)
PREFIX = Path(os.environ.get("WINEPREFIX", Path.home() / ".wine"))
MSMPI_BIN = PREFIX / "drive_c" / "Program Files" / "Microsoft MPI" / "Bin"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (BUNDLE / "openfoam").is_dir(), reason="no staged bundle (run packaging/prepare_thirdparty.py)"),
    pytest.mark.skipif(WINE is None, reason="wine not installed"),
    pytest.mark.skipif(not (MSMPI_BIN / "mpiexec.exe").exists(), reason="MS-MPI not installed in the Wine prefix"),
]


def zpath(p: str) -> str:
    return "Z:" + str(Path(p).resolve()).replace("/", "\\")


class WineNativeProvider(NativeWindowsProvider):
    """The production provider; only the process launch is wrapped in ``wine`` and the
    environment's paths are translated to the Windows view (Z: is the Unix root)."""

    kind = "wine-native"

    def command(self, app_argv, case_dir, nprocs=1):
        if nprocs > 1:
            pytest.skip("parallel MS-MPI does not run under Wine")
        spec = super().command(app_argv, case_dir, nprocs)
        env = dict(spec.env)
        parts = [p for p in env.pop("PATH").split(os.pathsep) if p]
        host_path = os.environ.get("PATH", "")
        win_parts = [zpath(p) for p in parts if p != host_path and Path(p).exists()]
        env["WINEPATH"] = ";".join(win_parts)
        env["PATH"] = os.pathsep.join([str(Path(WINE).parent), host_path])
        for k in ("WM_PROJECT_DIR", "FOAM_ETC"):
            env[k] = zpath(env[k])
        env["WINEDEBUG"] = "-all"
        return CommandSpec(argv=[WINE] + spec.argv, env=env, cwd=spec.cwd)


@pytest.fixture(scope="module")
def wine_ctx(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setenv("AEROBENCH_CONFIG_DIR", str(tmp_path_factory.mktemp("wcfg")))
    monkeypatch_module.setenv("MSMPI_BIN", str(MSMPI_BIN))
    # Windows is case-insensitive, Linux is not: expose the prefix's system32 as System32
    winroot = tmp_path_factory.mktemp("winroot")
    (winroot / "System32").symlink_to(PREFIX / "drive_c" / "windows" / "system32")
    monkeypatch_module.setenv("SystemRoot", str(winroot))
    monkeypatch_module.setitem(registry.KINDS, "wine-native", WineNativeProvider)
    # the bundle as the installed application would see it
    monkeypatch_module.setenv("AEROBENCH_BUNDLE_DIR", str(BUNDLE))
    s = AppSettings()
    s.max_cores = 1
    ctx = AppContext(s)
    prov = WineNativeProvider("wine-native", {})
    ctx._providers = [prov]
    info = prov.info(refresh=True)
    assert info.available, [p.summary for p in info.problems]
    return ctx, prov


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def _drain(p, ctx, max_ticks=400):
    w = Worker(p, ctx, launch_inline=True)
    for _ in range(max_ticks):
        w.tick()
        if not w.has_work():
            return
    raise AssertionError("worker did not finish")


def test_bundled_layout_is_detected(wine_ctx):
    _, prov = wine_ctx
    info = prov.info()
    assert info.details["bundled"] is True
    assert info.version == "v2606" and info.fork == "opencfd"
    assert info.label.endswith("(included)")
    assert Path(info.bin_dir, "simpleFoam.exe").exists()
    assert info.details["msmpi_installer"].endswith("msmpisetup.exe")
    assert info.mpi.available


def test_full_serial_pipeline_with_the_windows_binaries(wine_ctx, tmp_path):
    ctx, _ = wine_ctx
    root = tmp_path / "Wine car project"  # spaces in the path, as in "Documents\AeroBench Projects"
    stl = tmp_path / "car.stl"
    write_stl(stl, generic_car())
    p = Project.create(root, "Wine car")
    geo.import_geometry(p, stl)

    def setup(c):
        c.mesh = coarse_mesh()
        c.tunnel = c.tunnel.model_copy(update=dict(upstream=1.5, downstream=3.0, lateral=1.2, top=1.2))
        c.numerics.iterations = 60
        c.numerics.first_order_iterations = 20
        c.execution.n_procs = 1
        c.execution.provider_id = "wine-native"
        c.operating.speed = 30.0

    p.update(setup)
    res = prepare_run(p, ctx)
    _drain(p, ctx)
    row = run_record(p, res["run_id"])
    assert row["status"] == "completed", row
    m = row["metrics"]["metrics"]
    assert 0.1 < m["Cd"] < 2.0, m
    # the commands that ran are the Windows executables of the bundle
    st = JobManager(p).state(res["job_id"])
    solve = next(s for s in st.steps if s.name == "solve")
    assert solve.command[1].endswith("simpleFoam.exe") and "packaging/build/bundle/openfoam" in solve.command[1]
    log = (p.job_dir(res["job_id"]) / solve.log).read_text(errors="replace")
    assert "Time = 60" in log and "End" in log


def test_graceful_stop_with_the_windows_binaries(wine_ctx, tmp_path):
    """The abort function object (stop trigger file in the case) works with the Windows build."""
    ctx, _ = wine_ctx
    stl = tmp_path / "car.stl"
    write_stl(stl, generic_car())
    p = Project.create(tmp_path / "Stop", "Stop")
    geo.import_geometry(p, stl)

    def setup(c):
        c.mesh = coarse_mesh()
        c.tunnel = c.tunnel.model_copy(update=dict(upstream=1.5, downstream=3.0, lateral=1.2, top=1.2))
        c.numerics.iterations = 5000
        c.numerics.first_order_iterations = 5
        c.execution.n_procs = 1
        c.execution.provider_id = "wine-native"

    p.update(setup)
    res = prepare_run(p, ctx)
    jm = JobManager(p)
    # the runner must run in this process (the test provider is registered here only):
    # drive the worker in a thread and request the stop from this one
    import threading

    t = threading.Thread(target=_drain, args=(p, ctx), daemon=True)
    t.start()
    stop_sent = False
    deadline = time.time() + 1200
    while time.time() < deadline:
        st = jm.state(res["job_id"])
        solve = next((s for s in st.steps if s.name == "solve"), None) if st else None
        if solve and solve.log and not stop_sent:
            text = (p.job_dir(res["job_id"]) / solve.log).read_text(errors="replace")
            hits = re.findall(r"^Time = (\d+)", text, flags=re.M)
            if hits and int(hits[-1]) >= 15:
                jm.request_stop(res["job_id"])
                stop_sent = True
        if stop_sent and not t.is_alive():
            break
        time.sleep(1.0)
    assert stop_sent
    st = jm.state(res["job_id"])
    assert st.status.value == "stopped", st.message
    case = p.root / run_record(p, res["run_id"])["path"]
    times = sorted(int(d.name) for d in case.iterdir() if d.name.isdigit() and int(d.name) > 0)
    assert times and 15 <= times[-1] < 5000
