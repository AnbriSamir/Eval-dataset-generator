"""Deterministic embeddings (hashing default, pluggable real) + HDBSCAN +
stratified coverage sampling (ADR-0002 rules 5-7).
"""

from evalgen.cluster.clustering import cluster_records
from evalgen.cluster.embeddings import HashingEmbedder
from evalgen.cluster.sampling import stratified_sample

__all__ = [
    "HashingEmbedder",
    "cluster_records",
    "stratified_sample",
]
