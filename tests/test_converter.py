# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Synthetic journal vectors. These do not depend on a real vehicle dump."""

import struct
from pathlib import Path

from dflash_eee.converter import DFlashConverter, convert_bytes
from dflash_eee.profiles import get_profile
from dflash_eee.report import report_lines

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "ci" / "input" / "dflash" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"
SAMPLE_EEE = ROOT / "ci" / "input" / "eee" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"


def _erased():
    return b"\xFF" * 256


def _ready():
    data = bytearray(_erased())
    data[2:4] = b"\xFF\xFE"
    return bytes(data)


def _sector(records):
    data = bytearray(_erased())
    data[0:2] = b"\xFA\xCF"
    data[2:4] = b"\x00\x01"
    for index, (addr, value) in enumerate(records):
        off = 4 + index * 4
        data[off:off + 2] = struct.pack(">H", 0xB800 | (addr & 0x7FF))
        data[off + 2:off + 4] = struct.pack(">H", value & 0xFFFF)
    return bytes(data)


def _dump(sectors):
    assert len(sectors) == 128
    return b"".join(sectors)


def _word(image, addr):
    return (image[addr * 2] << 8) | image[addr * 2 + 1]


def test_bytes_api_matches_golden_file():
    result = convert_bytes(SAMPLE.read_bytes(), get_profile("bmw_frm3"))
    assert result.ok
    assert result.image == SAMPLE_EEE.read_bytes()
    fields = dict(result.fields)
    assert fields["VIN"] == "83IM9D60QABCUS9S4"
    assert fields["Production date"] == "22.07.2010"
    assert fields["HW-NR"].startswith("9206233")


def test_ready_sectors_win_and_later_record_overwrites():
    sectors = [_erased() for _ in range(128)]
    sectors[0] = _sector([(0x0010, 0xABCD)])
    sectors[1] = _sector([(0x0010, 0x1234), (0x0011, 0x5678)])
    sectors[2] = _ready()
    sectors[3] = _ready()
    sectors[4] = _ready()
    result = convert_bytes(_dump(sectors))
    assert result.ok
    assert result.endblock == 1
    assert result.endblock_reason == "new_blocks"
    assert _word(result.image, 0x0010) == 0x1234
    assert _word(result.image, 0x0011) == 0x5678
    assert _word(result.image, 0x0001) == 0xFFFF
    assert len(result.image) == 4096


def test_empty_marker_does_not_stop_the_sector():
    # A programmed record placed after the empty marker is still applied.
    # The original decoder behaves this way and the samples depend on it.
    data = bytearray(_erased())
    data[0:4] = b"\xFA\xCF\x00\x01"
    data[4:8] = struct.pack(">HH", 0xB800 | 0x20, 0x1111)
    data[8:12] = struct.pack(">HH", 0xF800, 0xFFFF)
    data[12:16] = struct.pack(">HH", 0xB800 | 0x21, 0x2222)
    sectors = [_erased() for _ in range(128)]
    sectors[0] = bytes(data)
    sectors[1] = _ready()
    result = convert_bytes(_dump(sectors))
    assert result.endblock_reason == "new_blocks"
    assert _word(result.image, 0x20) == 0x1111
    assert _word(result.image, 0x21) == 0x2222
    assert result.stats["last"] == 1


def test_unknown_command_is_an_error_and_still_builds_an_image():
    data = bytearray(_sector([(0x0001, 0x00AA)]))
    data[8:12] = struct.pack(">HH", 0x0000, 0x1234)
    sectors = [_erased() for _ in range(128)]
    sectors[3] = bytes(data)
    sectors[4] = _ready()
    result = convert_bytes(_dump(sectors))
    assert result.image is not None
    assert any("Unknown CMD" in issue.text for issue in result.errors)
    assert result.corrupt
    assert not result.ok
    assert _word(result.image, 0x0001) == 0x00AA


def test_short_dump_is_a_clear_error():
    result = convert_bytes(b"\xFF" * 512)
    assert result.image is None
    assert result.errors
    assert "512" in result.errors[0].text
    assert "32768" in result.errors[0].text


def test_unrecognized_layout():
    result = convert_bytes(b"\x00" * 32768)
    assert result.image is not None
    assert any("Unrecognized" in issue.text for issue in result.errors)
    assert result.image == b"\xFF" * 4096


def test_all_ready_sectors_are_rejected():
    blob = _dump([_ready() for _ in range(128)])
    converter = DFlashConverter()
    try:
        converter._read_bytes(blob)
        converter._find_endblock()
        raised = False
    except Exception as exc:
        raised = True
        assert str(exc) == "All blocks seem to be new blocks"
    assert raised


