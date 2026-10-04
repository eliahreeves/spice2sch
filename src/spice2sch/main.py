from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from spice2sch.models import Point, Primitive
import spice2sch.constants as constants
from spice2sch.cli_def import create_parser
from spice2sch.spice import Spice, SubcktCall
from spice2sch.symbols import SymbolIndex
from spice2sch.patterns import find_super_nodes
from spice2sch.placeable import next_label_name, render
from spice2sch.supernode_place import build_placeables
from spice2sch.placement import place


def create_io_block(
    pins: Tuple[List[str], List[str]], origin: Point, order: Sequence[str] = ()
) -> str:
    """Emit ipin/opin symbols. xschem netlists ports in pin-instance order,
    so pins are written in `order` (the netlist's .subckt port order) when
    given, which matters when outputs precede the rails (gf180mcu)."""
    inputs, outputs = pins
    rank = {port: index for index, port in enumerate(order)}
    tagged = [(pin, False, i) for i, pin in enumerate(inputs)] + [
        (pin, True, i) for i, pin in enumerate(outputs)
    ]
    tagged.sort(key=lambda item: rank.get(item[0], len(rank)))

    output = ""
    for pin, is_output, index in tagged:
        label = next_label_name()
        symbol, x = ("opin.sym", origin.x + 20) if is_output else ("ipin.sym", origin.x)
        output += (
            f"C {{{symbol}}} {x} {origin.y + index * 20} 0 0 "
            f"{{name={label} lab={pin}}}\n"
        )

    return output


def create_primitive_objects(
    calls: List[SubcktCall],
    symbol_index: Optional[SymbolIndex],
    cell_name: Optional[str] = None,
) -> List[Primitive]:
    primitives: List[Primitive] = []
    if not calls:
        return primitives

    if symbol_index is None:
        refs = ", ".join(sorted({call.subckt_ref for call in calls}))
        raise SystemExit(
            "Devices require a PDK root for symbol lookup "
            f"(set PDK_ROOT or pass --pdk-root). Found: {refs}"
        )

    for index, call in enumerate(calls):
        symbol = symbol_index.resolve(call.subckt_ref, near=cell_name)
        if symbol is None:
            print(
                f"Warning: no PDK symbol for {call.subckt_ref}; skipping",
                file=sys.stderr,
            )
            continue
        try:
            primitives.append(Primitive.from_subckt_call(call, index, symbol))
        except ValueError as exc:
            print(f"Warning: {exc}; skipping", file=sys.stderr)

    return primitives


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.input_file is None:
        parser.error("No input provided. Use -i FILE or pipe data to stdin.")

    with args.input_file as infile:
        spice_input = infile.read()
        spice_file = Spice(spice_input)

        sch_output = constants.file_header

        io_pins = spice_file.extract_io()
        sch_output += create_io_block(
            io_pins, constants.io_origin, order=spice_file.ports
        )

        calls = spice_file.extract_subckt_calls()

        symbol_index = None
        if args.pdk_root:
            try:
                symbol_index = SymbolIndex(Path(args.pdk_root))
            except FileNotFoundError as exc:
                parser.error(str(exc))

        primitives = create_primitive_objects(calls, symbol_index, spice_file.name)
        external_nets = set(io_pins[0]) | set(io_pins[1])
        super_nodes, leftovers = find_super_nodes(primitives, external_nets)
        placeables = build_placeables(super_nodes, leftovers)
        wires = place(
            placeables,
            constants.primitive_origin,
            constants.placement_gap,
            inputs=io_pins[0],
            outputs=io_pins[1],
        )
        sch_output += render(placeables, wires, external_nets)

        if args.output_file:
            with open(args.output_file, "w") as outfile:
                outfile.write(sch_output)
        else:
            print(sch_output)
