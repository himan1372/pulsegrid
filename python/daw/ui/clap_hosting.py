"""CLAP plugin hosting UI: plugin picker, generic parameter editor, and
native GUI sessions.

Plugins with a native GUI open it as a floating window (the plugin
manages its own window; Pulsegrid only creates/shows/hides it). Plugins
without one fall back to the generic slider panel built from their
published parameters.
"""

import tkinter as tk
from tkinter import ttk

from .widgets import Tooltip


class ClapPickerDialog(tk.Toplevel):
    """List scanned CLAP plugins; returns the chosen plugin dict."""

    def __init__(self, master, plugins, on_pick, title="Add CLAP plugin",
                 heading="CLAP audio effects"):
        """plugins: [{"path", "id", "name", "vendor"}]. on_pick(plugin)."""
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(True, True)
        self._on_pick = on_pick
        self._plugins = plugins

        ttk.Label(self, text=heading, font=("", 10, "bold"),
                  padding=(10, 8)).pack(anchor="w")
        if not plugins:
            ttk.Label(
                self,
                text="No CLAP plugins found.\n\nInstall a .clap plugin into "
                     "~/.clap (Linux) or set CLAP_PATH, then try again.",
                padding=(10, 4)).pack(anchor="w")
        else:
            frame = ttk.Frame(self, padding=(10, 0))
            frame.pack(fill="both", expand=True)
            self._list = tk.Listbox(frame, height=min(14, len(plugins)),
                                    activestyle="dotbox")
            self._list.pack(side="left", fill="both", expand=True)
            sb = ttk.Scrollbar(frame, orient="vertical",
                               command=self._list.yview)
            sb.pack(side="right", fill="y")
            self._list.configure(yscrollcommand=sb.set)
            for p in plugins:
                vendor = f" -- {p['vendor']}" if p.get("vendor") else ""
                self._list.insert("end", f"{p['name']}{vendor}")
            self._list.bind("<Double-Button-1>", lambda _e: self._pick())
            self._list.selection_set(0)

        btns = ttk.Frame(self, padding=(10, 8))
        btns.pack(fill="x")
        ttk.Button(btns, text="Cancel",
                   command=self.destroy).pack(side="right")
        if plugins:
            add = ttk.Button(btns, text="Add plugin", command=self._pick)
            add.pack(side="right", padx=(0, 8))
            Tooltip(add, "Validate and add the selected plugin to the track")
        self.bind("<Escape>", lambda _e: self.destroy())

    def _pick(self):
        sel = self._list.curselection() if hasattr(self, "_list") else ()
        if not sel:
            return
        plugin = self._plugins[sel[0]]
        self.destroy()
        self._on_pick(plugin)


