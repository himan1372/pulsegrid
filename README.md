# Pulsegrid

An original desktop music production app (DAW), built as a learning project
following the **FL Studio Feature Atlas** as a product and architecture guide.
All branding, UI, code, synthesis, and file formats are original — no
FL Studio interface, artwork, plug-ins, or project formats are used or
imitated.

**Current stage (v0.4.0):** DAW workspace overhaul — global toolbar +
transport, searchable Asset Browser with drag-and-drop, Channel Rack and
Piano Roll editors (variable note lengths, chords, per-note velocity), playlist zoom + clip menus,
Mixer with knob controls and hover hints, resizable panels with saved
layouts, UI scaling, and a Quick start guide. Python owns UI/editing;
Rust owns real-time audio through coarse-grained commands.

## Repository structure

```
pulsegrid/
├── engine/                 # Rust audio engine (PyO3 extension module)
│   └── src/
│       ├── lib.rs          # Python bridge: daw._daw_engine_rs (coarse-grained)
│       ├── engine.rs       # Transport, AudioCore render loop, offline render
│       ├── timeline.rs     # Arrangement → immutable Song snapshots
│       ├── graph.rs        # Audio graph: track strips, FX, mix bus, master
│       ├── effects.rs      # Insert effects: delay, drive, filter
│       ├── synth.rs        # Built-in instruments (kick/snare/hat/bass/lead)
│       ├── backend.rs      # CPAL live output + null-sink fallback
│       └── wav.rs          # 16-bit PCM WAV writer
├── python/daw/             # Python application layer
│   ├── __main__.py         # Entry point + headless smoke test (--smoke)
│   ├── project.py          # Versioned project model, save/load, recovery
│   ├── engine_bridge.py    # Typed wrapper over the Rust extension
│   ├── undo.py             # Undo/redo stack
│   └── ui/                 # tkinter desktop shell
│       ├── app.py          # Main window: workspace, DnD, layouts, workflows
│       ├── transport.py    # Play/stop, tempo, position, backend status
│       ├── browser.py      # Asset Browser: instruments/effects/patterns, search, DnD
│       ├── pluginpicker.py # Plugin Picker: grid of built-in instruments/effects
│       ├── settings.py     # Settings: UI scale, layout, confirm prompt, system info
│       ├── sequencer.py    # Channel Rack: step grid for the pattern
│       ├── pianoroll.py    # Piano Roll: variable-length notes, chords
│       ├── playlist.py     # Playlist timeline: tracks, clips, zoom, playhead
│       ├── mixer.py        # Mixer: volume/pan/mute + insert effects
│       ├── widgets.py      # Shared controls: Knob, Tooltip (one grammar)
│       └── dialogs.py      # Errors, about, shortcuts, quick-start guide
├── tests/                  # pytest: project model tests
└── docs/
    ├── ARCHITECTURE.md     # Module separation, threading, RT rules
    └── PROJECT_FORMAT.md   # Versioned .pulsegrid.json format (v3)
```

## Technologies

| Layer   | Technology | Reason |
|---------|-----------|--------|
| Audio engine | Rust 1.99 (PyO3 0.22, CPAL 0.15) | Deadline-critical DSP, scheduling, mixing, rendering |
| Bridge | PyO3, coarse-grained | Whole-pattern snapshots + transport commands only |
| UI / app | Python 3.10+, tkinter (stdlib) | Zero-install GUI, verified present on target |
| Build | maturin | Builds the Rust extension into the venv |

## Build & run

```bash
# 1. Rust toolchain (once)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rust-lang.org | sh

# 2. Python environment + build the engine
cd pulsegrid
python3 -m venv .venv
.venv/bin/pip install maturin pytest
.venv/bin/maturin develop

# 3. Launch
.venv/bin/python -m daw            # or: .venv/bin/pulsegrid

# 4. Tests
.venv/bin/python -m pytest tests/ -q                    # project model (15 tests)
cargo test --manifest-path engine/Cargo.toml            # engine (8 tests)
xvfb-run -a .venv/bin/python -m daw --smoke             # end-to-end slice
```

### Windows

