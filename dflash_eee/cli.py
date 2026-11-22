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
from dflash_eee.compare import compare_bytes, format_cli_report, format_report, write_report
from dflash_eee.converter import DFlashConverter, InputError, convert_bytes, render_info
from dflash_eee.decode import (
    decode_image,
    format_keys,
    format_module_info,
    load_module_image,
    module_info_json_text,
)
from dflash_eee.i18n import LanguageError, init_language, t
from dflash_eee.journal import JournalError, encode_journal
from dflash_eee.virginize import format_virginize_plan, output_path_problem, virginize_image
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


def _prefix_only(argv: list[str]) -> bool:
    """True when ``argv`` is only ``--lang`` and ``--profile`` options."""

    cursor = 0
    while cursor < len(argv):
        token = argv[cursor]
        if token in ("--lang", "--profile"):
            if cursor + 1 >= len(argv):
                return False
            cursor += 2
            continue
        if token.startswith("--lang=") or token.startswith("--profile="):
            cursor += 1
            continue
        return False
    return True


def _subcommand_argv(argv: list[str], name: str) -> list[str] | None:
    """Arguments for ``name``, or None when it is not the command.

    ``--lang`` and ``--profile`` may sit in front of the command. The returned
    list keeps those options and drops the command word, so the sub-parser
    sees them. ``info`` and ``keys`` are reserved even in the two-argument
    form. A file literally named info is not converted that way.
    """

    if name not in argv:
        return None
    index = argv.index(name)
    if not _prefix_only(argv[:index]):
        return None
    return argv[:index] + argv[index + 1:]


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


def _apply_lang(code: str | None) -> int | None:
    if not code:
        return None
    try:
        init_language(code, honor_env=False)
    except LanguageError as exc:
        init_language(honor_env=True)
        print(t("cli.err.bad_lang", lang=exc.code, choices="en, es"), file=sys.stderr)
        return 1
    return None


def _read_input(path: str) -> bytes | None:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        print(t("cli.cannot_read", path=path, error=exc), file=sys.stderr)
        return None


def _profile_or_exit(profile_id: str):
    profiles, load_errors = discover_profiles(default_extra_dirs())
    for message in load_errors:
        print(t("cli.profile_load_error", message=message), file=sys.stderr)
    try:
        return get_profile(profile_id, profiles)
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return None


def _load_for_info(path: str, profile):
    data = _read_input(path)
    if data is None:
        return None
    try:
        image, source, warnings = load_module_image(data, profile)
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return None
    for warning in warnings:
        print(warning, file=sys.stderr)
    info = decode_image(profile, image, source=source)
    info.warnings = warnings
    return info


def info_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dflash_to_eee info", description=t("cli.info.description"))
    parser.add_argument("--lang", default=None, help=t("cli.help.lang"))
    parser.add_argument("--profile", default="bmw_frm3", help=t("cli.help.profile"))
    parser.add_argument("--json", action="store_true", help=t("cli.info.help.json"))
    parser.add_argument("file", help=t("cli.info.help.file"))
    args = parser.parse_args(argv)
    if _apply_lang(args.lang):
        return 1
    profile = _profile_or_exit(args.profile)
    if profile is None:
        return 1
    info = _load_for_info(args.file, profile)
    if info is None:
        return 1
    if args.json:
        _write_out(module_info_json_text(info))
    else:
        _write_out(format_module_info(info))
    return 0


def keys_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dflash_to_eee keys", description=t("cli.keys.description"))
    parser.add_argument("--lang", default=None, help=t("cli.help.lang"))
    parser.add_argument("--profile", default="bmw_frm3", help=t("cli.help.profile"))
    parser.add_argument("--json", action="store_true", help=t("cli.info.help.json"))
    parser.add_argument("file", help=t("cli.info.help.file"))
    args = parser.parse_args(argv)
    if _apply_lang(args.lang):
        return 1
    profile = _profile_or_exit(args.profile)
    if profile is None:
        return 1
    info = _load_for_info(args.file, profile)
    if info is None:
        return 1
    if args.json:
        _write_out(module_info_json_text(info))
    else:
        _write_out(format_keys(info))
    return 0


