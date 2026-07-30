---
description: Scaffold a numbered Architecture Decision Record (context, options weighed, decision, consequences) and cross-link it from docs/architecture.md.
argument-hint: <topic, e.g. "near-dup threshold protocol" or "label taxonomy v2">
allowed-tools: [Task, Agent, Read, Grep, Glob, Write, Edit]
model: claude-opus-4-8
---

You are capturing one load-bearing architecture decision for the
eval-dataset-generator repo. The topic is:

    $ARGUMENTS

If `$ARGUMENTS` is empty, ask the user for the decision topic and stop until they
answer.

## 1. Author (evalgen-architect)
Delegate to the **evalgen-architect** subagent to author the decision content:
Status, Context, Decision Drivers, Options Considered (2-3 real options, each with
pros/cons on the axes label-fidelity/coverage/cost/reproducibility/complexity),
Decision, Consequences (positive AND negative), and where applicable the measured
numbers or the agreement/coverage metric that will validate the choice. No invented
benchmarks — typical topics here (dedup thresholding, clustering choice, taxonomy
design, κ protocol, export format) must cite the reproducible run that justifies
them, or name the measurement that will.

## 2. File and link (docs-historian)
Delegate to the **docs-historian** subagent to:
- assign the next `ADR-NNN` number under `docs/decisions/` (slugged filename);
- format the document consistently with the existing ADRs;
- cross-link it from `docs/architecture.md` in the decisions index.

## 3. Summarize
Present the decision in three sentences: what was decided, what it costs, what
measurement will confirm it. Do NOT commit — wait for human sign-off.
