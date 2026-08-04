# ADR-0005 — Export: canonical golden.jsonl, the two-section meta.json provenance, the contamination guard, and the κ gate with its deliberate override

**Status:** Accepted (2026-08-01) — amended 2026-08-01 after the pre-commit
red-team pass (see Amendment: (a) MAJOR-1 blocked-collision evidence is the
content hash, never a few-shot id, (b) MINOR-1 the line self-verification claim
scoped to identity + texts, (c) MINOR-2 pair-level write staging)

## Context

Phase 5 ships the product every earlier phase exists to make defensible: a
`golden.jsonl` eval set plus a `meta.json` provenance record. Nothing new is
*measured* here — everything published here was measured upstream — so the failure
modes of this phase are all *integrity* failures, and each one destroys the whole
repo's signal retroactively:

1. **An export the κ does not cover.** A dataset stamped "κ = 0.71" where the κ was
   measured on a different judge (other prompt, other few-shots, other model, other
   taxonomy) certifies nothing. ADR-0004 options §6 fixed the contract; this ADR
   implements it.
2. **A leaked answer key.** A few-shot example (the judge's worked answers) inside
   the export makes any downstream benchmark on those items measure memory, not
   quality. ADR-0003 rule 8 fixed `export ∩ few-shots = ∅` on canonical content
   hashes as a Phase 5 duty.
3. **Unreplayable provenance.** An export whose inputs, knobs, seeds and instrument
   cannot be re-derived byte-for-byte is a claim, not an artifact. CLAUDE.md §1
   names this a repo-destroying failure; `/repro-audit` needs a byte-diffable target.
4. **A silent gate.** A κ gate that can be bypassed invisibly (env var, default
   flag, hand-edited meta.json) is worse than no gate: it *launders* a low κ. The
   config docstring already fixes the stance: override is possible, deliberate, and
   "the export then carries the honest low kappa on its face".
5. **A denominator lie, export edition.** Candidates that vanish between labeling
   and the written file without a typed, counted cause (ADR-0001 failure mode 4,
   final form).

**Ground truth this ADR is built on** (re-read, not assumed): the demo pipeline
labels 49 of 50 sampled records (1 planted few-shot collision,
`rec-d1087e0ca3da3367`, excluded at labeling — ADR-0003 rule 6). The committed
agreement golden pins: matched n = 40, headline (outcome axis)
**κ = 0.513109**, CI95 = [0.286421, 0.707241], `headline_ready = True`, status `ok`,
judge fingerprint `fake / fake-judge-v1 / tax-d9ca3b87b403 / prompt b963c9e7aa28…`,
`human_labels_sha256 = 2beaf42e…`. Config: `min_export_kappa = 0.6`, `seed = 1750`.
So on the committed fixtures **the gate genuinely blocks** (0.513109 < 0.6) — the
offline demo must exercise the override path to produce files at all, which is
exactly the machinery proof we want committed. The protect hook blocks agent-tool
writes to any `golden*.jsonl` / `meta.json` / `human_labels*` basename — pipeline
*runtime* writes (the Python process) to gitignored `data/out/` are the sanctioned
path; no committed fixture may use those basenames.

`AgreementReport.headline` returns the outcome-axis global `KappaValue` or `None`
when `headline_ready` is false; per ADR-0004 Amendment (d) a present headline can
still carry status `undefined_single_class` (κ `None`) and MUST block exactly like a
missing one. ADR-0004 Amendment (a) obliges meta.json to copy `human_labels_sha256`
next to the embedded report.

## Decision drivers

- Same inputs → **byte-identical `golden.jsonl`** and byte-identical deterministic
  provenance; every volatile value (wall clock, git SHA, environment) is injected by
  the composition layer and quarantined in one clearly-marked section — pure
  functions never read a clock, git, or the network (CLAUDE.md §5).
- Nothing dropped in silence: `candidates = exported + blocked-by-cause`, enforced
  by a self-validating report; the full funnel (lines read → records → kept →
  sampled → labeled → exported) is enforced across the *embedded reports* in
  meta.json, not narrated.
- The gates of ADR-0003/0004 are implemented letter-for-letter, and the override is
  structurally loud: a typed verdict (`passed` / `passed_with_override` /
  `blocked`), a mandatory reason, and the low κ printed on the export's face.
- A forged artifact refuses to exist: golden lines recompute their own ids and
  content hashes; the manifest recomputes the gate from its own embedded reports
  (the `LogRecord`/`AgreementReport` tamper-evidence discipline, applied to the
  final product).
- Tests 100 % offline; `make export` is a zero-arg, zero-network, golden-pinned
  composition (`demo.py`/`agreement_demo.py` precedent) that says SYNTHETIC on its
  face; the demo and agreement goldens are byte-untouched this phase.
- `export` depends on everything at the *data* level but imports **only
  `contracts`** — every input arrives as an already-validated frozen model, so the
  import DAG stays minimal and the AST battery extends cheaply.

## Options considered

### 1. The golden.jsonl line — which fields, whose labels

**A. Minimal lines (`record_id`, `input_text`, `output_text`, `task_type`,
`outcome`).** Pros: lean, consumer-friendly. Cons: severs the traceability the repo
exists to provide — no source span, no cluster, no way to independently re-check the
contamination guard or re-derive the id; a line separated from its meta.json becomes
uninterpretable (whose labels? which taxonomy?).

**B. Lossless dump of every internal model.** Pros: nothing to decide. Cons: ships
join-artifacts (dedup entries, bootstrap internals) that are provenance, not data —
meta.json's job; bloats every line with run-level constants.

**C. Self-verifying eval lines + nested provenance block (chosen).** Per line:
`record_id`, `taxonomy_id`, the **judge labels** `task_type`/`outcome` (flat — the
consumption path), the judge diagnostics `judge_model_id` (the model that ACTUALLY
served the label, per ADR-0003), `judge_confidence`, `judge_rationale` (transparency:
"never trusted blindly" means the consumer can audit the reasoning; neither may be
used to filter — the κ was measured unfiltered, and filtering by the judge's own
confidence is the self-grading CLAUDE.md forbids), the post-redaction `input_text` /
`output_text`, the redacted flat `metadata`, and a `provenance` object:
`source_kind`, `source_name` (basename — ADR-0001 PII rule), `line_no`, `span_id`,
`task_id`, `timestamp` (source-derived or null, never wall-clock), `cluster_id`
(`cl-…` or `noise` — the coverage dimension), `content_hash` (the exact-dedup
identity — every consumer can re-run the contamination check independently).
The `GoldenRecord` model validator **recomputes `record_id` and `content_hash`
from the line's own fields** — a line tampered on its identity, origin or texts
refuses to parse (the line carries origin + texts, so both derivations close
over it). *Amended 2026-08-01 (red-team MINOR-1): this self-verification covers
identity + texts ONLY — the label fields are not derivable and are fenced at the
file level; see Amendment (b).*

**Human labels in golden.jsonl — decided: NO.** Weighed seriously, rejected on three
grounds. (a) *One label semantics:* a file where 40 of 49 lines also carry a human
opinion invites "use the human label where it exists" — a heterogeneous dataset
whose effective labeling protocol no κ describes. The product's labels are the
judge's, calibrated by the κ stamped on the export; that is the design. (b) *The
calibration set stays un-broadcast:* human labels live in ONE hook-protected place
and flow only into `validate/`. Copying them per-record into the most-shared
artifact of the repo maximizes the surface for them to leak into some future judge
context (the exact channel ADR-0003/0004 spent two phases closing). (c) The
*aggregate* human signal already ships: meta.json embeds the entire
`AgreementReport` (§2) — κ, per-class table, confusion matrix, and the disagreement
drill-down. Residual stated: the drill-down inside the report does reveal the human
label for *disagreeing* pairs — accepted, that is the honesty artifact (already
printed by `make agreement`), and any judge change invalidates the κ and demands a
fresh subset anyway (ADR-0004 flywheel rule).

**Records humans labeled are NOT excluded from the export.** Considered (hold out
the calibration overlap for reuse) and rejected: the fingerprint-invalidation rule
already forbids reusing the subset after any judge change, so holding out buys
nothing and silently gutting 40 of 49 records is coverage vandalism.

**Line order.** `record_id` ascending vs `record_sort_key` — chosen:
**`record_sort_key`** (source_name, line_no, record_id), THE canonical total order
of ADR-0002 rule 1; one order rules every artifact, and the file reads in
source-line order, which is the human-friendly diff order. The order is
re-verifiable from each line's own provenance fields (validator-enforced).

**Canonical JSON.** One recipe, pinned by test and used nowhere else:
`json.dumps(model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
separators=(",", ":"))` + `"\n"` per line; UTF-8, no BOM, LF only (written as
encoded bytes — never platform text mode), one trailing newline at EOF. No float
fields exist in a line (labels are enums, counts are ints), so repr instability is
structurally absent. `ensure_ascii=False`: the corpus is French; UTF-8 bytes of
accented text are deterministic and readable.

### 2. meta.json — content, embedded reports, and where volatility lives

**A. Flat meta.json mixing git SHA / timestamp with thresholds.** Cons: makes the
whole file un-byte-diffable; `/repro-audit` would need field-by-field exceptions —
the exception list *is* the bug surface. Rejected.

**B. No volatile data at all (fully deterministic meta.json).** Cons: provenance
without the git SHA and generation time fails the CLAUDE.md §1 promise (git SHA is
named there explicitly); honesty about *when/from what tree* an artifact was built
is part of the story. Rejected.

**C. Two top-level sections (chosen):** `{"deterministic": …, "volatile": …}`.
Everything under `deterministic` is a pure function of inputs + config. `volatile`
carries `git_commit` (or null → rendered "unrecorded", the ADR-0004 M-1 honesty
convention), `generated_at` (UTC ISO-8601), and `environment` (python/platform/
package versions — recorded because *useful*, quarantined because *volatile*;
determinism of the content is owed by tests, not by pip pins). The composition
layer collects volatile values and passes them in; no function under
`src/evalgen/export/` may touch `datetime.now`, `time`, `uuid`, `subprocess`, or
git (grep/AST-pinned).

**Deterministic-section content:** `export_format_version` (int constant, bumped
only by ADR); a **typed `SettingsSnapshot`** of every active knob (the config.py
docstring promise "the provenance writer copies the active settings into meta.json"
— typed, not a loose dict; a mirror test pins its field set equal to `Settings`'s so
a new knob cannot silently skip provenance); `input_files` — basename + SHA-256 +
role (`source_log` / `few_shot_store` / `human_labels`) for EVERY file read by the
run; the `EmbedderFingerprint`; and the **entire report chain embedded as models**:
per-source `IngestReport`s, `DedupReport`, `ClusteringReport`, `SamplingReport`,
`LabelingReport` (carries the `JudgeFingerprint`), `AgreementReport`, and the new
`ExportReport` (carries the gate decision); plus `golden_jsonl_sha256` — meta.json
names the exact bytes of the dataset it certifies (the M-1 binding pattern, applied
to our own output).

**Embed the whole AgreementReport or a summary? — decided: WHOLE.** A summary model
is a second copy of the truth that can drift, and it forfeits the property that
decides this: every embedded report **re-runs its validators on deserialization** —
an edited κ, a doctored confusion matrix, a flattered support table in meta.json
*refuses to parse*. Tamper-evidence is worth the bytes (tens of KB at any realistic
scale). Consequence accepted and stated in §1: the drill-down reveals human labels
for disagreeing pairs.

**The ADR-0004 Amendment (a) duty, made structural:** the manifest validator
requires exactly one `human_labels` input file and refuses to exist unless its
digest **equals `agreement.human_labels_sha256`** (which must be non-None — see
gate check 4). The duty is not "remember to copy" — an inconsistent copy is
unrepresentable.

**The funnel validator.** The manifest cross-checks the chain across its own
embedded reports: Σ ingest `records_normalized` = dedup `records_in`;
dedup `records_out` = clustering `records_in` = sampling `records_in`;
sampling `total_sampled` = labeling `records_in`; labeling `labeled` = export
`candidates_in`; and every knob echo (threshold, min_cluster_size, seed, budget,
B, gates, `min_export_kappa`) equals the `SettingsSnapshot` claim. A meta.json
whose reports do not form one coherent run refuses to deserialize.

**Serialization:** same canonical recipe as §1 but `indent=2` (meta.json is for
humans too), sorted keys, LF, trailing newline. All report floats are already
rounded to 6 decimals at their model boundaries (ADR-0002/0004 discipline);
`json.dumps` of those exact values is deterministic.

### 3. The contamination guard

**A. Trust the labeling-time gate.** The engine already skips any record whose
canonical hash is in the judge's `few_shot_content_hashes` (ADR-0003 rule 6), so no
collision can be labeled, so none can be a candidate. Cons: "trust" — a forged or
hand-assembled `LabelingOutcome`, or a bug, would ship the answer key with no second
fence exactly where the blast radius is maximal.

**B. Re-check at export against the store on disk.** Cons: the store *now* is not
the store the judge *saw*; gating on it conflates contamination with drift (drift
is gate 4's job).

**C. Defense in depth against the fingerprint's own hash set (chosen).** Export
recomputes the canonical `content_hash` of every candidate and blocks any whose
hash appears in `labeling.judge.few_shot_content_hashes` — the set the judge
actually saw — as a typed `BlockedCandidate(cause=fewshot_collision, detail=names
the colliding content hash)`, counted, never silent. *Amended 2026-08-01
(red-team MAJOR-1): originally "names the colliding few-shot id" — impossible
from the fingerprint; see Amendment (a).* On an honest run this bucket is
structurally 0 (the engine skipped them first); a nonzero count is a five-alarm
signal preserved as data. **And the ∅-intersection is a contract, not a code
path:** the `ExportOutcome` validator refuses to construct any outcome where an
exported record's `content_hash` intersects the fingerprint's few-shot hashes — a
contaminated export is unrepresentable, even deserialized. The
composition layer additionally asserts store-on-disk hashes == fingerprint hashes
before exporting (a drifted store fails loudly at the seam, not silently at gate 4).

**Candidate identical to an annotation-template example — weighed, no gate.** The
template's display copies ARE the sampled records (ADR-0004 rule 2): every
human-labeled record "matches the template" by construction. The human is *supposed*
to see the record; the judge never sees the template or its answers, and human
labels never enter golden.jsonl (§1). There is no leakage direction to gate;
inventing one would block the entire matched subset. Rejected, stated here so it is
never "discovered" later.

**Near-paraphrase few-shot leakage** (cosine check at the export gate, reusing the
calibrated near-dup machinery) stays explicitly deferred, unchanged from ADR-0003:
the store is 5 handwritten items, hash identity covers verbatim reuse, and the
threshold protocol (ADR-0002 rule 4) has not yet run on real labeled pairs.

### 4. The κ gate — checks, boundary, straddle, override

The gate is a pure function `(AgreementReport, LabelingReport, min_export_kappa,
override) → ExportGateDecision`, five named checks in pinned order, each a typed
`GateCheck(name, passed, detail)`:

| # | check | blocks when | overridable |
|---|---|---|---|
| 1 | `headline_ready` | `AgreementReport.headline_ready` is false (n < min_human_labels) | **no** |
| 2 | `headline_status` | headline present but `status != ok` (ADR-0004 Amendment (d): an undefined κ blocks exactly like a missing one) | **no** |
| 3 | `instrument_binding` | `agreement.judge != labeling.judge` — full `JudgeFingerprint` equality (model, taxonomy id, prompt sha256, few-shot ids + hashes): a κ measured on instrument A certifies nothing about instrument B; taxonomy equality is entailed (the fingerprint carries it, and `AgreementReport` already pins `taxonomy_id == judge.taxonomy_id`) | **no** |
| 4 | `ground_truth_bound` | `agreement.human_labels_sha256` is None — an export-grade κ must be replay-verifiable against exact ground-truth bytes (M-1); a hand-assembled unbound report cannot certify a shipped dataset (meta.json could only print "unrecorded" where its strongest binding belongs) | **no** |
| 5 | `kappa_threshold` | headline κ < `min_export_kappa` | **yes — the only one** |

- **The gated number is the report-stored (6-decimal-rounded) κ** — the number on
  the report's face is the number that gates; a gate on invisible full-precision
  digits would be unauditable. Consequence accepted: a true 0.5999996 rounds to
  0.6 and passes — the same rounding contract every report already lives under.
- **Boundary:** blocks iff `kappa < min_export_kappa` — `kappa == 0.6` passes
  (config docstring: "below this value blocks"), pinned by a boundary test pair.
- **CI95 straddle — stated, never blocking** (ADR-0004 options §6): when the gate
  passes on the point estimate but `ci95.lower < min_export_kappa`, the decision
  carries `ci_straddles_threshold = True` and the renderer + meta.json print it:
  a gate passed on a straddling interval is passed *stated*. An unavailable CI
  (all resamples degenerate) is likewise stated next to the verdict.
- **Override mechanism — explicit, singular, loud.** A typed
  `ExportGateOverride(reason: non-empty str)` passed at the seam (the future CLI
  surfaces it as a *named* flag `--allow-low-kappa "<reason>"` taking the reason as
  its mandatory argument; no env var, no default). Scope: check 5 **only** — checks
  1–4 mean the κ does not exist or does not apply; there is no honest low number to
  carry, so there is nothing to override. Verdict is a closed enum: `passed` /
  `passed_with_override` / `blocked`. The decision validator refuses (a) an
  override when nothing failed (an override must override something — no ritual
  flags), (b) an override alongside any non-overridable failure, (c) any verdict
  that disagrees with its own checks. `blocked` ⇒ `run_export` raises a typed
  `ExportBlockedError` carrying the decision and **writes nothing** — no partial
  artifacts, ever. *Amended 2026-08-01 (red-team MINOR-2): "no partial artifacts"
  is per-file atomic + pair-staged; the residual two-rename window is stated in
  Amendment (c).* `passed_with_override` ⇒ meta.json and the text report carry
  the reason and the honest low κ on their face.
- **Forgery-proof at the artifact level:** the `ExportManifest` validator
  *recomputes all five checks, the straddle flag and the verdict* from its own
  embedded `AgreementReport` + `LabelingReport` + `SettingsSnapshot.min_export_kappa`
  and refuses any divergence — a meta.json whose gate section lies about its own
  reports refuses to deserialize.

### 5. Assembly and the offline `make export`

**A. Umbrella CLI now.** Still premature for the *golden-pinned* proof: a CLI takes
arguments, and arguments are variance (ADR-0002 rule 9). The CLI lands as the
real-data wiring (explicit flags: source paths, `--judge anthropic`,
`--labels data/labels/human_labels.jsonl`, `--allow-low-kappa <reason>`,
`--out DIR`; **never autodetection** — ADR-0004 options §7) — deferred to its own
slice with `pipeline-engineer`, contracts frozen here so it cannot re-decide
anything.

**B. `export_demo.py` composition module (chosen), `make export` runs it.**
Zero-arg sibling of `agreement_demo.py` (wiring deliberately duplicated —
composition layers repeat wiring, they own no logic): re-run the fixture pipeline
(ingest → dedup → cluster → sample → FakeJudge labeling, byte-identical by
determinism), load the synthetic annotations, `compute_agreement` (same wiring as
`agreement_demo`, same sha256 binding), then `evaluate_export_gate` — which
**genuinely blocks** (κ = 0.513109 < 0.6) — then export **with the explicit
override** (reason names the truth: FakeJudge κ is hash-derived noise; the
override exists to exercise the full path offline). Writes
`data/out/golden.jsonl` + `data/out/meta.json` (runtime Python writes to a
gitignored dir — the sanctioned path; the hook only blocks agent-tool writes) and
prints one deterministic text report opening with the mandatory
**!! SYNTHETIC** banner. The golden thereby proves, in one committed byte-exact
artifact: the gate table with the real failing check, the loud override, the
contamination count (0 at export, 1 named collision already excluded at labeling),
the funnel counts (49 candidates = 49 exported + 0 blocked), and the two digests.
The demo's volatile section carries real values (honest even for a demo) — the
*text render never prints volatile values* (it prints a fixed "recorded, not
rendered" note), so the golden stays byte-stable.

**Goldens (all outside the hook's protected namespaces, per the fixture-naming
rule):** `tests/golden/export_output.txt` (the text report),
`tests/golden/export_dataset.txt` (the exact golden.jsonl content — readable diffs
when it breaks), `tests/golden/export_meta.txt` (the canonical deterministic-section
bytes). The text report *also prints* `golden.jsonl sha256=…` and
`meta.json deterministic sha256=…`, so the one text golden transitively pins both
artifacts. The demo and agreement goldens are **byte-untouched** this phase
(nothing upstream changes — same rule as Phase 4).

### 6. What `/repro-audit` byte-compares

- **`golden.jsonl`: the entire file**, byte-for-byte — it is 100 % deterministic by
  construction (no volatile field exists in a line).
- **`meta.json`: the `deterministic` section only**, via one public function
  (`canonical_deterministic_bytes(meta_text)` — parse, extract `deterministic`,
  re-dump with THE canonical recipe): the audit uses the same code path as the
  writer, so there is no second serializer to drift. The `volatile` section is
  excluded *by construction of the layout*, not by a field-exception list.
- **`export_output.txt`**: entire file vs the committed golden (double-run identity
  plus golden equality, as for the demo and agreement outputs).

## Decision

### Rule 1 — Contracts (`contracts/export.py`, all frozen, validators run on deserialization)

`GoldenRecord` (line schema of Options §1; recomputes `record_id` +
`content_hash` from its own fields — a forged line refuses to exist);
`RecordProvenance`; `BlockedCause` (`fewshot_collision`) + `BlockedCandidate`;
`GateCheckName` (5 members, Options §4 order) + `GateCheck` +
`ExportGateOverride` + `ExportGateVerdict` + `ExportGateDecision`
(self-coherent per Options §4); `ExportReport` (fingerprint, decision,
`candidates_in == exported + blocked`, blocked sorted/unique, `exported ≥ 1`,
verdict never `blocked` — a report only exists for a run that exported);
`ExportOutcome` (records ↔ report cross-checks; canonical order re-verified from
line provenance; taxonomy ids pinned to the fingerprint; **export ∩ few-shot
hashes = ∅ enforced by validator**); `InputFileRole` + `InputFileDigest`;
`SettingsSnapshot` (field-set mirror of `Settings`, pinned by test);
`VolatileProvenance`; `ExportManifest` (Options §2: the M-1 copy duty, the funnel
validator, the knob echoes, the gate recomputation, `golden_jsonl_sha256`);
`EXPORT_FORMAT_VERSION = 1`.

### Rule 2 — The gate (`export/gate.py`)

`evaluate_export_gate(agreement, labeling, *, min_export_kappa, override=None) ->
ExportGateDecision` — pure, five checks, rounded-κ comparison, `>=` passes,
straddle stated, override scope = check 5 only (Options §4). Knobs injected by the
composition layer, never imported from config (the `validate/` precedent).

### Rule 3 — Assembly (`export/assemble.py`)

`assemble_export(records, labeling, clustering, decision) -> ExportOutcome`:
refuses a `blocked` decision (`ExportBlockedError` carrying the decision — callers
cannot forget the gate because assembly will not run without a non-blocked
decision); candidates = the run's `labeled_examples`; unresolvable `record_id` or
missing stratum assignment is a typed `ExportInputError` (caller bug, never a
statistic); collision candidates → `BlockedCandidate`; zero exported →
`NothingToExportError`; golden records in `record_sort_key` order.

### Rule 4 — Serialization (`export/serialize.py`) and the writer (`export/writer.py`)

`render_golden_jsonl(outcome) -> str` and `render_meta_json(manifest) -> str`
implement the two canonical recipes of Options §1/§2 — pure string producers,
byte-tested. `canonical_deterministic_bytes(meta_text) -> bytes` is the shared
`/repro-audit` path. `write_export(out_dir, golden_text, meta_text)` is the ONLY
file-writing module in `export/` (bytes, UTF-8, LF — never platform text mode).
`export/render.py::render_export_report(outcome, manifest) -> str` renders the
deterministic text (banner-ready, digests included, volatile never printed).

### Rule 5 — Boundaries

`export/*` imports **only `contracts`** (+ stdlib) — AST-walked at every depth like
`label/`/`validate/`; no `anthropic`, no numpy, no config, no clock/git/uuid
(grep-pinned); `writer.py` is the sole module with write operations (grep-pinned,
the inverse of `validate/`'s no-write rule). `export_demo.py` is composition:
imports the pipeline, nothing imports it. Nothing imports `export` except
composition layers — the CLAUDE.md dependency arrow, enforced.

### Rule 6 — `make export`

`make export` = `python -m evalgen.export_demo` (Options §5), golden-pinned by the
three committed goldens. The Makefile placeholder is replaced; `make demo` and
`make agreement` targets and goldens are byte-untouched.

## Consequences

**Positive:** the exported dataset is self-verifying line-by-line (ids and hashes
recompute) and file-by-file (meta.json names the golden's digest and refuses to
coexist with reports it disagrees with); the κ gate is implemented exactly as
ADR-0003/0004 promised, with a boundary-tested threshold, a stated straddle, and an
override that is typed, scoped, reasoned, and printed; contamination is
unrepresentable in a valid `ExportOutcome`, not merely filtered; the provenance
story is one coherent validated chain from `lines_read` to `exported`; volatility
is quarantined so `/repro-audit` byte-diffs without exception lists; the offline
proof commits the gate *actually blocking* on the committed fixtures and the
override *actually shouting*.

**Negative (accepted):**

- **The demo's committed golden shows an overridden gate** — a reader could mistake
  override for routine. Mitigated: the SYNTHETIC banner, the reason text naming the
  noise, and the README rule (synthetic numbers are never findings) all travel with
  it; the alternative (a demo that writes nothing) would leave the writer,
  serializer, and `/repro-audit` unproven.
- **Judge rationale/confidence ride every line** — bulkier dataset, and a consumer
  *could* filter by confidence, recreating the self-grading bias downstream. Stated
  in the schema notes; the κ they hold was measured unfiltered.
- **The disagreement drill-down inside meta.json reveals the human label for
  disagreeing pairs** (Options §2). Accepted as the honesty artifact; a fresh
  subset is required after any judge change regardless.
- **Gate compares the rounded κ** — a knife-edge value passes/fails by its printed
  6-decimal form. Deliberate: the auditable number is the gated number.
- **κ certifies the instrument, not each corpus** — fingerprint equality cannot
  prove the human subset was drawn from the same distribution as a future export's
  corpus; the matched-pair id list is not carried in the report (size). Stated;
  the designed flow (CLI wires one run end-to-end) makes them identical in
  practice, and both reports ride meta.json so divergence is inspectable.
- **Whole-report embedding makes meta.json tens of KB** — trivial at this
  scale; the revisit trigger is a corpus where `DedupReport` entry lists dominate
  (already ADR-0002's documented scale limit).

**Explicitly deferred:** the umbrella real-data CLI (`python -m evalgen …`,
explicit flags incl. `--allow-low-kappa`, template emission, real judge selection —
contracts frozen here); near-paraphrase few-shot gating (cosine, after the
threshold protocol runs on real pairs); dataset packaging niceties (splits,
HuggingFace card) — out of v1 scope; per-line license/consent fields (no such
source exists yet).

**Validated by (the Phase 5 test battery — detailed in the design handoff):**
forged-line refusals (wrong id, wrong hash, edited text); `ExportOutcome`
contamination validator (deliberately-leaked fixture: a labeled collision candidate
must land in `blocked` with the colliding content hash named — *amended 2026-08-01,
see Amendment (a)* — and a hand-forged outcome
carrying it among exported refuses to exist); gate unit tests per check (each
failing alone), boundary pair (0.6 passes / 0.599999 blocks), straddle fixture
(κ = 0.65, lower = 0.55 → passes with the statement), unavailable-CI statement,
override refusals (no-op override, override on non-overridable failure) and the
`blocked ⇒ nothing written` tmp_path test; manifest cross-check refusals (M-1 digest
mismatch, fingerprint mismatch, funnel break, knob-echo mismatch, gate-section
forgery at 1 ulp); canonical-serialization pins (accented bytes, separators,
trailing newline, LF) + double-run byte-identity of all three rendered artifacts;
`SettingsSnapshot` ↔ `Settings` field-set mirror; the AST/grep boundary battery
(contracts-only imports, no clock/git/uuid, writer-only writes); export_demo golden
byte-equality + double-run identity + banner-first + no absolute-path/secret leak +
`data/out/` files byte-equal to the rendered strings; demo and agreement goldens
byte-untouched.

## Amendment (2026-08-01) — pre-commit red-team pass closures

The adversarial review returned 0 blockers / 1 major / 2 minors (report:
`.workflow-handoff/redteam.md`). All three are closed **now** — the MAJOR was a
false provenance string on the repo's own honesty axis, and both MINORs cost a few
lines each. Each fix ships with a regression test replaying the red team's own
payload. No golden changes: every finding lives off the committed-golden path
(blocked > 0, forged files, crash windows).

**(a) MAJOR-1 — a blocked collision names the colliding CONTENT HASH, never a
few-shot id.** Options §3 promised `detail` "names the colliding few-shot id" and
the implementation resolved it by `zip`-ing the fingerprint's
`few_shot_content_hashes` with its `few_shot_ids`. The red team proved those two
tuples are sorted **independently** (id = `sha256(content ␟ verdict)[:16]`, hash =
`sha256(content)`, unrelated digest orders): on the real committed 5-item store,
5 of 5 hashes zip to the WRONG id — a blocked collision's evidence string pointed
an investigator at the wrong gold example. Decision: the fingerprint carries no
id↔hash pairing, so **no honest id can be named from it**. The detail now names
what the fingerprint CAN prove — the candidate's canonical `content_hash` and its
membership in the judge's few-shot hash set (`content_hash <hex> is in the judge's
few-shot set (N hashes)`); the true owner is recoverable exactly by hashing the
store (`FewShotExample.content_hash`), which is how any consumer re-runs the
contamination check anyway (Options §1). Alternatives rejected: naming the id from
the store on disk would attribute evidence via data the gate deliberately does not
read (the store *now* is not the store the judge *saw* — Options §3 option B's own
argument); enriching `JudgeFingerprint` with an id↔hash pairing is a cross-module
contract change buying nothing the hash does not already prove. Regression: the
mispairing is pinned on the committed store (the zip map must disagree with the
true `{content_hash: few_shot_id}` map), and an end-to-end ≥2-shot collision
(`fs-966cc1d5fa0d0d00`'s content, whose 2-element zip names
`fs-46dacbf301812eec`) asserts the detail carries the true hash and **no** `fs-`
id (`tests/test_export_assemble.py::TestRedTeamMajor1Payload`).

**(b) MINOR-1 — the line self-verification claim is scoped to what it covers.**
"A tampered or hand-forged golden line refuses to parse" (Options §1, the
contracts docstring) overclaimed: `GoldenRecord` recomputes `record_id` +
`content_hash` from **origin + input_text + output_text only**. The four label
fields (`task_type`, `outcome`, `judge_confidence`, `judge_rationale`) and
`metadata` are the judge's *opinion* — not derivable from the line, so per-line
self-verification of them is impossible **by construction**, and a label-flipped
line validates cleanly (red-team P2/P3). Decision: no new hash (there is nothing
to derive a label from); the claim is scoped everywhere it appears — identity +
texts self-verify per line; **label integrity rests on the file-level fence**:
`meta.json` binds `golden_jsonl_sha256` to the exact bytes and `/repro-audit`
regenerates + byte-diffs, so a forged file can never match the manifest it shipped
with, and a coherently re-hashed forged *pair* cannot survive regeneration.
Regression: the label-flip boundary is pinned (flip validates at line level —
`tests/test_contracts_export.py`), and the fence is proven to fire on the exact
payload (flipped file digest ≠ bound digest, regeneration reproduces the bound
digest; the recomputed-sha manifest parse is pinned as the *stated residual* —
`tests/test_export_serialize.py::TestRedTeamMinor1Payload`).

**(c) MINOR-2 — the pair is staged before either rename.** `write_export` wrote
golden.jsonl (temp + replace) then meta.json (temp + replace): each file atomic,
the pair not — a crash between the two replaces left a new dataset beside a stale
or missing provenance, plus a leftover `*.tmp`. Decision: **stage both temps
fully, then rename both**; any staging failure unlinks both temps and re-raises,
leaving the previously published pair byte-intact. The residual window shrinks to
the two rename syscalls and is **stated, not hidden**: a torn pair is always
detectable because `golden_jsonl_sha256` binds meta.json to the exact golden
bytes (a mixed-generation pair cannot digest-match), and `data/out/` is
regenerated by construction. Regression: a simulated crash while staging the
second artifact leaves the previous pair byte-identical and no temp behind
(`tests/test_export_writer.py` — fails on the pre-fix writer, which had already
replaced golden.jsonl).
