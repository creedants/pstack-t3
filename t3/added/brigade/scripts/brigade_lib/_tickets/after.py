import re
from dataclasses import dataclass
from brigade_lib.shared import BrigadeError, slug
from brigade_lib.store import Restaurant, siblings


@dataclass(frozen=True)
class Rails:
    """The rail rows a dependency reads, by sibling directory name. A store that did not read holds a str reason."""
    own: str
    stores: dict
    asked: tuple | None = None


def sibling_named(restaurant, to):
    """A sibling's directory name and restaurant.json, found by directory name or restaurant name."""
    meta = restaurant.meta
    names = siblings(restaurant.dir, meta.get("projectRoot"))
    name = to if to in names else next((n for n in names if slug(names[n].get("restaurant") or n) == slug(to)), None)
    if name is None:
        raise BrigadeError(f"{to} is not a sibling coordinator on {meta.get('projectRoot')}")
    return name, names[name]


def read_waits(restaurant, given=None):
    """Call before the store lock. Reads each sibling a waiting ticket, the given ticket, or a dependency chain from it names, at most once."""
    own = restaurant.dir.name
    asked = None
    if given:
        match = re.fullmatch(r"(?:(.+)/)?(T\d+)", given)
        if match is None:
            raise BrigadeError(f"{given} is not a ticket; use T<n> or <sibling>/T<n>")
        store, ident = match.groups()
        asked = (sibling_named(restaurant, store)[0] if store else own, ident)
    rows = restaurant.rows("rail.tsv")
    listed = siblings(restaurant.dir, restaurant.meta.get("projectRoot"))
    stores = {}

    def table(store):
        if store == own:
            return rows
        if store not in stores:
            stores[store] = sibling_rail(restaurant.dir.parent / store, listed)
        return stores[store]

    for row in rows:
        if row["state"] == "waiting" and row["after"]:
            table(key_of(own, row["after"])[0])
    node, seen = asked, set()
    while node is not None and node not in seen:
        seen.add(node)
        found = table(node[0])
        if not readable(found):
            break
        row = row_in(found, node[1])
        if row is None or row["state"] != "waiting" or not row["after"]:
            break
        node = key_of(own, row["after"])
    return Rails(own, stores, asked)


def sibling_rail(directory, listed):
    """A sibling's rail rows, or a short reason it did not read. Never writes to the sibling."""
    if directory.name not in listed:
        return "not a sibling any more"
    try:
        store = Restaurant(directory)
        with store.read_lock(create=False):
            return tuple(store.rows("rail.tsv"))
    except (BrigadeError, OSError) as error:
        return str(error)


def key_of(own, cell):
    store, _, ident = cell.rpartition("/")
    return (store or own, ident)


def table_of(rails, rows, store):
    return rows if store == rails.own else rails.stores.get(store)


def readable(table):
    return isinstance(table, (list, tuple))


def row_in(table, ident):
    return next((row for row in table if row["id"] == ident), None)


def wait_words(rails, rows, cell, ident):
    """The words for a waiting ticket whose dependency is cell. None when the dependency is done."""
    store, target = key_of(rails.own, cell)
    where = "this rail" if store == rails.own else f"{store}'s rail"
    table = table_of(rails, rows, store)
    if not readable(table):
        return f"waiting on {cell}; cannot read {store}'s rail"
    row = row_in(table, target)
    if row is None:
        return f"waiting on {cell}, which is not on {where}"
    if row["state"] == "done":
        return None
    if row["state"] == "dropped":
        return f"waiting on {cell}, which was dropped and is never done; run ticket set {ident} --after '' to stop waiting"
    if row["state"] == "moved":
        destination = row["dish"].removeprefix("to:")
        return (f"waiting on {cell}, which moved to {destination} and is not done here; "
                f"point --after at the ticket {destination} filed for it")
    return f"waiting on {cell}"


def after_holds(rails, rows):
    """Call under the lock. {ticket id: words} for each waiting ticket whose dependency is not done, in rail order."""
    held = {}
    for row in rows:
        if row["state"] == "waiting" and row["after"]:
            words = wait_words(rails, rows, row["after"], row["id"])
            if words:
                held[row["id"]] = words
    return held


def after_cell(rails, rows, given, ident=None):
    """Call under the lock. The cell to store for the --after text given, or a refusal that writes nothing."""
    if not given:
        return ""
    store, target = rails.asked
    cell = target if store == rails.own else f"{store}/{target}"
    where = "this rail" if store == rails.own else f"{store}'s rail"
    table = table_of(rails, rows, store)
    if not readable(table):
        raise BrigadeError(f"cannot read {store}'s rail; nothing changed")
    if row_in(table, target) is None:
        raise BrigadeError(f"{cell} is not on {where}; nothing changed")
    if ident is None:
        return cell
    if (store, target) == (rails.own, ident):
        raise BrigadeError(f"{ident} cannot wait on itself; nothing changed")
    node, seen = (store, target), set()
    while node not in seen:
        if node == (rails.own, ident):
            raise BrigadeError(f"{ident} would wait on itself through {cell}; nothing changed")
        seen.add(node)
        found = table_of(rails, rows, node[0])
        if not readable(found):
            raise BrigadeError(f"cannot read {node[0]}'s rail; nothing changed")
        row = row_in(found, node[1])
        if row is None or row["state"] != "waiting" or not row["after"]:
            break
        node = key_of(rails.own, row["after"])
    return cell


def after_change(restaurant, ticket, rails, given):
    """Call under the lock for ticket set. The cell to store and the words it reports."""
    if ticket["state"] != "waiting":
        raise BrigadeError(f"{ticket['id']} is {ticket['state']}; --after changes a waiting ticket only")
    cell = after_cell(rails, restaurant.rows("rail.tsv"), given, ticket["id"])
    return cell, (f"after {cell}" if cell else "no after")
