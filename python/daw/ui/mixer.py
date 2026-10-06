"""Mixer panel: per-track volume, pan, mute, and insert effects.

All controls commit on release (sliders) or on toggle (mute) so a drag
produces one undoable edit and one engine sync, not hundreds.
"""

import tkinter as tk
from tkinter import ttk

from ..project import FX_DEFS, FX_NAMES
from .mixer_render import MeterRenderer, MeterState
from .playlist_painter import PATTERN_COLORS
from .strip_presentation import build_strip_presentations, strip_geometry
from .widgets import Knob, Tooltip

STRIP_W = 176


def _pan_label(v: float) -> str:
    v = int(round(v))
    if v == 0:
        return "C"
    return f"L{-v}" if v < 0 else f"R{v}"


_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _midi_note_name(midi: float) -> str:
    """MIDI note number -> name (60 = C4). ASCII sharps only."""
    m = int(round(max(0.0, min(127.0, midi))))
    return f"{_NOTE_NAMES[m % 12]}{(m // 12) - 1}"


class Mixer(ttk.Frame):
    def __init__(self, master, on_volume, on_pan, on_mute, on_add_effect,
                 on_effect_param, on_remove_effect, on_add_plugin,
                 on_edit_plugin, on_open_gui, on_set_generator,
                 on_edit_generator, on_clear_generator,
                 on_open_generator_gui, on_add_send=None,
                 on_send_amount=None, on_send_options=None,
                 on_remove_send=None, on_vel_track=None,
                 on_key_track=None,
                 on_save_chain_preset=None, on_load_chain_preset=None,
                 on_save_track_preset=None, on_load_track_preset=None,
                 on_add_layer=None, on_remove_layer=None,
                 on_set_layer_mode=None, on_toggle_layer=None,
                 on_set_layer_gain=None, on_set_layer_pitch=None,
                 on_set_output=None, on_route_only=None):
        super().__init__(master)
        self._on_volume = on_volume
        self._on_pan = on_pan
        self._on_mute = on_mute
        self._on_add_effect = on_add_effect
        self._on_effect_param = on_effect_param
        self._on_remove_effect = on_remove_effect
        self._on_add_plugin = on_add_plugin
        self._on_edit_plugin = on_edit_plugin
        self._on_open_gui = on_open_gui
        self._on_set_generator = on_set_generator
        self._on_edit_generator = on_edit_generator
        self._on_clear_generator = on_clear_generator
        self._on_open_generator_gui = on_open_generator_gui
        self._on_add_layer = on_add_layer
        self._on_remove_layer = on_remove_layer
        self._on_set_layer_mode = on_set_layer_mode
        self._on_toggle_layer = on_toggle_layer
        self._on_set_layer_gain = on_set_layer_gain
        self._on_set_layer_pitch = on_set_layer_pitch
        self._on_set_output = on_set_output
        self._on_route_only = on_route_only
        self._on_add_send = on_add_send
        self._on_send_amount = on_send_amount
        self._on_send_options = on_send_options
        self._on_remove_send = on_remove_send
        self._on_vel_track = on_vel_track
        self._on_key_track = on_key_track
        self._on_save_chain_preset = on_save_chain_preset
        self._on_load_chain_preset = on_load_chain_preset
        self._on_save_track_preset = on_save_track_preset
        self._on_load_track_preset = on_load_track_preset
        self._project = None

        ttk.Label(self, text="Mixer", font=("", 10, "bold"),
                  padding=(8, 4)).pack(anchor="w")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        self._canvas = tk.Canvas(body, bg="#0d1117", highlightthickness=0,
                                 height=360)
        self._canvas.pack(side="left", fill="both", expand=True)
        vscroll = ttk.Scrollbar(body, orient="vertical",
                                command=self._canvas.yview)
        vscroll.pack(side="right", fill="y")
        hscroll = ttk.Scrollbar(body, orient="horizontal",
                                command=self._canvas.xview)
        hscroll.pack(side="bottom", fill="x")
        self._canvas.configure(xscrollcommand=hscroll.set,
                               yscrollcommand=vscroll.set)

        self._strips = ttk.Frame(self._canvas)
        self._win = self._canvas.create_window(0, 0, window=self._strips,
                                               anchor="nw")
        self._strips.bind("<Configure>",
                          lambda _e: self._canvas.configure(
                              scrollregion=self._canvas.bbox("all")))
        # track_id -> dict of live widgets/vars for in-place updates.
        self._strip_widgets = {}
        # Extracted meter renderer (research topic 29): the Mixer owns
        # interaction, layout, and scheduling; MeterRenderer owns all
        # meter drawing; MeterState owns the ballistics/presentation
        # state per strip. The renderer never queries the engine.
        self._meter_renderer = MeterRenderer()

    # -- data -----------------------------------------------------------

    def set_project(self, project) -> None:
        """Sync strips with the project, rebuilding only what changed.

        Existing strips are updated in place (no destroy/rebuild flash);
        strips are only created/destroyed when tracks are added/removed.
        """
        old_ids = set(self._strip_widgets)
        new_ids = [t.id for t in project.tracks]
        # Set project first so _build_sends can resolve destinations.
        self._project = project
        # Remove strips for deleted tracks.
        for tid in old_ids - set(new_ids):
            self._strip_widgets[tid]["frame"].destroy()
            del self._strip_widgets[tid]
        # Add strips for new tracks, update existing ones.
        for track in project.tracks:
            if track.id in self._strip_widgets:
                self._update_strip(track)
            else:
                self._build_strip(track)
        # Reorder strips to match track order.
        for track in project.tracks:
            self._strip_widgets[track.id]["frame"].pack(
                side="left", fill="y", padx=4, pady=4)
        self.refresh()

    def _build_sends(self, parent, track) -> None:
        """Build send controls for a track (one row per send + Add button).

        Each row: destination, amount slider, pre/post tap toggle, pan
        slider, sidechain toggle, remove button. All ASCII labels (the
        Ubuntu 20.04 RenderAddGlyphs lesson).
        """
        # Existing sends.
        for send in track.sends:
            dest = self._project.track_by_id(send.to_track_id) \
                if self._project else None
            dest_name = dest.name if dest else send.to_track_id
            row = ttk.Frame(parent)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=f"-> {dest_name}", width=14).pack(side="left")
            amt_var = tk.DoubleVar(value=send.amount * 100)
            scale = tk.Scale(row, from_=0, to=100, orient="horizontal",
                             variable=amt_var, length=70, showvalue=False,
                             bg="#161b22", highlightthickness=0)
            scale.pack(side="left", padx=2)
            amt_label = ttk.Label(row, text=f"{send.amount*100:.0f}%", width=5)
            amt_label.pack(side="left")
            scale.bind("<ButtonRelease-1>",
                       lambda _e, t=track, d=send.to_track_id, v=amt_var,
                       lab=amt_label: self._commit_send_amount(
                           t.id, d, v.get(), lab))
            scale.bind("<Motion>",
                       lambda _e, v=amt_var, lab=amt_label:
                       lab.configure(text=f"{v.get():.0f}%"), add="+")
            Tooltip(scale, f"Send amount to {dest_name}")
            # Tap toggle: pre-fader (pre-gain, like FL's pre-fader sends)
            # or post-FX (the default, like FL Studio).
            tap_var = tk.StringVar(value=send.tap)
            tap_btn = ttk.Button(row, text=tap_var.get().upper(), width=4)
            tap_btn.configure(
                command=lambda t=track, d=send.to_track_id, v=tap_var,
                b=tap_btn: self._commit_send_tap(t.id, d, v, b))
            tap_btn.pack(side="left", padx=1)
            Tooltip(tap_btn, "Send tap: PRE reads before the track fader, "
                             "POST reads the post-FX output")
            # Independent send pan.
            pan_var = tk.DoubleVar(value=send.pan * 100)
            pan_scale = tk.Scale(row, from_=-100, to=100, orient="horizontal",
                                 variable=pan_var, length=50, showvalue=False,
                                 bg="#161b22", highlightthickness=0)
            pan_scale.pack(side="left", padx=1)
            pan_scale.bind("<ButtonRelease-1>",
                           lambda _e, t=track, d=send.to_track_id,
                           v=pan_var: self._commit_send_pan(
                               t.id, d, v.get()))
            Tooltip(pan_scale, "Send pan (independent of track pan)")
            # Sidechain toggle: feed the destination's sidechain bus only.
            sc_var = tk.BooleanVar(value=send.sidechain)
            sc_btn = ttk.Checkbutton(row, text="SC", variable=sc_var,
                                     command=lambda t=track, d=send.to_track_id,
                                     v=sc_var: self._commit_send_sidechain(
                                         t.id, d, v.get()))
            sc_btn.pack(side="left", padx=1)
            Tooltip(sc_btn, "Sidechain send: feeds the destination's "
                            "sidechain bus, never audible")
            ttk.Button(row, text="X", width=2,
                       command=lambda t=track, d=send.to_track_id:
                       self._on_remove_send(t.id, d)
                       if self._on_remove_send else None).pack(side="left")
        # Add send button.
        if self._project and len(self._project.tracks) > 1:
            ttk.Button(parent, text="+ Send",
                       command=lambda t=track: self._pick_send_dest(t.id)
                       ).pack(pady=(4, 0))

    def _pick_send_dest(self, from_id: str) -> None:
        """Show a dialog to pick a send destination."""
        if not self._project or not self._on_add_send:
            return
        from_track = self._project.track_by_id(from_id)
        existing = {s.to_track_id for s in from_track.sends}
        # Candidates: other tracks, no cycles.
        candidates = []
        for t in self._project.tracks:
            if t.id == from_id or t.id in existing:
                continue
            if self._project.would_create_cycle(from_id, t.id):
                continue
            candidates.append(t)
        if not candidates:
            return
        # Simple dialog: list candidates.
        dlg = tk.Toplevel(self)
        dlg.title("Add send")
        dlg.transient(self)
        ttk.Label(dlg, text=f"Send from '{from_track.name}' to:",
                  padding=8).pack()
        for t in candidates:
            ttk.Button(dlg, text=t.name, width=20,
                       command=lambda d=t.id: (
                           self._on_add_send(from_id, d),
                           dlg.destroy())
                       ).pack(pady=2, padx=8)
        ttk.Button(dlg, text="Cancel",
                   command=dlg.destroy).pack(pady=8)

    def _commit_send_amount(self, track_id: str, dest_id: str,
                            pct: float, label) -> None:
        label.configure(text=f"{pct:.0f}%")
        if self._on_send_amount:
            self._on_send_amount(track_id, dest_id, pct / 100.0)

    def _commit_send_tap(self, track_id: str, dest_id: str,
                         var: tk.StringVar, btn) -> None:
        tap = "pre" if var.get() == "post" else "post"
        var.set(tap)
        btn.configure(text=tap.upper())
        if self._on_send_options:
            self._on_send_options(track_id, dest_id, {"tap": tap})

    def _commit_send_pan(self, track_id: str, dest_id: str,
                         pct: float) -> None:
        if self._on_send_options:
            self._on_send_options(track_id, dest_id,
                                  {"pan": max(-1.0, min(1.0, pct / 100.0))})

    def _commit_send_sidechain(self, track_id: str, dest_id: str,
                               enabled: bool) -> None:
        if self._on_send_options:
            self._on_send_options(track_id, dest_id,
                                  {"sidechain": bool(enabled)})

    # -- exclusive output routing (FL "route to this track only") ---------

    def _output_label(self, track) -> str:
        """Display label for a track's current output route."""
        if track.output == "master":
            return "Master"
        try:
            dest = self._project.track_by_id(track.output)
            return dest.name
        except Exception:
            return "Master"

    def _output_options(self, track) -> list:
        """Candidate output destinations for a track (excludes self)."""
        if not self._project:
            return []
        return [t for t in self._project.tracks if t.id != track.id]

    def _commit_output(self, track_id: str, label: str) -> None:
        """Apply the OptionMenu choice (label -> track id or 'master')."""
        if not self._on_set_output or not self._project:
            return
        dest_id = "master"
        for t in self._project.tracks:
            if t.name == label and t.id != track_id:
                dest_id = t.id
                break
        self._on_set_output(track_id, dest_id)

    def _route_only_dialog(self, dest_id: str) -> None:
        """FL 'Route selected to this track only': pick source tracks and
        rewrite their output route to this track (one undo step)."""
        if not self._project or not self._on_route_only:
            return
        dest = self._project.track_by_id(dest_id)
        # Candidates: every other track whose output rewrite would not
        # create a routing cycle.
        candidates = []
        for t in self._project.tracks:
            if t.id == dest_id:
                continue
            if t.output == dest_id:
                continue  # already routed here
            if self._project.would_create_output_cycle(t.id, dest_id):
                continue
            candidates.append(t)
        if not candidates:
            return
        dlg = tk.Toplevel(self)
        dlg.title(f"Route to '{dest.name}' only")
        dlg.transient(self)
        ttk.Label(
            dlg,
            text=f"Route these tracks to '{dest.name}' ONLY\n"
                 "(their direct Master path is removed):",
            padding=8).pack()
        vars_ = []
        for t in candidates:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(dlg, text=t.name, variable=var).pack(
                anchor="w", padx=12)
            vars_.append((t.id, var))
        btns = ttk.Frame(dlg)
        btns.pack(pady=8)
        ttk.Button(btns, text="Route",
                   command=lambda: (
                       self._on_route_only(
                           dest_id,
                           [tid for tid, v in vars_ if v.get()]),
                       dlg.destroy())).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel",
                   command=dlg.destroy).pack(side="left", padx=4)

    def refresh(self) -> None:
        # Strips are synced in set_project/_update_strip; meters update
        # via set_levels().
        pass

    def set_levels(self, peaks: list[float]) -> None:
        """Update strip level meters from engine peaks (0.0-1.0+).

        Called on the UI poll timer. The Mixer (controller) only wires
        engine data to canvases: MeterState builds the presentation
        (ballistics + peak hold), MeterRenderer draws it. Only the meter
        canvases are touched -- never the whole strip (dirty-region
        optimization).
        """
        if self._project is None:
            return
        for i, track in enumerate(self._project.tracks):
            w = self._strip_widgets.get(track.id)
            if w is None:
                continue
            raw = peaks[i] if i < len(peaks) else 0.0
            pres = w["meter_state"].update(raw)
            self._meter_renderer.render(w["meter"], pres)

    # -- strips ----------------------------------------------------------

    def _build_strip(self, track) -> None:
        # Fixed width via the width option (propagation keeps it as the
        # minimum); height follows the content (varies with effect count).
        strip = ttk.Frame(self._strips, width=STRIP_W, relief="groove",
                          borderwidth=1, padding=8)
        strip.pack(side="left", fill="y", padx=4, pady=4)
        # Drop target for Browser/Plugin Picker effects.
        strip._pulsegrid_fx_track_id = track.id

        # Strip-level presentation object (research topic 33): the
        # accent bar uses the track's PERSISTENT color identity -- not the
        # old positional color -- and the icon badge shows its icon.
        pres = build_strip_presentations([track])[0]
        geo = strip_geometry(pres)
        accent_bar = tk.Canvas(strip, height=geo["accent_bar_height"],
                               bg=geo["accent_color"], highlightthickness=0)
        accent_bar.pack(fill="x", pady=(0, 6))

        name_row = ttk.Frame(strip)
        name_row.pack(fill="x")
        name_label = ttk.Label(name_row, text=geo["name"],
                               font=("", 10, "bold"))
        name_label.pack(side="left", anchor="w")
        badge_label = ttk.Label(name_row, text=geo["badge_text"],
                                font=("", 8))
        if geo["badge_text"]:
            badge_label.pack(side="left", padx=(6, 0))

        muted = tk.BooleanVar(value=track.muted)
        mute_btn = ttk.Checkbutton(strip, text="Mute", variable=muted,
                                   command=lambda t=track, v=muted: self._on_mute(
                                       t.id, bool(v.get())))
        mute_btn.pack(anchor="w", pady=(4, 0))
        Tooltip(mute_btn, "Mute this track")

        # Exclusive output route (FL "route to this track only"): where this
        # track's post-FX output goes. "Master" = direct master path.
        out_row = ttk.Frame(strip)
        out_row.pack(fill="x", pady=(4, 0))
        ttk.Label(out_row, text="Out:").pack(side="left")
        out_var = tk.StringVar(value=self._output_label(track))
        out_menu = ttk.OptionMenu(
            out_row, out_var, self._output_label(track),
            *[self._output_label(t)
              for t in self._output_options(track)],
            command=lambda _v, t=track, v=out_var:
                self._commit_output(t.id, v.get()))
        out_menu.pack(side="left", padx=(4, 0))
        Tooltip(out_menu, "Exclusive output route: this track's audio goes "
                          "here INSTEAD of directly to Master")
        route_btn = ttk.Button(out_row, text="Route only...",
                               command=lambda t=track:
                               self._route_only_dialog(t.id))
        route_btn.pack(side="left", padx=(4, 0))
        Tooltip(route_btn,
                "Route other tracks to THIS track only\n"
                "(their Master path is removed -- subgroup mode)")

        # Volume (vertical) + pan (horizontal) side by side.
        vp = ttk.Frame(strip)
        vp.pack(fill="x", pady=6)
        vol_box = ttk.Frame(vp)
        vol_box.pack(side="left")
        self._vol_label = None
        vol_var = tk.DoubleVar(value=track.gain * 100)
        vol_label = ttk.Label(vol_box, text=f"{track.gain * 100:.0f}%", width=6)
        vol_label.pack()
        vol_row = ttk.Frame(vol_box)
        vol_row.pack()
        vol = tk.Scale(vol_row, from_=200, to=0, orient="vertical",
                       variable=vol_var, length=110, showvalue=False,
                       bg="#161b22", fg="#e6edf3", highlightthickness=0)
        vol.pack(side="left")
        # Live level meter (driven by engine peaks via set_levels).
        meter = tk.Canvas(vol_row, width=10, height=110, bg="#0d1117",
                          highlightthickness=0)
        meter.pack(side="left", padx=(4, 0))
        Tooltip(meter, "Output level")
        vol.bind("<ButtonRelease-1>",
                 lambda _e, t=track, v=vol_var, lab=vol_label:
                 self._commit_volume(t.id, v.get(), lab))
        vol.bind("<Motion>",  # live label while dragging
                 lambda _e, v=vol_var, lab=vol_label:
                 lab.configure(text=f"{v.get():.0f}%"), add="+")
        Tooltip(vol, "Track volume 0-200% (release to apply)")
        ttk.Label(vol_box, text="Vol").pack()

        pan_box = ttk.Frame(vp)
        pan_box.pack(side="left", fill="x", expand=True, padx=(8, 0))
        knob = Knob(pan_box, min_value=-1.0, max_value=1.0,
                    value=track.pan, default=0.0, size=52,
                    format_value=lambda v: _pan_label(v * 50),
                    on_commit=lambda v, t=track: self._on_pan(
                        t.id, round(v, 3)))
        knob.pack()
        Tooltip(knob, "Pan: drag to move in the stereo field\n"
                      "Shift+drag for fine adjust | double-click to center")
        ttk.Label(pan_box, text="Pan").pack()

        ttk.Separator(strip, orient="horizontal").pack(fill="x", pady=6)

        # Generator layers (FL Layer-style): the track's sound sources.
        # One note fans out to layers per the layer mode.
        gen_box = ttk.LabelFrame(strip, text="Generators (layers)", padding=4)
        gen_box.pack(fill="x", pady=(0, 6))
        if track.generator_layers:
            # Layer mode selector.
            mode_row = ttk.Frame(gen_box)
            mode_row.pack(fill="x", pady=(0, 4))
            ttk.Label(mode_row, text="Mode:", font=("", 8)).pack(side="left")
            mode_var = tk.StringVar(value=track.layer_mode)
            mode_combo = ttk.Combobox(
                mode_row, textvariable=mode_var, width=10,
                values=["all", "random", "sequential"], state="readonly",
                font=("", 8))
            mode_combo.pack(side="left", padx=(4, 0))
            def _on_mode_change(evt, tid=track.id, var=mode_var):
                self._on_set_layer_mode(tid, var.get())
            mode_combo.bind("<<ComboboxSelected>>", _on_mode_change)
            # Per-layer rows.
            for li, layer in enumerate(track.generator_layers):
                lrow = ttk.Frame(gen_box)
                lrow.pack(fill="x", pady=2)
                # Enable checkbox.
                en_var = tk.BooleanVar(value=layer.enabled)
                def _on_toggle(tid=track.id, idx=li, var=en_var):
                    self._on_toggle_layer(tid, idx, var.get())
                ttk.Checkbutton(lrow, variable=en_var,
                                command=_on_toggle).pack(side="left")
                # Name.
                ttk.Label(lrow, text=layer.generator.display_name(),
                          font=("", 8, "bold")).pack(side="left", padx=(2, 4))
                # Pitch offset.
                ttk.Label(lrow, text="Pitch:", font=("", 7)).pack(side="left")
                po_var = tk.StringVar(value=str(layer.pitch_offset))
                po_spin = ttk.Spinbox(lrow, from_=-48, to=48, width=4,
                                      textvariable=po_var, font=("", 7))
                po_spin.pack(side="left", padx=(2, 4))
                def _on_pitch(tid=track.id, idx=li, var=po_var):
                    try:
                        self._on_set_layer_pitch(tid, idx, int(var.get()))
                    except ValueError:
                        pass
                po_spin.bind("<FocusOut>", lambda e, f=_on_pitch: f())
                po_spin.bind("<Return>", lambda e, f=_on_pitch: f())
                # Gain.
                ttk.Label(lrow, text="Gain:", font=("", 7)).pack(side="left")
                g_var = tk.StringVar(value=f"{layer.gain:.2f}")
                g_spin = ttk.Spinbox(lrow, from_=0.0, to=2.0, increment=0.1,
                                     width=4, textvariable=g_var, font=("", 7))
                g_spin.pack(side="left", padx=(2, 4))
                def _on_gain(tid=track.id, idx=li, var=g_var):
                    try:
                        self._on_set_layer_gain(tid, idx, float(var.get()))
                    except ValueError:
                        pass
                g_spin.bind("<FocusOut>", lambda e, f=_on_gain: f())
                g_spin.bind("<Return>", lambda e, f=_on_gain: f())
                # Buttons.
                ttk.Button(lrow, text="Edit", width=5,
                           command=lambda t=track, i=li: self._on_edit_generator(
                               t.id, i)).pack(side="left", padx=(2, 0))
                ttk.Button(lrow, text="x", width=3,
                           command=lambda t=track, i=li: self._on_remove_layer(
                               t.id, i)).pack(side="right")
            # Add layer button.
            ttk.Button(gen_box, text="Add layer...",
                       command=lambda t=track: self._on_add_layer(
                           t.id)).pack(anchor="w", pady=(4, 0))
        else:
            ttk.Label(gen_box, text="Built-in voices",
                      foreground="#8b949e", font=("", 8)).pack(anchor="w")
            # Velocity tracking (FL 3xOsc Volume Tracking model):
            # per-note velocity -> voice filter cutoff. Bipolar amount
            # slider + middle-velocity slider. Applies to built-in
            # voices only; CLAP instruments map velocity themselves.
            # ASCII labels only (Ubuntu 20.04 RenderAddGlyphs lesson).
            vt_row = ttk.Frame(gen_box)
            vt_row.pack(fill="x", pady=(2, 0))
            ttk.Label(vt_row, text="VelTrack:", font=("", 8)).pack(
                side="left")
            vt_var = tk.DoubleVar(value=track.vel_track * 100)
            vt_scale = tk.Scale(vt_row, from_=-100, to=100,
                                orient="horizontal", variable=vt_var,
                                length=64, showvalue=False,
                                bg="#161b22", highlightthickness=0)
            vt_scale.pack(side="left", padx=2)
            vt_label = ttk.Label(vt_row,
                                 text=f"{track.vel_track * 100:+.0f}%",
                                 width=5, font=("", 8))
            vt_label.pack(side="left")
            vtm_var = tk.DoubleVar(value=track.vel_track_mid * 100)
            vtm_scale = tk.Scale(vt_row, from_=0, to=100,
                                 orient="horizontal", variable=vtm_var,
                                 length=44, showvalue=False,
                                 bg="#161b22", highlightthickness=0)
            vtm_scale.pack(side="left", padx=(2, 0))
            vtm_label = ttk.Label(vt_row,
                                  text=f"{track.vel_track_mid * 100:.0f}",
                                  width=3, font=("", 8))
            vtm_label.pack(side="left")
            vt_scale.bind(
                "<ButtonRelease-1>",
                lambda _e, t=track, v=vt_var, m=vtm_var,
                lab=vt_label: self._commit_vel_track(
                    t.id, v.get() / 100.0, m.get() / 100.0, lab, None))
            vt_scale.bind(
                "<Motion>",
                lambda _e, v=vt_var, lab=vt_label:
                lab.configure(text=f"{v.get():+.0f}%"), add="+")
            vtm_scale.bind(
                "<ButtonRelease-1>",
                lambda _e, t=track, v=vt_var, m=vtm_var,
                lab=vtm_label: self._commit_vel_track(
                    t.id, v.get() / 100.0, m.get() / 100.0, None, lab))
            vtm_scale.bind(
                "<Motion>",
                lambda _e, m=vtm_var, lab=vtm_label:
                lab.configure(text=f"{m.get():.0f}"), add="+")
            Tooltip(vt_scale,
                    "Velocity tracking amount (bipolar): per-note velocity "
                    "offsets the voice filter cutoff.\n"
                    "+ = hard notes brighter, - = hard notes darker, "
                    "0 = off")
            Tooltip(vtm_scale,
                    "Middle velocity (0-100): the velocity with no cutoff "
                    "offset. Above = positive offset, below = negative.")
            # Keyboard tracking (FL Channel Keyboard Tracker model):
            # per-note pitch -> voice filter cutoff. Bipolar amount
            # slider + middle-note slider (MIDI 0-127, shown as note
            # name). +100% = one octave of pitch moves the cutoff one
            # octave. Applies to built-in voices only; CLAP instruments
            # map pitch themselves.
            kt_row = ttk.Frame(gen_box)
            kt_row.pack(fill="x", pady=(2, 0))
            ttk.Label(kt_row, text="KeyTrack:", font=("", 8)).pack(
                side="left")
            kt_var = tk.DoubleVar(value=track.key_track * 100)
            kt_scale = tk.Scale(kt_row, from_=-100, to=100,
                                orient="horizontal", variable=kt_var,
                                length=64, showvalue=False,
                                bg="#161b22", highlightthickness=0)
            kt_scale.pack(side="left", padx=2)
            kt_label = ttk.Label(kt_row,
                                 text=f"{track.key_track * 100:+.0f}%",
                                 width=5, font=("", 8))
            kt_label.pack(side="left")
            ktm_var = tk.DoubleVar(value=track.key_track_mid)
            ktm_scale = tk.Scale(kt_row, from_=0, to=127,
                                 orient="horizontal", variable=ktm_var,
                                 length=44, showvalue=False,
                                 bg="#161b22", highlightthickness=0)
            ktm_scale.pack(side="left", padx=(2, 0))
            ktm_label = ttk.Label(kt_row,
                                  text=_midi_note_name(track.key_track_mid),
                                  width=3, font=("", 8))
            ktm_label.pack(side="left")
            kt_scale.bind(
                "<ButtonRelease-1>",
                lambda _e, t=track, v=kt_var, m=ktm_var,
                lab=kt_label: self._commit_key_track(
                    t.id, v.get() / 100.0, m.get(), lab, None))
            kt_scale.bind(
                "<Motion>",
                lambda _e, v=kt_var, lab=kt_label:
                lab.configure(text=f"{v.get():+.0f}%"), add="+")
            ktm_scale.bind(
                "<ButtonRelease-1>",
                lambda _e, t=track, v=kt_var, m=ktm_var,
                lab=ktm_label: self._commit_key_track(
                    t.id, v.get() / 100.0, m.get(), None, lab))
            ktm_scale.bind(
                "<Motion>",
                lambda _e, m=ktm_var, lab=ktm_label:
                lab.configure(text=_midi_note_name(m.get())), add="+")
            Tooltip(kt_scale,
                    "Keyboard tracking amount (bipolar): per-note pitch "
                    "offsets the voice filter cutoff.\n"
                    "+ = high notes brighter, - = high notes darker, "
                    "0 = off")
            Tooltip(ktm_scale,
                    "Middle note: the pitch with no cutoff offset. "
                    "Above = positive offset, below = negative.")
            ttk.Button(gen_box, text="Set instrument...",
                       command=lambda t=track: self._on_add_layer(
                           t.id)).pack(anchor="w", pady=(4, 0))

        ttk.Separator(strip, orient="horizontal").pack(fill="x", pady=6)

        # Insert effects.
        for i, fx in enumerate(track.effects):
            self._build_effect(strip, track, i, fx)

        add = ttk.Frame(strip)
        add.pack(fill="x", pady=(2, 0))
        ttk.Label(add, text="Add FX:").pack(side="left")
        for kind, short in (("delay", "Dly"), ("drive", "Drv"),
                            ("filter", "Flt"), ("ducker", "Dck"),
                            ("pitchshift", "Ptc")):
            ttk.Button(add, text=short, width=4,
                       command=lambda t=track, k=kind: self._on_add_effect(
                           t.id, k)).pack(side="left", padx=2)
        ttk.Button(add, text="Plug", width=5,
                   command=lambda t=track: self._on_add_plugin(t.id)
                   ).pack(side="left", padx=2)

        # Presets (effect/chain/track -- 3-layer state system).
        presets = ttk.Frame(strip)
        presets.pack(fill="x", pady=(4, 0))
        ttk.Label(presets, text="Presets:").pack(side="left")
        ttk.Button(presets, text="Save FX", width=7,
                   command=lambda t=track: self._on_save_chain_preset(t.id)
                   if self._on_save_chain_preset else None
                   ).pack(side="left", padx=2)
        ttk.Button(presets, text="Load FX", width=7,
                   command=lambda t=track: self._on_load_chain_preset(t.id)
                   if self._on_load_chain_preset else None
                   ).pack(side="left", padx=2)
        ttk.Button(presets, text="Save Trk", width=7,
                   command=lambda t=track: self._on_save_track_preset(t.id)
                   if self._on_save_track_preset else None
                   ).pack(side="left", padx=2)
        ttk.Button(presets, text="Load Trk", width=7,
                   command=lambda t=track: self._on_load_track_preset(t.id)
                   if self._on_load_track_preset else None
                   ).pack(side="left", padx=2)

        # Sends (post-fader, post-FX -- FL normal send behavior).
        sends_frame = ttk.LabelFrame(strip, text="Sends", padding=4)
        sends_frame.pack(fill="x", pady=(6, 0))
        self._build_sends(sends_frame, track)

        # Track live widgets for in-place updates (no rebuild flash).
        self._strip_widgets[track.id] = {
            "frame": strip,
            "name_label": name_label,
            "badge_label": badge_label,
            "accent_bar": accent_bar,
            "muted": muted,
            "vol_var": vol_var,
            "vol_label": vol_label,
            "knob": knob,
            "meter": meter,
            "meter_state": MeterState(),
            "fx_count": len(track.effects),
            "gen_id": ",".join(l.generator.plugin_id
                               for l in track.generator_layers),
            "layer_count": len(track.generator_layers),
            "send_count": len(track.sends),
            "vt_var": vt_var if not track.generator_layers else None,
            "vt_label": vt_label if not track.generator_layers else None,
            "vtm_var": vtm_var if not track.generator_layers else None,
            "vtm_label": vtm_label if not track.generator_layers else None,
            "kt_var": kt_var if not track.generator_layers else None,
            "kt_label": kt_label if not track.generator_layers else None,
            "ktm_var": ktm_var if not track.generator_layers else None,
            "ktm_label": ktm_label if not track.generator_layers else None,
        }

    def _update_strip(self, track) -> None:
        """Update an existing strip's widgets in place (no flashing).

        Falls back to rebuilding just this strip if the effect list or
        generator changed structurally.
        """
        w = self._strip_widgets.get(track.id)
        if w is None:
            return
        gen_id = ",".join(l.generator.plugin_id
                          for l in track.generator_layers)
        if (w["fx_count"] != len(track.effects) or w["gen_id"] != gen_id
                or w["layer_count"] != len(track.generator_layers)
                or w["send_count"] != len(track.sends)):
            # Structural change: rebuild just this strip.
            w["frame"].destroy()
            del self._strip_widgets[track.id]
            self._build_strip(track)
            return
        pres = build_strip_presentations([track])[0]
        geo = strip_geometry(pres)
        w["name_label"].configure(text=geo["name"])
        w["badge_label"].configure(text=geo["badge_text"])
        if geo["badge_text"]:
            w["badge_label"].pack(side="left", padx=(6, 0))
        else:
            w["badge_label"].pack_forget()
        w["accent_bar"].configure(bg=geo["accent_color"])
        w["muted"].set(track.muted)
        w["vol_var"].set(track.gain * 100)
        w["vol_label"].configure(text=f"{track.gain * 100:.0f}%")
        w["knob"].set(track.pan)
        if w.get("vt_var") is not None:
            w["vt_var"].set(track.vel_track * 100)
            w["vt_label"].configure(text=f"{track.vel_track * 100:+.0f}%")
            w["vtm_var"].set(track.vel_track_mid * 100)
            w["vtm_label"].configure(text=f"{track.vel_track_mid * 100:.0f}")
        if w.get("kt_var") is not None:
            w["kt_var"].set(track.key_track * 100)
            w["kt_label"].configure(text=f"{track.key_track * 100:+.0f}%")
            w["ktm_var"].set(track.key_track_mid)
            w["ktm_label"].configure(
                text=_midi_note_name(track.key_track_mid))

    def _commit_volume(self, track_id, value, label) -> None:
        label.configure(text=f"{value:.0f}%")
        self._on_volume(track_id, round(value / 100, 3))

    def _commit_vel_track(self, track_id: str, amount: float, mid: float,
                          amount_label, mid_label) -> None:
        """Commit velocity-tracking amount (-1..1) and middle (0..1)."""
        if amount_label is not None:
            amount_label.configure(text=f"{amount * 100:+.0f}%")
        if mid_label is not None:
            mid_label.configure(text=f"{mid * 100:.0f}")
        if self._on_vel_track:
            self._on_vel_track(track_id, round(amount, 3), round(mid, 3))

    def _commit_key_track(self, track_id: str, amount: float, mid: float,
                          amount_label, mid_label) -> None:
        """Commit keyboard-tracking amount (-1..1) and middle note (0..127)."""
        if amount_label is not None:
            amount_label.configure(text=f"{amount * 100:+.0f}%")
        if mid_label is not None:
            mid_label.configure(text=_midi_note_name(mid))
        if self._on_key_track:
            self._on_key_track(track_id, round(amount, 3), round(mid, 1))

    def _build_effect(self, strip, track, index, fx) -> None:
        box = ttk.LabelFrame(strip, text="", padding=4)
        box.pack(fill="x", pady=3)
        header = ttk.Frame(box)
        header.pack(fill="x")
        ttk.Label(header, text=fx.display_name(),
                  font=("", 9, "bold")).pack(side="left")
        ttk.Button(header, text="x", width=3,
                   command=lambda t=track, i=index: self._on_remove_effect(
                       t.id, i)).pack(side="right")
        if fx.kind == "plugin":
            # Plugins get a generic editor dialog plus their native GUI
            # (floating window) when the plugin provides one.
            ttk.Button(header, text="GUI", width=5,
                       command=lambda t=track, i=index: self._on_open_gui(
                           t.id, i)).pack(side="right", padx=(0, 4))
            ttk.Button(header, text="Edit...", width=6,
                       command=lambda t=track, i=index: self._on_edit_plugin(
                           t.id, i)).pack(side="right", padx=(0, 4))
            ttk.Label(box, text=f"{len(fx.params)} parameters",
                      foreground="#8b949e", font=("", 8)).pack(anchor="w")
            return

        for param, label, lo, hi, unit, _default in FX_DEFS.get(fx.kind, []):
            row = ttk.Frame(box)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=9).pack(side="left")
            val = float(fx.params.get(param, lo))
            var = tk.DoubleVar(value=val)
            val_label = ttk.Label(row, width=10)
            val_label.pack(side="right")

            def fmt(v, u=unit):
                return f"{v:.0f} {u}" if u in ("ms", "Hz") else f"{v:.0f}{u}"

            val_label.configure(text=fmt(val))
            sc = tk.Scale(row, from_=lo, to=hi, orient="horizontal",
                          variable=var, showvalue=False,
                          bg="#161b22", fg="#e6edf3", highlightthickness=0)
            sc.pack(side="left", fill="x", expand=True)
            sc.bind("<ButtonRelease-1>",
                    lambda _e, t=track, i=index, p=param, v=var, lab=val_label,
                    f=fmt: self._commit_fx_param(t.id, i, p, v.get(), lab, f))
            sc.bind("<Motion>",
                    lambda _e, v=var, lab=val_label, f=fmt:
                    lab.configure(text=f(v.get())), add="+")

    def _commit_fx_param(self, track_id, index, param, value, label, fmt) -> None:
        label.configure(text=fmt(value))
        self._on_effect_param(track_id, index, param, round(float(value), 3))
