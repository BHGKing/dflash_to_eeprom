# Copyright 2017-2018 Ben van Leeuwen Autotechniek, https://www.benvanleeuwen.com/
# Copyright 2026 Jose
# Licensed under the BSD-3-Clause license. The full notice is in LICENSE.

"""Side-by-side hex compare window.

This module is loaded by the desktop tool and adds its own Tools menu.
The File menu, Help menu and first-run notices stay in gui.py.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from bisect import bisect_left
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from dflash_eee.compare import (
    ROW_BYTES,
    DiffIndex,
    clamp_top,
    column_header,
    compare_bytes,
    format_range_line,
    row_cells,
    row_count,
    visible_rows,
    write_report,
)
from dflash_eee.i18n import t
from dflash_eee.report import shorten_middle


def attach_compare(app) -> None:
    """Add Tools → Compare files... to the current menu bar.

    The desktop window rebuilds its menu when the language changes, so this
    runs after every rebuild. It does not keep a one-shot flag.
    """

    menu_name = app.root.cget("menu")
    if not menu_name:
        return
    bar = app.root.nametowidget(menu_name)
    tools_label = t("gui.menu.tools")
    if _cascade_index(bar, tools_label) is not None:
        return
    tools = tk.Menu(bar, tearoff=0)
    tools.add_command(label=t("gui.menu.compare"), command=lambda: open_compare_window(app))
    help_index = _cascade_index(bar, t("gui.menu.help"))
    if help_index is None:
        bar.add_cascade(label=tools_label, menu=tools)
    else:
        bar.insert_cascade(help_index, label=tools_label, menu=tools)
    app._compare_window = getattr(app, "_compare_window", None)
    app.open_compare = lambda: open_compare_window(app)


def open_compare_window(app):
    """Open the compare window, or bring the existing one forward."""

    existing = getattr(app, "_compare_window", None)
    if existing is not None and existing.is_open():
        existing.top.lift()
        try:
            existing.top.focus_set()
        except tk.TclError:
            pass
        return existing
    window = CompareWindow(app)
    app._compare_window = window
    return window


def _cascade_index(menu, label: str):
    last = menu.index("end")
    if last is None:
        return None
    for index in range(last + 1):
        if menu.type(index) == "cascade" and menu.entrycget(index, "label") == label:
            return index
    return None


def _wheel_steps(event) -> int:
    number = getattr(event, "num", None)
    if number == 4:
        return -3
    if number == 5:
        return 3
    delta = getattr(event, "delta", 0) or 0
    if delta == 0:
        return 0
    direction = -1 if delta > 0 else 1
    ticks = abs(delta) // 120
    if ticks < 1:
        ticks = 1
    return direction * ticks


class CompareWindow:
    """Two locked hex views, a clickable range list, and a report export."""

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title(t("gui.compare.title"))
        self.top.geometry("1280x760")
        self.top.minsize(860, 520)
        self.top.configure(bg="#e6edf3")
        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self.mono = app.mono
        self.ui = app.ui
        self.ui_bold = app.ui_bold
        self.ui_small = app.ui_small
        try:
            self.line_height = tkfont.Font(self.top, font=self.mono).metrics("linespace") or 16
        except tk.TclError:
            self.line_height = 16
        if self.line_height < 1:
            self.line_height = 16

        self.data_a: bytes | None = None
        self.data_b: bytes | None = None
        self.name_a = ""
        self.name_b = ""
        self.path_a: Path | None = None
        self.path_b: Path | None = None
        self.result = None
        self.index: DiffIndex | None = None
        self.diff_rows: list[int] = []
        self.total_rows = 0
        self.span = 0
        self.top_row = 0
        self.page_rows = 18
        self.selected: int | None = None
        self.range_top = 0
        self.range_page = 8
        self._rendering = False
        # Tests pin the visible row count. A live window leaves this false so
        # a resize shows more rows without drawing the whole file.
        self._page_locked = False
        self.origin_a = ""
        self.origin_b = ""
        self.source_a = ""
        self.source_b = ""
        self._status_key = "gui.compare.choose_hex"
        self._status_kwargs: dict = {}

        self._build()
        self._prefill_last_conversion()
        for sequence in ("<Control-o>", "<Control-O>", "<Control-s>", "<Control-S>", "<Control-Return>"):
            self.top.bind(sequence, self._swallow)
        self.top.bind("<F3>", self._on_next_key)
        self.top.bind("<Shift-F3>", self._on_prev_key)
        self.top.bind("<Next>", self._on_page_down)
        self.top.bind("<Prior>", self._on_page_up)
        self.top.bind("<Home>", self._on_home)
        self.top.bind("<End>", self._on_end)
        self.top.bind("<Configure>", self._on_window_configure)
        self.app._compare_window = self

    def is_open(self) -> bool:
        try:
            return bool(self.top.winfo_exists())
        except tk.TclError:
            return False

    def close(self) -> None:
        if getattr(self.app, "_compare_window", None) is self:
            self.app._compare_window = None
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    def refresh_language(self) -> None:
        """Apply the current catalog to this window, including an open compare."""

        self.top.title(t("gui.compare.title"))
        self.title_label.configure(text=t("gui.compare.title"))
        self.subtitle_label.configure(text=t("gui.compare.subtitle"))
        self.file_a_label.configure(text=t("gui.compare.file_a"))
        self.file_b_label.configure(text=t("gui.compare.file_b"))
        self.pane_a_label.configure(text=t("gui.compare.file_a"))
        self.pane_b_label.configure(text=t("gui.compare.file_b"))
        self.only_check.configure(text=t("gui.compare.only_rows"))
        self.prev_btn.configure(text=t("gui.compare.previous"))
        self.next_btn.configure(text=t("gui.compare.next"))
        self.export_text_btn.configure(text=t("gui.compare.export_text"))
        self.export_csv_btn.configure(text=t("gui.compare.export_csv"))
        self.hint_label.configure(text=t("gui.compare.hint"))
        self.range_heading.configure(text=t("gui.compare.range_heading"))
        for button in (self.browse_a, self.browse_b):
            button.configure(text=t("gui.button.browse"))
        for button in (self.last_a, self.last_b):
            button.configure(text=t("gui.compare.last_button"))
        self._apply_side_label("a")
        self._apply_side_label("b")
        if self.result is None:
            self.summary.configure(text=t("gui.compare.choose"))
        else:
            tone = "#1f7a4d" if self.result.identical else "#9b2335"
            self.summary.configure(text=self.result.summary_text(), fg=tone)
        self._note(self._status_key, **self._status_kwargs)
        self._render_hex()
        self._render_ranges()

    def browse(self, side: str) -> None:
        selected = filedialog.askopenfilename(
            parent=self.top,
            title=t("gui.compare.choose_side", side=side.upper()),
            filetypes=[
                (t("gui.filetypes.dump"), "*.bin *.eep *.dflash *.dump *.eee *.rom"),
                (t("gui.filetypes.all"), "*.*"),
            ],
        )
        if selected:
            self.load_file(side, selected)

    def load_file(self, side: str, path) -> bool:
        file_path = Path(path)
        try:
            data = file_path.read_bytes()
        except OSError as exc:
            messagebox.showerror(
                t("gui.dialog.cannot_open"),
                t("gui.compare.open_error", path=file_path, error=exc),
                parent=self.top,
            )
            return False
        self._set_side(side, data, file_path.name, file_path)
        return True

    def use_last_conversion(self, side: str, quiet: bool = False) -> bool:
        """Fill one side from the EEPROM image in the main window."""

        result = getattr(self.app, "result", None)
        image = None if result is None else getattr(result, "image", None)
        if not image:
            if not quiet:
                messagebox.showinfo(
                    t("gui.compare.no_conversion_title"),
                    t("gui.compare.no_conversion"),
                    parent=self.top,
                )
            return False
        source = getattr(self.app, "path", None)
        source_name = "" if source is None else Path(source).name
        data = image if isinstance(image, bytes) else bytes(image)
        self._set_side(side, data, source_name, None, origin="last")
        return True

    def export_to(self, path, kind: str | None = None) -> None:
        if self.result is None:
            raise RuntimeError(t("gui.compare.export_before"))
        write_report(path, self.result, self._report_name("a"), self._report_name("b"), kind)
        self._note("cli.compare.wrote", path=path)

    def set_top(self, top: int) -> None:
        page = self.page_rows if self.page_rows > 0 else 1
        self.top_row = clamp_top(top, self._scroll_length(), page)
        self._render_hex()

    def select_range(self, index: int) -> None:
        if self.result is None or not self.result.ranges:
            return
        if index < 0:
            index = 0
        if index >= len(self.result.ranges):
            index = len(self.result.ranges) - 1
        self.selected = index
        self._reveal_range(index)
        self._scroll_to_selected()
        self._render_ranges()
        item = self.result.ranges[index]
        self._note(
            "gui.compare.range_status",
            n=index + 1,
            total=len(self.result.ranges),
            offset="%08X" % item.offset,
        )

    def next_difference(self) -> None:
        if self.result is None or not self.result.ranges:
            return
        if self.selected is None:
            self.select_range(0)
            return
        if self.selected + 1 >= len(self.result.ranges):
            self._note("gui.compare.last_diff")
            return
        self.select_range(self.selected + 1)

    def previous_difference(self) -> None:
        if self.result is None or not self.result.ranges:
            return
        if self.selected is None:
            self.select_range(0)
            return
        if self.selected == 0:
            self._note("gui.compare.first_diff")
            return
        self.select_range(self.selected - 1)

    def _build(self) -> None:
        header = tk.Frame(self.top, bg="#1e3a5f", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)
        self.title_label = tk.Label(header, text=t("gui.compare.title"), bg="#1e3a5f", fg="#f7f4ef",
                                    font=self.ui_bold, anchor="w")
        self.title_label.pack(fill="x", padx=16, pady=(8, 0))
        self.subtitle_label = tk.Label(
            header, text=t("gui.compare.subtitle"),
            bg="#1e3a5f", fg="#c5d4e4", font=self.ui_small, anchor="w",
        )
        self.subtitle_label.pack(fill="x", padx=16)

        body = tk.Frame(self.top, bg="#e6edf3")
        body.pack(fill="both", expand=True, padx=12, pady=10)

        card = tk.Frame(body, bg="#ffffff", highlightthickness=1, highlightbackground="#c5d0da")
        card.pack(fill="x", pady=(0, 8))
        inner = tk.Frame(card, bg="#ffffff")
        inner.pack(fill="x", padx=12, pady=10)
        self.label_a = tk.StringVar(value=t("gui.compare.no_file"))
        self.label_b = tk.StringVar(value=t("gui.compare.no_file"))
        self._file_row(inner, "a", self.label_a).pack(fill="x", pady=(0, 6))
        self._file_row(inner, "b", self.label_b).pack(fill="x")

        tools = tk.Frame(inner, bg="#ffffff")
        tools.pack(fill="x", pady=(8, 0))
        self.only = tk.BooleanVar(value=False)
        self.only_check = ttk.Checkbutton(
            tools, text=t("gui.compare.only_rows"), variable=self.only, command=self._toggle_only,
        )
        self.only_check.pack(side="left")
        self.prev_btn = ttk.Button(tools, text=t("gui.compare.previous"), command=self.previous_difference)
        self.prev_btn.pack(side="left", padx=(12, 0))
        self.next_btn = ttk.Button(tools, text=t("gui.compare.next"), command=self.next_difference)
        self.next_btn.pack(side="left", padx=(6, 0))
        self.export_csv_btn = ttk.Button(tools, text=t("gui.compare.export_csv"), command=lambda: self._export("csv"))
        self.export_csv_btn.pack(side="right")
        self.export_text_btn = ttk.Button(tools, text=t("gui.compare.export_text"), command=lambda: self._export("text"))
        self.export_text_btn.pack(side="right", padx=(0, 6))

        self.hint_label = tk.Label(
            inner, text=t("gui.compare.hint"), bg="#ffffff", fg="#5c6b78", font=self.ui_small,
            anchor="w", justify="left", wraplength=1100,
        )
        self.hint_label.pack(fill="x", pady=(8, 4))
        self.summary = tk.Label(inner, text=t("gui.compare.choose"), bg="#ffffff", fg="#1c2833",
                                font=self.mono, anchor="w", justify="left", wraplength=1100)
        self.summary.pack(fill="x")

        paned = ttk.Panedwindow(body, orient="vertical")
        paned.pack(fill="both", expand=True)
        hex_card = tk.Frame(paned, bg="#ffffff", highlightthickness=1, highlightbackground="#c5d0da")
        range_card = tk.Frame(paned, bg="#ffffff", highlightthickness=1, highlightbackground="#c5d0da")
        paned.add(hex_card, weight=3)
        paned.add(range_card, weight=1)
        self._build_hex(hex_card)
        self._build_ranges(range_card)

        status_bar = tk.Frame(self.top, bg="#d5dee6", height=26)
        status_bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value=t(self._status_key))
        tk.Label(status_bar, textvariable=self.status, bg="#d5dee6", fg="#1c2833",
                 font=self.ui_small, anchor="w").pack(fill="x", padx=12, pady=3)
        self._refresh_actions()

    def _file_row(self, parent, side, variable):
        row = tk.Frame(parent, bg="#ffffff")
        title_key = "gui.compare.file_a" if side == "a" else "gui.compare.file_b"
        title = tk.Label(row, text=t(title_key), bg="#ffffff", fg="#1e3a5f", font=self.ui_bold,
                         width=12, anchor="w")
        title.pack(side="left")
        tk.Label(row, textvariable=variable, bg="#f4f8fb", fg="#1c2833", font=self.ui,
                 anchor="w", padx=8, pady=4).pack(side="left", fill="x", expand=True, padx=(0, 8))
        browse = ttk.Button(row, text=t("gui.button.browse"), command=lambda: self.browse(side))
        browse.pack(side="left")
        last = ttk.Button(row, text=t("gui.compare.last_button"), command=lambda: self.use_last_conversion(side))
        last.pack(side="left", padx=(6, 0))
        if side == "a":
            self.file_a_label = title
            self.browse_a = browse
            self.last_a = last
        else:
            self.file_b_label = title
            self.browse_b = browse
            self.last_b = last
        return row

    def _build_hex(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, minsize=8)
        parent.columnconfigure(2, weight=1)
        parent.rowconfigure(2, weight=1)
        self.pane_a_label = tk.Label(parent, text=t("gui.compare.file_a"), bg="#ffffff", fg="#1e3a5f",
                                     font=self.ui_bold, anchor="w")
        self.pane_b_label = tk.Label(parent, text=t("gui.compare.file_b"), bg="#ffffff", fg="#1e3a5f",
                                     font=self.ui_bold, anchor="w")
        self.pane_a_label.grid(row=0, column=0, sticky="ew", padx=(8, 0), pady=(6, 0))
        self.pane_b_label.grid(row=0, column=2, sticky="ew", padx=(8, 0), pady=(6, 0))
        self.head_a = self._hex_widget(parent, header=True)
        self.head_b = self._hex_widget(parent, header=True)
        self.hex_a = self._hex_widget(parent, header=False)
        self.hex_b = self._hex_widget(parent, header=False)
        self.head_a.grid(row=1, column=0, sticky="ew", padx=(8, 0))
        self.head_b.grid(row=1, column=2, sticky="ew", padx=(8, 0))
        self.hex_a.grid(row=2, column=0, sticky="nsew", padx=(8, 0), pady=(0, 4))
        self.hex_b.grid(row=2, column=2, sticky="nsew", padx=(8, 0), pady=(0, 4))
        self.yscroll = ttk.Scrollbar(parent, command=self._on_yscroll)
        self.yscroll.grid(row=1, column=3, rowspan=2, sticky="ns", padx=(4, 6), pady=(0, 4))
        self.xscroll = ttk.Scrollbar(parent, orient="horizontal", command=self._xview)
        self.xscroll.grid(row=3, column=0, columnspan=3, sticky="ew", padx=(8, 0), pady=(0, 6))
        self.hex_a.configure(yscrollcommand=self._ignore_scroll, xscrollcommand=self._on_xscroll)
        self.hex_b.configure(yscrollcommand=self._ignore_scroll)
        self._set_headers()
        for widget in (self.hex_a, self.hex_b, self.head_a, self.head_b):
            widget.bind("<MouseWheel>", self._on_hex_wheel)
            widget.bind("<Button-4>", self._on_hex_wheel)
            widget.bind("<Button-5>", self._on_hex_wheel)
            widget.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.hex_a.bind("<Configure>", self._on_hex_configure)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            parent.bind(sequence, self._on_hex_wheel)
        self._fill_widget(self.hex_a, t("gui.compare.choose_hex"))
        self._fill_widget(self.hex_b, t("gui.compare.choose_hex"))

    def _build_ranges(self, parent) -> None:
        self.range_heading = tk.Label(
            parent, text=t("gui.compare.range_heading"), bg="#ffffff", fg="#1e3a5f",
            font=self.ui_bold, anchor="w",
        )
        self.range_heading.pack(fill="x", padx=8, pady=(6, 2))
        frame = tk.Frame(parent, bg="#ffffff")
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.range_view = tk.Text(
            frame, wrap="none", font=self.mono, height=6, bg="#ffffff", fg="#1c2833",
            relief="flat", highlightthickness=1, highlightbackground="#c5d0da",
            padx=6, pady=4, cursor="hand2", state="disabled",
        )
        scroll = ttk.Scrollbar(frame, command=self._on_range_scroll)
        self.range_scroll = scroll
        self.range_xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.range_view.xview)
        self.range_view.configure(yscrollcommand=self._ignore_scroll, xscrollcommand=self.range_xscroll.set)
        self.range_xscroll.pack(side="bottom", fill="x")
        scroll.pack(side="right", fill="y")
        self.range_view.pack(side="left", fill="both", expand=True)
        self.range_view.tag_configure("range", foreground="#1c2833")
        self.range_view.tag_configure("selected", background="#1e3a5f", foreground="#ffffff")
        self.range_view.bind("<Button-1>", self._on_range_click)
        self.range_view.bind("<MouseWheel>", self._on_range_wheel)
        self.range_view.bind("<Button-4>", self._on_range_wheel)
        self.range_view.bind("<Button-5>", self._on_range_wheel)
        self.range_view.bind("<Configure>", self._on_range_configure)
        self._fill_widget(self.range_view, t("gui.compare.range_placeholder"))

    def _hex_widget(self, parent, header: bool):
        widget = tk.Text(
            parent, wrap="none", font=self.mono, height=1 if header else 12,
            bg="#0f1c2b", fg="#8fb4d4" if header else "#d6e2ee",
            relief="flat", highlightthickness=0, padx=8, pady=2 if header else 4,
            insertwidth=0, cursor="arrow", state="disabled",
        )
        if not header:
            widget.tag_configure("addr", foreground="#8fb4d4")
            widget.tag_configure("same", foreground="#d6e2ee")
            widget.tag_configure("diff", background="#7a2e24", foreground="#ffe8d6")
            widget.tag_configure("current", background="#1e4d7b", foreground="#ffffff")
            widget.tag_configure("missing", foreground="#6d7c8a")
            widget.tag_configure("pad", foreground="#0f1c2b")
            widget.tag_configure("gap", foreground="#d6e2ee")
        return widget

    def _set_headers(self) -> None:
        header = column_header()
        for widget in (self.head_a, self.head_b):
            self._fill_widget(widget, header)

    def _note(self, key: str, **kwargs) -> None:
        self._status_key = key
        self._status_kwargs = kwargs
        self.status.set(t(key, **kwargs))

    def _display_name(self, side: str) -> str:
        origin = self.origin_a if side == "a" else self.origin_b
        source = self.source_a if side == "a" else self.source_b
        if origin == "last":
            if source:
                return t("gui.compare.last_named", name=source)
            return t("gui.compare.last_plain")
        return source

    def _apply_side_label(self, side: str) -> None:
        data = self.data_a if side == "a" else self.data_b
        variable = self.label_a if side == "a" else self.label_b
        if data is None:
            variable.set(t("gui.compare.no_file"))
            if side == "a":
                self.name_a = ""
            else:
                self.name_b = ""
            return
        name = self._display_name(side)
        if side == "a":
            self.name_a = name
        else:
            self.name_b = name
        variable.set(t("gui.compare.file_bytes", name=shorten_middle(name, 48), n=len(data)))

    def _prefill_last_conversion(self) -> None:
        if self.use_last_conversion("b", quiet=True):
            self._note("gui.compare.prefill")

    def _set_side(self, side: str, data: bytes, source_name: str, path: Path | None, origin: str = "file") -> None:
        if side == "a":
            self.data_a = data
            self.path_a = path
            self.origin_a = origin
            self.source_a = source_name
        else:
            self.data_b = data
            self.path_b = path
            self.origin_b = origin
            self.source_b = source_name
        self._apply_side_label(side)
        self._note("gui.compare.loaded", name=self._display_name(side), n=len(data))
        if self.data_a is not None and self.data_b is not None:
            self._compare_now()

    def _compare_now(self) -> None:
        if self.data_a is None or self.data_b is None:
            return
        self.summary.configure(text=t("gui.compare.comparing"), fg="#1c2833")
        try:
            self.top.configure(cursor="watch")
            self.top.update_idletasks()
        except tk.TclError:
            pass
        try:
            result = compare_bytes(self.data_a, self.data_b)
        finally:
            try:
                self.top.configure(cursor="")
            except tk.TclError:
                pass
        self.result = result
        self.index = DiffIndex(result.ranges)
        self.span = result.size_a if result.size_a > result.size_b else result.size_b
        self.total_rows = row_count(self.span)
        self.diff_rows = self.index.differing_rows(self.span)
        self.selected = 0 if result.ranges else None
        self.range_top = 0
        self.top_row = 0
        tone = "#1f7a4d" if result.identical else "#9b2335"
        self.summary.configure(text=result.summary_text(), fg=tone)
        self._refresh_actions()
        if self.selected is not None:
            self._reveal_range(self.selected)
            self._scroll_to_selected()
            item = result.ranges[0]
            self._note(
                "gui.compare.range_status",
                n=1,
                total=len(result.ranges),
                offset="%08X" % item.offset,
            )
        else:
            self.set_top(0)
            self._note("gui.compare.identical_status")
        self._render_ranges()

    def _toggle_only(self) -> None:
        if self.selected is not None and self.result is not None and self.result.ranges:
            self._scroll_to_selected()
        else:
            self.set_top(0)

    def _scroll_length(self) -> int:
        if self.result is None:
            return 0
        if self.only.get():
            return len(self.diff_rows)
        return self.total_rows

    def _scroll_index_for_row(self, row: int) -> int:
        if not self.only.get():
            return row
        pos = bisect_left(self.diff_rows, row)
        if pos >= len(self.diff_rows):
            return max(0, len(self.diff_rows) - 1)
        return pos

    def _top_for_index(self, index: int) -> int:
        page = self.page_rows if self.page_rows > 0 else 1
        margin = 1 if page > 2 else 0
        top = index - margin
        if top < 0:
            top = 0
        if index >= top + page:
            top = index - page + 1
        return clamp_top(top, self._scroll_length(), page)

    def _scroll_to_selected(self) -> None:
        if self.selected is None or self.result is None or not self.result.ranges:
            self.set_top(0)
            return
        item = self.result.ranges[self.selected]
        row = item.offset // ROW_BYTES
        self.set_top(self._top_for_index(self._scroll_index_for_row(row)))

    def _visible_row_numbers(self) -> list[int]:
        return visible_rows(self.top_row, self.page_rows, self.total_rows, self.diff_rows, bool(self.only.get()))

    def _render_hex(self) -> None:
        if self._rendering:
            return
        self._rendering = True
        try:
            try:
                fraction = self.hex_a.xview()[0]
            except tk.TclError:
                fraction = 0.0
            if self.result is None or self.data_a is None or self.data_b is None:
                self._fill_widget(self.hex_a, t("gui.compare.choose_hex"))
                self._fill_widget(self.hex_b, t("gui.compare.choose_hex"))
            elif self.span == 0:
                self._fill_widget(self.hex_a, t("gui.compare.empty"))
                self._fill_widget(self.hex_b, t("gui.compare.empty"))
            else:
                rows = self._visible_row_numbers()
                if not rows:
                    message = t("gui.compare.no_diff_rows") if self.only.get() else t("gui.compare.nothing")
                    self._fill_widget(self.hex_a, message)
                    self._fill_widget(self.hex_b, message)
                else:
                    selected = None
                    if self.selected is not None and self.result.ranges:
                        selected = self.result.ranges[self.selected]
                    self._fill_rows(self.hex_a, self.data_a, rows, selected)
                    self._fill_rows(self.hex_b, self.data_b, rows, selected)
            if self.result is not None:
                for widget in (self.head_a, self.head_b, self.hex_a, self.hex_b):
                    try:
                        widget.xview_moveto(fraction)
                    except tk.TclError:
                        pass
            self._sync_yscroll()
        finally:
            self._rendering = False

    def _fill_rows(self, widget, data, rows, selected) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        for row in rows:
            for text, tag in row_cells(data, row, self.span, self.index, selected):
                widget.insert("end", text, tag)
        widget.configure(state="disabled")
        try:
            widget.yview_moveto(0)
        except tk.TclError:
            pass

    def _fill_widget(self, widget, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        try:
            widget.yview_moveto(0)
        except tk.TclError:
            pass

    def _sync_yscroll(self) -> None:
        span = self._scroll_length()
        if span <= 0:
            self.yscroll.set(0, 1)
            return
        page = self.page_rows if self.page_rows < span else span
        if page < 1:
            page = 1
        first = self.top_row / span
        last = (self.top_row + page) / span
        if last > 1:
            last = 1
        self.yscroll.set(first, last)

    def _on_yscroll(self, *args) -> None:
        span = self._scroll_length()
        if span <= 0 or not args:
            return
        page = self.page_rows if self.page_rows > 0 else 1
        if args[0] == "moveto":
            top = int(float(args[1]) * span)
        elif args[0] == "scroll":
            step = int(args[1])
            if len(args) > 2 and args[2] == "pages":
                step *= page - 1 if page > 1 else 1
            top = self.top_row + step
        else:
            return
        self.set_top(top)

    def _xview(self, *args) -> None:
        for widget in (self.head_a, self.head_b, self.hex_a, self.hex_b):
            try:
                widget.xview(*args)
            except tk.TclError:
                pass

    def _on_xscroll(self, first, last) -> None:
        self.xscroll.set(first, last)

    def _ignore_scroll(self, _first, _last) -> None:
        return None

    def _on_hex_wheel(self, event):
        self.set_top(self.top_row + _wheel_steps(event))
        return "break"

    def _on_shift_wheel(self, event):
        try:
            self.hex_a.xview_scroll(_wheel_steps(event), "units")
            fraction = self.hex_a.xview()[0]
        except tk.TclError:
            return "break"
        for widget in (self.head_a, self.head_b, self.hex_b):
            try:
                widget.xview_moveto(fraction)
            except tk.TclError:
                pass
        return "break"

    def _on_hex_configure(self, event) -> None:
        if self._page_locked or self._rendering or event.widget is not self.hex_a:
            return
        if event.height < self.line_height:
            return
        rows = (event.height - 8) // self.line_height
        if rows < 4:
            rows = 4
        if rows != self.page_rows:
            self.page_rows = rows
            self.set_top(self.top_row)

    def _render_ranges(self) -> None:
        if self.result is None:
            self._fill_widget(self.range_view, t("gui.compare.range_placeholder"))
            self.range_scroll.set(0, 1)
            return
        if not self.result.ranges:
            self._fill_widget(self.range_view, t("gui.compare.no_differences_view"))
            self.range_scroll.set(0, 1)
            return
        total = len(self.result.ranges)
        page = self.range_page if self.range_page > 0 else 1
        self.range_top = clamp_top(self.range_top, total, page)
        end = self.range_top + page
        if end > total:
            end = total
        self.range_view.configure(state="normal")
        self.range_view.delete("1.0", "end")
        for index in range(self.range_top, end):
            tag = "selected" if index == self.selected else "range"
            self.range_view.insert("end", format_range_line(self.result.ranges[index]) + "\n", tag)
        self.range_view.configure(state="disabled")
        first = self.range_top / total
        last = end / total
        if last > 1:
            last = 1
        self.range_scroll.set(first, last)

    def _reveal_range(self, index: int) -> None:
        if self.result is None or not self.result.ranges:
            return
        page = self.range_page if self.range_page > 0 else 1
        total = len(self.result.ranges)
        if index < self.range_top or index >= self.range_top + page:
            self.range_top = index - 1 if index > 0 else 0
        self.range_top = clamp_top(self.range_top, total, page)
        if index < self.range_top:
            self.range_top = index
        if index >= self.range_top + page:
            self.range_top = index - page + 1
        self.range_top = clamp_top(self.range_top, total, page)

    def _on_range_scroll(self, *args) -> None:
        if self.result is None or not self.result.ranges or not args:
            return
        total = len(self.result.ranges)
        page = self.range_page if self.range_page > 0 else 1
        if args[0] == "moveto":
            top = int(float(args[1]) * total)
        elif args[0] == "scroll":
            step = int(args[1])
            if len(args) > 2 and args[2] == "pages":
                step *= page - 1 if page > 1 else 1
            top = self.range_top + step
        else:
            return
        top = clamp_top(top, total, page)
        if top != self.range_top:
            self.range_top = top
            self._render_ranges()

    def _on_range_wheel(self, event):
        self._on_range_scroll("scroll", _wheel_steps(event), "units")
        return "break"

    def _on_range_configure(self, event) -> None:
        if self._page_locked or event.widget is not self.range_view or event.height < self.line_height:
            return
        rows = (event.height - 8) // self.line_height
        if rows < 3:
            rows = 3
        if rows != self.range_page:
            self.range_page = rows
            self._render_ranges()

    def _on_range_click(self, event) -> None:
        if self.result is None or not self.result.ranges:
            return
        where = self.range_view.index("@%d,%d" % (event.x, event.y))
        line = int(str(where).split(".")[0]) - 1
        rendered = self.range_page
        total = len(self.result.ranges)
        available = total - self.range_top
        if available < rendered:
            rendered = available
        if line < 0 or line >= rendered:
            return
        self.select_range(self.range_top + line)

    def _export(self, kind: str) -> None:
        if self.result is None:
            messagebox.showinfo(
                t("gui.compare.nothing_title"),
                t("gui.compare.nothing_body"),
                parent=self.top,
            )
            return
        if kind == "csv":
            extension = ".csv"
            types = [(t("gui.compare.filetype_csv"), "*.csv")]
            initial = t("gui.compare.initial_csv")
        else:
            extension = ".txt"
            types = [(t("gui.compare.filetype_text"), "*.txt")]
            initial = t("gui.compare.initial_text")
        selected = filedialog.asksaveasfilename(
            parent=self.top,
            title=t("gui.compare.export_title"),
            initialfile=initial,
            defaultextension=extension,
            filetypes=types,
        )
        if not selected:
            return
        path = Path(selected)
        if path.suffix.lower() != extension:
            path = path.with_suffix(extension)
        try:
            self.export_to(path, kind)
        except OSError as exc:
            messagebox.showerror(t("gui.dialog.cannot_save"), str(exc), parent=self.top)

    def _report_name(self, side: str) -> str:
        path = self.path_a if side == "a" else self.path_b
        if path is not None:
            return str(path)
        name = self.name_a if side == "a" else self.name_b
        return name or side.upper()

    def _refresh_actions(self) -> None:
        have = self.result is not None
        export_state = ["!disabled"] if have else ["disabled"]
        self.export_text_btn.state(export_state)
        self.export_csv_btn.state(export_state)
        move = bool(have and self.result.ranges)
        nav_state = ["!disabled"] if move else ["disabled"]
        self.prev_btn.state(nav_state)
        self.next_btn.state(nav_state)

    def _on_window_configure(self, event) -> None:
        if event.widget is not self.top:
            return
        width = event.width - 48
        if width < 320:
            width = 320
        self.summary.configure(wraplength=width)

    def _swallow(self, _event):
        return "break"

    def _on_next_key(self, _event):
        self.next_difference()
        return "break"

    def _on_prev_key(self, _event):
        self.previous_difference()
        return "break"

    def _on_page_down(self, _event):
        self.set_top(self.top_row + self.page_rows)
        return "break"

    def _on_page_up(self, _event):
        self.set_top(self.top_row - self.page_rows)
        return "break"

    def _on_home(self, _event):
        self.set_top(0)
        return "break"

    def _on_end(self, _event):
        self.set_top(self._scroll_length())
        return "break"
