//! Minimal 16-bit PCM WAV writer (stereo interleaved).
//!
//! Kept dependency-free on purpose: the format is small and the writer is
//! only used on the control thread / offline render path.

use std::fs::File;
use std::io::{BufWriter, Write};

/// Write `frames` stereo-interleaved f32 samples as 16-bit PCM WAV.
pub fn write_wav_stereo(path: &str, sample_rate: u32, frames: &[f32]) -> std::io::Result<()> {
    assert!(frames.len() % 2 == 0, "frames must be stereo interleaved");
    let file = File::create(path)?;
    let mut w = BufWriter::new(file);

    let _num_samples = (frames.len() / 2) as u32;
    let data_bytes = frames.len() as u32 * 2;
    let channels: u16 = 2;
    let bits: u16 = 16;
    let byte_rate = sample_rate * channels as u32 * (bits as u32 / 8);
    let block_align = channels * (bits / 8);

    // RIFF header
    w.write_all(b"RIFF")?;
    w.write_all(&(36 + data_bytes).to_le_bytes())?;
    w.write_all(b"WAVE")?;
    // fmt chunk
    w.write_all(b"fmt ")?;
    w.write_all(&16u32.to_le_bytes())?;
    w.write_all(&1u16.to_le_bytes())?; // PCM
    w.write_all(&channels.to_le_bytes())?;
    w.write_all(&sample_rate.to_le_bytes())?;
    w.write_all(&byte_rate.to_le_bytes())?;
    w.write_all(&block_align.to_le_bytes())?;
    w.write_all(&bits.to_le_bytes())?;
    // data chunk
    w.write_all(b"data")?;
    w.write_all(&data_bytes.to_le_bytes())?;
    for s in frames {
        let clamped = s.clamp(-1.0, 1.0);
        let v = (clamped * 32767.0).round() as i16;
        w.write_all(&v.to_le_bytes())?;
    }
    w.flush()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn wav_header_is_valid() {
        let path = "/tmp/pulsegrid_wav_test.wav";
        let frames = vec![0.0f32, 0.0, 0.5, -0.5];
        write_wav_stereo(path, 44100, &frames).unwrap();
        let bytes = fs::read(path).unwrap();
        assert_eq!(&bytes[0..4], b"RIFF");
        assert_eq!(&bytes[8..12], b"WAVE");
        assert_eq!(&bytes[12..16], b"fmt ");
        assert_eq!(&bytes[36..40], b"data");
        let sr = u32::from_le_bytes(bytes[24..28].try_into().unwrap());
        assert_eq!(sr, 44100);
        fs::remove_file(path).ok();
    }
}
