from __future__ import annotations

from dataclasses import dataclass
from typing import AbstractSet, Dict, List, Optional, Sequence, Set, Tuple, Union

from spice2sch.models import Primitive
from spice2sch.spice import GROUND_NETS, POWER_GROUND_NETS, POWER_NETS

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


@dataclass(frozen=True)
class SpLeaf:
    transistor: Transistor
    upper: str
    lower: str


@dataclass(frozen=True)
class SpSeries:
    parts: Tuple["SpNetwork", ...]
    upper: str
    lower: str


@dataclass(frozen=True)
class SpParallel:
    branches: Tuple["SpNetwork", ...]
    upper: str
    lower: str


SpNetwork = Union[SpLeaf, SpSeries, SpParallel]


def sp_transistors(network: SpNetwork) -> List[Transistor]:
    if isinstance(network, SpLeaf):
        return [network.transistor]
    children = network.parts if isinstance(network, SpSeries) else network.branches
    return [transistor for child in children for transistor in sp_transistors(child)]


@dataclass(frozen=True)
class CmosGate:
    pull_up: SpNetwork
    pull_down: SpNetwork
    output: str

    @property
    def primitives(self) -> List[Primitive]:
        return [
            t.primitive
            for t in sp_transistors(self.pull_up) + sp_transistors(self.pull_down)
        ]


SuperNode = Union[
    Inverter,
    TransmissionGate,
    CmosGate,
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


def _drop(transistors: List[Transistor], used: Sequence[Transistor]) -> None:
    used_ids = {id(transistor) for transistor in used}
    transistors[:] = [
        transistor for transistor in transistors if id(transistor) not in used_ids
    ]


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
    group: Sequence[Transistor],
    component: Sequence[int],
    adjacency: Dict[int, List[int]],
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
        group = [
            transistor for transistor in transistors if transistor.is_pmos == is_pmos
        ]
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

    _drop(
        transistors,
        [transistor for chain in chains for transistor in chain.transistors],
    )
    return chains


class _UnionFind:
    def __init__(self) -> None:
        self.parent: Dict[object, object] = {}

    def find(self, item: object) -> object:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, first: object, second: object) -> None:
        self.parent[self.find(first)] = self.find(second)


def _parallel_groups(
    edges: Sequence[Transistor], top: str, bottom: str
) -> List[List[Transistor]]:
    """Edges split into branches that meet only at ``top`` and ``bottom``."""
    sets = _UnionFind()
    for index, edge in enumerate(edges):
        sets.find(index)
        for net in edge.diffusion_terminals:
            if net not in (top, bottom):
                sets.union(index, ("net", net))
    groups: Dict[object, List[Transistor]] = {}
    for index, edge in enumerate(edges):
        groups.setdefault(sets.find(index), []).append(edge)
    return list(groups.values())


def _reachable(edges: Sequence[Transistor], start: str, blocked: str) -> Set[str]:
    seen = {start}
    frontier = [start]
    while frontier:
        net = frontier.pop()
        for edge in edges:
            if net not in edge.diffusion_terminals:
                continue
            other = edge.other_diffusion(net)
            if other not in seen and other != blocked:
                seen.add(other)
                frontier.append(other)
    return seen


def _series_cuts(edges: Sequence[Transistor], top: str, bottom: str) -> List[str]:
    """Nets every ``top``-to-``bottom`` path passes through, nearest ``top`` first."""
    nets = {net for edge in edges for net in edge.diffusion_terminals} - {top, bottom}
    cuts = [net for net in nets if bottom not in _reachable(edges, top, net)]
    distance: Dict[str, int] = {top: 0}
    frontier = [top]
    while frontier:
        following: List[str] = []
        for net in frontier:
            for edge in edges:
                if net in edge.diffusion_terminals:
                    other = edge.other_diffusion(net)
                    if other not in distance:
                        distance[other] = distance[net] + 1
                        following.append(other)
        frontier = following
    return sorted(cuts, key=lambda net: (distance.get(net, 0), net))


def _sp_sort_key(network: SpNetwork) -> Tuple[int, str]:
    transistors = sp_transistors(network)
    return (len(transistors), min(t.gate for t in transistors))


