# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Command-line entry point.

``dflash_to_eee.py <dflash> <eeprom>`` stays the historical FRM3 invocation:
same exit behaviour, same log lines, same bytes. ``--profile`` selects any
other module and prints the structured checks as well.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dflash_eee import __version__
from dflash_eee.compare import compare_bytes, format_cli_report, write_report
from dflash_eee.converter import DFlashConverter, convert_bytes, render_info
from dflash_eee.profiles import ProfileError, default_extra_dirs, discover_profiles, get_profile
from dflash_eee.report import report_lines, size_issue


def configure_logging() -> None:
    """Log bytes the way the Python 2 tool did, including 0xFF in a VIN."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="latin-1", errors="strict")
            except (OSError, ValueError):
                pass
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _print_profiles(profiles) -> None:
    print("dflash-to-eeprom %s" % __version__)
    print("")
    for profile in profiles:
        print("%-20s %-14s %s" % (profile.id, profile.status_label, profile.title))
        print("  MCU: %s" % profile.mcu)
        if profile.conversion_enabled:
            print("  D-Flash %d bytes -> EEE %d bytes" % (profile.dflash_size, profile.eee_size))
        else:
            print("  Conversion disabled")
        if profile.assumptions:
            print("  Note: %s" % profile.assumptions)
        print("")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dflash_to_eee",
        description=(
            "Rebuild an S12XE emulated EEPROM image from a D-Flash dump. "
            "compare FILE_A FILE_B lists the bytes that differ."
        ),
    )
    parser.add_argument("--profile", default="bmw_frm3", help="Module profile id (default: bmw_frm3)")
    parser.add_argument("--list-profiles", action="store_true", help="List built-in and drop-in profiles")
    parser.add_argument("--strict", action="store_true",
                        help="Exit with status 2 when the dump size, layout or records look wrong")
    parser.add_argument("--version", action="store_true", help="Print the version and exit")
    parser.add_argument("dflash", nargs="?", help="D-Flash dump")
    parser.add_argument("eeprom", nargs="?", help="EEPROM image to write")
    return parser


def _write_out(text: str) -> None:
    """Print compare output even when stdout was pinned to latin-1."""

    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        sys.stdout.write(text.encode(encoding, "replace").decode(encoding, "replace"))


def compare_command(argv: list[str]) -> int:
    """``compare FILE_A FILE_B``. Exit 0 if identical, 1 if not, 2 on error."""

    parser = argparse.ArgumentParser(
        prog="dflash_to_eee compare",
        description="Compare two binary dumps and list the bytes that differ.",
    )
    parser.add_argument("file_a", help="First file")
    parser.add_argument("file_b", help="Second file")
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="Write a text report, or CSV when the path ends in .csv",
    )
    args = parser.parse_args(argv)
    try:
        data_a = Path(args.file_a).read_bytes()
    except OSError as exc:
        print("Cannot read %s: %s" % (args.file_a, exc), file=sys.stderr)
        return 2
    try:
        data_b = Path(args.file_b).read_bytes()
    except OSError as exc:
        print("Cannot read %s: %s" % (args.file_b, exc), file=sys.stderr)
        return 2
    result = compare_bytes(data_a, data_b)
    _write_out(format_cli_report(result, args.file_a, args.file_b))
    if args.report:
        try:
            write_report(args.report, result, args.file_a, args.file_b)
        except OSError as exc:
            print("Cannot write %s: %s" % (args.report, exc), file=sys.stderr)
            return 2
        print("Wrote %s" % args.report, file=sys.stderr)
    return 0 if result.identical else 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "compare":
        return compare_command(argv[1:])

    # Historical two-argument form. No extra sentences, so the ci logs match.
    if len(argv) == 2 and not argv[0].startswith("-") and not argv[1].startswith("-"):
        converter = DFlashConverter(get_profile("bmw_frm3"))
        converter.convert(argv[0], argv[1])
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print("dflash-to-eeprom %s" % __version__)
        return 0

    profiles, load_errors = discover_profiles(default_extra_dirs())
    for message in load_errors:
        print("Profile load error: %s" % message, file=sys.stderr)
    if args.list_profiles:
        _print_profiles(profiles)
        return 1 if load_errors else 0
    if not args.dflash or not args.eeprom:
        parser.print_usage(sys.stderr)
        print("Usage: dflash_to_eee.py <dflash> <eeprom>", file=sys.stderr)
        print("       dflash_to_eee.py --profile <id> <dflash> <eeprom>", file=sys.stderr)
        print("       dflash_to_eee.py compare <file A> <file B>", file=sys.stderr)
        return 1

    try:
        profile = get_profile(args.profile, profiles)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        with open(args.dflash, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        print("Cannot read %s: %s" % (args.dflash, exc), file=sys.stderr)
        return 1

    if not profile.conversion_enabled:
        print(profile.summary, file=sys.stderr)
        print(profile.write_notes, file=sys.stderr)
        return 1

    preliminary = size_issue(len(data), profile)
    if preliminary is not None and preliminary.level == "error":
        print(preliminary.text, file=sys.stderr)
        return 1

    result = convert_bytes(data, profile, check_size=True)
    if result.image is None:
        for _level, text in report_lines(result):
            print(text, file=sys.stderr)
        return 1

    try:
        with open(args.eeprom, "wb") as handle:
            handle.write(result.image)
    except OSError as exc:
        print("Cannot write %s: %s" % (args.eeprom, exc), file=sys.stderr)
        return 1

    words = [
        (result.image[offset] << 8) | result.image[offset + 1]
        for offset in range(0, len(result.image), 2)
    ]
    logging.info(render_info(profile, words, result.corrupt))
    for _level, text in report_lines(result):
        print(text, file=sys.stderr)

    failed = bool(result.errors or result.corrupt or (preliminary is not None and preliminary.level == "error"))
    if args.strict and (failed or preliminary is not None):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
