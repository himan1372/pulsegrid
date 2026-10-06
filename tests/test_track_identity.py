"""Tests for track presentation identity (research topic 33).

Model tests (linking/ripple/serialization/migration) are pure.
StripPresentation builder/geometry tests are pure. Widget probes
(mixer accent bar, playlist header badge/chip) need an X11 display
(run under xvfb-run).
"""

import copy
import os

import pytest

from daw.project import (
    PlaylistTrack,
    Project,
    ProjectError,
    new_default_project,
)
from daw.track_identity import (
    TRACK_COLORS,
    TRACK_ICONS,
    TrackIdentity,
    default_color_for,
    linked_members,
    normalize_link_groups,
)
from daw.ui.strip_presentation import (
    build_strip_presentations,
    strip_geometry,
)

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="widget probes need an X11 display (run under xvfb-run)",
)


def _proj():
    p = new_default_project()
    p.validate()
    return p


# -- TrackIdentity -----------------------------------------------------------

def test_identity_defaults():
    ident = TrackIdentity()
    assert ident.name == "Track"
    assert ident.color_idx == 0
    assert ident.icon == ""


def test_identity_validation_rejects_bad_values():
    with pytest.raises(ValueError):
        TrackIdentity(name="  ").validate("t1")
    with pytest.raises(ValueError):
        TrackIdentity(name="X", color_idx=len(TRACK_COLORS)).validate("t1")
    with pytest.raises(ValueError):
        TrackIdentity(name="X", icon="nope").validate("t1")
    # Good values pass.
    TrackIdentity(name="Bass", color_idx=3, icon="bas").validate("t1")


def test_identity_serialization_round_trip():
    ident = TrackIdentity(name="Bass", color_idx=3, icon="bas")
    assert TrackIdentity.from_dict(ident.to_dict()).to_dict() == \
        ident.to_dict()


def test_identity_from_dict_is_defensive():
    # Hand-edited garbage must not crash load.
    ident = TrackIdentity.from_dict({"name": "X", "color_idx": 99,
                                     "icon": "nope"},
                                    fallback_color=2)
    assert ident.color_idx == 2
    assert ident.icon == ""


def test_default_color_matches_old_positional_scheme():
    for i in range(16):
        assert default_color_for(i) == i % len(TRACK_COLORS)


# -- linking (FL Track Mode analog) ------------------------------------------

def test_link_and_ripple():
    p = _proj()
    ids = [t.id for t in p.tracks]
    group = p.link_track_identities(ids)
    assert set(group) == set(ids)
    p.set_track_identity(ids[0], name="Rhythm", color_idx=5, icon="drm")
    for t in p.tracks:
        assert (t.name, t.color_idx, t.icon) == ("Rhythm", 5, "drm")
    p.validate()


def test_link_copies_first_identity_to_others():
    p = _proj()
    ids = [t.id for t in p.tracks]
    p.set_track_identity(ids[1], name="Music2", color_idx=4, icon="syn")
    p.link_track_identities([ids[1], ids[0]])
    assert p.track_by_id(ids[0]).name == "Music2"
    assert p.track_by_id(ids[0]).color_idx == 4


def test_link_merges_existing_groups():
    p = _proj()
    p.tracks.append(PlaylistTrack(id="t3", name="Extra"))
    p.validate()
    ids = [t.id for t in p.tracks]
    p.link_track_identities(ids[:2])
    p.link_track_identities(ids[1:])
    assert len(p.identity_groups) == 1
    assert set(p.identity_groups[0]) == set(ids)


def test_link_needs_two_tracks():
    p = _proj()
    with pytest.raises(ProjectError):
        p.link_track_identities([p.tracks[0].id])


def test_unlink_keeps_values():
    p = _proj()
    ids = [t.id for t in p.tracks]
    p.link_track_identities(ids)
    p.set_track_identity(ids[0], name="Shared", color_idx=2, icon="mix")
    assert p.unlink_track_identity(ids[0]) is True
    assert p.identity_groups == []
    # Unlinked track keeps its current identity values (a copy, not reset).
    t = p.track_by_id(ids[0])
    assert (t.name, t.color_idx, t.icon) == ("Shared", 2, "mix")
    # ...and is now independent.
    p.set_track_identity(ids[0], name="Solo")
    assert p.track_by_id(ids[1]).name == "Shared"
    assert p.unlink_track_identity(ids[0]) is False


