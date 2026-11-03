from brigade_lib import seam
from brigade_lib.shared import BrigadeError

CATEGORIES = {
    "navigation": "slow navigation",
    "check-gap": "missing or unconnected check",
    "reviewer-rule": "reviewer rule",
    "expensive-call": "expensive tool call",
    "no-op-instruction": "instruction that changes nothing",
    "unreachable": "information the agent could not reach",
}

FIXES = {
    "check": "check",
    "reviewer-rule": "reviewer-rule",
    "pointer": "pointer",
    "tool": "tool",
    "deletion": "deletion",
    "access": "access",
}

KEYS = ("category", "fix", "evidence", "change", "quote")


def parse(text):
    blocks = [block for block in text.split("\n\n") if block.strip()]
    if not blocks:
        raise BrigadeError("the file holds no proposal")
    found = []
    problems = []
    for number, block in enumerate(blocks, 1):
        seen = {}
        for line in block.splitlines():
            if not line.strip():
                continue
            stripped = line.strip()
            for marker in ("- ", "* ", "+ "):
                if stripped.startswith(marker):
                    stripped = stripped[len(marker):].strip()
                    break
            if stripped[:2].isdigit() or (stripped[:1].isdigit() and stripped[1:2] in (".", ")")):
                _, _, rest = stripped.partition(" ")
                if rest:
                    stripped = rest.strip()
            if ":" not in stripped:
                problems.append(f"proposal {number}: {line.strip()} is not key: value")
                continue
            key, _, value = stripped.partition(":")
            key, value = key.strip(), value.strip()
            if key not in KEYS:
                problems.append(f"proposal {number}: unknown key {key}")
                continue
            if key in seen:
                problems.append(f"proposal {number}: repeated key {key}")
                continue
            seen[key] = value
        if not seen.get("category") and not any(
            problem.startswith(f"proposal {number}: ") for problem in problems
        ):
            problems.append(f"proposal {number}: no category")
        found.append(seen)
    if problems:
        raise BrigadeError("\n".join(problems))
    return found


def _evidence_problem(restaurant, value, threads):
    if value in threads:
        return None
    name = value[len("reports/"):] if value.startswith("reports/") else ""
    if not name or "/" in name or name in (".", ".."):
        return f"is not a file this store holds"
    if seam.report_entry(restaurant, name) != "file":
        return f"is not a file this store holds"
    return None


def problems(restaurant, brief, proposals):
    found = []
    for number, proposal in enumerate(proposals, 1):
        category = proposal.get("category") or ""
        if category not in CATEGORIES:
            found.append(f"proposal {number}: category {category} is not a category")
        fix = proposal.get("fix") or ""
        if fix not in FIXES:
            found.append(f"proposal {number}: fix {fix} is not a fix type")
        if not (proposal.get("change") or "").strip():
            found.append(f"proposal {number}: change holds no change")
        if len(proposal.get("quote") or "") > 120:
            found.append(f"proposal {number}: quote holds more than one short line")
        evidence = proposal.get("evidence") or ""
        problem = _evidence_problem(restaurant, evidence, brief.get("threads") or [])
        if problem:
            found.append(f"proposal {number}: evidence {evidence} {problem}")
    return found


def refused(problems):
    raise BrigadeError("\n".join(problems))
