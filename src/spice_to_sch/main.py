from typing import List, Tuple
from spice_to_sch.models import Point, Transistor, TransistorGroup
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
            return file[start : index + 1]

    raise ValueError("Invalid format")


def create_transistor_objects(spice: List[str]) -> List[Transistor]:
    return [
        Transistor.from_spice_line(line, index)
        for index, line in enumerate(spice[1:-1])
    ]


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

        # group them
        extra_pmos_transistors = TransistorGroup([])
        extra_nmos_transistors = TransistorGroup([])

        for item in transistors:
            if item.is_pmos:
                extra_pmos_transistors.transistors.append(item)
            else:
                extra_nmos_transistors.transistors.append(item)

        sch_output += create_xschem_transistor_row(
            extra_pmos_transistors.transistors, constants.pmos_origin
        )
        sch_output += create_xschem_transistor_row(
            extra_nmos_transistors.transistors, constants.nmos_origin
        )

        outfile.write(sch_output)
