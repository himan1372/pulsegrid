"""Audio output device selection UI (v0.42.0).

Settings panel: driver/device dropdowns, Refresh, and the active-output
status line. Widget tests need an X11 display (run under xvfb-run).
"""

import os

import pytest

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="settings widget tests need an X11 display (run under xvfb-run)",
)

FAKE_DEVICES = [
    {"host_id": "ALSA", "host_name": "ALSA",
     "device_name": "default", "is_default": True},
    {"host_id": "ALSA", "host_name": "ALSA",
     "device_name": "HDA Intel PCH", "is_default": False},
    {"host_id": "JACK", "host_name": "JACK",
     "device_name": "system", "is_default": True},
]


@pytest.fixture
def panel():
    import tkinter as tk

    from daw.ui.settings import Settings

    calls = []

    root = tk.Tk()
    try:
        s = Settings(
            root,
            initial_scale=1.0, on_scale=lambda f: None,
            on_reset_layout=lambda: None,
            get_confirm=lambda: True, on_confirm=lambda v: None,
            get_backend=lambda: "null sink", get_config_path=lambda: "/tmp/x.json",
            get_refresh=lambda: "medium", on_refresh=lambda m: None,
            get_plugin_dir=lambda: "", on_plugin_dir=lambda p: None,
            get_multithreaded=lambda: True, on_multithreaded=lambda v: None,
            get_audio_devices=lambda: list(FAKE_DEVICES),
            get_audio_selection=lambda: (None, None),
            on_audio_output=lambda h, d: calls.append((h, d)),
            get_buffer_frames=lambda: None,
            on_buffer_frames=lambda f: calls.append(("buffer", f)),
        )
        s.pack(fill="both", expand=True)
        root.update()
        yield s, calls
    finally:
        root.destroy()


@needs_display
def test_dropdowns_populated(panel):
    s, _calls = panel
    assert "System default" in list(s._host_combo["values"])
    assert "ALSA" in list(s._host_combo["values"])
    assert "JACK" in list(s._host_combo["values"])
    assert s._host_var.get() == "System default"
    assert s._device_var.get() == "System default"


@needs_display
def test_host_choice_applies_default_device(panel):
    s, calls = panel
    s._host_var.set("ALSA")
    s._audio_host_chosen()
    # Picking a driver auto-selects that driver's default device.
    assert calls[-1] == ("ALSA", "default")
    assert s._device_var.get() == "default (default)"


@needs_display
def test_device_choice_applies(panel):
    s, calls = panel
    s._host_var.set("ALSA")
    s._audio_host_chosen()
    calls.clear()
    s._device_var.set("HDA Intel PCH")
    s._audio_device_chosen()
    assert calls[-1] == ("ALSA", "HDA Intel PCH")


@needs_display
def test_back_to_system_default(panel):
    s, calls = panel
    s._host_var.set("ALSA")
    s._audio_host_chosen()
    calls.clear()
    s._host_var.set("System default")
    s._audio_host_chosen()
    assert calls[-1] == (None, None)


@needs_display
def test_sync_selection_restores_dropdowns(panel):
    s, _calls = panel
    s.sync_audio_selection(("JACK", "system"))
    assert s._host_var.get() == "JACK"
    assert s._device_var.get() == "system (default)"


@needs_display
def test_empty_device_list_is_honest(panel):
    import tkinter as tk

    from daw.ui.settings import Settings

    root = tk.Tk()
    try:
        s = Settings(
            root,
            initial_scale=1.0, on_scale=lambda f: None,
            on_reset_layout=lambda: None,
            get_confirm=lambda: True, on_confirm=lambda v: None,
            get_backend=lambda: "null sink", get_config_path=lambda: "/tmp/x.json",
            get_refresh=lambda: "medium", on_refresh=lambda m: None,
            get_plugin_dir=lambda: "", on_plugin_dir=lambda p: None,
            get_multithreaded=lambda: True, on_multithreaded=lambda v: None,
            get_audio_devices=lambda: [],
            get_audio_selection=lambda: (None, None),
            on_audio_output=lambda h, d: None,
            get_buffer_frames=lambda: None,
            on_buffer_frames=lambda f: None,
        )
        root.update()
        assert list(s._host_combo["values"]) == ["System default"]
        assert "null sink" in s._audio_status.cget("text")
    finally:
        root.destroy()


