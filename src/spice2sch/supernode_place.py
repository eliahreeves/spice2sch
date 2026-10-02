from __future__ import annotations

import math
from dataclasses import dataclass
from functools import reduce
from typing import Dict, Iterable, List, Sequence, Tuple

from spice2sch.models import Point, Primitive
from spice2sch.patterns import (
    BODY_ALIASES,
    DRAIN_ALIASES,
    GATE_ALIASES,
    SOURCE_ALIASES,
    CmosGate,
    Inverter,
    ParallelChain,
    SeriesChain,
    SpLeaf,
    SpNetwork,
    SpSeries,
    SuperNode,
    Transistor,
    TransmissionGate,
)
from spice2sch.placeable import (
    GRID,
    XY,
    Anchor,
    CompositePlaceable,
    Orientation,
    Placeable,
    Pose,
    PrimitivePlaceable,
    Segment,
    _label_extent,
    _overlaps,
    _pin_direction,
    _pin_name,
    _segment_box,
    body_box,
    box_interior_hit,
    is_rail,
    on_segment,
    segments_cross,
)
from spice2sch.spice import GROUND_NETS, POWER_NETS
from spice2sch.symbols import BBox


def _snap_up(value: float) -> int:
    return int(math.ceil(value / GRID)) * GRID


# Wire run between stacked blocks, and between a block and its bus.
_STACK_GAP = 20
# Clearance between side-by-side branches' labels.
_BRANCH_GAP = 20
# Room for the two body-pin labels, which face each other across the gap.
_TG_STACK_GAP = 100
# Length of the stubs that bring a terminal out to a label or a wire.
_STUB = 20


def _diffusion_pins_by_height(primitive: Primitive) -> Tuple[str, str]:
    """``(upper, lower)`` diffusion pin names. Smaller local y is above."""
    drain_name = _pin_name(primitive, DRAIN_ALIASES)
    source_name = _pin_name(primitive, SOURCE_ALIASES)
    drain_y = next(pin.y for pin in primitive.symbol.pins if pin.name == drain_name)
    source_y = next(pin.y for pin in primitive.symbol.pins if pin.name == source_name)
    if drain_y <= source_y:
        return drain_name, source_name
    return source_name, drain_name


def _with_diffusion_nets(
    transistor: Transistor, upper_net: str, lower_net: str
) -> Tuple[PrimitivePlaceable, str, str]:
    """Put ``upper_net`` on the pin drawn above ``lower_net``.

    Swapping drain and source is LVS-safe because MOSFETs are symmetric.
    """
    primitive = transistor.primitive
    upper_pin, lower_pin = _diffusion_pins_by_height(primitive)
    placeable = PrimitivePlaceable(
        primitive,
        net_overrides={upper_pin: upper_net, lower_pin: lower_net},
    )
    return placeable, upper_pin, lower_pin


def _side_pin_extent(device: PrimitivePlaceable) -> BBox:
    """Body plus the labels on its gate and body pins, in its parent's frame."""
    box = device.bbox
    for pin in device.primitive.symbol.pins:
        if pin.name.lower() in GATE_ALIASES | BODY_ALIASES:
            point = device.pose.apply(pin.x, pin.y)
            direction = device.pose.turn(_pin_direction(pin.x, pin.y))
            box = box.union(_label_extent(point, direction, device.nets[pin.name]))
    return box


@dataclass
class _Block:
    """A drawn two-terminal network: devices, wires, and its end points."""

    devices: List[PrimitivePlaceable]
    wires: List[Segment]
    top: XY
    bottom: XY
    top_net: str
    bottom_net: str
    # True when the end is a device pin that a neighbor can butt against.
    top_is_pin: bool
    bottom_is_pin: bool

    def move(self, dx: int, dy: int) -> None:
        for device in self.devices:
            device.move_by(dx, dy)
        self.wires = [
            (net, (a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy))
            for net, a, b in self.wires
        ]
        self.top = (self.top[0] + dx, self.top[1] + dy)
        self.bottom = (self.bottom[0] + dx, self.bottom[1] + dy)

    @property
    def extent(self) -> BBox:
        boxes = [_side_pin_extent(device) for device in self.devices]
        boxes.extend(_segment_box(wire) for wire in self.wires)
        return reduce(BBox.union, boxes)


def _leaf(transistor: Transistor, upper_net: str, lower_net: str) -> _Block:
    device, upper_pin, lower_pin = _with_diffusion_nets(
        transistor, upper_net, lower_net
    )
    top, bottom = device.port_position(upper_pin), device.port_position(lower_pin)
    return _Block(
        [device],
        [],
        (top.x, top.y),
        (bottom.x, bottom.y),
        upper_net,
        lower_net,
        True,
        True,
    )


