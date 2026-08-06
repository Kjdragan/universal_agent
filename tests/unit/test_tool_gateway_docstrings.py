"""Regression contract: the public API of ``durable.tool_gateway`` stays documented.

The tool-name parsing/validation surface is depended on by four runtime modules
(``agent_core``, ``hooks``, ``main``, ``guardrails.tool_schema``). This test pins a
"public symbols must carry a human-authored docstring" contract so the
documentation cannot silently drift.
"""
import inspect

import pytest

import universal_agent.durable.tool_gateway as tool_gateway


def _public_symbols():
    """Yield (name, obj) for functions/classes *defined* in tool_gateway."""
    for name in dir(tool_gateway):
        if name.startswith("_"):
            continue
        obj = getattr(tool_gateway, name)
        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue
        if getattr(obj, "__module__", None) != tool_gateway.__name__:
            continue
        yield name, obj


def test_module_has_docstring():
    assert tool_gateway.__doc__ and tool_gateway.__doc__.strip(), (
        "durable.tool_gateway must have a module docstring"
    )


@pytest.mark.parametrize("name,obj", list(_public_symbols()))
def test_public_symbol_has_real_docstring(name, obj):
    doc = obj.__doc__
    assert doc and doc.strip(), f"{name} must carry a docstring"
    # @dataclass auto-generates a "ClassName(field: type, ...)" signature docstring
    # when no human-authored docstring is present; treat that as undocumented.
    first_line = doc.strip().splitlines()[0]
    assert not first_line.startswith(f"{name}("), (
        f"{name}'s docstring is the auto-generated dataclass signature, not real docs"
    )
