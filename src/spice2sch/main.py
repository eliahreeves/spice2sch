from typing import List, Tuple
from spice2sch.models import (
    Inverter,
    Point,
    TransmissionGate,
    Wire,
    Transistor,
    TransistorGroup,
)
import spice2sch.constants as constants
from spice2sch.cli_def import create_parser
from spice2sch.spice import Spice

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


def create_transistor_objects(spice: Spice) -> List[Transistor]:
    transistors = []
    for index, call in enumerate(spice.extract_subckt_calls()):
        t = Transistor.from_subckt_call(call, index)
        if t is not None:
            transistors.append(t)
    # Sort transistors by gate name alphabetically
    return sorted(transistors, key=lambda x: x.gate.lower())


def find_inverters(pmos: List[Transistor], nmos: List[Transistor]) -> List[Inverter]:
    groups: List[Inverter] = []
    p_index = 0
    while p_index < len(pmos):
        p_item = pmos[p_index]
        for n_index, n_item in enumerate(nmos):
            # Check if transistors share a connection
            shared_node = None
            if p_item.source in [n_item.source, n_item.drain]:
                shared_node = p_item.source
            elif p_item.drain in [n_item.source, n_item.drain]:
                shared_node = p_item.drain

            if shared_node is None:
                continue
            # Check if other connections are VPWR and VGND
            p_other = p_item.drain if p_item.source == shared_node else p_item.source
            n_other = n_item.drain if n_item.source == shared_node else n_item.source
            if p_other == "VPWR" and n_other == "VGND":
                groups.append(Inverter(p_item, n_item))
                del pmos[p_index]
                del nmos[n_index]
                break
        else:
            p_index += 1
    return groups


def find_transmission_gates(
    pmos: List[Transistor], nmos: List[Transistor]
) -> List[TransmissionGate]:
    groups: List[TransmissionGate] = []
    p_index = 0
    while p_index < len(pmos):
        p_item = pmos[p_index]
        for n_index, n_item in enumerate(nmos):
            if (p_item.source == n_item.source and p_item.drain == n_item.drain) or (
                p_item.source == n_item.drain and p_item.drain == n_item.source
            ):
                groups.append(TransmissionGate(p_item, n_item))
                del pmos[p_index]
                del nmos[n_index]
                break
        else:
            p_index += 1
    return groups


# def find_parallel_transistors(transistors: List[Transistor]) -> List[TransistorGroup]:
#     groups: List[TransistorGroup] = []
#     i = 0

#     while i < len(transistors):
#         t1 = transistors[i]
#         parallel_group = [t1]
#         j = i + 1

#         while j < len(transistors):
#             t2 = transistors[j]
#             if (t1.source == t2.source and
#                 t1.drain == t2.drain and
#                     t1.is_pmos == t2.is_pmos):
#                 parallel_group.append(t2)
#                 transistors.pop(j)  # Remove from list if it's parallel
#             else:
#                 j += 1

#         if len(parallel_group) > 1:
#             groups.append(TransistorGroup(parallel_group))
#             transistors.pop(i)  # Remove the first transistor of the group
#         else:
#             i += 1

#     return groups


