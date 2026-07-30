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

demo:  ## Phase 2+: end-to-end pipeline on committed fixture logs (offline, deterministic)
	@echo "make demo lands in Phase 2 (ingest + dedup + cluster on fixtures)." && exit 1

agreement:  ## Phase 4+: Cohen's kappa + CI95 of judge vs human labels — the headline number
	@echo "make agreement lands in Phase 4 (judge + human labels + kappa)." && exit 1

export:  ## Phase 5+: golden.jsonl + meta.json provenance from a run
	@echo "make export lands in Phase 5 (export + contamination guard + provenance)." && exit 1
