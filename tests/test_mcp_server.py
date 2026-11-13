"""MCP server wraps the same ops as the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from ydna.cli import ops
from ydna.cli.jsonio import CliError
from ydna.mcp.server import build_server


@pytest.fixture()
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


def test_build_server_registers_tools():
    server = build_server()
    tools = server._tool_manager.list_tools()  # noqa: SLF001 — FastMCP internal
    names = sorted(t.name for t in tools)
    assert "investigate" in names
    assert "story" in names
    assert "y_path" in names
    assert "aadr_y_hits" in names
    assert "modern_affinity" in names
    assert "gwas_search" in names
    assert "status" in names


def test_status_tool_envelope(isolated_xdg):
    server = build_server()
    fn = server._tool_manager.get_tool("status").fn  # noqa: SLF001
    raw = fn()
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert "version" in payload["data"]


def test_genome_summary_tool_missing(isolated_xdg):
    server = build_server()
    fn = server._tool_manager.get_tool("genome_summary").fn  # noqa: SLF001
    payload = json.loads(fn())
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_genome"


def test_ops_envelope_helper():
    assert ops.envelope({"a": 1}) == {"ok": True, "data": {"a": 1}}
    err = CliError("x", "y")
    assert ops.envelope(error=err) == {"ok": False, "error": {"code": "x", "message": "y"}}
