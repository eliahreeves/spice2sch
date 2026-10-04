from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from spice2sch.main import create_io_block
from spice2sch.models import Point, Primitive
from spice2sch.spice import Spice, SubcktCall, parse_number
from spice2sch.symbols import SymbolIndex

from .pipeline import cdl_to_spice
from .test_placeable import _symbol

GF180_INV = """\
* comment
.SUBCKT gf180mcu_fd_sc_mcu7t5v0__inv_1 I ZN VDD VNW VPW VSS
M_i_0 ZN I VSS VPW nfet_05v0 W=8.2e-07 L=6e-07
M_i_1 ZN I VDD VNW pfet_05v0 W=1.22e-06 L=5e-07
.ENDS
"""

GF180_ANTENNA = """\
.SUBCKT gf180mcu_fd_sc_mcu7t5v0__antenna I VDD VNW VPW VSS
d0 VPW I diode_nd2ps_06v0 0.2052p 1.86u $m=1
.ENDS
"""


def test_mosfet_line_has_bare_model_and_four_terminals() -> None:
    call = SubcktCall("M_i_0 ZN I VSS VPW nfet_05v0 W=8.2e-07 L=6e-07")
    assert call.name == "M_i_0"
    assert call.nodes == ["ZN", "I", "VSS", "VPW"]
    assert call.subckt_ref == "nfet_05v0"
    assert call.params == [("W", "8.2e-07"), ("L", "6e-07")]


def test_diode_line_names_positional_params_and_strips_dollar() -> None:
    call = SubcktCall("d0 VPW I diode_nd2ps_06v0 0.2052p 1.86u $m=1")
    assert call.nodes == ["VPW", "I"]
    assert call.subckt_ref == "diode_nd2ps_06v0"
    assert call.params == [("area", "0.2052p"), ("pj", "1.86u"), ("m", "1")]


def test_cdl_subckt_call_slash_separator_is_ignored() -> None:
    call = SubcktCall("X1 A Y VDD VSS / inv_cell m=2")
    assert call.nodes == ["A", "Y", "VDD", "VSS"]
    assert call.subckt_ref == "inv_cell"
    assert call.params == [("m", "2")]


def test_unsupported_device_line_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported device line"):
        SubcktCall("Q1 c b e npn")


@pytest.mark.parametrize(
    ("text", "value"),
    [("0.2052p", 0.2052e-12), ("1.86u", 1.86e-6), ("8.2e-07", 8.2e-7), ("2meg", 2e6)],
)
def test_parse_number_handles_si_suffixes(text: str, value: float) -> None:
    assert parse_number(text) == pytest.approx(value)


def test_uppercase_subckt_and_trailing_rails_infer_outputs() -> None:
    spice = Spice(GF180_INV)
    assert spice.name == "gf180mcu_fd_sc_mcu7t5v0__inv_1"
    assert spice.ports == ["I", "ZN", "VDD", "VNW", "VPW", "VSS"]
    inputs, outputs = spice.extract_io()
    assert outputs == ["ZN"]
    assert inputs == ["I", "VDD", "VNW", "VPW", "VSS"]


def test_port_feeding_a_gate_stays_an_input() -> None:
    # Q is on a drain but also fed back to a gate, so it reads as an input.
    spice = Spice(
        ".SUBCKT cell Q VDD VSS\n"
        "M0 Q n1 VSS VSS nfet_05v0 W=1 L=1\n"
        "M1 n1 Q VSS VSS nfet_05v0 W=1 L=1\n"
        ".ENDS\n"
    )
    assert spice.extract_io() == (["Q", "VDD", "VSS"], [])


def test_sky130_port_order_still_splits_on_rails() -> None:
    spice = Spice(
        ".subckt sky130_fd_sc_hd__inv_1 A VGND VNB VPB VPWR Y\n"
        "X0 Y A VGND VNB sky130_fd_pr__nfet_01v8 w=650000u l=150000u\n"
        ".ends\n"
    )
    assert spice.extract_io() == (["A", "VGND", "VNB", "VPB", "VPWR"], ["Y"])


def test_io_block_keeps_netlist_port_order() -> None:
    spice = Spice(GF180_INV)
    block = create_io_block(spice.extract_io(), Point(0, 0), order=spice.ports)
    labels = re.findall(r"lab=(\w+)", block)
    assert labels == spice.ports
    assert "C {opin.sym}" in block.splitlines()[1]


def test_diode_area_perimeter_become_symbol_rectangle() -> None:
    symbol = replace(
        _symbol(
            stem="diode_nd2ps_06v0",
            device_type="diode",
            pins=[("P", 0.0, -20.0), ("N", 0.0, 20.0)],
        ),
        template_attr_names={"r_w": "r_w", "r_l": "r_l", "m": "m"},
    )
    call = SubcktCall("d0 VPW I diode_nd2ps_06v0 0.2052p 1.86u $m=1")
    primitive = Primitive.from_subckt_call(call, 0, symbol)

    assert primitive.library == "testlib"
    assert primitive.model == "diode_nd2ps_06v0"
    assert set(primitive.params) == {"m", "r_w", "r_l"}
    width = parse_number(primitive.params["r_w"])
    length = parse_number(primitive.params["r_l"])
    assert width * length == pytest.approx(0.2052e-12)
    assert 2 * (width + length) == pytest.approx(1.86e-6)


def _write_fet_symbol(path: Path, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "v {xschem version=3.4.6 file_version=1.2}\n"
        f'K {{type=nmos\ntemplate="name=M1 L=1u W=1u model={model}"\n}}\n'
        "B 5 17.5 -32.5 22.5 -27.5 {name=D}\n"
        "B 5 -22.5 -2.5 -17.5 2.5 {name=G}\n"
        "B 5 17.5 27.5 22.5 32.5 {name=S}\n"
        "B 5 19.9 -0.1 20.1 0.1 {name=B}\n"
    )


def test_bare_model_resolves_to_library_closest_to_cell(tmp_path: Path) -> None:
    # gf180mcu ships identical symbols under both "gf180mcu_fd_pr" and
    # "symbols"; the former shares the cell's "gf180mcu_fd_" prefix.
    xschem = tmp_path / "gf180mcuD" / "libs.tech" / "xschem"
    _write_fet_symbol(xschem / "symbols" / "nfet_05v0.sym", "nfet_05v0")
    _write_fet_symbol(xschem / "gf180mcu_fd_pr" / "nfet_05v0.sym", "nfet_05v0")
    index = SymbolIndex(tmp_path)

    near = "gf180mcu_fd_sc_mcu7t5v0__inv_1"
    symbol = index.resolve("nfet_05v0", near=near)
    assert symbol is not None
    assert symbol.sch_path == "gf180mcu_fd_pr/nfet_05v0.sym"
    assert index.resolve("pfet_05v0", near=near) is None


def test_cdl_to_spice_names_diode_params_for_netgen() -> None:
    assert cdl_to_spice(GF180_ANTENNA).splitlines()[1] == (
        "d0 VPW I diode_nd2ps_06v0 area=0.2052p pj=1.86u m=1"
    )
