//! PyO3 bridge for the Pulsegrid audio engine.
//!
//! The bridge is intentionally coarse-grained: Python sends whole-arrangement
//! snapshots and transport commands, and reads back position/stats. The only
//! per-block crossing into Python is the optional progress callback of
//! `render_wav_file`, which runs with the GIL released during DSP.

mod backend;
mod debug;
mod effects;
mod engine;
mod graph;
mod modulators;
mod plugins;
mod sample;
mod synth;
mod timeline;
mod vst3;
mod wav;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::HashMap;

use crate::timeline::{RawAutoCurve, RawAutoPoint, RawLfo};
use crate::timeline::SendData;
use crate::timeline::SendTap;
use engine::{Engine, RawAudioClip, RawClip, RawFx, RawGenerator, RawNote, RawPattern, RawTrack};

/// Resolve a (track, fx_index, layer_index) triple to a plugin slot
/// key. `fx_index` is Some for insert effects, `layer_index` is
/// Some for generator layers; exactly one must be set.
fn slot_key(
    track: usize,
    fx_index: Option<usize>,
    layer_index: Option<usize>,
) -> PyResult<crate::plugins::PluginSlotKey> {
    match (fx_index, layer_index) {
        (Some(i), None) => Ok(crate::plugins::PluginSlotKey::Fx { track, index: i }),
        (None, Some(l)) => Ok(crate::plugins::PluginSlotKey::Instrument { track, layer: l }),
        _ => Err(PyValueError::new_err(
            "exactly one of fx_index / layer_index must be set",
        )),
    }
}

fn err_to_py(e: String) -> PyErr {
    PyRuntimeError::new_err(e)
}

fn get_required<'py, T>(dict: &Bound<'py, PyDict>, key: &str) -> PyResult<T>
where
    T: for<'a> FromPyObject<'a>,
{
    dict.get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing '{}'", key)))?
        .extract()
        .map_err(|_| PyValueError::new_err(format!("'{}' has wrong type", key)))
}

/// Extract a list of dicts from a dict key (concrete lifetimes so PyO3's
/// `FromPyObject` impls apply).
fn get_list<'py>(dict: &Bound<'py, PyDict>, key: &str) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let item = dict
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing '{}'", key)))?;
    let list = item
        .downcast::<pyo3::types::PyList>()
        .map_err(|_| PyValueError::new_err(format!("'{}' must be a list", key)))?;
    list.iter()
        .map(|v| {
            v.downcast_into::<PyDict>()
                .map_err(|_| PyValueError::new_err(format!("'{}' must be a list of dicts", key)))
        })
        .collect()
}

