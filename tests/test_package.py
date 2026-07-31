"""Phase 0+ invariants: package layout and module-boundary hygiene.

Boundary-grep convention: only MODULE-TOP imports count — lines starting with
``from``/``import`` at column 0. ``dedup/calibrate.py``'s ``main()`` imports the
concrete embedder INSIDE the function (the one sanctioned composition-layer exception,
documented in its module docstring); an indented import is invisible to these greps
on purpose.
"""

import importlib
import pathlib
import re

import evalgen

MODULES = [
    "evalgen.contracts",
    "evalgen.ingest",
    "evalgen.dedup",
    "evalgen.cluster",
    "evalgen.label",
    "evalgen.validate",
    "evalgen.export",
    "evalgen.config",
]

SRC = pathlib.Path(evalgen.__file__).resolve().parent


def _module_top_imports(package: str) -> list[str]:
    """Column-0 import lines of every module in src/evalgen/<package>/."""
    lines: list[str] = []
    for path in sorted((SRC / package).glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^(from|import)\s", line):
                lines.append(line)
    return lines


def test_version_is_pinned() -> None:
    assert evalgen.__version__ == "0.1.0"


def test_all_modules_import_offline() -> None:
    # The whole package must import with no API key, no network, no optional
    # heavy deps loaded at import time (lazy-import discipline starts Phase 1).
    for name in MODULES:
        importlib.import_module(name)


def test_contracts_imports_no_sibling_module() -> None:
    # Module-boundary rule (CLAUDE.md §3): contracts is imported by everyone
    # and imports no one. Guard it from day 0 so the rule never silently rots.
    import evalgen.contracts as contracts

    source = (contracts.__file__ or "").replace("__init__.py", "")
    forbidden = ("ingest", "dedup", "cluster", "label", "validate", "export")

    text = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path(source).glob("*.py"))
    for name in forbidden:
        assert f"from evalgen.{name}" not in text
        assert f"import evalgen.{name}" not in text


def test_dedup_imports_only_contracts() -> None:
    # ADR-0002 import DAG: dedup -> contracts (+ stdlib/numpy). The embedder arrives
    # INJECTED via the Protocol — dedup importing cluster would run against the
    # pipeline flow. (calibrate.main()'s inner import is indented — see module docstring.)
    forbidden = ("ingest", "cluster", "label", "validate", "export", "demo")
    for line in _module_top_imports("dedup"):
        for name in forbidden:
            assert f"evalgen.{name}" not in line, line


def test_cluster_imports_only_contracts() -> None:
    # ADR-0002 import DAG: cluster -> contracts (+ numpy, sklearn).
    forbidden = ("ingest", "dedup", "label", "validate", "export", "demo")
    for line in _module_top_imports("cluster"):
        for name in forbidden:
            assert f"evalgen.{name}" not in line, line


def test_nothing_imports_the_demo() -> None:
    # The demo is a composition layer: it imports the pipeline, nothing imports it.
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "demo.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "evalgen.demo" not in text, path
        assert "import demo" not in text, path


def test_phase2_modules_never_import_anthropic() -> None:
    # Phase 2 is 100 % offline: the judge SDK must not leak into mining code.
    for package in ("contracts", "dedup", "cluster"):
        for line in _module_top_imports(package):
            assert "anthropic" not in line, line
    demo_text = (SRC / "demo.py").read_text(encoding="utf-8")
    assert "anthropic" not in demo_text
