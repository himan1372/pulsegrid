"""Automation tab: envelope editor for track parameter lanes.

One lane = one (track, param) pair: gain, pan, or an effect parameter
("fx0.cutoff"). Click the line area to add a control point, drag a
point to move it (beat snaps to 1/4 beat), right-click to delete.
Linear interpolation between points; before the first point the
track's static value holds (dashed line), after the last point the
last value holds. One gesture = one undoable commit.
"""

import tkinter as tk
from tkinter import ttk

import copy

from ..project import (FX_DEFS, FX_NAMES, TRACK_AUTO_PARAMS, AutomationLane,
                       AutoPoint, CURVE_INTERPS, LFO_COMBINES, LFO_SHAPES,
                       LfoSettings, auto_param_spec, parse_auto_param)
from ..curve_eval import (auto_tangent, norm_point, sample_curve,
                          uses_tension)
from .widgets import Tooltip

GUTTER = 52
TOP_PAD = 12
BOT_PAD = 22
BEAT_SNAP = 0.25
POINT_R = 5


def fmt_value(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.0f}%"
    if unit == "ms":
        return f"{v:.0f} ms"
    if unit == "Hz":
        return f"{v:.0f} Hz"
    return f"{v:+.2f}"


class AutomationEditor(ttk.Frame):
    def __init__(self, master, on_commit, on_clear, ensure_plugin_params=None,
                 dirty_tracker=None):
        """on_commit(track_id, param, points, interp, tension, lfo): one
        gesture's edits (points incl. per-point tangents, curve shape,
        tension, or the LFO layer).
        on_clear(track_id, param): remove the lane.
        ensure_plugin_params(plugin_id, plugin_path) -> param list, or
        None when the plugin can't be read (its params are then skipped
        in the picker)."""
        self._dirty_tracker = dirty_tracker
        super().__init__(master)
        self._on_commit = on_commit
        self._on_clear = on_clear
        self._ensure_plugin_params = ensure_plugin_params
        self._project = None
        self._track_id = None
        self._param = "gain"
        self._points: list[AutoPoint] = []
        self._interp = "linear"
        self._tension = 0.5
        # Working copy of the lane's LFO layer (committed with points).
        self._lfo: LfoSettings | None = None
        self._drag_idx = None
        # Tangent-handle drag: (point index, "in" | "out").
        self._tan_drag = None

        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=8, pady=(6, 2))
        ttk.Label(top, text="Track:").pack(side="left")
        self._track_var = tk.StringVar()
        self._track_combo = ttk.Combobox(top, textvariable=self._track_var,
                                         state="readonly", width=16)
        self._track_combo.pack(side="left", padx=6)
        self._track_combo.bind("<<ComboboxSelected>>", self._on_track_picked)
        Tooltip(self._track_combo, "Track whose parameter to automate")
        ttk.Label(top, text="Parameter:").pack(side="left")
        self._param_var = tk.StringVar()
        self._param_combo = ttk.Combobox(top, textvariable=self._param_var,
                                        state="readonly", width=22)
        self._param_combo.pack(side="left", padx=6)
        self._param_combo.bind("<<ComboboxSelected>>", self._on_param_picked)
        Tooltip(self._param_combo, "Gain, pan, or an effect parameter")
        # Curve shape (research topic 30): per-lane interpolation mode
        # + tension, like LMMS's per-clip progression. The evaluator
        # lives in curve_eval (shared with the painter); the engine
        # mirrors it in Rust.
        ttk.Label(top, text="Curve:").pack(side="left", padx=(6, 0))
        self._interp_var = tk.StringVar(value="Linear")
        self._interp_menu = ttk.OptionMenu(
            top, self._interp_var, "Linear", *CURVE_INTERPS.values(),
            command=self._on_interp_picked)
        self._interp_menu.pack(side="left", padx=2)
        Tooltip(self._interp_menu,
                "Curve shape between control points.\n"
                "Linear: straight glides. Smooth: eased Hermite curve.\n"
                "Hold: steps (value jumps at each point). Stairs: "
                "quantized steps.\nPulse: square-wave alternation. Wave: "
                "sine wobble around the glide.")
        ttk.Label(top, text="Tension:").pack(side="left", padx=(6, 0))
        self._tension_var = tk.DoubleVar(value=50.0)
        self._tension_scale = tk.Scale(
            top, from_=0, to=100, orient="horizontal",
            variable=self._tension_var, length=80, showvalue=False,
            bg="#161b22", highlightthickness=0, borderwidth=0)
        self._tension_scale.pack(side="left", padx=2)
        self._tension_label = ttk.Label(top, text="50", width=3,
                                        font=("", 8))
        self._tension_label.pack(side="left")
        self._tension_scale.bind("<ButtonRelease-1>",
                                 lambda _e: self._on_tension_release())
        self._tension_scale.bind(
            "<Motion>",
            lambda _e: self._tension_label.configure(
                text=f"{self._tension_var.get():.0f}"), add="+")
        Tooltip(self._tension_scale,
                "Tension (0-100): shapes Smooth/Stairs/Pulse/Wave.\n"
                "Smooth: tangent strength. Stairs/Pulse/Wave: shape "
                "frequency.\nLinear and Hold ignore tension.")
        self._clear_btn = ttk.Button(top, text="Clear lane",
                                    command=self._on_clear_pressed)
        self._clear_btn.pack(side="left", padx=6)
        Tooltip(self._clear_btn, "Remove this automation lane (undoable)")
        self._val_label = ttk.Label(top, text="", font=("", 9, "bold"),
                                    foreground="#79c0ff")
        self._val_label.pack(side="left", padx=12)
        Tooltip(top, "Click to add a control point, drag a point to move it\n"
                     "(beat snaps to 1/4), right-click a point to delete it.\n"
                     "Between points the Curve menu picks the shape "
                     "(linear, smooth,\n"
                     "hold, stairs, pulse, wave); before the first point "
                     "the track's\nown setting holds (dashed line).\n"
                     "Smooth mode: drag the small tangent handles to lock a "
                     "point's\n"
                     "slope; Alt+right-click a point resets its tangents to "
                     "auto.")

        # LFO modulation layer (research topic 36; FL Automation Clip LFO
        # analog). A separate layer over the base spline: enabling it never
        # modifies the points. Combine Add suits bipolar params (pan),
        # Multiply suits unipolar ones (gain).
        lfo = ttk.Frame(self)
        lfo.pack(side="top", fill="x", padx=8, pady=(0, 2))
        self._lfo_enabled_var = tk.BooleanVar(value=False)
        self._lfo_enable = ttk.Checkbutton(
            lfo, text="LFO", variable=self._lfo_enabled_var,
            command=self._on_lfo_toggled)
        self._lfo_enable.pack(side="left")
        Tooltip(self._lfo_enable,
                "LFO modulation layer over the envelope.\n"
                "The base spline is kept; the LFO only modulates the "
                "evaluated value.")
        ttk.Label(lfo, text="Speed:").pack(side="left", padx=(8, 0))
        self._lfo_speed_var = tk.DoubleVar(value=1.0)
        self._lfo_speed = tk.Scale(
            lfo, from_=0.125, to=8.0, resolution=0.125, orient="horizontal",
            variable=self._lfo_speed_var, length=70, showvalue=False,
            bg="#161b22", highlightthickness=0, borderwidth=0)
        self._lfo_speed.pack(side="left", padx=2)
        self._lfo_speed_label = ttk.Label(lfo, text="1.00/bt", width=7,
                                          font=("", 8))
        self._lfo_speed_label.pack(side="left")
        self._lfo_speed.bind("<ButtonRelease-1>",
                            lambda _e: self._on_lfo_release())
        Tooltip(self._lfo_speed, "LFO cycles per beat")
        ttk.Label(lfo, text="Shape:").pack(side="left", padx=(8, 0))
        self._lfo_shape_var = tk.StringVar(value="sine")
        self._lfo_shape = ttk.OptionMenu(
            lfo, self._lfo_shape_var, "sine", *LFO_SHAPES,
            command=lambda _v: self._on_lfo_release())
        self._lfo_shape.pack(side="left", padx=2)
        Tooltip(self._lfo_shape, "LFO waveform")
        ttk.Label(lfo, text="Skew:").pack(side="left", padx=(8, 0))
        self._lfo_skew_var = tk.DoubleVar(value=0.0)
        self._lfo_skew = tk.Scale(
            lfo, from_=-100, to=100, orient="horizontal",
            variable=self._lfo_skew_var, length=60, showvalue=False,
            bg="#161b22", highlightthickness=0, borderwidth=0)
        self._lfo_skew.pack(side="left", padx=2)
        self._lfo_skew.bind("<ButtonRelease-1>",
                            lambda _e: self._on_lfo_release())
        Tooltip(self._lfo_skew,
                "Skew (-100..100): morphs the triangle toward saw / "
                "reverse saw")
        ttk.Label(lfo, text="PW:").pack(side="left", padx=(8, 0))
        self._lfo_pw_var = tk.DoubleVar(value=50.0)
        self._lfo_pw = tk.Scale(
            lfo, from_=1, to=99, orient="horizontal",
            variable=self._lfo_pw_var, length=60, showvalue=False,
            bg="#161b22", highlightthickness=0, borderwidth=0)
        self._lfo_pw.pack(side="left", padx=2)
        self._lfo_pw.bind("<ButtonRelease-1>",
                          lambda _e: self._on_lfo_release())
        Tooltip(self._lfo_pw, "Pulse width: duty cycle of the pulse shape")
        ttk.Label(lfo, text="Level:").pack(side="left", padx=(8, 0))
        self._lfo_level_var = tk.DoubleVar(value=100.0)
        self._lfo_level = tk.Scale(
            lfo, from_=-100, to=100, orient="horizontal",
            variable=self._lfo_level_var, length=60, showvalue=False,
            bg="#161b22", highlightthickness=0, borderwidth=0)
        self._lfo_level.pack(side="left", padx=2)
        self._lfo_level.bind("<ButtonRelease-1>",
                             lambda _e: self._on_lfo_release())
        Tooltip(self._lfo_level,
                "LFO amplitude (percent of the param range unit); "
                "negative inverts")
        ttk.Label(lfo, text="Combine:").pack(side="left", padx=(8, 0))
        self._lfo_combine_var = tk.StringVar(value="add")
        self._lfo_combine = ttk.OptionMenu(
            lfo, self._lfo_combine_var, "add", *LFO_COMBINES,
            command=lambda _v: self._on_lfo_release())
        self._lfo_combine.pack(side="left", padx=2)
        Tooltip(self._lfo_combine,
                "Add: base + level*wave (bipolar params like pan).\n"
                "Multiply: base * (1 + level*wave) (unipolar like gain).")

        self._canvas = tk.Canvas(self, bg="#0d1117", highlightthickness=0, borderwidth=0,
                                 height=230)
        self._canvas.pack(side="top", fill="both", expand=True,
                          padx=8, pady=(2, 8))
        self._canvas.bind("<Button-1>", self._press)
        self._canvas.bind("<B1-Motion>", self._motion)
        self._canvas.bind("<ButtonRelease-1>", self._release)
        self._canvas.bind("<Button-3>", self._right_click)
        self._canvas.bind("<Configure>", lambda _e: self._draw())

    # -- data -----------------------------------------------------------

    def set_project(self, project, track_id=None, param=None) -> None:
        self._project = project
        names = [t.name for t in project.tracks]
        self._track_combo["values"] = names
        if track_id is None or not any(t.id == track_id for t in project.tracks):
            track_id = project.tracks[0].id if project.tracks else None
        self._track_id = track_id
        if track_id is not None:
            idx = next(i for i, t in enumerate(project.tracks)
                       if t.id == track_id)
            self._track_combo.current(idx)
        self._rebuild_params(param)
        self.refresh()

    def _rebuild_params(self, param=None) -> None:
        track = self._track()
        self._param_ids = []
        self._param_labels = []
        for pid, (label, _lo, _hi, _u) in TRACK_AUTO_PARAMS.items():
            self._param_ids.append(pid)
            self._param_labels.append(label)
        if track is not None:
            for i, fx in enumerate(track.effects):
                if fx.kind == "plugin":
                    params = None
                    if self._ensure_plugin_params is not None:
                        try:
                            params = self._ensure_plugin_params(
                                fx.plugin_id, fx.plugin_path)
                        except Exception:
                            params = None
                    if params:
                        for p in params:
                            pid = int(p["id"])
                            self._param_ids.append(f"fx{i}.p{pid}")
                            self._param_labels.append(
                                f"{fx.display_name()}: {p['name']}")
                    continue
                for p, label, _lo, _hi, _u, _d in FX_DEFS[fx.kind]:
                    self._param_ids.append(f"fx{i}.{p}")
                    self._param_labels.append(f"{FX_NAMES[fx.kind]}: {label}")
            # Generator (instrument) parameters, if the track has layers.
            # Uses the first layer's params for the automation lane list.
            gen = (track.generator_layers[0].generator
                   if track.generator_layers else None)
            if gen is not None and self._ensure_plugin_params is not None:
                try:
                    gparams = self._ensure_plugin_params(
                        gen.plugin_id, gen.plugin_path)
                except Exception:
                    gparams = None
                if gparams:
                    for p in gparams:
                        pid = int(p["id"])
                        self._param_ids.append(f"gen.p{pid}")
                        self._param_labels.append(
                            f"{gen.display_name()}: {p['name']}")
            # Send amount lanes (FL-style send automation): one per
            # outgoing send, modulating the edge gain 0-1.
            for send in track.sends:
                dest = (self._project.track_by_id(send.to_track_id)
                        if self._project else None)
                dest_name = dest.name if dest else send.to_track_id
                self._param_ids.append(f"send.{send.to_track_id}.amount")
                self._param_labels.append(f"Send -> {dest_name}: Amount")
        self._param_combo["values"] = self._param_labels
        if param in self._param_ids:
            self._param = param
        elif self._param not in self._param_ids:
            self._param = "gain"
        self._param_combo.current(self._param_ids.index(self._param))
        self._load_points()

    def _on_track_picked(self, _event=None) -> None:
        idx = self._track_combo.current()
        if self._project is None or not (0 <= idx < len(self._project.tracks)):
            return
        self._track_id = self._project.tracks[idx].id
        self._rebuild_params()
        self.refresh()

    def _on_param_picked(self, _event=None) -> None:
        idx = self._param_combo.current()
        if 0 <= idx < len(self._param_ids):
            self._param = self._param_ids[idx]
            self._load_points()
            self.refresh()

    def _track(self):
        if self._project is None or self._track_id is None:
            return None
        for t in self._project.tracks:
            if t.id == self._track_id:
                return t
        return None

    def _spec(self):
        """(label, lo, hi, unit) for the current param."""
        track = self._track()
        if track is None:
            return ("", 0.0, 1.0, "")
        return auto_param_spec(self._param, track)

    def _base_value(self) -> float:
        track = self._track()
        if track is None:
            return 0.0
        kind, idx, name = parse_auto_param(self._param)
        if kind == "track":
            return float(track.gain if name == "gain" else track.pan)
        if kind == "gen":
            gen = (track.generator_layers[0].generator
                   if track.generator_layers else None)
            if gen is None:
                return 0.0
            # Generator params are keyed by bare CLAP id.
            return float(gen.params.get(name[1:], 0.0))
        fx = track.effects[idx]
        if fx.kind == "plugin" and name.startswith("p"):
            # Plugin params are keyed by bare CLAP id.
            return float(fx.params.get(name[1:], 0.0))
        return float(fx.params[name])

    def _load_points(self) -> None:
        self._points = []
        self._interp = "linear"
        self._tension = 0.5
        self._lfo = None
        if self._project is not None and self._track_id is not None:
            for lane in self._project.lanes_for(self._track_id, self._param):
                self._points = [AutoPoint(p.beat, p.value, p.in_tan, p.out_tan)
                                for p in lane.points]
                self._interp = lane.interp
                self._tension = lane.tension
                self._lfo = (copy.deepcopy(lane.lfo)
                             if lane.lfo is not None else None)
        self._drag_idx = None
        self._tan_drag = None
        self._sync_curve_widgets()
        self._sync_lfo_widgets()

    def _sync_curve_widgets(self) -> None:
        """Reflect the lane's interp/tension in the top-bar widgets."""
        self._interp_var.set(CURVE_INTERPS.get(self._interp, "Linear"))
        self._tension_var.set(self._tension * 100)
        self._tension_label.configure(text=f"{self._tension * 100:.0f}")
        self._update_tension_state()

    def _lfo_dict(self):
        """The working LFO as a plain dict for the evaluator, or None."""
        if self._lfo is None or not self._lfo.enabled:
            return None
        return self._lfo.to_dict()

    def _sync_lfo_widgets(self) -> None:
        """Reflect the working LFO layer in the panel widgets."""
        lfo = self._lfo
        self._lfo_enabled_var.set(bool(lfo and lfo.enabled))
        self._lfo_speed_var.set(lfo.speed if lfo else 1.0)
        self._lfo_shape_var.set(lfo.shape if lfo else "sine")
        self._lfo_skew_var.set((lfo.skew if lfo else 0.0) * 100)
        self._lfo_pw_var.set((lfo.pulse_width if lfo else 0.5) * 100)
        self._lfo_level_var.set((lfo.level if lfo else 1.0) * 100)
        self._lfo_combine_var.set(lfo.combine if lfo else "add")
        self._lfo_speed_label.configure(
            text=f"{self._lfo_speed_var.get():.2f}/bt")
        self._update_lfo_state()

    def _update_lfo_state(self) -> None:
        """The LFO panel needs at least one point to modulate."""
        state = "normal" if self._points else "disabled"
        for w in (self._lfo_enable, self._lfo_speed, self._lfo_shape,
                  self._lfo_skew, self._lfo_pw, self._lfo_level,
                  self._lfo_combine):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def _read_lfo_widgets(self) -> LfoSettings:
        return LfoSettings(
            enabled=bool(self._lfo_enabled_var.get()),
            speed=float(self._lfo_speed_var.get()),
            shape=self._lfo_shape_var.get(),
            skew=float(self._lfo_skew_var.get()) / 100.0,
            pulse_width=float(self._lfo_pw_var.get()) / 100.0,
            level=float(self._lfo_level_var.get()) / 100.0,
            combine=self._lfo_combine_var.get(),
        )

    def _on_lfo_toggled(self) -> None:
        self._lfo = self._read_lfo_widgets()
        self._lfo_speed_label.configure(
            text=f"{self._lfo.speed:.2f}/bt")
        self._draw()
        self._commit_points()
        self._mark_lfo_dirty()

    def _on_lfo_release(self) -> None:
        self._lfo = self._read_lfo_widgets()
        self._lfo_speed_label.configure(
            text=f"{self._lfo.speed:.2f}/bt")
        self._draw()
        self._commit_points()
        self._mark_lfo_dirty()

    def _mark_lfo_dirty(self) -> None:
        """An LFO edit changes the evaluated modulation layer, not the
        spline geometry (brief section 28): invalidate the lane's
        geometric region so the modulated preview refreshes."""
        if self._dirty_tracker is None:
            return
        from .dirty_regions import DirtyDomain, DirtyRect
        cv = self._canvas
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 50 or h < 50:
            return
        self._dirty_tracker.mark_dirty(
            DirtyDomain.AUTOMATION,
            [DirtyRect(GUTTER, TOP_PAD, w - 8, h - BOT_PAD)],
            note="automation LFO layer edit")

    def _update_tension_state(self) -> None:
        """Tension only shapes Smooth/Stairs/Pulse/Wave (engine mirrors)."""
        state = "normal" if uses_tension(self._interp) else "disabled"
        self._tension_scale.configure(state=state)

    def _current_points(self) -> list[AutoPoint]:
        pts = sorted(self._points, key=lambda p: p.beat)
        return [AutoPoint(p.beat, p.value, p.in_tan, p.out_tan) for p in pts]

    def _commit_points(self) -> None:
        if self._track_id is not None:
            self._on_commit(self._track_id, self._param,
                            self._current_points(),
                            self._interp, round(self._tension, 3),
                            copy.deepcopy(self._lfo))

    def _on_interp_picked(self, _label=None) -> None:
        # OptionMenu gives the label; map back to the mode id.
        label = self._interp_var.get()
        for mode_id, mode_label in CURVE_INTERPS.items():
            if mode_label == label:
                self._interp = mode_id
                break
        self._update_tension_state()
        self._draw()
        self._commit_points()

    def _on_tension_release(self) -> None:
        self._tension = round(self._tension_var.get() / 100.0, 3)
        self._tension_label.configure(text=f"{self._tension * 100:.0f}")
        self._draw()
        self._commit_points()

    @property
    def selection(self):
        return (self._track_id, self._param)

    def refresh(self) -> None:
        self._draw()

    def max_beat(self) -> float:
        if self._project is None:
            return 16.0
        return float(self._project.arrangement_bars() * 4)

    # -- drawing ----------------------------------------------------------

    def _x(self, beat: float, width: float) -> float:
        return GUTTER + beat / self.max_beat() * (width - GUTTER - 8)

    def _y(self, value: float, height: float) -> float:
        _label, lo, hi, _unit = self._spec()
        span = (hi - lo) or 1.0
        frac = (value - lo) / span
        return TOP_PAD + (1.0 - frac) * (height - TOP_PAD - BOT_PAD)

    # -- tangent handles (research topic 36) -------------------------------

    TAN_HB = 0.75  # beats of visual offset for a tangent handle

    def _effective_tangents(self, pts):
        """Per-point (in, out) tangents in value/beat, auto-resolved.

        Returns the tangent actually shaping each segment: the locked
        value when the user set one, else the automatic Catmull-Rom
        tangent (LMMS locked-tangent semantics). Only tangents that
        shape a real segment are resolved (no in-tangent for the first
        point, no out-tangent for the last).
        """
        npts = [norm_point(p) for p in pts]
        n = len(npts)
        eff = []
        for i, (b, v, in_t, out_t) in enumerate(npts):
            if i > 0 and in_t is not None:
                in_eff = in_t
            elif i > 0:
                in_eff = auto_tangent(npts, i, 1, self._tension)
            else:
                in_eff = 0.0
            if i < n - 1 and out_t is not None:
                out_eff = out_t
            elif i < n - 1:
                out_eff = auto_tangent(npts, i, 0, self._tension)
            else:
                out_eff = 0.0
            eff.append((in_eff, out_eff))
        return eff

    def _tangent_handle_at(self, event):
        """(point index into the beat-sorted list, 'in'|'out') of the
        tangent handle under the cursor, or None."""
        if self._interp != "smooth":
            return None
        cv = self._canvas
        width, height = cv.winfo_width(), cv.winfo_height()
        pts = sorted(self._points, key=lambda p: p.beat)
        n = len(pts)
        if n < 2 or width < 50:
            return None
        eff = self._effective_tangents(pts)
        for i, p in enumerate(pts):
            if i > 0:
                hx = self._x(p.beat - self.TAN_HB, width)
                hy = self._y(p.value - eff[i][0] * self.TAN_HB, height)
                if abs(event.x - hx) <= 7 and abs(event.y - hy) <= 7:
                    return (i, "in")
            if i < n - 1:
                hx = self._x(p.beat + self.TAN_HB, width)
                hy = self._y(p.value + eff[i][1] * self.TAN_HB, height)
                if abs(event.x - hx) <= 7 and abs(event.y - hy) <= 7:
                    return (i, "out")
        return None

    def _draw(self) -> None:
        cv = self._canvas
        cv.delete("all")
        width = cv.winfo_width()
        height = cv.winfo_height()
        if width < 50 or height < 50 or self._project is None:
            return
        label, lo, hi, unit = self._spec()
        max_beat = self.max_beat()

        # Beat grid + bar numbers.
        beat = 0.0
        while beat <= max_beat + 1e-9:
            x = self._x(beat, width)
            strong = abs(beat % 4.0) < 1e-9
            cv.create_line(x, TOP_PAD, x, height - BOT_PAD,
                           fill="#2a3340" if strong else "#1a222c")
            if strong:
                cv.create_text(x + 2, height - BOT_PAD + 4,
                               text=f"bar {int(beat // 4) + 1}", anchor="nw",
                               fill="#8b949e", font=("", 8))
            beat += 1.0

        # Value labels.
        for v, anchor in ((hi, f"{fmt_value(hi, unit)}"),
                          ((lo + hi) / 2, f"{fmt_value((lo + hi) / 2, unit)}"),
                          (lo, f"{fmt_value(lo, unit)}")):
            cv.create_text(GUTTER - 6, self._y(v, height), text=anchor,
                           anchor="e", fill="#8b949e", font=("", 8))

        # Base (static) value dashed line.
        base = self._base_value()
        yb = self._y(max(lo, min(hi, base)), height)
        cv.create_line(GUTTER, yb, width - 8, yb, fill="#6e7681", dash=(4, 4))
        cv.create_text(width - 10, yb - 8, text="static", anchor="e",
                       fill="#6e7681", font=("", 8, "italic"))

        pts = sorted(self._points, key=lambda p: p.beat)
        if not pts:
            cv.create_text(width / 2, height / 2,
                           text="Click to add control points -- drag to move, "
                                "right-click to delete",
                           fill="#8b949e", font=("", 11, "italic"))
            return

        # Filled area + envelope line (extended to the edges).
        # The painter samples the curve evaluator -- it draws values,
        # it never decides the curve shape (research topic 30, brief
        # section 52: "Here are values. Draw them.").
        # A single point draws as just the point (no line to draw).
        # Tagged "env" so drags can update just the envelope, not the grid.
        # The sampler receives the full points (tangents included);
        # locked tangents shape the smooth evaluator.
        samples = sample_curve(pts, self._interp, self._tension)
        coords = []
        for beat, value in samples:
            coords += [self._x(beat, width), self._y(value, height)]
        if len(pts) >= 2:
            base_y = height - BOT_PAD
            fill_coords = ([coords[0], base_y] + coords +
                           [coords[-2], base_y])
            cv.create_polygon(fill_coords, fill="#1f6feb", stipple="gray50",
                              outline="", tags="env")
            cv.create_line(coords, fill="#79c0ff", width=2, tags="env")
            # LFO preview overlay (research topic 36, brief section 28):
            # the modulated curve. The base spline above is untouched --
            # the LFO is a separate layer.
            lfo_d = self._lfo_dict()
            if lfo_d is not None:
                mod = sample_curve(pts, self._interp, self._tension,
                                   lfo=lfo_d)
                mcoords = []
                for beat, value in mod:
                    mcoords += [self._x(beat, width),
                                self._y(value, height)]
                cv.create_line(mcoords, fill="#ffa657", width=1,
                               dash=(4, 2), tags="lfoenv")

        # Before the first point the static value holds: draw the flat
        # segment from the left edge.
        if pts[0].beat > 0:
            cv.create_line(GUTTER, yb, coords[0], coords[1],
                           fill="#6e7681", width=1, dash=(2, 3))
        for i, p in enumerate(pts):
            x, y = self._x(p.beat, width), self._y(p.value, height)
            cv.create_oval(x - POINT_R, y - POINT_R, x + POINT_R, y + POINT_R,
                           fill="#79c0ff" if i != self._drag_idx else "#ffffff",
                           outline="#0d1117", width=1,
                           tags=(f"pt-{i}", "envpt"))

        # Tangent handles for the smooth evaluator (research topic 36).
        # A filled handle is a LOCKED (user) tangent; a hollow handle is
        # automatic. Drag a handle to lock that slope; Alt+right-click a
        # point to return its tangents to auto.
        if self._interp == "smooth" and len(pts) >= 2:
            eff = self._effective_tangents(pts)
            for i, p in enumerate(pts):
                _b, _v, in_t, out_t = norm_point(p)
                x, y = self._x(p.beat, width), self._y(p.value, height)
                if i > 0:
                    hx = self._x(p.beat - self.TAN_HB, width)
                    hy = self._y(p.value - eff[i][0] * self.TAN_HB, height)
                    cv.create_line(x, y, hx, hy, fill="#8b949e", tags="tan")
                    r = 4
                    cv.create_rectangle(
                        hx - r, hy - r, hx + r, hy + r,
                        fill="#ffa657" if in_t is not None else "",
                        outline="#ffa657", tags="tan")
                if i < len(pts) - 1:
                    hx = self._x(p.beat + self.TAN_HB, width)
                    hy = self._y(p.value + eff[i][1] * self.TAN_HB, height)
                    cv.create_line(x, y, hx, hy, fill="#8b949e", tags="tan")
                    r = 4
                    cv.create_rectangle(
                        hx - r, hy - r, hx + r, hy + r,
                        fill="#ffa657" if out_t is not None else "",
                        outline="#ffa657", tags="tan")

    def _refresh_envelope(self) -> None:
        """Update just the envelope line/fill/dragged point (no grid redraw)."""
        # Tangent handles (smooth mode) and the LFO preview overlay both
        # move with the points: take the full redraw when either is live.
        if self._interp == "smooth" or self._lfo_dict() is not None:
            self._draw()
            return
        cv = self._canvas
        width = cv.winfo_width()
        height = cv.winfo_height()
        if width < 50 or height < 50 or self._project is None:
            return
        pts = sorted(self._points, key=lambda p: p.beat)
        if len(pts) < 2:
            # Envelope shape changed structurally: full redraw is simplest.
            self._draw()
            return
        samples = sample_curve(pts, self._interp, self._tension)
        coords = []
        for beat, value in samples:
            coords += [self._x(beat, width), self._y(value, height)]
        base_y = height - BOT_PAD
        fill_coords = [coords[0], base_y] + coords + [coords[-2], base_y]
        items = cv.find_withtag("env")
        if len(items) < 2:
            self._draw()
            return
        # Polygon is created before the line, so it has the lower id.
        poly, line = sorted(items)[:2]
        cv.coords(poly, *fill_coords)
        cv.coords(line, *coords)
        # Move the dragged point's oval.
        if self._drag_idx is not None:
            # _drag_idx refers to position in self._points; find it in pts.
            p = self._points[self._drag_idx]
            try:
                si = pts.index(p)
            except ValueError:
                self._draw()
                return
            x, y = self._x(p.beat, width), self._y(p.value, height)
            tag = f"pt-{si}"
            if cv.find_withtag(tag):
                cv.coords(tag, x - POINT_R, y - POINT_R,
                          x + POINT_R, y + POINT_R)

    # -- interaction ------------------------------------------------------

    def _pos_to_beat_value(self, event):
        width = self._canvas.winfo_width()
        height = self._canvas.winfo_height()
        label, lo, hi, unit = self._spec()
        x = min(max(event.x, GUTTER), width - 8)
        y = min(max(event.y, TOP_PAD), height - BOT_PAD)
        beat = (x - GUTTER) / (width - GUTTER - 8) * self.max_beat()
        span = (hi - lo) or 1.0
        frac = 1.0 - (y - TOP_PAD) / (height - TOP_PAD - BOT_PAD)
        return beat, lo + frac * span

    def _point_at(self, event):
        width = self._canvas.winfo_width()
        height = self._canvas.winfo_height()
        for i, p in enumerate(self._points):
            x, y = self._x(p.beat, width), self._y(p.value, height)
            if abs(event.x - x) <= 8 and abs(event.y - y) <= 8:
                return i
        return None

    def _press(self, event) -> None:
        if self._project is None or self._track_id is None:
            return
        tan = self._tangent_handle_at(event)
        if tan is not None:
            self._tan_drag = tan
            self._canvas.focus_set()
            return
        idx = self._point_at(event)
        if idx is not None:
            self._drag_idx = idx
        else:
            beat, value = self._pos_to_beat_value(event)
            _label, lo, hi, _unit = self._spec()
            beat = round(beat / BEAT_SNAP) * BEAT_SNAP
            beat = max(0.0, min(self.max_beat() - 1e-6, beat))
            value = max(lo, min(hi, value))
            self._points.append(AutoPoint(beat, value))
            self._points.sort(key=lambda p: p.beat)
            self._drag_idx = next(i for i, p in enumerate(self._points)
                                  if p.beat == beat and p.value == value)
        self._canvas.focus_set()
        self._update_lfo_state()
        self._draw()

    def _motion(self, event) -> None:
        if self._tan_drag is not None:
            self._tan_motion(event)
            return
        if self._drag_idx is None:
            return
        beat, value = self._pos_to_beat_value(event)
        _label, lo, hi, _unit = self._spec()
        beat = round(beat / BEAT_SNAP) * BEAT_SNAP
        beat = max(0.0, min(self.max_beat() - 1e-6, beat))
        p = self._points[self._drag_idx]
        # Dirty region (research topic 34, brief section 33): the union of
        # the dragged point's old and new bounds, padded -- the curve
        # segments adjacent to the point are the affected geometry.
        old_rect = self._point_rect(p)
        p.beat = beat
        p.value = max(lo, min(hi, value))
        new_rect = self._point_rect(p)
        if self._dirty_tracker is not None and old_rect is not None:
            from .dirty_regions import DirtyDomain
            self._dirty_tracker.mark_dirty(
                DirtyDomain.AUTOMATION,
                [old_rect.union(new_rect).padded(POINT_R + 4)],
                note="automation point drag")
        _l, _lo, _hi, unit = self._spec()
        self._val_label.config(
            text=f"{p.beat:.2f} beats | {fmt_value(p.value, unit)}")
        self._refresh_envelope()

    def _tan_motion(self, event) -> None:
        """Drag a tangent handle: sets (locks) that point's tangent."""
        idx, side = self._tan_drag
        pts = sorted(self._points, key=lambda p: p.beat)
        p = pts[idx]
        beat, value = self._pos_to_beat_value(event)
        _label, lo, hi, _unit = self._spec()
        span = (hi - lo) or 1.0
        old_rect = self._point_rect(p)
        if side == "out":
            db = beat - p.beat
            if db > 1e-6:
                # Clamp to +/-4 param spans per beat: full range in a
                # quarter beat is already an extreme slope.
                p.out_tan = max(-4.0 * span,
                                min(4.0 * span, (value - p.value) / db))
        else:
            db = p.beat - beat
            if db > 1e-6:
                p.in_tan = max(-4.0 * span,
                               min(4.0 * span, (p.value - value) / db))
        if self._dirty_tracker is not None and old_rect is not None:
            from .dirty_regions import DirtyDomain
            new_rect = self._point_rect(p)
            self._dirty_tracker.mark_dirty(
                DirtyDomain.AUTOMATION,
                [old_rect.union(new_rect).padded(POINT_R + 4)],
                note="automation tangent drag")
        self._draw()

    def _point_rect(self, p):
        """Pixel bounds of an automation point, or None if not drawable."""
        from .dirty_regions import DirtyRect
        cv = self._canvas
        width, height = cv.winfo_width(), cv.winfo_height()
        if width < 50 or height < 50:
            return None
        x, y = self._x(p.beat, width), self._y(p.value, height)
        return DirtyRect(x - POINT_R, y - POINT_R, x + POINT_R, y + POINT_R)

    def _release(self, _event=None) -> None:
        if self._track_id is not None:
            if self._drag_idx is not None or self._tan_drag is not None:
                self._commit_points()
        self._drag_idx = None
        self._tan_drag = None
        self._val_label.config(text="")

    def _right_click(self, event) -> None:
        idx = self._point_at(event)
        if idx is None or self._track_id is None:
            return
        pts = sorted(self._points, key=lambda p: p.beat)
        p = pts[idx]
        if event.state & 0x0008:  # Alt held: reset tangents to auto
            if p.in_tan is not None or p.out_tan is not None:
                p.in_tan = None
                p.out_tan = None
                self._draw()
                self._commit_points()
            return
        del self._points[idx]
        self._drag_idx = None
        self._update_lfo_state()
        self._draw()
        self._commit_points()

    def _on_clear_pressed(self) -> None:
        if self._track_id is not None:
            self._on_clear(self._track_id, self._param)
