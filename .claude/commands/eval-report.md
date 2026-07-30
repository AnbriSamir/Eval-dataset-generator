---
description: Run the agreement measurement and produce the defensible κ/coverage report (Cohen's κ global + per-class with support, bootstrap CI95 on paired labels, disagreement drill-down, contamination + provenance checks), then sync the README headline block. Refuses to publish numbers that aren't reproducible from make agreement.
argument-hint: "[optional focus, e.g. 'per-class' or 'coverage']"
allowed-tools: [Task, Agent, Read, Grep, Glob, Bash, Edit, Write]
model: claude-opus-4-8
---

You are producing the agreement/coverage report for the eval-dataset-generator repo.
Optional focus: $ARGUMENTS

## 1. Measure (stats-scientist)
Delegate to the **stats-scientist** subagent to:
- run `make agreement` over the judge labels vs `data/labels/human_labels.jsonl` —
  Cohen's κ global AND per-class (each with its n; low-support classes flagged, never
  silently dropped), bootstrap CI95 on the PAIRED labels (seeded, B recorded), and
  the disagreement drill-down with the confusion matrix;
- where the focus includes coverage, report the cluster/stratification coverage of
  the candidate set against the deduplicated distribution (per-cluster quotas met,
  noise handling stated);
- sanity-check for contamination (export ∩ judge few-shots = ∅; no path from
  `label/` to the human labels), judge health (refusals/parse failures counted and
  reported, never silently excluded), and provenance completeness (meta.json:
  git SHA, input SHA-256, seeds, thresholds, model ids actually used).

## 2. Verify reproducibility
Numbers qualify for publication ONLY if produced by `make agreement` in this run and
provenance-stamped. If a number cannot be regenerated, it is not published — no
exceptions, no "approximately". A κ computed on a contaminated or stale run is not a
number; it is a bug report.

## 3. Publish (docs-historian)
Delegate to the **docs-historian** subagent to update the README headline κ block and
any affected docs, carrying EVERY caveat with its number: n, per-class support, CI95,
judge model id, taxonomy version, single-run status, and unfavorable κ published
as-is.

## 4. Summarize
Present the final tables, what changed vs the previous run, and any regression
worth investigating. Do NOT commit — wait for explicit human sign-off.
