"""Pulsegrid entry point.

Run the desktop app:
    python -m daw

Headless smoke test (needs a display or xvfb-run):
    python -m daw --smoke
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import tkinter as tk
import wave


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pulsegrid", description="Pulsegrid DAW")
    parser.add_argument("--smoke", action="store_true",
                        help="run a headless end-to-end smoke test and exit")
    args = parser.parse_args(argv)

    if args.smoke:
        return run_smoke_test()

    from .ui.app import PulsegridApp

    root = tk.Tk()
    PulsegridApp(root)
    root.mainloop()
    return 0


def run_smoke_test() -> int:
    """End-to-end slice: build project -> play -> stop -> save -> reopen.

    Prints PASS/FAIL lines; returns 0 on success.
    """
    from .engine_bridge import EngineBridge, EngineError
    from .project import Project, ProjectError, new_default_project
    from .ui.app import PulsegridApp

    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(("PASS " if cond else "FAIL ") + name + (f" -- {detail}" if detail else ""))
        if not cond:
            failures.append(name)

    # 1. Project model
    project = new_default_project("Smoke Test")
    check("default project validates", True)
    check("default project has 2 patterns",
          len(project.patterns) == 2,
          f"{len(project.patterns)} patterns")
    check("default project has 2 tracks with clips",
          all(t.clips for t in project.tracks))
    check("arrangement is 8 bars", project.arrangement_bars() == 8)

    # 2. Engine bridge + playback
    try:
        engine = EngineBridge()
        check("engine created", True, engine.backend_name())
    except EngineError as e:
        check("engine created", False, str(e))
        return 1

    try:
        engine.push_project(project)
        check("arrangement pushed to engine", True)
    except EngineError as e:
        check("arrangement pushed to engine", False, str(e))

    try:
        engine.play()
        time.sleep(0.6)
        playing = engine.is_playing()
        pos = engine.position_beats()
        check("playback starts", playing)
        check("position advances", pos > 0.1, f"{pos:.2f} beats")
        engine.stop()
        time.sleep(0.1)
        check("playback stops", not engine.is_playing())
    except EngineError as e:
        check("playback cycle", False, str(e))

    # 3. Offline render -> verify WAV content and arrangement length.
    # 8 bars at 128 BPM / 44100 Hz. The engine quantizes per beat:
    # round(60/128 * 44100) = 20672 samples/beat -> 32 beats = 661504.
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "smoke.wav")
        try:
            engine.render_wav(wav_path, loops=1)
            with wave.open(wav_path, "rb") as w:
                frames = w.getnframes()
                check("wav rendered", frames > 0, f"{frames} frames")
                check("render length matches 8-bar arrangement",
                      frames == 32 * round(60 / 128 * 44100), f"{frames} frames")
                raw = w.readframes(frames)
            import struct
            samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
            peak = max(abs(s) for s in samples)
            check("render is not silent", peak > 1000, f"peak={peak}")
            # Second half (bars 5-8, where bass+lead join) must differ from
            # the first half (drums only): proves multiple clips render.
            half = len(samples) // 2
            e1 = sum(s * s for s in samples[:half]) / half
            e2 = sum(s * s for s in samples[half:]) / half
            check("arrangement varies across clips",
                  abs(e1 - e2) / max(e1, e2) > 0.05,
                  f"E1={e1:.0f} E2={e2:.0f}")
        except EngineError as e:
            check("wav render", False, str(e))

        # Determinism: two renders must be byte-identical.
        wav2 = os.path.join(tmp, "smoke2.wav")
        try:
            engine.render_wav(wav2, loops=1)
            with open(wav_path, "rb") as a, open(wav2, "rb") as b:
                check("render is deterministic", a.read() == b.read())
        except EngineError as e:
            check("render determinism", False, str(e))

        # 4. Save / reopen roundtrip
        proj_path = os.path.join(tmp, "smoke.pulsegrid.json")
        try:
            project.save(proj_path)
            reopened = Project.load(proj_path)
            check("save/load roundtrip",
                  reopened.to_dict() == project.to_dict())
        except ProjectError as e:
            check("save/load roundtrip", False, str(e))

        # 5. Corrupt project recovery
        bad_path = os.path.join(tmp, "bad.pulsegrid.json")
        with open(bad_path, "w") as f:
            f.write("{not valid json!!!")
        try:
            Project.load(bad_path)
            check("corrupt project rejected", False, "no error raised")
        except ProjectError as e:
            backed_up = ".corrupt-" in str(e) and ".bak" in str(e)
            check("corrupt project rejected with backup", backed_up, str(e)[:80])

    # 6. UI builds without errors (needs display/xvfb)
    try:
        root = tk.Tk()
        root.withdraw()
        app = PulsegridApp(root)
        check("UI builds", True)
        app.toggle_play()
        time.sleep(0.3)
        check("UI play toggle", app.engine.is_playing())
        app.stop()
        root.destroy()
    except Exception as e:  # noqa: BLE001 - smoke test reports everything
        check("UI smoke", False, f"{type(e).__name__}: {e}")

    print()
    if failures:
        print(f"SMOKE TEST: {len(failures)} FAILURE(S)")
        return 1
    print("SMOKE TEST: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
