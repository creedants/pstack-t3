import ast
from pathlib import Path

FENCE_REFERENCE = "landing/support.py"
FENCE_FUNCTION = "fence_git_environment"
FENCE_SETUP = "setUpModule"
FENCE_BRIGADE_DIR = "brigade"
FENCE_BRIGADE_SUPPORT = "support.py"
FENCE_BRIGADE_MODULE = "tests.brigade.support"
FENCE_LANDING_DIR = "landing"
FENCE_LANDING_SUPPORT = "support.py"
FENCE_LANDING_MODULE = "tests.landing.support"

from .pyast import _calls_name, _module_function, _read_module, _target_binds, _unreadable_finding

def _module_fence_findings(module, rel, reference, sentence, label):
    findings = []
    fence = _module_function(module, FENCE_FUNCTION)
    setup = _module_function(module, FENCE_SETUP)
    if fence is None:
        findings.append(f"{rel}: has no def {FENCE_FUNCTION} directly in the module body. {sentence}")
    elif ast.dump(fence) != reference:
        findings.append(f"{rel}: {FENCE_FUNCTION} differs from the one in {label}/{FENCE_REFERENCE}. {sentence}")
    if setup is None:
        findings.append(f"{rel}: has no def {FENCE_SETUP} directly in the module body. {sentence}")
    elif not _calls_name(setup, FENCE_FUNCTION):
        findings.append(f"{rel}: {FENCE_SETUP} contains no call to the bare name {FENCE_FUNCTION}. {sentence}")
    return findings


def _is_support_import(node):
    relative = node.level == 1 and node.module == "support"
    absolute = node.level == 0 and node.module in (FENCE_BRIGADE_MODULE, FENCE_LANDING_MODULE)
    return relative or absolute


def _setup_name_binding(statement):
    if isinstance(statement, ast.FunctionDef) and statement.name == FENCE_SETUP:
        return "def"
    if isinstance(statement, (ast.AsyncFunctionDef, ast.ClassDef)) and statement.name == FENCE_SETUP:
        return "assign"
    if isinstance(statement, ast.Assign) and any(_target_binds(target, FENCE_SETUP) for target in statement.targets):
        return "assign"
    if isinstance(statement, ast.AnnAssign) and statement.value is not None and _target_binds(statement.target, FENCE_SETUP):
        return "assign"
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            if (alias.asname or alias.name.split(".", 1)[0]) == FENCE_SETUP:
                return "wrong"
        return None
    if not isinstance(statement, ast.ImportFrom):
        return None
    found = None
    for alias in statement.names:
        if (alias.asname or alias.name) != FENCE_SETUP:
            continue
        if alias.name != FENCE_SETUP:
            found = "alias"
        elif _is_support_import(statement):
            found = "import"
        else:
            found = "wrong"
    return found


def _renames_setup_away(statement):
    if not isinstance(statement, ast.ImportFrom):
        return False
    return any(alias.name == FENCE_SETUP and alias.asname not in (None, FENCE_SETUP) for alias in statement.names)


def _last_setup_binding(module):
    binding = None
    renamed = False
    for statement in module.body:
        found = _setup_name_binding(statement)
        if found is not None:
            binding = found
        elif _renames_setup_away(statement):
            renamed = True
    if binding is not None:
        return binding
    if renamed:
        return "alias"
    return None


def _imports_only_support_classes(module, class_names):
    if not module.body:
        return False
    for statement in module.body:
        if not isinstance(statement, ast.ImportFrom) or not _is_support_import(statement):
            return False
        if not statement.names or any(alias.name not in class_names for alias in statement.names):
            return False
    return True


def _area_findings(module, rel, reference, sentence, label, class_names, support_module):
    binding = _last_setup_binding(module)
    if binding == "import":
        return []
    if binding == "alias":
        return [f"{rel}: imports {FENCE_SETUP} under another name. {sentence}"]
    if binding == "wrong":
        return [f"{rel}: binds {FENCE_SETUP} by an import that is not from {support_module}. {sentence}"]
    if binding == "assign":
        return [f"{rel}: binds {FENCE_SETUP} to something other than an import from {support_module}. {sentence}"]
    if binding == "def":
        return _module_fence_findings(module, rel, reference, sentence, label)
    if _imports_only_support_classes(module, class_names):
        return []
    return [f"{rel}: has no {FENCE_SETUP} bound directly in the module body. {sentence}"]


