//! Minimal CLAP instrument plugin for Pulsegrid's generator tests.
//!
//! A simple polyphonic synth: sine/square oscillator with an attack/
//! release envelope. Handles CLAP note_on/note_off events. Deterministic
//! and dependency-free apart from clack.

use std::ffi::CStr;

use clack_common::events::event_types::{NoteChokeEvent, ParamValueEvent};
use clack_common::events::spaces::CoreEventSpace;
use clack_common::utils::ClapId;
use clack_extensions::audio_ports::{
    AudioPortFlags, AudioPortInfo, AudioPortInfoWriter, AudioPortType,
    PluginAudioPortsImpl,
};
use clack_extensions::note_ports::{
    NoteDialect, NotePortInfo, NotePortInfoWriter, PluginNotePortsImpl,
};
use clack_extensions::params::{
    ParamDisplayWriter, ParamInfo, ParamInfoFlags, ParamInfoWriter,
    PluginAudioProcessorParams, PluginMainThreadParams,
};
use clack_plugin::events::event_types::{NoteOffEvent, NoteOnEvent};
use clack_plugin::prelude::*;

pub const WAVE_PARAM_ID: u32 = 11;
pub const ATTACK_PARAM_ID: u32 = 12;
pub const RELEASE_PARAM_ID: u32 = 13;

const MAX_VOICES: usize = 16;

pub struct TestSynth;

impl Plugin for TestSynth {
    type AudioProcessor<'a> = TestSynthAudio;
    type Shared<'a> = ();
    type MainThread<'a> = TestSynthMain;

    fn declare_extensions(
        builder: &mut PluginExtensions<Self>,
        _shared: Option<&Self::Shared<'_>>,
    ) {
        builder.register::<clack_extensions::params::PluginParams>();
        builder.register::<clack_extensions::audio_ports::PluginAudioPorts>();
        builder.register::<clack_extensions::note_ports::PluginNotePorts>();
    }
}

impl DefaultPluginFactory for TestSynth {
    fn get_descriptor() -> PluginDescriptor {
        PluginDescriptor::new("org.pulsegrid.test-synth", "Pulsegrid Test Synth")
            .with_vendor("Pulsegrid")
            .with_features([c"instrument", c"synthesizer", c"stereo"])
    }

    fn new_shared(_host: HostSharedHandle<'_>) -> Result<Self::Shared<'_>, PluginError> {
        Ok(())
    }

    fn new_main_thread<'a>(
        _host: HostMainThreadHandle<'a>,
        _shared: &'a Self::Shared<'a>,
    ) -> Result<Self::MainThread<'a>, PluginError> {
        Ok(TestSynthMain {
            wave: 0.0,
            attack: 0.01,
            release: 0.1,
        })
    }
}

pub struct TestSynthMain {
    wave: f64,
    attack: f64,
    release: f64,
}

impl<'a> PluginMainThread<'a, ()> for TestSynthMain {}

impl PluginMainThreadParams for TestSynthMain {
    fn count(&self) -> u32 {
        3
    }

    fn get_info(&self, param_index: u32, info: &mut ParamInfoWriter) {
        let (id, name, min, max, default) = match param_index {
            0 => (WAVE_PARAM_ID, "Wave", 0.0, 1.0, 0.0),
            1 => (ATTACK_PARAM_ID, "Attack", 0.001, 1.0, 0.01),
            2 => (RELEASE_PARAM_ID, "Release", 0.01, 2.0, 0.1),
            _ => return,
        };
        let (name_b, module_b): (&[u8], &[u8]) = match param_index {
            0 => (b"Wave", b""),
            1 => (b"Attack", b""),
            2 => (b"Release", b""),
            _ => (b"", b""),
        };
        info.set(&ParamInfo {
            id: ClapId::from_raw(id).unwrap(),
            flags: ParamInfoFlags::empty(),
            cookie: Default::default(),
            name: name_b,
            module: module_b,
            min_value: min,
            max_value: max,
            default_value: default,
        });
    }

