//! Native sample loading: SampleDecoder -> SampleBuffer.
//!
//! Mirrors LMMS's explicit layering (a decoder produces decoded frames; a
//! buffer owns them) without copying its code. Decoded audio is converted
//! once, at load time, to stereo-interleaved f32 at the engine's sample
//! rate:
//!
//! * mono is upmixed by duplicating the channel (LMMS behavior),
//! * more than two channels keep the first two (LMMS behavior),
//! * sample-rate conversion is linear interpolation (documented
//!   simplification; LMMS uses libsamplerate at playback time).
//!
//! The buffer is immutable after construction and shared between clips
//! through `Arc`, like LMMS's `shared_ptr<const SampleBuffer>`: many
//! [`crate::timeline::AudioClipEvent`]s can reference one decoded file
//! while each keeps its own playback state (offset, pitch, reverse).
//!
//! Supported formats come from symphonia's default features: WAV, FLAC,
//! OGG/Vorbis, plus MP3 via the explicit `mp3` feature.

use std::path::Path;
use std::sync::Arc;

use symphonia::core::audio::GenericAudioBufferRef;
use symphonia::core::codecs::audio::AudioDecoderOptions;
use symphonia::core::codecs::CodecParameters;
use symphonia::core::errors::Error;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;

/// Maximum decoded length: 32M stereo frames (~11 minutes at 48 kHz).
/// Beyond this, `decode_file` fails loudly rather than allocating
/// unbounded RAM. (FL Studio's "Keep on disk" streaming is the proper
/// answer for longer material; that is a later step.)
const MAX_FRAMES: usize = 32 * 1024 * 1024;

/// Decoded sample data: stereo-interleaved f32 at the engine sample rate.
#[derive(Clone, Debug)]
pub struct SampleBuffer {
    /// Stereo-interleaved frames (`frames[2*i]`, `frames[2*i+1]`).
    pub frames: Arc<Vec<f32>>,
    /// Number of stereo frames.
    pub len: usize,
    /// Original file path (diagnostics / re-resolve on project load).
    pub source_path: String,
    /// Source sample rate before conversion.
    pub source_sample_rate: u32,
}

impl SampleBuffer {
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
}

/// Decode an audio file into a `SampleBuffer` at `target_sample_rate`.
///
/// Errors are loud and specific: missing file, unsupported container,
/// no audio track, decode failure, empty audio, or over-length audio.
pub fn decode_file(path: &Path, target_sample_rate: u32) -> Result<SampleBuffer, String> {
    let path_str = path.to_string_lossy().to_string();
    let file = std::fs::File::open(path)
        .map_err(|e| format!("cannot open '{}': {}", path_str, e))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());

    let mut hint = Hint::new();
    if let Some(ext) = path.extension().and_then(|e| e.to_str()) {
        hint.with_extension(ext);
    }
    let probed = symphonia::default::get_probe()
        .probe(
            &hint,
            mss,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .map_err(|e| format!("'{}': not a recognized audio file ({})", path_str, e))?;
    let mut format = probed;

    let (track_id, codec_params) = format
        .tracks()
        .iter()
        .find_map(|t| match &t.codec_params {
            Some(CodecParameters::Audio(p)) => Some((t.id, p.clone())),
            _ => None,
        })
        .ok_or_else(|| format!("'{}': no audio track found", path_str))?;
    let source_rate = codec_params
        .sample_rate
        .ok_or_else(|| format!("'{}': unknown sample rate", path_str))?;

    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(&codec_params, &AudioDecoderOptions::default())
        .map_err(|e| format!("'{}': unsupported codec ({})", path_str, e))?;

    // Decode to planar f32, one Vec per channel, then interleave to
    // stereo at the source rate.
    let mut channels: Vec<Vec<f32>> = Vec::new();
    loop {
        let packet = match format.next_packet() {
            Ok(Some(p)) => p,
            Ok(None) => break, // end of stream
            Err(e) => return Err(format!("'{}': packet error ({})", path_str, e)),
        };
        if packet.track_id != track_id {
            continue;
        }
        match decoder.decode(&packet) {
            Ok(audio_buf) => {
                append_packet_f32(&audio_buf, &mut channels)?;
                let frames = channels.first().map(|c| c.len()).unwrap_or(0);
                if frames > MAX_FRAMES * 4 {
                    return Err(format!(
                        "'{}': audio exceeds the {}-frame decode limit",
                        path_str, MAX_FRAMES
                    ));
                }
            }
            // Skip undecodable packets (common with MP3 padding); a
            // stream that yields nothing at all errors out below.
            Err(Error::DecodeError(_)) => continue,
            Err(e) => return Err(format!("'{}': decode error ({})", path_str, e)),
        }
    }
    let n_channels = channels.len();
    let src_frames = channels.first().map(|c| c.len()).unwrap_or(0);
    if n_channels == 0 || src_frames == 0 {
        return Err(format!("'{}': no audio frames decoded", path_str));
    }

    // Interleave to stereo at the source rate.
    let mut stereo_src = Vec::with_capacity(src_frames * 2);
    for i in 0..src_frames {
        let c0 = channels[0][i];
        // Mono duplicates; >2 channels keep the first two (LMMS behavior).
        let c1 = if n_channels >= 2 { channels[1][i] } else { c0 };
        stereo_src.push(c0);
        stereo_src.push(c1);
    }

    // Resample to the engine rate (linear interpolation).
    let frames = if source_rate == target_sample_rate {
        stereo_src
    } else {
        resample_linear(&stereo_src, source_rate, target_sample_rate)
    };
    let len = frames.len() / 2;
    if len == 0 {
        return Err(format!("'{}': no audio frames after resampling", path_str));
    }
    if len > MAX_FRAMES {
        return Err(format!(
            "'{}': resampled audio ({} frames) exceeds the {}-frame limit",
            path_str, len, MAX_FRAMES
        ));
    }
    Ok(SampleBuffer {
        frames: Arc::new(frames),
        len,
        source_path: path_str,
        source_sample_rate: source_rate,
    })
}

