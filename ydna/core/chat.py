"""F7 Chat: треды на диске и Gemini Interactions API. Без UI."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .jobs import Cancelled, Job
from . import llm
from .store import Store

TOOLS = [{"type": "google_search"}]
# gemini-2.5-flash: только low|high (medium даёт 400 invalid_request).
GEN_CFG = {"max_output_tokens": 65536, "thinking_level": "low"}
# Legacy single key — migrated into per-provider settings on read.
SETTING_MODEL = "gemini_model"
SETTING_MODEL_AISTUDIO = "gemini_model_aistudio"
SETTING_MODEL_VERTEX = "gemini_model_vertex"
SETTING_PROVIDER = llm.SETTING_PROVIDER
# Studio free-tier: 2.5-flash + Search is the safe default; Vertex can use newer tops.
DEFAULT_MODEL = "models/gemini-2.5-flash"
DEFAULT_MODEL_VERTEX = "gemini-4-argon"
# Curated tops per channel (merged with live list; used alone if list fails).
_STATIC_AISTUDIO = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
)
_STATIC_VERTEX = (
    "gemini-4-argon",
    "gemini-4-carbon",
    "gemini-3.1-pro-preview",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
)
# Network: without these, a stalled SSE blocks forever (no progress, C useless).
CONNECT_S = 15.0
READ_IDLE_S = 90.0          # no bytes from the wire → fail (Studio)
READ_IDLE_VERTEX_S = 240.0  # argon/pro think a long time before first text delta
WRITE_S = 60.0
WALL_S = 600.0              # whole Interactions call ceiling
WALL_MS = int(WALL_S * 1000)
# Shared F7 threads for mode-embedded asks (stable ids; titles show current F-keys).
REF_THREAD_ID = "ref-f5"
REF_THREAD_TITLE = "Reference · F6"
GWAS_THREAD_ID = "gwas-f5"
GWAS_THREAD_TITLE = "GWAS · F5"
TREE_THREAD_ID = "tree-f3"
TREE_THREAD_TITLE = "Tree · F3"
# Pinned special chats (mode-embedded asks). No free-form Send from F7.
_SHARED_THREAD_ORDER = (TREE_THREAD_ID, GWAS_THREAD_ID, REF_THREAD_ID)
_SHARED_THREAD_IDS = frozenset(_SHARED_THREAD_ORDER)
_SHARED_TITLES = {
    REF_THREAD_ID: REF_THREAD_TITLE,
    GWAS_THREAD_ID: GWAS_THREAD_TITLE,
    TREE_THREAD_ID: TREE_THREAD_TITLE,
}
_SKIP = ("image", "embedding", "banana", "omni", "antigravity", "deep-research",
         "tts", "veo", "aqa", "robotics", "native-audio", "live", "imagen", "gemma",
         "transcribe", "translate")
# Per-provider model list cache: mode → (monotonic_ts, rows)
_models_cache: dict[str, tuple[float, list["ChatModel"]]] = {}


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


def _pack_messages(messages: list[Message]) -> str:
    return json.dumps(
        [
            {
                "role": m.role,
                "text": m.text,
                "at": m.at or now(),
                "interaction_id": m.interaction_id,
            }
            for m in messages
        ],
        ensure_ascii=False,
    )


def _unpack_messages(raw) -> list[Message] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(raw, list):
        return None
    out: list[Message] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(Message(
            role=str(item.get("role") or "user"),
            text=str(item.get("text") or ""),
            at=str(item.get("at") or now()),
            interaction_id=item.get("interaction_id") or None,
        ))
    return out


def _legacy_messages(tid: str) -> list[Message]:
    from . import db

    try:
        msgs_rows = db.fetchall(
            "SELECT role, text, created_at, interaction_id FROM chat.messages "
            "WHERE thread_id = ? ORDER BY seq",
            [tid],
        )
    except Exception:  # noqa: BLE001
        return []
    return [
        Message(role=r, text=t, at=a or now(), interaction_id=iid or None)
        for r, t, a, iid in msgs_rows
    ]


def load(tid: str) -> Thread | None:
    from . import db

    row = db.fetchone(
        "SELECT id, title, created, updated, interaction_id, notes, messages_json "
        "FROM chat.threads WHERE id = ?",
        [tid],
    )
    if not row:
        return None
    msgs = _unpack_messages(row[6] if len(row) > 6 else None)
    if msgs is None:
        msgs = _legacy_messages(tid)
    thread = _row_thread(row[:6], messages=msgs)
    if thread.interaction_id:
        for m in reversed(msgs):
            if m.role == "model" and not m.interaction_id:
                m.interaction_id = thread.interaction_id
                break
    thread.user_turns = sum(1 for m in msgs if m.role == "user")
    return thread


def _save_once(thread: Thread) -> None:
    """Write thread + messages_json only — never mutate chat.messages (DuckDB ART)."""
    from . import db

    payload = _pack_messages(thread.messages)
    n_user = int(thread.user_turns or 0)
    with db.locked():
        con = db.connect()
        con.execute(
            "INSERT INTO chat.threads "
            "(id, title, created, updated, interaction_id, notes, "
            " messages_json, user_turns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?) "
            "ON CONFLICT (id) DO UPDATE SET "
            "title = excluded.title, "
            "updated = excluded.updated, "
            "interaction_id = excluded.interaction_id, "
            "notes = excluded.notes, "
            "messages_json = excluded.messages_json, "
            "user_turns = excluded.user_turns",
            [
                thread.id, thread.title, thread.created, thread.updated,
                thread.interaction_id, thread.notes or "", payload, n_user,
            ],
        )


def save(thread: Thread) -> None:
    from . import db

    thread.updated = now()
    thread.user_turns = sum(1 for m in thread.messages if m.role == "user")
    try:
        _save_once(thread)
    except Exception as exc:
        if not db._is_invalidated(exc):
            raise
        db._reset()
        _save_once(thread)


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

    if tid in _SHARED_THREAD_IDS:
        title = _SHARED_TITLES.get(tid, "Shared chat")
        raise RuntimeError(f"{title} is pinned — do not delete")

    def _once() -> None:
        with db.locked():
            con = db.connect()
            # Do not touch chat.messages — ART index there is corrupt-prone.
            con.execute("DELETE FROM chat.threads WHERE id = ?", [tid])

    try:
        _once()
    except Exception as exc:
        if not db._is_invalidated(exc):
            raise
        db._reset()
        _once()


def new_thread() -> Thread:
    t = Thread(id=uuid.uuid4().hex[:10])
    save(t)
    return t


def _ensure_shared(tid: str, title: str) -> Thread:
    t = load(tid)
    if t is not None:
        if t.title != title:
            t.title = title
            save(t)
        return t
    t = Thread(id=tid, title=title)
    save(t)
    return t


def ensure_reference_thread() -> Thread:
    """Load or create the single Reference SUMMARY chat."""
    return _ensure_shared(REF_THREAD_ID, REF_THREAD_TITLE)


def ensure_gwas_thread() -> Thread:
    """Load or create the single F5 GWAS ASK chat."""
    return _ensure_shared(GWAS_THREAD_ID, GWAS_THREAD_TITLE)


def ensure_tree_thread() -> Thread:
    """Load or create the single F3 Tree ASK chat."""
    return _ensure_shared(TREE_THREAD_ID, TREE_THREAD_TITLE)


def ensure_shared_threads() -> None:
    ensure_tree_thread()
    ensure_gwas_thread()
    ensure_reference_thread()


def is_reference_thread(tid: str | None) -> bool:
    return bool(tid) and tid == REF_THREAD_ID


def is_gwas_thread(tid: str | None) -> bool:
    return bool(tid) and tid == GWAS_THREAD_ID


def is_tree_thread(tid: str | None) -> bool:
    return bool(tid) and tid == TREE_THREAD_ID


def is_shared_thread(tid: str | None) -> bool:
    return bool(tid) and tid in _SHARED_THREAD_IDS


def shared_thread_label(tid: str | None) -> str:
    if not tid:
        return "Shared chat"
    return _SHARED_TITLES.get(tid, "Shared chat")


def last_user_index(thread: Thread) -> int | None:
    for i in range(len(thread.messages) - 1, -1, -1):
        if thread.messages[i].role == "user":
            return i
    return None


def last_model_before_user(thread: Thread) -> Message | None:
    """Nearest model message before the latest user turn (skip errors)."""
    stop = last_user_index(thread)
    msgs = thread.messages if stop is None else thread.messages[:stop]
    for m in reversed(msgs):
        if m.role == "model":
            return m
        if m.role == "user":
            break
    return None


def context_id(thread: Thread) -> str | None:
    """previous_interaction_id for the nearest prior model only.

    Do not skip to an older model that has an id — after Investigate the Oracle
    may lack an id while an earlier free-chat turn still has one; continuing
    that older id would ignore the report the user just saw.
    """
    prior = last_model_before_user(thread)
    if prior is None:
        return None
    return prior.interaction_id or None


_FOLLOWUP_PRIOR_MAX = 40_000


def followup_input(user_text: str, prior_model: Message | None) -> str:
    """Embed prior model text when API continue is unavailable (Vertex / no iid)."""
    user = (user_text or "").strip()
    if prior_model is None or not (prior_model.text or "").strip():
        return user
    prior = prior_model.text.strip()
    if len(prior) > _FOLLOWUP_PRIOR_MAX:
        prior = prior[: _FOLLOWUP_PRIOR_MAX - 20].rstrip() + "\n…[truncated]"
    return "\n".join([
        "### Prior assistant reply (same chat thread — treat as the report/context)",
        prior,
        "",
        "### User request",
        user,
    ])


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
    """Сайдбар: pinned shared chats first, then others by updated DESC."""
    from . import db

    rows = db.fetchall(
        "SELECT id, title, created, updated, interaction_id, notes, "
        "COALESCE(user_turns, 0) "
        "FROM chat.threads "
        "ORDER BY updated DESC"
    )
    threads = [_row_thread(r[:6], user_turns=int(r[6] or 0)) for r in rows]
    by_id = {t.id: t for t in threads}
    pinned = [by_id[tid] for tid in _SHARED_THREAD_ORDER if tid in by_id]
    rest = [t for t in threads if t.id not in _SHARED_THREAD_IDS]
    return pinned + rest


def api_key(store: Store) -> str | None:
    """AI Studio key only (F2 / GEMINI_API_KEY). Vertex does not use a key."""
    return llm.studio_api_key(store)


def provider_mode(store: Store) -> llm.Provider:
    return llm.provider_mode(store)


def provider_ready(store: Store) -> tuple[bool, str]:
    return llm.provider_ready(store)


def can_send(store: Store) -> str | None:
    """None if the chosen provider is ready; else a short error for notify()."""
    return llm.can_send(store)


def cycle_provider(store: Store) -> llm.Provider:
    return llm.cycle_provider(store)


def _http_timeout():
    """Per-hop timeout (google-genai 3.x ships httpx2, not httpx)."""
    import httpx2
    return httpx2.Timeout(
        connect=CONNECT_S, read=READ_IDLE_S, write=WRITE_S, pool=CONNECT_S,
    )


def _client(store: Store):
    """Client for the explicitly chosen provider (Vertex ADC or AI Studio key)."""
    mode = provider_mode(store)
    read_s = READ_IDLE_VERTEX_S if mode == "vertex" else READ_IDLE_S
    return llm.make_client(
        store,
        connect_s=CONNECT_S,
        read_idle_s=read_s,
        write_s=WRITE_S,
        wall_ms=WALL_MS,
    )


def wait_line(job: Job) -> str:
    """Live status for UI ticks — elapsed grows even while the SSE is silent."""
    elapsed = max(0, int(time.time() - (job.started or time.time())))
    raw = (job.stage or "").strip()
    # Worker may already append " · 12s"; strip so the UI owns the clock.
    phase = re.sub(r" · \d+s$", "", raw).strip() or short_model(DEFAULT_MODEL)
    n = len(job.draft or "")
    if n:
        return f"{phase} · {n:,} chars · {elapsed}s"
    if elapsed < 3:
        return f"{phase} · {elapsed}s"
    if elapsed < 30:
        return f"{phase} · {elapsed}s · first token…"
    return f"{phase} · {elapsed}s · still waiting (Search/thinking can be slow)"


def abort_send(job: Job | None) -> None:
    """Unblock a hung send(): close stream/client so cancel is not stuck on SSE wait."""
    if job is None:
        return
    stream = getattr(job, "_gemini_stream", None)
    if stream is not None:
        for name in ("close", "aclose", "cancel"):
            fn = getattr(stream, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break
        try:
            delattr(job, "_gemini_stream")
        except Exception:
            pass
    client = getattr(job, "_gemini_client", None)
    if client is not None:
        for name in ("close", "aclose"):
            fn = getattr(client, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
                break
        try:
            delattr(job, "_gemini_client")
        except Exception:
            pass


def _is_rate(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    text = str(exc)
    return code == 429 or status == "RESOURCE_EXHAUSTED" or "429" in text[:80]


def _is_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    return (
        "timeout" in name
        or "timed out" in text
        or "timeout" in text[:120]
        or "deadline" in text[:120]
    )


def _friendly(exc: BaseException, model: str = "", *,
              mode: llm.Provider | None = None) -> str:
    code = getattr(exc, "code", None)
    status = str(getattr(exc, "status", "") or "").upper()
    msg = (getattr(exc, "message", None) or "").strip() or str(exc).strip()
    who = f" · {short_model(model)}" if model else ""
    channel = llm.provider_label(mode) if mode else "Gemini"
    if _is_timeout(exc):
        return (f"{channel} timed out{who} "
                f"(idle {int(READ_IDLE_S)}s / wall {int(WALL_S)}s). Try again or M → another model.")
    if _is_rate(exc):
        tip = " Try M → gemini-2.5-flash (3.8 often hits free-tier quota)."
        bit = msg.split("For more information")[0].strip().rstrip(".")
        if bit and "429" not in bit and "quota" in bit.lower():
            return f"{channel} quota (429){who}: {bit[:180]}.{tip}"
        return f"{channel} rate limit / quota (429){who}.{tip}"
    if code in (401, 403) or status in ("UNAUTHENTICATED", "PERMISSION_DENIED"):
        if mode == "vertex" or "quota project" in msg.lower() or "ADC" in msg:
            return (f"Vertex auth/permission failed{who}. "
                    f"ADC at {llm.adc_hint()}; set GOOGLE_CLOUD_PROJECT / "
                    f"GOOGLE_CLOUD_QUOTA_PROJECT. No API key on this channel.")
        return "AI Studio key rejected. Check the key in F2 (Google Gemini · P)."
    if code:
        bit = (msg.split(".")[0] if msg else status)[:160]
        return f"{channel} HTTP {code}{who}" + (f": {bit}" if bit else "")
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
    """AI Studio / Interactions: models/<id>."""
    name = short_model(mid) or short_model(DEFAULT_MODEL)
    return f"models/{name}"


def wire_model(mid: str, mode: llm.Provider) -> str:
    """Model id as the chosen API expects it."""
    name = short_model(mid) or (
        short_model(DEFAULT_MODEL_VERTEX) if mode == "vertex" else short_model(DEFAULT_MODEL)
    )
    return name if mode == "vertex" else api_model(name)


def model_setting_key(mode: llm.Provider | None = None,
                      store: Store | None = None) -> str:
    m = mode or provider_mode(store)
    return SETTING_MODEL_VERTEX if m == "vertex" else SETTING_MODEL_AISTUDIO


def get_saved_model(store: Store, mode: llm.Provider | None = None) -> str:
    """Per-channel saved model. Migrates legacy gemini_model once."""
    m = mode or provider_mode(store)
    key = model_setting_key(m)
    saved = (store.setting(key) or "").strip()
    if saved:
        return saved
    legacy = (store.setting(SETTING_MODEL) or "").strip()
    if legacy:
        store.set_setting(key, legacy)
        return legacy
    return ""


def set_saved_model(store: Store, mid: str, mode: llm.Provider | None = None) -> str:
    m = mode or provider_mode(store)
    wired = wire_model(mid, m)
    store.set_setting(model_setting_key(m), wired)
    # Keep legacy key in sync for older UI bits.
    store.set_setting(SETTING_MODEL, wired)
    return wired


def tools_for(mid: str, mode: llm.Provider | None = None) -> list[dict]:
    """Google Search: Studio only; Gemini 3+ free tier often rejects grounding."""
    if mode == "vertex":
        return []
    name = short_model(mid).lower()
    if name.startswith("gemini-3"):
        return []
    return list(TOOLS)


def _version_key(name: str) -> tuple:
    """Новее / «топ» выше: major.minor → reasoning/pro → flash → not-lite → GA."""
    n = short_model(name).lower()
    nums = [int(x) for x in re.findall(r"\d+", n)]
    nums += [0, 0, 0]
    if any(tag in n for tag in ("argon", "carbon", "ultra")):
        kind = 4
    elif "pro" in n.split("-"):
        kind = 3
    elif "flash" in n:
        kind = 2
    else:
        kind = 1
    lite = 0 if "lite" in n else 1
    ga = 0 if ("preview" in n or "exp" in n) else 1
    return (nums[0], nums[1], nums[2], kind, lite, ga)


def _keep_model(name: str, actions: list[str] | None) -> bool:
    """Accept gemini chat models (flash/pro/argon/…). Skip media / embeddings."""
    n = short_model(name).lower()
    if not n.startswith("gemini-"):
        return False
    if any(tag in n for tag in _SKIP):
        return False
    if actions:
        # Prefer generateContent; also allow empty-action Vertex rows that look like chat.
        if "generateContent" not in actions and "predict" not in actions:
            return False
    return True


def _static_models(store: Store, mode: llm.Provider) -> list[ChatModel]:
    """Channel-local curated tops — never mix Studio ids into Vertex or vice versa."""
    base = _STATIC_VERTEX if mode == "vertex" else _STATIC_AISTUDIO
    names: list[str] = []
    for n in (llm.default_model_for(mode, tier="reasoning"),
              llm.default_model_for(mode, tier="fast"),
              *base):
        sn = short_model(n)
        if sn and sn not in names:
            names.append(sn)
    saved = short_model(get_saved_model(store, mode))
    if saved and saved not in names:
        names.insert(0, saved)
    rows = [
        ChatModel(
            id=wire_model(n, mode),
            name=short_model(n),
            label=short_model(n),
            thinking=("pro" in short_model(n).lower().split("-")
                      or "argon" in short_model(n).lower()
                      or "carbon" in short_model(n).lower()),
        )
        for n in names
    ]
    rows.sort(key=lambda m: _version_key(m.name), reverse=True)
    return rows


def _merge_models(live: list[ChatModel], static: list[ChatModel]) -> list[ChatModel]:
    """Live API rows plus curated tops (so flagship ids never disappear)."""
    by_name: dict[str, ChatModel] = {}
    for m in static + live:
        by_name[m.name.lower()] = m
    rows = list(by_name.values())
    rows.sort(key=lambda m: _version_key(m.name), reverse=True)
    return rows


def _list_client(store: Store, mode: llm.Provider):
    """Lighter client for models.list — separate from the send/Interactions client."""
    from google import genai
    if mode == "aistudio":
        key = llm.studio_api_key(store)
        if not key:
            raise RuntimeError("AI Studio: set key in F2 · Google Gemini · P")
        # Default api_version for list — v1beta Interactions options can 400 on list.
        return genai.Client(api_key=key)
    llm.apply_vertex_adc(store)
    project = llm.vertex_project(store)
    if not project:
        raise RuntimeError("Vertex: set Cloud project in F2 · Google Gemini · P")
    return genai.Client(
        vertexai=True,
        project=project,
        location=llm.vertex_location(store),
    )


def list_models(store: Store, force: bool = False) -> list[ChatModel]:
    """Live model list for the *current* channel only. Falls back to curated static.

    Always returns a list for the channel (so M can open even if credentials are incomplete).
    """
    global _models_cache
    mode = provider_mode(store)
    if not force:
        hit = _models_cache.get(mode)
        if hit and time.monotonic() - hit[0] < 600:
            return hit[1]
    static = _static_models(store, mode)
    # Incomplete credentials → curated tops only (still lets the user pick a model id).
    if can_send(store) is not None:
        _models_cache[mode] = (time.monotonic(), static)
        return static
    rows: list[ChatModel] = []
    try:
        client = _list_client(store, mode)
        listed = list(client.models.list())
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    except Exception:
        # Live list is best-effort. Never poison the other channel; use static for this one.
        _models_cache[mode] = (time.monotonic(), static)
        return static
    for m in listed:
        name = short_model(m.name or "")
        actions = list(m.supported_actions or []) or None
        if not _keep_model(name, actions):
            continue
        rows.append(ChatModel(
            id=wire_model(name, mode),
            name=name,
            label=(m.display_name or name).strip(),
            thinking=bool(getattr(m, "thinking", False)) or (
                "pro" in name.split("-") or "argon" in name or "carbon" in name
            ),
        ))
    rows = _merge_models(rows, static)
    _models_cache[mode] = (time.monotonic(), rows)
    return rows


def default_model(models: list[ChatModel],
                  mode: llm.Provider | None = None) -> ChatModel | None:
    want = short_model(
        DEFAULT_MODEL_VERTEX if mode == "vertex" else DEFAULT_MODEL
    ).lower()
    for m in models:
        if m.name.lower() == want:
            return m
    prefer = (
        ("gemini-4-argon", "gemini-3.1-pro-preview", "gemini-2.5-pro",
         "gemini-3.8-flash", "gemini-2.5-flash")
        if mode == "vertex" else
        ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash")
    )
    for name in prefer:
        for m in models:
            if m.name.lower() == name:
                return m
    full = [m for m in models if "lite" not in m.name.lower()]
    return (full or models)[0] if (full or models) else None


def chosen_model(store: Store, models: list[ChatModel] | None = None) -> str | None:
    mode = provider_mode(store)
    saved = get_saved_model(store, mode)
    if models is None:
        try:
            models = list_models(store)
        except Exception:
            return wire_model(saved or DEFAULT_MODEL, mode) if saved else None
    by_id = {m.id: m for m in models}
    by_name = {m.name: m for m in models}
    if saved in by_id:
        return saved
    if saved and short_model(saved) in by_name:
        return by_name[short_model(saved)].id
    pick = default_model(models, mode)
    return pick.id if pick else (wire_model(saved, mode) if saved else None)


def resolve_model(store: Store) -> tuple[str, ChatModel | None]:
    """Модель для send: сохранённая для текущего канала, иначе дефолт."""
    mode = provider_mode(store)
    saved = get_saved_model(store, mode)
    try:
        models = list_models(store)
    except Exception:
        mid = wire_model(saved or DEFAULT_MODEL, mode)
        return mid, None
    mid = chosen_model(store, models) or wire_model(DEFAULT_MODEL, mode)
    mid = wire_model(mid, mode)
    if saved != mid:
        set_saved_model(store, mid, mode)
    info = next((m for m in models if m.id == mid or m.name == short_model(mid)), None)
    return mid, info


def model_label(store: Store) -> str:
    mode = provider_mode(store)
    saved = get_saved_model(store, mode)
    base = short_model(saved) if saved else short_model(llm.default_model_for(mode))
    return f"{base} · {llm.provider_label(mode)}"

def _piece(item: object) -> str:
    if isinstance(item, str):
        return item
    t = getattr(item, "text", None)
    if t:
        return str(t)
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return ""


def _delta_kind(delta: object) -> str:
    raw = getattr(delta, "type", None)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    # Enum / Literal wrappers
    return str(getattr(raw, "value", raw) or "")


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


def _generate_content_text(client, mid: str, text: str, job: Job,
                           *, label: str) -> str:
    """Vertex fallback when Interactions yields no text (common on argon/pro)."""
    job.stage = f"{label} · generateContent"
    job.check()
    gen_kw: dict = {}
    max_tok = GEN_CFG.get("max_output_tokens")
    if max_tok:
        try:
            from google.genai import types as genai_types
            gen_kw["config"] = genai_types.GenerateContentConfig(
                max_output_tokens=int(max_tok),
            )
        except Exception:  # noqa: BLE001 — older SDK / missing types
            gen_kw = {}
    resp = client.models.generate_content(
        model=short_model(mid), contents=text, **gen_kw,
    )
    out = (getattr(resp, "text", None) or "").strip()
    if out:
        job.draft = out
        return out
    # Walk candidates/parts if .text is empty.
    bits: list[str] = []
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) if content is not None else None
        if not parts:
            continue
        for part in parts:
            t = getattr(part, "text", None)
            if t:
                bits.append(str(t))
    out = "".join(bits).strip()
    if out:
        job.draft = out
    return out


def _prefer_complete_text(streamed: str, interaction: object | None) -> str:
    """Reasoning models may stream a short prefix; completed payload is authoritative."""
    streamed = (streamed or "").strip()
    if interaction is None:
        return streamed
    final = _output_text(interaction).strip()
    if not final:
        return streamed
    if not streamed:
        return final
    if len(final) > len(streamed):
        return final
    # Same length or streamed longer — keep streamed (already joined deltas).
    return streamed


def _commit_model_reply(
    thread: Thread,
    out: str,
    *,
    mid: str,
    mode: llm.Provider,
    iid: str | None,
    append: bool,
    title_hint: str | None,
    job: Job | None = None,
) -> str:
    """Optionally append model reply + save. Always returns ``out``."""
    del mid  # label already painted on job.stage
    if job is not None:
        job._last_interaction_id = iid if (iid and mode == "aistudio") else None  # type: ignore[attr-defined]
    if not append:
        return out
    if iid and mode == "aistudio":
        thread.interaction_id = iid
        thread.messages.append(Message("model", out, interaction_id=iid))
    else:
        if mode == "vertex":
            thread.interaction_id = None
        thread.messages.append(Message("model", out))
    if thread.title == "New chat" and title_hint:
        thread.title = title_hint.strip().splitlines()[0][:48]
    save(thread)
    return out


def resolve_model_for_send(
    store: Store,
    *,
    tier: str = "fast",
    model: str | None = None,
    use_saved: bool = True,
) -> str:
    """Pick wire model id for one send.

    use_saved=True (F7 free chat): honour M picker; tier only if nothing saved.
    use_saved=False (ASK / Investigate): force ``GEMINI_FAST_MODEL`` /
    ``GEMINI_REASONING_MODEL`` (or ``model=``) and do not rewrite M.
    """
    mode = provider_mode(store)
    if model:
        return wire_model(model, mode)
    want_tier = "reasoning" if tier == "reasoning" else "fast"
    if not use_saved:
        return wire_model(llm.default_model_for(mode, tier=want_tier), mode)
    saved = get_saved_model(store, mode)
    if short_model(saved) in ("", "gemini-flash-latest", "gemini-flash-lite-latest"):
        return wire_model(llm.default_model_for(mode, tier=want_tier), mode)
    return wire_model(saved, mode)


def send(store: Store, thread: Thread, job: Job, *,
         continue_chat: bool = True,
         tier: str = "fast",
         model: str | None = None,
         use_saved: bool = True,
         input_text: str | None = None,
         append: bool = True) -> str:
    """Отправляет текст через Interactions API, как в примере Google.

    continue_chat=False: do not pass previous_interaction_id (fresh ask; still
    appends to the same thread for F6 history — used by F5 SUMMARY / F5 ASK).
    tier: fast | reasoning — used when use_saved=False, or when M is empty.
    use_saved=False: always the tier default; does not change the M picker.
    input_text: override body sent to the API (thread's last user stays as-is).
    append=False: return text only; caller owns thread messages.
    Provider is an explicit choice (Store / LLM_PROVIDER_MODE); no failover.
    """
    mode = provider_mode(store)
    err = can_send(store)
    if err:
        raise RuntimeError(err)
    try:
        client = _client(store)
    except ImportError as exc:
        raise RuntimeError("pip install google-genai") from exc
    except RuntimeError:
        raise
    job._gemini_client = client  # type: ignore[attr-defined]
    job._last_interaction_id = None  # type: ignore[attr-defined]
    title_hint: str | None = None
    prior = last_model_before_user(thread) if continue_chat else None
    prev_iid = (prior.interaction_id if prior else None) or None
    use_api_continue = bool(continue_chat and mode == "aistudio" and prev_iid)

    if input_text is not None:
        text = input_text
    else:
        user_text = next(
            (m.text for m in reversed(thread.messages) if m.role == "user"), "",
        )
        if not user_text.strip():
            raise RuntimeError("empty message")
        title_hint = user_text
        # Free-form follow-up: Studio continue when nearest model has iid;
        # otherwise embed that model text (Vertex, Investigate Oracle, etc.).
        if continue_chat and prior and prior.text.strip() and not use_api_continue:
            text = followup_input(user_text, prior)
        else:
            text = user_text
    if not text.strip():
        raise RuntimeError("empty message")
    mid = resolve_model_for_send(
        store, tier=tier, model=model, use_saved=use_saved,
    )
    if use_saved:
        saved = get_saved_model(store, mode)
        if saved != mid:
            set_saved_model(store, mid, mode)
    label = f"{short_model(mid)} · {llm.provider_label(mode)}"
    idle_s = READ_IDLE_VERTEX_S if mode == "vertex" else READ_IDLE_S
    t0 = time.time()

    def _beat(note: str) -> None:
        # Seconds are painted by wait_line() on the UI tick — keep stage as phase only.
        job.stage = f"{label} · {note}"

    _beat("connecting")
    job.check()
    gen = dict(GEN_CFG)
    name = short_model(mid).lower()
    if "lite" in name or mode == "vertex":
        # Vertex Interactions often rejects thinking_level from Studio configs.
        gen.pop("thinking_level", None)
    # 3.x Flash: official thinking levels differ; medium/high ok on 3.8, low safest
    if name.startswith("gemini-3") and "thinking_level" in gen:
        gen["thinking_level"] = "low"
    kw: dict = {
        "model": mid,
        "input": text,
        "generation_config": gen,
        "timeout": _http_timeout(),
    }
    tools = tools_for(mid, mode)
    if tools:
        kw["tools"] = tools
    # Studio↔Vertex interaction ids are not interchangeable — never cross-continue.
    if use_api_continue:
        kw["previous_interaction_id"] = prev_iid
    kw["stream"] = True
    job.draft = ""
    try:
        _beat("waiting")
        stream = client.interactions.create(**kw)
    except Cancelled:
        abort_send(job)
        raise
    except Exception as exc:
        # Vertex: Interactions may reject some tops — fall straight to generateContent.
        if mode == "vertex":
            try:
                out = _generate_content_text(client, mid, text, job, label=label)
            except Cancelled:
                abort_send(job)
                raise
            except Exception as exc2:
                abort_send(job)
                raise RuntimeError(_friendly(exc2, mid, mode=mode)) from exc2
            abort_send(job)
            if not out:
                raise RuntimeError(
                    f"{llm.provider_label(mode)} · {short_model(mid)} returned empty reply"
                ) from exc
            return _commit_model_reply(
                thread, out, mid=mid, mode=mode, iid=None, append=append,
                title_hint=title_hint, job=job,
            )
        abort_send(job)
        raise RuntimeError(_friendly(exc, mid, mode=mode)) from exc
    job._gemini_stream = stream  # type: ignore[attr-defined]
    chunks: list[str] = []
    iid: str | None = None
    last_inter: object | None = None
    last_data = time.time()
    try:
        for event in stream:
            job.check()
            now = time.time()
            if now - t0 > WALL_S:
                raise TimeoutError(
                    f"wall clock {int(WALL_S)}s exceeded while waiting for {llm.provider_label(mode)}"
                )
            if now - last_data > idle_s and not chunks:
                raise TimeoutError(
                    f"no reply for {int(idle_s)}s (still waiting for first token)"
                )
            et = getattr(event, "event_type", None) or ""
            # Any SSE traffic counts as keepalive (argon emits thought_signature for a long time).
            if et:
                last_data = now
            if et in ("interaction.created", "interaction.completed"):
                inter = getattr(event, "interaction", None)
                if inter is not None:
                    last_inter = inter
                if inter is not None and getattr(inter, "id", None):
                    iid = str(inter.id)
                status = str(getattr(inter, "status", "") or "") if inter is not None else ""
                _beat("created" if et.endswith("created") else (status or "done"))
                if et == "interaction.completed" and inter is not None:
                    if status in ("failed", "cancelled", "incomplete", "budget_exceeded"):
                        raise RuntimeError(
                            f"{llm.provider_label(mode)} · {short_model(mid)} "
                            f"ended with status={status or 'unknown'}"
                        )
                    # Prefer completed payload when longer than streamed prefix
                    # (argon/pro often emit a short text delta, then full output_text).
                    merged = _prefer_complete_text("".join(chunks), inter)
                    if merged:
                        chunks = [merged]
                        job.draft = merged
                        _beat(f"{len(merged):,} chars")
            elif et == "step.delta":
                delta = getattr(event, "delta", None)
                kind = _delta_kind(delta) if delta is not None else ""
                # Accept text / output_text; ignore thought* keepalives.
                if delta is None or kind not in ("text", "output_text", "content"):
                    _beat("thinking" if kind in ("thought_signature", "thought") else (et or "event"))
                    continue
                piece = (
                    getattr(delta, "text", None)
                    or getattr(delta, "content", None)
                    or ""
                )
                if not isinstance(piece, str):
                    piece = _piece(piece)
                if not piece:
                    _beat("delta")
                    continue
                chunks.append(piece)
                job.draft = "".join(chunks)
                _beat(f"{len(job.draft):,} chars")
            else:
                # Search / thinking / unknown — keep stage alive so UI is not frozen.
                _beat("thinking" if "status" in et or "step" in et else (et or "event"))
    except Cancelled:
        abort_send(job)
        raise
    except Exception as exc:
        abort_send(job)
        raise RuntimeError(_friendly(exc, mid, mode=mode)) from exc
    abort_send(job)
    job.check()
    out = _prefer_complete_text("".join(chunks), last_inter)
    if not out and mode == "vertex":
        try:
            out = _generate_content_text(client, mid, text, job, label=label)
        except Cancelled:
            raise
        except Exception as exc:
            raise RuntimeError(_friendly(exc, mid, mode=mode)) from exc
    if not out:
        raise RuntimeError(
            f"{llm.provider_label(mode)} · {short_model(mid)} returned empty reply"
        )
    job.draft = out
    return _commit_model_reply(
        thread, out, mid=mid, mode=mode, iid=iid, append=append,
        title_hint=title_hint, job=job,
    )
