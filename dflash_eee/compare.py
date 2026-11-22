# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Compare two binary dumps and list the runs of bytes that differ.

A run is contiguous: every address in it differs, and the bytes on either
side (when they exist) match. When one file is longer, the extra tail is its
own run, or it is folded into the previous run when that run already reaches
the end of the overlap. ``bytes_a`` and ``bytes_b`` start at ``offset``. The
shorter of the two is the side whose file ended inside the run.

The scan skips equal blocks with a memory compare, then binary-searches the
first mismatch, so a few megabytes that are almost the same stay quick.
"""

from __future__ import annotations

import csv
import hashlib
import io
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from dflash_eee.i18n import t

ROW_BYTES = 16
ADDR_DIGITS = 8
_CHUNK = 65536


@dataclass(frozen=True)
class DiffRange:
    """One contiguous run of differing bytes."""

    offset: int
    length: int
    bytes_a: bytes
    bytes_b: bytes

    def __post_init__(self) -> None:
        if self.offset < 0 or self.length < 0:
            raise ValueError("range offset and length must be >= 0")
        if self.length != max(len(self.bytes_a), len(self.bytes_b)):
            raise ValueError("range length must match the longer byte string")


@dataclass(frozen=True)
class CompareResult:
    """Outcome of comparing two buffers.

    ``longer`` is ``"a"``, ``"b"``, or ``""`` when the sizes match.
    ``identical`` is true only when the sizes match and every byte matches.
    ``differing_bytes`` counts both changed bytes in the overlap and bytes
    that exist on only one side.
    """

    size_a: int
    size_b: int
    identical: bool
    size_mismatch: bool
    longer: str
    extra_bytes: int
    differing_bytes: int
    ranges: tuple[DiffRange, ...]
    sha256_a: str
    sha256_b: str

    def summary_line(self) -> str:
        """One sentence: identical or not, counts, and the sizes."""

        relation = t("cli.compare.relation.identical") if self.identical else t("cli.compare.relation.different")
        byte_word = t("cli.compare.byte.one") if self.differing_bytes == 1 else t("cli.compare.byte.many")
        range_word = t("cli.compare.range.one") if len(self.ranges) == 1 else t("cli.compare.range.many")
        if self.size_mismatch:
            side = "A" if self.longer == "a" else "B"
            sizes = t(
                "cli.compare.size_mismatch",
                size_a=self.size_a,
                size_b=self.size_b,
                extra=self.extra_bytes,
                side=side,
            )
        else:
            sizes = t("cli.compare.sizes_match", n=self.size_a)
        return t(
            "cli.compare.summary",
            relation=relation,
            differing=self.differing_bytes,
            byte_word=byte_word,
            ranges=len(self.ranges),
            range_word=range_word,
            sizes=sizes,
        )

    def summary_text(self) -> str:
        """Summary line plus the SHA-256 of each buffer."""

        return t(
            "cli.compare.summary_sha",
            line=self.summary_line(),
            sha_a=self.sha256_a,
            sha_b=self.sha256_b,
        )


def compare_bytes(data_a: bytes | bytearray | memoryview, data_b: bytes | bytearray | memoryview) -> CompareResult:
    """Compare two binary buffers. Paths are the caller's job."""

    left = _as_view(data_a)
    right = _as_view(data_b)
    ranges = _diff_ranges(left, right)
    size_a = len(left)
    size_b = len(right)
    differing = 0
    for item in ranges:
        differing += item.length
    mismatch = size_a != size_b
    if size_a > size_b:
        longer = "a"
    elif size_b > size_a:
        longer = "b"
    else:
        longer = ""
    identical = not ranges
    sha_a = hashlib.sha256(left).hexdigest()
    # Equal buffers share a digest. A different length or any run needs both.
    if identical:
        sha_b = sha_a
    else:
        sha_b = hashlib.sha256(right).hexdigest()
    return CompareResult(
        size_a=size_a,
        size_b=size_b,
        identical=identical,
        size_mismatch=mismatch,
        longer=longer,
        extra_bytes=abs(size_a - size_b),
        differing_bytes=differing,
        ranges=tuple(ranges),
        sha256_a=sha_a,
        sha256_b=sha_b,
    )


