from __future__ import annotations

from pathlib import Path

import pytest

from spice2sch.symbols import SymbolIndex, _parse_bbox, _parse_symbol


def _write_symbol(
    path: Path, *, library: str, stem_model: str, extra_params: str = ""
) -> None:
    """Write a minimal xschem .sym file under `path` (already `<library>/<stem>.sym`)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "v {xschem version=3.4.6 file_version=1.2}\n"
        "K {type=%s\n"
        'template="name=X1 model=%s__%s VALUE=1%s"\n'
        "}\n"
        "L 4 -15 0 15 0 {}\n"
        "B 5 -20 -10 -20 10 {name=A pinnumber=1}\n"
        "B 5 20 -10 20 10 {name=B pinnumber=2}\n"
        % (stem_model, library, stem_model, extra_params)
    )


@pytest.fixture
def multi_library_pdk(tmp_path: Path) -> Path:
    """A fake PDK with two unrelated xschem libraries, using a PDK variant
    directory name that is deliberately *not* sky130A, to prove discovery
    isn't hardcoded to any single PDK."""
    root = tmp_path / "fake_pdk"
    xschem_dir = root / "some_pdk_variant" / "libs.tech" / "xschem"

    _write_symbol(
        xschem_dir / "lib_one" / "foo.sym", library="lib_one", stem_model="foo"
    )
    _write_symbol(
        xschem_dir / "lib_two" / "foo.sym", library="lib_two", stem_model="foo"
    )

    return root


def test_discovers_multiple_libraries(multi_library_pdk: Path):
    index = SymbolIndex(multi_library_pdk)
    assert {d.name for d in index.library_dirs} == {"lib_one", "lib_two"}


def test_resolve_is_scoped_per_library(multi_library_pdk: Path):
    index = SymbolIndex(multi_library_pdk)

    one = index.resolve("lib_one__foo")
    two = index.resolve("lib_two__foo")

    assert one is not None
    assert two is not None
    assert one is not two
    assert one.library == "lib_one"
    assert two.library == "lib_two"


def test_resolve_unknown_model_or_library_returns_none(multi_library_pdk: Path):
    index = SymbolIndex(multi_library_pdk)

    assert index.resolve("lib_one__missing") is None
    assert index.resolve("unknown_library__foo") is None
    assert index.resolve("no_separator_ref") is None


def test_template_model_prefix_is_library_specific(multi_library_pdk: Path):
    index = SymbolIndex(multi_library_pdk)

    symbol = index.resolve("lib_one__foo")
    assert symbol is not None
    # "lib_one__foo" in the template's model= attribute is stripped down to
    # "foo" using this symbol's *own* library, not a hardcoded PDK prefix.
    assert symbol.template_model == "foo"


def test_missing_pdk_root_raises(tmp_path: Path):
    empty_root = tmp_path / "empty_pdk"
    empty_root.mkdir()
    with pytest.raises(FileNotFoundError):
        SymbolIndex(empty_root)


def test_parse_bbox_from_geometry(tmp_path: Path):
    path = tmp_path / "lib" / "dev.sym"
    _write_symbol(path, library="lib", stem_model="dev")
    symbol = _parse_symbol(path)

    # pins at x=±20, y=±10 plus a line from (-15,0) to (15,0)
    assert symbol.bbox.min_x == -20.0
    assert symbol.bbox.max_x == 20.0
    assert symbol.bbox.min_y == -10.0
    assert symbol.bbox.max_y == 10.0
    assert symbol.bbox.size == (40.0, 20.0)


def test_parse_bbox_includes_arcs_and_polys():
    text = (
        "L 4 0 0 10 0 {}\n"
        "P 4 3 0 0 5 5 10 0 {}\n"
        "A 4 0 0 4 0 90 {}\n"
        "T {@name} 100 100 0 0 0.2 0.2 {}\n"  # text must not inflate bbox
    )
    bbox = _parse_bbox(text)
    assert bbox.min_x == -4.0
    assert bbox.max_x == 10.0
    assert bbox.min_y == -4.0
    assert bbox.max_y == 5.0
