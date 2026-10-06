"""Typed, coarse-grained bridge between the Python app and the Rust engine.

Design (per the architecture guide):
* Whole-pattern snapshots only -- Python never makes per-sample calls.
* Transport commands are single calls (play/stop).
* Reads are polling-style (position, stats); the UI polls on a timer.
* All Rust errors surface as EngineError with a message.
"""

from __future__ import annotations

from .project import FX_DEFS, Project, parse_auto_param


class EngineError(Exception):
    """Raised when the audio engine reports a failure."""


class EngineBridge:
    def __init__(self, sample_rate: int = 44100):
        try:
            from ._daw_engine_rs import PyEngine as _NativeEngine
        except ImportError as e:
            raise EngineError(
                "the Rust engine extension (daw._daw_engine_rs) is not built. "
                "Run: maturin develop"
            ) from e
        try:
            self._eng = _NativeEngine(sample_rate)
        except Exception as e:
            raise EngineError(f"could not create audio engine: {e}") from e
        self._sample_rate = sample_rate

    # -- arrangement snapshots ------------------------------------------

    @staticmethod
    def arrangement_dict(project: Project, base_dir: str | None = None) -> dict:
        """Build the engine's arrangement dict for a project.

        Public so callers (e.g. the threaded exporter) can snapshot the
        arrangement on the UI thread before handing it to a worker.

        `base_dir` is the project file's directory (None for unsaved
        projects): sample asset paths are resolved to absolute paths
        against it before crossing the bridge.
        """
        patterns = []
        index_of = {}
        for i, pat in enumerate(project.patterns):
            index_of[pat.id] = i
            patterns.append({
                "id": pat.id,
                "name": pat.name,
                "steps": int(pat.steps),
                "channels": [
                    {
                        "instrument": ch.instrument,
                        "notes": [
                            {
                                "start": float(n.start),
                                "len": float(n.length),
                                "pitch": int(n.pitch),
                                "vel": float(n.vel),
                                # FL-style 0.0-1.0 -> engine -1.0..1.0.
                                "pan": float(n.pan) * 2.0 - 1.0,
                            }
                            for n in ch.notes
                        ],
                    }
                    for ch in pat.channels
                ],
            })
        # Automation lanes grouped per track, values converted to engine
        # units (same conversion as Effect.engine_params: % -> fraction).
        track_index = {t.id: i for i, t in enumerate(project.tracks)}
        lanes_by_track: dict[str, list] = {}
        for lane in project.automation:
            kind, idx, name = parse_auto_param(lane.param)
            conv = 1.0
            engine_param = lane.param
            if kind == "gen":
                # Generator params are already in plugin real units.
                conv = 1.0
            elif kind == "fx":
                fx_kind = project.track_by_id(lane.track_id).effects[idx].kind
                if fx_kind == "plugin":
                    # Plugin params are already in real units.
                    conv = 1.0
                else:
                    _p, _label, _lo, _hi, unit, _d = next(
                        f for f in FX_DEFS[fx_kind] if f[0] == name)
                    conv = 0.01 if unit == "%" else 1.0
            elif kind == "send":
                # The engine addresses send destinations by track INDEX;
                # Python lanes use stable track ids. Translate here.
                # (Python validates the route exists; this is a backstop.)
                engine_param = (
                    f"send.{track_index.get(name, 0)}.amount"
                )
            # Tangents are d(value)/d(beat): the value conversion
            # applies to them exactly like it does to values.
            pts = []
            for p in lane.points:
                pts.append([
                    float(p.beat),
                    float(p.value) * conv,
                    None if p.in_tan is None else float(p.in_tan) * conv,
                    None if p.out_tan is None else float(p.out_tan) * conv,
                ])
            lfo = lane.lfo.to_dict() if lane.lfo is not None else None
            lanes_by_track.setdefault(lane.track_id, []).append({
                "param": engine_param,
                "points": pts,
                "interp": lane.interp,
                "tension": float(lane.tension),
                "lfo": lfo,
            })
        return {
            "tempo": float(project.tempo),
            "truncate_notes": bool(project.truncate_notes),
            "patterns": patterns,
            # Sample assets: the engine decodes any id it hasn't seen
            # (set_arrangement) or decodes them itself (render_wav_file).
            "samples": [
                {"id": s.id, "path": s.resolve(base_dir)}
                for s in project.samples
            ],
            "tracks": [
                {
                    "name": t.name,
                    "id": t.id,
                    "gain": float(t.gain),
                    "pan": float(t.pan),
                    "muted": bool(t.muted),
                    "vel_track": float(t.vel_track),
                    "vel_track_mid": float(t.vel_track_mid),
                    "key_track": float(t.key_track),
                    "key_track_mid": float(t.key_track_mid),
                    "effects": [e.engine_params() for e in t.effects],
                    "automation": lanes_by_track.get(t.id, []),
                    "generator_layers": [l.engine_params()
                                         for l in t.generator_layers],
                    "layer_mode": t.layer_mode,
                    "clips": [
                        {
                            "pattern": index_of[c.pattern_id],
                            "start_beat": int(c.start_beat),
                            "bars": int(c.bars),
                        }
                        for c in t.clips
                    ],
                    "audio_clips": [
                        {
                            "asset": c.asset_id,
                            "start_beat": float(c.start_beat),
                            "length_beats": float(c.length_beats),
                            "start_offset_beats": float(c.start_offset_beats),
                            "gain": float(c.gain),
                            # FL-style 0.0-1.0 -> engine -1.0..1.0.
                            "pan": float(c.pan) * 2.0 - 1.0,
                            "pitch_semitones": float(c.pitch_semitones),
                            "fine_cents": float(c.fine_cents),
                            "reverse": bool(c.reverse),
                            "muted": bool(c.muted),
                        }
                        for c in t.audio_clips
                    ],
                    "sends": [
                        {
                            "to": s.to_track_id,
                            "amount": float(s.amount),
                            "tap": s.tap,
                            "pan": float(s.pan),
                            "sidechain": bool(s.sidechain),
                        }
                        for s in t.sends
                    ],
                    "output": t.output,
                }
                for t in project.tracks
            ],
        }

    def push_project(self, project: Project, base_dir: str | None = None) -> None:
        """Send a full arrangement snapshot to the engine.

        `base_dir` resolves sample asset paths (the project file's
        directory; None for unsaved projects).
        """
        try:
            self._eng.set_arrangement(self.arrangement_dict(project, base_dir))
        except Exception as e:
            raise EngineError(f"engine rejected arrangement: {e}") from e

    def load_sample(self, asset_id: str, path: str) -> dict:
        """Decode an audio file into the engine's sample registry.

        Returns {"frames", "duration_secs", "source_sample_rate"}.
        Usually unnecessary: `push_project` auto-loads declared assets.
        """
        try:
            return dict(self._eng.load_sample(asset_id, path))
        except Exception as e:
            raise EngineError(f"could not load sample '{path}': {e}") from e

    def unload_sample(self, asset_id: str) -> None:
        """Drop a decoded asset from the engine registry."""
        try:
            self._eng.unload_sample(asset_id)
        except Exception as e:
            raise EngineError(f"could not unload sample '{asset_id}': {e}") from e

    def sample_peaks(self, asset_id: str, n: int = 256) -> list[tuple[float, float]]:
        """Waveform peak pairs for display: `n` (min, max) buckets."""
        try:
            return [(float(lo), float(hi))
                    for lo, hi in self._eng.sample_peaks(asset_id, int(n))]
        except Exception as e:
            raise EngineError(f"could not read peaks for '{asset_id}': {e}") from e

    def set_tempo(self, bpm: float) -> None:
        try:
            self._eng.set_tempo(float(bpm))
        except Exception as e:
            raise EngineError(f"engine rejected tempo: {e}") from e

    def set_multithreaded(self, enabled: bool) -> None:
        """Enable/disable multithreaded rendering (FL-style global switch)."""
        try:
            self._eng.set_multithreaded(bool(enabled))
        except Exception as e:
            raise EngineError(f"engine rejected multithreaded: {e}") from e

    # -- transport --------------------------------------------------------

    def play(self) -> None:
        try:
            self._eng.play()
        except Exception as e:
            raise EngineError(f"could not start playback: {e}") from e

    def stop(self) -> None:
        try:
            self._eng.stop()
        except Exception as e:
            raise EngineError(f"could not stop playback: {e}") from e

    def is_playing(self) -> bool:
        return bool(self._eng.is_playing())

    def position_beats(self) -> float:
        return float(self._eng.position_beats())

    # -- debug instrumentation (silent choke verification) ------------------

    def debug_peaks(self) -> list[float]:
        """Per-track peak levels since the last call."""
        try:
            return [float(v) for v in self._eng.debug_peaks()]
        except Exception:
            return []

    def debug_voices(self) -> list[int]:
        """Per-track active voice/note counts."""
        try:
            return [int(v) for v in self._eng.debug_voices()]
        except Exception:
            return []

    def debug_events(self) -> list[dict]:
        """Drain recorded note events.

        Each is {"track": int, "kind": "on"|"off"|"choke",
        "key": int, "note_id": int, "sample": int}.
        """
        try:
            return [
                {"track": int(t), "kind": str(k), "key": int(key),
                 "note_id": int(nid), "sample": int(s)}
                for t, k, key, nid, s in self._eng.debug_events()
            ]
        except Exception:
            return []

    # -- offline render ---------------------------------------------------

    def render_wav(self, path: str, loops: int = 4) -> None:
        try:
            self._eng.render_wav(path, int(loops))
        except Exception as e:
            raise EngineError(f"render failed: {e}") from e

    def render_wav_threaded(self, arrangement: dict, path: str, loops: int,
                            progress) -> bool:
        """Render on the calling thread without holding the GIL.

        Unlike `render_wav` (a method on the main-thread engine handle),
        this is a module-level function over an immutable arrangement
        snapshot, so it is safe to call from a worker thread while the UI
        stays responsive. `progress(done_blocks, total_blocks)` is called
        from the render thread; it must return True to continue or False
        to abort. Returns True when the file was fully written.
        """
        try:
            from ._daw_engine_rs import render_wav_file as _render
        except ImportError as e:
            raise EngineError(
                "the Rust engine extension (daw._daw_engine_rs) is not built. "
                "Run: maturin develop"
            ) from e
        try:
            return bool(_render(arrangement, self._sample_rate, path,
                                int(loops), progress))
        except Exception as e:
            raise EngineError(f"render failed: {e}") from e

    # -- introspection ----------------------------------------------------

    def backend_name(self) -> str:
        return str(self._eng.backend_name())

    def stats(self) -> dict:
        return dict(self._eng.stats())

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    # -- CLAP plugin hosting ----------------------------------------------

    def scan_clap_plugins(self) -> list:
        """Scan for CLAP audio-effect plugins (control thread)."""
        return self._eng.scan_clap_plugins()

    def clap_plugin_params(self, path: str, plugin_id: str) -> list:
        """List a plugin's parameters (control thread)."""
        return self._eng.clap_plugin_params(path, plugin_id)

    def check_clap_plugin(self, path: str, plugin_id: str) -> None:
        """Fully load-test a plugin; raises EngineError on failure."""
        try:
            self._eng.check_clap_plugin(path, plugin_id)
        except Exception as e:
            raise EngineError(str(e)) from e

    def save_plugin_states(self) -> list:
        """Ask the engine for every live plugin's opaque state blob.

        Returns [{"track", "fx_index", "layer_index", "plugin_id",
        "state_base64"}]. Control thread only; call at project-save time.
        """
        try:
            return self._eng.save_plugin_states()
        except Exception as e:
            raise EngineError(str(e)) from e

    def take_plugin_dirty_slots(self) -> list:
        """Drain plugin slots that called CLAP mark_dirty() since the last
        call. Returns [{"track", "fx_index", "layer_index"}] ("" when not
        applicable). The UI polls this and marks the project dirty: the
        plugin's non-parameter state changed outside the host's view.
        """
        try:
            return self._eng.take_plugin_dirty_slots()
        except Exception as e:
            raise EngineError(str(e)) from e

    def take_preset_events(self) -> dict:
        """Drain preset-load loaded()/on_error() reports since the last
        call. Returns {"loaded": [...], "errors": [...]} for preset
        browser synchronization.
        """
        try:
            return self._eng.take_preset_events()
        except Exception as e:
            raise EngineError(str(e)) from e

    def take_restart_requests(self) -> list:
        """Drain plugin restart requests (request_restart()) and
        latency-change notifications (latency.changed()) since the last
        call. Returns [{"kind", "track", "fx_index", "layer_index"}];
        kind is "restart" or "latency_changed". The UI polls this and
        services them with process_plugin_restarts().
        """
        try:
            return self._eng.take_restart_requests()
        except Exception as e:
            raise EngineError(str(e)) from e

    def process_plugin_restarts(self) -> list:
        """Restart every slot with a pending restart/latency request:
        state preserved via FOR_DUPLICATE, latency re-queried
        post-activation, PDC recalculated. Returns the restarted slots
        as [{"track", "fx_index", "layer_index"}].
        """
        try:
            return self._eng.process_plugin_restarts()
        except Exception as e:
            raise EngineError(str(e)) from e

    def plugin_latency_samples(self, track, fx_index=None,
                               layer_index=None):
        """The plugin's currently reported latency in samples for a
        slot (None when the slot has no live instance). This is the
        value PDC compensates for — the same number FL Studio's
        Wrapper shows as detected plugin latency.
        """
        try:
            return self._eng.plugin_latency_samples(
                track, fx_index=fx_index, layer_index=layer_index)
        except Exception as e:
            raise EngineError(str(e)) from e

    def save_plugin_preset_blob(self, track, fx_index=None,
                                layer_index=None) -> str:
        """Save one slot's plugin state with the FOR_PRESET state context.
        Returns the blob as Base64; the caller writes the preset file.
        """
        try:
            return self._eng.save_plugin_preset_blob(
                track, fx_index=fx_index, layer_index=layer_index)
        except Exception as e:
            raise EngineError(str(e)) from e

    def load_plugin_preset_blob(self, track, state_base64,
                                fx_index=None, layer_index=None) -> dict:
        """Load a preset blob (from save_plugin_preset_blob) with the
        FOR_PRESET context. Returns {"params": {str(id): value},
        "state_base64": str} — store both in one undoable edit.
        Rescanned values are NOT automation.
        """
        try:
            return self._eng.load_plugin_preset_blob(
                track, fx_index=fx_index, layer_index=layer_index,
                state_base64=state_base64)
        except Exception as e:
            raise EngineError(str(e)) from e

    def plugin_supports_preset_load(self, track, fx_index=None,
                                    layer_index=None) -> bool:
        """Does the slot's plugin implement CLAP_EXT_PRESET_LOAD
        (native preset files via from_location())?"""
        try:
            return self._eng.plugin_supports_preset_load(
                track, fx_index=fx_index, layer_index=layer_index)
        except Exception as e:
            raise EngineError(str(e)) from e

    def plugin_preset_from_location(self, track, path, fx_index=None,
                                    layer_index=None, load_key=None) -> dict:
        """Ask the slot's plugin to load a native preset file
        (CLAP_EXT_PRESET_LOAD from_location()). Returns {"params": ...,
        "state_base64": ...} like load_plugin_preset_blob.
        """
        try:
            return self._eng.plugin_preset_from_location(
                track, fx_index=fx_index, layer_index=layer_index,
                path=path, load_key=load_key)
        except Exception as e:
            raise EngineError(str(e)) from e

    def scan_clap_instruments(self) -> list:
        """Scan for CLAP instrument plugins (control thread)."""
        return self._eng.scan_clap_instruments()

    def check_clap_instrument(self, path: str, plugin_id: str) -> None:
        """Fully load-test an instrument; raises EngineError on failure."""
        try:
            self._eng.check_clap_instrument(path, plugin_id)
        except Exception as e:
            raise EngineError(str(e)) from e

    def open_plugin_gui(
        self, path: str, plugin_id: str, params: dict
    ) -> int:
        """Open a plugin's native GUI as a floating window.

        `params` maps str(clap_id) -> value and is flushed into the GUI
        instance so it reflects the project. Returns a GUI id for
        `plugin_gui_params`/`close_plugin_gui`. Raises EngineError if the
        plugin has no usable GUI.
        """
        try:
            return self._eng.open_plugin_gui(path, plugin_id, params)
        except Exception as e:
            raise EngineError(str(e)) from e

    def plugin_gui_params(self, gui_id: int) -> dict:
        """Read current param values from an open plugin GUI."""
        return self._eng.plugin_gui_params(gui_id)

    def close_plugin_gui(self, gui_id: int) -> None:
        """Hide and destroy an open plugin GUI (idempotent)."""
        self._eng.close_plugin_gui(gui_id)
