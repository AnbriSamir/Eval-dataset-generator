# Eval Dataset Generator

> Turn raw **production logs** into a **labeled, deduplicated, statistically validated
> evaluation dataset** — with agreement **measured** (Cohen's κ + bootstrap CI95),
> never declared. The third leg of the flywheel: *prod traces → eval data → better systems*.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](#)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Status](https://img.shields.io/badge/status-in%20progress-orange)](#roadmap)

> 🚧 **Status: Phase 0 (scaffold).** The pipeline lands phase by phase, each one
> designed (ADR), implemented with offline tests, adversarially red-teamed, and
> merged behind a green CI — the same discipline as the sibling repos
> [`multi-agent-orchestrator`](https://github.com/anbsamsam17/multi-agent-orchestrator)
> and [`hybrid-rag-pipeline`](https://github.com/anbsamsam17/hybrid-rag-pipeline).

## Why this project

Everyone evaluates LLM systems; almost nobody says where their eval set came from.
Hand-written sets drift from real traffic, silently contain near-duplicates, and
carry labels nobody ever validated. This repo industrializes the honest version:

- **Mine real production logs** — first-class input: the typed `TraceSpan` JSONL
  emitted by the sibling `multi-agent-orchestrator` (plus a generic adapter).
- **Redact at the boundary** — secrets/PII never persist past ingestion.
- **Dedup honestly** — exact + near-duplicate (embedding cosine), with a report of
  what was dropped and why. Hidden near-dupes inflate every downstream metric.
- **Cover the real distribution** — deterministic embeddings + HDBSCAN clustering,
  then stratified sampling: the export covers the traffic, not the easy head.
- **Auto-label with an LLM judge** — typed, structured output against a label
  taxonomy. Never free text, never trusted blindly.
- **Measure the agreement** — a human-labeled subset validates the judge with
  **Cohen's κ** (global + per-class) and a **bootstrap CI95**. The dataset ships
  with its κ printed on it; an embarrassing κ is published as-is.
- **Prove the provenance** — every export writes `meta.json` (git SHA, input
  SHA-256, seeds, model ids, thresholds) and passes a contamination guard
  (exported items never overlap the judge's few-shot prompts).

The differentiator is not the stack — it is **dedup done honestly + coverage by
clustering + agreement measured with κ/CI95 + full provenance**.

## Pipeline

```
production logs (TraceSpan JSONL · generic JSONL)
  → ingest     normalize + REDACT at the boundary
  → dedup      exact (hash) + near-dup (cosine) + dedup report
  → cluster    embeddings + HDBSCAN + stratified coverage sampling
  → label      LLM judge (structured output, typed taxonomy)
  → validate   human subset → Cohen's κ (global + per-class) + bootstrap CI95
  → export     golden.jsonl + meta.json provenance + contamination guard
```

## Quickstart

```bash
git clone https://github.com/anbsamsam17/eval-dataset-generator && cd eval-dataset-generator
make install   # pip install -e ".[dev]"
make test      # offline test suite — no API key, no Docker, no network
```

`make demo` (fixtures end-to-end), `make agreement` (the κ report) and
`make export` land with their phases — a number only appears in this README once
it is reproducible from one of those commands.

## Roadmap

| Phase | Delivers |
|---|---|
| 0 · Scaffold ✅ | src layout, typed config, offline tests, CI, governed agent team (`.claude/`) |
| 1 · Contracts + ingest | `LogRecord` + loaders (TraceSpan, generic) + redaction at the boundary |
| 2 · Dedup + cluster | exact/near-dup + HDBSCAN + coverage sampling + `make demo` |
| 3 · Judge | LLM auto-labeling (structured output) + taxonomy + few-shot leakage guards |
| 4 · Agreement | human-label workflow + Cohen's κ + CI95 + `make agreement` |
| 5 · Export | golden.jsonl + provenance + contamination guard + `make export` |
| 6 · Showcase | README with the real measured numbers |

## Engineering discipline

- **Typed everything** — Pydantic contracts at every module seam.
- **Deterministic** — seeded clustering/sampling/bootstrap, content-derived ids,
  hashing embedder by default: same inputs → byte-identical exports.
- **Measured, never declared** — κ with n and CI95, per-class when support allows;
  unfavorable verdicts published as-is.
- **Guarded ground truth** — the human label file is hook-protected against agent
  mutation; the judge is blind to it by construction.
- **Built by a governed multi-agent system** — the committed [`.claude/`](.claude/)
  team (8 specialized subagents, 5 commands, safety hooks), with every phase
  adversarially red-teamed before merge. Same recursion as the sibling repos.

## License

[MIT](LICENSE)