/// Parse an arrangement dict from Python:
/// {"tempo": 128.0,
///  "patterns": [{"id": "p1", "name": "Drums", "steps": 16,
///                "channels": [{"instrument": "kick",
///                              "notes": [{"start": 0.0, "len": 1.0,
///                                         "pitch": 36, "vel": 0.9}]}]}],
///  "tracks": [{"name": "Track 1", "gain": 1.0, "pan": 0.0, "muted": false,
///              "effects": [{"type": "delay", "time_ms": 375.0,
///                           "feedback": 0.35, "mix": 0.3}],
///              "automation": [{"param": "gain",
///                              "points": [[0.0, 0.0], [8.0, 1.0]]}],
///              "clips": [{"pattern": 0, "start_beat": 0, "bars": 4}]}]}
/// Parse an optional legacy single-generator dict (for backwards compat).
fn parse_generator(v: Option<Bound<'_, PyAny>>) -> PyResult<Option<engine::RawGenerator>> {
    let Some(v) = v else {
        return Ok(None);
    };
    if v.is_none() {
        return Ok(None);
    }
    let d: Bound<'_, PyDict> = v
        .extract()
        .map_err(|_| PyValueError::new_err("'generator' must be an object or null"))?;
    let kind: String = d
        .get_item("type")?
        .map(|v| v.extract::<String>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'generator.type' must be a string"))?
        .unwrap_or_default();
    if kind != "plugin" && kind != "vst3" {
        return Err(PyValueError::new_err(
            "unsupported generator type (only 'plugin' and 'vst3' are supported)",
        ));
    }
    let plugin_id: String = d
        .get_item("plugin_id")?
        .map(|v| v.extract::<String>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'plugin_id' must be a string"))?
        .ok_or_else(|| PyValueError::new_err("'generator' missing 'plugin_id'"))?;
    let path: String = d
        .get_item("plugin_path")?
        .map(|v| v.extract::<String>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'plugin_path' must be a string"))?
        .ok_or_else(|| PyValueError::new_err("'generator' missing 'plugin_path'"))?;
    let mut params = Vec::new();
    if let Some(pdict) = d.get_item("params")? {
        let pdict: Bound<'_, PyDict> = pdict
            .extract()
            .map_err(|_| PyValueError::new_err("'generator.params' must be an object"))?;
        for (k, v) in pdict.iter() {
            let id: u32 = k
                .extract::<String>()
                .map_err(|_| PyValueError::new_err("'generator.params' keys must be strings"))?
                .parse()
                .map_err(|_| PyValueError::new_err("'generator.params' keys must be integers"))?;
            let val: f64 = v
                .extract()
                .map_err(|_| PyValueError::new_err("'generator.params' values must be numbers"))?;
            params.push((id, val));
        }
    }
    Ok(Some(engine::RawGenerator {
        plugin_id,
        path,
        params,
        is_vst3: kind == "vst3",
    }))
}

/// Parse a track's generator layers:
/// [{"type": "plugin", "plugin_id": ..., "plugin_path": ...,
///   "params": {"7": 0.5}, "gain": 1.0, "pitch_offset": 0, "enabled": true}]
/// Also accepts the legacy single "generator" dict (converted to one layer).
fn parse_generator_layers(t: &Bound<'_, PyDict>) -> PyResult<Vec<engine::RawGeneratorLayer>> {
    // New style: "generator_layers" list.
    if let Some(layers_any) = t.get_item("generator_layers")? {
        if !layers_any.is_none() {
            let layer_list: Vec<Bound<'_, PyDict>> = layers_any
                .extract()
                .map_err(|_| PyValueError::new_err("'generator_layers' must be a list"))?;
            let mut out = Vec::new();
            for l in layer_list.iter() {
                out.push(parse_one_layer(l)?);
            }
            return Ok(out);
        }
    }
    // Legacy: single "generator" dict -> one layer.
    if let Some(gen) = parse_generator(t.get_item("generator")?)? {
        Ok(vec![engine::RawGeneratorLayer {
            plugin_id: gen.plugin_id,
            path: gen.path,
            params: gen.params,
            gain: 1.0,
            pitch_offset: 0,
            enabled: true,
            state: None,
            is_vst3: gen.is_vst3,
        }])
    } else {
        Ok(Vec::new())
    }
}

/// Parse an optional "state_base64" field: the host's Base64 encoding of
/// the plugin's opaque CLAP state blob. Invalid Base64 is a loud error
/// (corrupt project data must not silently become an empty blob).
fn parse_state_blob(d: &Bound<'_, PyDict>) -> PyResult<Option<Vec<u8>>> {
    match d.get_item("state_base64")? {
        None => Ok(None),
        Some(v) => {
            if v.is_none() {
                return Ok(None);
            }
            let s: String = v
                .extract()
                .map_err(|_| PyValueError::new_err("'state_base64' must be a string"))?;
            if s.is_empty() {
                return Ok(None);
            }
            crate::plugins::base64_decode(&s)
                .map(Some)
                .map_err(PyValueError::new_err)
        }
    }
}

/// Parse one generator layer dict.
fn parse_one_layer(l: &Bound<'_, PyDict>) -> PyResult<engine::RawGeneratorLayer> {
    let kind: String = l
        .get_item("type")?
        .map(|v| v.extract::<String>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'layer.type' must be a string"))?
        .unwrap_or_default();
    if kind != "plugin" && kind != "vst3" {
        return Err(PyValueError::new_err(
            "unsupported generator type (only 'plugin' and 'vst3' are supported)",
        ));
    }
    let plugin_id: String = l
        .get_item("plugin_id")?
        .map(|v| v.extract::<String>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'plugin_id' must be a string"))?
        .ok_or_else(|| PyValueError::new_err("'layer' missing 'plugin_id'"))?;
    let path: String = l
        .get_item("plugin_path")?
        .map(|v| v.extract::<String>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'plugin_path' must be a string"))?
        .ok_or_else(|| PyValueError::new_err("'layer' missing 'plugin_path'"))?;
    let mut params = Vec::new();
    if let Some(pdict) = l.get_item("params")? {
        let pdict: Bound<'_, PyDict> = pdict
            .extract()
            .map_err(|_| PyValueError::new_err("'layer.params' must be an object"))?;
        for (k, v) in pdict.iter() {
            let id: u32 = k
                .extract::<String>()
                .map_err(|_| PyValueError::new_err("'layer.params' keys must be strings"))?
                .parse()
                .map_err(|_| PyValueError::new_err("'layer.params' keys must be integers"))?;
            let val: f64 = v
                .extract()
                .map_err(|_| PyValueError::new_err("'layer.params' values must be numbers"))?;
            params.push((id, val));
        }
    }
    let gain: f32 = l
        .get_item("gain")?
        .map(|v| v.extract::<f32>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'layer.gain' must be a number"))?
        .unwrap_or(1.0);
    let pitch_offset: i32 = l
        .get_item("pitch_offset")?
        .map(|v| v.extract::<i32>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'layer.pitch_offset' must be an integer"))?
        .unwrap_or(0);
    let enabled: bool = l
        .get_item("enabled")?
        .map(|v| v.extract::<bool>())
        .transpose()
        .map_err(|_| PyValueError::new_err("'layer.enabled' must be a bool"))?
        .unwrap_or(true);
    let state = parse_state_blob(l)?;
    Ok(engine::RawGeneratorLayer {
        plugin_id,
        path,
        params,
        gain,
        pitch_offset,
        enabled,
        state,
        is_vst3: kind == "vst3",
    })
}

/// Parse the Python arrangement dict into validated Rust data, plus the
/// sample asset references (`(id, path)` pairs) the arrangement declares.
/// Sample bytes are NOT decoded here: `set_arrangement` loads missing ids
/// into the engine registry, and `render_wav_file` decodes them itself.
fn parse_arrangement(
    dict: &Bound<'_, PyDict>,
) -> PyResult<(timeline::ArrangementData, Vec<(String, String)>)> {
    let tempo: f64 = get_required(dict, "tempo")?;
    let truncate_notes: bool = dict
        .get_item("truncate_notes")?
        .and_then(|v| v.extract::<bool>().ok())
        .unwrap_or(false);

    // Sample assets: [{"id": str, "path": str}]. Ids are validated by
    // make_arrangement; paths are used to load missing registry entries.
    let mut sample_refs: Vec<(String, String)> = Vec::new();
    if let Some(s_any) = dict.get_item("samples")? {
        let sample_dicts: Vec<Bound<'_, PyDict>> = s_any
            .extract()
            .map_err(|_| PyValueError::new_err("'samples' must be a list of {id, path}"))?;
        for s in sample_dicts.iter() {
            let id: String = get_required(s, "id")?;
            let path: String = get_required(s, "path")?;
            sample_refs.push((id, path));
        }
    }

    let patterns_any: Vec<Bound<'_, PyDict>> = get_list(dict, "patterns")?;
    let mut patterns = Vec::with_capacity(patterns_any.len());
    for p in patterns_any.iter() {
        let channels_any: Vec<Bound<'_, PyDict>> = get_list(p, "channels")?;
        let mut channels = Vec::with_capacity(channels_any.len());
        for ch in channels_any.iter() {
            let instrument: String = get_required(ch, "instrument")?;
            let notes_any: Vec<Bound<'_, PyDict>> = get_list(ch, "notes")?;
            let mut notes = Vec::with_capacity(notes_any.len());
            for n in notes_any.iter() {
                notes.push(RawNote {
                    start_step: get_required(n, "start")?,
                    len_steps: get_required(n, "len")?,
                    pitch: get_required(n, "pitch")?,
                    velocity: n
                        .get_item("vel")?
                        .map(|v| v.extract::<f64>())
                        .transpose()
                        .map_err(|_| PyValueError::new_err("'vel' must be a number"))?
                        .unwrap_or(0.9),
                    // Per-note pan in engine convention (-1 left .. +1
                    // right); the Python bridge converts from FL-style
                    // 0.0-1.0. Absent = center.
                    pan: n
                        .get_item("pan")?
                        .map(|v| v.extract::<f64>())
                        .transpose()
                        .map_err(|_| PyValueError::new_err("'pan' must be a number"))?
                        .unwrap_or(0.0),
                });
            }
            channels.push((instrument, notes));
        }
        let steps: usize = get_required(p, "steps")?;
        patterns.push(RawPattern {
            id: get_required(p, "id")?,
            name: p
                .get_item("name")?
                .map(|v| v.extract::<String>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'name' must be a string"))?
                .unwrap_or_default(),
            steps,
            channels,
        });
    }

    let tracks_any: Vec<Bound<'_, PyDict>> = get_list(dict, "tracks")?;
    let mut tracks = Vec::with_capacity(tracks_any.len());
    // First pass: collect track IDs for send resolution.
    let mut track_ids = Vec::with_capacity(tracks_any.len());
    for t in tracks_any.iter() {
        let tid: String = t
            .get_item("id")?
            .map(|v| v.extract::<String>())
            .transpose()
            .map_err(|_| PyValueError::new_err("'id' must be a string"))?
            .unwrap_or_default();
        track_ids.push(tid);
    }
    for t in tracks_any.iter() {
        let clips_any: Vec<Bound<'_, PyDict>> = get_list(t, "clips")?;
        let mut clips = Vec::with_capacity(clips_any.len());
        for c in clips_any.iter() {
            clips.push(RawClip {
                pattern: get_required(c, "pattern")?,
                start_beat: get_required(c, "start_beat")?,
                bars: get_required(c, "bars")?,
            });
        }
        // "effects" is always present (the Python bridge includes it, possibly
        // empty); each entry carries kind + the union of effect params.
        let fx_any: Vec<Bound<'_, PyDict>> = get_list(t, "effects")?;
        let mut effects = Vec::with_capacity(fx_any.len());
        for f in fx_any.iter() {
            let kind: String = get_required(f, "type")?;
            let (plugin_id, plugin_path, plugin_params) = if kind == "plugin" || kind == "vst3" {
                let plugin_id: String = get_required(f, "plugin_id")?;
                let plugin_path: String = get_required(f, "plugin_path")?;
                // "params" is {param_id: value}; keys arrive as strings.
                let mut params = Vec::new();
                if let Ok(Some(d)) = f.get_item("params") {
                    if let Ok(dict) = d.downcast::<PyDict>() {
                        for (k, v) in dict.iter() {
                            let ks: String = k.extract().unwrap_or_default();
                            let id: u32 = ks.parse().unwrap_or(u32::MAX);
                            let val: f64 = v.extract().unwrap_or(f64::NAN);
                            if id != u32::MAX && val.is_finite() {
                                params.push((id, val));
                            }
                        }
                    }
                }
                params.sort_by_key(|(id, _)| *id);
                (plugin_id, plugin_path, params)
            } else {
                (String::new(), String::new(), Vec::new())
            };
            let plugin_state = parse_state_blob(f)?;
            effects.push(RawFx {
                kind,
                time_ms: get_f32(f, "time_ms"),
                feedback: get_f32(f, "feedback"),
                // The pitch shifter's mix arrives as "pitch_mix"
                // (Python keeps it distinct from Delay's "mix" so
                // automation names stay unambiguous); fall back to
                // "mix" for the other kinds.
                mix: {
                    let pm = get_f32(f, "pitch_mix");
                    if pm != 0.0 {
                        pm
                    } else {
                        get_f32(f, "mix")
                    }
                },
                amount: get_f32(f, "amount"),
                cutoff: get_f32(f, "cutoff"),
                threshold: get_f32(f, "threshold"),
                ratio: get_f32(f, "ratio"),
                attack_ms: get_f32(f, "attack_ms"),
                release_ms: get_f32(f, "release_ms"),
                semitones: get_f32(f, "semitones"),
                plugin_id,
                plugin_path,
                plugin_params,
                plugin_state,
            });
        }
        // "automation" is optional (older dicts); each entry is
        // {"param": "gain"|"pan"|"fx0.cutoff", "points": [[beat, value], ...]}
        // in engine units.
        let mut automation = Vec::new();
        if let Some(a_any) = t.get_item("automation")? {
            let curves: Vec<Bound<'_, PyDict>> = a_any
                .extract()
                .map_err(|_| PyValueError::new_err("'automation' must be a list of curves"))?;
            for a in curves.iter() {
                let param: String = get_required(a, "param")?;
                // Accept [[beat, value], ...] or
                // [[beat, value, in_tan, out_tan], ...] (tangents optional,
                // null = auto). Older dicts pass bare pairs.
                let pts_any: Vec<Vec<Option<f64>>> = a
                    .get_item("points")?
                    .ok_or_else(|| PyValueError::new_err("automation curve missing 'points'"))?
                    .extract()
                    .map_err(|_| PyValueError::new_err("'points' must be [[beat, value], ...]"))?;
                let mut points = Vec::with_capacity(pts_any.len());
                for pair in pts_any {
                    if !(2..=4).contains(&pair.len()) {
                        return Err(PyValueError::new_err(
                            "'points' entries must be [beat, value] or [beat, value, in_tan, out_tan]",
                        ));
                    }
                    let beat = pair[0].ok_or_else(|| {
                        PyValueError::new_err("'points' beat must be a number")
                    })?;
                    let value = pair[1].ok_or_else(|| {
                        PyValueError::new_err("'points' value must be a number")
                    })?;
                    points.push(RawAutoPoint {
                        beat,
                        value,
                        in_tan: pair.get(2).copied().flatten(),
                        out_tan: pair.get(3).copied().flatten(),
                    });
                }
                // "interp" is optional (older dicts); unknown names are
                // an error so typos can't silently change the curve.
                let interp: String = a
                    .get_item("interp")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'interp' must be a string"))?
                    .unwrap_or_else(|| "linear".to_string());
                let tension: f32 = a
                    .get_item("tension")?
                    .map(|v| v.extract::<f32>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'tension' must be a number"))?
                    .unwrap_or(0.5);
                // "lfo" is optional (older dicts); null/absent = no LFO.
                let lfo_item = a.get_item("lfo")?;
                let lfo: Option<RawLfo> = match lfo_item {
                    Some(v) if !v.is_none() => Some({
                        let d: Bound<'_, PyDict> = v.extract().map_err(|_| {
                            PyValueError::new_err("'lfo' must be an object")
                        })?;
                        let get_f = |k: &str, def: f32| -> PyResult<f32> {
                            d.get_item(k)?
                                .map(|x| {
                                    x.extract::<f32>().map_err(|_| {
                                        PyValueError::new_err(format!(
                                            "'lfo.{k}' must be a number"
                                        ))
                                    })
                                })
                                .transpose()
                                .map(|o| o.unwrap_or(def))
                        };
                        let get_s = |k: &str, def: &str| -> PyResult<String> {
                            d.get_item(k)?
                                .map(|x| {
                                    x.extract::<String>().map_err(|_| {
                                        PyValueError::new_err(format!(
                                            "'lfo.{k}' must be a string"
                                        ))
                                    })
                                })
                                .transpose()
                                .map(|o| o.unwrap_or_else(|| def.to_string()))
                        };
                        RawLfo {
                            enabled: d
                                .get_item("enabled")?
                                .map(|x| x.extract::<bool>())
                                .transpose()
                                .map_err(|_| {
                                    PyValueError::new_err(
                                        "'lfo.enabled' must be a boolean",
                                    )
                                })?
                                .unwrap_or(false),
                            speed: get_f("speed", 1.0)?,
                            shape: get_s("shape", "sine")?,
                            skew: get_f("skew", 0.0)?,
                            pulse_width: get_f("pulse_width", 0.5)?,
                            level: get_f("level", 1.0)?,
                            combine: get_s("combine", "add")?,
                        }
                    }),
                    _ => None,
                };
                automation.push(RawAutoCurve {
                    param,
                    points,
                    interp,
                    tension,
                    lfo,
                });
            }
        }
        // "audio_clips" is optional (older dicts); each entry is a
        // per-instance audio clip: {"asset": id, "start_beat": f,
        // "length_beats": f, "start_offset_beats": f, "gain": f,
        // "pan": -1..1 (engine convention), "pitch_semitones": f,
        // "fine_cents": f, "reverse": bool, "muted": bool}.
        // Range validation happens in Song::from_arrangement.
        let mut audio_clips = Vec::new();
        if let Some(ac_any) = t.get_item("audio_clips")? {
            let clip_dicts: Vec<Bound<'_, PyDict>> = ac_any
                .extract()
                .map_err(|_| PyValueError::new_err("'audio_clips' must be a list"))?;
            for c in clip_dicts.iter() {
                let asset: String = get_required(c, "asset")?;
                audio_clips.push(RawAudioClip {
                    asset,
                    start_beat: get_required(c, "start_beat")?,
                    length_beats: get_required(c, "length_beats")?,
                    start_offset_beats: c
                        .get_item("start_offset_beats")?
                        .map(|v| v.extract::<f64>())
                        .transpose()
                        .map_err(|_| {
                            PyValueError::new_err("'start_offset_beats' must be a number")
                        })?
                        .unwrap_or(0.0),
                    gain: get_f32(c, "gain").max(0.0),
                    pan: get_f32(c, "pan"),
                    pitch_semitones: get_f32(c, "pitch_semitones"),
                    fine_cents: get_f32(c, "fine_cents"),
                    reverse: c
                        .get_item("reverse")?
                        .and_then(|v| v.extract::<bool>().ok())
                        .unwrap_or(false),
                    muted: c
                        .get_item("muted")?
                        .and_then(|v| v.extract::<bool>().ok())
                        .unwrap_or(false),
                });
            }
        }
        tracks.push(RawTrack {
            name: t
                .get_item("name")?
                .map(|v| v.extract::<String>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'name' must be a string"))?
                .unwrap_or_else(|| "Track".to_string()),
            gain: t
                .get_item("gain")?
                .map(|v| v.extract::<f32>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'gain' must be a number"))?
                .unwrap_or(1.0),
            pan: t
                .get_item("pan")?
                .map(|v| v.extract::<f32>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'pan' must be a number"))?
                .unwrap_or(0.0),
            muted: t
                .get_item("muted")?
                .map(|v| v.extract::<bool>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'muted' must be a bool"))?
                .unwrap_or(false),
            vel_track: t
                .get_item("vel_track")?
                .map(|v| v.extract::<f32>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'vel_track' must be a number"))?
                .unwrap_or(0.0),
            vel_track_mid: t
                .get_item("vel_track_mid")?
                .map(|v| v.extract::<f32>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'vel_track_mid' must be a number"))?
                .unwrap_or(0.5),
            key_track: t
                .get_item("key_track")?
                .map(|v| v.extract::<f32>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'key_track' must be a number"))?
                .unwrap_or(0.0),
            key_track_mid: t
                .get_item("key_track_mid")?
                .map(|v| v.extract::<f32>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'key_track_mid' must be a number"))?
                .unwrap_or(60.0),
            effects,
            clips,
            audio_clips,
            automation,
            generator_layers: parse_generator_layers(t)?,
            layer_mode: t
                .get_item("layer_mode")?
                .map(|v| v.extract::<String>())
                .transpose()
                .map_err(|_| PyValueError::new_err("'layer_mode' must be a string"))?
                .unwrap_or_else(|| "all".to_string()),
            sends: {
                let mut sends = Vec::new();
                if let Some(s_any) = t.get_item("sends")? {
                    let send_list: Vec<Bound<'_, PyDict>> = s_any
                        .extract()
                        .map_err(|_| PyValueError::new_err("'sends' must be a list"))?;
                    for s in send_list.iter() {
                        let to_id: String = s
                            .get_item("to")?
                            .map(|v| v.extract::<String>())
                            .transpose()
                            .map_err(|_| PyValueError::new_err("'to' must be a string"))?
                            .unwrap_or_default();
                        let amount: f32 = s
                            .get_item("amount")?
                            .map(|v| v.extract::<f32>())
                            .transpose()
                            .map_err(|_| PyValueError::new_err("'amount' must be a number"))?
                            .unwrap_or(0.5);
                        let tap = match s
                            .get_item("tap")?
                            .map(|v| v.extract::<String>())
                            .transpose()
                            .map_err(|_| PyValueError::new_err("'tap' must be a string"))?
                            .as_deref()
                        {
                            None | Some("post") => SendTap::Post,
                            Some("pre") => SendTap::Pre,
                            Some(other) => {
                                return Err(PyValueError::new_err(format!(
                                    "unknown send tap '{}' (expected 'pre' or 'post')",
                                    other
                                )))
                            }
                        };
                        let pan: f32 = s
                            .get_item("pan")?
                            .map(|v| v.extract::<f32>())
                            .transpose()
                            .map_err(|_| PyValueError::new_err("'pan' must be a number"))?
                            .unwrap_or(0.0);
                        let sidechain: bool = s
                            .get_item("sidechain")?
                            .map(|v| v.extract::<bool>())
                            .transpose()
                            .map_err(|_| PyValueError::new_err("'sidechain' must be a bool"))?
                            .unwrap_or(false);
                        if let Some(idx) = track_ids.iter().position(|id| *id == to_id) {
                            sends.push(SendData {
                                to_track: idx,
                                amount: amount.clamp(0.0, 1.0),
                                tap,
                                pan: pan.clamp(-1.0, 1.0),
                                sidechain,
                            });
                        }
                        // Unknown destinations are skipped (Python validates).
                    }
                }
                sends
            },
            output: {
                // Exclusive output route: track id, "master", or absent.
                // Unknown ids fall back to Master (Python validates).
                let out_id: Option<String> = t
                    .get_item("output")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'output' must be a string"))?;
                match out_id.as_deref() {
                    None | Some("master") => None,
                    Some(id) => track_ids.iter().position(|tid| tid == id),
                }
            },
            modulators: parse_modulators(t.get_item("modulators")?)?,
        });
    }

    let sample_ids: Vec<String> = sample_refs.iter().map(|(id, _)| id.clone()).collect();
    let data = Engine::make_arrangement(tempo, truncate_notes, patterns, tracks, sample_ids)
        .map_err(PyValueError::new_err)?;
    Ok((data, sample_refs))
}

/// Parse a track's modulators list.
fn parse_modulators(mods: Option<Bound<'_, PyAny>>) -> PyResult<Vec<engine::RawModulator>> {
    let Some(mods) = mods else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for m in mods.iter()? {
        let m = m?;
        let m: Bound<'_, PyDict> = m.extract()
            .map_err(|_| PyValueError::new_err("'modulators' entries must be objects"))?;

        let get_str = |key: &str, default: &str| -> PyResult<String> {
            m.get_item(key)?
                .map(|v| v.extract::<String>())
                .transpose()
                .map_err(|_| PyValueError::new_err(format!("'modulators.{key}' must be a string")))?
                .map(Ok)
                .unwrap_or_else(|| Ok(default.to_string()))
        };
        let get_f64 = |key: &str, default: f64| -> PyResult<f64> {
            Ok(m.get_item(key)?
                .map(|v| v.extract::<f64>())
                .transpose()
                .map_err(|_| PyValueError::new_err(format!("'modulators.{key}' must be a number")))?
                .unwrap_or(default))
        };

        let id = get_str("id", "")?;
        let name = get_str("name", "Modulator")?;
        let loop_enabled = m.get_item("loop_enabled")?
            .map(|v| v.extract::<bool>())
            .transpose()
            .map_err(|_| PyValueError::new_err("'modulators.loop_enabled' must be a bool"))?
            .unwrap_or(true);
        let length_bars = get_f64("length_bars", 1.0)?;
        let rate_mult = get_f64("rate_mult", 1.0)?;

        // Nodes: [[time_beats, value], ...]
        let mut nodes = Vec::new();
        if let Some(nl) = m.get_item("nodes")? {
            for n in nl.iter()? {
                let n = n?;
                let pair: Vec<f64> = n.extract()
                    .map_err(|_| PyValueError::new_err("'modulators.nodes' entries must be [time, value] pairs"))?;
                if pair.len() != 2 {
                    return Err(PyValueError::new_err("'modulators.nodes' entries must be [time, value] pairs"));
                }
                nodes.push((pair[0], pair[1].clamp(0.0, 1.0)));
            }
            nodes.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
        }

        // Assignments
        let mut assignments = Vec::new();
        if let Some(al) = m.get_item("assignments")? {
            for a in al.iter()? {
                let a = a?;
                let a: Bound<'_, PyDict> = a.extract()
                    .map_err(|_| PyValueError::new_err("'modulators.assignments' entries must be objects"))?;
                let target_kind: String = a.get_item("target_kind")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.target_kind' must be a string"))?
                    .unwrap_or_else(|| "fx".to_string());
                let target_index: usize = a.get_item("target_index")?
                    .map(|v| v.extract::<usize>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.target_index' must be an integer"))?
                    .unwrap_or(0);
                let param_id: u32 = a.get_item("param_id")?
                    .map(|v| v.extract::<u32>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.param_id' must be an integer"))?
                    .unwrap_or(0);
                let amount: f64 = a.get_item("amount")?
                    .map(|v| v.extract::<f64>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.amount' must be a number"))?
                    .unwrap_or(0.5);
                let polarity: String = a.get_item("polarity")?
                    .map(|v| v.extract::<String>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.polarity' must be a string"))?
                    .unwrap_or_else(|| "positive".to_string());
                let param_min: f64 = a.get_item("param_min")?
                    .map(|v| v.extract::<f64>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.param_min' must be a number"))?
                    .unwrap_or(0.0);
                let param_max: f64 = a.get_item("param_max")?
                    .map(|v| v.extract::<f64>())
                    .transpose()
                    .map_err(|_| PyValueError::new_err("'assignment.param_max' must be a number"))?
                    .unwrap_or(1.0);
                assignments.push(engine::RawModAssignment {
                    is_fx: target_kind == "fx",
                    target_index,
                    param_id,
                    amount: amount.clamp(0.0, 1.0),
                    polarity,
                    param_min,
                    param_max,
                });
            }
        }

        out.push(engine::RawModulator {
            id,
            name,
            nodes,
            loop_enabled,
            length_bars: length_bars.max(0.0625),
            rate_mult: rate_mult.max(0.0625),
            assignments,
        });
    }
    Ok(out)
}

/// Optional float field, defaulting to 0.0 when absent or mistyped.
/// Missing params are caught by Rust-side range validation.
fn get_f32(dict: &Bound<'_, PyDict>, key: &str) -> f32 {
    dict.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| v.extract::<f32>().ok())
        .unwrap_or(0.0)
}

/// Real-time audio engine handle (control thread side).
///
/// `unsendable`: the engine owns the CPAL output stream, which is `!Send` on
/// some platforms (e.g. ALSA). The handle is therefore main-thread-only;
/// all Python calls must come from the thread that created it. A future
/// iteration can move stream ownership to a dedicated Rust thread steered
/// via message passing.
#[pyclass(unsendable)]
struct PyEngine {
    inner: std::sync::Mutex<Engine>,
}

#[pymethods]
impl PyEngine {
    #[new]
    #[pyo3(signature = (sample_rate=44100))]
    fn new(sample_rate: u32) -> PyResult<Self> {
        let inner = Engine::new(sample_rate).map_err(err_to_py)?;
        Ok(PyEngine {
            inner: std::sync::Mutex::new(inner),
        })
    }

    /// Replace the whole arrangement snapshot (coarse-grained).
    ///
    /// Sample assets declared by the arrangement are decoded on demand:
    /// any asset id not already in the engine's registry is loaded from
    /// its path (control thread; the audio thread only ever sees `Arc`s
    /// to finished buffers). Re-sends are cheap: already-loaded ids are
    /// skipped.
    fn set_arrangement(&self, arrangement: &Bound<'_, PyDict>) -> PyResult<()> {
        let (data, sample_refs) = parse_arrangement(arrangement)?;
        let mut engine = self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?;
        for (id, path) in &sample_refs {
            if !engine.has_sample(id) {
                engine.load_sample(id, path).map_err(err_to_py)?;
            }
        }
        engine.set_arrangement(data).map_err(err_to_py)
    }

    fn set_tempo(&self, bpm: f64) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .set_tempo(bpm)
            .map_err(err_to_py)
    }

    fn set_multithreaded(&self, enabled: bool) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .set_multithreaded(enabled);
        Ok(())
    }

    /// Decode an audio file into the engine's sample registry.
    /// Returns {"frames", "duration_secs", "source_sample_rate"}.
    /// Idempotent: re-loading an id replaces its buffer.
    fn load_sample(&self, asset_id: String, path: String) -> PyResult<PyObject> {
        let info = self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .load_sample(&asset_id, &path)
            .map_err(err_to_py)?;
        Python::with_gil(|py| {
            let d = PyDict::new_bound(py);
            d.set_item("frames", info.frames)?;
            d.set_item("duration_secs", info.duration_secs)?;
            d.set_item("source_sample_rate", info.source_sample_rate)?;
            d.set_item("sample_rate", info.sample_rate)?;
            Ok(d.into())
        })
    }

    /// Drop an asset from the registry. In-flight snapshots keep working.
    fn unload_sample(&self, asset_id: String) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .unload_sample(&asset_id);
        Ok(())
    }

    /// Waveform peak pairs for display: `n` (min, max) buckets.
    fn sample_peaks(&self, asset_id: String, n: usize) -> PyResult<Vec<(f32, f32)>> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .sample_peaks(&asset_id, n)
            .map_err(err_to_py)
    }

    fn play(&self) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .play()
            .map_err(err_to_py)
    }

    fn stop(&self) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .stop();
        Ok(())
    }

    fn is_playing(&self) -> PyResult<bool> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .is_playing())
    }

    fn position_beats(&self) -> PyResult<f64> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .position_beats())
    }

    /// Per-track peak levels since the last call (peak-hold, then reset).
    /// For the Debug window's meters (infographic section 1).
    fn debug_peaks(&self) -> PyResult<Vec<f32>> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .debug_state()
            .take_peaks())
    }

    /// Per-track active voice/note counts (infographic section 2).
    fn debug_voices(&self) -> PyResult<Vec<u32>> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .debug_state()
            .voice_counts())
    }

    /// Drain recorded note events: (track, kind, key, note_id, sample).
    /// kind is "on", "off", or "choke" (infographic section 4).
    fn debug_events(&self) -> PyResult<Vec<(u16, String, u8, i32, u64)>> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .debug_state()
            .drain_events()
            .into_iter()
            .map(|e| {
                (
                    e.track,
                    e.kind.as_str().to_string(),
                    e.key,
                    e.note_id,
                    e.sample,
                )
            })
            .collect())
    }

    fn backend_name(&self) -> PyResult<String> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .backend_name())
    }

    /// List audio outputs as (host_id, host_name, device_name, is_default).
    /// Never opens a stream; safe to call any time.
    fn audio_devices(&self) -> PyResult<Vec<(String, String, String, bool)>> {
        let devs = self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .audio_devices()
            .map_err(err_to_py)?;
        Ok(devs
            .into_iter()
            .map(|d| (d.host_id, d.host_name, d.device_name, d.is_default))
            .collect())
    }

    /// Choose the audio output. None/None = "System default".
    /// Takes effect on the next backend build; call
    /// `reapply_audio_backend` to switch live.
    #[pyo3(signature = (host_id=None, device_name=None))]
    fn set_audio_output(
        &self,
        host_id: Option<String>,
        device_name: Option<String>,
    ) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .set_audio_selection(host_id, device_name);
        Ok(())
    }

    /// The currently requested output as (host_id, device_name).
    fn audio_selection(&self) -> PyResult<(Option<String>, Option<String>)> {
        let sel = self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .audio_selection();
        Ok((sel.host_id, sel.device_name))
    }

    /// Set the audio buffer size in frames (None = driver default).
    /// Takes effect on the next backend build; call
    /// `reapply_audio_backend` to apply live.
    #[pyo3(signature = (frames=None))]
    fn set_buffer_frames(&self, frames: Option<u32>) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .set_buffer_frames(frames)
            .map_err(err_to_py)
    }

    /// The currently requested buffer size in frames (None = default).
    fn buffer_frames(&self) -> PyResult<Option<u32>> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .buffer_frames())
    }

    /// Every input device, as (host_id, host_name, device_name, is_default).
    fn audio_input_devices(&self) -> PyResult<Vec<(String, String, String, bool)>> {
        let devs = self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .audio_input_devices()
            .map_err(err_to_py)?;
        Ok(devs
            .into_iter()
            .map(|d| (d.host_id, d.host_name, d.device_name, d.is_default))
            .collect())
    }

    /// Remember the user's chosen input device (None/None = default).
    /// Stored + persisted; no input stream is opened in this version.
    #[pyo3(signature = (host_id=None, device_name=None))]
    fn set_audio_input(
        &self,
        host_id: Option<String>,
        device_name: Option<String>,
    ) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .set_audio_input(host_id, device_name);
        Ok(())
    }

    /// The currently requested input as (host_id, device_name).
    fn audio_input(&self) -> PyResult<(Option<String>, Option<String>)> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .audio_input())
    }

    /// Live backend counters: (callbacks, max_callback_us, underruns,
    /// block_frames). The UI renders FL Studio's CPU-meter metric from
    /// these: max render time as a percentage of the buffer deadline.
    fn backend_stats(&self) -> PyResult<(u64, u64, u64, u64)> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .backend_stats())
    }

    /// Re-open the backend on the selected device. Keeps playing (position
    /// preserved) if the transport was running.
    fn reapply_audio_backend(&self) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .reapply_audio_backend()
            .map_err(err_to_py)
    }

    /// Offline deterministic render. Holds the GIL; a few loops render in
    /// tens of milliseconds, so this stays responsive for UI use.
    fn render_wav(&self, path: &str, loops: u32) -> PyResult<()> {
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .render_wav(path, loops)
            .map_err(err_to_py)
    }

    fn stats(&self) -> PyResult<PyObject> {
        let guard = self
            .inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?;
        let s = guard.stats_snapshot();
        Python::with_gil(|py| {
            let d = PyDict::new_bound(py);
            d.set_item("callbacks", s.callbacks)?;
            d.set_item("max_callback_us", s.max_callback_us)?;
            d.set_item("underruns", s.underruns)?;
            d.set_item("sample_rate", s.sample_rate)?;
            d.set_item("backend", s.backend)?;
            d.set_item("playing", s.playing)?;
            Ok(d.into())
        })
    }

    /// Scan the CLAP search paths for audio-effect plugins. Returns a
    /// list of {"path", "id", "name", "vendor"} dicts. Runs on the
    /// calling thread; never touches the audio graph.
    fn scan_clap_plugins(&self) -> Vec<HashMap<String, String>> {
        crate::plugins::scan_clap_plugins()
            .into_iter()
            .map(|p| {
                let mut m = HashMap::new();
                m.insert("path".to_string(), p.path.display().to_string());
                m.insert("id".to_string(), p.id);
                m.insert("name".to_string(), p.name);
                m.insert("vendor".to_string(), p.vendor);
                m
            })
            .collect()
    }

    /// Scan the standard VST3 locations. Returns [{name, path, plugin_id,
    /// format}] dicts. Runs on the calling thread; never touches audio.
    fn scan_vst3_plugins(&self) -> Vec<HashMap<String, String>> {
        crate::vst3::scan_vst3_plugins()
    }

    /// Get the parameter list for a VST3 plugin: [{id, name, default}].
    /// Loads the plugin briefly on the calling thread.
    fn vst3_plugin_params(
        &self,
        path: &str,
    ) -> PyResult<Vec<HashMap<String, PyObject>>> {
        let params = crate::vst3::vst3_plugin_params(std::path::Path::new(path))
            .map_err(err_to_py)?;
        Python::with_gil(|py| {
            params
                .into_iter()
                .map(|(id, name, default)| {
                    let mut m = HashMap::new();
                    m.insert("id".to_string(), id.into_py(py));
                    m.insert("name".to_string(), name.into_py(py));
                    // VST3 params are normalized 0.0-1.0.
                    m.insert("min".to_string(), 0.0f64.into_py(py));
                    m.insert("max".to_string(), 1.0f64.into_py(py));
                    m.insert("default".to_string(), default.into_py(py));
                    Ok(m)
                })
                .collect()
        })
    }

    /// Fully load and start a VST3 plugin, then drop it. Used by the UI
    /// to validate a plugin *before* adding it to a track.
    fn check_vst3_plugin(&self, path: &str) -> PyResult<()> {
        let sample_rate = self.inner.lock().map(|e| e.sample_rate()).unwrap_or(44100);
        crate::vst3::HostedVst3Plugin::load(
            std::path::Path::new(path),
            sample_rate,
            &[],
        )
        .map(|_| ())
        .map_err(err_to_py)
    }

    /// Fully load and start a VST3 plugin as an instrument (stereo out,
    /// MIDI notes), then drop it. Used by the UI to validate a VST3
    /// instrument *before* adding it as a generator layer.
    fn check_vst3_instrument(&self, path: &str) -> PyResult<()> {
        let sample_rate = self.inner.lock().map(|e| e.sample_rate()).unwrap_or(44100);
        crate::vst3::HostedVst3Instrument::load(
            std::path::Path::new(path),
            sample_rate,
            &[],
        )
        .map(|_| ())
        .map_err(err_to_py)
    }

    /// Save every live plugin's opaque CLAP state blob. Returns a list of
    /// {"track", "fx_index", "layer_index", "plugin_id", "state_base64"}
    /// dicts (`fx_index`/`layer_index` are None when not applicable).
    /// Call this on the control thread at project-save time; the blobs
    /// are stored Base64-encoded in the JSON project (a host
    /// serialization decision — CLAP itself only defines the opaque
    /// byte stream). Plugins without the state extension are omitted
    /// (their params are saved as before).
    fn save_plugin_states(&self) -> PyResult<Vec<HashMap<String, String>>> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        Ok(inner
            .save_plugin_states()
            .into_iter()
            .map(|(track, fx_index, layer_index, plugin_id, state_base64)| {
                let mut m = HashMap::new();
                m.insert("track".to_string(), track.to_string());
                m.insert(
                    "fx_index".to_string(),
                    fx_index.map(|i| i.to_string()).unwrap_or_default(),
                );
                m.insert(
                    "layer_index".to_string(),
                    layer_index.map(|i| i.to_string()).unwrap_or_default(),
                );
                m.insert("plugin_id".to_string(), plugin_id);
                m.insert("state_base64".to_string(), state_base64);
                m
            })
            .collect())
    }

    /// Drain plugin slots that called `mark_dirty()` since the last
    /// call. Returns [{\"track\", \"fx_index\", \"layer_index\"}] with
    /// None rendered as \"\". The UI polls this and marks the project
    /// dirty: non-parameter plugin state changed outside the host's
    /// view, so the project needs saving again.
    fn take_plugin_dirty_slots(&self) -> PyResult<Vec<HashMap<String, String>>> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        Ok(inner
            .take_plugin_dirty_slots()
            .into_iter()
            .map(|key| {
                let mut m = HashMap::new();
                match key {
                    crate::plugins::PluginSlotKey::Fx { track, index } => {
                        m.insert("track".to_string(), track.to_string());
                        m.insert("fx_index".to_string(), index.to_string());
                        m.insert("layer_index".to_string(), String::new());
                    }
                    crate::plugins::PluginSlotKey::Instrument { track, layer } => {
                        m.insert("track".to_string(), track.to_string());
                        m.insert("fx_index".to_string(), String::new());
                        m.insert("layer_index".to_string(), layer.to_string());
                    }
                }
                m
            })
            .collect())
    }

    /// Drain plugin restart requests (`request_restart()`) and
    /// latency-change notifications (`latency.changed()`) since the
    /// last call. Returns [{"kind", "track", "fx_index",
    /// "layer_index"}]; kind is "restart" or "latency_changed". The UI
    /// polls this and services the requests with
    /// `process_plugin_restarts()`.
    fn take_restart_requests(&self) -> PyResult<Vec<HashMap<String, String>>> {
        let inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        Ok(inner
            .take_restart_requests()
            .into_iter()
            .map(|(kind, key)| {
                let mut m = HashMap::new();
                m.insert("kind".to_string(), kind);
                match key {
                    crate::plugins::PluginSlotKey::Fx { track, index } => {
                        m.insert("track".to_string(), track.to_string());
                        m.insert("fx_index".to_string(), index.to_string());
                        m.insert("layer_index".to_string(), String::new());
                    }
                    crate::plugins::PluginSlotKey::Instrument { track, layer } => {
                        m.insert("track".to_string(), track.to_string());
                        m.insert("fx_index".to_string(), String::new());
                        m.insert("layer_index".to_string(), layer.to_string());
                    }
                }
                m
            })
            .collect())
    }

    /// Service pending plugin restart requests: restart each
    /// affected slot (state preserved via FOR_DUPLICATE), re-query
    /// latency post-activation, recalculate PDC. Returns the
    /// restarted slots as [{"track", "fx_index", "layer_index"}].
    fn process_plugin_restarts(&self) -> PyResult<Vec<HashMap<String, String>>> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        inner
            .process_plugin_restarts()
            .map(|slots| {
                slots
                    .into_iter()
                    .map(|key| {
                        let mut m = HashMap::new();
                        match key {
                            crate::plugins::PluginSlotKey::Fx { track, index } => {
                                m.insert("track".to_string(), track.to_string());
                                m.insert("fx_index".to_string(), index.to_string());
                                m.insert("layer_index".to_string(), String::new());
                            }
                            crate::plugins::PluginSlotKey::Instrument { track, layer } => {
                                m.insert("track".to_string(), track.to_string());
                                m.insert("fx_index".to_string(), String::new());
                                m.insert("layer_index".to_string(), layer.to_string());
                            }
                        }
                        m
                    })
                    .collect()
            })
            .map_err(err_to_py)
    }

    /// The plugin's currently reported latency in samples for a
    /// slot (None when the slot has no live instance). This is the
    /// value PDC compensates for — the same number FL Studio's
    /// Wrapper shows as detected plugin latency.
    #[pyo3(signature = (track, fx_index=None, layer_index=None))]
    fn plugin_latency_samples(
        &self,
        track: usize,
        fx_index: Option<usize>,
        layer_index: Option<usize>,
    ) -> PyResult<Option<u32>> {
        let slot = slot_key(track, fx_index, layer_index)?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        Ok(inner.plugin_latency_samples(slot))
    }

    /// Drain preset-load `loaded()` / `on_error()` reports since the
    /// last call. Returns {\"loaded\": [{\"track\", \"fx_index\",
    /// \"layer_index\", \"location\", \"load_key\"}], \"errors\":
    /// [{\"track\", ..., \"location\", \"os_error\", \"message\"}]}.
    /// The UI uses these to keep its preset browser in sync with the
    /// plugin's own preset state.
    fn take_preset_events(&self) -> PyResult<HashMap<String, Vec<HashMap<String, String>>>> {
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        let (loaded, errors) = inner.take_preset_events();
        fn slot_fields(key: &Option<crate::plugins::PluginSlotKey>) -> (String, String, String) {
            match key {
                Some(crate::plugins::PluginSlotKey::Fx { track, index }) => {
                    (track.to_string(), index.to_string(), String::new())
                }
                Some(crate::plugins::PluginSlotKey::Instrument { track, layer }) => {
                    (track.to_string(), String::new(), layer.to_string())
                }
                None => (String::new(), String::new(), String::new()),
            }
        }
        let loaded = loaded
            .into_iter()
            .map(|e| {
                let (track, fx_index, layer_index) = slot_fields(&e.slot);
                let mut m = HashMap::new();
                m.insert("track".to_string(), track);
                m.insert("fx_index".to_string(), fx_index);
                m.insert("layer_index".to_string(), layer_index);
                m.insert("location".to_string(), e.location);
                m.insert("load_key".to_string(), e.load_key.unwrap_or_default());
                m
            })
            .collect();
        let errors = errors
            .into_iter()
            .map(|e| {
                let (track, fx_index, layer_index) = slot_fields(&e.slot);
                let mut m = HashMap::new();
                m.insert("track".to_string(), track);
                m.insert("fx_index".to_string(), fx_index);
                m.insert("layer_index".to_string(), layer_index);
                m.insert("location".to_string(), e.location);
                m.insert("os_error".to_string(), e.os_error.to_string());
                m.insert("message".to_string(), e.message);
                m
            })
            .collect();
        let mut out = HashMap::new();
        out.insert("loaded".to_string(), loaded);
        out.insert("errors".to_string(), errors);
        Ok(out)
    }

    /// Save one slot's plugin state with the FOR_PRESET state context
    /// ("save my state as a reusable preset"). Returns the blob as
    /// Base64; the Python side writes the preset file.
    #[pyo3(signature = (track, fx_index=None, layer_index=None))]
    fn save_plugin_preset_blob(
        &self,
        track: usize,
        fx_index: Option<usize>,
        layer_index: Option<usize>,
    ) -> PyResult<String> {
        let slot = slot_key(track, fx_index, layer_index)?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        inner
            .save_plugin_preset_blob(slot)
            .map(|b| crate::plugins::base64_encode(&b))
            .map_err(err_to_py)
    }

    /// Load a preset blob (from `save_plugin_preset_blob`) into the
    /// slot's plugin with the FOR_PRESET context. Returns {\"params\":
    /// {str(clap_id): value}, \"state_base64\": str} — the caller
    /// stores both in the project in one undoable edit. Rescanned
    /// values are NOT automation (per CLAP).
    #[pyo3(signature = (track, fx_index=None, layer_index=None, *, state_base64))]
    fn load_plugin_preset_blob(
        &self,
        track: usize,
        fx_index: Option<usize>,
        layer_index: Option<usize>,
        state_base64: &str,
    ) -> PyResult<HashMap<String, PyObject>> {
        let slot = slot_key(track, fx_index, layer_index)?;
        let blob = crate::plugins::base64_decode(state_base64).map_err(err_to_py)?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        let (params, blob_b64) = inner
            .load_plugin_preset_blob(slot, &blob)
            .map_err(err_to_py)?;
        Python::with_gil(|py| {
            let mut m = HashMap::new();
            let pd: HashMap<String, PyObject> = params
                .into_iter()
                .map(|(id, v)| (id.to_string(), v.into_py(py)))
                .collect();
            m.insert("params".to_string(), pd.into_py(py));
            m.insert("state_base64".to_string(), blob_b64.into_py(py));
            Ok(m)
        })
    }

    /// Does the slot's plugin implement `CLAP_EXT_PRESET_LOAD`
    /// (native preset files via `from_location()`)?
    #[pyo3(signature = (track, fx_index=None, layer_index=None))]
    fn plugin_supports_preset_load(
        &self,
        track: usize,
        fx_index: Option<usize>,
        layer_index: Option<usize>,
    ) -> PyResult<bool> {
        let slot = slot_key(track, fx_index, layer_index)?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        Ok(inner.plugin_supports_preset_load(slot))
    }

    /// Ask the slot's plugin to load one of its *native* preset files
    /// (`CLAP_EXT_PRESET_LOAD from_location()`). `load_key` selects a
    /// preset inside a container file (None for plain files). Returns
    /// {\"params\": {...}, \"state_base64\": str} like
    /// `load_plugin_preset_blob` — store both in one undoable edit.
    #[pyo3(signature = (track, fx_index=None, layer_index=None, *, path, load_key=None))]
    fn plugin_preset_from_location(
        &self,
        track: usize,
        fx_index: Option<usize>,
        layer_index: Option<usize>,
        path: &str,
        load_key: Option<String>,
    ) -> PyResult<HashMap<String, PyObject>> {
        let slot = slot_key(track, fx_index, layer_index)?;
        let mut inner = self
            .inner
            .lock()
            .map_err(|_| PyValueError::new_err("engine lock poisoned"))?;
        let (params, blob_b64) = inner
            .plugin_preset_from_location(slot, path, load_key.as_deref())
            .map_err(err_to_py)?;
        Python::with_gil(|py| {
            let mut m = HashMap::new();
            let pd: HashMap<String, PyObject> = params
                .into_iter()
                .map(|(id, v)| (id.to_string(), v.into_py(py)))
                .collect();
            m.insert("params".to_string(), pd.into_py(py));
            m.insert("state_base64".to_string(), blob_b64.into_py(py));
            Ok(m)
        })
    }
    /// List a plugin's parameters: [{"id", "name", "min", "max",
    /// "default"}]. Instantiates the plugin briefly on the calling
    /// thread; the audio graph is not involved.
    fn clap_plugin_params(
        &self,
        path: &str,
        plugin_id: &str,
    ) -> PyResult<Vec<HashMap<String, PyObject>>> {
        let params = crate::plugins::clap_plugin_params(std::path::Path::new(path), plugin_id)
            .map_err(err_to_py)?;
        Python::with_gil(|py| {
            params
                .into_iter()
                .map(|p| {
                    let mut m = HashMap::new();
                    m.insert("id".to_string(), p.id.into_py(py));
                    m.insert("name".to_string(), p.name.into_py(py));
                    m.insert("min".to_string(), p.min.into_py(py));
                    m.insert("max".to_string(), p.max.into_py(py));
                    m.insert("default".to_string(), p.default.into_py(py));
                    Ok(m)
                })
                .collect()
        })
    }

    /// Fully load, activate and start a plugin, then drop it. Used by
    /// the UI to validate a plugin *before* adding it to a track, so a
    /// broken plugin fails loudly in the picker instead of silently in
    /// the audio graph.
    fn check_clap_plugin(&self, path: &str, plugin_id: &str) -> PyResult<()> {
        let sample_rate = self.inner.lock().map(|e| e.sample_rate()).unwrap_or(44100);
        crate::plugins::HostedClapPlugin::load(
            std::path::Path::new(path),
            plugin_id,
            sample_rate,
            &[],
            None,
            None,
            &crate::plugins::PluginEventSinks::default(),
        )
        .map(|_| ())
        .map_err(err_to_py)
    }

    /// Scan the CLAP search paths for instrument plugins. Returns a
    /// list of {"path", "id", "name", "vendor"} dicts.
    fn scan_clap_instruments(&self) -> Vec<HashMap<String, String>> {
        crate::plugins::scan_clap_instruments()
            .into_iter()
            .map(|p| {
                let mut m = HashMap::new();
                m.insert("path".to_string(), p.path.display().to_string());
                m.insert("id".to_string(), p.id);
                m.insert("name".to_string(), p.name);
                m.insert("vendor".to_string(), p.vendor);
                m
            })
            .collect()
    }

    /// Fully load, activate and start an instrument plugin, then drop it.
    /// Used by the UI to validate before adding as a track generator.
    fn check_clap_instrument(&self, path: &str, plugin_id: &str) -> PyResult<()> {
        let sample_rate = self.inner.lock().map(|e| e.sample_rate()).unwrap_or(44100);
        crate::plugins::HostedClapInstrument::load(
            std::path::Path::new(path),
            plugin_id,
            sample_rate,
            &[],
            None,
            None,
            &crate::plugins::PluginEventSinks::default(),
        )
        .map(|_| ())
        .map_err(err_to_py)
    }

    /// Open a plugin's native GUI as a floating window. `params` is a
    /// dict of {str(clap_id): value} flushed into the GUI instance so it
    /// reflects the project. Returns a GUI id, or raises if the plugin
    /// has no usable GUI. The GUI instance is GUI-only; it never
    /// processes audio. Runs on the calling (control) thread.
    fn open_plugin_gui(
        &self,
        path: &str,
        plugin_id: &str,
        params: HashMap<String, f64>,
    ) -> PyResult<u64> {
        let pairs: Vec<(u32, f64)> = params
            .iter()
            .filter_map(|(k, v)| k.parse::<u32>().ok().map(|id| (id, *v)))
            .collect();
        self.inner
            .lock()
            .map_err(|_| err_to_py("engine lock poisoned".to_string()))?
            .open_plugin_gui(std::path::Path::new(path), plugin_id, &pairs)
            .map_err(err_to_py)
    }

    /// Read current parameter values from an open plugin GUI as
    /// {str(clap_id): value}. The UI polls this while the GUI is open to
    /// sync user tweaks back to the project/audio.
    fn plugin_gui_params(&self, gui_id: u64) -> HashMap<String, f64> {
        let pairs = self
            .inner
            .lock()
            .map(|mut e| e.plugin_gui_params(gui_id))
            .unwrap_or_default();
        pairs
            .into_iter()
            .map(|(id, v)| (id.to_string(), v))
            .collect()
    }

    /// Hide and destroy an open plugin GUI. Idempotent.
    fn close_plugin_gui(&self, gui_id: u64) {
        if let Ok(mut e) = self.inner.lock() {
            e.close_plugin_gui(gui_id);
        }
    }
}

