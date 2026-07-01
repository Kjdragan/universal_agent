"""Regression guard: every public function in the ``agent_college`` package is typed.

The ``agent_college`` package exposes a small public API (the ``Settings`` /
``get_settings`` config helpers, the ``setup_agent_college`` integrator, the
async ``main`` worker entrypoint, and the Critic/Professor/Scribe/LogfireReader
agent classes). Most of it is already fully type-annotated; this test pins that
convention onto *every* public signature in the package so the annotations
cannot silently regress.

The check is intentionally **AST/text based** rather than import based:
``runner.py`` performs ``sys.path`` mutation at import time and imports
``Memory_System.manager``, which is not importable in CI. Parsing the source as
text lets us assert the annotations exist without executing any imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

AGENT_COLLEGE_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "universal_agent"
    / "agent_college"
)

# Every module in the package that can contribute a public symbol.
PACKAGE_MODULES = [
    "config.py",
    "critic.py",
    "integration.py",
    "logfire_reader.py",
    "professor.py",
    "runner.py",
    "scribe.py",
]


def _public_functions_in_module(path: Path) -> list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return (qualname, kind, node) for every public function/method.

    Only module-level functions and methods of top-level classes are considered;
    nested functions (e.g. the ``signal_handler`` closure inside ``main``) are
    intentionally excluded. ``self``/``cls`` receivers are not required to be
    annotated, matching the convention used elsewhere in the package.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            out.append((node.name, "module", node))
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
                    out.append((f"{node.name}.{item.name}", "method", item))
    return out


def _all_public_functions() -> list[tuple[Path, str, str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found: list[tuple[Path, str, str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for module_name in PACKAGE_MODULES:
        module_path = AGENT_COLLEGE_DIR / module_name
        for qualname, _kind, node in _public_functions_in_module(module_path):
            found.append((module_path, module_name, qualname, node))
    return found


def test_public_api_is_non_empty() -> None:
    """Sanity check: the guard must actually inspect real public symbols."""
    assert _all_public_functions(), "no public functions discovered in agent_college package"


def test_all_public_functions_have_full_type_hints() -> None:
    offenders: list[str] = []
    for module_path, module_name, qualname, node in _all_public_functions():
        if node.returns is None:
            offenders.append(f"{module_name}::{qualname}: missing return annotation")
        args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
        for arg in args:
            if arg.arg in ("self", "cls"):
                continue
            if arg.annotation is None:
                offenders.append(f"{module_name}::{qualname}: parameter '{arg.arg}' missing annotation")
    assert not offenders, "untyped public signatures in agent_college package:\n" + "\n".join(offenders)
