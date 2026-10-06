import os
import shutil
from pathlib import Path

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
