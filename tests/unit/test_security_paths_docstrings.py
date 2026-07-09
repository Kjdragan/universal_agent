"""Regression tests guarding docstring coverage on ``security_paths``.

The public functions in this module enforce workspace-containment and
session-id security invariants, so their documentation is part of the
contract. These tests ensure the module and its public API cannot silently
lose their docstrings during future refactors.
"""

from __future__ import annotations

import inspect

from universal_agent import security_paths

PUBLIC_FUNCTIONS = [
    "is_valid_session_id",
    "validate_session_id",
    "resolve_workspace_dir",
    "resolve_ops_log_path",
    "allow_external_workspaces_from_env",
]


def test_module_has_docstring():
    assert security_paths.__doc__ and security_paths.__doc__.strip(), (
        "security_paths module must carry a docstring explaining its security role"
    )


def test_public_functions_are_documented():
    missing = [
        name
        for name in PUBLIC_FUNCTIONS
        if not inspect.getdoc(getattr(security_paths, name))
    ]
    assert not missing, f"Public security_paths functions missing docstrings: {missing}"


def test_containment_helpers_document_value_error_contract():
    """The path resolvers must document that escaping the root raises."""
    for name in ("resolve_workspace_dir", "resolve_ops_log_path"):
        doc = inspect.getdoc(getattr(security_paths, name)) or ""
        assert "ValueError" in doc, (
            f"{name} docstring must mention ValueError on escape"
        )
