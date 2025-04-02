from dataclasses import dataclass
from typing import List
from spice2sch.spice import SubcktCall

@dataclass
class Point:
    x: int
    y: int

    def __add__(self, other: "Point") -> "Point":
        return Point(self.x + other.x, self.y + other.y)


@dataclass
class Wire:
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    label: str

    def to_xschem(self) -> str:
        return f"N {self.start_x} {self.start_y} {self.end_x} {self.end_y} {{lab={self.label}}}\n"


class Transistor:
    def __init__(
        self,
        params: List[str],
        library: str,
        name: str,
        symbol_name: str,
        body: str,
        drain: str,
        gate: str,
        source: str,
        id: int,
    ):
        self.params = params
        self.library = library
        self.name = name
        self.symbol_name = symbol_name
        self.body = body
        self.drain = drain
        self.gate = gate
        self.source = source
        self.id = id

    @classmethod
    def from_subckt_call(cls, subckt_call: SubcktCall, index: int):
        library_name = subckt_call.subckt_ref.split("__")

        full_name = library_name[1]
        symbol_name = full_name

        if "special_" in symbol_name:
            symbol_name = symbol_name.replace("special_", "")

        if "fet" not in symbol_name:
            return None

        transistor = cls(
            params=subckt_call.params,
            library=library_name[0],
            name=full_name,
            symbol_name=symbol_name,
            body=subckt_call.nodes[3],
            drain=subckt_call.nodes[2],
            gate=subckt_call.nodes[1],
            source=subckt_call.nodes[0],
            id=index,
        )

        transistor.normalize()
        return transistor

    def normalize(self):
        if self.source > self.drain:
            self.source, self.drain = self.drain, self.source

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


class Inverter(TransistorGroup):
    def __init__(self, pmos: Transistor, nmos: Transistor):
        super().__init__([pmos, nmos])

    @property
    def nmos(self) -> Transistor:
        return self.transistors[1]

    @property
    def pmos(self) -> Transistor:
        return self.transistors[0]


class TransmissionGate(TransistorGroup):
    def __init__(self, pmos: Transistor, nmos: Transistor):
        super().__init__([pmos, nmos])

    @property
    def nmos(self) -> Transistor:
        return self.transistors[1]

    @property
    def pmos(self) -> Transistor:
        return self.transistors[0]
