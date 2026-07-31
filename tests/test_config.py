"""Phase 0 invariants: the configuration surface is sane and deterministic.

Every knob that shapes an exported dataset lives in config.py — these tests pin
the invariants that downstream phases (and the provenance writer) rely on.
"""

import pytest
from pydantic import ValidationError

from evalgen.config import Settings, get_settings


def test_defaults_are_in_valid_ranges() -> None:
    s = Settings(_env_file=None)
    assert 0.0 < s.near_dup_threshold <= 1.0  # cosine similarity domain
    assert 0.0 <= s.min_export_kappa <= 1.0  # kappa domain (we only gate on positive agreement)
    assert s.min_cluster_size >= 2  # a "cluster" of 1 is noise by definition
    assert s.min_human_labels >= 30  # below this, kappa is not reportable (config.py rationale)
    assert s.bootstrap_resamples >= 1_000  # CI95 needs enough resamples to be stable
    assert s.max_labels_per_run > 0
    assert s.hash_embedding_dim >= 64


def test_seed_matches_portfolio_convention() -> None:
    # The sibling repos pin 1750 as the base seed; provenance comparability
    # across the portfolio depends on the same default here.
    assert Settings(_env_file=None).seed == 1750


def test_judge_models_are_pinned_anthropic_ids() -> None:
    s = Settings(_env_file=None)
    assert s.judge_model.startswith("claude-")
    assert s.judge_model_fast.startswith("claude-")
    # The correctness-sensitive default must be the opus tier (CLAUDE.md §2).
    assert "opus" in s.judge_model


def test_get_settings_is_cached_singleton() -> None:
    assert get_settings() is get_settings()


def test_env_override_via_prefix(monkeypatch) -> None:
    monkeypatch.setenv("EVALGEN_SEED", "42")
    monkeypatch.setenv("EVALGEN_NEAR_DUP_THRESHOLD", "0.8")
    s = Settings(_env_file=None)
    assert s.seed == 42
    assert s.near_dup_threshold == 0.8


# Red-team MINOR-3: config is the single source of truth, so IT refuses bad knobs at
# load time, naming the knob — never an opaque ValidationError deep inside a stage.


def test_zero_sample_size_refuses_at_load_time(monkeypatch) -> None:
    monkeypatch.setenv("EVALGEN_SAMPLE_SIZE", "0")
    with pytest.raises(ValidationError, match="sample_size"):
        Settings(_env_file=None)


def test_min_cluster_size_below_two_refuses_at_load_time(monkeypatch) -> None:
    monkeypatch.setenv("EVALGEN_MIN_CLUSTER_SIZE", "1")
    with pytest.raises(ValidationError, match="min_cluster_size"):
        Settings(_env_file=None)


def test_zero_label_budget_refuses_at_load_time(monkeypatch) -> None:
    # A zero budget would silently label nothing (everything skipped_budget) instead
    # of failing — the knob carries its contract's bound (LabelingReport.max_labels ge=1).
    monkeypatch.setenv("EVALGEN_MAX_LABELS_PER_RUN", "0")
    with pytest.raises(ValidationError, match="max_labels_per_run"):
        Settings(_env_file=None)


def test_out_of_range_threshold_refuses_at_load_time(monkeypatch) -> None:
    # 7.0 would silently disable near-dup (every cosine < 7.0) rather than fail.
    monkeypatch.setenv("EVALGEN_NEAR_DUP_THRESHOLD", "7.0")
    with pytest.raises(ValidationError, match="near_dup_threshold"):
        Settings(_env_file=None)
    monkeypatch.setenv("EVALGEN_NEAR_DUP_THRESHOLD", "-1.5")
    with pytest.raises(ValidationError, match="near_dup_threshold"):
        Settings(_env_file=None)
