from argparse import ArgumentParser, FileType
import sys
import math
from typing import List, Tuple, NamedTuple


file_header = """v {xschem version=3.4.6RC file_version=1.2
}
G {}
K {}
V {}
S {}
E {}
"""

p_value = 0


class Point(NamedTuple):
    x: int
    y: int


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

    # subckt_name = tokens[1]
    ports = tokens[2:]

    power_ground = {"VDD", "VCC", "VSS", "GND", "VGND", "VPWR", "VNB", "VPB"}

    inputs: List[str] = []
    outputs: List[str] = []
    found_output = False

    for port in reversed(ports):  # Start from the end
        if port in power_ground:
            found_output = True

        if not found_output:
            outputs.append(port)
        else:
            inputs.append(port)

    inputs.reverse()  # Restore input order
    outputs.reverse()
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
            return file[start + 1 : index]

    raise ValueError("Invalid format")


def create_transistors(items: List[str], origin: Point) -> str:
    global p_value
    output = ""
    grid_width = math.floor(math.sqrt(len(items)))
    for index, item in enumerate(items):
        pos = Point(
            origin.x + ((index % grid_width) * 280),
            origin.y + ((index // grid_width) * 180),
        )

        item_list = item.split(" ")
        transistor_length = item_list.pop()
        transistor_width = item_list.pop()
        transistor_name = item_list.pop().split("__")

        transistor_body = item_list.pop()
        transistor_drain = item_list.pop()
        transistor_gate = item_list.pop()
        transistor_source = item_list.pop()

        output += f"C {{{'/'.join(transistor_name)}.sym}} {pos.x} {pos.y} 0 0 {{name=M{index}\nW={transistor_width[2:]}\nL={transistor_length[2:]}\nmodel={transistor_name[1]}\nspiceprefix=X\n}}\n"

        output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor_body}}}\n"
        p_value += 1

        output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y - 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor_source}}}\n"
        p_value += 1

        output += f"C {{lab_pin.sym}} {pos.x + 20} {pos.y + 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor_drain}}}\n"
        p_value += 1

        output += f"C {{lab_pin.sym}} {pos.x - 20} {pos.y} 0 0 {{name=p{p_value} sig_type=std_logic lab={transistor_gate}}}\n"
        p_value += 1
    return output


def main() -> None:
    parser = ArgumentParser(description="Copy the contents of one file to another.")
    parser.add_argument(
        "-i",
        "--input-file",
        type=FileType("r"),
        default=sys.stdin,
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

    args = parser.parse_args()

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
