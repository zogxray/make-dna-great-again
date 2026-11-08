"""F6 Chat: треды на диске и Gemini Interactions API. Без UI."""

from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .jobs import Cancelled, Job
from .store import Store

TOOLS = [{"type": "google_search"}]
# gemini-2.5-flash: только low|high (medium даёт 400 invalid_request).
GEN_CFG = {"max_output_tokens": 65536, "thinking_level": "low"}
SETTING_MODEL = "gemini_model"
# 3.8-flash на free-tier без Search ок, но медленный; 2.5 + Search — лучший дефолт.
DEFAULT_MODEL = "models/gemini-2.5-flash"
_SKIP = ("image", "embedding", "banana", "omni", "antigravity", "deep-research",
         "tts", "veo", "aqa", "robotics", "native-audio", "live")
_models_cache: tuple[float, list["ChatModel"]] | None = None


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Message:
    role: str                     # user · model · error
    text: str
    at: str = field(default_factory=now)
    interaction_id: str | None = None


@dataclass
class Thread:
    id: str
    title: str = "New chat"
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    interaction_id: str | None = None
    notes: str = ""
    messages: list[Message] = field(default_factory=list)
    # Сайдбар: число user-сообщений без загрузки тел (DuckDB list). None → считать из messages.
    user_turns: int | None = None


def _row_thread(row, *, messages: list[Message] | None = None,
                user_turns: int | None = None) -> Thread:
    tid, title, created, updated, iid, notes = row[:6]
    return Thread(
        id=tid, title=title or "New chat",
        created=created or now(), updated=updated or now(),
        interaction_id=iid or None, notes=notes or "",
        messages=messages if messages is not None else [],
        user_turns=user_turns,
    )


def load(tid: str) -> Thread | None:
    from . import db

    row = db.fetchone(
        "SELECT id, title, created, updated, interaction_id, notes "
        "FROM chat.threads WHERE id = ?",
        [tid],
    )
    if not row:
        return None
    msgs_rows = db.fetchall(
        "SELECT role, text, created_at, interaction_id FROM chat.messages "
        "WHERE thread_id = ? ORDER BY seq",
        [tid],
    )
    msgs = [
        Message(role=r, text=t, at=a or now(), interaction_id=iid or None)
        for r, t, a, iid in msgs_rows
    ]
    thread = _row_thread(row, messages=msgs)
    if thread.interaction_id:
        for m in reversed(msgs):
            if m.role == "model" and not m.interaction_id:
                m.interaction_id = thread.interaction_id
                break
    thread.user_turns = sum(1 for m in msgs if m.role == "user")
    return thread


def save(thread: Thread) -> None:
    from . import db

    thread.updated = now()
    thread.user_turns = sum(1 for m in thread.messages if m.role == "user")
    with db.locked():
        con = db.connect()
        con.execute("BEGIN")
        try:
            con.execute(
                "INSERT OR REPLACE INTO chat.threads "
                "(id, title, created, updated, interaction_id, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [thread.id, thread.title, thread.created, thread.updated,
                 thread.interaction_id, thread.notes or ""],
            )
            con.execute("DELETE FROM chat.messages WHERE thread_id = ?", [thread.id])
            if thread.messages:
                con.executemany(
                    "INSERT INTO chat.messages "
                    "(thread_id, seq, role, text, created_at, interaction_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        [thread.id, i, m.role, m.text, m.at or now(), m.interaction_id]
                        for i, m in enumerate(thread.messages)
                    ],
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def save_notes(thread: Thread) -> None:
    """Только NOTES — не трогать messages (иначе stub из list_threads сотрёт историю)."""
    from . import db

    thread.updated = now()
    with db.locked():
        con = db.connect()
        con.execute(
            "UPDATE chat.threads SET notes = ?, updated = ? WHERE id = ?",
            [thread.notes or "", thread.updated, thread.id],
        )


