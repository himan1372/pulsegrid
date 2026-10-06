"""Generate pulsegrid-fl-studio-research.xlsx from structured data.

FL Studio infographic research notes, kept as a regenerable LibreOffice-
compatible workbook (separate from the code index, which is regenerated
from source by tools/code_index/index.py).
"""

import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

OUT = os.path.expanduser("~/workspace/your_files/pulsegrid-fl-studio-research.xlsx")

TITLE_FONT = Font(bold=True, size=13)
SECTION_FONT = Font(bold=True, size=11)
WRAP = Alignment(wrap_text=True, vertical="top")

# (sheet name, intro, [(section, [points])], takeaways)
INFOGRAPHICS = [
    (
        "Plugin GUI System",
        "FL Studio plugin GUIs cover native Image-Line plugins, "
        "third-party VST/AU/CLAP plugins hosted via the Plugin wrapper, "
        "and user-created interfaces inside tools like Patcher. Native "
        "plugins use FL's own architecture for quick automation and "
        "per-note expression, while third-party plugins run through the "
        "wrapper, adding FL-specific controls around the plugin's own GUI.",
        [
            ("1. Core architecture & history", [
                "Shifted to vector-based GUI in v12 (2015) for infinite scaling.",
                "Native plugins use FL's architecture for better host integration.",
                "Third-party plugins use the Plugin wrapper for consistent controls.",
            ]),
            ("2. Plugin wrapper GUI options", [
                "DPI aware: the plugin handles its own scaling.",
                "Scale editor dimensions: matches the plugin GUI to the window size.",
                "Bridging + DPI-aware: fixes for high-resolution monitors.",
                "Native plugins have a separate scaling multiplier.",
            ]),
            ("3. Native plugin GUIs", [
                "Consistent, clean vector style with high contrast.",
                "Examples: Harmor, Sytrus, Gross Beat.",
                "Tight integration with FL's theming.",
            ]),
            ("4. Custom GUIs: Patcher & Dashboard", [
                "Patcher: Surface tab + Control Creator for knobs and sliders; "
                "grid snapping; link controls on the Map tab.",
                "Dashboard: legacy tool for MIDI hardware controllers.",
            ]),
            ("5. FL Studio theming", [
                "Pre-v12: modified bitmap skins.",
                "v12+: official Themes system (Options > Theme settings).",
                "User themes (.flstheme + thumbnail PNG) shared in the community.",
            ]),
            ("6. Developing VST plugins", [
                "JUCE is the most common framework (with APVTS).",
                "Other tools: VSTGUI, foleys_magic, iPlug2.",
                "Best practices: dark backgrounds, high-contrast accents, "
                "visual feedback.",
            ]),
        ],
        [
            "Pulsegrid takeaway: a 'wrapper' model fits — host chrome "
            "(title, bypass, our controls) around the plugin's own UI.",
            "Pulsegrid takeaway: DPI/scaling must be the plugin's job; the "
            "host only provides the parent window and resize handling.",
            "Pulsegrid takeaway: our generic fallback panel should follow "
            "the best-practice look — dark, high-contrast, clear value "
            "readouts.",
        ],
    ),    (
        "Instrument Plugins (Generators)",
        "Generators are core sound sources. Each is held in a Channel. "
        "Native plugins provide per-note slides and deeper automation, "
        "while third-party VSTs work via the Plugin Wrapper. "
        "\"The modular heart of sound creation, where note data flows and "
        "shapes expression.\"",
        [
            ("1. Channel, patterns, and routing architecture", [
                "Channel Rack: all instruments are loaded here; Patterns "
                "(piano roll/sequencer) drive them.",
                "Mixer routing: every Channel has a Mixer track selector "
                "(default = Master) routing its audio output.",
                "Best practice: linked Track+Channel+Mixer; drag to Mixer "
                "auto-routes and names; link selected channels.",
            ]),
            ("2. Note color groups and MIDI", [
                "16 note colors map piano-roll note colors to MIDI "
                "channels 1-16.",
                "One piano roll can drive a multi-timbral instrument "
                "(Kontakt, DirectWave, SliceX).",
                "Benefits: independent editing, slides/portamento, "
                "selection by color.",
            ]),
            ("3. Advanced MIDI handling and locking", [
                "MIDI Channel Through: incoming MIDI passes through on "
                "its original channel as the matching note color.",
                "Receive Notes From (Channel Locking): lock specific "
                "controllers or MIDI channels to instruments for "
                "multi-controller simultaneous play.",
                "MIDI Out plugin: sends MIDI to external hardware or "
                "internal plugins; map note color to MIDI channel.",
            ]),
            ("4. Instrument layering methods", [
                "Layer Channel: passes notes to child channels; keyboard "
                "regions, crossfading, level/pan/pitch offsets.",
                "Patcher: visual modular routing (MIDI/turquoise, "
                "audio/yellow, parameters/red); multiple instruments and "
                "effects inside one Channel.",
                "Multi-Output: map a multi-timbral plugin to multiple "
                "mixer tracks via the wrapper; enable auto-map.",
            ]),
            ("5. Per-note properties and expression", [
                "Detailed events: velocity, pan, release, Mod X/Y, fine "
                "pitch, slide, portamento.",
                "Deep native support; more limited on most VSTs. Slides "
                "travel with the note.",
                "Editor: double-click notes or use the integrated event "
                "editor.",
            ]),
            ("6. Practical workflow summary", [
                "Step 1: add instruments (Plugin Picker / Browser).",
                "Step 2: route early to the Mixer.",
                "Step 3: use colors for multi-timbral control.",
                "Step 4: Layer for simple stacking, Patcher for modular.",
                "Step 5: lock controllers / channel-through for "
                "performance.",
                "Step 6: save favorite presets with all configurations.",
            ]),
        ],
        [
            "Pulsegrid takeaway: a track-level 'generator' slot mirrors "
            "FL's Channel — one sound source per track, driven by the "
            "track's clips/notes, routed into the track's FX chain.",
            "Pulsegrid takeaway: CLAP note events (note_on/off with "
            "key/velocity/channel/note_id) are the third-party equivalent "
            "of per-note expression; velocity is already in our note "
            "model.",
            "Pulsegrid takeaway: layering (one note source -> multiple "
            "generators) is a natural future step once per-track "
            "generators exist.",
        ],
    ),    (
        "Automation & Note Choking Architecture",
        "The underlying mechanics of parameter modulation global bounds, "
        "and protective note-off execution within the FL Studio audio engine.",
        [
            ("1. Generator automation: track vs. channel targeting", [
                "Channel-level: automation clips are unique Generators in "
                "the Channel Rack, targeting parameters globally across "
                "the timeline.",
                "Native: right-click any knob/fader -> 'Create automation "
                "clip'.",
                "Third-party VST: Browser -> Current project -> Generators "
                "-> expand target plugin -> right-click parameter -> "
                "Create automation clip; or Tools -> Last tweaked.",
                "VST3 advantage: direct UI right-clicks behave like native "
                "tools.",
            ]),
            ("2. Automation limitations & initialized controls", [
                "Track-level constraint: automation clips operate at the "
                "global playlist/track level; no native per-note modulation "
                "for third-party synths.",
                "Per-note limits: restricted to Velocity, Pan, Release, "
                "Pitch, Mod X/Y; best on native engines.",
                "The 'initialized control' trap: creating a clip snapshots "
                "the current value; presets snap back at song start unless "
                "managed.",
                "Parameter ceiling: ~4,096 IDs practical, 32,767 absolute "
                "max.",
            ]),
            ("3. The loop wrap note choke mechanism", [
                "Deliberate protective behavior: truncates/chokes active "
                "notes at pattern loop boundaries to prevent hanging MIDI "
                "notes and engine hangs.",
                "Pure Pattern Mode: notes past the loop boundary get a "
                "note-off at the wrap point.",
                "Playlist Mode Slicing: shortening a clip mid-note lets "
                "the note decay by its release property by default, "
                "ignoring the visual slice.",
                "Mitigation: use Song/Playlist Mode with overlapping clips "
                "for continuous unchoked playback.",
            ]),
            ("4. Play truncated notes & advanced editing", [
                "Setting: Options -> Project general settings -> Advanced "
                "-> 'Play truncated notes in clips'.",
                "Enabling forces notes to respect visual playlist "
                "boundaries; notes choke at the clip truncation point.",
                "Splice restoration: the setting restores and plays notes "
                "at the truncation point.",
            ]),
        ],
        [
            "Pulsegrid takeaway: generator params need automation lanes "
            "(gen.p{id}) — the flagship gap vs. section 1.",
            "Pulsegrid takeaway: our loop-wrap 'choke' clears pending "
            "note-offs WITHOUT sending them — plugin voices hang. Must "
            "send real note-offs (section 3 done right).",
            "Pulsegrid takeaway: seed new automation lanes with the "
            "current param value (avoid the 'initialized control' trap, "
            "section 2).",
            "Pulsegrid takeaway: check clip-boundary note truncation "
            "behavior (section 4).",
        ],
    ),    (
        "Silent Choke Verification",
        "A technical guide to testing cut, truncation & loop-wrap behavior "
        "without audio: verify every choke with your eyes, not your ears -- "
        "meters, voices, waveforms, and events.",
        [
            ("1. Real-time visual meter monitoring (primary silent method)", [
                "Mute Master and watch signal activity on Channel Rack / "
                "Mixer / Master meters.",
                "When a note is choked, the meter should drop promptly "
                "(subject to the generator's release envelope).",
                "Test setup: long-held or high-release note; trigger "
                "overlapping notes, enable Cut itself / Cut-by groups, or "
                "truncate a clip mid-note; watch for the meter fall at the "
                "expected choke point.",
                "Companion settings: Cut / Cut by groups, Cut itself; "
                "'Play truncated notes in clips'; 'Play truncated notes on "
                "transport'.",
            ]),
            ("2. Polyphony / voice count indicators", [
                "Max polyphony slider forces release of oldest notes at the "
                "limit; voice-count display (CPU/voice panel) should drop "
                "when a choke occurs.",
                "Plugin Performance Monitor: per-plugin processing time; "
                "sustained activity after a choke point suggests voices not "
                "released.",
                "Mono mode interacts with choking (overrides Max Poly, "
                "enables portamento-style sliding).",
            ]),
            ("3. Offline waveform analysis (most precise / deterministic)", [
                "Render the choke scenario to WAV; open in a waveform "
                "editor; zoom to the expected choke point.",
                "Look for an abrupt amplitude drop / missing tail / clean "
                "silence after the choke point.",
                "Compare renders side-by-side: choke on vs off, truncated "
                "notes on vs off, different Cut groups.",
                "Powerful for loop-wrap choking: inspect multiple cycles or "
                "the exact sample where energy stops.",
            ]),
            ("4. MIDI / event-level observation", [
                "MIDI Settings -> Debug Log: incoming MIDI incl. note-offs "
                "logged.",
                "Score Logger: dump recent MIDI activity to a pattern.",
                "Scripting: midiNoteOn/note-off callbacks, piano-roll "
                "scripting to trigger and log note events at choke points.",
            ]),
            ("5. Specific scenarios & known behaviors to test", [
                "Loop-wrap choking: short looping pattern with a note "
                "crossing the boundary; render and inspect for a clean cut.",
                "Truncated pattern clips: shorten a clip mid-note; confirm "
                "the note stops at the clip edge.",
                "Cut groups across channels; step sequencer vs piano roll "
                "cut reliability; edge cases (high poly, mono, bridged "
                "plugins, high-release envelopes masking note-offs).",
            ]),
            ("6. Practical silent test workflow", [
                "Mute Master -> long-release generator + test notes -> "
                "configure choke setting -> watch meters + voice count -> "
                "export section -> inspect waveform -> optionally log MIDI "
                "events / Performance Monitor.",
            ]),
        ],
        [
            "Pulsegrid takeaway: we have no meters, no voice-count display, "
            "and no event log -- build a Debug window with all three.",
            "Pulsegrid takeaway: offline render + waveform analysis can be "
            "automated into a one-click 'verify choke' check.",
            "Pulsegrid takeaway: an engine event log (note on/off/choke "
            "with sample positions) is the precise equivalent of FL's MIDI "
            "debug log.",
        ],
    ),
    (
        "Vector Engine & Graphics",
        "FL Studio's vector graphics engine and rendering architecture: how "
        "the GUI stays smooth at any resolution and frame rate. Covers the "
        "v12 vector rewrite, frame-rate/smoothing controls, low-level "
        "rendering optimizations (dirty-region painting, PBO, VSync), GUI "
        "resource bottlenecks, and a practical diagnostic workflow. Philip "
        "attached this while reporting Pulsegrid felt 'choppy' and flashed "
        "on every adjustment -- the direct motivation for our smoothness pass.",
        [
            ("1. Vector graphics foundation (the modernization leap)", [
                "The Vector Leap: rewritten natively in v12 (2015) to replace "
                "old bitmap assets for infinite, clean scaling across any "
                "display form-factor.",
                "High-DPI futureproofing: razor-sharp on 4K/5K/8K with "
                "anti-aliased edge curves.",
                "Modern aesthetics: flat-but-shaded elements, cohesive color "
                "palettes, optimized contrast (away from heavy skeuomorphic "
                "3D bevels).",
                "Stock plugin overhaul: native instrument/effect GUIs "
                "progressively converted to vectors to prevent blurry "
                "interfaces or resolution mismatch.",
            ]),
            ("2. Frame-rate & smoothing controls (General options)", [
                "Smoothing slider: controls motion interpolation of knobs and "
                "window boundaries for fluid, responsive adjustments during "
                "mouse drag sequences.",
                "Animation refresh rate: GUI rendering intervals relative to "
                "target hardware screen refreshes -- Less Smooth (~1/4 "
                "monitor rate), Smooth (~1/2), Ultrasmooth (1:1 match, "
                "unlimited frame ceiling).",
                "Animations level: 'Don't distract me' (minimal) up to "
                "'Entertain me' (full visual detail), letting users tune "
                "visual overhead.",
            ]),
            ("3. Low-level rendering optimizations", [
                "Dirty-region painting: only the specific bounding rectangles "
                "that change are re-rendered, drastically lowering CPU draw "
                "calls during window interaction.",
                "Pixel Buffer Objects (PBO): OpenGL extensions accelerate "
                "buffer copies directly into graphics VRAM for immediate "
                "rendering feedback.",
                "VSync synchronization: canvas flushes sync to vertical "
                "refreshes to banish visual tearing and window stuttering.",
                "Force Refreshes toggle: obligates the OS window manager to "
                "update on time, preventing ghosting artifacts on high-speed "
                "animations.",
            ]),
            ("4. GUI resource & performance bottlenecks", [
                "Analyzer overhead: multiple complex plugin GUIs open with "
                "continuous live spectral/meter redraws kills host interface "
                "frame-rates.",
                "Global Emergency Choke: Alt+F12 instantly closes all active "
                "plugin windows to reclaim host rendering resources.",
                "OS handles & drivers: heavy graphical instances consume vast "
                "GDI handles (Windows) and need optimized system power "
                "profiles to avoid stutters.",
            ]),
            ("5. Practical workflow & diagnostic summary", [
                "1. Set animation refresh rate to Ultrasmooth to unleash the "
                "monitor's full frame capability during layout navigation.",
                "2. Check diagnostics (Help -> Diagnostics) to verify partial "
                "dirty-region redraws are fully active.",
                "3. Use native v21+ themes: customize contrast/hue/brightness "
                "without heavy bitmap rendering weight.",
                "4. High performance plan: keep GPUs on High Performance "
                "power plans to minimize audio-thread DPC latency spikes.",
                "5. Deploy window collapse: periodically use Alt+F12 to keep "
                "visual processing pools unburdened.",
            ]),
        ],
        [
            "Pulsegrid takeaway: the 'flash on every adjustment' is the "
            "absence of dirty-region painting -- our canvases delete('all') "
            "and redraw everything on each drag motion event.",
            "Pulsegrid takeaway: implement incremental canvas updates "
            "(coords/itemconfig on existing items) for piano roll, playlist, "
            "and automation drags.",
            "Pulsegrid takeaway: throttle drag redraws to one per frame "
            "(coalesce <B1-Motion> events; ~60 Hz cap) instead of redrawing "
            "per event.",
            "Pulsegrid takeaway: add a user-facing refresh/smoothing setting "
            "(mirroring FL's animation refresh rate) so low-power machines "
            "can trade visuals for responsiveness.",
            "Pulsegrid takeaway: keep audio-thread work out of UI handlers; "
            "UI polls engine state on a timer instead of pushing per event.",
        ],
    ),
    (
        "Recording Systems",
        "FL Studio's recording architecture: audio & MIDI input, patterns, "
        "playlists, latency, quantizing, and the score logger. 'Flexible "
        "recording. Low latency. Multiple data types. Built for creativity.' "
        "Philip attached this to motivate recording in Pulsegrid -- our "
        "biggest functional gap (no input capture at all).",
        [
            ("1. High-level architecture", [
                "Four data types via the Recording filter: AUDIO (internal "
                "mixer audio or external mic/line-in), NOTES (MIDI note "
                "on/off, velocity, pitch-bend, aftertouch), AUTOMATION "
                "(knobs, faders, mouse, MIDI CCs), CLIPS (triggered "
                "Pattern/Audio/Automation clips in Performance Mode).",
                "PATTERN MODE: data goes to the selected pattern.",
                "SONG MODE: pattern clips auto-placed/expanded in the playlist.",
                "Multi-type recording, loop recording (blend/overdub), "
                "count-in, input quantizing.",
            ]),
            ("2. MIDI input recording system", [
                "Device handling (Options -> MIDI Settings): devices appear "
                "in the Input list (detected by OS); enable device, set "
                "controller type; port assignment, access mode; MIDI "
                "activity light blinks on input; debug log shows raw MIDI.",
                "Channel routing: right-click channel -> Receive notes from "
                "(lock to a device/channel); prevents 'all notes to selected "
                "channel'.",
                "Recording flow: (1) Right-click Pattern + Instrument "
                "Channel. (2) Right-click Record -> check Notes (+ "
                "Automation). (3) Optional: Loop + Blend (overdub). "
                "(4) Optional: set Global Snap. (5) Arm (red), enable "
                "count-in, press Play, perform. (6) Song mode: pattern clip "
                "auto-placed/expanded.",
                "SCORE LOGGER (always on): buffers last ~30 min; captures "
                "external controllers + typing keyboard; independent of the "
                "Record button; Tools -> Dump score log to selected pattern; "
                "cleared on restart. Great for ideas!",
                "Additional MIDI features: typing keyboard acts as a piano "
                "(toolbar option); step entry mode; playback tracking "
                "offset; Python API (transport.record(), midiNoteOn()).",
            ]),
            ("3. Audio input recording system", [
                "Driver & low-latency foundation: ASIO (Windows) / Core "
                "Audio (macOS); buffer length = latency (lower = less "
                "latency / more CPU); typical 128-512 samples; latency(ms) "
                "= (buffer size / sample rate) x 1000.",
                "Routing & arming: in Mixer select Insert track, choose "
                "input, arm track (disk/arm button), set pickup point; "
                "external audio blends with internal signals.",
                "Latency compensation: PDC handles plugin latency; manual "
                "input delay; playback tracking source (Driver/Hybrid/"
                "Mixer); compensation removes buffer delay at start.",
                "Recording flow: configure driver -> route input & arm -> "
                "select Audio -> count-in/loop -> Play captures audio clip.",
            ]),
            ("4. Shared infrastructure & implementation", [
                "Transport & modes: Pattern vs Song mode = destination; "
                "Loop Record + Blend = overdub.",
                "Timing/quantize: Global Snap = real-time MIDI quantize; "
                "post-recording quantizers (piano roll).",
                "Latency & tracking: playback tracking source + offset; "
                "critical for audio alignment & MIDI timing.",
                "Filtering: recording filter selects data types (e.g. "
                "automation without notes).",
                "Undo/cancel: cancel current recording (before Stop); undo "
                "after the fact.",
                "Scripting/API: transport module (record/start/stop); "
                "channels & device modules; callbacks for MIDI events.",
                "Limitations: selected channel receives unassigned MIDI by "
                "default; FL Studio ASIO can introduce jitter.",
            ]),
            ("5. Practical impact", [
                "Why it works: workflow flexibility (pattern-centric); "
                "always-on score recovery; multi-type simultaneous "
                "recording; professional low-level controls.",
                "The result: audio tightly integrated with mixer and "
                "latency engine; MIDI event-based, pattern-oriented, "
                "backed by a circular buffer (Score Logger).",
            ]),
        ],
        [
            "Pulsegrid takeaway: no audio/MIDI input exists -- the honest "
            "scope is (a) typing-keyboard piano (infographic lists it!), "
            "(b) an always-on capture buffer (score logger), (c) record "
            "arm + count-in + input quantize. Real audio input needs engine "
            "input streams + hardware we don't have.",
            "Pulsegrid takeaway: typing keyboard -> live notes on the "
            "selected channel; capture buffer timestamps against the "
            "transport; 'Dump capture to pattern' commits with optional "
            "quantize.",
            "Pulsegrid takeaway: Pattern mode = capture goes to the "
            "current pattern; Song mode can auto-place (future).",
        ],
    ),
    (
        "FL Studio vs LMMS: GUI Architecture & Rendering Engine",
        "A technical comparison of two DAW UI philosophies: FL Studio's "
        "proprietary custom vector UI (Blend2D since 2024) vs LMMS's "
        "open-source Qt Widgets architecture (transitioning to Qt6/QML). "
        "'Different tools. Different philosophies. Both make music.' "
        "Philip attached this to guide Pulsegrid's UI architecture.",
        [
            ("1-2. The two UI stacks", [
                "FL STUDIO: proprietary, highly custom C++ UI system; "
                "vector-based since FL Studio 12 (2015); Blend2D rendering "
                "since 2024; built-in high-DPI scaling; specialized "
                "editors (Playlist, Piano Roll, Mixer).",
                "LMMS: open-source C++ on Qt Widgets; Qt5 historically, "
                "Qt6 transition underway; exploring QML/QtQuick; Model/View "
                "architecture; Core/UI separation in progress.",
            ]),
            ("3-4. Graphics evolution", [
                "FL: <=11 bitmap/custom -> 12 '100% vectorial' (sharp on "
                "4K/5K/8K) -> 20/21 mature vector + high-DPI -> 2024+ "
                "Blend2D (faster graphics, lower CPU).",
                "LMMS: Qt5 (current stable) -> Qt6 (optional build, active "
                "dev) -> QML/QtQuick (experimental proof of concept) -> "
                "future: Core/UI separation + alternative frontends.",
            ]),
            ("5-6. Rendering pipelines", [
                "Blend2D: 2D vector graphics engine with JIT compiler; "
                "vector drawing, text, clipping, rasterizing, compositing; "
                "JIT + SIMD optimization; multiple backends. A drawing "
                "engine, not a widget toolkit: drawRect/drawPath/drawText, "
                "then build the UI system on top.",
                "Qt: QWidget event handling/layouts -> QPainter drawing "
                "API -> widget rendering (custom painting) -> OS window. "
                "Gets mouse/keyboard/focus/accessibility/layouts/fonts/"
                "menus for free, but imposes architectural constraints.",
            ]),
            ("7-8. Custom controls & Model/View", [
                "FL builds specialized controls that don't map to standard "
                "widgets: piano roll, step sequencer, playlist, mixer, "
                "waveform/spectrum displays, knobs, envelope editors, "
                "meters, XY pads, draggable clips.",
                "LMMS uses Model/View: Model (data/state) -> View (Qt "
                "widgets, custom views) -> Controller (input/interaction). "
                "Custom widgets: PianoView, TrackView, MixerView, etc. "
                "March 2026: 'Pianoroll refactor into Pianorollpainter' -- "
                "separating state/interaction from rendering.",
            ]),
            ("9. Plugin embedding", [
                "FL: proprietary Plugin Wrapper (VST 1/2/3, AU, CLAP); "
                "common host functionality; unified interface; native vs "
                "third-party scaling handled separately.",
                "LMMS: Qt platform embedding (QWidget/HWND on Windows); "
                "platform-specific code; experimental QML support.",
            ]),
            ("10. LMMS Core/UI separation (in progress)", [
                "Current: GUI and Core tightly coupled. Target: QML/Qt GUI "
                "-> Interface Layer -> Core (audio, project, plugins). "
                "Benefits: less Qt in core, alternative frontends, cleaner "
                "architecture, easier testing. QML prototype even ran on "
                "Android.",
            ]),
            ("11-13. Performance & confirmed-vs-inferred", [
                "FL: Blend2D optimized rendering; vector UI (not bitmap "
                "scaling); high refresh rate support (120Hz+; 120Hz limit "
                "removed in 2025); custom scene/renderer; graphics treated "
                "as a performance-sensitive subsystem.",
                "LMMS: Qt widget rendering efficient; heavy widget "
                "hierarchies can be costly with large UIs; ongoing "
                "refactors (piano-roll painter, lock-free queues, "
                "track/mixer decoupling).",
                "CONFIRMED: FL 12 vector UI; FL 2024 Blend2D; LMMS Qt/C++; "
                "LMMS Qt6 + QML prototype. INFERRED: FL's exact widget "
                "classes, render-thread model, dirty-rect algorithm, "
                "CPU/GPU division (not publicly documented).",
            ]),
            ("14-15. The big picture: best of both worlds", [
                "FL: proprietary, custom, high-performance UI engine for a "
                "unique DAW workflow. LMMS: open, Qt-based, evolving toward "
                "Core/UI separation and QML.",
                "Proposed ideal: FL's rendering philosophy (vector + "
                "custom UI + specialized renderers) + LMMS's modular "
                "architecture (Core/UI separation) = a modern DAW UI that "
                "is powerful, flexible, and future-proof.",
                "Concrete shape: DAW Core -> UI Interface -> UI State -> "
                "Retained Scene -> Custom Renderer (Blend2D) -> Window; "
                "with specialized PlaylistRenderer, PianoRollRenderer, "
                "MixerRenderer, WaveformRenderer, etc.",
            ]),
        ],
        [
            "Pulsegrid takeaway: we are on tkinter (CPU-rasterized, "
            "widget-heavy -- closer to LMMS's Qt than FL's Blend2D). We "
            "cannot adopt Blend2D without a full UI rewrite.",
            "Pulsegrid takeaway: the actionable lesson is LMMS's "
            "PianoRollPainter refactor -- separate interaction/state from "
            "rendering. Our v0.9.0 incremental canvas updates were step 1; "
            "v0.11.0 formalizes it with a dedicated PianoRollPainter.",
            "Pulsegrid takeaway: our Rust engine / Python UI split ALREADY "
            "achieves the Core/UI separation LMMS is working toward. "
            "Document this as an architectural strength.",
            "Pulsegrid takeaway: treat complex editors as draw-command "
            "surfaces (retained canvas items, dirty-region updates), not "
            "widget trees. Avoid widget-per-note/clip patterns.",
        ],
    ),
    (
        "FL Studio vs LMMS: Audio Engine & Signal Path",
        "How audio, MIDI and effects flow from start to finish in both "
        "DAWs -- based on official documentation, source code, and "
        "technical details. 'Same goal. Different architectures.' Philip "
        "attached this to guide Pulsegrid's engine architecture.",
        [
            ("FL 1. High-level architecture", [
                "Dependency-aware, multi-threaded audio engine: "
                "generators, mixer tracks and effects processed in a "
                "directed graph. Independent paths run in parallel; "
                "dependent chains must wait.",
                "All audio passes through the Mixer. Generators and "
                "effects processed in sequence per track.",
            ]),
            ("FL 2. Audio processing threads & multicore", [
                "Three thread kinds: GUI thread (UI updates), MIDI "
                "thread (I/O, events), Audio Processing Thread "
                "(high priority, real-time mixing, buffer deadlines).",
                "Worker threads: parallel processing of independent "
                "tracks, generators, effects; non-dependent paths.",
                "'Safe overloads': reduces audio priority to keep GUI "
                "responsive when CPU is maxed (vs freezing the GUI).",
                "Multithreading is NOT 'one plugin = one thread' -- it's "
                "parallel paths; stages within a dependent path stay "
                "sequential.",
            ]),
            ("FL 3. Signal path (example)", [
                "Generator (Synth/Sampler) -> Mixer Track (Insert) -> EQ "
                "(Plugin) -> Compressor (Plugin) -> Send (Parallel) -> "
                "Master (Processing) -> Output (Buffer).",
                "All audio passes through the Mixer. Effects processed in "
                "order. PDC aligns latency across tracks.",
            ]),
            ("FL 4. Key engine features (documented)", [
                "Plugin Delay Compensation (automatic + manual, per-track).",
                "Smart Disable: skips inactive plugins, saves CPU "
                "(per-plugin control; disabled during rendering).",
                "Plugin Threading: threaded/bridged (separate process).",
                "Playback Tracking: driver/mixer/hybrid + offset.",
                "Buffer Size 128-512 typical (lower = lower latency).",
                "Multithreaded Generators; Mixer Parallelization "
                "(independent tracks, dependency-aware).",
                "Offline Render: no realtime deadline, no underruns.",
            ]),
            ("FL 5-6. Plugin wrapper; real-time vs offline", [
                "Plugin Wrapper: unified VST/AU/CLAP interface; handles "
                "latency, routing, threading; can bridge plugins to a "
                "separate process.",
                "Real-time: buffer deadline (e.g. 256 samples); underruns "
                "cause clicks/pops; audio thread priority critical. "
                "Offline: no deadline; can take as long as needed.",
            ]),
            ("LMMS 1-2. Core architecture; rendering pipeline", [
                "LmmsCore -> Song -> AudioEngine -> Mixer -> AudioDevice "
                "-> Hardware. Clear engine/interface/device separation.",
                "renderNextBuffer(): 1. remove finished PlayHandles, 2. "
                "Song::processNextBuffer(), 3. queue worker jobs, 4. wait "
                "for workers, 5. apply pending model changes.",
                "Pipeline: PlayHandles (notes/samples) -> AudioPorts "
                "(instrument FX) -> FxChannels (channel FX) -> Mixer "
                "(routing/sends) -> Master -> AudioDevice.",
            ]),
            ("LMMS 3. Worker threads", [
                "Main thread: renderNextBuffer(), job coordination. "
                "Worker pool: PlayHandle jobs, AudioPort jobs, FxChannel "
                "jobs. Thread sync: requestChangeInModel() / "
                "doneChangeInModel() / runChangesInModel() to avoid races.",
                "Real stack trace: NotePlayHandle::play() -> "
                "PlayHandle::doProcessing() -> ThreadableJob::process() -> "
                "AudioEngineWorkerThread -> renderStageInstruments() -> "
                "renderNextBuffer().",
            ]),
            ("LMMS 4-5. Signal path; backend layer", [
                "Note Event -> Instrument (PlayHandle) -> AudioPort "
                "(Instrument FX) -> MixerChannel (Channel FX) -> Send -> "
                "Master (Mix) -> Output (Buffer).",
                "AudioDevice abstraction: ALSA, JACK, PulseAudio, SDL, "
                "PortAudio, sndio. Engine renders independently of the "
                "backend.",
            ]),
            ("LMMS 6-7. Developments; real-time vs rendering", [
                "2025: removing FIFO thread, simplifying buffer rendering. "
                "Ongoing: core/UI separation, QML experiments.",
                "Real-time: buffer size affects latency; underruns cause "
                "crackling; OS scheduling can interfere. Offline "
                "(ProjectRenderer): same AudioEngine, no deadline.",
            ]),
            ("Confirmed vs inferred", [
                "CONFIRMED (FL): realtime buffers, audio thread priority, "
                "Safe overloads, multithreaded generators/mixer, "
                "dependencies, PDC, Smart Disable, bridging, offline "
                "rendering.",
                "CONFIRMED (LMMS): full pipeline from source + docs.",
                "NOT PUBLIC (FL): exact scheduler, worker-pool classes, "
                "lock-free structures, graph data structures, thread "
                "count algorithm.",
            ]),
        ],
        [
            "Pulsegrid takeaway: our engine is single-threaded "
            "(sequential graph render). FL/LMMS both parallelize -- but "
            "multithreading is a major change needing profiling data we "
            "don't have.",
            "Pulsegrid takeaway: SMART DISABLE is the implementable win -- "
            "skip FX/plugin processing when a track is silent. Well-"
            "understood, testable headless, big CPU saving on sparse "
            "arrangements.",
            "Pulsegrid takeaway: our Graph (TrackStrip -> FX -> mix bus -> "
            "master) already mirrors the FL/LMMS signal path conceptually. "
            "Sends and PDC are future work (routing graph changes).",
            "Pulsegrid takeaway: keep the audio thread lock-free (we "
            "already do: try_read song slot, atomic debug state). Never "
            "block it on the UI.",
        ],
    ),
    (
        "FL Studio vs LMMS: Mixer Routing, Sends & Bus Architecture",
        "Two different approaches to flexible routing, creative control, "
        "and powerful mixing. FL uses a proprietary flexible mixer graph; "
        "LMMS uses an open-source graph with explicit routing objects. "
        "Philip attached this to guide Pulsegrid's mixer sends/buses.",
        [
            ("FL 1. Mixer architecture", [
                "The Mixer is a flexible routing graph. Any Insert can "
                "act as a bus, send, return, or subgroup (no special BUS "
                "object).",
                "500 Insert tracks + Master + Current. Multiple sends "
                "per channel. Feedback loop protection. Integrated EQ "
                "as the last stage before audio leaves a track.",
                "Create submix: inserts a Mixer Track between selected "
                "tracks and the Master.",
            ]),
            ("FL 2. Signal path & send types", [
                "NORMAL SEND (post-fader): Input -> FX Slots -> EQ/Proc -> "
                "Fader -> Send -> Bus/Other Track. Lowering the source "
                "fader lowers its send.",
                "FRUITY SEND (pre-fader/mid-chain): taps the signal at "
                "any point in the FX chain -> destination.",
                "SIDECHAIN (not mixed): source -> destination plugin "
                "input only (no audible mix); implemented as a routing "
                "connection with send level at zero.",
                "Routing options: 'Route to this only' (removes Master "
                "send -> true subgroup); send to multiple tracks; "
                "parallel processing.",
            ]),
            ("FL 3-4. Routing examples; internal processing", [
                "Parallel: Vocal -> Master AND Vocal -> Reverb Bus. "
                "Submix: Drums/Perc/Hats -> Drum Bus -> Master. "
                "Sidechain: Kick -> Bass Compressor (not mixed).",
                "Dependency-aware processing; multithreaded independent "
                "paths; PDC; Smart Disable; Safe overloads.",
            ]),
            ("LMMS 1. Mixer architecture (from source)", [
                "Graph of MixerChannels connected by MixerRoute objects. "
                "Master = Channel 0 (index 0).",
                "MixerChannel: m_sends (vector), m_receives (vector), "
                "m_fxChain, m_buffer, m_volumeModel, m_muteModel, "
                "m_dependenciesMet.",
                "MixerRoute: from (channel), to (channel), amount "
                "(FloatModel, 0.0-1.0).",
            ]),
            ("LMMS 2. Signal path & send processing (from source)", [
                "Incoming routes -> Channel Buffer (mixed audio) -> FX "
                "Chain (per-channel) -> Channel Output.",
                "Send math: DESTINATION += SENDER x SENDER VOLUME x SEND "
                "AMOUNT (via MixHelpers::addMultiplied or sample-exact "
                "variants).",
                "Sends are mixed BEFORE the destination FX chain. "
                "Supports sample-exact automation of send amounts.",
            ]),
            ("LMMS 3. Routing example; depth", [
                "Any channel can be a bus (Channel 5 = 'Drum Bus'). "
                "Multiple sends per channel; channels receive from "
                "multiple sources.",
                "Feedback loops prevented: isInfiniteLoop() / "
                "checkInfiniteLoop() recursively follow destination "
                "sends. Also prevents A->A and Master as a send source.",
                "Dependency counters: channels with no incoming deps "
                "queue first; downstream queues when senders finish "
                "(graph-based worker scheduling).",
            ]),
            ("LMMS 4. Audio engine (source-based)", [
                "Rendering loop: remove finished PlayHandles -> "
                "Song::processNextBuffer() -> queue worker jobs -> wait "
                "-> process pending model changes.",
                "Backends: ALSA, JACK, PulseAudio, SDL, PortAudio, sndio "
                "(via AudioDevice).",
            ]),
            ("Side-by-side comparison", [
                "Routing unit: FL Mixer Track / LMMS MixerChannel. Bus: "
                "any Insert / any MixerChannel (no special class).",
                "Send type: FL post-fader normal, Fruity Send pre-fader / "
                "LMMS channel-to-channel buffer mix.",
                "Sidechain: FL explicit / LMMS no dedicated equivalent "
                "in core routing.",
                "Feedback protection: FL yes / LMMS isInfiniteLoop().",
                "PDC: FL extensive / LMMS less documented.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement LMMS-style MixerRoute -- a "
            "Send { to_track_id, amount } per track. Post-fader sends "
            "(like FL normal sends).",
            "Pulsegrid takeaway: send math = dest += src x amount, mixed "
            "BEFORE the destination FX chain (LMMS order).",
            "Pulsegrid takeaway: cycle detection on send creation "
            "(LMMS isInfiniteLoop pattern). No A->A, no cycles.",
            "Pulsegrid takeaway: any track can be a bus (no special "
            "class). 'Route to this only' = remove Master send (future).",
            "Pulsegrid takeaway: engine needs two-phase render -- voices "
            "first, then send mixing, then FX chains. Format v9 -> v10.",
        ],
    ),
    (
        "FL Studio vs LMMS: Plugin Presets, State & Project Serialization",
        "Three layers of state: Plugin Preset ('How should this plugin "
        "sound?'), Channel/Track State ('How should this channel behave?'), "
        "Full Project State ('How do we rebuild the session?'). FL uses "
        "proprietary layered state; LMMS uses source-visible XML. Philip "
        "attached this to guide Pulsegrid's preset system.",
        [
            ("The 3 levels of state", [
                "1. PLUGIN PRESET: 'How should this plugin sound?'",
                "2. CHANNEL/TRACK STATE: 'How should this channel behave?'",
                "3. FULL PROJECT STATE: 'How do we rebuild the entire "
                "session?'",
            ]),
            ("FL 1. Plugin presets (.fst / .fxp / .fxb)", [
                ".fst: state files for generator and effect presets (can "
                "also save channel/mixer state).",
                "VST: .fxp (single preset), .fxb (bank).",
                "Plugin presets store the plugin's parameters and "
                "internal state.",
            ]),
            ("FL 2. Channel state (.fst)", [
                "'Save Channel State As' stores: VST wrapper settings, "
                "sample settings, miscellaneous functions, plugin/channel "
                "state.",
                "Includes channel controls, envelopes, and more. Channel "
                "State != Plugin Preset.",
            ]),
            ("FL 3. Mixer track state (.fst)", [
                "Saves track settings and plugin filters. Can be dragged "
                "onto another Mixer Track to duplicate the FX chain.",
            ]),
            ("FL 4. Full project state (.flp / .zip)", [
                ".flp contains: pattern/playlist data, channels, mixer "
                "config, plugin/effect settings, automation.",
                ".zip projects can include sample data.",
            ]),
            ("LMMS 1. Presets", [
                "Native: saved via saveState()/loadState(). Stores plugin "
                "parameters and internal state.",
                "VST: tries state chunk (binary), falls back to individual "
                "parameters. Saved as Base64 in XML.",
                "File types: .xpf (LMMS instrument preset), .fxp/.fxb "
                "(VST).",
            ]),
            ("LMMS 2. Instrument track state (.xpf)", [
                "InstrumentTrackSettings. Saved data: volume, panning, "
                "pitch, pitch range, base note, key range, master-pitch, "
                "microtuner, MIDI CC config, instrument + sound shaping, "
                "arpeggio, note stacking, effect chain (with FX state).",
                "An instrument preset can carry its own effect chain.",
            ]),
            ("LMMS 3. FX & mixer state", [
                "Each effect stores: name, plugin key, effect state.",
                "If a plugin is missing, a DummyEffect is created "
                "(preserves the serialized data).",
            ]),
            ("LMMS 4. Full project (.mmp / .mmpz)", [
                ".mmp = uncompressed XML. .mmpz = compressed project.",
                "Stored via SerializingObject + DataFile.",
            ]),
            ("LMMS 5. Serialization architecture (from source)", [
                "SerializingObject (saveSettings/loadSettings) -> "
                "DataFile (XML writer/reader) -> Project/Track/Plugin.",
                "Explicit presetMode: project save includes contextual "
                "info; preset save is portable/reusable.",
            ]),
            ("Key differences", [
                "FL: proprietary, optimized; .fst/.flp; no public source.",
                "LMMS: Qt-based, open source; explicit MixerRoute graph; "
                "XML serialization; DummyEffect for missing plugins.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement 3-layer preset system -- effect "
            "preset, FX chain preset, track preset (like LMMS .xpf).",
            "Pulsegrid takeaway: track preset = generator + FX chain + "
            "gain/pan (portable, no sends to other tracks).",
            "Pulsegrid takeaway: use JSON for presets (human-readable, "
            "like our project format).",
            "Pulsegrid takeaway: investigate CLAP state extension for full "
            "plugin state blobs (currently only params saved).",
            "Pulsegrid takeaway: handle missing plugins gracefully "
            "(LMMS DummyEffect pattern).",
        ],
    ),
    (
        "FL Studio vs LMMS: Velocity Lane, Per-Note Properties & Data Models",
        "How each DAW represents, stores, and edits the thing users call "
        "the 'velocity lane'. Core idea: velocity is attached to an "
        "individual note, not the channel/mixer volume fader. Philip "
        "attached this (PPTX) to guide Pulsegrid's velocity lane.",
        [
            ("Core idea", [
                "Velocity is attached to an individual note -- not the "
                "channel/mixer volume fader.",
                "NOTE: pitch, start time, duration, velocity, other "
                "properties. Velocity is delivered to the instrument at "
                "note trigger; what it does (loudness, timbre, filter, "
                "sample layer) depends on the instrument.",
            ]),
            ("FL: lower editor is per-note property editor", [
                "Piano Roll lower Note/Event Editor displays: Velocity, "
                "Pan, Release velocity, Pitch, Filter cutoff / Mod X, "
                "Resonance / Mod Y, automation/event data.",
                "Target selector switches the same lower area between "
                "properties.",
                "Bar height = note velocity.",
            ]),
            ("FL: velocity is a note property (0.0-1.0)", [
                "Piano Roll API: note.velocity, 0.0=min, 1.0=max, default "
                "0.8. MIDI 127 = FL 1.0.",
                "Conceptually: FL NOTE { time, length, pitch, velocity, "
                "pan, release, Mod X/Y, color/channel }.",
                "Velocity isn't just volume -- instruments map it to "
                "amplitude, filter, envelope, sample layer, etc.",
            ]),
            ("FL: multiple edit paths", [
                "Event Editor (Target -> Velocity); Alt+mouse wheel over "
                "notes; Note Properties dialog (VEL numeric).",
                "Note properties move with their notes (not independent "
                "timeline curves).",
                "Chords: independent velocities per note; Note Properties "
                "edits them separately.",
            ]),
            ("FL: note property vs event data", [
                "Lower editor shows NOTE PROPERTY data (velocity, pan, "
                "release, Mod X/Y -- 'lollipops' that move with notes) "
                "and STANDARD EVENT AUTOMATION (separate system, supports "
                "interpolation).",
            ]),
            ("FL: velocity tracker", [
                "Channel Settings velocity tracker modulates Pan, Cutoff, "
                "Resonance, Mod X/Y from note velocity.",
            ]),
            ("LMMS: 'Note Volume' lane", [
                "Piano Roll toggle: NOTE VOLUME / NOTE PANNING. Manual: "
                "'The volume of each note is termed velocity in music "
                "sequencing.' Green vertical bars beneath notes.",
                "Higher velocity -> note brighter; lower -> dimmer (two "
                "visual representations: bar height + note brightness).",
            ]),
            ("LMMS: m_volume 0-200 (source-level)", [
                "Note class: volume_t m_volume (not m_velocity); "
                "getVolume()/setVolume(); UI labels it 'Note Velocity'.",
                "volume.h: MinVolume=0, DefaultVolume=100, MaxVolume=200.",
                "midiVelocity(): min(MIDI max, volume * base / 100), "
                "clamped. Default 100 = multiplier 1.0.",
                "Serialized as <note vol='...'/> attribute.",
            ]),
            ("LMMS: two per-note lanes (source)", [
                "NoteEditModes: Note Velocity, Note Panning. Specialized "
                "note-property editor, not generic automation.",
                "m_lastNoteVolume (default 100): new notes use last/current "
                "volume.",
                "Note::ParameterType: Detuning has automation-curve "
                "support; velocity/panning are direct fields (planned "
                "for future).",
            ]),
            ("Numerical scales side-by-side", [
                "MIDI: 0-127. FL API: 0.0-1.0 (default 0.8; 127=1.0). "
                "LMMS internal: 0-200 (default 100; converted to MIDI).",
                "Different internal data models, not just different UIs.",
            ]),
        ],
        [
            "Pulsegrid takeaway: notes already use vel 0.0-1.0 (FL model). "
            "Add a dedicated velocity lane: vertical bars under the piano "
            "roll, draggable.",
            "Pulsegrid takeaway: velocity bars move with notes (note-"
            "attached, not timeline automation).",
            "Pulsegrid takeaway: keep Alt+drag on notes AND add lane "
            "editing (multiple UI paths, like FL).",
            "Pulsegrid takeaway: note brightness already reflects velocity "
            "(like LMMS); lane bars give precise editing.",
            "Pulsegrid takeaway: default new-note velocity 0.8 (FL default).",
        ],
    ),
    (
        "FL Studio vs LMMS: Multithreading / Multicore Audio Processing",
        "How each DAW exploits multiple CPU cores while respecting audio "
        "dependency graphs. Core problem: real-time audio isn't 'give every "
        "plugin a different core' -- FX Slot 2 can't run until Slot 1 "
        "produces audio. Independent mixer tracks -> parallel; shared "
        "sends/serial chains -> serial. Philip attached this (PPTX) to "
        "guide Pulsegrid multithreading (must work on Windows 11).",
        [
            ("The fundamental problem", [
                "Audio engines have dependencies: Instrument -> Mixer "
                "Track -> FX1 -> FX2 -> FX3 -> Routed Track -> Master.",
                "Useful parallelism is between independent branches; "
                "dependent operations stay sequential.",
                "Image-Line: instruments/effects on the same Mixer path "
                "can't be simultaneous; independent Mixer tracks can.",
            ]),
            ("FL: architecture", [
                "Two global switches: Multithreaded generator processing, "
                "Multithreaded mixer processing (Audio Settings).",
                "Wrapper -> Allow threaded processing (per-plugin opt-out "
                "for misbehaving plugins).",
                "Each independent Mixer Track = parallel opportunity. "
                "Image-Line: put CPU-heavy plugins on independent tracks; "
                "avoid shared sends for max multicore.",
            ]),
            ("FL: CPU meter measures buffer time", [
                "FL's CPU meter = how much of the audio-buffer time is "
                "consumed, not OS CPU %. Can underrun while OS shows low "
                "utilization.",
                "Single core can bottleneck even with idle cores (serial "
                "dependency chain).",
            ]),
            ("FL: thread priority, Safe overloads", [
                "Audio processing thread priority configurable. Safe "
                "overloads leaves GUI budget during overload.",
                "Apple Silicon: Audio Workgroups API for deadline-aware "
                "scheduling.",
            ]),
            ("FL: Smart Disable != multithreading", [
                "Smart Disable skips inactive plugins (reduces load, no "
                "parallelism). Disabled during rendering.",
                "Bridging (separate process) != multithreading.",
            ]),
            ("LMMS: worker pool (source-visible)", [
                "AudioEngineWorkerThread, ThreadableJob, atomic JobQueue "
                "(8192 entries).",
                "Worker count = QThread::idealThreadCount() - 1; last "
                "worker runs inline in AudioEngine thread (reduces sync "
                "latency).",
                "Job states: UNSTARTED -> QUEUED -> IN PROGRESS -> DONE "
                "(atomic CAS claim).",
                "Workers started with QThread::TimeCriticalPriority.",
                "Busy-wait in JobQueue::wait() (avoid sleep/wake latency).",
            ]),
            ("LMMS: staged renderer", [
                "renderNextPeriod(): STAGE 0 Note Setup -> STAGE 1 "
                "Instruments (PlayHandle jobs) -> STAGE 2 Effects "
                "(AudioBus jobs) -> STAGE 3 Master Mix.",
                "Stages ordered; jobs within stages parallelized.",
                "Model changes synchronized via requestChangeInModel() "
                "(m_changeMutex).",
            ]),
            ("LMMS 1.3 development (2026)", [
                "Removed FIFO thread; reworked audio output path.",
                "Proposed new plugin API with explicit thread contracts "
                "(which thread may call what).",
                "Only one worker interacts with a given plugin per period "
                "(multi-stream exception deemed undesirable).",
            ]),
            ("Side-by-side", [
                "FL: proprietary dependency-aware graph; parallel "
                "independent paths, serial dependent chains.",
                "LMMS: source-visible staged renderer + atomic worker-job "
                "pool; 8192-queue, inline last worker.",
                "Both: more cores only help parallelizable work; serial "
                "dependencies limited by single-thread speed.",
            ]),
        ],
        [
            "Pulsegrid takeaway: Phase 1 (voice rendering) is dependency-"
            "free -- parallelize with rayon (cross-platform, Windows 11).",
            "Pulsegrid takeaway: Phase 2 (sends+FX) uses topological order "
            "-- keep sequential for correctness (dependencies).",
            "Pulsegrid takeaway: each track processed by one thread; CLAP "
            "instances are per-track (safe). Document per-plugin opt-out "
            "as future (FL pattern).",
            "Pulsegrid takeaway: add MT enable/disable setting (FL's two "
            "switches simplified to one).",
            "Pulsegrid takeaway: rayon can't set thread priority (LMMS "
            "TimeCriticalPriority not available) -- document limitation.",
        ],
    ),
    (
        "FL Studio vs LMMS: Plugin Delay Compensation (PDC)",
        "How each DAW handles timing offsets from plugins. PDC doesn't make "
        "slow plugins faster -- it delays fast paths so all arrive at "
        "Master simultaneously. PDC != audio-buffer latency != plugin "
        "processing time != monitoring latency. Philip attached this (PPTX) "
        "to guide Pulsegrid PDC implementation.",
        [
            ("What PDC solves", [
                "Lookahead compressor (1024 samples) makes one path late: "
                "Track A (0) vs Track B (1024) misaligned -> phase issues, "
                "transient smearing.",
                "PDC: delay Track A by 1024 so both arrive together.",
            ]),
            ("FL: Automatic PDC", [
                "Detects plugin latency, builds Mixer-path delay map, "
                "delays faster paths. Default for new projects.",
                "Operates on Mixer routing graph (not just per-plugin).",
                "Follows inter-track routing, multi-I/O, sidechains.",
                "Manual PDC offsets combine with automatic (total = auto "
                "+ manual).",
                "Per-plugin manual latency correction in Wrapper.",
                "Updates dynamically when latency changes.",
                "Wet/dry mix latency-compensated inside FX slots.",
                "Automation compensated for plugin delay.",
            ]),
            ("FL: trade-offs", [
                "PDC increases monitoring latency (everything delayed to "
                "align). Live-monitor PDC bypass for recording.",
                "Track Delay control doubles as manual micro-offset.",
            ]),
            ("LMMS: no general APDC", [
                "No equivalent general automatic PDC for arbitrary Mixer "
                "paths. Has audio buffer latency (I/O, not PDC), "
                "plugin-format latency concepts, but no host-wide "
                "compensation graph.",
                "2026 plugin API redesign may enable future PDC (needs "
                "well-defined latency contracts).",
            ]),
            ("Side-by-side", [
                "FL: documented automatic PDC on Mixer graph; manual "
                "offsets; per-plugin correction; routing-aware.",
                "LMMS: no general APDC layer today; buffer latency only.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement PDC -- each FX reports "
            "latency_samples(); compute per-track latency via topo order; "
            "delay faster tracks to max latency.",
            "Pulsegrid takeaway: CLAP latency extension for plugins; "
            "built-in FX report 0 (no lookahead).",
            "Pulsegrid takeaway: delay lines as ring buffers per track "
            "(post-FX, pre-master).",
            "Pulsegrid takeaway: with sends, track latency = max(sender "
            "latencies) + own FX latency (topo order).",
        ],
    ),
    (
        "FL Studio vs LMMS: Generator Layering (note fan-out)",
        "How each DAW turns one musical event into multiple sound sources. "
        "FL has a dedicated Layer Channel (host-level note fan-out node); "
        "LMMS has no generic Layer -- layers via multiple InstrumentTracks, "
        "MIDI routing, or Note Stacking (one note -> many notes -> SAME "
        "instrument). Philip attached this (PPTX) to guide Pulsegrid "
        "generator layering.",
        [
            ("Four kinds of layering", [
                "A. Host note fan-out: ONE NOTE -> Layer -> many generators "
                "(FL Layer).",
                "B. Independent tracks: same notes on multiple tracks "
                "(LMMS normal approach).",
                "C. Generator-internal: multiple oscillators in one "
                "generator (both DAWs).",
                "D. Note transformation: one note -> chord notes -> same "
                "instrument (LMMS Note Stacking).",
            ]),
            ("FL Layer Channel", [
                "Does not make sound itself; passes Piano Roll/controller "
                "data to linked child Channels (note fan-out node).",
                "Children: Sampler/Native/VST (not another Layer).",
                "Default: all children play simultaneously.",
                "Per-child volume/pan/pitch (layer-context only).",
                "Pitch per child: octave stacks, detune, harmony.",
                "Keyboard-region splitting (multi-sample instruments).",
                "Split Children: distribute children across keyboard.",
                "Crossfade mode (native plugins only).",
                "Random mode: one random child per note.",
                "Sequential mode: round-robin across children.",
                "Velocity/modulation layering concepts.",
            ]),
            ("FL other layering", [
                "FPC: per-pad velocity-range sample layers (overlap, "
                "random, cycle).",
                "Patcher: general modular audio/MIDI network (Image-Line "
                "calls it more flexible than Layer).",
            ]),
            ("LMMS", [
                "InstrumentTrack holds ONE Instrument*; no generic Layer "
                "object.",
                "Layering = multiple InstrumentTracks + duplicated MIDI "
                "material; tracks cloneable.",
                "Note Stacking: one note -> up to 13 chord notes (95 chord "
                "tables, 1-9 octave range) -> same instrument; preserves "
                "length/volume/pan/detune; generated at note-play time.",
                "Generator-internal: TripleOscillator (3 osc, Mix/AM/PM/FM/"
                "Sync).",
            ]),
            ("One-liner", [
                "FL: NOTE -> LAYER -> MANY GENERATORS.",
                "LMMS: NOTES -> MANY INSTRUMENT TRACKS -> MANY GENERATORS.",
                "LMMS Note Stacking: ONE NOTE -> MANY NOTES -> ONE "
                "GENERATOR.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement host-level note fan-out -- a "
            "track can host multiple generator layers; one note event is "
            "dispatched to all (or selected) layers.",
            "Pulsegrid takeaway: layer modes All / Random / Sequential "
            "(round-robin), like FL Layer.",
            "Pulsegrid takeaway: per-layer gain + pitch offset (semitones) "
            "+ mute; layer audio summed before the track FX chain.",
            "Pulsegrid takeaway: keyboard splitting and crossfade deferred "
            "-- modes + per-layer controls cover the core use case.",
        ],
    ),
    (
        "FL Studio vs LMMS: Song-Mode Capture / Auto-Place",
        "How each DAW turns a live MIDI performance into arranged content. "
        "FL's Song-mode recording is two operations: capture notes into a "
        "Pattern, then auto-place the Pattern Clip at the recording-start "
        "position. LMMS has MIDI/Piano Roll recording but no documented "
        "equivalent automatic clip placement. Philip attached this (PPTX) "
        "to guide Pulsegrid song-mode capture.",
        [
            ("Two operations, not one", [
                "LIVE MIDI -> NOTE CAPTURE -> PATTERN DATA -> CREATE/EXTEND "
                "CLIP -> PLACE AT RECORD START.",
                "Recording notes vs placing the arrangement object are "
                "separate.",
            ]),
            ("FL Song mode", [
                "Select Pattern; MIDI recorded into it; in Song mode FL "
                "auto-places the Pattern Clip at playback-start position.",
                "Empty Pattern Clip pre-placed -> recording fills/expands "
                "it on that track.",
                "Clip expands to fit recorded data.",
                "Placement anchor = T0 (record start), not stop position.",
                "Input quantization (start/end/both) before capture.",
            ]),
            ("FL other capture", [
                "Score Logger: rolling MIDI buffer, retrospective dump to "
                "Pattern.",
                "Burn MIDI: capture plugin-generated MIDI to Pattern.",
            ]),
            ("LMMS", [
                "MIDI/Piano Roll recording exists; 1.3 alphas actively "
                "fixing Record-Play, playhead, timeline sync.",
                "No documented equivalent of FL's automatic post-record "
                "clip placement.",
                "Timeline refactoring (positionChanged -> Timeline) is the "
                "natural anchor point for such a feature.",
            ]),
            ("Timing model", [
                "T0 = record start (placement anchor); T1 = now; T2 = stop.",
                "Note timestamp = MIDI time - T0 (relative to Pattern); "
                "clip.start = T0 (absolute).",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement song-mode auto-place -- on "
            "record start, remember T0 (playlist playhead in beats); "
            "capture notes relative to T0; on stop, create Pattern + Clip "
            "at T0.",
            "Pulsegrid takeaway: if an empty clip for the target pattern "
            "exists at T0, expand it instead of creating new.",
            "Pulsegrid takeaway: clip length = extent of recorded data "
            "(rounded up to whole bars).",
            "Pulsegrid takeaway: reuse existing input quantize from Piano "
            "tab.",
        ],
    ),
    (
        "FL Studio vs LMMS: Preset / Content Browser",
        "How each DAW lets you find, preview and load presets. FL has a "
        "unified persistent Browser (content OS); LMMS has File Browser + "
        "Plugin Browser pieces but preset loading stays file-oriented. "
        "Philip attached this (PPTX) to guide a proper Pulsegrid preset "
        "browser.",
        [
            ("Three meanings of preset browser", [
                "A. File dialog: transactional choose-file-load-close.",
                "B. Embedded file browser: persistent panel, tree+search, "
                "still path-oriented.",
                "C. Semantic browser: search 'warm bass', filter by "
                "instrument/category/character/favorite.",
            ]),
            ("FL Browser", [
                "First-class subsystem: projects, samples, plugins, "
                "presets, current-project data, favorites, online content; "
                "dockable; tabs (All/Current Project/Plugin DB/Starred).",
                "Recursive search, indexing, filtering as you type.",
                "Preview panel (samples, waveforms, plugin images).",
                "Drag-and-drop into Channels/Mixer/Plugins; context "
                "actions.",
                "Plugin Database: filesystem-backed (Effects/Generators/"
                "Installed), .fst/.nfo/.bmp per entry; feeds Plugin Picker.",
                "Plugin Picker (F8): visual grid, type-to-narrow.",
                "Browse presets opens plugin-relevant presets in Browser.",
                "Save Preset As -> FST -> Browser -> preset menu "
                "(full lifecycle).",
            ]),
            ("LMMS", [
                "File Browser: recursive search, tree, favorites (My "
                "Favorites), preview work, drag/drop, search-perf fixes.",
                "Plugin Browser: tree layout + search bar.",
                "But VST preset load/save still uses FileDialog "
                "(.fxp/.fxb) -- fragmented.",
                "Issue #4733 proposed tree+search+drag preset browser "
                "(closed, design only).",
            ]),
            ("Ideal architecture", [
                "Content service -> content index (path/type/plugin/"
                "category/favorite/metadata) -> search index -> browser "
                "panel -> preview/favorite/drag-drop/load.",
            ]),
        ],
        [
            "Pulsegrid takeaway: Browser tab already exists -- upgrade it "
            "toward a content browser: preset search, favorites, preview.",
            "Pulsegrid takeaway: add preset favorites (star) persisted in "
            "project or user config.",
            "Pulsegrid takeaway: plugin-aware preset filtering (show "
            "presets relevant to current plugin).",
            "Pulsegrid takeaway: keep filesystem-backed presets "
            "(.pulsegrid-preset) so the browser stays a thin view.",
        ],
    ),
    (
        "FL Studio vs LMMS: Route-Only / Subgroup Mode",
        "How each DAW forces source audio through a subgroup before "
        "Master. FL has explicit 'Route selected to this track only' "
        "(and 'Create submix...'); LMMS achieves subgroups by assigning "
        "instruments to the same FX channel. Philip attached this (PPTX) "
        "to guide exclusive subgroup routing in Pulsegrid.",
        [
            ("Normal send vs route-only", [
                "Normal send is parallel: SOURCE -> MASTER and "
                "SOURCE -> BUS -> MASTER (double contribution risk).",
                "Route-only is exclusive: SOURCE -> BUS -> MASTER; the "
                "source's direct Master path is removed.",
            ]),
            ("FL Studio", [
                "'Route selected to this track only': creates sends from "
                "selected tracks to the target and deselects their own "
                "Master sends.",
                "'Create submix...': inserts target between selected "
                "tracks and Master.",
                "Inverse exists: 'Route this track to selected only'.",
                "Also sidechain variants ('Sidechain selected to this "
                "track only').",
                "Routing graph is a directed audio graph between Mixer "
                "Tracks (sends, subgroups, sidechains, parallel paths); "
                "routing cables can be displayed.",
                "Nuance: 'only' can also alter/remove existing send "
                "relationships, not just the Master path.",
                "Channel -> Mixer Track (source entry) is a different "
                "layer from Mixer Track -> Mixer Track routing.",
            ]),
            ("LMMS", [
                "FX channels; Master is FX0; instruments default to "
                "channel 0 (Master).",
                "FX Mixer supports sends (select channel, click Send on "
                "another, adjust level).",
                "No documented one-command 'route only' equivalent; "
                "subgroup achieved by assigning instruments to the same "
                "FX channel (primary destination).",
                "Source-level routing stored on the track "
                "(m_mixerChannelModel / audio bus handle).",
                "AudioPort -> Mixer::mixToChannel(); inter-channel "
                "routing via MixerChannel::doProcessing().",
                "Routing UI long considered unintuitive; dedicated "
                "routing overview is a longstanding request.",
            ]),
            ("Abstraction", [
                "NORMAL ROUTE: addEdge(source, destination).",
                "ROUTE ONLY: addEdge(source, destination); "
                "removeEdge(source, master).",
            ]),
        ],
        [
            "Pulsegrid takeaway: add explicit route-output destination "
            "per track (default Master) + 'Route to this track only' "
            "mixer command (rewrites selected tracks' output to target, "
            "removing direct Master path).",
            "Pulsegrid takeaway: distinguish send (parallel, level) "
            "from output route (exclusive, series).",
            "Pulsegrid takeaway: guard against routing cycles; subgroup "
            "FX process before Master (dependency order).",
        ],
    ),
    (
        "FL Studio vs LMMS: Further Painter Extraction",
        "How far each DAW separates painting/rendering from "
        "interaction/model logic in Playlist/Mixer. LMMS gives strong "
        "source-level evidence (core/GUI split, but editors still combine "
        "paint+interaction); FL's internals are proprietary (only "
        "observable behavior is evidence-based). Philip attached this "
        "(PPTX, 10 slides) to guide renderer extraction in Pulsegrid.",
        [
            ("Separation levels", [
                "L0: widget does everything; L1: internal function split;",
                "L2: rendering helpers; L3: dedicated rendering layer "
                "(UI/interaction/presentation-state/renderer); L4: "
                "independent renderer with swappable backend.",
                "Real goal: visual representation independently "
                "renderable from interaction logic.",
            ]),
            ("Core principle", [
                "INTERACTION CHANGES STATE. RENDERING DISPLAYS STATE. "
                "AUDIO PROCESSING PROCESSES AUDIO. Do not conflate.",
            ]),
            ("Target architecture", [
                "PROJECT/AUDIO MODEL -> PRESENTATION STATE -> RENDERER "
                "(draw list) -> QPainter/GPU; Interaction Controller "
                "modifies model/view state independently.",
                "Extraction boundary: buildRenderState(project, "
                "viewState); renderer.paint(painter, state).",
                "Renderer must not know about mouse drags, shortcuts, "
                "undo, or how clips were created.",
            ]),
            ("Why playlists need it", [
                "Structural updates (move/resize/create/delete clip, "
                "zoom) are infrequent; continuous updates (playhead, "
                "meters, clip progress) are high-frequency.",
                "Separation enables: model changes occasionally -> "
                "presentation cache reused every frame -> renderer.",
            ]),
            ("LMMS evidence", [
                "src/gui/editors (SongEditor/PianoRoll/AutomationEditor) "
                "vs core/audio; Qt paint system; GUI bypassable for "
                "headless audio render (strong separation proof).",
                "But SongEditor/Mixer widgets still combine painting + "
                "interaction; no universal render-state abstraction; "
                "mixer peaks via Fader::peakChanged signal -> "
                "PeakIndicator (data->GUI, not engine painting).",
            ]),
            ("FL evidence (and limits)", [
                "Playlist is an arrangement surface (Pattern/Audio/"
                "Automation clips), not the Mixer signal graph.",
                "Performance Mode progress visuals can be disabled to "
                "reduce GUI load (visual cost is real, separate from "
                "DSP).",
                "NOT evidence-based: any internal PlaylistRenderer/ "
                "Controller classes, scene graphs, GPU backends.",
            ]),
            ("Mixer as projection", [
                "AUDIO GRAPH (real processing) -> MIXER UI MODEL (state "
                "projection) -> interaction + rendering. The UI does not "
                "become the audio engine by being visible.",
            ]),
        ],
        [
            "Pulsegrid takeaway: extract Playlist painting into a "
            "dedicated renderer module (like the existing "
            "pianoroll_painter.py pattern).",
            "Pulsegrid takeaway: build an explicit view/presentation "
            "state (zoom, scroll, selection, playhead) separate from the "
            "project model; renderer takes (render_state) only.",
            "Pulsegrid takeaway: keep audio engine free of all GUI "
            "painting (already true); meters flow engine->state->GUI.",
        ],
    ),
    (
        "FL Studio vs LMMS: CLAP State Blobs",
        "CLAP state extension integration: opaque plugin data, Base64, "
        "and project serialization. Central distinction: CLAP defines "
        "HOW state crosses the host boundary (save()/load() streams); "
        "the plugin defines WHAT the blob contains; the host defines HOW "
        "it is persisted. Base64/JSON are host serialization decisions, "
        "not CLAP requirements. Philip attached this (PPTX, 10 slides) "
        "to guide state-blob support in Pulsegrid.",
        [
            ("CLAP state extension", [
                "CLAP_EXT_STATE: plugin.save() -> host output stream; "
                "plugin.load() <- host input stream. Covers parameters "
                "AND non-parameter state (wavetable contents, sequencer "
                "patterns, routing, seeds, editor state).",
                "State is deliberately opaque: host must not parse it; "
                "plugin owns format and version migration.",
                "Parameters are not the whole state (param extension vs "
                "state extension are separate).",
            ]),
            ("Base64/JSON are not CLAP", [
                "CLAP supplies a stream interface; plugin writes bytes; "
                "host decides persistence. CLAP state != Base64, != JSON.",
                "Base64 is transport encoding, not state encoding: "
                "binary bytes -> text-safe representation (~33% size "
                "cost). Sensible for JSON/XML project formats.",
                "Structured container != structured plugin state: JSON "
                "{'state': 'VjMy...'} is still an opaque blob inside.",
            ]),
            ("State context / threading / dirty", [
                "State Context ext (CLAP 1.2.0): tells plugin WHY state "
                "is saved (project/preset/duplicate).",
                "Preset-load ext (clap.preset-load/2): load plugin's "
                "native preset file -- different from project state.",
                "save()/load() are main-thread in the stable ext; "
                "background-state-context ext exists for large states; "
                "never serialize on the audio thread.",
                "clap_host_state.mark_dirty(): plugin tells host its "
                "snapshot is stale (param changes imply dirty).",
                "Draft undo ext: binary deltas, or full save/load for "
                "undo/redo and crash recovery.",
                "Resources: state blob should stay small; large data "
                "via resource-directory, not Base64 in the project.",
            ]),
            ("FL Studio", [
                "CLAP since 2024.1, hosted through the Fruity Wrapper "
                "(compatibility layer + host-side settings).",
                "Two state categories: CLAP plugin state vs wrapper "
                "state (host settings); both must be preserved.",
                "Exact FLP representation of CLAP state: proprietary, "
                "undocumented -- cannot claim Base64-in-FLP.",
                "Changelog shows active CLAP wrapper/state integration "
                "work (26.1.6: wrapper settings not saved bug).",
            ]),
            ("LMMS", [
                "Current: XML .mmp / compressed .mmpz; has base64.h; VST "
                "issue #1049 shows opaque Base64 state-chunk precedent.",
                "No finished native CLAP host yet; Feb 2026 New Plugin "
                "API #8275 proposes CLAP support with CLAP-inspired "
                "state/lifecycle/threading rules.",
                "JSON project format was proposed, not adopted; do not "
                "claim 'LMMS stores CLAP state as Base64 in JSON'.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement CLAP state extension "
            "save/load; store the opaque blob Base64-encoded in the "
            "JSON project (host serialization decision, documented).",
            "Pulsegrid takeaway: keep params AND state blob (blob is "
            "authoritative for non-parameter state); save on main "
            "thread only, never audio thread.",
            "Pulsegrid takeaway: plugin owns blob versioning; host "
            "treats it as opaque bytes (save -> base64 -> JSON -> "
            "base64 -> load).",
        ],
    ),
    (
        "FL Studio vs LMMS: CLAP-Only Plugin Hosting",
        "Whether a host should make CLAP its sole third-party plugin ABI. "
        "Four meanings of 'CLAP-only': (A) CLAP-supported (FL today: "
        "VST1/2 + VST3 + CLAP + native, AU on macOS); (B) CLAP-preferred; "
        "(C) CLAP-only third-party API (native + CLAP, no VST); "
        "(D) CLAP-inspired internal plugin API. Pulsegrid is already (C) "
        "by construction: native built-ins + CLAP, no VST hosting. The "
        "interesting direction is (D): making the internal plugin "
        "abstraction mirror CLAP's contracts. Philip attached this (PPTX, "
        "10 slides) to guide Pulsegrid's plugin architecture.",
        [
            ("FL Studio: compatibility-first, NOT CLAP-only", [
                "FL is explicitly multi-format: VST1/2, VST3, CLAP, FL "
                "Native (AU additionally on macOS); CLAP since 2024.1.",
                "The Fruity Wrapper is a format-agnostic compatibility "
                "layer (VST/VST3/CLAP), historically VST-centric in its "
                "options (bridging, programs/banks, quirks).",
                "CLAP in FL is an adapter boundary: genuine maintained "
                "integration (2026 release notes still carry CLAP "
                "wrapper fixes), but proprietary internals.",
                "A CLAP-only FL would shed VST2/VST3/AU adapters at the "
                "cost of massive legacy compatibility loss -- little "
                "commercial incentive to do so.",
            ]),
            ("LMMS: VST-centric today, CLAP-inspired proposal", [
                "Current LMMS has substantial VST hosting (VstBase/"
                "VstPlugin.cpp, VeSTige) plus LV2/native paths.",
                "Issue #8275 (New Plugin API, Feb 2026) proposes a "
                "rewrite explicitly to implement CLAP without breaking "
                "CLAP threading rules and CLAP/VST3 without compromises "
                "or hacks; wants explicit plugin states, thread rules, "
                "RT-safe events, reduced Qt dependence, future remote "
                "plugins.",
                "Proposal says the new API should 'take heavy inspiration "
                "from CLAP' -- a C++ version of parts of CLAP adapted "
                "for LMMS (PluginDescriptor, Host/Plugin API split, "
                "event queues).",
                "Jan 2025 progress report: CLAP work to resume after "
                "multi-channel plugin support; intended to meet/surpass "
                "VST support eventually.",
            ]),
            ("Why CLAP makes a strong canonical contract", [
                "Extension model: core ABI + extensions (params, state, "
                "audio/note ports, latency, GUI, note expressions); both "
                "sides query each other's extensions.",
                "Explicit lifecycle: create/init/activate/"
                "start_processing/process/stop_processing/deactivate/"
                "destroy -- a real state machine, not inferred behavior.",
                "One event model (sample-offset events incl. note "
                "expression, beyond MIDI/MPE) instead of per-format "
                "translation paths.",
                "Standardized factory discovery (entry/factory/"
                "descriptors) and feature negotiation.",
                "Explicit threading contracts -- the host always knows "
                "what may be called where.",
            ]),
            ("What NOT to claim", [
                "Not 'FL uses CLAP internally' / 'FL replaced VST with "
                "CLAP' / 'LMMS already uses CLAP' / 'LMMS new API is "
                "CLAP' (it is CLAP-inspired) / 'CLAP automatically "
                "replaces VST' / 'VST plugins load through CLAP' "
                "(needs a wrapper; clap-wrapper goes CLAP->VST3/AU, "
                "not the reverse as a standard).",
            ]),
        ],
        [
            "Pulsegrid takeaway: document the CLAP-only third-party ABI "
            "as a deliberate architectural decision (native built-ins + "
            "CLAP; no VST hosting planned).",
            "Pulsegrid takeaway: make the hosted-plugin lifecycle "
            "explicit (Created/Active/Processing/Stopped) mirroring "
            "CLAP's state machine, with thread-contract documentation.",
            "Pulsegrid takeaway: keep one event path (CLAP events) and "
            "one state path (state extension) at the plugin boundary; "
            "do not accumulate per-format adapters.",
        ],
    ),
    (
        "FL Studio vs LMMS: Signal Routing",
        "Sends, sidechains, tap points, automation, and delay compensation "
        "as a signal-routing / audio-engine problem. Central thesis: FL "
        "treats sidechain as a special use of routed audio (SEND = routed "
        "audio edge; SIDECHAIN = special use of that edge; FRUITY SEND = "
        "in-chain tap into the routing system), while LMMS's traditional "
        "sidechain workflow is primarily audio -> control-value modulation "
        "(SEND = FxRoute between FX channels; SIDECHAIN = controller / "
        "plugin-specific mechanisms; PEAK CONTROLLER = audio -> "
        "control-value bridge). Philip attached this (PPTX, 21 slides) "
        "2026-10-05; independent verification of the key claims was run "
        "in parallel (see verification notes).",
        [
            ("Executive comparison (infographic table)", [
                "Normal mixer send: FL post-fader; LMMS FX-channel send "
                "(source FX channel routes to another FX channel).",
                "True pre-fader send: FL yes (Fruity Send); LMMS has no "
                "documented native pre-fader send equivalent.",
                "Pre-fader tap location: FL any point in the source FX "
                "stack where Fruity Send is inserted; LMMS does not expose "
                "a per-route tap point.",
                "Send amount: FL dedicated per-route level; LMMS FxRoute "
                "has an amount model.",
                "Send pan: FL normal mixer routing has no independent "
                "per-send pan, but Fruity Send does; LMMS has no independent "
                "per-send pan documented.",
                "Sidechain routing: FL first-class Mixer routing concept; "
                "LMMS has no equivalent general-purpose Mixer "
                "sidechain-routing graph.",
                "Sidechain-only send: FL send exists but level = 0 and "
                "destination effects can receive it; LMMS usually via Peak "
                "Controller/controller architecture or plugin-specific "
                "mechanisms.",
                "Sidechain + audible send simultaneously: FL yes; LMMS "
                "ordinary FX routing can be audible but is not equivalent "
                "to FL's explicit sidechain path.",
                "Send automation: FL yes; LMMS send amount is an "
                "automatable model in the underlying architecture.",
                "Routing itself automated: generally no in either; the "
                "amount is the parameter, topology stays static.",
                "Audio-triggered modulation: FL native sidechain inputs + "
                "many plugins; LMMS Peak Controller is the key built-in.",
                "PDC awareness: FL explicitly applies to inter-track "
                "routing and sidechains; LMMS has no equivalent FL-style "
                "documented Mixer PDC routing system.",
            ]),
            ("FL Studio: normal (post-fader) sends", [
                "Mixer routing is graph-based: a source Mixer Track can "
                "route to multiple destinations.",
                "The ordinary send is POST-FADER: signal taken after the "
                "source track's effects AND fader; destination gets a "
                "controllable send level.",
                "Pulling the source fader down pulls the ordinary send "
                "with it (it is not a pre-fader copy).",
            ]),
            ("FL Studio: Fruity Send is a different mechanism", [
                "Fruity Send is explicitly documented as a PRE-FADER send: "
                "an effect inserted into the source track's FX stack that "
                "extracts audio at its insertion point and sends it to a "
                "linked Mixer Track.",
                "It is an in-chain signal tap + independent send gain, "
                "not 'a pre-fader bus'. Move it in the chain and the tap "
                "point changes (e.g. after EQ but before compressor "
                "sends EQ-only signal).",
                "Multiple Fruity Send instances are required to send to "
                "multiple Mixer Tracks (one destination per instance).",
            ]),
            ("Fruity Send controls", [
                "Dry: pass-through to the next FX slot (default 0%, so "
                "the source chain is not doubled unless raised).",
                "Send to: destination selection.",
                "Pan: stereo position of the sent signal (independent "
                "per-send pan, which normal mixer routing lacks).",
                "Volume: level sent to the destination.",
                "Because it is a plugin in the FX chain, its controls "
                "participate in FL's general plugin parameter/automation "
                "system (Volume(t), Pan(t), Dry(t) all automatable).",
            ]),
            ("Fruity Send destination restriction", [
                "Fruity Send does not create the graph connection by "
                "itself: the destination Mixer Track must be linked to "
                "the Fruity Send's host track (documented sidechain-link "
                "workflow) so the source's normal post-fader path is not "
                "also duplicated at the destination.",
                "Architecture: Mixer routing establishes the legal graph "
                "connection; Fruity Send chooses what signal enters it.",
            ]),
            ("FL sidechain is routed audio, not a separate bus", [
                "Sidechaining = sending audio from one Mixer Track to "
                "another WITHOUT making it audible through the "
                "destination's normal path; an effect capable of receiving "
                "a sidechain input consumes that signal (e.g. Fruity "
                "Compressor uses it instead of its own input for the "
                "compression envelope).",
                "Model it as ROUTING GRAPH -> AUXILIARY AUDIO INPUT -> "
                "PLUGIN-SPECIFIC PROCESSING, not as a compressor feature.",
            ]),
            ("Audio send + sidechain send coexist on one route", [
                "When one Mixer Track routes to another there are two "
                "independent paths: the audible send (controlled by send "
                "level) and the sidechain path (always available to "
                "SC-capable plugins).",
                "Send level > 0: audible send + sidechain. Send level = 0: "
                "sidechain-only (no audible send). 'Sidechain to this "
                "track' is documented as a macro creating the send link "
                "with send volume 0.",
                "Automating that send level therefore morphs the edge "
                "between CONTROL ONLY and AUDIO + CONTROL.",
            ]),
            ("'Only' routing variants change Master routing", [
                "FL documents four macros: Route selected to this track / "
                "... to this track only / Sidechain selected to this "
                "track / Sidechain selected to this track only.",
                "The 'only' versions additionally deselect the source's "
                "Master send. So 'sidechain only' is a graph "
                "transformation (source no longer reaches Master), not "
                "just 'send kick to bass compressor'.",
            ]),
            ("Sidechain destination is plugin-dependent", [
                "The Mixer provides the signal; the plugin decides its "
                "role. Fruity Limiter receives a sidechain; Stereo Shaper "
                "can SEND a sidechain - direction depends on the plugin.",
                "For Fruity Compressor the plugin exposes a sidechain "
                "selector for which Mixer Track drives it: two pieces of "
                "state exist (Mixer routing SOURCE -> DESTINATION, and "
                "plugin input selection DESTINATION PLUGIN <- SOURCE).",
            ]),
            ("Send automation: edge gain, not topology", [
                "FL's Send Level is automatable (automation clip / link "
                "to controller / edit events).",
                "The invariant: A --[SEND GAIN(t)]--> B. The connection "
                "persists; its coefficient changes. Automation never "
                "creates/deletes the route per value change.",
            ]),
            ("FL PDC covers inter-track routing and sidechains", [
                "Automatic PDC is documented to apply to inter-track "
                "routing, multi-in/out plugins, and sidechains, bringing "
                "delayed audio streams back into alignment.",
                "A sidechain is therefore a real routed audio stream "
                "participating in timing management, not merely "
                "metadata/control data.",
            ]),
            ("LMMS: normal FX routing", [
                "Developer docs: AudioPort -> Mixer -> MixerChannel; "
                "AudioPorts feed assigned channels via Mixer::mixToChannel, "
                "channel-to-channel routing via MixerChannel::doProcessing.",
                "User-facing: an FX channel sends to another FX channel "
                "with a knob for the amount. Multiple instruments can "
                "feed one FX channel; one FX channel can route to many.",
            ]),
            ("LMMS: FxRoute is an explicit route object", [
                "LMMS PR #902 ('FxMixer: rewrite mixer routing', by the "
                "rewrite's author) confirms: 'The FxRoute object holds "
                "direct pointers to both sender and receiver channels, "
                "and a pointer to the send amount model... each fxchannel "
                "still has 'sends' and 'receives', but the sends and "
                "receives are pointers to the FxRoute objects.' User docs "
                "confirm the per-channel send knob controls the routed "
                "level.",
                "Amount is architecturally an automatable parameter "
                "(FloatModel = AutomatableModel - controllable by "
                "Controllers and AutomationPatterns), so automation "
                "modulates the edge, not the topology (same invariant "
                "as FL).",
                "NOT fully verified at source level: the specific "
                "identifiers isInfiniteLoop / checkInfiniteLoop and the "
                "function names createChannelSend / createRoute / "
                "deleteChannelSend did not surface in accessible sources. "
                "Treat those names as leads, not confirmed facts.",
            ]),
            ("LMMS: loop prevention is a topology constraint", [
                "The FX Mixer rejects routes that would create an infinite "
                "mixer loop (FX1 -> FX2 -> FX3 -> FX1 rejected). "
                "Graph-topology constraint, not a controller feature. "
                "(Exact guard-function names unverified at source level.)",
            ]),
            ("LMMS: no Fruity Send equivalent; sends not pre-fader", [
                "No documented native facility takes audio from an "
                "arbitrary point inside an FX chain and sends it "
                "elsewhere; documented routing is channel-to-channel. "
                "FxChannel = { EffectChain, volume, sends, receives } - "
                "no per-route PRE/POST selector or tap-position field.",
                "Do NOT label LMMS sends 'pre-fader': the docs describe "
                "only an amount knob. Defensible wording: 'FX-channel "
                "send; no documented native pre-fader tap mode equivalent "
                "to Fruity Send'.",
            ]),
            ("LMMS: Peak Controller is audio -> control value", [
                "Built-in Peak Controller analyzes a signal's waveform "
                "and exposes the result as a controller in the Controller "
                "Rack (controls: Base, Amount, Multiplier, Attack, Decay, "
                "Threshold, Absolute Value, Mute).",
                "Architecture: AUDIO SOURCE -> Peak Controller -> "
                "controller value -> AutomatableModel -> target parameter "
                "(e.g. kick -> Peak Controller -> bass volume = pumping).",
                "This is control modulation, NOT the same mechanism as "
                "FL's Mixer sidechain audio-input graph. Similar musical "
                "results, different engine models.",
            ]),
            ("LMMS: other sidechain mechanisms (keep them distinct)", [
                "Plugin-specific sidechain functionality exists (docs "
                "cover Peak Controller sidechaining, Calf sidechain "
                "compression, advanced sidechain compression).",
                "Newer LMMS compressor has a FEEDBACK option (uses its "
                "own output as detector input) - internal feedback "
                "detection, not external Mixer sidechain routing.",
                "CORRECTION (independent verification 2026-10-05): the "
                "infographic's description of LMMS issue #7383 was wrong. "
                "#7383 is titled 'Peak Controller Attack/Decay isn't a "
                "smooth curve' - a bug report that the Attack/Decay knobs "
                "'seem to act more like buttons' (turning them to 100% "
                "removes clicking at the cost of damping). It does NOT "
                "discuss flexible routing/sidechaining as an architectural "
                "limitation. Do not cite #7383 for that claim.",
            ]),
            ("FL has TWO different 'sends' - do not collapse", [
                "A. Mixer send: FX STACK -> FADER -> SEND (post-fader "
                "routing edge).",
                "B. Fruity Send: FX SLOT N -> FRUITY SEND -> DESTINATION, "
                "continuing to FX SLOT N+1 (in-chain tap).",
                "Frequency Splitter confirms the pre-fader concept is "
                "broader than Fruity Send: its band sends are pre-fader "
                "and stay audible at the destination even if the host "
                "track's send is sidechain-only or its fader is zero - "
                "plugin-generated routed outputs vs. ordinary mixer edges.",
            ]),
            ("Precision language: what NOT to claim", [
                "'LMMS has no sends' (false - it does).",
                "'LMMS sends are pre-fader' (no evidence).",
                "'LMMS has no sidechaining' (false - Peak Controller "
                "workflows + plugin-specific mechanisms exist).",
                "'LMMS has FL-style Mixer sidechain routing' (evidence "
                "points the other way).",
                "'LMMS send amount cannot be automated' (FxRoute::amount "
                "is a FloatModel = AutomatableModel).",
                "'Peak Controller is literally a sidechain bus' (it is "
                "an audio-driven controller/modulation mechanism).",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "CONFIRMED - Fruity Send is a documented pre-fader "
                "in-chain tap. Image-Line manual: 'The Fruity Send plugin "
                "is a pre-fader send that allows you to extract audio from "
                "any point in a Mixer channel's effects stack and send it "
                "to any linked Mixer track. This differs from the "
                "post-fader audio send switches/knobs built into the Mixer "
                "that take audio from the point after the effects stack "
                "and Mixer track fader.' Documents all four controls (Dry, "
                "Send To, Pan, Volume); multiple destinations = multiple "
                "Fruity Send instances (Dry at 100% pass-through). "
                "(image-line.com/.../plugins/Fruity Send.htm)",
                "CONFIRMED - 'Sidechain to this track' is a macro for a "
                "send with volume 0. Image-Line Mixer manual (Routing "
                "NOTES): 'When one Track is routed to another there are "
                "two independent audio paths. A regular audible send path "
                "that responds to the send volume level. A sidechain path "
                "that is NOT audible on the destination Mixer Track UNLESS "
                "it is picked up by a plugin with a sidechain audio Input "
                "capability...' 'The 'Sidechain to this track' option ... "
                "is just a macro to creates a send link with the send "
                "volume set to 0. So, you can change any 'send' into a "
                "'sidechain' (only) link by manually setting the volume "
                "to 0.' Also: the sidechain SOURCE stays routed to Master "
                "unless its send-to-master switch is deselected - the "
                "'only' variants change Master routing. "
                "(image-line.com/.../mixer.htm)",
                "CONFIRMED - FL's APDC explicitly covers inter-track "
                "routing AND sidechains. Image-Line manual "
                "(mixer_trackprops.htm#Mixer_PDC): 'Routing - APDC also "
                "applies to inter-track routing, including multi "
                "input/output plugins, and sidechains.'",
                "CONFIRMED - Peak Controller is LMMS's audio->controller "
                "mechanism (docs list BASE, AMNT, MULT, ATCK, DCAY, TRSH "
                "knobs, Absolute Value and Mute buttons; standard "
                "sidechain use = percussion driving a sustained sound's "
                "volume via the Controller Rack). No LMMS documentation "
                "describes a native arbitrary in-FX-chain tap like Fruity "
                "Send - supports the 'no documented equivalent' framing.",
                "CONFIRMED - FL Studio 2024.1 introduced CLAP support "
                "(Image-Line plugin-installation manual + FL Studio 2024 "
                "announcement).",
                "PARTIALLY CONFIRMED - LMMS FxRoute object "
                "(PR #902 quote above); exact function/loop-guard names "
                "unverified at source level.",
                "CORRECTED - LMMS issue #7383 is a Peak Controller "
                "knob-curve bug, not an architecture discussion. The "
                "infographic's characterization was wrong (see above).",
            ]),
            ("Bottom-line verdict", [
                "FL Studio: routing is a first-class Mixer graph - "
                "post-fader sends, in-chain pre-fader taps, "
                "plugin-generated routed outputs, sidechain-capable "
                "routing, simultaneous audio + sidechain, sidechain-only "
                "routing, automated send levels, plugin-specific sidechain "
                "inputs, PDC across inter-track routing and sidechains.",
                "LMMS: routing is primarily an FX-channel graph - "
                "instrument -> FX channel, FX -> FX, multiple sends, "
                "per-route amount, loop prevention, automatable route "
                "amount, controller-based audio modulation, Peak "
                "Controller sidechaining workflows. No general-purpose "
                "'arbitrary FX-chain tap -> dedicated auxiliary/sidechain "
                "input' Mixer concept. (Do NOT cite issue #7383 for "
                "routing-architecture claims - it is a knob-curve bug.)",
            ]),
        ],
        [
            "Pulsegrid takeaway: fix PDC units (CLAP latency is frames; "
            "the delay line works on interleaved scalars) and extend PDC "
            "to inter-track routing - align each track's inputs to its "
            "slowest sender before mixing, then align track outputs at "
            "the master (FL-style).",
            "Pulsegrid takeaway: give every send an explicit tap point - "
            "pre-fader (voice/generator output before track gain/pan) vs "
            "post-FX (current default). Full in-chain FX-slot taps (true "
            "Fruity Send) are a later step; pre/post is the honest first "
            "version.",
            "Pulsegrid takeaway: per-send pan (Fruity Send has it; normal "
            "mixer sends don't) and automatable send amount "
            "(route exists, amount(t) modulates the edge - the shared "
            "FL/LMMS invariant).",
            "Pulsegrid takeaway: sidechain-marked sends feed the "
            "destination's sidechain bus, not its audible path; a "
            "sidechain-capable native effect (ducker) consumes it. "
            "DESIGN DIVERGENCE (documented, deliberate): FL implements "
            "'sidechain to this track' as a macro for an audible send "
            "with volume 0 (the destination's two audio paths share one "
            "edge); Pulsegrid instead models sidechain as an explicit "
            "per-send flag feeding a separate inaudible SC bus. Same "
            "musical result, simpler engine model. Open item: FL's APDC "
            "covers sidechain paths too - Pulsegrid's SC bus is NOT yet "
            "PDC-aligned to the destination's timeline (fine at zero "
            "plugin latency; revisit for latent sidechain sources).",
            "Pulsegrid takeaway: keep cycle rejection (already have the "
            "LMMS isInfiniteLoop pattern) covering sends + output routes.",
        ],
    ),
    (
        "FL Studio vs LMMS: Velocity to Filter Cutoff",
        "Note-event / modulation-routing comparison: FL Studio exposes note "
        "velocity as a first-class modulation source with explicit tracking "
        "destinations (host-level Velocity Tracker + generator-level paths), "
        "while LMMS exposes velocity as per-note data and leaves "
        "velocity-to-filter interpretation to the instrument/plugin (no "
        "documented generic host-level tracker). Per-note modulation (cutoff "
        "= f(note.velocity)) vs time-domain automation (cutoff = "
        "automation(t)) is the central architectural distinction. Philip "
        "attached this (PPTX, 7 slides) 2026-10-05 with a 40-point analysis; "
        "independent verification of the key claims was run in parallel "
        "(see verification notes).",
        [
            ("What 'velocity to cutoff' means", [
                "Not ordinary automation. Automation: TIME -> curve -> "
                "cutoff (indexed by song time, unaware of individual "
                "notes). Velocity tracking: NOTE ON -> velocity -> "
                "modulation calculation -> cutoff (each note computes its "
                "own cutoff: vel 30 darker, vel 120 very bright).",
                "The crucial property: modulation is per note, not per "
                "time. Four notes need no automation points; the "
                "relationship is generated from note properties.",
            ]),
            ("FL Studio: the Velocity Tracker (Channel Settings)", [
                "Host-level modulation layer with two parallel trackers: "
                "Velocity Tracker (note velocity) and Keyboard Tracker "
                "(note pitch/key number). The velocity tracker modulates "
                "PAN, CUT, Mod X, Mod Y; it stays active even when the UI "
                "shows the other tracker.",
                "Offset system, not 'velocity = cutoff': a MIDDLE value "
                "gives zero offset; velocities above generate positive "
                "offsets, below generate negative offsets. "
                "cutoff_effective = base_cutoff + velocity_offset.",
                "MID matters: moving the middle point repositions the "
                "response curve (e.g. MID 64 vs MID 90 changes which "
                "velocities darken vs brighten).",
                "Bipolar: Mod X/Y tracker knobs allow positive AND "
                "negative tracking, so velocity can raise OR lower cutoff.",
                "Mod X/Y can link to plugin parameters including a "
                "plugin's filter cutoff: velocity becomes a general "
                "modulation source, not hardwired to one filter.",
            ]),
            ("FL Studio: generator-level velocity paths", [
                "3x Osc: Volume Tracking is described as driving Pan, "
                "Cutoff and Resonance with a Middle control - the "
                "cleanest example of the concept IF the current manual "
                "wording holds (primary-source wording UNVERIFIED; "
                "consistent with the confirmed tracker architecture).",
                "Sawer: VEL TRACK links filter cutoff frequency to note "
                "velocity (soft = darker/muted, hard = brighter); amount "
                "controls strength. The 'simple synth' version.",
                "Harmless: two documented methods - velocity controls the "
                "filter envelope amount (note-on velocity sets envelope "
                "height -> cutoff), or velocity acts as an LFO Velocity "
                "modulation source -> cutoff. Shows 'velocity -> cutoff' "
                "is a musical relationship with several implementations, "
                "not one fixed mechanism.",
                "Two concepts not to collapse: (A) channel-level velocity "
                "tracking (note -> tracker -> CUT/PAN/MOD X/Y) and (B) "
                "generator-specific velocity tracking (note -> synth "
                "engine -> filter). Both exist; a note can feed both at "
                "once (tracker cutoff + plugin's own velocity input).",
            ]),
            ("FL Studio: keyboard tracking as the parallel concept", [
                "Keyboard Tracker: note pitch -> cutoff/resonance/pan. "
                "Velocity Tracker: note velocity -> cutoff/resonance/pan/"
                "modulation. A filter can respond to two independent note "
                "dimensions at once (pitch AND velocity both move cutoff).",
                "Together the trackers are almost a small note-expression "
                "modulation matrix: sources (velocity, pitch) -> mapping "
                "(middle, amount, polarity) -> destinations (cutoff, "
                "resonance, pan, Mod X/Y).",
            ]),
            ("LMMS: velocity is note data", [
                "Note stores volume (UI calls it velocity) on a 0-200 "
                "internal range, converted to MIDI 0-127 on export. Piano "
                "Roll exposes Note Velocity and Note Panning; 1.3 "
                "development keeps fixing velocity/panning editing.",
                "Core Note carries pitch, position, length, "
                "volume/velocity, panning, detuning; instruments/plugins "
                "consume those properties. No core VelocityTracker object "
                "with velocity -> cutoff/resonance/pan/mod targets.",
                "An LMMS issue (#4137) asking why instruments don't "
                "universally use velocity timbrally was closed without "
                "creating a universal host-level velocity-to-parameter "
                "system: velocity stays note data.",
            ]),
            ("LMMS: no generic velocity-to-parameter bridge", [
                "LMMS HAS a sophisticated controller/automation "
                "architecture (AutomatableModel; 1.3 sample-exact "
                "controllers across FX mixer, instrument VOL, filters, "
                "LFO/Peak controllers). But note velocity is not "
                "automatically converted into a generic automation "
                "stream: there is no core path note.velocity -> "
                "AutomatableModel -> cutoff equivalent to FL's tracker.",
                "Time domain vs note domain: an automation lane "
                "cutoff(t) doesn't know four notes have four velocities. "
                "A genuine tracker evaluates cutoff = f(note.velocity) "
                "per note. LMMS CAN do velocity -> filter inside a "
                "specific instrument/plugin (its own velocity "
                "sensitivity), MIDI/controller routing, or per-note "
                "properties (1.3: per-note detuning/panning, pitch bend) "
                "- but not via one universal host-level tracker.",
                "Accuracy caution (keep): do NOT claim 'LMMS cannot do "
                "velocity -> cutoff.' Correct: no documented generic "
                "host-level Velocity Tracker; individual "
                "instruments/plugins can implement their own "
                "velocity-to-filter behavior.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "CONFIRMED - FL Channel Settings Velocity Tracker "
                "(Image-Line chansettings_misc.htm, fetched): 'There are "
                "two trackers, one for velocity and one for keyboard key "
                "number... The velocity tracker works in the same way, "
                "using a note's velocity to modulate target controls.' "
                "Targets: 'reset the PAN, CUT, Mod X & Mod Y knobs...'. "
                "Bipolar: 'The knobs are bi-polar so negative and "
                "positive tracking modulation can be generated.' Middle: "
                "'For both trackers there is a middle value where no "
                "offsets are generated.' Both trackers stay active "
                "regardless of which is displayed.",
                "CONFIRMED - Sawer VEL TRACK (Image-Line Sawer_Filter.htm): "
                "'VEL TRACK (Velocity Tracking) - This links the filter "
                "cutoff frequency to note velocity. Soft notes will have "
                "a darker more muted sound while harder struck notes will "
                "have a brighter sound. The amount of velocity tracking "
                "controls the strength of this effect.'",
                "CONFIRMED (substance) - Harmless two methods "
                "(Image-Line Harmless.htm): 'VEL - Links note-on velocity "
                "to filter envelope amount' and LFO special source "
                "'Velocity - Note-on velocity acts as a modulation "
                "variable' with 'Filter cutoff frequency' as an LFO "
                "target. The exact tutorial title 'Velocity to filter "
                "frequency' was not verified.",
                "UNVERIFIED at primary-source level - 3xOsc Volume "
                "Tracking. The 2002-2005 FL Studio Bible documents that "
                "era's 3xOsc with NO Volume Tracking section (the feature "
                "postdates the book); the current 3xOsc manual page did "
                "not surface. Plausible and consistent with the confirmed "
                "tracker architecture, but downgrade from 'Image-Line "
                "manual' to 'consistent with documented architecture, "
                "wording unverified'.",
                "CONFIRMED - LMMS Note 0-200 internal range (LMMS issue "
                "#4761 'Export MIDI - Note velocity'): 'Our note velocity "
                "go from 0 - 200 but midi note velocity is 0 - 127... To "
                "convert we need to multiply our velocity by 127/200 = "
                "0.635.' Corroborated by #2316, #1753, #6045.",
                "PARTIALLY CONFIRMED - LMMS issue #4137 exists with the "
                "described content (asks why Piano Roll 'Velocity control' "
                "doesn't make instruments universally velocity-sensitive; "
                "concludes note-wise volume seems correct). The close "
                "reason/disposition is UNVERIFIED - do not assert how it "
                "was closed, only that no universal velocity system "
                "resulted.",
                "CONFIRMED - LMMS 1.3 has sample-exact controllers (FX "
                "mixer, instrument VOL, filters, LFO/Peak controllers) "
                "and NO generic velocity tracker: 'velocity tracker' has "
                "zero matches in the 1.3.0-alpha.2 changelog; velocity "
                "appears only in per-note editing fixes.",
                "CONFIRMED (none found) - no documented LMMS generic "
                "velocity tracker: docs.lmms.io search for 'velocity "
                "tracker' returns no LMMS-relevant results.",
                "CORRECTION - soften 'Velocity -> resonance: Native'. The "
                "current manual's velocity-tracker knob set is PAN, CUT, "
                "Mod X, Mod Y; resonance is a named target of the "
                "KEYBOARD tracker, and velocity reaches it via Mod X/Y "
                "linking. Achievable natively, but not via a dedicated "
                "velocity-tracker RES knob in the current manual.",
            ]),
            ("Bottom-line verdict", [
                "FL Studio: velocity is both note data AND a modulation "
                "source - documented Velocity Tracker (PAN/CUT/Mod X/Y, "
                "middle, bipolar) plus generator-level paths (3x Osc "
                "Volume Tracking, Sawer VEL TRACK, Harmless envelope/LFO "
                "methods) plus parallel keyboard tracking.",
                "LMMS: velocity is fundamentally a note property that "
                "instruments/plugins may interpret; automation/controllers "
                "are a separate parameter system with no generic "
                "note-velocity bridge. Similar musical results are "
                "reachable per-instrument, not host-wide.",
                "Strongest one-line comparison: FL exposes note velocity "
                "as a first-class modulation source with explicit "
                "tracking destinations; LMMS exposes velocity as per-note "
                "data and leaves velocity-to-filter interpretation "
                "primarily to the instrument/plugin.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement an FL-style velocity tracker "
            "for the built-in voices (the 3xOsc/Sawer layer): per-track "
            "vel_track amount (bipolar) + vel_track_mid (no-offset "
            "velocity), evaluated per note at trigger time as "
            "cutoff = base_cutoff * 2^(amount * (vel - mid) * range). "
            "CLAP instruments already receive note velocity (the "
            "generator-specific layer, like FL) and do their own mapping.",
            "Pulsegrid takeaway: keep the note-domain / time-domain "
            "split honest - velocity tracking modulates the per-voice "
            "filter at note-on (note domain); the Filter insert effect "
            "stays time-domain (automation lanes). Document which is "
            "which; don't conflate them.",
            "Pulsegrid takeaway: hats bypass the voice lowpass, so "
            "velocity tracking skips them (no base cutoff to modulate) - "
            "documented, not a bug. Resonance tracking is out of scope: "
            "the voice filters are one-pole (no resonance control).",
        ],
    ),
    (
        "FL Studio vs LMMS: Time-Stretching Architecture",
        "Sample-playback / audio-processing comparison: FL Studio treats "
        "time stretching as a first-class integrated clip/sampler "
        "transformation system (ZPlane Elastique engine family, realtime "
        "and offline modes, tempo-aware Playlist integration), while "
        "LMMS's core sample playback is resampling-based (pitch changes "
        "playback speed) and true time/pitch transformation comes from "
        "separate specialized tools (Granular Pitch Shifter, SlicerT, "
        "external processors). Philip attached this (PPTX, 14 slides) "
        "2026-10-05 with a 48-point analysis; independent verification of "
        "the key claims was run in parallel (see verification notes).",
        [
            ("Time-stretching is several different things", [
                "The term covers: stretching a Playlist clip, changing a "
                "sample's duration while preserving pitch, changing pitch "
                "without changing duration, tempo-sync, real-time tempo "
                "automation, offline processing, or simply changing "
                "playback speed. The infographic disambiguates these "
                "before comparing.",
                "Resampling (tape-speed model): play faster -> duration "
                "down + pitch up; play slower -> duration up + pitch "
                "down. Time stretching: duration changes while pitch "
                "stays constant, via analysis and resynthesis. FL "
                "supports both; LMMS's traditional AudioFileProcessor "
                "playback is primarily the first model.",
                "Four operations, four outcomes: (1) Resampling changes "
                "playback rate - pitch AND duration change. (2) Pitch "
                "shifting changes pitch only. (3) Time stretching changes "
                "duration only. (4) Time+pitch shifting changes both "
                "independently. FL supports all four through different "
                "tools/modes; LMMS's core AFP primarily provides (1).",
            ]),
            ("FL: pitch and time are independent controls", [
                "FL's Sampler Channel separates PITCH, MUL, TIME and Mode "
                "/ Stretch Method; the documentation states the "
                "time-stretching/pitch-shifting algorithms adjust pitch "
                "and playback speed independently.",
                "Stretch modes: Resample (traditional sampler model, "
                "pitch<->speed linked), Stretch (pitch-preserving "
                "duration change), Stretch Pro (enhanced + formant "
                "control), Elastique (offline high-quality), Special "
                "(percussion/transient-oriented, incl. Slice stretch for "
                "drum loops with slice markers).",
            ]),
            ("FL: ZPlane Elastique engine family", [
                "FL's time-stretching uses ZPlane Elastique Pro v3 (e3 "
                "algorithms); e2 remains for compatibility with older "
                "projects. The same algorithm family serves Sampler "
                "Channels, Audio Clips, and Edison Time Stretch/Pitch "
                "Shift - one integrated engine family, not three "
                "unrelated stretchers.",
                "Realtime vs offline is an engine-level distinction: "
                "realtime is calculated on the fly (for tempo automation "
                "and interactive length changes); offline is calculated "
                "ahead of time for highest quality but cannot follow "
                "tempo changes.",
                "Manual instructs: Mode > Realtime > Stretch for tempo "
                "automation while keeping samples at constant pitch - "
                "audio follows the tempo curve via a time-varying stretch "
                "ratio.",
            ]),
            ("FL: stretch lives in the Playlist", [
                "Dragging an Audio Clip's edge stretches the clip per its "
                "time-stretch settings; pitch holds unless the method is "
                "Resample.",
                "Clip instances carry stretch state: start, duration, "
                "pitch, fine, stretch method, tempo relationship, gain, "
                "pan, reverse - the source file is not rewritten. Since "
                "FL 2025.1, stretching a clip creates an independent "
                "variant by default (option to restore old "
                "stretch-all-instances behavior).",
                "Tempo-sync: Edison sample properties can store the "
                "sample's original BPM; with tempo-sync enabled FL "
                "auto-stretches to project tempo. Sampler Autodetect "
                "detects tempo and stretches accordingly. Time can lock "
                "to beats/bars (tell FL a sample spans 4 bars; it "
                "computes the ratio). Ctrl+Alt+R force-restretches all "
                "Audio Clip channels.",
            ]),
            ("FL: Edison offline tool + formants + transients", [
                "Edison's Time Stretch / Pitch Shift tool independently "
                "manipulates pitch coarse/fine/multiplier, time "
                "multiplier, length, method, and formant preservation - "
                "an offline sample-editing pathway alongside playback "
                "stretching.",
                "Stretch Pro exposes Formant Shift: spectral "
                "resonance-aware processing avoids chipmunk artifacts on "
                "vocals (pitch and formant processed separately, then "
                "resynthesized).",
                "Special modes preserve attack transients; Slice stretch "
                "aligns slice markers to drum hits and adjusts timing per "
                "slice rather than phase-vocoding the whole loop; the "
                "Drum Tool adds transient detection + tail "
                "reconstruction.",
                "Algorithm selection is material-matched: e3 Mono / "
                "Stretch Pro for vocals/solo (formants), e3 Generic for "
                "general audio, Special/Slice for drums.",
            ]),
            ("LMMS: core playback is resampling", [
                "AudioFileProcessor (AFP) loads/plays samples, but "
                "changing pitch changes playback speed (issue #4653 "
                "explicitly asks to separate speed and tone; identified "
                "as part of the broader time-stretch/pitch-shift "
                "problem).",
                "Interpolation choices (None/Linear/Sinc): Sinc is the "
                "highest-quality interpolation at higher CPU cost, but "
                "interpolation quality is NOT time stretching - it "
                "improves resampled playback without decoupling pitch "
                "and duration.",
                "Sample Track arranges sample clips (sampleclip / "
                "audiofileprocessor project resources), but there is no "
                "evidence of an FL-style per-clip stretch engine with "
                "pitch/mode/tempo state. Issue #5922 (more Sample Track "
                "manipulation) supports the 'arrangement, not stretch "
                "engine' reading.",
                "LMMS issue #495 (2014) lists 'time stretching / pitch "
                "lock' as a desired feature. The issue body suggests "
                "Mixxx's open-source code; 'Librubberband' was proposed "
                "in a contributor comment (pgiblock, 2014). Accurate "
                "framing: both were PROPOSED, neither confirmed in the "
                "core.",
            ]),
            ("LMMS 1.3: separate specialized tools", [
                "1.3.0-alpha.2 (Sept 2026) added a native Granular Pitch "
                "Shifter effect: genuine granular engine (grain position "
                "(Spray) / per-grain pitch (Jitter) / grain timing "
                "(Twitch) / density, adjustable latency, prefilter "
                "lowpass). It sits in the FX layer, not the "
                "clip engine.",
                "SlicerT (1.3 alpha): native slicer for chopping loops "
                "and rearranging - useful for tempo workflows (set source "
                "BPM, chop drum material) but slicing is not continuous "
                "time stretching.",
                "1.3 adds clip split/resize for all clip types: "
                "arrangement editing, not evidence of pitch-preserving "
                "stretch. 1.3 tempo-sync knob improvements control "
                "PARAMETERS (e.g. LFO rate), not audio duration.",
                "No evidence LMMS core integrates ZPlane Elastique or "
                "Rubber Band natively.",
            ]),
            ("Why stretching costs more than resampling", [
                "A resampler computes output[n] = input[position] with "
                "interpolation. A high-quality stretcher must handle "
                "overlapping frames/grains, phase relationships, "
                "transients, spectral content, time-varying ratios, "
                "buffering, latency, artifact suppression, sometimes "
                "formant preservation - fundamentally more compute. This "
                "is why FL's realtime/offline split exists.",
                "Time-stretching LIVE input in an insert effect is "
                "ill-defined: stretch > 1x (slower) accumulates unbounded "
                "latency; stretch < 1x (faster) must skip input. An "
                "insert effect can honestly do pitch-shifting "
                "(duration-preserving) in realtime; true time-stretch "
                "belongs at the clip/sample level with lookahead, or "
                "offline.",
            ]),
            ("Claims to avoid (infographic's own rules)", [
                "Do NOT say 'LMMS has no time stretching whatsoever' - "
                "it has Granular Pitch Shifter, SlicerT, third-party "
                "options. Say: core playback is resampling; time/pitch "
                "tools are separate.",
                "Do NOT say 'LMMS uses Rubber Band' - proposed, not "
                "confirmed in core.",
                "Do NOT say 'Every FL stretch is Elastique' - Resample "
                "and Special modes exist.",
                "Do NOT say 'FL's engine is proprietary' - the Elastique "
                "algorithms are third-party ZPlane; FL's "
                "integration/state/engine architecture is proprietary.",
                "Do NOT treat Sinc interpolation, tempo-sync knobs, or "
                "clip resizing as evidence of pitch-preserving stretch.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "CONFIRMED - FL Sampler Channel separates PITCH ('Change "
                "sample pitch while preserving its length'), MUL ('modify "
                "the sample length'), TIME, and Mode/Stretch Method. "
                "Modes: Realtime, Resample, Stretch, Stretch Pro "
                "('Enhanced version of Stretch with Formant control'), "
                "Elastique (e3 Generic, e3 Mono), Special (Slice stretch, "
                "Slice map - 'designed to work with percussion, "
                "specifically to preserve attack transients'). "
                "(image-line.com/.../chansettings_sampler.htm)",
                "CONFIRMED - ZPlane Elastique Pro v3: 'Time-stretching "
                "uses ZPlane Elastique Pro version 3 (e3 algorithms, e2 "
                "is included for compatibility with pre FL Studio 12 "
                "projects). The same algorithms are used in Edison Time "
                "Stretch/Pitch Shift Tool.' 'Stretch works for both "
                "Audio Clips and Sampler Channels.' NOTE: manual says "
                "pre FL Studio 12 specifically, not just 'older "
                "projects'.",
                "CONFIRMED - Realtime vs offline: 'There are off-line "
                "(computed then applied) and realtime (computed "
                "on-the-fly) stretching options. Off-line provides the "
                "highest quality but can't be used with Tempo changes.' "
                "'To maintain a constant pitch as Project Tempo is "
                "automated, use Mode (menu) > Realtime > Stretch.'",
                "CONFIRMED - Playlist clip stretch + 2025.1 variants: "
                "'When the left or right edge of the clip is selected "
                "and dragged the clip will be stretched according to "
                "the Audio Clip Time stretching settings.' 'By default "
                "Stretched Audio Clips will create a variant (unique "
                "instance) of the Clip... This was the original "
                "behavior prior to FL Studio 2025.1.' Clip "
                "start/duration reflect the stretch, unique per "
                "instance.",
                "CONFIRMED - Edison tool (editortool_stretch.htm): 'The "
                "Time Stretch / Pitch Shift tool allows you to alter "
                "the duration, pitch and formant of a sample "
                "independently.' Pitch coarse/fine/Mul, Time mul, "
                "Length (ms), Method, plus formant Factor "
                "coarse/Fine/Mul/Order/'Copy from pitch'.",
                "CONFIRMED - Autodetect + beat/bar lock: 'The sample's "
                "tempo (BPM) is autodetected and the Channel stretched "
                "accordingly.' 'Beat/Bar # - Use these options when you "
                "know the exact number of Beats/Bars the sample is "
                "stretched to.' Plus 'Read sample tempo information' "
                "global option for WAV tempo metadata.",
                "CONFIRMED - LMMS 1.3.0-alpha.2 (published 2026-09-06 "
                "per GitHub API) added 'Granular Pitch Shifter effect "
                "(#7328)'. PR by LostRobotMusic: granular engine with "
                "equal-power fades, Spray (grain position), Jitter "
                "(per-grain pitch), Twitch (grain timing), Density "
                "knob, dynamically-minimal latency with manual "
                "override, prefilter lowpass. It is an Effect plugin "
                "(plugins/GranularPitchShifter/) - FX layer, not a "
                "clip engine. NUANCE: 'stereo detune' was NOT named in "
                "the PR body; do not cite it.",
                "CONFIRMED - SlicerT native slicer in 1.3 ('SlicerT "
                "(slicer plugin) (#6857)'), in-tree at plugins/SlicerT/.",
                "CONFIRMED with correction - LMMS #495 ('Ideas for "
                "better audio sample manipulation anyone?'): the issue "
                "BODY suggests Mixxx's open-source code ('i know there "
                "is some open source code for this in mixxx'), while "
                "'Librubberband anyone?' appears in a contributor "
                "comment (pgiblock, 2014). Cite both precisely.",
                "CONFIRMED - LMMS #4653 ('The sample changes tone, but "
                "it is accelerated in audiofile processor'): asks to "
                "separate tone and speed controls. Contributors frame "
                "it as the broader time-stretch/pitch-shift issue; it "
                "was CLOSED as a duplicate of #1734, not implemented.",
                "CONFIRMED - LMMS core is resampling-based: AFP "
                "playNote() uses 'DefaultBaseFreq / _n->frequency()' "
                "(tape-speed model). Interpolation None/Linear/Sinc "
                "maps to libsamplerate modes (ZOH/Linear/Sinc) via "
                "AudioResampler - quality-only, cannot decouple pitch "
                "from speed. CORRECTION: the infographic's "
                "'src_set_ratio fix' detail is UNVERIFIED - current "
                "master uses src_process; do not cite src_set_ratio.",
                "CONFIRMED - LMMS 1.3 clip split/resize (#7477) is "
                "arrangement editing, NOT pitch-preserving stretch: "
                "SampleClip exposes setSampleStartFrame / "
                "setSamplePlayLength / m_startFrameOffset - resizing "
                "adjusts which portion plays; no stretch-ratio "
                "property.",
                "CONFIRMED ABSENT - no Elastique/Rubber Band in LMMS "
                "core: grep over src/, plugins/, include/, CMake finds "
                "only src/gui/editors/Rubberband.cpp, a QRubberBand "
                "selection-marquee UI class unrelated to the audio "
                "library. (Grep trap worth documenting.)",
            ]),
        ],
        [
            "Pulsegrid takeaway: Pulsegrid has no sample playback at all "
            "(engine WAV support is write-only; no audio clips, no "
            "sampler voices), so an FL-style clip stretch engine has "
            "nothing to operate on - that is a multi-session project, "
            "not this increment. The honest shippable step is the "
            "'pitch shifting' row of the four-operation table: a native "
            "duration-preserving pitch-shift insert effect, mirroring "
            "LMMS 1.3's own architectural choice (Granular Pitch Shifter "
            "as an effect, not a clip engine).",
            "Pulsegrid takeaway: the final implementation is a dual-head "
            "modulated delay-line harmonizer (NOT granular OLA - the "
            "first OLA prototype failed measurement: -12 st of a 440 Hz "
            "sine read 186.3 Hz due to grain phase cancellation). Two "
            "read heads stay half a modulation cycle apart; delay drifts "
            "at (1 - ratio) with ratio = 2^(st/12); heads wrap through a "
            "4096-frame range with sin^2 crossfades concealing wrap "
            "discontinuities. D_MIN = 64, circular capacity 8192, "
            "nominal/reportable latency 2112 frames (~47.9 ms at 44.1 "
            "kHz) reported through the existing PDC path. Unity pitch "
            "bypasses the heads (avoids comb filtering). +/-12 "
            "semitones, wet/dry mix aligned via fixed dry delay. "
            "Documented honestly as creative-grade, not "
            "Elastique-grade: no formant preservation, no transient "
            "detection, possible delay-modulation/warble artifacts on "
            "complex material.",
            "Pulsegrid takeaway: do NOT implement time-stretch ratio on "
            "the live insert path - stretching live input slower than "
            "realtime accumulates unbounded latency (the infographic's "
            "own realtime/offline analysis). True time-stretch belongs "
            "with a future sample-clip layer; note this explicitly in "
            "docs so a later agent doesn't 'add a time knob' naively.",
        ],
    ),
    (
        "FL Studio vs LMMS: Sub-Step Timing",
        "Event-positioning / timing-resolution comparison: FL Studio uses "
        "a configurable PPQ (pulses-per-quarter-note) event timeline with "
        "snap grids layered over fine underlying resolution, while LMMS "
        "uses a fixed 192-ticks-per-whole-note grid (~48 PPQN in 4/4) with "
        "an increasingly capable Piano Roll editor on top. Philip attached "
        "this (PPTX, 12 slides) 2026-10-05 with a 34-point analysis; "
        "independent primary-source verification was run in parallel (see "
        "verification notes).",
        [
            ("'Sub-step timing' is six different concepts", [
                "1. Musical grid resolution (1/16, 1/32, 1/64). 2. Snap "
                "resolution (where a dragged note may land). 3. Underlying "
                "event resolution (smallest representable position). "
                "4. Quantization resolution (how recorded notes are "
                "rounded). 5. Free/micro-timing (deliberate off-grid "
                "offsets). 6. Nudge resolution (shifting an event without "
                "changing its musical length). FL and LMMS both cover "
                "these, at very different underlying resolutions.",
            ]),
            ("FL Studio: PPQ event timeline (96 default, increasable)", [
                "FL represents project timing in PPQ. Manual: 'The default "
                "Timebase is 96 PPQ. PPQ determines how finely the "
                "project's timeline represents notes, clips and events. "
                "Increasing PPQ gives finer positioning but also "
                "increases CPU usage.' At 96 PPQ: quarter=96 ticks, "
                "bar=384 ticks (4/4); 1/16=24, 1/32=12, 1/64=6, 1/128=3 "
                "ticks. Project settings allow raising PPQ (e.g. "
                "192/384/768); raising it never moves existing events, "
                "lowering it can - proof PPQ is part of the event "
                "coordinate system, not a zoom setting. Applies to Piano "
                "Roll, internal controllers, automation nodes, Edit "
                "Events, Playlist/clip positioning.",
            ]),
            ("FL: Step is not a tick; snap is layered over resolution", [
                "In 4/4 one FL Step = 1/16 note = 24 ticks at 96 PPQ. Snap "
                "menu offers fractional Steps (1/2, 1/3, 1/4, 1/5, 1/6) "
                "and Beats (1/4 Beat = 16ths, 1/3 Beat = triplets, 1/2 "
                "Beat = 8ths), Bar, Line, Cell, Events, Markers - and "
                "'None', which moves at the raw PPQ tick. Snap and event "
                "resolution are separate: snap=1/16 with PPQ=96 still "
                "represents between-grid positions. Alt-drag temporarily "
                "disables snap (documented). Transport can display "
                "Bar:Beat/Step:Tick.",
            ]),
            ("FL: nudge + recording quantization", [
                "21.2.99 beta added Shift+mousewheel to nudge clips/notes "
                "in Playlist and Piano Roll - a small controlled offset, "
                "distinct from dragging. Global snap also drives MIDI "
                "input quantization during recording, with separate "
                "choices for note start, note end, leave-duration, and "
                "record-to-Step-Sequencer.",
            ]),
            ("LMMS: fixed 192 ticks per whole note (~48 PPQN)", [
                "LMMS Piano Roll max free-drag/quantize resolution is "
                "documented as 1/192 (of a bar). A timing-architecture "
                "issue states LMMS historically uses 192 ticks per whole "
                "note = 48 PPQN - half FL's default 96 PPQ. Fixed: no "
                "user-facing project PPQ scaling. A 1.3-milestone feature "
                "request proposed 960 PPQN (citing recorded MIDI, BPM "
                "resolution, swing, integer ticks) - recognized as an "
                "architectural limitation, but a PROPOSAL, not adopted; "
                "1.3 development focused on editor/snapping/painting "
                "instead.",
            ]),
            ("LMMS: Alt-drag, auto-quantize, automation share the grid", [
                "Alt+drag bypasses the quantization grid but still snaps "
                "to the 1/192 maximum. Recording with auto-quantize off "
                "captures at maximum resolution. Automation Editor points "
                "follow the same Q/max-1/192 model. Recent releases "
                "improved Piano Roll nudge/snap-while-dragging, "
                "quantization, and note-misalignment fixes - editor "
                "improvements, not a higher underlying PPQN.",
            ]),
            ("Head-to-head numbers (4/4)", [
                "FL default: 384 timing positions/bar; LMMS: 192/bar - "
                "FL has 2x raw resolution, and can raise PPQ further. At "
                "120 BPM (quarter = 500 ms): FL tick ~= 5.21 ms, LMMS "
                "tick ~= 10.42 ms. At 180 BPM: 3.47 ms vs 6.94 ms. Ratio "
                "stays 2:1 at any tempo. Triplets: FL has explicit 1/3 "
                "Beat snap; LMMS supports 1/3-based quantization levels.",
            ]),
            ("Three comparison traps", [
                "1. Units differ: LMMS '1/192' is per BAR; FL snap "
                "fractions are per Step/Beat - never equate them. "
                "2. Event timing is not audio precision: PPQ positions "
                "MIDI/automation/clips; PCM audio still runs at the "
                "sample rate. Don't describe 48 PPQ as 'low audio "
                "precision'. 3. Don't present the 960-PPQN request as "
                "shipped LMMS work.",
            ]),
            ("Practical difference: the slightly-late hi-hat", [
                "FL: coarse snap + Alt-drag fine positioning at PPQ "
                "level + raise project PPQ if needed + Shift+wheel "
                "nudge. LMMS: quantization grid + Alt-drag to the 1/192 "
                "maximum + improved nudge UI - but no PPQN scaling. "
                "FL's model: configurable event resolution with snap "
                "layered on top. LMMS's model: fixed-resolution ticks "
                "with capable editing layered on top.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "ALL 15 CHECKED CLAIMS CONFIRMED against primary "
                "sources (image-line.com FL manual pages fetched "
                "directly; docs.lmms.io; LMMS GitHub issues/PRs "
                "#1530, #4877, #5848, #6714, #8312; 1.3.0-alpha.1/2 "
                "release notes).",
                "FL CONFIRMED: default Timebase 96 PPQ ('higher PPQ "
                "allowing finer control but it also uses more CPU' - "
                "songsettings_settings.htm); PPQ increasable, affects "
                "internal controllers, automation nodes, Edit Events, "
                "Piano Roll and Playlist placement; raising mid-project "
                "is safe, lowering can move events. Snap menu: 'Steps "
                "1/6 to 1 (step)', 'Beats 1/6 to 1 (beat)' with the "
                "Step/Beat->note conversion table (1/4 Step = 64ths, "
                "1/2 Step = 32nds, 1/4 Beat = 16ths, 1/3 Beat = "
                "triplets). Alt: 'Holding the Alt key temporarily sets "
                "snap to none' / 'Snapping can be temporarily disabled "
                "by holding the Alt key when dragging Notes'. Nudge: "
                "'Nudge notes with mouse wheel (Shift+Mouse-Wheel) ... "
                "Movement will be according to the PPQ settings' "
                "(pianoroll_menu.htm). MIDI input quantizing follows "
                "global snap with separate start/end/duration/record-to-"
                "step-sequencer options. Transport: 'Bar : Beat/Step : "
                "Tick'. '(none) - No snapping. Movement is limited only "
                "by the Project Timebase (PPQ) setting'.",
                "LMMS CONFIRMED: Piano Roll max quantization 1/192; "
                "'free-drag and snap to the nearest 1/192 time increment "
                "(max Quantization in LMMS)'; Alt+drag bypasses the grid "
                "but still snaps to 1/192 (docs.lmms.io Piano Roll). "
                "Timing architecture: 'Right now LMMS is running 192 "
                "ticks/whole note = 48 PPQN' (issue #1530 body, LMMS "
                "member softrabbit, 2014). Automation Editor: selection "
                "moves 'without regard for the Q setting ... at the "
                "maximum Q of 1/192'.",
                "960 PPQN = PROPOSAL, NOT SHIPPED: issue #1530 'More "
                "PPQN?' (1.3 milestone) closed 2019-03-14 without "
                "implementation; still listed unchecked in meta-issue "
                "#4877 (2026-08-03); 2023 thread discussed a sample-"
                "offset alternative instead (maintainer DomClark "
                "objected on unit-consistency grounds). Maintainer "
                "diizy (2014): recording bottleneck is audio period "
                "size, not tick count; 'Changing the number of ticks is "
                "not a small endeavour.' LMMS 1.3 timing core "
                "UNCHANGED - improvements are editor-level: PR #5848 "
                "'pianoroll: nudge/snap while dragging notes' (2021), "
                "1.3.0-alpha.1 'Add Quantize button to Piano Roll', "
                "1.3.0-alpha.2 'Don't auto-quantize notes when recording "
                "MIDI input' (#6714, added midi:autoquantize config). "
                "PR #8312 'Pianoroll refactor into Pianorollpainter' "
                "(2026) is OPEN/UNMERGED and rendering-only.",
                "VERIFICATION FLAGS folded in: (1) state plainly that "
                "960 PPQN never shipped; (2) keep the 1/192 unit-mismatch "
                "warning prominent (LMMS per-bar vs FL per-step/beat); "
                "(3) '1/5 Step' snap value UNVERIFIED as an explicit "
                "manual entry (range '1/6 to 1' confirmed, don't cite "
                "1/5); (4) recording-accuracy caveat: diizy's period-"
                "size point means the 5.21/10.42 ms-per-tick ratio is "
                "not a direct recording-accuracy ratio; (5) auto-"
                "quantize-off is now a 1.3 settings checkbox "
                "(midi:autoquantize), not just 'set Q to 1/192'.",
            ]),
        ],
        [
            "Pulsegrid takeaway: the engine ALREADY supports sub-step "
            "timing end to end - Note.start/length are floats, the "
            "bridge passes them through, and Rust rounds fractional "
            "steps to samples (sample-accurate). The gap was purely the "
            "Piano Roll editor, which snapped paint/move/resize to whole "
            "steps. v0.28.0 adds FL-style editing: snap selector "
            "(1/16, 1/32, 1/64, 1/128, Off), Alt-drag during a gesture "
            "temporarily bypasses snap (Alt+press on a note keeps its "
            "existing velocity-gesture meaning), and Shift+mousewheel "
            "nudges the selected note by the snap increment (FL 21.2.99 "
            "parity). No format change (v13): fractional positions were "
            "always legal in the model.",
            "Pulsegrid takeaway: the Piano tab's input quantize 'Off' "
            "already preserves captured sub-step timing (quantize_beat "
            "with grid 0 = identity), so recording was ahead of the "
            "editor - the editor now matches. Document the six-concept "
            "distinction in the user-facing tooltip so Philip (beginner) "
            "learns snap vs underlying resolution, not just which button "
            "to press.",
            "Pulsegrid takeaway: do NOT chase FL's increasable-PPQ "
            "project setting. Pulsegrid's event resolution is the audio "
            "sample itself (fractional steps round to samples at render) "
            "- finer than FL's default 96 PPQ at any sane tempo. The "
            "honest doc line: 'positions are continuous; the grid is "
            "just a snapping aid.'",
        ],
    ),
    (
        "FL Studio vs LMMS: Pan Lane / Note Panning",
        "Per-note stereo positioning comparison: FL Studio stores pan "
        "as a native Piano Roll note property (note.pan 0.0-1.0) inside "
        "a broad scriptable note-expression system, while LMMS has a "
        "dedicated Piano Roll Note Panning mode storing pan on each "
        "Note and carrying it into playback. Philip attached this "
        "(PPTX, 15 slides) 2026-10-05 with a 34-point analysis; "
        "independent primary-source verification was run in parallel "
        "(see verification notes).",
        [
            ("Three kinds of pan", [
                "Channel pan (whole sound source), note pan (each note "
                "individually: 'when this particular note is played, "
                "where should that note be positioned in the stereo "
                "field?'), and automation (a parameter changing over "
                "time). This comparison is about note pan.",
            ]),
            ("FL: pan is a native Piano Roll note property", [
                "FL's Piano Roll lower editor targets Note Pan alongside "
                "Velocity, Release, Pitch, Filter cutoff/resonance and "
                "automation/event data, drawn as vertical 'lollipop' "
                "controls beneath the notes. The scripting API exposes "
                "note.pan with 0.0 = hard left, 0.5 = center, 1.0 = hard "
                "right (default 0.5), alongside number, time, length, "
                "group, velocity, release, color, fcut, fres, pitchofs, "
                "slide. Independent values are retained for notes "
                "sharing the same start position (the editor's 'tail' "
                "visualization shows them).",
            ]),
            ("FL: three ways to edit note pan", [
                "A. Piano Roll lower editor PAN lane (drag values). B. "
                "Note Properties dialog (double-click a note; Levels "
                "section: PAN, VEL, REL, MOD X, MOD Y). C. Mouse-wheel "
                "editing: Alt/Option + wheel changes the selected note "
                "property, Ctrl + Alt/Option + wheel for fine adjustment. "
                "Note properties move with their notes (not anchored to "
                "absolute editor positions).",
            ]),
            ("FL: note pan vs channel/mixer pan", [
                "Channel/Mixer pan affects the channel as a whole; note "
                "pan is associated with individual note events, so a C4 "
                "can sit left while a simultaneous E4 sits center and G4 "
                "sits right without separate automation lanes. FL's "
                "lower editor can show BOTH note properties and genuine "
                "Event Automation - the docs warn interpolation only "
                "works with genuine Event Data, not the lollipop "
                "note-property data.",
            ]),
            ("FL limitation: plugins have to support it", [
                "FL can store note.pan, but third-party instruments may "
                "not audibly respond: Image-Line's MIDI Out docs state "
                "per-note Pan, filter cutoff and resonance are not "
                "supported by the MIDI standard (Note On carries note + "
                "velocity + channel only), and forum discussions cover "
                "VST instruments ignoring Piano Roll note-pan values. "
                "Note pan is host/plugin note-expression data, not "
                "ordinary MIDI CC panning; compatibility depends on the "
                "instrument path.",
            ]),
            ("FL: scriptable + procedural pan", [
                "The scripting API lets scripts read/write note.pan "
                "(e.g. pan = f(pitch): low notes left, high notes "
                "right). The Riff Machine's Levels & Panning section "
                "includes a PAN note-panning multiplier with "
                "randomization (bipolar, reset-before-processing, seed "
                "controls) alongside velocity/release/Mod X/Y/pitch.",
            ]),
            ("LMMS: dedicated Note Panning mode", [
                "LMMS's Piano Roll has a dedicated toggle between Note "
                "Volume and Note Panning; the lower editor then edits "
                "each note's pan, shown as a vertical orange bar beneath "
                "the note (default centered). The manual describes pan "
                "as the ratio of the note's volume sent to the right "
                "vs left channel.",
            ]),
            ("LMMS: pan lives on the Note object and reaches playback", [
                "The LMMS Note class has getPanning()/setPanning() "
                "alongside note volume; source references include Piano "
                "Roll drawing, mouse interaction, note playback, note "
                "stacking, arpeggiation, recording and previewing. "
                "Playback path: Note::panning -> NotePlayHandle -> "
                "InstrumentTrack::processAudioBuffer -> Mixer. A "
                "dedicated panning.h defines the note-panning "
                "types/constants (centered model: PanningLeft <- "
                "DefaultPanning -> PanningRight, not FL's normalized "
                "0.0-1.0).",
            ]),
            ("LMMS: note pan vs track pan", [
                "SampleTrack has its own m_panningModel (serialized as "
                "the track setting 'pan', connected to the track's "
                "AudioBusHandle) - that is track-level panning, "
                "distinct from the Piano Roll Note object's panning. "
                "Same conceptual layering as FL: note pan (individual "
                "note) + track pan (entire track).",
            ]),
            ("LMMS editor is more focused, less general", [
                "LMMS presents Note Volume / Note Panning as a focused "
                "editor switch; FL treats the lower area as a general "
                "note-property/event editor (Velocity, Pan, Release, "
                "Mod X, Mod Y, ...). Enhancement requests in LMMS "
                "history asked for richer note-property access (context "
                "menus for velocity/panning, customizable pan/velocity "
                "templates) - the core feature exists, surrounding "
                "workflows were requested separately.",
            ]),
            ("Core similarity and core difference", [
                "SIMILARITY: both store pan per note (FL Note.pan, LMMS "
                "Note.panning), both keep independent values for "
                "simultaneous notes, both distinguish it from channel/ "
                "track pan and from continuous pan automation (per-note "
                "values are discrete note properties, not a parameter "
                "trajectory). NEITHER has standard-MIDI per-note pan. "
                "DIFFERENCE: FL embeds pan in a broad scriptable "
                "per-note expression/event framework (scripting API, "
                "Riff Machine, note properties dialog, wheel editing); "
                "LMMS exposes it through a dedicated Note Panning Piano "
                "Roll editor with a slimmer surrounding framework. Do "
                "NOT write 'FL has note pan, LMMS doesn't' - factually "
                "wrong.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "13 of 16 CHECKED CLAIMS FULLY CONFIRMED with verbatim "
                "quotes (image-line.com FL manual pages fetched "
                "directly; il-group.github.io FL API stubs; "
                "docs.lmms.io; LMMS GitHub source/issues/PRs). Claims "
                "12-13 confirmed in substance (not at individual method "
                "names); claim 9's specific forum thread not found but "
                "the limitation is documented first-party.",
                "FL CONFIRMED: lower editor targets 'Note Velocity, Pan, "
                "Pitch, Filter cutoff & Automation events'; note "
                "properties 'displayed as vertical lines with a small "
                "circle at the top' ('lollipop' is the manual's own "
                "term). Scripting API: 'pan property writable ... "
                "between 0 and 1. 0.5 is centered' (members: number, "
                "time, length, group, pan, velocity, release, color, "
                "fcut, fres, pitchofs). Note Properties dialog: "
                "'Double-click a note ... Levels ... per note - panning "
                "(PAN), note on velocity (VEL), release velocity (REL), "
                "channel filter cutoff (MODX) and channel filter "
                "resonance (MODY)'. Alt/Opt+wheel changes the selected "
                "note property, Ctrl+Alt/Opt+wheel fine-tunes. "
                "Interpolate 'works only when actual Event Data is "
                "shown ... not with note-property lollipop data'. "
                "'Note properties move with the notes they belong to'; "
                "'The right-facing tail allows you to see the "
                "independent values of notes with the same start time.' "
                "MIDI Out docs: 'Per-note - Pan, filter cutoff & "
                "resonance ... are not supported, the MIDI standard "
                "only supports per-note velocity.' Riff Machine: 'PAN - "
                "Note panning multiplier' with Bipolar/Seed "
                "randomization.",
                "LMMS CONFIRMED: 'Click on the Note Volume/Note Panning "
                "button below the piano keys to toggle between the Note "
                "Volume and Note Panning editor.' 'The pan of each note "
                "is the ratio of the note volume that is transmitted "
                "out the right stereo channel versus the left stereo "
                "channel. Pan is shown as a vertical orange bar below "
                "the note ... By default, the pan is centered.' "
                "SampleTrack.cpp (master, fetched): m_panningModel "
                "(DefaultPanning, PanningLeft, PanningRight) connected "
                "to the AudioBusHandle and serialized separately as "
                "track setting 'pan' - confirming the two-layer model. "
                "Enhancement requests all exist: #5613 (velocity/ "
                "panning/pitch-bend menu), #1569 (note context menu), "
                "#4496 (pan/velocity templates).",
                "LMMS PLAYBACK INTEGRATION CONFIRMED IN SUBSTANCE: "
                "issue #6142 'ZynAddSubFX doesn't allow individual note "
                "panning' filed as a BUG ('Expected behavior: Notes "
                "should pan') - only makes sense if the pipeline exists; "
                "PR #8132 'Lb302: Velocity and note panning' added note "
                "panning to an instrument's audio code (1.3.0-alpha.2); "
                "1.3.0-alpha.2 'SF2Player: Add support for per-note "
                "detuning and panning (#6602)'. KEY PARALLEL WITH FL: "
                "neither DAW's per-note pan is universally honored - "
                "both are host-side note data the instrument path must "
                "explicitly consume (ZynAddSubFX ignores it, still "
                "open). This validates documenting Pulsegrid's CLAP "
                "limitation the same way.",
                "VERIFICATION FLAGS folded in: (1) 'slide' is NOT a "
                "Note object property in FL's scripting API (editor "
                "note-type flag) - don't list it as one; (2) pan "
                "'default 0.5' is implied by '0.5 is centered', not "
                "stated verbatim as the default; (3) LMMS bar color: "
                "current docs say orange, 1.2-era docs said green - "
                "cite current; (4) #5613 maintainer quote worth "
                "keeping: 'supporting per-note pan would be a "
                "significant amount of work ... this isn't just the "
                "case of exposing existing features via a new UI' - "
                "per-note pan is a STATIC per-note value; time-varying "
                "pan within a note is a separate unimplemented feature "
                "(keeps our discrete-vs-automation framing honest); "
                "(5) keep warning against equating FL's 0.0-1.0 with "
                "LMMS's internal constants.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement per-note pan as a true note "
            "property (FL/LMMS model), NOT as automation. v0.29.0: "
            "Note.pan (0.0 left - 0.5 center - 1.0 right, FL "
            "convention, default 0.5), format v14 (additive migration), "
            "engine TrackEvent/NoteData/Voice carry pan with "
            "constant-power stereo voice summing before the track "
            "fader (so note pan composes with track pan, like FL). "
            "Piano Roll gets an LMMS-style Vel/Pan lane-mode toggle; "
            "the painter draws pan bars bidirectionally from a center "
            "line. Honest FL-parity limitation documented: per-note "
            "pan applies to built-in voices; CLAP generator notes "
            "ignore it (mirrors FL's 'plugins have to support it' - "
            "CLAP note events carry no standard per-note pan).",
            "Pulsegrid takeaway: keep the numeric convention explicit "
            "everywhere - FL 0.0-1.0 in the Python model/UI, -1..1 in "
            "the Rust engine (matching existing pan_gains/track pan "
            "convention), converted at the bridge. The infographic's "
            "warning not to equate FL's and LMMS's internal "
            "representations applies to us too: document the mapping.",
        ],
    ),
    (
        "FL Studio vs LMMS: Native Sample Loading + Audio-Clip Architecture",
        "Research topic 26. Researched as an architecture problem - where the clip abstraction "
        "lives - not as a format checklist. FL Studio: Audio Clip Channel + "
        "Playlist clip-instance model (source + independent instances). LMMS: "
        "explicit open-source chain SampleDecoder -> SampleBuffer -> Sample -> "
        "SampleClip/SampleTrack, plus a separate AudioFileProcessor path for "
        "note-triggered sample playback.",
        [
            ("FL: Sampler Channel vs Audio Clip Channel", [
                "FL explicitly distinguishes the two. The manual: 'Audio Clips "
                "are a special version of the Sampler channels. The purpose "
                "of Audio Clips is to hold the samples displayed & triggered "
                "in the Playlist where they can be sliced and arranged as "
                "required.'",
                "Dragging a sample onto the Playlist creates an Audio Clip "
                "Channel; dropping it on the Channel Rack creates a normal "
                "Sampler Channel. Both look similar, but Audio Clips only "
                "expose the SMP and MISC tabs of Channel Settings - their "
                "main purpose is feeding the Playlist clips.",
            ]),
            ("FL: the two-level model (most important FL detail)", [
                "Channel Settings (global) apply to ALL instances of the "
                "Audio Clip: sample, time-stretch settings, precomputed "
                "effects, sampler behavior, channel volume/pan, envelopes.",
                "Clip Properties (local, per-instance) apply only to the "
                "specific Playlist instance: Gain, Pan, Pitch (semitones), "
                "Fine (cents), Mute, Reverse, Start time, Duration. The "
                "manual: 'create variants with different Gain, Pitch and "
                "Reverse settings etc. These controls apply only to the "
                "particular instance of the Audio Clip you are working on.'",
                "An instance means a slice or duplicate in the Playlist. "
                "This is a source + instance model: one shared channel "
                "state, many independent clip objects.",
            ]),
            ("FL: Make Unique vs Make Unique as Sample", [
                "Make Unique: creates a unique Clip/source instance, but the "
                "original audio file is re-used in the new Audio Clip "
                "channel.",
                "Make Unique as Sample: clones the Audio Clip AND clones "
                "the sample file on disk - 'Use this when you want to "
                "physically edit the sample data and change it in some way.'",
                "Deliberate distinction between duplicating the "
                "arrangement/source object and duplicating the underlying "
                "audio asset.",
            ]),
            ("FL: Playlist is a generic clip container", [
                "Playlist tracks are multipurpose Clip Tracks holding "
                "Pattern Clips, Audio Clips, and Automation Clips - and "
                "those clips can overlap on one track.",
                "The Audio Clip is therefore a timeline object, not merely "
                "'a sample loaded into a sampler.'",
            ]),
            ("FL: native sampler, memory strategy, formats", [
                "Audio Clips are built on the same native sample "
                "infrastructure as the Sampler Channel: waveform loading, "
                "start/end, loop points, envelopes, filtering, pitch, "
                "resampling, embedded regions, slice markers, time "
                "stretching.",
                "Keep on disk option: samples normally cached in RAM; "
                "compatible samples can stay on disk to reduce memory "
                "pressure (risk of underruns). Zooming the Playlist out "
                "until clips are visible can force clip data into RAM - "
                "the asset layer and the clip layer are not the same thing.",
                "WAV support incl. 8/16/24/32-bit int, 32-bit float, "
                "mono/stereo, 6-192 kHz; works internally with 32-bit "
                "float WAV data. OGG/FLAC also load, converted internally "
                "as needed.",
            ]),
            ("FL: project model reinforces the architecture", [
                "A normal .flp stores project info and REFERENCES the "
                "samples; on load FL searches for the referenced files. A "
                "ZIP project packages the .flp plus the sample assets.",
                "Classic references-external-media architecture: clip "
                "state + placement + instance properties live in the "
                "project; audio bytes live in external files.",
            ]),
            ("FL: deep timeline integration", [
                "Audio Clips participate in Playlist editing: resize, "
                "slice, chop (BeatSlicer autodetect, region slicing), "
                "zero-crossing-aware edits, fades, stretching, pitch "
                "shifting, reversing, cloning, unique instances, time "
                "warping, stem extraction, consolidation.",
                "Integrated per-instance Volume envelopes: fade in/out "
                "handles with curve shapes, automatic crossfades on "
                "overlap, declicker modes, per-instance clip gain handle "
                "(+36 dB to -inf). Fades replace declick modes when active.",
            ]),
            ("LMMS: two distinct sample workflows", [
                "LMMS has no single universal Audio Clip object. It has "
                "two native sample paths: (A) SampleTrack - timeline-"
                "oriented sample arrangement; (B) AudioFileProcessor - "
                "instrument/plugin-oriented sample playback through the "
                "InstrumentTrack/note system.",
            ]),
            ("LMMS: SampleDecoder -> SampleBuffer (source-verified)", [
                "SampleDecoder::decode(audioFile) returns "
                "optional<Result{ vector<SampleFrame> data; int "
                "sampleRate }>; supportedAudioTypes() exposes formats "
                "dynamically. Decoder paths: libsndfile-supported "
                "formats, OGG/Vorbis, DrumSynth .ds.",
                "SampleBuffer stores exactly: std::vector<SampleFrame> "
                "m_data, int sample rate, QString audio-file path. "
                "Factory methods fromFile() and fromBase64(); toBase64() "
                "for embedding. (include/SampleBuffer.h, master, "
                "2026-10-05.)",
            ]),
            ("LMMS: Sample = shared data + playback state (source-verified)", [
                "Sample holds std::shared_ptr<const SampleBuffer> "
                "m_buffer - multiple Sample objects share one immutable "
                "decoded buffer while each keeps its own playback state.",
                "Playback state (std::atomic): start/end frame, loop "
                "start/end frame, amplification, frequency, reversed. "
                "Loop enum: Off/On/PingPong. play(dst, state, numFrames, "
                "loopMode, ratio) resamples through AudioResampler "
                "(libsamplerate wrapper); ratio combines output rate / "
                "source rate x frequency ratio x playback ratio.",
                "PlaybackState carries the resampler + frame index + "
                "backwards flag. Reverse is a boolean, not a buffer "
                "rewrite (a deliberate outcome of the 2026 refactor).",
            ]),
            ("LMMS: SampleClip is a real model object (source-verified)", [
                "class SampleClip : public Clip, containing Sample "
                "m_sample, BoolModel m_recordModel, bool m_isPlaying, "
                "int m_startFrameOffset.",
                "API: sampleFile(), hasSampleFileLoaded(), sample(), "
                "sampleLength(), setSampleStartFrame(), "
                "setSamplePlayLength(), setStartTimeOffset(), "
                "setSampleBuffer(), clone(), setSampleFile(). "
                "(include/SampleClip.h, master.)",
                "SampleTrack::createClip() literally does 'new "
                "SampleClip(this)'. Track serializes effects, volume, "
                "panning, mixer channel; the clip owns timeline "
                "position/length/sample/playback offsets.",
            ]),
            ("LMMS: SampleTrack::play() clip math (source-verified)", [
                "For each clip under the song position: sampleStart = "
                "framesPerTick * (songPos - clipStart - startTimeOffset); "
                "clipFrameLength = framesPerTick * (clipEnd - clipStart - "
                "startTimeOffset); samplePlayLength = min(clipFrameLength, "
                "sampleBufferLength). Clips smaller than the sample play "
                "only to clip end; longer clips play the sample to its "
                "end and stop.",
                "Playback is handed off as SamplePlayHandle (or "
                "SampleRecordHandle when recording) into "
                "Engine::audioEngine(). Path: SampleTrack -> SampleClip "
                "-> SamplePlayHandle -> AudioEngine -> Mixer/AudioBus.",
            ]),
            ("LMMS: the 2026 refactor (source + release notes verified)", [
                "SampleLoader was removed; SampleBuffer refactored (PR "
                "#6610, in 1.3.0-alpha.2, Sept 2026). SampleBuffer went "
                "from duplicated data + extras to three members: sample "
                "data, optional file path, sample rate.",
                "Refactor outcomes (PR description): resampling now "
                "happens at playback time via Sample instead of "
                "resample-render-resample on export; reverse is a "
                "boolean; anti-alias wavetable moved out to the "
                "instruments that need it; file-size limit removed. "
                "Anything still citing 'SampleLoader' as the central "
                "loader is stale.",
            ]),
            ("LMMS: AudioFileProcessor + serialization", [
                "AFP path: AudioFileProcessor -> Sample -> "
                "Sample::PlaybackState -> Sample::play() -> audio "
                "buffer, driven by NotePlayHandle on an InstrumentTrack. "
                "Own controls: start/end, loop point, reverse, stutter, "
                "interpolation, amplitude.",
                "AFP can embed sample data: saves 'src' path normally, "
                "or Base64 'sampledata' when there is no file path. "
                "DataFile treats sampleclip->src and "
                "audiofileprocessor->src as external resource elements "
                "(relative paths stored).",
            ]),
            ("Head-to-head (condensed)", [
                "Native sample loading: both yes. Native WAV/OGG/FLAC: "
                "both yes.",
                "Decoder layer publicly documented: FL no (proprietary); "
                "LMMS yes (SampleDecoder).",
                "Internal decoded representation visible: FL no; LMMS "
                "yes (SampleBuffer).",
                "Shared sample buffer visible in source: FL not "
                "documented; LMMS yes (shared_ptr<const SampleBuffer>).",
                "Native audio-clip object: FL = Audio Clip Channel + "
                "Playlist instances; LMMS = SampleClip.",
                "Timeline sample track: FL = Playlist Audio Clip; LMMS "
                "= SampleTrack.",
                "Sample instrument: FL = Sampler Channel; LMMS = "
                "AudioFileProcessor.",
                "Clip -> playback handle: FL proprietary; LMMS = "
                "SampleClip -> SamplePlayHandle -> AudioEngine.",
                "External references: both yes. Embedded audio: FL = "
                "ZIP packaging; LMMS = AFP Base64 sampledata.",
                "Per-instance clip state: FL extensive (gain/pan/pitch/"
                "fine/mute/reverse/start/duration + fades); LMMS = "
                "SampleClip clip/playback state.",
                "Physical sample cloning: FL = Make unique as sample; "
                "LMMS less integrated.",
                "Time-stretch integrated with clips: FL yes; LMMS not "
                "equivalent.",
                "Sample reused by multiple clips: both yes (LMMS with "
                "explicit shared-buffer architecture).",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "FL: 5/5 core claims CONFIRMED with verbatim quotes from "
                "image-line.com/fl-studio-learning FL manual (Audio "
                "Clips - Playlist page, fetched directly): 'Audio Clips "
                "are a special version of the Sampler channels'; drag "
                "onto Playlist vs Channel Rack distinction; Clip "
                "Properties vs Channel Settings two-level model with the "
                "exact local property list (Gain, Pan, Pitch, Fine, "
                "Mute, Reverse, Start time & Duration); Make unique vs "
                "'Make unique as sample ... clones the sample file on "
                "disk'; per-instance volume envelopes/fades.",
                "LMMS: 7/7 CONFIRMED from current master source "
                "(raw.githubusercontent.com, fetched directly): "
                "SampleDecoder.h (decode -> optional<Result>); "
                "SampleBuffer.h (vector<SampleFrame>, sampleRate, "
                "audioFile, fromFile/fromBase64/toBase64); Sample.h "
                "(shared_ptr<const SampleBuffer>, atomic playback "
                "state, PlaybackState+AudioResampler, Loop "
                "Off/On/PingPong); SampleClip.h (public Clip, m_sample, "
                "m_recordModel, m_isPlaying, m_startFrameOffset, full "
                "method list); SampleTrack.cpp play() (exact "
                "sampleStart/samplePlayLength math, SamplePlayHandle -> "
                "AudioEngine); 1.3.0-alpha.2 release notes + PR #6610 "
                "(SampleBuffer refactor, three members, reverse-as-"
                "boolean, playback-time resampling).",
                "FLAGS folded in: (1) the infographic's '>2-channel "
                "files use first two channels (TODO)' detail was NOT "
                "independently verified - do not cite as confirmed; (2) "
                "the exact 'January 2026 SampleLoader removal' date was "
                "not checked - the refactor itself (PR #6610, alpha.2) "
                "is confirmed, cite that; (3) FL internals are "
                "proprietary - never invent FL C++ class names; the "
                "infographic correctly labels its FL side 'documented "
                "behavior / conceptual model'.",
            ]),
        ],
        [
            "Pulsegrid takeaway: mirror LMMS's explicit layering - it is "
            "source-visible, clean, and maps 1:1 onto our Rust engine. "
            "v0.30.0: SampleDecoder (symphonia: WAV/OGG/FLAC) -> "
            "SampleBuffer (Arc<Vec<f32>> stereo-interleaved at the "
            "engine sample rate, decoded once at load) -> clip playback "
            "state (start frame, play length, gain, pan, pitch ratio, "
            "reversed) -> AudioClip timeline object -> playlist track "
            "strip -> engine render. Clip math mirrors LMMS "
            "SampleTrack::play: samplePos = (songPos - clipStart - "
            "startOffset), clamped to min(clipLen, sampleLen).",
            "Pulsegrid takeaway: adopt FL's source + instance model for "
            "the UX. One SampleAsset (shared decoded data, like LMMS's "
            "shared_ptr<const SampleBuffer>) referenced by many "
            "AudioClip instances, each with local gain/pan/pitch/fine/"
            "reverse/mute/start/duration (FL's Clip Properties list). "
            "Project format v15 stores external file references "
            "(FL .flp style, relative paths like LMMS) - no audio bytes "
            "in the JSON. Mono upmixes to stereo at decode (LMMS "
            "behavior).",
            "Pulsegrid takeaway: honest deferrals. Clip time-stretch is "
            "UNLOCKED by this architecture but NOT in v0.30.0 (needs the "
            "stretch DSP; our pitch-shift insert is duration-preserving "
            "pitch, not time-stretch). Keep-on-disk streaming deferred "
            "(RAM cache first, like FL's default). Sample slicing / "
            "zero-crossing edits / per-instance fades deferred to a "
            "later editing pass.",
        ],
    ),
    (
        "FL Studio vs LMMS: Sidechain + Bus PDC",
        "Research topic 27. Researched as a routing-graph + latency-"
        "compensation architecture problem: 'sidechain' and 'PDC' are two "
        "separate mechanisms that interact. FL Studio has a Mixer routing "
        "graph in which sidechain connections are real inter-track audio "
        "paths, and its Automatic PDC explicitly applies to inter-track "
        "routing, multi-I/O routing, and sidechains. LMMS has an explicit "
        "FX-channel routing graph with dependency-aware scheduling, but no "
        "equivalent general host-level PDC layer; its roadmap describes "
        "fuller audio routing as necessary for 'proper sidechaining.'",
        [
            ("Three mechanisms get conflated", [
                "Bus routing (Track A + Track B -> Bus -> Master) is "
                "ordinary audio routing.",
                "Sidechain routing (KICK -> compressor detector while BASS "
                "-> compressor -> Master) supplies a second input stream "
                "to an effect; the kick is not meant to be audible through "
                "the compressor's normal path.",
                "Plugin Delay Compensation: when the vocal path has 2048 "
                "samples of plugin latency and the kick sidechain path has "
                "0, the detector can react early/late. The host must reason "
                "about audio-path latency + sidechain-path latency + "
                "bus/routing dependencies + plugin latency together.",
            ]),
            ("FL: the Mixer is a routing graph", [
                "A Mixer Track can route to another Mixer Track, to "
                "Master, or directly to an ASIO output (manual: 'It's also "
                "possible to route the audio of any Mixer Track directly "
                "to an ASIO Output and or to another Insert Track').",
                "Routing supports both ordinary audible sends and "
                "sidechain sends, and the two paths can coexist from one "
                "source track.",
            ]),
            ("FL: sidechain is audio, not a control signal", [
                "Manual: 'A sidechain is any unheard audio send from the "
                "Output of one Mixer Track directly into an effect plugin "
                "loaded somewhere on a second Mixer Track.'",
                "Fundamentally different from LMMS's classic Peak "
                "Controller -> parameter modulation workflow: FL's "
                "sidechain carries actual audio into the plugin's second "
                "input, which is why PDC can meaningfully align it.",
            ]),
            ("FL: 'Sidechain to this track' is a routing macro", [
                "Manual: 'The Sidechain to this track option ... is just a "
                "macro to creates a send link with the send volume set to "
                "0. So, you can change any send into a sidechain (only) "
                "link by manually setting the volume to 0.'",
                "Nonzero send level provides both audible and sidechain "
                "paths; zero send level leaves the sidechain path without "
                "making the source audible through the destination.",
            ]),
            ("FL: APDC explicitly covers sidechains (key finding)", [
                "Manual (Mixer Track Properties, PDC NOTES): '4. Routing - "
                "APDC also applies to inter-track routing, including multi "
                "input/output plugins, and sidechains.'",
                "This is not merely 'delay tracks until the Master lines "
                "up': the host maintains timing relationships throughout "
                "the Mixer routing topology, including the detector-vs-"
                "program relationship at a sidechain consumer.",
            ]),
            ("FL: why buses make PDC hard", [
                "KICK -> SIDECHAIN -> COMPRESSOR detector; BASS -> BUS -> "
                "[2048-sample FX] -> COMPRESSOR program input. Without "
                "alignment the compressor sees the kick transient at t=0 "
                "and the bass audio at t=+2048: detector and program are "
                "not temporally equivalent.",
                "FL's answer: the latency graph (plugin latency + route "
                "latency + sidechain latency + path compensation) is "
                "maintained alongside the audio graph.",
            ]),
            ("FL: per-track delay controls", [
                "Dedicated Track Delay/PDC control settable in "
                "milliseconds, samples ('the finest control over delay'), "
                "or beats.",
                "Manual PDC values act as offsets to Automatic PDC: "
                "'Manual PDC can be used in conjunction with Automatic "
                "PDC. Manual values are treated as offsets to APDC.'",
                "Monitoring distinction: PDC can be bypassed for live "
                "monitoring while still applying during rendering "
                "('PDC will be disabled/ignored for monitoring purposes "
                "but will still be applied when rendering to audio').",
            ]),
            ("FL: plugin latency reporting and drift", [
                "The Fruity Wrapper exposes plugin-reported latency and a "
                "manual correction: 'There is a Wrapper > Settings > "
                "Latency option where you can set a manual latency offset "
                "for plugins that incorrectly report their latency. This "
                "is remembered per-plugin.'",
                "APDC updates when latency is detected or changes "
                "(oversampling, lookahead, linear-phase modes). Maximus: "
                "'In [Linear Phase] mode the filters do not rotate the "
                "phase ... but the process needs to apply latency. "
                "Normally automatic plugin delay compensation in the "
                "mixer will align the audio output.' Fruity Limiter: "
                "look-ahead 'adds a delay to the plugins processed "
                "audio' - Image-Line recommends enabling APDC with "
                "attack > 0 ms.",
            ]),
            ("LMMS: a real routing graph (source-verified)", [
                "include/Mixer.h (master, fetched 2026-10-05): "
                "MixerChannel has 'pointers to other channels that this "
                "one sends to' (MixerRouteVector m_sends) and 'pointers "
                "to other channels that send to this one' "
                "(m_receives).",
                "class MixerRoute : public QObject with FloatModel "
                "m_amount (automatable route amount); Mixer::createRoute("
                "from, to, amount) / createChannelSend / deleteChannelSend.",
            ]),
            ("LMMS: dependency-aware scheduling (source-verified)", [
                "Mixer::masterMix(): 'add the channels that have no "
                "dependencies (no incoming senders, ie. no receives) to "
                "the jobqueue. The channels that have receives get added "
                "when their senders get processed, which is detected by "
                "dependency counting.'",
                "Worker-thread job queue (AudioEngineWorkerThread) with "
                "dependency counting (MixerChannel::processed() "
                "increments receivers' counters; a channel is queued once "
                "m_dependenciesMet >= m_receives.size()).",
                "This is DEPENDENCY SCHEDULING ('who runs first?'), not "
                "PDC ('who is late, and by how much?').",
            ]),
            ("LMMS: cycle prevention (source-verified)", [
                "Mixer::isInfiniteLoop / checkInfiniteLoop: 'determine if "
                "adding a send from sendFrom to sendTo would result in an "
                "infinite mixer loop' - recursive: 'follow sendTo's "
                "outputs recursively looking for something that sends to "
                "sendFrom'.",
            ]),
            ("LMMS: sidechain is real, but different", [
                "Do not write 'LMMS has no sidechain': it has "
                "sidechain-capable effects/workflows, Peak "
                "Controller-based modulation sidechaining, Mixer routing "
                "and FX-channel sends.",
                "The native Compressor's FEEDBACK option 'uses the "
                "compressor's output as the sidechain input' - an "
                "internal feedback detector, not an external "
                "Mixer-sidechain bus (docs.lmms.io).",
                "What it lacks is the general host-level audio sidechain "
                "routing + automatic PDC architecture FL exposes.",
            ]),
            ("LMMS: no general host PDC (source-verified)", [
                "The current Mixer/Mixer.cpp exposes routing, dependency "
                "detection, route amounts, cycle detection and worker "
                "scheduling - but no host-level latency/PDC calculation "
                "propagating plugin latency across buses and sidechain "
                "paths (checked 2026-10-05; worker class renamed to "
                "AudioEngineWorkerThread, m_dependenciesMet now atomic).",
                "The native Compressor's LOOKAHEAD 'will introduce a "
                "flat 20 ms of latency regardless of the set lookahead "
                "length' - plugin-internal latency, not host-wide "
                "compensation. Plugin-internal delay and host-wide PDC "
                "are two different layers.",
            ]),
            ("LMMS: roadmap acknowledges the gap", [
                "LMMS Progress Report, January 2025 (Long-term goals): "
                "'eventually we would like to see full audio routing "
                "capabilities in LMMS, which would enable proper "
                "sidechaining for example.'",
                "The 2026 plugin API proposal (issue #8275, open design "
                "proposal, not merged): motivated by 'implement CLAP "
                "support without breaking the CLAP API's threading "
                "rules', 'improve thread safety', 'improve real-time "
                "safety', 'clear separation of plugin and host "
                "responsibilities' - a prerequisite for robust host-level "
                "latency reporting, not a PDC engine itself.",
            ]),
            ("Head-to-head (condensed)", [
                "Mixer buses / inter-track routing / routing graph / send "
                "level / cycle prevention / dependency-aware processing / "
                "worker scheduling: both yes.",
                "Sidechain routing: FL = native audio sidechain (second "
                "plugin input); LMMS = controller-oriented workflows + "
                "internal feedback detector, no general host sidechain "
                "bus.",
                "Sidechain as actual audio path / sidechain-only routing "
                "(0% send macro): FL yes; LMMS no exact equivalent.",
                "Plugin latency reporting: FL yes (wrapper, per-plugin "
                "manual correction); LMMS plugin-specific only.",
                "Automatic PDC / PDC across routing / PDC across "
                "sidechains: FL yes (verbatim: 'APDC also applies to "
                "inter-track routing, including multi input/output "
                "plugins, and sidechains'); LMMS no equivalent general "
                "host system.",
                "Manual track delay (ms/samples/beats, offsets to APDC, "
                "monitoring bypass): FL yes; LMMS no equivalent.",
                "Lookahead latency: FL compensated by host PDC; LMMS "
                "plugin delays itself internally (20 ms flat).",
                "Core distinction: FL = routing graph + latency graph "
                "(answers 'when does the signal arrive?'); LMMS = "
                "routing graph + dependency scheduler.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "11/11 claims CONFIRMED with verbatim quotes. FL: APDC "
                "sidechain coverage (mixer_trackprops.htm NOTES); "
                "sidechain-to-this-track 0% macro + 'unheard audio send' "
                "definition + ASIO/insert routing (mixer.htm); per-track "
                "ms/samples/beats delay + manual-as-offset + monitoring "
                "bypass (mixer_trackprops.htm); wrapper latency option "
                "per-plugin (mixer_mixermenu.htm); Maximus linear-phase "
                "latency + Fruity Limiter lookahead APDC recommendation "
                "(plugin manuals).",
                "LMMS: m_sends/m_receives, MixerRoute/FloatModel amount, "
                "createRoute, isInfiniteLoop/checkInfiniteLoop recursive "
                "(include/Mixer.h, master); masterMix no-dependency-first "
                "comment + AudioEngineWorkerThread job queue + "
                "dependency counting (src/core/Mixer.cpp); Jan 2025 "
                "progress report 'full audio routing capabilities ... "
                "enable proper sidechaining' (lmms.io); Compressor "
                "FEEDBACK + flat-20ms LOOKAHEAD (docs.lmms.io); plugin "
                "API proposal #8275 motivations (open, unmerged).",
                "Also confirmed still-current: no host-level "
                "latency-propagation layer in Mixer.h/Mixer.cpp as of "
                "2026-10-05.",
            ]),
        ],
        [
            "Pulsegrid takeaway: v0.25.0 built the FL side of this "
            "architecture (sidechain sends -> inaudible per-track SC bus "
            "fed by sidechain-marked sends, native Ducker consumer, "
            "inter-track PDC with input_align + final_delay). The "
            "documented gap was that the SC bus was NOT PDC-aligned.",
            "Pulsegrid takeaway: v0.31.0 closes that gap by implementing "
            "FL's 'APDC also applies to ... sidechains'. Effect::"
            "uses_sidechain() marks detector consumers (Ducker today; "
            "CLAP hosting is stereo-only so hosted plugins never "
            "qualify). The PDC planner computes per-track latency before "
            "the first consumer; when the detector path is slower, the "
            "destination's input_align is raised so its program waits "
            "for the detector (the raise propagates through the audible "
            "DAG, Master stays aligned); the residual per-edge difference "
            "becomes a delay line on the SC-bus feed itself (per-edge, "
            "since senders can differ).",
            "Pulsegrid takeaway: the union of audible + sidechain edges "
            "is a DAG (Python validation rejects cycles across ALL "
            "sends, sidechain included - matching LMMS's "
            "checkInfiniteLoop philosophy), so one topological pass "
            "settles every input_align with no fixpoint iteration.",
            "Pulsegrid takeaway: honest FL-parity notes. Manual "
            "per-track delay (FL's ms/samples/beats control, offsets to "
            "APDC) is NOT implemented - open item. Runtime CLAP latency-"
            "change detection is NOT implemented (plan recomputes on "
            "arrangement/FX/send changes). SC alignment is inaudible-"
            "path only: it never shifts the destination's audible timing "
            "except via the input_align raise, which the Master "
            "alignment absorbs.",
        ],
    ),
    (
        "FL Studio vs LMMS: Keyboard Tracking",
        "Research topic 28. Researched as a synthesis/control "
        "architecture problem: who owns the pitch-to-parameter mapping. "
        "Two meanings must be separated: host-level keyboard tracking "
        "(the DAW takes the incoming note/pitch and modulates parameters "
        "like cutoff, resonance, pan, Mod X/Y) vs instrument/plugin-level "
        "tracking (the synth maps its own played note to an internal "
        "parameter). FL Studio has a genuine host-level Keyboard Tracker; "
        "LMMS's core InstrumentTrack knows the pitch but documents no "
        "equivalent generic host tracker - pitch-dependent modulation is "
        "the instrument/plugin's responsibility.",
        [
            ("What keyboard tracking means", [
                "NOTE PITCH -> MIDI note number -> modulation that rises "
                "with pitch -> target parameter (cutoff, resonance, pan).",
                "Classic use: a fixed 2 kHz lowpass behaves very "
                "differently on C2 vs C6; tracking lets the filter follow "
                "the played pitch so the timbral relationship stays "
                "consistent across the keyboard.",
            ]),
            ("FL: a genuine host-level Keyboard Tracker", [
                "Channel Settings -> Miscellaneous has two trackers: "
                "Velocity Tracker and Keyboard Tracker. The manual: "
                "'There are two trackers, one for velocity and one for "
                "keyboard key number. The keyboard tracker links the note "
                "number (i.e. note pitch) to the cutoff, resonance and "
                "panning properties of notes.'",
                "This is part of FL's Instrument Channel-level note "
                "processing, not a feature of one synthesizer: a "
                "note-derived modulation source at the DAW channel level.",
            ]),
            ("FL: configurable midpoint (bipolar)", [
                "The tracker has a MID value: 'For both trackers there is "
                "a middle value where no offsets are generated. Higher "
                "values generate positive offsets and lower values "
                "generate negative ones. For the keyboard tracker, the "
                "middle value of a note's pitch (e.g. C5 or B3) "
                "determines the no-offset point.'",
                "Mod X / Mod Y knobs are bipolar: 'The knobs are bi-polar "
                "so negative and positive tracking modulation can be "
                "generated.' Positive and negative tracking are both "
                "first-class (e.g. low notes brighter).",
            ]),
            ("FL: Mod X / Mod Y destinations", [
                "'There are two destinations for the keyboard tracking "
                "modulation, Mod X and Mod Y. These can be linked to "
                "parameters in FL Studio such as a plugins filter-"
                "cutoff.'",
                "The tracker is therefore closer to a host-level "
                "note-expression/modulation system than to a single "
                "synth filter knob.",
                "'Both trackers remain active regardless of which one is "
                "currently selected' - key and velocity tracking are "
                "independent modulation sources.",
            ]),
            ("FL: two layers of keyboard tracking", [
                "Layer A (host): FL Channel Keyboard Tracker -> "
                "Cut/Res/Pan/Mod X/Mod Y. Layer B (instrument): the "
                "synth's own tracker, e.g. Sawer KBD TRACK.",
                "Sawer manual: 'KBD TRACK (Keyboard Tracking) - This links "
                "the cutoff frequency to the MIDI note number. Low notes "
                "will have a lower cutoff frequency for a darker sound "
                "while higher notes will have a higher cutoff frequency "
                "for a brighter sound. The amount of keyboard tracking "
                "controls the strength of this effect.'",
            ]),
            ("FL instruments use tracking extensively", [
                "Harmless kb.t: 'Keyboard tracking, kb.t, is adding an "
                "offset depending on note pitch. This can be useful to "
                "make higher notes relatively brighter than lower "
                "notes.' (Bipolarity of kb.t itself NOT explicitly "
                "confirmed - the +/- wording on that page refers to the "
                "filter envelope amt control.)",
                "Harmor kb track: pitch-dependent filter cutoff offset, "
                "positive or negative by knob direction (secondary source "
                "- a flashcard set quoting the manual; primary page not "
                "retrieved).",
                "Morphine K-TRK is NOT filter tracking: 'Acts as an "
                "envelope playback speed modifier in response to "
                "keyboard/note position. When positive values are set the "
                "higher keys will progress through the Morph Path "
                "faster.' Keyboard tracking as a general synthesis "
                "technique.",
                "3xOsc Key Tracking: 'Middle - Middle value where no "
                "modulation offset is generated... To Pan - Note value is "
                "used to modulate Pan. To Cutoff - Note value is used to "
                "modulate Filter Cutoff. To Resonance - Note value is used "
                "to modulate Filter Resonance.' (Confirmed via FL Studio "
                "Mobile 3xOsc docs; desktop page not separately checked.)",
                "MiniSynth Kbd Trk: 'Filter Cutoff frequency can be set "
                "to increase (turn right) or decrease (turn left) with "
                "keyboard position.' GMS KBD: 'The filter cutoff will "
                "track up and down with the note played.'",
                "CORRECTION folded in: DirectWave's KTRK is playback-"
                "PITCH tracking ('100 = 100 cents per semitone, 0 = no "
                "pitch tracking'), NOT filter-cutoff tracking - removed "
                "from the filter examples.",
            ]),
            ("LMMS: pitch-aware, but no generic host tracker", [
                "The LMMS manual's Instrument Window ENV/LFO docs "
                "enumerate filter modulation sources as envelope and LFO "
                "only: 'LMMS allows you to control both the cutoff "
                "frequency and the Q factor via an envelope... The LFO "
                "provided by LMMS allows you to control the value of the "
                "volume, cutoff and Q factor targets independently.' No "
                "pitch/key source appears (confirmed as a negative).",
                "LMMS forum, 'Smoothing filter' (2014): user asked for a "
                "pitch-tracking filter; contributor diiz: 'You want a "
                "pitch-tracking filter. Yeah, we don't have that at the "
                "moment.' Historically recognized as a missing generic "
                "feature.",
            ]),
            ("LMMS: plugin-level tracking is still possible", [
                "The manual: 'Plugin: shows the controls for how this "
                "particular plugin generates sound. This is the only tab "
                "that changes per plugin.' Individual instruments/plugins "
                "(TripleOscillator, ZynAddSubFX, VSTs) can implement "
                "their own key tracking.",
                "Defensible claim: 'No documented generic host-level "
                "keyboard tracker equivalent to FL Studio's Channel "
                "Keyboard Tracker; plugin-specific key tracking can "
                "exist.' - NOT 'LMMS has no keyboard tracking.'",
            ]),
            ("What is NOT keyboard tracking in LMMS", [
                "Microtuner (1.3.0-alpha.2: 'Alternative tunings "
                "(microtonality) and keyboard mappings') changes "
                "keyboard key -> musical pitch, not musical pitch -> "
                "synthesis parameter.",
                "Base Note changes sample pitch interpretation ('for the "
                "AudioFileProcessor plugin ... allows you to adjust the "
                "note to be played back at its correct pitch'), not "
                "filter tracking.",
            ]),
            ("Modulation ownership (core distinction)", [
                "FL: the DAW owns NOTE -> TRACKER -> parameter "
                "modulation at the Channel level, AND instruments can "
                "additionally implement their own tracking.",
                "LMMS: the framework owns NOTE -> instrument/plugin; the "
                "PLUGIN owns NOTE -> internal parameter modulation. "
                "Responsibility is shifted toward the instrument.",
                "FL's exact Channel-tracker transfer equation is "
                "unpublished (documented as midpoint + bipolar offsets) "
                "- do not invent a formula for FL; plugin-level "
                "implementations differ.",
            ]),
            ("Head-to-head (condensed)", [
                "Knows incoming note pitch: both yes.",
                "Generic host-level Keyboard Tracker: FL yes; LMMS no "
                "documented equivalent.",
                "Tracker midpoint / bipolar tracking: FL yes; LMMS no "
                "equivalent found.",
                "Keyboard -> cutoff/resonance/pan/Mod X/Y: FL yes (host "
                "tracker); LMMS plugin-dependent (no generic host "
                "source).",
                "Velocity tracker as separate source: FL yes ('Both "
                "trackers remain active'); LMMS different per-note "
                "velocity/plugin mechanisms.",
                "Plugin-specific keyboard tracking: both possible (FL: "
                "Sawer/Harmless/Harmor/Morphine/GMS/MiniSynth; LMMS: "
                "plugin-dependent).",
                "Keyboard mapping / base-note / microtonal mapping: both "
                "yes - but these are key->pitch, not pitch->parameter.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "13/14 CONFIRMED with verbatim quotes: FL host tracker "
                "targets + MID + bipolar Mod X/Y + independent trackers "
                "(chansettings_misc.htm); 3xOsc Middle/To Pan/To Cutoff/"
                "To Resonance (FL Mobile 3xOsc manual); Sawer KBD TRACK "
                "(Sawer.htm); Harmless kb.t offset (Harmless_tutorials."
                "htm); Morphine K-TRK envelope speed (Morphine_MorphMix."
                "htm, Morphine_Generator.htm); MiniSynth Kbd Trk; GMS "
                "KBD; LMMS ENV/LFO negative (docs.lmms.io 3.6); LMMS "
                "forum 'we don't have that at the moment' (t=1366); "
                "Microtuner release notes; Base Note docs; Plugin-tab "
                "docs.",
                "CORRECTIONS folded in: (1) DirectWave KTRK is playback-"
                "pitch tracking, not filter tracking - removed from "
                "examples; (2) Harmless kb.t bipolarity unverified - "
                "softened; (3) Harmor via secondary source - flagged; "
                "(4) 3xOsc via Mobile docs - noted.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement the host-level tracker as the "
            "pitch-domain sibling of v0.26.0's velocity tracking (FL "
            "separates key and velocity tracking as independent sources). "
            "v0.32.0: per-track key_track (bipolar -1..1, 0 = off) + "
            "key_track_mid (MIDI 0..127, default 60 = C4). Voice::trigger "
            "computes per-note LP cutoff = base * 2^(vel_oct + key_oct) "
            "where key_oct = key_track * (pitch - mid) / 12, clamped "
            "40-20000 Hz (note domain, evaluated at trigger). +1.0 is "
            "100% tracking: one octave of pitch moves the cutoff one "
            "octave (the traditional synthesizer definition; FL's exact "
            "internal equation is unpublished, so ours is documented, "
            "not claimed as FL's).",
            "Pulsegrid takeaway: same voice coverage as velocity tracking "
            "(Kick/Snare/Bass/Lead base cutoffs; Hat's bypassed LP skips "
            "tracking, documented). CLAP instruments receive note pitch "
            "directly and map it themselves (FL's generator-level path, "
            "untouched). Python: additive model fields (no format bump, "
            "old projects default off/C4), bridge passes through, Mixer "
            "'Built-in voices' section gains a KeyTrack row (bipolar % "
            "slider + middle-note slider with note-name readout, e.g. "
            "A4), one undoable edit, live update without rebuild.",
            "Pulsegrid takeaway: honest FL-parity notes. Implemented "
            "keyboard -> cutoff only (the traditional pitch-tracking "
            "meaning the LMMS forum thread asked for). Keyboard -> "
            "resonance/pan/Mod X/Y (FL's wider tracker targets) are open "
            "items. No automation lanes for the tracker params (same as "
            "velocity tracking).",
        ],
    ),
    (
        "FL Studio vs LMMS: Mixer Painter Extraction",
        "Research topic 29. Researched as 'how is the Mixer's visual "
        "rendering coupled to the Mixer UI, and how far could the "
        "painting/rendering be extracted into a separate renderer?' "
        "Evidence asymmetry first: LMMS is open source, so the actual "
        "Mixer widgets, painting code, signals, models, layouts, and "
        "refactoring history are inspectable; FL Studio is proprietary, "
        "so only its observable UI architecture and documented behavior "
        "can be established - no honest claims about internal renderer "
        "classes, scene graphs, or GPU pipelines.",
        [
            ("What 'Mixer painter extraction' means", [
                "A fully extracted painter/renderer means the code that "
                "draws the Mixer is not also responsible for all Mixer "
                "behavior. Ideal: Mixer Model -> Presentation State -> "
                "Interaction Controller + Renderer -> GUI.",
                "The question: how close are FL Studio and LMMS to this "
                "separation?",
            ]),
            ("LMMS: core/GUI separation is inspectable", [
                "LMMS uses Qt; the Mixer is GUI classes (MixerView, "
                "MixerChannelView, Fader, PeakIndicator, buttons, "
                "effects UI), not part of the audio-processing "
                "implementation (AudioPort, FxChannel, MixerChannel, "
                "MixerRoute). Data crosses via models/signals.",
                "The 1.3 dev branch explicitly refactored MixerChannelView "
                "('Abstraction in MixerChannelView' #7057, 'Reformat "
                "MixerChannelView classes' #7431, 'Tidy up "
                "MixerChannelView' #7527, 'Improve performance when "
                "moving channels in the Mixer' #8235) - the project "
                "itself has been separating Mixer UI responsibilities.",
            ]),
            ("LMMS Fader: textbook widget-owned painting", [
                "Current master Fader::paintEvent() (src/gui/widgets/"
                "Fader.cpp) constructs a QPainter and calls "
                "paintLevels(ev, painter, ...), paintFaderTicks(painter) "
                "(gated by the showfaderticks setting and linear model), "
                "and painter.drawPixmap(...) for the knob.",
                "So the Fader owns interaction + model + geometry + peak "
                "state + tooltip + painting - closer to one big widget "
                "than to FaderController/FaderPresentationState/"
                "FaderRenderer.",
            ]),
            ("But paintLevels() is already partly separated", [
                "paintEvent() delegates to paintLevels(), "
                "paintFaderTicks(), drawPixmap(). paintLevels() computes "
                "min/max peak, L/R peaks, persistent peaks, unity "
                "position, meter geometry (leftMeterRect/rightMeterRect, "
                "peakRectL/R, unityRectL/R, level rects), clipping paths, "
                "gradients (clip/warn/ok stops), peak indicators - then "
                "draws them.",
                "Extraction step is straightforward: Fader::paintLevels"
                "(QPainter&) -> FaderMeterRenderer::paintLevels"
                "(RenderContext&).",
            ]),
            ("The data->geometry mapping is explicit", [
                "paintLevels() builds 'const LinearMap "
                "valuesToWindowCoordinates(...)' with the comment: 'This "
                "linear map performs the following mapping: Value (dbFS "
                "or linear) -> window coordinates of the widget. It is "
                "for example used to determine the height of peaks, "
                "markers and to define the gradient for the levels'.",
                "Clean boundary: presentation calculation (peak -> mapped "
                "Y coordinate) vs rendering (rectangle -> QPainter).",
            ]),
            ("A presentation-state layer hides in the meter", [
                "Fader maintains m_fPeakValue_L/R, m_persistentPeak_L/R, "
                "m_fMinPeak, m_fMaxPeak and converts them to visual "
                "geometry before painting - essentially a miniature "
                "Mixer Meter Presentation Model, not yet packaged as a "
                "standalone renderer object.",
            ]),
            ("Peak production vs peak visualization are separated", [
                "The Fader has a peakChanged signal reporting peak values; "
                "a new PeakIndicator (QLabel) class has an updatePeak slot "
                "connected to it. Architecture: audio processing -> Fader "
                "-> peakChanged -> PeakIndicator::updatePeak, and -> "
                "update() -> Fader paintEvent(). Data-driven UI updates, "
                "not polling.",
                "CORRECTION folded in: the brief cited PR #8476, but that "
                "is the recording-feature merge; the peak-indicator "
                "commit ('Add peak indicators to the mixer strips', "
                "Rossmaxx) is inside it and the feature is tracked as "
                "#7295 in the alpha.2 notes. Signal/slot substance "
                "confirmed; cite #7295.",
            ]),
            ("1.3.0-alpha.2 mixer items (release 2026-09-06)", [
                "'Peak indicators in mixer strips (#7295)', 'Scalable "
                "consistent faders with themeable gradients, marker at "
                "unity, dbFS by default (#7045)', 'Resizable mixer window "
                "(#7037)', 'Resizable mixer channels/strips (#7293)', "
                "'Improve performance when moving channels in the Mixer "
                "(#8235)', plus 'Add mixer LCD channels (#6831)' and "
                "'Only repaint LcdWidget if necessary (#7187)'.",
                "CAVEAT folded in: the notes aggregate 5+ years of dev-"
                "branch work since alpha.1 (932 commits); only the "
                "release itself is September 2026. '2026 Mixer work' "
                "overstates recency.",
            ]),
            ("Where LMMS sits on the extraction ladder", [
                "Level 1: everything in one widget. Level 2: widget + "
                "helper paint functions. Level 3: widgets + "
                "presentation/model separation. Level 4: dedicated "
                "renderer. Level 5: retained-mode scene. Level 6: GPU "
                "rendering.",
                "LMMS Mixer today is approximately Level 2 -> Level 3, "
                "with pieces moving toward Level 3 (Fader = widget + "
                "model + interaction + meter state + helper rendering "
                "functions; not yet independent Model/Controller/"
                "Renderer).",
            ]),
            ("FL Studio: what CAN be established", [
                "The manual documents FL's 'vectorial Graphical User "
                "Interface' with GUI scaling for high-resolution "
                "displays (envsettings_general.htm): 'rescale (resize) "
                "FL Studio's vectorial Graphical User Interface (GUI), "
                "including separate options for pop-ups, menus and the "
                "cursor.'",
                "Mixer visual state: peak meters, Meter-Wave view "
                "(Alt/Opt+W: 'Replaces the Mixer peak meters with "
                "waveform views. Width now equals peak level.'), faders, "
                "routing iconography ('Source Track - Any source track "
                "routed to the selected track shows as a grayed-out send "
                "switch, to indicate it's unavailable (to prevent "
                "feedback loops)'; 'Send in use - The small up-arrows "
                "indicate that another track is sending to the track'), "
                "effect slots, track colors/icons/names, selection, "
                "docking.",
                "Playlist/Mixer separation: 'As Playlist Tracks are not "
                "bound to Mixer tracks, the Channel Rack to Mixer routing "
                "of Instruments used in Pattern Clips determines the "
                "Mixer tracks that are used.' Separate graphical surfaces "
                "over the project model -> separate renderer concerns.",
                "Multiple visual representations of the same audio state "
                "(peak meter vs Meter-Wave) implies a presentation "
                "boundary conceptually - but this is an inferred model, "
                "not a claim about private classes.",
            ]),
            ("FL Studio: what CANNOT be established", [
                "No public source for: a MixerRenderer class, a retained-"
                "mode Mixer scene graph, GPU acceleration of the Mixer, "
                "or specific painter/controller boundaries. The "
                "technically honest diagram labels FL's Mixer rendering "
                "'proprietary / not publicly documented'.",
                "CORRECTION folded in: the brief's claim 10 conflated two "
                "features. Playlist 'Performance mode' (Ctrl+P) is a "
                "live-performance trigger/loop feature, NOT a CPU-"
                "scaling mode. FL's actual UI-load controls are General "
                "settings 'Animations' ('Don't distract me' = all "
                "animations off) and 'Animation refresh rate' (Less "
                "smooth/Smooth/Ultrasmooth) - and the manual explicitly "
                "says 'Animations generally don't increase CPU load.'",
            ]),
            ("Extraction stages (LMMS, and the recommended DAW design)", [
                "Stage 1 (current): MixerChannelView owns state + layout "
                "+ interaction + painting. Stage 2: extract visual "
                "calculations -> Presentation State + painting helpers. "
                "Stage 3: dedicated renderer (MixerChannelView keeps "
                "interaction + presentation state; MixerChannelRenderer "
                "-> QPainter). Stage 4: Renderer Interface with Qt/GPU/"
                "alternate/test backends consuming the same presentation "
                "model.",
                "Start with the Fader meter, not MixerView: Fader::"
                "paintLevels() already divides into DATA -> mapping -> "
                "geometry -> drawing. Then Fader Renderer -> MixerChannel "
                "Renderer -> Mixer Renderer, incrementally.",
                "Dirty flags for targeted rendering (NONE/METER/FADER/"
                "ROUTING/NAME/EFFECTS/LAYOUT/FULL): peak changed -> "
                "DIRTY_METER -> redraw meter, not the whole Mixer. LMMS "
                "already does localized update() repaints per widget; "
                "dirty flags are the next layer.",
                "Keep Qt's interaction infrastructure: the goal is not "
                "'one giant custom canvas' but Qt interaction widgets + "
                "presentation state + dedicated painting helpers.",
            ]),
            ("Independent verification (2026-10-05, primary sources)", [
                "CONFIRMED with verbatim quotes: Fader::paintEvent/paintLevels/LinearMap/gradient structure (raw "
                "Fader.cpp master); peakChanged -> PeakIndicator::updatePeak signal/slot (commit inside #8476, "
                "tracked as #7295); alpha.2 mixer items (release notes, lmms.io 2026-09-06); MixerChannelView PRs "
                "#7057/#7431/#7527/#8235; FL vectorial GUI (envsettings_general.htm); Meter-Wave "
                "(mixer.htm); routing iconography (mixer.htm); Playlist/Mixer separation (playlist.htm, mixer.htm).",
                "CORRECTIONS folded in: (1) peak-indicator PR is #7295, not #8476; (2) Performance Mode is live-"
                "performance, not CPU scaling - real UI-load controls are Animations/Animation refresh rate, and "
                "the manual disclaims the CPU rationale; (3) '2026 Mixer work' overstates recency - notes aggregate "
                "5+ years, only the release is Sep 2026; (4) paintFaderTicks is gated by the showfaderticks setting.",
            ]),
        ],
        [
            "Pulsegrid takeaway: implement the brief's section-38 "
            "recommendation one step further than current LMMS. v0.33.0: "
            "new python/daw/ui/mixer_render.py - MeterTheme (geometry, "
            "colors, zones, decay-per-tick, peak-hold dwell), "
            "MeterPresentation (level, peak_hold, clipping - the "
            "miniature presentation model), MeterState.update() "
            "(ballistics: instant attack / exponential decay / floor + "
            "peak-hold dwell/release; pure data, no tkinter, no engine), "
            "MeterRenderer.render(canvas, pres) (pure drawing; owns "
            "geometry/colors/dirty-checking via WeakKeyDictionary; "
            "returns True/False for redrawn/skipped).",
            "Pulsegrid takeaway: the Mixer is now controller-only for "
            "meters: set_levels() wires engine peaks -> MeterState -> "
            "MeterRenderer; Mixer._draw_meter deleted. The debug "
            "window's horizontal meters reuse the same renderer with a "
            "horizontal MeterTheme (one presentation model, multiple "
            "render targets). New behavior from the presentation model: "
            "peak-hold markers and clip LEDs on both meter styles. "
            "Dirty-check: render() skips canvases whose presentation is "
            "visually unchanged (sub-0.5px), so idle meters cost nothing "
            "per poll tick.",
            "Pulsegrid takeaway: honest FL-parity notes. This is a "
            "Level 2 -> Level 3 extraction (brief's ladder): widget + "
            "presentation/model separation. NOT done: strip-level "
            "presentation objects (name/color/mute/effects still widget-"
            "built), per-region dirty flags (METER/FADER/ROUTING/...), "
            "retained-mode scene or GPU backend - open items. tkinter "
            "keeps the interaction layer (brief section 30: don't fight "
            "Qt's - here Tk's - event system with one giant canvas).",
        ],
    ),
    (
        "FL Studio vs LMMS: Automation Curve Shapes",
        "Research topic 30 (infographic: Automation_Curve_Shapes_"
        "Presentation.pptx, 15 slides, real editable text, zero "
        "flattened images; verified 2026-10-05). Studied as 'how do "
        "the curve shapes between automation control points work, and "
        "how is the curve evaluator separated from the painter that "
        "draws it?' Evidence asymmetry first: LMMS is open source, so "
        "the actual AutomationNode/AutomationClip model, tension model, "
        "tangent editing, and paintEvent code are inspectable; FL "
        "Studio is proprietary, so only its documented curve vocabulary "
        "and observable behavior can be established - no honest claims "
        "about FL's internal math.",
        [
            ("FL Studio: Automation Clips as internal controllers", [
                "FL manual (automation_internal.htm): 'the Automation "
                "Clip moves linked controls according to a user drawn "
                "envelope path.'",
                "FL manual (playlist_automationclip.htm): Automation "
                "Clips 'move (automate) linked controls on the FL Studio "
                "interface or plugins. They are closely related to Event "
                "automation and are a type of internal controller. Unlike "
                "event data they are not bound to a specific pattern and "
                "exist as a special type of Generator, loaded into a "
                "Channel.' Data 'can be displayed in the Playlist window "
                "as a line-graph.'",
                "Event Automation is the contrast: bound to a specific "
                "Pattern/Pattern Clip (automation_eventeditor.htm). Draw "
                "tool draws freehand; right-drag draws straight lines; "
                "Paint tool right-drag edits in interpolate mode; "
                "'Interpolate (I) - Redraw and connect data with straight "
                "lines.'",
            ]),
            ("FL Studio: all 11 documented curve types (verbatim)", [
                "From the 'Change curve type' section of "
                "playlist_automationclip.htm - every description below "
                "is a manual quote, not a paraphrase:",
                "Single curve - 'Default mode for creating straight or "
                "curved segments (depending on the tension).'",
                "Double curve - 'Smooth 'S' curves, useful for scratching "
                "effects.'",
                "Alt single curve - 'Asymmetrical smooth 'S' curves, "
                "useful for scratching effects.'",
                "Alt double curve - 'Asymmetrical linear, or accelerating "
                "/ decelerating curves (depending on the tension).'",
                "Hold - 'Single steps between points, useful for creating "
                "jumps in position.'",
                "Stairs - 'Multiple steps between the control points. "
                "Left-click on the tension handle and move your mouse "
                "up/down to change the step frequency. Useful for glitch "
                "/ decimation effects. Step size controls the "
                "'graininess'.'",
                "Smooth stairs - 'Multiple smooth steps between the "
                "control points. ... Useful for changes in pitch and "
                "granular effects.'",
                "Pulse - 'Square wave pulse, adjust the frequency with "
                "the tension handle.'",
                "Wave - 'Sine wave pulse, adjust the frequency with the "
                "tension handle.'",
                "Half sine - 'One half of a sine wave. Useful for "
                "creating start, stop and scratch effects.'",
                "Smooth - 'Allows for smoothly joining points with an "
                "'S' curve.'",
                "Tension handle: 'Left-click a tension handle and move "
                "up/down. Holding (Ctrl) will allow fine adjustment. "
                "Right-Click the tension handle to reset curve tension.'",
                "Step mode: ''Step' - Sets the Automation Clip curve "
                "editor in 'step editing' mode. Left-click and drag in "
                "the clip to create a 'free hand' curve where a new "
                "control point is defined for every step in the timeline "
                "(steps depend on the current snap settings). Hold SHIFT "
                "while dragging to draw 'pulse' lines.'",
            ]),
            ("FL Studio: Automation Clip LFO layer (verbatim)", [
                "Channel Settings section of playlist_automationclip.htm: "
                "Speed (SPD) sets LFO speed; Tension (TENS) 'sets the LFO "
                "shape 'tension', a shape morph ... starting with square "
                "(pulse), sine, triangle and 'pulse'' (the manual's own "
                "wording is redundant as written); Shape Skew (AK) 'skews "
                "the LFO shape' (with triangle: saw/reversed saw); Pulse "
                "Width (NW) sets LFO width; Level (LVL) sets amplitude - "
                "'Turn it left for negative amplitude (inverted values) or "
                "right for positive amplitude.'",
                "Multiply switch: 'When the switch is enabled the two "
                "values will be multiplied, i.e. the LFO acts as an "
                "amplitude modulator for the envelope. This is useful for "
                "unipolar (one directional) properties such as cutoff "
                "frequency, volume, etc. When the switch is disabled both "
                "values are added together ... This is useful for bipolar "
                "parameters such as panning.'",
            ]),
            ("FL Studio: controller mapping formulas (verbatim)", [
                "automation_form.htm ('Automation mapping formula') "
                "documents custom formulas via Formulas.txt. Canonical "
                "example: 'Up and down smooth: Sin(Input*Pi)'. The list "
                "includes '1-Cos(Input*Pi)*0.5-0.5' and step/threshold "
                "functions such as '1-Min(Round(Input*2),1)', "
                "'Max(Round(Input*2),1)-1', 'int(Input*9)/8', and "
                "'ifg(ifl(...))' conditionals.",
            ]),
            ("FL Studio: common envelope-editing language (corrected "
             "framing)", [
                "Fruity Envelope Controller manual documents the shared "
                "curve-editing vocabulary verbatim: 'Add a new Control "
                "Point', 'Reposition a Control Point', 'Delete a Control "
                "Point', 'Change Segment Type - The envelope editor "
                "offers three types of spline segments ... Single Curve "
                "... Double Curve ... Hold', 'Change Segment Tension "
                "(Acceleration) - You can drag the tension handle ... "
                "up/down ... Right-click the handle to reset to a straight "
                "line. Hold CTRL during adjustment to fine tune.'",
                "CORRECTION (independent verification): no single "
                "Image-Line page states that one editor is literally "
                "shared across Edison/Sytrus/Harmor. Edison documents its "
                "own envelope-apply tools with the same ops "
                "(Edison_7.htm); Slicex documents the same editing ops; "
                "Harmor's product copy references the 'multipoint "
                "envelope editor of Sytrus fame' (secondary sources). "
                "Honest framing: a common documented envelope-editing "
                "language across FL's envelope tools - the shared "
                "vocabulary is primary-sourced; the 'one editor' claim "
                "was softened.",
                "Channel Settings envelopes (chansettings_ins.htm): "
                "'Tension (below the knob) - Controls the convex/concave "
                "shape of the curve' - repeated for Attack/Decay/Release.",
            ]),
            ("LMMS: AutomationNode members (verbatim source)", [
                "include/AutomationNode.h on current master declares "
                "m_pos, m_inValue, m_outValue, m_inTangent, m_outTangent, "
                "m_lockedTangents with public accessors (getInValue/"
                "setInValue, getOutValue/setOutValue/resetOutValue, "
                "getInTangent/setInTangent, getOutTangent/setOutTangent, "
                "lockedTangents()/setLockedTangents(bool)).",
                "Source comment: 'We might have discrete jumps between "
                "curves, so we possibly have two different tangents for "
                "each side of the curve. If m_inValue and m_outValue are "
                "equal, m_inTangent and m_outTangent are equal too.' On "
                "locking: 'If the tangents were edited manually, this "
                "will be true. That way the tangents from this node will "
                "not be recalculated.'",
                "In/out VALUES serve discrete jumps (valueAt()/setDragValue "
                "keep m_dragKeepOutValue); the discrete-jump comment is "
                "attached to the tangents - substance matches, wording "
                "placed carefully.",
            ]),
            ("LMMS: per-clip progression vocabulary (names verbatim; "
             "glosses are paraphrase)", [
                "AutomationEditor.cpp (~lines 2403-2407): the three "
                "interpolation toolbar actions are tr('Discrete "
                "progression'), tr('Linear progression'), tr('Cubic "
                "Hermite progression'), backed by AutomationClip::"
                "ProgressionType::{Discrete, Linear, CubicHermite} "
                "(src/core/AutomationClip.cpp setProgressionType).",
                "CORRECTION (independent verification): the descriptive "
                "glosses ('steady rate between control points', 'smooth "
                "curve and eases into peaks and valleys', 'does not "
                "smoothly interpolate') appear nowhere in the source or "
                "on docs.lmms.io - they were the brief's own paraphrase, "
                "not quoted UI strings. The UI exposes only the three "
                "names.",
                "Per-CLIP (not per-segment) progression is LMMS's design "
                "choice; FL's curve type is per-segment.",
            ]),
            ("LMMS: tangent editing with locking (verbatim source)", [
                "EditMode::EditTangents in mousePressEvent: 'Lock the "
                "tangents from that node, so it can only be manually "
                "edited' -> node.value().setLockedTangents(true) -> "
                "m_action = Action::MoveTangent.",
                "Right-click resets: resetTangent lambda = "
                "setLockedTangents(false), then m_clip->"
                "generateTangents(node, 1). mouseMoveEvent computes "
                "newTangent from the drag and calls setOutTangent/"
                "setInTangent.",
            ]),
            ("LMMS: tension model 0->1 (range confirmed; doc wording "
             "unverified)", [
                "m_tensionModel = new FloatModel(1.f, 0.f, 1.f, 0.01f) - "
                "default 1, min 0, max 1: the 0->1 range is confirmed in "
                "source. The knob tooltip is only tr('Tension value for "
                "spline'); tension is stored per-clip (m_tension(1.0) "
                "default; setTension accepts 0..1).",
                "CORRECTION (independent verification): the 'higher "
                "tension = smoother curves but may overshoot / lower "
                "tension = slope levels off at control points' wording "
                "was NOT found in the LMMS source or on docs.lmms.io "
                "(whose automation page merely lists 'Tension knob' as a "
                "toolbar tool). Treat that sentence as UNVERIFIED as a "
                "quoted doc statement.",
            ]),
            ("LMMS: paintEvent curve rendering (verbatim source)", [
                "void AutomationEditor::paintEvent(QPaintEvent *pe) -> "
                "QPainter p(this). Curve walk: 'float* values = "
                "m_clip->valuesAfter(POS(it));' then the comment 'We are "
                "creating a path to draw a polygon representing the "
                "values between two nodes.' QPainterPath path; path."
                "moveTo(QPointF(xCoordOfTick(POS(it)), yCoordOfLevel(0))); "
                "then path.lineTo(...) per tick, closed to the baseline, "
                "p.fillPath(path, m_graphColor).",
                "Tangent handles are conditional presentation state: 'if "
                "(m_clip->canEditTangents() && LOCKEDTAN(it)) { "
                "drawAutomationTangents(p, it); }'. xCoordOfTick/"
                "yCoordOfLevel are member functions doing tick->x / "
                "level->y mapping.",
                "The TODO is real, line ~1687: '// TODO: Get this out of "
                "paint event' - immediately followed by scrollbar-range "
                "STATE UPDATES inside paintEvent "
                "(m_leftRightScroll->setRange(0, l); "
                "m_leftRightScroll->setPageStep(l)), confirming the "
                "brief's point that paintEvent performs non-painting work.",
            ]),
            ("LMMS: core/GUI source separation (paths corrected)", [
                "Confirmed: src/core/AutomationNode.cpp and "
                "src/core/AutomationClip.cpp (model) vs "
                "src/gui/editors/AutomationEditor.cpp (editor/GUI) exist "
                "on current master.",
                "CORRECTION (independent verification): the brief's "
                "'AutomationPattern' naming is stale - the class was "
                "renamed to AutomationClip (the old file header in "
                "AutomationNode.cpp even reads 'AutomationClip.cpp - "
                "Implementation of class AutomationNode'). The core-vs-"
                "GUI split itself is confirmed with the corrected names.",
            ]),
            ("Verification summary (independent read-only subagent, "
             "2026-10-05)", [
                "CONFIRMED with verbatim quotes: Automation Clips as "
                "internal controllers; all 11 FL curve types with their "
                "manual descriptions; tension-handle behavior "
                "(left-drag, Ctrl fine-tune, right-click reset); Step-"
                "mode freehand drawing (+Shift pulse lines); the full LFO "
                "layer (Speed/Tension/Shape Skew/Pulse Width/Level/Level "
                "polarity/Multiply-vs-Add with the unipolar/bipolar "
                "guidance); Event Automation vs Automation Clips; the "
                "controller mapping formulas page; the AutomationNode "
                "member layout; EditTangents locking semantics; the "
                "QPainterPath fill-based curve rendering; the paint-event "
                "TODO; the core/GUI split.",
                "CORRECTED: progression-name glosses were paraphrase, "
                "not UI strings; tension 'smoother but may overshoot' "
                "doc wording is unverified (range 0->1 confirmed); "
                "AutomationPattern paths are stale (now AutomationClip); "
                "the 'shared editor across Edison/Sytrus/Harmor' framing "
                "softened to a common documented editing language "
                "(Fruity Envelope Controller manual is the primary "
                "source).",
                "Nothing in the brief's load-bearing claims failed "
                "outright; the four corrections are wording/attribution "
                "precision, not substance reversals.",
            ]),
        ],
        [
            "Pulsegrid takeaway: v0.34.0 implements the brief's core "
            "separation (section 52: 'Here are values. Draw them.') - "
            "Curve evaluator -> Curve geometry/samples -> Renderer. Rust "
            "engine/src/timeline.rs: InterpMode enum "
            "(Linear/Smooth/Hold/Stairs/Pulse/Wave), AutoCurve gains "
            "interp + tension fields, value_at() delegates to a segment "
            "evaluator. Smooth = cubic Hermite with Catmull-Rom-style "
            "neighbor tangents scaled by tension; Stairs = 2-16 quantized "
            "steps; Pulse = 1-8 square cycles; Wave = 1-8 sine wobble "
            "around the ramp; Hold = prior value until the next point; "
            "Linear = ordinary interpolation.",
            "Pulsegrid takeaway: per-LANE interpolation (like LMMS's "
            "per-clip progression), not FL's per-segment curve type. "
            "Old projects default to linear/0.5 - no project-format bump "
            "(additive fields). Honest scope: 6 modes, not FL's 11; no "
            "per-node incoming/outgoing tangents yet; no per-segment "
            "curve types.",
            "Pulsegrid takeaway: tension semantics - Linear and Hold "
            "ignore tension (mirrors InterpMode::uses_tension). Smooth: "
            "tangent strength (0 = smoothstep). Stairs: 2-16 steps. "
            "Pulse/Wave: 1-8 cycles. Python curve_eval.py is a pure-"
            "Python mirror of the Rust evaluator; the automation painter "
            "samples it (24 samples/segment, shared endpoints) and "
            "implements zero shape math itself. Python's round() is "
            "banker's rounding vs Rust's half-away-from-zero: curve_eval "
            "uses floor(x+0.5) to match Rust exactly at .5 boundaries "
            "(found by parity tests, not by ear).",
            "Pulsegrid takeaway: Automation tab gains a Curve menu "
            "(Linear/Smooth/Hold/Stairs/Pulse/Wave) + Tension slider "
            "(0-100); tension widget disables for Linear/Hold; commit "
            "callback is now on_commit(track_id, param, points, interp, "
            "tension) - one gesture stays one undo entry. Clamping "
            "safety review: smooth/wave can overshoot the endpoint range "
            "(documented property, consistent with the tension model's "
            "nature) - safe at every application point: native FX params "
            "are range-checked in Effect::set_param (out-of-range "
            "automated values are rejected, param keeps last value); pan "
            "is angle-based trig (bounded); gain is a plain multiplier "
            "bounded by the tanh master; plugin params only require "
            "finite values.",
            "Pulsegrid takeaway: open items carried forward - per-node "
            "incoming/outgoing tangents with locking (LMMS "
            "EditTangents); FL's remaining 5 curve types (double/alt "
            "curves, smooth stairs, half sine); an LFO layer "
            "(Speed/Tension/Skew/Width/Level, Multiply-vs-Add semantics) "
            "as a lane-level modulation source; step-mode freehand "
            "drawing; controller mapping formulas. The evaluator/bridge "
            "contract (interp string + tension float per lane) already "
            "supports adding modes without a format bump.",
        ],
    ),
    (
        "CLAP State Context, Preset Load, Dirty Tracking",
        "Research topic 31 (brief: CLAP host-integration/state-management, "
        "studied 2026-10-05; independently verified with verbatim primary-"
        "source quotes the same day). Studied as a host-integration problem "
        "rather than 'does FL/LMMS support CLAP': state-context, preset-"
        "load, and dirty tracking are three related but separate mechanisms. "
        "CLAP defines the contracts between plugin and host; the DAW decides "
        "how those contracts map onto its project, preset browser, undo "
        "system, and modified/needs-save state.",
        [
            ("CLAP version and stabilization", [
                "Current CLAP is 1.2.10 (free-audio/clap include/clap/"
                "version.h: CLAP_VERSION_MAJOR 1, MINOR 2, REVISION 10).",
                "CLAP_EXT_STATE_CONTEXT and CLAP_EXT_PRESET_LOAD are stable "
                "extensions, stabilized in CLAP 1.2.0 (ChangeLog.md '# "
                "Changes in 1.2.0' -> '## Stabilize extensions' lists both "
                "verbatim) - not experimental/draft concepts.",
                "Nuance: preset-load.h and the discovery factory still "
                "carry draft compat IDs ('The latest draft is 100% "
                "compatible. This compat ID may be removed in 2026') - "
                "transition shims for the 1.2.0 stabilization break, per "
                "the changelog.",
                "Extension IDs: 'clap.state-context/2', 'clap.preset-load/2'.",
            ]),
            ("The state extension and mark_dirty()", [
                "CLAP_EXT_STATE provides opaque stream-based save()/load() "
                "for both parameter and non-parameter plugin state; it is "
                "the foundation for project persistence, duplication, and "
                "host-side preset management (state.h).",
                "Host callback clap_host_state.mark_dirty(): 'Tell the host "
                "that the plugin state has changed and should be saved "
                "again.'",
                "Implicit-dirty rule (state.h): 'If a parameter value "
                "changes, then it is implicit that the state is dirty.' The "
                "plugin does NOT call mark_dirty() for ordinary parameter "
                "changes.",
                "mark_dirty() is for OTHER state: internal sample "
                "assignments, custom mappings, non-parameter configuration, "
                "internal routing, plugin-specific data, state altered "
                "through a plugin GUI not represented by a CLAP parameter.",
                "CLAP explicitly discourages host-side parameter-state "
                "hacks (params.h, 'Persisting parameter values'): 'Plugins "
                "are responsible for persisting their parameter\\'s values "
                "between sessions by implementing the state extension. "
                "Otherwise parameter value will not be recalled when "
                "reloading a project. Hosts should _not_ try to save and "
                "restore parameter values for plugins that don\\'t implement "
                "the state extension.' And 'Advice for the host': 'do not "
                "implement a parameter saving fall back for plugins that "
                "don\\'t implement the state extension.'",
                "Intended architecture: plugin (params + other state) -> "
                "CLAP State -> host -> project file. NOT: host saves each "
                "param and guesses internals.",
            ]),
            ("State context: 'why am I saving this?'", [
                "Ordinary state says 'save my current state.' State context "
                "says 'save my state differently depending on why you are "
                "saving it.'",
                "Three stable contexts (state-context.h): "
                "CLAP_STATE_CONTEXT_FOR_PRESET = 1 ('suitable for storing "
                "and loading a state as a preset'), "
                "CLAP_STATE_CONTEXT_FOR_DUPLICATE = 2 ('suitable for "
                "duplicating a plugin instance'), "
                "CLAP_STATE_CONTEXT_FOR_PROJECT = 3 ('suitable for storing "
                "and loading a state within a project/song').",
                "Motivation: a plugin may have state that behaves "
                "differently for reusable presets vs duplicates vs project "
                "reload - e.g. connections to external hardware or limited "
                "resources that a duplicate should not inherit verbatim.",
                "Mandatory pairing: 'If the plugin implements "
                "CLAP_EXT_STATE_CONTEXT then it is mandatory to also "
                "implement CLAP_EXT_STATE.'",
                "Load-compatibility: 'the result may be loaded by both "
                "clap_plugin_state.load() and "
                "clap_plugin_state_context.load'; 'the state may have been "
                "saved by clap_plugin_state.save() or "
                "clap_plugin_state_context.save() with a different "
                "context_type'; the three save/load paths 'should be "
                "equivalent.'",
                "Threading: both save and load carry [main-thread] "
                "annotations.",
                "Background support: specifically CLAP 1.2.9 ('# Changes in "
                "1.2.9' -> '## Background operations': "
                "'background-state-context.h: load and save the state from a "
                "background thread') - relevant for large sampler states.",
            ]),
            ("Preset load is NOT state context", [
                "CLAP_EXT_PRESET_LOAD: the host asks the plugin to load one "
                "of its NATIVE preset files: plugin->from_location("
                "location_kind, location, load_key), [main-thread] "
                "(preset-load.h: 'Loads a preset in the plugin native preset "
                "file format from a location. The preset discovery provider "
                "defines the location and load_key to be passed to this "
                "function.').",
                "State context: host <-> save/load opaque state. Preset "
                "load: host -> 'load this native preset at this location' "
                "-> plugin's own parser.",
                "load_key exists for container files (preset-discovery.h): "
                "'If the preset file is a preset container then name and "
                "load_key are mandatory... The load_key is a machine "
                "friendly string used to load the preset inside the "
                "container via the preset-load plug-in extension... it could "
                "also be some other unique id like a database primary key "
                "or a binary offset. It\\'s use is entirely up to the "
                "plug-in.'",
                "Browser sync: clap_host_preset_load_t::loaded 'Informs the "
                "host that the following preset has been loaded. This "
                "contributes to keep in sync the host preset browser and "
                "plugin preset browser.' Substantially more sophisticated "
                "than 'DAW opens file -> plugin gets arbitrary blob.'",
                "Preset-load rescan semantics (params.h, 'Scenarios: I. "
                "Loading a preset'): load into a temporary state; call "
                "clap_host_params.rescan() if values changed; "
                "CLAP_PARAM_RESCAN_VALUES: 'The host will not record those "
                "changes as automation points. New values takes effect "
                "immediately.' Also call clap_host_latency.changed() if "
                "latency changed; invalidate other cached info.",
            ]),
            ("Preset discovery vs preset load", [
                "CLAP deliberately separates finding presets from loading "
                "them. Discovery (a stabilized FACTORY: "
                "CLAP_PRESET_DISCOVERY_FACTORY_ID, "
                "'clap.preset-discovery-factory/2', in include/clap/factory/ "
                "- not an ext/ extension) identifies preset file types, "
                "locations, extracts metadata, provides names/features/"
                "tags, watches files for invalidation, builds an index.",
                "'VERY IMPORTANT: - the whole indexing process has to be "
                "**fast** - ... must not be interactive - don\\'t show "
                "dialogs, windows, ... - don\\'t ask for user input.'",
                "Then: preset database -> location + load_key -> preset-load "
                "-> plugin native loader. Discovery hands off to load "
                "('Then to load a preset, use ext/draft/preset-load.h').",
            ]),
            ("Dirty != automation (three distinct concepts)", [
                "A parameter can change because of automation, MIDI, preset "
                "load, GUI, or plugin internal modulation - those do not all "
                "mean the same thing for persistence or automation "
                "recording.",
                "params.h: 'When a MIDI CC changes a parameter\\'s value, "
                "set the flag CLAP_EVENT_DONT_RECORD in "
                "clap_event_param.header.flags. That way the host may record "
                "the MIDI CC automation, but not the parameter change and "
                "there won\\'t be conflict at playback.'",
                "Combined with the implicit-dirty rule and "
                "CLAP_PARAM_RESCAN_VALUES ('The host will not record those "
                "changes as automation points'), the spec keeps "
                "parameter-value synchronization, automation recording, and "
                "dirty/state persistence as three distinct concepts: "
                "PARAMETER CHANGED != AUTOMATION POINT CREATED != PROJECT "
                "DIRTY.",
            ]),
            ("FL Studio: what is actually established", [
                "FL Studio supports CLAP since 2024.1 (Image-Line manual "
                "basics_externalplugins.htm: 'Support for CLAP was "
                "**introduced with FL Studio 2024.1**'; forum RC1 thread "
                "t=325978: 'CLAP Plugin Support - Introducing CLever Audio "
                "Plugin (CLAP) plugin support.').",
                "CORRECTION (verified 2026-10-05): 'Implemented the CLAP "
                "project location extension' is in the **25.2.2** "
                "Maintenance Update 2 (2025/12/15) changelog, NOT 25.2.4 as "
                "the brief stated.",
                "2026 changelog breadth UNVERIFIED: the only other "
                "CLAP-specific 2026 line found is 25.2.4 (2026/02/05) "
                "'21599 Wrapper: the CLAP version of Vital crashes.' The "
                "brief\\'s longer list (init/deinit, param automation, "
                "scanning, song-position/tempo, preset info) could not be "
                "verified from public Image-Line pages - do not present as "
                "established.",
                "Fruity Wrapper is the plugin layer (manual plugins/"
                "wrapper.htm): 'The **Wrapper** is a software interface/"
                "layer between instrument / effect plugins and FL Studio.' "
                "The same page confirms CLAP is hosted through the Wrapper "
                "path ('see **3rd Party Plugin** for VST, AU and CLAP "
                "plugins').",
                "FL\\'s mature preset/state architecture around plugins: "
                "'**Save channel state as...** - Saves the entire Channel "
                "state as a single preset in the Browser, **including VST "
                "wrapper settings, Sample settings and Miscellaneous "
                "functions**.' So CLAP state enters an already-existing "
                "FL project/preset/state ecosystem: CLAP state -> Fruity "
                "Wrapper (plugin state + wrapper state + FL-specific state) "
                "-> FL project/preset. Exact serialization is proprietary.",
                "FL\\'s preset system is NOT equivalent to CLAP Preset Load: "
                "FL has Browser -> plugin preset -> Wrapper -> plugin; CLAP "
                "provides preset discovery -> location + load_key -> "
                "preset-load -> plugin native loader -> host.loaded(). CLAP "
                "lets FL integrate a plugin\\'s native preset format more "
                "intelligently instead of forcing every preset into an "
                "FL-specific format.",
                "Dirty tracking mapping (CLAP mark_dirty() -> FL internal "
                "dirty flag): NOT publicly documented. Documented/strongly "
                "supported: FL hosts CLAP, has project/plugin persistence, "
                "has wrapper/preset systems, CLAP defines mark_dirty(), the "
                "CLAP Wrapper is actively developed. Unknown/proprietary: "
                "exact internal dirty-state object, 1:1 mapping, "
                "coalescing, undo participation, FLP serialization of CLAP "
                "state, internal use of the three state contexts. Do not "
                "invent class names like ClapStateContextManager as if "
                "they were real FL internals.",
                "What must NOT be claimed: that FL stores CLAP state as "
                "JSON/Base64, or exact context usage - CLAP defines the "
                "state-transfer protocol; the host determines how the "
                "opaque state is persisted, and that part is "
                "proprietary/host-specific.",
            ]),
            ("LMMS: the New Plugin API proposal", [
                "Current LMMS has no established native CLAP host with "
                "these features - the interesting evidence is the 2026 New "
                "Plugin API proposal (GitHub issue lmms/lmms#8275), which is "
                "unusually explicit.",
                "Motivations verbatim: 'To implement CLAP support without "
                "breaking the CLAP API\\'s threading rules' and 'To "
                "implement CLAP and VST3 support without the need for "
                "compromises or hacks, following established conventions "
                "for how all other major audio plugin APIs work.'",
                "CLAP-inspired design: 'I favor a design that takes heavy "
                "inspiration from CLAP - basically a C++ version of parts "
                "of the CLAP API'; 'Maintain a clear separation of plugin "
                "and host responsibilities'; 'Introduce plugin states "
                "(active/inactive, processing/not-processing, etc.)'; "
                "'Specify which actions are allowed during which states'; "
                "'Specify which methods are allowed to be called on which "
                "threads'; 'Verify correct state transitions and compliance "
                "with threading rules'; 'Plugins will follow the states and "
                "state transitions CLAP uses.'",
                "Preset database / plugin manager: 'Coordinate design with "
                "a future plugin manager'; 'Coordinate design with a future "
                "preset database'; 'Eventually should also provide a preset "
                "discovery mechanism which can also run in a background "
                "thread.' - the ideal pipeline (preset database -> "
                "discovery -> location + load_key -> preset-load) rather "
                "than the older file-dialog approach.",
                "Honest representation: LMMS today = no established native "
                "CLAP State Context / Preset Load / mark_dirty host "
                "implementation. LMMS proposed API = explicit plugin "
                "states, state transitions, thread contracts, host/plugin "
                "separation, CLAP-inspired architecture - exactly the "
                "prerequisites for implementing CLAP state/dirty semantics "
                "cleanly.",
                "The crucial difference vs FL: FL = existing DAW with "
                "mature proprietary infrastructure, CLAP added through the "
                "Wrapper and integrated into it. LMMS = historical plugin "
                "architecture, redesigning the plugin API around "
                "CLAP-inspired contracts (states, threading, preset "
                "database) as the path to future CLAP/VST3/etc.",
            ]),
            ("Pulsegrid implementation (v0.35.0)", [
                "Engine (engine/src/plugins.rs): PulsegridHost now declares "
                "host extensions via HostHandlers::declare_extensions - "
                "HostState (mark_dirty) and a raw-FFI HostPresetLoad "
                "(clap-sys types; clack 0.2 ships no safe wrapper for this "
                "stable extension).",
                "PulsegridMainThread replaces the old () main-thread "
                "handler: per-instance slot + shared PluginEventSinks. "
                "mark_dirty() records the slot (deduplicated); the control "
                "thread drains it via Engine::take_plugin_dirty_slots().",
                "PluginInstanceSink changed from a Vec alias to a struct "
                "carrying entries + shared event sinks (cloned from the "
                "Engine-owned PluginEventSinks into every graph build, so "
                "instances from any build report into the same place). "
                "Audio-thread rebuilds use throwaway sinks (their instances "
                "are dropped immediately - documented).",
                "State context: save_plugin_state_ctx() prefers "
                "PluginStateContext with the requested context, falls back "
                "to ordinary state save (spec: context plugins MUST also "
                "implement ordinary state; the paths are load-compatible). "
                "Project save AND project load now use FOR_PROJECT; preset "
                "files use FOR_PRESET.",
                "Preset load: PluginPresetLoad::from_location() (raw FFI, "
                "file location kind 0, optional load_key for containers). "
                "Host records the plugin\\'s loaded()/on_error() callbacks "
                "into the preset sinks; Python drains via "
                "take_preset_events() for browser-sync status messages.",
                "Engine APIs (PyO3): take_plugin_dirty_slots(), "
                "take_preset_events(), save_plugin_preset_blob() "
                "(FOR_PRESET -> Base64), load_plugin_preset_blob() "
                "(FOR_PRESET load + param rescan -> params + blob), "
                "plugin_supports_preset_load(), "
                "plugin_preset_from_location(). Rescanned values are NOT "
                "automation (per CLAP_PARAM_RESCAN_VALUES) - stored in one "
                "undoable edit.",
                "Python: engine_bridge wrappers; app.py polls the drains "
                "every UI frame in _poll_position() - dirty slots mark the "
                "project dirty ('*'), preset events show status messages; "
                "PluginParamDialog gains Save.../Load.../Load native... "
                "preset buttons (wired for both FX inserts and generator "
                "layers); preset loads are single undoable edits that also "
                "refresh the open dialog\\'s sliders.",
                "Test plugin (test-plugin/): implements PluginStateContext "
                "(appends the context u32 to the blob - 24 bytes - so tests "
                "verify the host used the right context; ordinary save "
                "stays 20 bytes); implements from_location (native format "
                "'gain=<float>') calling host loaded() + mark_dirty() on "
                "success, on_error() on failure. Fixture rebuilt: "
                "tests/fixtures/clap/PulsegridTestGain.clap.",
                "No project format bump (additive behavior only); no new "
                "Linux binary built.",
            ]),
            ("Test lessons and honest scope", [
                "Rust 112/112 (5 new): context-tag assertions (1/2/3), "
                "plain<->context load compatibility, direct HostStateImpl "
                "drive (deduplication, slot-less instances report nowhere), "
                "full ABI E2E (from_location -> gain 1.5 applied, loaded() "
                "event with slot+location, mark_dirty recorded), bad-file "
                "error path (on_error called, message forwarded).",
                "Python 207/207 under Xvfb (8 new in "
                "tests/test_clap_state_context.py): preset-load support "
                "probe, empty drains, FOR_PRESET tag check, blob roundtrip, "
                "bad-blob rejection, native E2E incl. one-shot drain "
                "semantics, bad native file, slot_key arity validation. "
                "Xvfb smoke ALL PASS; Windows MSVC cross-check clean "
                "(runtime unverified, as always).",
                "Surgery lesson: moving Rust test fns between modules with "
                "regex scripts is fragile - one bad anchor dropped a test "
                "block inside fn host_info(). Fixed by extracting to a temp "
                "file and re-inserting via brace-depth counting. Prefer "
                "anchor-free structural edits for module moves.",
                "Stale .so lesson: maturin develop rebuilt the wheel but "
                "the in-tree python/daw/_daw_engine_rs .so stayed old - "
                "copied engine/target/debug/libdaw_engine_rs.so over it "
                "before running Python tests.",
                "Honest scope: preset DISCOVERY (the factory/indexing side) "
                "is not implemented - native preset picking is a file "
                "dialog; FOR_DUPLICATE context has engine plumbing but no "
                "UI trigger yet (no track/effect duplication feature); "
                "background state-context (1.2.9) not used - all state ops "
                "stay main-thread per the stable contract; plugin GUI "
                "instances are transient (slot None) so their mark_dirty "
                "reports are dropped - param tweaks there already sync via "
                "the 150ms poll.",
            ]),
        ],
        [
            "CLAP State Context is the 'why am I saving/loading this "
            "state?' layer. CLAP Preset Load is the 'load this plugin\\'s "
            "native preset' layer. CLAP dirty tracking is the 'the "
            "plugin\\'s persistent state has changed; save me again' "
            "layer. FL Studio already has the mature proprietary host "
            "infrastructure these concepts integrate into (Wrapper + "
            "Browser + channel-state presets; exact internals "
            "proprietary). LMMS has no finished equivalent, but its 2026 "
            "New Plugin API proposal (#8275) is explicitly designed around "
            "CLAP-inspired state machines, threading contracts, and a "
            "future preset database - the prerequisites for doing this "
            "cleanly.",
            "Pulsegrid takeaway (v0.35.0): the host now speaks all three "
            "mechanisms - state-context save/load (FOR_PROJECT for "
            "projects, FOR_PRESET for preset files), native preset loading "
            "via from_location() with loaded()/on_error() browser sync, "
            "and mark_dirty() -> project dirty flag for non-parameter "
            "state. The plugin defines WHAT the blob contains, CLAP "
            "defines HOW it crosses the boundary, Pulsegrid defines HOW "
            "it persists (Base64 in JSON projects, .pgpreset files) - "
            "exactly the separation the spec intends.",
            "Open items: preset discovery/indexing (factory side); "
            "FOR_DUPLICATE UI trigger; background state-context (1.2.9); "
            "GUI-instance dirty reporting; Windows 11 runtime verification.",
        ],
    ),

    (
        "CLAP Runtime Latency Changes",
        "Research topic 32 (brief: CLAP plugin-latency-change problem, "
        "studied 2026-10-05; claims checked against free-audio/clap "
        "headers + ChangeLog and Image-Line manuals the same day). "
        "Studied as a runtime/plugin-latency-change problem: how CLAP "
        "reports latency, when a host may query it, how a latency change "
        "propagates into the DAW's processing graph/PDC, and what is "
        "established for FL Studio vs LMMS. The central distinction: "
        "CLAP does not poll plugins for latency; the plugin reports that "
        "its latency changed, the host restarts the instance, re-queries "
        "at the right lifecycle point, and updates its PDC state.",
        [
            ("The CLAP latency contract (verbatim header)", [
                "Dedicated clap.latency extension (MIT-licensed header "
                "include/clap/ext/latency.h, verified 2026-10-05): plugin "
                "side `uint32_t get(const clap_plugin_t *plugin)` - "
                "'Returns the plugin latency in samples', annotated "
                "'[main-thread & (being-activated | active)]'. Host side "
                "`void changed(const clap_host_t *host)` - 'Tell the host "
                "that the latency changed.'",
                "Lifecycle restriction, verbatim: 'The latency is only "
                "allowed to change during plugin->activate.' / 'If the "
                "plugin is activated, call host->request_restart()'. The "
                "changed() callback itself is '[main-thread & "
                "being-activated]'.",
                "Consequence: latency is NOT an arbitrary runtime value. "
                "The plugin never mutates it mid-process(); a structural "
                "change (oversampling on/off, lookahead on/off, FFT size) "
                "goes through request_restart() -> host deactivates / "
                "reactivates -> host calls get() during activation -> "
                "host updates PDC. Polling get() on the audio thread "
                "would be unsafe and is not the design.",
            ]),
            ("request_restart() vs latency.changed()", [
                "Two different messages. request_restart(): 'Host, stop/"
                "restart me so I can apply a structural change' (core "
                "host callback, thread-safe). latency.changed(): 'The "
                "latency value changed; refresh your latency information' "
                "(extension callback, main-thread).",
                "After the restart the host obtains the latency via "
                "plugin_latency.get() during activation - changed() does "
                "not itself mean 'apply a new latency immediately'.",
                "params.h documents the structural-parameter rule "
                "verbatim: 'If this parameter affects the internal "
                "processing structure of the plugin, ie: max delay, fft "
                "size, ... and the plugins needs to re-allocate its "
                "working buffers, then it should call "
                "host->request_restart(), and perform the change once the "
                "plugin is re-activated.'",
            ]),
            ("Preset loading and latency (verbatim params.h scenario)", [
                "The params.h 'Scenarios: I. Loading a preset' sequence, "
                "verbatim: 'load the preset in a temporary state' -> "
                "'call clap_host_params.rescan() if anything changed' -> "
                "'call clap_host_latency.changed() if latency changed' -> "
                "'invalidate any other info that may be cached by the "
                "host' -> 'if the plugin is activated and the preset will "
                "introduce breaking changes (latency, audio ports, new "
                "parameters, ...) be sure to wait for the host to "
                "deactivate the plugin to apply those changes.'",
                "A preset can therefore change processing topology, not "
                "just values - preset loading is not merely a state-file "
                "operation. This is why v0.35.0's preset-load path and "
                "this topic's restart path are related but separate.",
            ]),
            ("Latency is samples, and query timing matters (1.2.2)", [
                "uint32_t sample count, not a time value: 480 samples is "
                "10 ms at 48 kHz and 5 ms at 96 kHz. The host needs the "
                "count; it may change with the sample rate.",
                "ChangeLog 1.2.2 (verbatim): 'latency.h: adjust latency "
                "extension requirements' and 'Require the plugin to be "
                "activated to get the latency and clarify that the "
                "latency can only be fetched when the plugin is "
                "activated'. Querying before activation is a real "
                "bug class - the plugin may not know the sample rate yet "
                "(a clap-wrapper compatibility issue was fixed by "
                "'check active state before querying latency').",
                "Current CLAP is 1.2.10 (version.h, verified 2026-10-05).",
            ]),
            ("Latency becomes a graph property (PDC)", [
                "The plugin reports a number; the DAW turns it into "
                "scheduling. A PDC-capable host aligns every path to the "
                "slowest: track A (0 samples) gets a 1024-sample delay "
                "line so it meets track B (1024-sample plugin) at the "
                "master. CLAP does not prescribe the host's PDC "
                "algorithm - it only defines how the number crosses the "
                "boundary.",
                "A runtime latency change is therefore a graph "
                "invalidation event: plugin reports -> restart -> "
                "re-activate -> re-query -> latency cache -> invalidate "
                "PDC -> recalculate paths -> update delay lines -> resume.",
            ]),
            ("FL Studio: mature PDC + Wrapper latency", [
                "FL's Automatic PDC (Image-Line mixer manuals, verified "
                "2026-10-05): 'Automatically applies PDC and updates the "
                "PDC settings when changes are detected'; 'APDC also "
                "applies to inter-track routing, including multi "
                "input/output plugins, and sidechains'; manual and "
                "automatic PDC can be combined (manual acts as offset).",
                "The Wrapper exposes detected plugin latency (hovering "
                "the PDC icon shows it in the hint bar) and Wrapper > "
                "Settings > Latency offers a per-plugin manual offset "
                "for plugins that report incorrectly. Track-delay colors: "
                "grey = none, orange = automatic PDC, blue = manual.",
                "The Wrapper Settings > Info > Latency readout shows "
                "the total (manual + plugin) with literal labels 'Manual' "
                "(manual only, plugin reports nothing), 'Plugin' "
                "(auto-detected, no manual value), 'Manual + Plugin' "
                "(both) - 'Automatic PDC' is the separate mixer-wide "
                "feature. Naming refinement vs the brief's gloss.",
                "What is NOT public: the internal bridge between CLAP "
                "latency callbacks and FL's proprietary PDC "
                "implementation. Label it as proprietary; do not invent "
                "class names.",
                "CORRECTION (independent verification 2026-10-05): the "
                "brief's FL 26.1 beta 8 line IS confirmed in the "
                "official WhatsNew changelog - '21887 Wrapper: "
                "interfaceless CLAP plugins don't update their controls "
                "when parameters are automated' (26.1 beta 8, "
                "2026/05/27); beta 10 adds '21978 Wrapper: can't record "
                "automation when changing control values in "
                "interfaceless CLAP plugins'. FL's CLAP host integration "
                "is actively handling the runtime sync layer.",
                "Real-world wrapper evidence (confirmed): clap-wrapper "
                "v0.8.0 - 'Check active state before querying latency. "
                "Fixes #229' (PR #230: the VST3 wrapper forwarded the "
                "host's latency request without checking active state).",
            ]),
            ("LMMS: routing without generalized PDC", [
                "LMMS has AudioEngine/Mixer routing with MixerChannel "
                "sends/receives and dependency scheduling - that answers "
                "'which channel depends on which', not 'how many samples "
                "of latency exist along every path'. A full PDC layer "
                "needs the second question answered, then compensating "
                "delays per shorter path.",
                "No finished native CLAP latency-change -> restart -> "
                "latency-query -> generalized PDC pipeline exists in "
                "current LMMS: Mixer.cpp greps clean for latency|PDC|"
                "delay_compens|pdc - zero matches. The 2026 New Plugin "
                "API proposal (#8275, verified for topic 31) explicitly "
                "targets CLAP threading/lifecycle support 'without "
                "compromises or hacks' - but the issue text never "
                "mentions latency at all, so a latency pipeline there is "
                "an architectural implication, not a documented plan.",
            ]),
            ("What Pulsegrid implemented (v0.36.0)", [
                "Host declares CLAP_EXT_LATENCY (HostLatency) alongside "
                "the state/preset-load extensions. PulsegridMainThread "
                "implements changed() -> records the slot; "
                "PulsegridShared implements the thread-safe core "
                "request_restart() -> records the slot (shared handler "
                "now carries the slot, like the main-thread handler).",
                "Lifecycle fix: latency is now queried AFTER activate() "
                "(was: before - a CLAP 1.2.2+ violation, the 'query "
                "before activation' bug class).",
                "Initial params are flushed into the main-thread instance "
                "pre-activation (params.flush), so a fresh load with a "
                "structural param set never spuriously requests a "
                "restart - only genuine runtime changes do.",
                "Engine::process_plugin_restarts() (control thread, "
                "polled every UI frame): drains both queues, preserves "
                "each slot's state with the FOR_DUPLICATE context "
                "(first real consumer of v0.35.0's duplicate plumbing), "
                "stashes the blob in the arrangement, rebuilds the "
                "graph - fresh instances re-query latency "
                "post-activation and Graph::new recalculates PDC. "
                "PyO3: take_restart_requests, process_plugin_restarts, "
                "plugin_latency_samples.",
                "UI: the frame poll services restarts automatically "
                "(status message); the plugin dialog shows 'Latency: N "
                "samples - compensated by PDC' (FL Wrapper parity).",
                "Test plugin: new Lookahead parameter (id 8, stepped "
                "0/1); latency 0/480 samples; audio thread calls "
                "host->request_restart() on a genuine change; state blob "
                "grew 20->21 bytes (+lookahead), context blob 24->25. "
                "Fixture rebuilt.",
            ]),
            ("Verification", [
                "Rust 115/115 (3 new: changed()/request_restart() slot "
                "recording incl. dedup + slot-less silence; full ABI "
                "round trip - queue_param(8,1) -> process_block -> "
                "request_restart recorded, cached latency stays 0 until "
                "restart).",
                "Python 8 new in tests/test_clap_latency.py, all passing: "
                "empty queues; initial latency 0; live E2E "
                "(structural change -> request_restart -> restart -> 480 "
                "samples, no re-request); notification shape; "
                "gain+lookahead preserved across restart (blob bytes); "
                "fresh load with lookahead=1 does not restart; PDC "
                "measured: non-latent track's kick onset shifts by "
                "exactly 480 samples after the restart.",
                "Full suite: 215/215 under Xvfb; smoke ALL PASS; Windows "
                "MSVC cross-check clean (runtime unverified).",
            ]),
            ("Test lessons and honest scope", [
                "A fresh load must not look like a runtime change: "
                "without the pre-activation params.flush, the first "
                "block's param application spuriously fired "
                "request_restart() on every load with lookahead=1. The "
                "flush is the architecturally correct fix (it is what "
                "params.flush exists for).",
                "The registry instance shares its TestGainShared with the "
                "live audio processor, so the FOR_DUPLICATE blob saved at "
                "restart time already contains the audio thread's "
                "lookahead flip - the restart is lossless. (Offline "
                "renders use throwaway instances and do not disturb the "
                "registry; a restart requested purely inside an offline "
                "render would save a stale blob - documented, live "
                "playback is the serviced path.)",
                "PDC measurement lesson: put the probe kick ONLY on the "
                "non-latent track - a kick on the latent track masks the "
                "shift at the master.",
                "Open items: instrument-plugin latency is not in the PDC "
                "sum (FX only, pre-existing); no manual latency offset "
                "UI (FL has one per-plugin); background restart "
                "scheduling is synchronous on the control thread; "
                "Windows 11 runtime verification.",
            ]),
        ],
        [
            "CLAP latency is a lifecycle-aware contract, not a polling "
            "mechanism: the plugin reports in samples, signals change via "
            "request_restart()/changed(), and the host re-queries after "
            "re-activation, then updates its PDC model.",
            "Pulsegrid takeaway (v0.36.0): the host now implements the "
            "full runtime chain - latency queried post-activation, "
            "request_restart()/changed() recorded per slot, restart "
            "preserves state via FOR_DUPLICATE, PDC recalculated, "
            "latency shown in the plugin dialog like FL's Wrapper.",
            "Open items: instrument-plugin latency not in the PDC sum; "
            "no manual latency offset UI; restart is synchronous on the "
            "control thread; Windows 11 runtime verification.",
        ],
    ),

    (
        "Strip-Level Presentation Objects: FL Studio vs LMMS Track Identity Architecture",
        "What a DAW considers the persistent presentation/interaction identity "
        "of a track or mixer strip, what belongs to the underlying model "
        "versus the visual object, and how FL Studio and LMMS expose that "
        "boundary. Terminology caveat (from the brief, preserved): "
        "'strip-level presentation object' is NOT a documented FL Studio or "
        "LMMS class name -- it is the brief's architectural term for the "
        "object representing visible track/strip presentation (name, color, "
        "icon, controls, selection/mute/solo state, meter/fader presentation, "
        "layout). For LMMS the actual source classes can be inspected; for "
        "FL Studio the internals are proprietary, so the architecture is "
        "reconstructed from documented behavior, never invented classes.",
        [
            (
                "The three-layer strip model",
                "A mixer/track strip separates into: STRIP PRESENTATION "
                "(name, icon, color, meter, fader, buttons, selection, mute, "
                "solo, layout, labels, indicators) over STRIP MODEL (volume, "
                "pan, routing, mute, solo, effects, automation, track "
                "identity) over AUDIO GRAPH (sources -> processing -> "
                "routing). A well-separated architecture makes the first "
                "layer a presentation object rather than having the audio "
                "model paint pixels. That distinction is especially "
                "inspectable in LMMS because the source is open.",
            ),
            (
                "FL Studio has several different kinds of 'strip' (verified: playlist docs)",
                "FL deliberately has parallel presentation identities: "
                "Channel Rack Channel, Playlist Track, Mixer Track, "
                "Instrument Track, Audio Track -- they are not all the same "
                "object. The official Playlist documentation states Playlist "
                "tracks are NOT inherently bound to Mixer tracks; Pattern "
                "Clips can route their instruments to arbitrary Mixer tracks. "
                "The architecture is not Track -> Mixer strip; it is Channel "
                "/ Instrument branching to Mixer Track and to Playlist Clip "
                "-> Playlist Track, unless the user creates Track Mode "
                "linking.",
            ),
            (
                "FL Track Mode: identity propagation across surfaces",
                "Track Mode associates an Instrument Channel, a Playlist "
                "Track and a Mixer Track into a group. The official docs say "
                "changes to the name, color, or icon of any member RIPPLE "
                "through the chain. Conceptually a shared TRACK IDENTITY "
                "(name/color/icon) projected onto three presentations "
                "(Channel, Playlist, Mixer). The exact internal mechanism is "
                "proprietary -- the safe abstraction is a Track Mode "
                "RELATIONSHIP synchronizing documented identity properties, "
                "not a claim that the three become one internal object. "
                "Documented workflow benefits: simpler project layout, less "
                "routing/naming, ripple renaming/coloring, opening "
                "Instrument Channels from Playlist headers, dropping FX onto "
                "Playlist headers -- presentation identity as a workflow "
                "affordance layer, not mere decoration.",
            ),
            (
                "FL: presentation identity vs audio identity",
                "A Playlist Track can visually read 'Bass' without being the "
                "audio-routing object called Bass -- the Mixer routing is "
                "determined by the Channels inside Pattern Clips. So "
                "Playlist presentation identity != Mixer/audio identity "
                "unless Track Mode links them. The Mixer Track itself is the "
                "audio-processing/routing strip (routing indicators, FX, "
                "PDC) with its own presentation layer: name, color, icon, "
                "selection, meter, routing indicators, record-arm, effects "
                "state, PDC/track-delay state (grey = no value, orange = "
                "automatic PDC, blue = manual -- model state projected into "
                "presentation), sends/sidechain controls.",
            ),
            (
                "FL: presentation state that is not merely decorative",
                "Track color is mostly presentation; but record arm, mute, "
                "solo, selected, PDC delay, routing state affect operation. "
                "The strip UI is presentation metadata + interactive control "
                "state + audio-state visualization, not a pure paint object. "
                "FL also exposes global Mixer presentation modes (compact/ "
                "expanded, track names, routing cables, plugin lists, "
                "inspector position, Colorful Mixer, alternative "
                "highlighting, separators) -- a PROJECT TRACK STATE -> MIXER "
                "PRESENTATION MODE -> rendered strip layering useful for "
                "renderer design. Playlist headers carry name/color/icon, "
                "Track Mode state, grouping, lock state, content lock, "
                "audio-track recording state, 'Lock to content' (CORRECTION: the brief's 'Lock to this size' is the wrong name), subtracks. "
                "Mixer groups add group-level presentation (separators, "
                "group names/icons, common color, Auto Color Group).",
            ),
            (
                "LMMS: source-transparent model/view hierarchy",
                "The source tree separates core (Track, Mixer, MixerChannel, "
                "models) from GUI (TrackView, TrackLabelButton, "
                "TrackOperationsWidget, TrackContentWidget, specialized "
                "TrackViews). TrackView IS the presentation object, and it "
                "is directly inspectable -- see the verification section.",
            ),
            (
                "LMMS TrackView: a real, source-visible presentation object",
                "class TrackView : public QWidget, public ModelView, public "
                "JournallingObject (include/TrackView.h), constructed as "
                "TrackView(Track* track, TrackContainerView* tcv). The "
                "constructor builds TrackOperationsWidget, "
                "TrackSettingsWidget and TrackContentWidget into its layout. "
                "modelChanged() binds the controls to the model: "
                "m_muteBtn->setModel(&m_track->m_mutedModel), "
                "m_soloBtn->setModel(&m_track->m_soloModel) -- mute/solo "
                "are model-bound controls, not painted indicators. The "
                "constructor connects m_mutedModel.dataChanged() to "
                "TrackContentWidget::update() and TrackView::muteChanged(): "
                "event-driven Model -> dataChanged() -> view updates, a "
                "conventional reactive UI architecture.",
            ),
            (
                "LMMS: ClipViews, minimal painting, derived icons",
                "When a Clip is added, createClipView() calls "
                "clip->createView(this) -- Clips create their OWN views "
                "(ClipView), a real model/view separation. TrackView::"
                "paintEvent() is minimal: QStyleOption + QPainter + "
                "style()->drawPrimitive(QStyle::PE_Widget) -- TrackView is "
                "layout + child-widget composition + interaction + model "
                "binding, not a giant renderer (contrast the "
                "AutomationEditor). TrackLabelButton is a separate "
                "QWidget/QToolButton presentation component (name display/ "
                "edit/elide/tooltip/icon/drag-drop); its paintEvent() "
                "DERIVES the icon for instrument tracks from the plugin "
                "descriptor logo (instrument()->key().logo() or "
                "descriptor()->logo) -- presentation computed from model "
                "relationships, not just stored metadata. Compact mode "
                "(compacttrackbuttons) changes the presentation composition "
                "in resizeEvent() without the Track model knowing the "
                "layout. Track height is written BACK to the model "
                "(m_track->setHeight(height()) in resizeToHeight()) -- the "
                "model/view boundary is not purely view-only. TrackView also "
                "owns interaction: drag/reorder (moveTrackViewUp/Down), "
                "resizing, drag/drop, rubber-band selection, tooltips. "
                "CORRECTION on height persistence: Track::setHeight()/m_"
                "height exist and resizeToHeight() writes back, but "
                "Track.h carries '@todo Save the track height' / '@todo "
                "Load the track height' -- height is model state that is "
                "NOT persisted to project files. The brief's 'persistent "
                "model state' overstates it.",
            ),
            (
                "LMMS mixer presentation: FxMixerView/FxLine",
                "The mixer side is likewise split -- Mixer/MixerChannel "
                "model (include/Mixer.h) under MixerView containing "
                "MixerChannelView strips, plus EffectRackView and "
                "EffectView (all present on master) -- not one monolithic "
                "visual object. CORRECTION: the brief's 'FxMixer, "
                "FxMixerView, FxLine' are pre-rename names and do NOT exist "
                "on master (verified by tree listing + 404s); the core/GUI "
                "split exists under the current names. FX-channel "
                "presentation functions (rename, move, remove, mute, solo, "
                "fader, sends) are exposed as UI strings.",
            ),
            (
                "The honest LMMS caveat: no pure presentation abstraction",
                "TrackView is a view/controller/widget composite (layout, "
                "event handling, model binding, view creation, some "
                "painting, state sync), NOT a clean retained-mode "
                "presentation object. The brief's proposed extraction -- "
                "MODEL -> PRESENTATION STATE -> GEOMETRY -> RENDERER <-> "
                "INTERACTION CONTROLLER, splitting TrackView into "
                "TrackPresentationModel / TrackGeometry / TrackRenderer / "
                "TrackInteractionController (and MixerStripPresentation for "
                "MixerChannel with fader/meter geometry, FX slot layout, "
                "routing/PDC indicators) -- is a proposal for LMMS's future, "
                "not its present. FL's side is behavioral architecture "
                "only; no private classes asserted.",
            ),
            (
                "Strongest contrast (the brief's one-sentence reduction)",
                "LMMS exposes an actual widget-level model/view hierarchy "
                "for track presentation; FL Studio exposes a higher-level "
                "behavioral identity system linking Channel, Playlist "
                "Track, and Mixer Track presentations, but its underlying "
                "presentation-object implementation is proprietary. "
                "Secondary contrast: FL presentation identity can SPAN "
                "several underlying objects (Track Mode); LMMS presentation "
                "attaches to a single Track model and its view hierarchy. "
                "FL's Playlist != Mixer by default; LMMS's Song Editor "
                "presentation maps more directly onto its Track model.",
            ),
            (
                "Independent verification (2026-10-05, primary sources)",
                "LMMS master, direct source reads: TrackView.h -- 'class "
                "TrackView : public QWidget, public ModelView, public "
                "JournallingObject' (three bases, not two), ctor (Track*, "
                "TrackContainerView*), m_track/m_trackContainerView "
                "members, the three child widgets, muteChanged() slot, "
                "createClipView() slot. src/gui/tracks/TrackView.cpp -- "
                "modelChanged() lines 170-171: mute/solo setModel(&m_track->"
                "m_mutedModel / m_soloModel); ctor lines 88-94: "
                "dataChanged() connected to content update + muteChanged(); "
                "line 337: style()->drawPrimitive(QStyle::PE_Widget) (the "
                "entire paintEvent); line 345: clip->createView(this); line "
                "385: m_track->setHeight(height()); line 277: "
                "moveTrackViewUp. TrackLabelButton.cpp lines 201-214: "
                "instrument logo from instrument()->key().logo() / "
                "descriptor()->logo before QToolButton::paintEvent; "
                "rename()/elideName()/tooltips confirmed (L103-156, "
                "L208-262). ALL CONFIRMED verbatim. FL manuals, official "
                "Image-Line pages, all CONFIRMED verbatim: playlist.htm -- "
                "'Playlist Tracks are not bound to Mixer tracks' and 'Track "
                "Mode ... changes to the name, color and or icon of any "
                "member in the group will ripple throughout the chain'; "
                "mixer.htm -- 'Track delay ... Grey (No value set), Orange "
                "(Automatically set PDC), Blue (Manual value set)' (exact "
                "match to the brief's legend); mixer_mixermenu.htm -- "
                "'Rename, color & icon... (F2)', 'Colorful mixer', "
                "'Alternative mixer highlighting', 'Lines between tracks', "
                "'Separator', 'Create group...', 'Auto color group'; "
                "playlist.htm header menu -- 'Rename/color', 'Reset ... "
                "Track Color, Name and Icon', 'Track mode icons', 'Group "
                "with above track'. CORRECTIONS applied: (1) the brief's "
                "'FxMixer/FxMixerView/FxLine' do not exist on master -- "
                "current names are Mixer/MixerView/MixerChannelView; (2) "
                "the brief's 'Lock to this size' is actually 'Lock to "
                "content'; (3) Track.h has '@todo Save/Load the track "
                "height' -- height is model state NOT persisted to project "
                "files. Standing caveat preserved: 'strip-level "
                "presentation object' is the brief's architectural term, "
                "not a documented class name.",
            ),
            (
                "Pulsegrid takeaway (v0.37.0): extracted identity + linking",
                "Two adaptations. (1) Extracted identity: new "
                "daw/track_identity.py -- TrackIdentity (name, color_idx, "
                "icon; pure data, ASCII-safe icon codes per the v0.8.1 "
                "Unicode lesson), TRACK_COLORS palette, link-group helpers. "
                "PlaylistTrack gains persistent color_idx/icon fields "
                "(name kept as a field; all 25 construction sites "
                "unchanged) plus an identity view property; the engine "
                "never receives identity fields. (2) Identity linking (FL "
                "Track Mode analog): Project.identity_groups; "
                "link_track_identities() / unlink_track_identity() / "
                "set_track_identity() with write-through ripple (copies, "
                "not shared refs -- JSON stays trivial); edits are single "
                "undoable _structural_edit operations refreshing playlist + "
                "mixer. (3) StripPresentation (ui/strip_presentation.py): "
                "Level-1 extraction -- pure presentation-state dataclass + "
                "strip_geometry(); mixer strips and playlist headers both "
                "build from it (one identity, many surfaces). UI: mixer "
                "accent bar now uses the PERSISTENT color (fixing the old "
                "positional-color scheme), icon badges on strips and "
                "headers, header right-click menu (Rename/Color/Icon/Link/ "
                "Unlink), headers refresh on identity-only edits without "
                "lane rebuilds. Format: additive, no bump (v15); old files "
                "migrate to positional default colors.",
            ),
            (
                "Test lessons",
                "23 new tests in tests/test_track_identity.py: model "
                "(link/ripple/merge/unlink/validation/serialization/"
                "migration/normalize edge cases), pure StripPresentation "
                "builder/geometry tests, Xvfb widget probes (mixer accent "
                "uses persistent color + in-place refresh; header shows "
                "chip/badge and refreshes on identity-only edit with lane "
                "canvases untouched; ripple reaches both surfaces). Two "
                "probe bugs found and fixed: Mixer() needs its 13 "
                "callbacks; header test held a stale widget reference after "
                "the header rebuild. Full suite: 115/115 Rust, 238/238 "
                "Python under Xvfb, smoke ALL PASS, Windows MSVC "
                "cross-check clean (runtime unverified).",
            ),
            (
                "Honest limits",
                "Level-1 extraction only: StripPresentation is state + "
                "geometry; tkinter widgets still own interaction (no "
                "renderer/controller split). No instrument-logo derivation "
                "(LMMS's plugin-descriptor logo read is documented as "
                "research, not adapted -- icons are user-set). No "
                "per-surface independent identities (Pulsegrid tracks stay "
                "unified like LMMS; linking is across tracks, not "
                "Channel/Playlist/Mixer). No group-level presentation "
                "objects (FL's mixer groups/separators). Track height is "
                "not persistent model state in either LMMS (has @todo "
                "Save/Load) or Pulsegrid (lanes are fixed height). FL-side "
                "manual claims CONFIRMED verbatim against the official "
                "Image-Line manuals (see verification section).",
            ),
        ],
        [
            "A strip-level presentation object is the persistent "
            "presentation/interaction identity of a track -- name, color, "
            "icon, selection, control state -- separated from the audio "
            "model and the audio graph.",
            "Pulsegrid takeaway (v0.37.0): TrackIdentity extracted as pure "
            "data, identity linking with FL-Track-Mode-style ripple across "
            "tracks, and StripPresentation as a Level-1 "
            "presentation-state/geometry extraction feeding both the "
            "playlist header and the mixer strip surfaces.",
            "Open items: full renderer/controller split; instrument-derived "
            "icons; group-level presentation; persistent track heights; "
            "Windows 11 runtime verification.",
        ],
    ),

    (
        "Per-Region Dirty Flags: Rendering/Invalidation Architecture in FL Studio vs LMMS",
        "Whether FL Studio or LMMS track which portions of a larger editor "
        "need repainting, rather than simply marking an entire editor/widget "
        "dirty. Terminology distinction (preserved from the brief): a dirty "
        "FLAG says 'something changed'; a dirty REGION says 'this geometric "
        "area needs repainting'; a SEMANTIC dirty category says 'the mixer "
        "display needs refresh'. For LMMS the Qt-based mechanism is "
        "source-visible; for FL Studio the internal invalidation system is "
        "proprietary -- behavior and public scripting flags can be "
        "established, but no private dirty-region implementation is claimed.",
        [
            (
                "The ideal pipeline and the three optimization layers",
                "MODEL CHANGE -> what changed? (track name -> header region; "
                "clip moved -> old rect + new rect; automation point -> "
                "affected curve region; meter -> meter region) -> DIRTY "
                "REGION SET -> CLIP TO VIEWPORT -> REPAINT. The key "
                "optimization: something changed -> repaint affected "
                "rectangle(s), not the entire editor. Critical distinction "
                "(the brief's most important point): PARTIAL INVALIDATION "
                "is not PARTIAL GEOMETRY RECOMPUTATION is not PARTIAL DRAW-"
                "COMMAND GENERATION -- three separate layers; a DAW may "
                "optimize one, two, or all three.",
            ),
            (
                "Qt gives LMMS a genuine geometric dirty-region system",
                "QWidget's backing store maintains dirty regions with "
                "QRegion: update(rect) schedules only that area for "
                "repainting, and the backing store merges overlapping "
                "regions before flushing. Conceptually: QWidget::update"
                "(rect) -> backing store -> QRegion dirty -> merge -> paint "
                "only the affected area. The graphics-view architecture "
                "makes the flag/geometry split explicit (item dirty flag + "
                "needsRepaint rect -> scene -> viewport dirty region), "
                "though LMMS editors mostly use plain QWidget/QPainter, "
                "not QGraphicsView. TERMINOLOGY CAUTION (independent "
                "verification): the word 'dirty' appears NOWHERE on the "
                "public QWidget docs page -- Qt's vocabulary is "
                "'update'/'schedule', never 'dirty'. The observable chain "
                "is fully documented (update(rect/region) -> scheduled "
                "paint event -> QPaintEvent::region() -> automatic "
                "clipping; multiple updates coalesce into one paintEvent), "
                "but 'dirty regions' is an informal implementation-level "
                "description, not Qt terminology.",
            ),
            (
                "LMMS Model::dataChanged() is semantic, not geometric",
                "include/Model.h verbatim: dataChanged() is 'emitted if "
                "actual data of the model (e.g. values) have changed'; "
                "propertiesChanged() is 'emitted if properties of the model "
                "(e.g. ranges) have changed' (plus dataUnchanged()). So "
                "Model signals are semantic invalidation -- they never say "
                "x=400,y=50,w=100,h=30. TrackView connects these signals to "
                "visual components (mute model -> content update + "
                "muteChanged()), which is a change-notification -> visual-"
                "update chain, not an application-level DirtyRegionManager. "
                "LMMS's architecture is signal-driven rather than region-"
                "driven: MODEL -> dataChanged() -> VIEW -> update() -> Qt -> "
                "QRegion -> repaint. No universal LMMS-level "
                "DirtyRegionManager/DirtyRect/InvalidationGraph was found.",
            ),
            (
                "Where the region opportunity lives in LMMS",
                "Song Editor: a moved clip ideally invalidates old rect + "
                "new rect instead of the whole editor. AutomationEditor "
                "illustrates the cost: its paintEvent() maps ticks->X, "
                "values->Y, samples the curve, builds a QPainterPath, fills "
                "it, draws nodes/tangents/grid -- a node change rebuilds "
                "all of that instead of invalidating the union of affected "
                "old/new segments (compare the automation curve painter "
                "extraction, topic 30). KEY NUANCE (independent "
                "verification): AutomationEditor::update() calls plain "
                "QWidget::update() -- full widget, no rect (line 216) -- "
                "wired via connect(m_clip, SIGNAL(dataChanged()), this, "
                "SLOT(update())) (line 180). So LMMS's semantic dirty FLAG "
                "drives a FULL-WIDGET repaint here, not a rect-limited "
                "one: the exact gap the brief's proposed invalidation "
                "layer would close. Metering wants meter-rectangle-only "
                "invalidation, not whole-mixer repaint. TrackView's child-"
                "widget decomposition (Operations/Settings/Content, separate "
                "ClipViews) already gives Qt natural per-widget dirty "
                "regions -- implicit per-region invalidation. The remaining "
                "gap: even with a small Qt dirty rectangle, a widget's "
                "paintEvent() can still do broad work (dirty region != "
                "partial rendering).",
            ),
            (
                "FL Studio: semantic dirty flags are public, geometry is proprietary",
                "FL's MIDI scripting API publicly exposes OnDirtyChannel "
                "flags (CE_New 'new channel is added', CE_Delete, "
                "CE_Replace, CE_Rename, CE_Select) and OnRefresh flags "
                "(HW_Dirty_Mixer_Sel, HW_Dirty_Mixer_Display 'mixer display "
                "changed', HW_Dirty_Mixer_Controls, HW_Dirty_RemoteLinks, "
                "HW_Dirty_FocusedWindow, HW_Dirty_Performance, HW_Dirty_LEDs, "
                "HW_Dirty_RemoteLinkValues, HW_Dirty_Patterns -- plus, on "
                "the same page, HW_ChannelEvent, HW_Dirty_Undo, "
                "HW_Dirty_ChannelRack, HW_Dirty_GraphEditor, which the "
                "brief's list omits) -- verified verbatim in the official "
                "MIDI scripting docs. These are SEMANTIC refresh categories "
                "('the Mixer display needs refreshing'), not geometric "
                "rectangles. The "
                "public architecture is: internal change -> semantic "
                "refresh category -> FL UI system -> proprietary repaint. "
                "FL's Playlist is behaviorally object-oriented (clips "
                "independently selected/moved/resized/sliced/duplicated; "
                "automation clips have independently editable points/ "
                "segments), which creates natural localized-invalidation "
                "opportunities -- but asserting a specific per-clip dirty-"
                "rectangle cache would exceed the public evidence. "
                "Performance Mode docs (visual indicators disableable to "
                "reduce load) prove FL treats GUI presentation as a real "
                "performance cost, without revealing the mechanism.",
            ),
            (
                "The three levels, and who has what",
                "Level 1 (boolean dirty): both have it (model/view state). "
                "Level 2 (semantic categories): FL exposes HW_Dirty_* "
                "publicly; LMMS has it implicitly through model/view "
                "connections. Level 3 (geometric regions): LMMS inherits it "
                "from Qt's backing store; FL's is proprietary/unknown. "
                "Neither has a documented universal application-level "
                "per-region dirty-flag manager. Most defensible summary: "
                "FL = publicly visible semantic dirty domains -> "
                "proprietary rendering; LMMS = model change signals -> "
                "QWidget/Qt invalidation -> QRegion repainting, with no "
                "centralized application-level dirty-region manager. The "
                "renderer-architecture opportunity both point at: an "
                "explicit MODEL -> CHANGE EVENTS -> PRESENTATION "
                "INVALIDATION -> DIRTY REGIONS -> GEOMETRY CACHE -> "
                "RENDERER layer answering 'what changed?', 'what geometry "
                "is invalid?', 'what pixels repaint?' as three separate "
                "questions.",
            ),
            (
                "Independent verification (2026-10-05, primary sources)",
                "CONFIRMED verbatim: LMMS Model.h doc comments "
                "(dataChanged/dataUnchanged/propertiesChanged); FL "
                "midi_scripting.htm flag tables (all CE_* and HW_Dirty_* "
                "values incl. HW_Dirty_Patterns 1024); Qt 6 docs for "
                "update() vs repaint() ('Calling update() several times "
                "normally results in just one paintEvent() call'); "
                "AutomationEditor.cpp paintEvent (QPainter/QPainterPath, "
                "xCoordOfTick/yCoordOfLevel, valuesAfter() sampling); "
                "playlist_performance.htm ('Turn these off if you have an "
                "older PC struggling for graphic resources ... keep "
                "processor load to a minimum'); playlist.htm clip ops "
                "(select/move/resize/chop/duplicate/Make Unique) and the "
                "Picker Panel section. PARTIALLY CONFIRMED: the Qt "
                "backing-store/QRegion mechanics -- every observable "
                "behavior is documented (update(rect), QPaintEvent::"
                "region(), coalescing, QBackingStore::beginPaint/flush "
                "with QRegion), but the word 'dirty' never appears in "
                "Qt's public docs ('update'/'schedule' only). UNVERIFIED "
                "(absence claim): no DirtyRegionManager/DirtyRect in "
                "LMMS -- include/ tree + API listing + web searches all "
                "empty, but absence can't be proven by search alone. "
                "CORRECTIONS: (1) the brief's HW_Dirty_* list is "
                "incomplete -- same page also documents HW_ChannelEvent, "
                "HW_Dirty_Undo, HW_Dirty_ChannelRack, HW_Dirty_GraphEditor; "
                "(2) AutomationEditor::update() is a plain full-widget "
                "QWidget::update() (line 216) driven by dataChanged() "
                "(line 180) -- semantic flag to full repaint, the exact "
                "gap the brief targets.",
            ),
            (
                "Pulsegrid takeaway (v0.38.0): an explicit invalidation layer",
                "New daw/ui/dirty_regions.py (pure, no tkinter): DirtyDomain "
                "-- Level-2 semantic categories, Pulsegrid's analog of FL's "
                "HW_Dirty_* flags (PLAYLIST_LANES/HEADERS, MIXER_STRIPS/"
                "METERS, AUTOMATION, SEQUENCER, PIANOROLL, TRANSPORT, "
                "BROWSER, DEBUG, MENU, STATUS); DirtyRect -- Level-3 "
                "geometric rects with union/intersects/area/pad plus "
                "Qt-backing-store-style merge_rects() coalescing; "
                "DirtyTracker -- the central log (semantic whole-domain "
                "events from the app, geometric events from widgets; capped "
                "history; feeds the Debug window). Wiring: App._structural_"
                "edit() logs a semantic event per affected panel (tuple "
                "panels skip -- the widget logs the geometric event "
                "itself); PlaylistPainter.redraw_clips() deletes/redraws "
                "only the tagged clips (grid + other clips untouched) and "
                "returns dirty rects; Playlist.refresh_clips()/delete_clips"
                "() use it -- clip move/place/edit/delete now do partial "
                "lane updates instead of full canvas.delete('all') "
                "redraws; the playlist_lane panel tuple grew to "
                "(name, track_id, tags, op). AutomationEditor._motion() "
                "computes the dragged point's old/new bounds union "
                "(padded) as the dirty rect -- the brief's section-33 "
                "pattern -- alongside the existing _refresh_envelope() "
                "partial update (grid never redrawn on drags). Debug "
                "window gains an 'Invalidation (dirty regions)' view "
                "showing recent events (domain, rect count, merged px^2, "
                "note), polled at 15 Hz. Pre-existing per-region behavior "
                "documented: playhead move_playhead() (no delete/create "
                "churn), mixer meter dirty-checking, targeted panel "
                "refreshes.",
            ),
            (
                "Test lessons",
                "16 new tests in tests/test_dirty_regions.py: DirtyRect "
                "union/intersects/area/pad/contains + inverted-rect "
                "rejection; merge_rects overlap coalescing incl. the "
                "chain-merge second pass; tracker caps/clears/semantic-vs-"
                "geometric events; Xvfb probes -- partial redraw keeps the "
                "total canvas item count identical (grid + other clips "
                "untouched), clip move shifts only the tagged clip's bbox "
                "and logs a geometric (non-whole-domain) tracker event, "
                "delete removes exactly the tagged items, unknown tags "
                "fall back to a full lane redraw, automation _point_rect() "
                "geometry is sane. Probe cleanup: removed a tautological "
                "assertion and an unused import. Full suite: 115/115 Rust, "
                "254/254 Python under Xvfb, smoke ALL PASS, Windows MSVC "
                "cross-check clean (runtime unverified).",
            ),
            (
                "Honest limits",
                "Partial INVALIDATION + partial DRAW-COMMAND generation "
                "only: pixel-level dirty-region repainting remains "
                "tkinter's job (the brief's section-31 distinction is "
                "documented, not blurred). Clip z-order after a partial "
                "move redraw follows delete+recreate order rather than "
                "model order until the next full refresh (transient, "
                "visually negligible). The automation dirty rect covers "
                "the dragged point's bounds, not the full affected curve "
                "segments' union (a documented simplification). No "
                "geometry cache yet -- draw commands are regenerated for "
                "the dirty clips, not reused. Mixer strips still refresh "
                "whole-strip on control edits (meter-only invalidation is "
                "future work). Brief claims about Performance Mode "
                "wording, Picker Panel, and AutomationEditor paint "
                "structure CONFIRMED verbatim against the official docs "
                "(see verification section).",
            ),
        ],
        [
            "Invalidation has three separable layers: WHAT changed "
            "(semantic dirty categories -- FL's public HW_Dirty_* flags), "
            "WHAT GEOMETRY is invalid (dirty rectangles -- Qt gives this "
            "to LMMS via QRegion), and WHAT PIXELS repaint (the toolkit's "
            "backing store).",
            "Pulsegrid takeaway (v0.38.0): an explicit DirtyTracker "
            "invalidation layer -- semantic domains logged on every "
            "structural edit, geometric dirty rects computed by the "
            "playlist/automation widgets for partial canvas redraws, all "
            "observable in the Debug window's new invalidation view.",
            "Open items: geometry cache for dirty regions; full affected-"
            "segment union for automation drags; meter-only mixer "
            "invalidation; Windows 11 runtime verification.",
        ],
    ),

    (
            "Retained-Mode Presentation & GPU Rendering Backend",
            "Research topic 35. The brief asked whether FL Studio and LMMS "
            "have a retained-mode presentation system and a clean, selectable "
            "GPU rendering backend -- and whether those are open items.",
            [
                (
                    "1. What 'retained' means (brief sections 1, 19-21)",
                    "Three GUI architectures: (a) immediate-mode painting -- "
                    "model -> paintEvent() -> QPainter -> draw everything again; "
                    "(b) retained-mode presentation -- model -> presentation "
                    "objects -> retained geometry/visual state -> renderer -> "
                    "GPU; (c) the hybrid a modern DAW wants -- model -> "
                    "presentation objects -> change detection -> dirty regions "
                    "-> GPU/CPU renderer. Retained mode is about retaining "
                    "scene/presentation state; GPU rendering is about where "
                    "drawing executes. Complementary, not synonymous (brief "
                    "section 20). Moving one clip should mean bounds update + "
                    "dirty(oldBounds)+dirty(newBounds), not reconstructing "
                    "every other clip (brief section 19).",
                ),
                (
                    "2. FL Studio: documented partial-repaint evidence (brief "
                    "sections 2-4; VERIFIED)",
                    "CONFIRMED from Image-Line's own macOS support page 'FL "
                    "Studio macOS - Frame rate, Graphics, Graphical and User "
                    "Interface issues': 'Only paint changed rectangles - When "
                    "this option is turned on then the user interface will only "
                    "repaint the areas of the window that has changed. When it "
                    "is OFF then the whole window will always repaint.' / 'Use "
                    "PBO - Allow FL Studio to use the PBO OpenGL extension for "
                    "faster graphical updates. Set to Auto ... If you "
                    "experience slow screen updates then experiment with "
                    "setting this to ON or OFF.' / 'Wait for sync - When this "
                    "option is turned on then FL Studio will wait for the next "
                    "vertical refresh period of your computers monitor before "
                    "repainting.' (URL: "
                    "support.image-line.com/action/knowledgebase/?ans=648). "
                    "This is direct evidence of dirty-region painting, an "
                    "OpenGL-related graphics path, and frame-sync control.",
                ),
                (
                    "3. FL Studio: Blend2D is the CURRENT documented direction "
                    "(verification correction)",
                    "CORRECTION to the brief's OpenGL framing: FL Studio 2024.1 "
                    "beta notes say '(macOS & Windows) - Blend2D is used for "
                    "the GUI on macOS in addition to Windows' and '(Windows) - "
                    "Now use Blend2D for the user interface (faster graphics "
                    "with lower CPU)' (Image-Line forum, topic 1938167). The "
                    "PBO option lives in the macOS diagnostic tool; the "
                    "current documented GUI-renderer direction is Blend2D, not "
                    "a general OpenGL backend. The brief's 'OpenGL/PBO "
                    "optimization' claim is accurate for what the diagnostic "
                    "page says, but must NOT be read as 'FL's GUI is "
                    "OpenGL-rendered'.",
                ),
                (
                    "4. FL Studio: vector GUI (brief sections 6, 22; VERIFIED)",
                    "CONFIRMED from the General Settings manual page: 'These "
                    "settings allow you to rescale (resize) FL Studio's "
                    "vectorial Graphical User Interface (GUI), including "
                    "separate options for pop-ups, menus and the cursor. GUI "
                    "up-scaling is intended for use with high resolution "
                    "displays.' Plus 'Main GUI Scaling / resizing'. (URL: "
                    "image-line.com .../html/envsettings_general.htm). Vector "
                    "geometry is fundamentally more compatible with retained "
                    "geometry than bitmap skins -- but vector rendering alone "
                    "does not prove retained mode.",
                ),
                (
                    "5. FL's public architecture stops before the renderer "
                    "(brief section 7; brief section 5 caveat UNVERIFIED)",
                    "We can safely name: project/UI state -> vector GUI -> "
                    "changed-rectangle optimization -> GPU-related graphics "
                    "path. We cannot safely name FL::SceneGraph/FL::Renderer/"
                    "FL::DirtyRegionManager -- Image-Line does not publicly "
                    "document internal classes. CAUTION (verification): the "
                    "brief's 'Image-Line also documents Windows GDI resources' "
                    "is UNVERIFIED -- five targeted attempts (support KB, "
                    "manual pages, web queries) found no official Image-Line "
                    "page on GDI consumption; closest is a third-party "
                    "CodeWeavers forum comment (FL Studio 10 era), not "
                    "Image-Line.",
                ),
                (
                    "6. FL Studio: ZGameEditor Visualizer is a SEPARATE system "
                    "(brief section 8; PARTIALLY CONFIRMED -- correction)",
                    "Confirmed: Image-Line manual says 'ZGameEditor Visualizer "
                    "is a visualization effect plugin ... based on the free "
                    "open source ZGameEditor', and zgameeditor.org says 'The "
                    "game engine use OpenGL for graphics and a real time "
                    "synthesizer for audio'. CORRECTION: the brief's "
                    "'scene-graph', 'evaluates components frame-by-frame', "
                    "'CPU components / GPU shader components' phrasing is the "
                    "BRIEF AUTHOR's wording -- zero hits in the Image-Line "
                    "manual page for scene-graph/evaluate/shader. Do not "
                    "present it as Image-Line's statement. The Visualizer's "
                    "architecture must not be assumed to be the main "
                    "Playlist/Mixer GUI's architecture (brief section 8).",
                ),
                (
                    "7. LMMS: Qt Widgets GUI stack (brief sections 10-12; "
                    "VERIFIED)",
                    "CONFIRMED verbatim from lmms/lmms master CMakeLists.txt: "
                    "'find_package(Qt${QT_VERSION_MAJOR} ${LMMS_QT_MIN_VERSION} "
                    "COMPONENTS Core Gui Widgets Xml Svg REQUIRED)'. So the "
                    "main LMMS UI is organized around QWidget/QPainter/Qt "
                    "layouts/signals-slots, not a custom DAW-wide GPU scene "
                    "graph. Qt can have accelerated rendering facilities, but "
                    "'Qt supports GPU rendering' does NOT mean 'LMMS has a "
                    "custom GPU DAW renderer' (brief section 12).",
                ),
                (
                    "8. LMMS: AutomationEditor is immediate-mode painting "
                    "(brief section 11)",
                    "Automation Model -> AutomationEditor -> paintEvent() -> "
                    "calculate coordinates, evaluate/sample curve, construct "
                    "QPainterPath, draw. Combined with topic-34's finding that "
                    "AutomationEditor::update() is a plain full-widget "
                    "QWidget::update() on dataChanged(), this is widget/painter "
                    "architecture, not an independent retained render graph.",
                ),
                (
                    "9. LMMS: the QML/OpenGL/NanoVG redesign concept (brief "
                    "sections 13-14, 18; VERIFIED)",
                    "CONFIRMED from lmms/lmms issue #1911 ('LMMS redesign "
                    "concept'), quoting the concept readme verbatim: 'Custom "
                    "nanovg (opengl 2.0) based rendering' / 'Custom constraint "
                    "based layout' / 'QML based structure/event handling'; "
                    "'QML/nanovg integration has been establed and it appears "
                    "to be relatively bug free'. This was an alternative GUI "
                    "architecture that was NOT adopted as the main LMMS GUI -- "
                    "historical evidence of architectural direction, not "
                    "current production architecture.",
                ),
                (
                    "10. LMMS: Qt6 transition, not a renderer rewrite (brief "
                    "sections 16-17; VERIFIED)",
                    "CONFIRMED from 'LMMS Progress Report: November 2025' "
                    "(discussions/8161): 'After over a year of on-and-off work "
                    "... Qt 6 support is finally here!' / 'For now, LMMS still "
                    "defaults to building with Qt 5, but the new Qt 6 support "
                    "can be opted into by configuring with -DWANT_QT6=ON.' / "
                    "'Out of all our Nightly builds, only Windows MSVC is "
                    "currently building with Qt 6'. CONFIRMED from master "
                    "CMakeLists.txt: option(WANT_QT6 ... OFF); Qt 6 requires "
                    "6.8.0 on MSVC, 6.0.0 elsewhere. Qt5->Qt6 does not turn "
                    "QWidget+QPainter into a retained GPU scene graph (brief "
                    "section 17).",
                ),
                (
                    "11. LMMS: audio renderer is not a GUI renderer (brief "
                    "section 26; VERIFIED)",
                    "CONFIRMED from lmms/lmms master src/core/main.cpp usage "
                    "text: 'render <project> [options...]  Render given "
                    "project file', 'rendertracks ...', plus -f/--format "
                    "(wav/flac/ogg/mp3), -s/--samplerate, -b/--bitrate, "
                    "-a/--float, -o/--output, -p/--profile. Includes "
                    "ProjectRenderer.h / RenderManager.h -- a dedicated "
                    "headless AUDIO render path, separate from any GUI "
                    "renderer. AudioEngine/ProjectRenderer existing does not "
                    "mean LMMS has a backend-neutral graphics renderer.",
                ),
                (
                    "12. LMMS: GUI performance is a live issue (brief section "
                    "25; VERIFIED)",
                    "CONFIRMED: issue #8402 'BeatPreview for Pattern Clip "
                    "causes lag' (~May 2026, Windows 11, 1.3.0-alpha.1.937): "
                    "'#7559 introduces a beat preview to the pattern clip view "
                    "in song editor. After this, when the pattern in song "
                    "editor has several dozens of tracks then scrolling and "
                    "zooming in the song editor lag extremely even though the "
                    "rendered view is just a white block.'",
                ),
                (
                    "13. Recommended architecture (brief sections 27-31)",
                    "LMMS CORE -> domain models -> presentation models -> "
                    "geometry/cache + interaction -> render command list -> "
                    "Renderer Interface -> Software/OpenGL/Vulkan/Metal/D3D "
                    "backends. The renderer should NOT understand "
                    "Track/MixerChannel/AutomationNode -- the DAW model feeds a "
                    "presentation system that emits render primitives "
                    "(rect/path/text/texture/waveform/meter). Retained "
                    "geometry should be object-specific: "
                    "TrackPresentation->[ClipPresentation], "
                    "AutomationPresentation->[SegmentGeometry], "
                    "MixerPresentation->[StripPresentation], "
                    "PianoRollPresentation->[NoteGeometry].",
                ),
                (
                    "14. The invalidation pipeline (brief section 21)",
                    "MODEL -> change event -> PRESENTATION OBJECT -> old/new "
                    "geometry -> dirty regions -> retained geometry -> "
                    "RENDERER -> CPU/GPU. And the three separable layers from "
                    "topic 34's section-31: WHAT changed (semantic dirty "
                    "categories), WHAT GEOMETRY is invalid (dirty regions), "
                    "WHAT PIXELS repaint (toolkit backing store). Pulsegrid "
                    "now implements layers 1-3 (v0.38.0) plus a retained "
                    "geometry cache + renderer interface (v0.39.0).",
                ),
                (
                    "15. Side-by-side verdict (brief sections 33-36, with "
                    "verification corrections)",
                    "FL Studio: DOCUMENTED -- vectorial GUI, changed-rectangle "
                    "painting, PBO OpenGL path (macOS diagnostic), V-sync "
                    "control, Blend2D as current renderer direction; NOT "
                    "PUBLICLY DOCUMENTED -- exact retained scene graph, "
                    "renderer classes, geometry cache (proprietary). LMMS: "
                    "CURRENT -- Qt Widgets, QPainter-based editors, "
                    "Model/View, Qt dirty-region repainting, Qt5/Qt6 "
                    "transition; HISTORICAL -- QML/OpenGL/NanoVG redesign "
                    "concept (never adopted); NOT ESTABLISHED -- DAW-wide "
                    "retained scene graph, renderer abstraction, dedicated GPU "
                    "backend, Vulkan/Metal/D3D/OpenGL backend selection. The "
                    "open item differs per DAW: for FL the question is "
                    "observability of an already-optimized proprietary "
                    "renderer; for LMMS it is architectural -- whether to "
                    "evolve Qt widget/painter into a retained presentation "
                    "system with an independent renderer.",
                ),
                (
                    "Pulsegrid adaptation (v0.39.0)",
                    "1. New python/daw/ui/renderer.py: backend-independent "
                    "Renderer ABC (draw_rect/draw_text/draw_line/draw_polygon/"
                    "delete/clear/bbox/has_node -- node ids, no DAW concepts). "
                    "TkCanvasRenderer maps node ids to canvas tags (the "
                    "production backend); RecordingRenderer records draw "
                    "commands for tests. The painter now emits primitives; the "
                    "backend draws them. 2. New "
                    "python/daw/ui/geometry_cache.py: LaneGeometryCache retains "
                    "one ClipDraw (presentation object: bounds, waveform "
                    "geometry, label layout, color, selection) per clip tag; "
                    "sync() diffs fresh presentation state against retained "
                    "geometry and reports added/removed/changed/unchanged ids; "
                    "unchanged ClipDraws are retained BY IDENTITY (no "
                    "reconstruction). Waveform peaks use a cheap content "
                    "fingerprint (length + endpoints + 16 sampled buckets) "
                    "because the widget rebuilds peak lists every frame. 3. "
                    "playlist_painter.draw_lane/draw_clip/redraw_clips now take "
                    "a Renderer instead of a raw canvas. 4. The Playlist "
                    "widget owns one cache per lane: refresh_clips diffs the "
                    "cache and redraws ONLY dirty node ids (in lane order, to "
                    "preserve z-order as closely as possible); cold cache and "
                    "unknown tags fall back to full lane redraw; delete_clips "
                    "drops cache entries.",
                ),
                (
                    "Test lessons",
                    "13 new tests in tests/test_retained_renderer.py: "
                    "Renderer is abstract; TkCanvasRenderer maps node ids to "
                    "canvas tags (create/find/delete/bbox/clear); "
                    "RecordingRenderer records primitives and "
                    "node_ids_drawn(); partial redraw emits draw commands ONLY "
                    "for the dirty node id -- proving the brief's section-31 "
                    "'partial draw-command generation' layer; unknown tags "
                    "emit nothing; cache cold sync marks everything added; "
                    "unchanged clips retain geometry BY IDENTITY across "
                    "syncs; moving one clip marks only it changed; added/"
                    "removed/drop/invalidate behave; color/label/selection "
                    "changes detected; Xvfb widget probes -- warm-cache "
                    "partial refresh leaves every unrelated canvas item "
                    "untouched, full refresh re-syncs the cache to zero "
                    "dirty. Bug found by tests: initial id()-based peaks "
                    "comparison marked every audio clip dirty every frame "
                    "(widget rebuilds peak lists each draw-state build); fixed "
                    "with the content fingerprint. Probe coupling: two "
                    "existing tests passed raw canvases to the painter and "
                    "were updated to wrap them in TkCanvasRenderer. Full "
                    "suite: 115/115 Rust, 267/267 Python under Xvfb (13 new), "
                    "smoke ALL PASS, Windows MSVC cross-check clean (runtime "
                    "unverified).",
                ),
                (
                    "Honest limits",
                    "Renderer abstraction covers the playlist lane path only "
                    "(ruler/playhead/mixer/automation painters still call "
                    "canvas directly -- future work). tkinter is the only "
                    "production backend (no GPU on the dev VM; tkinter has no "
                    "GPU path) -- the interface is the seam a future backend "
                    "would implement. Pixel repaint stays tkinter's job (as in "
                    "v0.38.0). The cache keys on presentation geometry, so a "
                    "widget-level zoom/bar-width change still rebuilds the "
                    "lane (correct: all geometry actually changed).",
                ),
                (
                    "Version",
                    "v0.39.0. Format unchanged (v15). No migration.",
                ),
            ],
            [
                "Retained mode and GPU rendering are different things: "
                "retaining scene state vs. where drawing executes. Pulsegrid "
                "now has both seams -- a retained geometry cache and a "
                "backend-independent renderer interface -- on the playlist "
                "lane path.",
                "FL Studio: changed-rectangle painting + Blend2D direction "
                "are documented; internals proprietary. LMMS: Qt "
                "Widgets/QPainter today, QML/OpenGL/NanoVG concept "
                "historical; a retained/GPU presentation layer is a genuine "
                "open architectural item.",
                "Pulsegrid takeaway (v0.39.0): ClipDraws are retained between "
                "frames by identity; moving one clip regenerates draw "
                "commands only for that clip (proven by RecordingRenderer "
                "tests); the renderer no longer knows about clips at all.",
                "Open items: extend the Renderer interface to "
                "ruler/playhead/mixer/automation painters; actual "
                "Windows 11 runtime verification.",
            ],
        ),

    (
        "Tangent Locking & LFO Layers",
        "Research topic 36. Two related automation architectures: (1) "
        "per-node tangent locking / spline control, (2) LFO layers applied "
        "to automation envelopes. FL Studio exposes a mature user-facing "
        "envelope/LFO system with tension handles and many curve modes; "
        "LMMS exposes a more inspectable mathematical spline model with "
        "per-node tangent locking, but its LFO functionality is "
        "controller/plugin-specific rather than an FL-style LFO layer on "
        "every automation spline.",
        [
            (
                "1. FL Studio: tension handles, not an exposed tangent model "
                "(brief sections 1-4)",
                "FL Automation Clips use control points connected by "
                "configurable envelope segments; each segment has a tension "
                "handle controlling curvature (right-click resets it; Ctrl "
                "fine-tunes). The user-facing model is control point -> "
                "position/value -> segment behavior -> tension handle -> "
                "curve shape. Image-Line's public documentation does not "
                "expose the internal node data structure, so no claim is "
                "made about an internal per-node tangent-lock boolean -- "
                "that would be speculation about proprietary internals.",
            ),
            (
                "2. FL's segment vocabulary: 11 curve types (brief section 2)",
                "The manual's 'Change curve type' list: Single curve, Double "
                "curve, Alt single curve, Alt double curve, Hold, Stairs, "
                "Smooth stairs, Pulse, Wave, Half sine, Smooth. Tension "
                "modifies many of them. The Hold curve is a genuine step "
                "(discontinuity), so FL's envelope system is a general "
                "segment evaluator (linear/spline/step/stairs/pulse/wave/"
                "half-sine), not exclusively a continuous spline system. "
                "VERIFIED CONFIRMED verbatim against the official "
                "playlist_automationclip.htm manual page (2026-10-05). "
                "Nuance from the verifier: the same 11-item list also "
                "appears in Harmor's and Gross Beat's envelope editors -- "
                "it is Image-Line's shared envelope vocabulary, not "
                "Automation-Clip-specific.",
            ),
            (
                "3. FL's LFO is a separate layer over the envelope (brief "
                "sections 5-8)",
                "Enabling LFO Mode does NOT erase the main automation "
                "envelope: 'Selecting this mode will not delete any "
                "automation clip data you have edited in' and 'Selecting "
                "the LFO function doesn't erase the 'main' automation "
                "envelope (although not visible). It remains available to "
                "be combined with the LFO when the Multiply switch is "
                "selected.' LFO parameters: Speed (SPD), Tension (TENS) -- "
                "a shape morph the manual describes as 'starting with "
                "square (pulse), sine, triangle and 'pulse'' -- Shape Skew "
                "(AK, triangle -> saw/reverse saw), Pulse Width (NW), "
                "Level (LVL, middle position = 0 = disabled, left = "
                "negative/inverted, right = positive). All CONFIRMED "
                "verbatim from the official manual (2026-10-05).",
            ),
            (
                "4. FL's Multiply switch: the key architectural idea (brief "
                "section 7)",
                "Manual, verbatim: 'When the switch is enabled the two "
                "values will be multiplied, i.e. the LFO acts as an "
                "amplitude modulator for the envelope. This is useful for "
                "unipolar (one directional) properties such as cutoff "
                "frequency, volume, etc. When the switch is disabled both "
                "values are added together, i.e. like an LFO 'offsetting' "
                "the envelope. This is useful for bipolar parameters such "
                "as panning.' So: main spline x LFO for unipolar params, "
                "main spline + LFO for bipolar ones. CONFIRMED verbatim "
                "(2026-10-05).",
            ),
            (
                "5. FL automation triggering and routing (brief sections "
                "9-11)",
                "Automation Clips can be triggered by Piano Roll, Step "
                "Sequencer, or a live controller keyboard, and 'will "
                "automate while the note is held'; they need not be placed "
                "in the Playlist when note-triggered. One Automation Clip "
                "may feed multiple linked controls, and multiple clips can "
                "target one control (a modulation graph, not 1:1). The "
                "Automation Clip itself is thus an LFO source -- different "
                "from routing an LFO plugin into a parameter. Harmor's "
                "manual confirms the same vocabulary elsewhere: tension "
                "handles, curve types, and LFO amplitude/phase/start time/"
                "tension/skew/pulse-width/tempo-lock/note-on retrigger. "
                "All CONFIRMED verbatim (2026-10-05).",
            ),
            (
                "6. LMMS: explicit in/out tangents + locking in source "
                "(brief sections 13-16)",
                "include/AutomationNode.h (lmms master) declares verbatim: "
                "'float m_inTangent; float m_outTangent; //!< @copydoc "
                "m_inTangent ... bool m_lockedTangents;' with the comment: "
                "'If the tangents were edited manually, this will be true. "
                "That way the tangents from this node will not be "
                "recalculated. It's set back to false if the tangents are "
                "reset.' So LMMS has a genuine per-node tangent-lock "
                "mechanism: AUTO (generated from neighbors) vs LOCKED "
                "(user-defined, fixed until explicitly reset). CONFIRMED "
                "verbatim from source (2026-10-05).",
            ),
            (
                "7. LMMS progression types and tangent editing (brief "
                "sections 17-19)",
                "include/AutomationClip.h declares 'enum class "
                "ProgressionType { Discrete, Linear, CubicHermite };'. "
                "Tangent editing for Cubic Hermite progressions is merged "
                "PR #5924 ('Adds feature to edit tangents of Cubic Hermite "
                "progressions'), listed in the 1.3.0-alpha.2 release notes "
                "under Minor features. A separate real fix, PR #7946 'Fix "
                "sensitivity of tangent editing in Automation Editor' "
                "(opened 2025-06-10, merged), is also in the 1.3.0-alpha.2 "
                "notes. CORRECTION (verifier, 2026-10-05): the June 2025 "
                "LMMS progress report (discussions/5915) does NOT mention "
                "the sensitivity fix -- the brief's attribution was wrong. "
                "Cite PR #7946 and the release notes instead. Also note: "
                "#5924 is a pull request, not an issue.",
            ),
            (
                "8. LMMS LFO: controllers, not an automation layer (brief "
                "sections 20-22)",
                "LMMS has LfoController ('A LFO-based controller and "
                "dialog', waveforms Sine/Triangle/Saw/Square/MoogSaw) and "
                "the 1.3.0-alpha.2 notes add 'Sample and Hold for LFO "
                "Controller (#6850)'. But there is NO general FL-style "
                "automation-pattern LFO layer: zero 'lfo' references in "
                "src/gui/editors/AutomationEditor.cpp (2316 lines), "
                "include/AutomationClip.h, src/core/AutomationClip.cpp, "
                "and include/AutomationNode.h on master (2026-10-05). LMMS "
                "thus separates automation and modulation more strongly "
                "(controller -> target) than FL (clip -> spline + LFO -> "
                "add/multiply -> target). CONFIRMED, with the absence "
                "scoped to master as of 2026-10-05.",
            ),
            (
                "9. LMMS is still expanding automation (brief section 23)",
                "The January 2026 LMMS progress report (discussions/8248): "
                "'January's major new feature is pitch bending directly in "
                "the Piano Roll without the need for a separate automation "
                "window... While it is currently limited to linear "
                "automation curves, support for other curve types may come "
                "in the future. Until then, those who want full control "
                "can still access the automation editor via Shift+Click.' "
                "CONFIRMED verbatim (2026-10-05).",
            ),
            (
                "10. The synthesis the brief proposes (brief sections 25-30)",
                "Combine LMMS's mathematical precision (nodes + in/out "
                "tangents + tangent locking + progression types) with FL's "
                "modulation architecture (many segment types + tension + "
                "persistent LFO layer + add/multiply). Keep three things "
                "separate: (a) spline geometry (nodes, tangents, lock "
                "state), (b) modulation layers (LFO: speed/shape/skew/pulse "
                "width/level/combine), (c) the evaluated final curve. The "
                "LFO must never modify the spline -- Final(t) = "
                "BaseSpline(t) [+|x] LFO(t) -- so disabling the LFO "
                "restores the hand-edited envelope exactly. Tangent edits "
                "invalidate only adjacent spline segments; LFO parameter "
                "edits invalidate only the evaluated/modulated preview -- "
                "the exact separation the retained-renderer architecture "
                "(topic 35) wants. The brief's comparison table and "
                "one-sentence verdict ('LMMS has the more explicit "
                "source-level concept of per-node spline tangents and "
                "tangent locking, while FL Studio has the substantially "
                "richer user-facing automation system') are consistent "
                "with the verified sources.",
            ),
            (
                "11. Verification summary (independent read-only subagent, "
                "2026-10-05)",
                "CONFIRMED with verbatim quotes: all 11 FL curve types; "
                "tension-handle drag/Ctrl/right-click-reset; LFO Mode "
                "preserving the main envelope; the full LFO parameter "
                "list (SPD/TENS/AK/NW/LVL) incl. negative level inversion; "
                "Multiply vs Add semantics with the unipolar/bipolar "
                "guidance; note-triggered automation; one-clip-to-many / "
                "many-to-one routing; Harmor's envelope + LFO vocabulary "
                "(tension handles, curve types, amplitude/phase/start "
                "time/TENS/SK/PW/tempo lock/note-on retrigger); LMMS "
                "m_inTangent/m_outTangent/m_lockedTangents and the "
                "locking comment; Discrete/Linear/CubicHermite "
                "ProgressionType; PR #5924 as a merged PR in the 1.3.0-"
                "alpha.2 notes; LFO Controller + Sample and Hold (#6850); "
                "no automation-pattern LFO layer in the four automation "
                "source files; the January 2026 pitch-bend report quote. "
                "REFUTED as attributed: the June 2025 progress report does "
                "not mention the tangent-editing sensitivity fix (real fix "
                "is PR #7946, in the 1.3.0-alpha.2 notes). Primary URLs: "
                "image-line.com/fl-studio-learning/fl-studio-online-manual/"
                "html/playlist_automationclip.htm, .../html/plugins/"
                "Harmor.htm, raw.githubusercontent.com/lmms/lmms/master/"
                "include/AutomationNode.h, .../include/AutomationClip.h, "
                "github.com/lmms/lmms/pull/5924, .../pull/7946, github.com/"
                "LMMS/lmms/releases/tag/v1.3.0-alpha.2, github.com/LMMS/"
                "lmms/discussions/8248, raw.githubusercontent.com/lmms/"
                "lmms/master/include/LfoController.h.",
            ),
        ],
        [
            "Pulsegrid takeaway (v0.40.0): per-node tangent locking on the "
            "smooth (cubic Hermite) evaluator -- AutoPoint gains optional "
            "in_tan/out_tan (value per beat); None = AUTO (Catmull-Rom from "
            "neighbors, tension-scaled, exactly the pre-36 formula), float "
            "= LOCKED (used verbatim, immune to neighbor moves). The "
            "AutomationEditor draws tangent handles in smooth mode (filled "
            "= locked, hollow = auto); dragging a handle locks that "
            "slope; Alt+right-click a point resets its tangents to auto. "
            "Serialization is additive (no format bump): old point dicts "
            "simply lack the keys.",
            "Pulsegrid takeaway (v0.40.0): a persistent LFO modulation "
            "layer per automation lane (LfoSettings: enabled, speed in "
            "cycles/beat, shape sine/triangle/saw/pulse, skew -1..1, pulse "
            "width, level incl. negative/invert, combine add/multiply). "
            "Evaluated in the Rust engine per block AFTER the base spline "
            "-- the spline is never modified; add = base + level*wave "
            "(bipolar, e.g. pan), multiply = base*(1 + level*wave) "
            "(unipolar, e.g. gain). Phase = (beat*speed) % 1, so realtime "
            "and offline render agree exactly. Tangents are scaled by the "
            "bridge's unit conversion (dvalue/dbeat) like values.",
            "Pulsegrid takeaway (v0.40.0): the editor draws the LFO-"
            "modulated curve as an orange dashed preview overlay while the "
            "base spline stays drawn underneath (brief section 26: the LFO "
            "must not destroy the envelope); LFO edits log a geometric "
            "AUTOMATION dirty event for the lane (brief section 28: LFO "
            "param edits invalidate the evaluated preview, not spline "
            "geometry); tangent drags log the point's old/new bounds "
            "union. One gesture = one undo throughout.",
            "Verification: 121/121 Rust (5 new: locked-vs-auto vectors, "
            "neighbor-move invariance, LFO add/multiply, wave shapes incl. "
            "skew extremes, from_arrangement LFO survival), 286/286 "
            "Python under Xvfb (19 new in test_automation_tangent_lfo.py: "
            "model round-trips + validation, evaluator vectors mirroring "
            "Rust, bridge tangent scaling + LFO passthrough, widget "
            "probes for handles/hit-test/Alt+right-click reset/LFO "
            "overlay + commit, and an E2E render proving the engine's "
            "output wobbles with the LFO), smoke ALL PASS, Windows MSVC "
            "cross-check clean (runtime unverified).",
            "Honest limits: tangents shape only the smooth evaluator "
            "(other modes ignore them, as documented); no per-SEGMENT "
            "curve types like FL's 11 (Pulsegrid lanes keep the per-lane "
            "interp from topic 30); FL's LFO Tension shape-morph "
            "(square->sine->triangle->pulse) is not modeled -- Pulsegrid "
            "uses an explicit shape enum + skew + pulse width instead; "
            "LFO is per automation lane (FL also allows one clip -> many "
            "targets; Pulsegrid lanes stay 1:1 with a param). No format "
            "bump (additive), no new binary built.",
            "Open items: per-segment curve types (FL's 11-type "
            "vocabulary); LFO tempo-sync note values; extending the "
            "Renderer abstraction to the automation painter (topic 35 "
            "follow-up); Windows 11 runtime verification.",
        ],
    )

]


def build():
    wb = Workbook()
    # Cover sheet.
    cover = wb.active
    cover.title = "Cover"
    cover["A1"] = "Pulsegrid — FL Studio research notes"
    cover["A1"].font = TITLE_FONT
    cover["A3"] = ("Infographic studies for the Pulsegrid DAW build. "
                   "Regenerate: cd ~/workspace/pulsegrid && "
                   ".venv/bin/python tools/fl_research/research.py")
    cover["A3"].alignment = WRAP
    cover.column_dimensions["A"].width = 90
    for i, (name, _intro, _sections, _takeaways) in enumerate(INFOGRAPHICS, 1):
        cover[f"A{5 + i}"] = f"{i}. {name}"

    for name, intro, sections, takeaways in INFOGRAPHICS:
        safe = name[:31].replace(":", " -").replace("/", "-")
        ws = wb.create_sheet(safe)
        ws["A1"] = name
        ws["A1"].font = TITLE_FONT
        ws["A2"] = intro
        ws["A2"].alignment = WRAP
        row = 4
        for section, points in sections:
            ws[f"A{row}"] = section
            ws[f"A{row}"].font = SECTION_FONT
            row += 1
            for pt in points:
                ws[f"A{row}"] = pt
                ws[f"A{row}"].alignment = WRAP
                ws[f"B{row}"] = ""
                row += 1
            row += 1
        ws[f"A{row}"] = "Pulsegrid takeaways"
        ws[f"A{row}"].font = SECTION_FONT
        row += 1
        for t in takeaways:
            ws[f"A{row}"] = t
            ws[f"A{row}"].alignment = WRAP
            row += 1
        ws.column_dimensions["A"].width = 100
        for r in ws.iter_rows():
            ws.row_dimensions[r[0].row].height = 30

    out = os.path.normpath(OUT)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    wb.save(out)
    print(f"Wrote {out}")


if __name__ == "__main__":
    build()
