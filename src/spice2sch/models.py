from dataclasses import dataclass
from typing import List

from spice2sch.spice import SubcktCall
from spice2sch.symbols import SymbolDef


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


@dataclass
class Primitive:
    """A SPICE subckt instance placed via PDK symbol lookup.

    Represents any device (FET or otherwise) resolved to an xschem symbol
    through a `SymbolIndex`, independent of PDK or device type.
    """

    id: int
    instance_name: str
    nodes: List[str]
    params: List[str]
    library: str
    model: str
    symbol: SymbolDef

    @classmethod
    def from_subckt_call(
        cls, subckt_call: SubcktCall, index: int, symbol: SymbolDef
    ) -> "Primitive":
        parts = subckt_call.subckt_ref.split("__", 1)
        library = parts[0]
        model = parts[1] if len(parts) == 2 else subckt_call.subckt_ref
        instance_name = subckt_call.name
        if instance_name[:1] in ("X", "x") and len(instance_name) > 1:
            instance_name = instance_name[1:]

        if len(subckt_call.nodes) != len(symbol.pins):
            raise ValueError(
                f"{subckt_call.subckt_ref}: spice has {len(subckt_call.nodes)} nodes "
                f"but symbol {symbol.sch_path} has {len(symbol.pins)} pins"
            )

        return cls(
            id=index,
            instance_name=instance_name,
            nodes=list(subckt_call.nodes),
            params=list(subckt_call.params),
            library=library,
            model=model,
            symbol=symbol,
        )
