from typing import List, Tuple
from spice_to_sch.models import Point, Wire, Transistor, TransistorGroup
import spice_to_sch.constants as constants
from spice_to_sch.cli_def import create_parser

p_value = 0


def extract_io_from_spice(subckt_line: str) -> Tuple[List[str], List[str]]:
    tokens = subckt_line.split()
    if len(tokens) < 3:
        raise ValueError("Invalid format")

    ports = tokens[2:]

    power_ground = {"VDD", "VCC", "VSS", "GND", "VGND", "VPWR", "VNB", "VPB"}

    inputs: List[str] = []
    outputs: List[str] = []
    found_inputs = False

    for port in ports:
        is_port_power_ground = port in power_ground
        if is_port_power_ground:
            found_inputs = True

        if not found_inputs or is_port_power_ground:
            inputs.append(port)
        else:
            outputs.append(port)
    return (inputs, outputs)


def create_io_block(pins: Tuple[List[str], List[str]], origin: Point) -> str:
    global p_value
    output = ""
    for input in pins[0]:
        output += f"C {{ipin.sym}} {origin.x} {origin.y + p_value * 20} 0 0 {{name=p{p_value} lab={input}}}\n"
        p_value += 1

    for index, output_pin in enumerate(pins[1]):
        output += f"C {{opin.sym}} {origin.x + 20} {origin.y - (index + 1) * 20} 0 0 {{name=p{p_value} lab={output_pin}}}\n"
        p_value += 1

    return output


def find_content(file: List[str]) -> List[str]:
    start = 0
    for index, line in enumerate(file):
        line = line.strip()
        if line.lower().startswith(".subckt"):
            start = index
        elif line.lower().startswith(".ends"):
            return file[start: index + 1]

    raise ValueError("Invalid format")


def create_transistor_objects(spice: List[str]) -> List[Transistor]:
    transistors = [
        Transistor.from_spice_line(line, index)
        for index, line in enumerate(spice[1:-1])
    ]
    # Sort transistors by gate name alphabetically
    return sorted(transistors, key=lambda x: x.gate.lower())


def find_parallel_transistors(transistors: List[Transistor]) -> List[TransistorGroup]:
    groups: List[TransistorGroup] = []
    i = 0

    while i < len(transistors):
        t1 = transistors[i]
        parallel_group = [t1]
        j = i + 1

        while j < len(transistors):
            t2 = transistors[j]
            if (t1.source == t2.source and
                t1.drain == t2.drain and
                    t1.is_pmos == t2.is_pmos):
                parallel_group.append(t2)
                transistors.pop(j)  # Remove from list if it's parallel
            else:
                j += 1

        if len(parallel_group) > 1:
            groups.append(TransistorGroup(parallel_group))
            transistors.pop(i)  # Remove the first transistor of the group
        else:
            i += 1

    return groups


def create_single_transistor(transistor: Transistor, pos: Point) -> str:
    global p_value
    output = ""

    output += f"C {{{transistor.library}/{transistor.name}.sym}} {pos.x} {pos.y} 0 0 {{name=M{transistor.id}\nW={transistor.width}\nL={transistor.length}\nmodel={transistor.name}\nspiceprefix=X\n}}\n"

    output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.body}}}\n"
    p_value += 1

    output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y - 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.source}}}\n"
    p_value += 1

    output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y + 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.drain}}}\n"
    p_value += 1

    output += f"C {{lab_pin.sym}} {pos.x - 20} {pos.y} 0 0 {{name=p{p_value} sig_type=std_logic lab={transistor.gate}}}\n"
    p_value += 1

    return output


def create_xschem_transistor_row(transistors: List[Transistor], origin: Point) -> str:
    output = ""
    for index, item in enumerate(transistors):
        pos = Point(origin.x + (index * constants.spacing), origin.y)
        output += create_single_transistor(item, pos)
    return output


def create_parallel_transistors(groups: List[TransistorGroup], origin: Point) -> str:
    output = ""
    current_x = origin.x

    for group in groups:
        # Store the positions of transistors in this group for wire connections
        transistor_positions: List[Point] = []

        # Create transistors in the group
        for index, item in enumerate(group.transistors):
            pos = Point(current_x + (index * constants.spacing), origin.y)
            transistor_positions.append(pos)
            output += create_single_transistor(item, pos)

        # Create wires
        if len(transistor_positions) > 1:
            first_trans = group.transistors[0]

            source_wire = Wire(
                start_x=transistor_positions[0].x + 20,
                start_y=origin.y - 30,
                end_x=transistor_positions[-1].x + 20,
                end_y=origin.y - 30,
                label=first_trans.source
            )
            output += source_wire.to_xschem()

            drain_wire = Wire(
                start_x=transistor_positions[0].x + 20,
                start_y=origin.y + 30,
                end_x=transistor_positions[-1].x + 20,
                end_y=origin.y + 30,
                label=first_trans.drain
            )
            output += drain_wire.to_xschem()

        # Update x position for next group
        current_x += (len(group.transistors) *
                      constants.spacing) + constants.spacing

    return output


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.input_file is None:
        parser.error("No input provided. Use -i FILE or pipe data to stdin.")

    with args.input_file as infile, args.output_file as outfile:
        spice_input = infile.read()
        spice_input_lines = spice_input.split("\n")

        # includes .subckt and .ends
        spice_content_lines = find_content(spice_input_lines)

        sch_output = constants.file_header

        # create io_pins
        io_pins = extract_io_from_spice(spice_content_lines[0])
        sch_output += create_io_block(io_pins, constants.io_origin)

        # create list of transistors
        transistors = create_transistor_objects(spice_content_lines)

        # sort
        parallel_transistors = find_parallel_transistors(transistors)

        # group extras into pmos/nmos
        extra_pmos_transistors = TransistorGroup([])
        extra_nmos_transistors = TransistorGroup([])
        for item in transistors:
            if item.is_pmos:
                extra_pmos_transistors.transistors.append(item)
            else:
                extra_nmos_transistors.transistors.append(item)

        # draw transistors
        sch_output += create_xschem_transistor_row(
            extra_pmos_transistors.transistors, constants.pmos_origin
        )
        sch_output += create_xschem_transistor_row(
            extra_nmos_transistors.transistors, constants.nmos_origin
        )
        sch_output += create_parallel_transistors(
            parallel_transistors, constants.parallel_origin)

        outfile.write(sch_output)
