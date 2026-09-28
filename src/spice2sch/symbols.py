from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_PIN_RE = re.compile(
    r"^B 5 (?P<x1>[-\d.]+) (?P<y1>[-\d.]+) (?P<x2>[-\d.]+) (?P<y2>[-\d.]+) \{(?P<attrs>.*)\}"
)
_ATTR_RE = re.compile(r"(\w+)=([^\s}]+)")
_TYPE_RE = re.compile(r"\btype=(\S+)")
_TEMPLATE_MODEL_RE = re.compile(r"\bmodel=(\S+)")


@dataclass(frozen=True)
class SymbolPin:
    name: str
    x: float
    y: float
    pinnumber: int | None
    index: int


@dataclass(frozen=True)
class SymbolDef:
    path: Path
    library: str
    stem: str
    pins: tuple[SymbolPin, ...]
    device_type: str | None
    template_model: str | None
    template_attr_names: dict[str, str]

    @property
    def sch_path(self) -> str:
        return f"{self.library}/{self.stem}.sym"

    def normalize_param_name(self, name: str) -> str:
        return self.template_attr_names.get(name.lower(), name)


def _parse_pins(text: str) -> tuple[SymbolPin, ...]:
    pins: list[SymbolPin] = []
    for index, line in enumerate(text.splitlines()):
        match = _PIN_RE.match(line.strip())
        if not match:
            continue
        attrs = dict(_ATTR_RE.findall(match.group("attrs")))
        name = attrs.get("name")
        if not name:
            continue
        x1, y1, x2, y2 = (
            float(match.group("x1")),
            float(match.group("y1")),
            float(match.group("x2")),
            float(match.group("y2")),
        )
        pinnumber = int(attrs["pinnumber"]) if "pinnumber" in attrs else None
        pins.append(
            SymbolPin(
                name=name,
                x=(x1 + x2) / 2,
                y=(y1 + y2) / 2,
                pinnumber=pinnumber,
                index=index,
            )
        )

    # When every pin has pinnumber, honor it (resistors). Otherwise keep file
    # order so partially-numbered symbols (diodes) match xschem @pinlist.
    if pins and all(pin.pinnumber is not None for pin in pins):
        pins.sort(key=lambda pin: (pin.pinnumber, pin.index))
    else:
        pins.sort(key=lambda pin: pin.index)
    return tuple(pins)


def _parse_symbol(path: Path) -> SymbolDef:
    text = path.read_text(errors="ignore")
    library = path.parent.name
    device_type_match = _TYPE_RE.search(text)
    template_model_match = _TEMPLATE_MODEL_RE.search(text)
    template_model = template_model_match.group(1) if template_model_match else None
    library_prefix = f"{library}__"
    if template_model and template_model.startswith(library_prefix):
        template_model = template_model.removeprefix(library_prefix)

    template_match = re.search(r'template="(.*?)"', text, re.S)
    template_attrs: dict[str, str] = {}
    if template_match:
        for key, value in _ATTR_RE.findall(template_match.group(1)):
            template_attrs[key.lower()] = key

    return SymbolDef(
        path=path,
        library=library,
        stem=path.stem,
        pins=_parse_pins(text),
        device_type=device_type_match.group(1) if device_type_match else None,
        template_model=template_model,
        template_attr_names=template_attrs,
    )


def _models_provided(path: Path, symbol: SymbolDef) -> set[str]:
    text = path.read_text(errors="ignore")
    models = {symbol.stem}
    if symbol.template_model:
        models.add(symbol.template_model)
    full_model_re = re.compile(rf"{re.escape(symbol.library)}__(\S+)")
    models.update(full_model_re.findall(text))
    return models


def _prefer_new(existing: SymbolDef, new: SymbolDef, model: str) -> bool:
    if existing.stem == model:
        return False
    if new.stem == model:
        return True
    if existing.stem.startswith("lvs") and not new.stem.startswith("lvs"):
        return True
    return False


class SymbolIndex:
    """Lookup xschem symbols for SPICE subckt models under a PDK root.

    Works with any PDK laid out the open_pdks way, i.e. one or more
    ``<variant>/libs.tech/xschem/<library>/*.sym`` directories, and SPICE
    subckt refs of the form ``<library>__<model>``.
    """

    def __init__(self, pdk_root: Path):
        self.pdk_root = Path(pdk_root)
        self._by_key: dict[tuple[str, str], SymbolDef] = {}
        self._load()

    @property
    def library_dirs(self) -> list[Path]:
        return sorted(
            path for path in self.pdk_root.glob("*/libs.tech/xschem/*") if path.is_dir()
        )

    def _load(self) -> None:
        library_dirs = self.library_dirs
        if not library_dirs:
            raise FileNotFoundError(
                "No xschem symbol libraries found under PDK root "
                f"{self.pdk_root} (expected "
                "<variant>/libs.tech/xschem/<library>/*.sym)"
            )

        for library_dir in library_dirs:
            library = library_dir.name
            for path in sorted(library_dir.glob("*.sym")):
                symbol = _parse_symbol(path)
                for model in _models_provided(path, symbol):
                    key = (library, model)
                    existing = self._by_key.get(key)
                    if existing is None or _prefer_new(existing, symbol, model):
                        self._by_key[key] = symbol

    def resolve(self, subckt_ref: str) -> SymbolDef | None:
        library, sep, model = subckt_ref.partition("__")
        if not sep:
            return None
        symbol = self._by_key.get((library, model))
        if symbol is not None:
            return symbol

        # Some PDKs (e.g. sky130) ship device variants such as
        # "special_nfet_01v8" that reuse the exact pinout of a base
        # device ("nfet_01v8") but are only ever expressed as a SPICE
        # model override -- no dedicated xschem symbol declares the
        # "special_" name. Fall back to the base device's symbol so the
        # generated schematic still carries the full ("special_...")
        # model name into the netlist, keeping LVS device classes intact.
        if model.startswith("special_"):
            return self._by_key.get((library, model[len("special_") :]))

        return None
