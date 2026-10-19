import ast
from pathlib import Path

LIBRARY_SENTENCES = (
    "invalid choice",
    "the following arguments are required",
    "unrecognized arguments",
    "expected one argument",
)
EXEMPT_FUNCTION = "assert_refused_choice"
EXEMPT_FILE = "test_check.py"
EXEMPT_METHOD_PREFIX = "test_library_wording"


def _docstring_statement(node):
    body = getattr(node, "body", None)
    if not body:
        return None
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return first
    return None


def _ast_list(value):
    if isinstance(value, ast.AST):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, ast.AST):
                yield item


def _library_strings(node, stack):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            for child in _ast_list(value):
                yield from _library_strings(child, stack)
        inner = (*stack, node.name)
        skipped = _docstring_statement(node)
        for statement in node.body:
            if statement is not skipped:
                yield from _library_strings(statement, inner)
        return
    if isinstance(node, (ast.Module, ast.ClassDef)):
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            for child in _ast_list(value):
                yield from _library_strings(child, stack)
        skipped = _docstring_statement(node)
        for statement in node.body:
            if statement is not skipped:
                yield from _library_strings(statement, stack)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            yield node, stack
        return
    for child in ast.iter_child_nodes(node):
        yield from _library_strings(child, stack)


def _library_allowed(path, stack):
    if EXEMPT_FUNCTION in stack:
        return True
    nearest = stack[-1] if stack else ""
    return path.name == EXEMPT_FILE and nearest.startswith(EXEMPT_METHOD_PREFIX)


def library_wording_findings(tests_dir):
    """Report a string literal under tests_dir that contains one of argparse's own sentences.

    The sentences are invalid choice, the following arguments are required,
    unrecognized arguments, and expected one argument. The formatting of argparse's choice list differs
    between Python 3.10 and 3.12. A comment is not a finding, because it is
    not in the syntax tree. The docstring, meaning the first string statement
    of a module, class, function, or async function, is not a finding. A later
    bare string statement is a finding. A string is allowed when an enclosing
    function is named assert_refused_choice. It is also allowed when the file
    name is test_check.py and the nearest enclosing function name starts with
    test_library_wording. A module-level constant in test_check.py is still
    checked. A helper in that file whose name does not start with
    test_library_wording is still checked. The allowance is the function name,
    not a line number. An unreadable or unparseable Python file is a finding
    that names the exception type. An empty list means nothing matched.
    """
    tests_dir = Path(tests_dir)
    findings = []
    files = sorted(
        (path for path in tests_dir.rglob("*.py") if path.is_file()),
        key=lambda path: path.relative_to(tests_dir).as_posix(),
    )
    for path in files:
        rel = path.relative_to(tests_dir.parent).as_posix()
        try:
            tree = ast.parse(path.read_text(), str(path))
        except (OSError, ValueError, SyntaxError, UnicodeDecodeError) as error:
            findings.append(
                f"{rel}: could not be read or parsed, so argparse wording was not checked: {type(error).__name__}"
            )
            continue
        for node, stack in _library_strings(tree, ()):
            if _library_allowed(path, stack):
                continue
            for sentence in LIBRARY_SENTENCES:
                if sentence in node.value:
                    findings.append(
                        f"{rel}:{node.lineno}: contains the argparse phrase {sentence!r}. "
                        "Assert the exit status and the words this project wrote, or call assert_refused_choice"
                    )
    return findings
