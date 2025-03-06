from argparse import ArgumentParser, FileType
import sys
from typing import List, NoReturn, Tuple
from importlib.metadata import version, PackageNotFoundError
from dataclasses import dataclass


file_header = """v {xschem version=3.4.6RC file_version=1.2
}
G {}
K {}
V {}
S {}
E {}
"""

p_value = 0


class Transistor:
    def __init__(self, spice_line: str):
        items = spice_line.split(" ")
        self.length = items[-1]
        self.width = items[-2]
        self.name = items[-3].split("__")
        self.body = items[-4]
        self.drain = items[-5]
        self.gate = items[-6]
        self.source = items[-7]

        # Normalize VPWR/VGND connections
        if self.drain == "VPWR" or self.source == "VGND":
            self.drain, self.source = self.source, self.drain

    @property
    def is_pmos(self) -> bool:
        return self.name[1].startswith("p")


@dataclass
class Point:
    x: int
    y: int


def get_version():
    try:
        return version("spice-to-sch")
    except PackageNotFoundError:
        return "Unknown (not installed as a package)"


def extract_io_from_spice(content: List[str]) -> Tuple[List[str], List[str]]:
    subckt_line = None

    for line in content:
        line = line.strip()
        if line.lower().startswith(".subckt"):
            subckt_line = line
            break

    if not subckt_line:
        raise ValueError("No .subckt definition found in the SPICE file.")

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


def find_content(file: List[str]) -> List[Transistor]:
    start = 0
    for index, line in enumerate(file):
        line = line.strip()
        if line.lower().startswith(".subckt"):
            start = index
        elif line.lower().startswith(".ends"):
            return [Transistor(line) for line in file[start + 1 : index]]

    raise ValueError("Invalid format")

    items = item.split(" ")
    transistor_name = items[-3].split("__")[1]
    return transistor_name.startswith("p")


def create_single_transistor(
    transistor: Transistor, pos: Point, index: int, num_pmos: int = 0
) -> str:
    global p_value
    output = ""

    output += f"C {{{'/'.join(transistor.name)}.sym}} {pos.x} {pos.y} 0 0 {{name=M{index + num_pmos}\nW={transistor.width[2:]}\nL={transistor.length[2:]}\nmodel={transistor.name[1]}\nspiceprefix=X\n}}\n"

    output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.body}}}\n"
    p_value += 1

    output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y - 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.source}}}\n"
    p_value += 1

    output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y + 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.drain}}}\n"
    p_value += 1

    output += f"C {{lab_pin.sym}} {pos.x - 20} {pos.y} 0 0 {{name=p{p_value} sig_type=std_logic lab={transistor.gate}}}\n"
    p_value += 1

    return output


def create_transistors(transistors: List[Transistor], origin: Point) -> str:
    pmos_transistors = []
    nmos_transistors = []

    for trans in transistors:
        if trans.is_pmos:
            pmos_transistors.append(trans)
        else:
            nmos_transistors.append(trans)

    output = ""
    for index, trans in enumerate(pmos_transistors):
        pos = Point(origin.x + (index * 120), origin.y)
        output += create_single_transistor(trans, pos, index)

    nmos_origin = Point(origin.x, origin.y + 280)

    for index, trans in enumerate(nmos_transistors):
        pos = Point(nmos_origin.x + (index * 120), nmos_origin.y)
        output += create_single_transistor(trans, pos, index, len(pmos_transistors))

    return output


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Convert SkyWater SKY130 spice files into xschem .sch files."
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=get_version(),
        help="Show version and exit",
    )
    parser.add_argument(
        "-i",
        "--input-file",
        type=FileType("r"),
        default=sys.stdin if not sys.stdin.isatty() else None,
        required=False,
        help="Input file to read from",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        type=FileType("w"),
        default=sys.stdout,
        required=False,
        help="Output file to write to",
    )

    def error_and_exit(message: str) -> NoReturn:
        print(f"Error: {message}\n", file=sys.stderr)
        parser.print_help()
        sys.exit(2)

    parser.error = error_and_exit

    return parser


def main() -> None:
    parser = create_parser()
    args = parser.parse_args()

    if args.input_file is None:
        parser.error("No input provided. Use -i FILE or pipe data to stdin.")

    with args.input_file as infile, args.output_file as outfile:
        spice_input = infile.read()
        sch_output = file_header
        io_pins = extract_io_from_spice(spice_input.split("\n"))
        sch_output += create_io_block(io_pins, Point(-120, -40))
        sch_output += create_transistors(
            find_content(spice_input.split("\n")), Point(20, 30)
        )
        outfile.write(sch_output)


if __name__ == "__main__":
    main()
