"""Seat parsing, matching, catalog helpers, levels, cursor, and OpenCode metering."""


import fnmatch
import re


from _roles.base import BUDGETS, CURSOR_DRIVER, DRIVER_RUNTIME_MODES, EFFORT_IDS, EXCLUDE_RULE, INHERIT, LADDER, RUNTIME_MODE_GAP, RolesError, SPECIAL


def parse_seat(text):
    """Parse `inherit` or `provider/model[?option=value&option=value]`."""
    text = text.strip()
    if text == INHERIT:
        return INHERIT
    head, _, query = text.partition("?")
    provider, slash, model = head.partition("/")
    if not slash or not provider or not model:
        raise RolesError(f"seat {text!r}: expected 'inherit' or 'provider/model[?option=value]'")
    options = {}
    for pair in filter(None, query.split("&")):
        key, equals, value = pair.partition("=")
        if not equals:
            raise RolesError(f"seat {text!r}: option {pair!r} needs a value")
        options[key] = {"true": True, "false": False}.get(value, value)
    seat = {"providerInstanceId": provider, "model": model}
    if options:
        seat["options"] = options
    return seat


def bare_id(model_id):
    return model_id.rsplit("/", 1)[-1].lower()


def normalized_bare(model_id):
    """Canonical id both Haiku checks compare.

    Removes a provider path prefix such as amazon-bedrock/, a Vertex @YYYYMMDD
    suffix, and one leading Bedrock prefix (anthropic., or us., eu., apac., or
    global. before anthropic.). Then folds dots and underscores to hyphens and
    removes a trailing -YYYYMMDD suffix.
    """
    text = bare_id(model_id)
    text = re.sub(r"@\d{8}$", "", text)
    text = re.sub(r"^(?:(?:us|eu|apac|global)\.)?anthropic\.", "", text)
    text = text.replace(".", "-").replace("_", "-")
    return re.sub(r"-\d{8}$", "", text)


def family(model_id):
    """Leading word of normalized_bare(model_id).

    opencode/muse-2-free -> muse, us.anthropic.claude-sonnet-5-5-v1:0 -> claude.
    """
    text = normalized_bare(model_id)
    return re.split(r"[-\d]", text, maxsplit=1)[0] or text


def model_matches(pattern, model_id):
    """True when pattern, a case-folded glob, names this model id's bare id or its normalized id."""
    if not isinstance(model_id, str) or not model_id:
        return False
    folded = pattern.lower()
    return (
        fnmatch.fnmatchcase(bare_id(model_id).lower(), folded)
        or fnmatch.fnmatchcase(normalized_bare(model_id), folded)
    )


def provider_matches(pattern, provider_id):
    """True when pattern, a case-folded glob, names the provider instance id."""
    return (
        isinstance(provider_id, str)
        and bool(provider_id)
        and fnmatch.fnmatchcase(provider_id.lower(), pattern.lower())
    )


def exclude_rule_matches(rule, seat, role=None):
    """True when every part of this rule holds for the seat.

    A rule with no model applies to every model. A rule with roles applies only
    when the caller names a role in that list. An option pair holds only when the
    seat declares that option at that value.
    """
    if "provider" in rule and not provider_matches(rule["provider"], seat.get("providerInstanceId")):
        return False
    if "roles" in rule and (role is None or role not in rule["roles"]):
        return False
    if "model" in rule and not model_matches(rule["model"], seat.get("model")):
        return False
    declared = seat.get("options") or {}
    return all(declared.get(key) == value for key, value in rule.get("options", {}).items())


def exclude_label(rule):
    """The parts the rule has, joined with " + ", in provider, roles, model, then options order."""
    parts = []
    if "provider" in rule:
        parts.append(f"provider={rule['provider']}")
    if "roles" in rule:
        parts.append("roles=" + ", ".join(rule["roles"]))
    if "model" in rule:
        parts.append(rule["model"])
    if "options" in rule:
        parts.append(", ".join(f"{key}={value}" for key, value in rule["options"].items()))
    return " + ".join(parts)


def exclude_reason(seat, exclude=(), role=None):
    """The clause that refuses this seat, or None when the exclude list allows it.

    The clause is "<why>, and <EXCLUDE_RULE>", where <why> names the first entry
    the seat matches. Needs no catalog.
    """
    if not isinstance(seat, dict):
        return None
    for number, rule in enumerate(exclude, 1):
        if exclude_rule_matches(rule, seat, role):
            return (
                f"{bare_id(seat['model'])} matches exclude entry {number} ({exclude_label(rule)}), "
                f"and {EXCLUDE_RULE}"
            )
    return None


def runs_on_cursor(catalog, provider_id):
    """True only when the catalog names this provider with the cursor driverKind."""
    if provider_id is None:
        return False
    provider = catalog_provider(catalog, provider_id)
    return provider is not None and provider.get("driverKind") == CURSOR_DRIVER


def catalog_provider(catalog, provider_instance_id):
    if not catalog:
        return None
    return next(
        (item for item in catalog.get("providers") or [] if item.get("providerInstanceId") == provider_instance_id),
        None,
    )