class DiffIndex:
    """O(log n) lookup from an address to the run that covers it."""

    def __init__(self, ranges: tuple[DiffRange, ...] | list[DiffRange]):
        self.ranges = tuple(ranges)
        self.starts = [item.offset for item in self.ranges]

    def covers(self, offset: int) -> bool:
        if offset < 0 or not self.ranges:
            return False
        pos = bisect_right(self.starts, offset) - 1
        if pos < 0:
            return False
        item = self.ranges[pos]
        return offset < item.offset + item.length

    def differing_rows(self, span: int, row_bytes: int = ROW_BYTES) -> list[int]:
        """Row numbers (``offset // row_bytes``) that contain a difference."""

        if span <= 0 or not self.ranges or row_bytes < 1:
            return []
        rows: list[int] = []
        last = -1
        for item in self.ranges:
            if item.length <= 0:
                continue
            first = item.offset // row_bytes
            final = (item.offset + item.length - 1) // row_bytes
            start_row = first if first > last else last + 1
            for row in range(start_row, final + 1):
                rows.append(row)
            last = final
        return rows


def row_count(span: int, row_bytes: int = ROW_BYTES) -> int:
    """How many hex rows a buffer of ``span`` bytes needs."""

    if span <= 0 or row_bytes < 1:
        return 0
    return (span + row_bytes - 1) // row_bytes


def clamp_top(top: int, total: int, page: int) -> int:
    """Keep a virtual scroll position inside the rows that exist."""

    if total <= 0:
        return 0
    page = 1 if page < 1 else page
    max_top = total - page
    if max_top < 0:
        max_top = 0
    if top < 0:
        return 0
    if top > max_top:
        return max_top
    return top


def visible_rows(top: int, page: int, total_rows: int, diff_rows: list[int], only_diffs: bool) -> list[int]:
    """Absolute row numbers for one screen, optionally skipping clean rows."""

    if page < 1:
        return []
    start = 0 if top < 0 else top
    if only_diffs:
        return list(diff_rows[start:start + page])
    if start >= total_rows:
        return []
    end = start + page
    if end > total_rows:
        end = total_rows
    return list(range(start, end))


def column_header(row_bytes: int = ROW_BYTES) -> str:
    """Column titles aligned with :func:`row_cells`."""

    cols = " ".join("%02X" % index for index in range(row_bytes))
    return "%s  %s  ASCII" % (" " * ADDR_DIGITS, cols)


def row_cells(
    data: bytes | bytearray | memoryview,
    row: int,
    span: int,
    index: DiffIndex | None,
    selected: DiffRange | None = None,
    row_bytes: int = ROW_BYTES,
) -> list[tuple[str, str]]:
    """One hex row as ``(text, tag)`` cells.

    Tags are ``addr``, ``same``, ``diff``, ``current``, ``missing``, ``pad``
    and ``gap``. Both files use the same ``span`` (the longer size) so the
    columns line up. Positions past ``span`` are padding. Positions past this
    buffer but inside ``span`` are the missing tail.
    """

    offset = row * row_bytes
    cells = [("%0*X  " % (ADDR_DIGITS, offset), "addr")]
    ascii_cells: list[tuple[str, str]] = []
    selected_lo = None if selected is None else selected.offset
    selected_hi = None if selected is None else selected.offset + selected.length
    for column in range(row_bytes):
        pos = offset + column
        if pos >= span:
            cells.append(("   ", "pad"))
            ascii_cells.append((" ", "pad"))
            continue
        if pos >= len(data):
            kind = _cell_kind(pos, index, selected_lo, selected_hi, missing=True)
            cells.append(("-- ", kind))
            ascii_cells.append((" ", kind))
            continue
        byte = data[pos]
        if isinstance(byte, (bytes, bytearray)):
            byte = byte[0]
        kind = _cell_kind(pos, index, selected_lo, selected_hi, missing=False)
        cells.append(("%02X " % byte, kind))
        ascii_cells.append((chr(byte) if 32 <= byte < 127 else ".", kind))
    cells.append((" ", "gap"))
    cells.extend(ascii_cells)
    cells.append(("\n", "gap"))
    return cells


def format_range_line(item: DiffRange, preview: int = 8) -> str:
    """One range-list row: offset, length, and a short hex preview."""

    return "%08X   %7d   A: %s    B: %s" % (
        item.offset,
        item.length,
        _preview_hex(item.bytes_a, preview),
        _preview_hex(item.bytes_b, preview),
    )


