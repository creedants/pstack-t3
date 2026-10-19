import importlib.util
import sys
import types
from pathlib import Path

_cache = {}


def _load(name, filename):
    if name not in _cache:
        path = Path(__file__).resolve().parent.parent / filename
        spec = importlib.util.spec_from_file_location(f"pstack_{name}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _cache[name] = module
    return _cache[name]


def _catalog():
    return _load("catalog", "catalog.py")


def _release():
    return _load("release", "release.py")


_size_limit_module = None


def _size_limit():
    global _size_limit_module
    if _size_limit_module is None:
        path = Path(__file__).resolve().parent.parent / "size_limit.py"
        if path.is_file():
            _size_limit_module = _load("size_limit", "size_limit.py")
        else:
            _size_limit_module = types.SimpleNamespace(findings=lambda root: [])
    return _size_limit_module