def is_opencode_provider(catalog, provider_instance_id):
    provider = catalog_provider(catalog, provider_instance_id)
    if provider is not None and "driverKind" in provider:
        return provider["driverKind"] == "opencode"
    return provider_instance_id in ("opencode", "opencode-go")


def _free_marker(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 0
    if isinstance(value, float):
        return value == 0.0
    return value == "free"


def catalog_says_free(model):
    if not isinstance(model, dict):
        return False
    present = [key for key in ("cost", "pricing", "tier") if key in model]
    return bool(present) and all(_free_marker(model[key]) for key in present)


def opencode_model_is_metered(catalog, provider_instance_id, model_id):
    if not is_opencode_provider(catalog, provider_instance_id):
        return False
    model = find_model(catalog_provider(catalog, provider_instance_id), model_id)
    return not catalog_says_free(model)


def allow_refuses(catalog, provider_instance_id, model_id, allow):
    """True when the allow list refuses this metered OpenCode model."""
    if allow is None:
        return False
    if not opencode_model_is_metered(catalog, provider_instance_id, model_id):
        return False
    return not allow.names(model_id)


def opencode_allow_line(role, seat, allow, catalog):
    if not isinstance(seat, dict):
        return None
    provider = seat.get("providerInstanceId")
    model = seat.get("model")
    if not provider or not model:
        return None
    if not allow_refuses(catalog, provider, model, allow):
        return None
    return (
        f"{role}: {provider}/{model} is a metered OpenCode model and the allow list does not name {model}. "
        f"Add it with --allow-opencode {model}"
    )


def opencode_allow_lines(roles, allow, catalog):
    lines = []
    for name, seats in roles.items():
        for seat in seats:
            line = opencode_allow_line(name, seat, allow, catalog)
            if line:
                lines.append(line)
    return lines


def providers_by_id(catalog):
    return {provider["providerInstanceId"]: provider for provider in catalog["providers"]}


def models_of(provider):
    return (provider or {}).get("models") or []


def options_of(model):
    return (model or {}).get("options") or []


def runnable(provider):
    return bool(provider and provider.get("canRunChildTask") and models_of(provider))


def for_runtime_mode(catalog, mode):
    """Catalog copy in which a runnable provider cannot run children when DRIVER_RUNTIME_MODES lists its driver without the mode."""
    if catalog is None or mode is None:
        return catalog
    providers = []
    for provider in catalog.get("providers") or []:
        driver = provider.get("driverKind")
        modes = DRIVER_RUNTIME_MODES.get(driver)
        if modes is not None and mode not in modes and runnable(provider):
            reason = f"the {driver} driver lacks runtime mode {mode}"
            provider = {**provider, "canRunChildTask": False, "constraints": [reason], RUNTIME_MODE_GAP: reason}
        providers.append(provider)
    return {**catalog, "providers": providers}


def find_model(provider, model_id):
    return next((model for model in models_of(provider) if model["id"] == model_id), None)


def rank(value):
    return LADDER.get(value)


def effort_option(model):
    return next((option for option in options_of(model) if option["id"] in EFFORT_IDS and option.get("type") == "select"), None)


def check_option_values(seat, model):
    """Return problems for option values the catalog does not offer."""
    problems = []
    declared = {option["id"]: option for option in options_of(model)}
    for key, value in (seat.get("options") or {}).items():
        option = declared.get(key)
        if option is None:
            continue
        if option.get("type") == "boolean" and not isinstance(value, bool):
            problems.append(f"option {key}={value!r} must be true or false")
        elif option.get("type") == "select":
            choices = [choice["id"] for choice in option.get("options") or []]
            if value not in choices:
                problems.append(f"option {key}={value!r} is not one of {', '.join(choices)}")
    return problems


def apply_level(seat, model, target_level):
    option = effort_option(model) if model else None
    if target_level is None or option is None:
        return seat
    values = [choice["id"] for choice in option.get("options") or [] if choice["id"] not in SPECIAL and rank(choice["id"]) is not None]
    if not values:
        return seat
    allowed = [value for value in values if rank(value) <= rank(target_level)] or [min(values, key=rank)]
    current = (seat.get("options") or {}).get(option["id"])
    chosen = current if current in allowed else max(allowed, key=rank)
    return {**seat, "options": {**(seat.get("options") or {}), option["id"]: chosen}}


def apply_budget(seat, model, budget):
    cap = BUDGETS[budget]
    if cap is None:
        return seat
    return apply_level(seat, model, cap)


__all__ = [
    "parse_seat",
    "bare_id",
    "normalized_bare",
    "family",
    "model_matches",
    "provider_matches",
    "exclude_rule_matches",
    "exclude_label",
    "exclude_reason",
    "runs_on_cursor",
    "catalog_provider",
    "is_opencode_provider",
    "catalog_says_free",
    "opencode_model_is_metered",
    "allow_refuses",
    "opencode_allow_line",
    "opencode_allow_lines",
    "providers_by_id",
    "models_of",
    "options_of",
    "runnable",
    "for_runtime_mode",
    "find_model",
    "rank",
    "effort_option",
    "check_option_values",
    "apply_level",
    "apply_budget",
]
