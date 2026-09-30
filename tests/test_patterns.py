from __future__ import annotations

from spice2sch.patterns import (
    Inverter,
    ParallelChain,
    SeriesChain,
    TransmissionGate,
    find_super_nodes,
)

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
    # NMOS sits on top of a series stack rather than on VGND. The pull-up is
    # a parallel chain and the pull-down is a series chain.
    nand2 = [
        _pfet("X0", "Y", "A", "VPWR"),
        _pfet("X1", "VPWR", "B", "Y"),
        _nfet("X2", "VGND", "B", "mid"),
        _nfet("X3", "mid", "A", "Y"),
    ]
    nodes, leftovers = find_super_nodes(nand2)
    assert [type(n) for n in nodes] == [ParallelChain, SeriesChain]
    assert leftovers == []
    parallel, series = nodes
    assert isinstance(parallel, ParallelChain) and parallel.is_pmos
    assert isinstance(series, SeriesChain) and not series.is_pmos
    assert [t.primitive.instance_name for t in parallel.transistors] == ["X0", "X1"]
    # Free ends are VGND and Y; the chain starts at the lexicographically smaller one.
    assert [t.primitive.instance_name for t in series.transistors] == ["X2", "X3"]


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


def test_nor2_pull_up_is_series_and_pull_down_is_parallel():
    nor2 = [
        _pfet("P0", "mid", "A", "VPWR"),
        _pfet("P1", "Y", "B", "mid"),
        _nfet("N0", "Y", "A", "VGND"),
        _nfet("N1", "VGND", "B", "Y"),
    ]
    nodes, leftovers = find_super_nodes(nor2)
    assert [type(n) for n in nodes] == [ParallelChain, SeriesChain]
    assert leftovers == []
    parallel, series = nodes
    assert isinstance(parallel, ParallelChain) and not parallel.is_pmos
    assert isinstance(series, SeriesChain) and series.is_pmos
    assert parallel.terminals == {"Y", "VGND"}
    assert [t.primitive.instance_name for t in parallel.transistors] == ["N0", "N1"]
    assert [t.primitive.instance_name for t in series.transistors] == ["P0", "P1"]


def test_nand3_series_pull_down_and_parallel_pull_up():
    nand3 = [
        _pfet("P0", "Y", "A", "VPWR"),
        _pfet("P1", "Y", "B", "VPWR"),
        _pfet("P2", "VPWR", "C", "Y"),
        _nfet("N0", "Y", "A", "m1"),
        _nfet("N1", "m1", "B", "m2"),
        _nfet("N2", "m2", "C", "VGND"),
    ]
    nodes, leftovers = find_super_nodes(nand3)
    assert leftovers == []
    assert [type(n) for n in nodes] == [ParallelChain, SeriesChain]
    parallel, series = nodes
    assert isinstance(parallel, ParallelChain)
    assert [t.primitive.instance_name for t in parallel.transistors] == ["P0", "P1", "P2"]
    assert isinstance(series, SeriesChain)
    assert [t.primitive.instance_name for t in series.transistors] == ["N2", "N1", "N0"]


def test_parallel_nmos_on_a_rail_is_not_a_series_chain():
    nodes, leftovers = find_super_nodes(
        [_nfet("N0", "Y", "A", "VGND"), _nfet("N1", "Y", "B", "VGND")]
    )
    assert [type(n) for n in nodes] == [ParallelChain]
    assert leftovers == []


def test_same_gate_fingers_are_one_parallel_chain():
    nodes, _ = find_super_nodes(
        [_nfet("N0", "Y", "A", "VGND"), _nfet("N1", "VGND", "A", "Y")]
    )
    assert len(nodes) == 1
    chain = nodes[0]
    assert isinstance(chain, ParallelChain)
    assert [t.gate for t in chain.transistors] == ["A", "A"]


def test_aoi21_keeps_inner_chains_and_leaves_the_outer_devices():
    # Y = !((A & B) | C). Inner parallel PMOS and inner series NMOS group;
    # the devices that tie those groups to the rails stay loose.
    devices = [
        _pfet("PC", "midp", "C", "VPWR"),
        _pfet("PA", "Y", "A", "midp"),
        _pfet("PB", "Y", "B", "midp"),
        _nfet("NA", "Y", "A", "midn"),
        _nfet("NB", "midn", "B", "VGND"),
        _nfet("NC", "Y", "C", "VGND"),
    ]
    nodes, leftovers = find_super_nodes(devices)
    assert [type(n) for n in nodes] == [ParallelChain, SeriesChain]
    parallel, series = nodes
    assert isinstance(parallel, ParallelChain) and parallel.is_pmos
    assert isinstance(series, SeriesChain) and not series.is_pmos
    assert {t.primitive.instance_name for t in parallel.transistors} == {"PA", "PB"}
    assert {t.primitive.instance_name for t in series.transistors} == {"NA", "NB"}
    assert {primitive.instance_name for primitive in leftovers} == {"PC", "NC"}


def test_output_port_is_not_a_series_junction():
    # Without the port, Y looks like a private node and the three NMOS form
    # one path. As an output it must not join the parallel branch to the stack.
    devices = [
        _nfet("NA", "Y", "A", "mid"),
        _nfet("NB", "mid", "B", "VGND"),
        _nfet("NC", "Y", "C", "VGND"),
    ]
    nodes, leftovers = find_super_nodes(devices, external_nets={"Y"})
    assert [type(n) for n in nodes] == [SeriesChain]
    series = nodes[0]
    assert isinstance(series, SeriesChain)
    assert {t.primitive.instance_name for t in series.transistors} == {"NA", "NB"}
    assert [primitive.instance_name for primitive in leftovers] == ["NC"]


def test_non_transistor_net_is_not_a_series_junction():
    resistor = _primitive(
        name="R1",
        symbol=_symbol(stem="r", device_type="res"),
        nodes=["mid", "x", "y", "z"],
    )
    nodes, leftovers = find_super_nodes(
        [_nfet("N0", "Y", "A", "mid"), _nfet("N1", "mid", "B", "VGND"), resistor]
    )
    assert nodes == []
    assert [primitive.instance_name for primitive in leftovers] == ["R1", "N0", "N1"]


def test_separate_stacks_are_not_joined_at_the_rail():
    devices = [
        _nfet("A0", "Y1", "A", "m1"),
        _nfet("A1", "m1", "B", "VGND"),
        _nfet("B0", "Y2", "C", "m2"),
        _nfet("B1", "m2", "D", "VGND"),
    ]
    nodes, leftovers = find_super_nodes(devices)
    assert leftovers == []
    assert [type(n) for n in nodes] == [SeriesChain, SeriesChain]
    assert [
        [t.primitive.instance_name for t in n.transistors]
        for n in nodes
        if isinstance(n, SeriesChain)
    ] == [["A1", "A0"], ["B1", "B0"]]