/// Copy one decoded packet into planar f32 storage (one Vec per
/// channel), converting any symphonia sample format via
/// `copy_to_vecs_planar` (which overwrites, so callers accumulate).
fn append_packet_f32(
    audio_buf: &GenericAudioBufferRef,
    channels: &mut Vec<Vec<f32>>,
) -> Result<(), String> {
    let mut planar: Vec<Vec<f32>> = Vec::new();
    audio_buf.copy_to_vecs_planar::<f32>(&mut planar);
    if channels.is_empty() {
        *channels = planar;
        return Ok(());
    }
    if channels.len() != planar.len() {
        return Err(format!(
            "channel count changed mid-stream ({} -> {})",
            channels.len(),
            planar.len()
        ));
    }
    for (dst, src) in channels.iter_mut().zip(planar.into_iter()) {
        dst.extend(src);
    }
    Ok(())
}

/// Linear-interpolation resample of stereo-interleaved f32.
fn resample_linear(src: &[f32], src_rate: u32, dst_rate: u32) -> Vec<f32> {
    let src_frames = src.len() / 2;
    if src_frames == 0 {
        return Vec::new();
    }
    let ratio = src_rate as f64 / dst_rate as f64;
    let dst_frames = ((src_frames as f64 / ratio).ceil() as usize).max(1);
    let mut dst = Vec::with_capacity(dst_frames * 2);
    for i in 0..dst_frames {
        let pos = i as f64 * ratio;
        let i0 = (pos as usize).min(src_frames - 1);
        let i1 = (i0 + 1).min(src_frames - 1);
        let t = (pos - i0 as f64) as f32;
        let l0 = src[2 * i0];
        let l1 = src[2 * i1];
        let r0 = src[2 * i0 + 1];
        let r1 = src[2 * i1 + 1];
        dst.push(l0 + (l1 - l0) * t);
        dst.push(r0 + (r1 - r0) * t);
    }
    dst
}