def create_single_transistor(
    transistor: Transistor,
    pos: Point,
    print_source: bool = True,
    print_drain: bool = True,
    print_gate: bool = True,
    orientation: Tuple[int, int] = (0, 0),
) -> str:
    global p_value
    output = ""

    # make parameter names uppercase
    fixed_transistor_params = []
    for param in transistor.params:
        name, value = param.split("=")
        fixed_transistor_params.append(f"{name.upper()}={value}")

    # Create transistor symbol
    newline = "\n"
    output += (
        f"C {{{transistor.library}/{transistor.symbol_name}.sym}} {pos.x} {pos.y} {orientation[0]} {orientation[1]} "
        "{"
        f"name=M{transistor.id}\n"
        f"{newline.join(fixed_transistor_params)}\n"
        f"model={transistor.name}\n"
        "spiceprefix=X\n"
        "}\n"
    )

    # Create body pin
    body_pos = pos
    body_orientation = (2, 0)
    if orientation == (0, 0):
        body_pos += Point(20, 0)
    # pmos in transmission_gates
    elif orientation == (1, 0):
        body_pos += Point(0, 20)
        body_orientation = (3, 0)
    # pmos in transmission_gates
    elif orientation == (3, 0):
        body_pos += Point(0, -20)
        body_orientation = (1, 0)
    output += f"C {{lab_pin.sym}} {body_pos.x} {body_pos.y} {body_orientation[0]} {body_orientation[1]} {{name=p{p_value} sig_type=std_logic lab={transistor.body}}}\n"
    p_value += 1

    # Create source pin
    if print_source:
        source_pos = pos
        if orientation == (0, 0):
            source_pos += Point(20, -30)
        output += f"C {{lab_pin.sym}} {source_pos.x} {source_pos.y} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.source}}}\n"
        p_value += 1

    # Create drain pin
    if print_drain:
        drain_pos = pos
        if orientation == (0, 0):
            drain_pos += Point(20, 30)
        output += f"C {{lab_pin.sym}} {drain_pos.x} {drain_pos.y} 2 0 {{name=p{p_value} sig_type=std_logic lab={transistor.drain}}}\n"
        p_value += 1

    # Create gate pin
    if print_gate:
        gate_pos = pos
        if orientation == (0, 0):
            gate_pos += Point(-20, 0)
        elif orientation == (1, 0):
            gate_pos += Point(0, -20)
        elif orientation == (3, 0):
            gate_pos += Point(0, 20)

        output += f"C {{lab_pin.sym}} {gate_pos.x} {gate_pos.y} 0 0 {{name=p{p_value} sig_type=std_logic lab={transistor.gate}}}\n"
        p_value += 1

    return output


def create_xschem_transistor_row(transistors: List[Transistor], origin: Point) -> str:
    output = ""
    for index, item in enumerate(transistors):
        pos = Point(origin.x + (index * constants.spacing), origin.y)
        output += create_single_transistor(item, pos)
    return output


def create_inverters(inverters: List[Inverter], origin: Point) -> str:
    global p_value
    output = ""
    current_x = origin.x

    for inverter in inverters:
        pmos_pos = Point(current_x, origin.y - 30)
        nmos_pos = Point(current_x, origin.y + 30)

        # Create both transistors
        output += create_single_transistor(
            inverter.pmos, pmos_pos, print_drain=False, print_gate=False
        )
        output += create_single_transistor(
            inverter.nmos, nmos_pos, print_source=False, print_gate=False
        )

        output += f"C {{lab_pin.sym}} {nmos_pos.x - 60} {nmos_pos.y - 30} 0 0 {{name=p{p_value} sig_type=std_logic lab={inverter.nmos.gate}}}\n"
        p_value += 1
        output += f"C {{lab_pin.sym}} {nmos_pos.x + 140} {nmos_pos.y - 30} 2 0 {{name=p{p_value} sig_type=std_logic lab={inverter.nmos.source}}}\n"
        p_value += 1

        # Create wires
        input_wire = Wire(
            start_x=nmos_pos.x - 20,
            start_y=nmos_pos.y - 30,
            end_x=nmos_pos.x - 60,
            end_y=nmos_pos.y - 30,
            label=inverter.pmos.drain,
        )
        output += input_wire.to_xschem()

        connecting_wire = Wire(
            start_x=pmos_pos.x - 20,
            start_y=pmos_pos.y,
            end_x=nmos_pos.x - 20,
            end_y=nmos_pos.y,
            label=inverter.pmos.drain,
        )
        output += connecting_wire.to_xschem()

        output_wire = Wire(
            start_x=nmos_pos.x + 20,
            start_y=nmos_pos.y - 30,
            end_x=nmos_pos.x + 140,
            end_y=nmos_pos.y - 30,
            label=inverter.pmos.drain,
        )
        output += output_wire.to_xschem()

        # Update x position for next inverter
        current_x += constants.spacing * 3

    return output