    fn get_value(&self, param_id: ClapId) -> Option<f64> {
        match param_id.get() {
            WAVE_PARAM_ID => Some(self.wave),
            ATTACK_PARAM_ID => Some(self.attack),
            RELEASE_PARAM_ID => Some(self.release),
            _ => None,
        }
    }

    fn value_to_text(
        &self,
        param_id: ClapId,
        value: f64,
        writer: &mut ParamDisplayWriter,
    ) -> std::fmt::Result {
        use std::fmt::Write;
        match param_id.get() {
            WAVE_PARAM_ID => {
                writer.write_str(if value < 0.5 { "sine" } else { "square" })
            }
            _ => write!(writer, "{:.3}", value),
        }
    }

    fn text_to_value(&self, _param_id: ClapId, _text: &CStr) -> Option<f64> {
        None
    }

    fn flush(&self, _input: &InputEvents, _output: &mut OutputEvents) {}
}

impl PluginAudioPortsImpl for TestSynthMain {
    fn count(&self, is_input: bool) -> u32 {
        if is_input {
            0
        } else {
            1
        }
    }

    fn get(&self, index: u32, is_input: bool, writer: &mut AudioPortInfoWriter) {
        if is_input || index != 0 {
            return;
        }
        writer.set(&AudioPortInfo {
            id: ClapId::new(0),
            name: b"Stereo Out",
            channel_count: 2,
            flags: AudioPortFlags::IS_MAIN,
            port_type: Some(AudioPortType::STEREO),
            in_place_pair: None,
        });
    }
}

impl PluginNotePortsImpl for TestSynthMain {
    fn count(&self, _is_input: bool) -> u32 {
        1
    }

    fn get(&self, index: u32, _is_input: bool, writer: &mut NotePortInfoWriter) {
        if index != 0 {
            return;
        }
        writer.set(&NotePortInfo {
            id: ClapId::new(0),
            supported_dialects: NoteDialect::Clap.into(),
            preferred_dialect: Some(NoteDialect::Clap),
            name: b"main",
        });
    }
}

struct Voice {
    active: bool,
    releasing: bool,
    note_id: i32,
    key: u8,
    phase: f32,
    env: f32,
    velocity: f32,
}

pub struct TestSynthAudio {
    voices: Vec<Voice>,
    wave: f64,
    attack: f32,
    release: f32,
    sample_rate: f32,
}

impl<'a> PluginAudioProcessor<'a, (), TestSynthMain> for TestSynthAudio {
    fn activate(
        _host: HostAudioProcessorHandle<'a>,
        _main_thread: &TestSynthMain,
        _shared: &'a (),
        audio_config: PluginAudioConfiguration,
    ) -> Result<Self, PluginError> {
        Ok(TestSynthAudio {
            voices: (0..MAX_VOICES)
                .map(|_| Voice {
                    active: false,
                    releasing: false,
                    note_id: -1,
                    key: 0,
                    phase: 0.0,
                    env: 0.0,
                    velocity: 0.0,
                })
                .collect(),
            wave: 0.0,
            attack: 0.01,
            release: 0.1,
            sample_rate: audio_config.sample_rate as f32,
        })
    }