/// Peak pairs for waveform display: `n` (min, max) buckets over the
/// whole buffer, computed on the control thread (never the audio path).
pub fn peak_pairs(buffer: &SampleBuffer, n: usize) -> Vec<(f32, f32)> {
    let n = n.max(1);
    let mut out = Vec::with_capacity(n);
    if buffer.len == 0 {
        out.resize(n, (0.0, 0.0));
        return out;
    }
    let frames = &buffer.frames;
    for b in 0..n {
        let start = (b as u64 * buffer.len as u64 / n as u64) as usize;
        let end = (((b + 1) as u64 * buffer.len as u64 / n as u64) as usize).max(start + 1);
        let mut mn = f32::INFINITY;
        let mut mx = f32::NEG_INFINITY;
        for i in start..end.min(buffer.len) {
            let l = frames[2 * i];
            let r = frames[2 * i + 1];
            if l < mn { mn = l; }
            if r < mn { mn = r; }
            if l > mx { mx = l; }
            if r > mx { mx = r; }
        }
        if mn == f32::INFINITY {
            mn = 0.0;
            mx = 0.0;
        }
        out.push((mn, mx));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Write a minimal PCM-16 WAV in memory for decoder tests.
    fn write_wav(samples: &[i16], sample_rate: u32, channels: u16) -> Vec<u8> {
        let mut v = Vec::new();
        let data_bytes = samples.len() * 2;
        v.extend_from_slice(b"RIFF");
        v.extend_from_slice(&(36 + data_bytes as u32).to_le_bytes());
        v.extend_from_slice(b"WAVE");
        v.extend_from_slice(b"fmt ");
        v.extend_from_slice(&16u32.to_le_bytes());
        v.extend_from_slice(&1u16.to_le_bytes()); // PCM
        v.extend_from_slice(&channels.to_le_bytes());
        v.extend_from_slice(&sample_rate.to_le_bytes());
        v.extend_from_slice(&(sample_rate * channels as u32 * 2).to_le_bytes());
        v.extend_from_slice(&(channels * 2).to_le_bytes());
        v.extend_from_slice(&16u16.to_le_bytes());
        v.extend_from_slice(b"data");
        v.extend_from_slice(&(data_bytes as u32).to_le_bytes());
        for s in samples {
            v.extend_from_slice(&s.to_le_bytes());
        }
        v
    }

    fn decode_bytes(wav: &[u8], rate: u32, tag: &str) -> SampleBuffer {
        // Unique per test: the suite runs tests in parallel in one
        // process, so a pid-only name collides.
        let dir = std::env::temp_dir();
        let path = dir.join(format!(
            "pulsegrid_test_{}_{}.wav",
            std::process::id(),
            tag
        ));
        std::fs::write(&path, wav).unwrap();
        let buf = decode_file(&path, rate).expect("decode failed");
        let _ = std::fs::remove_file(&path);
        buf
    }

    #[test]
    fn wav_mono_upmixes_to_stereo() {
        // 4 mono frames at 8kHz, decoded at 8kHz (no resample).
        let wav = write_wav(&[1000, 2000, 3000, 4000], 8000, 1);
        let buf = decode_bytes(&wav, 8000, "mono");
        assert_eq!(buf.len, 4);
        assert_eq!(buf.frames.len(), 8);
        // L == R for every frame (mono duplication).
        for i in 0..4 {
            assert!((buf.frames[2 * i] - buf.frames[2 * i + 1]).abs() < 1e-6);
        }
        // Amplitude is in the right ballpark (1000/32768).
        assert!((buf.frames[0] - 1000.0 / 32768.0).abs() < 1e-4);
    }

    #[test]
    fn wav_stereo_preserved_and_resampled() {
        // Stereo 8kHz -> 16kHz: frame count doubles.
        let wav = write_wav(&[1000, -1000, 2000, -2000], 8000, 2);
        let buf = decode_bytes(&wav, 16000, "stereo");
        assert_eq!(buf.len, 4); // 2 src frames -> 4 dst frames
        assert_eq!(buf.source_sample_rate, 8000);
        // First frame matches source.
        assert!((buf.frames[0] - 1000.0 / 32768.0).abs() < 1e-4);
        assert!((buf.frames[1] + 1000.0 / 32768.0).abs() < 1e-4);
    }

    #[test]
    fn missing_file_errors_loudly() {
        let r = decode_file(Path::new("/nonexistent/dir/nope.wav"), 44100);
        assert!(r.is_err());
        assert!(r.unwrap_err().contains("cannot open"));
    }

    #[test]
    fn peak_pairs_shape() {
        let wav = write_wav(&[1000, 2000, 3000, 4000], 8000, 1);
        let buf = decode_bytes(&wav, 8000, "peaks");
        let peaks = peak_pairs(&buf, 2);
        assert_eq!(peaks.len(), 2);
        assert!(peaks[0].1 >= peaks[0].0);
    }
}
