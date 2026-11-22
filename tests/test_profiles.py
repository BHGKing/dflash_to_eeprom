# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

import json

import pytest

from dflash_eee.converter import convert_bytes
from dflash_eee.profiles import (
    BUILTIN,
    RECORDS_PER_SECTOR,
    discover_profiles,
    eee_word_base,
    get_profile,
    partition_is_valid,
    profile_from_json,
    smallest_erpart,
)
from dflash_eee.report import hex_dump, parse_drop_paths, shorten_middle, size_issue


def test_builtin_statuses_and_geometry():
    by_id = {profile.id: profile for profile in BUILTIN}
    assert by_id["bmw_frm3"].status == "supported"
    assert by_id["bmw_frm3"].eee_size == 4096
    assert by_id["bmw_frm3"].dfpart_sectors == 0
    assert by_id["bmw_frm3"].erpart_pages == 16
    assert by_id["jlr_xf_bcm"].status == "supported"
    assert by_id["jlr_xf_bcm"].eee_size == 2048
    assert by_id["jlr_xf_bcm"].erpart_pages == 8
    assert by_id["bmw_frm3"].eee_word_base == 0
    assert by_id["jlr_xf_bcm"].eee_word_base == 1024
    assert by_id["jlr_kvm"].eee_word_base == 1024
    assert eee_word_base(1) == 1920
    assert eee_word_base(16) == 0
    assert smallest_erpart(1024, 1930) == 8
    assert smallest_erpart(0, 2042) == 16
    assert smallest_erpart(1920, 2047) == 1
    assert by_id["jlr_kvm"].status == "experimental"
    assert by_id["jlr_kvm"].assumptions
    assert by_id["bmw_frm_f_xet512"].status == "experimental"
    assert by_id["bmw_frm_f_xet512"].fields == ()
    assert by_id["bmw_cas4"].status == "unsupported"
    assert by_id["bmw_cas4"].conversion_enabled is False
    for profile in BUILTIN:
        assert profile.sources
        if profile.status == "supported":
            assert profile.assumptions == ""
            assert profile.partition_confirmed
        if profile.conversion_enabled:
            assert profile.layout.records_per_sector == RECORDS_PER_SECTOR == 63


def test_partition_rule_from_an3490():
    assert partition_is_valid(0, 16)
    assert partition_is_valid(0, 8)
    assert partition_is_valid(16, 8)
    assert partition_is_valid(128, 0)
    assert not partition_is_valid(127, 1)
    assert not partition_is_valid(0, 17)
    assert partition_is_valid(64, 4)  # (128-64)/4 = 16 >= 8
    assert not partition_is_valid(80, 8)  # 48/8 = 6 < 8


def test_cas4_refuses_to_convert():
    result = convert_bytes(b"\xFF" * 32768, get_profile("bmw_cas4"))
    assert result.image is None
    assert result.errors
    assert "no EEE" in result.errors[0].text or "CAS4" in result.errors[0].text


def test_unknown_profile():
    with pytest.raises(Exception) as caught:
        get_profile("not_a_module")
    assert "Unknown profile" in str(caught.value)


def test_drop_in_supported_is_downgraded(tmp_path):
    payload = {
        "id": "shop_special",
        "title": "Shop special",
        "manufacturer": "BMW",
        "status": "supported",
        "mcu": "MC9S12XEQ384",
        "vehicles": "A module the file claims is supported.",
        "dflash_size": 32768,
        "eee_size": 4096,
        "dfpart_sectors": 0,
        "erpart_pages": 16,
        "summary": "Drop-in copy of the family geometry.",
        "write_notes": "Compare with a known-good module.",
        "sources": ["Local bench notes, not reviewed here."],
    }
    path = tmp_path / "shop_special.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = profile_from_json(path)
    assert loaded.status == "experimental"
    assert loaded.external
    assert "experimental" in loaded.assumptions
    profiles, errors = discover_profiles([tmp_path])
    assert not errors
    assert any(profile.id == "shop_special" for profile in profiles)


def test_drop_in_collision_is_reported(tmp_path):
    payload = {
        "id": "bmw_frm3",
        "title": "Fake FRM",
        "manufacturer": "BMW",
        "status": "experimental",
        "mcu": "MC9S12XEQ384",
        "vehicles": "n/a",
        "dflash_size": 32768,
        "eee_size": 4096,
        "dfpart_sectors": 0,
        "erpart_pages": 16,
        "summary": "Should not replace the built-in.",
        "write_notes": "n/a",
        "assumptions": "This file must not override FRM3.",
        "sources": ["none that should win"],
    }
    (tmp_path / "override.json").write_text(json.dumps(payload), encoding="utf-8")
    profiles, errors = discover_profiles([tmp_path])
    assert errors
    assert sum(1 for profile in profiles if profile.id == "bmw_frm3") == 1
    assert get_profile("bmw_frm3", profiles).external is False


def test_size_issue_and_hex_and_drop_parser():
    frm = get_profile("bmw_frm3")
    assert size_issue(32768, frm) is None
    short = size_issue(512, frm)
    assert short.level == "error" and "Wrong size" in short.text
    long = size_issue(49152, frm)
    assert long.level == "warning"
    dumped = hex_dump(bytes(range(32)))
    assert dumped.startswith("0000  00 01 02 03")
    assert "10 11 12 13" in dumped
    paths = parse_drop_paths("{/tmp/my dump.bin} file:///tmp/other.bin")
    assert paths[0] == "/tmp/my dump.bin"
    assert paths[1] == "/tmp/other.bin"
    windows = parse_drop_paths("file:///C:/Dumps/frm.bin")
    assert windows == ["C:/Dumps/frm.bin"]
    assert shorten_middle("4208ada9b94692937991b4d801b67b54c03ba4da.bin").startswith("4208ada9")
    assert shorten_middle("4208ada9b94692937991b4d801b67b54c03ba4da.bin").endswith("c03ba4da.bin")
    assert "..." in shorten_middle("4208ada9b94692937991b4d801b67b54c03ba4da.bin")
