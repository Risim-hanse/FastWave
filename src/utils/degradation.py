from typing import Tuple
import numpy as np
from scipy.signal import cheby1, sosfiltfilt, resample_poly


def degrade_signal(
    wav_hr: np.ndarray,
    highcut: int,
    sr: int = 48000,
    order: int = 8,
    ripple: float = 0.05,
) -> np.ndarray:
    """Apply Chebyshev lowpass + resample to degrade a high-res signal.

    Matches the training pipeline: Chebyshev Type I lowpass, then
    downsample to (highcut*2) Hz and upsample back to sr.

    Args:
        wav_hr: High-resolution waveform at sr Hz, shape [T].
        highcut: Cutoff frequency in Hz (Nyquist of the target low rate).
        sr: Sample rate of wav_hr.
        order: Chebyshev filter order.
        ripple: Chebyshev passband ripple in dB.

    Returns:
        Degraded waveform at sr Hz, same length as wav_hr.
    """
    nyq = 0.5 * sr
    hi = highcut / nyq

    if hi >= 1.0:
        return wav_hr.copy()

    sos = cheby1(order, ripple, hi, btype="lowpass", output="sos")
    wav_l = sosfiltfilt(sos, wav_hr)
    wav_l = resample_poly(wav_l, highcut * 2, sr)
    wav_l = resample_poly(wav_l, sr, highcut * 2)

    if len(wav_l) < len(wav_hr):
        wav_l = np.pad(wav_l, (0, len(wav_hr) - len(wav_l)))
    elif len(wav_l) > len(wav_hr):
        wav_l = wav_l[: len(wav_hr)]

    return wav_l


def build_band_mask(highcut: int, sr: int, fft_size: int) -> Tuple[int, float]:
    """Return (cutoff_bin, hi) for a given degradation config.

    Args:
        highcut: Cutoff frequency in Hz.
        sr: Sample rate.
        fft_size: FFT size // 2 + 1 (typically 513).

    Returns:
        cutoff_bin: Number of low-frequency bins (int).
        hi: Normalized cutoff frequency (highcut / Nyquist).
    """
    nyq = 0.5 * sr
    hi = highcut / nyq
    cutoff_bin = int(hi * fft_size)
    return cutoff_bin, hi