"""The writer battery (ADR-0005 rule 4 + Amendment (c), red-team MINOR-2): each
file is temp+rename atomic, and the PAIR is fully staged before either rename —
a failure while producing bytes leaves the previously published pair byte-intact
and no temp behind. The residual window (a crash between the two renames) is
stated in the writer's docstring, and a torn pair stays detectable through
meta.json's ``golden_jsonl_sha256`` binding.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evalgen.export import write_export
from evalgen.export.writer import GOLDEN_BASENAME, META_BASENAME

V1_GOLDEN = '{"v":1}\n'
V1_META = '{"deterministic":{"v":1},"volatile":null}\n'
V2_GOLDEN = '{"v":2}\n'
V2_META = '{"deterministic":{"v":2},"volatile":null}\n'


def test_happy_path_writes_both_files_and_no_temps(tmp_path) -> None:
    golden_path, meta_path = write_export(tmp_path, golden_text=V1_GOLDEN, meta_text=V1_META)
    assert (golden_path.name, meta_path.name) == (GOLDEN_BASENAME, META_BASENAME)
    assert golden_path.read_bytes() == V1_GOLDEN.encode("utf-8")
    assert meta_path.read_bytes() == V1_META.encode("utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([GOLDEN_BASENAME, META_BASENAME])


def test_minor2_staging_failure_leaves_previous_pair_intact(tmp_path, monkeypatch) -> None:
    """Red-team MINOR-2 replayed: the process dies while producing the SECOND
    artifact's bytes. Pre-fix, ``golden.jsonl`` had already been replaced — a new
    dataset beside a stale ``meta.json`` (provenance mismatch). Post-fix, nothing
    is renamed until BOTH temps are staged: the v1 pair survives byte-intact and
    no ``*.tmp`` is left behind."""
    write_export(tmp_path, golden_text=V1_GOLDEN, meta_text=V1_META)

    real_write_bytes = Path.write_bytes

    def dying_write_bytes(self: Path, data) -> int:  # noqa: ANN001 — mirrors Path.write_bytes
        if self.name == META_BASENAME + ".tmp":
            raise OSError("simulated crash while staging meta.json")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", dying_write_bytes)
    with pytest.raises(OSError, match="simulated crash"):
        write_export(tmp_path, golden_text=V2_GOLDEN, meta_text=V2_META)

    assert (tmp_path / GOLDEN_BASENAME).read_bytes() == V1_GOLDEN.encode("utf-8")
    assert (tmp_path / META_BASENAME).read_bytes() == V1_META.encode("utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([GOLDEN_BASENAME, META_BASENAME])
