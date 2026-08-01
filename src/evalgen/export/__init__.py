"""golden.jsonl + meta.json provenance: the gate, the contamination guard, the
canonical serializers, and the sole writer (ADR-0005).

Three load-bearing choices live here:

1. **Contamination is unrepresentable.** The guard recomputes every candidate's
   canonical ``content_hash`` against the hashes the judge ACTUALLY saw and blocks
   collisions as typed, counted entries — and the ``ExportOutcome`` contract refuses
   to carry a contaminated export at all (``export ∩ few-shots = ∅`` by validator).
2. **The gate override is loud, scoped, and printed.** Only the κ-threshold check
   can be overridden, only with a mandatory reason, and the honest low κ rides the
   export's face — a silent bypass would *launder* a low κ (ADR-0005 context §4).
3. **Volatility is quarantined.** Every function here is pure; wall clock, git SHA
   and environment are injected by the composition layer into meta.json's
   ``volatile`` section, so ``/repro-audit`` byte-diffs the deterministic section
   with no field-exception list.

Import DAG: ``export → contracts`` (+ stdlib) only — AST-pinned; ``writer.py`` is
the single module with write operations (grep-pinned).
"""

from evalgen.export import errors
from evalgen.export.assemble import assemble_export
from evalgen.export.gate import evaluate_export_gate
from evalgen.export.render import render_export_report
from evalgen.export.serialize import (
    canonical_deterministic_bytes,
    render_golden_jsonl,
    render_meta_json,
    sha256_hex,
)
from evalgen.export.writer import write_export

__all__ = [
    "assemble_export",
    "canonical_deterministic_bytes",
    "errors",
    "evaluate_export_gate",
    "render_export_report",
    "render_golden_jsonl",
    "render_meta_json",
    "sha256_hex",
    "write_export",
]
