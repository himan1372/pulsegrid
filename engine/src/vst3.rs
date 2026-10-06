//! VST3 plugin hosting for audio effects.
//!
//! ARCHITECTURE (v0.45.0): Philip reversed the CLAP-only decision to load
//! VST3 plugins (notably NI Absynth for his .nabs presets, plus free VST3s).
//! VST3 hosting lives alongside CLAP hosting: same lifecycle model
//! (load -> process blocks -> save state), same stereo in/out contract,
//! same parameter-queue pattern. The `vst3-host` crate (0.9.0) provides the
//! safe hosting layer over the raw VST3 COM API.
//!
//! V1 scope, stated honestly (mirrors the CLAP v1 scope):
//! * Audio-effect plugins only. Instruments need note routing, which is a
//!   separate project (the crate supports MIDI, but Pulsegrid's engine
//!   doesn't route notes to VST3 yet).
//! * Stereo in / stereo out. Plugins without a stereo pair are rejected
//!   at load with a clear error.
//! * No plugin GUIs in v1 -- embedding foreign windows is out of scope;
//!   Pulsegrid shows a generic parameter editor instead. (The crate has
//!   `embed`/`window` modules for a future version.)
//! * Plugin state: VST3 state is saved/restored via the crate's state
//!   support when available; parameter values are ALSO saved as fallback.
//! * Plugin libraries stay loaded for the session once used; per-instance
//!   data is freed when the effect is removed.
//!
//! Threading: mirrors the CLAP contract. `HostedVst3Plugin` is created on
//! the control thread; `process_block` runs on the audio/render thread.
//! Parameter changes are queued and flushed on the next block.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use vst3_host::prelude::*;
use vst3_host::{AudioBuffers, Plugin, Vst3Host};

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/// Standard VST3 search paths per platform.
fn vst3_search_paths() -> Vec<PathBuf> {
    let mut paths = Vec::new();
    #[cfg(target_os = "windows")]
    {
        if let Ok(pf) = std::env::var("ProgramFiles") {
            paths.push(PathBuf::from(pf).join("Common Files").join("VST3"));
        }
        if let Ok(pf86) = std::env::var("ProgramFiles(x86)") {
            paths.push(PathBuf::from(pf86).join("Common Files").join("VST3"));
        }
    }
    #[cfg(target_os = "macos")]
    {
        if let Ok(home) = std::env::var("HOME") {
            paths.push(
                PathBuf::from(home)
                    .join("Library")
                    .join("Audio")
                    .join("Plug-Ins")
                    .join("VST3"),
            );
        }
        paths.push(PathBuf::from("/Library/Audio/Plug-Ins/VST3"));
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(home) = std::env::var("HOME") {
            paths.push(PathBuf::from(home).join(".vst3"));
        }
        paths.push(PathBuf::from("/usr/lib/vst3"));
        paths.push(PathBuf::from("/usr/local/lib/vst3"));
    }
    paths.into_iter().filter(|p| p.is_dir()).collect()
}

/// Find a VST3 plugin's path by id (file stem or full path).
/// Searches the standard VST3 locations.
pub fn find_vst3_path(plugin_id: &str) -> Option<PathBuf> {
    // Direct path first.
    let direct = PathBuf::from(plugin_id);
    if direct.is_file() {
        return Some(direct);
    }
    // Search by file stem.
    for dir in vst3_search_paths() {
        let entries = std::fs::read_dir(&dir).ok()?;
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().map(|e| e == "vst3").unwrap_or(false) {
                let stem = path
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_default();
                if stem == plugin_id
                    || path.to_string_lossy() == plugin_id
                {
                    return Some(path);
                }
            }
        }
    }
    None
}

/// Scan standard locations for VST3 plugins.
/// Returns [{name, path, plugin_id, format="vst3"}] dicts.
pub fn scan_vst3_plugins() -> Vec<HashMap<String, String>> {
    let mut out = Vec::new();
    for dir in vst3_search_paths() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let is_vst3 = path.extension().map(|e| e == "vst3").unwrap_or(false);
            if !is_vst3 {
                continue;
            }
            // Probe metadata without fully loading audio processing.
            let name = path
                .file_stem()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default();
            let mut m = HashMap::new();
            m.insert("name".into(), name);
            m.insert("path".into(), path.to_string_lossy().into_owned());
            m.insert("plugin_id".into(), path.to_string_lossy().into_owned());
            m.insert("format".into(), "vst3".into());
            out.push(m);
        }
    }
    out.sort_by(|a, b| a["name"].cmp(&b["name"]));
    out
}

// ---------------------------------------------------------------------------
// Hosted effect instance
// ---------------------------------------------------------------------------

/// A running VST3 audio effect: the loaded plugin (used on the audio
/// thread) plus scratch buffers. Takes stereo in, renders stereo out.
pub struct HostedVst3Plugin {
    plugin: Plugin,
    pending: Vec<(u32, f64)>,
    in_bufs: [Vec<f32>; 2],
    out_bufs: [Vec<f32>; 2],
    latency: u32,
    sample_rate: u32,
}

