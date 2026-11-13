"""Текст F10 Help: флоу и UX для продвинутого любителя (не мануал по API)."""

from __future__ import annotations

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

# Live Markdown is re-rendered on every Textual layout pass (hundreds of times on
# first F10 mount). Bake once to Text — same look, cheap Static updates.
_HELP_TEXT: Text | None = None
_HELP_BAKED_WIDTH: int | None = None
_DEFAULT_WIDTH = 100

HELP_MD = """\
# Make DNA Great Again · how to use it

This is a **local** tool for one person’s genotype file. Not a web report factory:
you keep the data, you choose what to run, and screens stay inspectable.

Intended reader: you already know haplogroups and PCA-ish ancestry talk, you are
tired of black-box consumer sites, but you are not living in R/`admixture` yet.
Technical guts exist under the hood; this page is about **what to do** and
**what you will see**.

---

## Suggested path (first hour)

1. **F1 Genome** — open your RAW/TXT (23andMe, Ancestry, Argenética, …).
2. **F2 Data** — point paths at the references you care about (at least FTDNA
   Haplotree; Discover folder if you want rich Y cards; AADR if you want F6).
3. **F3 Tree** — walk the Y tree with **your** branch highlighted (**O** = Our Y).
4. **F4 PGS** — polygenic scores when you have a models folder from F2.
5. **F5 GWAS** — match the active F1 chip against the local GWAS Catalog (F2).
6. **F6 Reference** — f3 / ancient & 1000 Genomes panels against AADR (or 1000G).
7. **F7 Chat** — ask Gemini (AI Studio key or Vertex ADC — F7 **U** picks channel).
8. **F8 Jobs** — watch downloads / fetches / scores (cancel here; start them elsewhere).
9. **F9 Samples** — Open Humans public kits (filter, get missing, DuckDB).
10. **F10 Help** — you are here.

You can skip screens. Nothing forces a “full report”.

---

## F1 · Genome

**Job:** pick the active genotype and see what is in the file.

- Add files (**O** / Ins), choose one as active (Enter).
- Chromosome panel + summary: builds, call rates, named Y markers when linked.
- First open may take a few seconds (parse into the local DB, then link Y names
  to the haplotree from F2). Later opens reuse that store.

**UX tip:** F1 is the “who am I looking at?” switch for every other mode.
Change the active file here; F3/F5/F6 follow it.

---

## F2 · Data

**Job:** tell the app where reference datasets live, download or verify them.

Grouped sources (FTDNA, YFull/TheYtree overlays, PGS models, AADR, Seshat Polaris,
Open Humans public samples, Gemini key…).
Each row: path status, size hints, optional fetch/verify in the background.

**Open Humans** catalog is **F9 Samples** (opens directly). In F2: **P** path,
**B** jump to F9, **V** API new + missing under filter. Per-genome download is
**D on a catalog row** (Space-mark several). ● READY / ○ NEED are labels only —
NEED stays downloadable; READY also ingests into `genome.*`.

**Discover fetch stages (keys 1–5 / D)** on the FTDNA Discover source:

1. Our path (tip→root) · 2. Children below Our · 3. This genome (exclusive) ·
4. All F1 genomes (exclusive) · 5. Whole tree (confirm twice). Progress in **F8**.

**Minimum for a useful Y session**

- FTDNA **Haplotree** JSON → F3 topology + Y linking.
- FTDNA **Discover** folder → dates, countries, ancient cards on nodes (optional
  but the tree feels empty without it).

**Minimum for F6**

- **AADR** folder (the classic `.geno` / `.ind` / `.anno` trio).
- **Seshat · Polaris** — historical polity codes (religion / complexity); **D** pulls
  the Excel snapshot (~3 MB, CC BY-NC). Feeds F3 **LORE** anchors (not a myth download).

**GWAS Catalog**

- **D** pulls the NHGRI-EBI “latest” FTP snapshot (associations, studies, EFO
  mappings) into a local folder. Or **P** to an existing dump. Feeds **F5**
  (genome × catalog match). Not a scoring engine — that is F4 PGS.

**PGS models (F4)**

- **D** builds the local catalog (PGS Catalog API + weight files + coverage vs
  the active F1 genome). Or **P** to an existing catalog folder.

Paths and keys are remembered. Background jobs keep running if you leave F2.

---

## F3 · Tree

**Job:** browse the FTDNA Y tree with **your** path marked.

On enter: load tree (cached after first build), then resolve **Our Y** (saved per
genome; recalculated only when inputs change). Loader stays until both finish so
the tree does not flash “empty of me”.

Useful reflexes

- **O** — jump to / show Our Y tip (scored on the haplotree: named SNPs + hg38
  position; Discover cards are content only, not the tip picker). Exact score
  ties and near-ties (same #DER nodes, sum within 2) paint forks in different
  colors; **O** cycles them. Het / odd alleles are **not** scored — shown as
  `?` uncertain; rejected `anc>der` siblings as `○` pruned.
- **A** — Ask: brief for the current haplogroup → **ASK** tab + pinned **Tree · F3** chat
  (into the active chat · Ctrl+S / Alt+Enter / Send). On **LORE**: skald+scientist weave
  (HARD / RECONSTRUCTED / STORY) from genome · ancient · Seshat · children geo.
- Search `/`, filter **F**, sort **S**, rich branches **B**.
- Card tabs on the right: overview… **LORE** (mythic lens) · Raw JSON (debug).
  Discover pages hydrate on demand.
- **D** / Shift+D — fetch missing Discover JSON for a node or branch (needs F2 path).

Overlays (YFull / TheYtree) appear as read-only extras when those sources are set
in F2 — they do not rewrite FTDNA topology.

---

## F4 · PGS

**Job:** score curated polygenic models on the active genome; optionally compare
to another file or 1000 Genomes-style references from the catalog.

Catalog comes from **F2 → PGS models → D** (or **P** to a folder with
`catalog/catalog.tsv`).

- Left: **L** — CUR (shortlist) / ALL (catalog) / HOLD (triage); on ALL: **F** gates (any/ok/low/few/dis); **/** search
  (trait, PGS id, theme).
- Center: card for the selected model.
- **G** / **A** — score theme or all; results stick for that genome.

This is “effect sizes on *your* SNPs”, not medical advice. Treat extremes as
conversation starters, not diagnoses.

---

## F5 · GWAS

**Job:** intersect the active F1 genome with the local GWAS Catalog associations
table from F2.

- Stats strip: catalog SNP count, how many of those rsIDs are on your chip,
  traits hit, optional **p-value cut** (**P** cycles genome-wide / 5e-8 / 1e-5 /
  1e-3 / any).
- Left: traits — **L** toggles **CUR** (themed shortlist: intelligence, sleep,
  body, … — same spirit as F4 CUR) / **ALL** (every matched Catalog trait).
  Center: associations for the highlighted trait (your genotype vs reported risk
  allele). Right: detail **HIT** / **ASK** (**C** copy rsID, **B** PubMed).
- **A** / ASK tab runs a **scientific** brief in-place (shared F7 thread
  «GWAS · F5»): Catalog facts, allelic concordance, population-genetic limits.
  Cached per trait panel; **Refresh** regenerates. Needs LLM ready (F7 channel).
- **/** search in **TRAITS** (trait · rsID · gene · GCST) · **R** rescan
  (ignore cache). First match on a catalog builds a DuckDB index under
  `~/.cache/make-dna-great-again/gwas_index/` (one-time per associations file);
  later matches join in a few seconds. Result cache (genome + catalog mtime +
  p-cut) still applies. Fallback scanner: `MDGA_GWAS_MATCH=python`.

Needs: active F1 file + **F2 → GWAS Catalog → D** (or **P**).

---

## F6 · Reference

**Job:** put your genome next to published panels — mainly **AADR** ancients
(and optionally 1000 Genomes), with f3-style results you already computed offline.

What loads when you open F6

- AADR **catalog** (who the groups are, including Y labels on ancients).
- **Your result bundles** for this genome (the scan you ran earlier).
- **Y relation tags** on groups (same / ancestor / downstream / other) using the
  same Our Y idea as F3.

So “Loading AADR…” is really “catalog + my results + Y badges”. If the scan is
done, numbers appear from the local store; the catalog is still needed to name
and tag the rows.

**F** starts / manages scoring jobs when you need a new panel. **G** toggles
AADR ↔ 1000G. Map / tree / layers views are different lenses on the same groups.

---

## F7 · Chat

**Job:** notebook-ish threads with Gemini. Mode asks use pinned chats (read-only here).

- **◆ Tree · F3 / GWAS · F5 / Reference · F6** — pinned at top; no free-form Send.
- **U** — pick channel: **AI Studio** or **Vertex**. **M** — model for free-form Send.
- Multiline prompt; **Ctrl+S** / **Alt+Enter** / **Send** (Enter = new line).
- Shortcut row under the thread: **Ctrl+S** ▶ Send · **I** ✶ Investigate ·
  **T** ◇ Story (clickable). ✶ = plan → facts → Oracle → Story Markdown.
  F7 shows **inline image previews** for `![caption](url)` (Discover / Commons
  thumbs). **T** re-runs Story (optional language in the prompt). Needs `pillow`.
  Same atoms: `ydna cli y|aadr|kg|investigate|story`.
  After ✶, **▶** follow-ups see the last model via Studio continue or a local
  prior-text bridge (Vertex / no id).
- **YOU** vs **GEMINI**; **N** last reply → NOTES; **S** select-mode.

Ask from F3 **ASK**, F5 **ASK**, F6 **SUMMARY** — results land in the mode tab and the pinned chat.

---

## F8 · Jobs

**Job:** see what is running in the background.

- Two panels: task list + detail (progress, rate, ETA, message).
- Tasks still **start** in F2 / F3 / F4 / F5 / F7; F8 monitors them.
- **P** pause/resume · **A** pause all · **C** cancel (or drop queued) · **X** clear finished · **/** filter.
- Discover stages 1–5 on F2 **enqueue** if the slot is busy; F8 shows the queue.

---

## F9 · Samples

**Job:** Open Humans catalog (the mode itself — no intermediate screen).
**D** on the highlighted row downloads that genome (or all Space-marked); **V**
updates new API items. ○ NEED / △ PEEK are labeled, not blocked.
Enter opens a local file in F1 when it is already on disk.

Compat: ● READY (F1 parses), ○ NEED CODE, △ PEEK, ✖ SKIP noise.

---

## F10 · Help

This page.

---

## Mental model

| Mode | Question it answers |
|------|---------------------|
| F1 | Which file is active? What SNPs / Y names does it carry? |
| F2 | Where is the reference data? Is it complete? |
| F3 | Where am I on the Y tree? What do Discover cards say? |
| F4 | How do PGS models land on *my* genotypes? |
| F5 | Which curated GWAS hits land on *my* chip SNPs? |
| F6 | How do I sit among ancients / reference pops (f3 + Y tags)? |
| F7 | Can I think out loud with a model, with notes beside? |
| F8 | What is running in the background right now? |
| F9 | Which public samples do I have / still need? |
| F10 | What is the intended path through the app? |

Everything important stays on your machine. Slow first opens are usually
“build or unpack a cache once”; warm revisits should feel light.

**Q** quits (confirm → shutdown progress in the UI, then exit). **F1–F10** switch modes anytime.

Only one app window at a time. If a second start says “already running”, close the
other window or `kill` that PID — you do not need to touch any database files.
"""


def _bake_help(width: int) -> Text:
    """Parse Markdown once → styled Text (ANSI round-trip keeps colours)."""
    console = Console(
        width=max(40, width),
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        legacy_windows=False,
    )
    with console.capture() as cap:
        console.print(Padding(Markdown(HELP_MD), (1, 2, 1, 2)), end="")
    return Text.from_ansi(cap.get())


def help_view(*, width: int | None = None) -> RenderableType:
    """Cached help body for F10. Pass width to rebuild after a large resize."""
    global _HELP_TEXT, _HELP_BAKED_WIDTH
    w = width if width is not None else (_HELP_BAKED_WIDTH or _DEFAULT_WIDTH)
    if _HELP_TEXT is None or w != _HELP_BAKED_WIDTH:
        _HELP_TEXT = _bake_help(w)
        _HELP_BAKED_WIDTH = w
    return _HELP_TEXT


def clear_help_cache() -> None:
    """Test helper."""
    global _HELP_TEXT, _HELP_BAKED_WIDTH
    _HELP_TEXT = None
    _HELP_BAKED_WIDTH = None
