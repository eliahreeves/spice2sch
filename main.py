from argparse import ArgumentParser, FileType
import sys
from typing import List, NoReturn, Tuple
from importlib.metadata import version, PackageNotFoundError
from dataclasses import dataclass


@dataclass
class Point:
    x: int
    y: int

    def __add__(self, other: "Point") -> "Point":
        return Point(self.x + other.x, self.y + other.y)


io_origin = Point(-120, -40)
pmos_origin = Point(0, 0)
nmos_origin = pmos_origin + Point(0, 200)
spacing = 120
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
    def __init__(
        self,
        length: str,
        width: str,
        library: str,
        name: str,
        body: str,
        drain: str,
        gate: str,
        source: str,
        id: int,
    ):
        self.length = length
        self.width = width
        self.library = library
        self.name = name
        self.body = body
        self.drain = drain
        self.gate = gate
        self.source = source
        self.id = id

    @classmethod
    def from_spice_line(cls, line: str, index: int):
        items = line.split(" ")
        library_name = items[-3].split("__")

        transistor = cls(
            length=items[-1][2:],
            width=items[-2][2:],
            library=library_name[0],
            name=library_name[1],
            body=items[-4],
            drain=items[-5],
            gate=items[-6],
            source=items[-7],
            id=index,
        )

        transistor.normalize()
        return transistor

    def normalize(self):
        if self.drain == "VPWR" or self.source == "VGND":
            self.drain, self.source = self.source, self.drain

    @property
    def is_pmos(self) -> bool:
        return self.name.startswith("p")

    @property
    def is_nmos(self) -> bool:
        return self.name.startswith("n")


class TransistorGroup:
    def __init__(self, transistors: List[Transistor]):
        self.transistors = transistors


def get_version():
    try:
        return version("spice-to-sch")
    except PackageNotFoundError:
        return "Unknown (not installed as a package)"


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
        pos = Point(origin.x + (index * spacing), origin.y)
        output += create_single_transistor(item, pos)
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
        spice_input_lines = spice_input.split("\n")

        # includes .subckt and .ends
        spice_content_lines = find_content(spice_input_lines)

        sch_output = file_header

        # create io_pins
        io_pins = extract_io_from_spice(spice_content_lines[0])
        sch_output += create_io_block(io_pins, io_origin)

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
            extra_pmos_transistors.transistors, pmos_origin
        )
        sch_output += create_xschem_transistor_row(
            extra_nmos_transistors.transistors, nmos_origin
        )

        outfile.write(sch_output)


if __name__ == "__main__":
    main()
