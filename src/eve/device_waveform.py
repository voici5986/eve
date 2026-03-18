from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import sounddevice as sd

from .waveform_bins import build_waveform_bins

WAVEFORM_BIN_COUNT = 64
_MIN_GATE_RMS = 0.0014
_NOISE_FLOOR_RMS = 0.0012
_NOISE_GATE_MULTIPLIER = 1.8
_ACTIVE_GATE_MULTIPLIER = 1.08
_IDLE_DB_DECAY = 0.28


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _rms_to_db(rms: float) -> float:
    return 20.0 * math.log10(max(rms, 1e-8))


def _normalize_device(device: str | None) -> int | str | None:
    if device in ("", "default", "auto", None):
        return None
    if isinstance(device, str) and device.startswith(":"):
        try:
            return int(device[1:])
        except ValueError:
            return device
    try:
        return int(device)
    except Exception:
        return device


@dataclass(frozen=True)
class DeviceWaveformSnapshot:
    db: float
    device_label: str
    waveform_bins: list[float]
    active: bool
    available: bool


class DeviceWaveformMonitor:
    def __init__(self, device: str | None) -> None:
        self._device = device or "default"
        self._samples: deque[float] = deque(maxlen=8192)
        self._db = -80.0
        self._device_label = "-"
        self._active = False
        self._available = False
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._restart_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._noise_floor_rms = _NOISE_FLOOR_RMS
        self._sample_rate = 16000

    def start(self) -> None:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return
        self._stop_event.clear()
        self._restart_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="eve-device-waveform",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._restart_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None

    def set_device(self, device: str | None) -> None:
        with self._lock:
            normalized = device or "default"
            if normalized == self._device:
                return
            self._device = normalized
            self._reset_levels()
        self._restart_event.set()

    def snapshot(self) -> DeviceWaveformSnapshot:
        with self._lock:
            waveform_bins = self._build_bins()
            return DeviceWaveformSnapshot(
                db=self._db,
                device_label=self._device_label,
                waveform_bins=waveform_bins,
                active=self._active,
                available=self._available,
            )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._restart_event.clear()
            try:
                self._stream_once()
            except Exception:
                with self._lock:
                    self._available = False
                    self._reset_levels()
                if self._stop_event.wait(0.8):
                    return

    def _stream_once(self) -> None:
        target_device = self._current_device()
        device_index = self._resolve_device_index(target_device)
        if device_index is None:
            with self._lock:
                self._device_label = "-"
                self._available = False
                self._reset_levels()
            self._stop_event.wait(0.8)
            return
        stream_info = sd.query_devices(device_index, kind="input")
        samplerate = int(float(stream_info.get("default_samplerate") or 16000))
        channels = max(1, min(2, int(stream_info.get("max_input_channels") or 1)))
        with self._lock:
            self._device_label = self._format_device_label(device_index)
            self._available = True
            self._sample_rate = samplerate

        def callback(indata, _frames, _time, _status) -> None:
            audio = np.asarray(indata, dtype=np.float32)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            samples = audio.reshape(-1)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float32))))
            with self._lock:
                self._noise_floor_rms = self._next_noise_floor(rms)
                self._samples.extend(float(sample) for sample in samples)
                active_floor = max(_MIN_GATE_RMS * 0.22, self._noise_floor_rms * 0.55)
                active = rms >= active_floor
                db = _rms_to_db(rms if rms > 0 else active_floor * 0.12)
                if active and db >= self._db:
                    self._db = db
                else:
                    self._db = (1.0 - _IDLE_DB_DECAY) * self._db + _IDLE_DB_DECAY * db
                self._active = active
                self._available = True

        with sd.InputStream(
            samplerate=samplerate,
            channels=channels,
            dtype="float32",
            blocksize=max(256, samplerate // 40),
            device=device_index,
            callback=callback,
        ):
            while not self._stop_event.is_set() and not self._restart_event.wait(0.12):
                continue

    def _current_device(self) -> str:
        with self._lock:
            return self._device

    def _build_bins(self) -> list[float]:
        return build_waveform_bins(
            np.asarray(self._samples, dtype=np.float32),
            sample_rate=self._sample_rate,
            bin_count=WAVEFORM_BIN_COUNT,
            floor=0.024,
        )

    def _reset_levels(self) -> None:
        self._samples.clear()
        self._db = -80.0
        self._active = False
        self._noise_floor_rms = _NOISE_FLOOR_RMS
        self._sample_rate = 16000

    def _next_noise_floor(self, rms: float) -> float:
        if rms <= self._noise_floor_rms:
            weight = 0.16
        else:
            weight = 0.025
        floor = (1.0 - weight) * self._noise_floor_rms + weight * rms
        return _clamp(floor, _NOISE_FLOOR_RMS, 0.02)

    def _apply_noise_gate(self, samples: np.ndarray, threshold: float) -> np.ndarray:
        magnitudes = np.abs(samples)
        keep = magnitudes > threshold
        if not np.any(keep):
            return np.zeros_like(samples)
        gated = np.zeros_like(samples)
        gated[keep] = samples[keep]
        return gated

    def _resolve_device_index(self, device: str) -> int | None:
        target = _normalize_device(device)
        if isinstance(target, int):
            try:
                sd.query_devices(target, kind="input")
            except Exception:
                return None
            return target
        if target is None:
            try:
                default_input = sd.default.device[0]
            except Exception:
                default_input = None
            if default_input is None:
                return self._first_input_device()
            try:
                default_index = int(default_input)
            except Exception:
                return self._first_input_device()
            if default_index >= 0:
                return default_index
            return self._first_input_device()
        try:
            info = sd.query_devices(target, kind="input")
        except Exception:
            return self._find_device_index(str(target))
        return self._find_matching_index(info.get("name"), info.get("hostapi"))

    def _first_input_device(self) -> int | None:
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for index, info in enumerate(devices):
            try:
                max_inputs = int(info.get("max_input_channels", 0))
            except Exception:
                max_inputs = 0
            if max_inputs > 0:
                return index
        return None

    def _find_device_index(self, query: str) -> int | None:
        lowered = query.strip().lower()
        if not lowered:
            return None
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for index, info in enumerate(devices):
            name = str(info.get("name") or "").strip()
            if not name or lowered not in name.lower():
                continue
            try:
                max_inputs = int(info.get("max_input_channels", 0))
            except Exception:
                max_inputs = 0
            if max_inputs > 0:
                return index
        return None

    def _find_matching_index(self, name: Any, hostapi: Any) -> int | None:
        if not name:
            return None
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        for index, info in enumerate(devices):
            if info.get("name") != name:
                continue
            if hostapi is not None and info.get("hostapi") != hostapi:
                continue
            try:
                max_inputs = int(info.get("max_input_channels", 0))
            except Exception:
                max_inputs = 0
            if max_inputs > 0:
                return index
        return None

    def _format_device_label(self, device_index: int) -> str:
        try:
            info = sd.query_devices(device_index, kind="input")
        except Exception:
            return str(device_index)
        name = str(info.get("name") or "").strip()
        return f"{device_index}:{name}" if name else str(device_index)