def _push_below(upper: BBox, lower: _Block) -> None:
    while _overlaps(upper, lower.extent):
        lower.move(0, GRID)


def _stack(blocks: Sequence[_Block]) -> _Block:
    """Blocks top to bottom, each one's top under the previous one's bottom."""
    result = blocks[0]
    for block in blocks[1:]:
        block.move(result.bottom[0] - block.top[0], 0)
        if result.bottom_is_pin and block.top_is_pin:
            block.move(0, result.bottom[1] - block.top[1])
        else:
            block.move(0, result.bottom[1] + _STACK_GAP - block.top[1])
            _push_below(result.extent, block)
        wires = result.wires + block.wires
        if result.bottom != block.top:
            wires.append((result.bottom_net, result.bottom, block.top))
        result = _Block(
            result.devices + block.devices,
            wires,
            result.top,
            block.bottom,
            result.top_net,
            block.bottom_net,
            result.top_is_pin,
            block.bottom_is_pin,
        )
    return result


def _bus(net: str, xs: Iterable[int], y: int) -> List[Segment]:
    ordered = sorted(set(xs))
    return [(net, (a, y), (b, y)) for a, b in zip(ordered, ordered[1:])]


def _snap(value: float) -> int:
    return int(round(value / GRID)) * GRID


def _side_by_side(blocks: Sequence[_Block]) -> _Block:
    """Blocks left to right with their tops level, tied by a bus at each end."""
    for block in blocks:
        block.move(0, -block.top[1])
    right = blocks[0].extent.max_x
    for block in blocks[1:]:
        shift = _snap_up(right + _BRANCH_GAP - block.extent.min_x)
        block.move(shift, 0)
        right = block.extent.max_x
    max_bottom = max(block.bottom[1] for block in blocks)
    top_y = -_STACK_GAP
    bottom_y = max_bottom + _STACK_GAP
    # Center shorter blocks vertically between the buses so that single
    # transistors sit in the middle of their stub wires.
    for block in blocks:
        slack = max_bottom - block.bottom[1]
        if slack > 0:
            block.move(0, _snap(slack / 2))
    first = blocks[0]
    wires: List[Segment] = []
    for block in blocks:
        wires.extend(block.wires)
        wires.append((first.top_net, block.top, (block.top[0], top_y)))
        wires.append((first.bottom_net, block.bottom, (block.bottom[0], bottom_y)))
    wires.extend(_bus(first.top_net, (b.top[0] for b in blocks), top_y))
    wires.extend(_bus(first.bottom_net, (b.bottom[0] for b in blocks), bottom_y))
    # Use the center of the bus as the connection point so that stacking
    # centers narrower blocks on wider ones and the source/drain rails
    # run straight through the middle.
    top_xs = sorted(set(b.top[0] for b in blocks))
    mid_x = _snap((top_xs[0] + top_xs[-1]) / 2)
    return _Block(
        [device for block in blocks for device in block.devices],
        wires,
        (mid_x, top_y),
        (mid_x, bottom_y),
        first.top_net,
        first.bottom_net,
        False,
        False,
    )


def _network(network: SpNetwork) -> _Block:
    if isinstance(network, SpLeaf):
        return _leaf(network.transistor, network.upper, network.lower)
    if isinstance(network, SpSeries):
        return _stack([_network(part) for part in network.parts])
    return _side_by_side([_network(branch) for branch in network.branches])


def _gate_pins(devices: Sequence[PrimitivePlaceable]) -> Dict[str, List[XY]]:
    pins: Dict[str, List[XY]] = {}
    for device in devices:
        if Transistor.try_from_primitive(device.primitive) is None:
            continue
        name = _pin_name(device.primitive, GATE_ALIASES)
        position = device.port_position(name)
        pins.setdefault(device.nets[name], []).append((position.x, position.y))
    return pins


def _is_clear(
    candidate: Segment,
    devices: Sequence[PrimitivePlaceable],
    wires: Sequence[Segment],
) -> bool:
    """True if a new wire touches no pin, wire, or body that isn't its own net."""
    net, start, end = candidate
    for device in devices:
        if box_interior_hit(candidate, body_box(device, device.pose)):
            return False
        for pin_net, (x, y) in device.local_net_pins():
            point = device.pose.apply_xy((x, y))
            if pin_net != net and on_segment(point, start, end):
                return False
    for wire in wires:
        if wire[0] != net and segments_cross(candidate, wire):
            return False
    return True


