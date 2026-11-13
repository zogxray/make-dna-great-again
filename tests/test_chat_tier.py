"""send() tier defaults must not clobber M when use_saved=False."""

from __future__ import annotations

from pathlib import Path

import pytest

from ydna.core import chat, llm
from ydna.core.store import Store


@pytest.fixture()
def isolated_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


def test_resolve_model_use_saved_false_uses_tier(isolated_xdg, monkeypatch):
    monkeypatch.setenv("GEMINI_FAST_MODEL", "gemini-3.8-flash")
    monkeypatch.setenv("GEMINI_REASONING_MODEL", "gemini-4-argon")
    store = Store()
    mode = chat.provider_mode(store)
    # Pretend user picked carbon in M.
    chat.set_saved_model(store, chat.wire_model("gemini-4-carbon", mode), mode)
    fast = chat.resolve_model_for_send(store, tier="fast", use_saved=False)
    reason = chat.resolve_model_for_send(store, tier="reasoning", use_saved=False)
    saved = chat.short_model(chat.get_saved_model(store, mode))
    assert chat.short_model(fast) == "gemini-3.8-flash"
    assert chat.short_model(reason) == "gemini-4-argon"
    assert saved == "gemini-4-carbon"
