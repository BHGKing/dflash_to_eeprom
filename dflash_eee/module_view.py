# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Module info tab and the Virginize command.

The tab sits in the same notebook as Convert and Compare. Tools → Virginize...
does not open a second layout. It asks before writing, and it always writes a
new file.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dflash_eee.compare import compare_bytes, format_report
from dflash_eee.converter import InputError
from dflash_eee.decode import decode_image, format_module_info, load_module_image
from dflash_eee.i18n import t
from dflash_eee.journal import JournalError, encode_journal
from dflash_eee.virginize import format_virginize_plan, output_path_problem, virginize_image


def attach_module_info(app) -> None:
    """Add the Module info tab and Tools → Virginize...

    The desktop window rebuilds its menu when the language changes, so the
    menu item is added after every rebuild. The page itself is created once.
    """

    _add_virginize_item(app)
    if getattr(app, "module_page", None) is None:
        frame = ttk.Frame(app.notebook)
        page = ModuleInfoPage(app, frame)
        app.module_page = page
        app.add_page("module_info", frame, "gui.tab.module_info", on_show=page.refresh)
    else:
        app.module_page.refresh_language()


def current_eeprom(app) -> bytes | None:
    """EEPROM bytes from the last conversion, or a file that is already one."""

    result = getattr(app, "result", None)
    if result is not None and getattr(result, "image", None) is not None:
        return result.image
    image = getattr(app, "eeprom_image", None)
    if image:
        return image
    return None


def refresh_module_info(app) -> None:
    page = getattr(app, "module_page", None)
    if page is not None:
        page.refresh()


class ModuleInfoPage:
    """Read-only VIN, mileage and key reading for the current image."""

    def __init__(self, app, frame):
        self.app = app
        self.frame = frame
        pad = ttk.Frame(frame, padding=(14, 12, 14, 8))
        pad.pack(fill="both", expand=True)
        self.title_label = tk.Label(pad, text="", anchor="w", font=app.ui_bold, bg="#e6edf3", fg="#1e3a5f")
        self.title_label.pack(fill="x")
        self.hint_label = tk.Label(
            pad, text="", anchor="w", justify="left", wraplength=900,
            font=app.ui_small, bg="#e6edf3", fg="#5c6b78",
        )
        self.hint_label.pack(fill="x", pady=(2, 8))
        self.text = tk.Text(
            pad, wrap="word", font=app.mono, bg="#ffffff", fg="#1c2833",
            relief="flat", highlightthickness=1, highlightbackground="#c5d0da", padx=8, pady=8,
        )
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")
        self.refresh_language()

    def refresh_language(self) -> None:
        self.title_label.configure(text=t("gui.module.title"))
        self.hint_label.configure(text=t("gui.module.hint"))
        self.refresh()

    def refresh(self) -> None:
        image = current_eeprom(self.app)
        if image is None:
            body = t("gui.module.empty")
        else:
            try:
                info = decode_image(self.app.profile, image, source="eeprom")
            except InputError as exc:
                body = str(exc)
            else:
                body = format_module_info(info)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", body)
        self.text.configure(state="disabled")


def open_virginize(app) -> None:
    """Show what would change, then write a new file if the user confirms."""

    image = current_eeprom(app)
    if image is None and app.data is not None:
        try:
            loaded, _source, _warnings = load_module_image(app.data, app.profile)
        except InputError:
            loaded = None
        image = loaded
    if image is None:
        messagebox.showinfo(t("gui.virginize.none_title"), t("gui.virginize.none"), parent=app.root)
        return
    plan = virginize_image(app.profile, image)
    text = format_virginize_plan(app.profile, plan)
    if not plan.ready:
        messagebox.showinfo(t("gui.virginize.refused_title"), text, parent=app.root)
        return
    VirginizeDialog(app, image, text)


