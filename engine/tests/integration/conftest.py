import os
import shutil
from pathlib import Path

from aerobench.geometry.fixtures import generic_car
from aerobench.geometry.io import write_stl
from aerobench.jobs.worker import Worker
from aerobench.project.store import Project
from aerobench.services import geometry as geo
from aerobench.services.runs import prepare_run

import pytest

from aerobench.domain.geometry import ComponentRole
from aerobench.domain.mesh import LayerConfig, LayerMode, MeshConfig, MeshPreset, SurfaceRefinement
from aerobench.execution.local_posix import find_bashrcs
from aerobench.services.context import AppContext
from aerobench.settings import AppSettings

HAVE_OPENFOAM = bool(find_bashrcs())


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="OpenFOAM not installed")
    for item in items:
        if "openfoam" in item.keywords and not HAVE_OPENFOAM:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def ctx(tmp_path_factory):
    os.environ["AEROBENCH_CONFIG_DIR"] = str(tmp_path_factory.mktemp("config"))
    s = AppSettings()
    s.max_cores = 2
    s.worker_idle_timeout_s = 5
    return AppContext(s)


def coarse_mesh(base: float = 0.6, level: int = 3, layers: int = 2) -> MeshConfig:
    return MeshConfig(
        preset=MeshPreset.CUSTOM,
        base_cell_size=base,
        auto_regions=True,
        surface=[
            SurfaceRefinement(role=ComponentRole.BODY, min_level=level, max_level=level + 1),
            SurfaceRefinement(role=ComponentRole.WHEEL, min_level=level, max_level=level + 1),
            SurfaceRefinement(role=ComponentRole.WING, min_level=level + 1, max_level=level + 2),
        ],
        layers=LayerConfig(mode=LayerMode.RELATIVE, n_layers=layers, expansion_ratio=1.2, final_relative=0.5),
    )


def _drain(project, ctx, max_ticks=400):
    w = Worker(project, ctx, launch_inline=True)
    for _ in range(max_ticks):
        w.tick()
        if not w.has_work():
            return
    raise AssertionError("worker did not finish")


@pytest.fixture(scope="session")
def solved(tmp_path_factory, ctx):
    root = tmp_path_factory.mktemp("proj") / "CarProject"
    stl = tmp_path_factory.mktemp("geom") / "synthetic_car.stl"
    write_stl(stl, generic_car())
    p = Project.create(root, "Synthetic car")
    imp = geo.import_geometry(p, stl)
    assert imp["units"] == "m" and len(imp["identification"]["wheels"]) == 4

    def setup(c):
        c.mesh = coarse_mesh()
        c.tunnel = c.tunnel.model_copy(update=dict(upstream=1.5, downstream=3.0, lateral=1.2, top=1.2))
        c.numerics.iterations = 120
        c.numerics.first_order_iterations = 40
        c.execution.n_procs = 2
        c.operating.speed = 30.0

    p.update(setup)
    res = prepare_run(p, ctx)
    _drain(p, ctx)
    return p, res



PVPYTHON = None
try:
    from aerobench.viz.detect import detect_paraview

    _pv = detect_paraview()
    PVPYTHON = _pv["pvpython"] if _pv.get("available") else None
except Exception:  # noqa: BLE001
    PVPYTHON = None


def pytest_runtest_setup(item):
    if "paraview" in item.keywords and not PVPYTHON:
        pytest.skip("ParaView (pvpython) not available")
