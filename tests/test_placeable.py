from __future__ import annotations

from pathlib import Path

from spice2sch.models import Point, Primitive
from spice2sch.patterns import (
    Inverter,
    ParallelChain,
    SeriesChain,
    Transistor,
    TransmissionGate,
)
from spice2sch.placeable import (
    CompositePlaceable,
    Orientation,
    Pose,
    PrimitivePlaceable,
    from_inverter,
    from_super_node,
    from_transmission_gate,
    place_in_row,
)
from spice2sch.symbols import BBox, SymbolDef, SymbolPin


def _symbol(
    *,
    stem: str = "nfet",
    device_type: str = "nmos",
    pins: list[tuple[str, float, float]] | None = None,
    bbox: BBox | None = None,
) -> SymbolDef:
    if pins is None:
        pins = [("D", 20.0, -30.0), ("G", -20.0, 0.0), ("S", 20.0, 30.0), ("B", 20.0, 0.0)]
    if bbox is None:
        bbox = BBox(-20.0, -30.0, 20.0, 30.0)
    return SymbolDef(
        path=Path(f"{stem}.sym"),
        library="testlib",
        stem=stem,
        pins=tuple(
            SymbolPin(name=name, x=x, y=y, pinnumber=None, index=i)
            for i, (name, x, y) in enumerate(pins)
        ),
        bbox=bbox,
        device_type=device_type,
        template_model=stem,
        template_attr_names={},
    )


def _pfet_symbol() -> SymbolDef:
    return _symbol(
        stem="pfet",
        device_type="pmos",
        pins=[("D", 20.0, 30.0), ("G", -20.0, 0.0), ("S", 20.0, -30.0), ("B", 20.0, 0.0)],
    )


def _primitive(
    *,
    name: str,
    symbol: SymbolDef,
    nodes: list[str],
    index: int = 0,
) -> Primitive:
    return Primitive(
        id=index,
        instance_name=name,
        nodes=nodes,
        params={"W": "1", "L": "0.15"},
        library="testlib",
        model=symbol.stem,
        symbol=symbol,
    )


def _transistor(primitive: Primitive, is_pmos: bool) -> Transistor:
    drain, gate, source, body = primitive.nodes
    return Transistor(
        primitive=primitive, drain=drain, gate=gate, source=source, body=body, is_pmos=is_pmos
    )


def _inverter() -> Inverter:
    p_prim = _primitive(name="MP", symbol=_pfet_symbol(), nodes=["Y", "A", "VPWR", "VPWR"])
    n_prim = _primitive(name="MN", symbol=_symbol(), nodes=["Y", "A", "VGND", "VGND"], index=1)
    return Inverter(
        pmos=_transistor(p_prim, is_pmos=True),
        nmos=_transistor(n_prim, is_pmos=False),
        input_node="A",
        output_node="Y",
    )


def test_orientation_transform_matches_xschem():
    assert Orientation(0, 0).transform(3.0, 4.0) == (3.0, 4.0)
    assert Orientation(0, 1).transform(3.0, 4.0) == (-3.0, 4.0)
    assert Orientation(1, 0).transform(3.0, 4.0) == (-4.0, 3.0)
    assert Orientation(2, 0).transform(3.0, 4.0) == (-3.0, -4.0)
    assert Orientation(3, 0).transform(3.0, 4.0) == (4.0, -3.0)
    assert Orientation(1, 1).transform(3.0, 4.0) == (-4.0, -3.0)


def test_orientation_compose():
    all_orientations = [Orientation(rot, flip) for rot in range(4) for flip in (0, 1)]
    for parent in all_orientations:
        for child in all_orientations:
            composed = parent.compose(child)
            for x, y in ((1.0, 0.0), (0.0, 1.0), (3.0, -2.0)):
                assert composed.transform(x, y) == parent.transform(*child.transform(x, y))


def test_bbox_helpers():
    box = BBox(-10.0, -20.0, 10.0, 20.0)
    assert box.center == (0.0, 0.0)
    other = BBox(0.0, 0.0, 30.0, 5.0)
    assert box.union(other) == BBox(-10.0, -20.0, 30.0, 20.0)
    rotated = box.map_points(Orientation(1, 0).transform)
    assert rotated == BBox(-20.0, -10.0, 20.0, 10.0)


