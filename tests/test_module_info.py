# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""VIN, key slots, virginize and journal packing. Fixtures are synthetic."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from dflash_eee.converter import convert_bytes
from dflash_eee.decode import (
    decode_image,
    format_module_info,
    is_valid_vin,
    module_info_json,
    vin_check_digit,
)
from dflash_eee.journal import encode_journal
from dflash_eee.profiles import (
    ChecksumSpec,
    KeyLayout,
    RegionField,
    get_profile,
    profile_from_json,
)
from dflash_eee.virginize import output_path_problem, virginize_image

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dflash_to_eee.py"


def _vin(text16_with_placeholder: str) -> str:
    assert len(text16_with_placeholder) == 17
    digit = vin_check_digit(text16_with_placeholder)
    assert digit is not None
    return text16_with_placeholder[:8] + digit + text16_with_placeholder[9:]


SAJ = _vin("SAJAA000012345678")
SAL = _vin("SALBB111087654321")
WBA = _vin("WBA0AA00012345678")


def _run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
    )


def test_check_digit_ignores_the_ninth_character_and_rejects_ioq():
    assert vin_check_digit(SAJ) == SAJ[8]
    assert is_valid_vin(SAJ, ("SAJ", "SAL"))
    assert not is_valid_vin(SAJ, ("WBA",))
    assert not is_valid_vin("SAJI" + SAJ[4:], ("SAJ",))
    assert vin_check_digit("83IM9D60QABCUS9S4") is None


def test_jaguar_scan_reports_agreement_and_does_not_invent_keys():
    image = bytearray(b"\xFF" * 2048)
    image[40:57] = SAJ.encode("ascii")
    image[200:217] = SAJ.encode("ascii")
    image[300:314] = b"CX23-14D628-AE"
    image[320:334] = b"DX23-14B476-BG"
    image[80:97] = b"WBAINVALIDVIN0000"
    info = decode_image(get_profile("jlr_xf_bcm"), bytes(image))
    assert [copy.value for copy in info.vins] == [SAJ, SAJ]
    assert {copy.confidence for copy in info.vins} == {"detected"}
    assert info.vin_agreement == "agree"
    assert [part.value for part in info.parts] == ["CX23-14D628-AE", "DX23-14B476-BG"]
    assert info.keys_status == "no_map"
    assert info.slots == []
    text = format_module_info(info)
    assert "Detected" in text
    assert "No key-slot map" in text
    assert "Not in the published map" in text
    payload = module_info_json(info)
    assert payload["mileage"]["status"] == "not_in_map"
    assert payload["coding"]["status"] == "not_in_map"
    assert payload["keys"]["status"] == "no_map"

    image[400:417] = SAL.encode("ascii")
    split = decode_image(get_profile("jlr_xf_bcm"), bytes(image))
    assert split.vin_agreement == "disagree"
    assert {copy.value for copy in split.vins} == {SAJ, SAL}


def test_frm3_keeps_the_confirmed_offset_and_flags_a_bad_check_digit():
    image = bytearray(b"\xFF" * 4096)
    image[0xFD3:0xFD3 + 17] = b"83IM9D60QABCUS9S4"
    image[0x20:0x20 + 17] = WBA.encode("ascii")
    info = decode_image(get_profile("bmw_frm3"), bytes(image))
    vin_field = next(item for item in info.fields if item.label == "VIN")
    assert vin_field.confidence == "confirmed"
    assert vin_field.offset == 0xFD3
    assert "not a valid VIN" in vin_field.note
    assert [copy.value for copy in info.vins] == [WBA]
    assert info.vins[0].confidence == "detected"
    assert info.keys_status == "not_applicable"
    assert any(item.label == "HW-NR" and item.confidence == "confirmed" for item in info.fields)
    placed = bytearray(b"\xFF" * 4096)
    placed[0xFD3:0xFD3 + 17] = WBA.encode("ascii")
    confirmed = decode_image(get_profile("bmw_frm3"), bytes(placed))
    assert confirmed.vins[0].offset == 0xFD3
    assert confirmed.vins[0].confidence == "confirmed"
    assert confirmed.vin_agreement == "agree"


