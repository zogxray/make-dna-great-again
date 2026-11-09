# Make DNA Great Again

Local terminal UI (Textual) for **one person’s** genotype file — not a web report
factory. You keep the data, you choose what to run, screens stay inspectable.

*(Formerly ydna-explorer. CLI stays `ydna`; alias `mdga`.)*

Intended reader: you already know haplogroups and PCA-ish ancestry talk, you are
tired of black-box consumer sites, but you are not living in R/`admixture` yet.
Same guide lives inside the app as **F8 Help** (flow & UX; light on internals).

Modes: **F1 Genome → F2 Data → F3 Tree → F4 PGS → F5 Reference → F6 Chat → F7 Jobs → F8 Help**.

## Requirements

- Python **3.10+**
- Linux / macOS terminal (Windows untested)
- Optional external data (paths in **F2**):
  - FTDNA Y-DNA Haplotree JSON — **F3** + Y linking
  - FTDNA Discover haplogroup JSON folder — rich **F3** cards
  - AADR panel — **F5**
  - 1000 Genomes PLINK2 set — **F4** / some **F5** layers
  - PGS catalog built in **F2** (or pointed at with **P**) — **F4**
  - Gemini API key — **F6**

Formats in **F1**: 23andMe, AncestryDNA, MyHeritage, FTDNA, Argenética; **VCF**
(incl. `.vcf.bz2` / `.vcf.gz`); **FTDNA Big Y** CSV (`Type`/`Position`/`SNPName`/
`Genotype`) and Big Y VCF zip. Archives: `.zip` / `.gz` / `.bz2`. BAM is not
supported. Missing Y SNP names / Known-SNP positions are filled from the FTDNA
haplotree (and TheYtree hg19 when the file is GRCh37).

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

1. **F1 Genome** — open RAW/TXT/VCF/Big Y.
2. **F2 Data** — at least FTDNA Haplotree; Discover if you want rich Y cards; AADR for F5.
3. **F3 Tree** — walk the tree with **your** branch (**O** = Our Y).
4. **F4 PGS** — after **F2 → PGS models → D** (or **P** to a catalog folder).
5. **F5 Reference** — f3 / ancients (or 1000G) against AADR.
6. **F6 Chat** — Gemini (key in F2).
7. **F7 Jobs** — watch long downloads / Discover / PGS / Gemini.
8. **F8 Help** — this guide inside the TUI.

You can skip screens. Nothing forces a “full report”.

## F1 · Genome

Pick the active genotype and see what is in the file.

- **O** / Ins add files; Enter makes one active.
- Chromosomes + summary: builds, call rates, named Y markers when linked.
- First open may take a few seconds (local store + Y link). Later opens reuse it.
- **VCF**: `GT` → letter alleles; empty `ID` becomes `chrom:pos`, then Y names from
  the haplotree when possible.
- **Big Y CSV**: Known SNP (name → position via haplotree) + Novel Variant (by
  Position). Build defaults to GRCh38.

F1 is the “who am I looking at?” switch — F3/F5 follow the active file.

## F2 · Data

Point the app at reference datasets; download or verify in the background.

**Minimum for a useful Y session:** Haplotree JSON; Discover folder strongly recommended.  
**Minimum for F5:** AADR (`.geno` / `.ind` / `.anno`).

| Source | How it gets on disk |
|--------|---------------------|
| FTDNA Discover | **P** to a folder (optional seed from `ftdna_downloader.py`). Missing nodes: staged fetch **1–5** / **D** below, or **D** / Shift+D in F3. |
| FTDNA Haplotree | **D** in F2, or **P** to an existing JSON. |
| AADR / 1000 Genomes | **D** or **P**. Large downloads. |
| PGS models | **D** in F2 (needs F1 genome for coverage), or **P** to an existing catalog folder. |
| Gemini | **P** (key in DuckDB) or `GEMINI_API_KEY`. |

**Discover fetch stages** (on the Discover source; already-downloaded JSON skipped):

1. **Our** path (tip→root) · **2** Children below Our · **3** This genome ·
4. All F1 genomes · **5** / **A** whole tree (confirm twice).

Direct JSON first; HaplogroupSearch only if the resource 403/404. Progress in **F7**.

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

Build the catalog once in **F2 → PGS models → D** (needs F1 for SNP coverage), or
**P** to an existing work folder with `catalog/catalog.tsv`.

- **L** — curated / catalog / diseases / low cov / too few  
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

## F7 · Jobs

Background task monitor (list + detail). Downloads, Discover fetches, PGS,
Tasks still **start** from F2/F3/F4/F6; F7 watches them. **P** pause/resume,
**A** pause all, **C** cancel (or drop a queued item), **X** clear finished.
Discover stages 1–5 on F2 enqueue onto the same slot when one is already active.

## F8 · Help

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
| F7 | What is running in the background right now? |
| F8 | What is the intended path through the app? |

Everything important stays on your machine. Slow first opens are usually “build
or unpack a cache once”; warm revisits should feel light.

## Keys (common)

- **F1–F8** — switch mode  
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
