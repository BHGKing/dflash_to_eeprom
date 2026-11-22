#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright 2017-2018, Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the
#    distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
# HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
# OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY
# WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
"""Desktop tool for rebuilding an S12XE emulated EEPROM from a D-Flash dump.

The 2017 gui.py was three file dialogs. This window keeps that job (open a
dump, convert, save) and adds the module list, validation text and a hex
preview. Tk is used because it ships with CPython and PyInstaller packs it
into one Windows executable without a Qt runtime.
"""

from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dflash_eee import __version__
from dflash_eee.compare_view import attach_compare
from dflash_eee.converter import convert_bytes
from dflash_eee.profiles import default_extra_dirs, discover_profiles, get_profile
from dflash_eee.report import hex_dump, parse_drop_paths, report_lines, shorten_middle, size_issue

SAFETY = (
    "Keep a backup of the original dump. Write the new file to the EEE / EEPROM "
    "partition, not back to D-Flash, and read it back to verify. This tool does "
    "not clear fault memory, counters or checksums."
)


def _font(names, size, weight="normal"):
    try:
        families = set(tkfont.families())
    except tk.TclError:
        families = set()
    chosen = "TkDefaultFont"
    for name in names:
        if name in families:
            chosen = name
            break
    return (chosen, size, weight)


UI = ("Inter", "Segoe UI", "Liberation Sans", "DejaVu Sans")
MONO = ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono")


def make_root():
    """Use the drag-and-drop root when tkinterdnd2 is installed."""

    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk(), True
    except Exception:
        return tk.Tk(), False


def _enable_drop(widget, callback) -> bool:
    try:
        from tkinterdnd2 import DND_FILES
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda event: callback(event.data))
        return True
    except Exception:
        return False


