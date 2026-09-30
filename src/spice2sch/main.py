from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from spice2sch.models import Point, Primitive
import spice2sch.constants as constants
from spice2sch.cli_def import create_parser
from spice2sch.spice import Spice, SubcktCall
from spice2sch.symbols import SymbolIndex
from spice2sch.patterns import find_super_nodes
from spice2sch.placeable import build_placeables, next_label_name, place_in_row


def create_io_block(pins: Tuple[List[str], List[str]], origin: Point) -> str:
    output = ""
    for index, input_pin in enumerate(pins[0]):
        label = next_label_name()
        output += (
            f"C {{ipin.sym}} {origin.x} {origin.y + index * 20} 0 0 "
            f"{{name={label} lab={input_pin}}}\n"
        )

    for index, output_pin in enumerate(pins[1]):
        label = next_label_name()
        output += (
            f"C {{opin.sym}} {origin.x + 20} {origin.y + index * 20} 0 0 "
            f"{{name={label} lab={output_pin}}}\n"
        )

    return output


def create_primitive_objects(
    calls: List[SubcktCall], symbol_index: Optional[SymbolIndex]
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
        symbol = symbol_index.resolve(call.subckt_ref)
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
        sch_output += create_io_block(io_pins, constants.io_origin)

        calls = spice_file.extract_subckt_calls()

        symbol_index = None
        if args.pdk_root:
            try:
                symbol_index = SymbolIndex(Path(args.pdk_root))
            except FileNotFoundError as exc:
                parser.error(str(exc))

        primitives = create_primitive_objects(calls, symbol_index)
        external_nets = set(io_pins[0]) | set(io_pins[1])
        super_nodes, leftovers = find_super_nodes(primitives, external_nets)
        placeables = build_placeables(super_nodes, leftovers)
        place_in_row(placeables, constants.primitive_origin, constants.spacing)

        for placeable in placeables:
            sch_output += placeable.draw()

        if args.output_file:
            with open(args.output_file, "w") as outfile:
                outfile.write(sch_output)
        else:
            print(sch_output)
