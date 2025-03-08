from dataclasses import dataclass
from typing import List


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