def create_transmission_gates(gates: List[TransmissionGate], origin: Point) -> str:
    global p_value
    output = ""
    current_x = origin.x

    for gate in gates:
        pmos_pos = Point(current_x, origin.y - 80)  # should be 40
        nmos_pos = Point(current_x, origin.y + 80)

        # Create both transistors
        output += create_single_transistor(
            gate.pmos,
            pmos_pos,
            print_drain=False,
            print_source=False,
            orientation=(1, 0),
        )
        output += create_single_transistor(
            gate.nmos,
            nmos_pos,
            print_source=False,
            print_drain=False,
            orientation=(3, 0),
        )

        output += f"C {{lab_pin.sym}} {nmos_pos.x - 70} {nmos_pos.y - 80} 0 0 {{name=p{p_value} sig_type=std_logic lab={gate.nmos.drain}}}\n"
        p_value += 1
        output += f"C {{lab_pin.sym}} {nmos_pos.x + 70} {nmos_pos.y - 80} 2 0 {{name=p{p_value} sig_type=std_logic lab={gate.nmos.source}}}\n"
        p_value += 1

        # Create wires
        input_wire = Wire(
            start_x=nmos_pos.x - 30,
            start_y=nmos_pos.y - 80,
            end_x=nmos_pos.x - 70,
            end_y=nmos_pos.y - 80,
            label=gate.pmos.drain,
        )
        output += input_wire.to_xschem()
        input_wire = Wire(
            start_x=nmos_pos.x + 30,
            start_y=nmos_pos.y - 80,
            end_x=nmos_pos.x + 70,
            end_y=nmos_pos.y - 80,
            label=gate.pmos.drain,
        )
        output += input_wire.to_xschem()
        input_wire = Wire(
            start_x=nmos_pos.x + 30,
            start_y=nmos_pos.y - 140,
            end_x=nmos_pos.x + 30,
            end_y=nmos_pos.y - 20,
            label=gate.pmos.drain,
        )
        output += input_wire.to_xschem()
        input_wire = Wire(
            start_x=nmos_pos.x - 30,
            start_y=nmos_pos.y - 140,
            end_x=nmos_pos.x - 30,
            end_y=nmos_pos.y - 20,
            label=gate.pmos.drain,
        )
        output += input_wire.to_xschem()

        # Update x position for next inverter
        current_x += constants.spacing * 3

    return output


# def create_parallel_transistors(groups: List[TransistorGroup], origin: Point) -> str:
#     output = ""
#     current_x = origin.x

#     for group in groups:
#         # Store the positions of transistors in this group for wire connections
#         transistor_positions: List[Point] = []

#         # Create transistors in the group
#         for index, item in enumerate(group.transistors):
#             pos = Point(current_x + (index * constants.spacing), origin.y)
#             transistor_positions.append(pos)
#             output += create_single_transistor(item, pos)

#         # Create wires
#         if len(transistor_positions) > 1:
#             first_trans = group.transistors[0]

#             source_wire = Wire(
#                 start_x=transistor_positions[0].x + 20,
#                 start_y=origin.y - 30,
#                 end_x=transistor_positions[-1].x + 20,
#                 end_y=origin.y - 30,
#                 label=first_trans.source
#             )
#             output += source_wire.to_xschem()

#             drain_wire = Wire(
#                 start_x=transistor_positions[0].x + 20,
#                 start_y=origin.y + 30,
#                 end_x=transistor_positions[-1].x + 20,
#                 end_y=origin.y + 30,
#                 label=first_trans.drain
#             )
#             output += drain_wire.to_xschem()

#         # Update x position for next group
#         current_x += (len(group.transistors) *
#                       constants.spacing) + constants.spacing

#     return output


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

        # create list of transistors
        transistors = create_transistor_objects(spice_file)

        # sort
        # parallel_transistors = find_parallel_transistors(transistors)

        # group extras into pmos/nmos
        extra_pmos_transistors = TransistorGroup([])
        extra_nmos_transistors = TransistorGroup([])
        for item in transistors:
            if item.is_pmos:
                extra_pmos_transistors.transistors.append(item)
            else:
                extra_nmos_transistors.transistors.append(item)

        inverters = find_inverters(
            extra_pmos_transistors.transistors, extra_nmos_transistors.transistors
        )
        transmission_gates = find_transmission_gates(
            extra_pmos_transistors.transistors, extra_nmos_transistors.transistors
        )
        # draw transistors
        sch_output += create_inverters(inverters, constants.inverter_origin)
        sch_output += create_transmission_gates(
            transmission_gates, constants.transmission_gate_origin
        )
        # sch_output += create_parallel_transistors(
        #     parallel_transistors, constants.parallel_origin)
        sch_output += create_xschem_transistor_row(
            extra_pmos_transistors.transistors, constants.pmos_extra_origin
        )
        sch_output += create_xschem_transistor_row(
            extra_nmos_transistors.transistors, constants.nmos_extra_origin
        )

        if args.output_file:
            with open(args.output_file, "w") as outfile:
                outfile.write(sch_output)
        else:
            print(sch_output)
