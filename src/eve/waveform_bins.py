from __future__ import annotations

import math

import numpy as np

_MIN_FREQUENCY_HZ = 70.0
_MAX_FREQUENCY_HZ = 5600.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def build_waveform_bins(
    samples: np.ndarray,
    *,
    sample_rate: int,
    bin_count: int,
    floor: float = 0.03,
) -> list[float]:
    signal = np.asarray(samples, dtype=np.float32).reshape(-1)
    if signal.size == 0 or sample_rate <= 0 or bin_count <= 0:
        return [0.0] * max(0, bin_count)

    signal = np.nan_to_num(signal, nan=0.0, posinf=0.0, neginf=0.0)
    window_size = int(_clamp(sample_rate / 3.0, 2048, 8192))
    if signal.size >= window_size:
        window = signal[-window_size:]
    else:
        window = np.pad(signal, (window_size - signal.size, 0))

    window = window - float(np.mean(window))
    if not np.any(window):
        return [0.0] * bin_count

    windowed = window * np.hanning(window_size)
    spectrum = np.abs(np.fft.rfft(windowed))
    if spectrum.size <= 1:
        return [0.0] * bin_count

    frequencies = np.fft.rfftfreq(window_size, d=1.0 / float(sample_rate))
    low = max(_MIN_FREQUENCY_HZ, float(sample_rate) / window_size)
    high = min(_MAX_FREQUENCY_HZ, float(sample_rate) * 0.45)
    if high <= low:
        return [0.0] * bin_count

    edges = np.geomspace(low, high, num=bin_count + 1)
    raw_bins: list[float] = []
    for start, end in zip(edges[:-1], edges[1:], strict=True):
        left = int(np.searchsorted(frequencies, start, side="left"))
        right = int(np.searchsorted(frequencies, end, side="right"))
        band = spectrum[left:max(left + 1, right)]
        if band.size == 0:
            raw_bins.append(0.0)
            continue
        dominant = float(np.percentile(band, 82))
        raw_bins.append(math.sqrt(max(0.0, dominant)))

    values = np.asarray(raw_bins, dtype=np.float32)
    if not np.any(values):
        return [0.0] * bin_count

    noise_floor = float(np.percentile(values, 28))
    peak = float(np.percentile(values, 96))
    span = max(peak - noise_floor * 1.05, 1e-6)
    normalized = np.clip((values - noise_floor * 1.05) / span, 0.0, 1.0)
    normalized = np.power(normalized, 0.72)
    normalized = np.convolve(normalized, np.array([0.2, 0.6, 0.2]), mode="same")
    normalized = np.clip(normalized * 1.12, 0.0, 1.0)
    normalized[normalized < floor] = 0.0
    return [float(value) for value in normalized.tolist()]
