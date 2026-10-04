from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
import pytest


@dataclass(frozen=True)
class CellLibrary:
    """A cloned standard-cell repo to round-trip, and the PDK it targets."""

    env_var: str
    pdk: str
    netlist_glob: str


@dataclass(frozen=True)
class ReferenceCell:
    path: Path
    pdk: str


CELL_LIBRARIES = (
    CellLibrary(env_var="SKY130_CELLS", pdk="sky130A", netlist_glob="*.spice"),
    CellLibrary(env_var="GF180_CELLS", pdk="gf180mcuD", netlist_glob="*.cdl"),
)


def _env_path(var: str) -> Path:
    value = os.environ.get(var)
    if not value:
        pytest.skip(f"${var} is not set")
    return Path(value)


@pytest.fixture(scope="session")
def cells_dir() -> Path:
    root = _env_path("SKY130_CELLS")
    cells = root / "cells"
    if not cells.is_dir():
        pytest.fail(f"SKY130_CELLS={root} has no 'cells' subdirectory")
    return cells


@pytest.fixture(scope="session")
def pdk_root() -> Path:
    return _env_path("PDK_ROOT")


@pytest.fixture(scope="session")
def scripts_dir() -> Path:
    return Path(__file__).parent / "lvs" / "scripts"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--cell-limit",
        type=int,
        default=None,
        help="only collect the first N cells of each library, for a quick smoke run",
    )


def _explain_skip(metafunc: pytest.Metafunc, reason: str) -> None:
    metafunc.parametrize(
        "reference_cell",
        [pytest.param(None, marks=pytest.mark.skip(reason=reason))],
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "reference_cell" not in metafunc.fixturenames:
        return

    limit = metafunc.config.getoption("--cell-limit")
    cells: list[ReferenceCell] = []
    reasons: list[str] = []
    for library in CELL_LIBRARIES:
        root = os.environ.get(library.env_var)
        if not root:
            reasons.append(f"${library.env_var} is not set")
            continue

        paths = sorted(Path(root, "cells").glob(f"*/{library.netlist_glob}"))
        if limit:
            paths = paths[:limit]
        if not paths:
            reasons.append(
                f"${library.env_var}={root} has no "
                f"cells/*/{library.netlist_glob} files to collect"
            )
        cells.extend(ReferenceCell(path=path, pdk=library.pdk) for path in paths)

    if not cells:
        _explain_skip(
            metafunc,
            "; ".join(reasons) + ". Run via `make test` (clones the cell libs "
            "and sets " + "/".join(lib.env_var for lib in CELL_LIBRARIES) + ").",
        )
        return

    metafunc.parametrize(
        "reference_cell",
        cells,
        ids=[cell.path.stem for cell in cells],
    )