Pulsegrid runs on Windows 10/11 (64-bit). The engine is verified to
compile for the Windows target (`x86_64-pc-windows-msvc`); audio goes
through WASAPI via CPAL, and the UI is plain tkinter — no Linux-only
code anywhere in the project.

```powershell
# 1. Install prerequisites (once):
#    - Python 3.10+ from python.org (check "Add python.exe to PATH")
#    - Rust from https://rustup.rs (Desktop C++ build tools get
#      installed automatically with the default options)

# 2. Python environment + build the engine
cd pulsegrid
py -m venv .venv
.venv\Scripts\pip install maturin pytest
.venv\Scripts\maturin develop

# 3. Launch
.venv\Scripts\pulsegrid.exe        # or: .venv\Scripts\python -m daw

# 4. Tests
.venv\Scripts\python -m pytest tests\ -q
cargo test --manifest-path engine\Cargo.toml
.venv\Scripts\python -m daw --smoke
```

Notes for Windows users:

- The launcher is `.venv\Scripts\pulsegrid.exe` (the `[project.scripts]`
  entry in `pyproject.toml` generates it on install).
- Panel layout and UI scale are saved per user under
  `%APPDATA%\pulsegrid\layout.json`.
- If live audio fails to open, the app falls back to a silent null sink
  so the UI and transport keep working; the backend name is shown in
  the transport bar.

## Standalone executables

Single-file builds (no Python install needed on the target machine)
are made with PyInstaller:

```bash
# Linux (from the repo root; needs the venv + engine built first)
.venv/bin/pip install pyinstaller
printf 'from daw.__main__ import main\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n' > /tmp/pulsegrid_entry.py
.venv/bin/pyinstaller --noconfirm --onefile --windowed --name pulsegrid \
    --paths python /tmp/pulsegrid_entry.py
# -> dist/pulsegrid
```

On Windows, run `scripts\build-windows-exe.ps1` from the repo root
instead — it creates the venv, builds the engine, and bundles
`dist\pulsegrid.exe` in one step. The same builds run automatically
on GitHub Actions (`.github/workflows/build-executables.yml`) for
every push to `master`, including a `--smoke` test of each binary.

## What works (v0.4.0)

- DAW workspace: global toolbar + transport, left dock with
  Browser / Plugin Picker / Settings tabs (center top),
  Playlist (center bottom), Mixer (right). All seven main
  panels (Channel Rack, Piano Roll, Playlist, Mixer, Browser,
  Plugin Picker, Settings) show/hide from the View menu and stay
  docked inside the main window — no floating windows. Panels
  resize via paned splitters; visibility, sash positions, UI scale
  (100/125/150%), and preferences persist between sessions
  (`~/.config/pulsegrid/layout.json`, `%APPDATA%\pulsegrid` on
  Windows). Ctrl+B / Ctrl+P / Ctrl+M toggle Browser, Plugins,
  and Mixer; View menu resets the layout.
- Asset Browser: searchable instrument, effect, and pattern
  lists. Drag an instrument onto a Channel Rack row to change
  its sound; drag an effect onto a mixer strip to add it; drag
  a pattern onto a playlist lane to place a clip. Right-click
  a pattern for edit/duplicate/rename/delete. Built-in content
  only — no external samples, no third-party plug-ins.
- Plugin Picker: visual grid of the built-in instruments and
  effects with the same drag-and-drop targets as the Browser.
- Settings: working preferences — UI scale, layout reset,
  unsaved-changes confirmation toggle, plus read-only audio
  backend and config-file info.
- Channel Rack: 16-step × 5-channel step grid (Kick, Snare, Hat, Bass,
  Lead). Click to toggle steps; the pitch box sets the default pitch for
  newly created notes; right-click a row for channel actions; playhead
  follows playback.
- Piano Roll: note editor (MIDI 24–96) for the selected channel — variable note lengths, chords, per-note velocity (Alt+drag).
  Click/drag to paint or erase notes at exact pitches; **Alt+drag a
  note up/down to set its velocity** (notes shade by velocity).
  Edits commit as one undoable change; each step sounds at its own
  pitch and velocity (project format v6; older files migrate
  automatically).
