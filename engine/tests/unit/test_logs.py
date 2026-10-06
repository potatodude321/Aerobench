from pathlib import Path

import numpy as np
import pytest

from aerobench.foam.logs.classify import STATUS_DLL_NOT_FOUND, classify
from aerobench.foam.logs.datfiles import DatTail, parse_dat_text, read_series, read_yplus
from aerobench.foam.logs.mesh_logs import parse_checkmesh, parse_snappy
from aerobench.foam.logs.solver_log import SolverLogState, parse_solver_log

FIX = Path(__file__).parent.parent / "fixtures"


def test_checkmesh_parser():
    r = parse_checkmesh((FIX / "logs/checkMesh.car.log").read_text())
    assert r.cells == 78299
    assert r.max_non_ortho == pytest.approx(64.51674014)
    assert r.max_skewness == pytest.approx(8.044083506)
    assert r.highly_skew_faces == 30
    assert r.failed_checks == 1 and not r.mesh_ok
    assert r.patches["body"]["faces"] == 8788
    assert r.total_volume == pytest.approx(1717.047181)


def test_snappy_parser_layers():
    r = parse_snappy((FIX / "logs/snappyHexMesh.car.log").read_text())
    assert r.final_cells == 78299
    assert r.finished_ok is True
    body = next(l for l in r.layers if l.patch == "body")
    assert body.target_layers == 2 and body.mesh_layers == pytest.approx(1.97)
    assert 0.8 < r.overall_layer_coverage() < 1.0
    assert r.cells_per_level[4] == 44986
    assert all(v == 0 for v in r.errors_in_final_mesh.values())


def test_solver_log_parser_incremental():
    text = (FIX / "logs/simpleFoam.car.log").read_text()
    st = SolverLogState()
    n = 0
    for i in range(0, len(text), 777):  # feed in odd-sized chunks
        n += len(st.feed(text[i:i + 777]))
    st.flush()
    assert st.records[0].time == 1
    r2 = st.records[1]
    assert r2.residuals["Ux"] == pytest.approx(0.36604712)
    assert r2.residuals["p"] == pytest.approx(0.10350287)
    assert r2.continuity[2] == pytest.approx(0.00012781649)
    assert r2.exec_time == pytest.approx(0.86)
    assert st.fatal is None
    full = parse_solver_log(text)
    assert len(full.records) == len(st.records)


def test_fatal_error_capture_and_classification():
    log = """Time = 1

--> FOAM FATAL IO ERROR: (openfoam-2606)
Empty word or non-word characters on line 39: string "Gauss linear limited corrected 0.33"

file: system/fvSchemes/laplacianSchemes/default at line 39.

    From Foam::Istream& Foam::operator>>(Istream&, word&)
    in file primitives/strings/word/wordIO.C at line 62.

FOAM exiting
"""
    st = parse_solver_log(log)
    assert "Gauss linear limited" in st.fatal
    d = classify(log, "solve", 1)
    assert d[0].code == "config.syntax"
    assert "fvSchemes" in d[0].details


def test_classify_divergence_and_dll():
    d = classify("Time = 120\n#0  Foam::error::printStack(Foam::Ostream&)\n#1  Foam::sigFpe::sigHandler(int)\nFloating point exception (core dumped)\n", "solve", 136)
    assert d[0].code == "solver.divergence"
    d2 = classify("", "mesh", STATUS_DLL_NOT_FOUND)
    assert d2[0].code == "environment.dll_missing"
    d3 = classify("random text", "solve", 3)
    assert d3[0].code == "solve.failed"


def test_dat_readers():
    t = read_series(FIX / "postProcessing/forces_total", "force.dat")
    assert t.columns[:4] == ["Time", "total_x", "total_y", "total_z"]
    num = t.numeric()
    assert len(num["Time"]) == 50 and num["total_x"][-1] == pytest.approx(799.919702, rel=1e-6)
    si = read_series(FIX / "postProcessing/solverInfo", "solverInfo.dat")
    assert "p_initial" in si.columns and si.numeric()["p_initial"][0] == pytest.approx(1.0)
    ce = read_series(FIX / "postProcessing/continuityError", "continuityError.dat")
    assert ce.columns == ["Time", "Local", "Global", "Cumulative"]
    yp = read_yplus(FIX / "postProcessing/yPlus")
    assert yp["body"]["average"] > 0


def test_restart_series_merge(tmp_path):
    d = tmp_path / "forces_total"
    (d / "0").mkdir(parents=True)
    (d / "30").mkdir()
    hdr = "# Time\ttotal_x total_y total_z\n"
    (d / "0/force.dat").write_text(hdr + "".join(f"{i} {i} 0 0\n" for i in range(1, 41)))
    (d / "30/force.dat").write_text(hdr + "".join(f"{i} {i * 10} 0 0\n" for i in range(31, 61)))
    t = read_series(d, "force.dat").numeric()
    assert len(t["Time"]) == 60
    assert t["total_x"][29] == 30 and t["total_x"][30] == 310  # later run overrides 31..40


def test_dat_tail(tmp_path):
    p = tmp_path / "f.dat"
    p.write_text("# Time\ta b\n1 2 3\n2 4")
    tail = DatTail(p)
    assert tail.poll() == [[1.0, 2.0, 3.0]]
    with open(p, "a") as fh:
        fh.write(" 5\n3 6 7\n")
    assert tail.poll() == [[2.0, 4.0, 5.0], [3.0, 6.0, 7.0]]
    assert tail.columns == ["Time", "a", "b"]
