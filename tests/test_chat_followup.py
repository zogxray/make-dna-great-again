"""Follow-up context: nearest model id + local bridge when continue is unavailable."""

from __future__ import annotations

from ydna.core.chat import (
    Message, Thread, context_id, followup_input, last_model_before_user,
)


def _thread(*msgs: Message) -> Thread:
    return Thread(id="t", title="t", messages=list(msgs))


def test_context_id_uses_nearest_model_only():
    t = _thread(
        Message("user", "old"),
        Message("model", "old reply", interaction_id="iid-old"),
        Message("user", "investigate q"),
        Message("model", "plan"),  # no id
        Message("model", "oracle report"),  # no id — nearest
        Message("user", "translate to Russian"),
    )
    assert last_model_before_user(t).text == "oracle report"
    assert context_id(t) is None  # do not fall back to iid-old


def test_context_id_returns_nearest_iid():
    t = _thread(
        Message("user", "q"),
        Message("model", "oracle", interaction_id="iid-oracle"),
        Message("user", "translate"),
    )
    assert context_id(t) == "iid-oracle"


def test_followup_input_embeds_prior():
    prior = Message("model", "## Oracle\nHello report")
    out = followup_input("переведи на русский", prior)
    assert "Hello report" in out
    assert "переведи на русский" in out
    assert "Prior assistant reply" in out


def test_followup_input_empty_prior():
    assert followup_input("hi", None) == "hi"
    assert followup_input("hi", Message("model", "  ")) == "hi"
