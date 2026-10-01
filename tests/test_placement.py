from __future__ import annotations

import itertools
import random

from spice2sch.models import Point
from spice2sch.patterns import Inverter, TransmissionGate
from spice2sch.placeable import (
    GRID,
    Orientation,
    Placeable,
    PrimitivePlaceable,
    render,
)
from spice2sch.placement import _isotonic, place
from spice2sch.supernode_place import (
    build_placeables,
    from_inverter,
    from_transmission_gate,
)

from .test_placeable import _inverter, _pfet_symbol, _primitive, _symbol, _transistor


def _inverter_between(input_net: str, output_net: str, index: int) -> Placeable:
    p_prim = _primitive(
        name=f"MP{index}",
        symbol=_pfet_symbol(),
        nodes=[output_net, input_net, "VPWR", "VPWR"],
        index=2 * index,
    )
    n_prim = _primitive(
        name=f"MN{index}",
        symbol=_symbol(),
        nodes=[output_net, input_net, "VGND", "VGND"],
        index=2 * index + 1,
    )
    return from_inverter(
        Inverter(
            pmos=_transistor(p_prim, is_pmos=True),
            nmos=_transistor(n_prim, is_pmos=False),
            input_node=input_net,
            output_node=output_net,
        )
    )


def _shuffled_chain(length: int) -> list[Placeable]:
    """Inverters driving each other ``n0 -> n1 -> ... -> n<length>``."""
    chain = [_inverter_between(f"n{i}", f"n{i + 1}", i) for i in range(length)]
    random.Random(1).shuffle(chain)
    return chain


def _overlaps(a: Placeable, b: Placeable) -> bool:
    x, y = a.extent, b.extent
    return (
        x.min_x < y.max_x and y.min_x < x.max_x and x.min_y < y.max_y and y.min_y < x.max_y
    )


def _input_net(placeable: Placeable) -> str:
    return placeable.children["pmos"].nets["G"]  # type: ignore[attr-defined]


def test_place_keeps_extents_apart_and_on_grid():
    placeables = _shuffled_chain(6)
    place(placeables, Point(100, 0), 40, inputs=["n0"], outputs=["n6"])
    for a, b in itertools.combinations(placeables, 2):
        assert not _overlaps(a, b)
    for placeable in placeables:
        assert placeable.pose.origin.x % GRID == 0
        assert placeable.pose.origin.y % GRID == 0
        assert placeable.extent.min_x >= 100 - GRID
        assert placeable.extent.min_y >= 0 - GRID


def test_place_orders_a_chain_by_signal_flow():
    placeables = _shuffled_chain(4)
    by_input = {_input_net(p): p for p in placeables}
    place(placeables, Point(0, 0), 40, inputs=["n0"], outputs=["n4"])
    xs = [by_input[f"n{i}"].center.x for i in range(4)]
    assert xs == sorted(xs) and len(set(xs)) == 4


def test_place_wires_a_chain_straight_across():
    placeables = _shuffled_chain(3)
    wires = place(placeables, Point(0, 0), 40, inputs=["n0"], outputs=["n3"])
    assert {net for net, _, _ in wires} == {"n1", "n2"}
    # Each inverter lines up with the next, so every wire is horizontal.
    assert all(a[1] == b[1] for _, a, b in wires)
    text = render(placeables, wires, external_nets={"n0", "n3"})
    assert "lab=n1}" not in text.replace("{lab=n1}", "")
    assert text.count("sig_type=std_logic lab=n0") == 1
    assert text.count("sig_type=std_logic lab=n3") == 1


def test_place_is_deterministic():
    first, second = _shuffled_chain(5), _shuffled_chain(5)
    wires_first = place(first, Point(0, 0), 40, inputs=["n0"], outputs=["n5"])
    wires_second = place(second, Point(0, 0), 40, inputs=["n0"], outputs=["n5"])
    assert [p.pose for p in first] == [p.pose for p in second]
    assert wires_first == wires_second


def test_place_single_and_empty():
    assert place([], Point(0, 0), 40) == []
    lone = PrimitivePlaceable(_primitive(name="M1", symbol=_symbol(), nodes=["d", "g", "s", "b"]))
    place([lone], Point(100, 0), 40)
    assert lone.extent.min_x >= 100 - GRID


def test_place_never_reorients():
    placeables = _shuffled_chain(4)
    before = [p.pose.orientation for p in placeables]
    place(placeables, Point(0, 0), 40, inputs=["n0"], outputs=["n4"])
    assert [p.pose.orientation for p in placeables] == before
    for placeable in placeables:
        for child in placeable.children.values():  # type: ignore[attr-defined]
            assert child.pose.orientation == Orientation()


def test_place_mirrors_a_transmission_gate_to_face_its_input():
    p_prim = _primitive(name="MP", symbol=_pfet_symbol(), nodes=["OUT", "ENB", "MID", "VPB"])
    n_prim = _primitive(name="MN", symbol=_symbol(), nodes=["OUT", "EN", "MID", "VNB"], index=1)
    tg = from_transmission_gate(
        TransmissionGate(
            pmos=_transistor(p_prim, is_pmos=True),
            nmos=_transistor(n_prim, is_pmos=False),
            terminal_a="OUT",
            terminal_b="MID",
        )
    )
    driver = _inverter_between("IN", "MID", 5)
    place([tg, driver], Point(0, 0), 40, inputs=["IN", "EN", "ENB"], outputs=["OUT"])
    assert driver.center.x < tg.center.x
    left, right = sorted(tg.anchors(), key=lambda anchor: anchor[1][0])
    assert (left[0], right[0]) == ("MID", "OUT")


def test_isotonic_pools_violators():
    assert _isotonic([1.0, 3.0, 2.0], [1.0, 1.0, 1.0]) == [1.0, 2.5, 2.5]
    assert _isotonic([5.0, 1.0], [3.0, 1.0]) == [4.0, 4.0]


def test_leftover_transistors_keep_rails_outside():
    # Listed with the rail on the drain, the pin sky130 draws on top for NMOS.
    nmos = _primitive(name="MN", symbol=_symbol(), nodes=["VGND", "A", "Y", "VNB"])
    pmos = _primitive(name="MP", symbol=_pfet_symbol(), nodes=["VPWR", "A", "Y", "VPB"])
    n_placed, p_placed = build_placeables([], [nmos, pmos])
    assert n_placed.nets["S"] == "VGND" and n_placed.nets["D"] == "Y"
    assert p_placed.nets["S"] == "VPWR" and p_placed.nets["D"] == "Y"
    assert not n_placed.can_swap_diffusion()


def test_only_signal_to_signal_transistors_swap():
    pass_gate = PrimitivePlaceable(
        _primitive(name="MN", symbol=_symbol(), nodes=["X", "EN", "Y", "VNB"])
    )
    assert pass_gate.can_swap_diffusion()
    pass_gate.swap_diffusion()
    assert pass_gate.nets["D"] == "Y" and pass_gate.nets["S"] == "X"


def test_extent_covers_labels():
    placeable = from_inverter(_inverter())
    extent, body = placeable.extent, placeable.bbox
    assert extent.min_x < body.min_x  # input label hangs off the left
    assert extent.max_x > body.max_x  # output label off the right
    assert extent.min_y < body.min_y and extent.max_y > body.max_y
