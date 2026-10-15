"""Seam between the entry and area modules."""
_bound = None
def bind(namespace):
    global _bound
    _bound = namespace
def set(name, value):
    _bound[name] = value
def __getattr__(name):
    try:
        return _bound[name]
    except (TypeError, KeyError):
        raise AttributeError(name) from None
def __dir__():
    return sorted(_bound) if isinstance(_bound, dict) else []
