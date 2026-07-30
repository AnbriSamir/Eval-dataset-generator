"""Phase 0 invariants: package layout and module-boundary hygiene."""

import importlib

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
    import pathlib

    text = "\n".join(p.read_text(encoding="utf-8") for p in pathlib.Path(source).glob("*.py"))
    for name in forbidden:
        assert f"from evalgen.{name}" not in text
        assert f"import evalgen.{name}" not in text
