# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Binary compare: equal files, one byte, several runs, length, empty."""

import csv
import hashlib
import io
import os
import subprocess
import sys
import time
from pathlib import Path

from dflash_eee.compare import (
    CompareResult,
    DiffIndex,
    column_header,
    compare_bytes,
    format_cli_report,
    format_range_line,
    format_report,
    row_cells,
    row_count,
    visible_rows,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dflash_to_eee.py"
GOOD = ROOT / "ci" / "input" / "dflash" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"
EMPTY_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
    )


def _assert_consistent(result: CompareResult, data_a: bytes, data_b: bytes) -> None:
    assert result.size_a == len(data_a)
    assert result.size_b == len(data_b)
    assert result.size_mismatch is (len(data_a) != len(data_b))
    assert result.identical is (data_a == data_b)
    assert result.sha256_a == hashlib.sha256(data_a).hexdigest()
    assert result.sha256_b == hashlib.sha256(data_b).hexdigest()
    if len(data_a) > len(data_b):
        assert result.longer == "a"
    elif len(data_b) > len(data_a):
        assert result.longer == "b"
    else:
        assert result.longer == ""
    assert result.extra_bytes == abs(len(data_a) - len(data_b))
    total = 0
    end = -1
    covered = []
    for item in result.ranges:
        assert item.offset > end
        assert data_a[item.offset:item.offset + len(item.bytes_a)] == item.bytes_a
        assert data_b[item.offset:item.offset + len(item.bytes_b)] == item.bytes_b
        for index in range(item.length):
            pos = item.offset + index
            byte_a = data_a[pos] if pos < len(data_a) else None
            byte_b = data_b[pos] if pos < len(data_b) else None
            assert byte_a != byte_b
        total += item.length
        end = item.offset + item.length - 1
        covered.append((item.offset, item.offset + item.length))
    assert result.differing_bytes == total

    def inside(pos):
        return any(start <= pos < stop for start, stop in covered)

    common = min(len(data_a), len(data_b))
    for pos in range(common):
        assert inside(pos) is (data_a[pos] != data_b[pos])
    for pos in range(common, max(len(data_a), len(data_b))):
        assert inside(pos)


def test_equal_buffers_are_identical():
    result = compare_bytes(b"abcd", b"abcd")
    _assert_consistent(result, b"abcd", b"abcd")
    assert result.ranges == ()
    assert result.differing_bytes == 0
    assert result.summary_line().startswith("Identical.")
    assert "0 differing bytes in 0 ranges" in result.summary_line()


def test_single_changed_byte():
    data_a = b"abcd"
    data_b = b"abXd"
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert len(result.ranges) == 1
    item = result.ranges[0]
    assert item.offset == 2
    assert item.length == 1
    assert item.bytes_a == b"c"
    assert item.bytes_b == b"X"
    assert "1 differing byte in 1 range" in result.summary_line()


def test_several_ranges_and_a_merged_run():
    data_a = bytes([0, 1, 2, 3, 4, 5, 6, 7])
    data_b = bytes([0, 9, 2, 3, 8, 8, 6, 7])
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert [(item.offset, item.length) for item in result.ranges] == [(1, 1), (4, 2)]
    assert result.ranges[1].bytes_a == b"\x04\x05"
    assert result.ranges[1].bytes_b == b"\x08\x08"
    assert result.differing_bytes == 3


def test_change_at_the_first_and_last_byte():
    result = compare_bytes(b"\x01\x02\x03", b"\x09\x02\x08")
    _assert_consistent(result, b"\x01\x02\x03", b"\x09\x02\x08")
    assert [(item.offset, item.length) for item in result.ranges] == [(0, 1), (2, 1)]


def test_whole_buffer_is_one_range():
    result = compare_bytes(b"\x00\x00", b"\x01\x02")
    _assert_consistent(result, b"\x00\x00", b"\x01\x02")
    assert len(result.ranges) == 1
    assert result.ranges[0].length == 2
    assert result.ranges[0].bytes_a == b"\x00\x00"
    assert result.ranges[0].bytes_b == b"\x01\x02"


def test_different_lengths_tail_only():
    data_a = b"abc"
    data_b = b"abcXY"
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert len(result.ranges) == 1
    assert result.ranges[0].offset == 3
    assert result.ranges[0].bytes_a == b""
    assert result.ranges[0].bytes_b == b"XY"
    assert result.extra_bytes == 2
    assert "Size mismatch" in result.summary_line()
    assert "2 extra in B" in result.summary_line()


