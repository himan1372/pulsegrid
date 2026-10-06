"""Asset Browser: instruments, effects, presets and patterns, with search,
favorites and drag-and-drop.

Workflow (per the design playbook): find a sound -> drag it onto a
channel; find an effect -> drag it onto a mixer strip; find a preset
-> drag it onto a mixer strip (or double-click to apply to the selected
track); find a pattern -> drag it onto the playlist. Double-click a
pattern to edit it; right-click for pattern actions. The Presets section
is the content-library layer: factory presets plus the user's
~/.pulsegrid/presets folder, searchable, starrable, and rescanable --
closer to a DAW content browser than a file dialog.
"""

import os
import tkinter as tk
from tkinter import ttk

from ..project import FX_NAMES, INSTRUMENTS
from .widgets import Tooltip

INSTRUMENT_COLORS = {
    "kick": "#f778ba",
    "snare": "#ffa657",
    "hat": "#d2a8ff",
    "bass": "#7ee787",
    "lead": "#79c0ff",
}

INSTRUMENT_LABELS = {
    "kick": "Kick",
    "snare": "Snare",
    "hat": "Hat",
    "bass": "Bass",
    "lead": "Lead",
}

EFFECT_COLORS = {
    "delay": "#79c0ff",
    "drive": "#ffa657",
    "filter": "#7ee787",
}

DRAG_THRESHOLD = 6


