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
