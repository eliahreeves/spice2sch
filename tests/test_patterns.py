from __future__ import annotations

from spice2sch.patterns import (
    CmosGate,
    Inverter,
    ParallelChain,
    SeriesChain,
    SpLeaf,
    SpNetwork,
    SpParallel,
    SpSeries,
    Transistor,
    TransmissionGate,
    _first_match,
    decompose_series_parallel,
    find_super_nodes,
    sp_transistors,
)

from .test_placeable import _pfet_symbol, _primitive, _symbol


def _pfet(name: str, d: str, g: str, s: str):
    return _primitive(name=name, symbol=_pfet_symbol(), nodes=[d, g, s, "VPB"])


def _nfet(name: str, d: str, g: str, s: str):
    return _primitive(name=name, symbol=_symbol(), nodes=[d, g, s, "VNB"])


def _names(network: SpNetwork) -> list[str]:
    return [t.primitive.instance_name for t in sp_transistors(network)]


def _shape(network: SpNetwork) -> tuple[str, list[str]]:
    """``("ser" | "par", names)`` for a network of single transistors."""
    children = network.parts if isinstance(network, SpSeries) else network.branches
    assert all(isinstance(child, SpLeaf) for child in children)
    return ("ser" if isinstance(network, SpSeries) else "par", _names(network))


def _transistors(primitives) -> list[Transistor]:
    return [Transistor.try_from_primitive(p) for p in primitives]


def test_first_match_is_deterministic() -> None:
    pins = {"drain": "net_drain", "d": "net_d"}
    # Alias containers are sets in production code; ordering must still be stable.
    assert _first_match(pins, {"drain", "d"}) == "net_d"


def test_inverter_detected():
    nodes, leftovers = find_super_nodes(
        [_pfet("P", "Y", "A", "VPWR"), _nfet("N", "Y", "A", "VGND")]
    )
    assert [type(n) for n in nodes] == [Inverter]
    assert leftovers == []


def test_nand2_is_one_cmos_gate():
    # sky130_fd_sc_hd__nand2_1: P(A) and N(A) share gate A and net Y, but the
    # NMOS sits on top of a series stack rather than on VGND.
    nand2 = [
        _pfet("X0", "Y", "A", "VPWR"),
        _pfet("X1", "VPWR", "B", "Y"),
        _nfet("X2", "VGND", "B", "mid"),
        _nfet("X3", "mid", "A", "Y"),
    ]
    nodes, leftovers = find_super_nodes(nand2)
    assert leftovers == []
    assert [type(n) for n in nodes] == [CmosGate]
    gate = nodes[0]
    assert isinstance(gate, CmosGate) and gate.output == "Y"
    assert _shape(gate.pull_up) == ("par", ["X0", "X1"])
    # Drawn from the output down to ground.
    assert _shape(gate.pull_down) == ("ser", ["X3", "X2"])


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
    assert leftovers == []
    gate = nodes[0]
    assert isinstance(gate, CmosGate)
    assert _shape(gate.pull_up) == ("ser", ["P0", "P1"])
    assert _shape(gate.pull_down) == ("par", ["N0", "N1"])
    assert gate.pull_down.upper == "Y" and gate.pull_down.lower == "VGND"


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
    gate = nodes[0]
    assert isinstance(gate, CmosGate)
    assert _shape(gate.pull_up) == ("par", ["P0", "P1", "P2"])
    assert _shape(gate.pull_down) == ("ser", ["N0", "N1", "N2"])


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


def test_aoi21_is_a_series_parallel_gate():
    # Y = !((A & B) | C).
    devices = [
        _pfet("PC", "midp", "C", "VPWR"),
        _pfet("PA", "Y", "A", "midp"),
        _pfet("PB", "Y", "B", "midp"),
        _nfet("NA", "Y", "A", "midn"),
        _nfet("NB", "midn", "B", "VGND"),
        _nfet("NC", "Y", "C", "VGND"),
    ]
    nodes, leftovers = find_super_nodes(devices)
    assert leftovers == []
    gate = nodes[0]
    assert isinstance(gate, CmosGate)
    assert isinstance(gate.pull_up, SpSeries)
    first, second = gate.pull_up.parts
    assert _names(first) == ["PC"]
    assert _shape(second) == ("par", ["PA", "PB"])
    assert isinstance(gate.pull_down, SpParallel)
    leaf, stack = gate.pull_down.branches
    assert _names(leaf) == ["NC"]
    assert _shape(stack) == ("ser", ["NA", "NB"])


def test_bridge_network_falls_back_to_chains():
    # A Wheatstone-bridge pull-down is not series-parallel.
    devices = [
        _pfet("P0", "Y", "A", "VPWR"),
        _nfet("N0", "Y", "A", "m1"),
        _nfet("N1", "Y", "B", "m2"),
        _nfet("N2", "m1", "C", "m2"),
        _nfet("N3", "m1", "D", "VGND"),
        _nfet("N4", "m2", "E", "VGND"),
    ]
    pull_down = [t for t in _transistors(devices) if t.is_nmos]
    assert decompose_series_parallel(pull_down, "Y", "VGND") is None
    nodes, leftovers = find_super_nodes(devices)
    assert not any(isinstance(n, CmosGate) for n in nodes)
    assert len(leftovers) + sum(len(n.primitives) for n in nodes) == len(devices)


def test_parallel_inverters_stay_inverters():
    devices = [
        _pfet("P0", "X", "A", "VPWR"),
        _pfet("P1", "VPWR", "A", "X"),
        _nfet("N0", "X", "A", "VGND"),
        _nfet("N1", "VGND", "A", "X"),
    ]
    nodes, leftovers = find_super_nodes(devices)
    assert leftovers == []
    assert [type(n) for n in nodes] == [Inverter, Inverter]


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
