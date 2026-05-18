"""Tests for python_parser helper methods and edge cases."""

import ast
from pathlib import Path
import tempfile

import pytest

from universal_agent.utils.python_parser import (
    ClassInfo,
    FunctionInfo,
    NodeType,
    ParameterInfo,
    ParameterKind,
    ParseResult,
    PythonParser,
    parse_python,
    parse_python_file,
)


# ---------------------------------------------------------------------------
# _should_include_name
# ---------------------------------------------------------------------------
class TestShouldIncludeName:
    """Cover _should_include_name across extract_private / extract_dunder combos."""

    def test_public_always_included(self):
        for priv in (True, False):
            for dunder in (True, False):
                p = PythonParser(extract_private=priv, extract_dunder=dunder)
                assert p._should_include_name("hello") is True

    def test_dunder_excluded_by_default(self):
        p = PythonParser(extract_dunder=False)
        assert p._should_include_name("__init__") is False

    def test_dunder_included_when_enabled(self):
        p = PythonParser(extract_dunder=True)
        assert p._should_include_name("__init__") is True

    def test_dunder_like_but_not_real(self):
        p = PythonParser(extract_dunder=False)
        assert p._should_include_name("__x") is False

    def test_private_excluded_when_disabled(self):
        p = PythonParser(extract_private=False, extract_dunder=True)
        assert p._should_include_name("_private") is False

    def test_private_included_when_enabled(self):
        p = PythonParser(extract_private=True, extract_dunder=True)
        assert p._should_include_name("_private") is True

    def test_name_mangled_double_underscore(self):
        p = PythonParser(extract_private=True, extract_dunder=False)
        assert p._should_include_name("__mangled") is False

    def test_all_disabled_only_public(self):
        p = PythonParser(extract_private=False, extract_dunder=False)
        assert p._should_include_name("public") is True
        assert p._should_include_name("_private") is False
        assert p._should_include_name("__init__") is False


# ---------------------------------------------------------------------------
# _is_constant_name
# ---------------------------------------------------------------------------
class TestIsConstantName:
    """Cover _is_constant_name heuristics."""

    def test_uppercase_is_constant(self):
        p = PythonParser()
        assert p._is_constant_name("MAX_ITEMS") is True

    def test_lowercase_is_not_constant(self):
        p = PythonParser()
        assert p._is_constant_name("items") is False

    def test_camelcase_not_constant(self):
        p = PythonParser()
        assert p._is_constant_name("myVar") is False

    def test_single_leading_underscore_upper(self):
        p = PythonParser()
        assert p._is_constant_name("_PRIVATE_CONST") is True

    def test_single_leading_underscore_mixed(self):
        p = PythonParser()
        assert p._is_constant_name("_privateVar") is False

    def test_double_leading_underscore(self):
        p = PythonParser()
        assert p._is_constant_name("__DUNDER") is False


# ---------------------------------------------------------------------------
# _safe_unparse
# ---------------------------------------------------------------------------
class TestSafeUnparse:
    """Cover _safe_unparse edge cases."""

    def test_none_returns_none(self):
        p = PythonParser()
        assert p._safe_unparse(None) is None

    def test_simple_expression(self):
        p = PythonParser()
        node = ast.Constant(value=42)
        assert p._safe_unparse(node) == "42"

    def test_name_node(self):
        p = PythonParser()
        node = ast.Name(id="x", ctx=ast.Load())
        assert p._safe_unparse(node) == "x"


# ---------------------------------------------------------------------------
# _get_error_context
# ---------------------------------------------------------------------------
class TestGetErrorContext:
    """Cover _get_error_context edge cases."""

    def test_none_line_returns_none(self):
        p = PythonParser()
        assert p._get_error_context("source", None) is None

    def test_valid_line_returns_context(self):
        p = PythonParser()
        source = "line1\nline2\nline3\nline4\nline5"
        result = p._get_error_context(source, 3, context_lines=1)
        assert "line3" in result

    def test_first_line(self):
        p = PythonParser()
        source = "line1\nline2\nline3"
        result = p._get_error_context(source, 1, context_lines=1)
        assert "line1" in result

    def test_last_line(self):
        p = PythonParser()
        source = "line1\nline2\nline3"
        result = p._get_error_context(source, 3, context_lines=1)
        assert "line3" in result

    def test_line_beyond_source(self):
        p = PythonParser()
        source = "line1\nline2"
        result = p._get_error_context(source, 100)
        # Should not crash; may return empty or partial
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Edge-case source inputs
# ---------------------------------------------------------------------------
class TestEdgeCaseSource:
    """Cover empty / whitespace / comment-only source."""

    def test_empty_source(self):
        result = parse_python("")
        assert result.success is True
        assert result.total_lines == 0
        assert result.functions == []

    def test_whitespace_only(self):
        result = parse_python("   \n   \n")
        assert result.success is True

    def test_comment_only(self):
        result = parse_python("# just a comment\n")
        assert result.success is True
        assert result.functions == []

    def test_no_docstring(self):
        source = "def nodoc():\n    pass\n"
        result = parse_python(source)
        assert result.functions[0].docstring is None

    def test_single_expression_function(self):
        source = "def single():\n    return 42\n"
        result = parse_python(source)
        assert result.functions[0].name == "single"
        assert result.functions[0].line_end > result.functions[0].line_start


