# Pulsegrid Architecture

Follows the "Rust + Python" blueprint from the FL Studio Feature Atlas:
**Python steers, Rust owns every deadline-critical audio path.**

## Module separation

```
┌──────────────────────────── Python ────────────────────────────┐
│ UI (tkinter)      │ DAW workspace: toolbar + transport, Browser,│
│                   │ Channel Rack + Piano Roll editors, Playlist,│
│                   │ Mixer; shared widgets (Knob, Tooltip);      │
│                   │ drag-and-drop, undoable structural edits    │
│ project.py        │ Document model (v3), versioned save/load    │
│ engine_bridge.py  │ Typed, coarse-grained calls into Rust      │
└───────────────────────────────┼─────────────────────────────────┘
                                │ PyO3: set_arrangement / play / stop /
                                │         position / stats / render_wav
┌───────────────────────────────┼─────────────────────────────────┐
│ Rust engine                   ▼                                  │
│ timeline.rs       │ Arrangement → immutable Song snapshots      │
│ graph.rs          │ Audio graph: track strips → mix → master    │
│ synth.rs          │ Voices: kick/snare/hat/bass/lead, ADSR      │
│ engine.rs         │ AudioCore render loop, transport, WAV render│
│ backend.rs        │ CPAL live output / null-sink fallback       │
│ wav.rs            │ PCM WAV writer (offline path only)          │
└─────────────────────────────────────────────────────────────────┘
```

Each layer can evolve independently: the UI never touches DSP state, and
the engine never touches files, the UI toolkit, or Python objects on the
audio path.

## UI architecture (v0.4.0)

The shell (`ui/app.py`) follows a DAW workspace layout:

```
┌──────────────────────────────────────────────────────┐
│ Global toolbar: menus · transport · panel toggles    │
├──────────┬──────────────────────────────┬────────────┤
│ Browser /│ Channel Rack / Piano Roll    │ Mixer      │
│ Plugins /│ (notebook tabs)              │ (volume /  │
│ Settings ├──────────────────────────────┤ pan / mute │
│ (tabs)   │ Playlist (clips, zoom)       │ / FX)      │
└──────────┴──────────────────────────────┴────────────┘
```

All seven main panels (Channel Rack, Piano Roll, Playlist, Mixer,
Browser, Plugin Picker, Settings) show/hide from the View menu and
stay docked inside the main window — no floating windows. The left
dock holds the Browser, Plugin Picker, and Settings as tabs.

- **One interaction grammar** (`ui/widgets.py`): `Knob` (drag, Shift+drag
  fine adjust, double-click reset, wheel nudge) and `Tooltip` (delayed
  hover hints) are reused everywhere, so pan and every effect parameter
  behave identically.
- **Browser** (`ui/browser.py`): searchable instrument, effect, and
  pattern lists. Dragging an instrument onto a Channel Rack row changes
  its instrument; dragging an effect onto a mixer strip adds it;
  dragging a pattern onto a playlist lane places a clip.
  Right-click on a pattern offers edit/duplicate/rename/delete.
  Built-in content only — the UI never implies external samples or
  third-party plug-ins.
- **Plugin Picker** (`ui/pluginpicker.py`): visual grid of the built-in
  instruments and effects with the same drag-and-drop targets.
- **Settings** (`ui/settings.py`): working preferences — UI scale,
  layout reset, unsaved-changes confirmation toggle, read-only audio
  backend and config-file info. Every control does something.
- **Channel Rack** (`ui/sequencer.py`): step grid per channel. The pitch
  box is the *default pitch for newly created notes*; right-click a row
  for channel actions.
- **Piano Roll** (`ui/pianoroll.py`): note editor (time × MIDI
  24–96) with variable-length notes and chords. Click empty space to
  add a note, drag a note's body to move it, drag its right edge to
  resize it, right-click to delete; Alt+drag a note up/down to set its
  velocity (notes shade by velocity). Edits commit as one undoable
  change and feed the channel's note list in the model. Step snapping
  still applies to starts and lengths.
- **Playlist** (`ui/playlist.py`): clip timeline with zoom, a Snap
  selector (Bar or Beat) quantizing placement and dragging,
  click-to-place, drag-to-move with a live ghost preview (the project
  only changes on drop, so undo restores the pre-drag position),
  right-click clip menu.
