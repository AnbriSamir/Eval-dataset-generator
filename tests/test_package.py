"""Phase 0+ invariants: package layout and module-boundary hygiene.

Boundary-grep convention: only MODULE-TOP imports count — lines starting with
``from``/``import`` at column 0. ``dedup/calibrate.py``'s ``main()`` imports the
concrete embedder INSIDE the function (the one sanctioned composition-layer exception,
documented in its module docstring); an indented import is invisible to these greps
on purpose.

EXCEPTION — ``label/`` blindness tests walk the AST at EVERY depth (red-team F-3): a
function-level ``import evalgen.validate`` would slip past a column-0 grep, and the
judge boundary is load-bearing in a way the calibrate composition exception is not.
Dynamically-constructed import strings remain out of reach of ANY static check; the
real guarantee stays the two-string ``Judge`` Protocol, which cannot transport a
human label (ADR-0003 rule 3) — the AST walk is the hardened second layer.
"""

import ast
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


# ---------------------------------------------------- Phase 3: structural blindness
# ADR-0003 rule 9 — the judge is blind by imports, paths, and exports, not by promise.


def _imported_modules_any_depth(path: pathlib.Path) -> list[str]:
    """Every module name imported ANYWHERE in the file — module top, function bodies,
    conditionals: ``ast.walk`` sees every nesting depth, so formatting tricks and
    lazy function-level imports cannot evade the check (red-team F-3). Covers
    ``import X``, ``from X import Y`` (both ``X`` and ``X.Y`` — catching
    ``from evalgen import validate``), and aliased forms."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_label_imports_no_pipeline_sibling() -> None:
    # label -> contracts only: no mining module, and NEVER validate (human labels).
    # AST-walked at every depth — see the module docstring's label exception.
    forbidden = ("ingest", "dedup", "cluster", "validate", "export", "demo")
    for path in sorted((SRC / "label").glob("*.py")):
        for module in _imported_modules_any_depth(path):
            for name in forbidden:
                assert not module.startswith(f"evalgen.{name}"), f"{path.name}: {module}"


def test_anthropic_is_confined_to_the_real_judge_module() -> None:
    # The SDK import lives in exactly one file, which label/__init__ never imports —
    # importing the package (tests, demo) therefore never imports the SDK. AST-walked
    # at every depth so a lazy in-function SDK import cannot hide elsewhere.
    for path in sorted((SRC / "label").glob("*.py")):
        for module in _imported_modules_any_depth(path):
            if module == "anthropic" or module.startswith("anthropic."):
                assert path.name == "anthropic_judge.py", f"{path.name}: {module}"


def test_label_never_references_the_human_label_store() -> None:
    # Path-level blindness: label/ takes records as arguments; it must not even NAME
    # the human ground-truth location (ADR-0003 rule 9, layer 3).
    for path in sorted((SRC / "label").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "data/labels" not in text, path
        assert "human_label" not in text, path


def test_label_package_does_not_export_the_real_judge() -> None:
    # AnthropicJudge is reached only by an explicit deep import at the composition
    # layer — no test or demo path can construct it by accident.
    import evalgen.label as label

    assert not hasattr(label, "AnthropicJudge")


# ------------------------------------------------- Phase 4: validate/ boundaries
# ADR-0004 rule 7 — validate reads both raters and only measures: contracts-only
# imports (AST-walked at every depth, the label battery pattern), and NO write
# capability of any kind (an agent that could write through validate could write
# the ground truth it is supposed to measure against).


def test_validate_imports_only_contracts() -> None:
    # validate -> contracts (+ numpy/stdlib) ONLY: no pipeline sibling, no config
    # (knobs are injected by the composition layer), no demo modules.
    forbidden = ("ingest", "dedup", "cluster", "label", "export", "demo", "config")
    for path in sorted((SRC / "validate").glob("*.py")):
        for module in _imported_modules_any_depth(path):
            for name in forbidden:
                assert not module.startswith(f"evalgen.{name}"), f"{path.name}: {module}"


def test_validate_never_writes() -> None:
    # The no-write rule, pinned by grep: file reads go through Path.read_text; any
    # open( / write_text / .write( / to_file appearing under validate/ is a defect.
    for path in sorted((SRC / "validate").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in ("open(", "write_text", ".write(", "to_file"):
            assert token not in text, f"{path.name} contains {token!r}"


def test_validate_and_agreement_demo_never_import_anthropic() -> None:
    # Phase 4 is 100 % offline: the SDK must not leak into measurement code.
    paths = sorted((SRC / "validate").glob("*.py")) + [SRC / "agreement_demo.py"]
    for path in paths:
        for module in _imported_modules_any_depth(path):
            assert module != "anthropic", f"{path.name}: {module}"
            assert not module.startswith("anthropic."), f"{path.name}: {module}"


def test_nothing_imports_agreement_demo() -> None:
    # Composition layer, demo pattern: it imports the pipeline, nothing imports it.
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "agreement_demo.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert "evalgen.agreement_demo" not in text, path
        assert "import agreement_demo" not in text, path


def test_agreement_axis_mirrors_taxonomy() -> None:
    # The kappa axes ARE the taxonomy axes — names AND order; drift between the
    # questionnaire and the measurement vocabulary must fail loudly.
    from evalgen.contracts import TAXONOMY_V1, AgreementAxis

    assert tuple(member.value for member in AgreementAxis) == tuple(
        axis.name for axis in TAXONOMY_V1.axes
    )