def test_primitive_place_center_and_port():
    prim = _primitive(name="M1", symbol=_symbol(), nodes=["d", "g", "s", "b"])
    placeable = PrimitivePlaceable(prim)

    placeable.place_center(Point(100, 50))
    assert placeable.center == Point(100, 50)
    assert placeable.pose.origin == Point(100, 50)

    placeable.place_port("G", Point(0, 0))
    assert placeable.port_position("G") == Point(0, 0)
    assert placeable.pose.origin == Point(20, 0)

    placeable.place_port("g", Point(10, 10))  # case-insensitive
    assert placeable.port_position("G") == Point(10, 10)


def test_primitive_orientation_preserves_center():
    prim = _primitive(name="M1", symbol=_symbol(), nodes=["d", "g", "s", "b"])
    placeable = PrimitivePlaceable(prim)
    placeable.place_center(Point(100, 50))

    placeable.set_orientation(Orientation(1, 0))
    assert placeable.center == Point(100, 50)
    assert placeable.pose.orientation == Orientation(1, 0)
    # Gate was at local (-20, 0); after rot=1 -> (0, -20) relative to origin
    origin = placeable.pose.origin
    assert placeable.port_position("G") == Point(origin.x, origin.y - 20)


def test_primitive_draw_emits_symbol_and_labels():
    prim = _primitive(name="M1", symbol=_symbol(), nodes=["out", "in", "vss", "vss"])
    placeable = PrimitivePlaceable(prim)
    placeable.place_center(Point(40, 0))

    text = placeable.draw()
    assert "C {testlib/nfet.sym} 40 0 0 0" in text
    assert "name=M1" in text
    assert "lab=out" in text
    assert "lab=in" in text
    assert "lab_pin.sym" in text


def test_composite_place_port_moves_children():
    pmos = PrimitivePlaceable(
        _primitive(name="MP", symbol=_pfet_symbol(), nodes=["out", "in", "vdd", "vdd"])
    )
    nmos = PrimitivePlaceable(
        _primitive(name="MN", symbol=_symbol(), nodes=["out", "in", "vss", "vss"], index=1)
    )
    pmos.pose = Pose(Point(0, -50))
    nmos.pose = Pose(Point(0, 50))
    composite = CompositePlaceable(
        children={"pmos": pmos, "nmos": nmos},
        ports={"in": ("pmos", "G"), "out": ("pmos", "D")},
    )

    composite.place_port("in", Point(200, 100))
    assert composite.port_position("in") == Point(200, 100)
    assert composite.bbox == BBox(200.0, 70.0, 240.0, 230.0)

    text = composite.draw()
    assert "C {testlib/pfet.sym} 220 100 0 0" in text
    assert "C {testlib/nfet.sym} 220 200 0 0" in text
    assert "name=MP" in text
    assert "name=MN" in text


def test_composite_rotation_carries_children():
    pmos = PrimitivePlaceable(
        _primitive(name="MP", symbol=_pfet_symbol(), nodes=["out", "in", "vdd", "vdd"])
    )
    pmos.pose = Pose(Point(0, -50))
    composite = CompositePlaceable(children={"pmos": pmos}, ports={"in": ("pmos", "G")})

    composite.pose = Pose(Point(0, 0), Orientation(1, 0))
    # Child origin (0, -50) rotates to (50, 0); the gate offset (-20, 0) to (0, -20).
    assert composite.port_position("in") == Point(50, -20)
    assert "C {testlib/pfet.sym} 50 0 1 0" in composite.draw()


def test_from_inverter_labels_gates_and_butts_drains():
    placeable = from_inverter(_inverter())
    placeable.place_center(Point(0, 0))
    text = placeable.draw()
    assert text.count("sig_type=std_logic lab=A") == 2
    assert text.count("sig_type=std_logic lab=Y") == 1
    assert "{lab=A}" not in text
    assert "{lab=Y}" not in text
    assert "\nN " not in text
    assert text.count("sig_type=std_logic lab=VPWR") == 2
    assert text.count("sig_type=std_logic lab=VGND") == 2

    pmos = placeable.children["pmos"]
    nmos = placeable.children["nmos"]
    assert pmos.nets["S"] == "VPWR"
    assert pmos.nets["D"] == "Y"
    assert nmos.nets["S"] == "VGND"
    assert nmos.nets["D"] == "Y"
    assert pmos.port_position("D") == nmos.port_position("D")
    assert pmos.port_position("G").x == nmos.port_position("G").x