- **Mixer** (`ui/mixer.py`): per-track volume/pan/mute + insert effects,
  all backed by real engine params.
- **Layouts**: the horizontal/vertical paned splitters resize freely;
  Browser/Mixer visibility, sash positions, and UI scale (100/125/150%)
  persist to the per-user config dir (`%APPDATA%\pulsegrid` on Windows,
`~/.config/pulsegrid` elsewhere) and restore on launch.
- **Success path**: find a sound → make a beat → edit notes → arrange
  clips → mix → export (File > Export arrangement to WAV). A Quick start
  guide under Help walks a new user through it.

Undo stays snapshot-based: structural edits mutate a copy, validate it,
and push before/after project snapshots, so undo can never touch a
stale object after the project was replaced. The pattern bar, Channel
Rack, Piano Roll, Playlist, Mixer, and Browser all re-sync from the
current project snapshot after every edit.

Channels carry note lists (project format v5): each note has a start,
length, pitch, and velocity, and notes may overlap (chords). The
engine (`timeline.rs`) schedules each note at its own sample offset
with its own length; pitched voices (`bass`, `lead`) hold the note for
its length then release, while one-shot drums ignore length. Clips are
positioned in beats (`start_beat`, v4), so the Playlist can snap to
bars or beats.

Offline export no longer freezes the UI: `render_wav_file` (in
`lib.rs`) is a module-level function over an immutable arrangement
snapshot — safe to call from a worker thread — that releases the GIL
during DSP and reports progress through a Python callback
(`done_blocks, total_blocks` → True to continue, False to abort).
The app snapshots the arrangement on the UI thread, renders on a
worker, and drives a determinate progress bar with Cancel via a
thread-safe queue. The old blocking `PyEngine::render_wav` remains
for the headless smoke test.

## Threading model

- **Control thread** (Python main thread): owns the `Engine` handle and the
  project document. Builds immutable `Song` snapshots and publishes them.
- **Audio thread** (CPAL callback, or the paced null-sink thread): owns
  `AudioCore`. Reads the song slot with `try_read` — it never blocks; on
  contention it finishes the current block with the previous snapshot.
- **UI → audio communication** is one-way and bounded: three atomics
  (`playing`, `reset`, `position`). No locks, no allocation, no I/O, no
  logging, and no Python on the audio path.

`PyEngine` is marked `unsendable`: it owns the CPAL stream, which is
`!Send` on some platforms. All engine calls must come from the creating
thread. A future iteration can move stream ownership to a dedicated Rust
thread steered via message passing.

## CLAP plugin hosting (v0.5.0)

Third-party CLAP audio effects hosted via the `clack-host` crate
(`engine/src/plugins.rs`):

- **Scanning** (`scan_clap_plugins`, control thread): walks `$CLAP_PATH`,
  `~/.clap`, `/usr/lib/clap` for `audio-effect` plugins. A broken bundle
  only removes itself.
- **Instance model**: `PluginInstance` is `!Send` (main-thread only), but
  `StartedPluginAudioProcessor` is `Send` — so the graph holds only the
  started processor plus scratch buffers. The main-thread handle is
  dropped after handoff; the processor's `Arc` keeps the plugin alive.
- **Parameters**: UI/automation changes are queued as CLAP param-value
  events and flushed with the next `process()` call — no locks on the
  audio thread. `update_params` detects plugin-only value changes and
  queues them *without rebuilding* the effect (plugin DSP state kept);
  only a plugin add/remove/swap rebuilds.
- **Validation before load**: the UI calls `check_clap_plugin` (full
  load → activate → start → drop) when adding a plugin, so broken
  plugins fail loudly in the picker instead of silently in the graph.
- **Native GUIs** (`PluginGui`, control thread): plugins that expose the
  CLAP GUI extension open their own interface as a *floating* window —
  the plugin creates and manages the window; Pulsegrid only calls
  create/show/hide/destroy. A GUI-only instance (never activated, never
  processes audio) is used: project params are flushed in via
  `params.flush` on the inactive handle before showing, and a
  `PluginGuiSession` (Python) polls `get_value` every 150ms, applying
  tweaks to the project without undo spam. Closing collapses the session
  into one undo step. Plugins without a GUI fall back to the generic
  slider panel. API preference: X11 → Wayland on Linux, WIN32 on
  Windows, COCOA on macOS.
