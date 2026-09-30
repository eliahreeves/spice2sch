from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Dict, List, Optional, Sequence, Set, Tuple, Union

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


@dataclass(frozen=True)
class SeriesChain:
    """Same-type transistors chained diffusion-to-diffusion.

    ``transistors`` is ordered so consecutive devices share a terminal. The
    first device is the end whose free net is lexicographically smaller;
    placement may flip the chain so a supply rail sits on the outside.
    """

    transistors: Tuple[Transistor, ...]
    is_pmos: bool

    @property
    def primitives(self) -> List[Primitive]:
        return [t.primitive for t in self.transistors]


@dataclass(frozen=True)
class ParallelChain:
    """Same-type transistors that share both diffusion terminals.

    NAND pull-ups, NOR pull-downs, and multi-finger devices. Ordered by gate
    net, then instance name.
    """

    transistors: Tuple[Transistor, ...]
    is_pmos: bool

    @property
    def terminals(self) -> frozenset[str]:
        return frozenset(self.transistors[0].diffusion_terminals)

    @property
    def primitives(self) -> List[Primitive]:
        return [t.primitive for t in self.transistors]


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
    TransmissionGate,
    SeriesChain,
    ParallelChain,
    # CurrentMirror, DifferentialPair
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
def _drop(transistors: List[Transistor], used: Sequence[Transistor]) -> None:
    used_ids = {id(transistor) for transistor in used}
    transistors[:] = [transistor for transistor in transistors if id(transistor) not in used_ids]


def _series_junctions(
    transistors: Sequence[Transistor], external: AbstractSet[str]
) -> Set[str]:
    """Nets that connect exactly two diffusion terminals and nothing else.

    A CMOS series stack (NAND pull-down, NOR pull-up) meets at a private
    node. Rails, subcircuit ports, and nets that also touch a gate, a body,
    or a third diffusion are outside the stack.
    """
    diffusion_count: Dict[str, int] = {}
    other_count: Dict[str, int] = {}
    for transistor in transistors:
        for net in transistor.diffusion_terminals:
            diffusion_count[net] = diffusion_count.get(net, 0) + 1
        other_count[transistor.gate] = other_count.get(transistor.gate, 0) + 1
        if transistor.body is not None:
            other_count[transistor.body] = other_count.get(transistor.body, 0) + 1

    return {
        net
        for net, count in diffusion_count.items()
        if count == 2
        and other_count.get(net, 0) == 0
        and net not in external
        and not _is_rail(net)
    }


def _shared_diffusion(left: Transistor, right: Transistor) -> Optional[str]:
    shared = set(left.diffusion_terminals) & set(right.diffusion_terminals)
    if len(shared) != 1:
        return None
    return next(iter(shared))


def find_parallel_chains(transistors: List[Transistor]) -> List[ParallelChain]:
    """Find same-type transistors sharing both diffusion terminals.

    Matched transistors are removed from ``transistors`` in place. Runs
    before series detection: a pair that shares a signal and a rail has only
    one non-rail net, which would otherwise look like a series chain of two.
    """
    groups: Dict[Tuple[bool, frozenset[str]], List[Transistor]] = {}
    for transistor in transistors:
        terminals = frozenset(transistor.diffusion_terminals)
        if len(terminals) != 2:
            continue
        key = (transistor.is_pmos, terminals)
        groups.setdefault(key, []).append(transistor)

    chains: List[ParallelChain] = []
    used: List[Transistor] = []
    for (is_pmos, _), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda transistor: (transistor.gate, transistor.primitive.instance_name)
        )
        chains.append(ParallelChain(transistors=tuple(members), is_pmos=is_pmos))
        used.extend(members)

    _drop(transistors, used)
    return chains


def _series_adjacency(
    group: Sequence[Transistor], junctions: Set[str]
) -> Dict[int, List[int]]:
    net_to_indices: Dict[str, List[int]] = {}
    for index, transistor in enumerate(group):
        seen: Set[str] = set()
        for net in transistor.diffusion_terminals:
            if net in junctions and net not in seen:
                seen.add(net)
                net_to_indices.setdefault(net, []).append(index)

    adjacency: Dict[int, List[int]] = {index: [] for index in range(len(group))}
    for indices in net_to_indices.values():
        if len(indices) != 2 or indices[0] == indices[1]:
            continue
        left, right = indices
        # Both terminals in common is a parallel pair, not a series link.
        same_terminals = set(group[left].diffusion_terminals) == set(
            group[right].diffusion_terminals
        )
        if same_terminals:
            continue
        adjacency[left].append(right)
        adjacency[right].append(left)
    return adjacency