def virginize_command(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dflash_to_eee virginize", description=t("cli.virginize.description"))
    parser.add_argument("--lang", default=None, help=t("cli.help.lang"))
    parser.add_argument("--profile", default="bmw_frm3", help=t("cli.help.profile"))
    parser.add_argument("--report", metavar="PATH", help=t("cli.virginize.help.report"))
    parser.add_argument("--journal", metavar="PATH", help=t("cli.virginize.help.journal"))
    parser.add_argument("source", help=t("cli.virginize.help.source"))
    parser.add_argument("output", help=t("cli.virginize.help.output"))
    args = parser.parse_args(argv)
    if _apply_lang(args.lang):
        return 1
    profile = _profile_or_exit(args.profile)
    if profile is None:
        return 1
    data = _read_input(args.source)
    if data is None:
        return 2
    try:
        image, _source, warnings = load_module_image(data, profile)
    except InputError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for warning in warnings:
        print(warning, file=sys.stderr)
    plan = virginize_image(profile, image)
    text = format_virginize_plan(profile, plan)
    if not plan.ready:
        _write_out(text)
        return 1
    source_path = Path(args.source)
    output_path = Path(args.output)
    problem = output_path_problem(source_path, output_path)
    if problem == "same":
        print(t("virginize.err.same", path=args.output), file=sys.stderr)
        return 1
    if problem == "exists":
        print(t("virginize.err.exists", path=args.output), file=sys.stderr)
        return 1
    journal_path = Path(args.journal) if args.journal else None
    report_path = Path(args.report) if args.report else None
    for extra in (journal_path, report_path):
        if extra is None:
            continue
        extra_problem = output_path_problem(source_path, extra)
        if extra_problem == "same" or extra.resolve() == output_path.resolve():
            print(t("virginize.err.same", path=str(extra)), file=sys.stderr)
            return 1
        if extra_problem == "exists":
            print(t("virginize.err.exists", path=str(extra)), file=sys.stderr)
            return 1
    assert plan.image is not None
    journal = None
    if journal_path is not None:
        try:
            journal = encode_journal(plan.image, profile)
        except JournalError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        output_path.write_bytes(plan.image)
    except OSError as exc:
        print(t("cli.cannot_write", path=args.output, error=exc), file=sys.stderr)
        return 2
    print(t("virginize.wrote", path=args.output, n=len(plan.image)), file=sys.stderr)
    _write_out(text)
    compared = compare_bytes(image, plan.image)
    _write_out(format_cli_report(compared, args.source, args.output))
    if report_path is not None:
        try:
            header = text
            body = format_report(compared, args.source, args.output, "text")
            report_path.write_text(header + body, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(t("cli.cannot_write", path=str(report_path), error=exc), file=sys.stderr)
            return 2
        print(t("cli.compare.wrote", path=str(report_path)), file=sys.stderr)
    if journal_path is not None and journal is not None:
        try:
            journal_path.write_bytes(journal)
        except OSError as exc:
            print(t("cli.cannot_write", path=str(journal_path), error=exc), file=sys.stderr)
            return 2
        print(t("virginize.wrote_journal", path=str(journal_path), n=len(journal)), file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    if argv is None:
        argv = sys.argv[1:]

    # info, keys and virginize are commands even when only two tokens follow
    # the program name. compare out.bin stays a conversion of a file named
    # compare; that case is decided with the historical form below.
    for name, handler in (
        ("info", info_command),
        ("keys", keys_command),
        ("virginize", virginize_command),
    ):
        tail = _subcommand_argv(argv, name)
        if tail is not None:
            try:
                init_language(_peek_lang(argv), honor_env=True)
            except LanguageError as exc:
                init_language(honor_env=True)
                print(t("cli.err.bad_lang", lang=exc.code, choices="en, es"), file=sys.stderr)
                return 1
            return handler(tail)

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
