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
    from dflash_eee.i18n import set_language
    from gui import App

    set_language("en")

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


def _tools_menu(app):
    menu = app.root.nametowidget(app.root["menu"])
    last = menu.index("end")
    for index in range(last + 1):
        if menu.type(index) == "cascade" and menu.entrycget(index, "label") == "Tools":
            return menu.nametowidget(menu.entrycget(index, "menu"))
    return None


def _tagged(widget, tag):
    ranges = widget.tag_ranges(tag)
    parts = []
    for index in range(0, len(ranges), 2):
        parts.append(widget.get(ranges[index], ranges[index + 1]))
    return "".join(parts)


def test_gui_compare_menu_synced_hex_and_export(app, tmp_path):
    assert app.current_page() == "convert"
    tools = _tools_menu(app)
    assert tools is not None
    assert tools.entrycget(0, "label") == "Compare files..."
    tools.invoke(0)
    window = app.compare_page
    assert window is app._compare_window
    assert window is not None and window.is_open()
    assert app.current_page() == "compare"
    assert "Toplevel" not in [child.winfo_class() for child in app.root.winfo_children()]
    window._page_locked = True
    window.page_rows = 4

    data_a = bytes([0x11]) * 64
    data_b = bytearray(data_a)
    data_b[5] = 0xAB
    data_b[48] = 0xCD
    file_a = tmp_path / "a.bin"
    file_b = tmp_path / "b.bin"
    file_a.write_bytes(data_a)
    file_b.write_bytes(bytes(data_b))
    assert window.load_file("a", file_a)
    assert window.load_file("b", file_b)
    assert window.result is not None
    assert not window.result.identical
    assert window.result.differing_bytes == 2
    summary = window.summary.cget("text")
    assert "Not identical." in summary
    assert window.result.sha256_a in summary
    assert window.result.sha256_b in summary
    assert "AB" in _tagged(window.hex_b, "current") or "AB" in _tagged(window.hex_b, "diff")

    left = window.hex_a.get("1.0", "end")
    right = window.hex_b.get("1.0", "end")
    assert left.splitlines()[0][:8] == right.splitlines()[0][:8]
    assert len(left.splitlines()) <= window.page_rows + 1

    app.root.deiconify()
    app.root.update()
    bbox = window.range_view.bbox("2.0")
    assert bbox is not None

    class _Click:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    window._on_range_click(_Click(bbox[0] + 4, bbox[1] + 4))
    assert window.selected == 1
    assert "00000030" in window.hex_a.get("1.0", "end")
    window.previous_difference()
    assert window.selected == 0
    window.next_difference()
    assert window.selected == 1
    assert "00000030" in window.hex_a.get("1.0", "end")

    window.only.set(True)
    window._toggle_only()
    filtered = [line for line in window.hex_a.get("1.0", "end").splitlines() if line.startswith("0000")]
    assert any(line.startswith("00000000") for line in filtered)
    assert any(line.startswith("00000030") for line in filtered)
    assert not any(line.startswith("00000010") for line in filtered)

    text_path = tmp_path / "report.txt"
    csv_path = tmp_path / "report.csv"
    window.export_to(text_path, "text")
    window.export_to(csv_path, "csv")
    assert "Not identical." in text_path.read_text(encoding="utf-8")
    assert "AB" in text_path.read_text(encoding="utf-8")
    assert "hex_a" in csv_path.read_text(encoding="utf-8")

    # A few kilobytes must not be inserted as one giant text widget.
    window.only.set(False)
    blob = b"\x00" * 65536
    changed = bytearray(blob)
    changed[-1] = 1
    window._page_locked = True
    window.page_rows = 6
    window._set_side("a", blob, "big-a.bin", None)
    window._set_side("b", bytes(changed), "big-b.bin", None)
    drawn = [line for line in window.hex_a.get("1.0", "end").splitlines() if line]
    assert window.result.differing_bytes == 1
    assert len(drawn) == 6
    assert len(drawn) < window.total_rows

    app.root.withdraw()
    window.close()
    assert not window.is_open()
    assert app.current_page() == "convert"


def test_gui_compare_uses_the_last_conversion(app, tmp_path):
    app.load_path(str(GOOD))
    app.convert()
    from dflash_eee.compare_view import CompareWindow

    window = CompareWindow(app)
    assert window is app.compare_page
    assert window.data_b == app.result.image
    assert "last conversion" in window.name_b
    assert window.use_last_conversion("a")
    assert window.result.identical
    assert "Identical." in window.summary.cget("text")
    report = tmp_path / "same.txt"
    window.export_to(report)
    assert "Identical." in report.read_text(encoding="utf-8")
    assert "No differences." in report.read_text(encoding="utf-8")
    window.close()


