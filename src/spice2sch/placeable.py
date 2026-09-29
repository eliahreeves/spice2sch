from __future__ import annotations

import itertools
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
    DRAIN_ALIASES,
    GATE_ALIASES,
    SOURCE_ALIASES,
    Inverter,
    SuperNode,
    Transistor,
    TransmissionGate,
)
from spice2sch.symbols import BBox, SymbolPin


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


def _lab_pin_orientation(pin_x: float, pin_y: float) -> Tuple[int, int]:
    if abs(pin_x) >= abs(pin_y):
        return (2, 0) if pin_x >= 0 else (0, 0)
    return (3, 0) if pin_y >= 0 else (1, 0)


def _lab_pin(pose: Pose, pin: SymbolPin, net: str) -> str:
    """A ``lab_pin`` on ``pin`` of a symbol instanced at ``pose``."""
    pos = _round_point(*pose.apply(pin.x, pin.y))
    rot, flip = _lab_pin_orientation(*pose.orientation.transform(pin.x, pin.y))
    return (
        f"C {{lab_pin.sym}} {pos.x} {pos.y} {rot} {flip} "
        f"{{name={next_label_name()} sig_type=std_logic lab={net}}}\n"
    )


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
    def draw(self, parent: Pose = _IDENTITY) -> str: ...

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

    def pin(self, name: str) -> SymbolPin:
        for pin in self.primitive.symbol.pins:
            if pin.name.lower() == name.lower():
                return pin
        known = ", ".join(self.port_names())
        raise KeyError(f"unknown port {name!r}; known: {known}")

    def port_names(self) -> Sequence[str]:
        return tuple(pin.name for pin in self.primitive.symbol.pins)

    def local_port(self, name: str) -> Tuple[float, float]:
        pin = self.pin(name)
        return (pin.x, pin.y)

    def local_bbox(self) -> BBox:
        return self.primitive.symbol.bbox

    def draw(
        self, parent: Pose = _IDENTITY, skip_pins: AbstractSet[str] = frozenset()
    ) -> str:
        """Emit the symbol plus a ``lab_pin`` on every pin not in ``skip_pins``."""
        primitive = self.primitive
        pose = parent.compose(self.pose)

        attr_lines = [f"name={primitive.instance_name}"]
        for name, value in primitive.params.items():
            canonical = primitive.symbol.normalize_param_name(name)
            attr_lines.append(f"{canonical}={value}")
        attr_lines.append(f"model={primitive.model}")
        attr_lines.append("spiceprefix=X")

        newline = "\n"
        output = (
            f"C {{{primitive.symbol.sch_path}}} {pose.origin.x} {pose.origin.y} "
            f"{pose.orientation.rot} {pose.orientation.flip} "
            "{"
            f"{newline.join(attr_lines)}"
            "}\n"
        )
        for pin in primitive.symbol.pins:
            if pin.name not in skip_pins:
                output += _lab_pin(pose, pin, self.nets[pin.name])
        return output


@dataclass(frozen=True)
class InternalWire:
    """A connection between two child ports, drawn instead of a lab_pin on each.

    Endpoints are ``(child_key, pin_name)``; the net label goes on ``a``.
    When the endpoints already coincide, only the label is drawn.
    """

    net: str
    a: Tuple[str, str]
    b: Tuple[str, str]


class CompositePlaceable(Placeable):
    """Children laid out in a shared local frame (e.g. a SuperNode).

    Each child's ``pose`` is relative to the composite, so moving or rotating
    the composite carries the children along.
    """

    def __init__(
        self,
        children: Mapping[str, PrimitivePlaceable],
        ports: Mapping[str, Tuple[str, str]],
        wires: Sequence[InternalWire] = (),
    ) -> None:
        super().__init__()
        self.children = dict(children)
        self.ports = {name.lower(): target for name, target in ports.items()}
        self.wires = tuple(wires)

    def port_names(self) -> Sequence[str]:
        return tuple(self.ports)

    def local_port(self, name: str) -> Tuple[float, float]:
        try:
            endpoint = self.ports[name.lower()]
        except KeyError as exc:
            known = ", ".join(self.port_names())
            raise KeyError(f"unknown port {name!r}; known: {known}") from exc
        return self._child_port(endpoint)

    def _child_port(self, endpoint: Tuple[str, str]) -> Tuple[float, float]:
        child_key, port = endpoint
        child = self.children[child_key]
        return child.pose.apply(*child.local_port(port))

    def local_bbox(self) -> BBox:
        return reduce(BBox.union, (child.bbox for child in self.children.values()))

    def draw(self, parent: Pose = _IDENTITY) -> str:
        pose = parent.compose(self.pose)

        wired: Dict[str, Set[str]] = {key: set() for key in self.children}
        for wire in self.wires:
            for key, port in (wire.a, wire.b):
                wired[key].add(self.children[key].pin(port).name)

        output = "".join(
            child.draw(pose, wired[key]) for key, child in self.children.items()
        )
        for wire in self.wires:
            start = _round_point(*pose.apply(*self._child_port(wire.a)))
            end = _round_point(*pose.apply(*self._child_port(wire.b)))
            if start != end:
                output += Wire(start.x, start.y, end.x, end.y, wire.net).to_xschem()
            child = self.children[wire.a[0]]
            output += _lab_pin(pose.compose(child.pose), child.pin(wire.a[1]), wire.net)
        return output


