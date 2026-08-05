"""Regression test: URW subsystem public method signatures are fully type-annotated.

These public functions on the live ``urw`` subsystem (wired into ``main.py`` via
``URWOrchestrator`` / the agent adapters) previously had untyped signatures. This
test locks in the annotations so they cannot regress. It inspects the AST rather
than calling the code, so it is fast and has no runtime side effects.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

URW_DIR = Path(__file__).resolve().parents[2] / "src" / "universal_agent" / "urw"


def _parse(module: str) -> ast.Module:
    return ast.parse((URW_DIR / f"{module}.py").read_text(encoding="utf-8"))


def _func(tree: ast.Module, qualname: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Find a top-level or method function by ``Class.method`` or ``func`` name."""
    if "." in qualname:
        class_name, method_name = qualname.split(".", 1)
    else:
        class_name, method_name = "", qualname
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != method_name:
            continue
        if class_name:
            # Confirm the enclosing class matches when requested.
            parent_matches = any(
                isinstance(parent, ast.ClassDef)
                and parent.name == class_name
                and any(child is node for child in ast.walk(parent))
                for parent in ast.walk(tree)
            )
            if not parent_matches:
                continue
        return node
    pytest.fail(f"function {qualname!r} not found in module")


def _is_fully_annotated(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True when every non-self/cls arg and the return are annotated."""
    args = list(node.args.args)
    if args and args[0].arg in ("self", "cls"):
        args = args[1:]
    args_ok = all(a.annotation is not None for a in args)
    return args_ok and node.returns is not None


@pytest.mark.parametrize(
    "module,qualname",
    [
        ("phase_planner", "Phase.mark_started"),
        ("phase_planner", "Phase.mark_complete"),
        ("state", "URWStateManager.transaction"),
        ("orchestrator", "AgentLoopInterface.cancel"),
        ("integration", "BaseAgentAdapter.cancel"),
        ("evaluator", "create_default_evaluator"),
    ],
)
def test_public_signature_fully_annotated(module: str, qualname: str) -> None:
    node = _func(_parse(module), qualname)
    assert _is_fully_annotated(node), (
        f"{module}.{qualname} must annotate all params and its return value"
    )


def test_mark_complete_failed_ids_is_optional() -> None:
    """``failed_ids`` defaults to None, so its annotation must be Optional[...]."""
    node = _func(_parse("phase_planner"), "Phase.mark_complete")
    failed_ids = next(a for a in node.args.args if a.arg == "failed_ids")
    assert failed_ids.annotation is not None, "failed_ids is missing an annotation"
    annotation = ast.unparse(failed_ids.annotation)
    assert "Optional" in annotation or "|" in annotation, (
        f"failed_ids should be Optional, got {annotation!r}"
    )


def test_transaction_is_iterator_context_manager() -> None:
    """``transaction`` is a @contextmanager generator; annotate as Iterator[...]."""
    node = _func(_parse("state"), "URWStateManager.transaction")
    assert node.returns is not None, "transaction is missing a return annotation"
    annotation = ast.unparse(node.returns)
    assert annotation.startswith("Iterator"), (
        f"transaction should return Iterator[...], got {annotation!r}"
    )


def test_cancel_methods_return_none() -> None:
    for module, qualname in [
        ("orchestrator", "AgentLoopInterface.cancel"),
        ("integration", "BaseAgentAdapter.cancel"),
    ]:
        node = _func(_parse(module), qualname)
        assert node.returns is not None, f"{module}.{qualname} is missing a return annotation"
        assert ast.unparse(node.returns) == "None", (
            f"{module}.{qualname} should return None, got {ast.unparse(node.returns)!r}"
        )
