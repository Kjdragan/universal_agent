"""
Python source code parser using AST.

Provides structured extraction of functions, classes, imports, variables,
and module metadata with error recovery for syntax errors.
"""

import ast
import logging
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Types of AST nodes that can be extracted."""

    FUNCTION = "function"
    ASYNC_FUNCTION = "async_function"
    CLASS = "class"
    IMPORT = "import"
    IMPORT_FROM = "import_from"
    VARIABLE = "variable"


class ParameterKind(Enum):
    """Parameter kinds for function signatures."""

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    VAR_POSITIONAL = "var_positional"  # *args
    KEYWORD_ONLY = "keyword_only"
    VAR_KEYWORD = "var_keyword"  # **kwargs


class ParseError(BaseModel):
    """Represents a parse error with context."""

    message: str = Field(description="Error message")
    line_number: Optional[int] = Field(default=None, description="Line where error occurred")
    column: Optional[int] = Field(default=None, description="Column offset")
    error_type: str = Field(default="SyntaxError", description="Type of error")
    context: Optional[str] = Field(default=None, description="Source context around error")


class DecoratorInfo(BaseModel):
    """Information about a decorator."""

    name: str = Field(description="Decorator name (e.g., 'property', 'classmethod')")
    args: list[str] = Field(default_factory=list, description="Positional arguments as strings")
    kwargs: dict[str, str] = Field(default_factory=dict, description="Keyword arguments as strings")


class ParameterInfo(BaseModel):
    """Information about a function parameter."""

    name: str = Field(description="Parameter name")
    annotation: Optional[str] = Field(default=None, description="Type annotation")
    default: Optional[str] = Field(default=None, description="Default value representation")
    kind: ParameterKind = Field(description="Parameter kind")


class FunctionInfo(BaseModel):
    """Information about a function definition."""

    name: str = Field(description="Function name")
    docstring: Optional[str] = Field(default=None, description="Function docstring")
    parameters: list[ParameterInfo] = Field(default_factory=list, description="Function parameters")
    return_annotation: Optional[str] = Field(default=None, description="Return type annotation")
    decorators: list[DecoratorInfo] = Field(default_factory=list, description="Decorators")
    line_start: int = Field(description="Starting line number")
    line_end: int = Field(description="Ending line number")
    is_async: bool = Field(default=False, description="Whether this is an async function")
    complexity_score: int = Field(default=1, ge=1, description="Cyclomatic complexity")
    node_type: NodeType = Field(default=NodeType.FUNCTION, description="Node type")


class ClassInfo(BaseModel):
    """Information about a class definition."""

    name: str = Field(description="Class name")
    docstring: Optional[str] = Field(default=None, description="Class docstring")
    bases: list[str] = Field(default_factory=list, description="Base class names")
    decorators: list[DecoratorInfo] = Field(default_factory=list, description="Decorators")
    methods: list[FunctionInfo] = Field(default_factory=list, description="Class methods")
    attributes: list[str] = Field(default_factory=list, description="Class-level attributes")
    line_start: int = Field(description="Starting line number")
    line_end: int = Field(description="Ending line number")


class ImportInfo(BaseModel):
    """Information about an import statement."""

    module: Optional[str] = Field(default=None, description="Module being imported from")
    names: list[str] = Field(default_factory=list, description="Names being imported")
    aliases: dict[str, str] = Field(default_factory=dict, description="Name to alias mapping")
    line_number: int = Field(description="Line number of import")
    is_from_import: bool = Field(default=False, description="Whether this is a from import")


class VariableInfo(BaseModel):
    """Information about a variable assignment."""

    name: str = Field(description="Variable name")
    value_repr: Optional[str] = Field(default=None, description="String representation of value")
    annotation: Optional[str] = Field(default=None, description="Type annotation")
    line_number: int = Field(description="Line number of assignment")
    is_constant: bool = Field(default=False, description="Whether this appears to be a constant")


class ParseResult(BaseModel):
    """Result of parsing Python source code."""

    success: bool = Field(description="Whether parsing was fully successful")
    functions: list[FunctionInfo] = Field(default_factory=list, description="Extracted functions")
    classes: list[ClassInfo] = Field(default_factory=list, description="Extracted classes")
    imports: list[ImportInfo] = Field(default_factory=list, description="Extracted imports")
    variables: list[VariableInfo] = Field(default_factory=list, description="Extracted variables")
    module_docstring: Optional[str] = Field(default=None, description="Module-level docstring")
    errors: list[ParseError] = Field(default_factory=list, description="Parse errors encountered")
    total_lines: int = Field(default=0, description="Total lines in source")


class PythonParser:
    """
    AST-based Python source code parser.

    Supports error recovery for partial parsing of syntactically invalid code.
    """

    def __init__(
        self,
        include_source: bool = False,
        extract_private: bool = True,
        extract_dunder: bool = False,
    ):
        """
        Initialize the parser.

        Args:
            include_source: Whether to include source snippets (not implemented yet)
            extract_private: Whether to extract private members (starting with _)
            extract_dunder: Whether to extract dunder methods (__init__, etc.)
        """
        self.include_source = include_source
        self.extract_private = extract_private
        self.extract_dunder = extract_dunder

    def parse(self, source: str) -> ParseResult:
        """
        Parse Python source code and extract structured information.

        Args:
            source: Python source code string

        Returns:
            ParseResult with extracted information and any errors
        """
        lines = source.splitlines()
        total_lines = len(lines)

        try:
            tree = ast.parse(source)
            errors: list[ParseError] = []
            success = True
        except SyntaxError as e:
            # Attempt partial parsing by extracting what we can
            tree, errors = self._partial_parse(source, e)
            success = False

        # Extract module docstring
        module_docstring = ast.get_docstring(tree)

        # Extract all node types
        functions = self._extract_functions(tree)
        classes = self._extract_classes(tree)
        imports = self._extract_imports(tree)
        variables = self._extract_variables(tree)

        return ParseResult(
            success=success,
            functions=functions,
            classes=classes,
            imports=imports,
            variables=variables,
            module_docstring=module_docstring,
            errors=errors,
            total_lines=total_lines,
        )

    def parse_file(self, filepath: Union[str, Path]) -> ParseResult:
        """
        Parse a Python file and extract structured information.

        Args:
            filepath: Path to the Python file

        Returns:
            ParseResult with extracted information and any errors
        """
        filepath = Path(filepath)
        try:
            source = filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ParseResult(
                success=False,
                errors=[
                    ParseError(
                        message=f"File not found: {filepath}",
                        error_type="FileNotFoundError",
                    )
                ],
            )
        except UnicodeDecodeError as e:
            return ParseResult(
                success=False,
                errors=[
                    ParseError(
                        message=f"Unicode decode error: {e}",
                        error_type="UnicodeDecodeError",
                    )
                ],
            )

        return self.parse(source)

    def _partial_parse(
        self, source: str, syntax_error: SyntaxError
    ) -> tuple[ast.Module, list[ParseError]]:
        """
        Attempt partial parsing of code with syntax errors.

        Uses a multi-pass approach to extract valid constructs.
        """
        errors = [
            ParseError(
                message=str(syntax_error.msg),
                line_number=syntax_error.lineno,
                column=syntax_error.offset,
                error_type="SyntaxError",
                context=self._get_error_context(source, syntax_error.lineno),
            )
        ]

        # Create an empty module to populate
        tree = ast.Module(body=[], type_ignores=[])

        lines = source.splitlines()

        # Extract imports line by line
        for i, line in enumerate(lines, start=1):
            line_stripped = line.strip()
            if line_stripped.startswith("import ") or line_stripped.startswith("from "):
                try:
                    line_tree = ast.parse(line)
                    tree.body.extend(line_tree.body)
                except SyntaxError:
                    pass  # Skip unparseable lines

        # Try to extract function and class definitions
        # Look for 'def ' and 'class ' at the start of lines
        for i, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("def ") or stripped.startswith("async def "):
                # Try to extract just the signature
                func_node = self._try_extract_function_signature(lines, i - 1)
                if func_node:
                    tree.body.append(func_node)
            elif stripped.startswith("class "):
                class_node = self._try_extract_class_header(lines, i - 1)
                if class_node:
                    tree.body.append(class_node)

        return tree, errors

    def _try_extract_function_signature(
        self, lines: list[str], start_idx: int
    ) -> Optional[Union[ast.FunctionDef, ast.AsyncFunctionDef]]:
        """Try to extract a function signature from lines starting at start_idx."""
        # Find the end of the signature (line ending with :)
        signature_lines = []
        for i in range(start_idx, len(lines)):
            line = lines[i]
            signature_lines.append(line)
            if ":" in line:
                break
            if i - start_idx > 5:  # Don't look too far
                return None

        signature = "\n".join(signature_lines)
        # Try to parse with a pass body
        test_code = signature.rstrip().rstrip(":") + ":\n    pass"
        try:
            tree = ast.parse(test_code)
            if tree.body and isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
                return tree.body[0]
        except SyntaxError:
            pass
        return None

    def _try_extract_class_header(
        self, lines: list[str], start_idx: int
    ) -> Optional[ast.ClassDef]:
        """Try to extract a class header from lines starting at start_idx."""
        signature_lines: list[str] = []
        for i in range(start_idx, len(lines)):
            line = lines[i]
            signature_lines.append(line)
            if ":" in line:
                break
            if i - start_idx > 3:  # Don't look too far
                return None

        signature = "\n".join(signature_lines)
        # Try to parse with a pass body
        test_code = signature.rstrip().rstrip(":") + ":\n    pass"
        try:
            tree = ast.parse(test_code)
            if tree.body and isinstance(tree.body[0], ast.ClassDef):
                return tree.body[0]
        except SyntaxError:
            pass
        return None

    def _get_error_context(
        self, source: str, line_number: Optional[int], context_lines: int = 2
    ) -> Optional[str]:
        """Get source context around an error line."""
        if line_number is None:
            return None
        lines = source.splitlines()
        start = max(0, line_number - context_lines - 1)
        end = min(len(lines), line_number + context_lines)
        return "\n".join(lines[start:end])

    def _should_include_name(self, name: str) -> bool:
        """Check if a name should be included based on filter settings."""
        if name.startswith("__") and name.endswith("__"):
            return self.extract_dunder
        if name.startswith("_"):
            return self.extract_private
        return True

    def _extract_functions(self, tree: ast.Module) -> list[FunctionInfo]:
        """Extract all top-level function definitions."""
        functions: list[FunctionInfo] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef):
                if self._should_include_name(node.name):
                    functions.append(self._process_function(node))
            elif isinstance(node, ast.AsyncFunctionDef):
                if self._should_include_name(node.name):
                    functions.append(self._process_function(node, is_async=True))
        return functions

    def _process_function(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef], is_async: bool = False
    ) -> FunctionInfo:
        """Process a function definition node."""
        # Extract parameters
        parameters = self._extract_parameters(node)

        # Extract decorators
        decorators = [self._process_decorator(d) for d in node.decorator_list]

        # Extract return annotation
        return_annotation: Optional[str] = None
        if node.returns:
            return_annotation = ast.unparse(node.returns)

        # Calculate complexity
        complexity = self._calculate_complexity(node)

        # Get docstring
        docstring = ast.get_docstring(node)

        return FunctionInfo(
            name=node.name,
            docstring=docstring,
            parameters=parameters,
            return_annotation=return_annotation,
            decorators=decorators,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            is_async=is_async,
            complexity_score=complexity,
            node_type=NodeType.ASYNC_FUNCTION if is_async else NodeType.FUNCTION,
        )

    def _extract_parameters(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> list[ParameterInfo]:
        """Extract function parameters with their details."""
        parameters: list[ParameterInfo] = []
        args = node.args

        # Regular positional arguments
        for i, arg in enumerate(args.args):
            default: Optional[str] = None
            default_offset = len(args.args) - len(args.defaults)
            if i >= default_offset:
                default_idx = i - default_offset
                default = ast.unparse(args.defaults[default_idx])

            param = ParameterInfo(
                name=arg.arg,
                annotation=ast.unparse(arg.annotation) if arg.annotation else None,
                default=default,
                kind=ParameterKind.POSITIONAL_OR_KEYWORD,
            )
            parameters.append(param)

        # Positional-only arguments (Python 3.8+)
        for arg in getattr(args, "posonlyargs", []):
            parameters.append(
                ParameterInfo(
                    name=arg.arg,
                    annotation=ast.unparse(arg.annotation) if arg.annotation else None,
                    default=None,
                    kind=ParameterKind.POSITIONAL_ONLY,
                )
            )

        # *args
        if args.vararg:
            parameters.append(
                ParameterInfo(
                    name=args.vararg.arg,
                    annotation=ast.unparse(args.vararg.annotation)
                    if args.vararg.annotation
                    else None,
                    default=None,
                    kind=ParameterKind.VAR_POSITIONAL,
                )
            )

        # Keyword-only arguments
        for i, arg in enumerate(args.kwonlyargs):
            default = None
            if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
                default = ast.unparse(args.kw_defaults[i])

            parameters.append(
                ParameterInfo(
                    name=arg.arg,
                    annotation=ast.unparse(arg.annotation) if arg.annotation else None,
                    default=default,
                    kind=ParameterKind.KEYWORD_ONLY,
                )
            )

        # **kwargs
        if args.kwarg:
            parameters.append(
                ParameterInfo(
                    name=args.kwarg.arg,
                    annotation=ast.unparse(args.kwarg.annotation) if args.kwarg.annotation else None,
                    default=None,
                    kind=ParameterKind.VAR_KEYWORD,
                )
            )

        return parameters

    def _process_decorator(self, decorator: ast.expr) -> DecoratorInfo:
        """Process a decorator node."""
        if isinstance(decorator, ast.Name):
            return DecoratorInfo(name=decorator.id)
        elif isinstance(decorator, ast.Attribute):
            return DecoratorInfo(name=ast.unparse(decorator))
        elif isinstance(decorator, ast.Call):
            name = ast.unparse(decorator.func)
            args_list: list[str] = [ast.unparse(arg) for arg in decorator.args]
            kwargs_dict: dict[str, str] = {}
            for kw in decorator.keywords:
                kwargs_dict[kw.arg or ""] = ast.unparse(kw.value)
            return DecoratorInfo(name=name, args=args_list, kwargs=kwargs_dict)
        else:
            return DecoratorInfo(name=ast.unparse(decorator))

    def _calculate_complexity(
        self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]
    ) -> int:
        """Calculate cyclomatic complexity of a function."""
        complexity = 1  # Base complexity

        for child in ast.walk(node):
            # Decision points that increase complexity
            if isinstance(child, (ast.If, ast.While, ast.For, ast.AsyncFor)):
                complexity += 1
            elif isinstance(child, ast.ExceptHandler):
                complexity += 1
            elif isinstance(child, (ast.And, ast.Or)):
                complexity += 1
            elif isinstance(child, ast.comprehension):
                complexity += 1
                # Each if clause in comprehension
                complexity += len(child.ifs)
            elif isinstance(child, ast.IfExp):  # Ternary operator
                complexity += 1
            # Match cases (Python 3.10+)
            elif hasattr(ast, "Match") and isinstance(child, ast.Match):
                complexity += len(child.cases)

        return complexity

    def _extract_classes(self, tree: ast.Module) -> list[ClassInfo]:
        """Extract all top-level class definitions."""
        classes: list[ClassInfo] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                if self._should_include_name(node.name):
                    classes.append(self._process_class(node))
        return classes

    def _process_class(self, node: ast.ClassDef) -> ClassInfo:
        """Process a class definition node."""
        # Extract base classes
        bases: list[str] = []
        for base in node.bases:
            bases.append(ast.unparse(base))

        # Extract decorators
        decorators = [self._process_decorator(d) for d in node.decorator_list]

        # Extract methods and attributes
        methods: list[FunctionInfo] = []
        attributes: list[str] = []

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if self._should_include_name(item.name):
                    is_async = isinstance(item, ast.AsyncFunctionDef)
                    methods.append(self._process_function(item, is_async=is_async))
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        attributes.append(target.id)
            elif isinstance(item, ast.AnnAssign):
                if isinstance(item.target, ast.Name):
                    attributes.append(item.target.id)

        # Get docstring
        docstring = ast.get_docstring(node)

        return ClassInfo(
            name=node.name,
            docstring=docstring,
            bases=bases,
            decorators=decorators,
            methods=methods,
            attributes=attributes,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
        )

    def _extract_imports(self, tree: ast.Module) -> list[ImportInfo]:
        """Extract all import statements."""
        imports: list[ImportInfo] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases: dict[str, str] = {}
                    if alias.asname:
                        aliases = {alias.name: alias.asname}
                    imports.append(
                        ImportInfo(
                            module=None,
                            names=[alias.name],
                            aliases=aliases,
                            line_number=node.lineno,
                            is_from_import=False,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                names: list[str] = []
                aliases: dict[str, str] = {}
                for alias in node.names:
                    names.append(alias.name)
                    if alias.asname:
                        aliases[alias.name] = alias.asname
                imports.append(
                    ImportInfo(
                        module=module,
                        names=names,
                        aliases=aliases,
                        line_number=node.lineno,
                        is_from_import=True,
                    )
                )
        return imports

    def _extract_variables(self, tree: ast.Module) -> list[VariableInfo]:
        """Extract top-level variable assignments."""
        variables: list[VariableInfo] = []
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        variables.append(
                            VariableInfo(
                                name=target.id,
                                value_repr=self._safe_unparse(node.value),
                                annotation=None,
                                line_number=node.lineno,
                                is_constant=self._is_constant_name(target.id),
                            )
                        )
            elif isinstance(node, ast.AnnAssign):
                if isinstance(node.target, ast.Name):
                    annotation = ast.unparse(node.annotation) if node.annotation else None
                    variables.append(
                        VariableInfo(
                            name=node.target.id,
                            value_repr=self._safe_unparse(node.value) if node.value else None,
                            annotation=annotation,
                            line_number=node.lineno,
                            is_constant=self._is_constant_name(node.target.id),
                        )
                    )
        return variables

    def _safe_unparse(self, node: Optional[ast.expr]) -> Optional[str]:
        """Safely unparse an AST node, handling errors."""
        if node is None:
            return None
        try:
            return ast.unparse(node)
        except Exception:
            return "<unparseable>"

    def _is_constant_name(self, name: str) -> bool:
        """Check if a name follows constant naming convention."""
        return name.isupper() or name.startswith("_") and name[1:].isupper()


# Convenience functions


def parse_python(source: str, **kwargs) -> ParseResult:
    """
    Parse Python source code and extract structured information.

    Args:
        source: Python source code string
        **kwargs: Additional arguments passed to PythonParser

    Returns:
        ParseResult with extracted information
    """
    parser = PythonParser(**kwargs)
    return parser.parse(source)


def parse_python_file(filepath: Union[str, Path], **kwargs) -> ParseResult:
    """
    Parse a Python file and extract structured information.

    Args:
        filepath: Path to the Python file
        **kwargs: Additional arguments passed to PythonParser

    Returns:
        ParseResult with extracted information
    """
    parser = PythonParser(**kwargs)
    return parser.parse_file(filepath)


def extract_functions(source: str, **kwargs) -> list[FunctionInfo]:
    """
    Extract functions from Python source code.

    Args:
        source: Python source code string
        **kwargs: Additional arguments passed to PythonParser

    Returns:
        List of FunctionInfo objects
    """
    result = parse_python(source, **kwargs)
    return result.functions


def extract_classes(source: str, **kwargs) -> list[ClassInfo]:
    """
    Extract classes from Python source code.

    Args:
        source: Python source code string
        **kwargs: Additional arguments passed to PythonParser

    Returns:
        List of ClassInfo objects
    """
    result = parse_python(source, **kwargs)
    return result.classes


def extract_imports(source: str, **kwargs) -> list[ImportInfo]:
    """
    Extract imports from Python source code.

    Args:
        source: Python source code string
        **kwargs: Additional arguments passed to PythonParser

    Returns:
        List of ImportInfo objects
    """
    result = parse_python(source, **kwargs)
    return result.imports