def test_from_inverter_only_skips_wired_pins():
    inv = _inverter()
    # Body tied to the output net must still get its own label.
    p_prim = _primitive(name="MP", symbol=_pfet_symbol(), nodes=["Y", "A", "VPWR", "Y"])
    inv = Inverter(
        pmos=_transistor(p_prim, is_pmos=True),
        nmos=inv.nmos,
        input_node="A",
        output_node="Y",
    )
    text = from_inverter(inv).draw()
    assert text.count("sig_type=std_logic lab=Y") == 2


def test_from_inverter_stacks_pmos_above_nmos():
    placeable = from_inverter(_inverter())
    assert set(placeable.port_names()) == {"in", "out", "vdd", "vss"}

    pmos = placeable.children["pmos"]
    nmos = placeable.children["nmos"]
    shared = pmos.port_position("D")
    assert shared == nmos.port_position("D")
    assert pmos.bbox.max_y <= nmos.bbox.min_y
    assert pmos.port_position("S").y < shared.y < nmos.port_position("S").y
    assert pmos.port_position("G").x == nmos.port_position("G").x


def test_from_transmission_gate_ports():
    p_prim = _primitive(name="MP", symbol=_pfet_symbol(), nodes=["A", "ENB", "B", "VPWR"])
    n_prim = _primitive(name="MN", symbol=_symbol(), nodes=["A", "EN", "B", "VGND"], index=1)
    tg = TransmissionGate(
        pmos=_transistor(p_prim, is_pmos=True),
        nmos=_transistor(n_prim, is_pmos=False),
        terminal_a="A",
        terminal_b="B",
    )

    placeable = from_transmission_gate(tg)
    assert set(placeable.port_names()) == {"a", "b", "en", "enb"}
    placeable.place_port("a", Point(50, 50))
    assert placeable.port_position("a") == Point(50, 50)


def test_from_transmission_gate_layout():
    # NMOS listed with A/B on source/drain reversed relative to the PMOS.
    p_prim = _primitive(name="MP", symbol=_pfet_symbol(), nodes=["A", "ENB", "B", "VPB"])
    n_prim = _primitive(name="MN", symbol=_symbol(), nodes=["B", "EN", "A", "VNB"], index=1)
    tg = TransmissionGate(
        pmos=_transistor(p_prim, is_pmos=True),
        nmos=_transistor(n_prim, is_pmos=False),
        terminal_a="A",
        terminal_b="B",
    )
    placeable = from_transmission_gate(tg)
    pmos = placeable.children["pmos"]
    nmos = placeable.children["nmos"]

    assert pmos.bbox.max_y < nmos.bbox.min_y
    enb, en = pmos.port_position("G"), nmos.port_position("G")
    assert enb.x == pmos.center.x and enb.y < pmos.center.y  # gate points up
    assert en.x == nmos.center.x and en.y > nmos.center.y  # gate points down

    for pin, net in (("D", "A"), ("S", "B")):
        assert pmos.nets[pin] == net and nmos.nets[pin] == net
        assert pmos.port_position(pin).x == nmos.port_position(pin).x

    text = placeable.draw()
    assert "{lab=A}" in text and "{lab=B}" in text
    assert text.count("sig_type=std_logic lab=A") == 1
    assert text.count("sig_type=std_logic lab=B") == 1


