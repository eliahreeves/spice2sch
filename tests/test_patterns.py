from __future__ import annotations

from spice2sch.patterns import Inverter, TransmissionGate, find_super_nodes

from .test_placeable import _pfet_symbol, _primitive, _symbol


def _pfet(name: str, d: str, g: str, s: str):
    return _primitive(name=name, symbol=_pfet_symbol(), nodes=[d, g, s, "VPB"])


def _nfet(name: str, d: str, g: str, s: str):
    return _primitive(name=name, symbol=_symbol(), nodes=[d, g, s, "VNB"])


def test_inverter_detected():
    nodes, leftovers = find_super_nodes(
        [_pfet("P", "Y", "A", "VPWR"), _nfet("N", "Y", "A", "VGND")]
    )
    assert [type(n) for n in nodes] == [Inverter]
    assert leftovers == []


def test_nand2_is_not_an_inverter():
    # sky130_fd_sc_hd__nand2_1: P(A) and N(A) share gate A and net Y, but the
    # NMOS sits on top of a series stack rather than on VGND.
    nand2 = [
        _pfet("X0", "Y", "A", "VPWR"),
        _pfet("X1", "VPWR", "B", "Y"),
        _nfet("X2", "VGND", "B", "mid"),
        _nfet("X3", "mid", "A", "Y"),
    ]
    nodes, leftovers = find_super_nodes(nand2)
    assert nodes == []
    assert len(leftovers) == 4


def test_clocked_transmission_gates_not_split_into_inverters():
    # Two TGs on opposite clock phases sharing node M. Each TG's PMOS shares a
    # gate and one diffusion net with the other TG's NMOS.
    latch = [
        _pfet("P1", "D", "CKB", "M"),
        _nfet("N1", "D", "CK", "M"),
        _pfet("P2", "M", "CK", "Q"),
        _nfet("N2", "M", "CKB", "Q"),
    ]
    nodes, leftovers = find_super_nodes(latch)
    assert [type(n) for n in nodes] == [TransmissionGate, TransmissionGate]
    assert leftovers == []


def test_parallel_devices_with_same_gate_are_not_a_transmission_gate():
    nodes, _ = find_super_nodes([_pfet("P", "A", "G", "B"), _nfet("N", "A", "G", "B")])
    assert nodes == []
