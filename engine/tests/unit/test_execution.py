"""Execution-provider behaviour that does not need OpenFOAM installed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aerobench.core.fs import project_folder_name
from aerobench.execution.local_posix import LocalPosixProvider


def test_project_folder_name_is_filesystem_safe():
    assert project_folder_name('a<b>:c?') == "a_b__c_"
    assert project_folder_name("   ") == "project"
    assert project_folder_name("  car.  ") == "car"
    if os.name != "nt":
        assert project_folder_name("Demo car") == "Demo_car"


@pytest.mark.skipif(os.name == "nt", reason="POSIX provider")
def test_space_path_is_linked_for_serial_and_rejected_for_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROBENCH_LINK_DIR", str(tmp_path / "links"))
    case = tmp_path / "my project" / "case"
    case.mkdir(parents=True)
    linked = LocalPosixProvider.safe_case_path(case)
    assert " " not in linked
    assert Path(linked).resolve() == case.resolve()
    assert LocalPosixProvider.safe_case_path(case) == linked  # stable
    prov = LocalPosixProvider("t", {"bashrc": "/nonexistent"})
    assert prov.check_case(case, 1) == []
    diags = prov.check_case(case, 4)
    assert diags and diags[0].code == "environment.path_spaces_parallel"
    assert prov.check_case(tmp_path, 4) == [] or " " in str(tmp_path)


def _fake_windows_install(root: Path) -> Path:
    inst = root / "Keysight-OpenCFD" / "OpenFOAM" / "v2606"
    pdir = inst / "msys64" / "home" / "ofuser" / "OpenFOAM" / "OpenFOAM-v2606"
    b = pdir / "platforms" / "win64MingwDPInt32Opt" / "bin"
    b.mkdir(parents=True)
    for exe in ("simpleFoam.exe", "snappyHexMesh.exe", "libPstream.dll"):
        (b / exe).write_bytes(b"")
    (pdir / "etc").mkdir()
    return inst


def test_native_windows_layout_detection_and_command(tmp_path, monkeypatch):
    from aerobench.execution.native_windows import NativeWindowsProvider, candidate_roots, locate_install

    appdata = tmp_path / "App Data" / "Roaming"
    inst = _fake_windows_install(appdata)
    assert candidate_roots(str(appdata)) == [inst]
    loc = locate_install(inst)
    assert loc["version"] == "v2606" and loc["bin_dir"].endswith("win64MingwDPInt32Opt/bin".replace("/", os.sep))
    prov = NativeWindowsProvider("native", {"appdata": str(appdata)})
    # no MS-MPI on this machine: detected, but blocked with an actionable diagnostic
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    info = prov.detect()
    assert info.version == "v2606" and not info.available
    assert info.problems[0].code == "environment.msmpi_missing"
    # with MS-MPI present
    (tmp_path / "pf" / "Microsoft MPI" / "Bin").mkdir(parents=True)
    (tmp_path / "pf" / "Microsoft MPI" / "Bin" / "mpiexec.exe").write_bytes(b"")
    (tmp_path / "win" / "System32").mkdir(parents=True)
    (tmp_path / "win" / "System32" / "msmpi.dll").write_bytes(b"")
    info = prov.info(refresh=True)
    assert info.available and info.mpi.launcher.endswith("mpiexec.exe")
    case = tmp_path / "E drive" / "OpenFOAM GUI" / "case"
    serial = prov.command(["simpleFoam"], case, 1)
    assert serial.argv[0].endswith("simpleFoam.exe") and "-case" not in serial.argv and serial.cwd == str(case)
    par = prov.command(["simpleFoam"], case, 4)
    assert par.argv[:3] == [info.mpi.launcher, "-n", "4"] and par.argv[-1] == "-parallel"
    assert not any(str(case) in a for a in par.argv), "case paths must not pass through mpiexec quoting"
    env = par.env
    assert env["WM_PROJECT_DIR"] == info.project_dir and env["MPI_BUFFER_SIZE"]
    assert env["PATH"].split(os.pathsep)[0] == info.bin_dir


def _fake_bundle(res: Path) -> Path:
    """The installer's resources folder: bundled OpenFOAM, ParaView and MS-MPI installer."""
    b = res / "openfoam" / "OpenFOAM-v2606" / "platforms" / "win64MingwDPInt32Opt" / "bin"
    b.mkdir(parents=True)
    for exe in ("simpleFoam.exe", "snappyHexMesh.exe", "libPstream.dll"):
        (b / exe).write_bytes(b"")
    (res / "openfoam" / "OpenFOAM-v2606" / "etc").mkdir()
    pv = res / "ParaView-6.2.0" / "bin"
    pv.mkdir(parents=True)
    (pv / ("pvpython.exe" if os.name == "nt" else "pvpython")).write_bytes(b"")
    (res / "redist").mkdir()
    (res / "redist" / "msmpisetup.exe").write_bytes(b"")
    (res / "bundle.json").write_text('{"components": {}}')
    return res


