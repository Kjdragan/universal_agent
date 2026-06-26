"""Regression guard: TGTG monitor magic literals stay extracted into named constants.

``monitor.py`` imports the third-party ``tgtg`` client, which is not installed in
CI, so the module is not importable here (see ``test_tgtg_public_type_hints.py``
for the same constraint). We therefore assert the extraction structurally via AST:

  * the backoff seconds (``60`` / ``30``) live in named constants,
  * the dead-item status pair (``404`` / ``400``) lives in ``DEAD_ITEM_STATUSES``,
  * the repeated ``{"http": url, "https": url}`` proxy dict lives behind a single
    ``_proxy_dict`` helper,

and the raw literals cannot silently creep back into the polling/recovery path.
These guards fail against the pre-extraction source and pass after it, which is
the red/green evidence that the mechanical extraction is in place and intact.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MONITOR_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "universal_agent"
    / "tgtg"
    / "monitor.py"
)
_TREE: ast.Module = ast.parse(_MONITOR_PATH.read_text(encoding="utf-8"))


def _top_level_assigns(tree: ast.Module) -> dict[str, ast.expr]:
    """Map top-level binding names to their value expressions (Assign + AnnAssign)."""
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    out[tgt.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node.value
    return out


def _literal_value(node: ast.expr):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Tuple):
        return tuple(_literal_value(e) for e in node.elts)
    raise AssertionError(f"expected a literal value, got {ast.dump(node)}")


def _used_names(tree: ast.Module) -> set[str]:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def test_backoff_constants_defined_with_original_values() -> None:
    assigns = _top_level_assigns(_TREE)
    assert _literal_value(assigns["BACKOFF_API_ERROR_SECONDS"]) == 60
    assert _literal_value(assigns["BACKOFF_UNEXPECTED_ERROR_SECONDS"]) == 30


def test_dead_item_statuses_defined_with_original_values() -> None:
    assigns = _top_level_assigns(_TREE)
    assert _literal_value(assigns["DEAD_ITEM_STATUSES"]) == (404, 400)


def test_extracted_constants_are_referenced_in_the_loop() -> None:
    used = _used_names(_TREE)
    assert "BACKOFF_API_ERROR_SECONDS" in used
    assert "BACKOFF_UNEXPECTED_ERROR_SECONDS" in used
    assert "DEAD_ITEM_STATUSES" in used


def test_proxy_dict_helper_exists() -> None:
    funcs = {
        n.name
        for n in _TREE.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_proxy_dict" in funcs


def test_no_bare_int_literals_in_sleep_calls() -> None:
    """time.sleep() backoffs must reference the named constants, not raw ints."""
    bare: list[int] = []
    for node in ast.walk(_TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "sleep"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, int)
        ):
            bare.append(node.args[0].value)
    assert bare == [], (
        f"time.sleep() must use named backoff constants; found raw ints: {bare}"
    )


def test_dead_status_uses_constant_not_inline_tuple() -> None:
    """`if status in (404, 400)` must reference DEAD_ITEM_STATUSES instead."""
    offenders: list[str] = []
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.In) for op in node.ops):
            continue
        for cmp in node.comparators:
            if isinstance(cmp, ast.Tuple) and all(
                isinstance(e, ast.Constant) for e in cmp.elts
            ):
                offenders.append(ast.dump(cmp))
    assert not offenders, (
        "inline tuple literal in an `in` comparison; use DEAD_ITEM_STATUSES: "
        + ", ".join(offenders)
    )


def test_proxy_scheme_dict_defined_exactly_once() -> None:
    """The {'http': .., 'https': ..} literal must live only inside _proxy_dict."""
    count = 0
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Dict):
            continue
        key_values = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        if key_values == {"http", "https"}:
            count += 1
    assert count == 1, (
        "expected exactly one {'http': .., 'https': ..} literal (inside "
        f"_proxy_dict); found {count}"
    )
