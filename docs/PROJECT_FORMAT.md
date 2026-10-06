# Pulsegrid Project Format

Versioned, documented, owned by Pulsegrid. Files use the
`.pulsegrid.json` extension. This format is original — it is not
compatible with, derived from, or imitative of FL Studio's `.flp`.

**Format versions:** v1 (single pattern, flat channels), v2 (named
patterns + playlist tracks with clips), v3 (per-step note pitches),
v4 (per-step velocities, clip starts in beats), v5 (note lists with
variable lengths, chords allowed), v6 (current: automation lanes).
Older files migrate automatically on load.

## Envelope (v5)

```json
{
  "format": "pulsegrid-project",
  "version": 5,
  "name": "My Beat",
  "tempo": 128.0,
  "patterns": [ ... ],
  "selected_pattern": "pattern-1",
  "playlist": { "tracks": [ ... ] }
}
```

## Pattern

A reusable musical phrase: a fixed channel set and step grid.

```json
{
  "id": "pattern-1",
  "name": "Drums",
  "steps": 16,
  "channels": [ ... ]
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `id` | string | unique within the project |
| `name` | string | display name |
| `steps` | int | 1–256 steps |
| `channels` | object[] | at least one; channel `id`s unique within the pattern |

## Channel

```json
{
  "id": "pattern-1-kick",
  "name": "Kick",
  "instrument": "kick",
  "pitch": 36,
  "notes": [
    { "start": 0.0, "len": 1.0, "pitch": 36, "vel": 1.0 },
    { "start": 4.0, "len": 2.0, "pitch": 40, "vel": 0.8 }
  ]
}
```

| Field | Type | Constraints |
|-------|------|-------------|
| `format` | string | must be `"pulsegrid-project"` |
| `version` | int | file format version; see below |
| `name` | string | display name |
| `tempo` | number | 20–300 BPM |
| `id` | string | unique within its scope |
| `instrument` | string | one of `kick`, `snare`, `hat`, `bass`, `lead` |
| `pitch` | int | MIDI note 0–127; default pitch for newly added notes |
| `notes` | note[] | note list (added in v5; see below) |

Each note has `start` (steps from the pattern start, 0 ≤ start < steps),
`len` (length in steps, 0 < len ≤ 64), `pitch` (MIDI 0–127), and `vel`
(velocity 0–1, defaults to 0.9 when absent). Notes may overlap —
stacked notes at one step are chords. Pitched instruments
(`bass`, `lead`) sustain for the note's length then release;
one-shot drums (`kick`, `snare`, `hat`) ignore length.

## Playlist track

```json
{
  "id": "track-1",
  "name": "Drums",
  "gain": 1.0,
  "pan": 0.0,
  "muted": false,
  "effects": [
    { "type": "delay",
      "params": { "time_ms": 375.0, "feedback": 35.0, "mix": 30.0 } }
  ],
  "clips": [
    { "pattern": "pattern-1", "start_beat": 0, "bars": 8 }
  ]
}
```

A clip places a pattern on the timeline: it starts at `start_beat`
(beats from the arrangement start) and the pattern repeats to fill
`bars` bars. (v3 and earlier stored whole-bar starts in `start_bar`;
they migrate by multiplying by 4.) `gain` (0–2), `pan` (−1–1), `muted`,
and `effects` are real engine parameters, edited in the Mixer tab.
Effect params are stored in display units (`%`, `ms`, `Hz`); kinds are
`delay` (`time_ms` 10–2000, `feedback` 0–95%, `mix` 0–100%),
`drive` (`amount` 0–100%), and `filter` (`cutoff` 200–18000 Hz).
These fields were added in v2 without a version bump — they default
(`muted: false`, `effects: []`) on files that lack them.

## Versioning policy

- `version` is an integer. Bump it for any breaking schema change; add a
  migration path in `Project.from_dict`.
- **v1 → v2 migration:** the single v1 pattern becomes `pattern-1`
  ("Pattern 1") on `track-1` ("Track 1"), with one clip covering
  `ceil(steps/16)` bars. v1 files open transparently and re-save as v2.
- **v2 → v3 migration:** each channel's `note_pitch` is filled with the
  channel's v2 `pitch` for every step, so old projects sound identical
  and re-save as v3. (`pitch` keeps its role as the default for new
  notes.)
- **v3 → v4 migration:** each channel's `note_vel` is filled with 0.9
  for every step, and each clip's `start_bar` becomes
  `start_beat = start_bar * 4`, so old projects sound identical and
  re-save as v4.
- **v4 → v5 migration:** each active step becomes a 1-step note
  (`start` = step, `len` = 1, `pitch`/`vel` from the per-step arrays),
  so old projects sound identical and re-save as v5. (v1–v3 reach v5
  through the same path: their per-step arrays are reconstructed first,
  then converted to notes.)
- **v5 → v6 migration:** no automation lanes yet — the project gains an
  empty `automation` list, so old projects sound identical and re-save
  as v6.
- **v6 → v7 migration:** no plugin effects yet — parsing is identical;
  v6 files load unchanged and re-save as v7.
- **v7 → v8 migration:** no track generators yet — every track gains
  `"generator": null`, so old projects sound identical and re-save
  as v8.
- **v8 → v9 migration:** no truncate-notes setting yet — the project gains
  `"truncate_notes": false`, so old projects sound identical and re-save
  as v9.
- Files with `version > FORMAT_VERSION` are rejected with an explicit
  "saved by a newer Pulsegrid" error — never silently misread.
- Unknown extra fields are ignored on load (forward-tolerant reads).

## Plugin effects (v7)

An effect entry may have `"type": "plugin"`, with two extra fields:

```json
{"type": "plugin", "plugin_id": "com.example.echo",
 "plugin_path": "/home/user/.clap/echo.clap",
 "params": {"12": 0.75, "13": 1200.0}}