def _tie_gates(
    devices: Sequence[PrimitivePlaceable], wires: List[Segment], anchors: List[Anchor]
) -> None:
    """Wire together gates of one net that line up vertically, with an input
    stub off the middle when there's room. Others keep their own labels."""
    for net, points in sorted(_gate_pins(devices).items()):
        if len(points) < 2 or is_rail(net) or len({x for x, _ in points}) != 1:
            continue
        x = points[0][0]
        ys = sorted(y for _, y in points)
        ties = [(net, (x, a), (x, b)) for a, b in zip(ys, ys[1:])]
        if not all(_is_clear(tie, devices, wires) for tie in ties):
            continue
        top, bottom = ys[0], ys[-1]
        middle = int(round((top + bottom) / 2 / GRID)) * GRID
        stub: Segment = (net, (x - _STUB, middle), (x, middle))
        if top < middle < bottom and _is_clear(stub, devices, wires + ties):
            split: List[Segment] = []
            for tie in ties:
                (_, (_, a), (_, b)) = tie
                if a < middle < b:
                    split.extend(
                        [(net, (x, a), (x, middle)), (net, (x, middle), (x, b))]
                    )
                else:
                    split.append(tie)
            wires.extend(split + [stub])
            anchors.append((net, stub[1], (-1, 0)))
        else:
            wires.extend(ties)
            anchors.append((net, (x, top), (-1, 0)))


def _from_networks(
    pull_up: _Block, pull_down: _Block, output: str
) -> Tuple[List[PrimitivePlaceable], List[Segment], List[Anchor], XY]:
    """Pull-up over pull-down, joined at the output, which runs out to the right."""
    pull_down.move(pull_up.bottom[0] - pull_down.top[0], 0)
    if pull_up.bottom_is_pin and pull_down.top_is_pin:
        pull_down.move(0, pull_up.bottom[1] - pull_down.top[1])
        junction = pull_up.bottom
    else:
        pull_down.move(0, pull_up.bottom[1] + 2 * _STACK_GAP - pull_down.top[1])
        while _overlaps(pull_up.extent, pull_down.extent):
            pull_down.move(0, 2 * GRID)
        junction = (pull_up.bottom[0], pull_up.bottom[1] + _STACK_GAP)
    devices = pull_up.devices + pull_down.devices
    wires = pull_up.wires + pull_down.wires
    for end in (pull_up.bottom, pull_down.top):
        if end != junction:
            wires.append((output, end, junction))
    # Past the devices, with at least a short stub off the junction. Body-pin
    # labels sit at other y's, so the output does not clear them.
    right = _snap_up(
        max([junction[0] + _STUB] + [d.bbox.max_x + GRID for d in devices])
    )
    out = (right, junction[1])
    wires.append((output, junction, out))
    anchors: List[Anchor] = [
        (pull_up.top_net, pull_up.top, (0, -1)),
        (pull_down.bottom_net, pull_down.bottom, (0, 1)),
        (output, out, (1, 0)),
    ]
    _tie_gates(devices, wires, anchors)
    return devices, wires, anchors, out


def _cmos_children(
    devices: Sequence[PrimitivePlaceable],
) -> Dict[str, PrimitivePlaceable]:
    return {f"t{index}": device for index, device in enumerate(devices)}


def from_cmos_gate(gate: CmosGate) -> CompositePlaceable:
    """Pull-up network over pull-down network, drawn series-parallel with
    wires, the output brought out on the right and rails labeled top and
    bottom."""
    devices, wires, anchors, _ = _from_networks(
        _network(gate.pull_up), _network(gate.pull_down), gate.output
    )
    return CompositePlaceable(_cmos_children(devices), wires=wires, anchors=anchors)


def _rail_for(transistor: Transistor, output: str) -> str:
    return transistor.other_diffusion(output)


def from_inverter(inv: Inverter) -> CompositePlaceable:
    """CMOS stack: PMOS above NMOS, drains butted at the output, which runs
    out to the right. The gates are tied with the input coming in on the left.
    """
    pull_up = _leaf(inv.pmos, _rail_for(inv.pmos, inv.output_node), inv.output_node)
    pull_down = _leaf(inv.nmos, inv.output_node, _rail_for(inv.nmos, inv.output_node))
    devices, wires, anchors, _ = _from_networks(pull_up, pull_down, inv.output_node)
    pmos, nmos = devices
    p_prim, n_prim = inv.pmos.primitive, inv.nmos.primitive
    return CompositePlaceable(
        children={"pmos": pmos, "nmos": nmos},
        ports={
            "in": ("pmos", _pin_name(p_prim, GATE_ALIASES)),
            "out": ("pmos", _pin_name(p_prim, DRAIN_ALIASES)),
            "vdd": ("pmos", _pin_name(p_prim, SOURCE_ALIASES)),
            "vss": ("nmos", _pin_name(n_prim, SOURCE_ALIASES)),
        },
        wires=wires,
        anchors=anchors,
    )


