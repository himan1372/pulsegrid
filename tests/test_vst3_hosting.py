"""Tests for VST3 plugin hosting (no GUI, no real VST3 required).

VST3 hosting is new in v0.45.0 (Philip reversed the CLAP-only decision).
These tests cover the Python-side integration: Effect factory,
validation, serialization, and graceful handling of missing plugins.
Audio-path tests need a real .vst3 and are Windows-shakedown items.
"""

import pytest

from daw.engine_bridge import EngineBridge, EngineError
from daw.project import Effect, ProjectError


def test_vst3_effect_factory():
    fx = Effect.vst3("absynth", "/fake/Absynth.vst3", {1: 0.5, 2: 0.0})
    assert fx.kind == "vst3"
    assert fx.plugin_id == "absynth"
    assert fx.plugin_path == "/fake/Absynth.vst3"
    assert fx.params == {"1": 0.5, "2": 0.0}
    fx.validate()  # must not raise


def test_vst3_validation_rejects_bad_params():
    fx = Effect.vst3("x", "/fake/x.vst3", {"1": 1.5})  # > 1.0
    with pytest.raises(ProjectError):
        fx.validate()
    fx2 = Effect.vst3("x", "/fake/x.vst3", {"1": float("nan")})
    with pytest.raises(ProjectError):
        fx2.validate()


def test_vst3_validation_rejects_empty():
    with pytest.raises(ProjectError):
        Effect.vst3("", "/fake/x.vst3", {}).validate()
    with pytest.raises(ProjectError):
        Effect.vst3("x", "", {}).validate()


def test_vst3_serialization_roundtrip():
    fx = Effect.vst3("absynth", "/fake/Absynth.vst3", {1: 0.25})
    d = fx.to_dict()
    assert d["type"] == "vst3"
    assert d["plugin_id"] == "absynth"
    fx2 = Effect.from_dict(d)
    assert fx2.kind == "vst3"
    assert fx2.params == {"1": 0.25}
    fx2.validate()


def test_vst3_engine_params():
    fx = Effect.vst3("absynth", "/fake/Absynth.vst3", {1: 0.75})
    ep = fx.engine_params()
    assert ep["type"] == "vst3"
    assert ep["params"] == {"1": 0.75}


def test_vst3_display_name():
    from daw.project import register_plugin
    register_plugin("absynth", "Absynth 5", [])
    fx = Effect.vst3("absynth", "/fake/Absynth.vst3", {})
    assert fx.display_name() == "Absynth 5"


def test_scan_vst3_no_crash():
    """Scanning must not crash when no VST3s are installed."""
    eng = EngineBridge()
    result = eng.scan_vst3_plugins()
    assert isinstance(result, list)
    for p in result:
        assert "name" in p and "path" in p
        assert p.get("format") == "vst3"


def test_check_vst3_missing_plugin_fails():
    eng = EngineBridge()
    with pytest.raises(EngineError):
        eng.check_vst3_plugin("/nonexistent/fake.vst3")


def test_vst3_generator_roundtrip():
    """VST3 generator serializes with type 'vst3' and format preserved."""
    from daw.project import Generator
    gen = Generator.vst3("absynth5", "C:/Program Files/Common Files/VST3/Absynth 5.vst3",
                         {"0": 0.5, "1": 0.25})
    assert gen.format == "vst3"
    d = gen.to_dict()
    assert d["type"] == "vst3"
    assert d["plugin_id"] == "absynth5"
    gen2 = Generator.from_dict(d)
    assert gen2.format == "vst3"
    assert gen2.params == {"0": 0.5, "1": 0.25}
    # Engine params use the vst3 type.
    ep = gen.engine_params()
    assert ep["type"] == "vst3"


def test_clap_generator_unchanged():
    """CLAP generators still serialize with type 'plugin'."""
    from daw.project import Generator
    gen = Generator.plugin("com.test.synth", "/tmp/test.clap", {"11": 0.5})
    assert gen.format == "clap"
    assert gen.to_dict()["type"] == "plugin"
    assert gen.engine_params()["type"] == "plugin"