@needs_display
def test_reapply_backend_while_playing_preserves_position():
    """Switching output mid-play keeps the transport running in place."""
    import time
    import tkinter as tk

    from daw.ui.app import PulsegridApp

    root = tk.Tk()
    try:
        app = PulsegridApp(root)
        root.update()
        app.toggle_play()
        deadline = time.time() + 3
        while time.time() < deadline:
            root.update()
            time.sleep(0.05)
        assert app.engine.is_playing()
        before = app.engine.position_beats()
        assert before > 0.5  # actually progressed (null sink paces it)

        # Select "System default" explicitly and switch live.
        app.engine.set_audio_output(None, None)
        app.engine.reapply_audio_backend()
        root.update()

        assert app.engine.is_playing()
        after = app.engine.position_beats()
        # Position preserved: not reset to ~0 by the rebuild.
        assert after >= before - 0.5
        app.stop()
        root.update()
    finally:
        root.destroy()


@needs_display
def test_audio_selection_roundtrip_through_bridge():
    import tkinter as tk

    from daw.ui.app import PulsegridApp

    root = tk.Tk()
    try:
        app = PulsegridApp(root)
        root.update()
        # Headless VM: no hardware, but the calls must not crash.
        assert app.engine.audio_devices() == []
        app.engine.set_audio_output("ALSA", "default")
        assert app.engine.audio_selection() == ("ALSA", "default")
        app.engine.set_audio_output(None, None)
        assert app.engine.audio_selection() == (None, None)
    finally:
        root.destroy()


@needs_display
def test_buffer_dropdown_applies(panel):
    s, calls = panel
    assert s._buffer_var.get() == "Default (driver)"
    s._buffer_var.set("1024 samples")
    s._buffer_chosen()
    assert calls[-1] == ("buffer", 1024)


@needs_display
def test_buffer_dropdown_back_to_default(panel):
    s, calls = panel
    s._buffer_var.set("512 samples")
    s._buffer_chosen()
    assert calls[-1] == ("buffer", 512)
    s._buffer_var.set("Default (driver)")
    s._buffer_chosen()
    assert calls[-1] == ("buffer", None)


@needs_display
def test_sync_buffer_frames(panel):
    s, _calls = panel
    s.sync_buffer_frames(256)
    assert s._buffer_var.get() == "256 samples"
    s.sync_buffer_frames(None)
    assert s._buffer_var.get() == "Default (driver)"


@needs_display
def test_buffer_frames_bridge_roundtrip():
    import tkinter as tk

    from daw.ui.app import PulsegridApp

    root = tk.Tk()
    try:
        app = PulsegridApp(root)
        root.update()
        assert app.engine.buffer_frames() is None
        app.engine.set_buffer_frames(512)
        assert app.engine.buffer_frames() == 512
        stats = app.engine.backend_stats()
        assert set(stats) == {"callbacks", "max_callback_us",
                              "underruns", "block_frames"}
        app.engine.set_buffer_frames(None)
        assert app.engine.buffer_frames() is None
    finally:
        root.destroy()


@needs_display
def test_debug_window_perf_section():
    """Debug window shows the audio performance readout."""
    import time
    import tkinter as tk

    from daw.ui.app import PulsegridApp
    from daw.ui.debug import DebugWindow

    root = tk.Tk()
    try:
        app = PulsegridApp(root)
        root.update()
        win = DebugWindow(root, lambda: app.engine,
                          lambda: app.project)
        win.update()
        # Play briefly so the backend collects stats.
        app.toggle_play()
        deadline = time.time() + 1.5
        while time.time() < deadline:
            root.update()
            win.update()
            time.sleep(0.05)
        assert "Backend:" in win._perf_backend.cget("text")
        assert "Buffer:" in win._perf_buffer.cget("text")
        assert "Underruns:" in win._perf_underruns.cget("text")
        assert "Buffer load:" in win._perf_load.cget("text")
        app.stop()
        win.destroy()
    finally:
        root.destroy()