def _tg_half(
    transistor: Transistor, tg: TransmissionGate, orientation: Orientation
) -> PrimitivePlaceable:
    """Put ``terminal_a`` on the drain pin and ``terminal_b`` on the source pin,
    so both devices line up the same way (LVS-safe: MOSFETs are symmetric)."""
    primitive = transistor.primitive
    half = PrimitivePlaceable(
        primitive,
        net_overrides={
            _pin_name(primitive, DRAIN_ALIASES): tg.terminal_a,
            _pin_name(primitive, SOURCE_ALIASES): tg.terminal_b,
        },
    )
    half.pose = Pose(orientation=orientation)
    return half


def _stack_above(
    upper: Placeable,
    upper_pin: str,
    lower: Placeable,
    lower_pin: str,
    gap: float = _STACK_GAP,
) -> None:
    """Move ``upper`` to sit ``gap`` above ``lower`` with the two pins x-aligned."""
    dx = lower.port_position(lower_pin).x - upper.port_position(upper_pin).x
    dy = lower.bbox.min_y - gap - upper.bbox.max_y
    upper.move_by(dx, dy)


def from_transmission_gate(tg: TransmissionGate) -> CompositePlaceable:
    """PMOS on top with its gate up, NMOS below with its gate down, the
    drains and sources wired straight across, and each side brought out to a
    terminal: ``terminal_a`` on the left, ``terminal_b`` on the right."""
    # sky130 FET symbols have the gate on the left: rot=1 turns it up, rot=3 down.
    pmos = _tg_half(tg.pmos, tg, Orientation(1, 0))
    nmos = _tg_half(tg.nmos, tg, Orientation(3, 0))
    p_prim, n_prim = tg.pmos.primitive, tg.nmos.primitive
    p_drain, n_drain = (
        _pin_name(p_prim, DRAIN_ALIASES),
        _pin_name(n_prim, DRAIN_ALIASES),
    )
    p_source, n_source = (
        _pin_name(p_prim, SOURCE_ALIASES),
        _pin_name(n_prim, SOURCE_ALIASES),
    )
    _stack_above(pmos, p_drain, nmos, n_drain, gap=_TG_STACK_GAP)

    wires: List[Segment] = []
    anchors: List[Anchor] = []
    for net, p_pin, n_pin in (
        (tg.terminal_a, p_drain, n_drain),
        (tg.terminal_b, p_source, n_source),
    ):
        top, bottom = pmos.port_position(p_pin), nmos.port_position(n_pin)
        middle = int(round((top.y + bottom.y) / 2 / GRID)) * GRID
        side = -1 if top.x < pmos.center.x else 1
        end = (top.x + side * _STUB, middle)
        wires += [
            (net, (top.x, top.y), (top.x, middle)),
            (net, (top.x, middle), (bottom.x, bottom.y)),
            (net, (top.x, middle), end),
        ]
        anchors.append((net, end, (side, 0)))

    placeable = CompositePlaceable(
        children={"pmos": pmos, "nmos": nmos},
        ports={
            "a": ("pmos", p_drain),
            "b": ("pmos", p_source),
            "en": ("nmos", _pin_name(n_prim, GATE_ALIASES)),
            "enb": ("pmos", _pin_name(p_prim, GATE_ALIASES)),
        },
        wires=wires,
        anchors=anchors,
    )
    if {tg.terminal_a, tg.terminal_b}.isdisjoint({tg.pmos.gate, tg.nmos.gate}):
        placeable.sides = (tg.terminal_a, tg.terminal_b)
    return placeable


def _prefers_top(net: str) -> int:
    """Higher means the net should sit at the top of a drawn chain."""
    upper = net.upper()
    if upper in POWER_NETS:
        return 2
    if upper in GROUND_NETS:
        return 0
    return 1


def _top_sort_key(net: str) -> Tuple[int, str]:
    return (-_prefers_top(net), net)


def _must_share(left: Transistor, right: Transistor) -> str:
    shared = set(left.diffusion_terminals) & set(right.diffusion_terminals)
    if len(shared) != 1:
        raise ValueError(
            f"{left.primitive.instance_name} and {right.primitive.instance_name} "
            "do not share exactly one diffusion net"
        )
    return next(iter(shared))


