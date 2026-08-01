"""THE canonical serialization recipes — pinned by tests, used nowhere else
(ADR-0005 rule 4).

golden.jsonl line recipe: ``json.dumps(model_dump(mode="json"), ensure_ascii=False,
sort_keys=True, separators=(",", ":")) + "\\n"`` — UTF-8, LF only, one trailing
newline at EOF, no BOM. No float field exists in a line (labels are enums, counts
are ints), so repr drift is structurally absent; ``ensure_ascii=False`` keeps the
French corpus readable and its UTF-8 bytes deterministic.

meta.json recipe: the same canonical dump with ``indent=2`` (meta.json is for
humans too), wrapped as ``{"deterministic": …, "volatile": …}`` — the volatile
section is excluded from byte comparison BY CONSTRUCTION OF THE LAYOUT, not by a
field-exception list. ``canonical_deterministic_bytes`` is the ONE ``/repro-audit``
path: it re-dumps the deterministic section through the same private function the
writer used, so there is no second serializer to drift.
"""

from __future__ import annotations

import hashlib
import json

from evalgen.contracts import ExportManifest, ExportOutcome


def sha256_hex(data: bytes | str) -> str:
    """sha256 hexdigest of raw bytes (str is encoded UTF-8 first)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _dump_canonical(payload: object) -> str:
    """THE canonical human-readable dump — shared by render_meta_json and
    canonical_deterministic_bytes so the audit path IS the writer path."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)


def render_golden_jsonl(outcome: ExportOutcome) -> str:
    """One canonical compact JSON line per golden record, in stored order."""
    return "".join(
        json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for record in outcome.golden_records
    )


def render_meta_json(manifest: ExportManifest) -> str:
    """The two-section meta.json text: deterministic + quarantined volatile."""
    volatile = manifest.volatile.model_dump(mode="json") if manifest.volatile is not None else None
    wrapper = {
        "deterministic": manifest.model_dump(mode="json", exclude={"volatile"}),
        "volatile": volatile,
    }
    return _dump_canonical(wrapper) + "\n"


def canonical_deterministic_bytes(meta_text: str) -> bytes:
    """The ``/repro-audit`` byte-compare target: the deterministic section alone,
    re-dumped with the exact writer recipe, UTF-8-encoded, trailing LF."""
    deterministic = json.loads(meta_text)["deterministic"]
    return (_dump_canonical(deterministic) + "\n").encode("utf-8")