def test_compare_window_follows_the_language(app):
    from dflash_eee.compare_view import CompareWindow

    window = CompareWindow(app)
    try:
        app.change_language("es")
        menu = app.root.nametowidget(app.root["menu"])
        tools = None
        for index in range(menu.index("end") + 1):
            if menu.type(index) == "cascade" and menu.entrycget(index, "label") == "Herramientas":
                tools = menu.nametowidget(menu.entrycget(index, "menu"))
        assert tools is not None
        assert tools.entrycget(0, "label") == "Comparar archivos..."
        assert window.title_label.cget("text") == "Comparar volcados"
        assert app.notebook.tab(app._pages["compare"][0], "text") == "Comparar"
        assert app.notebook.tab(app._pages["convert"][0], "text") == "Convertir"
        assert window.prev_btn.cget("text") == "Diferencia anterior"
        assert window.subtitle_label.cget("text").startswith("Hex")
        window.close()
    finally:
        if window.is_open():
            window.close()
        app.change_language("en")
    assert app.root.title() == "D-Flash to EEPROM - free & open source"


def test_another_page_can_sit_beside_compare(app):
    import tkinter as tk

    frame = tk.Frame(app.notebook)
    app.add_page("module_info", frame, "gui.menu.tools")
    app.add_page("module_info", frame, "gui.menu.tools")
    labels = [app.notebook.tab(index, "text") for index in range(app.notebook.index("end"))]
    assert labels == ["Convert", "Compare", "Tools"]
    app.show_page("module_info")
    assert app.current_page() == "module_info"
    app.open_compare()
    assert app.current_page() == "compare"
    app.show_page("convert")
    assert app.current_page() == "convert"


def _label_texts(widget):
    texts = []
    for child in widget.winfo_children():
        if child.winfo_class() == "Label":
            texts.append(child.cget("text"))
        texts.extend(_label_texts(child))
    return texts


def test_window_says_the_official_copy_is_free(app):
    assert "free" in app.root.title().lower()
    assert any("official download is free" in text.lower() for text in _label_texts(app.root))


def test_help_menu_can_verify_this_copy(app):
    menu = app.root.nametowidget(app.root["menu"])
    help_menu = None
    for index in range(menu.index("end") + 1):
        if menu.type(index) != "cascade":
            continue
        if menu.entrycget(index, "label") == "Help":
            help_menu = menu.nametowidget(menu.entrycget(index, "menu"))
    assert help_menu is not None
    labels = [
        help_menu.entrycget(index, "label")
        for index in range(help_menu.index("end") + 1)
        if help_menu.type(index) == "command"
    ]
    assert "Verify this copy" in labels
    assert "About" in labels


def test_first_run_notice_is_shown_once_and_does_not_use_the_network(app, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr("gui.config_path", lambda: path)

    def boom(*_args, **_kwargs):
        raise AssertionError("network used during the first-run notice")

    monkeypatch.setattr("gui.verify_release", boom)
    app.offer_first_run_notice()
    body = app._trust_body.get("1.0", "end")
    assert "Windows protected your PC" in body
    assert "you paid for something you could get free" in body
    assert "source checkout" in body
    assert not path.exists()
    app._trust_dismiss()
    assert path.is_file()
    from dflash_eee.integrity import notice_seen
    assert notice_seen(path)
    assert not app._trust_dialog.winfo_exists()
    app.offer_first_run_notice()
    assert not app._trust_dialog.winfo_exists()


def test_verify_from_source_does_not_download(app, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network used for a source checkout")

    monkeypatch.setattr("gui.verify_release", boom)
    app.verify_this_copy()
    assert "source" in app._trust_status.get().lower()
    app._trust_dismiss()


def test_gui_language_menu_refreshes_the_window(app):
    app.change_language("es")
    assert app.root.title() == "D-Flash a EEPROM - libre y de código abierto"
    assert app.header_title.cget("text") == "D-Flash a EEPROM - libre y de código abierto"
    assert app.convert_button.cget("text") == "Convertir a EEPROM"
    assert app.badge.cget("text") == "Soportado"
    assert app.profile_var.get() == "BMW / Mini - FRM3 del piso (soportado)"
    assert "oficial" in app.free_label.cget("text").lower()
    app.change_language("en")
    assert app.root.title() == "D-Flash to EEPROM - free & open source"
    assert app.convert_button.cget("text") == "Convert to EEPROM"
    assert app.badge.cget("text") == "Supported"
    assert app.profile_var.get() == "BMW / Mini - FRM3 footwell (supported)"


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
