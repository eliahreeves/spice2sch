from __future__ import annotations

import itertools
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from functools import reduce
from typing import (
    AbstractSet,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from spice2sch.models import Point, Primitive, Wire
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
from spice2sch.spice import GROUND_NETS, POWER_GROUND_NETS, POWER_NETS
from spice2sch.symbols import BBox, SymbolPin

GRID = 10

XY = Tuple[int, int]
# A wire on ``net`` from one point to another, in some placeable's frame.
Segment = Tuple[str, XY, XY]
# A preferred spot for a net's label: ``(net, point, outward direction)``.
Anchor = Tuple[str, XY, XY]


@dataclass(frozen=True)
class Orientation:
    """xschem instance orientation: ``rot`` in 0..3, ``flip`` in 0..1.

    Matches xschem's ``ROTATION`` macro: horizontal flip about the origin,
    then rotation by ``rot * 90°``.
    """

    rot: int = 0
    flip: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "rot", int(self.rot) & 3)
        object.__setattr__(self, "flip", 1 if self.flip else 0)

    def transform(self, x: float, y: float) -> Tuple[float, float]:
        xx = -x if self.flip else x
        if self.rot == 0:
            return (xx, y)
        if self.rot == 1:
            return (-y, xx)
        if self.rot == 2:
            return (-xx, -y)
        return (y, -xx)

    def compose(self, child: Orientation) -> Orientation:
        """Orientation equivalent to applying ``child`` then ``self``."""
        # A flip reverses the direction of any rotation applied before it.
        rot = self.rot - child.rot if self.flip else self.rot + child.rot
        return Orientation(rot, self.flip ^ child.flip)


@dataclass(frozen=True)
class Pose:
    """Position and orientation of a local frame within its parent frame."""

    origin: Point = field(default_factory=lambda: Point(0, 0))
    orientation: Orientation = Orientation()

    def apply(self, x: float, y: float) -> Tuple[float, float]:
        lx, ly = self.orientation.transform(x, y)
        return (self.origin.x + lx, self.origin.y + ly)

    def apply_xy(self, point: Tuple[float, float]) -> XY:
        x, y = self.apply(*point)
        return (int(round(x)), int(round(y)))

    def turn(self, direction: XY) -> XY:
        dx, dy = self.orientation.transform(*direction)
        return (int(round(dx)), int(round(dy)))

    def compose(self, child: Pose) -> Pose:
        """Pose of ``child`` (given relative to ``self``) in ``self``'s parent frame."""
        return Pose(
            _round_point(*self.apply(child.origin.x, child.origin.y)),
            self.orientation.compose(child.orientation),
        )


_IDENTITY = Pose()

_label_ids = itertools.count()


def next_label_name() -> str:
    return f"p{next(_label_ids)}"


def _round_point(x: float, y: float) -> Point:
    return Point(int(round(x)), int(round(y)))


def _pin_direction(pin_x: float, pin_y: float) -> XY:
    """Which way a label on a pin should run: away from the symbol origin."""
    if abs(pin_x) >= abs(pin_y):
        return (1, 0) if pin_x >= 0 else (-1, 0)
    return (0, 1) if pin_y >= 0 else (0, -1)


# lab_pin rotation that runs its text in each direction.
_LABEL_ROTATION = {(-1, 0): 0, (0, -1): 1, (1, 0): 2, (0, 1): 3}

# Approximate footprint of a lab_pin's text: it starts 7.5 past the pin and is
# drawn at size 0.33, about 17 units tall and 11 per character.
_LABEL_OFFSET = 7.5
_LABEL_CHAR_WIDTH = 11.0
_LABEL_HALF_HEIGHT = 10.0


def _label_extent(point: Tuple[float, float], direction: XY, net: str) -> BBox:
    """Bounds of a label at ``point`` whose text runs along ``direction``."""
    x, y = point
    dx, dy = direction
    length = _LABEL_OFFSET + _LABEL_CHAR_WIDTH * len(net)
    half = _LABEL_HALF_HEIGHT
    if dx:
        end = x + dx * length
        return BBox(min(x, end), y - half, max(x, end), y + half)
    end = y + dy * length
    return BBox(x - half, min(y, end), x + half, max(y, end))