def _pin_name(primitive: Primitive, aliases: AbstractSet[str]) -> str:
    for pin in primitive.symbol.pins:
        if pin.name.lower() in aliases:
            return pin.name
    raise KeyError(f"no pin in {sorted(aliases)} on {primitive.instance_name}")


_STACK_GAP = 20
# Room for the two body-pin labels, which face each other across the gap.
_TG_STACK_GAP = 80


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


def _inverter_half(transistor: Transistor, output_net: str) -> PrimitivePlaceable:
    """Put the output on the drain pin and the rail on the source pin.

    On sky130 FET symbols the drain is drawn above the source, so this keeps
    rails on the outside of the stack. Swapping D/S is LVS-safe because
    MOSFETs are symmetric.
    """
    primitive = transistor.primitive
    return PrimitivePlaceable(
        primitive,
        net_overrides={
            _pin_name(primitive, DRAIN_ALIASES): output_net,
            _pin_name(primitive, SOURCE_ALIASES): transistor.other_diffusion(output_net),
        },
    )


def from_inverter(inv: Inverter) -> CompositePlaceable:
    """CMOS stack: PMOS above NMOS, output drains on the same point.

    The drains touch, so the output net needs no wire. Only the gates are wired.
    """
    pmos = _inverter_half(inv.pmos, inv.output_node)
    nmos = _inverter_half(inv.nmos, inv.output_node)
    p_prim, n_prim = inv.pmos.primitive, inv.nmos.primitive
    p_gate, n_gate = _pin_name(p_prim, GATE_ALIASES), _pin_name(n_prim, GATE_ALIASES)
    p_drain, n_drain = (
        _pin_name(p_prim, DRAIN_ALIASES),
        _pin_name(n_prim, DRAIN_ALIASES),
    )
    pmos.place_port(p_drain, nmos.port_position(n_drain))

    return CompositePlaceable(
        children={"pmos": pmos, "nmos": nmos},
        ports={
            "in": ("pmos", p_gate),
            "out": ("pmos", p_drain),
            "vdd": ("pmos", _pin_name(p_prim, SOURCE_ALIASES)),
            "vss": ("nmos", _pin_name(n_prim, SOURCE_ALIASES)),
        },
        wires=(
            InternalWire(inv.input_node, ("pmos", p_gate), ("nmos", n_gate)),
            InternalWire(inv.output_node, ("pmos", p_drain), ("nmos", n_drain)),
        ),
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


def from_transmission_gate(tg: TransmissionGate) -> CompositePlaceable:
    """PMOS on top with its gate up, NMOS below with its gate down, and the
    drains and sources wired straight across."""
    # sky130 FET symbols have the gate on the left: rot=1 turns it up, rot=3 down.
    pmos = _tg_half(tg.pmos, tg, Orientation(1, 0))
    nmos = _tg_half(tg.nmos, tg, Orientation(3, 0))
    p_prim, n_prim = tg.pmos.primitive, tg.nmos.primitive
    p_drain, n_drain = _pin_name(p_prim, DRAIN_ALIASES), _pin_name(n_prim, DRAIN_ALIASES)
    p_source, n_source = _pin_name(p_prim, SOURCE_ALIASES), _pin_name(n_prim, SOURCE_ALIASES)
    _stack_above(pmos, p_drain, nmos, n_drain, gap=_TG_STACK_GAP)

    return CompositePlaceable(
        children={"pmos": pmos, "nmos": nmos},
        ports={
            "a": ("pmos", p_drain),
            "b": ("pmos", p_source),
            "en": ("nmos", _pin_name(n_prim, GATE_ALIASES)),
            "enb": ("pmos", _pin_name(p_prim, GATE_ALIASES)),
        },
        wires=(
            InternalWire(tg.terminal_a, ("pmos", p_drain), ("nmos", n_drain)),
            InternalWire(tg.terminal_b, ("pmos", p_source), ("nmos", n_source)),
        ),
    )


def from_super_node(node: SuperNode) -> CompositePlaceable:
    if isinstance(node, Inverter):
        return from_inverter(node)
    if isinstance(node, TransmissionGate):
        return from_transmission_gate(node)


def build_placeables(
    super_nodes: Sequence[SuperNode], leftovers: Sequence[Primitive]
) -> List[Placeable]:
    placeables: List[Placeable] = [from_super_node(node) for node in super_nodes]
    placeables.extend(PrimitivePlaceable(primitive) for primitive in leftovers)
    return placeables


def place_in_row(placeables: Iterable[Placeable], origin: Point, spacing: int) -> None:
    """Left to right, ``spacing`` apart, vertically centered on ``origin.y``."""
    cursor_x = origin.x
    for placeable in placeables:
        box = placeable.bbox
        placeable.move_by(cursor_x - box.min_x, origin.y - box.center[1])
        cursor_x = placeable.bbox.max_x + spacing