class VirginizeDialog:
    """Lists every changed range. Confirm writes a new file."""

    def __init__(self, app, image: bytes, text: str):
        self.app = app
        self.image = image
        self.win = tk.Toplevel(app.root)
        self.win.title(t("gui.virginize.title"))
        self.win.transient(app.root)
        self.win.geometry("720x520")
        body = tk.Text(self.win, wrap="word", font=app.mono, padx=8, pady=8)
        body.pack(fill="both", expand=True, padx=12, pady=(12, 4))
        body.insert("1.0", text)
        body.configure(state="disabled")
        self.journal = tk.BooleanVar(master=self.win, value=False)
        tk.Checkbutton(
            self.win,
            text=t("gui.virginize.journal"),
            variable=self.journal,
            anchor="w",
            justify="left",
            wraplength=680,
        ).pack(fill="x", padx=12)
        buttons = ttk.Frame(self.win, padding=(12, 8))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=t("gui.virginize.cancel"), command=self.win.destroy).pack(side="right")
        ttk.Button(buttons, text=t("gui.virginize.confirm"), command=self._confirm).pack(side="right", padx=(0, 8))

    def _confirm(self) -> None:
        source = self.app.path if self.app.path is not None else Path("eeprom.bin")
        initial = source.stem + "_virgin.bin"
        selected = filedialog.asksaveasfilename(
            parent=self.win,
            title=t("gui.virginize.save_title"),
            initialfile=initial,
            defaultextension=".bin",
            filetypes=[
                (t("gui.filetypes.binary"), "*.bin"),
                (t("gui.filetypes.all"), "*.*"),
            ],
        )
        if not selected:
            return
        journal_path = None
        if self.journal.get():
            journal_path = filedialog.asksaveasfilename(
                parent=self.win,
                title=t("gui.virginize.journal_title"),
                initialfile=source.stem + "_virgin.dflash",
                defaultextension=".bin",
                filetypes=[
                    (t("gui.filetypes.binary"), "*.bin"),
                    (t("gui.filetypes.all"), "*.*"),
                ],
            )
            if not journal_path:
                return
        report = Path(selected).with_name(Path(selected).stem + "_virginize.txt")
        error = write_virgin_files(
            self.app,
            self.image,
            Path(selected),
            report,
            Path(journal_path) if journal_path else None,
        )
        if error:
            messagebox.showerror(t("gui.dialog.cannot_save"), error, parent=self.win)
            return
        self.win.destroy()
        messagebox.showinfo(t("gui.virginize.saved_title"), t("gui.virginize.saved", path=selected, report=str(report)), parent=self.app.root)


def write_virgin_files(app, image: bytes, dest: Path, report: Path, journal_path: Path | None) -> str | None:
    """Write the virgin EEPROM, a diff report, and an optional journal.

    Returns an error string, or None. Refuses to overwrite the source or an
    existing file.
    """

    source = app.path if app.path is not None else dest
    plan = virginize_image(app.profile, image)
    if not plan.ready or plan.image is None:
        return format_virginize_plan(app.profile, plan).strip()
    for path in (dest, report, journal_path):
        if path is None:
            continue
        problem = output_path_problem(source, path)
        if problem == "same":
            return t("virginize.err.same", path=str(path))
        if problem == "exists":
            return t("virginize.err.exists", path=str(path))
        if path.resolve() == dest.resolve() and path is not dest:
            return t("virginize.err.same", path=str(path))
    journal = None
    if journal_path is not None:
        try:
            journal = encode_journal(plan.image, app.profile)
        except JournalError as exc:
            return str(exc)
    try:
        dest.write_bytes(plan.image)
        compared = compare_bytes(image, plan.image)
        header = format_virginize_plan(app.profile, plan)
        report.write_text(header + format_report(compared, str(source), str(dest), "text"), encoding="utf-8", newline="\n")
        if journal_path is not None and journal is not None:
            journal_path.write_bytes(journal)
    except OSError as exc:
        return str(exc)
    app.status.set(t("gui.virginize.saved_status", path=str(dest)))
    return None


def _add_virginize_item(app) -> None:
    menu_name = app.root.cget("menu")
    if not menu_name:
        return
    bar = app.root.nametowidget(menu_name)
    tools = _tools_menu(bar)
    if tools is None:
        return
    label = t("gui.menu.virginize")
    last = tools.index("end")
    if last is not None:
        for index in range(last + 1):
            if tools.type(index) == "command" and tools.entrycget(index, "label") == label:
                return
    tools.add_command(label=label, command=lambda: open_virginize(app))


def _tools_menu(bar):
    last = bar.index("end")
    if last is None:
        return None
    label = t("gui.menu.tools")
    for index in range(last + 1):
        if bar.type(index) == "cascade" and bar.entrycget(index, "label") == label:
            return bar.nametowidget(bar.entrycget(index, "menu"))
    return None
