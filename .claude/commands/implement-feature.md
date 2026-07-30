---
description: Build one pipeline feature end-to-end (design -> implement -> test -> red-team -> document) the way a senior engineer would sequence it, stopping for human sign-off before any commit.
argument-hint: <feature, e.g. "near-dup dedup with measured threshold" or "bootstrap CI95 on kappa">
allowed-tools: [Task, Agent, Read, Grep, Glob, Bash, Edit, Write]
model: claude-opus-4-8
---

You are orchestrating the full lifecycle for a single feature in the
eval-dataset-generator repo. The feature to build is:

    $ARGUMENTS

If `$ARGUMENTS` is empty, ask the user what feature to build and stop until they answer.

Drive the work by delegating to the named subagents in this exact sequence. Do not
skip stages, and do not let one agent do another agent's job. Summarize each stage's
output before moving to the next.

## 1. Design (evalgen-architect)
Delegate to the **evalgen-architect** subagent to frame the design: where this
feature plugs into `src/evalgen/`, the `contracts/` models it touches, and the
load-bearing trade-offs (taxonomy design, dedup thresholding, clustering choice,
κ protocol, export/provenance format). If the decision is load-bearing, have the
architect write or update an ADR under `docs/decisions/` (or invoke the /adr-new
flow). Capture the agreed design as the brief for the next stage.

## 2. Implement (the matching domain agent)
Route implementation to exactly one domain owner based on where the code lives:
- loaders / normalization / redaction at the boundary / TraceSpan adapter /
  shared `contracts/` models -> **ingest-engineer**
- exact + near-dup dedup, embeddings, HDBSCAN clustering, stratified sampling,
  dedup/coverage reports -> **mining-engineer**
- LLM judge (SDK structured output), label taxonomy, few-shot store + leakage
  discipline, fake judge -> **judge-engineer**
- Cohen's κ (global/per-class), bootstrap CI95, disagreement drill-down,
  contamination guards, human-label subset workflow -> **stats-scientist**
- export golden.jsonl + meta.json provenance, CLI (`python -m evalgen`),
  config.py, Makefile wiring -> **pipeline-engineer**

Hand the chosen agent the architect's brief and have it implement against the agreed
contracts. Keep the differentiators intact (redaction before persistence, dedup
before clustering, judge blind to human labels, few-shots never exported, seeded
determinism, complete provenance).

## 3. Tests
Have the same domain agent add pytest coverage for the new behavior (near-dup
threshold boundaries and transitive chains, adversarial redaction payloads,
hand-checked κ fixtures, the deliberately-leaked contamination fixture, byte-identical
re-export as applicable) — against the fake judge and hashing embedder, fully
offline. Then run the suite: `make test` (or `python -m pytest`). Iterate until it
passes.

## 4. Red-team (adversarial-reviewer)
Delegate to the **adversarial-reviewer** subagent (read-only) to attack the diff:
dedup misses, κ gaming, leakage judge↔human↔export, redaction bypasses,
nondeterminism, provenance gaps, SDK misuse. Collect its severity-tagged findings.
Route any blocking findings back to the relevant domain agent for fixes, then re-run
tests.

## 5. Document (docs-historian)
Delegate to the **docs-historian** subagent to sync `docs/architecture.md` and the
README so they stay truthful to the code. It must not invent agreement numbers -- if
the change affects metrics, point it at a reproducible `make agreement` /
/eval-report run.

## 6. Stop for sign-off
Present: the design summary, the diff overview, the test result, the adversarial
findings (and how each was resolved), and the doc updates. Then STOP. Do NOT commit
-- wait for explicit human sign-off before any `git commit`.
