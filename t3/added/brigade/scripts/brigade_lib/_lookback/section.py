from brigade_lib._lookback.proposals import CATEGORIES
from brigade_lib._lookback.setting import look_back_of
from brigade_lib._lookback.state import briefs, moment, unreadable


def look_back_section(restaurant, since):
    if not look_back_of(restaurant.meta):
        return []
    items = []
    for data in briefs(restaurant):
        recorded = data.get("recorded")
        if not isinstance(recorded, dict):
            continue
        if recorded.get("kind") != "proposals":
            continue
        at = recorded.get("at") or ""
        if since:
            bound, stamped = moment(since), moment(at)
            if bound is not None and stamped is not None:
                if not stamped > bound:
                    continue
            elif not at > since:
                continue
        for proposal in recorded.get("proposals") or []:
            words = CATEGORIES.get(proposal.get("category") or "", proposal.get("category") or "")
            items.append(
                f"- {words}: {proposal.get('change') or ''} "
                f"(evidence {proposal.get('evidence') or ''}, look-backs/{data.get('id') or ''}.json)"
            )
    if not items:
        return []
    lines = ["## Look-back", "", *items]
    for name in unreadable(restaurant):
        lines.append(f"- look-backs/{name} cannot be read; look-back state skips it")
    lines += [
        "",
        "Proposals are in `look-backs/`. They are not tickets and they are not decisions. "
        "Only a yes from the owner turns one into `ticket add`, by hand.",
        "",
    ]
    return lines
