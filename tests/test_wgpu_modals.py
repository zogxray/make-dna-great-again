"""ModeApp modal contract — no soft-fail hasattr; Tree F opens ask_form."""

from __future__ import annotations

from unittest.mock import MagicMock

from ydna.tui.app_api import require_app
from ydna.tui.wgpu_tree import TreeScreen


def test_require_app_notifies_when_missing():
    screen = MagicMock()
    screen.app = None
    assert require_app(screen, what="filter form") is False
    screen._notify.assert_called_once_with("No app · filter form")


def test_require_app_ok_when_attached():
    screen = MagicMock()
    screen.app = object()
    assert require_app(screen, what="filter form") is True
    screen._notify.assert_not_called()


def test_tree_filter_calls_ask_form():
    screen = TreeScreen()
    screen.ready = True
    screen.flt = {"country": "UA"}
    app = MagicMock()
    screen.app = app
    screen.filter()
    app.ask_form.assert_called_once()
    title, fields, callback = app.ask_form.call_args.args[:3]
    assert "FILTER" in title
    ids = [f[0] for f in fields]
    assert ids == ["country", "lo_raw", "hi_raw", "mind_raw", "minh_raw"]
    assert fields[0][2] == "UA"
    assert callable(callback)


def test_tree_filter_apply_callback_filters():
    screen = TreeScreen()
    screen.ready = True
    screen.flt = {}
    screen.hg_nodes = {
        "R-M269": {
            "placeholder": False,
            "date_mean": 1000,
            "descendants_count": 50,
            "height": 5,
            "flags": [],
        },
        "E-V13": {
            "placeholder": False,
            "date_mean": -500,
            "descendants_count": 10,
            "height": 2,
            "flags": [],
        },
    }
    screen.ctx = None
    applied: list[dict] = []

    def capture(flt: dict) -> None:
        applied.append(flt)

    screen.apply_filter = capture  # type: ignore[method-assign]
    app = MagicMock()
    screen.app = app
    screen.filter()
    callback = app.ask_form.call_args.args[2]
    callback({
        "country": "",
        "lo_raw": "",
        "hi_raw": "",
        "mind_raw": "20",
        "minh_raw": "",
    })
    assert len(applied) == 1
    assert applied[0]["mind"] == 20


def test_tree_filter_no_app_notifies():
    screen = TreeScreen()
    screen.ready = True
    screen.app = None
    notes: list[str] = []
    screen._notify = notes.append  # type: ignore[method-assign]
    screen.filter()
    assert notes == ["No app · filter form"]


def test_data_gemini_requires_app():
    from ydna.tui.wgpu_data import DataScreen

    screen = DataScreen()
    screen.app = None
    notes: list[str] = []
    screen._notify = notes.append  # type: ignore[method-assign]
    screen._set_gemini()
    assert notes == ["No app · Gemini config"]


def test_chat_pick_provider_requires_app():
    from ydna.tui.wgpu_chat import ChatScreen

    screen = ChatScreen()
    screen.app = None
    notes: list[str] = []
    screen._notify = notes.append  # type: ignore[method-assign]
    screen.pick_provider()
    assert notes == ["No app · LLM channel"]


def test_chat_pick_provider_calls_ask_pick(monkeypatch):
    from ydna.core import chat as chat_mod
    from ydna.core import llm
    from ydna.tui.wgpu_chat import ChatScreen

    monkeypatch.setattr(chat_mod, "provider_mode", lambda _s: "vertex")
    monkeypatch.setattr(llm, "PROVIDERS", ("vertex", "aistudio"))
    monkeypatch.setattr(llm, "provider_ready", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(llm, "provider_label", lambda m: m)

    screen = ChatScreen()
    app = MagicMock()
    screen.app = app
    screen.pick_provider()
    app.ask_pick.assert_called_once()
    assert "LLM channel" in app.ask_pick.call_args.args[0]


def test_chat_vertex_config_calls_ask_form(monkeypatch):
    from ydna.core import llm
    from ydna.tui.wgpu_chat import ChatScreen

    monkeypatch.setattr(llm, "studio_api_key", lambda _s: None)
    monkeypatch.setattr(llm, "vertex_project", lambda _s: "")
    monkeypatch.setattr(llm, "vertex_adc_path", lambda _s: None)
    monkeypatch.setattr(llm, "default_adc_path", lambda: None)

    screen = ChatScreen()
    app = MagicMock()
    screen.app = app
    screen._vertex_config()
    app.ask_form.assert_called_once()
    assert "Gemini" in app.ask_form.call_args.args[0]


def test_samples_query_requires_app():
    from ydna.tui.wgpu_samples import SamplesScreen

    screen = SamplesScreen()
    screen.app = None
    notes: list[str] = []
    screen._notify = notes.append  # type: ignore[method-assign]
    screen.query()
    assert notes == ["No app · samples filter"]


def test_samples_query_calls_ask_prompt():
    from ydna.tui.wgpu_samples import SamplesScreen

    screen = SamplesScreen()
    app = MagicMock()
    screen.app = app
    screen.query()
    app.ask_prompt.assert_called_once()
    assert "Filter query" in app.ask_prompt.call_args.args[0]


def test_focus_next_helper_calls_app():
    from ydna.tui.app_api import focus_next

    screen = MagicMock()
    focus_next(screen)
    screen.app.focus_next.assert_called_once()


def test_focus_next_requires_app():
    from ydna.tui.app_api import focus_next

    screen = MagicMock()
    screen.app = None
    focus_next(screen)
    screen._notify.assert_called_once_with("No app · focus next")


def test_chat_delete_requires_confirm_not_silent(monkeypatch):
    from ydna.core import chat as chat_mod
    from ydna.tui.wgpu_chat import ChatScreen

    monkeypatch.setattr(chat_mod, "is_shared_thread", lambda _id: False)

    screen = ChatScreen()
    screen.current = MagicMock(id="t1", title="Hi", messages=[])
    screen._chats_focused = lambda: True  # type: ignore[method-assign]
    app = MagicMock()
    screen.app = app
    screen.delete_chat()
    app.ask_confirm.assert_called_once()
    assert "Delete chat" in app.ask_confirm.call_args.args[0]
