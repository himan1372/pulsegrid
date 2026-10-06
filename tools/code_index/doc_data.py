"""Documentation data for the Pulsegrid code index spreadsheet.

This module feeds the documentation sheets (Changelog, Research, Findings).
Edit the structures below; the generator (index.py) picks them up on the
next run. Keep entries newest-first.
"""

# (version, date, commit, format, summary)
VERSIONS = [
    ("0.12.0", "2026-10-05", None, "v9",
     "Engine: Smart Disable (FL Studio concept) -- skips FX chain on "
     "silent tracks after 16-block tail window; instant re-enable. "
     "Saves CPU on sparse arrangements. 38/38 Rust tests pass."),
    ("0.11.0", "2026-10-05", None, "v9",
     "Architectural: extracted PianoRollPainter (LMMS lesson) -- separates "
     "piano-roll rendering from state/interaction. No behavior change. "
     "Documents Core/UI separation (Rust/Python split) as a strength."),
    ("0.10.0", "2026-10-05", None, "v9",
     "Live play + capture (score logger): typing-keyboard piano (FL Studio "
     "layout), always-on capture buffer with transport timestamps, visual "
     "piano widget, dump-to-pattern with input quantize, record arm + "
     "1-bar count-in. Honest scope: no live audio preview or audio input "
     "(needs engine input streams + hardware to verify)."),
    ("0.9.0", "2026-10-04", "e9e010c", "v9",
     "UI smoothness pass + plugin folder setting. From the FL Studio Vector "
     "Engine infographic: dirty-region painting for all canvas drags "
     "(piano roll/automation/playlist move items via coords instead of "
     "delete-all redraws), incremental playhead updates during playback, "
     "and an Animation refresh rate setting (Less smooth/Smooth/"
     "Ultrasmooth). Also added a CLAP plugin folder picker in Settings "
     "(Browse/Clear, persisted, applied via CLAP_PATH)."),
    ("0.8.1", "2026-10-04", "5614fff", "v9",
     "ASCII-only UI text: fixed Tk/XRender BadLength crash on startup. The UI "
     "used Unicode symbols (play/stop triangles, close X, em dashes, arrows, "
     "ellipsis) that trigger a known Tk font-rendering bug (X Error: BadLength "
     "on RenderAddGlyphs) on systems where fontconfig resolves them to color "
     "emoji fonts (e.g. Ubuntu 20.04's Noto Color Emoji). All UI strings are "
     "now pure ASCII. Binary rebuilt in the Ubuntu 20.04 container."),
    ("0.8.0", "2026-10-04", None, "v9",
     "Linux executable REBUILT TWICE: first on Ubuntu 22.04 (glibc 2.35), then "
     "on Ubuntu 20.04 (glibc 2.30) after Philip's machine rejected 2.35 too. "
     "deadsnakes has no Python 3.12 for focal, so CPython 3.12.15 was built "
     "from source in the container. Smoke passes inside the binary; runs on "
     "Ubuntu 20.04+. Replaces the 22.04 build."),
    ("0.8.0", "2026-10-04", None, "v9",
     "Linux executable REBUILT on Ubuntu 22.04 (glibc 2.35): the 24.04-built "
     "binary failed to start on older systems with 'GLIBC_2.38 not found'. "
     "Built in a jammy container (debootstrap + offline toolchain); smoke "
     "suite passes inside the binary. Now runs on Ubuntu 22.04+."),
    ("0.8.0", "2026-10-04", None, "v9",
     "Silent choke verification tools: Debug window with live event log, "
     "per-track voice counts, peak meters; automated loop-wrap choke "
     "verification via offline render + waveform analysis; engine debug "
     "instrumentation (lock-free peaks/voices, try_lock event log)."),
    ("0.7.1", "2026-10-04", "fa53121", "v9",
     "Automation & Note Choking Architecture: gen.p{id} generator automation lanes; "
     "loop-wrap sends real CLAP NOTE_CHOKE (fixes voice hangs); "
     "'Truncate notes at clip boundaries' project setting."),
    ("0.7.0", "2026-10-04", "8b1f666", "v8",
     "CLAP instrument generators: tracks host a CLAP instrument as sound source "
     "via note events; Generator model + Mixer UI + test synth."),
    ("0.6.0", "2026-10-04", "eb105f0", "v7",
     "Native plugin GUIs: floating CLAP windows with live parameter sync."),
    ("0.5.0", "2026-10-04", "5735972", "v7",
     "CLAP plugin hosting: third-party audio effects as track inserts."),
    ("0.4.0", "2026-10-04", "330a160", "v6",
     "Automation lanes: per-track parameter envelopes."),
    ("0.4.0", "2026-10-04", "c6c4a56", "v5",
     "Note lists: variable lengths, chords, per-note velocity."),
    ("0.2.0", "2026-10-04", "57c5d7f", "v2",
     "Playlist timeline + Rust audio graph; executable builds."),
    ("0.1.0", "2026-10-04", None, "v1",
     "Initial slice: sequencer, playback, WAV export, save/load."),
]

