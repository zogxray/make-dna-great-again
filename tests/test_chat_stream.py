"""Interactions stream helpers — reasoning models must not truncate replies."""

from __future__ import annotations

from types import SimpleNamespace

from ydna.core.chat import _prefer_complete_text, _output_text


def test_prefer_complete_text_takes_longer_final():
    inter = SimpleNamespace(output_text="A" * 200)
    assert _prefer_complete_text("A" * 80, inter) == "A" * 200


def test_prefer_complete_text_keeps_stream_when_longer():
    inter = SimpleNamespace(output_text="short")
    streamed = "streamed-longer-content-here"
    assert _prefer_complete_text(streamed, inter) == streamed


def test_prefer_complete_text_empty_sides():
    assert _prefer_complete_text("", None) == ""
    assert _prefer_complete_text("", SimpleNamespace(output_text="final")) == "final"
    assert _prefer_complete_text("only", SimpleNamespace(output_text="")) == "only"


def test_output_text_from_outputs_list():
    inter = SimpleNamespace(
        output_text=None,
        outputs=[SimpleNamespace(text="hello"), SimpleNamespace(text=" world")],
    )
    assert _output_text(inter) == "hello\n world"
