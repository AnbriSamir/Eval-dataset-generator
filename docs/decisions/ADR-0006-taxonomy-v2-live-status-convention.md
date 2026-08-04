# ADR-0006 — Taxonomy v2: the bounded-plausibility convention for live-status claims

**Status:** Accepted (2026-08-04) — the flywheel's second turn, triggered by the first
real double-blind agreement session (ADR-0004's protocol running on real data for the
first time). This ADR sharpens the `outcome` guideline; the re-annotation and re-measure
that follow it are a separate, human-driven step (see Consequences).

## Context — the finding, in the committed numbers

The real session
([`docs/reports/agreement_run_report.20260804T002205Z-7c2b30d6.json`](../reports/agreement_run_report.20260804T002205Z-7c2b30d6.json),
bound to `human_labels_sha256=eceeb0a9aa00118db7420360e51e4a0ed669d575a547da0104f8644b3fd80157`):
one human annotator (`samir`) filled the blind template for 49 exchanges;
`claude-opus-4-8` judged the same 49 live under `TAXONOMY_V1`
(`tax-d9ca3b87b403`, prompt `b963c9e7aa28…`). Perfect join, 49/49 matched, zero
orphans. The two axes split dramatically:

| Axis | κ | p_o | p_e | CI95 (B=10000) | Band |
|---|---|---|---|---|---|
| `task_type` | **0.861190** | 0.918367 | 0.411912 | [0.721817, 0.966667] | almost perfect |
| `outcome` (headline) | **0.263158** | 0.755102 | 0.667638 | [−0.024390, 0.532698] | fair |

The export gate blocked the dataset (0.263158 < `min_export_kappa` = 0.6) — correctly.

The disagreement drill-down (ADR-0004 rule 6 — built for exactly this moment) turned
the low number into ONE dominant, systematic confusion instead of diffuse noise. Of the
**12 `outcome` disagreements**:

