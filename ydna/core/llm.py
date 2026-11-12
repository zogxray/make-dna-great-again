"""Gemini via google-genai: explicit provider choice (Vertex ADC | AI Studio key).

No silent failover — the chosen channel is the only one used for a send.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from .store import Store

Provider = Literal["vertex", "aistudio"]

SETTING_PROVIDER = "llm_provider"
SETTING_GCP_PROJECT = "google_cloud_project"
SETTING_GCP_LOCATION = "google_cloud_location"
SETTING_GCP_ADC = "google_application_credentials"
PROVIDERS: tuple[Provider, ...] = ("aistudio", "vertex")

# Env (also documented in .env.example) — F2 Store values win when set.
ENV_MODE = "LLM_PROVIDER_MODE"
ENV_PROJECT = "GOOGLE_CLOUD_PROJECT"
ENV_LOCATION = "GOOGLE_CLOUD_LOCATION"
ENV_ADC = "GOOGLE_APPLICATION_CREDENTIALS"
ENV_API_KEY = "GEMINI_API_KEY"
ENV_FAST = "GEMINI_FAST_MODEL"
ENV_REASONING = "GEMINI_REASONING_MODEL"

DEFAULT_LOCATION = "global"
DEFAULT_FAST = "gemini-3.8-flash"
DEFAULT_REASONING = "gemini-4-argon"
DEFAULT_ADC = Path.home() / ".config/gcloud/application_default_credentials.json"


def load_dotenv(path: Path | None = None) -> Path | None:
    """Minimal .env loader (KEY=VALUE). Does not override existing os.environ."""
    candidates: list[Path] = []
    if path is not None:
        candidates.append(path)
    candidates.append(Path.cwd() / ".env")
    # ydna/core/llm.py → repo root make-dna-great-again/
    candidates.append(Path(__file__).resolve().parents[2] / ".env")
    seen: set[Path] = set()
    loaded: Path | None = None
    for p in candidates:
        try:
            p = p.resolve()
        except OSError:
            continue
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            os.environ[key] = val
        loaded = p
    return loaded


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def studio_api_key(store: Store | None = None) -> str | None:
    """AI Studio key: F2 DuckDB secret first, then GEMINI_API_KEY."""
    if store is not None:
        secret = store.secret("gemini")
        if secret and str(secret).strip():
            return str(secret).strip()
    key = _env(ENV_API_KEY)
    return key or None


def vertex_project(store: Store | None = None) -> str:
    """Vertex project: F2 setting first, then env."""
    if store is not None:
        saved = (store.setting(SETTING_GCP_PROJECT) or "").strip()
        if saved:
            return saved
    return _env(ENV_PROJECT) or _env("GCLOUD_PROJECT") or _env("GCP_PROJECT")


def set_vertex_project(store: Store, project: str | None) -> None:
    store.set_setting(SETTING_GCP_PROJECT, (project or "").strip() or None)


def vertex_location(store: Store | None = None) -> str:
    if store is not None:
        saved = (store.setting(SETTING_GCP_LOCATION) or "").strip()
        if saved:
            return saved
    return _env(ENV_LOCATION) or DEFAULT_LOCATION


def default_adc_path() -> Path:
    env = _env(ENV_ADC)
    return Path(env).expanduser() if env else DEFAULT_ADC


def vertex_adc_path(store: Store | None = None) -> Path | None:
    """Path to ADC JSON for Vertex. F2 → env → default gcloud path.

    If a configured path is missing on disk, fall back to the default ADC file.
    """
    candidates: list[Path] = []
    raw = ""
    if store is not None:
        raw = (store.setting(SETTING_GCP_ADC) or "").strip()
    if not raw:
        raw = _env(ENV_ADC)
    if raw:
        candidates.append(Path(raw).expanduser())
    candidates.append(DEFAULT_ADC)
    seen: set[str] = set()
    first: Path | None = None
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if first is None:
            first = p
        if p.is_file():
            return p
    return first  # may be missing — provider_ready checks is_file()


def set_vertex_adc(store: Store, path: str | Path | None) -> None:
    text = str(path).strip() if path else ""
    store.set_setting(SETTING_GCP_ADC, text or None)


def apply_vertex_adc(store: Store | None = None) -> Path | None:
    """Point google-auth at the chosen ADC file (sets GOOGLE_APPLICATION_CREDENTIALS)."""
    path = vertex_adc_path(store)
    if path is None or not path.is_file():
        return None
    resolved = path.expanduser().resolve()
    os.environ[ENV_ADC] = str(resolved)
    return resolved

def fast_model() -> str:
    return _env(ENV_FAST) or DEFAULT_FAST


def reasoning_model() -> str:
    return _env(ENV_REASONING) or DEFAULT_REASONING


def normalize_provider(raw: str | None) -> Provider | None:
    s = (raw or "").strip().lower()
    if s in ("vertex", "vertexai", "gcp", "cloud"):
        return "vertex"
    if s in ("aistudio", "ai-studio", "studio", "gemini", "developer"):
        return "aistudio"
    # "auto" kept as alias for inference — not a failover path.
    if s in ("auto", ""):
        return None
    return None


def infer_provider(store: Store | None = None) -> Provider:
    """Default channel when nothing is chosen: key → Studio, else Vertex if project set."""
    if studio_api_key(store):
        return "aistudio"
    if vertex_project(store):
        return "vertex"
    return "aistudio"


def provider_mode(store: Store | None = None) -> Provider:
    """Explicit choice: Store → env → infer. Never switches mid-request."""
    if store is not None:
        saved = normalize_provider(store.setting(SETTING_PROVIDER))
        if saved:
            return saved
    env = normalize_provider(_env(ENV_MODE))
    if env:
        return env
    return infer_provider(store)


def set_provider(store: Store, mode: Provider) -> None:
    store.set_setting(SETTING_PROVIDER, mode)


def cycle_provider(store: Store) -> Provider:
    cur = provider_mode(store)
    nxt = PROVIDERS[(PROVIDERS.index(cur) + 1) % len(PROVIDERS)]
    set_provider(store, nxt)
    return nxt


def provider_label(mode: Provider | None = None, store: Store | None = None) -> str:
    m = mode or provider_mode(store)
    return "Vertex AI" if m == "vertex" else "AI Studio"


def adc_hint(store: Store | None = None) -> str:
    path = vertex_adc_path(store) or default_adc_path()
    return str(path)


def vertex_gaps(store: Store | None = None) -> list[str]:
    """Human checklist of what Vertex still needs."""
    gaps: list[str] = []
    if not vertex_project(store):
        gaps.append("Cloud project ID")
    adc = vertex_adc_path(store)
    if adc is None or not adc.is_file():
        gaps.append(f"ADC file ({adc_hint(store)})")
    return gaps


def provider_ready(store: Store | None = None,
                   mode: Provider | None = None) -> tuple[bool, str]:
    """Whether the chosen provider can send. Second value is a short human status."""
    m = mode or provider_mode(store)
    if m == "aistudio":
        key = studio_api_key(store)
        if not key:
            return False, "AI Studio: set key in F2 · Google Gemini · P"
        return True, f"AI Studio · …{key[-4:]}"
    gaps = vertex_gaps(store)
    if gaps:
        return False, "Vertex needs " + " + ".join(gaps) + " · F2 · Gemini · P"
    project = vertex_project(store)
    loc = vertex_location(store)
    return True, f"Vertex · {project} · {loc}"


def can_send(store: Store | None = None) -> str | None:
    """None if ready; otherwise a notify-ready error string."""
    ok, detail = provider_ready(store)
    return None if ok else detail


def _ensure_quota_project(project: str) -> None:
    """User ADC often needs an explicit quota project for aiplatform."""
    if not _env("GOOGLE_CLOUD_QUOTA_PROJECT") and project:
        os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = project


def make_client(store: Store | None = None, *, mode: Provider | None = None,
                connect_s: float = 15.0, read_idle_s: float = 90.0,
                write_s: float = 60.0, wall_ms: int = 600_000):
    """Build google.genai.Client for the chosen provider only."""
    from google import genai
    from google.genai import types
    import httpx2

    m = mode or provider_mode(store)
    timeout = httpx2.Timeout(
        connect=connect_s, read=read_idle_s, write=write_s, pool=connect_s,
    )
    http = types.HttpOptions(
        timeout=wall_ms,
        client_args={"timeout": timeout},
    )
    if m == "aistudio":
        key = studio_api_key(store)
        if not key:
            raise RuntimeError(
                "AI Studio chosen — paste the API key in F2 (Google Gemini · P)."
            )
        # v1beta: Search + thinking on Developer API
        http.api_version = "v1beta"
        return genai.Client(api_key=key, http_options=http)

    project = vertex_project(store)
    if not project:
        raise RuntimeError(
            "Vertex chosen — set the Cloud project in F2 (Google Gemini · P). ADC, no API key."
        )
    adc = apply_vertex_adc(store)
    if adc is None or not Path(adc).is_file():
        raise RuntimeError(
            f"Vertex chosen — ADC file not found ({adc_hint(store)}). "
            "Set the path in F2 · Google Gemini · P."
        )
    _ensure_quota_project(project)
    return genai.Client(
        vertexai=True,
        project=project,
        location=vertex_location(store),
        http_options=http,
    )


def default_model_for(mode: Provider | None = None, *,
                      tier: Literal["fast", "reasoning"] = "fast") -> str:
    m = mode or "aistudio"
    name = reasoning_model() if tier == "reasoning" else fast_model()
    # Developer API likes models/<id>; Vertex accepts bare ids — chat.api_model normalizes.
    return name