def _lab_pin(point: XY, direction: XY, net: str) -> str:
    rot = _LABEL_ROTATION[direction]
    return (
        f"C {{lab_pin.sym}} {point[0]} {point[1]} {rot} 0 "
        f"{{name={next_label_name()} sig_type=std_logic lab={net}}}\n"
    )


def _segment_box(segment: Segment) -> BBox:
    _, (x1, y1), (x2, y2) = segment
    return BBox(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def on_segment(point: XY, start: XY, end: XY) -> bool:
    cross = (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
        point[0] - start[0]
    )
    if cross != 0:
        return False
    return min(start[0], end[0]) <= point[0] <= max(start[0], end[0]) and min(
        start[1], end[1]
    ) <= point[1] <= max(start[1], end[1])


def segments_cross(first: Segment, second: Segment) -> bool:
    """True if two axis-aligned segments share any point."""
    a, b = _segment_box(first), _segment_box(second)
    return (
        a.min_x <= b.max_x
        and b.min_x <= a.max_x
        and a.min_y <= b.max_y
        and b.min_y <= a.max_y
    )


def wires_join(first: Segment, second: Segment) -> bool:
    """True if xschem would connect the two wires: an end of one on the other."""
    return any(on_segment(p, second[1], second[2]) for p in first[1:]) or any(
        on_segment(p, first[1], first[2]) for p in second[1:]
    )


# Half the size of the little box xschem draws on every symbol pin.
_PIN_BOX = 2.5


def body_box(device: PrimitivePlaceable, pose: Pose) -> BBox:
    """A device's drawing in ``pose``, less the pin boxes poking out of it."""
    box = device.local_bbox().map_points(pose.apply)
    return BBox(
        box.min_x + _PIN_BOX, box.min_y + _PIN_BOX, box.max_x - _PIN_BOX, box.max_y - _PIN_BOX
    )


def box_interior_hit(segment: Segment, box: BBox) -> bool:
    """True if an axis-aligned segment passes through the inside of ``box``."""
    s = _segment_box(segment)
    if s.min_x == s.max_x:
        return box.min_x < s.min_x < box.max_x and s.min_y < box.max_y and box.min_y < s.max_y
    return box.min_y < s.min_y < box.max_y and s.min_x < box.max_x and box.min_x < s.max_x


def _overlaps(first: BBox, second: BBox) -> bool:
    return (
        first.min_x < second.max_x
        and second.min_x < first.max_x
        and first.min_y < second.max_y
        and second.min_y < first.max_y
    )


def is_rail(net: str) -> bool:
    return net.upper() in POWER_GROUND_NETS


@dataclass(frozen=True)
class DrawnPin:
    """A device pin in some frame, with the way its own label would run."""

    point: XY
    net: str
    direction: XY
    owner: int


class Placeable(ABC):
    """Something a placer can move, rotate, and draw.

    Subclasses describe their ports and bounds in their own local frame;
    ``pose`` places that frame in the parent (the schematic, or an enclosing
    ``CompositePlaceable``).
    """

    def __init__(self) -> None:
        self.pose = Pose()

    @abstractmethod
    def port_names(self) -> Sequence[str]: ...

    @abstractmethod
    def local_port(self, name: str) -> Tuple[float, float]: ...

    @abstractmethod
    def local_bbox(self) -> BBox: ...

    @abstractmethod
    def local_devices(self) -> List[Tuple[PrimitivePlaceable, Pose]]:
        """Every primitive with its pose in this placeable's frame."""

    def local_segments(self) -> List[Segment]:
        """Wires drawn between this placeable's own pins."""
        return []

    def local_anchors(self) -> List[Anchor]:
        """Where to put the one label a wired-together net needs."""
        return []

    def local_net_pins(self) -> List[Tuple[str, Tuple[float, float]]]:
        """Every pin as ``(net, (x, y))`` in the local frame."""
        return [(pin.net, pin.point) for pin in device_pins(self.local_devices())]

    def local_extent(self) -> BBox:
        """``local_bbox`` grown to cover the wires and the net labels drawn
        on its own (assuming every net that leaves it gets a label)."""
        devices = self.local_devices()
        segments = self.local_segments()
        anchors = self.local_anchors()
        pins = device_pins(devices)
        boxes = [self.local_bbox()] + [_segment_box(s) for s in segments]
        for component in _components(pins, segments, anchors).groups:
            site = component.site(pins, anchors, internal_ok=True)
            if site is not None:
                net, point, direction = site
                boxes.append(_label_extent(point, direction, net))
        return reduce(BBox.union, boxes)

    def draw(self, parent: Pose = _IDENTITY) -> str:
        """Draw this placeable on its own, labeling every connection."""
        return render([self], parent=parent, label_everything=True)

    @property
    def extent(self) -> BBox:
        return self.local_extent().map_points(self.pose.apply)

    def net_pins(self) -> List[Tuple[str, Point]]:
        return [
            (net, _round_point(*self.pose.apply(x, y)))
            for net, (x, y) in self.local_net_pins()
        ]

    def devices(self, parent: Pose = _IDENTITY) -> List[Tuple[PrimitivePlaceable, Pose]]:
        pose = parent.compose(self.pose)
        return [(device, pose.compose(local)) for device, local in self.local_devices()]

    def segments(self, parent: Pose = _IDENTITY) -> List[Segment]:
        pose = parent.compose(self.pose)
        return [
            (net, pose.apply_xy(start), pose.apply_xy(end))
            for net, start, end in self.local_segments()
        ]

    def anchors(self, parent: Pose = _IDENTITY) -> List[Anchor]:
        pose = parent.compose(self.pose)
        return [
            (net, pose.apply_xy(point), pose.turn(direction))
            for net, point, direction in self.local_anchors()
        ]

    def port_position(self, name: str) -> Point:
        return _round_point(*self.pose.apply(*self.local_port(name)))

    @property
    def bbox(self) -> BBox:
        return self.local_bbox().map_points(self.pose.apply)

    @property
    def center(self) -> Point:
        return _round_point(*self.bbox.center)

    def move_by(self, dx: float, dy: float) -> None:
        origin = self.pose.origin
        self.pose = replace(
            self.pose, origin=_round_point(origin.x + dx, origin.y + dy)
        )

    def place_center(self, pos: Point) -> None:
        cx, cy = self.bbox.center
        self.move_by(pos.x - cx, pos.y - cy)

    def place_port(self, name: str, pos: Point) -> None:
        current = self.port_position(name)
        self.move_by(pos.x - current.x, pos.y - current.y)

    def set_orientation(self, orientation: Orientation) -> None:
        """Rotate/flip in place, keeping the bbox center fixed."""
        center = self.center
        self.pose = replace(self.pose, orientation=orientation)
        self.place_center(center)


class PrimitivePlaceable(Placeable):
    """A single PDK-resolved ``Primitive``."""

    def __init__(
        self, primitive: Primitive, net_overrides: Optional[Mapping[str, str]] = None
    ) -> None:
        super().__init__()
        self.primitive = primitive
        self.nets: Dict[str, str] = {
            pin.name: node for node, pin in zip(primitive.nodes, primitive.symbol.pins)
        }
        self.nets.update(net_overrides or {})

    def can_swap_diffusion(self) -> bool:
        """True for a transistor with a signal (not a rail) on both drain and
        source, whose ends can trade places without moving a rail inward."""
        if Transistor.try_from_primitive(self.primitive) is None:
            return False
        drain = self.nets[_pin_name(self.primitive, DRAIN_ALIASES)]
        source = self.nets[_pin_name(self.primitive, SOURCE_ALIASES)]
        return drain != source and not (is_rail(drain) or is_rail(source))

    def swap_diffusion(self) -> None:
        """Exchange the drain and source nets. LVS-safe: MOSFETs are symmetric."""
        drain = _pin_name(self.primitive, DRAIN_ALIASES)
        source = _pin_name(self.primitive, SOURCE_ALIASES)
        self.nets[drain], self.nets[source] = self.nets[source], self.nets[drain]

    def pin(self, name: str) -> SymbolPin:
        for pin in self.primitive.symbol.pins:
            if pin.name.lower() == name.lower():
                return pin
        known = ", ".join(self.port_names())
        raise KeyError(f"unknown port {name!r}; known: {known}")

    def pin_roles(self) -> Tuple[Set[str], Set[str], bool]:
        """``(gate nets, signal diffusion nets, touches a rail)``.

        Anything that isn't a transistor treats every signal pin as a
        diffusion terminal.
        """
        if Transistor.try_from_primitive(self.primitive) is None:
            nets = set(self.nets.values())
            return set(), {n for n in nets if not is_rail(n)}, any(map(is_rail, nets))
        gate = self.nets[_pin_name(self.primitive, GATE_ALIASES)]
        diffusion = {
            self.nets[_pin_name(self.primitive, DRAIN_ALIASES)],
            self.nets[_pin_name(self.primitive, SOURCE_ALIASES)],
        }
        gates = set() if is_rail(gate) else {gate}
        return gates, {n for n in diffusion if not is_rail(n)}, any(map(is_rail, diffusion))

    def port_names(self) -> Sequence[str]:
        return tuple(pin.name for pin in self.primitive.symbol.pins)

    def local_port(self, name: str) -> Tuple[float, float]:
        pin = self.pin(name)
        return (pin.x, pin.y)

    def local_bbox(self) -> BBox:
        return self.primitive.symbol.bbox

    def local_devices(self) -> List[Tuple[PrimitivePlaceable, Pose]]:
        return [(self, _IDENTITY)]

    def symbol_line(self, pose: Pose) -> str:
        primitive = self.primitive
        attr_lines = [f"name={primitive.instance_name}"]
        for name, value in primitive.params.items():
            canonical = primitive.symbol.normalize_param_name(name)
            attr_lines.append(f"{canonical}={value}")
        attr_lines.append(f"model={primitive.model}")
        attr_lines.append("spiceprefix=X")
        newline = "\n"
        return (
            f"C {{{primitive.symbol.sch_path}}} {pose.origin.x} {pose.origin.y} "
            f"{pose.orientation.rot} {pose.orientation.flip} "
            "{"
            f"{newline.join(attr_lines)}"
            "}\n"
        )


def device_pins(
    devices: Sequence[Tuple[PrimitivePlaceable, Pose]], owner_base: int = 0
) -> List[DrawnPin]:
    pins: List[DrawnPin] = []
    for index, (device, pose) in enumerate(devices):
        for pin in device.primitive.symbol.pins:
            pins.append(
                DrawnPin(
                    pose.apply_xy((pin.x, pin.y)),
                    device.nets[pin.name],
                    pose.turn(_pin_direction(pin.x, pin.y)),
                    owner_base + index,
                )
            )
    return pins


@dataclass
class _Component:
    net: str
    pins: List[int] = field(default_factory=list)
    segments: List[int] = field(default_factory=list)
    anchors: List[int] = field(default_factory=list)

    def site(
        self,
        pins: Sequence[DrawnPin],
        anchors: Sequence[Anchor],
        internal_ok: bool = False,
    ) -> Optional[Anchor]:
        """Where this component's label goes, if it gets one.

        With ``internal_ok``, a wired-up run of pins with no anchor is taken
        to be private to its placeable and left unlabeled.
        """
        if self.anchors:
            return anchors[self.anchors[0]]
        if not self.pins:
            return None
        if internal_ok and (self.segments or len(self.pins) > 1):
            return None
        pin = pins[self.pins[0]]
        return (pin.net, pin.point, pin.direction)


@dataclass
class _Connectivity:
    groups: List[_Component]
    shorts: Set[int]  # segments that touch something on another net


def _components(
    pins: Sequence[DrawnPin], segments: Sequence[Segment], anchors: Sequence[Anchor]
) -> _Connectivity:
    """Group pins, wires, and label anchors into connected runs of one net.

    xschem joins anything that touches: coincident pins, a pin anywhere on a
    wire, and a wire end anywhere on another wire. A wire that would join two
    different nets is reported in ``shorts`` rather than merged.
    """
    parent: Dict[Tuple[str, int], Tuple[str, int]] = {}

    def find(item: Tuple[str, int]) -> Tuple[str, int]:
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(first: Tuple[str, int], second: Tuple[str, int]) -> None:
        parent[find(first)] = find(second)

    shorts: Set[int] = set()
    by_point: Dict[XY, List[Tuple[str, int, str]]] = {}
    for index, pin in enumerate(pins):
        by_point.setdefault(pin.point, []).append(("p", index, pin.net))
    for index, (net, point, _) in enumerate(anchors):
        by_point.setdefault(point, []).append(("a", index, net))
    for items in by_point.values():
        first = items[0]
        for item in items[1:]:
            if item[2] == first[2]:
                union((item[0], item[1]), (first[0], first[1]))

    points = list(by_point.items())
    for s_index, segment in enumerate(segments):
        net, start, end = segment
        box = _segment_box(segment)
        find(("s", s_index))
        for point, items in points:
            if not (box.min_x <= point[0] <= box.max_x and box.min_y <= point[1] <= box.max_y):
                continue
            if not on_segment(point, start, end):
                continue
            for kind, index, item_net in items:
                if item_net == net:
                    union(("s", s_index), (kind, index))
                else:
                    shorts.add(s_index)
        for other_index in range(s_index):
            other = segments[other_index]
            if not segments_cross(segment, other) or not wires_join(segment, other):
                continue
            if other[0] == net:
                union(("s", s_index), ("s", other_index))
            else:
                shorts.add(s_index)

    groups: Dict[Tuple[str, int], _Component] = {}
    for index, pin in enumerate(pins):
        groups.setdefault(find(("p", index)), _Component(pin.net)).pins.append(index)
    for index, segment in enumerate(segments):
        groups.setdefault(find(("s", index)), _Component(segment[0])).segments.append(index)
    for index, anchor in enumerate(anchors):
        groups.setdefault(find(("a", index)), _Component(anchor[0])).anchors.append(index)
    return _Connectivity(list(groups.values()), shorts)


def render(
    placeables: Sequence[Placeable],
    wires: Sequence[Segment] = (),
    external_nets: AbstractSet[str] = frozenset(),
    parent: Pose = _IDENTITY,
    label_everything: bool = False,
) -> str:
    """Symbols, wires, and the net labels needed to connect what wires don't.

    A run of wire-connected pins gets one label if its net is a rail, a
    subcircuit port, or also appears somewhere it isn't wired to. Wires that
    would short two nets are dropped, which leaves those pins to labels.
    """
    devices: List[Tuple[PrimitivePlaceable, Pose]] = []
    segments: List[Segment] = []
    anchors: List[Anchor] = []
    for placeable in placeables:
        devices.extend(placeable.devices(parent))
        segments.extend(placeable.segments(parent))
        anchors.extend(placeable.anchors(parent))
    segments.extend(wires)
    pins = device_pins(devices)

    while True:
        connectivity = _components(pins, segments, anchors)
        if not connectivity.shorts:
            break
        segments = [s for i, s in enumerate(segments) if i not in connectivity.shorts]

    per_net: Dict[str, int] = {}
    for component in connectivity.groups:
        if component.pins:
            per_net[component.net] = per_net.get(component.net, 0) + 1

    output = "".join(device.symbol_line(pose) for device, pose in devices)
    for net, start, end in segments:
        if start != end:
            output += Wire(start[0], start[1], end[0], end[1], net).to_xschem()
    for component in connectivity.groups:
        if not component.pins:
            continue
        net = component.net
        if not (
            label_everything
            or is_rail(net)
            or net in external_nets
            or per_net[net] > 1
        ):
            continue
        site = component.site(pins, anchors)
        if site is not None:
            output += _lab_pin(site[1], site[2], net)
    return output


class CompositePlaceable(Placeable):
    """Children laid out in a shared local frame (e.g. a SuperNode).

    Each child's ``pose`` is relative to the composite, so moving or rotating
    the composite carries the children along. ``wires`` and ``anchors`` are in
    the composite's frame.
    """

    def __init__(
        self,
        children: Mapping[str, PrimitivePlaceable],
        ports: Mapping[str, Tuple[str, str]] = {},
        wires: Sequence[Segment] = (),
        anchors: Sequence[Anchor] = (),
    ) -> None:
        super().__init__()
        self.children = dict(children)
        self.ports = {name.lower(): target for name, target in ports.items()}
        self.wires = list(wires)
        self.anchor_list = list(anchors)
        self.sides: Optional[Tuple[str, str]] = None

    def port_names(self) -> Sequence[str]:
        return tuple(self.ports)

    def local_port(self, name: str) -> Tuple[float, float]:
        try:
            child_key, port = self.ports[name.lower()]
        except KeyError as exc:
            known = ", ".join(self.port_names())
            raise KeyError(f"unknown port {name!r}; known: {known}") from exc
        child = self.children[child_key]
        return child.pose.apply(*child.local_port(port))

    def local_bbox(self) -> BBox:
        return reduce(BBox.union, (child.bbox for child in self.children.values()))

    def local_devices(self) -> List[Tuple[PrimitivePlaceable, Pose]]:
        return [(child, child.pose) for child in self.children.values()]

    def local_segments(self) -> List[Segment]:
        return list(self.wires)

    def local_anchors(self) -> List[Anchor]:
        return list(self.anchor_list)

    def swap_sides(self) -> None:
        """Mirror a two-terminal element by trading its terminal nets.

        LVS-safe: every device between the two terminals is symmetric.
        """
        assert self.sides is not None
        first, second = self.sides
        swap = {first: second, second: first}
        for child in self.children.values():
            for name in (
                _pin_name(child.primitive, DRAIN_ALIASES),
                _pin_name(child.primitive, SOURCE_ALIASES),
            ):
                child.nets[name] = swap.get(child.nets[name], child.nets[name])
        self.wires = [(swap.get(n, n), a, b) for n, a, b in self.wires]
        self.anchor_list = [(swap.get(n, n), p, d) for n, p, d in self.anchor_list]
        self.sides = (second, first)


def _pin_name(primitive: Primitive, aliases: AbstractSet[str]) -> str:
    for pin in primitive.symbol.pins:
        if pin.name.lower() in aliases:
            return pin.name
    raise KeyError(f"no pin in {sorted(aliases)} on {primitive.instance_name}")


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
    device, upper_pin, lower_pin = _with_diffusion_nets(transistor, upper_net, lower_net)
    top, bottom = device.port_position(upper_pin), device.port_position(lower_pin)
    return _Block(
        [device], [], (top.x, top.y), (bottom.x, bottom.y),
        upper_net, lower_net, True, True,
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
            result.devices + block.devices, wires, result.top, block.bottom,
            result.top_net, block.bottom_net, result.top_is_pin, block.bottom_is_pin,
        )
    return result


def _bus(net: str, xs: Iterable[int], y: int) -> List[Segment]:
    ordered = sorted(set(xs))
    return [(net, (a, y), (b, y)) for a, b in zip(ordered, ordered[1:])]


def _side_by_side(blocks: Sequence[_Block]) -> _Block:
    """Blocks left to right with their tops level, tied by a bus at each end."""
    for block in blocks:
        block.move(0, -block.top[1])
    right = blocks[0].extent.max_x
    for block in blocks[1:]:
        shift = _snap_up(right + _BRANCH_GAP - block.extent.min_x)
        block.move(shift, 0)
        right = block.extent.max_x
    top_y = -_STACK_GAP
    bottom_y = max(block.bottom[1] for block in blocks) + _STACK_GAP
    first = blocks[0]
    wires: List[Segment] = []
    for block in blocks:
        wires.extend(block.wires)
        wires.append((first.top_net, block.top, (block.top[0], top_y)))
        wires.append((first.bottom_net, block.bottom, (block.bottom[0], bottom_y)))
    wires.extend(_bus(first.top_net, (b.top[0] for b in blocks), top_y))
    wires.extend(_bus(first.bottom_net, (b.bottom[0] for b in blocks), bottom_y))
    return _Block(
        [device for block in blocks for device in block.devices],
        wires,
        (first.top[0], top_y),
        (first.bottom[0], bottom_y),
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
                    split.extend([(net, (x, a), (x, middle)), (net, (x, middle), (x, b))])
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
    right = _snap_up(max(pull_up.extent.max_x, pull_down.extent.max_x) + GRID)
    out = (right, junction[1])
    wires.append((output, junction, out))
    anchors: List[Anchor] = [
        (pull_up.top_net, pull_up.top, (0, -1)),
        (pull_down.bottom_net, pull_down.bottom, (0, 1)),
        (output, out, (1, 0)),
    ]
    _tie_gates(devices, wires, anchors)
    return devices, wires, anchors, out


def _cmos_children(devices: Sequence[PrimitivePlaceable]) -> Dict[str, PrimitivePlaceable]:
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
    raise TypeError(f"unsupported supernode {type(node).__name__}")


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