    fn process(
        &mut self,
        _process: Process,
        mut audio: Audio,
        events: Events,
    ) -> Result<ProcessStatus, PluginError> {
        for event in events.input.iter() {
            if let Some(param_event) = event.as_event::<ParamValueEvent>() {
                let v = param_event.value();
                match param_event.param_id().map(|id| id.get()) {
                    Some(WAVE_PARAM_ID) => self.wave = v,
                    Some(ATTACK_PARAM_ID) => self.attack = v.max(0.001) as f32,
                    Some(RELEASE_PARAM_ID) => self.release = v.max(0.01) as f32,
                    _ => {}
                }
            } else if let Some(note_on) = event.as_event::<NoteOnEvent>() {
                let pckn = note_on.pckn();
                self.note_on(
                    pckn.raw_key() as u8,
                    note_on.velocity() as f32,
                    pckn.raw_note_id(),
                );
            } else if let Some(note_off) = event.as_event::<NoteOffEvent>() {
                self.note_off(note_off.pckn().raw_note_id());
            } else if let Some(note_choke) = event.as_event::<NoteChokeEvent>() {
                self.note_choke(note_choke.pckn().raw_note_id());
            }
        }

        // Collect the stereo output buffers.
        let mut outputs: Vec<&mut [f32]> = Vec::new();
        for mut port_pair in &mut audio {
            let Some(channel_pairs) = port_pair.channels()?.into_f32() else {
                continue;
            };
            for channel_pair in channel_pairs {
                if let ChannelPair::OutputOnly(buf) = channel_pair {
                    outputs.push(buf);
                }
            }
        }
        if outputs.len() < 2 {
            return Ok(ProcessStatus::Continue);
        }
        let (left, right) = outputs.split_at_mut(1);
        let left = &mut left[0];
        let right = &mut right[0];
        for (l, r) in left.iter_mut().zip(right.iter_mut()) {
            let mut s = 0.0f32;
            for v in self.voices.iter_mut() {
                if !v.active {
                    continue;
                }
                let freq = 440.0 * 2.0f32.powf((v.key as f32 - 69.0) / 12.0);
                v.phase += freq / self.sample_rate;
                if v.phase >= 1.0 {
                    v.phase -= 1.0;
                }
                let osc = if self.wave < 0.5 {
                    (v.phase * std::f32::consts::TAU).sin()
                } else if v.phase < 0.5 {
                    1.0
                } else {
                    -1.0
                };
                if !v.releasing {
                    v.env += 1.0 / (self.attack * self.sample_rate).max(1.0);
                    if v.env >= 1.0 {
                        v.env = 1.0;
                    }
                } else {
                    v.env -= 1.0 / (self.release * self.sample_rate).max(1.0);
                    if v.env <= 0.0 {
                        v.env = 0.0;
                        v.active = false;
                    }
                }
                s += osc * v.env * v.velocity * 0.25;
            }
            *l = s;
            *r = s;
        }

        Ok(ProcessStatus::Continue)
    }
}

impl TestSynthAudio {
    fn note_on(&mut self, key: u8, velocity: f32, note_id: i32) {
        let idx = self.voices.iter().position(|v| !v.active).unwrap_or(0);
        let v = &mut self.voices[idx];
        v.active = true;
        v.releasing = false;
        v.note_id = note_id;
        v.key = key.min(127);
        v.phase = 0.0;
        v.env = 0.0;
        v.velocity = velocity.clamp(0.0, 1.0);
    }

    fn note_off(&mut self, note_id: i32) {
        for v in self.voices.iter_mut() {
            if v.active && v.note_id == note_id {
                v.releasing = true;
            }
        }
    }

    /// Cut the voice immediately (host loop-wrap choke): skip the release
    /// phase and go silent at once.
    fn note_choke(&mut self, note_id: i32) {
        for v in self.voices.iter_mut() {
            if v.active && v.note_id == note_id {
                v.active = false;
                v.env = 0.0;
            }
        }
    }
}

impl PluginAudioProcessorParams for TestSynthAudio {
    fn flush(&mut self, input: &InputEvents, _output: &mut OutputEvents) {
        for event in input.iter() {
            if let Some(param_event) = event.as_event::<ParamValueEvent>() {
                let v = param_event.value();
                match param_event.param_id().map(|id| id.get()) {
                    Some(WAVE_PARAM_ID) => self.wave = v,
                    Some(ATTACK_PARAM_ID) => self.attack = v.max(0.001) as f32,
                    Some(RELEASE_PARAM_ID) => self.release = v.max(0.01) as f32,
                    _ => {}
                }
            }
        }
    }
}

clack_export_entry!(SinglePluginEntry<TestSynth>);
