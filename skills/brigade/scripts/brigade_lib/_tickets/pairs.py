from brigade_lib._tickets.labels import whole_share
from brigade_lib.shared import BrigadeError, clean, one_of
from brigade_lib.store import KINDS, MODES

def a_mode(text):
    """The text when it names a review depth, or "" when it names none."""
    return text if text in MODES else ""


CAP_WORDS = {"pairs": "kind=<n> pairs, such as polish=1,study=1",
             "kind": "a kind of {one_of}",
             "value": "{kind} takes a whole number of 1 or more, or nothing for no cap",
             "twice": "names {kind} twice; pass one pair per kind"}


REVIEW_WORDS = {"pairs": "kind=<mode> pairs, such as feature=full,fix=light",
                "kind": "a kind of {one_of}",
                "value": "{kind} takes full or light, or nothing for no depth",
                "twice": "names {kind} twice; pass one pair per kind"}


def setting_pairs(text, flag, values, check, words):
    """A comma-separated flag of kind=value pairs, as {kind: value}. Empty text gives no pair.

    A pair with no =, a kind outside `values`, a value `check` refuses, and a second pair for one kind are each
    refused. An empty value is kept as "" and means the kind's setting is cleared.
    """
    found = {}
    for item in text.split(","):
        pair = clean(item)
        if not pair:
            continue
        if "=" not in pair:
            raise BrigadeError(f"{flag} takes {words['pairs']}; got {pair!r}")
        kind, value = pair.split("=", 1)
        if kind not in values:
            raise BrigadeError(f"{flag} takes {words['kind'].format(one_of=one_of(values))}; got {kind!r}")
        if value and not check(value):
            raise BrigadeError(f"{flag} {words['value'].format(kind=kind)}; got {value!r}")
        if kind in found:
            raise BrigadeError(f"{flag} {words['twice'].format(kind=kind)}")
        found[kind] = value
    return found


def cap_pairs(text):
    """--cap as {kind: cap}. A cap is a whole number of 1 or more."""
    return {kind: int(whole_share(value)) if value else "" for kind, value in
            setting_pairs(text, "--cap", KINDS, whole_share, CAP_WORDS).items()}


def review_pairs(text):
    """--review as {kind: mode}. A mode is full or light."""
    return setting_pairs(text, "--review", KINDS, a_mode, REVIEW_WORDS)
