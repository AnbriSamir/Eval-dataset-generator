# ADR-0004 — The agreement protocol: human labels, Cohen's κ (global + per-class), paired bootstrap CI95, and the honest report

**Status:** Accepted (2026-07-31) — amended 2026-08-01 after the pre-commit
red-team pass (see Amendment: (a) M-1 report-level ground-truth binding,
(b) M-2 report-level support gate, (c) NIT dispositions, (d) Phase 5 export-gate
clarification) — amended 2026-08-04 after the real-agreement-CLI red-team pass
(see Amendment: (e) F-1 annotation-directory separation, (f) F-2 content-based
synthetic detection, (g) F-3 per-run real-judge audit trail)

## Context

Phase 4 measures the instrument Phase 3 built: the judge's labels against a
human-labeled subset, per axis, with Cohen's κ and a bootstrap CI95 — the repo's
headline number. Everything published in the README flows through this phase, so the
failure modes here are not bugs, they are *credibility-destroying measurement
artifacts*:

1. **An anchored human.** A human who labels while seeing the judge's verdict is
   anchored (a well-documented annotation bias); the resulting κ measures compliance,
   not agreement. The judge is already structurally blind to human labels (ADR-0003
   rules 3/9); the reverse direction has no guarantee yet.
2. **The denominator lie, join edition.** Humans label records the judge refused;
   the judge labels records no human reached. A join that silently drops either side
   makes "κ = 0.71 (n = 42)" unverifiable — and refusals correlate with hard cases,
   so dropping them silently is cherry-picking (ADR-0003 failure mode 3, measured
   here instead of created here).
3. **Degenerate κ shipping as a number.** When both raters use a single identical
   class, p_e = 1 and κ = 0/0 — *undefined*. sklearn emits NaN with a warning;
   naive code emits 0. Both are lies: 0 claims "chance-level agreement" about a
   situation where agreement above chance is unmeasurable. Same trap per class
   (a class absent from both raters), and inside every bootstrap resample.
4. **An unpaired bootstrap.** Resampling the two raters independently destroys the
   pairing and fabricates variance — a wrong interval, not a conservative one
   (stats-scientist brief). The unit of resampling must be the *pair*.
5. **κ gaming.** Shrinking the human subset until κ looks good; filtering by judge
   confidence; collapsing classes after seeing disagreements; comparing κ across
   different taxonomies. Each must be structurally refused, not policied.
6. **Forgeable ground truth.** An agent that can write `data/labels/human_labels*.jsonl`
   can fabricate its κ. The hook blocks agent writes (exit 2); this ADR must keep every
   synthetic/test label file *out* of that namespace so the protection stays meaningful.

**Ground truth this ADR is built on** (re-read, not assumed): `LabeledExample`
carries `record_id`, `taxonomy_id`, `model_id`, and the full `JudgeVerdict`
(task_type, outcome, confidence, rationale) — everything the drill-down needs;
`LabelingOutcome.report` carries the `JudgeFingerprint` and the five-bucket
accounting including per-record refusal/failure/budget/collision ids — everything
the join-loss classification needs. `TAXONOMY_V1` (`tax-d9ca3b87b403`) has two
unconditional axes: `task_type` (5 classes) and `outcome` (4 classes). Config:
`min_human_labels = 30`, `bootstrap_resamples = 10_000`, `seed = 1750`,
`min_export_kappa = 0.6`. The demo labels 49 of 50 sampled records (1 planted
few-shot collision, `rec-d1087e0ca3da3367`). The hook blocks any basename starting
with `human_labels` and any `golden*`/`human_labels*` JSONL under a data directory.

## Decision drivers

- Same inputs → byte-identical `AgreementReport` including bootstrap intervals; the
  seed and B come from config and are recorded in the report (CLAUDE.md §5).
- Tests 100 % offline; every formula verified on hand-computed fixtures — a wrong κ
  must fail a test, not ship.
- κ is never a naked number: n, per-class support, CI95, degenerate-resample count,
  taxonomy id, and judge fingerprint travel with it or it does not travel.
- Nothing dropped in silence: every human label and every judgment lands in
  *matched*, *human-only (classified by cause)*, or *judge-only* — sums enforced by
  a self-validating report.
- `validate/` only measures: it never modifies labels or judgments, and it writes no
  files. `validate → contracts` only (module-boundary rule; the judge stays blind in
  the other direction, pinned since Phase 3).
- Human ground truth is produced by a human outside any agent (hook-enforced);
  synthetic fixtures live under names the hook does not protect and say so on their
  face.

## Options considered

### 1. The human-label workflow and the blindness question

**A. Humans label in a spreadsheet / ad-hoc format, converted later.** Cons: a
conversion script is an agent-writable path into ground truth, and format drift
(free-text class names, locale dates) lands exactly where errors are most expensive.
Rejected.

