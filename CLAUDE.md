# CLAUDE.md

Project memory for **eval-dataset-generator**. This file is loaded into context at the
start of every Claude Code session. Read it before touching code — it encodes the
non-negotiables that keep this repo's senior signal intact.

---

## 1. What this project is

The third leg of a three-repo system (with `hybrid-rag-pipeline` and
`multi-agent-orchestrator`): an **evaluation-dataset generator that turns raw
production logs into a labeled, deduplicated, statistically validated golden set** —
closing the improvement flywheel (prod traces → eval data → better systems).

- **First-class input: the sibling repo's traces.** `multi-agent-orchestrator` emits
  typed `TraceSpan` JSONL — this repo mines them natively, plus a generic JSONL
  adapter for any log source. The synergy IS the point: each repo feeds the next in daily use.
- **Mining, typed** — ingestion normalizes heterogeneous logs into a Pydantic
  `LogRecord`; **secrets/PII are redacted at the ingestion boundary**, before
  anything persists.
- **Dedup before anything else** — exact dedup (content hash) then near-dup
  (embedding cosine above a measured threshold). A golden set with hidden
  near-duplicates silently inflates every downstream metric.
- **Clustering for coverage** — deterministic embeddings + HDBSCAN
  (scikit-learn ≥ 1.3) to map the log distribution, then **stratified sampling**
  so the exported set covers the real traffic, not the easy head.
- **Auto-labeling with an LLM judge** — structured output against a typed label
  taxonomy. Never free text, never trusted blindly.
- **THE headline signal: measured agreement.** A subset is human-labeled; the
  judge's labels are validated with **Cohen's κ** (global + per-class) and a
  **bootstrap CI95**. The dataset ships with its κ printed on it — measured,
  never declared. A κ that would embarrass is published as-is.
- **Provenance + contamination guards** — every export writes `meta.json`
  (git SHA, input SHA-256, seeds, model ids, thresholds); exported items are
  checked against judge few-shot prompts (leakage) and can be traced back to
  their source spans.

The differentiator is **not the stack** — it is dedup done honestly + coverage by
clustering + agreement measured with κ/CI95 + full provenance. An auto-label
pipeline whose κ is unmeasured, a golden set with near-dupes, or an export whose
provenance can't be replayed destroys the entire signal. Treat those failure modes
as the things this codebase exists to get right.

## 2. Stack

- **Python 3.11+**, typed throughout; **Pydantic v2** models at every seam.
- **scikit-learn ≥ 1.3** (HDBSCAN, metrics), **numpy/scipy** (κ, bootstrap CI95).
- **Anthropic SDK** for the LLM judge (binding rules below). Tests use a
  **deterministic fake judge** + a **hashing embedder** — the suite needs no API
  key, no Docker, no network.
- **ruff + black** (lint/format), **mypy**, **pytest**, GitHub Actions CI.
- CLI-first: `python -m evalgen …` driven through `make` targets. No web service
  in v1 (an ADR can revisit).

### LLM / Anthropic SDK rules (load-bearing)

Any code that calls Claude (the judge, taxonomy bootstrapping) MUST use the official
**Anthropic Python SDK** (`anthropic`) — never raw `requests`/`httpx`.

- Default judge model: **`claude-opus-4-8`**; `claude-sonnet-4-6` for high-volume
  passes when explicitly chosen. Every record stores the **model id actually used**.
- **Adaptive thinking**: `thinking={"type": "adaptive"}`; depth via
  `output_config={"effort": "high"}`.
- **Do NOT** use deprecated `budget_tokens`; **do NOT** pass
  `temperature`/`top_p`/`top_k` (removed on 4.7/4.8 — 400).
- Structured labels via `client.messages.parse(output_format=<PydanticModel>)` →
  `.parsed_output` — never prefill, never regex over free text.
- The judge is correctness-sensitive: schema-validated output, refusals surface as
  typed errors (never silently dropped records).

## 3. Target architecture

```
production logs (JSONL: TraceSpans from multi-agent-orchestrator · generic adapter)
   ─▶ src/evalgen/contracts/   shared Pydantic models: LogRecord, Cluster, LabeledExample,
                                LabelTaxonomy, AgreementReport, ExportManifest
   ─▶ src/evalgen/ingest/      loaders (tracespan, generic jsonl) · normalization ·
                                REDACTION at the boundary (secrets/PII never persist)
   ─▶ src/evalgen/dedup/       exact (content hash) · near-dup (embedding cosine, threshold
                                measured not guessed) · dedup report (what was dropped, why)
   ─▶ src/evalgen/cluster/     embeddings (hashing default, pluggable real) · HDBSCAN ·
                                stratified coverage sampling (per-cluster quotas)
   ─▶ src/evalgen/label/       LLM judge (structured output) · label taxonomy · few-shot
                                store (guarded against leakage into exports)
   ─▶ src/evalgen/validate/    human-label subset workflow · Cohen's κ (global + per-class)
                                · bootstrap CI95 · disagreement drill-down
   ─▶ src/evalgen/export/      golden.jsonl candidates · meta.json provenance ·
                                contamination guard (export ∩ few-shots = ∅)
src/evalgen/config.py           Pydantic settings (thresholds, seeds, budgets, model ids)
data/labels/human_labels.jsonl  human ground truth — PROTECTED, never agent-mutated
docs/architecture.md            kept in sync with src/evalgen
docs/decisions/ADR-NNN-*.md     architecture decision records
```

**Module-boundary rules:** `contracts` is imported by everyone and imports no one.
Redaction lives in `ingest` and nothing downstream ever sees raw secrets. `label`
never reads `validate`'s human labels (the judge must stay blind to ground truth).
`export` depends on everything; nothing depends on `export`. Cross-module contract
changes go through `evalgen-architect` and get an ADR.

