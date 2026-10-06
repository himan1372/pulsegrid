"""Unit tests for the content library (preset browser index + favorites).

No GUI, no audio engine. Uses tmp_path for the user presets dir and the
favorites file so the real ~/.pulsegrid is never touched.
"""

import json
import os

import pytest

from daw.content import (
    PresetEntry,
    factory_presets,
    find_entry,
    load_favorite_ids,
    save_favorite_ids,
    scan_presets,
    toggle_favorite,
)
from daw.presets import PRESET_EXTENSION, Preset, save_preset
from daw.project import Effect, ProjectError


def _write_preset(directory, name, preset):
    path = os.path.join(directory, name + PRESET_EXTENSION)
    save_preset(preset, path)
    return path


def test_factory_presets_are_valid():
    presets = factory_presets()
    assert len(presets) >= 3
    for p in presets:
        p.validate()
        assert p.type == "chain"
        fx = p.to_effects()
        assert fx
        for e in fx:
            e.validate()


def test_scan_empty_dir_returns_only_factory(tmp_path):
    entries = scan_presets(preset_dir=str(tmp_path),
                           favorites_file=str(tmp_path / "fav.json"))
    assert all(e.entry_id.startswith("factory:") for e in entries)
    assert len(entries) == len(factory_presets())


def test_scan_finds_user_presets_recursively(tmp_path):
    sub = tmp_path / "user" / "bass"
    sub.mkdir(parents=True)
    p = Preset.from_chain(
        [Effect.default("delay")], "Deep Echo", "a test preset")
    _write_preset(str(sub), "deep-echo", p)
    entries = scan_presets(preset_dir=str(tmp_path / "user"),
                           favorites_file=str(tmp_path / "fav.json"))
    by_id = {e.entry_id: e for e in entries}
    assert "file:bass/deep-echo.pulsegrid-preset" in by_id
    entry = by_id["file:bass/deep-echo.pulsegrid-preset"]
    assert entry.name == "Deep Echo"
    assert entry.kind == "chain"
    assert entry.path.endswith("deep-echo" + PRESET_EXTENSION)


def test_scan_skips_corrupt_files(tmp_path):
    bad = tmp_path / ("broken" + PRESET_EXTENSION)
    bad.write_text("{ not valid json", encoding="utf-8")
    entries = scan_presets(preset_dir=str(tmp_path),
                           favorites_file=str(tmp_path / "fav.json"))
    assert all("broken" not in e.entry_id for e in entries)


def test_scan_ignores_non_preset_files(tmp_path):
    (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
    entries = scan_presets(preset_dir=str(tmp_path),
                           favorites_file=str(tmp_path / "fav.json"))
    assert all(e.entry_id.startswith("factory:") for e in entries)


def test_favorites_round_trip(tmp_path):
    fav = str(tmp_path / "fav.json")
    assert load_favorite_ids(fav) == set()
    assert toggle_favorite("factory:slapback-echo", fav) is True
    assert load_favorite_ids(fav) == {"factory:slapback-echo"}
    assert toggle_favorite("factory:slapback-echo", fav) is False
    assert load_favorite_ids(fav) == set()


def test_favorites_applied_during_scan(tmp_path):
    fav = str(tmp_path / "fav.json")
    save_favorite_ids({"factory:warm-lowpass"}, fav)
    entries = scan_presets(preset_dir=str(tmp_path),
                           favorites_file=fav)
    by_id = {e.entry_id: e for e in entries}
    assert by_id["factory:warm-lowpass"].favorite is True
    assert by_id["factory:slapback-echo"].favorite is False


def test_corrupt_favorites_file_is_empty_set(tmp_path):
    fav = tmp_path / "fav.json"
    fav.write_text("nope", encoding="utf-8")
    assert load_favorite_ids(str(fav)) == set()


def test_find_entry(tmp_path):
    entries = scan_presets(preset_dir=str(tmp_path),
                           favorites_file=str(tmp_path / "fav.json"))
    entry = find_entry(entries, "factory:punch-drive")
    assert isinstance(entry, PresetEntry)
    assert entry.name == "Punch Drive"
    with pytest.raises(ProjectError):
        find_entry(entries, "factory:nope")


def test_chain_preset_applies_fx_to_track(tmp_path):
    from daw.project import PlaylistTrack
    track = PlaylistTrack(id="t1", name="T1")
    track.effects = [Effect.default("drive")]
    entries = scan_presets(preset_dir=str(tmp_path),
                           favorites_file=str(tmp_path / "fav.json"))
    entry = find_entry(entries, "factory:space-echo")
    track.effects = entry.preset.to_effects()
    assert [e.kind for e in track.effects] == ["delay", "filter"]
    assert track.effects[0].params["time_ms"] == 450.0
