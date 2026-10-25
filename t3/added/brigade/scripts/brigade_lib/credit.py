"""The prCredit setting and the credit line a pull request body carries."""
from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError, clean

KEY = "prCredit"
OFF_LINE = "credit: off for this restaurant; set --pr-credit on to turn it on"


def parse_credit(text):
    """The boolean --pr-credit records. on is true, off is false, and any other value is refused."""
    if text == "on":
        return True
    if text == "off":
        return False
    raise BrigadeError(f"--pr-credit takes on or off, got {text!r}")


def credit_on(meta):
    """Whether this restaurant credits the models. A missing key reads as off.

    A present value that is not a boolean calls refuse_setting(KEY, value, "it is not true or false").
    """
    if KEY not in meta:
        return False
    value = meta[KEY]
    if not isinstance(value, bool):
        refuse_setting(KEY, value, "it is not true or false")
    return value


def _values(rows, dish_id, column):
    """The distinct nonempty cells of one column over every row of dish_id, in first-recorded order."""
    found = []
    for row in rows:
        if row.get("dish") != dish_id:
            continue
        value = clean(row.get(column))
        if value and value not in found:
            found.append(value)
    return found


def credit_line(rows, dish_id, coordinator=""):
    """One line for these pass.tsv rows, with every part that names nothing left out.

    Every row of dish_id counts, a panel member row included. Authors are the distinct author cells and
    verifiers the distinct verifier cells, in first-recorded order, joined with ", ". Returns "" when no
    part is left.
    """
    parts = []
    if coordinator:
        parts.append(f"Coordinated by {coordinator}")
    authors = _values(rows, dish_id, "author")
    if authors:
        parts.append(f"Written by {', '.join(authors)}")
    verifiers = _values(rows, dish_id, "verifier")
    if verifiers:
        parts.append(f"Reviewed by {', '.join(verifiers)}")
    return ". ".join(parts) + "." if parts else ""


def credit_text(restaurant, dish_id, coordinator):
    """The line the credit command prints.

    It reads under read_lock(create=False), finds the dish first, so an unknown id raises
    no <id> in dishes.tsv, and returns OFF_LINE when credit_on(restaurant.meta) is false.
    """
    with restaurant.read_lock(create=False):
        restaurant.find("dishes.tsv", dish_id)
        if not credit_on(restaurant.meta):
            return OFF_LINE
        return credit_line(restaurant.rows("pass.tsv"), dish_id, clean(coordinator))
