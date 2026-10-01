from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from spice2sch.models import Point
from spice2sch.placeable import (
    GRID,
    XY,
    CompositePlaceable,
    Placeable,
    Pose,
    Segment,
    body_box,
    box_interior_hit,
    device_pins,
    on_segment,
    segments_cross,
    wires_join,
)
from spice2sch.symbols import BBox

_SWEEPS = 4
_ALIGN_PASSES = 3
# Tracks a channel may use before the remaining nets fall back to labels.
_MAX_TRACKS = 24
# Stand-in x for terminals in the right-hand column while tracks are chosen.
_FAR = 1_000_000


def _snap(value: float) -> int:
    return int(round(value / GRID)) * GRID


def _snap_up(value: float) -> int:
    return -int(-value // GRID) * GRID


# ``(net, point, direction)``: where a wire on ``net`` would meet a placeable.
_Terminal = Tuple[str, XY, XY]


@dataclass
class _Node:
    placeable: Placeable
    gates: Set[str]
    diffusion: Set[str]
    static: bool
    terminals: List[_Terminal] = field(default_factory=list)
    extent: BBox = BBox(0, 0, 0, 0)

    @property
    def nets(self) -> Set[str]:
        return self.gates | self.diffusion


def _terminals(placeable: Placeable, nets: Set[str]) -> List[_Terminal]:
    """A placeable's anchors on ``nets``, plus its pins on nets without one."""
    anchors = [a for a in placeable.local_anchors() if a[0] in nets]
    anchored = {net for net, _, _ in anchors}
    pins = [
        (pin.net, pin.point, pin.direction)
        for pin in device_pins(placeable.local_devices())
        if pin.net in nets and pin.net not in anchored
    ]
    return anchors + pins


def _nodes(placeables: Sequence[Placeable], ports: Set[str]) -> List[_Node]:
    users: Dict[str, Set[int]] = {}
    nodes: List[_Node] = []
    for index, placeable in enumerate(placeables):
        gates: Set[str] = set()
        diffusion: Set[str] = set()
        static = False
        for device, _ in placeable.local_devices():
            device_gates, device_diffusion, touches_rail = device.pin_roles()
            gates |= device_gates
            diffusion |= device_diffusion
            static = static or touches_rail
        for net in gates | diffusion:
            users.setdefault(net, set()).add(index)
        nodes.append(_Node(placeable, gates, diffusion, static))
    for index, node in enumerate(nodes):
        private = {n for n in node.nets if users[n] == {index} and n not in ports}
        node.gates -= private
        node.diffusion -= private
        node.terminals = _terminals(node.placeable, node.nets)
        node.extent = node.placeable.local_extent()
    return nodes


def _users(nodes: Sequence[_Node]) -> Dict[str, List[int]]:
    users: Dict[str, List[int]] = {}
    for index, node in enumerate(nodes):
        for net in sorted(node.nets):
            users.setdefault(net, []).append(index)
    return users


def _columns(nodes: Sequence[_Node], inputs: Sequence[str], outputs: Set[str]) -> List[int]:
    """Column of every node: one past the latest column its inputs come from."""
    level: Dict[str, int] = {net: 0 for net in inputs}
    columns: List[Optional[int]] = [None] * len(nodes)
    pending = list(range(len(nodes)))

    def ready(index: int) -> bool:
        node = nodes[index]
        if not node.gates <= level.keys():
            return False
        return node.static or not node.diffusion or bool(node.diffusion & level.keys())

    while pending:
        batch = [index for index in pending if ready(index)]
        if not batch:
            # A loop (latch, flop): start it from its best-known member.
            batch = [max(pending, key=lambda i: (len(nodes[i].nets & level.keys()), -i))]
        placed: List[Tuple[int, int]] = []
        for index in batch:
            node = nodes[index]
            sources = node.gates if node.static else node.nets
            placed.append((index, max((level[n] for n in sources if n in level), default=0)))
        for index, column in placed:
            node = nodes[index]
            columns[index] = column
            drives = node.diffusion if node.static else node.diffusion - level.keys()
            for net in drives:
                level.setdefault(net, column + 1)
        pending = [index for index in pending if columns[index] is None]

    result = [column or 0 for column in columns]
    last = max(result, default=0)
    users = _users(nodes)
    for index, node in enumerate(nodes):
        if node.static and node.diffusion and all(
            users[n] == [index] and n in outputs for n in node.diffusion
        ):
            result[index] = last
    used = sorted(set(result))
    rank = {column: position for position, column in enumerate(used)}
    return [rank[column] for column in result]


def _order(
    nodes: Sequence[_Node],
    columns: Sequence[int],
    inputs: Sequence[str],
    outputs: Sequence[str],
) -> List[List[int]]:
    """Members of each column, top to bottom, by barycenter sweeps."""
    count = max(columns, default=0) + 1
    order: List[List[int]] = [[] for _ in range(count)]
    for index, column in enumerate(columns):
        order[column].append(index)
    shared: List[Dict[int, int]] = [{} for _ in nodes]
    for members in _users(nodes).values():
        for a in members:
            for b in members:
                if a != b:
                    shared[a][b] = shared[a].get(b, 0) + 1
    input_rank = {net: (r + 0.5) / len(inputs) for r, net in enumerate(inputs)}
    output_rank = {net: (r + 0.5) / len(outputs) for r, net in enumerate(outputs)}

    def sweep(column: int, forward: bool) -> None:
        position = {
            index: (slot + 0.5) / len(members)
            for members in order
            for slot, index in enumerate(members)
        }
        ports = input_rank if forward else output_rank
        keys: Dict[int, float] = {}
        for index in order[column]:
            total = weight = 0.0
            for other, count in shared[index].items():
                if columns[other] != column and (columns[other] < column) == forward:
                    total += position[other] * count
                    weight += count
            for net in nodes[index].nets & ports.keys():
                total += ports[net]
                weight += 1
            keys[index] = total / weight if weight else position[index]
        order[column].sort(key=lambda index: keys[index])

    for _ in range(_SWEEPS):
        for column in range(count):
            sweep(column, forward=True)
        for column in reversed(range(count)):
            sweep(column, forward=False)
    for column in range(count):
        sweep(column, forward=True)
    return order


def _isotonic(targets: Sequence[float], weights: Sequence[float]) -> List[float]:
    """Nondecreasing values closest to ``targets`` in weighted least squares."""
    blocks: List[Tuple[float, float, int]] = []  # (value, weight, count)
    for target, weight in zip(targets, weights):
        blocks.append((target, weight, 1))
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            value, w, n = blocks.pop()
            prev_value, prev_w, prev_n = blocks.pop()
            total = prev_w + w
            blocks.append(((prev_value * prev_w + value * w) / total, total, prev_n + n))
    return [value for value, _, n in blocks for _ in range(n)]


def _channel_wires(net: str, track: int, left: Sequence[XY], right: Sequence[XY]) -> List[Segment]:
    """Runs from ``left`` terminals across to a vertical track at x=``track``
    and on to the ``right`` terminals, split wherever runs meet the track."""
    ys = sorted({y for _, y in left} | {y for _, y in right})
    wires: List[Segment] = [(net, point, (track, point[1])) for point in left]
    wires += [(net, (track, point[1]), point) for point in right]
    wires += [(net, (track, a), (track, b)) for a, b in zip(ys, ys[1:])]
    return wires


@dataclass
class _ChannelNet:
    net: str
    left: List[XY]  # relative to the left column's right edge
    right: List[Tuple[int, XY]]  # (node, point relative to that node's origin)
    track: int = 0


class _Layout:
    def __init__(
        self,
        placeables: Sequence[Placeable],
        spacing: int,
        inputs: Sequence[str],
        outputs: Sequence[str],
    ) -> None:
        self.spacing = spacing
        self.ports = set(inputs) | set(outputs)
        self.nodes = _nodes(placeables, self.ports)
        self.columns = _columns(self.nodes, inputs, set(outputs))
        self._face_inputs_left()
        self.order = _order(self.nodes, self.columns, inputs, outputs)
        self.xs: List[int] = [0] * len(self.nodes)
        self.ys: List[int] = [0] * len(self.nodes)

    # Columns and orientation ----------------------------------------------

    def _face_inputs_left(self) -> None:
        """Mirror two-terminal elements whose right side connects further left."""
        users = _users(self.nodes)

        def reach(net: str, index: int) -> float:
            others = [self.columns[i] for i in users.get(net, []) if i != index]
            return sum(others) / len(others) if others else self.columns[index]

        for index, node in enumerate(self.nodes):
            placeable = node.placeable
            if not isinstance(placeable, CompositePlaceable) or placeable.sides is None:
                continue
            left, right = placeable.sides
            if reach(right, index) < reach(left, index):
                placeable.swap_sides()
                node.terminals = _terminals(placeable, node.nets)

    # Heights ----------------------------------------------------------------

    def _height(self, index: int) -> float:
        extent = self.nodes[index].extent
        return extent.max_y - extent.min_y

    def _pack(self, column: int, desired: Dict[int, float], weight: Dict[int, float]) -> None:
        """Origins as close to ``desired`` as the column's order and spacing allow."""
        members = self.order[column]
        offsets: List[float] = []
        running = 0.0
        for index in members:
            offsets.append(running)
            running += self._height(index) + self.spacing
        targets = [
            desired[index] + self.nodes[index].extent.min_y - offset
            for index, offset in zip(members, offsets)
        ]
        tops = _isotonic(targets, [weight[index] for index in members])
        floor: Optional[float] = None
        for index, top, offset in zip(members, tops, offsets):
            extent = self.nodes[index].extent
            y = _snap(top + offset - extent.min_y)
            if floor is not None and y + extent.min_y < floor:
                y = _snap_up(floor - extent.min_y)
            self.ys[index] = y
            floor = y + extent.max_y + self.spacing

    def _align(self, column: int, sides: Sequence[int]) -> None:
        """Pull each member level with its terminals' partners in ``sides``."""
        by_net: Dict[str, List[float]] = {}
        for side in sides:
            if 0 <= side < len(self.order):
                for index in self.order[side]:
                    for net, (_, y), _ in self.nodes[index].terminals:
                        by_net.setdefault(net, []).append(self.ys[index] + y)
        desired: Dict[int, float] = {}
        weight: Dict[int, float] = {}
        for index in self.order[column]:
            pulls = [
                partner - y
                for net, (_, y), _ in self.nodes[index].terminals
                for partner in by_net.get(net, [])
            ]
            if pulls:
                desired[index] = sum(pulls) / len(pulls)
                weight[index] = float(len(pulls))
            else:
                desired[index] = self.ys[index]
                weight[index] = 1e-3
        self._pack(column, desired, weight)

    def arrange_heights(self) -> None:
        for members in self.order:
            y = -sum(self._height(i) + self.spacing for i in members) / 2
            for index in members:
                self.ys[index] = _snap(y - self.nodes[index].extent.min_y)
                y += self._height(index) + self.spacing
        count = len(self.order)
        for column in range(1, count):
            self._align(column, [column - 1])
        for _ in range(_ALIGN_PASSES):
            for column in reversed(range(count)):
                self._align(column, [column - 1, column + 1])
            for column in range(count):
                self._align(column, [column - 1, column + 1])

    # Widths and wires -------------------------------------------------------

    def _frame(self, index: int, x: int) -> Pose:
        return Pose(Point(x, self.ys[index]))

    def _run_is_clear(self, index: int, x: int, run: Segment) -> bool:
        """True if a wire from a terminal out to its column edge touches
        nothing else of its placeable, with the placeable's origin at ``x``."""
        placeable = self.nodes[index].placeable
        frame = self._frame(index, x)
        net, start, end = run
        devices = placeable.devices(frame)
        for device, pose in devices:
            if box_interior_hit(run, body_box(device, pose)):
                return False
        for pin in device_pins(devices):
            if pin.net != net and on_segment(pin.point, start, end):
                return False
        for wire in placeable.segments(frame):
            if wire[0] != net and segments_cross(run, wire):
                return False
        for anchor_net, point, _ in placeable.anchors(frame):
            if anchor_net != net and on_segment(point, start, end):
                return False
        return True

    def _channel(self, column: int, users: Dict[str, Set[int]]) -> List[_ChannelNet]:
        """Internal nets used only by ``column`` and the next one, all of whose
        terminals can run straight into the channel between them.

        Left points are relative to the left column's right edge; right
        points to their own node's origin.
        """
        width = self._width(column)
        found: Dict[str, _ChannelNet] = {}
        rejected: Set[str] = set()
        for side, facing in ((column, (1, 0)), (column + 1, (-1, 0))):
            for index in self.order[side]:
                origin = self._inset(index)
                for net, (px, py), direction in self.nodes[index].terminals:
                    if net in self.ports or users[net] != {column, column + 1}:
                        continue
                    y = self.ys[index] + py
                    x = origin + px
                    if side == column:
                        run: Segment = (net, (x, y), (width, y))
                    else:
                        run = (net, (0, y), (x, y))
                    if direction != facing or not self._run_is_clear(index, origin, run):
                        rejected.add(net)
                        continue
                    entry = found.setdefault(net, _ChannelNet(net, [], []))
                    if side == column:
                        entry.left.append((x - width, y))
                    else:
                        entry.right.append((index, (px, py)))
        return [
            entry
            for net, entry in sorted(found.items())
            if net not in rejected and entry.left and entry.right
        ]

    def _inset(self, index: int) -> int:
        """Origin's distance from its column's left edge, kept on the grid."""
        return _snap_up(-self.nodes[index].extent.min_x)

    def _width(self, column: int) -> int:
        return _snap_up(
            max(self._inset(i) + self.nodes[i].extent.max_x for i in self.order[column])
        )

    def _assign_tracks(self, nets: List[_ChannelNet]) -> Tuple[List[_ChannelNet], int]:
        """Give each net a track where none of its wires end on another net's.

        Track ``k`` sits ``(k + 1) * GRID`` right of the left column.
        """
        def span(entry: _ChannelNet) -> int:
            ys = [y for _, y in entry.left] + [self.ys[i] + p[1] for i, p in entry.right]
            return max(ys) - min(ys)

        placed: List[Tuple[_ChannelNet, List[Segment]]] = []
        for entry in sorted(nets, key=lambda e: (span(e), e.net)):
            right = [(_FAR, self.ys[i] + p[1]) for i, p in entry.right]
            for track in range(_MAX_TRACKS):
                wires = _channel_wires(entry.net, (track + 1) * GRID, entry.left, right)
                if not any(
                    wires_join(mine, theirs)
                    for _, others in placed
                    for mine in wires
                    for theirs in others
                ):
                    entry.track = track
                    placed.append((entry, wires))
                    break
        used = max((entry.track + 1 for entry, _ in placed), default=0)
        return [entry for entry, _ in placed], used

    def route(self, gap: int) -> List[Segment]:
        """Set every column's x and return the wires drawn across channels."""
        users: Dict[str, Set[int]] = {}
        for index, node in enumerate(self.nodes):
            for net, _, _ in node.terminals:
                users.setdefault(net, set()).add(self.columns[index])
        channels: List[Tuple[int, List[_ChannelNet]]] = []
        x = 0
        for column, members in enumerate(self.order):
            for index in members:
                self.xs[index] = x + self._inset(index)
            if column + 1 == len(self.order):
                break
            edge = x + self._width(column)
            routed, tracks = self._assign_tracks(self._channel(column, users))
            channels.append((edge, routed))
            x = edge + gap + tracks * GRID
        wires: List[Segment] = []
        for edge, routed in channels:
            for entry in routed:
                left = [(edge + px, py) for px, py in entry.left]
                right = [(self.xs[i] + p[0], self.ys[i] + p[1]) for i, p in entry.right]
                wires += _channel_wires(entry.net, edge + (entry.track + 1) * GRID, left, right)
        return wires

    def apply(self, origin: Point) -> None:
        for index, node in enumerate(self.nodes):
            node.placeable.pose = Pose(
                Point(origin.x + self.xs[index], origin.y + self.ys[index]),
                node.placeable.pose.orientation,
            )


def place(
    placeables: Sequence[Placeable],
    origin: Point,
    spacing: int,
    inputs: Iterable[str] = (),
    outputs: Iterable[str] = (),
) -> List[Segment]:
    """Arrange ``placeables`` below and to the right of ``origin``.

    ``inputs`` and ``outputs`` are the subcircuit ports. Returns the wires
    drawn between placeables, in schematic coordinates.
    """
    if not placeables:
        return []
    layout = _Layout(placeables, spacing, list(inputs), list(outputs))
    layout.arrange_heights()
    wires = layout.route(spacing)
    top = min(layout.ys[i] + node.extent.min_y for i, node in enumerate(layout.nodes))
    left = min(layout.xs[i] + node.extent.min_x for i, node in enumerate(layout.nodes))
    shift = Point(origin.x - _snap(left), origin.y - _snap(top))
    layout.apply(shift)
    return [
        (net, (a[0] + shift.x, a[1] + shift.y), (b[0] + shift.x, b[1] + shift.y))
        for net, a, b in wires
    ]