def _free_end(transistor: Transistor, neighbor: Transistor) -> str:
    return transistor.other_diffusion(_must_share(transistor, neighbor))


def _ordered_top_to_bottom(transistors: Sequence[Transistor]) -> List[Transistor]:
    """Flip a series chain so the supply-side end is first (drawn on top)."""
    devices = list(transistors)
    top_net = _free_end(devices[0], devices[1])
    bottom_net = _free_end(devices[-1], devices[-2])
    if _top_sort_key(bottom_net) < _top_sort_key(top_net):
        devices.reverse()
    return devices


def _from_block(block: _Block) -> CompositePlaceable:
    """A lone series or parallel network, its ends labeled above and below."""
    wires = list(block.wires)
    anchors: List[Anchor] = [
        (block.top_net, block.top, (0, -1)),
        (block.bottom_net, block.bottom, (0, 1)),
    ]
    _tie_gates(block.devices, wires, anchors)
    children = _cmos_children(block.devices)
    keys = list(children)
    first, last = children[keys[0]], children[keys[-1]]
    ports: Dict[str, Tuple[str, str]] = {
        "top": (keys[0], _diffusion_pins_by_height(first.primitive)[0]),
        "bot": (keys[-1], _diffusion_pins_by_height(last.primitive)[1]),
    }
    for index, (key, device) in enumerate(children.items()):
        ports[f"g{index}"] = (key, _pin_name(device.primitive, GATE_ALIASES))
    return CompositePlaceable(children, ports=ports, wires=wires, anchors=anchors)


def from_series_chain(chain: SeriesChain) -> CompositePlaceable:
    """Stack the chain vertically with power on top and ground on the bottom.

    Internal diffusion nets are butted, so each junction needs no label.
    """
    ordered = _ordered_top_to_bottom(chain.transistors)
    blocks: List[_Block] = []
    last = len(ordered) - 1
    for index, transistor in enumerate(ordered):
        if index == 0:
            upper_net = _free_end(ordered[0], ordered[1])
        else:
            upper_net = _must_share(ordered[index - 1], transistor)
        if index == last:
            lower_net = _free_end(ordered[-1], ordered[-2])
        else:
            lower_net = _must_share(transistor, ordered[index + 1])
        blocks.append(_leaf(transistor, upper_net, lower_net))
    return _from_block(_stack(blocks))


def from_parallel_chain(chain: ParallelChain) -> CompositePlaceable:
    """Place the chain left to right with both diffusion nets bussed across.

    Power is drawn on the upper bus and ground on the lower.
    """
    terminals = list(chain.transistors[0].diffusion_terminals)
    top_net = min(terminals, key=_top_sort_key)
    bottom_net = next(net for net in terminals if net != top_net)
    blocks = [_leaf(t, top_net, bottom_net) for t in chain.transistors]
    return _from_block(_side_by_side(blocks))


def from_super_node(node: SuperNode) -> CompositePlaceable:
    if isinstance(node, Inverter):
        return from_inverter(node)
    if isinstance(node, TransmissionGate):
        return from_transmission_gate(node)
    if isinstance(node, CmosGate):
        return from_cmos_gate(node)
    if isinstance(node, SeriesChain):
        return from_series_chain(node)
    if isinstance(node, ParallelChain):
        return from_parallel_chain(node)


def build_placeables(
    super_nodes: Sequence[SuperNode], leftovers: Sequence[Primitive]
) -> List[Placeable]:
    placeables: List[Placeable] = [from_super_node(node) for node in super_nodes]
    placeables.extend(_from_leftover(primitive) for primitive in leftovers)
    return placeables


def _from_leftover(primitive: Primitive) -> PrimitivePlaceable:
    """A lone device. Transistors get power on top and ground on the bottom."""
    transistor = Transistor.try_from_primitive(primitive)
    if transistor is None:
        return PrimitivePlaceable(primitive)
    upper_net, lower_net = sorted(transistor.diffusion_terminals, key=_top_sort_key)
    return _with_diffusion_nets(transistor, upper_net, lower_net)[0]


def place_in_row(placeables: Iterable[Placeable], origin: Point, spacing: int) -> None:
    """Left to right, ``spacing`` apart, vertically centered on ``origin.y``."""
    cursor_x = origin.x
    for placeable in placeables:
        box = placeable.bbox
        placeable.move_by(cursor_x - box.min_x, origin.y - box.center[1])
        cursor_x = placeable.bbox.max_x + spacing
