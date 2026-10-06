"""Debug window: silent choke verification tools.

Implements the FL Studio "Silent Choke Verification" infographic's
toolset for Pulsegrid, without needing audio hardware:

- Event log: note on/off/choke events with sample positions
  (infographic section 4, event-level observation).
- Voice counts: per-track active voices/notes (section 2, polyphony).
- Peak meters: per-track levels with peak-hold ballistics (section 1,
  visual meter monitoring).
- "Verify choke" button: automated offline render + waveform analysis
  (section 3, most precise/deterministic).

Polls the engine at ~15 Hz; all engine calls are non-blocking.
"""

import tkinter as tk
from tkinter import ttk

from ..verify_choke import verify_loop_wrap_choke
from .mixer_render import MeterRenderer, MeterState, MeterTheme

# Debug meters reuse the extracted renderer (research topic 29) with a
# horizontal theme: one presentation model, multiple render targets.
# Decay is per poll tick; the debug window polls at ~15 Hz (66 ms).
_DEBUG_METER_THEME = MeterTheme(
    orientation="horizontal",
    width=None,  # live canvas width
    height=14,
    decay=0.92,
    hold_ticks=15,  # ~1 s at 66 ms polls
)


class DebugWindow(tk.Toplevel):
    """Floating debug window. Created on demand from the View menu."""

    def __init__(self, master, get_bridge, get_project,
                 get_dirty_tracker=None):
        """get_bridge(): EngineBridge; get_project(): current Project;
        get_dirty_tracker(): DirtyTracker or None."""
        self._get_dirty_tracker = get_dirty_tracker
        super().__init__(master)
        self.title("Debug -- Silent Choke Verification")
        self.geometry("640x480")
        self._get_bridge = get_bridge
        self._get_project = get_project
        self._polling = True
        self._meter_states = {}  # track_id -> MeterState (presentation)
        self._meter_renderer = MeterRenderer(_DEBUG_METER_THEME)
        self._track_rows = {}    # track_id -> (voice_label, meter_canvas)

        main = ttk.Frame(self, padding=8)
        main.pack(fill="both", expand=True)

        # -- audio performance (research topic 37: real-time overload) -----
        # FL Studio's CPU-meter analog: max render time as a percentage of
        # the buffer deadline, plus FL's underrun counter analog.
        perf_frame = ttk.LabelFrame(main, text="Audio performance",
                                    padding=6)
        perf_frame.pack(fill="x")
        self._perf_backend = ttk.Label(perf_frame, text="Backend: -",
                                       font=("", 9))
        self._perf_backend.pack(anchor="w")
        perf_row = ttk.Frame(perf_frame)
        perf_row.pack(fill="x", pady=(2, 0))
        self._perf_buffer = ttk.Label(perf_row, text="Buffer: -", width=22,
                                      font=("", 9))
        self._perf_buffer.pack(side="left")
        self._perf_load = ttk.Label(perf_row, text="Buffer load: -", width=20,
                                    font=("", 9))
        self._perf_load.pack(side="left", padx=(8, 0))
        self._perf_underruns = ttk.Label(perf_row, text="Underruns: -",
                                         width=18, font=("", 9))
        self._perf_underruns.pack(side="left", padx=(8, 0))
        self._perf_hint = ttk.Label(
            perf_frame,
            text="Buffer load ~= % of the audio deadline the render used "
                 "(FL CPU-meter analog). Near 100% -> clicks/pops: raise the "
                 "buffer size in Settings > Audio.",
            font=("", 8), foreground="#8b949e", wraplength=600,
            justify="left")
        self._perf_hint.pack(anchor="w", pady=(4, 0))

        # -- per-track meters + voices ---------------------------------
        tracks_frame = ttk.LabelFrame(main, text="Tracks (live)", padding=6)
        tracks_frame.pack(fill="x", pady=(8, 0))
        self._tracks_frame = tracks_frame
        self._rebuild_tracks()

        # -- event log --------------------------------------------------
        log_frame = ttk.LabelFrame(main, text="Note event log", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        self._log = tk.Text(log_frame, height=12, wrap="none",
                            font=("TkFixedFont", 9),
                            background="#0d1117", foreground="#c9d1d9")
        self._log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, command=self._log.yview)
        scrollbar.pack(side="right", fill="y")
        self._log.configure(yscrollcommand=scrollbar.set)
        self._log.configure(state="disabled")
        for kind, color in (("on", "#7ee787"), ("off", "#79c0ff"),
                            ("choke", "#ffa657")):
            self._log.tag_configure(kind, foreground=color)

        # -- invalidation log (research topic 34) --------------------------
        inv_frame = ttk.LabelFrame(main, text="Invalidation (dirty regions)",
                                   padding=6)
        inv_frame.pack(fill="x", pady=(8, 0))
        self._inv_log = tk.Text(inv_frame, height=6, wrap="none",
                                font=("TkFixedFont", 9),
                                background="#0d1117", foreground="#c9d1d9")
        self._inv_log.pack(fill="x", expand=True)
        self._inv_log.configure(state="disabled")
        self._inv_log.tag_configure("geo", foreground="#79c0ff")
        self._inv_log.tag_configure("sem", foreground="#8b949e")
        self._inv_shown = 0

        # -- verification ------------------------------------------------
        ver_frame = ttk.Frame(main)
        ver_frame.pack(fill="x", pady=(8, 0))
        ttk.Button(ver_frame, text="Verify loop-wrap choke...",
                   command=self._run_verification).pack(side="left")
        self._ver_result = ttk.Label(ver_frame, text="", wraplength=480)
        self._ver_result.pack(side="left", padx=(8, 0))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    # -- track rows ------------------------------------------------------

    def _rebuild_tracks(self):
        for child in self._tracks_frame.winfo_children():
            child.destroy()
        self._track_rows.clear()
        project = self._get_project()
        if project is None:
            return
        for track in project.tracks:
            row = ttk.Frame(self._tracks_frame)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=track.name, width=18,
                      anchor="w").pack(side="left")
            voice_label = ttk.Label(row, text="voices: -", width=12)
            voice_label.pack(side="left", padx=(4, 4))
            meter = tk.Canvas(row, height=14, width=200,
                              background="#0d1117",
                              highlightthickness=1,
                              highlightbackground="#30363d")
            meter.pack(side="left", fill="x", expand=True)
            self._track_rows[track.id] = (voice_label, meter)
            self._meter_states[track.id] = MeterState(_DEBUG_METER_THEME)

    def refresh_tracks(self):
        """Rebuild rows when the project changes."""
        self._rebuild_tracks()

    # -- polling ----------------------------------------------------------

    def _poll(self):
        if not self._polling:
            return
        try:
            bridge = self._get_bridge()
            project = self._get_project()
            if bridge is not None and project is not None:
                self._poll_meters(bridge, project)
                self._poll_voices(bridge, project)
                self._poll_events(bridge, project)
                self._poll_perf(bridge)
            self._poll_invalidation()
        except Exception:
            pass
        self.after(66, self._poll)  # ~15 Hz

    def _poll_perf(self, bridge):
        """Audio performance readout (FL CPU-meter / underrun analogs)."""
        try:
            stats = bridge.backend_stats()
            backend = bridge.backend_name()
            sample_rate = bridge.sample_rate
        except Exception:
            return
        block_frames = stats["block_frames"]
        max_us = stats["max_callback_us"]
        self._perf_backend.config(text=f"Backend: {backend}")
        if block_frames and sample_rate:
            ms = block_frames / sample_rate * 1000.0
            self._perf_buffer.config(
                text=f"Buffer: {block_frames} frames ({ms:.1f} ms)")
            # FL's CPU meter: % of the buffer deadline the render consumed.
            deadline_us = block_frames / sample_rate * 1_000_000.0
            load = max_us / deadline_us * 100.0 if deadline_us else 0.0
            self._perf_load.config(text=f"Buffer load: {load:.0f}%")
            # Warn color when close to the deadline.
            self._perf_load.config(
                foreground="#f85149" if load >= 90 else
                           "#d29922" if load >= 70 else "#c9d1d9")
        else:
            self._perf_buffer.config(text="Buffer: -")
            self._perf_load.config(text="Buffer load: -")
        self._perf_underruns.config(
            text=f"Underruns: {stats['underruns']}")

    def _poll_invalidation(self):
        """Show recent dirty-region events (research topic 34)."""
        if self._get_dirty_tracker is None:
            return
        tracker = self._get_dirty_tracker()
        if tracker is None:
            return
        total = tracker.event_count()
        if total == self._inv_shown:
            return
        self._inv_shown = total
        events = tracker.recent(12)
        self._inv_log.configure(state="normal")
        self._inv_log.delete("1.0", "end")
        for e in events[-12:]:
            if e.whole_domain:
                line = f"{e.domain.name}: whole domain"
                tag = "sem"
            else:
                line = (f"{e.domain.name}: {len(e.rects)} rect(s), "
                        f"{e.merged_area:.0f}px^2")
                tag = "geo"
            if e.note:
                line += f" -- {e.note}"
            self._inv_log.insert("end", line + "\n", tag)
        self._inv_log.configure(state="disabled")

    def _poll_meters(self, bridge, project):
        peaks = bridge.debug_peaks()
        for i, track in enumerate(project.tracks):
            if track.id not in self._track_rows:
                continue
            raw = peaks[i] if i < len(peaks) else 0.0
            # Presentation (ballistics + peak hold) -> renderer. The
            # debug window only wires engine data to canvases.
            pres = self._meter_states[track.id].update(raw)
            _, meter = self._track_rows[track.id]
            self._meter_renderer.render(meter, pres)

    def _poll_voices(self, bridge, project):
        counts = bridge.debug_voices()
        for i, track in enumerate(project.tracks):
            if track.id not in self._track_rows:
                continue
            n = counts[i] if i < len(counts) else 0
            voice_label, _ = self._track_rows[track.id]
            voice_label.configure(text=f"voices: {n}")

    def _poll_events(self, bridge, project):
        events = bridge.debug_events()
        if not events:
            return
        names = {t.id: t.name for t in project.tracks}
        self._log.configure(state="normal")
        for e in events[-200:]:  # cap per poll to stay responsive
            track_name = names.get(
                project.tracks[e["track"]].id
                if e["track"] < len(project.tracks) else "",
                f"track {e['track']}")
            # Sample -> beats for readability.
            line = (f"{e['kind']:6s} {track_name:16s} "
                    f"key={e['key']:3d} id={e['note_id']:4d} "
                    f"sample={e['sample']}\n")
            self._log.insert("end", line, e["kind"])
        self._log.see("end")
        # Trim to keep the widget bounded.
        lines = int(self._log.index("end-1c").split(".")[0])
        if lines > 2000:
            self._log.delete("1.0", f"{lines - 2000}.0")
        self._log.configure(state="disabled")

    # -- verification ------------------------------------------------------

    def _run_verification(self):
        self._ver_result.configure(text="Rendering test...")
        self.update_idletasks()
        # Find the test synth fixture; fall back to any scanned instrument.
        synth_path = None
        try:
            from ..project import register_plugin  # noqa
            import os
            candidate = os.path.join(
                os.path.dirname(__file__), "..", "..", "..",
                "tests", "fixtures", "clap", "PulsegridTestSynth.clap")
            candidate = os.path.normpath(candidate)
            if os.path.exists(candidate):
                synth_path = candidate
        except Exception:
            pass
        if synth_path is None:
            self._ver_result.configure(
                text="Test synth fixture not found; cannot verify.")
            return
        try:
            report = verify_loop_wrap_choke(synth_path)
        except Exception as e:
            self._ver_result.configure(text=f"Verification error: {e}")
            return
        color = "#3fb950" if report["passed"] else "#f85149"
        self._ver_result.configure(text=report["detail"], foreground=color)

    def _on_close(self):
        self._polling = False
        self.destroy()