def decompose_series_parallel(
    edges: Sequence[Transistor], top: str, bottom: str
) -> Optional[SpNetwork]:
    """``edges`` as a series-parallel network from ``top`` to ``bottom``, or
    None if they don't form one (bridges, dangling branches, shorted devices)."""
    if not edges or top == bottom:
        return None
    if len(edges) == 1:
        edge = edges[0]
        if set(edge.diffusion_terminals) == {top, bottom}:
            return SpLeaf(edge, top, bottom)
        return None

    groups = _parallel_groups(edges, top, bottom)
    if len(groups) > 1:
        branches: List[SpNetwork] = []
        for group in groups:
            branch = decompose_series_parallel(group, top, bottom)
            if branch is None:
                return None
            branches.append(branch)
        return SpParallel(tuple(sorted(branches, key=_sp_sort_key)), top, bottom)

    cuts = _series_cuts(edges, top, bottom)
    if not cuts:
        return None
    chain = [top, *cuts, bottom]
    position = {net: index for index, net in enumerate(chain)}
    sets = _UnionFind()
    for edge in edges:
        inner = [net for net in edge.diffusion_terminals if net not in position]
        for net in inner:
            sets.union(("net", net), ("edge", id(edge)))
        sets.find(("edge", id(edge)))
    touches: Dict[object, Set[int]] = {}
    for edge in edges:
        root = sets.find(("edge", id(edge)))
        touches.setdefault(root, set()).update(
            position[net] for net in edge.diffusion_terminals if net in position
        )
    segments: Dict[int, List[Transistor]] = {}
    for edge in edges:
        ends = sorted(touches[sets.find(("edge", id(edge)))])
        if len(ends) != 2 or ends[1] != ends[0] + 1:
            return None
        segments.setdefault(ends[0], []).append(edge)
    if sorted(segments) != list(range(len(chain) - 1)):
        return None
    parts: List[SpNetwork] = []
    for index in range(len(chain) - 1):
        part = decompose_series_parallel(
            segments[index], chain[index], chain[index + 1]
        )
        if part is None:
            return None
        parts.extend(part.parts if isinstance(part, SpSeries) else [part])
    return SpSeries(tuple(parts), top, bottom)


def _channel_connected(transistors: Sequence[Transistor]) -> List[List[Transistor]]:
    """Transistors grouped by diffusion connectivity through signal nets."""
    sets = _UnionFind()
    for index, transistor in enumerate(transistors):
        sets.find(index)
        for net in transistor.diffusion_terminals:
            if not _is_rail(net):
                sets.union(index, ("net", net))
    groups: Dict[object, List[Transistor]] = {}
    for index, transistor in enumerate(transistors):
        groups.setdefault(sets.find(index), []).append(transistor)
    return list(groups.values())


def find_cmos_gate(group: Sequence[Transistor]) -> Optional[CmosGate]:
    """``group`` as one static CMOS gate, if it is exactly that."""
    pmos = [t for t in group if t.is_pmos]
    nmos = [t for t in group if t.is_nmos]
    if not pmos or not nmos:
        return None
    p_nets = {net for t in pmos for net in t.diffusion_terminals}
    n_nets = {net for t in nmos for net in t.diffusion_terminals}
    outputs = [net for net in p_nets & n_nets if not _is_rail(net)]
    supplies = [net for net in p_nets if net.upper() in POWER_NETS]
    grounds = [net for net in n_nets if net.upper() in GROUND_NETS]
    if len(outputs) != 1 or len(supplies) != 1 or len(grounds) != 1:
        return None
    output = outputs[0]
    if any(t.gate == output for t in group):
        return None
    pull_up = decompose_series_parallel(pmos, supplies[0], output)
    pull_down = decompose_series_parallel(nmos, output, grounds[0])
    if pull_up is None or pull_down is None:
        return None
    return CmosGate(pull_up=pull_up, pull_down=pull_down, output=output)


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
    # Count before anything else is removed. Dropping devices would hide the
    # fan-out that keeps an output from looking like a private series node.
    junctions = _series_junctions(pmos + nmos, external)
    leftover = list(other)
    for group in _channel_connected(pmos + nmos):
        group_pmos = [t for t in group if t.is_pmos]
        group_nmos = [t for t in group if t.is_nmos]
        inverters = find_inverters(group_pmos, group_nmos)
        if inverters and not group_pmos and not group_nmos:
            super_nodes.extend(inverters)
            continue
        gate = find_cmos_gate(group)
        if gate is not None:
            super_nodes.append(gate)
            continue
        super_nodes.extend(inverters)
        remaining = group_pmos + group_nmos
        super_nodes.extend(find_parallel_chains(remaining))
        super_nodes.extend(find_series_chains(remaining, junctions))
        leftover.extend(transistor.primitive for transistor in remaining)
    return super_nodes, leftover
