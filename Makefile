# eval-dataset-generator — reproducible entry points.
# Agents and humans drive the repo through these targets (CLAUDE.md §4).

.PHONY: install test lint fmt typecheck demo agreement export

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

export:  ## Phase 5+: golden.jsonl + meta.json provenance from a run
	@echo "make export lands in Phase 5 (export + contamination guard + provenance)." && exit 1
