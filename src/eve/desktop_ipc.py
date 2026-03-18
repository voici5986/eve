from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any
from uuid import uuid4

from .settings import settings_file


def desktop_runtime_dir() -> Path:
    return settings_file().parent / "runtime"


def desktop_feedback_file() -> Path:
    return desktop_runtime_dir() / "recorder_feedback.json"


def desktop_command_dir() -> Path:
    return desktop_runtime_dir() / "commands"


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def write_feedback_snapshot(payload: dict[str, Any]) -> None:
    path = desktop_feedback_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_feedback_snapshot() -> dict[str, Any]:
    path = desktop_feedback_file()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    owner_pid = payload.get("owner_pid")
    if isinstance(owner_pid, int) and owner_pid > 0 and not pid_is_running(owner_pid):
        return {}
    return payload


def desktop_controller_available() -> bool:
    owner_pid = read_feedback_snapshot().get("owner_pid")
    return isinstance(owner_pid, int) and owner_pid > 0 and pid_is_running(owner_pid)


def enqueue_command(kind: str, payload: dict[str, Any] | None = None) -> Path:
    command_dir = desktop_command_dir()
    command_dir.mkdir(parents=True, exist_ok=True)
    command = {
        "id": uuid4().hex,
        "kind": kind,
        "payload": payload or {},
        "timestamp": time.time(),
        "sender_pid": os.getpid(),
    }
    filename = f"{int(command['timestamp'] * 1000)}-{command['sender_pid']}-{command['id']}.json"
    path = command_dir / filename
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(command, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)
    return path


def consume_commands() -> list[dict[str, Any]]:
    command_dir = desktop_command_dir()
    if not command_dir.exists():
        return []
    commands: list[dict[str, Any]] = []
    for path in sorted(command_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            path.unlink(missing_ok=True)
            continue
        if isinstance(payload, dict):
            commands.append(payload)
        path.unlink(missing_ok=True)
    return commands
