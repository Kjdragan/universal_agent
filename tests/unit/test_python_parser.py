"""Tests for the Python parser module."""

import tempfile
from pathlib import Path

import pytest

from universal_agent.utils.python_parser import (
    ClassInfo,
    DecoratorInfo,
    FunctionInfo,
    ImportInfo,
    NodeType,
    ParameterInfo,
    ParameterKind,
    ParseError,
    ParseResult,
    PythonParser,
    VariableInfo,
    extract_classes,
    extract_functions,
    extract_imports,
    parse_python,
    parse_python_file,
)


# Sample Python code for testing
SAMPLE_MODULE = '''
"""Module docstring for testing."""

import os
import sys
from typing import List, Optional, Dict
from pathlib import Path as P

# Constants
MAX_ITEMS = 100
DEFAULT_NAME = "test"

# Variables
counter = 0
items: list[str] = []


def simple_function():
    """A simple function."""
    pass


def function_with_params(a: int, b: str = "default", *args, **kwargs) -> str:
    """Function with various parameter types.
    
    Args:
        a: First parameter
        b: Second parameter with default
        
    Returns:
        A string result
    """
    if a > 10:
        return b
    elif a > 5:
        return str(a)
    else:
        return "small"


async def async_function(url: str) -> dict:
    """An async function."""
    import asyncio
    await asyncio.sleep(1)
    return {"status": "ok"}


@decorator1
@decorator2(arg="value")
def decorated_function(x: int) -> int:
    """A decorated function."""
    return x * 2


class SimpleClass:
    """A simple class."""
    
    class_attr = "class value"
    
    def __init__(self, name: str):
        self.name = name
    
    def method(self) -> str:
        """Instance method."""
        return self.name
    
    @staticmethod
    def static_method() -> int:
        """Static method."""
        return 42
    
    @property
    def name_prop(self) -> str:
        """Property."""
        return self.name


class InheritedClass(SimpleClass):
    """Class with inheritance."""
    pass


class DecoratedClass:
    """A decorated class."""
    pass
'''


def test_parse_simple_function():
    """Test parsing a simple function."""
    source = '''
def hello():
    """Say hello."""
    print("Hello")
'''
    result = parse_python(source)
    
    assert result.success is True
    assert len(result.functions) == 1
    
    func = result.functions[0]
    assert func.name == "hello"
    assert func.docstring == "Say hello."
    assert func.is_async is False
    assert len(func.parameters) == 0
    assert func.node_type == NodeType.FUNCTION


def test_parse_async_function():
    """Test parsing an async function."""
    source = '''
async def fetch_data(url: str) -> dict:
    """Fetch data from URL."""
    return {}
'''
    result = parse_python(source)
    
    assert result.success is True
    assert len(result.functions) == 1
    
    func = result.functions[0]
    assert func.name == "fetch_data"
    assert func.is_async is True
    assert func.node_type == NodeType.ASYNC_FUNCTION
    assert func.return_annotation == "dict"
    assert len(func.parameters) == 1
    assert func.parameters[0].name == "url"
    assert func.parameters[0].annotation == "str"


def test_parse_function_parameters():
    """Test parsing function with various parameter types."""
    source = '''
def complex_func(
    a: int,
    b: str = "default",
    *args: int,
    c: float = 1.0,
    **kwargs: str
) -> None:
    pass
'''
    result = parse_python(source)
    
    assert len(result.functions) == 1
    params = result.functions[0].parameters
    
    # Check positional/keyword param
    assert params[0].name == "a"
    assert params[0].annotation == "int"
    assert params[0].default is None
    assert params[0].kind == ParameterKind.POSITIONAL_OR_KEYWORD
    
    # Check param with default (ast.unparse uses single quotes)
    assert params[1].name == "b"
    assert params[1].annotation == "str"
    assert params[1].default == "'default'"  # ast.unparse uses single quotes
    
    # Check *args
    assert params[2].name == "args"
    assert params[2].kind == ParameterKind.VAR_POSITIONAL
    
    # Check keyword-only param
    assert params[3].name == "c"
    assert params[3].kind == ParameterKind.KEYWORD_ONLY
    
    # Check **kwargs
    assert params[4].name == "kwargs"
    assert params[4].kind == ParameterKind.VAR_KEYWORD


def test_parse_decorators():
    """Test parsing decorators."""
    source = '''
@decorator1
@decorator2("arg")
@decorator3(key="value", num=42)
def decorated() -> None:
    pass
'''
    result = parse_python(source)
    
    assert len(result.functions) == 1
    decorators = result.functions[0].decorators
    
    assert len(decorators) == 3
    assert decorators[0].name == "decorator1"
    assert decorators[0].args == []
    assert decorators[0].kwargs == {}
    
    assert decorators[1].name == "decorator2"
    assert decorators[1].args == ["'arg'"]  # ast.unparse uses single quotes
    
    assert decorators[2].name == "decorator3"
    assert "key" in decorators[2].kwargs
    assert "num" in decorators[2].kwargs


