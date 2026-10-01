from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

from spice2sch.models import Point
from spice2sch.symbols import BBox

GRID = 10

XY = Tuple[int, int]
# A wire on ``net`` from one point to another, in some placeable's frame.
Segment = Tuple[str, XY, XY]
# A preferred spot for a net's label: ``(net, point, outward direction)``.
Anchor = Tuple[str, XY, XY]


def round_point(x: float, y: float) -> Point:
    return Point(int(round(x)), int(round(y)))


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
            round_point(*self.apply(child.origin.x, child.origin.y)),
            self.orientation.compose(child.orientation),
        )


def pin_direction(pin_x: float, pin_y: float) -> XY:
    """Which way a label on a pin should run: away from the symbol origin."""
    if abs(pin_x) >= abs(pin_y):
        return (1, 0) if pin_x >= 0 else (-1, 0)
    return (0, 1) if pin_y >= 0 else (0, -1)


def segment_box(segment: Segment) -> BBox:
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
    a, b = segment_box(first), segment_box(second)
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


def overlaps(first: BBox, second: BBox) -> bool:
    return (
        first.min_x < second.max_x
        and second.min_x < first.max_x
        and first.min_y < second.max_y
        and second.min_y < first.max_y
    )