- **Honest limits**: stereo in/out only; floating GUIs only (no embedded
  foreign windows in the tkinter UI); parameter values saved, not full
  state blobs; plugin libraries stay loaded for the session.

## CLAP instrument generators (v0.7.0)

A track's **generator** is its sound source — `None` means the built-in
voice bank; a `GeneratorParams { plugin_id, path, params }` hosts a CLAP
instrument plugin (`engine/src/plugins.rs` `HostedClapInstrument`):

- **Scanning**: `scan_clap_instruments()` filters the `instrument`
  feature (separate from `audio-effect`).
- **Note routing**: `TrackStrip::render_generator` converts `TrackEvent`s
  to CLAP `NoteOnEvent`/`NoteOffEvent` (key = MIDI pitch, velocity,
  unique note_id; channel 0, port 0). Note-offs are scheduled from
  `len_samples` via a pending queue; loop wraps send real CLAP
  `NOTE_CHOKE` events for all sounding notes so plugin voices are cut at
  the boundary instead of hanging. The plugin renders a full stereo block
  per audio block; track gain/pan apply after, then the normal FX chain.
- **Generator automation (v0.7.1)**: `AutoParam::Generator { param }`
  (`"gen.p{id}"` lanes) automates CLAP instrument parameters. Applied in
  `TrackStrip::apply_automation` via `set_generator_param`, which queues
  a CLAP param event — no rebuild, the plugin keeps its DSP state. The
  Automation tab lists generator params when the track has a generator.
- **Truncate notes (v0.7.1, format v9)**: the `truncate_notes` project
  setting (Project menu) cuts notes at clip boundaries during
  `Song::from_arrangement`; default false preserves the v8 behavior of
  notes ringing past clip ends.

## Silent choke verification (v0.8.0)

`engine/src/debug.rs` implements the FL Studio "Silent Choke
Verification" toolset without audio hardware. The audio thread must
never block: peaks and voice counts are lock-free `AtomicU32`s, and
the event log uses `try_lock` (skips rather than waits), matching the
engine's existing `try_read` philosophy.

- **Peaks**: per-track peak-hold (`AtomicU32` f32 bits) recorded in
  `Graph::render`; Python takes-and-resets; the UI applies ballistics.
- **Voices**: per-track counts (built-in active voices, generator
  pending notes) updated per block.
- **Events**: note on/off/choke with track, key, note ID, sample;
  drained by the UI into a scrolling log.
- **Automated check**: `python/daw/verify_choke.py` renders a
  wrap-crossing note and asserts the post-wrap energy drop (< 10%).
- **UI**: `python/daw/ui/debug.py` `DebugWindow` (View menu) with
  meters, voice counts, event log, and the verify button.
- **Lifecycle**: the instrument is built in `TrackStrip::new`; 
  `update_params` rebuilds only on add/remove/swap — param-only changes
  are queued as CLAP events without rebuild (DSP state kept).
- **Ports**: stereo output required; input provided only if the plugin
  declares one (most instruments have none).
- **Model (format v8)**: `PlaylistTrack.generator` (`Generator`
  dataclass); v7 files migrate with `generator=None`.
- **UI**: Mixer Generator section — "Set instrument…" (picker +
  `check_clap_instrument` validation), Edit… (generic params),
  GUI (native floating window via `GeneratorGuiSession`), × (clear).
- **Test synth**: `test-instrument/` — polyphonic sine/square synth with
  ADSR (`org.pulsegrid.test-synth`, params 11/12/13); fixture at
  `tests/fixtures/clap/PulsegridTestSynth.clap`.
- **Honest limits**: no generator param automation yet (track-level params
  only); no multi-output; notes cut at loop wrap.

## Real-time rules (enforced by construction)

1. All buffers and DSP state (voice pool, scratch) are allocated before
   playback starts (`AudioCore::new`).
2. The audio callback does no heap allocation, locking, file/network I/O,
   logging, or Python calls.
3. Scheduling is sample-accurate: events carry absolute sample offsets
   within the loop; the render loop walks a pre-sorted event list with an
   index (no searching per sample).
