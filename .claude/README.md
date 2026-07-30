# `.claude/` — Multi-Agent Engineering for eval-dataset-generator

This directory is the project's **agentic engineering setup** for
[Claude Code](https://claude.com/claude-code): a team of specialized subagents,
the orchestration commands that sequence them, and the safety hooks that keep the
repo's headline signal honest. It is committed deliberately — browsing it tells you
exactly how this codebase is built and reviewed.

> The recursion is intentional: **an eval-dataset pipeline whose own development is
> governed by the discipline it sells.** The product's promise — dedup done honestly,
> coverage measured, agreement validated with κ/CI95, provenance replayable — is
> enforced on the agents that build it. Every agent owns a real pipeline stage, every
> command encodes how a senior sequences the work, and every hook defends a specific
> way this repo could silently lose its credibility.

---

## Philosophy

The repo's whole senior signal lives in four places: **honest dedup** (exact hash +
near-dup cosine above a measured threshold — hidden near-dupes silently inflate every
downstream metric), **real coverage** (deterministic embeddings + HDBSCAN + stratified
sampling over the actual traffic, not the easy head), **measured — not declared —
agreement** (an LLM judge validated against human labels with Cohen's κ, global and
per-class, plus a bootstrap CI95, published unfavorable values included), and **full
provenance** (byte-identical exports, `meta.json` with git SHA / input SHA-256 /
seeds / model ids, a contamination guard proving export ∩ few-shots = ∅). Those are
exactly the places where a subtle bug is invisible: a transitive near-dup chain that
survives, a threshold that flips at its boundary, a judge that peeked at the ground
truth, a bootstrap that was never seeded. A monolithic "do everything" agent blurs
those boundaries and lets such bugs through.

The pipeline the team owns, stage by stage:

```
production logs (TraceSpan JSONL from multi-agent-orchestrator · generic adapter)
   │
   ▼
 contracts/   shared Pydantic models — LogRecord, Cluster, LabeledExample,
   │          LabelTaxonomy, AgreementReport, ExportManifest
   ▼
 ingest/      loaders · normalization · REDACTION at the boundary
   │          (secrets/PII never persist)                        [ingest-engineer]
   ▼
 dedup/       exact (content hash) · near-dup (cosine, measured
   │          threshold) · dedup report                          [mining-engineer]
   ▼
 cluster/     deterministic embeddings · HDBSCAN · stratified
   │          coverage sampling                                  [mining-engineer]
   ▼
 label/       LLM judge (structured output) · taxonomy · few-shot
   │          store (leak-guarded) · fake judge for tests        [judge-engineer]
   ▼
 validate/    human-label subset · Cohen's κ global + per-class ·
   │          bootstrap CI95 · disagreement drill-down           [stats-scientist]
   ▼
 export/      golden.jsonl · meta.json provenance · contamination
              guard (export ∩ few-shots = ∅)                     [pipeline-engineer]
```

This setup splits the work the way a strong team would:

1. **One owner per `src/evalgen` boundary.** Each agent has a narrow mandate and a
   tool allowlist, so changes stay inside their stage and contracts are explicit.
2. **The differentiators get the strongest model.** Statistics, architecture, and
   adversarial review run on **opus** — high-volume implementation runs on **sonnet**,
   documentation prose on **haiku**. Reasoning budget goes where a mistake is most
   expensive.
3. **Review and fix are separated.** The adversarial reviewer is **read-only**. It
   finds problems; the relevant domain agent fixes them. This mirrors real staff-level
   practice and prevents a reviewer from rubber-stamping its own changes.
4. **Ground truth is structurally protected.** A pre-write hook makes it *impossible*
   for any agent to silently mutate the human labels, the golden set, or the
   provenance that every published number depends on.

## The agent team

Defined in [`agents/`](agents). Each is a Markdown file with YAML frontmatter
(`name`, `description`, `tools`, `model`) and a system prompt.

| Agent | Model | Responsibility |
|-------|-------|----------------|
| `evalgen-architect` | opus | System design, ADRs, and cross-cutting trade-offs — taxonomy design, dedup thresholding, clustering choice, **κ protocol**, export/provenance format. Arbitrates module contracts. |
| `ingest-engineer` | sonnet | `ingest/` + `contracts/` — the TraceSpan adapter (sibling repo synergy), generic JSONL loaders, normalization into typed `LogRecord`, and **redaction at the boundary** hardened against adversarial payloads. |
| `mining-engineer` | sonnet | `dedup/` + `cluster/` — content-hash exact dedup, near-dup cosine above a **measured threshold** (transitive chains handled deterministically), HDBSCAN, stratified per-cluster sampling, dedup/coverage reports. |
| `judge-engineer` | sonnet | `label/` — the LLM judge via **SDK structured output**, the typed taxonomy, the leak-guarded few-shot store, and the deterministic fake judge that keeps the suite offline. The judge stays **blind to human labels**. |
| `stats-scientist` | opus | `validate/` — **Cohen's κ global + per-class (with support), bootstrap CI95 on paired labels**, disagreement drill-down, contamination guards, the human-label subset workflow. The repo's headline signal. |
| `pipeline-engineer` | sonnet | `export/`, the CLI (`python -m evalgen`), `config.py`, Makefile wiring — **byte-identical exports**, complete `meta.json` provenance, the contamination guard that aborts a leaky export. |
| `adversarial-reviewer` | opus | **Read-only** red-team. Attacks the diff for dedup misses, κ gaming, judge↔human↔export leakage, redaction bypasses, nondeterminism, provenance gaps, SDK misuse. Emits severity-tagged findings; edits nothing. |
| `docs-historian` | haiku | Keeps `docs/architecture.md`, ADRs, and the README headline-κ block truthful to the code and to the latest **reproducible** numbers. |

