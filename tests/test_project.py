"""Unit tests for the Pulsegrid project model (no GUI, no audio engine)."""

import json
import os

import pytest

from daw.project import (
    FORMAT_VERSION,
    FX_DEFS,
    AutomationLane,
    AutoPoint,
    Channel,
    Clip,
    Effect,
    Note,
    Pattern,
    PlaylistTrack,
    Project,
    ProjectError,
    Send,
    blank_channels,
    empty_project,
    new_default_project,
    auto_param_spec,
    parse_auto_param,
)


def test_default_project_validates():
    p = new_default_project()
    p.validate()  # must not raise
    assert p.tempo == 128.0
    assert len(p.patterns) == 2
    assert len(p.tracks) == 2
    assert p.selected_pattern == "pattern-1"


def test_default_project_arrangement():
    p = new_default_project()
    assert p.arrangement_bars() == 8  # drums 0-8, music 4-8
    drums = p.pattern_by_id("pattern-1")
    assert any(c.notes for c in drums.channels)
    # The demo now showcases lengths: the bass has a 3-step note.
    bass = p.pattern_by_id("pattern-2").channels[3]
    assert any(n.length > 1.0 for n in bass.notes)
    # ...and the lead plays a two-note chord.
    lead = p.pattern_by_id("pattern-2").channels[4]
    assert len([n for n in lead.notes if n.start == 14.0]) == 2


def test_empty_project_has_no_notes():
    p = empty_project()
    pat = p.current_pattern()
    assert not any(c.notes for c in pat.channels)
    assert len(p.tracks) == 1


def test_rejects_bad_tempo():
    p = new_default_project()
    p.tempo = 5.0
    with pytest.raises(ProjectError):
        p.validate()


def test_rejects_unknown_instrument():
    p = new_default_project()
    p.patterns[0].channels[0].instrument = "didgeridoo"
    with pytest.raises(ProjectError):
        p.validate()


def test_rejects_note_start_past_end():
    p = new_default_project()
    p.patterns[0].channels[0].notes.append(Note(start=16.0, length=1.0, pitch=36))
    with pytest.raises(ProjectError, match="start"):
        p.validate()


def test_rejects_duplicate_channel_ids():
    p = new_default_project()
    chs = p.patterns[0].channels
    chs[1].id = chs[0].id
    with pytest.raises(ProjectError):
        p.validate()


def test_rejects_dangling_clip_pattern():
    p = new_default_project()
    p.tracks[0].clips.append(Clip(pattern_id="nope", start_beat=0, bars=1))
    with pytest.raises(ProjectError, match="unknown pattern"):
        p.validate()


def test_rejects_bad_track_gain():
    p = new_default_project()
    p.tracks[0].gain = 99.0
    with pytest.raises(ProjectError, match="gain"):
        p.validate()


def test_rejects_bad_selected_pattern():
    p = new_default_project()
    p.selected_pattern = "ghost"
    with pytest.raises(ProjectError, match="selected pattern"):
        p.validate()


def test_new_ids_do_not_collide():
    p = new_default_project()
    pid = p.new_pattern_id()
    tid = p.new_track_id()
    assert pid not in [x.id for x in p.patterns]
    assert tid not in [t.id for t in p.tracks]
    p.patterns.append(Pattern(id=pid, name="X", channels=blank_channels(pid)))
    assert p.new_pattern_id() != pid


def test_roundtrip_dict():
    p = new_default_project("Roundtrip")
    p2 = Project.from_dict(p.to_dict())
    assert p2.to_dict() == p.to_dict()


def test_v1_migration():
    v1 = {
        "format": "pulsegrid-project",
        "version": 1,
        "name": "Old Song",
        "tempo": 100.0,
        "steps": 16,
        "channels": [
            {"id": "kick", "name": "Kick", "instrument": "kick",
             "pitch": 36, "steps": [True] + [False] * 15},
        ],
    }
    p = Project.from_dict(v1)
    p.validate()
    assert p.tempo == 100.0
    assert len(p.patterns) == 1
    ch = p.patterns[0].channels[0]
    assert [(n.start, n.length, n.pitch) for n in ch.notes] == [(0.0, 1.0, 36)]
    assert len(p.tracks) == 1
    clip = p.tracks[0].clips[0]
    assert clip.pattern_id == "pattern-1"
    assert (clip.start_beat, clip.bars) == (0, 1)
    # And it re-saves as the current version (notes filled in).
    assert p.to_dict()["version"] == FORMAT_VERSION


