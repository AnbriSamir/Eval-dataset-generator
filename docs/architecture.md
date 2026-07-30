# Architecture

> Living document — kept in sync with `src/evalgen` by `docs-historian`.
> Phase 0: skeleton only. Each phase fills in its section and links its ADR(s).

## Pipeline (target)

```
production logs (TraceSpan JSONL from multi-agent-orchestrator · generic JSONL)
  → ingest/     loaders · normalization · redaction at the boundary
  → dedup/      exact (content hash) · near-dup (embedding cosine) · dedup report
  → cluster/    embeddings (hashing default) · HDBSCAN · stratified sampling
  → label/      LLM judge (structured output) · taxonomy · few-shot store
  → validate/   human subset · Cohen's κ (global + per-class) · bootstrap CI95
  → export/     golden.jsonl · meta.json provenance · contamination guard
```

## Module boundaries (enforced from day 0)

- `contracts` is imported by everyone and imports no one (pinned by a test).
- Redaction lives in `ingest`; nothing downstream ever sees raw secrets.
- `label` never reads the human labels (the judge stays blind to ground truth).
- `export` depends on everything; nothing depends on `export`.

## Decisions

ADRs land in [`decisions/`](decisions/) as the phases begin (dedup thresholding
protocol, clustering choice, taxonomy design, κ protocol, export format).