def delete(tid: str) -> None:
    from . import db

    with db.locked():
        con = db.connect()
        con.execute("BEGIN")
        try:
            con.execute("DELETE FROM chat.messages WHERE thread_id = ?", [tid])
            con.execute("DELETE FROM chat.threads WHERE id = ?", [tid])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def new_thread() -> Thread:
    t = Thread(id=uuid.uuid4().hex[:10])
    save(t)
    return t

def last_user_index(thread: Thread) -> int | None:
    for i in range(len(thread.messages) - 1, -1, -1):
        if thread.messages[i].role == "user":
            return i
    return None


def context_id(thread: Thread) -> str | None:
    """previous_interaction_id: последний model перед последним user."""
    stop = last_user_index(thread)
    msgs = thread.messages if stop is None else thread.messages[:stop]
    for m in reversed(msgs):
        if m.role == "model" and m.interaction_id:
            return m.interaction_id
    return None


def _copy_msg(m: Message) -> Message:
    return Message(m.role, m.text, m.at, m.interaction_id)


def replace_user(thread: Thread, index: int, text: str) -> None:
    """Обрезает тред после user[index] и ставит новый текст. Тот же чат."""
    thread.messages = [_copy_msg(m) for m in thread.messages[:index]]
    thread.messages.append(Message("user", text))
    if index == 0:
        thread.title = text.splitlines()[0][:48]
    thread.interaction_id = context_id(thread)
    save(thread)


def fork_at(src: Thread, index: int, text: str) -> Thread:
    """Новая ветка: история до user[index], затем отредактированный текст."""
    t = Thread(id=uuid.uuid4().hex[:10], title=text.splitlines()[0][:48])
    t.messages = [_copy_msg(m) for m in src.messages[:index]]
    t.messages.append(Message("user", text))
    t.interaction_id = context_id(t)
    save(t)
    return t


def list_threads() -> list[Thread]:
    """Сайдбар: метаданные + user_turns, без тел сообщений."""
    from . import db

    rows = db.fetchall(
        "SELECT t.id, t.title, t.created, t.updated, t.interaction_id, t.notes, "
        "  (SELECT COUNT(*) FROM chat.messages m "
        "   WHERE m.thread_id = t.id AND m.role = 'user') AS user_turns "
        "FROM chat.threads t "
        "ORDER BY t.updated DESC"
    )
    return [_row_thread(r[:6], user_turns=int(r[6] or 0)) for r in rows]


def api_key(store: Store) -> str | None:
    return store.secret("gemini") or (os.environ.get("GEMINI_API_KEY") or "").strip() or None


def _client(key: str):
    """google-genai 3.x по умолчанию api_version=v1 — там нет Search и thinking; нужен v1beta."""
    from google import genai
    from google.genai import types
    return genai.Client(api_key=key, http_options=types.HttpOptions(api_version="v1beta"))