```

- `plugin_id` is the CLAP plugin id (stable across machines);
  `plugin_path` is where it was found when added (used directly, with
  a fresh scan-by-id as fallback if the file moved).
- `params` maps CLAP parameter id → value, in the plugin's real units.
  Values are validated finite; ranges are the plugin's own.
- Only **parameter values** are stored, not full plugin state blobs —
  for typical effects the params fully describe the sound.
- Automation lanes can target plugin params as `"fx{i}.p{clap_id}"`
  (e.g. `"fx0.p12"`), validated against the scanned parameter list.
- If the plugin isn't installed, the effect is skipped at load with a
  clear error — the project still opens.

## Automation lanes (v6)

Top-level `"automation"`: a list of lanes, each bound to one track and
one parameter:

```json
"automation": [
  {"id": "auto-1", "track": "track-2", "param": "fx0.cutoff",
   "points": [{"beat": 16.0, "value": 800.0},
              {"beat": 31.9, "value": 8000.0}]}
]
```

- `param` is `"gain"`, `"pan"`, or `"fx{i}.{name}"` where `i` is the
  effect's index on the track and `name` is one of its parameters
  (`time_ms`, `feedback`, `mix` for delay; `amount` for drive;
  `cutoff` for filter).
- Point values are in **display units** (%, ms, Hz — the same units the
  mixer and effect dialogs show); the bridge converts to engine units.
- Points are sorted by beat on load. Beats are in arrangement time
  (0 ≤ beat < arrangement beats) and wrap with the arrangement loop.
- Semantics: linear interpolation between points; before the first
  point the track's static value holds; after the last point the last
  value holds. A single point at beat 0 is a constant override.
- Limits: 512 points per lane; values range-checked against the
  parameter's range.

## Save / recovery behavior

- Saves are atomic: write to a temp file in the same directory, then
  `os.replace` over the target. A crash mid-save never leaves a
  half-written project.
- If a file is not valid JSON or fails schema validation, loading raises
  `ProjectError` **and** preserves the original bytes at
  `<name>.corrupt-<timestamp>.bak` before reporting. The UI shows the
  error; nothing is silently discarded.

## Track generators (v8)

A playlist track may have a `"generator"` field — its sound source:

```json
"generator": {"type": "plugin", "plugin_id": "org.pulsegrid.test-synth",
 "plugin_path": "/home/user/.clap/PulsegridTestSynth.clap",
 "params": {"11": 0.0, "12": 0.01}}
```

- `null` (or absent) means the built-in voice bank.
- `plugin_id`/`plugin_path`/`params` work like plugin effects: the CLAP
  id, the path where it was found, and parameter values in the plugin's
  real units keyed by str(param id).

## Undo

Undo/redo is an in-memory command stack in the UI layer (`daw/undo.py`,
default depth 100). It is not persisted in the file.