class App(object):
    def __init__(self, root):
        self.root = root
        self.root.title("D-Flash to EEPROM")
        self.root.geometry("1180x820")
        self.root.minsize(980, 680)
        self.root.configure(bg="#e6edf3")
        self.ui = _font(UI, 11)
        self.ui_bold = _font(UI, 11, "bold")
        self.ui_small = _font(UI, 10)
        self.mono = _font(MONO, 10)
        self.title_font = _font(UI, 18, "bold")
        self.profiles, self.profile_errors = discover_profiles(default_extra_dirs())
        self.by_label = {profile.menu_label: profile for profile in self.profiles}
        self.profile = get_profile("bmw_frm3", self.profiles)
        self.path: Path | None = None
        self.data: bytes | None = None
        self.result = None
        self.drop_enabled = False
        self._styles()
        self._build()
        self._show_profile()
        self._set_save_enabled(False)
        if self.profile_errors:
            self.status.set("A drop-in profile failed to load. See the module panel.")

    def _styles(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", font=self.ui, background="#e6edf3")
        style.configure("TFrame", background="#e6edf3")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#e6edf3", foreground="#1c2833")
        style.configure("Card.TLabel", background="#ffffff", foreground="#1c2833")
        style.configure("Hint.TLabel", background="#ffffff", foreground="#5c6b78", font=self.ui_small)
        style.configure("Convert.TButton", font=self.ui_bold, padding=(18, 8),
                        background="#1e3a5f", foreground="#ffffff")
        style.map("Convert.TButton",
                  background=[("active", "#2d5280"), ("disabled", "#b7c3ce")],
                  foreground=[("disabled", "#f4f7fa")])
        style.configure("TButton", padding=(12, 6))
        style.configure("TCombobox", padding=4)

    def _build(self):
        header = tk.Frame(self.root, bg="#1e3a5f", height=74)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="D-Flash to EEPROM", bg="#1e3a5f", fg="#f7f4ef",
                 font=self.title_font, anchor="w").pack(fill="x", padx=18, pady=(12, 0))
        tk.Label(header, text="Rebuild an S12XE emulated EEPROM from a D-Flash dump.  v%s" % __version__,
                 bg="#1e3a5f", fg="#c5d4e4", font=self.ui_small, anchor="w").pack(fill="x", padx=18)

        banner = tk.Frame(self.root, bg="#fff4dc")
        banner.pack(fill="x")
        tk.Label(banner, text=SAFETY, bg="#fff4dc", fg="#6b4e16", font=self.ui_small,
                 anchor="w", justify="left", wraplength=1120).pack(fill="x", padx=18, pady=8)

        body = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(1, weight=1)

        self._build_dump_card(body)
        self._build_module_card(body)
        self._build_results(body)

        status_bar = tk.Frame(self.root, bg="#d5dee6", height=26)
        status_bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value="Open a D-Flash dump, check the module, then convert.")
        tk.Label(status_bar, textvariable=self.status, bg="#d5dee6", fg="#1c2833",
                 font=self.ui_small, anchor="w").pack(fill="x", padx=12, pady=3)

        self._menu()
        self.root.bind("<Control-o>", lambda _event: self.browse())
        self.root.bind("<Control-O>", lambda _event: self.browse())
        self.root.bind("<Control-s>", lambda _event: self.save())
        self.root.bind("<Control-S>", lambda _event: self.save())
        self.root.bind("<Control-Return>", lambda _event: self.convert())
        # Tools → Compare lives in its own module so File and Help can change
        # without touching the hex compare window.
        attach_compare(self)

    def _menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open D-Flash...", command=self.browse, accelerator="Ctrl+O")
        file_menu.add_command(label="Save EEPROM...", command=self.save, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.destroy)
        menu.add_cascade(label="File", menu=file_menu)
        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Safety notes", command=self._safety_dialog)
        help_menu.add_command(label="About", command=self._about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menu)

    def _card(self, parent, title):
        frame = tk.Frame(parent, bg="#ffffff", highlightthickness=1, highlightbackground="#c5d0da")
        tk.Label(frame, text=title, bg="#ffffff", fg="#1e3a5f", font=self.ui_bold, anchor="w").pack(
            fill="x", padx=12, pady=(10, 4))
        return frame

    def _build_dump_card(self, body):
        card = self._card(body, "D-Flash dump")
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))

        self.drop = tk.Frame(card, bg="#f4f8fb", height=118, highlightthickness=2,
                             highlightbackground="#8aa0b4")
        self.drop.pack(fill="x", padx=12, pady=(4, 8))
        self.drop.pack_propagate(False)
        self.drop_var = tk.StringVar(value="Drop a D-Flash .bin here\nor use Browse")
        self.drop_label = tk.Label(
            self.drop, textvariable=self.drop_var, bg="#f4f8fb", fg="#1e3a5f",
            font=_font(UI, 11), justify="center", wraplength=420,
        )
        self.drop_label.pack(expand=True, padx=16)
        self.drop_enabled = _enable_drop(self.drop, self._on_drop) or _enable_drop(self.drop_label, self._on_drop)
        if not self.drop_enabled:
            self.drop_var.set("Use Browse to open a D-Flash .bin\n(drag-and-drop needs tkinterdnd2)")

        self.size_var = tk.StringVar(value="No file loaded.")
        self.size_label = tk.Label(card, textvariable=self.size_var, bg="#ffffff", fg="#5c6b78",
                                    font=self.ui_small, anchor="w", justify="left", wraplength=420)
        self.size_label.pack(fill="x", padx=12)

        buttons = tk.Frame(card, bg="#ffffff")
        buttons.pack(fill="x", padx=12, pady=(8, 12))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        ttk.Button(buttons, text="Browse...", command=self.browse).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.save_button = ttk.Button(buttons, text="Save EEPROM...", command=self.save)
        self.save_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.convert_button = ttk.Button(buttons, text="Convert to EEPROM", style="Convert.TButton",
                                          command=self.convert)
        self.convert_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_module_card(self, body):
        card = self._card(body, "Vehicle / module")
        card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))

        row = tk.Frame(card, bg="#ffffff")
        row.pack(fill="x", padx=12, pady=(0, 6))
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(
            row, textvariable=self.profile_var, state="readonly",
            values=[profile.menu_label for profile in self.profiles],
        )
        self.profile_box.pack(side="left", fill="x", expand=True)
        self.profile_box.bind("<<ComboboxSelected>>", self._on_profile)
        self.badge = tk.Label(row, text="", bg="#1f7a4d", fg="#ffffff", font=self.ui_small, padx=8, pady=2)
        self.badge.pack(side="left", padx=(8, 0))

        self.details = tk.Text(
            card, height=12, wrap="word", font=self.ui_small, bg="#ffffff", fg="#1c2833",
            relief="flat", highlightthickness=0, padx=4, pady=4,
        )
        scroll = ttk.Scrollbar(card, command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y", padx=(0, 6), pady=(0, 10))
        self.details.pack(fill="both", expand=True, padx=(12, 0), pady=(0, 10))
        for name, colour in (
            ("supported", "#1f7a4d"),
            ("experimental", "#8a5a00"),
            ("unsupported", "#9b2335"),
            ("heading", "#1e3a5f"),
            ("assumption", "#8a5a00"),
            ("source", "#5c6b78"),
            ("error", "#9b2335"),
        ):
            weight = "bold" if name in ("supported", "experimental", "unsupported", "heading") else "normal"
            self.details.tag_configure(name, foreground=colour, font=_font(UI, 10, weight))
        self.details.configure(state="disabled")

    def _build_results(self, body):
        lower = ttk.Panedwindow(body, orient="horizontal")
        lower.grid(row=1, column=0, columnspan=2, sticky="nsew")

        report_card = self._card(lower, "Validation")
        hex_card = self._card(lower, "EEPROM preview")
        lower.add(report_card, weight=1)
        lower.add(hex_card, weight=1)

        self.report = tk.Text(
            report_card, wrap="word", font=self.ui_small, bg="#ffffff", fg="#1c2833",
            relief="flat", highlightthickness=0, padx=8, pady=4,
        )
        report_scroll = ttk.Scrollbar(report_card, command=self.report.yview)
        self.report.configure(yscrollcommand=report_scroll.set)
        report_scroll.pack(side="right", fill="y", pady=(0, 8))
        self.report.pack(fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        self.report.tag_configure("ok", foreground="#1f7a4d", font=self.ui_bold)
        self.report.tag_configure("warning", foreground="#8a5a00")
        self.report.tag_configure("error", foreground="#9b2335")
        self.report.tag_configure("info", foreground="#1c2833")
        self.report.configure(state="disabled")

        self.hex_view = tk.Text(
            hex_card, wrap="none", font=self.mono, bg="#0f1c2b", fg="#d6e2ee",
            relief="flat", highlightthickness=0, padx=8, pady=6, insertbackground="#d6e2ee",
        )
        hex_y = ttk.Scrollbar(hex_card, command=self.hex_view.yview)
        hex_x = ttk.Scrollbar(hex_card, orient="horizontal", command=self.hex_view.xview)
        self.hex_view.configure(yscrollcommand=hex_y.set, xscrollcommand=hex_x.set)
        hex_y.pack(side="right", fill="y")
        hex_x.pack(side="bottom", fill="x")
        self.hex_view.pack(fill="both", expand=True)
        self.hex_view.insert("1.0", "The converted image will appear here.")
        self.hex_view.configure(state="disabled")

    def _set_text(self, widget, chunks):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for tag, text in chunks:
            widget.insert("end", text, tag or "")
        widget.configure(state="disabled")

    def _show_profile(self):
        profile = self.profile
        self.profile_var.set(profile.menu_label)
        colours = {
            "supported": ("#1f7a4d", "Supported"),
            "experimental": ("#8a5a00", "Experimental"),
            "unsupported": ("#9b2335", "Not supported"),
        }
        colour, label = colours[profile.status]
        self.badge.configure(text=label, bg=colour)
        chunks = [(profile.status, profile.status_label + "\n"), (None, profile.mcu + "\n\n")]
        chunks.append((None, profile.vehicles + "\n\n"))
        chunks.append((None, profile.summary + "\n\n"))
        if profile.conversion_enabled:
            chunks.append((None, "D-Flash %d bytes (%d sectors x %d).\n" % (
                profile.dflash_size, profile.scan_sectors, profile.layout.sector_size)))
            chunks.append((None, "EEPROM image %d bytes.\n" % profile.eee_size))
            chunks.append((None, profile.partition_text() + "\n"))
            chunks.append((None, "Record format: S12XE EEE, sector header 0xFACF, 63 records of 4 bytes.\n\n"))
        chunks.append(("heading", "Write-back\n"))
        chunks.append((None, profile.write_notes + "\n\n"))
        if profile.assumptions:
            chunks.append(("heading", "Why this is experimental\n"))
            chunks.append(("assumption", profile.assumptions + "\n\n"))
        chunks.append(("heading", "Sources\n"))
        for source in profile.sources:
            chunks.append(("source", "- " + source + "\n"))
        if self.profile_errors:
            chunks.append(("heading", "\nDrop-in profile problems\n"))
            for error in self.profile_errors:
                chunks.append(("error", error + "\n"))
        self._set_text(self.details, chunks)
        if profile.conversion_enabled:
            self.convert_button.configure(text="Convert to EEPROM", state="normal")
        else:
            self.convert_button.configure(text="Conversion disabled", state="disabled")
        self._refresh_size_label()

    def _on_profile(self, _event=None):
        label = self.profile_var.get()
        profile = self.by_label.get(label)
        if profile is None or profile.id == self.profile.id:
            return
        self.profile = profile
        self.result = None
        self._set_save_enabled(False)
        self._show_profile()
        self._set_text(self.report, [("warning", "Profile changed. Convert again before saving.\n")])
        self._set_hex("The previous image was cleared because the module changed.")
        self.status.set(profile.menu_label)

    def _on_drop(self, payload):
        paths = parse_drop_paths(payload)
        if not paths:
            return
        if len(paths) > 1:
            self.status.set("Several files were dropped. Using the first one.")
        self.load_path(paths[0])

    def browse(self):
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Open D-Flash dump",
            filetypes=[("Binary dumps", "*.bin *.eep *.dflash *.dump"), ("All files", "*.*")],
        )
        if selected:
            self.load_path(selected)

    def load_path(self, path):
        file_path = Path(path)
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            messagebox.showerror("Cannot open file", str(exc), parent=self.root)
            return
        self.path = file_path
        self.data = data
        self.result = None
        self._set_save_enabled(False)
        self.drop_var.set(shorten_middle(file_path.name, limit=36) + "\n%d bytes" % len(data))
        self.drop.configure(highlightbackground="#1e3a5f")
        self._refresh_size_label()
        self._set_text(self.report, [("info", "File loaded. Check the module, then convert.\n")])
        self._set_hex("Convert to preview the EEPROM image.")
        self.status.set("Loaded %s (%d bytes)." % (file_path.name, len(data)))

    def _refresh_size_label(self):
        if self.data is None:
            self.size_var.set("No file loaded.")
            self.size_label.configure(fg="#5c6b78")
            return
        issue = size_issue(len(self.data), self.profile) if self.profile.conversion_enabled else None
        if issue is None:
            self.size_var.set("Size OK: %d bytes." % len(self.data))
            self.size_label.configure(fg="#1f7a4d")
        elif issue.level == "error":
            self.size_var.set(issue.text)
            self.size_label.configure(fg="#9b2335")
        else:
            self.size_var.set(issue.text)
            self.size_label.configure(fg="#8a5a00")

    def convert(self):
        if not self.profile.conversion_enabled:
            self._set_text(self.report, [("error", self.profile.summary + "\n\n" + self.profile.write_notes + "\n")])
            self.status.set("This module is not converted by this tool.")
            return
        if self.data is None:
            messagebox.showinfo("No dump", "Open a D-Flash dump first.", parent=self.root)
            return
        issue = size_issue(len(self.data), self.profile)
        if issue is not None and issue.level == "error":
            self.result = None
            self._set_save_enabled(False)
            self._set_text(self.report, [("error", issue.text + "\n")])
            self._set_hex("No image. The dump is the wrong size for this module.")
            self.status.set("Wrong size.")
            return
        self.result = convert_bytes(self.data, self.profile, check_size=True)
        rows = [(level, text + "\n") for level, text in report_lines(self.result)]
        self._set_text(self.report, rows)
        if self.result.image is not None:
            self._set_hex(hex_dump(self.result.image))
            self._set_save_enabled(True)
            self.status.set("%s. Image is %d bytes." % (self.result.profile.title, len(self.result.image)))
        else:
            self._set_hex("No image was produced.")
            self._set_save_enabled(False)
            self.status.set("Conversion failed.")

    def _set_hex(self, text):
        self.hex_view.configure(state="normal")
        self.hex_view.delete("1.0", "end")
        self.hex_view.insert("1.0", text)
        self.hex_view.configure(state="disabled")

    def _set_save_enabled(self, enabled):
        self.save_button.state(["!disabled"] if enabled else ["disabled"])

    def save(self):
        if self.result is None or self.result.image is None:
            messagebox.showinfo("Nothing to save", "Convert a dump first.", parent=self.root)
            return
        if self.result.errors or self.result.corrupt:
            proceed = messagebox.askokcancel(
                "Save this image anyway?",
                "The conversion reported errors. The image may be wrong.\n\n"
                "Keep the original dump. If you save, program the EEE partition only and verify the read-back.",
                parent=self.root,
            )
            if not proceed:
                return
        initial = "eeprom.bin"
        if self.path is not None:
            initial = self.path.stem + "_eee.bin"
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save EEPROM image",
            initialfile=initial,
            defaultextension=".bin",
            filetypes=[("Binary", "*.bin"), ("All files", "*.*")],
        )
        if not selected:
            return
        try:
            Path(selected).write_bytes(self.result.image)
        except OSError as exc:
            messagebox.showerror("Cannot save", str(exc), parent=self.root)
            return
        self.status.set("Saved %s (%d bytes)." % (selected, len(self.result.image)))
        messagebox.showinfo(
            "EEPROM saved",
            "Saved %s.\n\nWrite it to the EEE partition, not to D-Flash, and verify the read-back.\n\n%s"
            % (selected, self.profile.write_notes),
            parent=self.root,
        )

    def _safety_dialog(self):
        messagebox.showwarning("Safety", SAFETY + "\n\n" + self.profile.write_notes, parent=self.root)

    def _about(self):
        messagebox.showinfo(
            "About",
            "D-Flash to EEPROM %s\n\n"
            "Copyright 2017-2018 Ben van Leeuwen Autotechniek\n"
            "Copyright 2026 Jose\n\n"
            "BSD-3-Clause. See the LICENSE file.\n\n"
            "The replay is the original FRM3 decoder, parameterized by module. "
            "A profile marked Experimental is not a confirmed memory map."
            % __version__,
            parent=self.root,
        )


def main(argv=None):
    root, _drop = make_root()
    app = App(root)
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        candidate = Path(args[0])
        if candidate.is_file():
            app.load_path(str(candidate))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