def _order_series_path(
    group: Sequence[Transistor], component: Sequence[int], adjacency: Dict[int, List[int]]
) -> Optional[List[Transistor]]:
    degrees = {node: len(adjacency[node]) for node in component}
    if any(degree > 2 for degree in degrees.values()):
        return None
    if sum(degrees.values()) // 2 != len(component) - 1:
        return None
    endpoints = [node for node in component if degrees[node] == 1]
    if len(endpoints) != 2:
        return None

    def walk(start: int) -> List[int]:
        ordered = [start]
        previous: Optional[int] = None
        current = start
        while len(ordered) < len(component):
            next_node = next(
                neighbor for neighbor in adjacency[current] if neighbor != previous
            )
            ordered.append(next_node)
            previous, current = current, next_node
        return ordered

    def outer_net(order: Sequence[int]) -> str:
        shared = _shared_diffusion(group[order[0]], group[order[1]])
        if shared is None:
            raise ValueError("series path neighbors do not share one diffusion net")
        return group[order[0]].other_diffusion(shared)

    candidates = [walk(endpoint) for endpoint in endpoints]
    best = min(candidates, key=outer_net)
    return [group[index] for index in best]


def find_series_chains(
    transistors: List[Transistor], junctions: Set[str]
) -> List[SeriesChain]:
    """Find same-type transistors chained through ``junctions``.

    Each chain is a simple path (NAND/NOR stacks). Branched networks, such
    as an AOI pull-down, are left for the inner series segments only.
    Matched transistors are removed from ``transistors`` in place.
    """
    chains: List[SeriesChain] = []
    for is_pmos in (True, False):
        group = [transistor for transistor in transistors if transistor.is_pmos == is_pmos]
        if len(group) < 2:
            continue
        adjacency = _series_adjacency(group, junctions)

        visited: Set[int] = set()
        for start in range(len(group)):
            if start in visited or not adjacency[start]:
                continue
            component: List[int] = []
            frontier = [start]
            visited.add(start)
            while frontier:
                node = frontier.pop()
                component.append(node)
                for neighbor in adjacency[node]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        frontier.append(neighbor)
            if len(component) < 2:
                continue
            ordered = _order_series_path(group, component, adjacency)
            if ordered is None:
                continue
            chains.append(SeriesChain(transistors=tuple(ordered), is_pmos=is_pmos))

    _drop(transistors, [transistor for chain in chains for transistor in chain.transistors])
    return chains


def find_super_nodes(
    primitives: Sequence[Primitive],
    external_nets: AbstractSet[str] = frozenset(),
) -> Tuple[List[SuperNode], List[Primitive]]:
    """Group transistors into supernodes. Ungrouped devices are leftovers.

    ``external_nets`` (subcircuit ports) are not internal series junctions.
    Nets on non-transistor devices are treated the same way.
    """
    transistors: list[Transistor] = []
    other: list[Primitive] = []

    for primitive in primitives:
        if (transistor := Transistor.try_from_primitive(primitive)) is not None:
            transistors.append(transistor)
        else:
            other.append(primitive)

    external = set(external_nets)
    for primitive in other:
        external.update(primitive.nodes)

    super_nodes: List[SuperNode] = []
    pmos = [transistor for transistor in transistors if transistor.is_pmos]
    nmos = [transistor for transistor in transistors if transistor.is_nmos]
    # TGs first: they are the stricter match (both diffusions shared), and
    # their devices otherwise pair up with neighbors as false inverters.
    super_nodes.extend(find_transmission_gates(pmos, nmos))
    super_nodes.extend(find_inverters(pmos, nmos))
    remaining = pmos + nmos
    # Count before parallel devices are removed. Dropping them would hide
    # the fan-out that keeps an output from looking like a private series node.
    junctions = _series_junctions(remaining, external)
    super_nodes.extend(find_parallel_chains(remaining))
    super_nodes.extend(find_series_chains(remaining, junctions))
    leftover = other + [transistor.primitive for transistor in remaining]
    return super_nodes, leftover
