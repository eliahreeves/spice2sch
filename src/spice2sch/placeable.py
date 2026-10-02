from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from functools import reduce
from typing import (
    AbstractSet,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from spice2sch.models import Point, Primitive, Wire
from spice2sch.patterns import (
    DRAIN_ALIASES,
    GATE_ALIASES,
    SOURCE_ALIASES,
    Transistor,
)
from spice2sch.geom import (
    GRID,
    Anchor,
    Orientation,
    Pose,
    Segment,
    XY,
    on_segment,
    overlaps as _overlaps,
    pin_direction as _pin_direction,
    round_point as _round_point,
    segment_box as _segment_box,
    segments_cross,
    wires_join,
)
from spice2sch.spice import POWER_GROUND_NETS
from spice2sch.symbols import BBox, SymbolPin

_IDENTITY = Pose()

_label_ids = itertools.count()


def next_label_name() -> str:
    return f"p{next(_label_ids)}"


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


# Half the size of the little box xschem draws on every symbol pin.
_PIN_BOX = 2.5


def body_box(device: PrimitivePlaceable, pose: Pose) -> BBox:
    """A device's drawing in ``pose``, less the pin boxes poking out of it."""
    box = device.local_bbox().map_points(pose.apply)
    return BBox(
        box.min_x + _PIN_BOX,
        box.min_y + _PIN_BOX,
        box.max_x - _PIN_BOX,
        box.max_y - _PIN_BOX,
    )


def box_interior_hit(segment: Segment, box: BBox) -> bool:
    """True if an axis-aligned segment passes through the inside of ``box``."""
    s = _segment_box(segment)
    if s.min_x == s.max_x:
        return (
            box.min_x < s.min_x < box.max_x
            and s.min_y < box.max_y
            and box.min_y < s.max_y
        )
    return (
        box.min_y < s.min_y < box.max_y and s.min_x < box.max_x and box.min_x < s.max_x
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

    def local_extent(
        self,
        unlabeled: AbstractSet[str] = frozenset(),
        *,
        side_rails: bool = True,
    ) -> BBox:
        """``local_bbox`` grown to cover the wires and the net labels drawn
        on its own (assuming every net that leaves it gets a label, except
        those in ``unlabeled`` which are expected to be wired externally).

        When ``side_rails`` is false, left/right-facing rail labels are omitted
        so column packing can let them hang into the wiring channel.
        """
        devices = self.local_devices()
        segments = self.local_segments()
        anchors = self.local_anchors()
        pins = device_pins(devices)
        boxes = [self.local_bbox()] + [_segment_box(s) for s in segments]
        for component in _components(pins, segments, anchors).groups:
            site = component.site(pins, anchors, internal_ok=True)
            if site is not None:
                net, point, direction = site
                if net in unlabeled:
                    continue
                if not side_rails and is_rail(net) and direction[0] != 0:
                    continue
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

    def devices(
        self, parent: Pose = _IDENTITY
    ) -> List[Tuple[PrimitivePlaceable, Pose]]:
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
        return (
            gates,
            {n for n in diffusion if not is_rail(n)},
            any(map(is_rail, diffusion)),
        )

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
            if not (
                box.min_x <= point[0] <= box.max_x
                and box.min_y <= point[1] <= box.max_y
            ):
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
        groups.setdefault(find(("s", index)), _Component(segment[0])).segments.append(
            index
        )
    for index, anchor in enumerate(anchors):
        groups.setdefault(find(("a", index)), _Component(anchor[0])).anchors.append(
            index
        )
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
            label_everything or is_rail(net) or net in external_nets or per_net[net] > 1
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
