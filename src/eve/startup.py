from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import sys
from typing import Sequence


APP_LABEL = "build.nexmoe.eve.desktop"


def desktop_launch_command() -> list[str]:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return [str(executable), "desktop"]
    desktop_launcher = shutil.which("eve-desktop")
    if desktop_launcher:
        return [str(Path(desktop_launcher).resolve())]
    cli_launcher = shutil.which("eve")
    if cli_launcher:
        return [str(Path(cli_launcher).resolve()), "desktop"]
    if sys.platform == "win32":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    return [str(executable), "-m", "eve", "desktop"]


def launch_at_login_enabled() -> bool:
    return _autostart_path().exists()


def set_launch_at_login(enabled: bool, command: Sequence[str] | None = None) -> Path:
    path = _autostart_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return path
    cmd = list(command or desktop_launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        path.write_text(_macos_launch_agent(cmd), encoding="utf-8")
    elif sys.platform.startswith("linux"):
        path.write_text(_linux_autostart_desktop(cmd), encoding="utf-8")
    elif sys.platform == "win32":
        path.write_text(_windows_startup_script(cmd), encoding="utf-8")
    else:
        raise RuntimeError(f"Unsupported platform for launch at login: {sys.platform}")
    return path


def _autostart_path() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "LaunchAgents" / f"{APP_LABEL}.plist"
    if sys.platform.startswith("linux"):
        return home / ".config" / "autostart" / "eve.desktop"
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return (
            appdata
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / "eve-desktop.cmd"
        )
    raise RuntimeError(f"Unsupported platform: {sys.platform}")


def _macos_launch_agent(command: Sequence[str]) -> str:
    program_args = "\n".join(
        f"      <string>{_xml_escape(part)}</string>" for part in command
    )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
            '<plist version="1.0">',
            "<dict>",
            "  <key>Label</key>",
            f"  <string>{APP_LABEL}</string>",
            "  <key>ProgramArguments</key>",
            "  <array>",
            program_args,
            "  </array>",
            "  <key>RunAtLoad</key>",
            "  <true/>",
            "  <key>ProcessType</key>",
            "  <string>Background</string>",
            "</dict>",
            "</plist>",
            "",
        ]
    )


def _linux_autostart_desktop(command: Sequence[str]) -> str:
    exec_line = shlex.join(list(command))
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.0",
            "Name=eve",
            "Comment=Start eve desktop helper",
            f"Exec={exec_line}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )


def _windows_startup_script(command: Sequence[str]) -> str:
    executable, *args = command
    arg_line = " ".join(shlex.quote(arg) for arg in args)
    return "\n".join(
        [
            "@echo off",
            f'start "" "{executable}" {arg_line}',
            "",
        ]
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