def test_unlinked_track_identity_is_independent():
    p = _proj()
    ids = [t.id for t in p.tracks]
    p.set_track_identity(ids[0], name="Only")
    assert p.track_by_id(ids[1]).name != "Only"
    assert p.linked_group(ids[0]) == [ids[0]]


def test_set_track_identity_validates():
    p = _proj()
    tid = p.tracks[0].id
    with pytest.raises(ProjectError):
        p.set_track_identity(tid, name="   ")
    with pytest.raises(ProjectError):
        p.set_track_identity(tid, color_idx=999)
    with pytest.raises(ProjectError):
        p.set_track_identity(tid, icon="nope")


def test_normalize_link_groups():
    ids = {"a", "b", "c"}
    groups = normalize_link_groups(
        [["a", "b"], ["b"], ["a", "b"], ["x", "a"], ["c", "c", "b"]], ids)
    assert groups == [["a", "b"], ["c", "b"]]


def test_linked_members():
    assert linked_members([["a", "b"]], "a") == ["a", "b"]
    assert linked_members([["a", "b"]], "z") == ["z"]


# -- serialization / migration ------------------------------------------------

def test_identity_round_trip_with_groups():
    p = _proj()
    ids = [t.id for t in p.tracks]
    p.link_track_identities(ids)
    p.set_track_identity(ids[0], name="Bus", color_idx=6, icon="fx")
    p2 = Project.from_dict(p.to_dict())
    p2.validate()
    assert p2.identity_groups == [ids]
    for t in p2.tracks:
        assert (t.name, t.color_idx, t.icon) == ("Bus", 6, "fx")


def test_old_files_migrate_to_positional_colors():
    p = _proj()
    data = p.to_dict()
    for t in data["playlist"]["tracks"]:
        del t["color_idx"]
        del t["icon"]
    del data["identity_groups"]
    p2 = Project.from_dict(data)
    p2.validate()
    assert [t.color_idx for t in p2.tracks] == [0, 1]
    assert all(t.icon == "" for t in p2.tracks)
    assert p2.identity_groups == []


def test_track_delete_cleans_groups():
    p = _proj()
    ids = [t.id for t in p.tracks]
    p.link_track_identities(ids)
    p.tracks = [t for t in p.tracks if t.id != ids[0]]
    p.validate()  # normalize_link_groups drops the singleton
    assert p.identity_groups == []


# -- StripPresentation (pure) --------------------------------------------------

def test_strip_presentation_resolves_color_and_badge():
    p = _proj()
    t = p.tracks[0]
    t.color_idx = 3
    t.icon = "drm"
    (pres,) = build_strip_presentations([t], selected_ids={t.id},
                                        latency_by_track={t.id: 128})
    assert pres.color == TRACK_COLORS[3]
    assert pres.badge_text == "[DRM]"
    assert pres.display_name == t.name
    assert pres.selected is True
    assert pres.latency_samples == 128
    geo = strip_geometry(pres)
    assert geo["accent_color"] == TRACK_COLORS[3]
    assert geo["badge_text"] == "[DRM]"
    assert geo["strip_width"] > 0


def test_strip_presentation_no_icon_no_badge():
    p = _proj()
    t = p.tracks[0]
    t.icon = ""
    (pres,) = build_strip_presentations([t])
    assert pres.badge_text == ""
    assert strip_geometry(pres)["badge_text"] == ""


def test_identity_property_is_a_view():
    p = _proj()
    t = p.tracks[0]
    ident = t.identity
    assert isinstance(ident, TrackIdentity)
    assert (ident.name, ident.color_idx, ident.icon) == \
        (t.name, t.color_idx, t.icon)


# -- widget probes (need display) ----------------------------------------------

