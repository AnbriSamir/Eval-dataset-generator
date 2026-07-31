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
                                                                         [Phase 3 ✔]
  → validate/   human subset · Cohen's κ (global + per-class) · bootstrap CI95
                                                                         [Phase 4 ✔]
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

## Label — taxonomy + LLM judge (Phase 3 — implemented, [ADR-0003](decisions/ADR-0003-label-taxonomy-judge.md))

```
contracts/taxonomy.py     TaskTypeLabel / OutcomeLabel / JudgeConfidence StrEnums ·
                          LabelTaxonomy (content-derived self-verified taxonomy_id) ·
                          TAXONOMY_V1 — ONE questionnaire for judge and human labeler
contracts/labeling.py     JudgeVerdict — IS the output_format schema (closed enums: an
                          out-of-taxonomy label is unrepresentable at the API) ·
                          Judgment (verdict + model ACTUALLY served) · Judge Protocol
                          (two strings in, a Judgment out — blindness by signature) ·
                          JudgeFingerprint (requested model, taxonomy id, prompt sha256,
                          few-shot ids + content hashes) · FewShotExample (self-verified
                          id; content_hash = the exact-dedup identity over input ␟ output)
                          · TextSanitizer Protocol (the loader's injected redaction seam)
                          · five-bucket LabelingReport + LabelingOutcome (self-validating:
                          labeled + refused + failed + skipped_budget +
                          skipped_fewshot_collision == records_in; budget never exceeded;
                          buckets pairwise disjoint; forged outcomes refuse to exist)
label/errors.py           JudgeError hierarchy: JudgeRefusalError / JudgeParseError /
                          JudgeAPIError — anything else PROPAGATES (our own bugs are
                          never laundered into labeling statistics)
label/prompt.py           pure render_system_prompt / render_user_message + prompt_sha256
                          (system prompt ␟ user template — any drift breaks the golden) ·
                          system prompt demarcates INPUT/OUTPUT as data-not-instructions
                          (red-team F-2 hardening; closed enums bound the blast radius)
label/fewshots.py         load_few_shots(path, *, sanitizer) — validated, sorted,
                          tamper-evident AND redaction-clean: refuses any example whose
                          string fields the injected production sanitizer would rewrite
                          (red-team F-1: a raw few-shot would ship its secret to the API
                          and evade the collision/export gates, which hash REDACTED text)
label/fake.py             FakeJudge — sha256-derived verdicts, platform-stable, never
                          raises; same render/hash as the real judge, so the offline
                          golden pins the production prompt byte-for-byte
label/anthropic_judge.py  AnthropicJudge — the ONLY module importing anthropic, NOT
                          exported from label/__init__ · messages.parse(output_format=
                          JudgeVerdict) → .parsed_output · adaptive thinking ·
                          output_config effort=high · no temperature/top_p/top_k, no
                          budget_tokens · stop_reason checked BEFORE parsed_output ·
                          model_id from response.model (served, not requested) · pure
                          response/exception mappers tested offline, class never
                          instantiated in tests
label/engine.py           run_labeling(records, judge=…, max_labels=…) — canonical
                          record_sort_key order · collision gate first (consumes no
                          budget) · budget cut EXACT (labeled+refused+failed==max) ·
                          typed failures → counted entries · non-JudgeError propagates
demo.py                   [5/5] label — FakeJudge over the sampled records; fingerprint
                          line, five-bucket accounting, planted collision named, synthetic
                          distributions marked; production sanitizer injected to the
                          few-shot loader at this composition layer
```

Invariants the tests pin: enum↔`TAXONOMY_V1` mirror; tampered taxonomy/few-shot/report/
outcome refuse to validate; FakeJudge determinism with pinned verdicts; five-bucket sums
under every mix (refusals, failures, budget cut, collisions — stub judges raising each
typed error); budget boundary shuffle-invariant, collisions consume no budget; a planted
`AttributeError` propagates; SDK call structure recorded (including the ABSENCE of
temperature/top_p/top_k/budget_tokens); `stop_reason` precedence; the red-team F-1
payload replayed verbatim (raw-vs-redacted hash miss proven, redactable few-shot refuses
to load, secret never echoed); committed store sanitize-neutral under the production
sanitizer; injection payload lands as data under the demarcating prompt; blindness by
AST walk at every import depth (F-3) + path-literal grep + no-`AnthropicJudge`-export;
demo golden byte-equality + double-run identity with `fewshot_collisions=1` named.

