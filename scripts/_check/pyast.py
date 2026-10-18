import ast
from pathlib import Path

def _module_function(module, name):
    """The last `def` of `name` directly in the module body. A later rebinding by assignment, import, or a block is not seen."""
    found = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == name]
    return found[-1] if found else None


def _calls_name(function, name):
    return any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name for node in ast.walk(function))


def _read_module(path):
    try:
        return ast.parse(path.read_text(), str(path)), None
    except (OSError, ValueError, SyntaxError) as error:
        return None, error


def _unreadable_finding(rel, error):
    return f"{rel}: could not be read or parsed, so its git environment fence was not checked: {type(error).__name__}"


def _target_binds(target, name):
    if isinstance(target, ast.Name):
        return target.id == name
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_target_binds(element, name) for element in target.elts)
    if isinstance(target, ast.Starred):
        return _target_binds(target.value, name)
    return False