def _is_rate(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    text = str(exc)
    return code == 429 or status == "RESOURCE_EXHAUSTED" or "429" in text[:80]


def _friendly(exc: BaseException, model: str = "") -> str:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    msg = (getattr(exc, "message", None) or "").strip() or str(exc).strip()
    who = f" · {short_model(model)}" if model else ""
    if _is_rate(exc):
        tip = " Try M → gemini-2.5-flash (3.8 often hits free-tier quota)."
        bit = msg.split("For more information")[0].strip().rstrip(".")
        if bit and "429" not in bit and "quota" in bit.lower():
            return f"Gemini quota (429){who}: {bit[:180]}.{tip}"
        return f"Gemini rate limit / quota (429){who}.{tip}"
    if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        return "Gemini key rejected. Check the key in F2 (Google Gemini · P)."
    if code:
        bit = (msg.split(".")[0] if msg else status)[:160]
        return f"Gemini HTTP {code}{who}" + (f": {bit}" if bit else "")
    return str(exc)[:400]


@dataclass
class ChatModel:
    id: str
    name: str
    label: str
    thinking: bool


def short_model(mid: str) -> str:
    return (mid or "").split("/", 1)[-1]


def api_model(mid: str) -> str:
    """Interactions API ждёт models/<id>, как в примере Google."""
    name = short_model(mid) or short_model(DEFAULT_MODEL)
    return f"models/{name}"


def tools_for(mid: str) -> list[dict]:
    """Google Search: на free tier у Gemini 3+ Flash grounding Not available (см. pricing).
    Playground без Search работает; наш чат раньше слал tools всегда → 429 на 3.8."""
    name = short_model(mid).lower()
    if name.startswith("gemini-3"):
        return []
    return list(TOOLS)


def _version_key(name: str) -> tuple:
    """Новее — больше. Flash без lite выше lite той же версии."""
    n = short_model(name).lower()
    nums = [int(x) for x in re.findall(r"\d+", n)]
    nums += [0, 0, 0]
    return (nums[0], nums[1], nums[2], 0 if "lite" in n else 1)


def _keep_model(name: str, actions: list[str]) -> bool:
    n = short_model(name).lower()
    if "generateContent" not in actions:
        return False
    if not n.startswith("gemini-"):
        return False
    if any(tag in n for tag in _SKIP):
        return False
    if "pro" in n.split("-"):
        return False
    return "flash" in n


def list_models(store: Store, force: bool = False) -> list[ChatModel]:
    """Flash / Flash-Lite с generateContent. Свежий список с API, без зашитой версии."""
    global _models_cache
    if not force and _models_cache and time.monotonic() - _models_cache[0] < 600:
        return _models_cache[1]
    key = api_key(store)
    if not key:
        raise RuntimeError("set the Gemini API key in F2 (Google Gemini · P)")
    try:
        client = _client(key)
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    rows: list[ChatModel] = []
    try:
        listed = list(client.models.list())
    except Exception as exc:
        raise RuntimeError(_friendly(exc)) from exc
    for m in listed:
        mid = api_model(m.name or "")
        name = short_model(mid)
        if not _keep_model(name, list(m.supported_actions or [])):
            continue
        rows.append(ChatModel(
            id=mid, name=name,
            label=(m.display_name or name).strip(),
            thinking=bool(getattr(m, "thinking", False)),
        ))
    rows.sort(key=lambda m: _version_key(m.name), reverse=True)
    if not rows:
        raise RuntimeError("Gemini API returned no free Flash models")
    _models_cache = (time.monotonic(), rows)
    return rows


def default_model(models: list[ChatModel]) -> ChatModel | None:
    want = short_model(DEFAULT_MODEL).lower()
    for m in models:
        if m.name.lower() == want:
            return m
    # Иначе предпочитаем 2.5-flash (не lite): у 3.x на free key часто quota 429.
    for prefer in ("gemini-2.5-flash", "gemini-2.0-flash"):
        for m in models:
            if m.name.lower() == prefer:
                return m
    full = [m for m in models if "lite" not in m.name.lower() and not m.name.lower().startswith("gemini-3")]
    if not full:
        full = [m for m in models if "lite" not in m.name.lower()]
    return (full or models)[0] if (full or models) else None


def chosen_model(store: Store, models: list[ChatModel] | None = None) -> str | None:
    saved = store.setting(SETTING_MODEL)
    if models is None:
        try:
            models = list_models(store)
        except Exception:
            return api_model(saved) if saved else None
    by_id = {m.id: m for m in models}
    by_name = {m.name: m for m in models}
    if saved in by_id:
        return saved
    if saved and short_model(saved) in by_name:
        return by_name[short_model(saved)].id
    pick = default_model(models)
    return pick.id if pick else (api_model(saved) if saved else None)


def resolve_model(store: Store) -> tuple[str, ChatModel | None]:
    """Модель для send: сохранённая, если ещё в списке, иначе дефолт Google. Пишет в Store."""
    saved = store.setting(SETTING_MODEL)
    try:
        models = list_models(store)
    except Exception:
        mid = api_model(saved or DEFAULT_MODEL)
        return mid, None
    mid = chosen_model(store, models) or DEFAULT_MODEL
    mid = api_model(mid)
    if saved != mid:
        store.set_setting(SETTING_MODEL, mid)
    info = next((m for m in models if m.id == mid or m.name == short_model(mid)), None)
    return mid, info


def model_label(store: Store) -> str:
    saved = store.setting(SETTING_MODEL)
    return short_model(saved) if saved else short_model(DEFAULT_MODEL)


def _piece(item: object) -> str:
    if isinstance(item, str):
        return item
    t = getattr(item, "text", None)
    if t:
        return str(t)
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return ""


def _output_text(interaction: object) -> str:
    text = getattr(interaction, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text
    chunks: list[str] = []
    for attr in ("outputs", "output", "steps"):
        items = getattr(interaction, attr, None)
        if isinstance(items, str) and items.strip():
            return items
        if isinstance(items, list):
            for item in items:
                p = _piece(item)
                if p:
                    chunks.append(p)
                contents = getattr(item, "contents", None) or getattr(item, "content", None)
                if isinstance(contents, list):
                    chunks.extend(_piece(c) for c in contents)
                elif contents:
                    chunks.append(_piece(contents))
    return "\n".join(c for c in chunks if c).strip()


def send(store: Store, thread: Thread, job: Job) -> str:
    """Отправляет последний user-текст через Interactions API, как в примере Google."""
    key = api_key(store)
    if not key:
        raise RuntimeError("set the Gemini API key in F2 (Google Gemini · P)")
    try:
        client = _client(key)
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    text = next((m.text for m in reversed(thread.messages) if m.role == "user"), "")
    if not text.strip():
        raise RuntimeError("empty message")
    saved = store.setting(SETTING_MODEL) or ""
    # прошлый generateContent-alias; Interactions + Search требуют v1beta
    if short_model(saved) in ("", "gemini-flash-latest", "gemini-flash-lite-latest"):
        mid = DEFAULT_MODEL
    else:
        mid = api_model(saved)
    if saved != mid:
        store.set_setting(SETTING_MODEL, mid)
    job.set_stage(short_model(mid))
    job.check()
    gen = dict(GEN_CFG)
    name = short_model(mid).lower()
    if "lite" in name:
        gen.pop("thinking_level", None)
    # 3.x Flash: official thinking levels differ; medium/high ok on 3.8, low safest
    if name.startswith("gemini-3") and "thinking_level" in gen:
        gen["thinking_level"] = "low"
    kw: dict = {
        "model": mid,
        "input": text,
        "generation_config": gen,
    }
    tools = tools_for(mid)
    if tools:
        kw["tools"] = tools
    prev = context_id(thread)
    if prev:
        kw["previous_interaction_id"] = prev
    kw["stream"] = True
    job.draft = ""
    try:
        stream = client.interactions.create(**kw)
    except Exception as exc:
        raise RuntimeError(_friendly(exc, mid)) from exc
    chunks: list[str] = []
    iid: str | None = None
    try:
        for event in stream:
            job.check()
            et = getattr(event, "event_type", None) or ""
            if et in ("interaction.created", "interaction.completed"):
                inter = getattr(event, "interaction", None)
                if inter is not None and getattr(inter, "id", None):
                    iid = str(inter.id)
                if et == "interaction.completed" and not chunks and inter is not None:
                    fallback = _output_text(inter)
                    if fallback:
                        chunks.append(fallback)
                        job.draft = fallback
            elif et == "step.delta":
                delta = getattr(event, "delta", None)
                if delta is None or (getattr(delta, "type", None) or "") != "text":
                    continue
                piece = getattr(delta, "text", None) or ""
                if not piece:
                    continue
                chunks.append(piece)
                job.draft = "".join(chunks)
                job.stage = f"{short_model(mid)} · {len(job.draft):,} chars"
    except Cancelled:
        raise
    except Exception as exc:
        raise RuntimeError(_friendly(exc, mid)) from exc
    job.check()
    out = "".join(chunks).strip() or "(empty reply)"
    job.draft = out
    if iid:
        thread.interaction_id = iid
        thread.messages.append(Message("model", out, interaction_id=iid))
    else:
        thread.messages.append(Message("model", out))
    if thread.title == "New chat":
        thread.title = text.strip().splitlines()[0][:48]
    save(thread)
    return out[:80]