**Why this model split?** `stats-scientist`, `evalgen-architect`, and
`adversarial-reviewer` are opus because the repo's credibility rests on statistics
that must be correct and on leakage/determinism properties that must actually hold —
exactly where a quiet error is fatal. Implementation throughput runs on sonnet;
high-volume documentation prose runs on haiku, escalating the actual decisions back
to `evalgen-architect`.

## Orchestration commands

Defined in [`commands/`](commands). They chain the agents into the way a senior
engineer actually works.

| Command | What it orchestrates |
|---------|----------------------|
| `/implement-feature` | **Design → implement → test → red-team → document.** Architect frames the design (+ ADR if load-bearing), the matching stage owner implements with offline tests, the adversarial reviewer attacks the diff, the historian syncs docs. **Stops for human sign-off before any commit.** |
| `/eval-report` | Runs `make agreement` and produces the defensible report — κ global + per-class with support, bootstrap CI95 on paired labels, disagreement drill-down, contamination + provenance checks — then refreshes the README headline block. **Refuses to publish numbers that aren't reproducible from `make agreement`.** |
| `/adr-new <topic>` | Scaffolds a numbered Architecture Decision Record (context, options weighed, decision, consequences) and cross-links it from `architecture.md`. |
| `/adversarial-review` | Runs a focused red-team pass over the current `git diff` before committing, targeting the dedup/leakage/statistics/determinism failure modes specific to eval-dataset mining. |
| `/repro-audit` | Verifies **byte-identical reproducibility**: re-runs the pipeline on committed fixtures, byte-diffs `golden.jsonl` + `meta.json` against the committed reference, and re-checks that seeded κ/CI95 regenerate bit-exact — flagging any drift as a regression. |

## Hooks

Defined in [`hooks/`](hooks) and wired in `settings.json`. All are Python scripts that
read the hook event JSON from stdin and **degrade gracefully** (exit 0 silently) when
optional tooling isn't installed — so they never block a fresh checkout.

| Hook | Event | Why it earns its place |
|------|-------|------------------------|
| `format_python.py` | PostToolUse · `Edit\|Write` | Auto-runs `ruff --fix` + `black` on the changed file, so the adversarial reviewer reviews **real logic, not formatting noise**. |
| `protect_golden_and_secrets.py` | PreToolUse · `Edit\|Write` | **Blocks** (exit 2) any write to `data/labels/human_labels*.jsonl` (the human ground truth), any `golden*.jsonl`, `.env*`, or provenance `meta.json`. An agent that could rewrite the human labels could **fabricate its own κ** — this makes it structurally impossible. |
| `eval_guard.py` | Stop | If anything under `src/evalgen/{ingest,dedup,cluster,label,validate}/` changed, reminds (exit 2) to re-run `make test` + `/eval-report` before claiming results — so no metric-bearing change ships with a stale κ. |

## Why this yields higher-quality, reviewable output

- **Right specialist, right depth.** Each task is handled by the agent that owns that
  pipeline stage, at a reasoning budget matched to its blast radius — opus where a bug
  is fatal, sonnet/haiku where throughput matters.
- **Adversarial review is built into the loop**, not an afterthought. Every feature is
  attacked by a skeptical opus reviewer before a human is asked to approve, and the
  reviewer is read-only so review and fix never collapse into the same step.
- **The headline number can't be faked.** The eval command refuses non-reproducible
  numbers, the judge is structurally blind to the ground truth, and the protect hook
  locks the human labels and provenance. The κ a recruiter sees is, by construction,
  reproducible from `make agreement`.
- **Decisions are durable.** Load-bearing trade-offs become ADRs, and documentation is
  kept in lock-step with the code and the latest reproducible metrics.

Full operational detail (commands, standards, the Anthropic SDK rules) lives in
[`../CLAUDE.md`](../CLAUDE.md). Personal/machine-specific overrides belong in
`settings.local.json`, which is gitignored.
