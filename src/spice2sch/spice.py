from typing import List, Tuple

class SubcktCall:
    name: str
    nodes: List[str]
    component: str
    params: List[str]

    def __init__(self, call_str: str):
        tokens = call_str.split()
        if not tokens:
            raise ValueError("Input string is empty")

        self.name = tokens[0]
        # This conditional causes errors with gf180 and it seems like it doesn't provide useful error checking
        # if not self.name.startswith("x") and not self.name.startswith("X"):
        #     raise ValueError("Subckt call must begin with X")

        # this loop iterates backwards through the list
        param_index = len(tokens)-1

        while "=" in tokens[param_index]:
            param_index -= 1

        self.nodes = tokens[1:param_index]
        self.component = tokens[param_index]
        self.params = tokens[param_index+1:]


class Spice:
    pdk: str
    content: List[str]
    def __init__(self, spice_input: str, pdk: str):
        self.content = spice_input.split("\n")
        self.__remove_comments()
        self.__append_plus()
        self.__reduce_to_subckt_definition()
        if pdk == "infer":
            if 'gf180' in self.content[0]:
                self.pdk = 'gf180'
            elif 'sky130' in self.content[0]:
                self.pdk = 'sky130'
            else:
                raise ValueError("Could not infer PDK")
        else:
            self.pdk = pdk



    def extract_subckt_calls(self) -> List[SubcktCall]:
        return [SubcktCall(subckt_call) for subckt_call in self.content[1:-1]]

    def extract_io(self) -> Tuple[List[str], List[str]]:
        subckt_line = self.content[0]

        tokens = subckt_line.split()
        if len(tokens) < 3:
            raise ValueError("Invalid format")

        ports = tokens[2:]

        power_ground = {"VDD", "VCC", "VSS", "GND", "VGND", "VPWR", "VNB", "VPB", "VPWRIN", "LOWLVPWR", "VNW", "VPW"}

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


    def __reduce_to_subckt_definition(self):
        start = 0
        for index, line in enumerate(self.content):
            line = line.strip()
            if line.lower().startswith(".subckt"):
                start = index
            elif line.lower().startswith(".ends"):
                self.content = self.content[start: index + 1]
                return
        raise ValueError("Invalid format")


    def __remove_comments(self):
        new_content = []
        for line in self.content:
            if not line.startswith("*"):
                new_content.append(line)
        self.content = new_content


    def __append_plus(self):
        new_content = []
        for line in self.content:
            strip_line = line.lstrip()
            if strip_line.startswith("+"):
                if not new_content:
                    ValueError("Unexpected + at beginning of file")
                new_content[-1] += f" {strip_line[1:].lstrip()}"
            else:
                new_content.append(line)
        self.content = new_content
