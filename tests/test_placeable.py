from __future__ import annotations

from pathlib import Path

from spice2sch.models import Point, Primitive
from spice2sch.patterns import Inverter, Transistor, TransmissionGate
from spice2sch.placeable import (
    CompositePlaceable,
    Orientation,
    Pose,
    PrimitivePlaceable,
    from_inverter,
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


def test_from_inverter_wires_only_the_gates():
    placeable = from_inverter(_inverter())
    placeable.place_center(Point(0, 0))
    text = placeable.draw()
    assert text.count("sig_type=std_logic lab=A") == 1
    assert text.count("sig_type=std_logic lab=Y") == 1
    assert "{lab=A}" in text
    assert "{lab=Y}" not in text
    assert text.count("\nN ") == 1
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