@needs_display
def _make_mixer(root):
    from daw.ui.mixer import Mixer
    noop = lambda *a: None
    return Mixer(root, on_volume=noop, on_pan=noop, on_mute=noop,
                 on_add_effect=noop, on_effect_param=noop,
                 on_remove_effect=noop, on_add_plugin=noop,
                 on_edit_plugin=noop, on_open_gui=noop,
                 on_set_generator=noop, on_edit_generator=noop,
                 on_clear_generator=noop, on_open_generator_gui=noop)


def test_mixer_strip_uses_persistent_color():
    import tkinter as tk
    p = _proj()
    # Give the SECOND track a distinctive persistent color; the old
    # positional scheme would have painted it with index 1.
    p.tracks[1].color_idx = 6
    p.tracks[1].icon = "syn"
    root = tk.Tk()
    try:
        mixer = _make_mixer(root)
        mixer.set_project(p)
        w = mixer._strip_widgets[p.tracks[1].id]
        assert w["accent_bar"].cget("bg") == TRACK_COLORS[6]
        assert w["badge_label"].cget("text") == "[SYN]"
        assert w["name_label"].cget("text") == p.tracks[1].name
        # In-place identity refresh (no rebuild).
        p.set_track_identity(p.tracks[1].id, color_idx=2, icon="")
        mixer.set_project(p)
        assert w["accent_bar"].cget("bg") == TRACK_COLORS[2]
        assert w["badge_label"].cget("text") == ""
    finally:
        root.destroy()


@needs_display
def test_playlist_header_shows_identity_and_refreshes():
    import tkinter as tk
    from daw.ui.playlist import Playlist
    p = _proj()
    p.tracks[0].color_idx = 4
    p.tracks[0].icon = "drm"
    root = tk.Tk()
    try:
        pl = Playlist(root, lambda *a: None, lambda *a: None,
                      lambda *a: None, lambda *a: None, lambda *a: None)
        pl.pack()
        pl.set_project(p)
        headers = pl._track_headers.winfo_children()
        assert len(headers) == len(p.tracks)
        first = headers[0].winfo_children()
        chip = next(w for w in first if w.winfo_class() == "Canvas")
        assert chip.cget("bg") == TRACK_COLORS[4]
        labels = [w.cget("text") for w in first
                  if w.winfo_class() == "TLabel"]
        assert p.tracks[0].name in labels
        assert "[DRM]" in labels
        # Identity-only edit refreshes the header without a lane rebuild.
        lanes_before = dict(pl._canvases)
        p.set_track_identity(p.tracks[0].id, name="Renamed", color_idx=1)
        pl.set_project(p)
        assert pl._canvases.keys() == lanes_before.keys()
        headers = pl._track_headers.winfo_children()  # re-fetch: rebuilt
        labels = [w.cget("text")
                  for w in headers[0].winfo_children()
                  if w.winfo_class() == "TLabel"]
        assert "Renamed" in labels
    finally:
        root.destroy()


@needs_display
def test_identity_ripple_reaches_both_surfaces():
    """FL Track Mode analog: one edit, playlist header + mixer strip agree."""
    import tkinter as tk
    from daw.ui.mixer import Mixer
    from daw.ui.playlist import Playlist
    p = _proj()
    ids = [t.id for t in p.tracks]
    p.link_track_identities(ids)
    root = tk.Tk()
    try:
        mixer = _make_mixer(root)
        mixer.set_project(p)
        pl = Playlist(root, lambda *a: None, lambda *a: None,
                      lambda *a: None, lambda *a: None, lambda *a: None)
        pl.pack()
        pl.set_project(p)
        p.set_track_identity(ids[0], name="Bus", color_idx=7, icon="mix")
        mixer.set_project(p)
        pl.set_project(p)
        for tid in ids:
            assert mixer._strip_widgets[tid]["name_label"].cget(
                "text") == "Bus"
            assert mixer._strip_widgets[tid]["accent_bar"].cget(
                "bg") == TRACK_COLORS[7]
        labels = [w.cget("text")
                  for h in pl._track_headers.winfo_children()
                  for w in h.winfo_children()
                  if w.winfo_class() == "TLabel"]
        assert labels.count("Bus") == len(ids)
        assert labels.count("[MIX]") == len(ids)
    finally:
        root.destroy()
