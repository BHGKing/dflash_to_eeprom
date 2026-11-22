# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Drive the desktop window without a mouse. Skipped when Tk cannot open."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOOD = ROOT / "ci" / "input" / "dflash" / "4208ada9b94692937991b4d801b67b54c03ba4da.bin"
SHORT = ROOT / "ci" / "input" / "dflash" / "5c3eb80066420002bc3dcc7ca4ab6efad7ed4ae5.bin"
ZERO = ROOT / "ci" / "input" / "dflash" / "5188431849b4613152fd7bdba6a3ff0a4fd6424b.bin"


@pytest.fixture
def app():
    # Windows has a desktop session and does not use DISPLAY. Elsewhere, skip
    # when there is no X server, and also when Tk itself refuses to open.
    if sys.platform != "win32" and not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    tk = pytest.importorskip("tkinter")
    from gui import App

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip("Tk cannot open a window: %s" % exc)
    root.withdraw()
    window = App(root)
    yield window
    root.destroy()


def _report(window) -> str:
    return window.report.get("1.0", "end")


def test_gui_converts_frm3_and_previews_vin(app, tmp_path):
    app.load_path(str(GOOD))
    app.convert()
    assert app.result.ok
    assert len(app.result.image) == 4096
    text = _report(app)
    assert "Conversion succeeded" in text
    assert "83IM9D60QABCUS9S4" in text
    hex_text = app.hex_view.get("1.0", "end")
    assert hex_text.startswith("0000")
    destination = tmp_path / "out.bin"
    destination.write_bytes(app.result.image)
    assert destination.read_bytes() == (ROOT / "ci" / "input" / "eee" / GOOD.name).read_bytes()


def test_gui_reports_wrong_size_and_unrecognized_layout(app):
    app.load_path(str(SHORT))
    app.convert()
    assert app.result is None
    assert "Wrong size" in _report(app)

    app.load_path(str(ZERO))
    app.convert()
    assert app.result is not None
    assert not app.result.ok
    assert "Unrecognized" in _report(app)


def test_gui_blocks_cas4_and_clears_a_stale_image(app):
    app.load_path(str(GOOD))
    app.convert()
    assert app.result.ok
    app.profile_var.set("BMW - CAS4 / CAS4+ (not this tool)")
    app._on_profile()
    assert app.result is None
    assert "Not supported" in app.badge.cget("text")
    app.convert()
    assert "CAS4" in _report(app)


def test_gui_experimental_jaguar_profile_is_labelled(app):
    app.profile_var.set("Jaguar - XF BCM / BCMB (supported)")
    app._on_profile()
    details = app.details.get("1.0", "end")
    assert "2048" in details
    assert "Supported" in app.badge.cget("text")
    app.profile_var.set("Jaguar / Land Rover - KVM / RFA (experimental)")
    app._on_profile()
    assert "Experimental" in app.badge.cget("text")
    assert "experimental" in app.details.get("1.0", "end").lower()
