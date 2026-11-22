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
from dflash_eee.i18n import LanguageError, init_language, t
from dflash_eee.profiles import (
    ProfileError,
    default_extra_dirs,
    discover_profiles,
    get_profile,
    profile_text,
)
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


def _historical(argv: list[str]) -> bool:
    return len(argv) == 2 and not argv[0].startswith("-") and not argv[1].startswith("-")


def _peek_lang(argv: list[str]) -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--lang", default=None)
    known, _rest = parser.parse_known_args(argv)
    return known.lang


def _compare_argv(argv: list[str]) -> list[str] | None:
    """Arguments that follow the compare subcommand, or None.

    ``compare`` is the subcommand when it is the first token, or when the
    only tokens in front of it are ``--lang``. The historical two-argument
    form is decided before this runs, so ``compare out.bin`` still converts
    a file named compare.
    """

    if "compare" not in argv:
        return None
    index = argv.index("compare")
    before = argv[:index]
    cursor = 0
    while cursor < len(before):
        token = before[cursor]
        if token == "--lang":
            if cursor + 1 >= len(before):
                return None
            cursor += 2
            continue
        if token.startswith("--lang="):
            cursor += 1
            continue
        return None
    return argv[index + 1:]


def _print_profiles(profiles) -> None:
    print(t("cli.version_line", version=__version__))
    print("")
    for profile in profiles:
        print("%-20s %-14s %s" % (profile.id, profile.status_label, profile_text(profile, "title")))
        print("  " + t("cli.mcu", mcu=profile.mcu))
        if profile.conversion_enabled:
            print("  " + t("cli.sizes", dflash=profile.dflash_size, eee=profile.eee_size))
        else:
            print("  " + t("cli.disabled"))
        assumptions = profile_text(profile, "assumptions")
        if assumptions:
            print("  " + t("cli.note", text=assumptions))
        print("")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dflash_to_eee",
        description=t("cli.description"),
    )
    parser.add_argument("--lang", default=None, help=t("cli.help.lang"))
    parser.add_argument("--profile", default="bmw_frm3", help=t("cli.help.profile"))
    parser.add_argument("--list-profiles", action="store_true", help=t("cli.help.list"))
    parser.add_argument("--strict", action="store_true", help=t("cli.help.strict"))
    parser.add_argument("--version", action="store_true", help=t("cli.help.version"))
    parser.add_argument("dflash", nargs="?", help=t("cli.help.dflash"))
    parser.add_argument("eeprom", nargs="?", help=t("cli.help.eeprom"))
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
        description=t("cli.compare.description"),
    )
    parser.add_argument("--lang", default=None, help=t("cli.help.lang"))
    parser.add_argument("file_a", help=t("cli.compare.help.file_a"))
    parser.add_argument("file_b", help=t("cli.compare.help.file_b"))
    parser.add_argument("--report", metavar="PATH", help=t("cli.compare.help.report"))
    args = parser.parse_args(argv)
    if args.lang:
        try:
            init_language(args.lang, honor_env=False)
        except LanguageError as exc:
            init_language(honor_env=True)
            print(t("cli.err.bad_lang", lang=exc.code, choices="en, es"), file=sys.stderr)
            return 1
    try:
        data_a = Path(args.file_a).read_bytes()
    except OSError as exc:
        print(t("cli.cannot_read", path=args.file_a, error=exc), file=sys.stderr)
        return 2
    try:
        data_b = Path(args.file_b).read_bytes()
    except OSError as exc:
        print(t("cli.cannot_read", path=args.file_b, error=exc), file=sys.stderr)
        return 2
    result = compare_bytes(data_a, data_b)
    _write_out(format_cli_report(result, args.file_a, args.file_b))
    if args.report:
        try:
            write_report(args.report, result, args.file_a, args.file_b)
        except OSError as exc:
            print(t("cli.cannot_write", path=args.report, error=exc), file=sys.stderr)
            return 2
        print(t("cli.compare.wrote", path=args.report), file=sys.stderr)
    return 0 if result.identical else 1


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    if argv is None:
        argv = sys.argv[1:]

    # Historical two-argument form. No extra sentences, so the ci logs match.
    # Language is ignored here on purpose: those log lines are the 2017 text.
    # ``compare out.bin`` stays a conversion of a file named compare.
    if _historical(argv):
        converter = DFlashConverter(get_profile("bmw_frm3"))
        converter.convert(argv[0], argv[1])
        return 0

    try:
        init_language(_peek_lang(argv), honor_env=True)
    except LanguageError as exc:
        # The requested tag is not a catalog. Say so in the language we can use.
        init_language(honor_env=True)
        print(t("cli.err.bad_lang", lang=exc.code, choices="en, es"), file=sys.stderr)
        return 1

    compare_args = _compare_argv(argv)
    if compare_args is not None:
        return compare_command(compare_args)

    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(t("cli.version_line", version=__version__))
        return 0

    profiles, load_errors = discover_profiles(default_extra_dirs())
    for message in load_errors:
        print(t("cli.profile_load_error", message=message), file=sys.stderr)
    if args.list_profiles:
        _print_profiles(profiles)
        return 1 if load_errors else 0
    if not args.dflash or not args.eeprom:
        parser.print_usage(sys.stderr)
        print(t("cli.usage1"), file=sys.stderr)
        print(t("cli.usage2"), file=sys.stderr)
        print(t("cli.usage3"), file=sys.stderr)
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
        print(t("cli.cannot_read", path=args.dflash, error=exc), file=sys.stderr)
        return 1

    if not profile.conversion_enabled:
        print(profile_text(profile, "summary"), file=sys.stderr)
        print(profile_text(profile, "write_notes"), file=sys.stderr)
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
        print(t("cli.cannot_write", path=args.eeprom, error=exc), file=sys.stderr)
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
