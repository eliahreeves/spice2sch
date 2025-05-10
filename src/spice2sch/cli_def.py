from argparse import ArgumentParser, FileType
from importlib.metadata import version, PackageNotFoundError
from typing import NoReturn
import sys


def get_version():
    try:
        return version("spice2sch")
    except PackageNotFoundError:
        return "Unknown (not installed as a package)"


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Convert SkyWater SKY130 spice files into xschem .sch files."
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=get_version(),
        help="Show version and exit",
    )
    parser.add_argument(
        "-i",
        "--input-file",
        type=FileType("r"),
        default=sys.stdin if not sys.stdin.isatty() else None,
        required=False,
        help="Input file to read from",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        type=str,
        default=sys.stdout,
        required=False,
        help="Output file to write to",
    )
    parser.add_argument(
        "-p",
        "--pdk",
        type=str,
        choices=["sky130", "gf180", "infer"],
        default="infer",
        required=False,
        help="PDK to use (sky130, gf180, or infer)",
    )

    def error_and_exit(message: str) -> NoReturn:
        print(f"Error: {message}\n", file=sys.stderr)
        parser.print_help()
        sys.exit(2)

    parser.error = error_and_exit

    return parser