/// Block size for the standalone offline render (matches Engine::render_wav).
const RENDER_BLOCK_FRAMES: usize = 512;

/// Offline render of an arrangement dict to a WAV file, without holding
/// the GIL.
///
/// Unlike `PyEngine::render_wav` — a method on the unsendable engine handle
/// that must run on the creating thread and holds the GIL for the whole
/// render — this is a plain module-level function over immutable data, so
/// Python can call it on a worker thread while the UI stays alive. The GIL
/// is released during DSP; `progress(done_blocks, total_blocks)` is invoked
/// (GIL reacquired) after every block. If it returns a falsy value the
/// render aborts and this returns `Ok(false)`; the caller should delete
/// the partial file. Returns `Ok(true)` once the file is fully written.
#[pyfunction]
fn render_wav_file(
    py: Python<'_>,
    arrangement: &Bound<'_, PyDict>,
    sample_rate: u32,
    path: &str,
    loops: u32,
    progress: PyObject,
) -> PyResult<bool> {
    if loops == 0 || loops > 1024 {
        return Err(PyValueError::new_err("loops must be 1-1024"));
    }
    let (data, sample_refs) = parse_arrangement(arrangement)?;
    // Decode declared samples (worker thread; the GIL is released below
    // during DSP). Missing files are a hard error, like the engine path.
    let mut registry: std::collections::HashMap<
        String,
        std::sync::Arc<crate::sample::SampleBuffer>,
    > = std::collections::HashMap::new();
    for (id, path) in &sample_refs {
        let buffer = crate::sample::decode_file(std::path::Path::new(path), sample_rate)
            .map_err(PyValueError::new_err)?;
        registry.insert(id.clone(), std::sync::Arc::new(buffer));
    }
    let song = std::sync::Arc::new(
        timeline::Song::from_arrangement(sample_rate, &data, &registry)
            .map_err(PyValueError::new_err)?,
    );
    let total_frames = song.loop_samples as usize * loops as usize;
    let total_blocks = total_frames.div_ceil(RENDER_BLOCK_FRAMES);
    let path = path.to_string();

    let result: Result<bool, String> = py.allow_threads(|| {
        let slot = std::sync::Arc::new(std::sync::RwLock::new(song.clone()));
        let debug = std::sync::Arc::new(crate::debug::DebugState::new(
            crate::debug::MAX_DEBUG_TRACKS,
        ));
        let mut sink =
            crate::plugins::PluginInstanceSink::new(crate::plugins::PluginEventSinks::default());
        let mut core = engine::AudioCore::new(slot, song.clone(), debug, &mut sink);
        let control = engine::Control::new();
        control
            .playing
            .store(true, std::sync::atomic::Ordering::Release);

        let mut out = vec![0.0f32; total_frames * 2];
        for (i, chunk) in out.chunks_mut(RENDER_BLOCK_FRAMES * 2).enumerate() {
            core.render_block(chunk, &control);
            let keep_going = Python::with_gil(|py| {
                progress
                    .call1(py, (i + 1, total_blocks))
                    .and_then(|v| v.extract::<bool>(py))
                    .unwrap_or(true)
            });
            if !keep_going {
                return Ok(false);
            }
        }
        wav::write_wav_stereo(&path, sample_rate, &out)
            .map(|_| true)
            .map_err(|e| format!("failed to write {}: {}", path, e))
    });
    result.map_err(PyRuntimeError::new_err)
}

/// Python module: `daw._daw_engine_rs` (imported as a submodule of the
/// `daw` package; see python/daw/engine_bridge.py).
#[pymodule]
fn _daw_engine_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyEngine>()?;
    m.add_function(wrap_pyfunction!(render_wav_file, m)?)?;
    m.add("__version__", "0.2.0")?;
    Ok(())
}
