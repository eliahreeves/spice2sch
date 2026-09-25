from __future__ import annotations

from pathlib import Path

import pytest

from .pipeline import generate_schematic, netlist_schematic, prepare_workdir, run_lvs

REPO_ROOT = Path(__file__).parents[1]
KNOWN_FAILURES = {
    "sky130_fd_sc_hd__diode_2": "non-transistor component, see README limitations",
}


def test_lvs_round_trip(reference_spice: Path, tmp_path: Path, pdk_root: Path):
    cell_name = reference_spice.stem

    if cell_name in KNOWN_FAILURES:
        pytest.xfail(KNOWN_FAILURES[cell_name])

    prepare_workdir(tmp_path, REPO_ROOT)

    sch_path = tmp_path / "schematics" / f"{cell_name}.sch"
    sch_path.parent.mkdir(parents=True)
    generate_schematic(reference_spice, sch_path)

    generated_netlist = netlist_schematic(tmp_path, cell_name, pdk_root)

    report_path = tmp_path / "lvs" / f"{cell_name}.report"
    result = run_lvs(
        reference_spice, generated_netlist, cell_name, report_path, pdk_root
    )

    assert result.passed, (
        f"LVS mismatch for {cell_name}\n"
        f"reference : {reference_spice}\n"
        f"generated : {generated_netlist}\n\n"
        f"{result.report_text}"
    )
