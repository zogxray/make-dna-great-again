"""Системный буфер обмена.

Textual по умолчанию держит свой clipboard и шлёт OSC 52; в части терминалов
это не попадает в wl-clipboard / X11. Читаем и пишем через системные утилиты,
OSC 52 оставляем как запасной путь на стороне App.copy_to_clipboard.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def read_clipboard() -> str:
    commands: list[list[str]] = []
    if os.name == "nt":
        commands.append(["powershell", "-NoProfile", "-Command", "Get-Clipboard"])
    elif sys.platform == "darwin":
        commands.append(["pbpaste"])
    else:
        if shutil.which("wl-paste"):
            commands.append(["wl-paste", "-n"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard", "-o"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--output"])
    for cmd in commands:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=0.4)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0 and r.stdout:
            return r.stdout
    return ""


def write_clipboard(text: str) -> bool:
    """Пишет в системный буфер. True, если внешняя команда приняла текст."""
    commands: list[list[str]] = []
    if os.name == "nt":
        commands.append(["clip"])
    elif sys.platform == "darwin":
        commands.append(["pbcopy"])
    else:
        if shutil.which("wl-copy"):
            commands.append(["wl-copy"])
        if shutil.which("xclip"):
            commands.append(["xclip", "-selection", "clipboard"])
        if shutil.which("xsel"):
            commands.append(["xsel", "--clipboard", "--input"])
    for cmd in commands:
        try:
            subprocess.run(
                cmd,
                input=text,
                text=True,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            continue
    return False
