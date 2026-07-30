#!/usr/bin/env python
"""Stop hook: remind to re-measure when metric-bearing orchestration logic changed.

Contract (Claude Code hooks):
  - Fires when Claude finishes responding (Stop event).
  - Reads the hook event JSON from stdin.
  - Uses `git diff --name-only` (working tree vs HEAD, plus staged changes) to
    see whether any file under src/evalgen/{ingest,dedup,cluster,label,validate}/
    changed.
  - If so, it exits 2 (Stop hooks can use exit 2 to keep Claude working) and
    writes a reminder to stderr: a metric-bearing change must be re-measured
    (`make test` + /eval-report) before any results are claimed. This prevents
    shipping an ingest/dedup/cluster/label/validate change without re-running
    the agreement (kappa) pipeline.
  - Degrades gracefully: not a git repo, git unavailable, malformed stdin, or
    any unexpected error -> exit 0 silently.

  Loop-safety: to avoid nagging forever, the hook records the set of
  changed metric-paths it last reminded about (in a small marker file under the
  scratch/state dir). If nothing new changed since the last reminder, it stays
  quiet (exit 0). A genuinely new change re-arms the reminder.

Windows-safe: invoked as `python .../eval_guard.py`; calls git via subprocess
(no shell), tolerates git being absent. No bash-only syntax.

Exit codes:
  0  -> nothing relevant changed, or git unavailable, or already reminded.
  2  -> retrieval/eval/verification changed; stderr reminder is fed back to Claude.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath

# Directories whose changes mean "the numbers may have moved -- re-measure".
WATCHED_PREFIXES = (
    "src/evalgen/ingest/",     # normalization/redaction -> what enters every downstream number
    "src/evalgen/dedup/",      # dedup thresholds -> dataset composition & inflation risk
    "src/evalgen/cluster/",    # clustering/sampling -> coverage numbers
    "src/evalgen/label/",      # judge prompts/taxonomy -> kappa moves
    "src/evalgen/validate/",   # kappa / CI95 computation itself
)


def _read_event() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _project_dir(event: dict) -> Path:
    cwd = event.get("cwd")
    if isinstance(cwd, str) and cwd:
        try:
            return Path(cwd)
        except Exception:
            pass
    return Path.cwd()


def _git_changed_files(project_dir: Path) -> list[str] | None:
    """Return changed paths (unstaged + staged + NEW untracked) relative to repo
    root, or None if git is unavailable / this isn't a repo.

    Untracked files matter: an agent that just `Write`s a brand-new module under
    src/rag/retrieval/ has not `git add`-ed it yet, so `git diff` alone would miss
    the most common post-agent case. `ls-files --others --exclude-standard` covers it."""
    paths: set[str] = set()
    for args in (
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=str(project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
                text=True,
            )
        except Exception:
            # git not installed / not a repo / timed out -> degrade silently.
            return None
        if proc.returncode != 0:
            return None
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line:
                paths.add(line.replace("\\", "/"))
    return sorted(paths)


def _matches_watched(rel_path: str) -> bool:
    unified = str(PurePosixPath(rel_path.replace("\\", "/")))
    return any(unified.startswith(prefix) for prefix in WATCHED_PREFIXES)


def _state_file(project_dir: Path) -> Path:
    # Keep runtime state OUT of the committed .claude/ dir of a public repo: write a
    # per-project marker under the OS temp dir instead, so nothing churns in git.
    import tempfile

    key = hashlib.sha256(str(project_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / f"eval_guard_{key}.state"


def _fingerprint(paths: list[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(paths)).encode("utf-8")).hexdigest()
    return digest


def _already_reminded(project_dir: Path, fingerprint: str) -> bool:
    try:
        prev = _state_file(project_dir).read_text(encoding="utf-8").strip()
    except Exception:
        return False
    return prev == fingerprint


def _record_reminder(project_dir: Path, fingerprint: str) -> None:
    try:
        state = _state_file(project_dir)
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(fingerprint, encoding="utf-8")
    except Exception:
        # Best effort; if we can't persist state we'll just remind again.
        pass


def main() -> int:
    event = _read_event()

    # If a stop hook is already continuing the loop, don't pile on.
    if event.get("stop_hook_active") is True:
        return 0

    project_dir = _project_dir(event)

    changed = _git_changed_files(project_dir)
    if changed is None:
        return 0  # not a git repo / git unavailable -> silent

    watched = [p for p in changed if _matches_watched(p)]
    if not watched:
        return 0

    fingerprint = _fingerprint(watched)
    if _already_reminded(project_dir, fingerprint):
        # Same set of metric-bearing changes we already flagged -> stay quiet.
        return 0

    _record_reminder(project_dir, fingerprint)

    listing = "\n".join("  - " + p for p in watched)
    sys.stderr.write(
        "Metric-bearing pipeline logic changed in this working tree:\n"
        + listing
        + "\n\n"
        "These files drive the repo's headline numbers (Cohen's kappa vs human "
        "labels, dedup/coverage reports, export provenance). Re-measure before "
        "claiming any results:\n"
        "  1. Run `make test` (the pytest suite).\n"
        "  2. Run the /eval-report command to regenerate the agreement numbers "
        "(kappa global + per-class, bootstrap CI95).\n"
        "Do not report metrics that were not produced by a fresh pipeline run on "
        "this changed code.\n"
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A Stop guard must never wedge the session.
        sys.exit(0)