# (date, finding, impact, status)
# Impact: "bug" (something broken), "decision" (a design choice),
# "limitation" (honest known gap).
FINDINGS = [
    ("2026-10-05",
     "FL Studio vs LMMS audio engine infographic: FL uses dependency-aware "
     "multithreading (parallel paths, sequential stages); LMMS uses worker "
     "thread pools (PlayHandle/AudioPort/FxChannel jobs). Both separate "
     "engine from device. Our engine is single-threaded; Smart Disable "
     "(skip silent FX chains) is the implementable optimization.",
     "engine",
     "v0.12.0 implements Smart Disable; sends/PDC/threads deferred."),
    ("2026-10-05",
     "FL Studio vs LMMS infographic: FL uses proprietary custom vector UI "
     "+ Blend2D (since 2024); LMMS uses Qt Widgets transitioning to Qt6/QML "
     "with Core/UI separation in progress. Key lesson for Pulsegrid: "
     "separate rendering from interaction (LMMS PianoRollPainter refactor); "
     "treat editors as draw-command surfaces, not widget trees. Our "
     "Rust/Python split already achieves Core/UI separation.",
     "architecture",
     "v0.11.0 extracts PianoRollPainter; documents the architecture."),
    ("2026-10-05",
     "Recording scope decision: real audio input needs Rust engine "
     "input-stream support + audio hardware to verify; live audio preview "
     "needs a lock-free live-note queue. Neither verifiable (no audio "
     "device on dev VM, Philip on iOS). Built the score-logger concept "
     "instead: typing-keyboard piano -> always-on capture buffer -> "
     "dump-to-pattern with quantize. Fully testable headless; honest about "
     "the gap.",
     "decision",
     "v0.10.0 implements capture; engine input/live-note work scoped for "
     "when hardware is available."),
    ("2026-10-05",
     "Reference UI research (Philip's DAW screenshots): smooth DAWs use "
     "retained-mode rendering (static chrome drawn once), meters that bypass "
     "the widget tree (direct render-target writes at 60fps), and strict "
     "frame budgets (16.6ms). Applied to Pulsegrid: mixer strips and playlist "
     "lanes now persist across edits with in-place updates instead of "
     "destroy/rebuild; clip ops redraw just the affected lane. Honest gap: "
     "tkinter is CPU-rasterized, so ~15-30 Hz is our practical ceiling vs "
     "their GPU-driven 60fps.",
     "finding",
     "Documented in v0.9.0 log section 7; incremental widget reuse "
     "implemented in mixer.py/playlist.py."),
    ("2026-10-04",
     "UI 'flashing' on every adjustment: all canvas drag handlers called "
     "delete('all') + full redraw per mouse-motion event (100+ Hz). The "
     "piano roll recreates ~1300 items per motion event. Fixed with "
     "dirty-region updates: drags now move existing items via coords()/move() "
     "(piano roll notes tagged note-{i}, automation envelope tagged env, "
     "playlist clips moved with canvas.move). Playheads updated "
     "incrementally during playback instead of full redraws. Verified "
     "headless: item count unchanged during drags.",
     "bug",
     "Fixed in v0.9.0 (commits 9aa9a75, e9e010c)."),
    ("2026-10-04",
     "X Error of failed request: BadLength (RenderAddGlyphs) on startup — a "
     "Tk font-rendering crash. Root cause: the UI used Unicode symbols "
     "(U+23F5/U+23F9 play/stop, U+25B6 triangle, U+2715 close X, em dashes, "
     "arrows, ellipsis). On systems where fontconfig resolves these to a "
     "color emoji font (Ubuntu 20.04 ships Noto Color Emoji), Tk sends glyph "
     "data XRender cannot handle -> BadLength. Known upstream: Python issue "
     "43647 (Noto Emoji crashes IDLE), Manjaro forum (bad fontconfig "
     "local.conf). The transport-bar buttons rendered at startup, so the "
     "crash happened before the window appeared.",
     "bug",
     "Fixed in v0.8.1: all UI strings converted to pure ASCII (commit "
     "5614fff); binary rebuilt in the Ubuntu 20.04 container."),
    ("2026-10-04",
     "The 22.04-rebuilt executable still failed on Philip's machine with "
     "'GLIBC_2.35 not found' — his system predates Ubuntu 22.04 (likely "
     "20.04/glibc 2.31). Rebuilt a second time on Ubuntu 20.04; bundled "
     "libpython now needs at most GLIBC_2.30. Lesson: when a user reports a "
     "glibc error, ask for `ldd --version` FIRST and target one LTS older "
     "than their system, not just one older than the build machine.",
     "bug",
     "Fixed: 20.04-built binary (26 MB) replaces the 22.04 one; runs on "
     "20.04+. Verified via objdump (max GLIBC_2.30) and in-binary smoke."),
    ("2026-10-04",
     "deadsnakes PPA publishes no Python 3.12 for Ubuntu 20.04 (focal) — "
     "only jammy+. Compiled CPython 3.12.15 from source in the focal "
     "container instead (--enable-shared; required ldconfig for the "
     "installed binary to find its own libpython).",
     "decision",
     "Source-built Python works fine with maturin/PyInstaller; bundled "
     "libpython targets glibc 2.30."),
    ("2026-10-04",
     "Linux executable built on Ubuntu 24.04 failed to start on Philip's "
     "machine: 'Failed to load Python shared library ... libm.so.6: version "
     "GLIBC_2.38 not found'. Root cause: the bundled libpython3.12 was built "
     "against glibc 2.39, but the target system has glibc < 2.38.",
     "bug",
     "Rebuilt the executable inside an Ubuntu 22.04 container (debootstrap). "
     "Bundled libpython now requires at most GLIBC_2.35; bootloader at most "
     "GLIBC_2.14. Binary runs on Ubuntu 22.04+. Lesson: always build the "
     "Linux executable on the OLDEST supported Ubuntu (22.04), as the CI "
     "workflow already does."),
    ("2026-10-04",
     "The dev VM's sandbox blocks network inside chroot/containers (apt "
     "through the egress proxy fails; raw TCP is policy-blocked), while the "
     "host's own network works fine.",
     "decision",
     "Build inputs (271 jammy .debs via a host-side jammy apt config, Rust "
     "toolchain + cargo registry copied from host, pip wheels via pip "
     "download) are fetched on the host and installed offline in the "
     "container (local file:// deb repo, --no-index pip, CARGO_NET_OFFLINE). "
     "Container recipe: /tmp/pg-enter.sh + /tmp/pg-offline-install.sh + "
     "/tmp/pg-offline-build.sh; rootfs kept at /var/lib/machines/pg2204 for "
     "future rebuilds."),
    ("2026-10-04",
     "The automated choke verifier initially 'failed' because the test note "
     "did not cross the wrap: the arrangement loop has a 4-bar minimum, so a "
     "1-bar test arrangement still wraps at beat 16.",
     "bug",
     "Fixed the test (4-bar arrangement, note crossing beat 16). The verifier "
     "now passes with post-wrap peak 0.0000. Lesson: the tool is sensitive "
     "enough to catch test-design errors."),
    ("2026-10-04",
     "Audio thread must never block: used AtomicU32 peak-hold + try_lock event "
     "log, matching the engine's existing try_read philosophy on the song slot.",
     "decision",
     "Implemented in engine/src/debug.rs."),
    ("2026-10-04",
     "Loop-wrap 'choke' silently cleared pending note-offs without notifying the plugin.",
     "bug",
     "Fixed in v0.7.1: now sends CLAP NOTE_CHOKE for all sounding notes."),
    ("2026-10-04",
     "Supplying dummy input buffers to a CLAP instrument with zero declared inputs produced silence.",
     "bug",
     "Fixed in v0.7.0: hosting respects the plugin's declared input-port count."),
    ("2026-10-04",
     "Automation 'initialized control' snap-back does not apply: value_at holds the static base before the first point.",
     "decision",
     "Verified correct; no change needed."),
    ("2026-10-04",
     "Notes ring past clip ends by default (matches FL Studio's default playlist behavior).",
     "decision",
     "v0.7.1 adds opt-in 'Truncate notes at clip boundaries' project setting."),
    ("2026-10-04",
     "Plugin GUIs are floating windows, not embedded in tkinter.",
     "limitation",
     "By design; documented."),
    ("2026-10-04",
     "Project saves parameter values, not full plugin state blobs.",
     "limitation",
     "Documented; state-blob support is future work."),
    ("2026-10-04",
     "No audio device on the dev VM: audible playback never verified by ear.",
     "limitation",
     "All verification is at the event/render level."),
    ("2026-10-04",
     "CLAP only; no VST2/VST3 hosting.",
     "limitation",
     "Documented."),
]
