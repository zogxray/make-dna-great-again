"""Open the TUI in a separate terminal-emulator window (not a browser)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..core.store import APP_TITLE


def self_command(passthrough: list[str]) -> list[str]:
    """Command line that starts this app without --window."""
    if getattr(sys, "frozen", False):
        return [sys.executable, *passthrough]
    entry = Path(sys.argv[0]).resolve()
    if entry.name in ("ydna", "mdga") or entry.suffix == ".py":
        return [str(entry), *passthrough]
    return [sys.executable, "-m", "ydna", *passthrough]


def _candidates() -> list[tuple[str, list[str]]]:
    """(binary, prefix args before the app command). Prefer env override."""
    forced = (os.environ.get("MDGA_TERMINAL") or "").strip()
    if forced:
        return [(forced, [])]

    out: list[tuple[str, list[str]]] = []
    # gnome-terminal: `gnome-terminal [opts] -- CMD...`
    if shutil.which("gnome-terminal"):
        out.append((
            "gnome-terminal",
            [
                f"--title={APP_TITLE}",
                "--geometry=140x40",
                "--hide-menubar",
                "--",
            ],
        ))
    for name in ("kitty", "alacritty", "konsole", "xfce4-terminal", "tilix", "foot"):
        if shutil.which(name):
            if name == "kitty":
                out.append((name, ["--title", APP_TITLE]))
            elif name == "alacritty":
                out.append((name, ["--title", APP_TITLE, "-e"]))
            elif name == "konsole":
                out.append((name, ["-p", f"tabtitle={APP_TITLE}", "-e"]))
            elif name == "xfce4-terminal":
                out.append((name, [f"--title={APP_TITLE}", f"--geometry=140x40", "-e"]))
            elif name == "tilix":
                out.append((name, ["-t", APP_TITLE, "-e"]))
            elif name == "foot":
                out.append((name, ["-T", APP_TITLE]))
    # Debian/Ubuntu generic
    if shutil.which("x-terminal-emulator"):
        out.append(("x-terminal-emulator", ["-T", APP_TITLE, "-e"]))
    return out


def _window_env() -> dict[str, str]:
    """Env for the GUI terminal child.

    Agent/IDE shells often set NO_COLOR=1 and TERM=dumb; gnome-terminal
    forwards the parent env into the pane, which forces a monochrome TUI.
    """
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    # IDE/agent shells often force monochrome — strip those for the GUI pane.
    for key in ("NO_COLOR", "TERM"):
        env.pop(key, None)
    env["COLORTERM"] = "truecolor"
    env["FORCE_COLOR"] = "1"
    return env


def launch_in_window(passthrough: list[str]) -> int:
    """Spawn a terminal window running the app; return immediately."""
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print(
            "No graphical display (DISPLAY/WAYLAND_DISPLAY). "
            "Run without --window in a terminal.",
            file=sys.stderr,
        )
        return 1

    app_cmd = self_command(passthrough)
    env = _window_env()
    errors: list[str] = []
    for binary, prefix in _candidates():
        path = shutil.which(binary) if binary != "gnome-terminal" else shutil.which("gnome-terminal")
        if not path:
            continue
        # xfce4-terminal -e wants a single string
        if binary == "xfce4-terminal":
            cmd = [path, *prefix, subprocess.list2cmdline(app_cmd)]
        else:
            cmd = [path, *prefix, *app_cmd]
        try:
            subprocess.Popen(cmd, start_new_session=True, env=env)  # noqa: S603
            return 0
        except OSError as exc:
            errors.append(f"{binary}: {exc}")
            continue

    print(
        "Could not open a terminal window. Install gnome-terminal "
        "(or set MDGA_TERMINAL to kitty/alacritty/…).\n"
        + ("\n".join(errors) if errors else "No terminal emulator found."),
        file=sys.stderr,
    )
    return 1
