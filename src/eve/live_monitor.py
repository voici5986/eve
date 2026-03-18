from __future__ import annotations

import math
import time
from typing import Any, Mapping

import flet as ft
import flet.canvas as cv

from .device_waveform import WAVEFORM_BIN_COUNT

_BASELINE_SEGMENTS = 28
_BAR_GAP = 3.0
_BAR_RADIUS = 999
_IDLE_BAR_HEIGHT = 3.0
_MIN_BAR_HEIGHT = 10.0
_MAX_BAR_HEIGHT_RATIO = 0.74


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _lerp(start: float, end: float, amount: float) -> float:
    return start + (end - start) * amount


def _sample(values: list[float], position: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    scaled = _clamp(position) * (len(values) - 1)
    left = int(math.floor(scaled))
    right = min(len(values) - 1, left + 1)
    return _lerp(values[left], values[right], scaled - left)


def _mix(start: str, end: str, amount: float) -> str:
    amount = _clamp(amount)
    start = start.lstrip("#")
    end = end.lstrip("#")
    sr, sg, sb = int(start[:2], 16), int(start[2:4], 16), int(start[4:6], 16)
    er, eg, eb = int(end[:2], 16), int(end[2:4], 16), int(end[4:6], 16)
    red = round(sr + (er - sr) * amount)
    green = round(sg + (eg - sg) * amount)
    blue = round(sb + (eb - sb) * amount)
    return f"#{red:02X}{green:02X}{blue:02X}"


class LiveMonitorPanel:
    def __init__(self) -> None:
        self._mode = "idle"
        self._mode_started_at = time.monotonic()
        self._canvas_width = 760.0
        self._canvas_height = 118.0
        self._bar_count = 0
        self._bar_width = 4.0
        self._rendered_bars: list[float] = []
        self._last_active_bars: list[float] = []
        self._canvas: cv.Canvas | None = None
        self._root: ft.Column | None = None
        self._state_pill: ft.Text | None = None
        self._elapsed_text: ft.Text | None = None
        self._state_text: ft.Text | None = None
        self._db_text: ft.Text | None = None
        self._device_text: ft.Text | None = None
        self._meta_text: ft.Text | None = None
        self._hint_text: ft.Text | None = None
        self._asr_chip_text: ft.Text | None = None
        self._asr_preview_text: ft.Text | None = None
        self._history_texts: list[ft.Text] = []

    def build(self) -> ft.Control:
        self._state_pill = ft.Text("IDLE", size=11, weight=ft.FontWeight.W_700, color="#6E665D")
        self._elapsed_text = ft.Text("00:00:00", size=42, weight=ft.FontWeight.W_800, color="#1F211E")
        self._state_text = ft.Text("待机中，波形保持静止基线。", size=13, color="#6C716B")
        self._db_text = ft.Text("-80.0 dB", size=14, weight=ft.FontWeight.W_700, color="#575E58")
        self._device_text = ft.Text("麦克风: -", size=12, color="#727A74")
        self._meta_text = ft.Text("自动切麦 关闭 / ASR 开启", size=12, color="#7C847D")
        self._hint_text = ft.Text("波形直接绑定当前设置里的麦克风设备。", size=11, color="#7B8079")
        self._asr_chip_text = ft.Text("ASR 已开启", size=11, weight=ft.FontWeight.W_700, color="#325145")
        self._asr_preview_text = ft.Text("最近识别: 暂无内容", size=14, weight=ft.FontWeight.W_600, color="#364038")
        self._history_texts = [ft.Text("", size=11, color="#8D938C") for _ in range(3)]
        self._canvas = cv.Canvas(
            shapes=[],
            expand=True,
            resize_interval=0,
            on_resize=self._on_canvas_resize,
        )
        self._rebuild_geometry()
        self._root = ft.Column(
            spacing=18,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                    controls=[
                        ft.Column(spacing=6, controls=[self._pill(), self._elapsed_text, self._state_text]),
                        ft.Column(
                            spacing=5,
                            horizontal_alignment=ft.CrossAxisAlignment.END,
                            controls=[self._db_text, self._device_text, self._meta_text],
                        ),
                    ],
                ),
                ft.Container(
                    height=self._canvas_height,
                    border_radius=24,
                    bgcolor="#FCF9F3",
                    border=ft.Border.all(1, "#E7DED1"),
                    padding=0,
                    content=self._canvas,
                ),
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[self._hint_text, self._asr_chip()],
                ),
                ft.Divider(color="#EFE8DC", height=1),
                ft.Column(spacing=10, controls=[self._asr_preview_text, ft.Column(spacing=6, controls=self._history_texts)]),
            ],
        )
        return self._root

    def apply_payload(self, payload: Mapping[str, Any]) -> None:
        recording = bool(payload.get("recording"))
        waveform_active = bool(payload.get("waveform_active"))
        waveform_processing = bool(payload.get("waveform_processing"))
        mode = "active" if waveform_active else "processing" if waveform_processing else "idle"
        if mode != self._mode:
            if self._mode == "active":
                self._last_active_bars = list(self._rendered_bars)
            self._mode = mode
            self._mode_started_at = time.monotonic()
        waveform_bins = self._normalize_bins(payload.get("waveform_bins"))
        target = self._target_bars(mode, waveform_bins)
        self._rendered_bars = self._smooth(target, mode)
        if mode == "active":
            self._last_active_bars = list(self._rendered_bars)
        self._update_copy(payload, mode, recording)
        self._render_canvas()
        self._update_history(payload, mode)

    def update(self) -> None:
        controls: list[ft.Control | None] = [
            self._state_pill,
            self._elapsed_text,
            self._state_text,
            self._db_text,
            self._device_text,
            self._meta_text,
            self._hint_text,
            self._asr_chip_text,
            self._asr_preview_text,
            *self._history_texts,
        ]
        for control in controls:
            if control is None:
                continue
            try:
                control.update()
            except RuntimeError:
                continue

    def _pill(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            border_radius=999,
            bgcolor="#EEE8DE",
            border=ft.Border.all(1, "#DDD4C5"),
            content=self._state_pill,
        )

    def _asr_chip(self) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=12, vertical=6),
            border_radius=999,
            bgcolor="#E1EBE5",
            border=ft.Border.all(1, "#B8CBBE"),
            content=self._asr_chip_text,
        )

    def _on_canvas_resize(self, event: cv.CanvasResizeEvent) -> None:
        self._canvas_width = max(220.0, float(event.width or self._canvas_width))
        self._canvas_height = max(96.0, float(event.height or self._canvas_height))
        self._rebuild_geometry()
        self._render_canvas()

    def _rebuild_geometry(self) -> None:
        step = 7.0
        self._bar_count = max(28, int(self._canvas_width / step))
        total_gap = max(0.0, float(self._bar_count - 1) * _BAR_GAP)
        available_width = max(self._bar_count * 2.0, self._canvas_width - total_gap)
        self._bar_width = max(2.0, min(5.0, available_width / self._bar_count))
        self._rendered_bars = self._fit(self._rendered_bars)
        self._last_active_bars = self._fit(self._last_active_bars or [0.08] * self._bar_count)

    def _fit(self, values: list[float]) -> list[float]:
        if not values:
            return [0.0] * self._bar_count
        return [_sample(values, index / max(self._bar_count - 1, 1)) for index in range(self._bar_count)]

    def _normalize_bins(self, values: Any) -> list[float]:
        if not isinstance(values, list):
            return [0.0] * WAVEFORM_BIN_COUNT
        bins = [_clamp(float(value)) for value in values[:WAVEFORM_BIN_COUNT]]
        return bins or [0.0] * WAVEFORM_BIN_COUNT

    def _target_bars(self, mode: str, waveform_bins: list[float]) -> list[float]:
        if mode == "active":
            return self._build_active(waveform_bins)
        return [0.0] * self._bar_count

    def _build_active(self, waveform_bins: list[float]) -> list[float]:
        half_count = max(1, math.ceil(self._bar_count / 2))
        half_bars: list[float] = []
        for index in range(half_count):
            position = index / max(half_count - 1, 1)
            energy = _sample(waveform_bins, position)
            edge_weight = 1.0 - position * 0.14
            half_bars.append(_clamp(pow(energy, 0.82) * edge_weight))
        if self._bar_count % 2 == 0:
            return list(reversed(half_bars)) + half_bars
        return list(reversed(half_bars[1:])) + half_bars

    def _smooth(self, target: list[float], mode: str) -> list[float]:
        smoothed: list[float] = []
        for current, end in zip(self._rendered_bars, target, strict=True):
            if mode == "idle":
                response = 0.12
            elif end >= current:
                response = 0.5
            else:
                response = 0.18
            value = current + (end - current) * response
            smoothed.append(0.0 if mode == "idle" and abs(value) < 0.002 else value)
        return smoothed

    def _update_copy(self, payload: Mapping[str, Any], mode: str, recording: bool) -> None:
        elapsed = str(payload.get("elapsed") or "00:00:00")
        db_value = float(payload.get("db") or -80.0)
        device_label = str(payload.get("device_label") or "-").strip() or "-"
        asr_enabled = bool(payload.get("asr_enabled"))
        auto_switch = "开启" if bool(payload.get("auto_switch_enabled")) else "关闭"
        if self._elapsed_text is not None:
            self._elapsed_text.value = elapsed
            self._elapsed_text.color = {"active": "#A34F39", "processing": "#244B3C", "idle": "#1F211E"}[mode]
        if self._state_pill is not None:
            self._state_pill.value = {"active": "LIVE INPUT", "processing": "LISTENING", "idle": "IDLE"}[mode]
            self._state_pill.color = {"active": "#8F432E", "processing": "#2F4F42", "idle": "#6E665D"}[mode]
        if self._state_text is not None:
            self._state_text.value = {
                "active": "波形直接读取当前指定麦克风的音频流。",
                "processing": "当前没有明显输入，波形会回落到静止基线。",
                "idle": "未录音时显示静止基线；有输入时会按设备预览变化。",
            }[mode]
        if self._db_text is not None:
            self._db_text.value = f"{db_value:0.1f} dB"
            self._db_text.color = {"active": "#A34F39", "processing": "#335547", "idle": "#575E58"}[mode]
        if self._device_text is not None:
            self._device_text.value = f"麦克风: {device_label}"
        if self._meta_text is not None:
            self._meta_text.value = f"{'录音中' if recording else '未录音'} / 自动切麦 {auto_switch}"
        if self._hint_text is not None:
            self._hint_text.value = {
                "active": "输入态直接用真实麦克风波形，不再靠状态条高度假装动画。",
                "processing": "静音时只做自然回落，不会再自己生成波形动画。",
                "idle": "待机态显示静止基线，设备有声音时才会动起来。",
            }[mode]
        if self._asr_chip_text is not None:
            self._asr_chip_text.value = "ASR 已开启" if asr_enabled else "ASR 已关闭"
            self._asr_chip_text.color = "#325145" if asr_enabled else "#85552C"

    def _render_canvas(self) -> None:
        if self._canvas is None:
            return
        center_y = self._canvas_height / 2
        baseline_y = center_y - 1
        shapes: list[cv.Shape] = []
        baseline_step = self._canvas_width / (_BASELINE_SEGMENTS + 1)
        for index in range(_BASELINE_SEGMENTS):
            x = baseline_step * (index + 0.5)
            distance = abs(index - (_BASELINE_SEGMENTS - 1) / 2) / max((_BASELINE_SEGMENTS - 1) / 2, 1)
            color = _mix("#D0C7B8", "#FCF9F3", 0.18 + pow(distance, 1.4) * 0.65)
            shapes.append(
                cv.Line(
                    x,
                    baseline_y,
                    x + baseline_step * 0.55,
                    baseline_y,
                    paint=ft.Paint(color=color, stroke_width=2, stroke_cap=ft.StrokeCap.ROUND),
                )
            )
        total_width = self._bar_count * self._bar_width + (self._bar_count - 1) * _BAR_GAP
        start_x = max(0.0, (self._canvas_width - total_width) / 2)
        for index, value in enumerate(self._rendered_bars):
            x = start_x + index * (self._bar_width + _BAR_GAP)
            distance = abs(index - (self._bar_count - 1) / 2) / max((self._bar_count - 1) / 2, 1)
            if self._mode == "idle":
                height = _IDLE_BAR_HEIGHT
            else:
                height = max(
                    _MIN_BAR_HEIGHT,
                    _IDLE_BAR_HEIGHT
                    + pow(_clamp(value), 0.9) * self._canvas_height * _MAX_BAR_HEIGHT_RATIO,
                )
            y = center_y - height / 2
            color = self._bar_color(distance, value)
            shapes.append(
                cv.Rect(
                    x=x,
                    y=y,
                    width=self._bar_width,
                    height=height,
                    border_radius=_BAR_RADIUS,
                    paint=ft.Paint(color=color, style=ft.PaintingStyle.FILL),
                )
            )
        self._canvas.shapes = shapes
        try:
            self._canvas.update()
        except RuntimeError:
            return

    def _bar_color(self, distance: float, value: float) -> str:
        palette = {
            "active": ("#B45D44", "#FCF9F3"),
            "processing": ("#5F7B6D", "#FBF7EF"),
            "idle": ("#E6DFD4", "#FCF9F3"),
        }
        base, fade = palette[self._mode]
        fade_amount = pow(distance, 1.45) * 0.56 + (1.0 - _clamp(value)) * 0.08
        return _mix(base, fade, fade_amount)

    def _update_history(self, payload: Mapping[str, Any], mode: str) -> None:
        asr_enabled = bool(payload.get("asr_enabled"))
        preview = str(payload.get("asr_preview") or "").strip()
        history = [str(line).strip() for line in payload.get("asr_history", []) if str(line).strip()][:3]
        if self._asr_preview_text is not None:
            if asr_enabled:
                self._asr_preview_text.value = f"最近识别: {preview}" if preview else "最近识别: 暂无内容"
                self._asr_preview_text.color = "#364038" if preview else "#69706A"
            else:
                self._asr_preview_text.value = "最近识别: 已关闭实时识别"
                self._asr_preview_text.color = "#85552C"
        placeholders = {
            "active": [
                "这时波形完全来自真实设备输入。",
                "说话一停，波形只会自然回落。",
                "不会再自己生成假的呼吸动画。",
            ],
            "processing": [
                "processing 态只表示刚刚结束输入后的回落。",
                "一旦指定设备重新有声音，就立刻切回 live 输入。",
                "没有声音时不会自己左右起伏。",
            ],
            "idle": [
                "首页波形始终绑定当前设置中的麦克风设备。",
                "未开始录音时也会保留静态基线。",
                "只有检测到真实输入，波形才会变化。",
            ],
        }[mode]
        for index, control in enumerate(self._history_texts):
            if asr_enabled and index < len(history):
                control.value = history[index]
                control.color = "#5A625C"
            else:
                control.value = placeholders[index]
                control.color = "#959C95"
