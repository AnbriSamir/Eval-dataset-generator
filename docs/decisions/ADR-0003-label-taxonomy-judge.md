# ADR-0003 — Label taxonomy, the LLM judge seam, typed labeling failures, and the few-shot leakage gate

**Status:** Accepted (2026-07-31) — amended same day after the pre-commit red-team pass:
(a) **the few-shot store is redaction-clean by construction, not by review** (red-team
F-1, MAJOR latent). `FewShotExample.content_hash` hashes the store's RAW text while the
labeling-time collision gate and the Phase 5 export gate hash the record's REDACTED
`canonical_text` (records are redacted at ingestion, ADR-0001; few-shots enter through
their own door). A future few-shot carrying a secret/PII would therefore (i) ship its
secret verbatim in the system prompt to the external judge API — bypassing the ADR-0001
boundary — and (ii) evade both content-hash gates, because `hash(raw) != hash(redacted)`
(proven by the red team with the fixture's planted email). `load_few_shots` now REQUIRES
an injected `TextSanitizer` (a Protocol in `contracts/labeling.py`, satisfied by
`ingest.redaction.sanitize_text` — the `Embedder`/`Judge` seam pattern, so `label/`
still never imports `ingest`) and refuses to load any example whose string fields
(`input_text`, `output_text`, `verdict.rationale`, `note`) the sanitizer would rewrite;
the refusal message echoes only the SANITIZED form (an exception is a leak channel too).
Refuse-to-load was chosen over the alternative fix (hashing few-shots post-redaction)
because the latter would repair only the gate half while still sending the raw secret to
the API — the leaky path must not exist (ADR-0001 rule 3 philosophy). The no-op check
also forces the store into the same normalized text space (NFKC, invisible-strip,
C0-strip) as record `canonical_text`, so the gate identity holds byte-for-byte.
(b) **the system prompt demarcates INPUT/OUTPUT as data-not-instructions** (red-team
F-2, MINOR — discretionary hardening): a mined record can contain directive text
("Ignore all previous instructions…"); the blast radius was already bounded by the
closed-enum `output_format` schema (an out-of-taxonomy label is unrepresentable —
proven), so this is defense-in-depth, not a defect fix. Delimiter fencing of the user
template was considered and skipped (it would diverge from the few-shot block rendering
for marginal benefit). The change moves `prompt_sha256` → second sanctioned golden
regeneration, reviewed under the same diff rule: the ONLY changed byte is the `[5/5]`
`prompt=` prefix (verified line-by-line).
(c) **the blindness greps became AST walks** (red-team F-3, informational): the rule 9
import/SDK-confinement tests now parse each `label/` file and check every `Import`/
`ImportFrom` node at EVERY nesting depth — a function-level `import evalgen.validate`
or a formatting trick can no longer slip past a column-0 grep. Stated residual:
dynamically-constructed import strings are beyond any static check; the real guarantee
remains the two-string `Judge` Protocol, which cannot transport a human label (rule 3).
Details in Rules 8 and 9.

## Context

Phase 3 builds the instrument whose calibration is the repo's headline number: an LLM
judge auto-labels the mined exchanges, and Phase 4 measures its agreement against a
human-labeled subset with Cohen's κ (global + per-class) + bootstrap CI95. Whatever
this phase gets wrong is not a bug — it is a *measurement artifact* that ships on the
dataset's face. Four failure modes dominate LLM-judge pipelines and must be prevented
structurally, in the same spirit as ADR-0001's redaction and ADR-0002's dedup honesty:

1. **A taxonomy κ cannot defend.** Per-class κ needs per-class support. With
   `min_human_labels = 30` (config) and the demo-scale sample of 50, a 4-class axis has
   an expected balanced support of ~7–8 per class — already thin, and real traffic is
   skewed. Every additional class, and especially every *conditional* axis (a
   `failure_mode` that only applies when the outcome is bad), divides that support
   further until per-class κ is a coin flip with a CI95 spanning the whole scale. A
   taxonomy designed for expressiveness instead of measurability produces a κ table
   full of unreportable cells — the opposite of the headline signal.
2. **The judge seeing what it must not.** A judge that can read human labels (or any
   channel that could carry them) is measuring prompt-echo, not agreement. Promising
   blindness is worthless; the *signatures and import graph* must make the leak
   unrepresentable (CLAUDE.md §3: `label` never reads `validate`'s human labels).
3. **Silently swallowed records.** A refusal or parse failure that vanishes makes the
   labeled set's denominator a lie (ADR-0001 failure mode 4, judge edition) — and
   worse, it biases κ: refusals correlate with hard cases, and dropping them silently
   is unintentional cherry-picking.
4. **Few-shots contaminating the product.** A few-shot example that reaches the export
   is a leaked answer key; a few-shot that matches a record *being labeled* means the
   judge was handed the verdict for that very item — κ on it measures memory, not
   judgment. Both directions must be gated on content, not on good intentions.

What exists and is consumed here (re-read, not assumed): `LogRecord` with
`input_text`/`output_text` and the ADR-0001 rule-1 consumer table — "LLM judge:
`input_text` and `output_text`, presented separately"; `record_sort_key` as THE
canonical order (ADR-0002 rule 1); the exact-dedup content hash = full SHA-256 over
`canonical_text` = `input ␟ output` (ADR-0002 rule 2) — the content-identity key this
ADR reuses for the leakage gate; the `Embedder` Protocol + fingerprint precedent
(injection at the composition layer, identity travels in every report — ADR-0002 rule
5); the self-validating report discipline (`IngestReport`, `DedupReport`); config knobs
`judge_model = "claude-opus-4-8"`, `judge_model_fast = "claude-sonnet-4-6"`,
`max_labels_per_run = 500`, `min_human_labels = 30`; and the SDK binding rules
(CLAUDE.md §2: `client.messages.parse(output_format=<Model>) → .parsed_output`,
`thinking={"type": "adaptive"}`, `output_config={"effort": "high"}`, never
`temperature`/`top_p`/`top_k`, never `budget_tokens`, never raw `requests`/`httpx`).

The corpus this taxonomy must fit (committed fixtures + the sibling repo's spans):
French autoroute-traffic user exchanges (toll opening hours, live traffic, télépéage
badge activation, data-export incidents) and orchestrator `plan`/`execute`/`verdict`
spans — question-answering, how-to, troubleshooting, and reasoning/planning content.

## Decision drivers

- Per-class κ must be *reportable* at n = 30–50 human labels (CLAUDE.md §5: per-class
  when class support allows) — taxonomy size is a statistics decision, not an ontology
  exercise.
- The judge and the human labeler must answer **the same questions with the same class
  definitions** — otherwise κ measures instruction drift, not agreement. One shared,
  versioned, content-addressed taxonomy artifact.
- Structured output only, schema-enforced at the API — never free text, never regex
  over prose (CLAUDE.md §2).
- Every label stores the model id **actually used** (from the API response envelope,
  not the config echo).
- Refusals / parse failures / API errors are typed, counted, and reported; the sum
  invariant is enforced by a validator that refuses to construct a lying report.
- Offline path fully deterministic: `make demo` stays byte-identical between runs; the
  fake judge derives its verdict from record content (same record → same label).
- Blindness and leakage guards are structural (imports, signatures, content hashes),
  not procedural.

## Options considered

### 1. Taxonomy shape

**A. Flat single axis** (one `quality` enum). Pros: maximum support per class, one κ.
Cons: an exported golden set with only "correct/incorrect" is a thin product — no way
to slice by what kind of exchange was judged, and the coverage story (clusters map
*intents*) has no labeled counterpart.

**B. Three axes — task_type × outcome × failure_mode.** Pros: textbook completeness.
Cons: `failure_mode` is *conditional* — it only applies where outcome ≠ correct. At 50
labels and a plausible 20 % failure rate, ~10 items spread over 4–6 failure classes ≈
1.7–2.5 per class: per-class κ is unreportable by construction, and the axis would ship
as a permanently-empty κ table. A conditional axis also breaks the "one schema for
every record" property that makes `messages.parse` clean (nullable-when means the
model can rationalize skipping it).

**C. Two unconditional axes + confidence + free rationale (chosen).**
`task_type` (what kind of exchange this is — applies to every record, humans agree on
it cheaply, gives the export its slicing dimension) and `outcome` (does the output
correctly address the input — the headline axis κ is measured on). Both closed enums,
both answered for **every** record, so support is n per axis, not n × rate. A
`confidence` enum (high/medium/low) and a mandatory free-text `rationale` ride along
for Phase 4's disagreement drill-down — **neither ever enters a κ**: filtering by the
judge's own confidence before measuring agreement is self-grading, the exact κ-gaming
CLAUDE.md forbids. `failure_mode` is explicitly deferred until enough failure-labeled
data exists to give it support (revisit trigger: outcome ≠ correct count ≥ ~60 in the
human subset); until then the rationale captures failure texture qualitatively.

**Class inventory (v1)** — sized against the support arithmetic above (4–5 classes per
axis; every axis carries an escape valve so the judge is never forced to fabricate a
fit):

- `task_type`: `factual_query` (asks for a fact/status — horaires, tarifs, trafic),
  `procedural_request` (how-to / instructions — activer un badge),
  `troubleshooting` (an error or incident is reported and help is sought),
  `planning_or_reasoning` (multi-step plan, verdict, analysis — the orchestrator's
  `plan`/`verdict` spans), `other` (closed-set escape valve).
- `outcome`: `correct` (output fully addresses the input, no visible factual/logic
  error), `partially_correct` (addresses it but incomplete, or contains a minor
  error), `incorrect` (fails the task or is factually wrong), `unjudgeable` (cannot
  be assessed from the exchange alone — missing context, ambiguous input; **a label,
  not an error**: the judge succeeded at judging that judgment is impossible, which is
  itself information Phase 4 measures agreement on).

**Enums vs data-driven schema.** A data-driven taxonomy (classes as runtime data,
dynamic schema) would allow hot-swapping taxonomies — and destroy the two things this
repo cares about: static typing (mypy sees every label site) and API-level schema
enforcement (`JudgeVerdict` with `StrEnum` fields compiles to a closed JSON-schema enum
the API *cannot* violate). Chosen: **the `StrEnum`s are the source of truth**; the
`LabelTaxonomy` model carries the human-readable definitions (name, the question the
labeler answers, per-class definition text) and a test pins that its classes mirror the
enums exactly. Changing the taxonomy is a code change + ADR — correct, because a
taxonomy change invalidates every existing κ anyway.

**Taxonomy identity.** `taxonomy_id = "tax-" + sha256(canonical serialization of axes
and definitions)[:12]`, self-verified by a model validator (house style: `record_id`,
`cluster_id`). Every `LabeledExample` carries it; Phase 4 refuses to join judge and
human labels across different taxonomy ids — agreement between different questionnaires
is not agreement.

### 2. The judge interface

**A. ABC base class with shared prompt logic.** Cons: inheritance couples the fake to
SDK-adjacent code; the Phase 2 precedent (`Embedder`) is a Protocol and it bought exact
offline tests for free. Rejected.

**B. `Judge` Protocol in `contracts/labeling.py` (chosen)**, two implementations in
`label/`: `AnthropicJudge` (SDK, §2 rules) and `FakeJudge` (deterministic,
content-derived, offline). The Protocol is deliberately **the narrowest possible
channel**:

```python
class Judge(Protocol):
    @property
    def fingerprint(self) -> JudgeFingerprint: ...
    def judge(self, input_text: str, output_text: str) -> Judgment: ...
```

**What the judge receives — decided here, once:** the two texts of the ADR-0001
consumer table, separately. **Not** `canonical_text` (the `␟`-joined form is a dedup
artifact; feeding it to a judge invites confusion about which side is under
evaluation). **Not** the `LogRecord` (metadata carries `status`, `model_id`, agent
names — priming the judge with "status: ok" biases the outcome axis). **Not**
`record_id`, cluster membership, sampling info, or any report. The signature *cannot
transport* a human label, a metadata hint, or a cluster assignment — blindness by type,
not by discipline (Decision area 5 builds on this). System-side, the judge receives the
taxonomy definitions and the few-shot examples — both fixed at construction, both
hashed into the fingerprint.

**Injection: constructor injection at the composition layer, no factory, no env
switch.** Same as the embedder: the demo constructs `FakeJudge` explicitly (offline by
declaration); the Phase 5 CLI will construct `AnthropicJudge` behind an explicit flag.
A `EVALGEN_JUDGE_BACKEND` env var was considered and rejected: an environment variable
that silently swaps the measuring instrument is exactly the class of invisible variance
this repo exists to kill. The instrument's identity travels in `JudgeFingerprint`
(judge name, configured model id, taxonomy id, prompt SHA-256, few-shot ids + content
hashes) inside every `LabelingReport` — so even a wrong composition is visible in
provenance, and the demo golden pins the fake judge's full fingerprint byte-for-byte
(any drift in the prompt template breaks the golden — a feature).

**Model id actually used:** the Protocol returns `Judgment = verdict + model_id`, where
`AnthropicJudge` fills `model_id` from `response.model` (the serving model), never from
its own config. The fingerprint's `model_id` is the *requested* model; the per-label
`model_id` is the *served* one. Both are recorded; a discrepancy is visible, not
hidden.

### 3. Typed failures, the self-validating LabelingReport, and the cost guard

**A. Judge returns `Judgment | None` / error codes.** Cons: `None` is the swallowing
path with extra steps. **B. Engine-level try/except over generic `Exception`.** Cons:
an unexpected bug (an `AttributeError` in our own code) would be laundered into an
"api_error" statistic. **C. A small typed exception hierarchy, caught exhaustively and
converted to typed report entries (chosen):** `JudgeError` base with
`JudgeRefusalError` (the model declined — on `claude-opus-4-8` surfaced as
`stop_reason == "refusal"`), `JudgeParseError` (schema-invalid or truncated output:
`parsed_output` missing/invalid, client-side constraint validation failure,
`stop_reason == "max_tokens"`), `JudgeAPIError` (SDK `APIStatusError` /
`APIConnectionError` after the SDK's own retries). Anything *not* a `JudgeError`
propagates and crashes the run — our own bugs are not labeling statistics.

`LabelingReport` extends the `IngestReport`/`DedupReport` discipline: every input
record lands in **exactly one** of five buckets, and the validator refuses a report
where `records_in != labeled + refused + failed + skipped_budget +
skipped_fewshot_collision`. Refusals and failures carry per-record entries
(`record_id`, typed reason, truncated detail); budget- and collision-skips carry their
id lists. Details need no re-scrub: every byte the judge ever saw or echoed is
post-redaction by ADR-0001 construction (the structural argument, stated once here,
replaces a second redaction pass; SDK error strings carry request ids, not record
text — and truncation to 200 chars bounds them regardless).

**Where the cost guard lives:** in the engine (`label/engine.py`), not in the judges —
the budget must bind identically on the fake path or the demo would not exercise it.
Records are processed in `record_sort_key` order (canonical, input-order independent —
the *same* records are labeled no matter how the caller assembled the list); once
`labeled + refused + failed == max_labels`, every remaining record becomes a
`skipped_budget` entry. Refusals and failures **consume budget** (an API call was
spent — the guard caps spend, not success). Overrun visibility: `skipped_budget > 0`
with the full id list in the report, the budget itself recorded alongside, and the
validator enforcing `labeled + refused + failed ≤ max_labels` — a report claiming more
judge calls than its budget refuses to exist.

### 4. The few-shot store and the leakage gate

**A. Few-shots inline in the prompt template string.** Cons: invisible to the
contamination guard, unversioned, undiffable. **B. Mined from the corpus (label a few
records by hand, use them as few-shots).** Cons: manufactures the contamination the
export guard exists to prevent. **C. Handwritten synthetic examples in a committed,
contract-validated JSONL (chosen):** `data/fewshots/judge_v1.jsonl`, one
`FewShotExample` per line — `few_shot_id` (content-derived,
`"fs-" + sha256(input ␟ output ␟ verdict fields)[:16]`, recomputed by the model
validator: a tampered few-shot refuses to load), `input_text`, `output_text`, the gold
`verdict`, and a free `note`. v1 ships one example per `outcome` class (4) plus one
deliberately planted collision twin (below).

**The anti-leak key is content, not id.** Few-shots are synthetic — they have no
`record_id` — so identity joins cannot gate them. `FewShotExample.content_hash` is the
full SHA-256 over `input_text ␟ output_text`: **the exact same formula as the
exact-dedup content hash over `canonical_text`** (ADR-0002 rule 2). One identity
function, three gates:

- **Labeling-time gate (this phase):** the engine skips any record whose canonical
  hash appears among the judge's `few_shot_content_hashes` —
  `skipped_fewshot_collision`, counted, id-listed, consuming no budget. A record whose
  answer key was handed to the judge is never labeled, so it can never enter the κ
  join (Phase 4) — the strongest possible guarantee, applied at the earliest gate.
- **Export-time gate (Phase 5 contract, fixed here):** export recomputes canonical
  hashes of every exported record and asserts intersection with the few-shot hash set
  is empty (`export ∩ few-shots = ∅`), and records `few_shot_ids` + hashes in
  `meta.json`.
- **Human-subset gate (Phase 4 corollary):** because collision records are never
  labeled by the judge, they cannot appear in the agreement join even if a human
  labeled them — κ is structurally clean of judge-seen items.

**Boundary:** the `FewShotExample` model lives in `contracts/labeling.py` (export and
validate consume hashes without importing `label`); the loader
(`label/fewshots.py::load_few_shots`) and the prompt rendering live in `label/`. The
store's limitation is stated, not hidden: the hash gate catches **verbatim** reuse
only; a near-paraphrase few-shot would pass it. Accepted for v1 (few-shots are
handwritten and reviewed; they are 5 items, not a corpus) — the documented revisit is
a cosine check against the export at the Phase 5 gate, reusing the calibrated near-dup
machinery.

### 5. Structural blindness — how it is guaranteed

Layered, each layer independently tested:

1. **Type-level:** the only per-record channel into any judge is
   `judge(input_text, output_text)` — two `str`. `LogRecord` has no label field, so
   even the engine's input cannot represent "a record with its human label". There is
   no signature in `label/` through which a human label *can* flow.
2. **Import-level (architecture test, house grep convention):** no module in
   `src/evalgen/label/` imports `evalgen.validate`, `evalgen.export`, `evalgen.demo`,
   or any mining module (`ingest`/`dedup`/`cluster`) — `label → contracts` only,
   pinned by the same module-top grep as `test_dedup_imports_only_contracts`.
3. **Path-level:** the literal strings `data/labels` and `human_label` must not appear
   anywhere under `src/evalgen/label/` (grep test). `label/` takes records as
   arguments; it opens no data files except the few-shot store it is given a path to.
4. **Prompt-level:** the prompt is a pure function
   `render_system_prompt(taxonomy, few_shots)` / `render_user_message(input, output)`
   of exactly those arguments — unit-testable, and its SHA-256 rides in the
   fingerprint, so *any* change to what the judge is told is provenance-visible and
   breaks the demo golden.
5. **SDK containment:** only `label/anthropic_judge.py` may import `anthropic`
   (grep-tested); it is deliberately **not** re-exported from `label/__init__` — the
   real judge is reached only by an explicit deep import at the composition layer, so
   no test or demo path can construct it by accident. The existing
   `test_phase2_modules_never_import_anthropic` (contracts/dedup/cluster/demo) stays
   green untouched.

### 6. Demo integration

**A. Leave the demo at four stages.** Cons: the phase that carries the repo's central
instrument would be invisible in the one artifact a first-time reader runs. **B. Extend to
`[5/5] label` with the FakeJudge over the sampled records (chosen):** the pipeline
narrative becomes ingest → dedup → cluster → sample → **label the sample** — which is
exactly the production shape (the sample is what gets labeled and exported). Offline,
deterministic, golden regenerated once with a documented, reviewable diff: stage
headers renumber `[N/4] → [N/5]`, and one new section appears; stages 1–4 content is
byte-identical (labeling happens after sampling and touches nothing upstream). The
section shows the judge fingerprint (name, model, taxonomy id, prompt hash), the
five-bucket accounting including `budget=500`, the per-axis label distributions
(marked **synthetic** — fake-judge verdicts are hash-derived, and the README must
never present them as findings, same rule as ADR-0002's demo cluster counts), and the
planted few-shot collision — the demo thereby *proves* the leakage gate in a committed
byte-exact artifact, exactly as it already proves redaction via planted secrets.

## Decision

### Rule 1 — Two closed axes, enums as schema, one shared taxonomy artifact

`contracts/taxonomy.py`: `TaskTypeLabel` and `OutcomeLabel` `StrEnum`s (classes per
Options §1), `JudgeConfidence` (`high`/`medium`/`low`), `TaxonomyClass` (name +
definition text), `TaxonomyAxis` (name, the question the labeler answers, ≥ 2 classes,
unique names), `LabelTaxonomy` (frozen; `taxonomy_id` content-derived via
`derive_taxonomy_id` and recomputed by a validator — a tampered taxonomy refuses to
exist), and the singleton **`TAXONOMY_V1`**. A test pins `TAXONOMY_V1`'s axes to the
enums member-for-member. Phase 4 renders human-labeler instructions from the *same*
`TAXONOMY_V1` definitions the judge prompt uses — one questionnaire, two annotators.

### Rule 2 — The verdict and the labeled example

`contracts/labeling.py`: `JudgeVerdict` (frozen: `task_type`, `outcome`, `confidence`,
`rationale` 1–2000 chars) — this exact model is the `output_format` schema, so a label
outside the closed sets is unrepresentable at the API boundary. `Judgment` (frozen:
`verdict` + `model_id` actually used). `LabeledExample` (frozen: `record_id`,
`taxonomy_id`, `model_id`, `verdict`) — text is **not** duplicated into the label
(join by `record_id`; one source of truth for content, no drift).

### Rule 3 — The Judge Protocol and its fingerprint

`contracts/labeling.py`: the `Judge` Protocol of Options §2 and `JudgeFingerprint`
(frozen: `judge_name`, `model_id` requested, `taxonomy_id`, `prompt_sha256` (64-hex
validated), `few_shot_ids` sorted unique, `few_shot_content_hashes` sorted unique,
lengths equal). Injection is constructor-based at the composition layer; no factory,
no env switch. The fingerprint is embedded in every `LabelingReport` and (Phase 5) in
`meta.json`.

### Rule 4 — AnthropicJudge: thin shell, pure mappers, §2 letter-for-letter

`label/anthropic_judge.py` — the only module importing `anthropic`, not exported from
`label/__init__`. Constructor takes `model: str` explicitly (composition passes
`settings.judge_model`; `label/` imports no config), taxonomy, few-shots, optional
injected client. The call is exactly:

```python
response = self._client.messages.parse(
    model=self._model,
    max_tokens=_MAX_OUTPUT_TOKENS,            # module constant, not a config knob
    system=[{"type": "text", "text": self._system_prompt,
             "cache_control": {"type": "ephemeral"}}],   # constant per run → cacheable
    messages=[{"role": "user", "content": render_user_message(input_text, output_text)}],
    thinking={"type": "adaptive"},
    output_config={"effort": "high"},
    output_format=JudgeVerdict,
)
```

No `temperature`/`top_p`/`top_k`, no `budget_tokens`, ever (400 on these models).
`stop_reason` is checked **before** `parsed_output` (`"refusal"` →
`JudgeRefusalError`; `"max_tokens"` → `JudgeParseError`); `model_id` comes from
`response.model`. Response→`Judgment` and exception→`JudgeError` mapping are
module-level **pure functions** tested offline with duck-typed stand-ins; the class
itself is a ~20-line shell that is **never instantiated in tests** (the strict reading
of the offline rule — its logic is tested through the pure mappers, its wiring only in
real runs). The few-shots are rendered into the system prompt sorted by `few_shot_id`
(deterministic bytes → stable prompt hash → stable cache prefix).

### Rule 5 — FakeJudge: content-derived, offline, total

`label/fake.py`: `FAKE_JUDGE_MODEL_ID = "fake-judge-v1"`. The verdict is a pure
function of the exchange: `digest = sha256("fake-judge-v1 ␟ input ␟ output")`; bytes
0/1/2 select `task_type`/`outcome`/`confidence` by modulo over the enum members
(platform-independent — no salted `hash()`, no RNG state); the rationale is a fixed
template naming the digest prefix and declaring itself synthetic. Same record → same
label, across instances, runs, and machines. The FakeJudge **never raises**; failure
paths are exercised by test-local stub judges (conftest style, the `StubEmbedder`
precedent), keeping test scaffolding out of production code. Its fingerprint is built
with the same `render_system_prompt`/`prompt_sha256` as the real judge — the demo
golden thereby pins the production prompt template byte-for-byte without any network.

### Rule 6 — The engine: canonical order, five buckets, budget in one place

`label/engine.py::run_labeling(records, *, judge, max_labels) -> LabelingOutcome`:

1. Refuse duplicate `record_id`s (labeling expects post-dedup input; a duplicated id
   is a caller bug, not a statistic).
2. Sort by `record_sort_key` (ADR-0002 rule 1) — the budget cut and every report entry
   are input-order independent (shuffle-tested).
3. Per record, in order: canonical-hash it; if the hash ∈
   `judge.fingerprint.few_shot_content_hashes` → `skipped_fewshot_collision` (no
   budget consumed); elif `labeled + refused + failed == max_labels` →
   `skipped_budget`; else call `judge.judge(input_text, output_text)` and convert the
   result — `Judgment` → `LabeledExample` (with `taxonomy_id` from the fingerprint,
   `model_id` from the judgment), `JudgeRefusalError` → refusal entry,
   `JudgeParseError`/`JudgeAPIError` → failure entry. Non-`JudgeError` exceptions
   propagate.
4. Assemble `LabelingOutcome` (labeled sorted by `record_id`; all entry tuples sorted;
   all sums checked by the contracts below).

### Rule 7 — Self-validating LabelingReport and LabelingOutcome

`contracts/labeling.py` (the `IngestReport` discipline, extended; validators run on
deserialization too): `LabelFailureReason` (`refusal`/`parse_error`/`api_error`),
`LabelFailureEntry` (`record_id`, reason, detail ≤ 200 chars), `LabelingReport`
(fingerprint, `max_labels`, `records_in`, the five counters, `failures_by_reason`,
refusal/failure entries, budget-skip and collision-skip id lists) refusing to exist
unless: the five buckets sum to `records_in`; entry/list lengths match their counters;
reason kinds match their buckets; `failures_by_reason` sums to `failed`;
`labeled + refused + failed ≤ max_labels`; all entries sorted by `record_id`; the four
id sets pairwise disjoint. `LabelingOutcome` (labeled tuple + report) cross-checks:
`len(labeled) == report.labeled`, ids sorted unique, disjoint from every report
bucket, and every example's `taxonomy_id` equals the fingerprint's — a forged outcome
refuses to deserialize, same tamper-evidence as `LogRecord` and `DedupOutcome`.

### Rule 8 — The few-shot store and its gates

`contracts/labeling.py`: `FewShotExample` (frozen; content-derived self-verified
`few_shot_id`; `content_hash` property = SHA-256 over `input ␟ output`, the exact-dedup
identity function). `label/fewshots.py::load_few_shots(path, *, sanitizer)` validates
every line, refuses duplicate ids or duplicate content hashes, returns the tuple sorted
by `few_shot_id`. `data/fewshots/judge_v1.jsonl` ships 5 handwritten examples: one per
`outcome` class + **one planted collision twin** of a sampled demo record (its `note`
says so), making the labeling-time gate visible in the golden. The export-time gate
(`export ∩ few-shots = ∅` on canonical hashes, few-shot ids + hashes in `meta.json`)
is fixed here as Phase 5's contract.

**Amendment (red team F-1):** the loader takes a REQUIRED injected `TextSanitizer`
(Protocol in `contracts/labeling.py`; the composition layer passes
`ingest.redaction.sanitize_text` — `label/` never imports `ingest`) and refuses any
example whose `input_text`, `output_text`, `verdict.rationale` or `note` the sanitizer
would rewrite. The store is thereby redaction-clean **by construction**: a secret-bearing
few-shot can neither reach the judge API verbatim nor evade the collision/export gates
through the raw-vs-redacted hash asymmetry. The refusal echoes only the sanitized form.
The previous "handwritten and reviewed" stance for v1 is retired — review is exactly the
procedural guarantee ADR-0001 §3 rejects for redaction. The near-paraphrase limitation
(cosine check at the export gate) is unchanged and still deferred.

### Rule 9 — Blindness, enforced by tests

New architecture tests alongside `test_package.py`'s greps: `label` imports
no `evalgen.{ingest,dedup,cluster,validate,export,demo}`; `anthropic` is imported
**only** in `label/anthropic_judge.py`; the literals `data/labels` and
`human_label` appear nowhere under `src/evalgen/label/`; `contracts` still imports no
sibling (existing test covers the new modules for free); nothing imports the demo;
`label/__init__` does not export `AnthropicJudge`.

**Amendment (red team F-3):** the two import tests walk the AST of every `label/` file
and check every `Import`/`ImportFrom` node at **every** nesting depth (function bodies
and conditionals included, `from evalgen import validate` included) — column-0 grep
evasion is closed. Dynamically-built import strings remain out of reach of any static
check; the load-bearing guarantee stays layer 1 (the two-string Protocol).

### Rule 10 — Demo `[5/5] label` and the golden regeneration

`demo.py` labels the **sampled** records with
`FakeJudge(taxonomy=TAXONOMY_V1, few_shots=load_few_shots(...))` and
`max_labels=settings.max_labels_per_run`, rendering: fingerprint line (judge, model,
taxonomy id, prompt-hash prefix), the five-bucket accounting with the budget, the
collision entry, and per-axis distributions in enum declaration order marked
`[synthetic fake-judge verdicts]`. `tests/golden/demo_output.txt` is regenerated
**once**, with the review rule: the diff may contain only the `[N/4] → [N/5]` header
renumbering and the appended `[5/5]` section — any other changed byte means labeling
perturbed an upstream stage and is a defect. `config.py` gains the missing bound
`max_labels_per_run: int = Field(default=500, ge=1)` (ADR-0002 amendment discipline:
knobs carry their contracts' bounds).

**Amendment (red team F-2):** a second sanctioned regeneration accompanies the
prompt-demarcation hardening, under its own reviewed diff rule: the only changed byte
is the `[5/5]` line's `prompt=` prefix (`11a50e228582 → b963c9e7aa28`); stages 1–4,
the five-bucket accounting, the collision entry, and both distributions are
byte-identical (verified). Every future prompt change pays the same price by design —
the golden IS the prompt-drift alarm.

## Consequences

**Positive:** per-class κ is designed to be reportable at the configured n (4–5
classes, no conditional axis); the judge's entire input surface is two strings plus a
hashed, versioned prompt — blindness and prompt drift are testable properties, not
promises; every non-label outcome is a typed, counted, id-traceable entry and the
report cannot mis-sum; the budget binds identically on fake and real paths and
overruns are visible with ids; few-shot leakage is gated at labeling time *and* fixed
as an export-time contract, on the same content-identity function dedup already uses;
the offline demo pins the production prompt template, the taxonomy id, and the
collision gate byte-for-byte; the model id that actually served each label is stored
per label.

**Negative (accepted):**

- **The fake judge's labels are noise** — hash-derived, semantically arbitrary. The
  demo says so on its face; κ machinery in Phase 4 gets *plausible* fixtures from
  hand-built label sets, not from the FakeJudge.
- **Two axes are lossy** — no failure-mode axis in v1 (support arithmetic forbids it);
  failure texture lives in free-text rationales until the revisit trigger.
- **`unjudgeable` and `other` are abuse-able escape valves** — a lazy judge could
  overuse them. Not hidden: both are ordinary classes whose per-class κ and confusion
  rows Phase 4 reports; drift into the valves is measurable.
- **The hash gate catches verbatim few-shot reuse only** — near-paraphrase leakage
  waits for the documented cosine check at the export gate.
- **The real judge is nondeterministic** — inherent to the instrument; determinism is
  owed by the offline path, provenance (model id, prompt hash, taxonomy id) by the
  online one.
- **`messages.parse` + enum schema constrains but does not grade** — schema validity
  ≠ label validity; that is precisely why Phase 4 exists.
- **One-attempt-per-record** beyond the SDK's built-in retries — a flaky-API run
  yields honest `api_error` entries rather than silent re-rolls (re-rolling a judge
  until it answers is sampling bias); re-running the engine is the recovery path.

**Explicitly deferred:** `failure_mode` axis (trigger: ≥ ~60 failure-outcome human
labels); judge self-consistency measurement (same record, k calls — an instrument-noise
number for the README); batch/parallel labeling and the Batches API (cost optimization,
needs no contract change); near-paraphrase few-shot gate (cosine at export);
`judge_model_fast` wiring (an explicit Phase 5 CLI choice); token-usage accounting per
label.

**Validated by (Phase 3 test battery):** enum↔`TAXONOMY_V1` mirror test; tampered
taxonomy/few-shot/report/outcome all refuse to validate (id and sum validators);
FakeJudge determinism (same exchange → identical `Judgment` across instances; pinned
expected verdicts for named fixtures); engine five-bucket sum under every mix
(refusals, failures, budget cut, collisions — via test-local stub judges raising each
typed error); budget boundary (`n > max_labels` → exactly `max_labels` judge calls,
canonical-order cut, shuffle-invariant); collision consumes no budget; non-`JudgeError`
exceptions propagate (a planted `AttributeError` is not laundered into a statistic);
pure-mapper tests for response→`Judgment` (including `stop_reason` precedence) and
exception→error taxonomy with duck-typed stand-ins — the real judge never
instantiated; prompt render purity + `prompt_sha256` stability; the Rule 9 AST
battery (every import node at every depth); demo golden byte-equality + double-run
identity, the `[5/5]` section showing the planted collision, and the
stages-1–4-unchanged diff rule; **amendment battery:** the red-team F-1 payload
replayed verbatim (the fixture's planted email inside a few-shot → raw-vs-redacted
hash mismatch proven, loader refuses, secret never echoed in the exception), every
string field redaction-checked (`output_text`/`rationale`/`note` refuse cases), the
committed store proven sanitize-neutral under the production sanitizer, the injected
sanitizer's judgment binding (an always-rewriting stub refuses a clean store), and the
F-2 injection payload landing as user-turn data under the demarcating system prompt.