def test_from_series_chain_stacks_output_above_ground():
    # Listed ground-end first, the order the finder emits. Placement flips it.
    n_gnd = _primitive(name="N1", symbol=_symbol(), nodes=["mid", "B", "VGND", "VNB"])
    n_out = _primitive(name="N0", symbol=_symbol(), nodes=["Y", "A", "mid", "VNB"], index=1)
    chain = SeriesChain(
        transistors=(_transistor(n_gnd, is_pmos=False), _transistor(n_out, is_pmos=False)),
        is_pmos=False,
    )
    placeable = from_super_node(chain)
    top, bottom = placeable.children["t0"], placeable.children["t1"]

    assert top.primitive.instance_name == "N0"
    assert top.nets["D"] == "Y" and top.nets["S"] == "mid"
    assert bottom.nets["D"] == "mid" and bottom.nets["S"] == "VGND"
    assert top.port_position("S") == bottom.port_position("D")
    assert top.bbox.max_y <= bottom.bbox.min_y
    assert top.port_position("G").x == bottom.port_position("G").x
    assert set(placeable.port_names()) == {"top", "bot", "g0", "g1"}

    text = placeable.draw()
    assert text.count("sig_type=std_logic lab=mid") == 1
    assert "{lab=mid}" not in text


def test_from_series_chain_puts_power_on_top_of_pmos():
    p_out = _primitive(name="P1", symbol=_pfet_symbol(), nodes=["Y", "B", "mid", "VPB"])
    p_pwr = _primitive(
        name="P0", symbol=_pfet_symbol(), nodes=["mid", "A", "VPWR", "VPB"], index=1
    )
    chain = SeriesChain(
        transistors=(_transistor(p_out, is_pmos=True), _transistor(p_pwr, is_pmos=True)),
        is_pmos=True,
    )
    placeable = from_super_node(chain)
    top, bottom = placeable.children["t0"], placeable.children["t1"]

    assert top.primitive.instance_name == "P0"
    assert top.nets["S"] == "VPWR" and top.nets["D"] == "mid"
    assert bottom.nets["S"] == "mid" and bottom.nets["D"] == "Y"
    assert top.port_position("D") == bottom.port_position("S")
    assert top.bbox.max_y <= bottom.bbox.min_y


def test_from_series_chain_of_three_is_monotonic():
    n2 = _primitive(name="N2", symbol=_symbol(), nodes=["m2", "C", "VGND", "VNB"])
    n1 = _primitive(name="N1", symbol=_symbol(), nodes=["m1", "B", "m2", "VNB"], index=1)
    n0 = _primitive(name="N0", symbol=_symbol(), nodes=["Y", "A", "m1", "VNB"], index=2)
    chain = SeriesChain(
        transistors=tuple(_transistor(n, is_pmos=False) for n in (n2, n1, n0)),
        is_pmos=False,
    )
    placeable = from_super_node(chain)
    t0, t1, t2 = (placeable.children[key] for key in ("t0", "t1", "t2"))

    assert t0.nets["D"] == "Y" and t2.nets["S"] == "VGND"
    assert t0.port_position("S") == t1.port_position("D")
    assert t1.port_position("S") == t2.port_position("D")
    assert t0.bbox.max_y <= t1.bbox.min_y <= t1.bbox.max_y <= t2.bbox.min_y
    text = placeable.draw()
    assert text.count("sig_type=std_logic lab=m1") == 1
    assert text.count("sig_type=std_logic lab=m2") == 1


def test_from_series_chain_labels_a_shared_gate():
    n0 = _primitive(name="N0", symbol=_symbol(), nodes=["Y", "A", "mid", "VNB"])
    n1 = _primitive(name="N1", symbol=_symbol(), nodes=["mid", "A", "VGND", "VNB"], index=1)
    chain = SeriesChain(
        transistors=(_transistor(n0, is_pmos=False), _transistor(n1, is_pmos=False)),
        is_pmos=False,
    )
    text = from_super_node(chain).draw()
    assert text.count("sig_type=std_logic lab=A") == 2
    assert "{lab=A}" not in text


def test_from_series_chain_does_not_wire_a_gate_across_the_middle_device():
    n0 = _primitive(name="N0", symbol=_symbol(), nodes=["Y", "A", "m1", "VNB"])
    n1 = _primitive(name="N1", symbol=_symbol(), nodes=["m1", "B", "m2", "VNB"], index=1)
    n2 = _primitive(name="N2", symbol=_symbol(), nodes=["m2", "A", "VGND", "VNB"], index=2)
    chain = SeriesChain(
        transistors=tuple(_transistor(n, is_pmos=False) for n in (n0, n1, n2)),
        is_pmos=False,
    )
    text = from_super_node(chain).draw()
    assert text.count("sig_type=std_logic lab=A") == 2
    assert text.count("sig_type=std_logic lab=B") == 1
    assert "{lab=A}" not in text


