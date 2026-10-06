"""Pulsegrid project model: versioned, validated, recoverable.

The project format is owned by Pulsegrid (JSON, documented in
docs/PROJECT_FORMAT.md). It is intentionally separate from the audio engine:
Python owns the document, the engine only receives snapshots.

Format versions:
* v1 -- single pattern, flat channel list (no playlist).
* v2 -- multiple named patterns + a playlist of tracks with clips.
  v1 files are migrated automatically on load.
* v3 -- channels carry per-step note pitches (piano roll editing).
  v2 files migrate by filling each step with the channel pitch.
* v4 -- channels carry per-step velocities; clip positions are stored
  in beats (`start_beat`) instead of whole bars. Older files migrate
  automatically (velocities default to 0.9, bar starts become beats).
* v5 -- channels carry a note list (`notes`: start/length/pitch/
  velocity per note) instead of per-step arrays. Notes have variable
  lengths and chords (overlapping notes) are allowed. Older files
  migrate automatically (each active step becomes a 1-step note).
* v6 -- automation lanes: per-track parameter envelopes
  (`automation`: track gain/pan and effect parameters over
  arrangement beats). Older files migrate automatically (no lanes).
* v7 -- CLAP plugin effects (`plugin_id`/`plugin_path`, param values,
  state blobs) on tracks.
* v8 -- CLAP instrument generators per track.
* v9 -- note choking at loop wrap + `truncate_notes` project setting.
* v10 -- per-track sends (post-fader amount-only routing).
* v11 -- generator layering (one-layer stacks).
* v12 -- routing options per send: `tap` (pre/post), `pan`, `sidechain`;
  send-amount automation lanes (`send.<dest_id>.amount`). Additive;
  older files migrate automatically (sends default to post tap,
  centered pan, audible).
* v13 -- per-track velocity tracking (`vel_track` bipolar amount,
  `vel_track_mid` middle velocity) for built-in voices, FL Studio
  3xOsc Volume Tracking model. Additive; older files migrate
  automatically (tracking off, mid 0.5).
* v14 -- per-note panning (`pan` 0.0 left .. 0.5 center .. 1.0 right,
  FL Studio note.pan convention) for built-in voices. Additive;
  older files migrate automatically (pan defaults to 0.5 center).
* v15 -- native sample assets + audio clips (FL Audio Clip / LMMS
  SampleClip model): one decoded `SampleBuffer` per audio file
  (`samples`), many per-instance `AudioClip`s per track with their
  own start/length/offset/gain/pan/pitch/reverse/mute. Additive;
  older files migrate automatically (no samples, no audio clips).
* v15+identity -- per-track presentation identity (research topic 33,
  FL Track Mode analog): `color_idx` + `icon` on each track and
  project-level `identity_groups` (linked tracks share name/color/icon;
  edits ripple). Additive; older files migrate automatically (tracks
  get positional default colors, no icon, no links).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from dataclasses import dataclass, field

from .track_identity import (TRACK_COLORS, TRACK_ICONS, TrackIdentity,
                             default_color_for, linked_members,
                             normalize_link_groups)

FORMAT_ID = "pulsegrid-project"
FORMAT_VERSION = 15

INSTRUMENTS = ("kick", "snare", "hat", "bass", "lead")

_DEFAULT_CHANNELS = (
    ("kick", "Kick", "kick", 36),
    ("snare", "Snare", "snare", 38),
    ("hat", "Hat", "hat", 42),
    ("bass", "Bass", "bass", 33),
    ("lead", "Lead", "lead", 64),
)


class ProjectError(Exception):
    """Raised for any project load/save/validation failure."""


def _validate_state_base64(value: str, what: str) -> None:
    """Check that an opaque CLAP state blob field is valid Base64.

    The blob itself is never interpreted here (it is the plugin's
    private format); this only guards the transport encoding so a
    corrupt project fails loudly instead of producing a garbage blob.
    """
    if not value:
        return
    if not isinstance(value, str):
        raise ProjectError(f"{what}: state_base64 must be a string")
    import base64
    try:
        base64.b64decode(value, validate=True)
    except Exception as e:
        raise ProjectError(f"{what}: state_base64 is not valid Base64: {e}") from None


def _next_id(prefix: str, existing: list[str]) -> str:
    """Allocate 'prefix-N' with N one above the highest numeric suffix seen."""
    best = 0
    for eid in existing:
        if eid.startswith(prefix + "-"):
            try:
                best = max(best, int(eid[len(prefix) + 1:]))
            except ValueError:
                pass
    return f"{prefix}-{best + 1}"


@dataclass
class Note:
    """One note: `start` and `length` in steps, MIDI `pitch`, `vel` 0-1.

    Notes may overlap (chords) and have any positive length; the piano
    roll edits them directly. Lengths are whole steps in the UI.
    `pan` is per-note stereo position in FL Studio note.pan convention:
    0.0 = hard left, 0.5 = center, 1.0 = hard right (default 0.5).
    It is a true note property, not automation: simultaneous notes can
    have independent pans.
    """
    start: float
    length: float
    pitch: int
    vel: float = 0.9
    pan: float = 0.5

    def validate(self, steps: int) -> None:
        if not isinstance(self.start, (int, float)) or not (0 <= self.start < steps):
            raise ProjectError(f"note start {self.start} out of range 0-{steps - 1}")
        if not isinstance(self.length, (int, float)) or not (0 < self.length <= 64):
            raise ProjectError(f"note length {self.length} out of range 0-64 steps")
        if not isinstance(self.pitch, int) or not (0 <= self.pitch <= 127):
            raise ProjectError(f"note pitch {self.pitch} out of MIDI range")
        if not isinstance(self.vel, (int, float)) or not (0.0 <= self.vel <= 1.0):
            raise ProjectError(f"note velocity {self.vel} out of range 0-1")
        if not isinstance(self.pan, (int, float)) or not (0.0 <= self.pan <= 1.0):
            raise ProjectError(f"note pan {self.pan} out of range 0-1")

    def to_dict(self) -> dict:
        return {"start": self.start, "len": self.length,
                "pitch": self.pitch, "vel": float(self.vel),
                "pan": float(self.pan)}

    @classmethod
    def from_dict(cls, data: dict) -> "Note":
        if not isinstance(data, dict):
            raise ProjectError("note entry must be an object")
        try:
            return cls(start=float(data["start"]), length=float(data["len"]),
                       pitch=int(data["pitch"]), vel=float(data.get("vel", 0.9)),
                       pan=float(data.get("pan", 0.5)))
        except KeyError as e:
            raise ProjectError(f"note missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"note has invalid field: {e}") from e


@dataclass
class Channel:
    id: str
    name: str
    instrument: str
    pitch: int  # MIDI note 0-127; default pitch for newly added notes
    notes: list[Note] = field(default_factory=list)

    def note_at_step(self, step: int) -> list[Note]:
        """Notes covering a step (for the rack's cell display)."""
        return [n for n in self.notes if n.start <= step < n.start + n.length]

    def covers(self, step: int) -> bool:
        return any(n.start <= step < n.start + n.length for n in self.notes)

    def validate(self, steps: int) -> None:
        if self.instrument not in INSTRUMENTS:
            raise ProjectError(f"channel '{self.id}': unknown instrument '{self.instrument}'")
        if not (0 <= self.pitch <= 127):
            raise ProjectError(f"channel '{self.id}': pitch {self.pitch} out of MIDI range")
        if len(self.notes) > 4096:
            raise ProjectError(f"channel '{self.id}': too many notes ({len(self.notes)})")
        for n in self.notes:
            if not isinstance(n, Note):
                raise ProjectError(f"channel '{self.id}': note entries must be notes")
            n.validate(steps)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "instrument": self.instrument,
            "pitch": self.pitch,
            "notes": [n.to_dict() for n in self.notes],
        }

    @classmethod
    def from_dict(cls, data: dict, pre_v3: bool = False) -> "Channel":
        """Parse a channel. Pre-v5 files (per-step arrays) become 1-step
        notes; pre_v3 additionally fills pitches from the channel pitch."""
        if not isinstance(data, dict):
            raise ProjectError("channel entry must be an object")
        try:
            pitch = int(data["pitch"])
            if "notes" in data:
                notes = [Note.from_dict(n) for n in data["notes"]]
            else:
                steps = [bool(s) for s in data["steps"]]
                if pre_v3 or "note_pitch" not in data:
                    note_pitch = [pitch] * len(steps)
                else:
                    note_pitch = [int(p) for p in data["note_pitch"]]
                if "note_vel" not in data:
                    note_vel = [0.9] * len(steps)
                else:
                    note_vel = [float(v) for v in data["note_vel"]]
                notes = [Note(start=i, length=1.0, pitch=note_pitch[i],
                              vel=note_vel[i])
                         for i, on in enumerate(steps) if on]
            return cls(
                id=str(data["id"]),
                name=str(data["name"]),
                instrument=str(data["instrument"]),
                pitch=pitch,
                notes=notes,
            )
        except KeyError as e:
            raise ProjectError(f"channel missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"channel has invalid field: {e}") from e


@dataclass
class Pattern:
    """A reusable musical phrase: a fixed channel set and step grid."""
    id: str
    name: str
    steps: int = 16
    channels: list[Channel] = field(default_factory=list)

    def bars(self) -> int:
        return max(1, -(-self.steps // 16))  # ceil(steps/16)

    def validate(self) -> None:
        if not (1 <= self.steps <= 256):
            raise ProjectError(f"pattern '{self.id}': step count {self.steps} out of range 1-256")
        if not self.channels:
            raise ProjectError(f"pattern '{self.id}' has no channels")
        seen = set()
        for ch in self.channels:
            if ch.id in seen:
                raise ProjectError(f"pattern '{self.id}': duplicate channel id '{ch.id}'")
            seen.add(ch.id)
            ch.validate(self.steps)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "steps": self.steps,
            "channels": [c.to_dict() for c in self.channels],
        }

    @classmethod
    def from_dict(cls, data: dict, pre_v3: bool = False) -> "Pattern":
        if not isinstance(data, dict):
            raise ProjectError("pattern entry must be an object")
        try:
            return cls(
                id=str(data["id"]),
                name=str(data.get("name", data["id"])),
                steps=int(data.get("steps", 16)),
                channels=[Channel.from_dict(c, pre_v3) for c in data["channels"]],
            )
        except KeyError as e:
            raise ProjectError(f"pattern missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"pattern has invalid field: {e}") from e


@dataclass
class SampleAsset:
    """One audio file registered with the project (FL's Audio Clip
    Channel / LMMS's SampleBuffer side of the model).

    Many `AudioClip` instances can reference one asset; the engine
    decodes the file once into an immutable, reference-counted
    `SampleBuffer` shared by all of them. Audio bytes are never stored
    in the project JSON -- only the path.

    `path` is stored project-relative when the file lives under the
    project directory (so the project folder stays portable), else
    absolute. Use :meth:`resolve` to get a usable filesystem path.
    """
    id: str
    path: str
    name: str = ""

    def validate(self) -> None:
        if not self.id:
            raise ProjectError("sample asset id must not be empty")
        if not self.path:
            raise ProjectError(f"sample '{self.id}': path must not be empty")

    def resolve(self, base_dir: str | None) -> str:
        """Absolute filesystem path for this asset."""
        if os.path.isabs(self.path):
            return self.path
        if base_dir:
            return os.path.normpath(os.path.join(base_dir, self.path))
        return os.path.abspath(self.path)

    def to_dict(self) -> dict:
        d = {"id": self.id, "path": self.path}
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "SampleAsset":
        if not isinstance(data, dict):
            raise ProjectError("sample entry must be an object")
        try:
            return cls(
                id=str(data["id"]),
                path=str(data["path"]),
                name=str(data.get("name", "")),
            )
        except KeyError as e:
            raise ProjectError(f"sample missing field {e}") from e


@dataclass
class AudioClip:
    """One timeline instance of a sample asset (FL's Clip Properties /
    LMMS's SampleClip side of the model).

    Per-instance properties -- the clip carries its own source window
    (`start_offset_beats`, `length_beats`) and playback properties, so
    "make unique" style edits never touch the shared asset or sibling
    clips. `pan` follows the FL note.pan convention: 0.0 left ..
    0.5 center .. 1.0 right (the bridge converts to engine -1..1).
    Pitch is a resample ratio, so it changes duration (LMMS Sample
    frequency behavior); true time-stretch is a later step.
    """
    id: str
    asset_id: str
    start_beat: float
    length_beats: float
    start_offset_beats: float = 0.0
    gain: float = 1.0
    pan: float = 0.5
    pitch_semitones: float = 0.0
    fine_cents: float = 0.0
    reverse: bool = False
    muted: bool = False

    def validate(self, asset_ids: set[str]) -> None:
        if self.asset_id not in asset_ids:
            raise ProjectError(
                f"audio clip '{self.id}' references unknown sample '{self.asset_id}'")
        if self.start_beat < 0:
            raise ProjectError("audio clip start_beat must be >= 0")
        if not (0.0 < self.length_beats <= 4096.0):
            raise ProjectError(
                f"audio clip length {self.length_beats} out of range 0-4096 beats")
        if self.start_offset_beats < 0:
            raise ProjectError("audio clip start_offset_beats must be >= 0")
        if not (0.0 <= self.gain <= 2.0):
            raise ProjectError(f"audio clip gain {self.gain} out of range 0-2")
        if not (0.0 <= self.pan <= 1.0):
            raise ProjectError(f"audio clip pan {self.pan} out of range 0-1")
        if not (-48.0 <= self.pitch_semitones <= 48.0):
            raise ProjectError(
                f"audio clip pitch {self.pitch_semitones} out of range -48..48")
        if not (-100.0 <= self.fine_cents <= 100.0):
            raise ProjectError(
                f"audio clip fine pitch {self.fine_cents} out of range -100..100")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "asset": self.asset_id,
            "start_beat": float(self.start_beat),
            "length_beats": float(self.length_beats),
            "start_offset_beats": float(self.start_offset_beats),
            "gain": float(self.gain),
            "pan": float(self.pan),
            "pitch_semitones": float(self.pitch_semitones),
            "fine_cents": float(self.fine_cents),
            "reverse": bool(self.reverse),
            "muted": bool(self.muted),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AudioClip":
        if not isinstance(data, dict):
            raise ProjectError("audio clip entry must be an object")
        try:
            return cls(
                id=str(data.get("id", "")),
                asset_id=str(data["asset"]),
                start_beat=float(data["start_beat"]),
                length_beats=float(data["length_beats"]),
                start_offset_beats=float(data.get("start_offset_beats", 0.0)),
                gain=float(data.get("gain", 1.0)),
                pan=float(data.get("pan", 0.5)),
                pitch_semitones=float(data.get("pitch_semitones", 0.0)),
                fine_cents=float(data.get("fine_cents", 0.0)),
                reverse=bool(data.get("reverse", False)),
                muted=bool(data.get("muted", False)),
            )
        except KeyError as e:
            raise ProjectError(f"audio clip missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"audio clip has invalid field: {e}") from e


@dataclass
class Clip:
    """Placement of a pattern on a track: repeats to fill `bars` bars.

    `start_beat` is the clip's start in beats from the arrangement start
    (v4; v3 and earlier stored whole bars in `start_bar`).
    """
    pattern_id: str
    start_beat: int
    bars: int

    def validate(self, pattern_ids: set[str]) -> None:
        if self.pattern_id not in pattern_ids:
            raise ProjectError(f"clip references unknown pattern '{self.pattern_id}'")
        if self.start_beat < 0:
            raise ProjectError("clip start_beat must be >= 0")
        if not (1 <= self.bars <= 256):
            raise ProjectError(f"clip length {self.bars} out of range 1-256 bars")

    def to_dict(self) -> dict:
        return {"pattern": self.pattern_id, "start_beat": self.start_beat, "bars": self.bars}

    @classmethod
    def from_dict(cls, data: dict) -> "Clip":
        if not isinstance(data, dict):
            raise ProjectError("clip entry must be an object")
        try:
            if "start_beat" in data:
                start_beat = int(data["start_beat"])
            else:
                # Legacy v3 and earlier: whole-bar starts.
                start_beat = int(data["start_bar"]) * 4
            return cls(
                pattern_id=str(data["pattern"]),
                start_beat=start_beat,
                bars=int(data["bars"]),
            )
        except KeyError as e:
            raise ProjectError(f"clip missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"clip has invalid field: {e}") from e


# Effect kinds with their parameter specs:
# kind -> [(param, label, min, max, unit, default)]
FX_DEFS: dict[str, list[tuple]] = {
    "delay": [
        ("time_ms", "Time", 10.0, 2000.0, "ms", 375.0),
        ("feedback", "Feedback", 0.0, 95.0, "%", 35.0),
        ("mix", "Mix", 0.0, 100.0, "%", 30.0),
    ],
    "drive": [
        ("amount", "Drive", 0.0, 100.0, "%", 40.0),
    ],
    "filter": [
        ("cutoff", "Cutoff", 200.0, 18000.0, "Hz", 8000.0),
    ],
    # Ducker: sidechain-driven gain reduction (native compressor-like
    # effect fed by the track's sidechain bus). Display units: threshold
    # as % (1-100 -> engine 0.01-1.0), ratio as x, times as ms.
    "ducker": [
        ("threshold", "Threshold", 1.0, 100.0, "%", 20.0),
        ("ratio", "Ratio", 1.0, 20.0, "x", 4.0),
        ("attack_ms", "Attack", 0.1, 100.0, "ms", 10.0),
        ("release_ms", "Release", 1.0, 1000.0, "ms", 200.0),
    ],
    # PitchShift: dual-head delay-line pitch shifter (duration
    # preserving). Display units: semitones as-is, mix as %
    # (engine: semitones -12..12, mix 0..1). See the time-stretching
    # research notes: only pitch is offered on the live insert path;
    # true time-stretch belongs at a future sample-clip layer.
    "pitchshift": [
        ("semitones", "Pitch", -12.0, 12.0, "st", 0.0),
        ("pitch_mix", "Mix", 0.0, 100.0, "%", 100.0),
    ],
}

FX_NAMES = {
    "delay": "Delay", "drive": "Drive", "filter": "Filter",
    "ducker": "Ducker", "pitchshift": "Pitch Shift",
}


@dataclass
class Effect:
    """One insert effect on a track. Params are stored in display units
    (%, ms, Hz); the bridge converts to engine units.

    Plugin effects (kind == "plugin") carry `plugin_id`/`plugin_path`
    and store CLAP parameter values in `params` keyed by str(param id).
    They may also carry `state_base64`: the plugin's opaque CLAP state
    blob, Base64-encoded for the JSON project. The blob is authoritative
    when the plugin accepts it; `params` remain as fallback/inspection.
    """
    kind: str
    params: dict = field(default_factory=dict)
    plugin_id: str = ""
    plugin_path: str = ""
    state_base64: str = ""

    @classmethod
    def default(cls, kind: str) -> "Effect":
        if kind == "plugin":
            raise ProjectError(
                "plugin effects are created from the plugin picker, "
                "not from defaults"
            )
        if kind not in FX_DEFS:
            raise ProjectError(f"unknown effect '{kind}'")
        return cls(kind=kind,
                   params={p: d for p, _l, _a, _b, _u, d in FX_DEFS[kind]})

    @classmethod
    def plugin(cls, plugin_id: str, plugin_path: str,
               params: dict) -> "Effect":
        """Create a plugin effect with CLAP param values.

        `params` maps CLAP param id (int) -> value; missing params fall
        back to the plugin defaults at load time.
        """
        return cls(kind="plugin",
                   params={str(k): float(v) for k, v in params.items()},
                   plugin_id=plugin_id, plugin_path=plugin_path)

    @classmethod
    def vst3(cls, plugin_id: str, plugin_path: str,
             params: dict) -> "Effect":
        """Create a VST3 plugin effect.

        `params` maps VST3 param id (int) -> normalized value 0.0-1.0;
        missing params fall back to the plugin defaults at load time.
        """
        return cls(kind="vst3",
                   params={str(k): float(v) for k, v in params.items()},
                   plugin_id=plugin_id, plugin_path=plugin_path)

    def validate(self) -> None:
        if self.kind == "plugin":
            if not self.plugin_id:
                raise ProjectError("plugin effect has no plugin id")
            if not self.plugin_path:
                raise ProjectError("plugin effect has no library path")
            for k, v in self.params.items():
                try:
                    int(k)
                    fv = float(v)
                except (TypeError, ValueError):
                    raise ProjectError(
                        f"plugin effect: param '{k}' has invalid id/value"
                    ) from None
                if fv != fv:  # NaN
                    raise ProjectError(
                        f"plugin effect: param '{k}' is not a number")
            _validate_state_base64(self.state_base64, "plugin effect")
            return
        if self.kind == "vst3":
            if not self.plugin_id:
                raise ProjectError("VST3 effect has no plugin id")
            if not self.plugin_path:
                raise ProjectError("VST3 effect has no library path")
            for k, v in self.params.items():
                try:
                    int(k)
                    fv = float(v)
                except (TypeError, ValueError):
                    raise ProjectError(
                        f"VST3 effect: param '{k}' has invalid id/value"
                    ) from None
                if fv != fv:  # NaN
                    raise ProjectError(
                        f"VST3 effect: param '{k}' is not a number")
                if not (0.0 <= fv <= 1.0):
                    raise ProjectError(
                        f"VST3 effect: param '{k}' out of normalized range 0.0-1.0")
            return
        spec = FX_DEFS.get(self.kind)
        if spec is None:
            raise ProjectError(f"unknown effect '{self.kind}'")
        for param, _label, lo, hi, _unit, _default in spec:
            try:
                v = float(self.params[param])
            except (KeyError, TypeError, ValueError):
                raise ProjectError(
                    f"effect '{self.kind}': param '{param}' missing or invalid") from None
            if not (lo <= v <= hi):
                raise ProjectError(
                    f"effect '{self.kind}': {param}={v} out of range {lo}-{hi}")

    def engine_params(self) -> dict:
        """Convert display units to engine units (fractions, ms, Hz)."""
        if self.kind == "plugin":
            return {"type": "plugin",
                    "plugin_id": self.plugin_id,
                    "plugin_path": self.plugin_path,
                    "params": {k: float(v)
                               for k, v in self.params.items()},
                    "state_base64": self.state_base64}
        if self.kind == "vst3":
            return {"type": "vst3",
                    "plugin_id": self.plugin_id,
                    "plugin_path": self.plugin_path,
                    "params": {k: float(v)
                               for k, v in self.params.items()}}
        out = {"type": self.kind}
        for param, _label, _lo, _hi, unit, _default in FX_DEFS[self.kind]:
            v = float(self.params[param])
            out[param] = v / 100.0 if unit == "%" else v
        return out

    def to_dict(self) -> dict:
        d = {"type": self.kind,
             "params": {k: float(v) for k, v in self.params.items()}}
        if self.kind in ("plugin", "vst3"):
            d["plugin_id"] = self.plugin_id
            d["plugin_path"] = self.plugin_path
            if self.state_base64:
                d["state_base64"] = self.state_base64
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Effect":
        if not isinstance(data, dict):
            raise ProjectError("effect entry must be an object")
        try:
            kind = str(data["type"])
            params = {str(k): float(v) for k, v in data.get("params", {}).items()}
            plugin_id = str(data.get("plugin_id", ""))
            plugin_path = str(data.get("plugin_path", ""))
            state_base64 = str(data.get("state_base64", ""))
            return cls(kind=kind, params=params,
                       plugin_id=plugin_id, plugin_path=plugin_path,
                       state_base64=state_base64)
        except (TypeError, ValueError) as e:
            raise ProjectError(f"effect has invalid field: {e}") from e

    def display_name(self) -> str:
        """Short name for mixer lists: 'Delay', or the plugin name."""
        if self.kind in ("plugin", "vst3"):
            return _plugin_display_names.get(self.plugin_id,
                                             f"Plugin ({self.plugin_id})")
        return FX_NAMES.get(self.kind, self.kind)


        return FX_NAMES.get(self.kind, self.kind)


@dataclass
class Generator:
    """A track's sound source. None (on the track) = built-in voices.

    Currently only plugin instruments (CLAP) are supported as generators.
    `params` maps str(CLAP param id) -> value in the plugin's real units.
    `state_base64` is the plugin's opaque CLAP state blob, Base64-encoded
    for the JSON project (authoritative when the plugin accepts it).
    """

    plugin_id: str = ""
    plugin_path: str = ""
    params: dict = field(default_factory=dict)
    state_base64: str = ""

    @classmethod
    def plugin(cls, plugin_id: str, plugin_path: str,
               params: dict) -> "Generator":
        """Create a plugin generator with CLAP param values."""
        return cls(plugin_id=plugin_id, plugin_path=plugin_path,
                   params={str(int(k)): float(v)
                           for k, v in params.items()})

    def validate(self) -> None:
        if not self.plugin_id:
            raise ProjectError("generator: missing plugin_id")
        if not self.plugin_path:
            raise ProjectError("generator: missing plugin_path")
        for k, v in self.params.items():
            try:
                int(k)
                float(v)
            except (TypeError, ValueError):
                raise ProjectError(
                    f"generator: bad param {k}={v}") from None
        _validate_state_base64(self.state_base64, "generator")

    def display_name(self) -> str:
        """Short name for mixer lists."""
        return _plugin_display_names.get(self.plugin_id,
                                         f"Plugin ({self.plugin_id})")

    def engine_params(self) -> dict:
        """Convert to the engine's generator dict."""
        return {"type": "plugin",
                "plugin_id": self.plugin_id,
                "plugin_path": self.plugin_path,
                "params": {k: float(v) for k, v in self.params.items()},
                "state_base64": self.state_base64}

    def to_dict(self) -> dict:
        d = {"type": "plugin",
             "plugin_id": self.plugin_id,
             "plugin_path": self.plugin_path,
             "params": {k: float(v) for k, v in self.params.items()}}
        if self.state_base64:
            d["state_base64"] = self.state_base64
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Generator":
        if not isinstance(data, dict):
            raise ProjectError("generator entry must be an object")
        if data.get("type", "plugin") != "plugin":
            raise ProjectError(
                f"unsupported generator type '{data.get('type')}'")
        try:
            gen = cls(plugin_id=str(data["plugin_id"]),
                      plugin_path=str(data["plugin_path"]),
                      params={str(k): float(v)
                              for k, v in data.get("params", {}).items()},
                      state_base64=str(data.get("state_base64", "")))
        except KeyError as e:
            raise ProjectError(f"generator missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"generator has invalid field: {e}") from e
        gen.validate()
        return gen


@dataclass
class GeneratorLayer:
    """One generator in a track's layer stack (FL Layer-style).

    A track can host multiple layers; note events fan out to layers
    according to the track's layer_mode ('all', 'random', 'sequential').
    """
    generator: Generator
    gain: float = 1.0
    pitch_offset: int = 0  # semitones
    enabled: bool = True

    def validate(self) -> None:
        self.generator.validate()
        if not (0.0 <= self.gain <= 2.0):
            raise ProjectError(f"layer: gain {self.gain} out of range 0-2")
        if not (-48 <= self.pitch_offset <= 48):
            raise ProjectError(
                f"layer: pitch_offset {self.pitch_offset} out of range -48..48")

    def engine_params(self) -> dict:
        """Convert to the engine's layer dict."""
        d = self.generator.engine_params()
        d.update({"gain": float(self.gain),
                  "pitch_offset": int(self.pitch_offset),
                  "enabled": bool(self.enabled)})
        return d

    def to_dict(self) -> dict:
        d = self.generator.to_dict()
        d.update({"gain": float(self.gain),
                  "pitch_offset": int(self.pitch_offset),
                  "enabled": bool(self.enabled)})
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "GeneratorLayer":
        gen = Generator.from_dict(data)
        try:
            layer = cls(
                generator=gen,
                gain=float(data.get("gain", 1.0)),
                pitch_offset=int(data.get("pitch_offset", 0)),
                enabled=bool(data.get("enabled", True)),
            )
        except (TypeError, ValueError) as e:
            raise ProjectError(f"layer has invalid field: {e}") from e
        layer.validate()
        return layer


# Registry of known plugin parameters, populated by the UI from the
# engine's scan: plugin_id -> {clap_id: (name, min, max)}. Used to
# validate and label plugin automation lanes.
_plugin_params: dict[str, dict[int, tuple[str, float, float]]] = {}
# plugin_id -> display name (from the last scan).
_plugin_display_names: dict[str, str] = {}


def register_plugin(plugin_id: str, display_name: str,
                    params: list[dict]) -> None:
    """Register a plugin's parameters for validation and automation."""
    _plugin_display_names[plugin_id] = display_name
    _plugin_params[plugin_id] = {
        int(p["id"]): (str(p["name"]), float(p["min"]), float(p["max"]))
        for p in params
    }


def plugin_param_spec(plugin_id: str,
                      clap_id: int) -> tuple[str, float, float]:
    """(name, min, max) for one CLAP parameter of a known plugin."""
    try:
        return _plugin_params[plugin_id][clap_id]
    except KeyError:
        raise ProjectError(
            f"plugin '{plugin_id}': unknown parameter {clap_id} "
            "(rescan plugins to refresh)"
        ) from None


# Automatable track parameters: id -> (label, lo, hi, unit) in display
# units (matching the mixer/effect dialogs). Effect params reuse FX_DEFS.
TRACK_AUTO_PARAMS: dict[str, tuple[str, float, float, str]] = {
    "gain": ("Gain", 0.0, 2.0, ""),
    "pan": ("Pan", -1.0, 1.0, ""),
}

# Automation curve interpolation modes (research topic 30): id -> label.
# Per-lane, like LMMS's per-clip progression. The engine's InterpMode
# must stay in sync with these names.
CURVE_INTERPS: dict[str, str] = {
    "linear": "Linear",
    "smooth": "Smooth",
    "hold": "Hold",
    "stairs": "Stairs",
    "pulse": "Pulse",
    "wave": "Wave",
}


def parse_auto_param(param: str) -> tuple[str, int, str]:
    """Split an automation param id into (kind, fx_index, name).

    kind is "track" (fx_index -1) for "gain"/"pan", "fx" for
    "fx{i}.{name}", or "gen" (fx_index -1) for "gen.p{id}".
    """
    if param in TRACK_AUTO_PARAMS:
        return ("track", -1, param)
    if param.startswith("gen."):
        name = param[4:]
        if name.startswith("p"):
            try:
                int(name[1:])
                return ("gen", -1, name)
            except ValueError:
                pass
        raise ProjectError(f"unknown automation param '{param}'")
    if param.startswith("send."):
        # Send amount automation: "send.<dest_track_id>.amount".
        # The bridge translates the destination track id to the engine's
        # track index before pushing the arrangement.
        try:
            dest, name = param[5:].rsplit(".", 1)
            if name == "amount" and dest:
                return ("send", -1, dest)
        except ValueError:
            pass
        raise ProjectError(f"unknown automation param '{param}'")
    if param.startswith("fx"):
        try:
            idx_s, name = param[2:].split(".", 1)
            return ("fx", int(idx_s), name)
        except ValueError:
            pass
    raise ProjectError(f"unknown automation param '{param}'")


def auto_param_spec(param: str, track: "PlaylistTrack") -> tuple[str, float, float, str]:
    """(label, lo, hi, unit) for an automation param id on a track."""
    kind, idx, name = parse_auto_param(param)
    if kind == "track":
        return TRACK_AUTO_PARAMS[name]
    if kind == "send":
        # name is the destination track id (parse_auto_param returns
        # ("send", -1, dest_id)). Range 0-1: the edge gain.
        return (f"Send -> {name}", 0.0, 1.0, "")
    if kind == "gen":
        gen = (track.generator_layers[0].generator
               if track.generator_layers else None)
        if gen is None:
            raise ProjectError(
                f"automation param '{param}': track has no generator"
            )
        clap_id = int(name[1:])
        if gen.plugin_id in _plugin_params:
            pname, lo, hi = plugin_param_spec(gen.plugin_id, clap_id)
            return (f"{gen.display_name()} {pname}", lo, hi, "")
        return (f"{gen.display_name()} param {clap_id}",
                float("-inf"), float("inf"), "")
    try:
        fx = track.effects[idx]
    except IndexError:
        raise ProjectError(
            f"automation param '{param}': track has no effect {idx}"
        ) from None
    if fx.kind == "plugin":
        # Plugin params are "p{clap_id}".
        if not name.startswith("p"):
            raise ProjectError(
                f"automation param '{param}': plugin params look like 'p123'"
            )
        try:
            clap_id = int(name[1:])
        except ValueError:
            raise ProjectError(
                f"automation param '{param}': bad plugin param id"
            ) from None
        if fx.plugin_id in _plugin_params:
            pname, lo, hi = plugin_param_spec(fx.plugin_id, clap_id)
            return (f"{fx.display_name()} {pname}", lo, hi, "")
        # Not scanned yet (e.g. project just loaded): accept the lane,
        # range unknown. The editor registers the plugin on open.
        return (f"{fx.display_name()} param {clap_id}",
                float("-inf"), float("inf"), "")
    for p, label, lo, hi, unit, _default in FX_DEFS[fx.kind]:
        if p == name:
            return (f"{FX_NAMES[fx.kind]} {label}", lo, hi, unit)
    raise ProjectError(
        f"automation param '{param}': {fx.kind} has no '{name}'"
    )


@dataclass
class AutoPoint:
    """One automation control point: `beat` (arrangement beats), `value`
    in the param's display units.

    `in_tan` / `out_tan` are per-node spline tangents in value-per-beat
    (research topic 36; LMMS's m_inTangent/m_outTangent analog). None
    means AUTO -- the evaluator derives the tangent from neighboring
    points (Catmull-Rom, scaled by the lane tension). A float means
    LOCKED -- the user-defined tangent is used verbatim and never
    recalculated when neighbors move. Tangents only shape the "smooth"
    (cubic Hermite) evaluator; other interp modes ignore them.
    """
    beat: float
    value: float
    in_tan: float | None = None
    out_tan: float | None = None

    def to_dict(self) -> dict:
        return {"beat": self.beat, "value": float(self.value),
                "in_tan": self.in_tan, "out_tan": self.out_tan}

    @classmethod
    def from_dict(cls, data: dict) -> "AutoPoint":
        if not isinstance(data, dict):
            raise ProjectError("automation point must be an object")
        try:
            in_tan = data.get("in_tan")
            out_tan = data.get("out_tan")
            return cls(beat=float(data["beat"]), value=float(data["value"]),
                       in_tan=None if in_tan is None else float(in_tan),
                       out_tan=None if out_tan is None else float(out_tan))
        except KeyError as e:
            raise ProjectError(f"automation point missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"automation point has invalid field: {e}") from e


LFO_SHAPES = ("sine", "triangle", "saw", "pulse")
LFO_COMBINES = ("add", "multiply")


@dataclass
class LfoSettings:
    """Persistent LFO modulation layer on an automation lane (research
    topic 36; FL Studio Automation Clip LFO analog).

    The base spline is NEVER modified by the LFO: the evaluator computes
    base(t) from the points, then applies the LFO as a separate layer:

        add:      final = base + level * wave(phase)
        multiply: final = base * (1 + level * wave(phase))

    `speed` is cycles per arrangement beat (phase = (beat*speed) % 1 --
    deterministic, so realtime and offline render agree). `shape` is one
    of "sine" | "triangle" | "saw" | "pulse"; `skew` (-1..1) morphs the
    triangle toward saw/reverse-saw; `pulse_width` (0..1) is the pulse
    duty cycle; `level` scales the wave and may be negative (inverts).
    """
    enabled: bool = False
    speed: float = 1.0
    shape: str = "sine"
    skew: float = 0.0
    pulse_width: float = 0.5
    level: float = 1.0
    combine: str = "add"

    def validate(self, lane_id: str) -> None:
        if self.shape not in LFO_SHAPES:
            raise ProjectError(
                f"automation lane '{lane_id}': unknown LFO shape '{self.shape}'")
        if self.combine not in LFO_COMBINES:
            raise ProjectError(
                f"automation lane '{lane_id}': unknown LFO combine '{self.combine}'")
        if not (self.speed > 0.0):
            raise ProjectError(
                f"automation lane '{lane_id}': LFO speed must be positive")
        if not (-1.0 <= self.skew <= 1.0):
            raise ProjectError(
                f"automation lane '{lane_id}': LFO skew {self.skew} out of range -1..1")
        if not (0.0 <= self.pulse_width <= 1.0):
            raise ProjectError(
                f"automation lane '{lane_id}': LFO pulse width {self.pulse_width} "
                f"out of range 0..1")

    def to_dict(self) -> dict:
        return {"enabled": bool(self.enabled), "speed": float(self.speed),
                "shape": self.shape, "skew": float(self.skew),
                "pulse_width": float(self.pulse_width),
                "level": float(self.level), "combine": self.combine}

    @classmethod
    def from_dict(cls, data: dict) -> "LfoSettings":
        if not isinstance(data, dict):
            raise ProjectError("LFO settings must be an object")
        try:
            return cls(
                enabled=bool(data.get("enabled", False)),
                speed=float(data.get("speed", 1.0)),
                shape=str(data.get("shape", "sine")),
                skew=float(data.get("skew", 0.0)),
                pulse_width=float(data.get("pulse_width", 0.5)),
                level=float(data.get("level", 1.0)),
                combine=str(data.get("combine", "add")),
            )
        except (TypeError, ValueError) as e:
            raise ProjectError(f"LFO settings have invalid field: {e}") from e


@dataclass
class AutomationLane:
    """A parameter envelope on a track: sorted control points over
    arrangement beats.

    `interp` selects the curve evaluator for the lane (research topic 30):
    "linear" (piecewise linear), "smooth" (cubic Hermite with Catmull-Rom
    tangents scaled by tension), "hold" (step: last point's value),
    "stairs" (quantized steps; tension sets the count 2..16), "pulse"
    (square-wave alternation; tension sets cycles 1..8), "wave" (sine
    wobble around the ramp; tension sets cycles 1..8). Per-lane, like
    LMMS's per-clip progression -- not per-segment like FL's curve types.

    `tension` (0..1, default 0.5) shapes the smooth/stairs/pulse/wave
    modes; linear and hold ignore it.

    Before the first point the static value holds, after the last the
    last value holds (all modes).
    """
    id: str
    track_id: str
    param: str  # "gain", "pan", or "fx{i}.{name}"
    points: list[AutoPoint] = field(default_factory=list)
    interp: str = "linear"
    tension: float = 0.5
    lfo: LfoSettings | None = None

    def validate(self, track: "PlaylistTrack", max_beat: float) -> None:
        _label, lo, hi, _unit = auto_param_spec(self.param, track)
        kind, _idx, name = parse_auto_param(self.param)
        if kind == "send":
            # The route must exist; automation modulates edge gain, never
            # topology (the FL/LMMS shared invariant).
            if not any(s.to_track_id == name for s in track.sends):
                raise ProjectError(
                    f"automation lane '{self.id}': track has no send to '{name}'"
                )
        if self.interp not in CURVE_INTERPS:
            raise ProjectError(
                f"automation lane '{self.id}': unknown interp '{self.interp}'")
        if not (0.0 <= self.tension <= 1.0):
            raise ProjectError(
                f"automation lane '{self.id}': tension {self.tension} "
                f"out of range 0..1")
        if self.lfo is not None:
            if not isinstance(self.lfo, LfoSettings):
                raise ProjectError(
                    f"automation lane '{self.id}': lfo must be LFO settings")
            self.lfo.validate(self.id)
        if not self.points:
            raise ProjectError(f"automation lane '{self.id}' has no points")
        if len(self.points) > 512:
            raise ProjectError(
                f"automation lane '{self.id}' has too many points"
            )
        prev = -1.0
        for pt in self.points:
            if not isinstance(pt, AutoPoint):
                raise ProjectError(
                    f"automation lane '{self.id}': points must be automation points"
                )
            if not (0.0 <= pt.beat < max_beat):
                raise ProjectError(
                    f"automation lane '{self.id}': beat {pt.beat} outside arrangement"
                )
            if pt.beat < prev:
                raise ProjectError(
                    f"automation lane '{self.id}': points must be sorted by beat"
                )
            prev = pt.beat
            if not (lo <= pt.value <= hi):
                raise ProjectError(
                    f"automation lane '{self.id}': value {pt.value} out of range"
                )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "track": self.track_id,
            "param": self.param,
            "points": [p.to_dict() for p in self.points],
            "interp": self.interp,
            "tension": float(self.tension),
            "lfo": self.lfo.to_dict() if self.lfo is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AutomationLane":
        if not isinstance(data, dict):
            raise ProjectError("automation lane must be an object")
        try:
            points = [AutoPoint.from_dict(p) for p in data.get("points", [])]
            points.sort(key=lambda p: p.beat)
            lfo_data = data.get("lfo")
            return cls(
                id=str(data["id"]),
                track_id=str(data["track"]),
                param=str(data["param"]),
                points=points,
                interp=str(data.get("interp", "linear")),
                tension=float(data.get("tension", 0.5)),
                lfo=(LfoSettings.from_dict(lfo_data)
                     if lfo_data is not None else None),
            )
        except KeyError as e:
            raise ProjectError(f"automation lane missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"automation lane has invalid field: {e}") from e


@dataclass
class Send:
    """An inter-track send from this track to another (FL Mixer routing).

    amount: 0.0-1.0 linear edge gain (automatable).
    tap: "pre" reads the source's signal before its gain/pan fader
         (FL pre-fader send); "post" reads the post-FX output.
    pan: independent -1..1 stereo position of the routed copy.
    sidechain: when True the send feeds the destination's sidechain bus
         only (FL "sidechain to this track") -- it is never audible.
    The destination mixes the routed copy into its own buffer BEFORE its
    FX chain. Any track can be a bus; cycles are rejected on creation.
    """
    to_track_id: str
    amount: float = 0.5
    tap: str = "post"
    pan: float = 0.0
    sidechain: bool = False

    def validate(self, track_ids: set[str], from_id: str) -> None:
        if self.to_track_id == from_id:
            raise ProjectError(f"send: track cannot send to itself")
        if self.to_track_id not in track_ids:
            raise ProjectError(f"send: unknown destination '{self.to_track_id}'")
        if not (0.0 <= self.amount <= 1.0):
            raise ProjectError(f"send: amount {self.amount} out of range 0-1")
        if self.tap not in ("pre", "post"):
            raise ProjectError(f"send: tap must be 'pre' or 'post'")
        if not (-1.0 <= self.pan <= 1.0):
            raise ProjectError(f"send: pan {self.pan} out of range -1..1")
        if not isinstance(self.sidechain, bool):
            raise ProjectError(f"send: sidechain must be a bool")

    def to_dict(self) -> dict:
        return {
            "to": self.to_track_id,
            "amount": float(self.amount),
            "tap": self.tap,
            "pan": float(self.pan),
            "sidechain": bool(self.sidechain),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Send":
        tap = d.get("tap", "post")
        if tap not in ("pre", "post"):
            raise ProjectError(f"send: unknown tap '{tap}'")
        try:
            return cls(
                to_track_id=d["to"],
                amount=float(d.get("amount", 0.5)),
                tap=tap,
                pan=float(d.get("pan", 0.0)),
                sidechain=bool(d.get("sidechain", False)),
            )
        except KeyError as e:
            raise ProjectError(f"send missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"send has invalid field: {e}") from e


@dataclass
class PlaylistTrack:
    """One playlist lane. gain/pan/muted/effects are engine-real parameters.

    `output` is the track's exclusive output route (FL "route to this track
    only"): a track id, or "master" (default). The track's post-FX output is
    mixed into the destination track's buffer BEFORE its FX chain, and the
    track does NOT reach the Master directly. Parallel `sends` are separate
    (they keep their own level and do not affect the output route).

    `vel_track` / `vel_track_mid` are the FL Studio-style velocity tracker
    (3xOsc Volume Tracking model) for the track's built-in voices: bipolar
    amount (-1..1, 0 = off) and the middle velocity (0..1) where no offset
    is generated. Evaluated per note at trigger time (note domain): each
    note's velocity offsets its voice's lowpass cutoff. CLAP instruments
    receive note velocity directly and do their own mapping (FL's
    generator-level velocity path).

    `key_track` / `key_track_mid` are the FL Studio-style keyboard tracker
    (Channel Keyboard Tracker model): bipolar amount (-1..1, 0 = off) and
    the middle MIDI note (0..127, default 60 = C4) where no offset is
    generated. +1.0 is 100% tracking: one octave of pitch moves the voice
    lowpass cutoff one octave. Independent from velocity tracking; both
    sum in the cutoff exponent. CLAP instruments receive note pitch
    directly and do their own mapping.
    """
    id: str
    name: str
    # Presentation identity (research topic 33): persistent per-track
    # name/color/icon shown on every surface (playlist header, mixer
    # strip). The audio model (gain/pan/mute/FX/routing) never reads these;
    # the engine never receives them.
    color_idx: int = 0  # index into track_identity.TRACK_COLORS
    icon: str = ""      # "" or a key from track_identity.TRACK_ICONS
    clips: list[Clip] = field(default_factory=list)
    audio_clips: list[AudioClip] = field(default_factory=list)
    gain: float = 1.0
    pan: float = 0.0
    muted: bool = False
    vel_track: float = 0.0
    vel_track_mid: float = 0.5
    key_track: float = 0.0
    key_track_mid: float = 60.0
    effects: list[Effect] = field(default_factory=list)
    generator_layers: list[GeneratorLayer] = field(default_factory=list)
    layer_mode: str = "all"  # "all" | "random" | "sequential"
    sends: list[Send] = field(default_factory=list)
    output: str = "master"  # track id or "master"

    @property
    def identity(self) -> TrackIdentity:
        """The strip-level presentation object for this track (view).

        Pure data snapshot: name/color/icon as one object, the way FL
        surfaces and LMMS's TrackView treat track presentation identity.
        """
        return TrackIdentity(name=self.name, color_idx=self.color_idx,
                             icon=self.icon)

    def apply_identity(self, identity: TrackIdentity) -> None:
        """Write-through copy of an identity (used for link-group ripple)."""
        self.name = identity.name
        self.color_idx = identity.color_idx
        self.icon = identity.icon

    def validate(self, pattern_ids: set[str], asset_ids: set[str]) -> None:
        self.identity.validate(self.id)
        if not (0.0 <= self.gain <= 2.0):
            raise ProjectError(f"track '{self.id}': gain {self.gain} out of range 0-2")
        if not (-1.0 <= self.pan <= 1.0):
            raise ProjectError(f"track '{self.id}': pan {self.pan} out of range -1..1")
        if not (-1.0 <= self.vel_track <= 1.0):
            raise ProjectError(
                f"track '{self.id}': vel_track {self.vel_track} out of range -1..1")
        if not (0.0 <= self.vel_track_mid <= 1.0):
            raise ProjectError(
                f"track '{self.id}': vel_track_mid {self.vel_track_mid} "
                f"out of range 0..1")
        if not (-1.0 <= self.key_track <= 1.0):
            raise ProjectError(
                f"track '{self.id}': key_track {self.key_track} out of range -1..1")
        if not (0.0 <= self.key_track_mid <= 127.0):
            raise ProjectError(
                f"track '{self.id}': key_track_mid {self.key_track_mid} "
                f"out of range 0..127")
        if self.layer_mode not in ("all", "random", "sequential"):
            raise ProjectError(f"track '{self.id}': bad layer_mode '{self.layer_mode}'")
        for clip in self.clips:
            clip.validate(pattern_ids)
        for acl in self.audio_clips:
            acl.validate(asset_ids)
        for fx in self.effects:
            fx.validate()
        for layer in self.generator_layers:
            layer.validate()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color_idx": self.color_idx,
            "icon": self.icon,
            "gain": self.gain,
            "pan": self.pan,
            "muted": self.muted,
            "vel_track": float(self.vel_track),
            "vel_track_mid": float(self.vel_track_mid),
            "key_track": float(self.key_track),
            "key_track_mid": float(self.key_track_mid),
            "effects": [e.to_dict() for e in self.effects],
            "generator_layers": [l.to_dict() for l in self.generator_layers],
            "layer_mode": self.layer_mode,
            "clips": [c.to_dict() for c in self.clips],
            "audio_clips": [c.to_dict() for c in self.audio_clips],
            "sends": [s.to_dict() for s in self.sends],
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PlaylistTrack":
        if not isinstance(data, dict):
            raise ProjectError("playlist track entry must be an object")
        try:
            # New style: generator_layers list; legacy: single generator dict.
            layers = []
            if "generator_layers" in data:
                for ldata in data["generator_layers"]:
                    layers.append(GeneratorLayer.from_dict(ldata))
            elif data.get("generator") is not None:
                layers.append(GeneratorLayer.from_dict(data["generator"]))
            return cls(
                id=str(data["id"]),
                name=str(data.get("name", data["id"])),
                color_idx=int(data.get("color_idx", 0)),
                icon=str(data.get("icon", "")),
                clips=[Clip.from_dict(c) for c in data.get("clips", [])],
                audio_clips=[AudioClip.from_dict(c)
                             for c in data.get("audio_clips", [])],
                gain=float(data.get("gain", 1.0)),
                pan=float(data.get("pan", 0.0)),
                muted=bool(data.get("muted", False)),
                vel_track=float(data.get("vel_track", 0.0)),
                vel_track_mid=float(data.get("vel_track_mid", 0.5)),
                key_track=float(data.get("key_track", 0.0)),
                key_track_mid=float(data.get("key_track_mid", 60.0)),
                effects=[Effect.from_dict(e) for e in data.get("effects", [])],
                generator_layers=layers,
                layer_mode=str(data.get("layer_mode", "all")),
                sends=[Send.from_dict(s) for s in data.get("sends", [])],
                output=str(data.get("output", "master")),
            )
        except KeyError as e:
            raise ProjectError(f"playlist track missing field {e}") from e
        except (TypeError, ValueError) as e:
            raise ProjectError(f"playlist track has invalid field: {e}") from e

    def validate_sends(self, track_ids: set[str]) -> None:
        """Validate sends and the exclusive output route."""
        for s in self.sends:
            s.validate(track_ids, self.id)
        # Output route: "master", another existing track, never self.
        if self.output != "master":
            if self.output == self.id:
                raise ProjectError(
                    f"track '{self.id}': output cannot route to itself")
            if self.output not in track_ids:
                raise ProjectError(
                    f"track '{self.id}': unknown output "
                    f"destination '{self.output}'")
        # Cycle detection (LMMS isInfiniteLoop pattern): follow sends
        # from this track; if we return to self, it's a cycle.
        # (Full graph check happens at Project.validate.)


@dataclass
class Project:
    name: str
    tempo: float
    patterns: list[Pattern] = field(default_factory=list)
    tracks: list[PlaylistTrack] = field(default_factory=list)
    samples: list[SampleAsset] = field(default_factory=list)
    selected_pattern: str = ""
    automation: list[AutomationLane] = field(default_factory=list)
    # When True, notes are cut at clip boundaries instead of ringing past
    # them (FL Studio's "Play truncated notes in clips" option).
    truncate_notes: bool = False
    # Identity link groups (FL Track Mode analog, research topic 33):
    # each group is a list of track ids whose presentation identity
    # (name/color/icon) is shared -- an edit to one member ripples to all.
    identity_groups: list[list[str]] = field(default_factory=list)

    # -- pattern helpers ------------------------------------------------

    def pattern_by_id(self, pid: str) -> Pattern:
        for p in self.patterns:
            if p.id == pid:
                return p
        raise ProjectError(f"unknown pattern '{pid}'")

    def current_pattern(self) -> Pattern:
        return self.pattern_by_id(self.selected_pattern)

    def new_pattern_id(self) -> str:
        return _next_id("pattern", [p.id for p in self.patterns])

    def new_track_id(self) -> str:
        return _next_id("track", [t.id for t in self.tracks])

    def new_automation_id(self) -> str:
        return _next_id("auto", [a.id for a in self.automation])

    def new_sample_id(self) -> str:
        return _next_id("sample", [s.id for s in self.samples])

    def new_audio_clip_id(self) -> str:
        ids = [c.id for t in self.tracks for c in t.audio_clips if c.id]
        return _next_id("aclip", ids)

    # -- identity linking (FL Track Mode analog) -------------------------

    def track_by_id(self, tid: str) -> "PlaylistTrack":
        for t in self.tracks:
            if t.id == tid:
                return t
        raise ProjectError(f"unknown track '{tid}'")

    def linked_group(self, track_id: str) -> list[str]:
        """Track ids sharing presentation identity with track_id."""
        return linked_members(self.identity_groups, track_id)

    def set_track_identity(self, track_id: str, *,
                           name: str | None = None,
                           color_idx: int | None = None,
                           icon: str | None = None) -> list[str]:
        """Set identity fields on a track; ripples to its link group.

        Returns the ids that were updated (the whole group).
        """
        members = self.linked_group(track_id)
        if name is not None and not name.strip():
            raise ProjectError("track name must not be empty")
        if color_idx is not None and not (
                0 <= color_idx < len(TRACK_COLORS)):
            raise ProjectError(f"color_idx {color_idx} out of range")
        if icon is not None and icon not in TRACK_ICONS:
            raise ProjectError(f"unknown icon {icon!r}")
        for tid in members:
            t = self.track_by_id(tid)
            if name is not None:
                t.name = name.strip()
            if color_idx is not None:
                t.color_idx = color_idx
            if icon is not None:
                t.icon = icon
        return members

    def link_track_identities(self, track_ids: list[str]) -> list[str]:
        """Link tracks so they share presentation identity (FL Track Mode).

        The first track's identity wins; it is copied to the others.
        Returns the final group.
        """
        ids = [tid for tid in dict.fromkeys(track_ids)
               if any(t.id == tid for t in self.tracks)]
        if len(ids) < 2:
            raise ProjectError("linking needs at least two tracks")
        # Merge with any existing groups these tracks belong to.
        merged: list[str] = []
        for tid in ids:
            if tid not in merged:
                merged.append(tid)
        remaining = [g for g in self.identity_groups
                     if not any(tid in g for tid in merged)]
        for g in self.identity_groups:
            if any(tid in g for tid in merged):
                for tid in g:
                    if tid not in merged:
                        merged.append(tid)
        source = self.track_by_id(merged[0]).identity
        for tid in merged[1:]:
            self.track_by_id(tid).apply_identity(source)
        self.identity_groups = normalize_link_groups(
            remaining + [merged], {t.id for t in self.tracks})
        return merged

    def unlink_track_identity(self, track_id: str) -> bool:
        """Remove a track from its identity link group. Keeps its current
        identity values (a copy, not a reset). Returns True if it was
        linked."""
        for g in self.identity_groups:
            if track_id in g:
                g.remove(track_id)
                break
        else:
            return False
        self.identity_groups = normalize_link_groups(
            self.identity_groups, {t.id for t in self.tracks})
        return True

    def sample_by_id(self, sid: str) -> SampleAsset:
        for s in self.samples:
            if s.id == sid:
                return s
        raise ProjectError(f"unknown sample '{sid}'")

    def track_by_id(self, tid: str) -> PlaylistTrack:
        for t in self.tracks:
            if t.id == tid:
                return t
        raise ProjectError(f"unknown track '{tid}'")

    def lanes_for(self, track_id: str, param: str) -> list[AutomationLane]:
        return [a for a in self.automation
                if a.track_id == track_id and a.param == param]

    def arrangement_bars(self) -> int:
        """Length of the arrangement in bars (min 4, matching the engine)."""
        bars = 4
        for t in self.tracks:
            for c in t.clips:
                bars = max(bars, (c.start_beat + c.bars * 4 + 3) // 4)
            for ac in t.audio_clips:
                end_beat = ac.start_beat + ac.length_beats
                bars = max(bars, int(-(-end_beat // 4)))
        return bars

    # -- validation -------------------------------------------------------

    def validate(self) -> None:
        if not (20.0 <= self.tempo <= 300.0):
            raise ProjectError(f"tempo {self.tempo} out of range 20-300")
        if not self.patterns:
            raise ProjectError("project has no patterns")
        if not self.tracks:
            raise ProjectError("project has no playlist tracks")
        # Identity link groups: drop stale ids (e.g. after track delete).
        self.identity_groups = normalize_link_groups(
            self.identity_groups, {t.id for t in self.tracks})
        seen = set()
        for p in self.patterns:
            if p.id in seen:
                raise ProjectError(f"duplicate pattern id '{p.id}'")
            seen.add(p.id)
            p.validate()
        if self.selected_pattern not in seen:
            raise ProjectError(f"selected pattern '{self.selected_pattern}' does not exist")
        seen_samples = set()
        for s in self.samples:
            if s.id in seen_samples:
                raise ProjectError(f"duplicate sample id '{s.id}'")
            seen_samples.add(s.id)
            s.validate()
        seen_tracks = set()
        for t in self.tracks:
            if t.id in seen_tracks:
                raise ProjectError(f"duplicate track id '{t.id}'")
            seen_tracks.add(t.id)
            t.validate(seen, seen_samples)
        max_beat = float(self.arrangement_bars() * 4)
        seen_lanes = set()
        for lane in self.automation:
            if lane.id in seen_lanes:
                raise ProjectError(f"duplicate automation id '{lane.id}'")
            seen_lanes.add(lane.id)
            if lane.track_id not in seen_tracks:
                raise ProjectError(
                    f"automation lane '{lane.id}': unknown track '{lane.track_id}'"
                )
            lane.validate(self.track_by_id(lane.track_id), max_beat)
        # Validate sends: targets exist, no self-sends, no cycles
        # (LMMS isInfiniteLoop pattern).
        for t in self.tracks:
            t.validate_sends(seen_tracks)
        self._check_send_cycles()

    def _routing_adjacency(self) -> dict[str, list[str]]:
        """Adjacency of the full routing graph: sends + output routes.

        Both edge kinds carry audio between tracks, so both participate in
        cycle detection (an output route back into its own source chain
        would be a feedback loop).
        """
        adj = {}
        for t in self.tracks:
            dests = [s.to_track_id for s in t.sends]
            if t.output != "master":
                dests.append(t.output)
            adj[t.id] = dests
        return adj

    def _check_send_cycles(self) -> None:
        """Reject routing graphs with cycles (feedback loops)."""
        # Build adjacency: track_id -> [destination ids].
        adj = self._routing_adjacency()
        # DFS from each track; if we revisit a node on the current path,
        # there's a cycle.
        def has_cycle(start: str) -> bool:
            visited = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                for nxt in adj.get(node, []):
                    if nxt == start:
                        return True
                    if nxt not in visited:
                        stack.append(nxt)
            return False
        for t in self.tracks:
            if has_cycle(t.id):
                raise ProjectError(
                    f"routing cycle detected involving track '{t.id}'")

    def would_create_cycle(self, from_id: str, to_id: str) -> bool:
        """Check if adding from_id -> to_id would create a cycle.

        Considers both sends and exclusive output routes.
        """
        adj = self._routing_adjacency()
        adj.setdefault(from_id, []).append(to_id)
        # Can we get from to_id back to from_id?
        visited = set()
        stack = [to_id]
        while stack:
            node = stack.pop()
            if node == from_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adj.get(node, []))
        return False

    def would_create_output_cycle(self, from_id: str, to_id: str) -> bool:
        """Check if setting from_id's output route to to_id cycles.

        The track's existing output edge is replaced (not added), so it is
        removed from the adjacency before testing.
        """
        adj = self._routing_adjacency()
        current = [t for t in self.tracks if t.id == from_id]
        if current and current[0].output != "master":
            try:
                adj[from_id].remove(current[0].output)
            except ValueError:
                pass
        adj.setdefault(from_id, []).append(to_id)
        visited = set()
        stack = [to_id]
        while stack:
            node = stack.pop()
            if node == from_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adj.get(node, []))
        return False

    # -- serialization ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "format": FORMAT_ID,
            "version": FORMAT_VERSION,
            "truncate_notes": self.truncate_notes,
            "identity_groups": [list(g) for g in self.identity_groups],
            "name": self.name,
            "tempo": self.tempo,
            "patterns": [p.to_dict() for p in self.patterns],
            "selected_pattern": self.selected_pattern,
            "samples": [s.to_dict() for s in self.samples],
            "playlist": {"tracks": [t.to_dict() for t in self.tracks]},
            "automation": [a.to_dict() for a in self.automation],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        if not isinstance(data, dict):
            raise ProjectError("project root must be an object")
        if data.get("format") != FORMAT_ID:
            raise ProjectError(
                f"not a Pulsegrid project (format={data.get('format')!r})"
            )
        version = data.get("version")
        if not isinstance(version, int):
            raise ProjectError("project version missing or invalid")
        if version > FORMAT_VERSION:
            raise ProjectError(
                f"project was saved by a newer Pulsegrid (v{version}); "
                f"this build reads up to v{FORMAT_VERSION}"
            )
        if version == 1:
            project = _migrate_v1(data)
        elif version == 2:
            project = _parse_v2(data)  # fills per-step pitches from v2 pitch
        elif version in (3, 4, 5):
            # v3/v4 migrate: per-step arrays become 1-step notes,
            # bar starts already became beats in v4; v5 has no lanes yet.
            project = _parse_v5(data)
        elif version == 6:
            project = _parse_v6(data)
        elif version == 7:
            # v7: plugin effects (additive; v6 files load unchanged).
            project = _parse_v7(data)
        elif version == 8:
            # v8 -> v9: truncate_notes defaults to False (v8 behavior).
            project = _parse_v8(data)
            project.truncate_notes = bool(data.get("truncate_notes", False))
        elif version == 9:
            # v9 -> v10: sends default to [] (v9 behavior: no routing).
            project = _parse_v9(data)
            for t in project.tracks:
                t.sends = []
        elif version == 10:
            # v10 -> v11: single generator becomes one-layer stack
            # (handled by PlaylistTrack.from_dict legacy path).
            project = _parse_v10(data)
        elif version == 11:
            # v11 -> v12: sends gain tap/pan/sidechain fields (parsed by
            # Send.from_dict with defaults; additive).
            project = _parse_v11(data)
        elif version == 12:
            # v12 -> v13: per-track velocity tracking (vel_track /
            # vel_track_mid, parsed by PlaylistTrack.from_dict with
            # defaults; additive).
            project = _parse_v12(data)
        elif version == 13:
            # v13 -> v14: per-note panning (pan, parsed by
            # Note.from_dict with default 0.5; additive).
            project = _parse_v13(data)
        elif version == 14:
            # v14 -> v15: sample assets + audio clips (both parsed
            # additively with empty defaults; additive).
            project = _parse_v15(data)
        else:
            # v15: sample assets + audio clips.
            project = _parse_v15(data)
        _migrate_identity(project, data)
        project.validate()
        return project

    def snapshot(self) -> "Project":
        """Deep copy for undo/redo."""
        return copy.deepcopy(self)

    # -- persistence -----------------------------------------------------

    def save(self, path: str) -> None:
        """Atomic save: write temp file, then rename over the target."""
        self.validate()
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=".pulsegrid-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
                f.write("\n")
            os.replace(tmp, path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise ProjectError(f"could not save '{path}': {e}") from e

    @classmethod
    def load(cls, path: str) -> "Project":
        """Load and validate. On corrupt files, keep a backup and explain."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise ProjectError(f"file not found: '{path}'")
        except json.JSONDecodeError as e:
            backup = _backup_corrupt_file(path)
            raise ProjectError(
                f"'{path}' is not valid JSON ({e}). "
                f"The original was preserved at '{backup}'."
            ) from e
        except OSError as e:
            raise ProjectError(f"could not read '{path}': {e}") from e

        try:
            return cls.from_dict(data)
        except ProjectError as e:
            backup = _backup_corrupt_file(path)
            raise ProjectError(f"'{path}' failed validation: {e} "
                               f"Original preserved at '{backup}'.") from e


def _migrate_identity(project: "Project", data: dict) -> None:
    """Additive identity migration (research topic 33).

    Reads identity_groups (absent in older files) and gives tracks that
    predate persistent colors a positional default, matching the old
    positional mixer accent colors so migrated projects look the same.
    """
    project.identity_groups = [list(g)
                               for g in data.get("identity_groups", [])]
    raw_tracks = data.get("playlist", {}).get("tracks", [])
    raw_by_id = {t.get("id"): t for t in raw_tracks
                 if isinstance(t, dict)}
    for i, track in enumerate(project.tracks):
        raw = raw_by_id.get(track.id, {})
        if "color_idx" not in raw:
            track.color_idx = default_color_for(i)


def _parse_v2(data: dict) -> Project:
    return _parse_v5(data, pre_v3=True)


def _parse_v7(data: dict) -> Project:
    # v7 is v6 plus plugin effects; the Effect parser already handles
    # the plugin fields, so parsing is identical.
    return _parse_v6(data)


def _parse_v8(data: dict) -> Project:
    # v8 is v7 plus track generators; PlaylistTrack.from_dict already
    # handles the generator field (None when absent), so parsing is
    # identical.
    return _parse_v7(data)


def _parse_v9(data: dict) -> Project:
    # v9 is v8 plus the truncate_notes project setting.
    project = _parse_v8(data)
    project.truncate_notes = bool(data.get("truncate_notes", False))
    return project


def _parse_v10(data: dict) -> Project:
    # v10 is v9 plus per-track sends (parsed by PlaylistTrack.from_dict).
    return _parse_v9(data)


def _parse_v11(data: dict) -> Project:
    # v11 is v10 plus generator layers (parsed by PlaylistTrack.from_dict).
    return _parse_v10(data)


def _parse_v12(data: dict) -> Project:
    # v12 is v11 plus per-send tap/pan/sidechain and send automation lanes
    # (both parsed additively by Send/AutomationLane.from_dict).
    return _parse_v11(data)


def _parse_v13(data: dict) -> Project:
    # v13 is v12 plus per-track velocity tracking (parsed additively by
    # PlaylistTrack.from_dict with defaults).
    return _parse_v12(data)


def _parse_v14(data: dict) -> Project:
    # v14 is v13 plus per-note panning (parsed additively by
    # Note.from_dict with default 0.5 center).
    return _parse_v13(data)


def _parse_v15(data: dict) -> Project:
    # v15 is v14 plus sample assets and per-track audio clips (both
    # parsed additively: SampleAsset.from_dict / AudioClip.from_dict
    # with empty defaults).
    project = _parse_v14(data)
    project.samples = [SampleAsset.from_dict(s)
                       for s in data.get("samples", [])]
    return project


def _parse_v6(data: dict) -> Project:
    try:
        playlist = data.get("playlist", {})
        return Project(
            name=str(data.get("name", "Untitled")),
            tempo=float(data["tempo"]),
            patterns=[Pattern.from_dict(p) for p in data["patterns"]],
            selected_pattern=str(data.get("selected_pattern", "")),
            tracks=[PlaylistTrack.from_dict(t) for t in playlist.get("tracks", [])],
            automation=[AutomationLane.from_dict(a)
                        for a in data.get("automation", [])],
        )
    except KeyError as e:
        raise ProjectError(f"project missing field {e}") from e
    except (TypeError, ValueError) as e:
        raise ProjectError(f"project has invalid field: {e}") from e


def _parse_v5(data: dict, pre_v3: bool = False) -> Project:
    try:
        playlist = data.get("playlist", {})
        return Project(
            name=str(data.get("name", "Untitled")),
            tempo=float(data["tempo"]),
            patterns=[Pattern.from_dict(p, pre_v3) for p in data["patterns"]],
            selected_pattern=str(data.get("selected_pattern", "")),
            tracks=[PlaylistTrack.from_dict(t) for t in playlist.get("tracks", [])],
        )
    except KeyError as e:
        raise ProjectError(f"project missing field {e}") from e
    except (TypeError, ValueError) as e:
        raise ProjectError(f"project has invalid field: {e}") from e


def _migrate_v1(data: dict) -> Project:
    """v1 -> v2: the single pattern becomes 'Pattern 1' on its own track."""
    try:
        steps = int(data.get("steps", 16))
        channels = [Channel.from_dict(c, pre_v3=True) for c in data["channels"]]
        pattern = Pattern(id="pattern-1", name="Pattern 1", steps=steps,
                          channels=channels)
        clip_bars = max(1, -(-steps // 16))
        track = PlaylistTrack(
            id="track-1", name="Track 1",
            clips=[Clip(pattern_id="pattern-1", start_beat=0, bars=clip_bars)],
        )
        return Project(
            name=str(data.get("name", "Untitled")),
            tempo=float(data["tempo"]),
            patterns=[pattern],
            selected_pattern="pattern-1",
            tracks=[track],
        )
    except KeyError as e:
        raise ProjectError(f"v1 project missing field {e}") from e
    except (TypeError, ValueError) as e:
        raise ProjectError(f"v1 project has invalid field: {e}") from e


def _backup_corrupt_file(path: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.corrupt-{stamp}.bak"
    try:
        with open(path, "rb") as src, open(backup, "wb") as dst:
            dst.write(src.read())
    except OSError:
        return path  # best effort; report the original path
    return backup


def blank_channels(prefix: str, steps: int = 16) -> list[Channel]:
    """Five empty channels (kick/snare/hat/bass/lead) for a new pattern."""
    return [
        Channel(id=f"{prefix}-{cid}", name=cname, instrument=inst, pitch=pitch,
                notes=[])
        for cid, cname, inst, pitch in _DEFAULT_CHANNELS
    ]


def _blank_channels(prefix: str, steps: int = 16) -> list[Channel]:
    return blank_channels(prefix, steps)


def new_default_project(name: str = "Untitled") -> Project:
    """A musical starting point: a Drums pattern and a Bass & Lead pattern
    arranged on two playlist tracks (bass joins after 4 bars)."""
    drums = Pattern(id="pattern-1", name="Drums", steps=16,
                    channels=_blank_channels("pattern-1"))
    music = Pattern(id="pattern-2", name="Bass & Lead", steps=16,
                    channels=_blank_channels("pattern-2"))

    d = {c.id: c for c in drums.channels}

    def _n(start, length=1.0, pitch=36, vel=0.9):
        return Note(start=start, length=length, pitch=pitch, vel=vel)

    for s in (0, 4, 8, 12):
        d["pattern-1-kick"].notes.append(_n(s, pitch=36))
    for s in (4, 12):
        d["pattern-1-snare"].notes.append(_n(s, pitch=38))
    for s in range(16):
        if s % 2 == 0:
            d["pattern-1-hat"].notes.append(
                _n(s, pitch=42, vel=1.0 if s % 4 == 0 else 0.6))

    m = {c.id: c for c in music.channels}
    # Bassline with real note lengths; lead answers with a two-note chord.
    for start, length, pitch in ((0, 3, 33), (3, 1, 36), (6, 2, 31),
                                 (10, 2, 34), (12, 3, 29)):
        m["pattern-2-bass"].notes.append(_n(start, length, pitch))
    m["pattern-2-lead"].notes.append(_n(14, 2, 60))
    m["pattern-2-lead"].notes.append(_n(14, 2, 64))

    tracks = [
        PlaylistTrack(id="track-1", name="Drums",
                      color_idx=default_color_for(0), icon="drm",
                      clips=[Clip(pattern_id="pattern-1", start_beat=0, bars=8)]),
        PlaylistTrack(id="track-2", name="Music",
                      color_idx=default_color_for(1), icon="syn",
                      clips=[Clip(pattern_id="pattern-2", start_beat=16, bars=4)]),
    ]
    project = Project(name=name, tempo=128.0, patterns=[drums, music],
                      selected_pattern="pattern-1", tracks=tracks)
    project.validate()
    return project


def empty_project(name: str = "Untitled") -> Project:
    """Blank project: one empty pattern on one track."""
    pattern = Pattern(id="pattern-1", name="Pattern 1", steps=16,
                      channels=_blank_channels("pattern-1"))
    track = PlaylistTrack(id="track-1", name="Track 1",
                          clips=[Clip(pattern_id="pattern-1", start_beat=0, bars=1)])
    project = Project(name=name, tempo=128.0, patterns=[pattern],
                      selected_pattern="pattern-1", tracks=[track])
    project.validate()
    return project
