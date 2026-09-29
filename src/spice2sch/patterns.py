from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from spice2sch.models import Primitive
from spice2sch.spice import POWER_GROUND_NETS

DRAIN_ALIASES = {"d", "drain"}
GATE_ALIASES = {"g", "gate"}
SOURCE_ALIASES = {"s", "source"}
BODY_ALIASES = {"b", "bulk", "body", "nb", "pb", "sub", "substrate", "well"}


def _pin_node_map(primitive: Primitive) -> Dict[str, str]:
    return {
        pin.name.lower(): node
        for node, pin in zip(primitive.nodes, primitive.symbol.pins)
    }


def _first_match(pins: Dict[str, str], aliases: Set[str]) -> Optional[str]:
    for alias in aliases:
        if alias in pins:
            return pins[alias]
    return None


@dataclass(frozen=True)
class Transistor:
    primitive: Primitive
    drain: str
    gate: str
    source: str
    body: Optional[str]
    is_pmos: bool

    @property
    def is_nmos(self) -> bool:
        return not self.is_pmos

    @property
    def diffusion_terminals(self) -> Tuple[str, str]:
        return (self.drain, self.source)

    def other_diffusion(self, net: str) -> str:
        """The diffusion terminal on the opposite side from ``net``."""
        return self.source if self.drain == net else self.drain

    @property
    def is_diode_connected(self) -> bool:
        return self.gate == self.drain or self.gate == self.source

    @classmethod
    def try_from_primitive(cls, primitive: Primitive) -> Optional["Transistor"]:
        device_type = (primitive.symbol.device_type or "").strip().lower()
        if device_type.startswith("p"):
            is_pmos = True
        elif device_type.startswith("n"):
            is_pmos = False
        else:
            return None

        pins = _pin_node_map(primitive)
        drain = _first_match(pins, DRAIN_ALIASES)
        gate = _first_match(pins, GATE_ALIASES)
        source = _first_match(pins, SOURCE_ALIASES)
        if drain is None or gate is None or source is None:
            return None

        body = _first_match(pins, BODY_ALIASES)
        return cls(
            primitive=primitive,
            drain=drain,
            gate=gate,
            source=source,
            body=body,
            is_pmos=is_pmos,
        )


@dataclass(frozen=True)
class Inverter:
    pmos: Transistor
    nmos: Transistor
    input_node: str
    output_node: str

    @property
    def primitives(self) -> List[Primitive]:
        return [self.pmos.primitive, self.nmos.primitive]


@dataclass(frozen=True)
class TransmissionGate:
    pmos: Transistor
    nmos: Transistor
    terminal_a: str
    terminal_b: str

    @property
    def primitives(self) -> List[Primitive]:
        return [self.pmos.primitive, self.nmos.primitive]


#
#
# @dataclass(frozen=True)
# class SeriesStack:
#     """Same-type transistors chained diffusion-to-diffusion, ordered so that
#     consecutive entries share a terminal (i.e. in physical stack order)."""
#
#     transistors: List[Transistor]
#     is_pmos: bool
#
#     @property
#     def primitives(self) -> List[Primitive]:
#         return [t.primitive for t in self.transistors]
#
#
# @dataclass(frozen=True)
# class CurrentMirror:
#     reference: Transistor
#     mirrors: List[Transistor]
#     tail_node: str
#
#     @property
#     def primitives(self) -> List[Primitive]:
#         return [self.reference.primitive] + [t.primitive for t in self.mirrors]
#
#
# @dataclass(frozen=True)
# class DifferentialPair:
#     left: Transistor
#     right: Transistor
#     tail_node: str
#
#     @property
#     def primitives(self) -> List[Primitive]:
#         return [self.left.primitive, self.right.primitive]


SuperNode = Union[
    Inverter,
    TransmissionGate,  # SeriesStack, CurrentMirror, DifferentialPair
]


def _is_rail(net: str) -> bool:
    return net.upper() in POWER_GROUND_NETS


def _inverter_output(p_item: Transistor, n_item: Transistor) -> Optional[str]:
    """The output net if ``p_item``/``n_item`` form a CMOS inverter, else None.

    Both devices must share the input gate and exactly one diffusion net (the
    output), and each must connect its other diffusion terminal to a distinct
    rail. Without the rail check, the top and bottom devices of NAND/NOR
    stacks, and the halves of clocked transmission gates, look like
    inverters.
    """
    if p_item.gate != n_item.gate:
        return None
    shared = set(p_item.diffusion_terminals) & set(n_item.diffusion_terminals)
    if len(shared) != 1:
        return None
    output = next(iter(shared))
    p_rail = p_item.other_diffusion(output)
    n_rail = n_item.other_diffusion(output)
    if _is_rail(output) or output == p_item.gate:
        return None
    if not (_is_rail(p_rail) and _is_rail(n_rail)) or p_rail == n_rail:
        return None
    return output