4. Noise uses a seeded SplitMix64 per trigger → offline renders are
   bit-deterministic.
5. Callback timing is measured per block (`Instant`, vDSO clock) and
   exposed via `stats()`: callback count, max callback time, underruns.

## Audio graph (v0.3.0)

```
TrackEvent list ──► VoiceBank ──► Gain/Pan ──► [Delay|Drive|Filter…] ──┐
TrackEvent list ──► VoiceBank ──► Gain/Pan ──► [Drive] ────────────────┼──► MixBus ──► Master(tanh) ──► out
...                                                                   ┘
```

Each playlist track owns a `TrackStrip`: a 16-voice pool (silent voices are
preferred; round-robin stealing only when all are busy), a constant-power
gain/pan stage, an insert effect chain (any number of Delay / Drive /
Filter, in order), and its own event cursor. Muting a track advances its
event cursors but renders silence (voices are silenced on the mute
transition, so unmuting never replays missed notes). Strips render into
preallocated scratch buffers (`MAX_BLOCK_FRAMES` = 4096), sum into the mix
bus, and the master applies a tanh soft-clip. `Graph::update_params`
changes gain/pan/mute without touching voices (no clicks) and rebuilds an
effect chain only when its params actually changed; `Graph::sync_cursors`
resyncs event cursors after an arrangement edit without silencing ringing
voices.

Automation (`AutoCurve` in `timeline.rs`, applied in `graph.rs`):
per-track parameter envelopes over arrangement beats (track gain/pan and
effect parameters). Each block, the graph evaluates every curve once at
the block's start beat (~11 ms granularity) and applies it: gain/pan
curves recompute the strip's constant-power gains; FX curves call
`Effect::set_param`, which updates the live coefficient/state in place
(delay read offset, drive norm, filter alpha) without rebuilding the
effect — no allocation, no clicks from state loss. Before a curve's
first point the static value holds; between points values interpolate
linearly; after the last point the last value holds. Curves are
validated at `Song` build (param id, beat inside the arrangement loop,
value in range) and travel with the arrangement snapshot, so export
renders them identically to playback.

Built-in effects (`effects.rs`, all original DSP):
- **Delay**: feedback echo, 10–2000 ms, feedback 0–0.95, mix 0–1.
- **Drive**: soft-saturation overdrive, normalized so 0% is transparent.
- **Filter**: one-pole lowpass, 20–20000 Hz.

Fixed graph topology for now: every track routes to the master bus
(sends/subgroups are future work).

## Buffer-size and latency decisions (v0.2.0)

- Sample rate: 44 100 Hz (engine supports 8 000–192 000).
- Live output: CPAL default buffer size (backend-chosen, typically
  128–512 frames). Reported max callback time is usually < 100 µs for the
  built-in instruments — far below a 512-frame budget (~11.6 ms).
- Offline render uses 512-frame blocks through the same `render_block`
  path as live playback, so renders and live output cannot diverge.
- Step timing is sample-accurate without drift: each event's offset is
  `round(step * exact_step_samples)` from an unrounded step duration, so a
  16-step pattern lands exactly on bar lines even when the step length is
  fractional (e.g. 5512.5 samples at 128 BPM / 44.1 kHz).
- No plug-in delay compensation yet: there are no plug-ins and no
  variable-latency nodes in the graph (documented limitation; required
  before mixer/effects land in stage 4).

## What was measured

- `cargo test`: 12 engine tests (arrangement timing incl. bar-exact
  pattern loops, pan law, multi-track summing, envelope decay, noise
  determinism, WAV header).
- Smoke test asserts: playback position advances at the correct musical
  rate (0.6 s @ 128 BPM → 1.29 beats), rendered WAV has the exact
  expected frame count for the 8-bar arrangement (661504 frames) and is
  non-silent, the second half (bass+lead join) is measurably louder than
  the first (drums only), two renders are byte-identical.
- UI smoke: window builds, play toggle works, playlist place/move/delete
  with full undo back to the default project.
- UI interaction checks: clip place/move/delete/undo/redo, pattern
  add/delete, track add, double-click-to-edit-pattern.

Profile before optimizing: the next stages should add per-stage
benchmarks (worst-case voice count, render throughput) before changing
any DSP code.
