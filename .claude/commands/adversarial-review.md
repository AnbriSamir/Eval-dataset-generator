---
description: Red-team the current git diff before committing — a read-only opus reviewer hunts dedup misses, κ gaming, judge↔human↔export leakage, redaction bypasses, nondeterminism and provenance gaps; the architect triages; domain agents fix blockers.
argument-hint: "[optional scope, e.g. 'src/evalgen/dedup/']"
allowed-tools: [Task, Agent, Read, Grep, Glob, Bash, Edit, Write]
model: claude-opus-4-8
---

You are running a focused red-team pass over the working diff of the
eval-dataset-generator repo. Optional scope: $ARGUMENTS

## 1. Attack (adversarial-reviewer)
Delegate to the **adversarial-reviewer** subagent (read-only) to inspect
`git diff` + `git diff --staged` (narrowed to the scope if given) and produce its
severity-tagged findings list: BLOCKER / MAJOR / MINOR / NIT, each anchored to
file:line with the concrete leaked secret, surviving near-dup, wrong κ, contamination
path, or nondeterministic export it causes.

## 2. Triage (evalgen-architect)
Delegate to the **evalgen-architect** subagent to triage the findings:
which BLOCKERs/MAJORs gate the commit, which are deferred (with a stated reason),
and which domain agent owns each fix.

## 3. Fix (the owning domain agents)
Route each gating finding to its owner (ingest-engineer / mining-engineer /
judge-engineer / stats-scientist / pipeline-engineer), apply the fixes, then re-run
`make test` and — if anything under `src/evalgen/{ingest,dedup,cluster,label,
validate}/` changed — remind that /eval-report is required before any κ or coverage
number is claimed.

## 4. Close the loop
Re-run the **adversarial-reviewer** on the updated diff to confirm the gating
findings are resolved. Present: initial verdict, fixes applied, final verdict.
Do NOT commit — wait for explicit human sign-off.
