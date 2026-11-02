from brigade_lib.compact import refuse_setting
from brigade_lib.shared import BrigadeError

KEY = "ciTarget"
AT_KEY = "ciTargetAt"


def ci_target_of(meta):
    """The minutes of CI time that trips the ci-time trigger.

    A missing key takes 0, which turns the trigger off. 0 and an empty string turn it off.
    A present value that is a boolean, is not an integer, or is under 1 is refused.
    """
    if KEY not in meta:
        return 0
    value = meta[KEY]
    if value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        refuse_setting(KEY, value, "it is not a whole number of 1 or more")
    if value == 0:
        return 0
    if value < 1:
        refuse_setting(KEY, value, "it is not a whole number of 1 or more")
    return value


def parse_ci_target(text):
    """The integer a --ci-target flag stores. Empty string stores 0, turning the trigger off."""
    if text == "":
        return 0
    try:
        value = int(text)
    except (TypeError, ValueError):
        raise BrigadeError(f"--ci-target takes a whole number of 1 or more, or empty string to turn it off, got '{text}'") from None
    if value < 0:
        raise BrigadeError(f"--ci-target takes a whole number of 1 or more, or empty string to turn it off, got '{text}'")
    return value