# ---------------------------------------------------------------------------
# Partial parse: function signature extraction
# ---------------------------------------------------------------------------
class TestPartialParseFunctionSignature:
    """Cover _try_extract_function_signature from broken code."""

    def test_incomplete_function_def(self):
        source = "def broken(\n"
        result = parse_python(source)
        assert result.success is False
        # May recover partial info
        if result.functions:
            assert result.functions[0].name == "broken"

    def test_incomplete_class_def(self):
        source = "class BrokenBase(\n"
        result = parse_python(source)
        assert result.success is False

    def test_function_with_default_but_no_body(self):
        source = "import os\ndef partial(x=1):\nclass C:\n    pass\n"
        result = parse_python(source)
        assert result.success is False
        # Imports should still be recovered
        assert len(result.imports) >= 1


# ---------------------------------------------------------------------------
# parse_python_file edge cases
# ---------------------------------------------------------------------------
class TestParseFileEdgeCases:
    """Cover parse_python_file with unusual inputs."""

    def test_empty_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("")
            f.flush()
            result = parse_python_file(f.name)
            assert result.success is True

    def test_non_py_extension(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("def func(): pass\n")
            f.flush()
            result = parse_python_file(f.name)
            # Should still parse even with non-.py extension
            assert result.success is True


# ---------------------------------------------------------------------------
# Convenience function kwargs passthrough
# ---------------------------------------------------------------------------
class TestConvenienceKwargs:
    """Cover that convenience functions pass kwargs to PythonParser."""

    def test_extract_functions_private(self):
        from universal_agent.utils.python_parser import extract_functions
        source = "def pub(): pass\ndef _priv(): pass\n"
        funcs = extract_functions(source, extract_private=False)
        assert len(funcs) == 1
        assert funcs[0].name == "pub"

    def test_extract_classes_dunder(self):
        from universal_agent.utils.python_parser import extract_classes
        source = "class C:\n    def __init__(self): pass\n    def method(self): pass\n"
        classes = extract_classes(source, extract_dunder=True)
        assert any(m.name == "__init__" for m in classes[0].methods)

    def test_extract_imports_from_broken(self):
        from universal_agent.utils.python_parser import extract_imports
        source = "import os\nbroken\n"
        imports = extract_imports(source)
        assert len(imports) >= 1


# ---------------------------------------------------------------------------
# Class member filtering
# ---------------------------------------------------------------------------
class TestClassMemberFiltering:
    """Cover class attribute and method extraction details."""

    def test_class_with_property(self):
        source = '''
class WithProp:
    @property
    def value(self):
        return 42
'''
        result = parse_python(source)
        assert len(result.classes) == 1
        methods = result.classes[0].methods
        assert any(m.name == "value" for m in methods)

    def test_class_no_bases(self):
        source = "class Plain:\n    pass\n"
        result = parse_python(source)
        assert result.classes[0].bases == []


# ---------------------------------------------------------------------------
# Complexity edge cases
# ---------------------------------------------------------------------------
class TestComplexityEdgeCases:
    """Cover _calculate_complexity for less-common AST nodes."""

    def test_while_loop(self):
        source = '''
def f(x):
    while x > 0:
        x -= 1
'''
        result = parse_python(source)
        assert result.functions[0].complexity_score >= 2

    def test_try_except(self):
        source = '''
def f():
    try:
        pass
    except ValueError:
        pass
'''
        result = parse_python(source)
        assert result.functions[0].complexity_score >= 2

    def test_boolean_ops(self):
        source = '''
def f(a, b):
    if a and b:
        return True
    return False
'''
        result = parse_python(source)
        assert result.functions[0].complexity_score >= 3  # if + and

    def test_ternary_expression(self):
        source = '''
def f(x):
    return "yes" if x else "no"
'''
        result = parse_python(source)
        assert result.functions[0].complexity_score >= 2


# ---------------------------------------------------------------------------
# Return annotations
# ---------------------------------------------------------------------------
class TestReturnAnnotations:
    """Cover return annotation parsing."""

    def test_none_return_annotation(self):
        source = "def f():\n    pass\n"
        result = parse_python(source)
        assert result.functions[0].return_annotation is None

    def test_complex_return_annotation(self):
        source = "def f() -> list[dict[str, int]]:\n    pass\n"
        result = parse_python(source)
        assert result.functions[0].return_annotation is not None
        assert "list" in result.functions[0].return_annotation

    def test_string_return_annotation(self):
        source = 'def f() -> "MyClass":\n    pass\n'
        result = parse_python(source)
        assert result.functions[0].return_annotation is not None


# ---------------------------------------------------------------------------
# Class bases
# ---------------------------------------------------------------------------
class TestClassBases:
    """Cover class base / inheritance extraction."""

    def test_multiple_inheritance(self):
        source = '''
class A: pass
class B: pass
class C(A, B): pass
'''
        result = parse_python(source)
        cls_c = next(c for c in result.classes if c.name == "C")
        assert "A" in cls_c.bases
        assert "B" in cls_c.bases

    def test_generic_base(self):
        source = '''
class Container(list[str]): pass
'''
        result = parse_python(source)
        assert len(result.classes) == 1
        assert result.classes[0].bases  # non-empty

    def test_no_inheritance(self):
        source = "class Solo:\n    pass\n"
        result = parse_python(source)
        assert result.classes[0].bases == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