def test_different_lengths_merge_a_change_at_the_end():
    data_a = b"\x00" * 10
    data_b = b"\x00" * 9 + b"\x01\x02"
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert len(result.ranges) == 1
    assert result.ranges[0].offset == 9
    assert result.ranges[0].bytes_a == b"\x00"
    assert result.ranges[0].bytes_b == b"\x01\x02"
    assert result.differing_bytes == 2


def test_shorter_second_file_and_a_change_away_from_the_tail():
    # The changed byte does not touch the end of the overlap, so the extra
    # tail stays its own run instead of merging into that change.
    data_a = b"abcdef"
    data_b = b"aXc"
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert [(item.offset, item.length, item.bytes_a, item.bytes_b) for item in result.ranges] == [
        (1, 1, b"b", b"X"),
        (3, 3, b"def", b""),
    ]
    assert result.longer == "a"
    assert "3 extra in A" in result.summary_line()


def test_empty_buffers():
    both = compare_bytes(b"", b"")
    _assert_consistent(both, b"", b"")
    assert both.identical
    assert both.ranges == ()
    assert both.sha256_a == EMPTY_SHA
    assert both.sha256_b == EMPTY_SHA

    one = compare_bytes(b"", b"A")
    _assert_consistent(one, b"", b"A")
    assert not one.identical
    assert one.ranges[0].offset == 0
    assert one.ranges[0].bytes_a == b""
    assert one.ranges[0].bytes_b == b"A"

    other = compare_bytes(b"Z", b"")
    _assert_consistent(other, b"Z", b"")
    assert other.longer == "a"
    assert other.ranges[0].bytes_a == b"Z"
    assert other.ranges[0].bytes_b == b""


def test_bytearray_and_memoryview_match_bytes():
    raw = bytearray(b"\x00\x01\x02\x03")
    other = memoryview(b"\x00\xff\x02\x03")
    result = compare_bytes(raw, other)
    _assert_consistent(result, bytes(raw), bytes(other))
    assert result.ranges[0].offset == 1
    assert result.ranges[0].bytes_b == b"\xff"


def test_mismatch_on_a_chunk_boundary():
    size = 70_000
    data_a = bytes([0x11]) * size
    data_b = bytearray(data_a)
    for offset in (0, 1, 65535, 65536, 65537, size - 1):
        data_b[offset] = 0x22
    data_b = bytes(data_b)
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert [item.offset for item in result.ranges] == [0, 65535, size - 1]
    assert result.ranges[0].length == 2
    assert result.ranges[1].length == 3
    assert result.ranges[1].bytes_a == b"\x11\x11\x11"
    assert result.ranges[1].bytes_b == b"\x22\x22\x22"
    assert result.ranges[2].length == 1


def test_long_difference_then_equal_bytes():
    data_a = b"\x00" * 80_000
    data_b = bytearray(data_a)
    for index in range(70_000):
        data_b[index] = 0x01
    data_b = bytes(data_b)
    result = compare_bytes(data_a, data_b)
    _assert_consistent(result, data_a, data_b)
    assert len(result.ranges) == 1
    assert result.ranges[0].offset == 0
    assert result.ranges[0].length == 70_000


