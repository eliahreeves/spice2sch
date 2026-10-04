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


def generate_schematic(
    reference_spice: Path, out_sch: Path, pdk_root: Path | None = None
) -> None:
    import os

    env = os.environ.copy()
    if pdk_root is not None:
        env["PDK_ROOT"] = str(pdk_root)

    result = subprocess.run(
        ["uv", "run", "spice2sch", "-i", str(reference_spice), "-o", str(out_sch)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise PipelineError(f"spice2sch failed:\n{result.stdout}\n{result.stderr}")


def netlist_schematic(
    workdir: Path, cell_name: str, pdk_root: Path, pdk: str = "sky130A"
) -> Path:
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
            **_passthrough_env(pdk_root, pdk),
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


def generate_svg(
    workdir: Path, cell_name: str, pdk_root: Path, pdk: str = "sky130A"
) -> Path:
    """Export an SVG for a schematic via xschem, matching sky130_schematics."""
    log_path = workdir / "logs" / f"{cell_name}.svg.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path = workdir / "svg" / f"{cell_name}.svg"
    svg_path.parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "xschem",
            "--no_x",
            "--log",
            str(log_path),
            "--script",
            str(SCRIPTS_DIR / "generate_svg.tcl"),
        ],
        cwd=workdir,
        env={
            **_passthrough_env(pdk_root, pdk),
            "SCHEMATIC": cell_name,
            "PWD": str(workdir),
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not svg_path.exists():
        raise PipelineError(
            f"xschem SVG export failed for {cell_name}:\n"
            f"{result.stdout}\n{result.stderr}\n"
            f"(see {log_path})"
        )
    return svg_path


def cdl_to_spice(text: str) -> str:
    """Rewrite CDL-only syntax that netgen's SPICE reader misreads.

    Netgen drops a diode's positional area/perimeter and keeps "$m" as a
    literal property name, so the reference would carry no comparable
    properties. Deliberately independent of spice2sch's own parser.
    """
    lines = []
    for line in text.splitlines():
        tokens = line.split()
        if tokens and tokens[0][0] in "Dd" and len(tokens) > 4:
            named = [t for t in tokens[4:] if "=" in t]
            positional = [t for t in tokens[4:] if "=" not in t]
            tokens = tokens[:4] + [
                f"{name}={value}" for name, value in zip(("area", "pj"), positional)
            ] + named
        line = " ".join(re.sub(r"^\$(\w+=)", r"\1", t) for t in tokens) or line
        lines.append(line)
    return "\n".join(lines) + "\n"


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
    pdk: str = "sky130A",
) -> LvsResult:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)

    netgen_reference = reference_spice
    if reference_spice.suffix.lower() == ".cdl":
        netgen_reference = report_path.parent / f"{cell_name}.reference.spice"
        netgen_reference.write_text(cdl_to_spice(reference_spice.read_text()))

    result = subprocess.run(
        ["netgen", "-batch", "source", str(SCRIPTS_DIR / "netgen_lvs.tcl")],
        env={
            **_passthrough_env(pdk_root, pdk),
            "REFERENCE_SPICE_FILE": str(netgen_reference),
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

    passed = bool(
        re.search(r"Circuits match uniquely", report_text)
    ) or _is_matching_empty_circuit(reference_spice, generated_netlist, report_text)
    return LvsResult(passed=passed, report_text=report_text, report_path=report_path)


def _spice_has_devices(path: Path) -> bool:
    """Return True if a SPICE file has any device/instance lines inside a subckt."""
    in_subckt = False
    for raw in path.read_text().splitlines():
        line = raw.split("*", 1)[0].strip()
        if not line:
            continue
        lower = line.lower()
        if lower.startswith(".subckt"):
            in_subckt = True
            continue
        if lower.startswith(".ends"):
            in_subckt = False
            continue
        if in_subckt and not lower.startswith("."):
            return True
    return False


def _is_matching_empty_circuit(
    reference_spice: Path, generated_netlist: Path, report_text: str
) -> bool:
    """Pass only when *both* netlists are empty and pins still match.

    Netgen reports empty generated cells as "Not checked" even when the
    reference has devices, so trusting that message alone lets blank
    schematics slip through.
    """
    if _spice_has_devices(reference_spice) or _spice_has_devices(generated_netlist):
        return False
    if not re.search(r"has no elements and/or nodes\.\s*Not checked\.", report_text):
        return False
    return (
        "Cell pin lists are equivalent." in report_text
        and "**Mismatch**" not in report_text
    )


def prepare_workdir(tmp_path: Path, repo_root: Path) -> None:
    shutil.copy(repo_root / "xschemrc", tmp_path / "xschemrc")


def _passthrough_env(pdk_root: Path, pdk: str) -> dict[str, str]:
    import os

    return {**os.environ, "PDK_ROOT": str(pdk_root), "PDK": pdk}
