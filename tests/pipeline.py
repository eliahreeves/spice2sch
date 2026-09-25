from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent / "scripts"


class PipelineError(RuntimeError):
    """Raised when a step fails outright (nonzero exit), as opposed to a
    clean LVS mismatch, which is reported as a normal test failure."""


def generate_schematic(reference_spice: Path, out_sch: Path) -> None:
    result = subprocess.run(
        ["uv", "run", "spice2sch", "-i", str(reference_spice), "-o", str(out_sch)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PipelineError(f"spice2sch failed:\n{result.stderr}")


def netlist_schematic(workdir: Path, cell_name: str, pdk_root: Path) -> Path:
    log_path = workdir / "logs" / f"{cell_name}.spice.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "xschem",
            "--no_x",
            "--log",
            str(log_path),
            "--script",
            str(SCRIPTS_DIR / "xschem_generate_netlist.tcl"),
        ],
        cwd=workdir,
        env={
            **_passthrough_env(pdk_root),
            "SCHEMATIC": cell_name,
            "PWD": str(workdir),
        },
        capture_output=True,
        text=True,
    )
    netlist_path = workdir / "netlists" / f"{cell_name}.spice"
    if result.returncode != 0 or not netlist_path.exists():
        raise PipelineError(
            f"xschem netlisting failed for {cell_name}:\n"
            f"{result.stdout}\n{result.stderr}\n"
            f"(see {log_path})"
        )
    return netlist_path


@dataclass
class LvsResult:
    passed: bool
    report_text: str
    report_path: Path


def run_lvs(
    reference_spice: Path,
    generated_netlist: Path,
    cell_name: str,
    report_path: Path,
    pdk_root: Path,
) -> LvsResult:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)

    result = subprocess.run(
        ["netgen", "-batch", "source", str(SCRIPTS_DIR / "netgen_lvs.tcl")],
        env={
            **_passthrough_env(pdk_root),
            "REFERENCE_SPICE_FILE": str(reference_spice),
            "REFERENCE_CELL_NAME": cell_name,
            "XSCHEM_SPICE_FILE": str(generated_netlist),
            "XSCHEM_CELL_NAME": cell_name,
            "REPORT_FILE": str(report_path),
        },
        capture_output=True,
        text=True,
    )
    report_text = report_path.read_text() if report_path.exists() else result.stdout

    if result.returncode != 0 and not report_path.exists():
        raise PipelineError(f"netgen failed to run:\n{result.stderr}")

    passed = bool(re.search(r"Circuits match uniquely", report_text))
    return LvsResult(passed=passed, report_text=report_text, report_path=report_path)


def prepare_workdir(tmp_path: Path, repo_root: Path) -> None:
    shutil.copy(repo_root / "xschemrc", tmp_path / "xschemrc")


def _passthrough_env(pdk_root: Path) -> dict[str, str]:
    import os

    return {**os.environ, "PDK_ROOT": str(pdk_root)}