def test_bundled_components_are_preferred_and_damage_is_explained(tmp_path, monkeypatch):
    from aerobench import bundle
    from aerobench.execution.native_windows import NativeWindowsProvider
    from aerobench.viz.detect import detect_paraview

    res = _fake_bundle(tmp_path / "Program Files" / "AeroBench" / "resources")
    appdata = tmp_path / "AppData"
    _fake_windows_install(appdata)  # a user's own install exists as well
    monkeypatch.setenv("AEROBENCH_BUNDLE_DIR", str(res))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "win"))
    monkeypatch.delenv("AEROBENCH_PVPYTHON", raising=False)
    assert bundle.bundle_dir() == res and bundle.msmpi_installer() == res / "redist" / "msmpisetup.exe"

    prov = NativeWindowsProvider("native", {"appdata": str(appdata)})
    info = prov.detect()
    assert info.details["bundled"] and info.label.endswith("(included)")
    assert info.project_dir == str(res / "openfoam" / "OpenFOAM-v2606")
    # MS-MPI missing: the fix is the bundled installer, offered in the app
    d = info.problems[0]
    assert d.code == "environment.msmpi_missing" and d.context["bundled_installer"].endswith("msmpisetup.exe")
    assert "Install Microsoft MPI" in d.action
    # an explicit root in Settings still wins over the bundle
    own = NativeWindowsProvider("native", {"root": str(appdata / "Keysight-OpenCFD" / "OpenFOAM" / "v2606")})
    assert not own.detect().details["bundled"]

    pv = detect_paraview()
    assert pv["bundled"] and pv["version"] == "6.2.0" and pv["pvpython"].startswith(str(res))

    # antivirus removed the solver: say so and how to repair, not "download OpenFOAM"
    import shutil

    shutil.rmtree(res / "openfoam" / "OpenFOAM-v2606" / "platforms")
    bad = NativeWindowsProvider("native", {"appdata": str(tmp_path / "nowhere")}).detect()
    assert not bad.available and "included with AeroBench is missing or damaged" in bad.problems[0].summary
    assert "setup again" in bad.problems[0].action


def test_restore_links_recreates_duplicates_idempotently(tmp_path):
    import importlib.util
    import json

    spec = importlib.util.spec_from_file_location("restore_links", Path(__file__).resolve().parents[3] / "packaging" / "restore_links.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = tmp_path / "resources"
    (res / "ParaView-6.2.0" / "bin").mkdir(parents=True)
    (res / "ParaView-6.2.0" / "bin" / "vtkCommonCore.dll").write_bytes(b"x" * 1000)
    (res / "tools").mkdir()
    links = [["bin/Lib/site-packages/vtkmodules/vtkCommonCore.dll", "bin/vtkCommonCore.dll"],
             ["bin/Lib/site-packages/paraview/modules/vtkCommonCore.dll", "bin/vtkCommonCore.dll"]]
    (res / "tools" / "paraview-links.json").write_text(json.dumps({"root": "ParaView-6.2.0", "links": links}))
    assert mod.main([str(res)]) == 0
    for rel, _ in links:
        f = res / "ParaView-6.2.0" / rel
        assert f.read_bytes() == b"x" * 1000
        assert os.path.samefile(f, res / "ParaView-6.2.0" / "bin" / "vtkCommonCore.dll")  # a hard link, no extra space
    assert mod.main([str(res)]) == 0  # repair install: nothing to do, still success
    # a path escaping the ParaView folder is refused
    (res / "tools" / "paraview-links.json").write_text(json.dumps({"root": "ParaView-6.2.0", "links": [["../../evil.dll", "bin/vtkCommonCore.dll"]]}))
    assert mod.main([str(res)]) == 1 and not (tmp_path / "evil.dll").exists()


def test_two_processes_never_overwrite_each_others_config(tmp_path):
    """The engine and the worker hold separate Project objects for the same folder."""
    import time

    from aerobench.project.store import Project

    api = Project.create(tmp_path / "p", "Shared")
    worker = Project.open(tmp_path / "p")
    time.sleep(0.01)
    api.update(lambda c: setattr(c.operating, "speed", 55.0))  # user edit in the GUI
    worker.update(lambda c: setattr(c.reference, "locked", True))  # worker ingests a run
    for p in (api, worker):
        assert p.config.operating.speed == 55.0 and p.config.reference.locked is True
    reopened = Project.open(tmp_path / "p")
    assert reopened.config.operating.speed == 55.0 and reopened.config.reference.locked is True