- **7 are `correct → unjudgeable`** (human said correct, judge said unjudgeable) —
  records `rec-1cbd5ae757464011`, `rec-7e11ce59b29a4580`, `rec-c1a67f62f2e4d615`,
  `rec-c67c4896e7d79b42`, `rec-eb78f7082d5d0fef`, `rec-f8102818860818b2`,
  `rec-f9e1699074bcd6cb`. Six are live traffic-status questions ("Le trafic est-il
  fluide sur l'A13 ce matin ?"), the seventh a same-day toll-tariff query. The judge's
  own rationales say it plainly: *"this claim depends on real-time data that is not
  present in the exchange and cannot be verified"* — the judge applied v1's written
  `unjudgeable` definition ("the answer depends on missing context") to the letter,
  while the human graded the answer's *plausibility as a response*. The marginal
  asymmetry confirms the systematic drift: judge `unjudgeable` support 9 vs human 2.
- 4 are `correct → partially_correct` (ordinary severity-boundary judgment calls),
- 1 is `partially_correct → correct`.

Both raters were **internally consistent**; they were answering **different
questionnaires in their heads**. That is an annotation-guideline gap — v1's `outcome`
definitions simply never said which convention governs intrinsically-live claims — not
rater noise and not judge failure. A guideline gap is fixed in the guideline.

## The decision (user-ratified): the "bounded plausibility" convention

For a question that is **inherently live** (live traffic state, today's tariffs — any
claim whose ground truth exists only at answer time and can never be recovered from the
transcript), the answer is judged **AS A RESPONSE**: internal consistency and adequacy
to the question asked. External verification is neither required nor possible, and its
impossibility is a property of the *question*, not a defect of the *answer* —
`unjudgeable` is reserved for defective **inputs** (ambiguous input, incomplete
exchange) and never fires merely because the claim is live.

Rationale for this direction (rather than teaching the human to say `unjudgeable`): the
corpus is autoroute-traffic production traffic — live-status questions are its core, not
an edge case. A convention that routes the corpus's most common intent into
`unjudgeable` makes the headline axis structurally unmeasurable on exactly the traffic
the golden set exists to cover, and turns `unjudgeable` (designed in ADR-0003 as a
label for *unjudgeable exchanges*) into a dumping ground for *unverifiable-but-
perfectly-judgeable* ones.

### The amended definitions (v2, verbatim from `contracts/taxonomy.py`)

`correct` —

> The output fully addresses the input with no visible factual or logical error.
> Stylistic differences do not matter. For inherently live claims (live traffic,
> today's rates), grade the answer AS A RESPONSE: it is correct when it is internally
> consistent and adequately answers the question — external verification of the live
> value is neither required nor possible.

`unjudgeable` —

> No grading is possible from the exchange alone — choose this ONLY when the INPUT is
> ambiguous or the exchange is incomplete, never merely because the claim is live or
> externally unverifiable (grade those AS RESPONSES). Choose this only when judging is
> impossible, not when it is merely hard; it is a label, not an error.

### `partially_correct` — weighed, and retouched for coherence

Considered leaving it byte-identical (smallest diff). Rejected: once live answers are
graded as responses, the incompleteness question immediately follows — *what is an
incomplete live answer?* Two of the real session's own disagreements sit exactly there:
`rec-91f350d70ee60bd9` (the judge marked `partially_correct` because the output answered
a generic baseline — "dans des conditions normales" — where the live state was asked)
and `rec-c1a67f62f2e4d615` (tariff for one vehicle class where the full schedule was
asked). Without an explicit boundary, v2 would trade the old `correct/unjudgeable`
ambiguity for a new `partially_correct/unjudgeable` one on the same records. Amended —
one appended sentence, decision-oriented like every v1 definition:

`partially_correct` —

> The output addresses the input but is incomplete, or contains a minor peripheral
> error. Any hard factual error on the asked question itself makes the outcome
> incorrect, not partially_correct. Live claims are graded as responses under the same
> rule: an answer that covers only part of what was asked, or answers a generic
> baseline where the live state was asked, is partially_correct — never unjudgeable.

`incorrect` is untouched (an internally inconsistent or off-question live answer
already fails as a response under its existing text), and the **entire `task_type`
axis is untouched** — v2 reuses v1's axis object verbatim (κ = 0.861 measured no
problem to fix there).

**No class added, none removed, no reordering.** The class inventory is a statistics
decision (ADR-0003 options §1): Phase 4's per-class support arithmetic at n = 30–50 is
sized for exactly these 5+4 classes, and the enums — which are the `output_format`
schema — do not move, so `JudgeVerdict`, `HumanLabel`, every report contract and every
κ formula are untouched by construction. v2 is a *definitions* change riding the
existing structure; the content-derived id does the rest.

## Continuity — v1 is frozen, not deleted

- `TAXONOMY_V1` stays in `contracts/taxonomy.py`, exported from `evalgen.contracts`,
  byte-identical, importable forever. Its id `tax-d9ca3b87b403` is pinned by test — a
  drift in the frozen artifact fails the suite.
- The committed run report, the README's "The real κ" section, and the human's v1
  labels (`data/labels/human_labels.jsonl` — hook-protected historical artifact) all
  reference `tax-d9ca3b87b403`; that provenance chain must never break, and now cannot:
  the referenced object still exists and still self-verifies.
- `TAXONOMY_V2` (`tax-d8ba44dd70c7`, content-derived, pinned by test) becomes the
  default of every composition layer: `demo`, `agreement_demo`, `export_demo`,
  `annotation_cli`, `agreement_run`. The judge prompt and the annotator instructions
  render the v2 definitions VERBATIM from the same object — one questionnaire, two
  annotators (ADR-0003 rule 1), pinned by a test asserting the same definition text
  appears on both sides.
- Flipping the default changes `prompt_sha256` — **that is the ADR-0003 drift alarm
  doing its job**, and it pays the sanctioned price: every golden that renders a
  fingerprint, a taxonomy id, or the definitions regenerates once, with the diff
  reviewed line-by-line (only ids / fingerprints / digests / rendered definitions may
  move; every κ, count, confusion cell and record id in the goldens must be
  byte-identical, since the FakeJudge derives verdicts from record content, never from
  the taxonomy text).

## The anti-mix guard — audited, one hole closed

v1 human labels measured against a v2 judge would compute agreement between two
different questionnaires — the exact thing ADR-0003 rule 1 forbids. Audit of the
existing layers:

| Layer | Status before this ADR |
|---|---|
| `load_human_labels` | refuses a file MIXING taxonomy ids — but a uniformly-v1 file loads fine (correct: the loader cannot know the run's taxonomy). |
| `compute_agreement` | refuses labels whose id differs from the judge fingerprint's (typed `TaxonomyMismatchError` naming both) — but it runs AFTER labeling. |
| `AgreementReport` | validator: `taxonomy_id == judge.taxonomy_id` — covered. |
| `LabelingOutcome` | validator: every example's id equals the fingerprint's — covered. |
| Export gate `instrument_binding` | compares full fingerprints field-by-field, `taxonomy_id` included (ADR-0004 options §6 honored — verified in `export/gate.py`). |
| `agreement_run` CLI | **the hole**: with `--judge anthropic`, all 49 API calls are spent BEFORE `compute_agreement` raises — and the refusal surfaced as a raw traceback, not a typed CLI refusal. |

Closure: `agreement_run` now checks every loaded label's `taxonomy_id` against the
run's judge fingerprint **immediately after the strict load, before the preflight print
and before any judge call** (the F-1 guard's discipline: refuse before any cost), and
refuses with exit 2 and the typed `TaxonomyMismatchError` message naming BOTH ids and
the remedy (`make annotate` regenerates a current-taxonomy template). The guard is
symmetric — it compares ids, so v1-labels/v2-run and v2-labels/v1-run are both refused
— and both directions plus the zero-API-calls property are pinned by tests.

## Consequences

- **Positive:** the systematic 7/12 confusion has a written convention both annotators
  will read from the same artifact; `unjudgeable` returns to its designed meaning;
  the corpus's dominant intent (live status) becomes measurable on the headline axis;
  v1 provenance is untouchable; cross-version joins are structurally refused at every
  layer including the CLI, pre-cost.
- **Negative (accepted):** the published κ = 0.263158 remains the repo's headline until
  a full v2 session exists — v2 does not retroactively improve anything and must not be
  cited as if it did. The synthetic fixture's κ (0.513109) is unchanged in value but
  now rides v2 ids; it remains machinery proof, never a finding.
- **Explicitly out of scope for this change** (the flywheel's next steps, in order):
  (1) the human re-annotates from the regenerated v2 template (`make annotate`,
  filled OUTSIDE any agent, saved as the hook-protected
  `data/labels/human_labels.jsonl` — the v1 file it replaces stays in git history as
  the committed report's ground truth); (2) `agreement_run --judge anthropic` re-runs;
  (3) the new κ is published **whatever it is**, next to the v1 number, never instead
  of it. Judging the convention by whether κ went up would be tuning the instrument to
  its own exam; the convention is justified by the drill-down evidence above, not by
  the number it produces.
- **Fixture migration (sanctioned):** `data/fixtures/annotations_synthetic.jsonl` is a
  synthetic machinery fixture (ADR-0004 options §7), not ground truth; its 42
  `taxonomy_id` fields migrate to `tax-d8ba44dd70c7` so the offline demos keep
  exercising the headline path under the default taxonomy. Labels, record ids,
  annotator marker and everything else stay byte-identical; the agreement/export
  goldens re-pin its new sha256.

## Amendment (2026-08-04) — post red-team (report: `.workflow-handoff/redteam.md`, verdict PASS)

Two decisions recorded after the adversarial review of this ADR's implementation:

1. **The clean-refusal channel widens to the strict loader (red-team R-1).** The
   closure above routed `check_labels_match_run_taxonomy`'s `TaxonomyMismatchError`
   to a typed exit-2 refusal — but the SAME exception raised by `load_human_labels`
   on a file MIXING taxonomy ids (and its siblings `HumanLabelFormatError` /
   `DuplicateHumanLabelError`) still escaped `agreement_run` as a raw traceback.
   Amended: the strict load and the anti-mix guard now share one refusal path —
   typed message on stderr, exit 2, `--out` never created, zero API calls (the
   pre-cost property is pinned with a recording client). An operator error in a
   curated 30–50 line file is a mistake to fix now, not a crash to decode.
2. **No few-shot anchors the v2 convention in this turn — deliberately (red-team
   R-2/R-4, both INFO).** The convention is carried by the definitions text alone,
   which names and excludes the exact v1 rationale that produced the 7/12 block
   ("never merely because the claim is live or externally unverifiable"); no
   committed few-shot teaches against it (fs-966c grounds `unjudgeable` on the
   incomplete exchange, not on liveness). Adding a positive "live traffic →
   correct" exemplar was weighed and REJECTED for this turn: the flywheel's second
   measurement must isolate ONE variable — the guideline — and changing the
   few-shot set in the same turn would confound attribution of whatever κ movement
   round 2 measures. **Binding constraint for later turns:** if a few-shot anchor
   (or the R-4 wording hardening of "the exchange is incomplete") is ever adopted,
   it lands BEFORE a (re-)annotation session — never between the human's
   annotation and the judge re-run — because it moves `prompt_sha256`, and the
   instrument must be frozen for the whole session it measures. Round 3's
   drill-down decides, on evidence, whether the anchor earns its goldens cost.

**Validated by:** pinned ids for BOTH versions (v1 `tax-d9ca3b87b403` unchanged, v2
`tax-d8ba44dd70c7` new); v1 importable + intact (version tag, axis structure,
round-trip); v2 mirrors the enums member-for-member; v2's `task_type` axis is v1's
verbatim; the amended definitions rendered VERBATIM and IDENTICALLY in the judge system
prompt and the annotator instructions; the convention's key phrases present in v2 and
absent from v1 (drift both ways); the anti-mix guard refusing v1-labels/v2-run and
v2-labels/v1-run, naming both ids, before any API call (recording-client proof); all
five CLI goldens regenerated under the line-by-line diff rule with double-run
byte-identity re-pinned.
