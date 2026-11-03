from datetime import timedelta, timezone

from brigade_lib._lookback.state import moment


def look_back_due(snapshot):
    if not snapshot.look_back:
        return False
    if snapshot.mode != "light":
        return False
    if snapshot.rounds_since_look_back < 1:
        return False
    if not snapshot.look_back_at:
        return True
    recorded = moment(snapshot.look_back_at)
    if recorded is None:
        return True
    now = snapshot.moment
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now >= recorded + timedelta(days=snapshot.look_back)