def find_inverters(pmos: List[Transistor], nmos: List[Transistor]) -> List[Inverter]:
    """Find PMOS/NMOS pairs forming a CMOS inverter (see `_inverter_output`).
    Matched transistors are removed from `pmos`/`nmos` in place."""
    inverters: List[Inverter] = []
    p_index = 0
    while p_index < len(pmos):
        p_item = pmos[p_index]
        match: Optional[Tuple[int, str]] = None
        for n_index, n_item in enumerate(nmos):
            output = _inverter_output(p_item, n_item)
            if output is not None:
                match = (n_index, output)
                break
        if match is None:
            p_index += 1
            continue

        n_index, output_node = match
        n_item = nmos.pop(n_index)
        pmos.pop(p_index)
        inverters.append(
            Inverter(
                pmos=p_item,
                nmos=n_item,
                input_node=p_item.gate,
                output_node=output_node,
            )
        )
    return inverters


def find_transmission_gates(
    pmos: List[Transistor], nmos: List[Transistor]
) -> List[TransmissionGate]:
    """Find PMOS/NMOS pairs connected source-to-source and drain-to-drain
    (in either order), driven by different (complementary) gate nets.
    Matched transistors are removed from `pmos`/`nmos` in place."""
    gates: List[TransmissionGate] = []
    p_index = 0
    while p_index < len(pmos):
        p_item = pmos[p_index]
        match_index: Optional[int] = None
        for n_index, n_item in enumerate(nmos):
            if p_item.gate != n_item.gate and set(p_item.diffusion_terminals) == set(
                n_item.diffusion_terminals
            ):
                match_index = n_index
                break
        if match_index is None:
            p_index += 1
            continue

        n_item = nmos.pop(match_index)
        pmos.pop(p_index)
        gates.append(
            TransmissionGate(
                pmos=p_item,
                nmos=n_item,
                terminal_a=p_item.drain,
                terminal_b=p_item.source,
            )
        )
    return gates


