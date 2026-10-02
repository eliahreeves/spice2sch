from __future__ import annotations

from pathlib import Path

import pytest

from spice2sch import main as main_module
from spice2sch.cli_def import create_parser
from spice2sch.spice import Spice


def test_cli_output_file_defaults_to_stdout_mode(tmp_path: Path) -> None:
    spice_path = tmp_path / "cell.spice"
    spice_path.write_text(".subckt inv a y vdd vss\n.ends\n")
    args = create_parser().parse_args(["-i", str(spice_path)])
    assert args.output_file is None


def test_spice_leading_plus_reports_value_error() -> None:
    with pytest.raises(ValueError, match="Unexpected \\+ at beginning of file"):
        Spice("+ x1 a b sky130_fd_pr__res_generic_po\n.subckt inv a y vdd vss\n.ends\n")


def test_main_writes_to_stdout_without_output_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    spice_path = tmp_path / "cell.spice"
    spice_path.write_text(".subckt inv a vdd y vss\n.ends\n")
    monkeypatch.setattr("sys.argv", ["spice2sch", "-i", str(spice_path)])
    main_module.main()
    out = capsys.readouterr().out
    assert "xschem version" in out
