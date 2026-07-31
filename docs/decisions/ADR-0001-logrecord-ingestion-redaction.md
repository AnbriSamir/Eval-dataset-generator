# ADR-0001 — LogRecord contract, TraceSpan/generic ingestion, and redaction at the boundary

**Status:** Accepted (2026-07-31) — amended same day after the pre-commit red-team pass:
(a) the zero-width strip became a full invisible-character strip (the whole Unicode `Cf`
format category + non-`Cf` stragglers, applied on *both* sides of NFKC) after a U+2060
WORD JOINER payload walked past the original four-code-point table; (b) the generic
adapter's exchange-candidacy check now runs on *sanitized* text so an invisible-only
field is one `no_exchange` skip instead of a file-aborting error; (c) `normalize_text`
now strips C0 control characters (except `\n`/`\t`) and DEL after newline
normalization — an injected 0x1f (the `CANONICAL_SEP`) previously made two distinct
exchanges collide on both `record_id` and `canonical_text`, so the "cannot appear in
normal text" assumption is now enforced rather than asserted. Details in Rules 3
and 5.

## Context

Phase 1 defines the atom every later phase operates on. Dedup hashes it, clustering embeds it,
the judge reads it, exports trace back to it — so whatever shape it takes is effectively frozen
by downstream dependence. Four failure modes dominate log-mining pipelines and must be prevented
*structurally* here, not mitigated later:

1. **Undefined "text of a record".** If each downstream module re-decides which text to hash,
   embed, or show the judge, dedup and clustering silently diverge (a record deduped on one text
   but clustered on another), and no two runs agree on what a duplicate even is.
2. **Secrets outliving ingestion.** Anything that survives normalization gets hashed, embedded,
   clustered, sent to the judge (an external API), and exported. There is exactly one moment when
   redaction can be total: before the record exists.
3. **Ids that leak or drift.** An id derived from *pre*-redaction content embeds secret bits in a
   value that is deliberately published everywhere (dedup reports, exports, provenance) — and
   changes when the secret rotates, breaking replayability. `uuid4` breaks it differently: two
   identical runs disagree on every id.
4. **Silent drops.** A malformed line that vanishes without a count makes the exported dataset's
   denominator a lie — "n records from source X" becomes unverifiable, and the provenance story
   collapses.

First-class input is the sibling repo's `TraceSpan` JSONL
(`multi-agent-orchestrator/src/orchestrator/contracts/trace.py`): frozen Pydantic spans with
`span_id`, `task_id`, `agent`, `action`, `status`, cost fields, and a free-form
`payload: dict[str, Any]`. Two ground truths from reading that repo shape this ADR:

- **Spans are mostly control flow.** The graph emits `intake`, `select`, memory `retrieve`,
  `plan`, `execute`, `verdict`, `synthesize` spans whose payloads are largely bookkeeping
  (`subtask=t1`, `attempt=2`, `passed=True` — `graph/builder.py`). Only some spans can ever carry
  a judgeable input/output exchange.
- **Upstream redaction exists but is not a contract we may rely on.** The sibling scrubs payloads
  before persistence (`observability/trace_store.py::_redacted`), but it is best-effort, its
  pattern set differs, generic JSONL sources promise nothing, and files may predate the scrub.
  The trust boundary is ours: ingest assumes every input line is hostile.

Also relevant: `TraceSpan` carries **no timestamp field** — a `LogRecord` timestamp must
therefore be optional, not defaulted from the wall clock (determinism).

## Decision drivers

- Same inputs → byte-identical outputs; ids content-derived, no `uuid4`, no wall-clock defaults
  (CLAUDE.md §5, same bit-exact discipline as the sibling repos).
- Redaction is structural: the leaky path must not exist, rather than downstream code being
  careful (CLAUDE.md §3 — redaction lives in `ingest`, nothing downstream sees raw secrets).
- Downstream consumers need one answer each: dedup hashes *what*, clustering embeds *what*, the
  judge reads *what*.
