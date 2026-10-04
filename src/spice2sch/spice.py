import re
from typing import List, Set, Tuple

# Supply nets, drawn at the top of a CMOS stack. Shared with I/O
# classification below and with chain placement in `placeable.py`.
POWER_NETS = {
    "VDD",
    "VCC",
    "VPWR",
    "VPWRIN",
    "LOWLVPWR",
    "KAPWR",
    "VPB",
    "VNW",
}

# Ground nets, drawn at the bottom of a CMOS stack.
GROUND_NETS = {
    "VSS",
    "GND",
    "VGND",
    "VNB",
    "VPW",
}

# Rails are never internal series junctions, even when some local subset of
# a design happens to touch one with exactly two diffusion terminals.
POWER_GROUND_NETS = POWER_NETS | GROUND_NETS

# Primitive (non-X) device lines have a fixed terminal count before the
# model name, as written by CDL netlists such as gf180mcu's.
_PRIMITIVE_NODE_COUNTS = {"M": 4, "D": 2}

# Names for unlabeled values after the model, in SPICE positional order.
_POSITIONAL_PARAMS = {"D": ("area", "pj")}

_SI_SCALE = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,
    "u": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
    "a": 1e-18,
}
_NUMBER_RE = re.compile(
    r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)(meg|[tgkmunpfa])?[a-z]*$",
    re.IGNORECASE,
)


def parse_number(text: str) -> float:
    """Parse a SPICE number with an optional scale suffix, e.g. ``0.2052p``."""
    match = _NUMBER_RE.match(text.strip())
    if not match:
        raise ValueError(f"Not a SPICE number: {text!r}")
    value = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    return value * _SI_SCALE.get(suffix, 1.0)


class SubcktCall:
    name: str
    nodes: List[str]
    subckt_ref: str
    params: List[Tuple[str, str]]

    def __init__(self, call_str: str):
        # CDL may separate an X call's nodes from its subckt name with "/".
        tokens = [token for token in call_str.split() if token != "/"]
        if not tokens:
            raise ValueError("Input string is empty")

        self.name = tokens[0]
        prefix = self.name[0].upper()

        if prefix == "X":
            ref_index = len(tokens) - 1
            while "=" in tokens[ref_index]:
                ref_index -= 1
        elif prefix in _PRIMITIVE_NODE_COUNTS:
            ref_index = 1 + _PRIMITIVE_NODE_COUNTS[prefix]
            if ref_index >= len(tokens):
                raise ValueError(f"{self.name}: missing model name")
        else:
            raise ValueError(f"Unsupported device line: {self.name}")

        self.nodes = tokens[1:ref_index]
        self.subckt_ref = tokens[ref_index]

        positional = [token for token in tokens[ref_index + 1 :] if "=" not in token]
        params: List[Tuple[str, str]] = list(
            zip(_POSITIONAL_PARAMS.get(prefix, ()), positional)
        )
        for token in tokens[ref_index + 1 :]:
            if "=" not in token:
                continue
            name, value = token.split("=", 1)
            # CDL marks netlister-only params with "$" (e.g. "$m=1").
            params.append((name.lstrip("$"), value))
        self.params = params


class Spice:
    content: List[str]

    def __init__(self, spice_input):
        self.content = spice_input.split("\n")
        self.__remove_comments()
        self.__append_plus()
        self.__reduce_to_subckt_definition()

    @property
    def name(self) -> str:
        tokens = self.content[0].split()
        if len(tokens) < 2:
            raise ValueError("Invalid format")
        return tokens[1]

    @property
    def ports(self) -> List[str]:
        tokens = self.content[0].split()
        if len(tokens) < 3:
            raise ValueError("Invalid format")
        return tokens[2:]

    def extract_subckt_calls(self) -> List[SubcktCall]:
        return [
            SubcktCall(subckt_call)
            for subckt_call in self.content[1:-1]
            if subckt_call.strip()
        ]

    def extract_io(self) -> Tuple[List[str], List[str]]:
        ports = self.ports

        first_rail = next(
            (i for i, port in enumerate(ports) if port in POWER_GROUND_NETS), None
        )
        has_signal_after_rail = first_rail is not None and any(
            port not in POWER_GROUND_NETS for port in ports[first_rail:]
        )
        # sky130 lists inputs, rails, then outputs. Netlists that put every
        # rail last (gf180mcu CDL) carry no order hint, so infer outputs from
        # connectivity instead.
        if not has_signal_after_rail:
            return self.__extract_io_by_connectivity(ports)

        inputs: List[str] = []
        outputs: List[str] = []
        found_inputs = False

        for port in ports:
            is_port_power_ground = port in POWER_GROUND_NETS
            if is_port_power_ground:
                found_inputs = True

            if not found_inputs or is_port_power_ground:
                inputs.append(port)
            else:
                outputs.append(port)
        return (inputs, outputs)

    def __extract_io_by_connectivity(
        self, ports: List[str]
    ) -> Tuple[List[str], List[str]]:
        """A signal port is an output when it touches a FET's drain/source
        but never a gate; everything else (including rails) is an input."""
        diffusion: Set[str] = set()
        gates: Set[str] = set()
        for call in self.extract_subckt_calls():
            if call.name[0].upper() != "M":
                continue
            drain, gate, source = call.nodes[:3]
            diffusion.update((drain, source))
            gates.add(gate)

        inputs: List[str] = []
        outputs: List[str] = []
        for port in ports:
            is_output = (
                port not in POWER_GROUND_NETS
                and port in diffusion
                and port not in gates
            )
            (outputs if is_output else inputs).append(port)
        return (inputs, outputs)

    def __reduce_to_subckt_definition(self):
        start = 0
        for index, line in enumerate(self.content):
            line = line.strip()
            if line.lower().startswith(".subckt"):
                start = index
            elif line.lower().startswith(".ends"):
                self.content = self.content[start : index + 1]
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
                    raise ValueError("Unexpected + at beginning of file")
                new_content[-1] += f" {strip_line[1:].lstrip()}"
            else:
                new_content.append(line)
        self.content = new_content
