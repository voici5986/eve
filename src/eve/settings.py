from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
from typing import Any

from platformdirs import user_config_path


@dataclass(slots=True)
class RecordingSettings:
    device: str = "default"
    output_dir: str = "recordings"
    audio_format: str = "flac"
    device_check_seconds: float = 2.0
    device_retry_seconds: float = 2.0
    auto_switch_device: bool | None = None
    auto_switch_scan_seconds: float = 3.0
    auto_switch_probe_seconds: float = 0.25
    auto_switch_max_candidates_per_scan: int = 2
    exclude_device_keywords: str = "iphone,continuity"
    auto_switch_min_rms: float = 0.006
    auto_switch_min_ratio: float = 1.8
    auto_switch_cooldown_seconds: float = 8.0
    auto_switch_confirmations: int = 2
    console_feedback: bool = True
    console_feedback_hz: float = 12.0
    total_hours: float = 24.0
    segment_minutes: float = 60.0
    asr_model: str = "Qwen/Qwen3-ASR-0.6B"
    disable_asr: bool = False
    asr_language: str = "auto"
    asr_device: str = "auto"
    asr_dtype: str = "auto"
    asr_max_new_tokens: int = 256
    asr_max_batch_size: int = 1
    asr_preload: bool = False


@dataclass(slots=True)
class TranscribeSettings:
    input_dir: str = "recordings"
    prefix: str = "eve"
    watch: bool = False
    poll_seconds: float = 2.0
    settle_seconds: float = 3.0
    force: bool = False
    limit: int = 0
    asr_model: str = "Qwen/Qwen3-ASR-0.6B"
    asr_language: str = "auto"
    asr_device: str = "auto"
    asr_dtype: str = "auto"
    asr_max_new_tokens: int = 256
    asr_max_batch_size: int = 1
    asr_preload: bool = False


@dataclass(slots=True)
class DesktopSettings:
    launch_at_login: bool = False
    start_recording_on_launch: bool = False
    hide_window_on_close: bool = True


@dataclass(slots=True)
class AppSettings:
    recording: RecordingSettings
    transcribe: TranscribeSettings
    desktop: DesktopSettings


def default_settings() -> AppSettings:
    return AppSettings(
        recording=RecordingSettings(),
        transcribe=TranscribeSettings(),
        desktop=DesktopSettings(),
    )


def settings_file() -> Path:
    return user_config_path("eve", "nexmoe") / "settings.json"


def _coerce_value(value: Any, default: Any) -> Any:
    if value is None:
        return default
    target_type = type(default)
    if target_type is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default
    if target_type is int and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if target_type is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if default is None:
        if value in ("", None):
            return None
        if isinstance(value, bool):
            return value
        return str(value)
    return str(value)


def _merge_dataclass(cls, payload: Any):
    merged: dict[str, Any] = {}
    incoming = payload if isinstance(payload, dict) else {}
    defaults = cls()
    for field in fields(cls):
        default_value = getattr(defaults, field.name)
        merged[field.name] = _coerce_value(incoming.get(field.name), default_value)
    return cls(**merged)


def load_settings() -> AppSettings:
    path = settings_file()
    defaults = default_settings()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return defaults
    except Exception:
        return defaults
    if not isinstance(payload, dict):
        return defaults
    return AppSettings(
        recording=_merge_dataclass(RecordingSettings, payload.get("recording")),
        transcribe=_merge_dataclass(TranscribeSettings, payload.get("transcribe")),
        desktop=_merge_dataclass(DesktopSettings, payload.get("desktop")),
    )


def save_settings(settings: AppSettings) -> Path:
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return path


def recording_defaults(settings: AppSettings) -> dict[str, Any]:
    return asdict(settings.recording)


def transcribe_defaults(settings: AppSettings) -> dict[str, Any]:
    return asdict(settings.transcribe)