def format_report(result: CompareResult, name_a: str = "A", name_b: str = "B", kind: str = "text") -> str:
    """Render a text or CSV report. ``kind`` is ``"text"`` or ``"csv"``."""

    if kind == "csv":
        return _format_csv(result, name_a, name_b)
    if kind == "text":
        return _format_text(result, name_a, name_b)
    raise ValueError("kind must be 'text' or 'csv'")


def format_cli_report(result: CompareResult, name_a: str, name_b: str, preview: int = 16) -> str:
    """Terminal summary. Long runs show the first bytes and a count of the rest."""

    lines = [
        t("cli.compare.file_line", side="A", name=name_a),
        t("cli.compare.cli_nbytes", n=result.size_a),
        t("cli.compare.sha", sha=result.sha256_a),
        t("cli.compare.file_line", side="B", name=name_b),
        t("cli.compare.cli_nbytes", n=result.size_b),
        t("cli.compare.sha", sha=result.sha256_b),
        result.summary_line(),
    ]
    if result.ranges:
        lines.append("")
        for item in result.ranges:
            word = t("cli.compare.unit.one") if item.length == 1 else t("cli.compare.unit.many")
            lines.append(t(
                "cli.compare.cli_range",
                offset="%08X" % item.offset,
                n=item.length,
                word=word,
            ))
            lines.append(t("cli.compare.side_hex", side="A", hex=_preview_hex(item.bytes_a, preview)))
            lines.append(t("cli.compare.side_hex", side="B", hex=_preview_hex(item.bytes_b, preview)))
    return "\n".join(lines) + "\n"


def write_report(
    path: str | Path,
    result: CompareResult,
    name_a: str = "A",
    name_b: str = "B",
    kind: str | None = None,
) -> None:
    """Write a report. A ``.csv`` path selects CSV unless ``kind`` is set."""

    if kind is None:
        kind = "csv" if str(path).lower().endswith(".csv") else "text"
    Path(path).write_text(format_report(result, name_a, name_b, kind), encoding="utf-8", newline="\n")


def _as_view(data: bytes | bytearray | memoryview) -> memoryview:
    if isinstance(data, str):
        raise TypeError("compare_bytes expects binary buffers, not str")
    try:
        view = memoryview(data)
    except TypeError as exc:
        raise TypeError("compare_bytes expects a bytes-like object") from exc
    if view.ndim != 1:
        raise TypeError("compare_bytes expects a one-dimensional buffer")
    if view.format == "B":
        return view
    try:
        return view.cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("compare_bytes expects a bytes-like object") from exc


def _diff_ranges(left: memoryview, right: memoryview) -> list[DiffRange]:
    len_a = len(left)
    len_b = len(right)
    common = len_a if len_a < len_b else len_b
    ranges: list[DiffRange] = []
    pos = 0
    while pos < common:
        mismatch = _find_mismatch(left, right, pos, common)
        if mismatch >= common:
            break
        match = _find_match(left, right, mismatch + 1, common)
        ranges.append(DiffRange(
            mismatch,
            match - mismatch,
            bytes(left[mismatch:match]),
            bytes(right[mismatch:match]),
        ))
        pos = match
    if len_a != len_b:
        _append_tail(ranges, left, right, common)
    return ranges


def _append_tail(ranges: list[DiffRange], left: memoryview, right: memoryview, common: int) -> None:
    longer = len(left) if len(left) > len(right) else len(right)
    if ranges and ranges[-1].offset + ranges[-1].length == common:
        start = ranges[-1].offset
        ranges[-1] = DiffRange(start, longer - start, bytes(left[start:]), bytes(right[start:]))
        return
    ranges.append(DiffRange(common, longer - common, bytes(left[common:]), bytes(right[common:])))


def _find_mismatch(left: memoryview, right: memoryview, start: int, end: int) -> int:
    """First index in ``[start, end)`` whose bytes differ, or ``end``."""

    index = start
    while index < end:
        step = end - index
        if step > _CHUNK:
            step = _CHUNK
        if left[index:index + step] != right[index:index + step]:
            return _first_mismatch(left, right, index, index + step)
        index += step
    return end


