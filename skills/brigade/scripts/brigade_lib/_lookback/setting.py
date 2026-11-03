from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError

KEY = "lookBack"
DEFAULT_DAYS = 7


def look_back_of(meta):
    if KEY not in meta:
        return DEFAULT_DAYS
    value = meta[KEY]
    if value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        refuse_setting(KEY, value, "it is not a whole number of 0 or more")
    return value


def parse_look_back(text):
    if text == "":
        return 0
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(
            f"--look-back takes a whole number of 0 or more, or empty string to turn it off, got '{text}'"
        ) from None
    if value < 0:
        raise BrigadeError(
            f"--look-back takes a whole number of 0 or more, or empty string to turn it off, got '{text}'"
        )
    return value