def test_jaguar_virginize_clears_only_valid_vins():
    image = bytearray(b"\x11" * 2048)
    image[40:57] = SAJ.encode("ascii")
    image[80:97] = b"SAJNOTAVALIDVIN!!"
    image[300:314] = b"CX23-14D628-AE"
    profile = get_profile("jlr_xf_bcm")
    plan = virginize_image(profile, bytes(image))
    assert plan.ready
    assert plan.image is not None
    assert plan.image[40:57] == b"\xFF" * 17
    assert plan.image[80:97] == b"SAJNOTAVALIDVIN!!"
    assert plan.image[300:314] == b"CX23-14D628-AE"
    assert plan.image[:40] == bytes(image[:40])
    assert all(change.reason != "recompute declared checksum" for change in plan.changes)
    text = "\n".join(plan.notes)
    assert "No declared checksum" in text
    assert "SDD" in text
    untouched = bytearray(b"\x11" * 2048)
    empty = virginize_image(profile, bytes(untouched))
    assert not empty.ready
    assert empty.image is None


def test_frm3_virginize_is_refused():
    plan = virginize_image(get_profile("bmw_frm3"), b"\xFF" * 4096)
    assert not plan.supported
    assert plan.image is None
    assert "not implemented" in plan.reason
    kvm = virginize_image(get_profile("jlr_kvm"), b"\xFF" * 2048)
    assert not kvm.supported


def test_declared_checksum_is_recomputed_and_keys_need_one():
    base = get_profile("jlr_xf_bcm")
    region = RegionField(
        "VIN",
        0,
        17,
        "ascii",
        confidence="confirmed",
        checksum=ChecksumSpec("sum8", offset=17, start=0, length=17),
        clear_on_virgin=True,
    )
    profile = replace(
        base,
        id="synth_region",
        scan_vin=False,
        regions=(region,),
        virginize_mode="clear_vin",
    )
    image = bytearray(b"\x00" * 2048)
    image[0:17] = SAJ.encode("ascii")
    image[17] = 0x00
    plan = virginize_image(profile, bytes(image))
    assert plan.ready and plan.image is not None
    assert plan.image[0:17] == b"\xFF" * 17
    assert plan.image[17] == (0xFF * 17) & 0xFF
    assert any("checksum" in change.reason for change in plan.changes)

    layout = KeyLayout(
        base=0x100,
        count=2,
        stride=8,
        id_offset=0,
        id_length=4,
        blank=b"\xFF" * 4,
        checksum=ChecksumSpec("xor8", offset=4, start=0, length=4, relative=True),
        confidence="detected",
    )
    keyed = replace(
        base,
        id="synth_keys",
        scan_vin=False,
        keys_status="layout",
        key_layout=layout,
        virginize_mode="clear_vin_and_keys",
    )
    slot = bytearray(b"\xFF" * 2048)
    slot[0x100:0x104] = b"\x01\x02\x03\x04"
    slot[0x104] = 0x00
    info = decode_image(keyed, bytes(slot))
    assert [item.state for item in info.slots] == ["used", "blank"]
    assert info.slots[0].ident == b"\x01\x02\x03\x04"
    assert info.keys_confidence == "detected"
    wiped = virginize_image(keyed, bytes(slot))
    assert wiped.ready and wiped.image is not None
    assert wiped.image[0x100:0x104] == b"\xFF" * 4
    assert wiped.image[0x104] == 0x00
    assert wiped.image[0x108:0x10C] == b"\xFF" * 4


def test_drop_in_confirmed_region_is_downgraded(tmp_path):
    payload = {
        "id": "shop_vin",
        "title": "Shop VIN",
        "manufacturer": "Jaguar",
        "status": "experimental",
        "mcu": "MC9S12XET256",
        "vehicles": "Bench only.",
        "dflash_size": 32768,
        "eee_size": 2048,
        "dfpart_sectors": 0,
        "erpart_pages": 8,
        "summary": "Synthetic drop-in.",
        "write_notes": "Do not program this.",
        "assumptions": "Offsets are a guess.",
        "sources": ["This test."],
        "scan_vin": True,
        "vin_wmis": ["saj"],
        "regions": [{
            "label": "VIN",
            "offset": 0,
            "length": 17,
            "encoding": "ascii",
            "confidence": "confirmed",
            "clear_on_virgin": True,
            "checksum": {"algorithm": "sum8", "offset": 17, "start": 0, "length": 17},
        }],
        "keys_status": "layout",
        "key_layout": {
            "base": 256,
            "count": 1,
            "stride": 8,
            "id_offset": 0,
            "id_length": 4,
            "blank": "FFFFFFFF",
            "confidence": "confirmed",
            "checksum": {
                "algorithm": "sum8",
                "offset": 4,
                "start": 0,
                "length": 4,
                "relative": True,
            },
        },
        "virginize": "clear_vin_and_keys",
    }
    path = tmp_path / "shop_vin.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = profile_from_json(path)
    assert loaded.regions[0].confidence == "detected"
    assert loaded.key_layout is not None
    assert loaded.key_layout.confidence == "detected"
    assert loaded.vin_wmis == ("SAJ",)
    assert loaded.virginize_mode == "clear_vin_and_keys"
    broken = dict(payload)
    broken["id"] = "shop_vin_bad"
    broken["virginize"] = "clear_vin_and_keys"
    broken["key_layout"] = None
    broken["keys_status"] = "no_map"
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(Exception) as caught:
        profile_from_json(bad_path)
    assert "checksum" in str(caught.value).lower() or "layout" in str(caught.value).lower()