def _first_mismatch(left: memoryview, right: memoryview, start: int, end: int) -> int:
    """First mismatch inside a block already known to differ."""

    while end - start > 1:
        mid = start + ((end - start) // 2)
        if left[start:mid] != right[start:mid]:
            end = mid
        else:
            start = mid
    return start


def _find_match(left: memoryview, right: memoryview, start: int, end: int) -> int:
    """First index in ``[start, end)`` whose bytes match, or ``end``."""

    index = start
    last = end - 8
    while index <= last:
        if (left[index] == right[index] or left[index + 1] == right[index + 1]
                or left[index + 2] == right[index + 2] or left[index + 3] == right[index + 3]
                or left[index + 4] == right[index + 4] or left[index + 5] == right[index + 5]
                or left[index + 6] == right[index + 6] or left[index + 7] == right[index + 7]):
            while left[index] != right[index]:
                index += 1
            return index
        index += 8
    while index < end:
        if left[index] == right[index]:
            return index
        index += 1
    return end


def _cell_kind(pos: int, index: DiffIndex | None, selected_lo: int | None, selected_hi: int | None, missing: bool) -> str:
    if selected_lo is not None and selected_hi is not None and selected_lo <= pos < selected_hi:
        return "current"
    if index is not None and index.covers(pos):
        return "diff"
    if missing:
        return "missing"
    return "same"


def _preview_hex(data: bytes, limit: int) -> str:
    if not data:
        return t("cli.compare.preview.none")
    shown = data[:limit]
    text = " ".join("%02X" % byte for byte in shown)
    extra = len(data) - len(shown)
    if extra:
        text += t("cli.compare.preview.more", n=extra)
    return text


def _hex_bytes(data: bytes) -> str:
    return " ".join("%02X" % byte for byte in data)


def _format_text(result: CompareResult, name_a: str, name_b: str) -> str:
    lines = [
        t("cli.compare.report_title"),
        t("cli.compare.file_line", side="A", name=name_a),
        t("cli.compare.report_size", n=result.size_a),
        t("cli.compare.sha", sha=result.sha256_a),
        t("cli.compare.file_line", side="B", name=name_b),
        t("cli.compare.report_size", n=result.size_b),
        t("cli.compare.sha", sha=result.sha256_b),
        result.summary_line(),
        "",
    ]
    if not result.ranges:
        lines.append(t("cli.compare.no_differences"))
    else:
        lines.append(t("cli.compare.report_columns"))
        for item in result.ranges:
            lines.append("%08X  %d" % (item.offset, item.length))
            lines.extend(_side_lines("A", item.bytes_a))
            lines.extend(_side_lines("B", item.bytes_b))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _side_lines(label: str, data: bytes) -> list[str]:
    if not data:
        return [t("cli.compare.side_none", side=label)]
    lines = []
    for start in range(0, len(data), ROW_BYTES):
        chunk = data[start:start + ROW_BYTES]
        hex_part = _hex_bytes(chunk)
        if start == 0:
            lines.append(t("cli.compare.side_hex", side=label, hex=hex_part))
        else:
            lines.append("     %s" % hex_part)
    return lines


def _format_csv(result: CompareResult, name_a: str, name_b: str) -> str:
    # Column names stay English. offset, hex, and sha256 are the interchange terms.
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["field", "value"])
    writer.writerow(["file_a", name_a])
    writer.writerow(["size_a", result.size_a])
    writer.writerow(["sha256_a", result.sha256_a])
    writer.writerow(["file_b", name_b])
    writer.writerow(["size_b", result.size_b])
    writer.writerow(["sha256_b", result.sha256_b])
    writer.writerow(["identical", "yes" if result.identical else "no"])
    writer.writerow(["differing_bytes", result.differing_bytes])
    writer.writerow(["range_count", len(result.ranges)])
    writer.writerow(["size_mismatch", "yes" if result.size_mismatch else "no"])
    writer.writerow(["extra_bytes", result.extra_bytes])
    writer.writerow(["longer", result.longer])
    writer.writerow([])
    writer.writerow(["offset", "length", "hex_a", "hex_b"])
    for item in result.ranges:
        writer.writerow([item.offset, item.length, _hex_bytes(item.bytes_a), _hex_bytes(item.bytes_b)])
    return buffer.getvalue()
