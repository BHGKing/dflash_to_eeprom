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
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dflash_eee import __version__
from dflash_eee.build_info import COMMIT, RELEASE_TAG
from dflash_eee.converter import convert_bytes
from dflash_eee.i18n import (
    available_languages,
    ensure_language,
    get_language,
    init_language,
    normalize,
    save_language,
    set_language,
    t,
)
from dflash_eee.integrity import (
    FREE_BAR,
    SOURCE_REPOSITORY_URL,
    config_path,
    identify_running_copy,
    mark_notice_seen,
    notice_body,
    notice_seen,
    release_page_url,
    status_for_plan,
    verification_plan,
    verify_release,
)
from dflash_eee.profiles import (
    default_extra_dirs,
    discover_profiles,
    get_profile,
    localized_sources,
    profile_text,
)
from dflash_eee.report import hex_dump, parse_drop_paths, report_lines, shorten_middle, size_issue

_VERIFY_COLOUR = {
    "match": "#1f7a4d",
    "mismatch": "#9b2335",
    "could_not_check": "#8a5a00",
}


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
        ensure_language()
        self.root = root
        self.root.geometry("1180x820")
        self.root.minsize(980, 680)
        self.root.configure(bg="#e6edf3")
        self.ui = _font(UI, 11)
        self.ui_bold = _font(UI, 11, "bold")
        self.ui_small = _font(UI, 10)
        self.mono = _font(MONO, 10)
        self.title_font = _font(UI, 18, "bold")
        self.lang_var = tk.StringVar(master=self.root, value=get_language())
        self.profiles, self.profile_errors = discover_profiles(default_extra_dirs())
        self.by_label = {}
        self.profile = get_profile("bmw_frm3", self.profiles)
        self.path: Path | None = None
        self.data: bytes | None = None
        self.result = None
        self.drop_enabled = False
        self._trust_dialog = None
        self._trust_dismiss = None
        self._trust_body = None
        self._trust_status = None
        self._profile_stale = False
        self._cards = []
        self._styles()
        self._build()
        self._set_save_enabled(False)

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
        self.header_title = tk.Label(header, text="", bg="#1e3a5f", fg="#f7f4ef",
                                     font=self.title_font, anchor="w")
        self.header_title.pack(fill="x", padx=18, pady=(12, 0))
        self.header_subtitle = tk.Label(header, text="", bg="#1e3a5f", fg="#c5d4e4",
                                        font=self.ui_small, anchor="w")
        self.header_subtitle.pack(fill="x", padx=18)

        banner = tk.Frame(self.root, bg="#fff4dc")
        banner.pack(fill="x")
        self.safety_label = tk.Label(banner, text="", bg="#fff4dc", fg="#6b4e16", font=self.ui_small,
                                     anchor="w", justify="left", wraplength=1120)
        self.safety_label.pack(fill="x", padx=18, pady=8)

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
        self.status = tk.StringVar(value="")
        tk.Label(status_bar, textvariable=self.status, bg="#d5dee6", fg="#1c2833",
                 font=self.ui_small, anchor="w").pack(fill="x", padx=12, pady=3)
        free_bar = tk.Frame(self.root, bg="#e7f2ea")
        free_bar.pack(fill="x", side="bottom")
        self.free_label = tk.Label(free_bar, text=FREE_BAR, bg="#e7f2ea", fg="#1f7a4d",
                                   font=self.ui_small, anchor="w")
        self.free_label.pack(fill="x", padx=12, pady=3)

        self.root.bind("<Control-o>", lambda _event: self.browse())
        self.root.bind("<Control-O>", lambda _event: self.browse())
        self.root.bind("<Control-s>", lambda _event: self.save())
        self.root.bind("<Control-S>", lambda _event: self.save())
        self.root.bind("<Control-Return>", lambda _event: self.convert())
        self._apply_language()

    def _menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label=t("gui.menu.open"), command=self.browse, accelerator="Ctrl+O")
        file_menu.add_command(label=t("gui.menu.save"), command=self.save, accelerator="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_command(label=t("gui.menu.quit"), command=self.root.destroy)
        menu.add_cascade(label=t("gui.menu.file"), menu=file_menu)
        lang_menu = tk.Menu(menu, tearoff=0)
        for code, name in available_languages():
            lang_menu.add_radiobutton(
                label=name,
                value=code,
                variable=self.lang_var,
                command=lambda chosen=code: self.change_language(chosen),
            )
        menu.add_cascade(label=t("gui.menu.language"), menu=lang_menu)
        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label=t("gui.menu.verify"), command=self.verify_this_copy)
        help_menu.add_command(label=t("gui.menu.safety"), command=self._safety_dialog)
        help_menu.add_command(label=t("gui.menu.about"), command=self._about)
        menu.add_cascade(label=t("gui.menu.help"), menu=help_menu)
        self.root.config(menu=menu)

    def change_language(self, code: str) -> None:
        """Switch language, remember it, and refresh the window in place."""

        chosen = normalize(code)
        if chosen is None or chosen == get_language():
            self.lang_var.set(get_language())
            return
        set_language(chosen)
        self.lang_var.set(chosen)
        saved = True
        try:
            save_language(chosen)
        except OSError:
            saved = False
        self._apply_language()
        if not saved:
            self.status.set(t("gui.status.lang_save_failed"))

    def _apply_language(self) -> None:
        current = self.profile.id
        self.profiles, self.profile_errors = discover_profiles(default_extra_dirs())
        self.profile = get_profile(current, self.profiles)
        self.root.title(t("gui.window_title"))
        self.header_title.configure(text=t("gui.window_title"))
        self.header_subtitle.configure(text=t("gui.header.subtitle", version=__version__))
        self.safety_label.configure(text=t("gui.safety"))
        self.free_label.configure(text=t("gui.free_bar", default=FREE_BAR))
        for frame in self._cards:
            frame.title_label.configure(text=t(frame.title_key))
        self.browse_button.configure(text=t("gui.button.browse"))
        self.save_button.configure(text=t("gui.button.save"))
        self._refresh_drop_hint()
        self._menu()
        self._show_profile()
        self._refresh_result_text()

    def _card(self, parent, title_key):
        frame = tk.Frame(parent, bg="#ffffff", highlightthickness=1, highlightbackground="#c5d0da")
        label = tk.Label(frame, text=t(title_key), bg="#ffffff", fg="#1e3a5f", font=self.ui_bold, anchor="w")
        label.pack(fill="x", padx=12, pady=(10, 4))
        frame.title_label = label
        frame.title_key = title_key
        self._cards.append(frame)
        return frame

    def _build_dump_card(self, body):
        card = self._card(body, "gui.card.dump")
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))

        self.drop = tk.Frame(card, bg="#f4f8fb", height=118, highlightthickness=2,
                             highlightbackground="#8aa0b4")
        self.drop.pack(fill="x", padx=12, pady=(4, 8))
        self.drop.pack_propagate(False)
        self.drop_var = tk.StringVar(value="")
        self.drop_label = tk.Label(
            self.drop, textvariable=self.drop_var, bg="#f4f8fb", fg="#1e3a5f",
            font=_font(UI, 11), justify="center", wraplength=420,
        )
        self.drop_label.pack(expand=True, padx=16)
        self.drop_enabled = _enable_drop(self.drop, self._on_drop) or _enable_drop(self.drop_label, self._on_drop)

        self.size_var = tk.StringVar(value="")
        self.size_label = tk.Label(card, textvariable=self.size_var, bg="#ffffff", fg="#5c6b78",
                                    font=self.ui_small, anchor="w", justify="left", wraplength=420)
        self.size_label.pack(fill="x", padx=12)

        buttons = tk.Frame(card, bg="#ffffff")
        buttons.pack(fill="x", padx=12, pady=(8, 12))
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)
        self.browse_button = ttk.Button(buttons, text="", command=self.browse)
        self.browse_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.save_button = ttk.Button(buttons, text="", command=self.save)
        self.save_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.convert_button = ttk.Button(buttons, text="", style="Convert.TButton",
                                          command=self.convert)
        self.convert_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _build_module_card(self, body):
        card = self._card(body, "gui.card.module")
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

        report_card = self._card(lower, "gui.card.validation")
        hex_card = self._card(lower, "gui.card.preview")
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
        self.hex_view.configure(state="disabled")

    def _set_text(self, widget, chunks):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for tag, text in chunks:
            widget.insert("end", text, tag or "")
        widget.configure(state="disabled")

    def _sync_profile_choices(self):
        labels = []
        self.by_label = {}
        for profile in self.profiles:
            label = profile_text(profile, "menu_label")
            labels.append(label)
            self.by_label[label] = profile
        self.profile_box.configure(values=labels)

    def _show_profile(self):
        profile = self.profile
        self._sync_profile_choices()
        self.profile_var.set(profile_text(profile, "menu_label"))
        colours = {
            "supported": "#1f7a4d",
            "experimental": "#8a5a00",
            "unsupported": "#9b2335",
        }
        self.badge.configure(text=profile.status_label, bg=colours[profile.status])
        chunks = [(profile.status, profile.status_label + "\n"), (None, profile.mcu + "\n\n")]
        chunks.append((None, profile_text(profile, "vehicles") + "\n\n"))
        chunks.append((None, profile_text(profile, "summary") + "\n\n"))
        if profile.conversion_enabled:
            chunks.append((None, t(
                "gui.profile.dflash_line",
                nbytes=profile.dflash_size,
                sectors=profile.scan_sectors,
                sector=profile.layout.sector_size,
            ) + "\n"))
            chunks.append((None, t("gui.profile.eee_line", n=profile.eee_size) + "\n"))
            chunks.append((None, profile.partition_text() + "\n"))
            chunks.append((None, t("gui.profile.record_format") + "\n\n"))
        chunks.append(("heading", t("gui.profile.write_back") + "\n"))
        chunks.append((None, profile_text(profile, "write_notes") + "\n\n"))
        assumptions = profile_text(profile, "assumptions")
        if assumptions:
            chunks.append(("heading", t("gui.profile.why_experimental") + "\n"))
            chunks.append(("assumption", assumptions + "\n\n"))
        chunks.append(("heading", t("gui.profile.sources") + "\n"))
        for source in localized_sources(profile):
            chunks.append(("source", "- " + source + "\n"))
        if self.profile_errors:
            chunks.append(("heading", "\n" + t("gui.profile.dropin_problems") + "\n"))
            for error in self.profile_errors:
                chunks.append(("error", error + "\n"))
        self._set_text(self.details, chunks)
        if profile.conversion_enabled:
            self.convert_button.configure(text=t("gui.button.convert"), state="normal")
        else:
            self.convert_button.configure(text=t("gui.button.convert_disabled"), state="disabled")
        self._refresh_size_label()

    def _on_profile(self, _event=None):
        label = self.profile_var.get()
        profile = self.by_label.get(label)
        if profile is None or profile.id == self.profile.id:
            return
        self.profile = profile
        self.result = None
        self._profile_stale = self.data is not None
        self._set_save_enabled(False)
        self._show_profile()
        self._set_text(self.report, [("warning", t("gui.report.profile_changed") + "\n")])
        self._set_hex(t("gui.hex.cleared"))
        self.status.set(profile_text(profile, "menu_label"))

    def _on_drop(self, payload):
        paths = parse_drop_paths(payload)
        if not paths:
            return
        if len(paths) > 1:
            self.status.set(t("gui.status.several_dropped"))
        self.load_path(paths[0])

    def browse(self):
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=t("gui.dialog.open_title"),
            filetypes=[
                (t("gui.filetypes.dump"), "*.bin *.eep *.dflash *.dump"),
                (t("gui.filetypes.all"), "*.*"),
            ],
        )
        if selected:
            self.load_path(selected)

    def _refresh_drop_hint(self):
        if self.path is None or self.data is None:
            key = "gui.drop.hint" if self.drop_enabled else "gui.drop.no_dnd"
            self.drop_var.set(t(key))
            return
        self.drop_var.set(t(
            "gui.drop.loaded",
            name=shorten_middle(self.path.name, limit=36),
            n=len(self.data),
        ))

    def load_path(self, path):
        file_path = Path(path)
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            messagebox.showerror(t("gui.dialog.cannot_open"), str(exc), parent=self.root)
            return
        self.path = file_path
        self.data = data
        self.result = None
        self._profile_stale = False
        self._set_save_enabled(False)
        self.drop.configure(highlightbackground="#1e3a5f")
        self._refresh_drop_hint()
        self._refresh_size_label()
        self._set_text(self.report, [("info", t("gui.report.loaded") + "\n")])
        self._set_hex(t("gui.hex.preview_prompt"))
        self.status.set(t("gui.status.loaded", name=file_path.name, n=len(data)))

    def _refresh_size_label(self):
        if self.data is None:
            self.size_var.set(t("gui.size.none"))
            self.size_label.configure(fg="#5c6b78")
            return
        issue = size_issue(len(self.data), self.profile) if self.profile.conversion_enabled else None
        if issue is None:
            self.size_var.set(t("gui.size.ok", n=len(self.data)))
            self.size_label.configure(fg="#1f7a4d")
        elif issue.level == "error":
            self.size_var.set(issue.text)
            self.size_label.configure(fg="#9b2335")
        else:
            self.size_var.set(issue.text)
            self.size_label.configure(fg="#8a5a00")

    def _refresh_result_text(self):
        """Redraw validation text in the active language without a dialog."""

        if self._profile_stale:
            self._set_text(self.report, [("warning", t("gui.report.profile_changed") + "\n")])
            self._set_hex(t("gui.hex.cleared"))
            self.status.set(profile_text(self.profile, "menu_label"))
            return
        if self.data is not None and self.result is not None and self.profile.conversion_enabled:
            self.result = convert_bytes(self.data, self.profile, check_size=True)
            rows = [(level, text + "\n") for level, text in report_lines(self.result)]
            self._set_text(self.report, rows)
            if self.result.image is not None:
                self._set_hex(hex_dump(self.result.image))
                self._set_save_enabled(True)
                self.status.set(t(
                    "gui.status.image",
                    title=profile_text(self.profile, "title"),
                    n=len(self.result.image),
                ))
            else:
                self._set_hex(t("gui.hex.none"))
                self._set_save_enabled(False)
                self.status.set(t("gui.status.failed"))
            return
        if self.data is not None:
            issue = size_issue(len(self.data), self.profile) if self.profile.conversion_enabled else None
            if issue is not None and issue.level == "error":
                self._set_text(self.report, [("error", issue.text + "\n")])
                self._set_hex(t("gui.hex.wrong_size"))
                self.status.set(t("gui.status.wrong_size"))
            else:
                self._set_text(self.report, [("info", t("gui.report.loaded") + "\n")])
                self._set_hex(t("gui.hex.preview_prompt"))
                self.status.set(t("gui.status.loaded", name=self.path.name, n=len(self.data)))
            return
        self._set_text(self.report, [])
        self._set_hex(t("gui.hex.placeholder"))
        if self.profile_errors:
            self.status.set(t("gui.status.profile_load_error"))
        else:
            self.status.set(t("gui.status.idle"))

    def convert(self):
        if not self.profile.conversion_enabled:
            summary = profile_text(self.profile, "summary")
            notes = profile_text(self.profile, "write_notes")
            self._set_text(self.report, [("error", summary + "\n\n" + notes + "\n")])
            self.status.set(t("gui.status.not_converted"))
            return
        if self.data is None:
            messagebox.showinfo(t("gui.dialog.no_dump_title"), t("gui.dialog.no_dump"), parent=self.root)
            return
        issue = size_issue(len(self.data), self.profile)
        if issue is not None and issue.level == "error":
            self.result = None
            self._profile_stale = False
            self._set_save_enabled(False)
            self._set_text(self.report, [("error", issue.text + "\n")])
            self._set_hex(t("gui.hex.wrong_size"))
            self.status.set(t("gui.status.wrong_size"))
            return
        self._profile_stale = False
        self.result = convert_bytes(self.data, self.profile, check_size=True)
        rows = [(level, text + "\n") for level, text in report_lines(self.result)]
        self._set_text(self.report, rows)
        if self.result.image is not None:
            self._set_hex(hex_dump(self.result.image))
            self._set_save_enabled(True)
            self.status.set(t(
                "gui.status.image",
                title=profile_text(self.profile, "title"),
                n=len(self.result.image),
            ))
        else:
            self._set_hex(t("gui.hex.none"))
            self._set_save_enabled(False)
            self.status.set(t("gui.status.failed"))

    def _set_hex(self, text):
        self.hex_view.configure(state="normal")
        self.hex_view.delete("1.0", "end")
        self.hex_view.insert("1.0", text)
        self.hex_view.configure(state="disabled")

    def _set_save_enabled(self, enabled):
        self.save_button.state(["!disabled"] if enabled else ["disabled"])

    def save(self):
        if self.result is None or self.result.image is None:
            messagebox.showinfo(t("gui.dialog.nothing_title"), t("gui.dialog.nothing"), parent=self.root)
            return
        if self.result.errors or self.result.corrupt:
            proceed = messagebox.askokcancel(
                t("gui.dialog.save_anyway_title"),
                t("gui.dialog.save_anyway"),
                parent=self.root,
            )
            if not proceed:
                return
        initial = "eeprom.bin"
        if self.path is not None:
            initial = self.path.stem + "_eee.bin"
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title=t("gui.dialog.save_title"),
            initialfile=initial,
            defaultextension=".bin",
            filetypes=[
                (t("gui.filetypes.binary"), "*.bin"),
                (t("gui.filetypes.all"), "*.*"),
            ],
        )
        if not selected:
            return
        try:
            Path(selected).write_bytes(self.result.image)
        except OSError as exc:
            messagebox.showerror(t("gui.dialog.cannot_save"), str(exc), parent=self.root)
            return
        self.status.set(t("gui.status.saved", path=selected, n=len(self.result.image)))
        messagebox.showinfo(
            t("gui.dialog.saved_title"),
            t("gui.dialog.saved", path=selected, notes=profile_text(self.profile, "write_notes")),
            parent=self.root,
        )

    def _safety_dialog(self):
        messagebox.showwarning(
            t("gui.dialog.safety_title"),
            t("gui.safety") + "\n\n" + profile_text(self.profile, "write_notes"),
            parent=self.root,
        )

    def _about(self):
        if RELEASE_TAG:
            build = t("gui.about.release", tag=RELEASE_TAG, commit=COMMIT)
        else:
            build = t("gui.about.source_build")
        messagebox.showinfo(
            t("gui.dialog.about_title"),
            t(
                "gui.dialog.about",
                version=__version__,
                notice=t("gui.free_notice"),
                build=build,
            ),
            parent=self.root,
        )

    def verify_this_copy(self):
        """Recompute the local hash and, when this is a tagged exe, compare it online."""

        try:
            self.show_trust_dialog(check_online=True)
        except tk.TclError:
            return

    def offer_first_run_notice(self):
        """Show the SmartScreen and free-software notice once. No network."""

        try:
            path = config_path()
            if notice_seen(path):
                return
        except Exception:
            return

        def acknowledge():
            try:
                mark_notice_seen(path)
            except OSError:
                pass

        try:
            self.show_trust_dialog(check_online=False, on_dismiss=acknowledge)
        except tk.TclError:
            return

    def show_trust_dialog(self, check_online=False, on_dismiss=None):
        copy = identify_running_copy()
        dialog = tk.Toplevel(self.root)
        self._trust_dialog = dialog
        dialog.title(t("gui.verify.title"))
        dialog.configure(bg="#ffffff")
        dialog.transient(self.root)
        dialog.minsize(560, 440)
        dialog.geometry("660x560")

        header = tk.Frame(dialog, bg="#1e3a5f")
        header.pack(fill="x")
        tk.Label(header, text=t("gui.verify.title"), bg="#1e3a5f", fg="#f7f4ef",
                 font=self.ui_bold, anchor="w").pack(fill="x", padx=16, pady=12)

        closed = {"done": False}

        def dismiss(_event=None):
            if closed["done"]:
                return
            closed["done"] = True
            try:
                dialog.destroy()
            except tk.TclError:
                pass
            if on_dismiss is not None:
                on_dismiss()

        self._trust_dismiss = dismiss
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        dialog.bind("<Escape>", dismiss)

        buttons = tk.Frame(dialog, bg="#ffffff")
        buttons.pack(fill="x", side="bottom", padx=16, pady=12)
        ttk.Button(
            buttons, text=t("gui.verify.open_release"),
            command=lambda: webbrowser.open(release_page_url(RELEASE_TAG)),
        ).pack(side="left")
        ttk.Button(
            buttons, text=t("gui.verify.open_source"),
            command=lambda: webbrowser.open(SOURCE_REPOSITORY_URL),
        ).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text=t("gui.verify.dismiss"), command=dismiss).pack(side="right")

        self._trust_status = tk.StringVar()
        status_label = tk.Label(
            dialog, textvariable=self._trust_status, bg="#ffffff", fg="#1c2833",
            font=self.ui_small, anchor="w", justify="left", wraplength=620,
        )
        status_label.pack(fill="x", side="bottom", padx=16, pady=(0, 4))

        if copy.kind == "executable" and copy.sha256:
            hash_frame = tk.Frame(dialog, bg="#ffffff")
            hash_frame.pack(fill="x", side="bottom", padx=16, pady=(0, 8))
            tk.Label(hash_frame, text="SHA-256", bg="#ffffff", fg="#1e3a5f",
                     font=self.ui_bold, anchor="w").pack(fill="x")
            entry = tk.Entry(
                hash_frame, font=self.mono, readonlybackground="#f4f8fb", relief="flat",
            )
            entry.insert(0, copy.sha256)
            entry.configure(state="readonly")
            entry.pack(fill="x", pady=(4, 0))

        text_frame = tk.Frame(dialog, bg="#ffffff")
        text_frame.pack(fill="both", expand=True, padx=12, pady=(8, 4))
        body = tk.Text(
            text_frame, wrap="word", font=self.ui_small, bg="#ffffff", fg="#1c2833",
            relief="flat", highlightthickness=0, padx=8, pady=8,
        )
        scroll = ttk.Scrollbar(text_frame, command=body.yview)
        body.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        body.pack(side="left", fill="both", expand=True)
        body.insert("1.0", notice_body(copy, RELEASE_TAG))
        body.configure(state="disabled")
        self._trust_body = body

        plan = verification_plan(copy, RELEASE_TAG, check_online)
        self._trust_status.set(status_for_plan(plan, RELEASE_TAG))
        if plan in ("source", "unreadable", "no_tag"):
            status_label.configure(fg=_VERIFY_COLOUR["could_not_check"])
        if plan == "download":
            self._start_online_check(dialog, status_label, copy.sha256)

        try:
            dialog.lift()
            dialog.focus_set()
        except tk.TclError:
            pass
        return dialog

    def _start_online_check(self, dialog, status_label, local_hash):
        """Fetch SHA256SUMS.txt off the UI thread. A timeout applies inside verify_release."""

        token = object()
        dialog._verify_token = token

        def apply(result, token=token):
            if getattr(dialog, "_verify_token", None) is not token:
                return
            try:
                if not dialog.winfo_exists():
                    return
            except tk.TclError:
                return
            if result is None:
                self._trust_status.set(t("gui.verify.download_failed"))
                status_label.configure(fg=_VERIFY_COLOUR["could_not_check"])
                return
            self._trust_status.set(result.detail)
            status_label.configure(fg=_VERIFY_COLOUR.get(result.status, "#1c2833"))

        def work():
            try:
                result = verify_release(RELEASE_TAG, local_hash, timeout=5.0)
            except Exception:
                result = None
            try:
                self.root.after(0, lambda result=result: apply(result))
            except tk.TclError:
                pass

        threading.Thread(target=work, name="verify-copy", daemon=True).start()


def _take_lang(args: list[str]) -> tuple[str | None, list[str]]:
    if args and args[0] == "--lang" and len(args) >= 2:
        return args[1], args[2:]
    if args and args[0].startswith("--lang="):
        return args[0].split("=", 1)[1], args[1:]
    return None, args


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    lang, args = _take_lang(args)
    if lang:
        if normalize(lang) is None:
            init_language(honor_env=False)
            print(t("cli.err.bad_lang", lang=lang, choices="en, es"), file=sys.stderr)
        else:
            # One launch only. The menu is what writes the saved choice.
            set_language(normalize(lang))
    else:
        ensure_language()
    root, _drop = make_root()
    app = App(root)
    if args:
        candidate = Path(args[0])
        if candidate.is_file():
            app.load_path(str(candidate))
    # Local notice only. Comparing with GitHub waits until Help → Verify this copy.
    root.after(200, app.offer_first_run_notice)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
