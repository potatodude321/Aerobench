"""Golden files for generated cases (OpenCFD v2606 and the experimental Foundation adapter).

Any change to generated dictionaries must be deliberate: regenerate with
``AEROBENCH_UPDATE_GOLDEN=1 pytest tests/unit/test_golden_cases.py`` and review the diff.
The case is a fixed synthetic vehicle at yaw (exercises far-field, moving ground and
rotating-wheel boundary conditions)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from aerobench.domain.project import ProjectConfig
from aerobench.domain.templates import apply_template
from aerobench.foam.adapters import get_adapter
from aerobench.foam.case.builder import build_mesh_case, build_solve_case
from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.project.store import Project
from aerobench.services.geometry import import_geometry
from aerobench.services.mesh import plan_mesh

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
UPDATE = os.environ.get("AEROBENCH_UPDATE_GOLDEN") == "1"


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    os.environ.setdefault("AEROBENCH_CONFIG_DIR", str(tmp_path_factory.mktemp("cfg")))
    root = tmp_path_factory.mktemp("g")
    p = Project.create(root / "proj", "Golden", apply_template(ProjectConfig(), "generic_road_car"))
    stl = root / "car.stl"
    write_stl(stl, generic_car(rear_wing=True))
    import_geometry(p, stl)

    def setup(c):
        c.operating.speed = 40.0
        c.operating.yaw_deg = 5.0
        c.numerics.iterations = 2000
        c.execution.n_procs = 8

    p.update(setup)
    return p


def _normalise(text: str) -> str:
    # headers carry the application version (but no dates or paths): a release number alone
    # must not count as a change of the generated case
    return re.sub(r"AeroBench \d+\.\d+\.\d+", "AeroBench X.Y.Z", text.replace("\r\n", "\n"))


@pytest.mark.parametrize("fork,version", [("opencfd", "v2606"), ("foundation", "14")])
def test_generated_case_matches_golden(project, fork, version):
    cfg = project.config.model_copy(deep=True)
    cfg.solver.fork = type(cfg.solver.fork)(fork)
    cfg.solver.version = version
    adapter = get_adapter(fork, version, allow_experimental=True)
    _, rc, _ = plan_mesh(project, cfg)
    files = {}
    for stage, gc in (("mesh", build_mesh_case(cfg, rc, adapter)), ("solve", build_solve_case(cfg, rc, adapter))):
        for path, text in gc.render_all().items():
            files[f"{stage}/{path}"] = _normalise(text)
    base = GOLDEN / f"{fork}-{version}"
    for name, text in files.items():
        assert str(project.root) not in text, f"{name} contains an absolute project path"
    if UPDATE:
        for name, text in files.items():
            (base / name).parent.mkdir(parents=True, exist_ok=True)
            (base / name).write_text(text)
        return
    if not base.exists():
        pytest.skip("golden files not generated yet (run with AEROBENCH_UPDATE_GOLDEN=1)")
    expected = {str(p.relative_to(base)).replace(os.sep, "/"): _normalise(p.read_text()) for p in base.rglob("*") if p.is_file()}
    assert sorted(expected) == sorted(files), "set of generated files changed"
    for name in files:
        assert files[name] == expected[name], f"{name} differs from the golden file"