## Validate — the agreement protocol (Phase 4 — implemented, [ADR-0004](decisions/ADR-0004-agreement-kappa-protocol.md))

```
contracts/agreement.py    kappa_from_confusion — THE κ formula (exact integer numerator/
                          denominator, ONE float division; degeneracy p_e=1 ⟺ S=n² tested in
                          integers, typed None never NaN/0) · binarize_confusion (per-class κ
                          IS global κ on the 2×2 collapse — one formula total) · HumanLabel
                          (extra="ignore": edited display copies cannot alter κ; no
                          content-derived id — hook + git + sha256 binding are the tamper
                          story) · KappaStatus typed degeneracy vocabulary · BootstrapCI
                          (bounds None ⟺ ALL resamples degenerate, always counted) ·
                          KappaValue (status ok ⟺ values present) · AxisAgreement —
                          self-validating: recomputes p_o/p_e/κ (global AND per-class),
                          supports, statuses and per-cell disagreement multiplicities from
                          its own confusion on every construction · MatchAccounting (join
                          sums enforced; orphan sets sorted, unique, disjoint) ·
                          AgreementReport — headline_ready cannot lie, every CI's b_total ==
                          B, taxonomy id pinned to the fingerprint, ONE report-level
                          min_class_support every axis must match, human_labels_sha256 binds
                          the κ to the exact ground-truth bytes (red-team M-1/M-2 closures)
validate/errors.py        typed refusals: TaxonomyMismatch, DuplicateHumanLabel,
                          HumanLabelFormat (names the 1-based line), NoMatchedPairs
validate/human_labels.py  STRICT loader (the deliberate opposite of ingest's tolerance):
                          any invalid/unfilled line, duplicate record_id, mixed taxonomy_id
                          or empty file refuses the whole file naming the line
validate/annotation.py    render_label_template(records, taxonomy) + annotator instructions —
                          judgments unrepresentable by signature (blindness by type; the
                          mirror of the two-string Judge Protocol)
validate/kappa.py         confusion_matrix over label pairs (every class keeps its row/col
                          at zero support) · landis_koch_band (reading aid, nothing gates)
validate/bootstrap.py     draw_index_matrix — ONE Generator(PCG64(seed)), ONE integers call,
                          function of (seed, B, n) only · bootstrap_kappa — paired resampling
                          of joint codes via bincount, degenerate resamples excluded AND
                          counted, percentile method pinned BY NAME ("linear")
validate/agreement.py     compute_agreement — taxonomy guard → duplicate guard → join on
                          record_id (matched sorted ascending = THE canonical bootstrap pair
                          order) → human-only orphans classified by cause from the labeling
                          report's buckets (refusals printed as coverage loss) → ONE index
                          matrix shared by every metric → frozen self-verifying report;
                          knobs injected, never imported
validate/render.py        deterministic text report — κ never travels naked (n, po/pe, CI95,
                          B, degenerate count, band, per-class supports ride every line);
                          header prints source, sha256 binding (or "unrecorded"), bootstrap
                          knobs and the gates line
agreement_demo.py         make agreement → re-runs the fixture pipeline + joins the committed
                          SYNTHETIC annotation fixture; mandatory !! SYNTHETIC banner first;
                          byte-pinned by tests/golden/agreement_output.txt (demo.py and its
                          golden untouched this phase)
```

