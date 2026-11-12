"""Package layering: core must stay free of TUI / CLI / workspaces."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "ydna"
CORE = ROOT / "core"
CLI = ROOT / "cli"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if node.level == 0:
                    out.add(node.module)
                else:
                    # relative: map onto package names from file location
                    out.add(node.module)
            if node.level and node.module:
                top = node.module.split(".")[0]
                out.add(top)
            elif node.level and not node.module:
                continue
    return out


def test_core_does_not_import_ui_or_cli():
    forbidden_tops = {"textual", "rich", "tui", "cli", "workspaces", "prompts"}
    # rich is OK in core? Currently core doesn't use rich - forbid to keep headless clean.
    violations: list[str] = []
    for path in CORE.rglob("*.py"):
        for mod in _imported_modules(path):
            top = mod.split(".")[0]
            if top in forbidden_tops or mod.startswith(
                ("ydna.tui", "ydna.cli", "ydna.workspaces", "ydna.prompts")
            ):
                violations.append(f"{path.relative_to(ROOT.parent)}: {mod}")
    assert not violations, "core layer leak:\n" + "\n".join(violations)


def test_cli_does_not_import_tui_or_workspaces():
    violations: list[str] = []
    for path in CLI.rglob("*.py"):
        for mod in _imported_modules(path):
            top = mod.split(".")[0]
            if top in {"tui", "workspaces", "textual", "rich"} or mod.startswith(
                ("ydna.tui", "ydna.workspaces")
            ):
                violations.append(f"{path.relative_to(ROOT.parent)}: {mod}")
    assert not violations, "cli layer leak:\n" + "\n".join(violations)


def test_dispatcher_recognizes_cli_token():
    from ydna.__main__ import _CLI_TOKENS
    assert "cli" in _CLI_TOKENS
