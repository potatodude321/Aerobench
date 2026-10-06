from pathlib import Path

import pytest

from aerobench.foam.ast import Dict, Dimensions, List, Str, T, Tokens, Word, dims, from_json, to_json, uniform
from aerobench.foam.parser import FoamParseError, parse, parse_value, strip_header
from aerobench.foam.writer import fmt_number, write_dict_body, write_foam_file

TUTORIALS = Path("/usr/lib/openfoam/openfoam2606/tutorials")


def test_fmt_number():
    assert fmt_number(3) == "3"
    assert fmt_number(3.0) == "3"
    assert fmt_number(0.1) == "0.1"
    assert fmt_number(1e-7) == "1e-07"
    assert fmt_number(-0.0) == "0"
    assert fmt_number(True) == "true"
    with pytest.raises(ValueError):
        fmt_number(float("nan"))


def test_build_and_write_control_dict():
    d = Dict()
    d["application"] = "simpleFoam"
    d["endTime"] = 500
    d["writeFormat"] = "binary"
    d["functions"] = Dict(forces=Dict(type="forces", libs=["forces"], patches=["body", "wheels"], CofR=(0.72, 0, 0)))
    txt = write_foam_file(d, "controlDict")
    assert "application     simpleFoam;" in txt
    assert "libs            (forces);" in txt
    assert "CofR            (0.72 0 0);" in txt
    assert "version         2.0;" in txt
    # deterministic
    assert txt == write_foam_file(d, "controlDict")


def test_regex_keys_are_quoted_and_parse_back():
    d = Dict()
    d["(lowerWall|motorBike).*"] = Dict(nSurfaceLayers=3)
    d["div(phi,U)"] = T("bounded", "Gauss", "linearUpwindV", "grad(U)")
    txt = write_dict_body(d)
    assert '"(lowerWall|motorBike).*"' in txt
    assert "div(phi,U)      bounded Gauss linearUpwindV grad(U);" in txt
    back = parse(txt)
    assert back['"(lowerWall|motorBike).*"']["nSurfaceLayers"] == 3
    assert isinstance(back["div(phi,U)"], Tokens)


def test_field_like_values_roundtrip():
    d = Dict()
    d["dimensions"] = dims(0, 1, -1, 0, 0, 0, 0)
    d["internalField"] = uniform((40.0, 0, 0))
    bf = Dict()
    bf["inlet"] = Dict(type="fixedValue", value=uniform((40.0, 0, 0)))
    bf["outlet"] = Dict(type="inletOutlet", inletValue=uniform((0, 0, 0)), value=Word("$internalField"))
    d["boundaryField"] = bf
    txt = write_foam_file(d, "U", cls="volVectorField")
    back = parse(txt)
    hdr, body = strip_header(back)
    assert hdr["class"] == Word("volVectorField")
    assert body["dimensions"] == Dimensions((0, 1, -1, 0, 0, 0, 0))
    assert body["internalField"] == Tokens([Word("uniform"), List([40, 0, 0])])
    assert body["boundaryField"]["outlet"]["value"] == Word("$internalField")


def test_list_of_dicts_and_nested_lists():
    d = Dict()
    d["features"] = List([Dict(file=Str("body.eMesh"), level=6)])
    d["levels"] = List([List([1e15, 4])])
    txt = write_dict_body(d)
    back = parse(txt)
    assert back["features"].items[0]["level"] == 6
    assert back["levels"] == List([List([1e15, 4])])


def test_path_operations():
    d = Dict()
    d.set_path("SIMPLE/nNonOrthogonalCorrectors", 1)
    d.set_path("relaxationFactors/equations/U", 0.9)
    assert d.get_path("relaxationFactors/equations/U") == 0.9
    d.delete_path("SIMPLE/nNonOrthogonalCorrectors")
    assert not d.has_path("SIMPLE/nNonOrthogonalCorrectors")
    with pytest.raises(KeyError):
        d.get_path("nope/x")


def test_json_roundtrip():
    d = Dict()
    d["a"] = T("uniform", (1, 2, 3))
    d["b"] = Dict(c=Str("x y"), e=[1, 2])
    d.add_directive("#includeEtc", Str("caseDicts/setConstraintTypes"))
    j = to_json(d)
    assert from_json(j) == d


def test_parser_handles_comments_directives_and_code_blocks():
    text = r'''
    /* header */
    FoamFile { version 2.0; format ascii; class dictionary; object x; }
    // comment
    #include "include/initialConditions"
    a 1; // trailing
    b ( 1 2 3 );
    code
    #{
        Info<< "hi" << endl;
    #};
    c { d "str with ; semicolon"; }
    '''
    d = parse(text)
    assert d["a"] == 1
    assert d["c"]["d"] == Str("str with ; semicolon")
    assert any(getattr(it, "name", None) == "#include" for it in d.items)
    assert "Info<<" in d["code"].text


def test_parse_errors_have_line_numbers():
    with pytest.raises(FoamParseError) as e:
        parse("a 1;\nb { c 2;\n")
    assert "line" in str(e.value)


@pytest.mark.skipif(not TUTORIALS.exists(), reason="OpenFOAM tutorials not installed")
def test_parse_all_motorbike_dictionaries_and_rewrite():
    base = TUTORIALS / "incompressible/simpleFoam/motorBike"
    files = [p for p in base.rglob("*") if p.is_file() and p.suffix not in (".md",) and p.name not in ("Allrun", "Allclean")]
    assert files
    for p in files:
        d = parse(p.read_text())
        # re-write and re-parse must give the same tree
        txt = write_dict_body(d)
        assert parse(txt) == d, p


@pytest.mark.skipif(not TUTORIALS.exists(), reason="OpenFOAM tutorials not installed")
def test_parse_many_tutorial_system_files():
    count = 0
    for name in ("fvSchemes", "fvSolution", "controlDict", "snappyHexMeshDict", "blockMeshDict"):
        for p in list(TUTORIALS.rglob(f"system/{name}"))[:60]:
            d = parse(p.read_text(errors="replace"))
            assert parse(write_dict_body(d)) == d, p
            count += 1
    assert count > 100


def test_parse_value():
    v = parse_value("bounded Gauss linearUpwind grad(U)")
    assert isinstance(v, Tokens) and v.items[-1] == Word("grad(U)")