- Nothing is ever silently dropped: every line is normalized, rejected (typed reason), or skipped
  (typed reason), and the three counts must sum to lines read.
- Tests are fully offline and adversarial (nested secrets, unicode tricks, secrets in dict keys).

## Options considered

### 1. LogRecord content shape

**A. Generic `payload: dict` (mirror `TraceSpan`).** Pros: lossless, adapter-trivial. Cons: this
*is* failure mode 1 — every consumer re-decides the text; empty/unjudgeable records are
representable; `dict[str, Any]` reintroduces arbitrary nesting that redaction must chase forever.

**B. Explicit `input_text` / `output_text` pair (chosen).** Pros: the exchange is the atom the
judge grades, dedup hashes, and clustering embeds; `min_length=1` makes a nothing-to-judge record
unrepresentable; extra signals (model id, tokens, status) survive as flat, stringified, redacted
`metadata`. Cons: lossy for exotic payloads — accepted, that is the discipline (the sibling's
ADR-0001 made the same trade for tool schemas).

**C. Input-only records.** Cons: the judge labels *exchanges*; a same-input-different-output pair
is two distinct eval cases and must not collapse at the contract level. Rejected.

### 2. Which TraceSpans become candidate records

**A. Every span.** Cons: control-flow spans (`intake`, `select`, `retrieve`…) carry no exchange;
they would flood exact-dedup with near-identical bookkeeping strings, distort cluster densities
(and therefore stratified quotas) with a giant control-flow blob, and give the judge nothing to
grade. Volume is not coverage.

**B. Only spans carrying an exploitable exchange (chosen).** Candidacy is a conjunction, each
miss counted separately: action in an allowlist (default `{"plan", "execute", "verdict"}` — the
three decision points where the orchestrator produces judgeable content), `status == "ok"`, and a
non-empty input/output pair extractable from the payload via ordered key preferences. `error` /
`blocked` spans are skipped by default: a failure-mode dataset is a different taxonomy and a
deliberate future flag, not an accidental mixture.

**C. One record per task (join all spans).** Cons: loses per-decision granularity; a label could
no longer trace to one span; that is a *different* dataset an aggregating adapter can build later
without touching this contract. Rejected for v1.

### 3. Redaction mechanism

**A. Reuse the sibling's scrub verbatim.** Cons: single `[REDACTED]` placeholder destroys
category information; no phones, JWTs, or local user paths; no unicode normalization, so
zero-width splitting and fullwidth homoglyphs walk through.

**B. Categorized regex scrub over normalized text (chosen).** Strip invisible characters +
NFKC-normalize first, then apply an ordered tuple of compiled patterns replacing with
`[REDACTED:<category>]`. Recursive descent for structured values — dicts (keys included), lists,
tuples, string leaves — following the sibling's `trace_store.py::_scrub_value` precedent.

**C. Entropy/ML-based detection.** Cons: nondeterministic thresholds, heavyweight, unverifiable
in offline tests. Rejected; regex + honest limitations wins on reproducibility.

### 4. Malformed-line handling

**A. Raise on first bad line.** Cons: one corrupt byte discards a whole file of good records.
**B. Skip silently / log at WARN.** Cons: failure mode 4 — the denominator lie.
**C. Typed, counted rejection with a self-validating report (chosen).** Every line lands in
exactly one bucket; the report model *refuses to validate* if the buckets don't sum.

## Decision

### Rule 1 — LogRecord is the frozen atom; its texts are canon

`src/evalgen/contracts/records.py` defines (all `ConfigDict(frozen=True)`):

- `SourceKind` — `StrEnum`: `tracespan`, `generic_jsonl`.
- `RecordOrigin` — `source_kind`, `source_name` (a **logical** name, by default the file's
  *basename* — never the absolute path, which embeds `C:\Users\<name>` and is itself PII),
  `line_no` (1-based line in the source), `span_id: str | None`, `task_id: str | None`.