class PluginParamDialog(tk.Toplevel):
    """Generic slider panel for a plugin's parameters.

    params: [{"id", "name", "min", "max", "default"}]; values maps
    str(id) -> current value. on_change(clap_id, value) on release.
    """

    def __init__(self, master, title, params, values, on_change,
                 on_open_gui=None, on_save_preset=None,
                 on_load_preset=None, on_load_native_preset=None,
                 latency_samples=None):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(True, True)
        self._on_change = on_change
        self._on_open_gui = on_open_gui
        self._on_save_preset = on_save_preset
        self._on_load_preset = on_load_preset
        self._on_load_native_preset = on_load_native_preset
        self._params = params
        self._sliders = {}  # clap_id -> (DoubleVar, Label, fmt)

        if on_open_gui is not None:
            top = ttk.Frame(self)
            top.pack(fill="x", padx=10, pady=(8, 0))
            ttk.Button(top, text="Open plugin GUI...",
                       command=self._open_gui).pack(side="left")
            ttk.Label(top, text="The plugin's own interface (floating window).",
                      foreground="#8b949e", font=("", 8)).pack(
                          side="left", padx=8)

        if (on_save_preset is not None or on_load_preset is not None
                or on_load_native_preset is not None):
            prow = ttk.Frame(self)
            prow.pack(fill="x", padx=10, pady=(8, 0))
            ttk.Label(prow, text="Preset:",
                      font=("", 9, "bold")).pack(side="left")
            if on_save_preset is not None:
                ttk.Button(prow, text="Save...",
                           command=self._save_preset).pack(
                               side="left", padx=(8, 0))
            if on_load_preset is not None:
                ttk.Button(prow, text="Load...",
                           command=self._load_preset).pack(
                               side="left", padx=(4, 0))
            if on_load_native_preset is not None:
                ttk.Button(prow, text="Load native...",
                           command=self._load_native_preset).pack(
                               side="left", padx=(4, 0))
            ttk.Label(prow, text="State-context presets + the plugin's own "
                                 "native preset files.",
                      foreground="#8b949e", font=("", 8)).pack(
                          side="left", padx=8)

        if latency_samples is not None:
            lrow = ttk.Frame(self)
            lrow.pack(fill="x", padx=10, pady=(8, 0))
            ttk.Label(lrow,
                      text=f"Latency: {latency_samples} samples "
                           f"- compensated by PDC.",
                      foreground="#8b949e", font=("", 8)).pack(side="left")

        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0)
        sb = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        shown = 0
        for p in params:
            pid = int(p["id"])
            lo, hi = float(p["min"]), float(p["max"])
            if not (hi > lo):
                continue
            cur = float(values.get(str(pid), p.get("default", lo)))
            cur = min(hi, max(lo, cur))
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=str(p["name"]), width=22,
                      anchor="w").pack(side="left")
            val_label = ttk.Label(row, width=12, anchor="e")
            val_label.pack(side="right")

            def fmt(v):
                return f"{v:.3f}".rstrip("0").rstrip(".")

            val_label.configure(text=fmt(cur))
            var = tk.DoubleVar(value=cur)
            sc = tk.Scale(row, from_=lo, to=hi, orient="horizontal",
                          variable=var, showvalue=False, resolution=(hi - lo) / 1000.0,
                          bg="#161b22", fg="#e6edf3", highlightthickness=0)
            sc.pack(side="left", fill="x", expand=True, padx=6)
            sc.bind("<ButtonRelease-1>",
                    lambda _e, i=pid, v=var, lab=val_label:
                    self._commit(i, v.get(), lab))
            sc.bind("<Motion>",
                    lambda _e, v=var, lab=val_label:
                    lab.configure(text=fmt(v.get())), add="+")
            self._sliders[pid] = (var, val_label, fmt)
            shown += 1

        if shown == 0:
            ttk.Label(inner, text="This plugin exposes no adjustable "
                                 "parameters.").pack(padx=8, pady=8)
        ttk.Button(self, text="Close",
                   command=self.destroy).pack(pady=(0, 10))
        self.bind("<Escape>", lambda _e: self.destroy())

    def _open_gui(self):
        if self._on_open_gui is not None:
            self._on_open_gui(self)

    def _save_preset(self):
        if self._on_save_preset is not None:
            self._on_save_preset()

    def _load_preset(self):
        if self._on_load_preset is not None:
            self._on_load_preset()

    def _load_native_preset(self):
        if self._on_load_native_preset is not None:
            self._on_load_native_preset()

    def refresh_values(self, values):
        """Update sliders from the project (e.g. after native GUI tweaks)."""
        for pid, (var, lab, fmt) in self._sliders.items():
            v = values.get(str(pid))
            if v is None:
                continue
            v = float(v)
            if abs(var.get() - v) > 1e-9:
                var.set(v)
                lab.configure(text=fmt(v))

    def _commit(self, clap_id, value, label):
        label.configure(
            text=f"{value:.3f}".rstrip("0").rstrip("."))
        self._on_change(clap_id, round(float(value), 6))


class PluginGuiSession:
    """A plugin's native floating GUI, synced with the project.

    The engine opens the plugin's own window (floating; the plugin
    manages it). This session polls the GUI instance for parameter
    changes and applies them to the project *without* undo spam; on
    close, the whole session collapses into a single undo step.

    Only one session per (track, fx) is kept; opening again focuses the
    existing one.
    """

    POLL_MS = 150
    _active: dict = {}

    def __init__(self, app, track_idx, fx_idx):
        self.app = app
        self.track_idx = track_idx
        self.fx_idx = fx_idx
        self.gui_id = None
        self._poll_id = None
        self._before = None
        self._changed = False
        self._dialog = None  # generic PluginParamDialog to refresh
        self._plugin_id = None

    @classmethod
    def open(cls, app, track_idx, fx_idx, dialog=None):
        key = (track_idx, fx_idx)
        existing = cls._active.get(key)
        if existing is not None:
            if dialog is not None:
                existing._dialog = dialog
            return existing
        session = cls(app, track_idx, fx_idx)
        if not session._start(dialog):
            return None
        cls._active[key] = session
        return session

    def _effect(self):
        try:
            fx = self.app.project.tracks[self.track_idx].effects[self.fx_idx]
        except IndexError:
            return None
        # If the effect was replaced by a different plugin, this session
        # is stale -- close it. (During _start, _plugin_id is None.)
        if fx.kind != "plugin":
            return None
        if self._plugin_id is not None and fx.plugin_id != self._plugin_id:
            return None
        return fx

    def _start(self, dialog):
        fx = self._effect()
        if fx is None:
            return False
        self._plugin_id = fx.plugin_id
        try:
            self.gui_id = self.app.engine.open_plugin_gui(
                fx.plugin_path, fx.plugin_id,
                {k: float(v) for k, v in fx.params.items()})
        except Exception as e:
            from .widgets import show_error
            show_error(self.app.root, "Plugin GUI",
                       f"Could not open the plugin's interface:\n{e}\n\n"
                       "Use Edit... for the generic controls instead.")
            return False
        self._before = self.app.project.snapshot()
        self._dialog = dialog
        self._schedule()
        return True

    def _schedule(self):
        if self.gui_id is None:
            return
        self._poll_id = self.app.root.after(self.POLL_MS, self._poll)

    def _poll(self):
        self._poll_id = None
        if self.gui_id is None:
            return
        try:
            values = self.app.engine.plugin_gui_params(self.gui_id)
        except Exception:
            values = {}
        fx = self._effect()
        if fx is not None and values:
            for cid, value in values.items():
                old = fx.params.get(cid)
                if old is None or abs(float(old) - float(value)) > 1e-9:
                    fx.params[cid] = round(float(value), 6)
                    self._changed = True
            if self._changed:
                self.app._push_to_engine(report_errors=False)
                if self._dialog is not None:
                    try:
                        self._dialog.refresh_values(fx.params)
                    except Exception:
                        pass
        self._schedule()

    def close(self):
        """Stop polling, destroy the GUI, and record one undo step."""
        key = (self.track_idx, self.fx_idx)
        if self._poll_id is not None:
            try:
                self.app.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        if self.gui_id is not None:
            try:
                self.app.engine.close_plugin_gui(self.gui_id)
            except Exception:
                pass
            self.gui_id = None
        PluginGuiSession._active.pop(key, None)
        if self._changed and self._before is not None:
            after = self.app.project.snapshot()
            before = self._before
            app = self.app

            def do():
                app.project = after.snapshot()
                app._refresh_all()

            def undo():
                app.project = before.snapshot()
                app._refresh_all()

            app.undo.execute("plugin GUI tweaks", do, undo)
            app._mark_dirty()
        self._before = None
        self._changed = False


