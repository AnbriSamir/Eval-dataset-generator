# eval-dataset-generator — reproducible entry points.
# Agents and humans drive the repo through these targets (CLAUDE.md §4).

.PHONY: install test lint fmt typecheck demo agreement export annotate

install:
	pip install -e ".[dev]"

test:
	python -m pytest

lint:
	python -m ruff check src tests
	python -m black --check src tests

fmt:
	python -m ruff check --fix src tests
	python -m black src tests

typecheck:
	python -m mypy src

# ---- pipeline targets (wired up as the phases land) ------------------------

demo:  ## End-to-end pipeline on committed fixture logs — offline, deterministic
	python -m evalgen.demo

# Phase 4: offline synthetic proof — FakeJudge + committed synthetic annotations,
# golden-pinned (ADR-0004 options §7). The real-data path lands with the Phase 5 CLI
# behind an explicit flag — never autodetection.
agreement:  ## Cohen's kappa + CI95 of judge vs human labels — the headline number
	python -m evalgen.agreement_demo

# Phase 5: offline machinery proof — on the committed fixtures the gate genuinely
# blocks (kappa 0.513109 < 0.6) and the demo exports via the explicit, loudly-rendered
# override (ADR-0005 options §5). Writes to gitignored data/out/; golden-pinned.
export:  ## Produce golden.jsonl + meta.json provenance in data/out/ — offline, deterministic
	python -m evalgen.export_demo

# Phase 6: the real labeling session. `make annotate` emits the fillable template +
# instructions (offline, deterministic, zero judge info) into data/annotation/ —
# NEVER data/out/, which holds judge verdicts (the CLI refuses a directory that does);
# the human fills the template OUTSIDE any agent and saves it as
# data/labels/human_labels.jsonl (hook-protected).
# The real kappa run is NOT a make target on purpose — explicit flags only, never
# autodetection (README roadmap):
#   python -m evalgen.agreement_run --labels data/labels/human_labels.jsonl --judge anthropic
annotate:  ## Emit annotation_template.jsonl + annotation_instructions.txt into data/annotation/
	python -m evalgen.annotation_cli
