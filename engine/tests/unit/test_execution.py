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
