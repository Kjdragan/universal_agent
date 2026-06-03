"""Regression guard: every public command handler in ``tgtg/cli.py`` is typed.

``tgtg/cli.py`` exposes a family of ``cmd_*`` argparse handlers plus ``main()``.
The rest of the ``tgtg`` package (config, scanner, targets, purchaser) is already
fully type-annotated; this test pins the same convention onto the CLI's public
function signatures so the annotations can't silently regress.

The check is intentionally **AST/text based** rather than import based: ``cli.py``
imports the third-party ``tgtg`` client, which is not installed in CI, so the
module is not importable here. Parsing the source as text lets us assert the
annotations exist without executing any imports.
"""
from __future__ import annotations

import ast
from pathlib import Path

CLI_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "universal_agent"
    / "tgtg"
    / "cli.py"
)

# Public command handlers the CLI dispatches to, plus the entrypoint. Pinning the
# expected set keeps the guard meaningful even if a handler is renamed/removed.
EXPECTED_PUBLIC_FUNCS = {
    "cmd_login",
    "cmd_status",
    "cmd_list",
    "cmd_buy",
    "cmd_run",
    "cmd_scan",
    "cmd_db_stats",
    "cmd_db_list",
    "cmd_db_search",
    "cmd_db_speed",
    "cmd_target_list",
    "cmd_target_add",
    "cmd_target_remove",
    "cmd_target_set",
    "main",
}


def _module_level_public_functions() -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    return [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]


def test_expected_public_handlers_present() -> None:
    found = {fn.name for fn in _module_level_public_functions()}
    missing = EXPECTED_PUBLIC_FUNCS - found
    assert not missing, f"expected public CLI handlers not found in cli.py: {sorted(missing)}"


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
    assert not offenders, "untyped public signatures in tgtg/cli.py:\n" + "\n".join(offenders)
