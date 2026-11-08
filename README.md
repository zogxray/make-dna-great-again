# Make DNA Great Again

Local terminal UI (Textual) for **one person’s** genotype file — not a web report
factory. You keep the data, you choose what to run, screens stay inspectable.

*(Formerly ydna-explorer. CLI stays `ydna`; alias `mdga`.)*

Intended reader: you already know haplogroups and PCA-ish ancestry talk, you are
tired of black-box consumer sites, but you are not living in R/`admixture` yet.
Same guide lives inside the app as **F7 Help** (flow & UX; light on internals).

Modes: **F1 Genome → F2 Data → F3 Tree → F4 PGS → F5 Reference → F6 Chat → F7 Help**.

## Requirements

- Python **3.10+**
- Linux / macOS terminal (Windows untested)
- Optional external data (paths in **F2**):
  - FTDNA Y-DNA Haplotree JSON — **F3** + Y linking
  - FTDNA Discover haplogroup JSON folder — rich **F3** cards
  - AADR panel — **F5**
  - 1000 Genomes PLINK2 set — **F4** / some **F5** layers
  - PGS catalog from `build_catalog.py` — **F4**
  - Gemini API key — **F6**

Chip formats in **F1**: 23andMe, AncestryDNA, MyHeritage, FTDNA, Argenética
(plain, `.gz`, or `.zip`). VCF/BAM are not supported yet.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -U pip
python -m pip install -e .
```

Dependencies from `pyproject.toml`: `textual`, `numpy`, `google-genai`, `duckdb`, `pyarrow`.

Shared DNA venv: activate it and `pip install -e .` from `make-dna-great-again/`.

## Run

```bash
ydna
# or
mdga
# or
python -m ydna
# or
python ydna_tui.py
```

```bash
ydna --genome ~/Downloads/genome.txt
ydna --input /path/to/discover/haplogroups --genome ~/kit.txt
```

- `--genome FILE` — preload into **F1** (repeatable)
- `--input DIR` — Discover folder (else path from **F2**)

Quit with **Q** (confirm). App state lives in
`~/.config/make-dna-great-again/ydna.duckdb`
(legacy `~/.config/ydna-explorer` is renamed on first launch).

**One window only.** A second start prints `already running` with a PID — close
that window or `kill <pid>`. You do not need to touch database files.

## Suggested path (first hour)

1. **F1 Genome** — open your RAW/TXT.
2. **F2 Data** — at least FTDNA Haplotree; Discover if you want rich Y cards; AADR for F5.
3. **F3 Tree** — walk the tree with **your** branch (**O** = Our Y).
4. **F4 PGS** — when a models folder is set in F2.
5. **F5 Reference** — f3 / ancients (or 1000G) against AADR.
6. **F6 Chat** — Gemini (key in F2).
7. **F7 Help** — this guide inside the TUI.

You can skip screens. Nothing forces a “full report”.

## F1 · Genome

Pick the active genotype and see what is in the file.

- **O** / Ins add files; Enter makes one active.
- Chromosomes + summary: builds, call rates, named Y markers when linked.
- First open may take a few seconds (local store + Y link). Later opens reuse it.

F1 is the “who am I looking at?” switch — F3/F5 follow the active file.

## F2 · Data

Point the app at reference datasets; download or verify in the background.

**Minimum for a useful Y session:** Haplotree JSON; Discover folder strongly recommended.  
**Minimum for F5:** AADR (`.geno` / `.ind` / `.anno`).

| Source | How it gets on disk |
|--------|---------------------|
| FTDNA Discover | Seed with sibling `ftdna_downloader.py`, then **F2 → P**. Missing nodes: **D** in F2 / F3. |
| FTDNA Haplotree | **D** in F2, or **P** to an existing JSON. |
| AADR / 1000 Genomes | **D** or **P**. Large downloads. |
| PGS models | `GENETIC_IQ_TEST/build_catalog.py`, then **P**. |
| Gemini | **P** (key in DuckDB) or `GEMINI_API_KEY`. |

URLs and readiness checks are in the F2 detail panel.

## F3 · Tree

Browse the FTDNA Y tree with **your** path marked.

On enter: load tree (cached after first build), then **Our Y** (saved per genome;
recalculated when inputs change). The loader waits for both so the tree does not
flash without you.

- **O** — Our Y tip · `/` search · **F** filter · **S** sort · **B** rich branches  
- Card tabs: overview, variants, ancient, map… Discover pages load on demand  
- **D** / Shift+D — fetch missing Discover JSON (needs F2 path)  

YFull / TheYtree overlays (if set in F2) are read-only extras; they do not rewrite FTDNA topology.

## F4 · PGS

Score curated polygenic models on the active genome; optionally compare to another
file or 1000 Genomes-style refs from the catalog.

- **G** / **A** — score theme or all · results stick for that genome  

Effect sizes on *your* SNPs — not medical advice.

## F5 · Reference

Your genome next to published panels — mainly **AADR** (and optionally 1000G),
with f3-style results you computed offline.

Opening F5 loads: AADR **catalog** (who the groups are + Y labels), **your result
bundles**, and **Y relation tags** (same / ancestor / downstream / other), same
Our Y idea as F3. “Loading AADR…” means that whole package.

- **F** — scoring jobs · **G** — AADR ↔ 1000G · map / tree / layers lenses  

Left column: **GROUPS** (list) above **GROUP** (card).  

**LAYERS** (smartpca / qpAdm / imputation / ChromoPainter) need lab binaries on
`PATH` — optional research tools, not the default path.

## F6 · Chat

Threads with Gemini (key + model in F2).

- Left chats · center transcript · right NOTES (yours)  
- Enter sends; replies stream and are stored locally  
- **N** last reply → NOTES · **S** select mode for copy  

The model does not auto-read your RAW file — talk from F3–F5 findings or paste.

## F7 · Help

In-app copy of this flow guide. Scroll with ↑↓ / PgUp/Dn.

## Mental model

| Mode | Question it answers |
|------|---------------------|
| F1 | Which file is active? What SNPs / Y names does it carry? |
| F2 | Where is the reference data? Is it complete? |
| F3 | Where am I on the Y tree? What do Discover cards say? |
| F4 | How do PGS models land on *my* genotypes? |
| F5 | How do I sit among ancients / reference pops (f3 + Y tags)? |
| F6 | Can I think out loud with a model, with notes beside? |
| F7 | What is the intended path through the app? |

Everything important stays on your machine. Slow first opens are usually “build
or unpack a cache once”; warm revisits should feel light.

## Keys (common)

- **F1–F7** — switch mode  
- **Tab** — next panel  
- **Q** — quit  
- Mode-specific hints sit on the bottom key bar of each screen  

## Development

```bash
source .venv/bin/activate
pip install -e .
python -m ydna --genome /path/to/kit.txt
```

Package layout: `ydna/` (app + workspaces + core). Entry: `ydna` → `ydna.__main__:main`.