def test_parse_class():
    """Test parsing a class."""
    source = '''
class MyClass(BaseClass):
    """A class with a base."""
    
    attr = "value"
    
    def __init__(self, name: str):
        self.name = name
    
    def get_name(self) -> str:
        return self.name
'''
    # By default, dunder methods are excluded
    parser = PythonParser(extract_dunder=False)
    result = parser.parse(source)
    
    assert len(result.classes) == 1
    
    cls = result.classes[0]
    assert cls.name == "MyClass"
    assert cls.docstring == "A class with a base."
    assert "BaseClass" in cls.bases
    assert "attr" in cls.attributes
    # By default, __init__ is excluded (extract_dunder=False)
    assert len(cls.methods) == 1  # Only get_name


def test_parse_class_with_dunder_methods():
    """Test parsing class with dunder methods when extract_dunder=True."""
    source = '''
class MyClass:
    def __init__(self):
        pass
    
    def __str__(self) -> str:
        return "MyClass"
    
    def normal_method(self):
        pass
'''
    parser = PythonParser(extract_dunder=True)
    result = parser.parse(source)
    
    cls = result.classes[0]
    method_names = [m.name for m in cls.methods]
    assert "__init__" in method_names
    assert "__str__" in method_names
    assert "normal_method" in method_names


def test_parse_class_without_dunder_methods():
    """Test that dunder methods are excluded by default."""
    source = '''
class MyClass:
    def __init__(self):
        pass
    
    def normal_method(self):
        pass
'''
    parser = PythonParser(extract_dunder=False)
    result = parser.parse(source)
    
    cls = result.classes[0]
    method_names = [m.name for m in cls.methods]
    assert "__init__" not in method_names
    assert "normal_method" in method_names


def test_parse_imports():
    """Test parsing import statements."""
    source = '''
import os
import sys as system
from typing import List, Optional
from pathlib import Path as P
from . import local_module
'''
    result = parse_python(source)
    
    assert len(result.imports) >= 4
    
    # Find the 'import os'
    os_import = next((i for i in result.imports if "os" in i.names and not i.is_from_import), None)
    assert os_import is not None
    assert os_import.is_from_import is False
    
    # Find the sys import with alias
    sys_import = next((i for i in result.imports if "sys" in i.names and i.aliases), None)
    assert sys_import is not None
    assert sys_import.aliases.get("sys") == "system"


def test_parse_variables():
    """Test parsing variable assignments."""
    source = '''
MAX_ITEMS = 100
name = "test"
items: list[int] = []
annotated: str
'''
    result = parse_python(source)
    
    assert len(result.variables) >= 3
    
    # Check constant detection
    max_items = next((v for v in result.variables if v.name == "MAX_ITEMS"), None)
    assert max_items is not None
    assert max_items.is_constant is True
    assert max_items.value_repr == "100"
    
    # Check regular variable (ast.unparse uses single quotes)
    name_var = next((v for v in result.variables if v.name == "name"), None)
    assert name_var is not None
    assert name_var.is_constant is False
    assert name_var.value_repr == "'test'"  # ast.unparse uses single quotes
    
    # Check annotated variable
    items_var = next((v for v in result.variables if v.name == "items"), None)
    assert items_var is not None
    assert items_var.annotation is not None


def test_parse_module_docstring():
    """Test parsing module docstring."""
    source = '''"""This is a module docstring.

It can span multiple lines.
"""

def func():
    pass
'''
    result = parse_python(source)
    
    assert result.module_docstring is not None
    assert "module docstring" in result.module_docstring


def test_cyclomatic_complexity():
    """Test cyclomatic complexity calculation."""
    source = '''
def simple():
    return 1

def with_if(x):
    if x > 0:
        return 1
    return 0

def complex_func(x):
    if x > 10:
        return 1
    elif x > 5:
        return 2
    else:
        for i in range(x):
            if i % 2 == 0:
                return 3
        return 4
'''
    result = parse_python(source)
    
    simple = next(f for f in result.functions if f.name == "simple")
    with_if = next(f for f in result.functions if f.name == "with_if")
    complex_func = next(f for f in result.functions if f.name == "complex_func")
    
    assert simple.complexity_score == 1  # Base complexity
    assert with_if.complexity_score == 2  # 1 + 1 if
    assert complex_func.complexity_score > with_if.complexity_score


def test_syntax_error_recovery():
    """Test error recovery for syntax errors."""
    source = '''
import os
from typing import List

def broken_function(
    # Missing closing paren and body

def working_function():
    pass

class BrokenClass(
    # Missing body
    
class WorkingClass:
    pass
'''
    result = parse_python(source)
    
    # Should still parse successfully with partial data
    assert result.success is False
    assert len(result.errors) > 0
    
    # Check that we captured the error
    error = result.errors[0]
    assert error.error_type == "SyntaxError"
    assert error.line_number is not None


def test_partial_import_recovery():
    """Test that imports are recovered from broken code."""
    source = '''
import os
import sys
from typing import List, Optional

this is broken syntax

def func():
    pass
'''
    result = parse_python(source)
    
    # Should have imports despite syntax error
    assert result.success is False
    assert len(result.imports) >= 2


