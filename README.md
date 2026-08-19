# Eval Dataset Generator

> Everyone evaluates LLM systems; almost nobody can say where their eval set came from.
> This repo turns **raw production logs** into a **labeled, deduplicated, statistically
> validated golden dataset** — agreement **measured** (Cohen's κ + bootstrap CI95),
> never declared. Measured for real, twice: a first run blocked at **κ = 0.26** and
> surfaced a guideline gap; after the fix, it re-ran at **κ = 0.80** on my real working
> domain, and the gate opened. The improvement loop, closed on itself.

[![CI](https://github.com/AnbriSamir/Eval-dataset-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/AnbriSamir/Eval-dataset-generator/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-677%20offline-brightgreen)](#the-machinery-was-proven-on-synthetic-data-first)
[![Agreement](https://img.shields.io/badge/outcome%20κ-0.804%20·%20CI95%20%5B0.65%2C%200.93%5D-blueviolet)](#the-real-κ-measured--and-a-full-flywheel-turn)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-v1%20complete-brightgreen)](#roadmap)

## The real κ (measured) — and a full flywheel turn

Both numbers below come from real, double-blind sessions: a human annotator fills a blind
template (no judge output anywhere in sight — the annotation CLI refuses to even write
into a directory holding judge artifacts), then `claude-opus-4-8` judges the same records
live. A live-LLM run is not byte-deterministic, so every κ is bound to its committed run
report and the exact ground-truth bytes it was measured on.

**Turn 1 — the honest failure that drove a fix.** The first corpus was a set of generic
traffic Q&A. The headline came back **`outcome` κ = 0.263** (n=49, CI95 **[−0.024, 0.533]**,
straddling zero) — and the export gate **blocked it** (0.263 < 0.6). Raw agreement was
75.5%, but both raters skew `correct`, so chance alone predicts 66.8%: a percent-agreement
metric would have shipped a passing-looking dataset; κ refused. The drill-down then made
the low number *actionable*: most `outcome` disagreements were `correct → unjudgeable` on
live-status questions ("is traffic flowing right now?") — the human graded plausibility,
the judge applied the letter of `unjudgeable` to claims no transcript can verify. Not
noise: an **annotation-guideline gap**. It drove **taxonomy v2**
([ADR-0006](docs/decisions/ADR-0006-taxonomy-v2-live-status-convention.md)) — live claims
are graded *as responses*; `unjudgeable` is reserved for defective inputs. (v1 stays
frozen, so [turn 1's report](docs/reports/agreement_run_report.20260804T002205Z-7c2b30d6.json)
keeps its provenance, and the pipeline refuses to measure v1 labels against a v2 judge.)

**Turn 2 — re-measured on the real work, with the sharpened guideline.** The demo corpus
then moved to my actual working domain — machine-learning **redressement of Floating Car
Data** (partial probe-vehicle flow → true all-vehicle flow, road-reference features
calibrated on SIREO permanent stations and pneumatic tubes) — and the session was re-run
under v2. Perfect join: 49/49 matched, zero orphans.
[Committed report](docs/reports/agreement_run_report.20260804T160003Z-76d0eacb.json),
`human_labels_sha256=83649922…`:

| Axis | κ | CI95 (B=10000) | Band |
|---|---|---|---|
| `task_type` — *what is this exchange?* | **0.870** (n=49, p_o=0.939) | [0.722, 1.0] | almost perfect |
| `outcome` — *is the answer correct?* (**the headline**) | **0.804** (n=49, p_o=0.878) | [0.652, 0.933] | almost perfect |

**This time the gate opens** — 0.804 clears 0.6, and even the CI95 lower bound (0.652)
sits above it. Every `outcome` class now has the support to report its own κ (0.88
`correct`, 0.56 `partially_correct`, 0.85 `incorrect`, 0.90 `unjudgeable`) — the
corpus was engineered for that, the fix of turn 1's under-supported classes. Just six
`outcome` disagreements remain, all landing on `partially_correct` (never a two-class
jump like `correct → incorrect`).

**What this pair is, and isn't.** It is one honest measurement that failed and blocked,
a guideline fix it motivated, and a second honest measurement that passed — the
improvement loop this whole repo exists to run, executed on itself. It is **not** a
controlled A/B: turn 2 changed both the guideline *and* the corpus (from generic traffic
to a coherent domain set), so the lift is not attributable to v2 alone. Both reports ship
verbatim; read each κ with its n, its supports, and its interval.

> ✅ **Complete.** All six pipeline stages implemented, **677 offline tests**, CI green
> (lint · typecheck · test) — no API key, no Docker, no network. Three zero-argument
> CLIs (`make demo` · `make agreement` · `make export`) are **byte-identical between
> runs** and pinned by committed golden files. Six ADRs record the load-bearing
> decisions, and every phase was [adversarially red-teamed before
> merge](#what-the-red-team-caught). Every number in this README reproduces from one of
> those commands or is pinned by the test suite — except the one that can't be: the
> real κ above comes from a live-LLM run and is bound instead to its committed run
> report and ground-truth digests. Nothing is declared without a measurement behind it.

**The differentiator is not the stack — it is dedup done honestly + coverage by
clustering + agreement measured with κ/CI95 + full provenance.** A golden set with hidden
near-duplicates, an auto-label pipeline whose κ is unmeasured, or an export whose
provenance can't be replayed would each destroy the entire signal. Those failure modes
are exactly what this codebase exists to get right — and to *prove* it got right.

---

## The flywheel (three repos that feed each other)

This is the third leg of a loop I run on my own tooling: *production traces → eval data
→ better systems*.

- [`multi-agent-orchestrator`](https://github.com/AnbriSamir/multi-agent-orchestrator) —
  agents as production infrastructure. Every decision it makes is a typed, costed
  `TraceSpan` — and that JSONL is **this repo's first-class native input** (a structural
  adapter mines `plan`/`execute`/`verdict` spans; a generic mapping adapter onboards any
  other JSONL source via a six-field declared mapping, only two of them required).
- [`hybrid-rag-pipeline`](https://github.com/AnbriSamir/hybrid-rag-pipeline) — the
  evaluation science: nDCG/recall@k, verified citations, paired bootstrap CI95.
- **`eval-dataset-generator`** *(this repo)* — industrializes the loop's fuel: mines
  those traces into a labeled, deduplicated, provenance-stamped golden set that the
  other two can be measured against.

Same discipline in all three: *measured, never declared; every published number
reproducible from a harness*.

## The questions nobody answers about their eval set

| The question that matters | How this repo answers it — structurally |
|---|---|
| **Where did each item come from?** | Every `golden.jsonl` line carries typed provenance (source, line, span id, cluster, content hash) and recomputes its own id and content hash on parse — a line tampered in its identity, origin, or texts refuses to exist; the label fields are fenced at file level by the `golden.jsonl` digest in `meta.json`. `meta.json` embeds the **entire validated report chain** from `lines_read` to `exported`. |
| **Are there hidden near-duplicates?** | Exact (SHA-256) + near-dup (embedding cosine, union-find) dedup, with a report naming **every drop, its survivor, and (for near-dups) its similarity** — chain collapses flagged, never hidden. |
| **Does it cover real traffic, or the easy head?** | HDBSCAN over deterministic embeddings + stratified sampling with floor-1 quotas. Noise is a **first-class stratum** — the tail cannot be silently discarded. |
| **Can you trust the LLM judge?** | A human-labeled subset validates it: Cohen's κ (global **and** per-class) + paired bootstrap CI95, degenerate cases as typed statuses (never NaN, never a silent 0). An embarrassing κ is published as-is — [it was](#the-real-κ-measured--and-a-full-flywheel-turn). |
| **Could the judge have seen the answers?** | Blindness is structural: the judge's entire input surface is two strings (a human label is untransportable by signature), and `export ∩ few-shots = ∅` is enforced by a **validator** — a contaminated export is unrepresentable, not merely filtered. |
| **Can you replay it?** | Same inputs → byte-identical outputs. Seeds, thresholds, model ids, input SHA-256s, and the ground-truth-file SHA-256 all travel in `meta.json`. |

## Pipeline

```
production logs (TraceSpan JSONL from multi-agent-orchestrator · any JSONL via a declared mapping)
   │
   ▼
[1] ingest     normalize → REDACT → derive id → freeze     secrets/PII never persist; record ids
   │           one constructor, one frozen LogRecord        are computed AFTER redaction
   ▼
[2] dedup      exact (SHA-256) + near-dup (cosine ≥ 0.92,  every drop is a typed entry naming its
   │           union-find) — threshold has a measurement    survivor + similarity; chain collapses
   │           protocol, not a hunch                        flagged per entry
   ▼
[3] cluster    deterministic embeddings → HDBSCAN →        noise is a first-class stratum; quotas
   │           stratified sampling (floor-1, integer        are integer arithmetic — no float
   │           largest-remainder, seeded hash ranking)      tie-wobble across platforms
   ▼
[4] label      LLM judge via structured output only        closed enums as the API schema: an
   │           (Anthropic SDK) · deterministic FakeJudge    out-of-taxonomy label is
   │           offline · typed refusals/failures, counted   unrepresentable
   ▼
[5] validate   human subset → Cohen's κ (global +          one hand-checked formula serves both;
   │           per-class) + paired bootstrap CI95           sklearn is the independent test oracle
   ▼
[6] export     five-check κ gate → golden.jsonl +          contamination guard is a validator;
               meta.json (two sections: deterministic       κ below the gate blocks — override is
               / volatile; full report chain embedded)      typed, reasoned, and printed loudly
```

Full module map in [`docs/architecture.md`](docs/architecture.md); every load-bearing
decision is an ADR in [`docs/decisions/`](docs/decisions/).

## Quickstart

```bash
git clone https://github.com/AnbriSamir/Eval-dataset-generator && cd Eval-dataset-generator
make install     # pip install -e ".[dev]"
make test        # 677 offline tests — no API key, no Docker, no network
make demo        # ingest → dedup → cluster → sample → label, on committed fixtures
make agreement   # Cohen's κ + CI95, judge vs (synthetic) human labels
make export      # the κ gate + golden.jsonl + meta.json provenance
```

Each command is deterministic and byte-pinned. Real output excerpts (lines verbatim from
the committed goldens; a lone `…` marks elided lines):

`make demo` — every dedup drop names its survivor; the few-shot contamination gate is
visible in the run itself:

```
[2/5] dedup   threshold=0.92  embedder=hashing dim=512 char_wb(3,5)
  in=64  out=56  id_collapsed=0  exact=3  near=5 (via_chain=1)
  …
  near   rec-e022b434d327f609 -> kept rec-e7e848ff300b70bf  sim=0.890676  [chain]
  …

[5/5] label   judge=fake model=fake-judge-v1  taxonomy=tax-d8ba44dd70c7  prompt=b714f9ad2e94
  in=50  labeled=49  refused=0  failed=0  budget_skipped=0  fewshot_collisions=1  (budget=500)
  collision  rec-5e3329f36f536ec4  (canonical text matches a committed few-shot — never labeled, never exportable)
```

`make agreement` — the banner is mandatory, and κ never travels naked:

```
!! SYNTHETIC — annotations_synthetic.jsonl + FakeJudge: machinery proof, NOT a
!! measured kappa. The real number waits for data/labels/human_labels.jsonl.

headline (outcome axis, the export gate's number)
  kappa=0.565581 (n=40, po=0.675, pe=0.251875)  CI95=[0.361881, 0.757581] (B=10000, degenerate=0)  band=moderate
```

`make export` — five named checks; the failing one fails in public:

```
gate        min_export_kappa=0.6
  [pass] headline_ready      n_matched=40 >= min_human_labels=30
  [pass] headline_status     ok
  [pass] instrument_binding  agreement fingerprint == labeling fingerprint
  [pass] ground_truth_bound  human_labels_sha256=dfed5b686cb8…
  [FAIL] kappa_threshold     kappa=0.565581 < min_export_kappa=0.6
  verdict     blocked -> OVERRIDDEN (deliberate)
```

## The machinery was proven on synthetic data first

Before the live session ran, the same three CLIs proved the entire measurement
machinery on committed synthetic fixtures — the numbers below are machinery proof, not
findings, and say so on their face:

| | Measured | The honest fine print |
|---|---|---|
| **Test suite** | **677 offline tests**, CI green | no API key, no Docker, no network; includes byte-equality against committed goldens, double-run identity, and the red-team payloads replayed verbatim |
| **Demo funnel** | 64 records in → 56 after dedup (3 exact + 5 near, 1 chain-flagged) → 5 clusters + 21 noise → 50 sampled → 49 labeled | `make demo`, byte-pinned by `tests/golden/demo_output.txt`; fixtures are synthetic — cluster counts are machinery proof, not findings |
| **Agreement machinery** | κ = **0.565581** (outcome axis, n=40), CI95 **[0.361881, 0.757581]**, B=10000, seed 1750, per-class table + confusion matrix (every class clears `min_class_support`) | `make agreement`, byte-pinned — **SYNTHETIC by design**, see below |
| **The gate fails honestly** | export gate **blocks**: 0.565581 < 0.6 | `make export`, byte-pinned; the demo exports only through an explicit override whose reason prints on the export's face |
| **The product** | `golden.jsonl` (49 lines; identity + texts self-verify per line, labels fenced by the file digest in `meta.json`) + `meta.json` (input SHA-256s, all knobs, the full validated report chain) | written to gitignored `data/out/`; `/repro-audit` byte-diffs both against regeneration |

**Why publishing a fake κ is a feature.** The κ above is deliberately meaningless as a
measurement: the judge is a deterministic `FakeJudge` (sha256-derived verdicts) and the
human side is a committed synthetic fixture — both CLIs open with a mandatory
`!! SYNTHETIC` banner. What it proves is the **entire measurement machinery**, verified
before a single real label exists: hand-computed κ fixtures (e.g. κ = 16/31 checked on
paper, sklearn as an independent oracle), a paired seeded bootstrap with degenerate
resamples excluded *and counted*, self-validating reports that recompute their own κ on
every deserialization — a report that lies about its own numbers refuses to exist. When
the real human labels landed in `data/labels/human_labels.jsonl`, only the number
changed — the machinery was already proven byte-for-byte. And on the committed fixtures
the export gate **genuinely blocks** (κ = 0.565581 < 0.6) — a pipeline demonstrated
failing honestly is worth more than one demonstrated only passing. The
[real domain dataset](#the-real-κ-measured--and-a-full-flywheel-turn) *clears* the same gate at κ = 0.80; the
first real run did not, and blocked. Both outcomes are the gate doing its job.

## What the red team caught

Every phase was attacked by a **read-only adversarial reviewer before merge** — a
separate agent whose only job is to break the diff. It found real defects that
passed the happy path; each fix ships with a regression test **replaying the red team's
own payload**:

| Phase | The attack, and the proof | The structural fix |
|---|---|---|
| 1 · Redaction | An `sk-ant-` key split with **U+2060 WORD JOINER** walked straight past the invisible-character strip — the hand-enumerated four-code-point table missed the direct successor of the one word joiner it covered. | Strip the **entire Unicode `Cf` category by predicate** (plus non-`Cf` stragglers), on *both* sides of NFKC — never a code-point list again. The payload family (word joiner, soft hyphen, CGJ, tag chars, Hangul fillers) is replayed verbatim in the suite. |
| 2 · Dedup (**blocker**) | An exact-dup pair (A, B) plus an earlier-sorting near-variant Z of the survivor (measured cosine 0.933872): the report said `exact(B → kept A)` *and* `near(A → kept Z)` — A was simultaneously a kept-reference and a dropped id, so the report's own validator refused and killed the run. On mundane, guaranteed-in-production data (redaction twins produce exact-dup pairs by design). | Exact entries are **remapped to the final survivor** before the report is assembled (one hop provably suffices). The validator that caught it stays; the forged-outcome refusal cases became contract tests. |
| 3 · Judge | The few-shot store hashed **raw** text while the contamination gates hash **redacted** text: a secret-bearing few-shot would ship its secret verbatim to the judge API *and* evade both gates, because `hash(raw) ≠ hash(redacted)` — proven with the fixture's planted email. | The loader **refuses to load** any example the production sanitizer would rewrite (injected via a Protocol, so `label/` still imports nothing but contracts). The leaky path doesn't exist, rather than being reviewed away. |
| 4 · κ | Selectively deleting the 3 disagreeing lines of a human-label file lifts κ from **0.516129 to exactly 1.0** (red-team fixture, arithmetic checked in integers) — and nothing tied a published κ to the file bytes it was measured on. | `AgreementReport` now binds **`human_labels_sha256`** — the exact ground-truth bytes — printed in the report header and structurally copied into `meta.json`. Two filtered files now produce two visibly different bindings. |

Phase 5's pass caught one more: the export's contamination evidence named the colliding
*few-shot id* by zipping two independently-sorted tuples — on the committed store, **5 of
5 hashes zipped to the wrong id**. The evidence now names the colliding *content hash*,
the one thing the fingerprint can actually prove. Every finding and its closure is
recorded in the ADR amendment sections.

## Design principles

- **Ids are content-derived, computed *after* redaction.** Two raw lines identical except
  for their secret yield the same `record_id` — no secret bits in a published
  identifier, no id rotation when a secret rotates. Pinned by test.
- **Double blindness, structurally.** The judge's only per-record channel is
  `judge(input_text, output_text)` — two strings; the human annotation template renderer
  *cannot receive* judgments by signature. Both directions are AST-walk import-tested at
  every nesting depth.
- **Nothing is dropped in silence.** Every line/record lands in exactly one typed,
  counted bucket (ingest: normalized/rejected/skipped; labeling: five buckets; export:
  exported/blocked) and every report **refuses to validate if its sums lie**.
- **Byte-exact determinism.** Seeded clustering/sampling/bootstrap, content-derived ids,
  no wall clock in pure paths, one canonical sort order for every artifact. The three
  CLIs are pinned by committed goldens and double-run identity tests.
- **Provenance by SHA-256.** `meta.json` digests every input file, the human-label bytes
  the κ was measured on, and the exact `golden.jsonl` it certifies — and recomputes the
  gate verdict from its own embedded reports: a manifest that lies about its run
  **refuses to deserialize**.
- **Honest gates.** κ below 0.6 blocks the export. The only escape is a typed override
  with a mandatory reason, scoped to the value check alone, rendered loudly on the
  export's face.

## Architecture decisions

| ADR | Decides |
|---|---|
| [ADR-0001](docs/decisions/ADR-0001-logrecord-ingestion-redaction.md) | The frozen `LogRecord` atom, TraceSpan + generic ingestion, redaction at the boundary, ids after redaction, the self-validating ingest report |
| [ADR-0002](docs/decisions/ADR-0002-dedup-clustering-sampling.md) | Exact + near-dup dedup (union-find, flagged chains), the supervised threshold-measurement protocol, HDBSCAN coverage clustering, stratified sampling, the byte-pinned demo |
| [ADR-0003](docs/decisions/ADR-0003-label-taxonomy-judge.md) | A two-axis taxonomy sized for per-class κ at n=30–50, the two-string `Judge` Protocol (blindness by signature), typed labeling failures, the few-shot leakage gate |
| [ADR-0004](docs/decisions/ADR-0004-agreement-kappa-protocol.md) | The agreement protocol: strict human-label loader, one hand-checked κ formula (global + per-class), paired percentile bootstrap CI95, typed degeneracy, ground-truth SHA-256 binding |
| [ADR-0005](docs/decisions/ADR-0005-export-provenance-gates.md) | Canonical `golden.jsonl`, the two-section `meta.json`, the contamination guard as a validator, the five-check κ gate and its deliberate, scoped override |
| [ADR-0006](docs/decisions/ADR-0006-taxonomy-v2-live-status-convention.md) | Taxonomy v2: the bounded-plausibility convention for live-status claims (the κ=0.263 `correct → unjudgeable` gap closed in the guideline), v1 frozen for provenance, the cross-version anti-mix guard |

## Project structure

```
src/evalgen/
  contracts/        frozen Pydantic models shared by every stage — imports no sibling module
  ingest/           loaders (TraceSpan, generic JSONL) · normalization · redaction at the boundary
  dedup/            exact + near-dup · the threshold calibration harness
  cluster/          hashing embedder · HDBSCAN · stratified sampling
  label/            Judge Protocol · AnthropicJudge (SDK) · FakeJudge · few-shot store + gates
  validate/         human labels · κ + bootstrap · agreement report (sees both raters, writes nothing)
  export/           five-check gate · assembly · canonical serialization · atomic pair-staged writer
  demo.py / agreement_demo.py / export_demo.py    zero-arg composition layers — the three CLIs
data/fixtures/      committed demo logs + synthetic annotations (say SYNTHETIC on their face)
data/fewshots/      the judge's few-shot store — redaction-clean by construction
data/labels/        real human ground truth lands here — hook-protected, agent writes blocked
docs/decisions/     the six ADRs (red-team amendments included)
tests/              677 offline tests, incl. golden byte-equality + replayed red-team payloads
```

Module boundaries are enforced by tests, not convention: `contracts` imports no sibling;
`label` can never see human labels; `validate` writes nothing; `export` imports only
contracts and `writer.py` is its sole writing module — all pinned by AST walks and greps.

## Built by a governed multi-agent system

The committed [`.claude/`](.claude/) directory is the agentic engineering setup that
built this repo: **8 specialized subagents** (an architect, four domain engineers, a
stats scientist, a read-only red-teamer, a docs historian), **5 orchestration commands**,
and hooks that block agent writes to the human ground truth — an agent that could rewrite
`human_labels.jsonl` could fabricate its own κ. Every phase was designed (ADR),
implemented offline-tested, adversarially red-teamed, and merged behind a green CI — the
same recursion as the sibling repos.

## Roadmap

Done:

- [x] Phases 0–5: contracts + ingest/redaction · dedup/cluster/sampling · judge ·
  agreement · export — five ADRs, three byte-pinned CLIs (612 offline tests then;
  658 with the real-session CLI pair below, 677 with taxonomy v2 + the domain corpus)
- [x] Red-team pass on every phase, with closures recorded as ADR amendments and
  regression tests replaying each payload
- [x] **Real human labels → the real κ — measured twice, published as-is.** Turn 1
  (generic corpus): outcome κ = **0.263158**, CI95 straddling zero, gate blocked —
  the unfavorable number this rule exists for. Turn 2 (domain corpus, v2 guideline):
  outcome κ = **0.803869**, CI95 [0.652, 0.933], gate opens. Both live-judge runs are
  committed under [`docs/reports/`](docs/reports/); see [The real κ](#the-real-κ-measured--and-a-full-flywheel-turn).
- [x] Real-data CLI pair — explicit flags only, never autodetection: `make annotate`
  emits the blank template into `data/annotation/` (structurally apart from judge
  output — each CLI refuses a directory holding the other family's artifacts), and
  `python -m evalgen.agreement_run --labels … --judge {fake,anthropic}` computes the
  agreement with the `!! SYNTHETIC` banner decided on label *content* (never bytes
  alone) and a per-run audit trail for live-judge re-rolls.

Next — the honest part:

- [x] Sharpen the annotation guideline for live-status claims (the
  `correct → unjudgeable` gap [the drill-down surfaced](#the-real-κ-measured--and-a-full-flywheel-turn)):
  **taxonomy v2** ([ADR-0006](docs/decisions/ADR-0006-taxonomy-v2-live-status-convention.md))
  grades inherently live claims *as responses* and reserves `unjudgeable` for
  defective inputs; v1 stays frozen for provenance and `agreement_run` refuses v1
  labels against the v2 judge (both directions, before any API cost).
- [x] Relabel under v2 on the domain corpus and re-measure — done: outcome
  κ = **0.804** on the FCD-redressement set, gate clears (turn 2 above), every
  `outcome` class now with the support to report its own κ.
- [ ] Real-data export now that a κ clears the gate — wire `make export` to the domain
  run's report (the Phase 5 gate + provenance ship today); the `--allow-low-kappa
  "<reason>"` override stays for the runs that don't.
- [ ] Near-dup threshold calibration on real labeled pairs — the protocol, harness, and
  report validator ship today; 0.92 stays a default until measured.
- [ ] Semantic embedding backend behind the `Embedder` Protocol, with its own
  calibration run (thresholds are embedder-specific by construction).
- [ ] A `failure_mode` taxonomy axis once support exists (≥ ~60 failure-outcome human
  labels — per-class κ needs support, not ontology).

## License

[MIT](LICENSE)
