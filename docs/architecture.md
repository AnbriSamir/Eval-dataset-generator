# Architecture

> Living document — kept in sync with `src/evalgen` by `docs-historian`.
> Each phase fills in its section and links its ADR(s).

## Pipeline (target)

```
production logs (TraceSpan JSONL from multi-agent-orchestrator · generic JSONL)
  → ingest/     loaders · normalization · redaction at the boundary      [Phase 1 ✔]
  → dedup/      exact (content hash) · near-dup (embedding cosine) · dedup report
  → cluster/    embeddings (hashing default) · HDBSCAN · stratified sampling
  → label/      LLM judge (structured output) · taxonomy · few-shot store
  → validate/   human subset · Cohen's κ (global + per-class) · bootstrap CI95
  → export/     golden.jsonl · meta.json provenance · contamination guard
```

## Contracts + ingest (Phase 1 — implemented, [ADR-0001](decisions/ADR-0001-logrecord-ingestion-redaction.md))

```
contracts/records.py   LogRecord (frozen atom) · RecordOrigin · derive_record_id
                       canonical_text (dedup) / cluster_text (coverage) decided ONCE here;
                       a model_validator recomputes the id — a tampered record refuses to exist
contracts/reports.py   IngestReport (self-validating: normalized+rejected+skipped == lines_read)
                       · RejectReason / SkipReason · scrubbed, truncated reject samples
ingest/reader.py       byte-level JSONL reading — one bad UTF-8 line = one typed reject,
                       never a lost file
ingest/redaction.py    normalize (invisible-char strip [whole Cf category + stragglers,
                       BOTH sides of NFKC] · NFKC · newline fold) then ordered categorized
                       patterns → [REDACTED:<category>] · recursive scrub_value (dict keys too)
ingest/normalize.py    build_record — the ONLY production constructor of LogRecord:
                       normalize → redact → derive id → freeze · ReportBuilder accumulator
ingest/tracespan.py    sibling-repo adapter (local structural mirror, no cross-repo import) ·
                       candidacy: action allowlist ∧ status ok ∧ extractable exchange
ingest/generic.py      any JSONL via an explicit GenericMapping (dot-paths, opt-in metadata) ·
                       candidacy decided on SANITIZED text (an invisible-only field is one
                       no_exchange skip, never a file-aborting error)
```

Invariants the tests pin: ids content-derived **after** redaction (same id when only the
secret differs); byte-identical double loads; every line in exactly one report bucket; no
planted secret survives into records *or* report; the adversarial redaction battery replays
the red-team payloads verbatim (word-joiner/soft-hyphen/CGJ-split keys, split opaque tokens,
fullwidth homoglyphs, secrets in dict keys and parse-error details).

## Module boundaries (enforced from day 0)

- `contracts` is imported by everyone and imports no one (pinned by a test).
- Redaction lives in `ingest`; nothing downstream ever sees raw secrets.
- `label` never reads the human labels (the judge stays blind to ground truth).
- `export` depends on everything; nothing depends on `export`.

## Decisions

ADRs land in [`decisions/`](decisions/) as the phases begin (dedup thresholding
protocol, clustering choice, taxonomy design, κ protocol, export format).

- [ADR-0001 — LogRecord contract, TraceSpan/generic ingestion, and redaction at the
  boundary](decisions/ADR-0001-logrecord-ingestion-redaction.md) — Phase 1: the frozen
  `LogRecord` atom (canonical texts for dedup/cluster/judge, content-derived
  `record_id` computed **after** redaction), span candidacy for the TraceSpan adapter,
  the generic JSONL mapping, and the self-validating ingestion report (nothing
  silently dropped).