def test_from_parallel_chain_ties_diffusions_and_splits_gates():
    p0 = _primitive(name="P0", symbol=_pfet_symbol(), nodes=["Y", "A", "VPWR", "VPB"])
    p1 = _primitive(name="P1", symbol=_pfet_symbol(), nodes=["VPWR", "B", "Y", "VPB"], index=1)
    chain = ParallelChain(
        transistors=(_transistor(p0, is_pmos=True), _transistor(p1, is_pmos=True)),
        is_pmos=True,
    )
    placeable = from_super_node(chain)
    left, right = placeable.children["t0"], placeable.children["t1"]

    assert left.nets["S"] == "VPWR" and left.nets["D"] == "Y"
    assert right.nets["S"] == "VPWR" and right.nets["D"] == "Y"
    assert left.port_position("S").y == right.port_position("S").y
    assert left.port_position("D").y == right.port_position("D").y
    assert right.bbox.min_x == left.bbox.max_x + 60
    assert set(placeable.port_names()) == {"top", "bot", "g0", "g1"}

    text = placeable.draw()
    assert text.count("sig_type=std_logic lab=VPWR") == 1
    assert text.count("sig_type=std_logic lab=Y") == 1
    assert text.count("sig_type=std_logic lab=A") == 1
    assert text.count("sig_type=std_logic lab=B") == 1
    assert "{lab=VPWR}" in text and "{lab=Y}" in text
    assert "{lab=A}" not in text


def test_from_parallel_chain_labels_shared_gates_instead_of_crossing_body():
    n0 = _primitive(name="N0", symbol=_symbol(), nodes=["Y", "A", "VGND", "VNB"])
    n1 = _primitive(name="N1", symbol=_symbol(), nodes=["VGND", "A", "Y", "VNB"], index=1)
    chain = ParallelChain(
        transistors=(_transistor(n0, is_pmos=False), _transistor(n1, is_pmos=False)),
        is_pmos=False,
    )
    placeable = from_super_node(chain)
    left = placeable.children["t0"]
    assert left.nets["D"] == "Y" and left.nets["S"] == "VGND"
    text = placeable.draw()
    # A straight gate-to-gate wire would run over N0's body pin and short A to VNB.
    assert text.count("sig_type=std_logic lab=A") == 2
    assert "{lab=A}" not in text


def test_from_parallel_chain_of_three_labels_each_tie_once():
    devices = [
        _primitive(name=name, symbol=_symbol(), nodes=["Y", "A", "VGND", "VNB"], index=index)
        for index, name in enumerate(("N0", "N1", "N2"))
    ]
    chain = ParallelChain(
        transistors=tuple(_transistor(device, is_pmos=False) for device in devices),
        is_pmos=False,
    )
    text = from_super_node(chain).draw()
    assert text.count("sig_type=std_logic lab=Y") == 1
    assert text.count("sig_type=std_logic lab=VGND") == 1
    assert text.count("sig_type=std_logic lab=A") == 3
    assert text.count("{lab=Y}") == 2


def test_place_in_row_uses_bbox_width():
    wide = _symbol(bbox=BBox(-40.0, -10.0, 40.0, 10.0))
    narrow = _symbol(stem="r", device_type="res", bbox=BBox(-10.0, -5.0, 10.0, 5.0))
    a = PrimitivePlaceable(_primitive(name="M1", symbol=wide, nodes=["d", "g", "s", "b"]))
    b = PrimitivePlaceable(
        _primitive(name="R1", symbol=narrow, nodes=["d", "g", "s", "b"], index=1)
    )

    place_in_row([a, b], Point(100, 0), spacing=20)
    assert a.bbox.min_x == 100
    assert b.bbox.min_x == a.bbox.max_x + 20
    assert a.center.y == 0
    assert b.center.y == 0