def _package_findings(tests_dir, label, reference, copy, area_dir, area_support, area_module):
    package = tests_dir / area_dir
    if not package.is_dir():
        return []
    sentence = f"Import {FENCE_SETUP} from {label}/{area_dir}/{area_support}"
    support_rel = f"{label}/{area_dir}/{area_support}"
    findings = []
    support, error = _read_module(package / area_support)
    if error is not None:
        findings.append(_unreadable_finding(support_rel, error))
        class_names = frozenset()
    else:
        class_names = frozenset(node.name for node in support.body if isinstance(node, ast.ClassDef))
        findings.extend(_module_fence_findings(support, support_rel, reference, copy, label))
    for file in sorted(package.glob("test_*.py")):
        if not file.is_file():
            continue
        rel = f"{label}/{area_dir}/{file.name}"
        module, error = _read_module(file)
        if error is not None:
            findings.append(_unreadable_finding(rel, error))
            continue
        findings.extend(_area_findings(module, rel, reference, sentence, label, class_names, area_module))
    return findings


def _brigade_findings(tests_dir, label, reference, copy):
    return _package_findings(tests_dir, label, reference, copy, FENCE_BRIGADE_DIR, FENCE_BRIGADE_SUPPORT, FENCE_BRIGADE_MODULE)


def _landing_findings(tests_dir, label, reference, copy):
    return _package_findings(tests_dir, label, reference, copy, FENCE_LANDING_DIR, FENCE_LANDING_SUPPORT, FENCE_LANDING_MODULE)


def fence_findings(tests_dir):
    """Name test modules whose git environment fence does not match tests/landing/support.py.

    The comparison value is ast.dump of the last def fence_git_environment
    directly in the module body. That dump ignores comments, whitespace, and
    line numbers and includes the docstring. Each top-level test_*.py module
    also needs a def setUpModule directly in the module body that calls the
    bare name fence_git_environment.

    A missing or unreadable landing/support.py, or one with no such function,
    returns that one finding and checks nothing else.

    An absent brigade directory adds no finding. When that directory is
    present, support.py uses the same rules and the same copy sentence as a
    top-level module. A missing or unreadable support.py reports that it could
    not be read or parsed and includes the exception type name. A missing file
    is FileNotFoundError. Each test_*.py file directly in that directory is an
    area module. Files in subdirectories are not checked.

    A missing landing/support.py reference returns that one finding and checks nothing else. This includes an absent landing directory. When that directory is present, its support.py and its test_*.py area modules follow the same
    rules as the brigade ones, with the landing support path in the findings.

    The last binding of the name setUpModule in an area module body decides.
    A from-import of that name from tests.brigade.support or
    tests.landing.support at level 0, or from .support at level 1, passes even when the import lists other names and the
    module defines a class with another name. A later statement that binds the
    name replaces that import. A from-import that binds the name setUpModule
    to a different imported name is an alias binding. A from-import that
    renames setUpModule away does not bind the name. That from-import after a
    binding does not replace that binding. The alias finding applies when no
    actual binding of the name was found. A from-import that binds the name
    from any other module is a finding. A plain import that binds the name
    setUpModule is that same other-module finding. A plain import that only
    renames setUpModule away does not bind the name. An assignment of the name
    is a finding. An annotated assignment with a value is a finding. An
    annotation with no value does not bind the name. A def setUpModule uses
    the top-level fence rules. An async def setUpModule or a class statement
    named setUpModule is a finding, including when a valid import came
    earlier. Those findings end with Import setUpModule from the area's
    support path.

    Only a statement in the module body can bind the name. An area module that
    does not bind the name still passes when every body statement imports
    classes defined directly in support.py and there is at least one such
    import. Anything else that leaves the name unbound is a finding.

    Path labels and both sentences use the tests directory name.
    """
    tests_dir = Path(tests_dir)
    label = tests_dir.name
    reference_path = tests_dir / FENCE_REFERENCE
    copy = f"Copy {FENCE_FUNCTION} and {FENCE_SETUP} from {label}/{FENCE_REFERENCE}"
    reference = None
    if reference_path.is_file():
        try:
            reference_function = _module_function(ast.parse(reference_path.read_text(), str(reference_path)), FENCE_FUNCTION)
        except (OSError, ValueError, SyntaxError):
            reference_function = None
        reference = ast.dump(reference_function) if reference_function else None
    if reference is None:
        return [f"{label}/{FENCE_REFERENCE}: is missing, cannot be read or parsed, or has no def {FENCE_FUNCTION} directly in the module body, so no test module can be compared with it"]
    findings = []
    for file in sorted(tests_dir.glob("test_*.py")):
        if not file.is_file():
            continue
        rel = f"{label}/{file.name}"
        module, error = _read_module(file)
        if error is not None:
            findings.append(_unreadable_finding(rel, error))
            continue
        findings.extend(_module_fence_findings(module, rel, reference, copy, label))
    findings.extend(_brigade_findings(tests_dir, label, reference, copy))
    findings.extend(_landing_findings(tests_dir, label, reference, copy))
    return findings
