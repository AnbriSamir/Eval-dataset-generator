"""Single source of configuration truth (Pydantic settings).

Every threshold, seed and budget that shapes the exported dataset lives HERE —
never as a magic constant buried in a module. The provenance writer copies the
active settings into ``meta.json`` so any exported dataset can be traced back to
the exact knobs that produced it (same discipline as the sibling repos'
bit-exact reproducibility).
"""

from __future__ import annotations

from functools import lru_cache

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
    near_dup_threshold: float = 0.92

    # --------------------------------------------------------------- cluster
    #: HDBSCAN minimum cluster size (records); smaller clusters become noise.
    min_cluster_size: int = 5
    #: Dimensionality of the deterministic hashing embedder used in tests/CI.
    hash_embedding_dim: int = 512

    # ---------------------------------------------------------------- label
    #: Max records auto-labeled per run (cost guard).
    max_labels_per_run: int = 500

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