**B. A generated JSONL template the human fills in place (chosen).** A pure renderer
produces one JSON line per sampled record — `record_id`, `taxonomy_id`, the two
*empty* label fields, empty `annotator`, plus the (post-redaction) `input_text`/
`output_text` as display copies — and a companion instructions document rendered
from `TAXONOMY_V1` (the *same* questions and class definitions the judge prompt
uses: one questionnaire, two annotators — ADR-0003 rule 1's promise, honored here).
The human fills the blanks in an editor, outside any agent, and saves as
`data/labels/human_labels.jsonl`. An unfilled row (`"task_type": ""`) fails enum
validation at load time naming its line — an incomplete file *cannot* be measured
accidentally. The display-text fields are ignored by the loader by declaration
(`extra="ignore"`): record text has exactly one source of truth (`LogRecord`, joined
by `record_id`), so a human accidentally editing a display copy cannot alter what κ
is computed on.

**C. A per-record interactive CLI labeler.** Nicer UX, but it *writes* the protected
file from code an agent could run, and it adds an interactive surface to test.
Deferred — the template is the v1 contract; a labeler UI can emit the same format
later without touching this ADR.

**Blindness — decided: double-blind, structurally on our side.** The template
renderer's signature is `render_label_template(records, taxonomy)` — it *cannot
receive* judgments, so no judge verdict, confidence, or rationale can appear in what
the human fills (the mirror of ADR-0003's two-string `Judge` Protocol). The judge
remains blind to humans by the Phase 3 guarantees. Residual stated honestly: we
cannot technically stop a determined human from reading a labeling report before
annotating; the structural guarantee covers the artifacts, and the instructions
open with "label independently; do not consult the judge's output". Anchoring
inflates agreement — an inflated κ is precisely the number this repo refuses to
ship.

**Annotator identity:** each line carries a free-form `annotator` (pseudonym, never
an email — ingestion-grade PII discipline applies to what we *publish*) and an
optional `labeled_on` date. v1 measures "the human" as one rater; if a file mixes
annotators, the report lists them and the caveat travels (inter-human κ is deferred
until a second annotator actually exists — Fleiss/Krippendorff territory, different
statistic).

**No content-derived id on `HumanLabel` — deliberate.** House style hashes ids from
content (`rec-`, `cl-`, `tax-`, `fs-`), but a self-verified hash on human labels
would force humans to compute hashes by hand (or hand agents the writing job the
hook exists to block). Tamper evidence for ground truth is the hook + git history +
the human's own review — stated, not hidden. *Amended 2026-08-01 (red-team M-1):
the per-label decision stands, but the REPORT now binds to the whole file — see
Amendment (a).*

### 2. The agreement statistic

**A. Raw percent agreement.** Ignores chance agreement — with skewed marginals (a
judge that answers `correct` 80 % of the time) it flatters. Reported as p_o inside
the κ block, never alone. Rejected as the headline.

**B. Scott's π / Krippendorff's α.** Pool the marginals (π) or generalize across
raters/missing data (α). Krippendorff's α would be the tool for >2 annotators with
gaps; we have exactly two raters (judge, human) fully paired after the join, and the
repo's public claim (CLAUDE.md, README, agent briefs) names Cohen's κ. Deferred with
the multi-annotator workflow.

**C. Cohen's κ, unweighted, per axis (chosen).** Two fixed raters, nominal classes,
the standard the claim already names. Computed *separately* for `task_type` and
`outcome` — the axes are different questions; mixing them into one κ would average
away exactly the per-axis diagnostic the flywheel needs.

**Weighted κ (linear/quadratic) for `outcome`?** Tempting (`correct` >
`partially_correct` > `incorrect` is ordinal) — rejected for v1: `unjudgeable`
breaks the ordinal scale (it is orthogonal to quality, not "worse than incorrect"),
and quadratic weights are the classic flattering choice an adversarial reviewer
attacks first. Unweighted is the conservative reading; the per-class table and the
confusion matrix carry the granularity instead. Documented revisit: a linear-weighted
κ over the three ordinal classes only, as a *secondary* diagnostic, never the
headline.

### 3. Per-class κ — which definition

**A. Per-class accuracy / F1.** Not chance-corrected; a judge spamming the majority
class scores high. Rejected.

**B. Conditional κ (restrict to items the human labeled c).** Conditions away the
false-positive direction (judge says c, human didn't) — half the confusion.
Rejected.

**C. One-vs-rest binarized κ per class (chosen)** — the standard: collapse the k×k
confusion matrix to 2×2 (class c vs rest) and compute the same Cohen's κ on it. One
formula total: per-class κ *is* global κ on a collapsed matrix, so a single
`kappa_from_confusion` function serves both — hand-check one function, trust every
number (formulas in the Decision).

### 4. Degenerate cases and the support policy

Enumerated one by one — each is a typed status, never a NaN, never a silent 0, never
a dropped row:

| Case | Detection (exact, integer) | Verdict |
|---|---|---|
| p_e = 1 globally (both raters single-class, same class) | Σᵢ rᵢ·cᵢ == n² | κ **undefined** — status `undefined_single_class`, no number, reason printed. NOT 0 (0 claims chance-level agreement; here agreement-above-chance is unmeasurable). |
| Class absent from both raters | row_c + col_c == 0 | per-class status `absent`, supports 0/0 shown, no κ (mathematically the p_e=1 sub-case where d = n). |
| Class universal in both raters (a = n) | a == n | per-class `undefined_single_class` (co-occurs with the global degenerate case by construction). |
| Class present on exactly one side | row_c = 0 xor col_c = 0 | κ_c is **defined and exactly 0** (proof in the Decision) — reported with supports (e.g. 0 vs 20), which is itself a flywheel finding ("the judge uses a class the human never does"). Subject to the support gate like any class. |
| Support insufficient | 0 < row_c + col_c < `min_class_support` | status `insufficient_support`: the row appears with both supports, the κ value is **suppressed** (a κ on 3 occurrences is noise wearing a number's clothes). Never silently dropped — suppression-with-status is the anti-cherry-picking guarantee. |
| Perfect agreement, p_e < 1 | diag == n and Σrᵢcᵢ < n² | κ = 1 exactly (falls out of the formula; pinned by a fixture). |
| n_matched < `min_human_labels` (30) | join count | The report is still produced (diagnostics must survive), but `headline_ready = false` — a validator-enforced field — and the renderer prints "NOT REPORTABLE: n=… < min_human_labels=30" instead of a headline block. Phase 5's export gate treats a missing headline as blocking. |
| n_matched = 0 | join count | Typed `NoMatchedPairsError` — a report with n = 0 is a lie machine; there is nothing to diagnose. |

**The support threshold** is a new config knob, `min_class_support = 5` (bounded
`ge=1`), gating on `row_c + col_c` over *matched pairs* (human support + judge
support; the diagonal counts on both sides — declared, it is a gate, not a
statistic). Why 5: below ~5 total occurrences the binarized 2×2 has so little mass
off `d` that the bootstrap CI spans most of [−1, 1] — printing that next to real
numbers invites misreading. Measured honesty beats fake precision. At the config
scale (n = 30–50, 4–5 classes/axis, unconditional axes by ADR-0003 design), balanced
classes clear the gate comfortably; only genuinely rare classes get suppressed, with
their rarity printed.

**Two structural theorems worth pinning (anti-gaming):** (a) a single-class judge
scores κ = 0 exactly against any non-single-class human — "always say correct"
earns zero, not a flattering number; (b) one-side-absent classes score κ_c = 0
exactly. Both are consequences of p_o = p_e in those configurations; both get
hand-computed fixtures.

### 5. The CI95 — method and degenerate resamples

**A. Analytic large-sample SE (Fleiss–Cohen–Everitt).** Asymptotic; at n = 30–50
with skewed marginals the normal approximation is exactly what a reviewer would
question, and per-class binarized tables make it worse. Rejected.

**B. BCa bootstrap.** Second-order accurate in theory. In practice on discrete
paired labels at n ≤ 50: the bias-correction z₀ = Φ⁻¹(#{κ*_b < κ̂}/B) is unstable
under the heavy ties a 4-class κ produces (many resamples equal κ̂ exactly — the
strictly-less-than fraction jumps), and it degenerates at the boundary (κ̂ = 1 ⇒
all resamples 1 ⇒ z₀ = Φ⁻¹(0) = −∞ needs special-casing). Its machinery (jackknife
acceleration, Φ⁻¹) cannot be hand-checked on paper the way this repo checks
formulas. Rejected: the honest caveat at these n is *small n itself*, which we
print; second-order refinement does not buy credibility here, auditable arithmetic
does.

**C. Percentile bootstrap on paired resamples (chosen).**
- Unit of resampling: the **(human, judge) pair**, i.e. the matched record. One
  index matrix of shape (B, n) — `B = bootstrap_resamples = 10_000`, n = matched
  pairs — drawn in a single call from `numpy.random.Generator(numpy.random.PCG64(seed))`
  with the config seed (1750). Never `np.random` global state. The matrix depends
  only on (seed, B, n), so adding a metric never changes the draws, and the *same*
  resampled worlds serve every metric (both axes, global + per-class): comparable
  CIs, one source of randomness, byte-identical across runs.
- Per resample b: rebuild the confusion matrix from the resampled pairs and apply
  the same `kappa_from_confusion`. Bounds = 2.5th and 97.5th percentiles of the
  valid κ*_b values, `numpy.percentile(…, method="linear")` — the method is pinned
  by name so a numpy default change can never silently move a published interval.
- **Degenerate resamples** (p_e = 1 inside the resample — possible whenever a
  resample lands entirely on one agreeing cell): the statistic *does not exist*
  there, so the resample is **excluded from the percentiles and counted** —
  `b_degenerate` travels in the report next to the interval, and the renderer adds
  a caveat line whenever it is nonzero. Mapping them to 0 or 1 would inject
  invented mass at an arbitrary value; dropping them uncounted would hide that the
  data sits near a degenerate boundary — exactly when the reader must be told. If
  *all* B resamples are degenerate (only possible in near-degenerate data), the CI
  is reported as unavailable with the count, never as [NaN, NaN].
- Consequence accepted: percentile intervals can slightly undercover at these n;
  the caveat is printed. The whole computation is hand-checkable: the tests pin a
  CI computed on paper from a hand-built 5×4 index matrix.

### 6. Interpretation and where the gate lives

- **Landis–Koch bands** (< 0 poor; 0–0.20 slight; 0.21–0.40 fair; 0.41–0.60
  moderate; 0.61–0.80 substantial; 0.81–1.00 almost perfect) are printed next to
  each defined κ as a *descriptive convention*, labeled as such ("Landis & Koch
  1977 descriptive bands — a reading aid, not a test"). Band edges are pinned by
  test (κ = 0.60 → "moderate", 0.61 → "substantial"). Nothing gates on a band.
- **`min_export_kappa = 0.6` applies in Phase 5, not here.** `validate/` measures;
  it never refuses because a *value* is low (only because a value is
  *unmeasurable*: n, degeneracy). Fixed here as Phase 5's contract: the export gate
  reads the **headline = the `outcome`-axis global κ** (ADR-0003 named outcome the
  headline axis; `task_type` κ is always reported alongside), compares the *point
  estimate* against `min_export_kappa`, blocks (or requires the explicit
  deliberate-override that prints the low κ on the export's face, per config
  docstring), and additionally refuses when `headline_ready` is false, when the
  `AgreementReport.judge` fingerprint differs from the fingerprint of the labeling
  run being exported (a κ measured on instrument A must not certify labels from
  instrument B — prompt/model/few-shot drift is fingerprint-visible by ADR-0003),
  or when taxonomy ids differ. The report states when the CI95 straddles
  `min_export_kappa` — a gate passed on a straddling interval is passed *stated*.
- **The flywheel rule:** the disagreement drill-down exists to improve the judge —
  via `evalgen-architect`, never by feeding human labels into `label/` (imports
  pinned since Phase 3). Any judge change (prompt, few-shots, model) changes the
  fingerprint and therefore *invalidates the κ*: recalibration requires a fresh
  human subset (tuning against the calibration set is overfitting the instrument to
  its own exam). The fingerprint-equality export gate makes this rule mechanical.

### 7. `make agreement` offline — extend the demo or a separate target?

**A. Extend `make demo` with a `[6/6] agreement` stage.** Cons: the demo golden's
diff discipline is deliberately expensive (ADR-0003 paid it twice); agreement wants
its own golden; and CLAUDE.md §4 already defines `make agreement` as its own
headline command — burying the headline inside the demo dilutes the story a
first-time user runs. Rejected — **the demo and its golden are byte-untouched this phase**
(a phase that changes nothing upstream must not pay a golden regeneration).

**B. Separate target + separate composition module (chosen).** `make agreement` =
`python -m evalgen.agreement_demo`: a zero-arg composition layer (sibling of
`demo.py`; imports the pipeline, nothing imports it) that re-runs the fixture
pipeline (ingest → dedup → cluster → sample → FakeJudge labeling — byte-identical
to the demo's by determinism), loads the **synthetic annotation fixture**
`data/fixtures/annotations_synthetic.jsonl`, computes the agreement, and renders
one deterministic report pinned by `tests/golden/agreement_output.txt`.

The fixture: valid `HumanLabel` lines, `annotator = "synthetic"` on every line
(pinned by test so real-looking labels cannot be quietly swapped in), basename
deliberately **outside** the hook-protected `human_labels*` namespace and outside
`data/labels/` — the protection stays meaningful, and the report prints the source
basename on its face. Sized against the demo run: ≥ `min_human_labels` matched pairs
(target ~40 of the 49 labeled) so the golden exercises the *headline* path, plus
deliberate join losses — at least one human-only label for the planted few-shot
collision record (`rec-d1087e0ca3da3367` — proving the ADR-0003 corollary: a
judge-seen record can never enter κ), one human-only label for a record outside the
labeling run, and the remaining judged records as judge-only. The labels are
hand-crafted against the (deterministic) FakeJudge verdicts to produce a mid-scale
κ with a non-trivial confusion structure, including one class under the support
gate. The output carries a mandatory banner: **synthetic fixture + FakeJudge —
machinery proof, NOT a measured κ; the real number waits for
`data/labels/human_labels.jsonl`** — the same rule as the demo's synthetic
distributions (never quoted as findings). The real-data path (real human labels +
a real judged run) is wired by the Phase 5 CLI behind an explicit flag — never by
autodetection (a target that silently switches data sources is invisible variance).

### 8. Cross-checking the implementation

Implementation is **ours** (pure integer/float arithmetic in one function): sklearn
has no per-class one-vs-rest κ, no paired bootstrap, and no typed degeneracy — and
the headline number must not silently move with a dependency's minor bump. sklearn
is the **independent test oracle**: `sklearn.metrics.cohen_kappa_score` (with
`labels=` alignment) must agree with `kappa_from_confusion` on every hand fixture
and on seeded randomized label sets, both for global κ and for per-class κ (checked
by feeding sklearn the *binarized* arrays — an independent path to the same 2×2).
The bootstrap has no sklearn oracle; it is decomposed so each piece is checkable:
the index-matrix generator (shape/range/determinism + first-row values pinned for
the config seed) separately from the CI arithmetic (hand-built index matrix →
paper-computed interval).

## Decision

### Rule 1 — The formulas, exact, in one place

For an axis with classes 1..k over n matched pairs, confusion matrix m (rows =
human, columns = judge), row sums rᵢ, column sums cᵢ, diagonal D = Σᵢ mᵢᵢ:

```
p_o = D / n
p_e = (Σᵢ rᵢ·cᵢ) / n²
κ   = (p_o − p_e) / (1 − p_e)                    defined iff p_e < 1
    = (n·D − Σᵢ rᵢ·cᵢ) / (n² − Σᵢ rᵢ·cᵢ)         (implementation form: exact integer
                                                  numerator/denominator, ONE float division)
```

Degeneracy test in **integers**: p_e = 1 ⟺ Σᵢ rᵢ·cᵢ == n² (by Cauchy–Schwarz this
happens iff both raters are single-class on the same class) — no float-epsilon
hazard on the most dangerous branch.

**Per-class (one-vs-rest):** collapse m to 2×2 for class i —
`a = mᵢᵢ`, `b = rᵢ − a`, `c = cᵢ − a`, `d = n − a − b − c` — then apply the *same*
formula:

```
p_o(i) = (a + d) / n
p_e(i) = ((a+b)(a+c) + (c+d)(b+d)) / n²
κ(i)   = (p_o(i) − p_e(i)) / (1 − p_e(i))         defined iff p_e(i) < 1
```

One function, `kappa_from_confusion`, lives in **`contracts/agreement.py`** (a pure
function over an integer matrix, no numpy — the `derive_record_id` precedent):
contracts must host it because the self-validating report recomputes κ from its own
confusion matrix on every construction (Rule 4), and contracts imports no sibling.
`validate/` computes *over data*; contracts owns the arithmetic identity.

Worked check (pinned as a test fixture): pairs (C,C)×4, (C,P)×1, (P,P)×2, (P,I)×1,
(I,I)×1, (I,C)×1 → n = 10, D = 7, Σrᵢcᵢ = 5·5+3·3+2·2 = 38 →
κ = (70−38)/(100−38) = 32/62 = **16/31 ≈ 0.516129**. Per-class C: a=4,b=1,c=1,d=4 →
κ_C = 0.6 exactly; P: κ_P = 11/21 ≈ 0.523810; I: κ_I = 0.375 exactly; U: absent
(0/0). Full arithmetic in the design handoff.

### Rule 2 — The human-label contract and loader

`contracts/agreement.py::HumanLabel` (frozen, `extra="ignore"` — display-text
fields in the filled template are ignored by declaration): `record_id`,
`taxonomy_id`, `task_type: TaskTypeLabel`, `outcome: OutcomeLabel`, `annotator`
(min_length 1), `labeled_on: date | None = None`, `note: str = ""`. No confidence
field (the judge's own confidence never enters κ either — ADR-0003), no
content-derived id (Options §1).

`validate/human_labels.py::load_human_labels(path)` is **strict**, the opposite of
ingest's tolerant bucketing, and deliberately so: ingest reads hostile production
logs (tolerance + accounting); this loader reads a 30–50 line artifact a human just
curated — a malformed or unfilled line is a mistake to fix *now*, so any invalid
line, duplicate `record_id`, mixed `taxonomy_id`, or empty file refuses the whole
file with a typed error naming the line. Returns labels sorted by `record_id`.

`validate/annotation.py` renders the template and the instructions
(`render_label_template(records, taxonomy)`, `render_annotator_instructions(taxonomy)`)
as pure functions of exactly those arguments — judgments are unrepresentable in
their signatures (blindness by type, Options §1). Both render from `TAXONOMY_V1`
verbatim: one questionnaire, two annotators. The Phase 5 CLI wires them to real
pipeline runs; Phase 4 freezes format and renderers.

### Rule 3 — The join: counted, classified, never averaged away

`validate/agreement.py::compute_agreement(labeling: LabelingOutcome, human_labels,
*, human_labels_source, min_human_labels, min_class_support, bootstrap_resamples,
seed) -> AgreementReport` (all knobs injected by the composition layer — `validate/`
imports no config, the `label/` precedent):

1. **Taxonomy guard first:** every human label's `taxonomy_id` must equal the
   fingerprint's, else typed `TaxonomyMismatchError` — agreement between different
   questionnaires is not agreement (ADR-0003 rule 1).
2. Join `labeling.labeled_examples` ↔ human labels on `record_id`.
   - matched → the κ population, ordered by `record_id` ascending (the canonical
     pair order the bootstrap indexes into; content-derived, input-order free).
   - human-only → counted and **classified by cause** from the labeling report's
     buckets: `refused`, `failed`, `skipped_budget`, `fewshot_collision`
     (the ADR-0003 corollary made visible: a judge-seen record structurally cannot
     enter κ), `not_in_run`. Refusals among these are printed as **coverage loss**
     — refusals correlate with hard cases; κ's blind spot is stated, not hidden.
   - judge-only → counted, ids listed.
   - Sums enforced: `human_in = matched + human_only`,
     `judged_in = matched + judge_only`.
3. n_matched = 0 → `NoMatchedPairsError`. Duplicate ids in either input → typed
   error (defense in depth behind the loader).

### Rule 4 — The self-validating AgreementReport

`contracts/agreement.py` (all frozen, validators run on deserialization — the
`IngestReport`/`DedupReport`/`LabelingReport` discipline): `AgreementAxis`,
`KappaStatus` (`ok` / `undefined_single_class` / `absent` / `insufficient_support`),
`BootstrapCI` (`lower`/`upper` or unavailable-with-count, `b_total`,
`b_degenerate`, method literal `"percentile"`), `KappaValue` (status + p_o, p_e, κ
rounded to 6 decimals + CI; non-`ok` ⟹ all values None), `ClassAgreement` (class,
human/judge/both supports, `KappaValue`), `DisagreementEntry`, `AxisAgreement`,
`UnmatchedHuman`+cause enum, `MatchAccounting`, `AgreementReport`.

The load-bearing validators — **a report that lies about its own κ refuses to
exist**: `AxisAgreement` recomputes p_o, p_e, κ, per-class supports, and every
status from its *own confusion matrix* (integer arithmetic + the shared
`kappa_from_confusion`, rounded identically) and refuses on any mismatch; confusion
sums equal `n_matched`; disagreement entries match the off-diagonal cells in count
*and per-cell multiplicity*, sorted `(human_label, judge_label, record_id)`;
`headline_ready == (n_matched >= min_human_labels)`; accounting sums hold; id sets
sorted, unique, disjoint; `taxonomy_id == judge.taxonomy_id`; every present CI has
`b_total == bootstrap_resamples` and `b_used = b_total − b_degenerate ≥ 1`.
*Amended 2026-08-01:* the report also carries `min_class_support` (every axis must
match it — Amendment (b)) and the optional `human_labels_sha256` ground-truth
binding (format-validated — Amendment (a)). Stated
limitation: the bootstrap *interval* is not recomputable in a validator (it would
re-run 10 000 resamples on every deserialization); its integrity is owned by the
determinism tests (double-run byte-equality) and the hand-checked fixtures.

### Rule 5 — The bootstrap, exactly

One index matrix per agreement run: `rng = numpy.random.Generator(numpy.random.PCG64(seed))`;
`indices = rng.integers(0, n, size=(B, n))` — a single draw call, matrix a function
of (seed, B, n) only, shared by every metric. Per resample: rebuild the axis
confusion from the resampled *pairs*, apply `kappa_from_confusion`; degenerate
resamples excluded and counted per metric; bounds =
`numpy.percentile(valid, [2.5, 97.5], method="linear")`, rounded to 6 decimals at
the report boundary (decisions in full float64 — the ADR-0002 rounding discipline).
Per-class CIs only for classes whose status is `ok`.

### Rule 6 — The drill-down (the flywheel's fuel)

Per axis, every matched pair where human ≠ judge becomes a typed
`DisagreementEntry`: `record_id`, `human_label`, `judge_label`,
`judge_confidence`, `judge_rationale` — the judge's own stated reasoning is the
debugging signal (and confidence appears *here*, in diagnostics, never as a κ
filter). Deterministic order `(human_label, judge_label, record_id)` groups
systematic confusions (e.g. every `partially_correct`→`correct` upgrade adjacent).
The confusion matrix rides in the same report. Findings flow to
`evalgen-architect`; the fingerprint-invalidation rule (Options §6) closes the loop
without ever routing human labels toward `label/`.

### Rule 7 — Boundaries and the never-writes rule

`validate → contracts` (+ numpy) only — AST-walked at every depth like `label/`
(the human-label reader must never be importable from the judge's side, and
`validate` must not reach into `label`'s internals either; it consumes
`LabelingOutcome` through contracts). `validate/` **writes nothing**: no `open()`
(file reads go through `Path.read_text`), no write-mode operation — pinned by grep.
It reads `data/labels/` (its designed privilege — the one module allowed to see
both raters) and measures. `agreement_demo.py` is composition: imports the
pipeline, nothing imports it, never imports `anthropic`.

### Rule 8 — Config

New knob `min_class_support: int = Field(default=5, ge=1)` (Options §4). Existing
validate knobs gain their contract bounds (ADR-0002 amendment discipline):
`min_human_labels ge=1`, `bootstrap_resamples ge=1`, and `min_export_kappa`
`ge=-1.0, le=1.0` (κ's actual range). All recorded in the report / future
`meta.json`.

## Consequences

**Positive:** the headline number is backed by one hand-checked formula shared by
its own report validator (a forged or drifted κ refuses to deserialize); blindness
is now symmetric and structural (two-string judge one way, judgment-free template
signature the other); every join loss is counted and *classified* — including the
few-shot-collision corollary made visible; degeneracy is a typed vocabulary, not a
NaN; the CI is paired, seeded, method-pinned, and its degenerate resamples are
first-class citizens of the report; κ gaming vectors (confidence filtering, class
collapsing, subset shrinking, cross-taxonomy comparison, instrument drift) each hit
a structural wall; `make agreement` is a byte-pinned offline proof that says
SYNTHETIC on its face; the demo golden is untouched.

**Negative (accepted):**

- **Percentile CIs can undercover at n = 30–50** vs BCa; chosen for auditability
  and tie-robustness; the small-n caveat is printed with every interval.
- **Suppressed per-class κ under the support gate loses information** — mitigated:
  the row, both supports, and the confusion column always ship; only the noise
  number is withheld.
- **`HumanLabel` has no tamper-evident hash** — hook + git + human review are the
  guarantee, stated in Options §1.
- **One human rater assumed** — a mixed-annotator file is measured as "the human"
  with the annotator list printed; inter-human agreement is deferred.
- **The synthetic agreement number is meaningless by design** — hand-crafted labels
  vs hash-derived FakeJudge verdicts; the banner is mandatory and the README rule
  (never quote synthetic numbers) extends to it.
- **The report validator cannot recompute the bootstrap** — determinism tests and
  hand fixtures own that half (Rule 4).
- **Bootstrap cost** B × (2 global + 9 per-class) κ recomputations ≈ trivial at
  n ≤ 500 with integer confusion rebuilds; revisit only if profiling ever says so.

**Explicitly deferred:** real-data `make agreement` wiring + template emission CLI
(Phase 5 umbrella CLI, explicit flags, no autodetection); weighted-κ secondary
diagnostic; multi-annotator statistics (Fleiss/Krippendorff) + inter-human κ;
judge self-consistency (same record, k calls); a labeler UI emitting the same
template format.

**Validated by (the Phase 4 test battery — detailed in the design handoff):** the
hand-computed fixture family (κ = 16/31 global with per-class 0.6 / 11/21 / 0.375 /
absent; perfect κ = 1; chance κ = 0; κ = −1; monoclass-judge → exactly 0;
one-side-absent → exactly 0; p_e = 1 → typed undefined); sklearn cross-checks
(global + binarized per-class, hand fixtures + seeded randomized sets); the
hand-built index-matrix CI fixture ([0.0, 0.9625] with b_degenerate = 1, computed
on paper); index-matrix determinism + pinned draws for seed 1750; double-run
byte-identity of `AgreementReport` and of `make agreement` output; every
refuse-to-validate case (κ/confusion mismatch, status/value inconsistency, sum
violations, multiplicity mismatch, headline_ready lie); join classification
fixture (refused/collision/not-in-run causes); taxonomy-mismatch and
zero-match refusals; loader strictness (unfilled template line named, duplicates,
mixed taxonomy, empty file); template/instructions renderers (verbatim taxonomy
definitions, no judge tokens representable); Landis–Koch band edges; support-gate
boundary (4 vs 5); `min_human_labels` boundary (29 vs 30); the module-boundary AST
battery extended to `validate/` + the no-write grep; golden byte-equality of
`make agreement` with the SYNTHETIC banner and no secret / absolute-path leak.

## Amendment (2026-08-01) — pre-commit red-team pass closures

The adversarial review returned 0 blockers / 0 majors, 2 minors, 4 nits (report:
`.workflow-handoff/redteam.md`). Both minors are closed **now** rather than
deferred — each cost a few lines and the residuals sat exactly on the repo's
credibility axis. Each fix ships with a regression test replaying the red team's
own payload/arithmetic.

**(a) M-1 — the report now binds to the exact ground-truth bytes.** Options §1
accepted "hook + git history" as the whole tamper story; the red team proved that
selective filtering of the labels file is ledger-visible (`human_in` drops, the
dropped ids surface in `judge_only_ids`) but noted the residual: nothing tied a
published κ to the file bytes it was measured against. Closure:
`AgreementReport.human_labels_sha256: str | None` — the sha256 hexdigest of the
label-file bytes, computed by the **composition layer** that read the file
(`validate/` stays read-and-measure-only; `.gitattributes` forces LF on `*.jsonl`,
so the digest is platform-stable). The field refuses anything that is not a
64-char lowercase hexdigest; the renderer prints `labels sha256=…` in the header,
and an unbound report prints `sha256=unrecorded` — absence never hides.
Regression: the filtering payload replayed through real files (fixture A: dropping
the 3 disagreeing labels lifts κ from 16/31 = 0.516129 to exactly 1.0 — n=7, D=7,
S = 4²+2²+1² = 21 < 49 ⇒ κ = 28/28) now produces two reports with **different
bindings on their face**. Phase 5 duty unchanged: `meta.json` must copy this
digest next to the embedded report (CLAUDE.md input-SHA-256 provenance rule).

**(b) M-2 — ONE support gate per measurement protocol, visible in the header.**
`min_class_support` lived only on `AxisAgreement`, untied across axes: a
hand-assembled report could carry a trivialized (`=1`) gate on one axis and still
validate, and when no class happened to be suppressed the active gate value
appeared nowhere in the rendered text. Closure: `AgreementReport.min_class_support`
(required), with a validator refusing any axis whose gate diverges (both
directions boundary-tested, the `headline_ready` discipline), and a new header
line `gates min_human_labels=… min_class_support=…` — the gate knobs are protocol,
as visible as B and seed. Rule 4's validator list and Rule 8's config table are
amended accordingly.

**(c) NIT dispositions.** N-1 (empty pair arrays died with
`kappa_from_confusion`'s misleading "sums to 0"): fixed — `bootstrap_kappa`
refuses n = 0 naming the real problem; unreachable through `compute_agreement`
(`NoMatchedPairsError` fires first), defensive polish with a test. N-4
(`_classify_human_only` rebuilt the report's id sets per orphan): fixed — the four
sets are built once per orphan list; same causes, same order, linear cost
(behavior pinned by the existing fixture-H causes test). N-2 (Landis–Koch band on
the rounded κ): **no action** — the red team verified the direction itself:
with ≤-edges, 6-decimal rounding can only demote a band at a boundary
(conservative); the convention stays documented in `landis_koch_band`'s docstring.
N-3 (legacy Windows console cp1252): **no action** — golden comparisons are
in-process true bytes, CI is Linux, and the regeneration procedure documents
`PYTHONUTF8=1` (same platform note as the Phase 2/3 goldens).

**(d) Phase 5 export-gate clarification (from the red team's forward notes).**
`headline` can be present (`headline_ready=True`) yet carry status
`undefined_single_class` — kappa `None`, all pairs single-class. The export gate
MUST treat a headline whose status ≠ `ok` as blocking, exactly like
`headline_ready=False`: "compares the point estimate against `min_export_kappa`"
(Options §6) is only defined over a κ that exists. Written here so the Phase 5
spec cannot meet a `None` unprepared.

**Golden regeneration #1 for this phase (sanctioned, reviewed line-by-line):**
`tests/golden/agreement_output.txt` gains exactly two header lines (`labels
sha256=…`, `gates …`); every κ, CI, count, confusion cell and disagreement line is
byte-identical to the pre-amendment golden. The demo golden stays untouched.

## Amendment (2026-08-04) — real-agreement-CLI red-team closures (F-1 / F-2 / F-3)

The adversarial review of the real-data CLIs (`annotation_cli`, `agreement_run` —
the Options §7 deferred wiring) returned 1 HIGH + 2 MEDIUM
(`.workflow-handoff/redteam.md`). All three are closed before commit; each closure
ships with regression tests replaying the red team's executed payload. The common
thread is this ADR's own doctrine: guarantees must be structural, not procedural.

**(e) F-1 (HIGH) — the annotation artifacts get their own directory, and mixing is
refused on BOTH sides.** Options §1 made verdicts unrepresentable in the template
*signature*, but the first CLI wiring wrote the blank template into `data/out/` —
the same default directory where `export_demo` drops `golden.jsonl`/`meta.json`
and `agreement_run` drops its run report, all carrying the judge's verdicts for
the same 49 record_ids. The human sent there to fetch the template found the
answer key one file away (anchoring — the exact bias §1's structural design
exists to prevent). Closure, structural on both sides of the seam:
`annotation_cli` writes to **`data/annotation/`** (gitignored, human-facing
artifacts ONLY) and **refuses** (typed `JudgeArtifactsPresentError`, nothing
written, exit 2) any target directory containing `golden.jsonl`, `meta.json`, or
`agreement_run_report*.json`; `agreement_run` enforces the mirror guard — it
refuses (exit 2, before the pipeline runs and before any API cost) an `--out`
containing `annotation_template.jsonl` or `annotation_instructions.txt`. The
stdout report states the separation and the refusal rule on its face. Regression:
the payload replayed — a directory holding each judge artifact in turn refuses
the template write; a mixed `--out` refuses with zero SDK calls (recording
client).

**(f) F-2 (MEDIUM) — "synthetic" is decided on label CONTENT, never on bytes
alone.** The first wiring flagged synthetic iff `sha256(--labels bytes)` equaled
the committed fixture's digest; a CRLF/trailing-newline re-encode kept the exact
synthetic labels but shed the `!! SYNTHETIC` banner and recorded
`synthetic: false` — a real-data-looking κ over labels §7 calls meaningless by
design. Closure: three INDEPENDENT data-side triggers, any of which forces the
banner and `synthetic: true` (byte identity remains one trigger, never the only
one): (1) byte identity with the fixture; (2) **canonical label content** — the
sorted `(record_id, task_type, outcome)` tuples equal the fixture's, so
re-encoding, field reordering, note edits and annotator renames cannot shed the
banner (only actually different labels can — and a fabricated variant is forgery,
out of scope for honesty labeling); (3) the fixture's pinned
`annotator == "synthetic"` marker, now a trigger rather than a printed hint.
Plus `judge == fake`, as before. Every reason that fired is printed in the banner
and recorded in the run report (`synthetic_reasons`). Regression: the CRLF
payload replayed byte-for-byte (banner survives, `synthetic: true`), the formerly
*blessed* trailing-newline path inverted, the marker-alone case pinned, and the
genuinely-real shape (different labels AND different annotator) still prints the
REAL DATA header with `synthetic_reasons == []`.

**(g) F-3 (MEDIUM) — real-judge runs leave a per-run trace; re-rolls cannot erase
each other.** The run report was written to one fixed basename via atomic
replace, with no timestamp or run id: κ-gaming failure mode 5 at run granularity
— re-roll the (non-deterministic) real judge until κ clears the gate, keep the
lucky run, no evidence the others existed. Closure: a `--judge anthropic` run
writes `agreement_run_report.<run_id>.json` and stamps a quarantined `volatile`
section (`run_id`, UTC `generated_at` — the `export_demo` `VolatileProvenance`
discipline: the composition layer is the only clock reader, volatile values never
enter deterministic surfaces). Discarded runs accumulate side by side; the stdout
footer names the exact per-run file. This makes cherry-picking *visible*, not
impossible — consistent with "measured, never declared". The fake path is
byte-deterministic by contract and keeps the fixed basename with
`volatile: null`. Regression: two mocked real runs into the same `--out` → two
stamped reports, first one byte-untouched; the fixed basename never used by the
real path; fake double-run byte-identity re-pinned.

**Golden regeneration #2 for this phase (sanctioned, reviewed line-by-line):**
`tests/golden/annotation_template_output.txt` — only the `files` block changes
(the `data/annotation/` home + the separation statement); both artifact sha256
digests are byte-identical to the pre-amendment golden, proving the template and
instructions themselves carry zero change. The demo, agreement and export goldens
stay untouched.
