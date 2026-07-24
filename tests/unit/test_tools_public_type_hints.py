"""Regression guard: every public module-level function in the ``tools`` helpers is typed.

Mirrors ``tests/unit/test_tgtg_public_type_hints.py``: pin the public API of the
``tools`` helper modules to "fully annotated" so the signatures cannot silently
regress.

AST/text based (not import based) for the same reason as the tgtg test:
``tools/corpus_refiner.py`` imports third-party packages (``httpx``, ``yaml``,
``claude_agent_sdk``) that may be unavailable or slow to import in CI, so we
parse the source as text and assert the annotations exist without executing any
imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

TOOLS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "universal_agent" / "tools"
)

# Modules whose public-API surface we pin to "fully annotated".
MODULES = ["context_logging.py", "corpus_refiner.py"]


def _module_level_public_functions(
    module_rel: str,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((TOOLS_DIR / module_rel).read_text(encoding="utf-8"))
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]


def _collect_offenders() -> dict[str, list[str]]:
    offenders: dict[str, list[str]] = {}
    for module_rel in MODULES:
        for fn in _module_level_public_functions(module_rel):
            issues: list[str] = []
            if fn.returns is None:
                issues.append(f"{fn.name}: missing return annotation")
            args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            for arg in args:
                if arg.arg in ("self", "cls"):
                    continue
                if arg.annotation is None:
                    issues.append(f"{fn.name}: parameter \'{arg.arg}\' missing annotation")
            if issues:
                offenders.setdefault(module_rel, []).extend(issues)
    return offenders


def test_tools_public_functions_have_full_type_hints() -> None:
    offenders = _collect_offenders()
    assert not offenders, "untyped public signatures in tools/ package:\n" + "\n".join(
        f"  {mod}: {issue}" for mod, issues in offenders.items() for issue in issues
    )
