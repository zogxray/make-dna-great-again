"""Текст F9 Help: флоу и UX для продвинутого любителя (не мануал по API)."""

from __future__ import annotations

from rich.console import Console, RenderableType
from rich.markdown import Markdown
from rich.padding import Padding
from rich.text import Text

# Live Markdown is re-rendered on every Textual layout pass (hundreds of times on
# first F9 mount). Bake once to Text — same look, cheap Static updates.
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
   Haplotree; Discover folder if you want rich Y cards; AADR if you want F5).
3. **F3 Tree** — walk the Y tree with **your** branch highlighted (**O** = Our Y).
4. **F4 PGS** — polygenic scores when you have a models folder from F2.
5. **F5 Reference** — f3 / ancient & 1000 Genomes panels against AADR (or 1000G).
6. **F6 Chat** — ask Gemini with the same context you already loaded (API key in F2).
7. **F7 Jobs** — watch downloads / fetches / scores (cancel here; start them elsewhere).
8. **F8 Samples** — Open Humans public kits (filter, get missing, DuckDB).
9. **F9 Help** — you are here.

You can skip screens. Nothing forces a “full report”.

---

## F1 · Genome

**Job:** pick the active genotype and see what is in the file.

- Add files (**O** / Ins), choose one as active (Enter).
- Chromosome panel + summary: builds, call rates, named Y markers when linked.
- First open may take a few seconds (parse into the local DB, then link Y names
  to the haplotree from F2). Later opens reuse that store.

**UX tip:** F1 is the “who am I looking at?” switch for every other mode.
Change the active file here; F3/F5 follow it.

---

## F2 · Data

**Job:** tell the app where reference datasets live, download or verify them.

Grouped sources (FTDNA, YFull/TheYtree overlays, PGS models, AADR, Seshat Polaris,
Open Humans public samples, Gemini key…).
Each row: path status, size hints, optional fetch/verify in the background.

**Open Humans** catalog is **F8 Samples** (opens directly). In F2: **P** path,
**B** jump to F8, **V** API new + missing under filter. Per-genome download is
**D on a catalog row** (Space-mark several). ● READY / ○ NEED are labels only —
NEED stays downloadable; READY also ingests into `genome.*`.

**Discover fetch stages (keys 1–5 / D)** on the FTDNA Discover source:

1. Our path (tip→root) · 2. Children below Our · 3. This genome (exclusive) ·
4. All F1 genomes (exclusive) · 5. Whole tree (confirm twice). Progress in **F7**.

**Minimum for a useful Y session**

- FTDNA **Haplotree** JSON → F3 topology + Y linking.
- FTDNA **Discover** folder → dates, countries, ancient cards on nodes (optional
  but the tree feels empty without it).

**Minimum for F5**

- **AADR** folder (the classic `.geno` / `.ind` / `.anno` trio).
- **Seshat · Polaris** — historical polity codes (religion / complexity); **D** pulls
  the Excel snapshot (~3 MB, CC BY-NC). Feeds F3 **LORE** anchors (not a myth download).

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
- **A** — Ask GPT: builds a short prompt from the current tab and opens **F6**
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

- Left: short lists (**L** — CUR / CAT / DIS / LOW / FEW) and **/** search
  (trait, PGS id, theme).
- Center: card for the selected model.
- **G** / **A** — score theme or all; results stick for that genome.

This is “effect sizes on *your* SNPs”, not medical advice. Treat extremes as
conversation starters, not diagnoses.

---

## F5 · Reference

**Job:** put your genome next to published panels — mainly **AADR** ancients
(and optionally 1000 Genomes), with f3-style results you already computed offline.

What loads when you open F5

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

## F6 · Chat

**Job:** notebook-ish threads with Gemini (key + model in F2).

- Left: chats. Center: transcript. Right: free NOTES (yours, not the model).
- Multiline prompt; **Ctrl+S** / **Alt+Enter** / **Send** button send (Enter = new line).
  (Plain Ctrl+Enter is unreliable in most terminals.) Replies stream and are stored locally.
- **YOU** vs **GEMINI** are styled differently on purpose.
- **N** copies the last model reply into NOTES; **S** select-mode for copy/paste.

The model does not automatically see your RAW file; talk in terms of what you
already found in F3–F5, or paste snippets into NOTES / the prompt yourself.
From F3, **A** prefills Ask GPT into the **active** chat (new chat only if none exist).

---

## F7 · Jobs

**Job:** see what is running in the background.

- Two panels: task list + detail (progress, rate, ETA, message).
- Tasks still **start** in F2 / F3 / F4 / F6; F7 monitors them.
- **P** pause/resume · **A** pause all · **C** cancel (or drop queued) · **X** clear finished · **/** filter.
- Discover stages 1–5 on F2 **enqueue** if the slot is busy; F7 shows the queue.

---

## F8 · Samples

**Job:** Open Humans catalog (the mode itself — no intermediate screen).
**D** on the highlighted row downloads that genome (or all Space-marked); **V**
updates new API items. ○ NEED / △ PEEK are labeled, not blocked.
Enter opens a local file in F1 when it is already on disk.

Compat: ● READY (F1 parses), ○ NEED CODE, △ PEEK, ✖ SKIP noise.

---

## F9 · Help

This page.

---

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
| F8 | Which public samples do I have / still need? |
| F9 | What is the intended path through the app? |

Everything important stays on your machine. Slow first opens are usually
“build or unpack a cache once”; warm revisits should feel light.

**Q** quits (confirm → shutdown progress in the UI, then exit). **F1–F9** switch modes anytime.

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
    """Cached help body for F9. Pass width to rebuild after a large resize."""
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
