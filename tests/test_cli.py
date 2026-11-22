# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dflash_to_eee.py"
GOOD = ROOT / "ci" / "input" / "dflash" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"
GOOD_EEE = ROOT / "ci" / "input" / "eee" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"
LONG = ROOT / "ci" / "input" / "dflash" / "f1f3a6a6e23fa01d7d2ddd2614913b0d3adfba6d.bin"
ZERO = ROOT / "ci" / "input" / "dflash" / "5188431849b4613152fd7bdba6a3ff0a4fd6424b.bin"


def _run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(ROOT),
        capture_output=True,
    )


def test_list_profiles_names_every_builtin():
    result = _run(["--list-profiles"])
    text = result.stdout.decode("latin-1")
    assert result.returncode == 0
    for profile_id in ("bmw_frm3", "bmw_frm_f_xet512", "jlr_xf_bcm", "jlr_kvm", "bmw_cas4"):
        assert profile_id in text
    assert "Supported" in text
    assert "Experimental" in text
    assert "Not supported" in text


def test_profile_flag_matches_legacy_bytes(tmp_path):
    destination = tmp_path / "out.bin"
    result = _run(["--profile", "bmw_frm3", str(GOOD), str(destination)])
    assert result.returncode == 0
    assert destination.read_bytes() == GOOD_EEE.read_bytes()
    assert b"VIN: 83IM9D60QABCUS9S4" in result.stderr


def test_strict_accepts_a_clean_dump_and_rejects_size_and_corrupt(tmp_path):
    clean = tmp_path / "clean.bin"
    assert _run(["--strict", "--profile", "bmw_frm3", str(GOOD), str(clean)]).returncode == 0
    long = tmp_path / "long.bin"
    assert _run(["--strict", "--profile", "bmw_frm3", str(LONG), str(long)]).returncode == 2
    # The historical two-argument form still accepts the longer dump.
    legacy = tmp_path / "legacy.bin"
    assert _run([str(LONG), str(legacy)]).returncode == 0
    assert legacy.stat().st_size == 4096
    bad = tmp_path / "bad.bin"
    assert _run(["--strict", "--profile", "bmw_frm3", str(ZERO), str(bad)]).returncode == 2


def test_cas4_profile_does_not_write(tmp_path):
    destination = tmp_path / "nope.bin"
    result = _run(["--profile", "bmw_cas4", str(GOOD), str(destination)])
    assert result.returncode == 1
    assert not destination.exists()
    assert b"CAS4" in result.stderr


def test_usage_error():
    result = _run(["--profile", "bmw_frm3"])
    assert result.returncode == 1
