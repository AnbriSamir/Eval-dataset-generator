# Eval Dataset Generator

> Everyone evaluates LLM systems; almost nobody can say where their eval set came from.
> This repo turns **raw production logs** into a **labeled, deduplicated, statistically
> validated golden dataset** — agreement **measured** (Cohen's κ + bootstrap CI95),
> never declared. And measured for real: the headline human-vs-judge κ came back
> **0.26**, and the export gate **blocks the repo's own dataset**. That is the system
> working.

[![CI](https://github.com/anbsamsam17/Eval-dataset-generator/actions/workflows/ci.yml/badge.svg)](https://github.com/anbsamsam17/Eval-dataset-generator/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-658%20offline-brightgreen)](#the-machinery-was-proven-on-synthetic-data-first)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-complete-brightgreen)](#roadmap)

## The real κ (measured)

The proof is a real, double-blind session: a human annotator filled the blind template
(49 exchanges, no judge output anywhere in sight — the annotation CLI refuses to even
write into a directory holding judge artifacts), and `claude-opus-4-8` judged the same
49 records live. Perfect join: 49/49 matched, zero orphans. The committed run report is
[`docs/reports/agreement_run_report.20260804T002205Z-7c2b30d6.json`](docs/reports/agreement_run_report.20260804T002205Z-7c2b30d6.json),
bound to the exact ground-truth bytes (`human_labels_sha256=eceeb0a9…`); a live-LLM run
is not byte-deterministic, so every number below travels with its digests.

| Axis | κ | CI95 (B=10000) | Band |
|---|---|---|---|
| `task_type` — *what is this exchange?* | **0.861** (n=49, p_o=0.918) | [0.722, 0.967] | almost perfect |
| `outcome` — *is the answer correct?* (**the headline**) | **0.263** (n=49, p_o=0.755) | [**−0.024**, 0.533] | fair |

**And the export gate blocks this dataset for real** (0.263 < 0.6). This unfavorable
headline is the repo's thesis working, published exactly as measured:

- **A percent-agreement metric would have shipped a lie; the κ machinery refused.**
  Raw agreement on `outcome` is 75.5% — sounds fine — but both raters say `correct`
  most of the time, so chance alone predicts 66.8% (p_e): κ collapses to 0.26 and its
  CI95 straddles zero.
- **The gate blocks my own dataset.** κ below 0.6 blocks the export — no exception for
  the author. The only escape is a typed override with a mandatory reason, rendered
  loudly on the export's face; this dataset does not ship until the number earns it.
- **The disagreement drill-down turns the low number into an actionable finding instead
  of a shrug:** 7 of the 12 `outcome` disagreements are `correct → unjudgeable`, six of
  them on live traffic-status questions ("is traffic flowing on the A10 right now?"),
  the seventh on an equally unverifiable tariff query. The human
  graded *plausibility*; the judge applied the written definition of `unjudgeable`
  (*"the answer depends on missing context"*) to claims no transcript can verify. That
  is not noise — it is an **annotation-guideline gap**, surfaced by exactly the
  drill-down built to surface it. The flywheel's next turn is now concrete: sharpen the
  `outcome` definitions for live-status claims, relabel, re-measure — and only then
  should the gate open.

Per-class κ tables, both confusion matrices, and every disagreement with the judge's
rationale are in the committed run report.

> ✅ **Complete.** All six pipeline stages implemented, **658 offline tests**, CI green
> (lint · typecheck · test) — no API key, no Docker, no network. Three zero-argument
> CLIs (`make demo` · `make agreement` · `make export`) are **byte-identical between
> runs** and pinned by committed golden files. Five ADRs record the load-bearing
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

## The flywheel (a three-repo portfolio)

This is the third leg of a loop: *production traces → eval data → better systems*.

- [`multi-agent-orchestrator`](https://github.com/anbsamsam17/multi-agent-orchestrator) —
  agents as production infrastructure. Every decision it makes is a typed, costed
  `TraceSpan` — and that JSONL is **this repo's first-class native input** (a structural
  adapter mines `plan`/`execute`/`verdict` spans; a generic mapping adapter onboards any
  other JSONL source via a six-field declared mapping, only two of them required).
- [`hybrid-rag-pipeline`](https://github.com/anbsamsam17/hybrid-rag-pipeline) — the
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
| **Can you trust the LLM judge?** | A human-labeled subset validates it: Cohen's κ (global **and** per-class) + paired bootstrap CI95, degenerate cases as typed statuses (never NaN, never a silent 0). An embarrassing κ is published as-is — [it was](#the-real-κ-measured). |
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
git clone https://github.com/anbsamsam17/Eval-dataset-generator && cd Eval-dataset-generator
make install     # pip install -e ".[dev]"
make test        # 658 offline tests — no API key, no Docker, no network
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
  in=62  out=54  id_collapsed=0  exact=3  near=5 (via_chain=1)
  …
  near   rec-d829e64df1f0efad -> kept rec-083298153276e970  sim=0.910607  [chain]
  …

[5/5] label   judge=fake model=fake-judge-v1  taxonomy=tax-d9ca3b87b403  prompt=b963c9e7aa28
  in=50  labeled=49  refused=0  failed=0  budget_skipped=0  fewshot_collisions=1  (budget=500)
  collision  rec-d1087e0ca3da3367  (canonical text matches a committed few-shot — never labeled, never exportable)
```

`make agreement` — the banner is mandatory, and κ never travels naked:

```
!! SYNTHETIC — annotations_synthetic.jsonl + FakeJudge: machinery proof, NOT a
!! measured kappa. The real number waits for data/labels/human_labels.jsonl.

headline (outcome axis, the export gate's number)
  kappa=0.513109 (n=40, po=0.675, pe=0.3325)  CI95=[0.286421, 0.707241] (B=10000, degenerate=0)  band=moderate
```

`make export` — five named checks; the failing one fails in public:

```
gate        min_export_kappa=0.6
  [pass] headline_ready      n_matched=40 >= min_human_labels=30
  [pass] headline_status     ok
  [pass] instrument_binding  agreement fingerprint == labeling fingerprint
  [pass] ground_truth_bound  human_labels_sha256=2beaf42e8fd6…
  [FAIL] kappa_threshold     kappa=0.513109 < min_export_kappa=0.6
  verdict     blocked -> OVERRIDDEN (deliberate)
```

## The machinery was proven on synthetic data first

Before the live session ran, the same three CLIs proved the entire measurement
machinery on committed synthetic fixtures — the numbers below are machinery proof, not
findings, and say so on their face:

| | Measured | The honest fine print |
|---|---|---|
| **Test suite** | **658 offline tests**, CI green | no API key, no Docker, no network; includes byte-equality against committed goldens, double-run identity, and the red-team payloads replayed verbatim |
| **Demo funnel** | 62 records in → 54 after dedup (3 exact + 5 near, 1 chain-flagged) → 4 clusters + 12 noise → 50 sampled → 49 labeled | `make demo`, byte-pinned by `tests/golden/demo_output.txt`; fixtures are synthetic — cluster counts are machinery proof, not findings |
| **Agreement machinery** | κ = **0.513109** (outcome axis, n=40), CI95 **[0.286421, 0.707241]**, B=10000, seed 1750, per-class table + confusion matrix | `make agreement`, byte-pinned — **SYNTHETIC by design**, see below |
| **The gate fails honestly** | export gate **blocks**: 0.513109 < 0.6 | `make export`, byte-pinned; the demo exports only through an explicit override whose reason prints on the export's face |
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
the export gate **genuinely blocks** — a pipeline demonstrated failing honestly is
worth more than one demonstrated only passing. The same gate now blocks
[the real dataset](#the-real-κ-measured).

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
docs/decisions/     the five ADRs (red-team amendments included)
tests/              658 offline tests, incl. golden byte-equality + replayed red-team payloads
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
  658 with the real-session CLI pair below)
- [x] Red-team pass on every phase, with closures recorded as ADR amendments and
  regression tests replaying each payload
- [x] **Real human labels → the real κ — measured, published as-is.** A human
  annotator filled the blind template into `data/labels/human_labels.jsonl`
  (hook-protected: humans only) and the live judge ran. See
  [The real κ](#the-real-κ-measured) above — outcome κ = **0.263158** with a CI95
  straddling zero, exactly the kind of unfavorable number this rule exists for.
- [x] Real-data CLI pair — explicit flags only, never autodetection: `make annotate`
  emits the blank template into `data/annotation/` (structurally apart from judge
  output — each CLI refuses a directory holding the other family's artifacts), and
  `python -m evalgen.agreement_run --labels … --judge {fake,anthropic}` computes the
  agreement with the `!! SYNTHETIC` banner decided on label *content* (never bytes
  alone) and a per-run audit trail for live-judge re-rolls.

Next — the honest part:

- [ ] Sharpen the annotation guideline for live-status claims (the
  `correct → unjudgeable` gap [the drill-down surfaced](#the-real-κ-measured)),
  relabel, re-measure — the gate stays closed until the number earns it.
- [ ] Real-data export behind `--allow-low-kappa "<reason>"` — the Phase 5 gate and its
  override contract ship today; the explicit flag waits for a κ worth arguing about.
- [ ] Near-dup threshold calibration on real labeled pairs — the protocol, harness, and
  report validator ship today; 0.92 stays a default until measured.
- [ ] Semantic embedding backend behind the `Embedder` Protocol, with its own
  calibration run (thresholds are embedder-specific by construction).
- [ ] A `failure_mode` taxonomy axis once support exists (≥ ~60 failure-outcome human
  labels — per-class κ needs support, not ontology).

## License

[MIT](LICENSE)
