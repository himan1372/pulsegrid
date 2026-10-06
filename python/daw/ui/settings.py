"""Settings panel: real, working preferences.

Every control here does something. UI scale and layout reset act
immediately; the unsaved-changes confirmation is honored on exit;
the backend and config-path rows are read-only information.
"""

import os
import tkinter as tk
from tkinter import ttk

from .widgets import Tooltip

SCALES = (("100%", 1.0), ("125%", 1.25), ("150%", 1.5))

# Animation refresh rates, mirroring FL Studio's General options.
# (label, mode) -- mode maps to a _poll_position interval in app.py.
REFRESH_RATES = (("Less smooth", "low"),
                 ("Smooth", "medium"),
                 ("Ultrasmooth", "high"))
REFRESH_TIPS = {
    "low": "Repaint playheads/meters ~15x/sec. Lowest CPU use.",
    "medium": "Repaint ~30x/sec. Good balance.",
    "high": "Repaint ~60x/sec. Smoothest, highest CPU use.",
}


class Settings(ttk.Frame):
    def __init__(self, master, initial_scale, on_scale, on_reset_layout,
                 get_confirm, on_confirm, get_backend, get_config_path,
                 get_refresh, on_refresh, get_plugin_dir, on_plugin_dir,
                 get_multithreaded, on_multithreaded):
        """on_scale(factor): apply a UI scale.
        on_reset_layout(): restore the default workspace layout.
        get_confirm()/on_confirm(bool): unsaved-changes prompt preference.
        get_backend()/get_config_path(): read-only info strings.
        get_refresh()/on_refresh(mode): animation refresh-rate preference.
        get_plugin_dir()/on_plugin_dir(path): CLAP plugin folder preference.
        """
        super().__init__(master)
        self._on_scale = on_scale
        self._scale_var = tk.DoubleVar(value=initial_scale)
        self._on_refresh = on_refresh
        self._on_plugin_dir = on_plugin_dir

        ttk.Label(self, text="Settings", font=("", 10, "bold"),
                  padding=(8, 4)).pack(anchor="w")

        display = ttk.LabelFrame(self, text="Display", padding=8)
        display.pack(fill="x", padx=8, pady=4)
        ttk.Label(display, text="UI scale:").pack(anchor="w")
        scale_row = ttk.Frame(display)
        scale_row.pack(anchor="w", pady=(4, 0))
        for label, factor in SCALES:
            rb = ttk.Radiobutton(scale_row, text=label, value=factor,
                                 variable=self._scale_var,
                                 command=self._scale_chosen)
            rb.pack(side="left", padx=(0, 10))
            Tooltip(rb, f"Scale the interface to {label}")

        # Animation refresh rate (mirrors FL Studio's General options):
        # controls how often playheads/meters repaint during playback.
        ttk.Label(display, text="Animation refresh rate:").pack(
            anchor="w", pady=(8, 0))
        self._refresh_var = tk.StringVar(value=get_refresh())
        refresh_row = ttk.Frame(display)
        refresh_row.pack(anchor="w", pady=(4, 0))
        for label, mode in REFRESH_RATES:
            rb = ttk.Radiobutton(refresh_row, text=label, value=mode,
                                 variable=self._refresh_var,
                                 command=self._refresh_chosen)
            rb.pack(side="left", padx=(0, 10))
            Tooltip(rb, REFRESH_TIPS[mode])

        # Audio: multithreading (FL-style global switch).
        audio = ttk.LabelFrame(self, text="Audio", padding=8)
        audio.pack(fill="x", padx=8, pady=4)
        self._mt_var = tk.BooleanVar(value=get_multithreaded())
        mt_chk = ttk.Checkbutton(
            audio, text="Multithreaded rendering (use all CPU cores)",
            variable=self._mt_var,
            command=lambda: on_multithreaded(bool(self._mt_var.get())))
        mt_chk.pack(anchor="w")
        Tooltip(mt_chk, "Render track voices in parallel across CPU cores. "
                "Disable if a plugin misbehaves (FL-style per-host opt-out).")

        workspace = ttk.LabelFrame(self, text="Workspace", padding=8)
        workspace.pack(fill="x", padx=8, pady=4)
        self._confirm_var = tk.BooleanVar(value=get_confirm())
        chk = ttk.Checkbutton(
            workspace, text="Ask before discarding unsaved changes",
            variable=self._confirm_var,
            command=lambda: on_confirm(bool(self._confirm_var.get())))
        chk.pack(anchor="w")
        Tooltip(chk, "Show a confirmation dialog when closing or\n"
                     "starting a new project with unsaved edits")
        btn = ttk.Button(workspace, text="Reset layout",
                         command=on_reset_layout)
        btn.pack(anchor="w", pady=(6, 0))
        Tooltip(btn, "Show all panels, reset sizes and UI scale")

        plugins = ttk.LabelFrame(self, text="Plugins", padding=8)
        plugins.pack(fill="x", padx=8, pady=4)
        ttk.Label(plugins, text="CLAP plugin folder:").pack(anchor="w")
        self._plugin_dir_var = tk.StringVar(value=get_plugin_dir())
        dir_row = ttk.Frame(plugins)
        dir_row.pack(fill="x", pady=(4, 0))
        self._plugin_dir_entry = ttk.Entry(dir_row,
                                           textvariable=self._plugin_dir_var)
        self._plugin_dir_entry.pack(side="left", fill="x", expand=True)
        Tooltip(self._plugin_dir_entry,
                "Folder scanned for .clap plugins.\n"
                "Also checks ~/.clap and /usr/lib/clap.\n"
                "Leave empty to use the defaults.")
        browse = ttk.Button(dir_row, text="Browse...",
                            command=self._browse_plugin_dir)
        browse.pack(side="left", padx=(6, 0))
        clear = ttk.Button(dir_row, text="Clear",
                           command=self._clear_plugin_dir)
        clear.pack(side="left", padx=(6, 0))
        Tooltip(browse, "Pick the folder containing your .clap files")
        Tooltip(clear, "Use the default plugin locations")

        system = ttk.LabelFrame(self, text="System", padding=8)
        system.pack(fill="x", padx=8, pady=4)
        ttk.Label(system, text="Audio backend:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w")
        self._backend_label = ttk.Label(system, text=get_backend(),
                                        font=("", 9))
        self._backend_label.pack(anchor="w", pady=(0, 6))
        Tooltip(self._backend_label,
                "Live audio output. If no device is found the app\n"
                "uses a silent null sink so the UI keeps working.")
        ttk.Label(system, text="Layout file:", foreground="#8b949e",
                  font=("", 9)).pack(anchor="w")
        path_label = ttk.Label(system, text=get_config_path(), font=("", 9),
                               wraplength=220)
        path_label.pack(anchor="w")
        Tooltip(path_label, "Panel layout and preferences are saved here.")

    # -- external sync (the app calls these when state changes elsewhere)

    def sync_scale(self, factor: float) -> None:
        self._scale_var.set(factor)

    def sync_confirm(self, value: bool) -> None:
        self._confirm_var.set(value)

    def sync_refresh(self, mode: str) -> None:
        self._refresh_var.set(mode)

    def sync_plugin_dir(self, path: str) -> None:
        self._plugin_dir_var.set(path or "")

    def refresh_backend(self, backend: str) -> None:
        self._backend_label.config(text=backend)

    def _scale_chosen(self) -> None:
        self._on_scale(self._scale_var.get())

    def _refresh_chosen(self) -> None:
        self._on_refresh(self._refresh_var.get())

    def _browse_plugin_dir(self) -> None:
        from tkinter import filedialog
        initial = self._plugin_dir_var.get() or "~"
        path = filedialog.askdirectory(title="Choose CLAP plugin folder",
                                       initialdir=os.path.expanduser(initial))
        if path:
            self._plugin_dir_var.set(path)
            self._on_plugin_dir(path)

    def _clear_plugin_dir(self) -> None:
        self._plugin_dir_var.set("")
        self._on_plugin_dir("")
