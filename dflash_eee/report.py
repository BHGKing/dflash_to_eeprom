# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Plain-language checks and the hex preview shared by the GUI and the CLI."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

from dflash_eee.converter import ConversionResult, Issue
from dflash_eee.i18n import t
from dflash_eee.profiles import ModuleProfile, profile_text


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
    title = profile_text(profile, "title")
    if nbytes < needed:
        return Issue(
            "error",
            t(
                "report.size.short",
                nbytes=nbytes,
                title=title,
                needed=profile.dflash_size,
            ),
        )
    if nbytes != profile.dflash_size:
        return Issue(
            "warning",
            t(
                "report.size.extra",
                nbytes=nbytes,
                title=title,
                expected=profile.dflash_size,
            ),
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
        return t("report.outcome.failed"), "error"
    if result.errors or result.corrupt:
        return t("report.outcome.errors"), "error"
    if result.warnings:
        return t("report.outcome.warnings"), "warning"
    return t("report.outcome.ok"), "ok"


_FIELD_LABELS = {
    "Production date": "field.production_date",
    "Programming date": "field.programming_date",
}

_FIELD_CAPTIONS = {
    "Hardware part number": "field.hardware_part",
    "Updated part number": "field.updated_part",
    "Original part number": "field.original_part",
}


def display_field(label: str, value: str) -> str:
    """Hide embedded NULs and non-text bytes in a field shown to a tech.

    VIN, HW-NR and the other register names stay as written. The date names
    and the parenthetical part captions are translated.
    """

    shown = []
    for char in value:
        code = ord(char)
        if char in "\r\n\t" or 32 <= code < 127:
            shown.append(char)
        else:
            shown.append(".")
    text = "".join(shown)
    label_key = _FIELD_LABELS.get(label)
    label_out = t(label_key) if label_key else label
    if " (" in text and text.endswith(")"):
        main, _, paren = text.rpartition(" (")
        caption = paren[:-1]
        caption_key = _FIELD_CAPTIONS.get(caption)
        if caption_key:
            text = "%s (%s)" % (main, t(caption_key))
    return "%s: %s" % (label_out, text)


def _count(stem: str, n: int, **kwargs: object) -> str:
    suffix = "one" if n == 1 else "many"
    return t("%s.%s" % (stem, suffix), n=n, **kwargs)


_END_REASONS = {
    "new_blocks": "report.reason.new_blocks",
    "last_block": "report.reason.last_block",
    "longest_empty": "report.reason.longest_empty",
}


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
        rows.append((
            "warning" if not result.corrupt else "error",
            _count("report.sectors_not_eee", invalid),
        ))
    unknown_cmds = sum(1 for issue in result.issues if issue.text.startswith("Unknown CMD type"))
    if unknown_cmds:
        rows.append(("error", _count("report.unknown_cmd", unknown_cmds)))
    if result.image is not None:
        rows.append(("info", t("report.image_bytes", n=len(result.image))))
    if result.endblock is not None:
        reason_key = _END_REASONS.get(result.endblock_reason)
        reason = t(reason_key) if reason_key else str(result.stats.get("endblock_reason", ""))
        rows.append(("info", t("report.journal_end", n=result.endblock, reason=reason)))
    counts = result.stats
    if counts:
        rows.append((
            "info",
            t(
                "report.counts",
                valid=counts.get("valid", 0),
                last=counts.get("last", 0),
                ready=counts.get("new", 0),
                erased=counts.get("empty", 0),
                unrecognized=counts.get("invalid", 0),
                records=counts.get("records", 0),
            ),
        ))
    for label, value in result.fields:
        rows.append(("info", display_field(label, value)))
    return rows
