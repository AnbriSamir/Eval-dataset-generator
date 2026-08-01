"""The ONLY file-writing module in ``export/`` (ADR-0005 rule 4, grep-pinned).

Bytes only, UTF-8, LF preserved — never platform text mode (CRLF on Windows would
break byte-identical exports). Each file goes through a same-directory temp file +
atomic replace, so a crash mid-write can never leave a truncated artifact wearing
the real name.

Pair discipline (ADR-0005 Amendment (c), red-team MINOR-2): BOTH temp files are
fully staged before EITHER rename happens — a failure while producing bytes leaves
the previous ``golden.jsonl``/``meta.json`` pair intact and no temp behind. The
residual window (a crash between the two renames tears the pair) is stated, not
hidden: a torn pair is always DETECTABLE because ``meta.json`` binds
``golden_jsonl_sha256`` to the exact golden bytes it certifies — the digests of a
mixed-generation pair do not match, and ``/repro-audit`` regenerates both.

Runtime Python writes to the gitignored ``data/out/`` (or a test's ``tmp_path``)
are the sanctioned path — the protect hook gates agent tools, not this process.
"""

from __future__ import annotations

from pathlib import Path

GOLDEN_BASENAME = "golden.jsonl"
META_BASENAME = "meta.json"


def write_export(out_dir: Path, *, golden_text: str, meta_text: str) -> tuple[Path, Path]:
    """Write golden.jsonl + meta.json under ``out_dir``; return the two paths.

    Stage-both-then-replace-both: no rename happens until both byte payloads sit
    complete on disk, and a staging failure unlinks both temps and re-raises —
    the previously published pair survives untouched.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    golden_path = out_dir / GOLDEN_BASENAME
    meta_path = out_dir / META_BASENAME
    golden_tmp = golden_path.with_name(golden_path.name + ".tmp")
    meta_tmp = meta_path.with_name(meta_path.name + ".tmp")
    try:
        golden_tmp.write_bytes(golden_text.encode("utf-8"))
        meta_tmp.write_bytes(meta_text.encode("utf-8"))
    except BaseException:
        golden_tmp.unlink(missing_ok=True)
        meta_tmp.unlink(missing_ok=True)
        raise
    golden_tmp.replace(golden_path)
    meta_tmp.replace(meta_path)
    return golden_path, meta_path