- `LogRecord` — `record_id`, `origin`, `timestamp: datetime | None = None` (never a wall-clock
  default; `None` for TraceSpan sources, which carry no timestamp), `input_text` and
  `output_text` (both `min_length=1` — an empty exchange is unrepresentable),
  `metadata: dict[str, str]` (flat, stringified, already-redacted scalars: `agent`, `action`,
  `model_id`, token counts…).

**The canonical texts, decided once here:**

| Consumer | Reads | Why |
|---|---|---|
| exact dedup + near-dup | `canonical_text` = `input_text + "\x1f" + output_text` | An exchange repeated verbatim inflates every downstream metric; same input with a different output is a *distinct* eval case and must survive. `\x1f` (the unit separator, same trick as the sibling's `ids.py`) prevents boundary-shift collisions (`"ab"+"c"` vs `"a"+"bc"`). |
| clustering / coverage | `cluster_text` = `input_text` | Coverage means covering the *traffic* distribution, and traffic is defined by what came in — not by output phrasing, which would split one intent across clusters. |
| LLM judge | `input_text` and `output_text`, presented separately | The judge grades the output *against* the input; concatenation would invite confusion about which side is under evaluation. |

Both texts are exposed as read-only properties on `LogRecord` so no downstream module ever
re-derives them.

### Rule 2 — record_id: content-derived, computed AFTER redaction, self-verifying

```
record_id = "rec-" + sha256("\x1f".join([
    source_kind, source_name, str(line_no), input_text, output_text
]).encode("utf-8")).hexdigest()[:16]
```

- **After redaction, always.** All five parts are the post-normalization, post-redaction values.
  An id over raw text would both leak secret bits into a published value and rotate whenever the
  secret does. The invariant test: two raw lines identical except for the secret value yield the
  **same** `record_id`.
- **Origin is part of identity.** Two occurrences of the same exchange (different lines/files)
  get distinct ids, so the dedup report can say "dropped `rec-a…`, kept `rec-b…`" with real
  references — while re-ingesting the same file is idempotent (same ids, byte-identical output).
  Content equality is the *dedup hash's* job (full sha256 over `canonical_text`), not the id's.
- **Excluded from the hash:** `timestamp` (volatile, absent for TraceSpans) and `metadata`
  (auxiliary; dict-order sensitivity would fabricate false identity distinctions).
- **16 hex (64 bits), not the sibling's 12.** Spans number in the dozens per task; records number
  in the millions per corpus. At 10⁶ records, collision odds are ~2.7 × 10⁻⁸ at 64 bits vs ~0.2%
  at 48 — cheap insurance on a published identifier.
- **Self-verifying:** a `model_validator` on `LogRecord` recomputes the id from the fields and
  rejects a mismatch — a record whose id does not match its content is *unrepresentable*, even
  when deserialized from disk (tamper detection for free). `derive_record_id()` is a pure
  function in `contracts` (no import of `ingest` — the boundary test stays green). What the
  validator *cannot* check is that redaction ran (contracts must not know the patterns); that
  half of the invariant is enforced by `ingest` being the only production constructor of
  `LogRecord`, plus the adversarial test battery.

### Rule 3 — Redaction: normalize, then scrub, then hash; recursive; categorized

`src/evalgen/ingest/redaction.py`, applied by the normalizer to every extracted string (and via
recursive descent to every metadata value **and dict key** — a secret-bearing key would otherwise
leak, per the sibling's `trace_store.py` precedent):

1. **Normalize first:** strip invisible characters, NFKC (folds fullwidth homoglyphs like
   `ｓｋ－` into ASCII), strip invisibles *again*, normalize `\r\n` → `\n`. The invisible set is
   the whole **`Cf` format category by predicate** (zero-widths U+200B–200D, word joiner U+2060,
   soft hyphen U+00AD, bidi marks, invisible operators, BOM, the U+E00xx tag block) **plus
   non-`Cf` stragglers** (U+034F CGJ, variation selectors, invisible Hangul fillers) — *never a
   hand-enumerated code-point table*: the red-team defeated the original four-code-point table
   with U+2060, the direct successor of the one word joiner it did cover. Both strip passes are
   load-bearing: **before** NFKC because U+034F blocks canonical composition (`e + CGJ + ́`
   would stay decomposed and hash-diverge from `é` — a dedup miss); **after** NFKC because the
   fold itself can emit an invisible (U+3164/U+FFA0 → U+1160). Normalization also makes
   unicode-equivalent texts hash equal — a dedup correctness win, not just a security step.
2. **Scrub second**, module-level compiled patterns in a fixed order (specific before generic, so
   categories are precise and a JWT is not half-eaten by the opaque-token rule):
   JWTs → provider API keys (`sk-`, `sk-ant-`) → AWS key ids (`AKIA…`) → bearer/authorization
   headers → `key[:=]value` where the key name signals a secret → long opaque tokens (≥ 32
   base64/hex-ish chars) → emails → phone numbers (international `+…` and French `0X XX XX XX XX`
   forms only — deliberately conservative to avoid eating numeric ids) → local user paths
   (`C:\Users\<name>`, `/home/<name>`, `/Users/<name>` — the username segment is PII).
3. **Constant placeholders** `[REDACTED:<category>]` — never a hash or salt of the secret (that
   would leak bits and break the same-id-regardless-of-secret invariant). Consequence embraced:
   two records differing only in their secrets become exact duplicates post-redaction and dedup
   collapses them — correct, they *are* the same eval case.
4. **Order is load-bearing:** normalize → redact → derive `record_id` → construct the frozen
   record. Nothing persists, hashes, or embeds text that has not passed steps 1–2.

**Honest limitation (stated, tested, not hidden):** regex scrubbing reduces blast radius; it is
not a guarantee (same stance as the sibling's `memory/redaction.py`). Accepted false positives:
40-hex git SHAs and base64 blobs in legitimate content get redacted — and the invisible strip
eats bidi marks and variation selectors in legitimate text — over-redaction is the chosen
failure direction. The adversarial suite must cover at minimum: a secret nested three dicts
deep; a secret in a dict *key*; an invisible-split `sk-ant-…` key across the family (zero-width
U+200B, word joiner U+2060, soft hyphen U+00AD, CGJ U+034F, a tag character, a Hangul filler —
the red-team payloads are replayed verbatim so the strip can never silently regress to a
code-point table); a split 20/20 opaque token; a mid-body split (no recognizable key *tail* may
survive); a fullwidth-homoglyph key; an email inside a malformed line's parse-error message
(reject samples are scrubbed too — Rule 4); the same-id-when-only-the-secret-differs invariant;
a French phone number; a Windows user path; NFC/NFD equivalents hashing identically; both strip
passes pinned (CGJ-blocked composition; NFKC-emitted U+1160).

### Rule 4 — Nothing is silently dropped: the self-validating IngestReport

Every source line lands in exactly one of three buckets:

- **normalized** → a `LogRecord`;
- **rejected** (`RejectReason`: `invalid_encoding`, `invalid_json`, `schema_mismatch`,
  `missing_field`) — data we could not read: malformed UTF-8, non-JSON, non-dict JSON, a span
  that fails `TraceSpan` validation, a mapped key absent from the object;
- **skipped** (`SkipReason`: `blank_line`, `action_not_candidate`, `status_not_ok`,
  `no_exchange`) — data we read and *chose* not to take, by declared policy (an empty-string
  exchange is `no_exchange`: real-world empty, not malformed).

`IngestReport` (in `contracts` — export's `meta.json` will embed it as provenance) carries
`lines_read`, `records_normalized`, `lines_rejected`, `lines_skipped`, per-reason counters, up to
20 reject samples (first-in-file order — deterministic), and a `timestamps_unparsed` warning
counter (a bad clock demotes `timestamp` to `None`; it does not drop data). A `model_validator`
enforces `lines_read == normalized + rejected + skipped` — a report that doesn't add up refuses
to exist. Reject samples carry `line_no`, the typed reason, and a *scrubbed, truncated* detail
string — parse-error messages embed the raw line, so the redactor runs on them too. Raw line
content is never stored.

Files are read as bytes and split on `\n`, decoding per line — one malformed UTF-8 line becomes
one `invalid_encoding` reject instead of crashing the file.

### Rule 5 — Two adapters, one normalizer

- `ingest/tracespan.py` — validates each line against a local structural mirror of the sibling's
  `TraceSpan` (same fields; we do not import across repos), applies the candidacy conjunction
  decided in Options §2 (action in the allowlist — default `{"plan", "execute", "verdict"}`,
  config-exposed — AND `status == "ok"` AND a non-empty extractable exchange; each miss counted
  under its own `SkipReason`), and extracts the exchange from `payload` by ordered key preference: input from the first
  present of `("input", "prompt", "task", "question")`, output from
  `("output", "response", "content", "answer", "result")`. `agent`, `action`, `status`,
  `model_id`, `tokens_in/out`, `cost_usd`, `latency_ms` land stringified in `metadata`;
  `span_id`/`task_id` land in `origin`.
- `ingest/generic.py` — driven by a frozen `GenericMapping`: `input_key`, `output_key`
  (dot-paths, e.g. `payload.question`), optional `timestamp_key` (ISO-8601 only), optional
  `id_key`/`task_key` (filling `origin.span_id`/`task_id` with the source's native ids), and an
  explicit opt-in `metadata_keys` tuple. No implicit "take everything" — surviving fields are a
  declared decision. Exchange candidacy (non-empty input/output) is decided on the **sanitized**
  text — the same discipline as the TraceSpan extractor: raw `str.strip()` does not see
  invisible characters, so a field made only of them would pass a raw check and then explode
  `build_record`'s empty-exchange guard, aborting the whole file (red-team MAJOR). One hostile
  line must cost one `no_exchange` skip, never the other N−1 records.
- Both funnel into one `ingest/normalize.py::build_record()` — the **only** production
  constructor of `LogRecord`, and therefore the single place where the
  normalize → redact → derive-id → freeze order lives.

## Consequences

**Positive:** one canonical answer per consumer (dedup/cluster/judge) frozen at the contract;
ids are publishable, replayable, and tamper-evident (self-verifying validator); the leaky paths
are structurally absent (no unredacted text ever reaches a hash, an embedding, the judge SDK, or
disk; reject samples included); re-ingestion is idempotent; every published dataset can state
"n read, n normalized, n rejected (why), n skipped (why)" and the report cannot lie about the
sum; the generic adapter onboards any JSONL source with a five-field mapping.

**Negative (accepted):** input/output is lossy for exotic span payloads (metadata catches flat
scalars only); the action allowlist must be revisited if the sibling adds new exchange-bearing
actions (config-exposed, not hardcoded); regex redaction over-redacts legitimate high-entropy
content (chosen failure direction) and under-redacts adversaries beyond its patterns (stated
limitation); conservative phone patterns miss some local formats; v1 loaders materialize whole
files in memory (streaming is a revisit-when-measured concern, not a contract change).

**Explicitly deferred:** `error`/`blocked` span mining (failure-mode taxonomy — different
dataset, future flag); a task-level aggregating adapter; streaming ingestion; per-line size caps.

**Validated by:** the Phase 1 test suite — module-boundary test stays green (`contracts` imports
nothing); determinism test (same fixture file twice → byte-identical records and report);
idempotence + id-stability tests; the Rule 3 adversarial redaction battery; report-sum
`ValidationError` test; malformed-fixture tests covering every `RejectReason`/`SkipReason`; and
downstream, Phase 2's dedup report referencing `record_id`s that this ADR made stable.
