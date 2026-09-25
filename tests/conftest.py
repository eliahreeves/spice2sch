from __future__ import annotations
import os
from pathlib import Path
import pytest


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
        help="only collect the first N cells, for a quick smoke run",
    )


def _explain_skip(metafunc: pytest.Metafunc, reason: str) -> None:
    metafunc.parametrize(
        "reference_spice",
        [pytest.param(None, marks=pytest.mark.skip(reason=reason))],
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "reference_spice" not in metafunc.fixturenames:
        return

    root = os.environ.get("SKY130_CELLS")
    if not root:
        _explain_skip(
            metafunc,
            "$SKY130_CELLS is not set; no reference cells to collect. "
            "Run via `make test` (clones the cell lib and sets SKY130_CELLS).",
        )
        return

    cells = sorted(Path(root, "cells").glob("*/*.spice"))

    limit = metafunc.config.getoption("--cell-limit")
    if limit:
        cells = cells[:limit]

    if not cells:
        _explain_skip(
            metafunc,
            f"$SKY130_CELLS={root} has no cells/*/*.spice files to collect",
        )
        return

    metafunc.parametrize(
        "reference_spice",
        cells,
        ids=[p.stem for p in cells],
    )
