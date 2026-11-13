"""Headless CLI envelope: status + genome summary (+ gwas wiring)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ydna.cli.main import main as cli_main


@pytest.fixture()
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    # Avoid touching the real repo data_dir for source paths.
    return tmp_path


def _run(argv: list[str], capsys) -> tuple[int, dict]:
    code = cli_main(argv)
    out = capsys.readouterr().out.strip()
    return code, json.loads(out)


def test_status_json_ok(isolated_xdg, capsys):
    code, payload = _run(["status"], capsys)
    assert code == 0
    assert payload["ok"] is True
    data = payload["data"]
    assert "version" in data
    assert "sources" in data
    assert "genome" in data
    assert data["lock"]["held_by"] == "cli"


def test_genome_summary_missing(isolated_xdg, capsys):
    code, payload = _run(["genome", "summary"], capsys)
    assert code == 4
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_genome"


def test_gwas_match_missing_genome(isolated_xdg, capsys):
    code, payload = _run(["gwas", "match"], capsys)
    assert code == 4
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_genome"


def test_gwas_search_missing_genome(isolated_xdg, capsys):
    code, payload = _run(["gwas", "search", "vitamin D"], capsys)
    assert code == 4
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_genome"


def test_pgs_search_empty_db(isolated_xdg, capsys):
    code, payload = _run(["pgs", "search", "vitamin"], capsys)
    assert code == 0
    assert payload["ok"] is True
    assert payload["data"]["hits"] == 0


def test_genome_rs_missing(isolated_xdg, capsys):
    code, payload = _run(["genome", "rs", "rs123"], capsys)
    assert code == 4
    assert payload["ok"] is False
    assert payload["error"]["code"] == "no_genome"


def test_investigate_help_lists_command(capsys):
    assert cli_main([]) == 0
    out = capsys.readouterr().out
    assert "investigate" in out


def test_cli_help_exit_zero(capsys):
    assert cli_main([]) == 0
    assert "status" in capsys.readouterr().out