def test_v2_migration_fills_note_pitch():
    """v2 channels stored one pitch; v5 loads fill 1-step notes with it."""
    v2 = {
        "format": "pulsegrid-project",
        "version": 2,
        "name": "v2 Song",
        "tempo": 120.0,
        "patterns": [
            {"id": "pattern-1", "name": "Drums", "steps": 16, "channels": [
                {"id": "pattern-1-kick", "name": "Kick", "instrument": "kick",
                 "pitch": 36, "steps": [True] + [False] * 15},
            ]},
        ],
        "selected_pattern": "pattern-1",
        "playlist": {"tracks": [
            {"id": "track-1", "name": "Drums", "gain": 1.0, "pan": 0.0,
             "clips": [{"pattern": "pattern-1", "start_bar": 1, "bars": 2}]},
        ]},
    }
    p = Project.from_dict(v2)
    p.validate()
    ch = p.patterns[0].channels[0]
    assert [(n.start, n.length, n.pitch, n.vel) for n in ch.notes] == [
        (0.0, 1.0, 36, 0.9)]
    assert ch.pitch == 36  # kept as the default for new notes
    clip = p.tracks[0].clips[0]
    assert (clip.start_beat, clip.bars) == (4, 2)  # v4: bar starts -> beats
    assert p.to_dict()["version"] == FORMAT_VERSION


def test_v4_migration_to_v5():
    """v4 files (per-step arrays, beat clips) load as v5 note lists."""
    v4 = {
        "format": "pulsegrid-project",
        "version": 4,
        "name": "v4 Song",
        "tempo": 120.0,
        "patterns": [
            {"id": "pattern-1", "name": "Drums", "steps": 16, "channels": [
                {"id": "pattern-1-kick", "name": "Kick", "instrument": "kick",
                 "pitch": 36,
                 "steps": [True, False, False, False] + [False] * 12,
                 "note_pitch": [36, 36, 36, 40] + [36] * 12,
                 "note_vel": [1.0, 0.9, 0.9, 0.5] + [0.9] * 12},
            ]},
        ],
        "selected_pattern": "pattern-1",
        "playlist": {"tracks": [
            {"id": "track-1", "name": "Drums", "gain": 1.0, "pan": 0.0,
             "clips": [{"pattern": "pattern-1", "start_beat": 0, "bars": 1}]},
        ]},
    }
    p = Project.from_dict(v4)
    p.validate()
    ch = p.patterns[0].channels[0]
    # Only active steps become notes; pitch/velocity preserved.
    assert [(n.start, n.length, n.pitch, n.vel) for n in ch.notes] == [
        (0.0, 1.0, 36, 1.0)]
    assert p.to_dict()["version"] == FORMAT_VERSION


def test_v5_note_roundtrip():
    p = new_default_project()
    ch = p.patterns[0].channels[0]
    ch.notes.append(Note(start=2.0, length=3.0, pitch=40, vel=0.5))
    p2 = Project.from_dict(p.to_dict())
    n = p2.patterns[0].channels[0].notes[-1]
    assert (n.start, n.length, n.pitch, n.vel) == (2.0, 3.0, 40, 0.5)
    assert p2.to_dict() == p.to_dict()


def test_rejects_bad_note():
    p = new_default_project()
    ch = p.patterns[0].channels[0]
    ch.notes.append(Note(start=0.0, length=0.0, pitch=36))
    with pytest.raises(ProjectError, match="length"):
        p.validate()
    ch.notes[-1] = Note(start=99.0, length=1.0, pitch=36)
    with pytest.raises(ProjectError, match="start"):
        p.validate()
    ch.notes[-1] = Note(start=0.0, length=1.0, pitch=200)
    with pytest.raises(ProjectError, match="pitch"):
        p.validate()


def test_rejects_negative_start_beat():
    p = new_default_project()
    p.tracks[0].clips[0].start_beat = -4
    with pytest.raises(ProjectError, match="start_beat"):
        p.validate()


def test_v5_note_pitch_roundtrip():
    p = new_default_project()
    ch = p.patterns[0].channels[3]  # bass
    ch.notes.append(Note(start=0.0, length=2.0, pitch=48, vel=0.7))
    p2 = Project.from_dict(p.to_dict())
    ch2 = p2.patterns[0].channels[3]
    n = ch2.notes[-1]
    assert (n.start, n.length, n.pitch, n.vel) == (0.0, 2.0, 48, 0.7)
    assert p2.to_dict() == p.to_dict()


def test_rejects_non_note_entries():
    p = new_default_project()
    ch = p.patterns[0].channels[0]
    ch.notes.append("not-a-note")
    with pytest.raises(ProjectError, match="must be notes"):
        p.validate()


def test_rejects_bad_note_pitch_value():
    p = new_default_project()
    ch = p.patterns[0].channels[0]
    ch.notes.append(Note(start=0.0, length=1.0, pitch=200))
    with pytest.raises(ProjectError, match="MIDI"):
        p.validate()


def test_rejects_newer_version():
    p = new_default_project()
    d = p.to_dict()
    d["version"] = FORMAT_VERSION + 99
    with pytest.raises(ProjectError, match="newer"):
        Project.from_dict(d)


def test_rejects_wrong_format_id():
    d = new_default_project().to_dict()
    d["format"] = "something-else"
    with pytest.raises(ProjectError):
        Project.from_dict(d)


