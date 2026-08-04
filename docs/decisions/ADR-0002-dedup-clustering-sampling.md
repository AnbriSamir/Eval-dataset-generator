# ADR-0002 — Dedup (exact + near-dup), HDBSCAN coverage clustering, stratified sampling, and the offline demo

**Status:** Accepted (2026-07-31) — amended same day after the pre-commit red-team pass:
(a) **exact entries name the FINAL survivor.** As originally written, rule 2 let an exact
entry reference a survivor that near-dup itself then dropped: with the production embedder
at 0.92, an exact-dup pair (A, B) plus an earlier-sorting punctuation variant Z of the same
text (measured cosine(Z, A) = 0.933872) produced `exact(B → kept A)` **and**
`near(A → kept Z)` in one report — A simultaneously a kept-reference and a dropped id — and
the rule-8 validator "no kept among dropped" correctly refused, killing `run_dedup` on
mundane, guaranteed-in-production data (the redaction-twin scenario produces exact-dup pairs
by design). The invariant was right; the entries were wrong. `run_dedup` now remaps every
exact `kept_record_id` through the near-dup drop map before assembling the report; ONE hop
suffices because near survivors are component representatives and are never themselves
dropped (exact groups partition, id-collapse runs first — the only cross-stage hazard is
exact-kept → near-dropped). `content_hash` is untouched: it documents the dropped record's
exact group, which stays true. (b) `DedupOutcome` now cross-checks kept against the report
(kept ∩ dropped-entries = ∅; every entry's `kept_record_id` exists among kept) — the
outcome-side check that would have caught (a), and the one that makes a forged outcome
refuse to deserialize. (c) `calibrate_threshold` refuses degenerate inputs by name (fewer
than two distinct similarity values) instead of an internal `max()` crash, and candidate
midpoints are rounded to report precision and de-duplicated BEFORE scoring — two midpoints
closer than 1e-6 encode the same decision boundary at report precision; metrics are
computed against the rounded threshold itself so every report row is reproducible by
replaying `sim >= threshold` with the printed value. (d) Config knobs now carry the bounds
their downstream contracts enforce (`near_dup_threshold` ∈ [−1, 1], `min_cluster_size` ≥ 2,
`sample_size` ≥ 1) — a bad env value fails at load time naming the knob, never as an opaque
`ValidationError` deep inside a stage. Details in Rules 2, 3, 4 and 8.

## Context

Phase 2 consumes the atom Phase 1 froze: dedup hashes `canonical_text`
(`input ␟ output`), clustering embeds `cluster_text` (`input` only) — decided once in
ADR-0001 rule 1 and exposed as read-only properties on `LogRecord`
(`src/evalgen/contracts/records.py`). Phase 2 must not re-decide those texts; it must
decide everything *about* them: what survives dedup, how near-duplication is defined,
how the traffic distribution is mapped, and which slice of it a sample represents.

The failure modes this phase exists to prevent (CLAUDE.md §1):

1. **Hidden near-duplicates** silently inflate every downstream metric — a golden set
   where the judge grades the same exchange three times under three ids reports a κ
   measured on 1/3 fewer effective cases than it claims.
2. **Coverage gaming** — sampling only the easy head (or silently discarding HDBSCAN
   noise) produces a flattering, useless dataset.
3. **Nondeterminism** — an unseeded sampler or an iteration-order-dependent dedup makes
   two runs disagree; the provenance story (`meta.json`, Phase 5) collapses.
4. **Silent drops** — a record that vanishes between ingest and export without a typed,
   counted trace is the dedup-stage version of ADR-0001's denominator lie.

**Measured ground truth this ADR is built on** (reproducible against the committed
fixtures and the proposed embedder, sklearn 1.8.0):

- The Phase 1 fixtures yield **9 records** (6 tracespan + 3 generic) and already
  contain **exactly one post-redaction exact-dup pair**: fixture lines 4 and 11 of
  `tracespans_demo.jsonl` differ only in their email addresses, which both redact to
  `[REDACTED:email]` — their `canonical_text`s are byte-equal while their `record_id`s
  differ (origin is part of identity, ADR-0001 rule 2). This is the designed
  consequence of constant redaction placeholders: secret-twins *are* the same eval case.
- With `min_cluster_size = 5` (config), 9 records cannot form a single cluster —
  **Phase 2 must ship a larger clustering fixture** or the demo would show an
  all-noise partition.
- Cosine similarities under the proposed hashing embedder
  (`HashingVectorizer`, `char_wb` 3–5-grams, 512 dims, `alternate_sign`, L2 norm):

  | pair | cosine | wanted verdict at 0.92 |
  |---|---|---|
  | one-word edit ("the affected" → "all affected") | 0.9592 | duplicate ✓ |
  | punctuation/politeness variant ("… ?" → "…, svp ?") | 0.9321 | duplicate ✓ |
  | same entity, different question (horaires vs tarifs du péage) | 0.8318 | distinct ✓ |
  | same road, different question (fluide? vs accident?) | 0.4536 | distinct ✓ |
  | same question, different time slot (matin vs soir) | 0.8647 | distinct ✓ (a different eval case) |
  | unrelated cross-language pair | −0.0402 | distinct ✓ |

  The default 0.92 sits inside the measured gap between the hardest distinct pair
  (0.86) and the easiest true duplicate (0.93) — a *sane default region*, not a
  measured threshold. Rule 4 defines how the real value gets measured. Note the
  similarity range is **[−1, 1]**: `alternate_sign` makes negative cosines possible.

## Decision drivers

- Same inputs → byte-identical outputs; seeds from `config.py`; no wall clock, no
  `uuid4`, no unsorted-dict iteration feeding any decision (CLAUDE.md §5).
- Tests 100 % offline: the hashing embedder is the default and the only one CI touches;
  no Anthropic SDK import anywhere in Phase 2 code.
- Nothing dropped in silence: every dedup drop is a typed entry naming the surviving
  representative; reports self-validate their sums like `IngestReport` (ADR-0001 rule 4).
- Thresholds are measured artifacts (CLAUDE.md §5): 0.92 is a starting point; this ADR
  pins the measurement protocol and ships its harness + fixture, even though the final
  number waits for real labeled data.
- Module boundaries: `contracts` imports no evalgen module (pinned by test); dedup runs
  *before* clustering (near-dupes distort densities and quotas); nothing imports `demo`.
- sklearn ≥ 1.3 for HDBSCAN (pyproject already requires it; 1.8.0 installed).

## Options considered

### 1. Exact dedup — which duplicate survives

Key is fixed by ADR-0001: full SHA-256 over `canonical_text` (post-redaction by
construction — `build_record` is the only production constructor). The open question is
the survivor rule.

**A. First-seen in input order.** Pros: trivial. Cons: "input order" is the
concatenation order of loader calls — reorder the source list and the survivor (and
every dedup-report reference) changes. Exactly the "first seen under nondeterministic
ordering" trap. Rejected.

**B. Minimum of a canonical sort key (chosen).** Define once, in `contracts`:
`record_sort_key(r) = (origin.source_name, origin.line_no, record_id)`. The survivor of
any duplicate group is the minimum under this key — a pure function of record *content*
(origin is part of the record), independent of load order, and human-explainable: "the
earliest line of the lexicographically-first source". The same key orders every
downstream artifact, so one rule serves exact dedup, near-dup representatives, and
stable matrix construction.

**C. Earliest timestamp.** Cons: `timestamp` is optional and `None` for every TraceSpan
record (ADR-0001) — the rule would be undefined for the first-class input. Rejected.

**Id-collision pre-step.** Two records can share a `record_id` only via double-ingest
of the same origin+exchange (the id is a pure function of both; `timestamp`/`metadata`
are excluded from it). The sort key cannot break such a tie, and a dedup entry
"dropped rec-X, kept rec-X" would be self-referential nonsense. Decision: collapse
identical `record_id`s *before* exact dedup, counted separately (`id_collapsed`) —
re-ingesting the same file twice is thereby idempotent and visible, and the report
validator "no kept id appears among dropped ids" stays enforceable.

### 2. Near-dup semantics — the transitive-chain trap

A≈B and B≈C at/above threshold while A~C is below it. Three semantics were on the table:

**A. Greedy pairwise against kept representatives.** Iterate in canonical order; drop a
record if it matches any already-kept survivor. Pros: bounded drop radius (C survives
if only B was its bridge). Cons: the *semantics* depend on which record happened to
become representative first — deterministic given the sort, but arbitrary: whether C
lives depends on A < B in the sort key, which has nothing to do with content
similarity. Two near-dupes (B, C) can also both survive if each matched a different
earlier representative. Rejected.

**B. Connected components over the sim ≥ threshold graph — union-find (chosen).** The
duplicate graph is symmetric; its connected components are order-independent and
well-defined regardless of iteration. One survivor per component: the minimum under
`record_sort_key`. Cons — *chain collapse*: a long chain merges A and C even though
sim(A, C) < threshold. Accepted at 0.92 (chains are short when the threshold is high)
**and made visible instead of hidden**: every dropped record's report entry carries its
cosine *to the survivor it was dropped against*, plus `via_chain = (similarity <
threshold)`. A reviewer can count chain-collapsed drops (`near_dropped_via_chain`) and
audit each one — the honest-report answer to the trap, rather than pretending the
transitivity problem doesn't exist.

**C. Agglomerative single-linkage at a distance cutoff.** Mathematically identical to
connected components with more machinery and a fitted estimator to keep deterministic.
Rejected as redundant.

**Boundary rule:** `similarity >= threshold` drops — *inclusive*, because
`config.py::near_dup_threshold` already documents itself as "at/above which two records
are near-duplicates". Comparisons run in full float64; similarities are rounded to 6
decimals only at the report boundary (serialization stability without moving the
decision point). A boundary test pins `>=` with injected unit vectors whose dot product
is exactly the threshold.

**Embedder seam:** near-dup needs embeddings, but the embedder implementation lives in
`cluster/` (CLAUDE.md §3 layout) and dedup must not import against the pipeline flow.
Decision: an `Embedder` **Protocol** + `EmbedderFingerprint` model in
`contracts/embeddings.py` (contracts may import numpy — the boundary rule forbids
evalgen sibling imports, not third-party ones); the `HashingEmbedder` implementation in
`cluster/embeddings.py`; the composition layer (demo, later export) instantiates and
injects it into dedup. Import DAG stays acyclic: `dedup → contracts`,
`cluster → contracts`. The Protocol also buys offline testability for free — near-dup
tests inject a stub embedder returning hand-built vectors, so threshold-boundary and
chain fixtures are exact, not hash-approximate.

**What near-dup embeds:** `canonical_text` (same text exact dedup hashes — ADR-0001's
consumer table row "exact dedup + near-dup"), *not* `cluster_text`. Same input with a
meaningfully different output must be able to survive as a distinct eval case.

### 3. The threshold-measurement protocol

**A. Keep tuning by eye.** Rejected — that is precisely the "0.9 because it felt right"
CLAUDE.md forbids.

**B. Unsupervised knee-finding on the corpus similarity histogram.** Rejected: no
ground truth, unfalsifiable, and the knee moves with every corpus — a threshold that
cannot be wrong cannot be right.

**C. Supervised sweep over labeled pairs (chosen).** A committed fixture
`data/fixtures/neardup_pairs.jsonl` of pairs hand-labeled `duplicate` / `distinct`
(≥ 15 of each, including *hard negatives* — same entity/different question, same
question/different time slot, per the measured table above — and easy positives:
punctuation variants, one-word edits, redaction twins). Protocol, implemented in
`dedup/calibrate.py` and runnable offline:

1. Embed every pair side with the **production embedder configuration** (fingerprint
   recorded in the output — a threshold is only valid for the embedder it was measured
   with).
2. Compute the cosine for each pair.
3. Candidate thresholds = midpoints between adjacent distinct values in the sorted
   similarity list (the standard ROC sweep — no arbitrary grid).
4. For each candidate: precision/recall/F1 on the `duplicate` class.
5. Chosen threshold = the candidate maximizing F1; **ties break to the higher
   threshold** (prefer keeping data — a false drop destroys a real record, a false keep
   is caught by the next audit of the report).
6. Emit a self-validating `ThresholdCalibrationReport` (pair counts, embedder
   fingerprint, full candidate table, chosen threshold; the validator recomputes that
   the chosen value is the F1-argmax under the tie rule — a report claiming a
   non-optimal choice refuses to exist).

**Update path (the part that makes 0.92 honest):** the config default stays 0.92 until
real labeled pairs from production data exist; running the same protocol on them
produces the measured value, which replaces the default in one reviewed commit citing
its calibration report. `meta.json` (Phase 5) always records the *active* threshold and
embedder fingerprint, so every export names the number it was built with. Phase 2 ships
the protocol, the harness, the fixture, and tests that pin the sweep's arithmetic on a
hand-computed mini-set — the measurement machinery is proven even though the final
number waits for real data.

### 4. Clustering — metric, noise, and cluster identity

**Metric.** HDBSCAN needs a distance. (A) `metric="cosine"` forces brute-force
neighbor search in sklearn (cosine is not a KD/ball-tree metric). (B) **Euclidean on
L2-normalized vectors (chosen):** for unit vectors, d² = 2 − 2·cos — a strictly
monotone bijection, so density orderings match cosine's while staying tree-accelerated.
The embedder already L2-normalizes (required by the `Embedder` Protocol contract), so
this is free. (C) Precomputed cosine distance matrix: O(n²) memory locked in at the
API. Rejected.

**Determinism.** sklearn's HDBSCAN has no `random_state` — it is algorithmically
deterministic *given the same input matrix*. The input matrix is made canonical by
sorting records with `record_sort_key` before embedding; a shuffle test pins that a
permuted input list yields the identical report. Records numbering fewer than
`min_cluster_size` skip HDBSCAN entirely (all-noise partition) — the guard is explicit,
not an sklearn error surface.

**Noise (label −1).** Options: exclude with a note (coverage gaming with paperwork);
force-assign to nearest cluster (fabricates memberships the algorithm refused to
claim); **own stratum, first-class (chosen)** — noise records appear in the clustering
report (`noise_record_ids`, summed by the validator: cluster sizes + noise =
records in) and are sampled like any other stratum. Noise is the tail of the real
distribution; the whole point of stratified coverage is that the tail survives.

**Cluster identity.** Raw HDBSCAN integer labels are an implementation artifact —
nothing guarantees label numbering is stable across library versions, and they collide
across runs on different inputs. Chosen: content-derived ids in the house style,
`cluster_id = "cl-" + sha256("␟".join(sorted(member record_ids)))[:12]` — stable across
runs, meaningful in diffs, and self-verifying (the `Cluster` model validator recomputes
it). The noise stratum uses the reserved id `"noise"`.

### 5. Stratified sampling — quotas and the seed

**Allocation.** (A) Equal per stratum: over-samples the tail, misrepresents traffic.
(B) Pure proportional with floor rounding: naive `floor(k·sᵢ/N)` zeroes out minority
clusters — exactly the tail stratification exists to protect. (C) **Proportional with
a floor of 1 and largest-remainder (Hamilton) allocation, in integer arithmetic
(chosen):**

1. `k = min(sample_size, records_in)`; strata (clusters + noise) sorted by
   `(size desc, cluster_id asc)`.
2. If `k ≤ m` (budget below stratum count — floor-1 unsatisfiable): the first `k`
   strata in sort order get quota 1, the rest 0. The report shows the zeros.
3. Else: every stratum gets 1; the remainder `r = k − m` is split over capacities
   `wᵢ = sᵢ − 1` as `quotaᵢ = 1 + (r·wᵢ)//W` with `W = Σwᵢ`, and the leftover units go
   one each to strata ranked by `((r·wᵢ) mod W desc, sᵢ desc, cluster_id asc)`,
   skipping strata already at capacity. All integer arithmetic — no float remainder can
   tie-wobble across platforms; `quotaᵢ ≤ sᵢ` holds by construction (`r ≤ W`).

**Within-stratum selection.** (A) `random.Random(seed).sample` per stratum: workable
but stateful — correctness depends on member ordering and per-stratum seed derivation
done right everywhere. (B) **Seeded hash ranking (chosen):** score every member as
`sha256(f"{seed}␟{record_id}")` and take the `quota` smallest scores. Stateless, no RNG
object to misuse, invariant to member iteration order, ties impossible (ids are
unique), and changing the seed re-rolls the whole sample deterministically. The seed is
`Settings.seed` (1750) and is recorded in the sampling report.

### 6. The demo — CLI shape and proof of determinism

(A) A full `python -m evalgen` umbrella CLI now: premature — the CLI belongs to
`pipeline-engineer` in Phase 5 with export wiring. (B) **`python -m evalgen.demo`
(chosen):** one module, zero arguments (arguments are variance; the demo's job is to be
identical every time), fixed fixture list, prints one deterministic text report to
stdout, exit 0. `make demo` runs exactly that. Phase 5 may fold it into the umbrella
CLI without touching its internals. (C) Demo writes JSON artifacts to `data/out/`:
deferred to Phase 5 where provenance (`meta.json`) gives artifacts meaning; a demo that
writes files invites treating demo output as data.

**Determinism is *proven*, not claimed:** the rendered report is committed as a golden
file (`tests/golden/demo_output.txt`, LF-forced by `.gitattributes`) and a test asserts
byte-equality; a second test runs the pipeline twice and compares. `/repro-audit` gets
its byte-diff reference for free.

## Decision

### Rule 1 — One canonical order for everything

`contracts/records.py` gains `record_sort_key(record) -> (source_name, line_no,
record_id)`. Every Phase 2 stage sorts by it before deciding anything: dedup survivor
choice, near-dup representative choice, embedding-matrix row order, report entry order.
Input list order is *never* load-bearing (pinned by shuffle tests).

### Rule 2 — Exact dedup: id-collapse, then content-hash groups

`dedup/exact.py`: collapse duplicate `record_id`s (count `id_collapsed`), then group by
full SHA-256 hex over `canonical_text`; each group's survivor is the `record_sort_key`
minimum; every other member becomes an `ExactDupEntry(dropped_record_id,
kept_record_id, content_hash)`. The content hash is publishable — it hashes
post-redaction text only (ADR-0001 guarantees no other kind exists).

**Amendment (red team):** in the assembled report, exact entries name the **final**
survivor — if an exact survivor is itself near-dropped, `run_dedup` remaps the entry's
`kept_record_id` to the near-dup survivor (one hop suffices; near survivors are never
themselves dropped). `content_hash` keeps documenting the dropped record's exact group.

### Rule 3 — Near-dup: union-find components, flagged chains, `>=` at the boundary

`dedup/neardup.py`, on exact-dedup survivors: embed `canonical_text` via the injected
`Embedder`; build the sim ≥ threshold graph in float64; union-find components; survivor
per component = `record_sort_key` minimum; every dropped member becomes a
`NearDupEntry(dropped_record_id, kept_record_id, similarity, via_chain)` where
`similarity` is its cosine *to the survivor* (rounded to 6 decimals at the report) and
`via_chain = similarity < threshold`. The public seam is
`dedup.run_dedup(records, embedder=…, threshold=…) -> DedupOutcome` — kept records in
canonical order plus a self-validating `DedupReport` (invariants in Rule 8), with exact
entries remapped to the final survivor before assembly (Rule 2 amendment).

### Rule 4 — The threshold is a measured artifact

`dedup/calibrate.py` implements the Options §3 protocol over
`data/fixtures/neardup_pairs.jsonl` (schema: `pair_id`, `text_a`, `text_b`,
`label ∈ {duplicate, distinct}`), producing a `ThresholdCalibrationReport` whose
validator recomputes the F1-argmax. Runnable as `python -m evalgen.dedup.calibrate`
(offline). `near_dup_threshold` stays 0.92 until the protocol runs on real labeled
pairs; the replacing commit must cite its calibration report.

**Amendment (red team):** the sweep refuses (with an error naming the precondition)
when the pairs yield fewer than two distinct similarity values, and candidate midpoints
are rounded to report precision and de-duplicated *before* scoring — near-identical
cosines (plausible on real labeled pairs: verbatim + whitespace twins both at 1.0−ε)
no longer produce two candidates that collide after rounding. Metrics are computed
against the rounded threshold itself, so each report row replays exactly as printed.

### Rule 5 — The embedding seam

`contracts/embeddings.py`: `EmbedderFingerprint` (frozen: name, dim, analyzer,
ngram range) and the `Embedder` Protocol — `embed(texts) -> np.ndarray` of shape
`(n, dim)`, dtype float64, **rows L2-normalized** (that contract is what makes
cosine-as-dot and euclidean-on-unit-sphere both valid). `cluster/embeddings.py`:
`HashingEmbedder(dim)` wrapping `HashingVectorizer(n_features=dim, analyzer="char_wb",
ngram_range=(3, 5), norm="l2", alternate_sign=True, dtype=float64)` — deterministic
across instances and runs (fixed MurmurHash3 seed; verified), fit-free, offline,
dim from `Settings.hash_embedding_dim`. The analyzer/ngram choice is part of the
embedder's *identity* (reported in every fingerprint), not a config knob — changing it
is a code change that shows up in provenance, not an env var that silently moves every
threshold. Real embedding backends plug in behind the same Protocol later; their
fingerprint travels through every report the same way.

### Rule 6 — Clustering: HDBSCAN on unit vectors, noise first-class, content-derived ids

`cluster/clustering.py::cluster_records(records, embedder=…, min_cluster_size=…) ->
ClusteringReport`: sort by `record_sort_key`, embed `cluster_text`, run
`sklearn.cluster.HDBSCAN(min_cluster_size=…, metric="euclidean")` on the dense matrix;
fewer records than `min_cluster_size` skips HDBSCAN (all noise). Clusters carry
content-derived ids (`derive_cluster_id`, Rule 8 models); noise is `noise_record_ids`,
never dropped. `min_cluster_size` from config; other HDBSCAN params stay library
defaults until a measurement motivates exposing them (a config knob nobody measured is
a lie waiting to be recorded in meta.json).

### Rule 7 — Sampling: floor-1 largest-remainder quotas, seeded hash ranking

`cluster/sampling.py::stratified_sample(clustering, sample_size=…, seed=…) ->
SamplingReport` implementing Options §5 exactly: strata = clusters + noise; integer
largest-remainder quotas with floor 1 (or the k-largest-strata rule when the budget is
below the stratum count); member selection by ascending `sha256(f"{seed}␟{record_id}")`;
sampled ids listed sorted. New config knob: `sample_size: int = 50` (recorded in
meta.json by Phase 5 like every other knob).

### Rule 8 — Self-validating contracts (the IngestReport discipline, extended)

New contracts modules (all frozen, all refusing to exist when inconsistent — the
validators run on deserialization too, same tamper-evidence as `LogRecord`):

- `contracts/dedup.py` — `ExactDupEntry`, `NearDupEntry`, `DedupReport`
  (`records_in == records_out + id_collapsed + exact_dropped + near_dropped`; entry
  tuple lengths match their counters; `near_dropped_via_chain` equals the count of
  flagged entries; `via_chain == (similarity < threshold)` per entry; no `kept_id`
  appears among dropped ids; entries sorted by `dropped_record_id`), `DedupOutcome`
  (kept records sorted by `record_sort_key`, `len(kept) == report.records_out`;
  **amendment**: kept cross-checked against the report — no kept record among the
  dropped entries, every entry's `kept_record_id` present among kept — so a forged
  outcome refuses to exist on deserialization too).
- `contracts/clustering.py` — `derive_cluster_id`, `NOISE_CLUSTER_ID = "noise"`,
  `Cluster` (sorted unique members; id recomputed by validator), `ClusteringReport`
  (clusters sorted `(size desc, cluster_id asc)`; members + noise disjoint; sizes sum
  to `records_in`; embedder fingerprint + `min_cluster_size` + metric recorded),
  `StratumSample` (`quota == len(sampled) ≤ stratum_size`), `SamplingReport`
  (`total_sampled == Σ quotas == min(requested, records_in)`; strata sizes sum to
  `records_in`; sampled ids globally unique; seed recorded).
- `contracts/calibration.py` — `LabeledPair`, `ThresholdCandidate`,
  `ThresholdCalibrationReport` (chosen threshold is the F1-argmax with
  highest-threshold tie-break — recomputed by the validator).

### Rule 9 — Demo: zero-arg, zero-network, byte-identical

`src/evalgen/demo.py` (composition layer; imports ingest/dedup/cluster; nothing imports
it): ingest the fixed fixture list (`generic_demo.jsonl` with its explicit
`GenericMapping`, `tracespans_demo.jsonl`, `cluster_demo.jsonl`) → `run_dedup` →
`cluster_records` → `stratified_sample`, all knobs from `get_settings()`; render one
deterministic text report (per-source ingest accounting, dedup drops with similarities
and chain flags, clusters with sizes and a truncated preview, per-stratum quotas and
sampled ids; no timestamps, no absolute paths, post-redaction text only) and print it.
`make demo` = `python -m evalgen.demo`. Golden-file byte-equality is the acceptance
test. **New fixture** `data/fixtures/cluster_demo.jsonl` (generic shape, reusing the
demo mapping): ~50 records in 4 intent groups of ≥ 8 (sized against
`min_cluster_size=5`), plus planted exact dups, near-dup pairs built from the measured
similarity table (one-word edits ≈ 0.96, punctuation variants ≈ 0.93), hard-negative
pairs (≈ 0.83 — must survive), outlier singletons for noise, and planted secrets
(proving redaction flows through the demo output).

## Consequences

**Positive:** every drop is a typed entry naming its survivor and its similarity —
chain collapses included, flagged, countable; survivor choice and every ordering are
content-derived (no load-order dependence anywhere); the threshold has a measurement
protocol whose report cannot claim a non-optimal choice; noise cannot be silently
discarded (sum validators); cluster ids are stable across runs and library versions;
sampling is stateless-deterministic with a recorded seed; the demo is a committed
byte-exact artifact that `/repro-audit` can diff; the embedder seam makes near-dup
tests exact (injected vectors) and real embedding backends a drop-in with provenance.

**Negative (accepted):**

- **O(n²) similarity** (dense n×n in float64) and full drop-entry lists in reports:
  fine at demo scale (≤ ~50k records ≈ 20 GB would *not* be fine — blocking/
  ANN is the documented revisit trigger, and report size is proportional to drops,
  which is the point: provenance over compactness).
- **Chain collapse is possible** under union-find; mitigated by visibility
  (`via_chain`), not prevented. At 0.92 chains are short; if `near_dropped_via_chain`
  grows on real data, that is the signal to revisit (e.g. component-diameter cap).
- **The hashing embedder is lexical, not semantic:** a true paraphrase with different
  wording escapes near-dup (0.86 for a one-word semantic change shows how close the
  regimes are). Stated honestly: char-n-gram near-dup catches *near-verbatim*
  duplication — the kind that actually plagues log corpora. Semantic dedup needs a real
  embedding backend behind the same Protocol, with its own calibration run (Rule 4
  makes the threshold embedder-specific by construction).
- **Similarities rounded to 6 decimals in reports** — decisions use full precision;
  the rounding exists so serialized reports are stable, at the cost of not being able
  to reconstruct exact float comparisons from a report alone.
- **The demo fixture is synthetic** — its clusters demonstrate the machinery, not real
  traffic. The README must not present demo cluster counts as findings.

**Explicitly deferred:** ANN/blocking for near-dup at scale; real embedding backends
(OpenAI/local sentence-transformers) behind the Protocol; exposing further HDBSCAN
params (`min_samples`, `cluster_selection_epsilon`) pending a measured need; demo
artifact files (Phase 5 export owns files + `meta.json`); threshold measurement on real
labeled pairs (protocol ships now, number lands with real data).

**Validated by (Phase 2 test battery):** exact-dup survivor rule + fixture-twin
collapse (the measured lines-4/11 pair); id-collapse idempotence (same file ingested
twice); near-dup boundary at exactly the threshold (`>=`, injected vectors); the
transitive-chain fixture (A~B~C, A≁C → one survivor, C's entry `via_chain=True`);
the red-team BLOCKER payload replayed verbatim (exact-dup pair + earlier-sorting
near-dup of the survivor, production embedder at 0.92 → exact entries name the final
survivor, input-order independent); the two forged-outcome refuse cases (kept record
also reported dropped; ghost survivor reference); calibration degenerate inputs (all
sims identical → named error; sims closer than report precision → merged candidates);
out-of-range config knobs refusing at load time by name;
input-shuffle invariance of dedup, clustering, and sampling reports; calibration sweep
arithmetic on a hand-computed mini-set; hashing-embedder determinism, unit norms, and
dim; all-noise small-corpus guard; skewed-quota allocation (floor-1, largest remainder,
budget < stratum count); every new report's refuses-to-validate cases; module-boundary
greps (dedup imports no evalgen module but contracts; cluster likewise; contracts still
imports no sibling); demo golden byte-equality + double-run identity + no secret/
absolute-path leak in output.