## 4. Build / test / eval commands (Makefile)

| Target | What it does |
|--------|--------------|
| `make install` | Install deps (pyproject) + dev extras |
| `make test` | `pytest` (fully offline: fake judge, hashing embedder, fixture logs) |
| `make lint` | `ruff check` + `black --check` |
| `make fmt` | `ruff check --fix` + `black` |
| `make typecheck` | `mypy src` |
| `make demo` | End-to-end pipeline on committed fixture logs — offline, deterministic *(Phase 2+)* |
| `make agreement` | Compute Cohen's κ + CI95 of judge vs human labels — the headline number *(Phase 4+)* |
| `make export` | Produce golden.jsonl + meta.json provenance from a run *(Phase 5+)* |

**Headline-metric rule:** numbers in the README / `docs/` come **only** from a
reproducible `make agreement` / `make export` run, provenance-stamped. Never invent,
round up, or hand-edit a metric. No κ, no dataset.

## 5. Coding standards

- **Typing:** full type hints; Pydantic models for all structured data. No bare
  `dict` across module boundaries.
- **Determinism:** seeded everywhere (clustering, sampling, bootstrap); hashing
  embedder default; content-derived ids (no bare uuid4 in pipeline paths). Same
  inputs → byte-identical exports. This mirrors the bit-exact discipline of the
  sibling repos.
- **Tests:** offline only. Dedup covered including **near-dup edge cases** (threshold
  boundaries, transitive chains); κ covered with **hand-checked fixtures** (a wrong
  κ must fail a test, not ship); redaction covered with **adversarial payloads**
  (keys in nested fields, unicode tricks); contamination guard covered with a
  **deliberately-leaked fixture**.
- **No metric leakage / overclaiming:** human labels never reach the judge; few-shot
  examples never reach exports; κ is reported with n and CI95, per-class when class
  support allows; unfavorable κ published as-is.
- **Safety:** redaction before persistence, always; human-label file is
  hook-protected; judge refusals/parse failures are typed, counted, and reported —
  never silently dropped.
- **ADRs** for load-bearing decisions (dedup thresholding, clustering choice,
  taxonomy design, κ protocol, export format) in `docs/decisions/`.
- **Secrets:** never read/write `.env`; config through `config.py`.

## 6. Agent team

Each agent owns one boundary; opus goes where a subtle bug silently destroys
credibility (design, statistics, red team).

| Agent | Model | Owns / when to use |
|-------|-------|--------------------|
| `evalgen-architect` | opus | Design, ADRs, cross-cutting trade-offs (taxonomy, dedup strategy, κ protocol, export format). **Use proactively** before any new subsystem. |
| `ingest-engineer` | sonnet | `ingest/` + `contracts/` — loaders, normalization, **redaction at the boundary**, TraceSpan adapter. |
| `mining-engineer` | sonnet | `dedup/` + `cluster/` — content hashing, near-dup cosine, HDBSCAN, stratified sampling, dedup/coverage reports. |
| `judge-engineer` | sonnet | `label/` — LLM judge via SDK structured output, taxonomy models, few-shot store + leakage discipline, fake judge for tests. |
| `stats-scientist` | opus | `validate/` — Cohen's κ (global/per-class), bootstrap CI95, disagreement analysis, contamination guards. The repo's headline signal. |
| `pipeline-engineer` | sonnet | `export/`, CLI (`python -m evalgen`), `config.py`, provenance `meta.json`, Makefile wiring. |
| `adversarial-reviewer` | opus | **Read-only** red team. Attacks the diff: dedup misses, κ gaming, leakage judge↔human↔export, redaction bypasses, nondeterminism. **Use proactively** before any commit. Never edits. |
| `docs-historian` | haiku | `docs/architecture.md`, ADRs, README headline block. Syncs docs to code and to **reproducible** numbers only. |

## 7. Slash commands

| Command | Use it to |
|---------|-----------|
| `/implement-feature` | Build one feature end-to-end: architect frames (+ADR) → domain agent implements + tests → adversarial review → docs sync. **Stops for human sign-off before any commit.** |
| `/eval-report` | Produce the defensible κ/coverage report; refuses non-reproducible numbers. |
| `/adr-new <topic>` | Capture a load-bearing decision. |
| `/adversarial-review` | Red-team the working diff before commit. |
| `/repro-audit` | Re-run the pipeline on fixtures and byte-diff exports + meta.json against the committed reference. |

## 8. Hooks

- **PostToolUse / Edit|Write → `format_python.py`** — auto ruff + black.
- **PreToolUse / Edit|Write → `protect_golden_and_secrets.py`** — **blocks (exit 2)**
  writes to `data/labels/human_labels*.jsonl` (the human ground truth), any
  `golden*.jsonl`, `.env*`, and provenance `meta.json`. An agent that could rewrite
  the human labels could fabricate its own κ.
- **Stop → `eval_guard.py`** — if anything under
  `src/evalgen/{ingest,dedup,cluster,label,validate}/` changed, reminds (exit 2) to
  re-run `make test` + `/eval-report` before claiming results.

## 9. House workflow rules

- Branch before committing; commit/push **only when asked**. Each phase lands as a
  PR; the human merges (sign-off gate).
- Re-run `make test` + `make lint` before claiming a feature done.
- After any dedup/cluster/label/validate change, re-measure — never report stale κ.
- **No AI attribution anywhere public**: no `Co-Authored-By: Claude` trailers, no
  "Generated with" footers — in commits, PRs, or docs. Recruiters read this repo.