def test_save_load_roundtrip(tmp_path):
    p = new_default_project("Disk")
    path = str(tmp_path / "song.pulsegrid.json")
    p.save(path)
    assert os.path.exists(path)
    p2 = Project.load(path)
    assert p2.to_dict() == p.to_dict()


def test_corrupt_json_backed_up(tmp_path):
    bad = tmp_path / "bad.pulsegrid.json"
    bad.write_text("{nope")
    with pytest.raises(ProjectError, match=r"\.bak"):
        Project.load(str(bad))
    backups = list(tmp_path.glob("*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text() == "{nope"


def test_invalid_schema_backed_up(tmp_path):
    bad = tmp_path / "bad2.pulsegrid.json"
    bad.write_text(json.dumps({"format": "pulsegrid-project", "version": 2}))
    with pytest.raises(ProjectError):
        Project.load(str(bad))
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_missing_file():
    with pytest.raises(ProjectError, match="not found"):
        Project.load("/tmp/does-not-exist-12345.pulsegrid.json")


def test_snapshot_is_independent():
    p = new_default_project()
    s = p.snapshot()
    ch = s.patterns[0].channels[0]
    ch.notes.append(Note(start=0.0, length=1.0, pitch=36))
    assert len(ch.notes) != len(p.patterns[0].channels[0].notes)


def test_channel_from_dict_missing_field():
    with pytest.raises(ProjectError):
        Channel.from_dict({"id": "x"})


def test_pattern_bars_ceil():
    pat = Pattern(id="p", name="P", steps=20, channels=blank_channels("p", 20))
    assert pat.bars() == 2


def _lane(track_id="track-1", param="gain", points=((0.0, 0.5), (8.0, 1.5))):
    return AutomationLane(
        id="auto-1", track_id=track_id, param=param,
        points=[AutoPoint(b, v) for b, v in points])


def test_v5_migration_has_no_automation():
    p = new_default_project()
    d = p.to_dict()
    d["version"] = 5
    del d["automation"]
    p2 = Project.from_dict(d)
    p2.validate()
    assert p2.automation == []
    assert p2.to_dict()["version"] == FORMAT_VERSION


def test_v6_automation_roundtrip():
    p = new_default_project()
    p.automation.append(_lane())
    p2 = Project.from_dict(p.to_dict())
    assert p2.to_dict() == p.to_dict()
    lane = p2.automation[0]
    assert (lane.track_id, lane.param) == ("track-1", "gain")
    assert [(pt.beat, pt.value) for pt in lane.points] == [(0.0, 0.5), (8.0, 1.5)]


def test_automation_points_sorted_on_load():
    p = new_default_project()
    p.automation.append(_lane(points=((8.0, 1.5), (0.0, 0.5))))
    p2 = Project.from_dict(p.to_dict())
    assert [pt.beat for pt in p2.automation[0].points] == [0.0, 8.0]


def test_rejects_bad_automation():
    p = new_default_project()
    # Unknown track.
    p.automation.append(_lane(track_id="nope"))
    with pytest.raises(ProjectError, match="unknown track"):
        p.validate()
    # Unknown param.
    p.automation = [_lane(param="reverb")]
    with pytest.raises(ProjectError, match="unknown automation param"):
        p.validate()
    # FX index out of range (track-1 has no effects).
    p.automation = [_lane(param="fx0.cutoff")]
    with pytest.raises(ProjectError, match="no effect"):
        p.validate()
    # Value out of range.
    p.automation = [_lane(points=((0.0, 9.0),))]
    with pytest.raises(ProjectError, match="out of range"):
        p.validate()
    # Beat past the arrangement end (8 bars -> 32 beats).
    p.automation = [_lane(points=((0.0, 1.0), (40.0, 1.0)))]
    with pytest.raises(ProjectError, match="outside arrangement"):
        p.validate()
    # Duplicate lane ids.
    p.automation = [_lane(), _lane()]
    with pytest.raises(ProjectError, match="duplicate automation"):
        p.validate()


def test_fx_automation_lane_validates_against_kind():
    p = new_default_project()
    track = p.tracks[0]
    track.effects.append(Effect.default("filter"))
    p.automation.append(_lane(track_id=track.id, param="fx0.cutoff",
                              points=((0.0, 800.0), (8.0, 8000.0))))
    p.validate()
    # Wrong kind for the effect: filter has no "amount".
    p.automation = [_lane(track_id=track.id, param="fx0.amount",
                          points=((0.0, 50.0),))]
    with pytest.raises(ProjectError, match="has no 'amount'"):
        p.validate()


def test_lanes_for_and_new_id():
    p = new_default_project()
    assert p.lanes_for("track-1", "gain") == []
    p.automation.append(_lane())
    assert len(p.lanes_for("track-1", "gain")) == 1
    assert p.new_automation_id() == "auto-2"


# -- v0.34.0 automation curve shapes (research topic 30) ---------------------

def test_curve_interp_defaults():
    lane = _lane()
    assert lane.interp == "linear"
    assert lane.tension == 0.5


def test_curve_interp_validation():
    from daw.project import ProjectError
    p = new_default_project()
    track = p.tracks[0]
    lane = _lane()
    lane.interp = "smooth"
    lane.tension = 0.75
    lane.validate(track, 64.0)
    for bad in [dict(interp="bogus"), dict(tension=-0.1),
                dict(tension=1.5)]:
        with pytest.raises(ProjectError):
            bad_lane = _lane()
            for k, v in bad.items():
                setattr(bad_lane, k, v)
            bad_lane.validate(track, 64.0)


def test_curve_interp_round_trip():
    lane = _lane()
    lane.interp = "wave"
    lane.tension = 0.3
    d = lane.to_dict()
    assert d["interp"] == "wave"
    assert d["tension"] == 0.3
    lane2 = AutomationLane.from_dict(d)
    assert (lane2.interp, lane2.tension) == ("wave", 0.3)


def test_curve_interp_missing_keys_default():
    # Additive fields: old lane dicts load as linear / 0.5.
    lane = AutomationLane.from_dict({
        "id": "a1", "track": "t1", "param": "gain",
        "points": [{"beat": 0.0, "value": 1.0}],
    })
    assert (lane.interp, lane.tension) == ("linear", 0.5)


# -- exclusive output routing (FL "route to this track only") -------------

def _two_track_project():
    p = new_default_project()
    while len(p.tracks) < 2:
        p.tracks.append(PlaylistTrack(id=f"track-{len(p.tracks)+1}",
                                      name=f"Track {len(p.tracks)+1}"))
    return p


def test_output_defaults_to_master():
    t = PlaylistTrack(id="t1", name="T1")
    assert t.output == "master"
    assert t.to_dict()["output"] == "master"


def test_output_migrates_from_old_projects():
    t = PlaylistTrack(id="t1", name="T1")
    d = t.to_dict()
    del d["output"]
    t2 = PlaylistTrack.from_dict(d)
    assert t2.output == "master"


def test_output_roundtrip():
    t = PlaylistTrack(id="t1", name="T1", output="track-2")
    assert PlaylistTrack.from_dict(t.to_dict()).output == "track-2"


def test_output_validates_target_and_self():
    p = _two_track_project()
    a, b = p.tracks[0], p.tracks[1]
    a.output = b.id
    p.validate()  # ok
    a.output = "nope"
    with pytest.raises(ProjectError, match="unknown output"):
        p.validate()
    a.output = a.id
    with pytest.raises(ProjectError, match="cannot route to itself"):
        p.validate()


def test_output_cycle_rejected():
    p = _two_track_project()
    a, b = p.tracks[0], p.tracks[1]
    a.output = b.id
    b.output = a.id
    with pytest.raises(ProjectError, match="routing cycle"):
        p.validate()


def test_output_send_mixed_cycle_rejected():
    # A --send--> B --output--> A is a cycle too.
    p = _two_track_project()
    a, b = p.tracks[0], p.tracks[1]
    from daw.project import Send
    a.sends.append(Send(to_track_id=b.id, amount=0.5))
    b.output = a.id
    with pytest.raises(ProjectError, match="routing cycle"):
        p.validate()


def test_would_create_output_cycle():
    p = _two_track_project()
    a, b = p.tracks[0], p.tracks[1]
    assert p.would_create_output_cycle(a.id, b.id) is False
    a.output = b.id
    # Re-setting the same output is not a cycle.
    assert p.would_create_output_cycle(a.id, b.id) is False
    # But routing b back to a would close a loop.
    assert p.would_create_output_cycle(b.id, a.id) is True


def test_bridge_dict_carries_output():
    from daw.engine_bridge import EngineBridge
    p = _two_track_project()
    p.tracks[0].output = p.tracks[1].id
    d = EngineBridge.arrangement_dict(p)
    assert d["tracks"][0]["output"] == p.tracks[1].id
    assert d["tracks"][1]["output"] == "master"


def _plugin_effect():
    from daw.project import Effect
    return Effect(kind="plugin", params={"7": 1.5},
                  plugin_id="org.pulsegrid.test-gain",
                  plugin_path="/tmp/x.clap",
                  state_base64="UFNUZXhhbXBsZQ==")


def test_effect_state_blob_round_trips():
    from daw.project import Effect
    fx = _plugin_effect()
    fx.validate()
    d = fx.to_dict()
    assert d["state_base64"] == "UFNUZXhhbXBsZQ=="
    fx2 = Effect.from_dict(d)
    assert fx2.state_base64 == fx.state_base64
    assert fx2.params == {"7": 1.5}
    # The engine dict carries the blob for the bridge.
    eng = fx.engine_params()
    assert eng["state_base64"] == "UFNUZXhhbXBsZQ=="


def test_effect_state_blob_omitted_when_empty():
    from daw.project import Effect
    fx = Effect(kind="plugin", params={},
                plugin_id="org.pulsegrid.test-gain",
                plugin_path="/tmp/x.clap")
    assert "state_base64" not in fx.to_dict()
    assert Effect.from_dict(fx.to_dict()).state_base64 == ""


def test_effect_rejects_bad_state_base64():
    from daw.project import Effect, ProjectError
    fx = _plugin_effect()
    fx.state_base64 = "!!! not base64 !!!"
    try:
        fx.validate()
    except ProjectError:
        pass
    else:
        raise AssertionError("invalid base64 should fail validation")


def test_generator_state_blob_round_trips():
    from daw.project import Generator
    g = Generator(plugin_id="org.pulsegrid.test-synth",
                  plugin_path="/tmp/y.clap",
                  params={"11": 0.5},
                  state_base64="UFNUZXhhbXBsZQ==")
    g.validate()
    d = g.to_dict()
    assert d["state_base64"] == "UFNUZXhhbXBsZQ=="
    g2 = Generator.from_dict(d)
    assert g2.state_base64 == g.state_base64
    assert g.engine_params()["state_base64"] == "UFNUZXhhbXBsZQ=="


def test_generator_rejects_bad_state_base64():
    from daw.project import Generator, ProjectError
    g = Generator(plugin_id="x", plugin_path="/tmp/y.clap",
                  state_base64="***")
    try:
        g.validate()
    except ProjectError:
        pass
    else:
        raise AssertionError("invalid base64 should fail validation")


def test_bridge_dict_carries_state_blobs():
    from daw.engine_bridge import EngineBridge
    from daw.project import empty_project
    p = empty_project()
    track = p.tracks[0]
    track.effects.append(_plugin_effect())
    d = EngineBridge.arrangement_dict(p)
    fx = d["tracks"][0]["effects"][0]
    assert fx["type"] == "plugin"
    assert fx["state_base64"] == "UFNUZXhhbXBsZQ=="


# -- v0.25.0 routing options (per-send tap/pan/sidechain, send lanes) --

def _two_track_parts():
    p = empty_project()
    p.tracks.append(PlaylistTrack(id="track-2", name="Track 2"))
    return p, p.tracks[0], p.tracks[1]


def test_send_defaults_are_post_centered_audible():
    s = Send(to_track_id="t2")
    assert s.tap == "post"
    assert s.pan == 0.0
    assert s.sidechain is False
    assert s.amount == 0.5


def test_send_routing_options_round_trip():
    s = Send(to_track_id="t2", amount=0.7, tap="pre", pan=-0.5,
             sidechain=True)
    d = s.to_dict()
    assert d == {"to": "t2", "amount": 0.7, "tap": "pre", "pan": -0.5,
                 "sidechain": True}
    s2 = Send.from_dict(d)
    assert s2.tap == "pre" and s2.pan == -0.5 and s2.sidechain is True


def test_send_validation_rejects_bad_options():
    p, t0, t1 = _two_track_parts()
    ids = {t.id for t in p.tracks}
    for bad in [dict(tap="side"), dict(pan=1.5), dict(pan=-1.5),
                dict(amount=1.5)]:
        with pytest.raises(ProjectError):
            Send(to_track_id=t1.id, **bad).validate(ids, t0.id)
    # Unknown tap string in stored data is rejected on load.
    with pytest.raises(ProjectError):
        Send.from_dict({"to": t1.id, "tap": "side"})


def test_v11_project_migrates_sends_with_defaults():
    # v11 sends carry only to/amount; v12 load fills tap/pan/sidechain.
    p, t0, t1 = _two_track_parts()
    t0.sends.append(Send(to_track_id=t1.id, amount=0.4))
    d = p.to_dict()
    d["version"] = 11
    for tr in d["playlist"]["tracks"]:
        for snd in tr.get("sends", []):
            for k in ("tap", "pan", "sidechain"):
                snd.pop(k, None)
    p2 = Project.from_dict(d)
    p2.validate()
    assert len(p2.tracks[0].sends) == 1
    s = p2.tracks[0].sends[0]
    assert (s.tap, s.pan, s.sidechain) == ("post", 0.0, False)
    assert s.amount == pytest.approx(0.4)


def test_send_lane_param_parses_and_specs():
    p, t0, t1 = _two_track_parts()
    t0.sends.append(Send(to_track_id=t1.id, amount=0.5))
    kind, idx, dest = parse_auto_param(f"send.{t1.id}.amount")
    assert (kind, idx, dest) == ("send", -1, t1.id)
    label, lo, hi, _unit = auto_param_spec(f"send.{t1.id}.amount", t0)
    assert label == f"Send -> {t1.id}"
    assert (lo, hi) == (0.0, 1.0)


def test_send_lane_requires_existing_route():
    p, t0, t1 = _two_track_parts()
    lane = AutomationLane(id="a1", track_id=t0.id,
                          param=f"send.{t1.id}.amount",
                          points=[AutoPoint(0.0, 0.0), AutoPoint(4.0, 1.0)])
    # No send to t1 yet: lane is invalid.
    with pytest.raises(ProjectError):
        lane.validate(t0, 8.0)
    # After adding the route it validates.
    t0.sends.append(Send(to_track_id=t1.id, amount=0.5))
    lane.validate(t0, 8.0)


def test_ducker_fx_defs_and_engine_units():
    assert "ducker" in FX_DEFS
    fx = Effect.default("ducker")
    params = fx.engine_params()
    assert params["type"] == "ducker"
    # Threshold is % in display units, fraction in engine units.
    assert params["threshold"] == pytest.approx(fx.params["threshold"] / 100)
    assert params["ratio"] == pytest.approx(fx.params["ratio"])
    assert params["attack_ms"] == pytest.approx(fx.params["attack_ms"])
    assert params["release_ms"] == pytest.approx(fx.params["release_ms"])
    # Ducker params are automatable like any native FX param.
    p, t0, _t1 = _two_track_parts()
    t0.effects.append(fx)
    label, lo, hi, _unit = auto_param_spec("fx0.threshold", t0)
    assert lo == 1.0 and hi == 100.0 and "Threshold" in label


# -- v0.27.0 pitch shift (time-stretching research: pitch row of the
# -- four-operation table; dual-head delay-line harmonizer) --

def test_pitchshift_fx_defs_and_engine_units():
    from daw.project import FX_DEFS, FX_NAMES, Effect, auto_param_spec
    assert "pitchshift" in FX_DEFS
    assert FX_NAMES["pitchshift"] == "Pitch Shift"
    fx = Effect.default("pitchshift")
    assert fx.params["semitones"] == 0.0
    assert fx.params["pitch_mix"] == 100.0
    params = fx.engine_params()
    assert params["type"] == "pitchshift"
    # Semitones pass through as-is; mix % -> fraction.
    assert params["semitones"] == pytest.approx(0.0)
    assert params["pitch_mix"] == pytest.approx(1.0)
    fx.params["semitones"] = 7.0
    fx.params["pitch_mix"] = 50.0
    fx.validate()
    params = fx.engine_params()
    assert params["semitones"] == pytest.approx(7.0)
    assert params["pitch_mix"] == pytest.approx(0.5)
    # Both params are automatable.
    p, t0, _t1 = _two_track_parts()
    t0.effects.append(fx)
    label, lo, hi, _unit = auto_param_spec("fx0.semitones", t0)
    assert lo == -12.0 and hi == 12.0 and "Pitch" in label
    label, lo, hi, _unit = auto_param_spec("fx0.pitch_mix", t0)
    assert lo == 0.0 and hi == 100.0 and "Mix" in label


def test_pitchshift_rejects_out_of_range():
    from daw.project import Effect, ProjectError
    fx = Effect.default("pitchshift")
    fx.params["semitones"] = 13.0
    with pytest.raises(ProjectError):
        fx.validate()
    fx.params["semitones"] = -12.0
    fx.params["pitch_mix"] = 101.0
    with pytest.raises(ProjectError):
        fx.validate()


def test_pitchshift_serializes_in_project():
    # New effect kind needs no format bump: it serializes as
    # {"type": "pitchshift", "params": {...}} like other native FX.
    from daw.project import Effect
    fx = Effect.default("pitchshift")
    fx.params["semitones"] = -5.0
    d = fx.to_dict()
    assert d["type"] == "pitchshift"
    assert d["params"]["semitones"] == -5.0
    fx2 = Effect.from_dict(d)
    fx2.validate()
    assert fx2.params["semitones"] == -5.0


# -- v0.26.0 velocity tracking (FL 3xOsc Volume Tracking model) --

def test_vel_track_defaults_off():
    from daw.project import PlaylistTrack
    t = PlaylistTrack(id="t1", name="T1")
    assert t.vel_track == 0.0
    assert t.vel_track_mid == 0.5


# -- v0.32.0 keyboard tracking (FL Channel Keyboard Tracker model) --

def test_key_track_defaults_off():
    from daw.project import PlaylistTrack
    t = PlaylistTrack(id="t1", name="T1")
    assert t.key_track == 0.0
    assert t.key_track_mid == 60.0


def test_key_track_validation():
    from daw.project import PlaylistTrack, ProjectError
    t = PlaylistTrack(id="t1", name="T1", key_track=0.5, key_track_mid=69.0)
    t.validate(set(), set())
    for bad in [dict(key_track=1.5), dict(key_track=-1.5),
                dict(key_track_mid=128.0), dict(key_track_mid=-0.1)]:
        with pytest.raises(ProjectError):
            PlaylistTrack(id="t1", name="T1", **bad).validate(set(), set())


def test_key_track_round_trip():
    from daw.project import PlaylistTrack
    t = PlaylistTrack(id="t1", name="T1", key_track=-0.75, key_track_mid=72.0)
    d = t.to_dict()
    assert d["key_track"] == -0.75
    assert d["key_track_mid"] == 72.0
    t2 = PlaylistTrack.from_dict(d)
    assert (t2.key_track, t2.key_track_mid) == (-0.75, 72.0)


def test_key_track_missing_keys_default():
    # Additive field: old project dicts without the keys load as off/C4.
    from daw.project import PlaylistTrack
    t = PlaylistTrack.from_dict({"id": "t1"})
    assert (t.key_track, t.key_track_mid) == (0.0, 60.0)


def test_vel_track_validation():
    from daw.project import PlaylistTrack, ProjectError
    t = PlaylistTrack(id="t1", name="T1", vel_track=0.5, vel_track_mid=0.4)
    t.validate(set(), set())
    for bad in [dict(vel_track=1.5), dict(vel_track=-1.5),
                dict(vel_track_mid=1.5), dict(vel_track_mid=-0.1)]:
        with pytest.raises(ProjectError):
            PlaylistTrack(id="t1", name="T1", **bad).validate(set(), set())


def test_vel_track_round_trip():
    from daw.project import PlaylistTrack
    t = PlaylistTrack(id="t1", name="T1", vel_track=-0.75, vel_track_mid=0.6)
    d = t.to_dict()
    assert d["vel_track"] == -0.75
    assert d["vel_track_mid"] == 0.6
    t2 = PlaylistTrack.from_dict(d)
    assert (t2.vel_track, t2.vel_track_mid) == (-0.75, 0.6)


def test_v12_project_migrates_vel_track_with_defaults():
    p = new_default_project()
    d = p.to_dict()
    d["version"] = 12
    for tr in d["playlist"]["tracks"]:
        tr.pop("vel_track", None)
        tr.pop("vel_track_mid", None)
    p2 = Project.from_dict(d)
    p2.validate()
    for t in p2.tracks:
        assert (t.vel_track, t.vel_track_mid) == (0.0, 0.5)


# -- v0.29.0 per-note panning (FL Studio note.pan model) --

def test_note_pan_defaults_center():
    from daw.project import Note
    n = Note(start=0.0, length=1.0, pitch=60)
    assert n.pan == 0.5
    n.validate(16)


def test_note_pan_validation():
    from daw.project import Note, ProjectError
    Note(start=0.0, length=1.0, pitch=60, pan=0.0).validate(16)
    Note(start=0.0, length=1.0, pitch=60, pan=1.0).validate(16)
    for bad in (-0.1, 1.1, float("nan")):
        with pytest.raises(ProjectError):
            Note(start=0.0, length=1.0, pitch=60, pan=bad).validate(16)


def test_note_pan_round_trip():
    from daw.project import Note
    n = Note(start=2.5, length=0.5, pitch=69, vel=0.9, pan=0.25)
    d = n.to_dict()
    assert d["pan"] == 0.25
    n2 = Note.from_dict(d)
    n2.validate(16)
    assert n2.pan == 0.25


def test_v13_migration_defaults_note_pan_center():
    """v13 files have no note 'pan' key -> 0.5 center (additive v14)."""
    from daw.project import Project, FORMAT_VERSION
    from daw.project import new_default_project
    p = new_default_project()
    d = p.to_dict()
    d["version"] = 13
    for pat in d["patterns"]:
        for ch in pat["channels"]:
            for n in ch["notes"]:
                n.pop("pan", None)
    p2 = Project.from_dict(d)
    p2.validate()
    for pat in p2.patterns:
        for ch in pat.channels:
            for n in ch.notes:
                assert n.pan == 0.5
    assert p2.to_dict()["version"] == FORMAT_VERSION


def test_v14_migrates_to_v15_additively():
    from daw.project import FORMAT_VERSION, Project
    p = empty_project()
    d = p.to_dict()
    d["version"] = 14
    # v14 dicts have no samples / audio_clips keys.
    d.pop("samples", None)
    for t in d["playlist"]["tracks"]:
        t.pop("audio_clips", None)
    p2 = Project.from_dict(d)
    p2.validate()
    assert p2.samples == []
    assert all(t.audio_clips == [] for t in p2.tracks)
    assert p2.to_dict()["version"] == FORMAT_VERSION


def test_v15_sample_and_clip_round_trip():
    from daw.project import AudioClip, Project, SampleAsset
    p = empty_project()
    p.samples.append(SampleAsset(id="s1", path="drums/kick.wav",
                                 name="kick.wav"))
    p.tracks[0].audio_clips.append(AudioClip(
        id="c1", asset_id="s1", start_beat=4.0, length_beats=8.0,
        start_offset_beats=1.0, gain=0.8, pan=0.25,
        pitch_semitones=-12.0, fine_cents=50.0, reverse=True, muted=True))
    p.validate()
    d = p.to_dict()
    assert d["version"] == 16
    assert d["samples"] == [{"id": "s1", "path": "drums/kick.wav",
                             "name": "kick.wav"}]
    p2 = Project.from_dict(d)
    p2.validate()
    c = p2.tracks[0].audio_clips[0]
    assert (c.asset_id, c.start_beat, c.length_beats) == ("s1", 4.0, 8.0)
    assert c.reverse is True and c.muted is True
    assert c.pitch_semitones == -12.0 and c.fine_cents == 50.0


def test_audio_clip_validation():
    import pytest as _pytest
    from daw.project import AudioClip, ProjectError, empty_project
    p = empty_project()
    p.samples.append(__import__("daw.project", fromlist=["SampleAsset"])
                     .SampleAsset(id="s1", path="x.wav"))
    good = dict(id="c1", asset_id="s1", start_beat=0.0, length_beats=4.0)
    AudioClip(**good).validate({"s1"})
    bad_cases = [
        dict(asset_id="ghost"),          # unknown sample
        dict(start_beat=-1.0),           # negative start
        dict(length_beats=0.0),          # zero length
        dict(length_beats=5000.0),       # too long
        dict(gain=2.5),                  # gain range
        dict(pan=1.5),                   # pan range (0..1 Python convention)
        dict(pitch_semitones=49.0),      # pitch range
        dict(fine_cents=101.0),          # fine range
    ]
    for bad in bad_cases:
        kw = dict(good)
        kw.update(bad)
        with _pytest.raises(ProjectError):
            AudioClip(**kw).validate({"s1"})
    # Unknown asset is also caught at project level.
    p.tracks[0].audio_clips.append(AudioClip(**{**good, "asset_id": "ghost"}))
    with _pytest.raises(ProjectError):
        p.validate()


def test_sample_asset_path_resolution(tmp_path):
    from daw.project import SampleAsset
    a = SampleAsset(id="s1", path="drums/kick.wav")
    assert a.resolve("/proj") == os.path.join("/proj", "drums/kick.wav")
    b = SampleAsset(id="s2", path="/abs/kick.wav")
    assert b.resolve("/proj") == "/abs/kick.wav"


def test_arrangement_bars_includes_audio_clips():
    from daw.project import AudioClip, SampleAsset, empty_project
    p = empty_project()
    p.samples.append(SampleAsset(id="s1", path="x.wav"))
    assert p.arrangement_bars() == 4
    p.tracks[0].audio_clips.append(AudioClip(
        id="c1", asset_id="s1", start_beat=20.0, length_beats=8.0))
    # 28 beats -> 7 bars.
    assert p.arrangement_bars() == 7


def test_v16_modulator_round_trip():
    """v16: modulators serialize with assignments and survive round-trip."""
    from daw.project import ModAssignment, Modulator, empty_project
    p = empty_project()
    mod = Modulator(
        id="mod1", name="Filter Sweep",
        nodes=[[0.0, 0.0], [2.0, 1.0], [4.0, 0.0]],
        loop_enabled=True, length_bars=1.0, rate_mult=1.0,
        assignments=[ModAssignment(
            target_kind="fx", target_index=0, param_id=5,
            amount=0.8, polarity="bipolar",
            param_min=0.0, param_max=1.0)])
    p.tracks[0].modulators.append(mod)
    p.validate()
    d = p.to_dict()
    assert d["version"] == 16
    assert len(d["playlist"]["tracks"][0]["modulators"]) == 1
    md = d["playlist"]["tracks"][0]["modulators"][0]
    assert md["id"] == "mod1"
    assert md["nodes"] == [[0.0, 0.0], [2.0, 1.0], [4.0, 0.0]]
    assert md["assignments"][0]["polarity"] == "bipolar"
    # Round-trip
    from daw.project import Project
    p2 = Project.from_dict(d)
    p2.validate()
    m2 = p2.tracks[0].modulators[0]
    assert m2.name == "Filter Sweep"
    assert len(m2.assignments) == 1
    assert m2.assignments[0].amount == 0.8
    # Engine params
    ep = m2.engine_params()
    assert ep["length_bars"] == 1.0
    assert ep["assignments"][0]["target_kind"] == "fx"


def test_modulator_validation():
    """Modulator validation rejects bad data."""
    from daw.project import ModAssignment, Modulator, ProjectError
    import pytest
    # Bad polarity
    with pytest.raises(ProjectError):
        ModAssignment(polarity="sideways").validate()
    # Bad amount
    with pytest.raises(ProjectError):
        ModAssignment(amount=1.5).validate()
    # Bad node value
    with pytest.raises(ProjectError):
        Modulator(id="m", nodes=[[0.0, 1.5]]).validate()
    # Missing id
    with pytest.raises(ProjectError):
        Modulator(id="").validate()