class Browser(ttk.Frame):
    def __init__(self, master, dnd_start, on_edit_pattern, on_pattern_action,
                 on_apply_preset=None, on_add_sample=None,
                 on_sample_action=None):
        """dnd_start(kind, payload, label, event): begin a drag.
        on_edit_pattern(pattern_id): open pattern in the editors.
        on_pattern_action(action, pattern_id): 'rename' | 'duplicate' | 'delete'.
        on_apply_preset(entry_id): apply a library preset to the selected track.
        on_add_sample(): open the file picker and register a sample asset.
        on_sample_action(action, sample_id): 'remove'.
        """
        super().__init__(master)
        self._dnd_start = dnd_start
        self._on_edit_pattern = on_edit_pattern
        self._on_pattern_action = on_pattern_action
        self._on_apply_preset = on_apply_preset
        self._on_add_sample = on_add_sample
        self._on_sample_action = on_sample_action
        self._project = None
        self._press = None  # (kind, payload, label, x, y)
        self._presets: list = []  # PresetEntry list (content library index)
        self._starred_only = tk.BooleanVar(value=False)

        ttk.Label(self, text="Browser", font=("", 10, "bold"),
                  padding=(8, 4)).pack(anchor="w")

        search = ttk.Frame(self)
        search.pack(fill="x", padx=8, pady=(0, 4))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self.refresh())
        entry = ttk.Entry(search, textvariable=self._filter_var)
        entry.pack(fill="x")
        Tooltip(entry, "Filter instruments, effects, presets and patterns")

        self._body = ttk.Frame(self)
        self._body.pack(fill="both", expand=True)
        # Vertical scrolling for the section list (wheel = vertical).
        self._body_canvas = tk.Canvas(self._body, bg="#0d1117",
                                      highlightthickness=0)
        _body_vscroll = ttk.Scrollbar(self._body, orient="vertical",
                                      command=self._body_canvas.yview)
        self._body_canvas.pack(side="left", fill="both", expand=True)
        _body_vscroll.pack(side="right", fill="y")
        self._body_canvas.configure(yscrollcommand=_body_vscroll.set)
        self._body_inner = ttk.Frame(self._body_canvas)
        self._body_win = self._body_canvas.create_window(
            0, 0, window=self._body_inner, anchor="nw")
        self._body_inner.bind(
            "<Configure>",
            lambda _e: self._body_canvas.configure(
                scrollregion=self._body_canvas.bbox("all")))
        self._body_canvas.bind(
            "<Configure>",
            lambda e: self._body_canvas.itemconfigure(
                self._body_win, width=e.width))
        from .scroll import bind_wheel
        bind_wheel(self._body_canvas,
                   yscroll=lambda n: self._body_canvas.yview_scroll(n, "units"))
        # refresh() rebuilds sections inside _body_inner (below).

        self.refresh_presets()

    # -- data -----------------------------------------------------------

    def set_project(self, project) -> None:
        self._project = project
        self.refresh()

    def refresh_presets(self) -> None:
        """Rescan the preset library (factory + user folder)."""
        from ..content import scan_presets
        self._presets = scan_presets()
        self.refresh()

    def refresh(self) -> None:
        for child in self._body_inner.winfo_children():
            child.destroy()
        filt = self._filter_var.get().strip().lower()

        inst_frame = ttk.LabelFrame(self._body_inner, text="Instruments", padding=4)
        inst_frame.pack(fill="x", padx=8, pady=4)
        for inst in INSTRUMENTS:
            label = INSTRUMENT_LABELS[inst]
            if filt and filt not in label.lower():
                continue
            row = self._row(inst_frame, INSTRUMENT_COLORS[inst], label)
            self._bind_row(
                row,
                press=lambda e, k="instrument", p=inst, t=label:
                      self._press_begin(k, p, t, e),
                motion=self._press_motion,
                hint=f"Drag onto a channel to make it a {label}",
            )

        fx_frame = ttk.LabelFrame(self._body_inner, text="Effects", padding=4)
        fx_frame.pack(fill="x", padx=8, pady=4)
        for key, name in FX_NAMES.items():
            if filt and filt not in name.lower():
                continue
            row = self._row(fx_frame, EFFECT_COLORS.get(key, "#8b949e"), name)
            self._bind_row(
                row,
                press=lambda e, k="effect", p=key, t=name:
                      self._press_begin(k, p, t, e),
                motion=self._press_motion,
                hint=f"Drag onto a mixer strip to add {name}\n"
                     "(built-in effect -- no plug-ins)",
            )

        pre_outer = ttk.LabelFrame(self._body_inner, text="Presets", padding=4)
        pre_outer.pack(fill="x", padx=8, pady=4)
        pre_tools = ttk.Frame(pre_outer)
        pre_tools.pack(fill="x", pady=(0, 2))
        ttk.Checkbutton(pre_tools, text="Starred only",
                        variable=self._starred_only,
                        command=self.refresh).pack(side="left")
        ttk.Button(pre_tools, text="Rescan",
                   command=self.refresh_presets).pack(side="right")
        ttk.Button(pre_tools, text="Open folder",
                   command=self._open_presets_folder).pack(side="right",
                                                           padx=(0, 4))
        for entry_ in self._presets:
            if self._starred_only.get() and not entry_.favorite:
                continue
            hay = (f"{entry_.name} {entry_.preset.description} "
                   f"{entry_.kind}").lower()
            if filt and filt not in hay:
                continue
            self._preset_row(pre_outer, entry_)

        pat_frame = ttk.LabelFrame(self._body_inner, text="Patterns", padding=4)
        pat_frame.pack(fill="x", padx=8, pady=4)
        if self._project is not None:
            for i, pat in enumerate(self._project.patterns):
                if filt and filt not in pat.name.lower():
                    continue
                color = ["#f778ba", "#79c0ff", "#7ee787", "#ffa657",
                         "#d2a8ff"][i % 5]
                row = self._row(pat_frame, color, pat.name)
                self._bind_row(
                    row,
                    press=lambda e, k="pattern", p=pat.id, t=pat.name:
                          self._press_begin(k, p, t, e),
                    motion=self._press_motion,
                    double=lambda _e, pid=pat.id: self._on_edit_pattern(pid),
                    right=lambda e, pid=pat.id: self._pattern_menu(e, pid),
                    hint="Drag onto the playlist to place a clip\n"
                         "Double-click to edit | Right-click for actions",
                )

        samp_frame = ttk.LabelFrame(self._body_inner, text="Samples", padding=4)
        samp_frame.pack(fill="x", padx=8, pady=4)
        if self._on_add_sample is not None:
            ttk.Button(samp_frame, text="Add sample...",
                       command=self._on_add_sample).pack(fill="x", pady=(0, 4))
        if self._project is not None:
            for samp in self._project.samples:
                label = samp.name or samp.id
                if filt and filt not in label.lower():
                    continue
                row = self._row(samp_frame, "#58a6ff", label)
                self._bind_row(
                    row,
                    press=lambda e, k="sample", p=samp.id, t=label:
                          self._press_begin(k, p, t, e),
                    motion=self._press_motion,
                    right=lambda e, sid=samp.id: self._sample_menu(e, sid),
                    hint="Drag onto a playlist lane to place an audio clip\n"
                         "Right-click to remove",
                )

    def _sample_menu(self, event, sample_id: str) -> None:
        if self._on_sample_action is None:
            return
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(
            label="Remove sample",
            command=lambda: self._on_sample_action("remove", sample_id))
        menu.tk_popup(event.x_root, event.y_root)

    def _row(self, parent, color: str, text: str) -> ttk.Frame:
        row = ttk.Frame(parent, padding=(4, 3))
        row.pack(fill="x")
        dot = tk.Canvas(row, width=10, height=10, bg="#161b22",
                        highlightthickness=0)
        dot.pack(side="left", padx=(2, 6))
        dot.create_oval(1, 1, 9, 9, fill=color, outline="")
        label = ttk.Label(row, text=text)
        label.pack(side="left")
        return row

    def _bind_row(self, row, press=None, motion=None, double=None, right=None,
                  hint: str | None = None):
        """Bind gestures on a row and all its children (whole-row hit)."""
        targets = [row] + list(row.winfo_children())
        for target in targets:
            if hint is not None:
                Tooltip(target, hint)
            if press is not None:
                target.bind("<ButtonPress-1>", press, add="+")
            if motion is not None:
                target.bind("<B1-Motion>", motion, add="+")
            if double is not None:
                target.bind("<Double-Button-1>", double, add="+")
            if right is not None:
                target.bind("<Button-3>", right, add="+")

    def _pattern_menu(self, event, pattern_id: str) -> None:
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Edit pattern",
                         command=lambda: self._on_edit_pattern(pattern_id))
        menu.add_command(label="Duplicate",
                         command=lambda: self._on_pattern_action("duplicate", pattern_id))
        menu.add_command(label="Rename...",
                         command=lambda: self._on_pattern_action("rename", pattern_id))
        menu.add_separator()
        menu.add_command(label="Delete",
                         command=lambda: self._on_pattern_action("delete", pattern_id))
        menu.tk_popup(event.x_root, event.y_root)

    # -- preset library ---------------------------------------------------

    _PRESET_COLORS = {"effect": "#8b949e", "chain": "#ffa657",
                      "track": "#79c0ff"}
    _PRESET_LABELS = {"effect": "FX", "chain": "Chain", "track": "Track"}

    def _preset_row(self, parent, entry_) -> None:
        """One library row: star toggle, color dot, name, type badge."""
        row = ttk.Frame(parent, padding=(4, 3))
        row.pack(fill="x")
        star = tk.Button(row,
                         text="[*]" if entry_.favorite else "[ ]",
                         width=3, relief="flat",
                         fg="#e3b341" if entry_.favorite else "#8b949e",
                         bg="#161b22", activebackground="#21262d",
                         command=lambda e=entry_: self._toggle_favorite(e))
        star.pack(side="left", padx=(0, 4))
        dot = tk.Canvas(row, width=10, height=10, bg="#161b22",
                        highlightthickness=0)
        dot.pack(side="left", padx=(2, 6))
        dot.create_oval(1, 1, 9, 9,
                        fill=self._PRESET_COLORS.get(entry_.kind, "#8b949e"),
                        outline="")
        ttk.Label(row, text=entry_.name).pack(side="left")
        badge = self._PRESET_LABELS.get(entry_.kind, entry_.kind)
        src = "Built-in" if entry_.path is None else "User"
        ttk.Label(row, text=f"{badge} - {src}",
                  foreground="#8b949e").pack(side="right")
        hint = entry_.preset.description or entry_.name
        self._bind_row(
            row,
            press=lambda e, eid=entry_.entry_id, t=entry_.name:
                  self._press_begin("preset", eid, t, e),
            motion=self._press_motion,
            double=lambda _e, eid=entry_.entry_id: self._apply_preset(eid),
            right=lambda e, en=entry_: self._preset_menu(e, en),
            hint=f"{hint}\nDrag onto a mixer strip to apply\n"
                 "Double-click to apply to the selected track",
        )

    def _apply_preset(self, entry_id: str) -> None:
        if self._on_apply_preset is not None:
            self._on_apply_preset(entry_id)

    def _toggle_favorite(self, entry_) -> None:
        from ..content import toggle_favorite
        entry_.favorite = toggle_favorite(entry_.entry_id)
        self.refresh()

    def _preset_menu(self, event, entry_) -> None:
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="Apply to selected track",
                         command=lambda: self._apply_preset(entry_.entry_id))
        menu.add_command(
            label="Remove from starred" if entry_.favorite
            else "Add to starred",
            command=lambda: self._toggle_favorite(entry_))
        if entry_.path is not None:
            menu.add_separator()
            menu.add_command(label="Delete preset file...",
                             command=lambda: self._delete_preset_ui(entry_))
        menu.tk_popup(event.x_root, event.y_root)

    def _delete_preset_ui(self, entry_) -> None:
        from tkinter import messagebox
        if entry_.path is None:
            return
        if messagebox.askyesno(
                "Delete preset",
                f"Delete '{entry_.name}' from your presets folder?\n"
                "This cannot be undone.",
                parent=self):
            self.delete_preset_entry(entry_.entry_id)

    def delete_preset_entry(self, entry_id: str) -> None:
        """Delete a user preset file and rescan (no confirmation)."""
        from ..content import find_entry
        entry_ = find_entry(self._presets, entry_id)
        if entry_.path is None:
            raise ValueError("cannot delete a built-in preset")
        os.remove(entry_.path)
        self.refresh_presets()

    def _open_presets_folder(self) -> None:
        """Open the user presets folder in the OS file manager."""
        import subprocess
        import sys
        from ..content import ensure_user_presets_dir
        folder = ensure_user_presets_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa: S606 (local path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError:
            pass

    # -- drag initiation --------------------------------------------------

    def _press_begin(self, kind, payload, label, event) -> None:
        self._press = (kind, payload, label, event.x_root, event.y_root)

    def _press_motion(self, event) -> None:
        if self._press is None:
            return
        kind, payload, label, x0, y0 = self._press
        if abs(event.x_root - x0) < DRAG_THRESHOLD and abs(event.y_root - y0) < DRAG_THRESHOLD:
            return
        self._press = None
        self._dnd_start(kind, payload, label, event)
