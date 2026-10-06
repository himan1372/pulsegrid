"""Pulsegrid content library: preset discovery, indexing, favorites.

This is the "content browser" layer of the app, inspired by the FL Studio
vs LMMS Preset/Content Browser research: a persistent, searchable,
favorites-capable library of presets rather than a pile of file dialogs.

Architecture:

    factory presets (bundled) ──┐
                                ├──> scan_presets() ──> [PresetEntry]
    ~/.pulsegrid/presets/**/*.pulsegrid-preset ──┘            │
                                                              ▼
                                                    Browser tab UI
                                                    (search / star /
                                                     preview / drag-drop)

Favorites are stored separately (~/.pulsegrid/favorites.json) so starring
a factory or user preset never modifies the preset file itself.

PresetEntry ids are stable strings:
    "factory:<slug>"            -- bundled preset
    "file:<relative path>"      -- user preset file
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .presets import PRESET_EXTENSION, Preset, ProjectError, load_preset
from .project import Effect

# -- locations ------------------------------------------------------------

APP_DIR_NAME = ".pulsegrid"
PRESETS_DIR_NAME = "presets"
FAVORITES_FILE_NAME = "favorites.json"


def app_dir() -> str:
    """Per-user Pulsegrid data directory (~/.pulsegrid)."""
    return os.path.join(os.path.expanduser("~"), APP_DIR_NAME)


def user_presets_dir() -> str:
    """Directory scanned for user presets (created on demand)."""
    return os.path.join(app_dir(), PRESETS_DIR_NAME)


def favorites_path() -> str:
    return os.path.join(app_dir(), FAVORITES_FILE_NAME)


def ensure_user_presets_dir() -> str:
    path = user_presets_dir()
    os.makedirs(path, exist_ok=True)
    return path


# -- factory presets ------------------------------------------------------

def _chain_preset(slug: str, name: str, description: str,
                  effects: list[Effect]) -> Preset:
    return Preset(type="chain", name=name, description=description,
                  data={"effects": [e.to_dict() for e in effects],
                        "_factory_slug": slug})


def factory_presets() -> list[Preset]:
    """Bundled presets that always show in the Browser.

    All use built-in effects so they work on every machine without
    third-party plugins installed.
    """
    slapback = Effect(kind="delay",
                      params={"time_ms": 120.0, "feedback": 25.0,
                              "mix": 35.0})
    space = Effect(kind="delay",
                   params={"time_ms": 450.0, "feedback": 55.0,
                           "mix": 45.0})
    dark = Effect(kind="filter", params={"cutoff": 4000.0})
    warm = Effect(kind="filter", params={"cutoff": 1200.0})
    punch = Effect(kind="drive", params={"amount": 55.0})
    grit = Effect(kind="drive", params={"amount": 25.0})
    return [
        _chain_preset("slapback-echo", "Slapback Echo",
                      "Short rockabilly-style slap delay.",
                      [slapback]),
        _chain_preset("warm-lowpass", "Warm Lowpass",
                      "Darkens the track with a gentle lowpass filter.",
                      [warm]),
        _chain_preset("punch-drive", "Punch Drive",
                      "Adds grit and presence with a drive stage.",
                      [punch]),
        _chain_preset("space-echo", "Space Echo",
                      "Long washed-out delay rolled off with a filter.",
                      [space, dark]),
        _chain_preset("gritty-lowpass", "Gritty Lowpass",
                      "Lo-fi combo: light drive into a dark filter.",
                      [grit, warm]),
    ]


# -- entries --------------------------------------------------------------

@dataclass
class PresetEntry:
    """One item in the content library index."""
    entry_id: str          # "factory:<slug>" | "file:<relpath>"
    preset: Preset
    path: str | None       # absolute path for file presets, else None
    favorite: bool = False

    @property
    def name(self) -> str:
        return self.preset.name

    @property
    def kind(self) -> str:
        return self.preset.type  # "effect" | "chain" | "track"


# -- favorites ------------------------------------------------------------

def load_favorite_ids(path: str | None = None) -> set[str]:
    """Load the set of favorited entry ids (empty set on any problem)."""
    path = path or favorites_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(x) for x in data}


def save_favorite_ids(ids: set[str], path: str | None = None) -> None:
    path = path or favorites_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(ids), f, indent=2)
        f.write("\n")


def toggle_favorite(entry_id: str, path: str | None = None) -> bool:
    """Toggle favorite state; returns the new state (True = favorited)."""
    ids = load_favorite_ids(path)
    if entry_id in ids:
        ids.discard(entry_id)
        new_state = False
    else:
        ids.add(entry_id)
        new_state = True
    save_favorite_ids(ids, path)
    return new_state


# -- scanning -------------------------------------------------------------

def scan_presets(preset_dir: str | None = None,
                 favorites_file: str | None = None) -> list[PresetEntry]:
    """Build the content index: factory presets + recursive user scan.

    Corrupt preset files are skipped silently (they still exist on disk
    for the user to fix). Favorites are applied from favorites.json.
    """
    favs = load_favorite_ids(favorites_file)
    entries: list[PresetEntry] = []
    for preset in factory_presets():
        slug = preset.data.get("_factory_slug", preset.name)
        entry_id = f"factory:{slug}"
        entries.append(PresetEntry(entry_id=entry_id, preset=preset,
                                  path=None,
                                  favorite=entry_id in favs))
    root = preset_dir or user_presets_dir()
    if os.path.isdir(root):
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in sorted(filenames):
                if not fname.endswith(PRESET_EXTENSION):
                    continue
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, root)
                entry_id = f"file:{rel}"
                try:
                    preset = load_preset(full)
                except ProjectError:
                    continue
                entries.append(PresetEntry(entry_id=entry_id, preset=preset,
                                          path=full,
                                          favorite=entry_id in favs))
    return entries


def find_entry(entries: list[PresetEntry], entry_id: str) -> PresetEntry:
    for e in entries:
        if e.entry_id == entry_id:
            return e
    raise ProjectError(f"preset not found in library: {entry_id}")
