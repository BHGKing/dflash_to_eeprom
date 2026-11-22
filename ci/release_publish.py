#!/usr/bin/env python3
# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Helpers for the release workflow. No network.

``embed`` writes the version tag into ``dflash_eee/build_info.py`` so
PyInstaller freezes it into the exe. ``notes`` prepends the verify section
to the changelog GitHub generated.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dflash_eee.integrity import compose_release_notes, write_build_info


def _default_build_info() -> Path:
    return ROOT / "dflash_eee" / "build_info.py"


def command_embed(args: argparse.Namespace) -> int:
    tag = os.environ.get("DFLASH_RELEASE_TAG", "")
    commit = os.environ.get("DFLASH_RELEASE_COMMIT", "")
    path = Path(args.path) if args.path else _default_build_info()
    try:
        write_build_info(path, tag, commit)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("Embedded %s %s into %s" % (tag, commit, path))
    return 0


def command_notes(args: argparse.Namespace) -> int:
    generated = Path(args.generated).read_text(encoding="utf-8")
    try:
        notes = compose_release_notes(
            generated,
            sha256=args.hash,
            tag=args.tag,
            commit=args.commit,
            repository=args.repository,
            run_id=args.run_id,
            server=args.server,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(notes, encoding="utf-8", newline="\n")
    if (
        "This software is free" not in notes
        or "Verify your download" not in notes
        or args.hash.lower() not in notes
    ):
        print("composed notes are missing the free-software or verify section", file=sys.stderr)
        return 2
    print("Wrote %s" % destination)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a dflash_to_eee release.")
    commands = parser.add_subparsers(dest="command", required=True)

    embed = commands.add_parser("embed", help="write the release tag into build_info.py")
    embed.add_argument("--path", help="destination file (default: dflash_eee/build_info.py)")
    embed.set_defaults(func=command_embed)

    notes = commands.add_parser("notes", help="prepend the verify section to generated notes")
    notes.add_argument("--generated", required=True)
    notes.add_argument("--output", required=True)
    notes.add_argument("--tag", required=True)
    notes.add_argument("--hash", required=True)
    notes.add_argument("--commit", required=True)
    notes.add_argument("--run-id", required=True)
    notes.add_argument("--repository", required=True)
    notes.add_argument("--server", default="https://github.com")
    notes.set_defaults(func=command_notes)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