def test_journal_round_trip_for_both_supported_sizes():
    for profile_id, size in (("bmw_frm3", 4096), ("jlr_xf_bcm", 2048)):
        profile = get_profile(profile_id)
        image = bytearray(size)
        for offset in range(size):
            image[offset] = (offset * 3 + 9) & 0xFF
        image[10] = 0xFF
        image[11] = 0xFF
        packed = encode_journal(bytes(image), profile)
        assert len(packed) == profile.dflash_size
        result = convert_bytes(packed, profile, check_size=True)
        assert result.image == bytes(image)
        assert not result.errors


def test_cli_info_keys_and_virginize(tmp_path):
    image = bytearray(b"\xFF" * 2048)
    image[40:57] = SAJ.encode("ascii")
    image[200:217] = SAL.encode("ascii")
    image[300:314] = b"CX23-14D628-AE"
    source = tmp_path / "bcm.bin"
    source.write_bytes(bytes(image))
    info = _run(["--lang", "en", "info", str(source), "--profile", "jlr_xf_bcm", "--json"])
    assert info.returncode == 0
    payload = json.loads(info.stdout.decode("latin-1"))
    assert payload["vin"]["agreement"] == "disagree"
    assert {item["value"] for item in payload["vin"]["copies"]} == {SAJ, SAL}
    assert payload["source"] == "eeprom"
    keys = _run(["keys", "--profile", "jlr_xf_bcm", str(source)])
    assert keys.returncode == 0
    assert b"No key-slot map" in keys.stdout

    output = tmp_path / "virgin.bin"
    report = tmp_path / "changes.txt"
    journal = tmp_path / "virgin.dflash"
    done = _run([
        "virginize", "--profile", "jlr_xf_bcm",
        str(source), str(output),
        "--report", str(report),
        "--journal", str(journal),
    ])
    assert done.returncode == 0, done.stderr.decode("latin-1", errors="replace")
    assert source.read_bytes() == bytes(image)
    produced = output.read_bytes()
    assert produced[40:57] == b"\xFF" * 17
    assert produced[200:217] == b"\xFF" * 17
    assert produced[300:314] == b"CX23-14D628-AE"
    report_text = report.read_text(encoding="utf-8")
    assert "0x0028" in report_text
    assert "0x00C8" in report_text
    assert "Not identical" in report_text
    assert b"CX23-14D628-AE" in produced
    replayed = convert_bytes(journal.read_bytes(), get_profile("jlr_xf_bcm"))
    assert replayed.image == produced

    same = _run(["virginize", "--profile", "jlr_xf_bcm", str(source), str(source)])
    assert same.returncode == 1
    assert source.read_bytes() == bytes(image)
    blocked = _run(["virginize", "--profile", "jlr_xf_bcm", str(source), str(output)])
    assert blocked.returncode == 1
    assert output.read_bytes() == produced

    frm = tmp_path / "frm.bin"
    frm.write_bytes(b"\xFF" * 4096)
    frm_out = tmp_path / "frm_virgin.bin"
    refused = _run(["virginize", "--profile", "bmw_frm3", str(frm), str(frm_out)])
    assert refused.returncode == 1
    assert not frm_out.exists()
    assert b"not implemented" in refused.stdout


def test_output_path_problem(tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"abc")
    assert output_path_problem(source, source) == "same"
    other = tmp_path / "b.bin"
    assert output_path_problem(source, other) is None
    other.write_bytes(b"x")
    assert output_path_problem(source, other) == "exists"


def test_compare_out_bin_stays_a_historical_conversion(tmp_path, monkeypatch):
    """``compare out.bin`` converts a file named compare. It is not the compare command."""

    monkeypatch.chdir(tmp_path)
    (tmp_path / "compare").write_bytes(b"\xFF" * 32)
    from dflash_eee.cli import main

    with pytest.raises(Exception) as caught:
        main(["compare", "out.bin"])
    assert "Input file too short" in str(caught.value)
