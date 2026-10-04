from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import ReferenceCell
from .pipeline import (
    generate_schematic,
    generate_svg,
    netlist_schematic,
    prepare_workdir,
    run_lvs,
)

REPO_ROOT = Path(__file__).parents[1]
SVG_DIR = REPO_ROOT / ".cache" / "svg"
KNOWN_FAILURES = {
    "sky130_fd_sc_hd__macro_sparecell": "hierarchical stdcell instances, not PDK primitives",
}


def test_lvs_round_trip(reference_cell: ReferenceCell, tmp_path: Path, pdk_root: Path):
    reference_spice = reference_cell.path
    pdk = reference_cell.pdk
    cell_name = reference_spice.stem

    if cell_name in KNOWN_FAILURES:
        pytest.xfail(KNOWN_FAILURES[cell_name])

    prepare_workdir(tmp_path, REPO_ROOT)

    sch_path = tmp_path / "schematics" / f"{cell_name}.sch"
    sch_path.parent.mkdir(parents=True)
    generate_schematic(reference_spice, sch_path, pdk_root)

    svg_path = generate_svg(tmp_path, cell_name, pdk_root, pdk)
    SVG_DIR.mkdir(parents=True, exist_ok=True)
    persisted = SVG_DIR / f"{cell_name}.svg"
    persisted.write_bytes(svg_path.read_bytes())

    generated_netlist = netlist_schematic(tmp_path, cell_name, pdk_root, pdk)

    report_path = tmp_path / "lvs" / f"{cell_name}.report"
    result = run_lvs(
        reference_spice, generated_netlist, cell_name, report_path, pdk_root, pdk
    )

    assert result.passed, (
        f"LVS mismatch for {cell_name}\n"
        f"reference : {reference_spice}\n"
        f"generated : {generated_netlist}\n\n"
        f"{result.report_text}"
    )
