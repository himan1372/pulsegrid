"""Pulsegrid preset system: reusable effect/chain/track states.

Three layers (from the FL Studio vs LMMS presets infographic):
1. Effect preset -- "How should this plugin sound?" (single FX)
2. Chain preset -- FX chain (like FL's Mixer Track State)
3. Track preset -- "How should this channel behave?" (like LMMS .xpf:
   generator + FX chain + gain/pan, portable, no sends/clips)

Presets are JSON (.pulsegrid-preset), human-readable like our project format.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .project import (
    Effect,
    Generator,
    GeneratorLayer,
    PlaylistTrack,
    ProjectError,
)

PRESET_FORMAT_ID = "pulsegrid-preset"
PRESET_VERSION = 1
PRESET_EXTENSION = ".pulsegrid-preset"


@dataclass
class Preset:
    """A reusable state snapshot."""
    type: str  # "effect" | "chain" | "track"
    name: str
    description: str = ""
    data: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.type not in ("effect", "chain", "track"):
            raise ProjectError(f"preset: unknown type '{self.type}'")
        if not self.name:
            raise ProjectError("preset: name is required")
        if not isinstance(self.data, dict):
            raise ProjectError("preset: data must be an object")

    def to_dict(self) -> dict:
        return {
            "format": PRESET_FORMAT_ID,
            "version": PRESET_VERSION,
            "type": self.type,
            "name": self.name,
            "description": self.description,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Preset":
        if not isinstance(d, dict):
            raise ProjectError("preset must be an object")
        if d.get("format") != PRESET_FORMAT_ID:
            raise ProjectError(
                f"not a Pulsegrid preset (format={d.get('format')})")
        version = d.get("version", 1)
        if version != PRESET_VERSION:
            raise ProjectError(
                f"unsupported preset version {version}")
        p = cls(
            type=str(d.get("type", "")),
            name=str(d.get("name", "Untitled")),
            description=str(d.get("description", "")),
            data=d.get("data", {}),
        )
        p.validate()
        return p

    # -- constructors ---------------------------------------------------

    @classmethod
    def from_effect(cls, effect: Effect, name: str,
                    description: str = "") -> "Preset":
        """Single effect preset (layer 1)."""
        return cls(type="effect", name=name, description=description,
                   data={"effect": effect.to_dict()})

    @classmethod
    def from_chain(cls, effects: list[Effect], name: str,
                   description: str = "") -> "Preset":
        """FX chain preset (layer 2, like FL Mixer Track State)."""
        return cls(type="chain", name=name, description=description,
                   data={"effects": [e.to_dict() for e in effects]})

    @classmethod
    def from_track(cls, track: PlaylistTrack, name: str,
                   description: str = "") -> "Preset":
        """Track preset (layer 3, like LMMS .xpf).

        Portable: includes generator, FX chain, gain/pan. Excludes clips,
        sends, and automation (those reference other tracks/timeline).
        """
        return cls(
            type="track",
            name=name,
            description=description,
            data={
                "track_name": track.name,
                "gain": track.gain,
                "pan": track.pan,
                "generator_layers": [l.to_dict()
                                     for l in track.generator_layers],
                "layer_mode": track.layer_mode,
                "effects": [e.to_dict() for e in track.effects],
            },
        )

    # -- application ----------------------------------------------------

    def to_effect(self) -> Effect:
        """Convert an effect preset to an Effect."""
        if self.type != "effect":
            raise ProjectError(
                f"preset '{self.name}' is not an effect preset")
        return Effect.from_dict(self.data["effect"])

    def to_effects(self) -> list[Effect]:
        """Convert a chain preset to a list of Effects."""
        if self.type != "chain":
            raise ProjectError(
                f"preset '{self.name}' is not a chain preset")
        return [Effect.from_dict(e) for e in self.data.get("effects", [])]

    def apply_to_track(self, track: PlaylistTrack) -> None:
        """Apply a track preset to a track (replaces generator/FX/gain/pan).

        Like LMMS .xpf: portable instrument+FX state. Does not touch clips,
        sends, or automation.
        """
        if self.type != "track":
            raise ProjectError(
                f"preset '{self.name}' is not a track preset")
        d = self.data
        track.gain = float(d.get("gain", 1.0))
        track.pan = float(d.get("pan", 0.0))
        # New style: generator_layers; legacy: single generator.
        if "generator_layers" in d:
            track.generator_layers = [
                GeneratorLayer.from_dict(l)
                for l in d["generator_layers"]]
            track.layer_mode = str(d.get("layer_mode", "all"))
        else:
            gen_data = d.get("generator")
            track.generator_layers = (
                [GeneratorLayer.from_dict(gen_data)] if gen_data else [])
            track.layer_mode = "all"
        track.effects = [Effect.from_dict(e)
                         for e in d.get("effects", [])]
        # Validate the changed fields (not clips -- preset doesn't touch them).
        if not (0.0 <= track.gain <= 2.0):
            raise ProjectError(f"preset: gain {track.gain} out of range 0-2")
        if not (-1.0 <= track.pan <= 1.0):
            raise ProjectError(f"preset: pan {track.pan} out of range -1..1")
        for fx in track.effects:
            fx.validate()
        for layer in track.generator_layers:
            layer.validate()


def save_preset(preset: Preset, path: str) -> None:
    """Save a preset to a .pulsegrid-preset JSON file."""
    if not path.endswith(PRESET_EXTENSION):
        path += PRESET_EXTENSION
    preset.validate()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(preset.to_dict(), f, indent=2)
        f.write("\n")


def load_preset(path: str) -> Preset:
    """Load a preset from a .pulsegrid-preset JSON file."""
    if not os.path.isfile(path):
        raise ProjectError(f"preset file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ProjectError(f"preset file is not valid JSON: {e}") from e
    return Preset.from_dict(data)


def list_presets(directory: str,
                 preset_type: str | None = None) -> list[Preset]:
    """List presets in a directory, optionally filtered by type."""
    presets = []
    if not os.path.isdir(directory):
        return presets
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(PRESET_EXTENSION):
            continue
        try:
            p = load_preset(os.path.join(directory, fname))
            if preset_type is None or p.type == preset_type:
                presets.append(p)
        except ProjectError:
            continue  # Skip invalid presets.
    return presets
