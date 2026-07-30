---
description: Prove byte-identical reproducibility — re-run the pipeline on committed fixtures, byte-diff golden.jsonl + meta.json against the committed reference, and re-verify that the seeded agreement numbers regenerate bit-exact. Any drift is flagged as a regression.
argument-hint: "[optional stage, e.g. 'export' or 'agreement' or 'all']"
allowed-tools: [Task, Agent, Read, Grep, Glob, Bash, Edit, Write]
model: claude-opus-4-8
---

You are auditing the reproducibility guarantees of the eval-dataset-generator
repo. Optional scope: $ARGUMENTS

## 1. Pipeline determinism (pipeline-engineer)
Delegate to the **pipeline-engineer** subagent to re-run the end-to-end pipeline on
the committed fixture logs (`make demo`, then `make export`) — offline, fake judge,
hashing embedder — and BYTE-DIFF the regenerated `golden.jsonl` + `meta.json`
against the committed reference artifacts. Any divergence: identify the
nondeterminism source (wall-clock, uuid4, unsorted iteration, unseeded sampling,
unstable JSON serialization, CRLF/locale drift) down to the line.

## 2. Agreement reproducibility (stats-scientist)
Delegate to the **stats-scientist** subagent to re-run `make agreement` and diff the
regenerated `AgreementReport` against the prior committed run. The seeded bootstrap
must reproduce identical CI95 intervals; κ global and per-class must match exactly;
the input SHA-256 and seeds in `meta.json` must match the reference.

## 3. Confirm nothing leaked (adversarial-reviewer)
Delegate to the **adversarial-reviewer** subagent (read-only) to confirm no
nondeterminism or contamination entered the metric-bearing paths since the last
audit: export ∩ few-shots still empty, no path from `label/` to the human labels,
no un-redacted content in regenerated artifacts.

## 4. Verdict
Present: pipeline verdict (byte-identical / diverged where and why), agreement
verdict (bit-exact / drifted on which metric), and the fix owner for any regression.
A drift is a REGRESSION to fix, never a number to quietly re-publish.
Do NOT commit — wait for explicit human sign-off.
