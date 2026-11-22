# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Plain-language checks and the hex preview shared by the GUI and the CLI."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from dflash_eee.converter import ConversionResult, Issue
from dflash_eee.profiles import ModuleProfile


def parse_drop_paths(data: str) -> list[str]:
    """Split a tkinterdnd2 drop payload into filesystem paths."""

    paths = []
    for braced, plain in re.findall(r"\{([^}]*)\}|(\S+)", data.strip()):
        raw = braced or plain
        if raw.startswith("file:"):
            raw = unquote(urlparse(raw).path)
            if re.match(r"^/[A-Za-z]:", raw):
                raw = raw[1:]
        if raw:
            paths.append(raw)
    return paths


def shorten_middle(text: str, limit: int = 42) -> str:
    """Keep both ends of a long filename so a drop target does not clip it."""

    if len(text) <= limit:
        return text
    keep = limit - 3
    head = keep // 2
    tail = keep - head
    return text[:head] + "..." + text[-tail:]


def size_issue(nbytes: int, profile: ModuleProfile) -> Issue | None:
    """Compare a dump's length with the profile before replaying it."""

    if not profile.conversion_enabled:
        return None
    needed = profile.scan_offset + profile.scan_sectors * profile.layout.sector_size
    if nbytes < needed:
        return Issue(
            "error",
            "Wrong size: %d bytes. %s needs %d bytes."
            % (nbytes, profile.title, profile.dflash_size),
        )
    if nbytes != profile.dflash_size:
        return Issue(
            "warning",
            "Wrong size: %d bytes. %s expects %d. Extra bytes are ignored."
            % (nbytes, profile.title, profile.dflash_size),
        )
    return None


def hex_dump(data: bytes, width: int = 16) -> str:
    """Classic hex + ASCII view of an image about to be programmed."""

    if not data:
        return ""
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset:offset + width]
        hex_part = " ".join("%02X" % byte for byte in chunk)
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append("%04X  %s  %s" % (offset, hex_part, ascii_part))
    return "\n".join(lines)


def outcome(result: ConversionResult) -> tuple[str, str]:
    """Short headline and a tone name: ok, warning or error."""

    if result.image is None:
        return "Conversion failed", "error"
    if result.errors or result.corrupt:
        return "Converted with errors - read them before saving", "error"
    if result.warnings:
        return "Converted with warnings", "warning"
    return "Conversion succeeded", "ok"


def display_field(label: str, value: str) -> str:
    """Hide embedded NULs and non-text bytes in a field shown to a tech."""

    shown = []
    for char in value:
        code = ord(char)
        if char in "\r\n\t" or 32 <= code < 127:
            shown.append(char)
        else:
            shown.append(".")
    return "%s: %s" % (label, "".join(shown))


def report_lines(result: ConversionResult, extra: list[Issue] | None = None) -> list[tuple[str, str]]:
    """Ordered ``(level, text)`` rows for the result pane."""

    rows: list[tuple[str, str]] = []
    headline, tone = outcome(result)
    rows.append((tone, headline))
    for issue in (extra or []):
        if issue.text not in {text for _level, text in rows}:
            rows.append((issue.level, issue.text))
    # Size warnings are also stored on the result when convert_bytes checked.
    seen = {text for _level, text in rows}
    for issue in result.issues:
        if issue.text in seen:
            continue
        # Collapse the per-sector "unknown block" flood; the count is in stats.
        if issue.text.startswith("Unknown block type:") or issue.text.startswith("Unknown CMD type"):
            continue
        if issue.text.startswith("Record address"):
            continue
        rows.append((issue.level, issue.text))
        seen.add(issue.text)
    invalid = result.stats.get("invalid", 0)
    if invalid:
        rows.append(("warning" if not result.corrupt else "error",
                     "%d sector(s) are not S12XE EEE records." % invalid))
    unknown_cmds = sum(1 for issue in result.issues if issue.text.startswith("Unknown CMD type"))
    if unknown_cmds:
        rows.append(("error", "%d record(s) have an unknown command code." % unknown_cmds))
    out_of_range = result.stats.get("out_of_range", 0)
    if out_of_range:
        rows.append(("error", "%d record(s) were outside the EEE and were skipped." % out_of_range))
    if result.image is not None:
        rows.append(("info", "EEPROM image: %d bytes." % len(result.image)))
    if result.endblock is not None:
        rows.append(("info", "Journal ends at sector %d. %s" % (result.endblock, result.stats.get("endblock_reason", ""))))
    counts = result.stats
    if counts:
        rows.append((
            "info",
            "Sectors: %d valid, %d current, %d ready, %d erased, %d unrecognized. Records applied: %d."
            % (
                counts.get("valid", 0),
                counts.get("last", 0),
                counts.get("new", 0),
                counts.get("empty", 0),
                counts.get("invalid", 0),
                counts.get("records", 0),
            ),
        ))
    for label, value in result.fields:
        rows.append(("info", display_field(label, value)))
    return rows
