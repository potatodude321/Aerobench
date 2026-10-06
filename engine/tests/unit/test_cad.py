"""CAD import through OpenCASCADE (skipped when the optional CAD component is absent)."""

import math

import pytest

pytest.importorskip("OCP")


def _write_models(d):
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
    from OCP.IGESControl import IGESControl_Writer
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    b = BRep_Builder()
    comp = TopoDS_Compound()
    b.MakeCompound(comp)
    b.Add(comp, BRepPrimAPI_MakeBox(gp_Pnt(0, -0.9, 0.2), 4.5, 1.8, 1.2).Shape())
    b.Add(comp, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(1.0, -0.95, 0.32), gp_Dir(0, 1, 0)), 0.32, 0.22).Shape())
    w = STEPControl_Writer()
    w.Transfer(comp, STEPControl_AsIs)
    w.Write(str(d / "m.step"))
    iw = IGESControl_Writer("MM", 1)
    iw.AddShape(comp)
    iw.ComputeModel()
    iw.Write(str(d / "m.igs"))


@pytest.mark.parametrize("name", ["m.step", "m.igs"])
def test_cad_solids_become_closed_regions(tmp_path, name):
    from aerobench.geometry.inspect import inspect_surface
    from aerobench.geometry.io import read_surface

    _write_models(tmp_path)
    s = read_surface(tmp_path / name)
    assert s.region_names == ["solid_1", "solid_2"]
    exact = 2 * (4.5 * 1.8 + 4.5 * 1.2 + 1.8 * 1.2) + 2 * math.pi * 0.32**2 + 2 * math.pi * 0.32 * 0.22
    assert s.area() == pytest.approx(exact, rel=2e-3)
    assert inspect_surface(s).as_dict()["closed"] is True


def test_missing_backend_is_actionable(tmp_path, monkeypatch):
    import aerobench.geometry.cad as cad
    from aerobench.core.errors import AeroBenchError

    monkeypatch.setattr(cad, "have_ocp", lambda: False)
    monkeypatch.setattr(cad, "find_gmsh", lambda: None)
    (tmp_path / "x.step").write_text("ISO-10303-21;")
    with pytest.raises(AeroBenchError) as e:
        cad.tessellate_cad(tmp_path / "x.step")
    assert e.value.diagnostic.code == "geometry.cad_backend_missing"