class GeneratorGuiSession:
    """A track generator's native floating GUI, synced with the project.

    Mirrors PluginGuiSession but targets the track's generator instead
    of an insert effect. One session per track; closing collapses to a
    single undo step.
    """

    POLL_MS = 150
    _active: dict = {}

    def __init__(self, app, track_idx):
        self.app = app
        self.track_idx = track_idx
        self.gui_id = None
        self._poll_id = None
        self._before = None
        self._changed = False
        self._plugin_id = None

    @classmethod
    def open(cls, app, track_idx):
        existing = cls._active.get(track_idx)
        if existing is not None:
            return existing
        session = cls(app, track_idx)
        if not session._start():
            return None
        cls._active[track_idx] = session
        return session

    def _generator(self):
        try:
            layers = self.app.project.tracks[self.track_idx].generator_layers
        except IndexError:
            return None
        if not layers:
            return None
        gen = layers[0].generator
        if self._plugin_id is not None and gen.plugin_id != self._plugin_id:
            return None
        return gen

    def _start(self):
        gen = self._generator()
        if gen is None:
            return False
        self._plugin_id = gen.plugin_id
        try:
            self.gui_id = self.app.engine.open_plugin_gui(
                gen.plugin_path, gen.plugin_id,
                {k: float(v) for k, v in gen.params.items()})
        except Exception as e:
            from .widgets import show_error
            show_error(self.app.root, "Plugin GUI",
                       f"Could not open the plugin's interface:\n{e}\n\n"
                       "Use Edit... for the generic controls instead.")
            return False
        self._before = self.app.project.snapshot()
        self._schedule()
        return True

    def _schedule(self):
        if self.gui_id is None:
            return
        self._poll_id = self.app.root.after(self.POLL_MS, self._poll)

    def _poll(self):
        self._poll_id = None
        if self.gui_id is None:
            return
        try:
            values = self.app.engine.plugin_gui_params(self.gui_id)
        except Exception:
            values = {}
        gen = self._generator()
        if gen is not None and values:
            for cid, value in values.items():
                old = gen.params.get(cid)
                if old is None or abs(float(old) - float(value)) > 1e-9:
                    gen.params[cid] = round(float(value), 6)
                    self._changed = True
            if self._changed:
                self.app._push_to_engine(report_errors=False)
        self._schedule()

    def close(self):
        if self._poll_id is not None:
            try:
                self.app.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        if self.gui_id is not None:
            try:
                self.app.engine.close_plugin_gui(self.gui_id)
            except Exception:
                pass
            self.gui_id = None
        GeneratorGuiSession._active.pop(self.track_idx, None)
        if self._changed and self._before is not None:
            after = self.app.project.snapshot()
            before = self._before
            app = self.app

            def do():
                app.project = after.snapshot()
                app._refresh_all()

            def undo():
                app.project = before.snapshot()
                app._refresh_all()

            app.undo.execute("generator GUI tweaks", do, undo)
            app._mark_dirty()
        self._before = None
        self._changed = False