#
#
# def find_current_mirrors(transistors: List[Transistor]) -> List[CurrentMirror]:
#     """Find a diode-connected transistor (gate tied to its own drain or
#     source) plus one or more same-type transistors sharing its gate and
#     source nets. Matched transistors are removed from `transistors` in
#     place."""
#     mirrors_found: List[CurrentMirror] = []
#     i = 0
#     while i < len(transistors):
#         reference = transistors[i]
#         if not reference.is_diode_connected:
#             i += 1
#             continue
#
#         mirror_indices = [
#             j
#             for j, other in enumerate(transistors)
#             if j != i
#             and other.is_pmos == reference.is_pmos
#             and other.gate == reference.gate
#             and other.source == reference.source
#             and not other.is_diode_connected
#         ]
#         if not mirror_indices:
#             i += 1
#             continue
#
#         mirrors = [transistors[j] for j in mirror_indices]
#         for j in sorted(mirror_indices + [i], reverse=True):
#             transistors.pop(j)
#
#         mirrors_found.append(
#             CurrentMirror(
#                 reference=reference, mirrors=mirrors, tail_node=reference.source
#             )
#         )
#         # Don't advance `i`: the list shifted after popping.
#     return mirrors_found
#
#
# def find_differential_pairs(
#     transistors: List[Transistor],
# ) -> List[DifferentialPair]:
#     """Find two same-type transistors sharing a common source ("tail") node
#     but with distinct gate nets (differential inputs) and distinct drain
#     nets (differential outputs). Matched transistors are removed from
#     `transistors` in place."""
#     pairs: List[DifferentialPair] = []
#     i = 0
#     while i < len(transistors):
#         left = transistors[i]
#         match_index: Optional[int] = None
#         for j in range(i + 1, len(transistors)):
#             right = transistors[j]
#             if (
#                 right.is_pmos == left.is_pmos
#                 and right.source == left.source
#                 and right.gate != left.gate
#                 and right.drain != left.drain
#             ):
#                 match_index = j
#                 break
#         if match_index is None:
#             i += 1
#             continue
#
#         right = transistors.pop(match_index)
#         transistors.pop(i)
#         pairs.append(DifferentialPair(left=left, right=right, tail_node=left.source))
#         # Don't advance `i`: the list shifted after popping.
#     return pairs
#
#
# def find_series_stacks(transistors: List[Transistor]) -> List[SeriesStack]:
#     """Find same-type transistors chained diffusion-to-diffusion into a
#     simple linear chain (common in NAND/NOR pull-up/pull-down stacks).
#     Matched transistors are removed from `transistors` in place."""
#     stacks: List[SeriesStack] = []
#
#     for is_pmos in (True, False):
#         group = [t for t in transistors if t.is_pmos == is_pmos]
#         if len(group) < 2:
#             continue
#
#         # A net is a genuine series junction only if exactly two
#         # diffusion terminals (within this same-type group) land on it;
#         # anything else is a fan-out shared by more than two transistors
#         # and doesn't imply a simple stack. Supply/ground rails are always
#         # excluded from candidacy even if they land on exactly two
#         # terminals here -- within the small local subset of transistors
#         # left ungrouped at this point, a rail can coincidentally look
#         # like a two-terminal junction while actually being an external
#         # connection shared by unrelated branches (e.g. two independent
#         # pull-up branches that both happen to tie to VPWR).
#         net_counts: Dict[str, int] = {}
#         for t in group:
#             net_counts[t.drain] = net_counts.get(t.drain, 0) + 1
#             net_counts[t.source] = net_counts.get(t.source, 0) + 1
#
#         net_to_indices: Dict[str, List[int]] = {}
#         for index, t in enumerate(group):
#             for net in (t.drain, t.source):
#                 if net.upper() in POWER_GROUND_NETS:
#                     continue
#                 if net_counts[net] == 2:
#                     net_to_indices.setdefault(net, []).append(index)
#
#         adjacency: Dict[int, List[int]] = {index: [] for index in range(len(group))}
#         for indices in net_to_indices.values():
#             if len(indices) != 2:
#                 continue
#             a, b = indices
#             if a == b:
#                 continue
#             adjacency[a].append(b)
#             adjacency[b].append(a)
#
#         visited = [False] * len(group)
#         for start in range(len(group)):
#             if visited[start] or not adjacency[start]:
#                 continue
#
#             component: List[int] = []
#             frontier = [start]
#             seen = {start}
#             while frontier:
#                 node = frontier.pop()
#                 component.append(node)
#                 for neighbor in adjacency[node]:
#                     if neighbor not in seen:
#                         seen.add(neighbor)
#                         frontier.append(neighbor)
#             for node in component:
#                 visited[node] = True
#
#             if len(component) < 2:
#                 continue
#
#             degrees = {node: len(adjacency[node]) for node in component}
#             if any(degree > 2 for degree in degrees.values()):
#                 continue  # branching junction: not a simple stack
#
#             edge_count = sum(degrees.values()) // 2
#             if edge_count != len(component) - 1:
#                 continue  # cycle: shouldn't happen for FETs, skip defensively
#
#             endpoints = [node for node in component if degrees[node] == 1]
#             if len(endpoints) != 2:
#                 continue
#
#             ordered = [endpoints[0]]
#             previous: Optional[int] = None
#             current = endpoints[0]
#             while len(ordered) < len(component):
#                 next_node = next(
#                     neighbor for neighbor in adjacency[current] if neighbor != previous
#                 )
#                 ordered.append(next_node)
#                 previous, current = current, next_node
#
#             stacks.append(
#                 SeriesStack(
#                     transistors=[group[index] for index in ordered],
#                     is_pmos=is_pmos,
#                 )
#             )
#
#     grouped_ids = {id(t) for stack in stacks for t in stack.transistors}
#     transistors[:] = [t for t in transistors if id(t) not in grouped_ids]
#
#     return stacks
#
#
def find_super_nodes(
    primitives: Sequence[Primitive],
) -> Tuple[List[SuperNode], List[Primitive]]:

    transistors: list[Transistor] = []
    other: list[Primitive] = []

    for primitive in primitives:
        if (transistor := Transistor.try_from_primitive(primitive)) is not None:
            transistors.append(transistor)
        else:
            other.append(primitive)

    super_nodes: List[SuperNode] = []
    pmos = [t for t in transistors if t.is_pmos]
    nmos = [t for t in transistors if t.is_nmos]
    # TGs first: they are the stricter match (both diffusions shared), and
    # their devices otherwise pair up with neighbors as false inverters.
    super_nodes.extend(find_transmission_gates(pmos, nmos))
    super_nodes.extend(find_inverters(pmos, nmos))
    remaining = pmos + nmos
    leftover = other + [t.primitive for t in remaining]
    return super_nodes, leftover
