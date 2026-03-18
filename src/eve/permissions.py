from __future__ import annotations

from dataclasses import dataclass
import subprocess
import sys
import threading
from typing import Literal


PermissionState = Literal[
    "authorized",
    "not_determined",
    "denied",
    "restricted",
    "unsupported",
]


@dataclass(frozen=True)
class PermissionStatus:
    state: PermissionState
    supported: bool
    promptable: bool
    message: str


def microphone_permission_status() -> PermissionStatus:
    if sys.platform != "darwin":
        return PermissionStatus(
            state="unsupported",
            supported=False,
            promptable=False,
            message="当前平台未启用系统级麦克风权限检测。",
        )

    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
    except Exception:
        return PermissionStatus(
            state="unsupported",
            supported=False,
            promptable=False,
            message="当前环境缺少 macOS 麦克风权限组件。",
        )

    status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    if status == 3:
        return PermissionStatus(
            state="authorized",
            supported=True,
            promptable=False,
            message="麦克风权限已授权，可以正常录音。",
        )
    if status == 2:
        return PermissionStatus(
            state="denied",
            supported=True,
            promptable=False,
            message="麦克风权限已被拒绝，请到系统设置里手动开启。",
        )
    if status == 1:
        return PermissionStatus(
            state="restricted",
            supported=True,
            promptable=False,
            message="麦克风权限受系统限制，当前无法申请。",
        )
    return PermissionStatus(
        state="not_determined",
        supported=True,
        promptable=True,
        message="麦克风权限尚未申请，开始录音时会弹出系统授权框。",
    )


def request_microphone_permission(timeout_seconds: float = 60.0) -> PermissionStatus:
    status = microphone_permission_status()
    if not status.supported or not status.promptable:
        return status

    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

    done = threading.Event()
    granted_box = {"value": False}

    def _completion(granted: bool) -> None:
        granted_box["value"] = bool(granted)
        done.set()

    AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVMediaTypeAudio,
        _completion,
    )
    done.wait(timeout_seconds)
    if done.is_set():
        if granted_box["value"]:
            return PermissionStatus(
                state="authorized",
                supported=True,
                promptable=False,
                message="麦克风权限已授权，可以正常录音。",
            )
        return microphone_permission_status()
    return PermissionStatus(
        state="not_determined",
        supported=True,
        promptable=True,
        message="系统授权窗口仍未完成，请在弹窗里允许麦克风访问。",
    )


def open_microphone_privacy_settings() -> bool:
    if sys.platform != "darwin":
        return False
    candidates = [
        ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"],
        ["open", "-b", "com.apple.systempreferences"],
    ]
    for command in candidates:
        try:
            subprocess.Popen(command)
            return True
        except Exception:
            continue
    return False