def test_a_few_megabytes_stay_fast():
    size = 2_000_000
    base = b"\x5A" * size
    other = bytearray(base)
    other[100] = 0
    other[size // 2] = 1
    other[size - 1] = 2
    started = time.perf_counter()
    same = compare_bytes(base, base)
    identical_elapsed = time.perf_counter() - started
    assert same.identical
    assert same.sha256_a == same.sha256_b
    started = time.perf_counter()
    result = compare_bytes(base, bytes(other))
    differ_elapsed = time.perf_counter() - started
    assert [item.offset for item in result.ranges] == [100, size // 2, size - 1]
    assert result.differing_bytes == 3
    assert identical_elapsed < 8, identical_elapsed
    assert differ_elapsed < 8, differ_elapsed


def test_reports_text_csv_and_cli_preview(tmp_path):
    data_a = b"\x10" * 4 + b"\x11" * 20
    data_b = b"\x10" * 4 + b"\x22" * 20
    result = compare_bytes(data_a, data_b)
    text = format_report(result, "a.bin", "b, c.bin", "text")
    assert text.startswith("Binary compare\n")
    assert "File A: a.bin" in text
    assert "Not identical." in text
    assert result.sha256_a in text
    assert text.count("11") >= 20
    # 20 bytes wrap onto a second hex line.
    assert "\n     11 11 11 11\n" in text
    assert text.endswith("\n")

    cli = format_cli_report(result, "a.bin", "b.bin", preview=16)
    assert "Not identical." in cli
    assert "(+4 more)" in cli
    assert "00000004  20 bytes" in cli

    table = format_report(result, "a, b.bin", "c.bin", "csv")
    rows = list(csv.reader(io.StringIO(table)))
    assert ["file_a", "a, b.bin"] in rows
    assert ["identical", "no"] in rows
    header = rows.index(["offset", "length", "hex_a", "hex_b"])
    assert rows[header + 1][0] == "4"
    assert rows[header + 1][1] == "20"
    assert rows[header + 1][2].count("11") == 20

    path = tmp_path / "diff.csv"
    write_report(path, result, "a.bin", "b.bin")
    saved = path.read_text(encoding="utf-8")
    assert "sha256_a" in saved
    text_path = tmp_path / "diff.txt"
    write_report(text_path, result, "a.bin", "b.bin", kind="text")
    assert "No differences." not in text_path.read_text(encoding="utf-8")
    assert "Binary compare" in text_path.read_text(encoding="utf-8")


def test_identical_report_says_no_differences():
    result = compare_bytes(b"\x00\x01", b"\x00\x01")
    text = format_report(result, "a.bin", "b.bin", "text")
    assert "Identical." in text
    assert "No differences." in text
    assert "(+0 more)" not in format_cli_report(result, "a", "b")


def test_row_cells_mark_the_changed_byte_and_line_up():
    data_b = b"\x00" * 5 + b"\xff" + b"\x00" * 10
    result = compare_bytes(b"\x00" * 16, data_b)
    index = DiffIndex(result.ranges)
    cells = row_cells(data_b, 0, 16, index, result.ranges[0])
    assert any(text == "FF " and kind == "current" for text, kind in cells)
    plain = row_cells(b"\x00" * 16, 0, 16, index, None)
    assert any(text == "00 " and kind == "diff" for text, kind in plain)
    assert "".join(text for text, _kind in cells).startswith("00000000  ")

    short = compare_bytes(b"\x00" * 4, b"\x00" * 2)
    short_index = DiffIndex(short.ranges)
    side_b = row_cells(b"\x00" * 2, 0, 4, short_index, short.ranges[0])
    side_a = row_cells(b"\x00" * 4, 0, 4, short_index, short.ranges[0])
    assert any(text == "-- " and kind == "current" for text, kind in side_b)
    assert len("".join(text for text, _kind in side_a)) == len("".join(text for text, _kind in side_b))
    header = column_header()
    sample = "".join(text for text, _kind in row_cells(bytes(range(16)), 0, 16, DiffIndex(()), None)).rstrip("\n")
    ascii_part = "." * 16
    assert header.index("ASCII") == sample.index(ascii_part)


def test_visible_rows_skip_clean_lines():
    ranges = compare_bytes(b"\x01" + b"\x00" * 47 + b"\x02", b"\x09" + b"\x00" * 47 + b"\x08").ranges
    rows = DiffIndex(ranges).differing_rows(48)
    assert rows == [0, 3]
    assert row_count(48) == 3
    assert row_count(49) == 4
    assert visible_rows(0, 10, 4, rows, only_diffs=True) == [0, 3]
    assert visible_rows(1, 10, 4, rows, only_diffs=False) == [1, 2, 3]
    assert format_range_line(ranges[0]).startswith("00000000")


def test_cli_identical_diff_report_and_errors(tmp_path):
    same_a = tmp_path / "same_a.bin"
    same_b = tmp_path / "same_b.bin"
    same_a.write_bytes(b"\x00\x01\x02")
    same_b.write_bytes(b"\x00\x01\x02")
    identical = _run(["compare", str(same_a), str(same_b)])
    assert identical.returncode == 0
    assert b"Identical." in identical.stdout
    assert b"Not identical." not in identical.stdout
    digest = hashlib.sha256(b"\x00\x01\x02").hexdigest().encode("ascii")
    assert digest in identical.stdout

    different = tmp_path / "different.bin"
    different.write_bytes(b"\x00\x09\x02\xff")
    report = tmp_path / "out.txt"
    changed = _run(["compare", "--report", str(report), str(same_a), str(different)])
    assert changed.returncode == 1
    assert b"Not identical." in changed.stdout
    assert b"Size mismatch" in changed.stdout
    assert b"(+" not in changed.stdout or b"00000001" in changed.stdout
    body = report.read_text(encoding="utf-8")
    assert "Not identical." in body
    assert "FF" in body
    assert b"Wrote" in changed.stderr

    csv_report = tmp_path / "out.csv"
    csv_run = _run(["compare", str(same_a), str(different), "--report", str(csv_report)])
    assert csv_run.returncode == 1
    csv_body = csv_report.read_text(encoding="utf-8")
    assert "hex_a" in csv_body
    assert "size_mismatch" in csv_body

    missing = _run(["compare", str(tmp_path / "nope.bin"), str(same_b)])
    assert missing.returncode == 2
    assert b"Cannot read" in missing.stderr

    usage = _run(["compare"])
    assert usage.returncode == 2

    version = _run(["--version"])
    assert version.returncode == 0
    assert b"dflash-to-eeprom" in version.stdout


def test_spanish_summary_keeps_sha256_offset_and_hex(tmp_path):
    from dflash_eee.i18n import set_language

    set_language("es")
    result = compare_bytes(b"\x00\x01", b"\x00\x02")
    line = result.summary_line()
    assert line.startswith("No son idénticos.")
    assert "1 byte distinto en 1 rango" in line
    assert "SHA-256" in result.summary_text()
    text = format_report(result, "a.bin", "b.bin", "text")
    assert text.startswith("Comparación binaria\n")
    assert "Archivo A: a.bin" in text
    assert "Offset    Longitud" in text
    assert "No hay diferencias." not in text
    same = compare_bytes(b"\x11", b"\x11")
    assert same.summary_line().startswith("Idénticos.")
    assert "No hay diferencias." in format_report(same, "a", "b", "text")
    longer = compare_bytes(b"\x00\x01\x02\x03", b"\x00\x09")
    assert "Los tamaños no coinciden" in longer.summary_line()
    assert "de más en A" in longer.summary_line()
    cli = format_cli_report(result, "a.bin", "b.bin")
    assert "No son idénticos." in cli
    assert "SHA-256" in cli
    preview = format_cli_report(
        compare_bytes(b"\x10" * 4 + b"\x11" * 20, b"\x10" * 4 + b"\x22" * 20),
        "a.bin",
        "b.bin",
        preview=16,
    )
    assert "(+4 más)" in preview
    assert "00000004" in preview
    set_language("en")

    left = tmp_path / "a.bin"
    right = tmp_path / "b.bin"
    left.write_bytes(b"\x00")
    right.write_bytes(b"\x01\x02")
    env = os.environ.copy()
    env["LANG"] = "C"
    env["LC_ALL"] = "C"
    env["PYTHONIOENCODING"] = "utf-8"
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "--lang", "es", "compare", str(left), str(right)],
        cwd=str(ROOT),
        capture_output=True,
        env=env,
    )
    assert run.returncode == 1
    stdout = run.stdout.decode("latin-1")
    assert "No son idénticos." in stdout
    assert "SHA-256" in stdout
    assert "Los tamaños no coinciden" in stdout
    assert "de más en B" in stdout
    after = subprocess.run(
        [sys.executable, str(SCRIPT), "compare", "--lang", "es", str(left), str(right)],
        cwd=str(ROOT),
        capture_output=True,
        env=env,
    )
    assert after.returncode == 1
    assert "No son idénticos." in after.stdout.decode("latin-1")
    assert b"SHA-256" in after.stdout


def test_two_argument_compare_is_still_a_filename(tmp_path):
    source = tmp_path / "compare"
    source.write_bytes(GOOD.read_bytes())
    out = tmp_path / "out.bin"
    run = subprocess.run(
        [sys.executable, str(SCRIPT), "compare", "out.bin"],
        cwd=str(tmp_path),
        capture_output=True,
    )
    assert run.returncode == 0
    assert out.stat().st_size == 4096
    assert "No son idénticos".encode("utf-8") not in run.stdout
    assert b"Not identical" not in run.stdout
