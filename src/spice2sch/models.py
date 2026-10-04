import math
from dataclasses import dataclass
from typing import Dict, List, Mapping

from spice2sch.spice import SubcktCall, parse_number
from spice2sch.symbols import SymbolDef


def _adapt_params(params: Dict[str, str], symbol: SymbolDef) -> Dict[str, str]:
    """Translate netlist params into the ones `symbol` actually formats.

    gf180mcu diode symbols take a rectangle (``r_w``/``r_l``) and derive
    ``area``/``pj`` from it, while CDL netlists give ``area``/``pj``
    directly, so solve for the rectangle with that area and perimeter.
    """
    attrs = symbol.template_attr_names
    lowered = {name.lower(): name for name in params}
    if not (
        {"area", "pj"} <= lowered.keys()
        and {"r_w", "r_l"} <= attrs.keys()
        and "area" not in attrs
    ):
        return params

    area = parse_number(params[lowered["area"]])
    half_perimeter = parse_number(params[lowered["pj"]]) / 2
    discriminant = half_perimeter**2 - 4 * area
    if area <= 0 or discriminant < 0:
        # No rectangle fits; keep the area, which is what LVS compares.
        width = math.sqrt(max(area, 0.0))
    else:
        width = (half_perimeter + math.sqrt(discriminant)) / 2
    length = area / width if width else 0.0

    adapted = {
        name: value
        for name, value in params.items()
        if name.lower() not in ("area", "pj")
    }
    adapted[attrs["r_w"]] = f"{width:.6g}"
    adapted[attrs["r_l"]] = f"{length:.6g}"
    return adapted


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
    params: Mapping[str, str]
    library: str
    model: str
    symbol: SymbolDef

    @property
    def size(self) -> Point:
        """Width/height of the resolved symbol, from its drawing bounding box."""
        width, height = self.symbol.bbox.size
        return Point(int(round(width)), int(round(height)))

    @classmethod
    def from_subckt_call(
        cls, subckt_call: SubcktCall, index: int, symbol: SymbolDef
    ) -> "Primitive":
        parts = subckt_call.subckt_ref.split("__", 1)
        if len(parts) == 2:
            library, model = parts
        else:
            library, model = symbol.library, subckt_call.subckt_ref
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
            params=_adapt_params(dict(subckt_call.params), symbol),
            library=library,
            model=model,
            symbol=symbol,
        )
