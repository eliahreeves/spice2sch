from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from spice2sch.models import (
    Point,
    Primitive,
    Wire,
)
import spice2sch.constants as constants
from spice2sch.cli_def import create_parser
from spice2sch.spice import Spice, SubcktCall
from spice2sch.symbols import SymbolIndex
from spice2sch.patterns import Transistor, find_super_nodes

p_value = 0


def create_io_block(pins: Tuple[List[str], List[str]], origin: Point) -> str:
    global p_value
    output = ""
    for index, input_pin in enumerate(pins[0]):
        output += f"C {{ipin.sym}} {origin.x} {origin.y + index * 20} 0 0 {{name=p{p_value} lab={input_pin}}}\n"
        p_value += 1

    for index, output_pin in enumerate(pins[1]):
        output += f"C {{opin.sym}} {origin.x + 20} {origin.y + index * 20} 0 0 {{name=p{p_value} lab={output_pin}}}\n"
        p_value += 1

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


def _lab_pin_orientation(pin_x: float, pin_y: float) -> Tuple[int, int]:
    if abs(pin_x) >= abs(pin_y):
        return (2, 0) if pin_x >= 0 else (0, 0)
    return (3, 0) if pin_y >= 0 else (1, 0)


def create_single_primitive(primitive: Primitive, pos: Point) -> str:
    global p_value
    output = ""

    attr_lines = [f"name={primitive.instance_name}"]
    for param in primitive.params:
        name, value = param.split("=", 1)
        canonical = primitive.symbol.normalize_param_name(name)
        attr_lines.append(f"{canonical}={value}")
    attr_lines.append(f"model={primitive.model}")
    attr_lines.append("spiceprefix=X")

    newline = "\n"
    output += (
        f"C {{{primitive.symbol.sch_path}}} {pos.x} {pos.y} 0 0 "
        "{"
        f"{newline.join(attr_lines)}"
        "}\n"
    )

    for node, pin in zip(primitive.nodes, primitive.symbol.pins):
        pin_x = int(round(pos.x + pin.x))
        pin_y = int(round(pos.y + pin.y))
        orient = _lab_pin_orientation(pin.x, pin.y)
        output += (
            f"C {{lab_pin.sym}} {pin_x} {pin_y} {orient[0]} {orient[1]} "
            f"{{name=p{p_value} sig_type=std_logic lab={node}}}\n"
        )
        p_value += 1

    return output


def create_xschem_primitive_row(primitives: List[Primitive], origin: Point) -> str:
    output = ""
    for index, item in enumerate(primitives):
        pos = Point(origin.x + (index * constants.spacing), origin.y)
        output += create_single_primitive(item, pos)
    return output


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.input_file is None:
        parser.error("No input provided. Use -i FILE or pipe data to stdin.")

    with args.input_file as infile:
        spice_input = infile.read()
        spice_file = Spice(spice_input)

        sch_output = constants.file_header

        # create io_pins
        io_pins = spice_file.extract_io()
        sch_output += create_io_block(io_pins, constants.io_origin)

        calls = spice_file.extract_subckt_calls()

        symbol_index = None
        if args.pdk_root:
            try:
                symbol_index = SymbolIndex(Path(args.pdk_root))
            except FileNotFoundError as exc:
                parser.error(str(exc))

        # create list of devices (FET and non-FET alike) via PDK symbol lookup
        primitives = create_primitive_objects(calls, symbol_index)
        (super_nodes, primitives) = find_super_nodes(primitives)

        sch_output += create_xschem_node_row(primitives, constants.primitive_origin)

        sch_output += create_xschem_primitive_row(
            primitives, constants.primitive_origin
        )

        if args.output_file:
            with open(args.output_file, "w") as outfile:
                outfile.write(sch_output)
        else:
            print(sch_output)