def test_private_filtering():
    """Test filtering of private members."""
    source = '''
def public_func():
    pass

def _private_func():
    pass

class PublicClass:
    def public_method(self):
        pass
    
    def _private_method(self):
        pass
'''
    # With private extraction enabled (default)
    parser_with_private = PythonParser(extract_private=True)
    result_with = parser_with_private.parse(source)
    assert any(f.name == "_private_func" for f in result_with.functions)
    
    # With private extraction disabled
    parser_no_private = PythonParser(extract_private=False)
    result_without = parser_no_private.parse(source)
    assert not any(f.name == "_private_func" for f in result_without.functions)


def test_parse_file():
    """Test parsing a file."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        f.write('''
def test_func():
    """Test function."""
    return 42
''')
        f.flush()
        
        result = parse_python_file(f.name)
        
        assert result.success is True
        assert len(result.functions) == 1
        assert result.functions[0].name == "test_func"


def test_parse_file_not_found():
    """Test parsing a non-existent file."""
    result = parse_python_file("/nonexistent/path/file.py")
    
    assert result.success is False
    assert len(result.errors) == 1
    assert "File not found" in result.errors[0].message


def test_convenience_functions():
    """Test convenience functions."""
    source = '''
import os
def func1(): pass
def func2(): pass
class MyClass: pass
'''
    
    functions = extract_functions(source)
    assert len(functions) == 2
    
    classes = extract_classes(source)
    assert len(classes) == 1
    
    imports = extract_imports(source)
    assert len(imports) >= 1


def test_line_numbers():
    """Test that line numbers are correctly captured."""
    # Note: When source starts with a newline, line 1 is empty
    source = '''
# Line 2 comment
import os  # Line 3

def func():  # Line 5
    pass

class MyClass:  # Line 8
    pass
'''
    result = parse_python(source)
    
    # Import should be on line 3 (after initial newline and comment)
    assert result.imports[0].line_number == 3
    
    # Function should start on line 5
    assert result.functions[0].line_start == 5
    
    # Class should start on line 8
    assert result.classes[0].line_start == 8


def test_total_lines():
    """Test total line count."""
    source = "line1\nline2\nline3\n"
    result = parse_python(source)
    
    assert result.total_lines == 3


def test_class_decorators():
    """Test parsing class decorators."""
    source = '''
@dataclass
@decorator_with_args(arg=1)
class DecoratedClass:
    pass
'''
    result = parse_python(source)
    
    assert len(result.classes) == 1
    cls = result.classes[0]
    
    assert len(cls.decorators) == 2
    assert cls.decorators[0].name == "dataclass"
    assert "arg" in cls.decorators[1].kwargs


def test_nested_classes_not_extracted():
    """Test that nested classes are not extracted at top level."""
    source = '''
class Outer:
    class Inner:
        pass
'''
    result = parse_python(source)
    
    # Only the outer class should be extracted
    assert len(result.classes) == 1
    assert result.classes[0].name == "Outer"


def test_comprehension_complexity():
    """Test that comprehensions add to complexity."""
    source = '''
def with_comprehension(items):
    return [x for x in items if x > 0]

def with_nested_comprehension(items):
    return [[x for x in row if x] for row in items if len(row) > 0]
'''
    result = parse_python(source)
    
    simple = next(f for f in result.functions if f.name == "with_comprehension")
    nested = next(f for f in result.functions if f.name == "with_nested_comprehension")
    
    # Both should have complexity > 1 due to comprehensions
    assert simple.complexity_score > 1
    assert nested.complexity_score > simple.complexity_score


def test_parse_result_model():
    """Test ParseResult Pydantic model."""
    result = ParseResult(
        success=True,
        functions=[FunctionInfo(name="test", line_start=1, line_end=2)],
        classes=[],
        imports=[],
        variables=[],
        module_docstring="test",
        errors=[],
        total_lines=10,
    )
    
    assert result.success is True
    assert len(result.functions) == 1
    assert result.total_lines == 10


def test_function_info_model():
    """Test FunctionInfo Pydantic model."""
    func = FunctionInfo(
        name="test_func",
        docstring="Test docstring",
        parameters=[
            ParameterInfo(name="x", annotation="int", kind=ParameterKind.POSITIONAL_OR_KEYWORD),
        ],
        return_annotation="str",
        decorators=[DecoratorInfo(name="decorator")],
        line_start=1,
        line_end=5,
        is_async=False,
        complexity_score=2,
    )
    
    assert func.name == "test_func"
    assert func.docstring == "Test docstring"
    assert len(func.parameters) == 1
    assert func.complexity_score == 2


def test_full_module_parsing():
    """Test parsing a full module with all elements."""
    result = parse_python(SAMPLE_MODULE)
    
    assert result.success is True
    assert result.module_docstring is not None
    assert len(result.functions) >= 4  # simple_function, function_with_params, async_function, decorated_function
    assert len(result.classes) >= 3  # SimpleClass, InheritedClass, DecoratedClass
    assert len(result.imports) >= 2
    assert len(result.variables) >= 2  # MAX_ITEMS, DEFAULT_NAME, counter, items


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