def test_converter_can_be_reused():
    sectors_a = [_erased() for _ in range(128)]
    sectors_a[0] = _sector([(0x0003, 0x0001)])
    sectors_a[1] = _ready()
    sectors_b = [_erased() for _ in range(128)]
    sectors_b[0] = _sector([(0x0003, 0x0002)])
    sectors_b[1] = _ready()
    converter = DFlashConverter()
    converter._read_bytes(_dump(sectors_a))
    converter._find_endblock()
    converter._build_image()
    assert _word(converter._image_bytes(), 0x0003) == 0x0001
    converter._read_bytes(_dump(sectors_b))
    converter._find_endblock()
    converter._build_image()
    assert _word(converter._image_bytes(), 0x0003) == 0x0002


def test_two_kilobyte_profile_rejects_a_four_kilobyte_span():
    # An FRM3 journal uses word addresses from the bottom of the 4 KB buffer.
    # The 2 KB profile keeps only the top half, so the low records are an error
    # and the message names the 4 KB profile instead of saving a blank file.
    frm = convert_bytes(SAMPLE.read_bytes(), get_profile("jlr_xf_bcm"))
    assert frm.image is not None
    assert len(frm.image) == 2048
    assert frm.stats["out_of_range"] > 0
    assert frm.stats["landed"] > 0
    assert not frm.ok
    text = " ".join(issue.text for issue in frm.errors)
    assert "FRM3" in text
    assert "4096" in text


def test_two_kilobyte_window_is_the_top_of_the_four_kilobyte_buffer():
    # Addresses in the real 2 KB journals sit at words 0x400 and above.
    # 0x400 is byte 0x800 of the 4 KB buffer, the first byte of ERPART 8.
    sectors = [_erased() for _ in range(128)]
    sectors[0] = _sector([
        (0x0400, 0x1111),
        (0x0490, 0x00FF),
        (0x0498, 0x2222),
        (0x0499, 0x3333),
        (0x078A, 0x4444),
        (0x0010, 0xABCD),
    ])
    sectors[1] = _sector([(0x0498, 0x5555)])
    sectors[2] = _ready()
    blob = _dump(sectors)
    wide = convert_bytes(blob, get_profile("bmw_frm3"))
    narrow = convert_bytes(blob, get_profile("jlr_xf_bcm"))
    assert wide.ok
    # The low word is outside ERPART 8, so the 2 KB conversion is not clean.
    assert not narrow.ok
    assert narrow.stats["out_of_range"] == 1
    assert narrow.image == wide.image[2048:]
    assert _word(narrow.image, 0x0000) == 0x1111
    assert _word(narrow.image, 0x0090) == 0x00FF
    assert _word(narrow.image, 0x0098) == 0x5555
    assert _word(narrow.image, 0x0099) == 0x3333
    assert _word(narrow.image, 0x038A) == 0x4444
    assert _word(wide.image, 0x0010) == 0xABCD


def test_records_only_in_the_top_window_match_the_upper_half():
    sectors = [_erased() for _ in range(128)]
    sectors[0] = _sector([(0x0400, 0x1111), (0x0498, 0x2222), (0x07FF, 0x3333)])
    sectors[1] = _ready()
    blob = _dump(sectors)
    wide = convert_bytes(blob, get_profile("bmw_frm3"))
    narrow = convert_bytes(blob, get_profile("jlr_xf_bcm"))
    assert wide.ok and narrow.ok
    assert wide.image[:2048] == b"\xFF" * 2048
    assert narrow.image == wide.image[2048:]
    assert narrow.stats["out_of_range"] == 0
    assert _word(narrow.image, 0) == 0x1111
    assert _word(narrow.image, 0x03FF) == 0x3333


def test_low_addresses_on_a_two_kilobyte_profile_do_not_save_a_blank_image():
    sectors = [_erased() for _ in range(128)]
    sectors[0] = _sector([(0x0010, 0xABCD), (0x03FF, 0x0A0A)])
    sectors[1] = _ready()
    result = convert_bytes(_dump(sectors), get_profile("jlr_xf_bcm"))
    assert result.image is None
    assert not result.ok
    assert result.stats["landed"] == 0
    text = " ".join(issue.text for issue in result.errors)
    assert "no EEPROM image" in text
    assert "FRM3" in text
    assert "4096" in text
    shown = " ".join(line for _level, line in report_lines(result))
    assert shown.count("outside this profile's EEE window") == 1
    assert "FRM3" in shown


def test_longer_dump_warns_and_ignores_the_tail():
    base = SAMPLE.read_bytes()
    result = convert_bytes(base + b"\x00" * 16384)
    assert result.image == SAMPLE_EEE.read_bytes()
    assert any(issue.level == "warning" and "49152" in issue.text for issue in result.issues)