- Automation: per-track parameter envelopes over arrangement beats —
  gain, pan, and effect parameters (delay time/feedback/mix, drive
  amount, filter cutoff). Click to add control points, drag to move
  them (beat snaps to 1/4), right-click to delete. Values glide
  linearly between points; before the first point the track's own
  setting holds. One gesture = one undoable edit; lanes follow FX
  add/remove and shrink safely when clips are deleted.
- Playlist: pattern clips on multiple tracks, click-to-place,
  drag-to-move with a live ghost preview (the project only changes on
  drop, so undo restores the pre-drag position), **snap selector:
  Bar or Beat**, zoom controls, right-click clip menu (edit pattern,
  delete), playhead.
- Mixer: per-track volume, pan (knob with fine adjust / reset grammar),
  mute, and insert effects (delay, drive, filter) backed by real engine
  parameters; hover hints on every control. Routing is track → master
  bus; offline WAV rendering covers the full mix.
- CLAP plugin hosting: third-party CLAP audio effects as track inserts —
  Mixer → Plug scans your CLAP folders, validates the plugin before
  adding. Each plugin gets a generic slider panel for its parameters,
  plus a GUI button that opens the plugin's *native* interface as a
  floating window when the plugin provides one (tweaks sync back live;
  one undo step covers the session). Plugin parameters are automatable
  (`fx0.p12` lanes) and saved in the project (format v9). Stereo
  in/out.
- CLAP instrument generators: a track's Generator slot hosts a CLAP
  instrument plugin as the sound source (instead of built-in voices).
  Notes from the track's clips become CLAP note-on/off events (pitch,
  velocity, note ID); the plugin's stereo output flows through the
  track's FX chain. Mixer → "Set instrument…" picks from scanned
  instruments; Edit…/GUI work like effects. Generator parameters are
  automatable (`gen.p11` lanes) without rebuilding the plugin. Saved in
  the project (format v9).
- Automation & note choking (FL Studio architecture study): loop wrap
  sends real CLAP NOTE_CHOKE events so plugin voices are cut at the
  boundary instead of hanging; Project menu → "Truncate notes at clip
  boundaries" cuts notes at clip edges (default: notes ring past, like
  FL's default). Before the first automation point the static value
  holds (no "initialized control" snap).
- Silent choke verification (FL Studio study): View menu → "Debug —
  choke verification" opens live per-track peak meters, voice counts,
  and a note on/off/choke event log, plus a one-click automated
  loop-wrap choke check (offline render + waveform analysis). No audio
  hardware needed.
- Playback through the Rust engine: sample-accurate 16th-note scheduling,
  polyphonic synth voices (pitch-swept kick, noise snare/hat, saw bass,
  square lead with ADSR + filters), master soft-clip bus. Tempo 20–300
  BPM; bar/beat position readout; audio backend status.
- Live output via CPAL when an audio device exists; automatic null-sink
  fallback (paced silent thread) when it doesn't — transport and UI keep
  working, and the UI reports which backend is active.
- Deterministic offline render to 16-bit WAV (bit-identical across runs).
  Export runs on a worker thread with a real progress bar and Cancel —
  the UI stays responsive (the Rust side releases the GIL during DSP).
- Project save/open as versioned `.pulsegrid.json` (atomic writes);
  corrupt files are backed up with a clear error instead of data loss.
- Undo/redo for pattern, clip, mixer, and structural edits (snapshot
  based, so undo can never touch stale objects).
- Keyboard: Space (play/stop), Delete (selected clip), Ctrl+N/O/S/E/Z/Y/B/M.
  A Quick start guide (Help menu) walks a new user through the loop:
  find a sound → make a beat → edit notes → arrange → mix → export.

## Roadmap (from the feature atlas)

1. ✅ MIDI clock + step sequencing
2. ✅ Playlist-style timeline, Rust audio graph with multiple clips/instruments
3. ✅ Mixer channels, volume/pan, effects, routing, more render options
4. ✅ DAW workspace: browser, piano roll, DnD workflow, saved layouts
5. Automation lanes, audio clips, recording
6. Later: third-party plug-in hosting, time-stretch, stem separation —
   these will not appear in the UI until they really exist.

## License note

Original code and assets. Do not copy FL Studio's interface, artwork,
proprietary formats, or plug-ins into this project.
