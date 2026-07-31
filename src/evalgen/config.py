"""Single source of configuration truth (Pydantic settings).

Every threshold, seed and budget that shapes the exported dataset lives HERE —
never as a magic constant buried in a module. The provenance writer copies the
active settings into ``meta.json`` so any exported dataset can be traced back to
the exact knobs that produced it (same discipline as the sibling repos'
bit-exact reproducibility).

Knobs carry the same bounds their downstream contracts enforce (``Field``
constraints below): a bad value set via env var or ``.env`` fails HERE, at load
time, with a message naming the knob — never as an opaque ``ValidationError``
deep inside a pipeline stage (red-team finding, ADR-0002 amendment).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EVALGEN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------ models
    #: Judge model for auto-labeling (correctness-sensitive -> opus by default).
    judge_model: str = "claude-opus-4-8"
    #: Optional high-volume judge for cheap passes; used only when explicitly chosen.
    judge_model_fast: str = "claude-sonnet-4-6"

    # ------------------------------------------------------------ determinism
    #: Base seed propagated to clustering, sampling and the bootstrap.
    seed: int = 1750  # same base seed convention as the sibling repos

    # ----------------------------------------------------------------- dedup
    #: Cosine similarity at/above which two records are near-duplicates.
    #: The default is a starting point — the real threshold is MEASURED on a
    #: labeled duplicate fixture before any export (ADR to pin the protocol).
    #: Bounds mirror ``DedupReport`` ([-1, 1] — alternate_sign hashing makes
    #: negative cosines possible); a value like 7.0 would silently disable
    #: near-dup instead of failing, hence the load-time refusal.
    near_dup_threshold: float = Field(default=0.92, ge=-1.0, le=1.0)

    # --------------------------------------------------------------- cluster
    #: HDBSCAN minimum cluster size (records); smaller clusters become noise.
    #: ``ge=2`` mirrors ``ClusteringReport`` and sklearn's own requirement.
    min_cluster_size: int = Field(default=5, ge=2)
    #: Dimensionality of the deterministic hashing embedder used in tests/CI.
    hash_embedding_dim: int = 512

    # -------------------------------------------------------------- sampling
    #: Total stratified-sample budget across clusters + noise (make demo scale;
    #: Phase 5 export will surface its own budget knob if needs diverge).
    #: ``ge=1`` mirrors ``SamplingReport.sample_size_requested``.
    sample_size: int = Field(default=50, ge=1)

    # ---------------------------------------------------------------- label
    #: Max records auto-labeled per run (cost guard). Enforced in ``label/engine.py``
    #: identically on the fake and real paths; overrun is visible as ``skipped_budget``
    #: + the full id list in the LabelingReport. ``ge=1`` mirrors
    #: ``LabelingReport.max_labels`` (ADR-0002 amendment discipline: knobs carry the
    #: bounds their downstream contracts enforce).
    max_labels_per_run: int = Field(default=500, ge=1)

    # -------------------------------------------------------------- validate
    #: Minimum human-labeled subset size before a kappa is considered reportable.
    min_human_labels: int = 30
    #: Bootstrap resamples for the CI95 on agreement metrics.
    bootstrap_resamples: int = 10_000

    # ---------------------------------------------------------------- export
    #: Kappa below this value blocks an export by default (may be overridden
    #: deliberately — the export then carries the honest low kappa on its face).
    min_export_kappa: float = 0.6


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor — import this, never instantiate Settings() ad hoc."""
    return Settings()
