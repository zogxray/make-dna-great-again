# AGENTS.md — Make DNA Great Again

Thin guide for coding agents working in this repo. Product UX lives in
**README.md** and **F10 Help** (`ydna/workspaces/help/view.py`); keep those in
sync when behavior changes.

## What this is

Local **Textual** TUI for one person’s genotype. CLI: `ydna` / `mdga` /
`python -m ydna`. Not a web service; data stays on disk.

## Layout

| Path | Role |
|------|------|
| `ydna/__main__.py` | Dispatcher: default → TUI; `ydna cli …` → headless |
| `ydna/tui/` | Textual app shell + `--window` launch (`run.py`, `app.py`) |
| `ydna/cli/` | Headless JSON/text (no Textual); `ops.py` shared with MCP |
| `ydna/mcp/` | stdio MCP (`python -m ydna.mcp`) — optional extra `[mcp]` |
| `ydna/core/` | Domain: genome, DuckDB, sources, PGS, GWAS, chat/LLM, jobs |
| `ydna/prompts/` | Future ask builders without Rich (empty placeholder) |
| `ydna/workspaces/` | F1–F10 TUI modes only |
| `ydna/theme/` | Shared Rich styles |
| `ydna/app.py` | Compat re-export of `YDnaApp` — prefer `ydna.tui` |
| `packaging/` | Linux PyInstaller + `.deb` |
| `.github/workflows/` | `release-linux.yml` |
| `tests/` | pytest |
| `data/` | **Local only** — gitignored content; never commit kits/catalogs |

**Layer rule:** `cli` / `prompts` → `core` only. `mcp` → `cli.ops` (+ SDK).  
`tui` / `workspaces` → `core`. `core` must not import `tui`, `cli`, `mcp`, `workspaces`, or Textual.

App state: `~/.config/make-dna-great-again/` (DuckDB + lock).  
Caches: `~/.cache/make-dna-great-again/` (GWAS match/index, etc.).

## Modes (F-keys)

F1 Genome → F2 Data → F3 Tree → F4 PGS → F5 GWAS → F6 Reference → F7 Chat → F8 Jobs → F9 Samples → F10 Help.

Cross-cutting: active F1 genome drives F3/F5/F6; long work goes through
`ydna/core/jobs.py` and shows in F8.

## Hard rules

- **Secrets:** never commit `.env`, API keys, ADC JSON, or real genotype files.
  Use `.env.example` only.
- **`data/`:** directory skeleton may exist; payloads stay local / gitignored.
- **Singleton:** one app instance (`ydna/core/single.py` flock). Don’t spawn a
  second `ydna` against the same config DB in tests without isolating
  `XDG_CONFIG_HOME` / `XDG_CACHE_HOME`.
- **LLM:** dual channel, explicit choice — Vertex ADC **or** AI Studio key
  (`ydna/core/llm.py`, F2). No silent failover.
- **Medical framing:** GWAS/PGS/ASK are exploratory; don’t turn copy into
  diagnosis language.

## Dev commands

```bash
# prefer the shared DNA venv when present
source ../venv/bin/activate   # or: python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ydna                          # or: ydna --window
python -m pytest tests/ -q
./packaging/build_linux.sh    # optional Linux artifacts → dist/
```

Useful env overrides:

- `MDGA_GWAS_MATCH=python` — F5 legacy TSV scan (skip DuckDB index)
- `MDGA_TERMINAL=kitty` — emulator for `ydna --window`
- `NO_COLOR` / `FORCE_COLOR` — `--window` strips IDE monochrome; don’t reintroduce

## Where to edit what

| Change | Start here |
|--------|------------|
| TUI flags / window launch | `ydna/__main__.py` → `ydna/tui/run.py`, `window_launch.py` |
| Headless CLI | `ydna/cli/` — `jsonio.py` envelope, `session.py` lock, `cmd_*.py` |
| MCP tools | `ydna/mcp/server.py` → `cli.ops` only |
| Agent search loop | `ydna/core/investigate.py` (`gwas.search` / `trait` / `pgs.search` / `genome.rs`) |
| Paths, settings, secrets store | `ydna/core/store.py`, `sources_db.py` |
| Downloads / F2 sources | `ydna/core/sources.py` |
| Gemini client / models | `ydna/core/llm.py`, `chat.py` |
| F5 match + index | `ydna/core/gwas_match.py` |
| F4 scoring | `ydna/core/pgs.py` |
| Mode UI / keys | `ydna/workspaces/<mode>/` |
| User-facing docs | `README.md` **and** `ydna/workspaces/help/view.py` |
| Linux package | `packaging/*`, `.github/workflows/release-linux.yml` |

**Investigation:** dual-tier tool loop — Flash plans **tool calls** → local
gather → Argon Oracle → human Story MD (`ydna/core/investigate_brief.py`,
`ydna/core/story_brief.py`). Tools span GWAS **and** Y/AADR/KG (`y_path`,
`y_milestones`, `aadr_y_hits`, `modern_affinity` via `ydna/cli/lineage_session.py`
+ `ydna/core/tree_data.py`). Story uses Discover `image_url` + Wikimedia search
cues (no HTML export). F7 **✶**/**◇** / `ydna cli investigate|story` / MCP;
atoms also `ydna cli y|aadr|kg …`.

## Tests

- Default: fast unit tests under `tests/`.
- Marker `local` / `MDGA_LOCAL_BENCH=1`: needs private `data/` — don’t assume CI has it.
- GWAS engines: `tests/test_gwas_match_engines.py` (DuckDB ≡ Python semantics).

## Style

- Match existing modules: typed Python, small dataclasses, Jobs for background
  work, Rich/Textual for UI — no new GUI framework.
- Prefer extending `ydna/core/` helpers over duplicating SQL/TSV parsing in a
  workspace.
- Keep agent docs thin; put deep UX narrative in README/Help, not here.
