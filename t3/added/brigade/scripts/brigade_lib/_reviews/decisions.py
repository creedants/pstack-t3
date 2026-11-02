from brigade_lib import seam


def append_decision(restaurant, *, dish, question, options, default, kind):
    """Append one open row to 86.tsv and log it, and return its id."""
    ident = restaurant.next_id("86.tsv")
    restaurant.append("86.tsv", {"id": ident, "at": seam.now(), "state": "open", "dish": dish,
                                 "question": question, "options": options, "default": default,
                                 "answer": "", "kind": kind, "answered": ""})
    restaurant.log("decision", ident, "open", question)
    return ident
