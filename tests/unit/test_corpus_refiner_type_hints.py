"""Regression guard: every public function in ``tools/corpus_refiner.py`` is typed.

``corpus_refiner.py`` exposes the refiner engine (``refine_corpus``,
``refine_corpus_programmatic`` and its helpers) plus a CLI surface
(``parse_args``, ``main``). The engine functions are already fully annotated;
this test pins the same convention onto the whole module's public surface so the
annotations cannot silently regress.

The check is intentionally **AST/text based** rather than import based: the module
imports the Claude Agent SDK and the optional ``logfire`` package and performs
module-level setup, so parsing the source as text lets us assert the annotations
exist without executing any imports (the module is not cheaply importable here).
"""
from __future__ import annotations

import ast
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "universal_agent"
    / "tools"
    / "corpus_refiner.py"
)

# Known module-level public surface. Pinning the set keeps the guard meaningful
# even if a function is renamed/removed.
EXPECTED_PUBLIC_FUNCS = {
    "parse_article_file",
    "extract_batch",
    "generate_report_outline",
    "refine_corpus",
    "parse_args",
    "main",
    "refine_corpus_programmatic",
}


def _module_level_public_functions() -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]


def test_expected_public_functions_present() -> None:
    found = {fn.name for fn in _module_level_public_functions()}
    missing = EXPECTED_PUBLIC_FUNCS - found
    assert not missing, (
        f"expected public functions not found in corpus_refiner.py: {sorted(missing)}"
    )


def test_all_public_functions_have_full_type_hints() -> None:
    offenders: list[str] = []
    for fn in _module_level_public_functions():
        if fn.returns is None:
            offenders.append(f"{fn.name}: missing return annotation")
        args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
        for arg in args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                offenders.append(f"{fn.name}: parameter '{arg.arg}' missing annotation")
    assert not offenders, (
        "untyped public signatures in tools/corpus_refiner.py:\n" + "\n".join(offenders)
    )