// The vst3-host Plugin is Send; everything else here is plain data.
unsafe impl Send for HostedVst3Plugin {}

impl HostedVst3Plugin {
    /// Load, instantiate and start a VST3 effect plugin.
    /// `params` are applied as initial values (normalized 0.0..=1.0).
    pub fn load(
        path: &Path,
        sample_rate: u32,
        params: &[(u32, f64)],
    ) -> Result<Self, String> {
        let mut host = Vst3Host::builder()
            .sample_rate(sample_rate as f64)
            .block_size(crate::graph::MAX_BLOCK_FRAMES as usize)
            .build()
            .map_err(|e| format!("cannot create VST3 host: {:?}", e))?;

        let mut plugin = host
            .load_plugin(path.to_string_lossy().as_ref())
            .map_err(|e| format!("cannot load VST3 '{}': {:?}", path.display(), e))?;

        // Require stereo in/out via bus arrangements.
        {
            let arrangements = plugin
                .bus_arrangements()
                .map_err(|e| format!("cannot query buses: {:?}", e))?;
            let stereo_in = arrangements
                .inputs
                .iter()
                .any(|b| b.channel_count() == 2);
            let stereo_out = arrangements
                .outputs
                .iter()
                .any(|b| b.channel_count() == 2);
            if !stereo_in || !stereo_out {
                return Err(format!(
                    "VST3 '{}': only stereo in/out is supported",
                    path.display()
                ));
            }
        }

        // Apply initial parameters before starting.
        for (id, value) in params {
            let v = (*value).clamp(0.0, 1.0);
            plugin
                .set_parameter(*id, v)
                .map_err(|e| format!("cannot set param {}: {:?}", id, e))?;
        }

        let latency = plugin.latency_samples();

        plugin
            .start_processing()
            .map_err(|e| format!("cannot start VST3 '{}': {:?}", path.display(), e))?;

        Ok(Self {
            plugin,
            pending: Vec::new(),
            in_bufs: [Vec::new(), Vec::new()],
            out_bufs: [Vec::new(), Vec::new()],
            latency,
            sample_rate,
        })
    }

    /// Queue a parameter change for the next audio block.
    pub fn queue_param(&mut self, id: u32, value: f64) {
        if let Some(slot) = self.pending.iter_mut().find(|(pid, _)| *pid == id) {
            slot.1 = value;
        } else {
            self.pending.push((id, value));
        }
    }

    /// Run one stereo block through the plugin (in place on `buf`,
    /// interleaved LRLR...). A failing plugin passes audio through.
    pub fn process_block(&mut self, buf: &mut [f32]) {
        let frames = buf.len() / 2;
        if frames == 0 {
            return;
        }
        for ch in 0..2 {
            self.in_bufs[ch].resize(frames, 0.0);
            self.out_bufs[ch].resize(frames, 0.0);
            for (i, s) in self.in_bufs[ch].iter_mut().enumerate() {
                *s = buf[2 * i + ch];
            }
        }

        // Flush queued parameter changes.
        for (id, value) in self.pending.drain(..) {
            let v = value.clamp(0.0, 1.0);
            let _ = self.plugin.set_parameter(id, v);
        }

        // VST3 wants an AudioBuffers struct with separate in/out.
        let mut buffers =
            AudioBuffers::new(2, 2, frames, self.sample_rate as f64);
        for ch in 0..2 {
            buffers.inputs[ch][..frames].copy_from_slice(&self.in_bufs[ch][..frames]);
        }

        let result = self.plugin.process_audio(&mut buffers);
        if result.is_err() {
            return; // pass through unchanged
        }

        for ch in 0..2 {
            for (i, s) in buffers.outputs[ch][..frames].iter().enumerate() {
                buf[2 * i + ch] = *s;
            }
        }
    }

    /// Reported latency in samples (for PDC). 0 if none.
    pub fn latency_samples(&self) -> u32 {
        self.latency
    }

    /// Enumerate parameters: (id, name, default_value).
    pub fn parameters(&self) -> Vec<(u32, String, f64)> {
        self.plugin
            .get_parameters()
            .map(|params| {
                params
                    .into_iter()
                    .map(|p| (p.id, p.name.clone(), p.default))
                    .collect()
            })
            .unwrap_or_default()
    }
}

// ---------------------------------------------------------------------------
// Metadata helpers (for UI probing without full instantiation)
// ---------------------------------------------------------------------------

/// Get parameter list for a VST3 plugin (loads it briefly).
pub fn vst3_plugin_params(path: &Path) -> Result<Vec<(u32, String, f64)>, String> {
    let p = HostedVst3Plugin::load(path, 44100, &[])?;
    Ok(p.parameters())
}

/// Validate that a VST3 plugin loads and has stereo I/O.
pub fn check_vst3_plugin(path: &Path) -> Result<(), String> {
    HostedVst3Plugin::load(path, 44100, &[])?;
    Ok(())
}