Invariants the tests pin: every formula on hand-computed fixtures (κ = 16/31 global with
per-class 0.6 / 11/21 / 0.375 / absent; perfect 1; chance 0; perfect disagreement −1;
monoclass judge → exactly 0; one-side-absent class → exactly 0; p_e = 1 → typed undefined)
+ sklearn as independent oracle (global and binarized per-class); the hand-built index
matrix CI ([0.0, 0.9625], b_degenerate = 1, computed on paper — the linear-method pin);
pinned first bootstrap row for seed 1750 against numpy drift; global np.random untouched;
forged reports refuse (flattered κ at 1 ulp, wrong supports/statuses, filtered drill-down,
headline_ready lie both directions, diverging support gate both directions, non-hexdigest
binding); the red-team M-1 payload replayed through files (selective filtering lifts κ
0.516129 → 1.0 but changes the sha256 binding on the report's face); join losses classified
(refused/collision/not_in_run) with sums enforced; min_human_labels boundary 29 vs 30;
support-gate boundary; loader strictness battery; template renderers cannot receive
judgments; validate imports only contracts (AST at every depth) and writes nothing (grep);
agreement golden byte-equality + double-run identity + banner-first + no leak.

## Module boundaries (enforced from day 0)

- `contracts` is imported by everyone and imports no one (pinned by a test).
- Redaction lives in `ingest`; nothing downstream ever sees raw secrets.
- `dedup` and `cluster` import only `contracts`: the embedder is INJECTED through the
  `Embedder` Protocol, so dedup never imports against the pipeline flow (pinned by
  module-top-import greps; `calibrate.main()`'s inner import is the one sanctioned
  composition-layer exception).
- `demo` is composition: it imports the pipeline, nothing imports it (pinned).
- `label` imports only `contracts` and never reads the human labels (the judge stays
  blind to ground truth) — pinned by AST-walk import tests at every nesting depth and
  a path-literal grep; the two-string `Judge` Protocol is the type-level guarantee.
  The few-shot loader's redaction check arrives INJECTED through the `TextSanitizer`
  Protocol (composition passes `ingest.sanitize_text`), so `label` never imports
  `ingest` either.
- `validate` imports only `contracts` (+ numpy) — AST-walked at every depth, both
  directions of blindness pinned (`label` never sees human labels; `validate` never
  reaches `label` internals — it consumes `LabelingOutcome` through contracts). It is
  the ONE module allowed to see both raters and it only measures: no write capability
  (grep-pinned), knobs injected by the composition layer, never imported from config.
  `agreement_demo` is composition: imports the pipeline, nothing imports it.
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
- [ADR-0003 — Label taxonomy, the LLM judge seam, typed labeling failures, and the
  few-shot leakage gate](decisions/ADR-0003-label-taxonomy-judge.md) — Phase 3
  (implemented; amended after the pre-commit red-team pass): two closed unconditional
  axes (`task_type`, `outcome`) sized for per-class κ at n = 30–50, enums as the
  structured-output schema, the narrow `Judge` Protocol (two strings in, a `Judgment`
  out — blindness by signature), `AnthropicJudge` (SDK §2 rules, thin shell, pure
  mappers) vs deterministic content-derived `FakeJudge`, the five-bucket
  self-validating `LabelingReport` (labeled + refused + failed + skipped_budget +
  skipped_fewshot_collision == records_in) with the `max_labels_per_run` guard
  enforced in the engine, and the few-shot store gated on the exact-dedup content
  hash (labeling-time skip now, export ∩ few-shots = ∅ at Phase 5). Amendments:
  redaction-clean few-shot loading via the injected `TextSanitizer` Protocol (a
  redactable few-shot refuses to load — closes the raw-vs-redacted hash asymmetry),
  the data-not-instructions prompt demarcation (second sanctioned golden
  regeneration: `prompt=` prefix only), and AST-walk blindness tests.
- [ADR-0004 — The agreement protocol: human labels, Cohen's κ (global + per-class),
  paired bootstrap CI95, and the honest
  report](decisions/ADR-0004-agreement-kappa-protocol.md) — Phase 4 (implemented;
  amended after the pre-commit red-team pass): the filled-template human-label
  workflow with structural double-blindness (judgment-free renderer signatures
  mirroring the two-string `Judge` Protocol), unweighted Cohen's κ per axis with
  one-vs-rest per-class κ through ONE hand-checked formula, typed degeneracy
  statuses (never NaN, never a silent 0), the support gate
  (`min_class_support = 5`) as suppression-with-status, the paired seeded
  percentile bootstrap (degenerate resamples excluded-and-counted, method pinned
  by name), the classified join ledger, the self-validating `AgreementReport`,
  and the byte-pinned SYNTHETIC `make agreement`. Amendments (2026-08-01):
  report-level `human_labels_sha256` ground-truth binding (M-1), report-level
  `min_class_support` + the header gates line (M-2), the empty-input bootstrap
  refusal (N-1), and the Phase 5 rule that a headline with status ≠ `ok` blocks
  export exactly like `headline_ready = False`.
