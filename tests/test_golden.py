# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""The ci/ sample pairs are the FRM3 regression set from the original tool."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DFLASH = ROOT / "ci" / "input" / "dflash"
EEE = ROOT / "ci" / "input" / "eee"
LOGS = ROOT / "ci" / "input" / "logs"
SCRIPT = ROOT / "dflash_to_eee.py"


def _run(source: Path, destination: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(destination)],
        cwd=str(ROOT),
        capture_output=True,
    )


def _log_lines(blob: bytes) -> list[bytes]:
    """Drop traceback frames. The historical logs were produced by Python 2."""

    kept = []
    for line in blob.splitlines():
        if line.startswith(b"Traceback"):
            continue
        if line.startswith(b"  File "):
            continue
        if line.startswith(b"    "):
            continue
        kept.append(line)
    return kept


def test_golden_eeprom_bytes(tmp_path):
    missing = []
    for source in sorted(DFLASH.glob("*.bin")):
        expected = EEE / source.name
        if not expected.exists():
            missing.append(source.name)
            continue
        destination = tmp_path / source.name
        result = _run(source, destination)
        assert result.returncode == 0, (source.name, result.stderr)
        assert destination.read_bytes() == expected.read_bytes(), source.name
    # The original set has a few dumps that abort before an image is written.
    assert missing, "expected some dumps without a golden image"


def test_golden_log_messages(tmp_path):
    for source in sorted(DFLASH.glob("*.bin")):
        golden = (LOGS / source.name).read_bytes()
        destination = tmp_path / ("eee-" + source.name)
        result = _run(source, destination)
        got = _log_lines(result.stdout + result.stderr)
        expect = _log_lines(golden)
        assert got == expect, (source.name, got, expect)
        if b"Exception: Input file too short" in golden:
            assert result.returncode != 0
        else:
            assert result.returncode == 0
