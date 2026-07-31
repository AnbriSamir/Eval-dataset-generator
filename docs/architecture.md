# Architecture

> Living document — kept in sync with `src/evalgen` by `docs-historian`.
> Each phase fills in its section and links its ADR(s).

## Pipeline (target)

```
production logs (TraceSpan JSONL from multi-agent-orchestrator · generic JSONL)
  → ingest/     loaders · normalization · redaction at the boundary      [Phase 1 ✔]
  → dedup/      exact (content hash) · near-dup (embedding cosine) · dedup report
                                                                         [Phase 2 ✔]
  → cluster/    embeddings (hashing default) · HDBSCAN · stratified sampling
                                                                         [Phase 2 ✔]
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

## Dedup + clustering + sampling + demo (Phase 2 — implemented, [ADR-0002](decisions/ADR-0002-dedup-clustering-sampling.md))

```
contracts/records.py      + record_sort_key — THE canonical total order of Phase 2+:
                          survivor choice, representatives, matrix rows, report entries
contracts/embeddings.py   Embedder Protocol (unit-row float64, deterministic, offline) ·
                          EmbedderFingerprint travels in every report (a threshold is only
                          valid for the embedder it was measured with)
contracts/dedup.py        ExactDupEntry / NearDupEntry / DedupReport / DedupOutcome —
                          self-validating (in == out + id_collapsed + exact + near; every
                          via_chain flag re-checked; no survivor among the dropped) ·
                          DedupOutcome cross-checks kept against the report (no dropped
                          record among kept, no ghost survivor referenced)
contracts/clustering.py   derive_cluster_id (content-derived "cl-…") · Cluster ·
                          ClusteringReport (cluster sizes + noise == records_in) ·
                          StratumSample / SamplingReport (Σ quotas == total_sampled)
contracts/calibration.py  LabeledPair · ThresholdCalibrationReport (validator recomputes
                          the F1-argmax, ties to the highest threshold)
dedup/__init__.py         run_dedup — the public seam: id-collapse → exact → near, then
                          exact entries REMAPPED to the final survivor (an exact survivor
                          can itself be near-dropped; one hop suffices) before the report
dedup/exact.py            id-collapse (double-ingest idempotence, counted) then full
                          SHA-256 groups over canonical_text; survivor = sort-key minimum
dedup/neardup.py          union-find components over cosine >= threshold (inclusive) ·
                          chain collapses flagged per entry (via_chain), never hidden
dedup/calibrate.py        the threshold-measurement protocol over
                          data/fixtures/neardup_pairs.jsonl (0.92 stays the default until
                          measured on real labeled pairs) · python -m evalgen.dedup.calibrate
                          · refuses < 2 distinct sims by name · rounded midpoints merged,
                          metrics computed against the printed threshold (replayable rows)
cluster/embeddings.py     HashingEmbedder — char_wb 3-5-grams, L2 rows, bit-stable,
                          fit-free; dim from Settings.hash_embedding_dim
cluster/clustering.py     HDBSCAN (metric="euclidean" on unit rows ⇔ cosine ordering) ·
                          label < 0 → noise, first-class · content-derived cluster ids
cluster/sampling.py       floor-1 largest-remainder quotas (all-integer) · seeded
                          sha256(seed ␟ record_id) ranking — stateless, order-invariant
demo.py                   make demo → ingest → dedup → cluster → sample on the fixtures;
                          byte-identical output pinned by tests/golden/demo_output.txt
```

Invariants the tests pin: survivor rule independent of input order (shuffle tests on
dedup, clustering, sampling); the `>=` boundary AT the threshold (injected vectors);
the transitive chain A~B~C with A≁C — one survivor, C's entry `via_chain=True`;
the red-team BLOCKER payload verbatim (exact-dup pair whose survivor is itself
near-dropped by an earlier-sorting variant → exact entries name the FINAL survivor);
forged DedupOutcomes refuse (kept record reported dropped, ghost survivor reference);
hand-computed sweep arithmetic + the high-tie rule; calibration degenerate inputs
(identical sims → named error, near-identical sims → merged candidates); config knobs
out of contract range refuse at load time naming the knob; hand-computed skewed quotas
[50, 3, 2] × k=10 → [8, 1, 1]; HDBSCAN labels < 0 (not just −1) all become noise;
every report's refuse-to-validate cases; demo golden byte-equality + leak scan
(no planted secret, no absolute path).

## Module boundaries (enforced from day 0)

- `contracts` is imported by everyone and imports no one (pinned by a test).
- Redaction lives in `ingest`; nothing downstream ever sees raw secrets.
- `dedup` and `cluster` import only `contracts`: the embedder is INJECTED through the
  `Embedder` Protocol, so dedup never imports against the pipeline flow (pinned by
  module-top-import greps; `calibrate.main()`'s inner import is the one sanctioned
  composition-layer exception).
- `demo` is composition: it imports the pipeline, nothing imports it (pinned).
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
- [ADR-0002 — Dedup (exact + near-dup), HDBSCAN coverage clustering, stratified
  sampling, and the offline demo](decisions/ADR-0002-dedup-clustering-sampling.md) —
  Phase 2: content-derived survivor rule (`record_sort_key`), union-find near-dup
  components with flagged chain collapses, the supervised threshold-measurement
  protocol (labeled-pairs fixture; 0.92 stays a default until measured on real data),
  the `Embedder` Protocol seam, HDBSCAN on L2-normalized hashing embeddings with
  noise as a first-class stratum, floor-1 largest-remainder stratified sampling with
  seeded-hash selection, and the byte-identical `make demo` golden.
