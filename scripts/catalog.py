#!/usr/bin/env python3
"""Write docs/skills.md, the catalog of every generated skill and playbook."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = [
    ("Entry point", lambda name: name in {"poteto-help", "poteto-mode"}),
    ("Multi-model workflows", lambda name: name in {"interrogate", "swarm", "arena", "architect", "reflect"}),
    ("Understand", lambda name: name in {"how", "why", "teach", "recall", "blast-radius", "bro", "ketchup"}),
    ("Plan and run long work", lambda name: name in {"brigade", "brigade-admin", "landing", "figure-it-out", "show-me-your-work"}),
    ("Verify", lambda name: name in {"tdd", "create-verification-skill", "maintain-verification-skill", "benchmark-checklist",
                                     "statechart", "create-formal-verification-skill", "maintain-formal-verification-skill"}),
    ("Write and clean", lambda name: name in {"unslop", "technical-writing", "no-comments", "typescript-best-practices"}),
    ("Setup and authoring", lambda name: name in {"setup-pstack", "pstack-runtime", "pstack-author-skill", "automate-me", "make-bot-ui", "correct"}),
    ("Principles", lambda name: name.startswith("principle-")),
]
BULLET = re.compile(r"^- \*\*(.+?)\.\*\* (.+)$", re.M)
COMMA_LIST = re.compile(r"^[A-Z][A-Za-z0-9 -]*(?:,\s*[A-Z][A-Za-z0-9 -]*)+\.?$")
# A bullet gives its file as a backticked playbooks/<name>.md that ends the bullet, with or without a "File: " lead-in.
GIVEN_FILE = re.compile(r"\s*(?:File: )?`(playbooks/[\w-]+\.md)`\.?$")
MODES_POINTER = "[The runtime's Modes section]("
LIST_OR_HEADING = re.compile(r"^(?:[-*+] |\d+\. |#)")
BOLD_LEAD = re.compile(r"^\*\*(.+?)\*\*")


def description(skill_md):
    match = re.search(r"^description:\s*(.+?)\n(?=\w+:|---)", skill_md.read_text(), re.S | re.M)
    text = " ".join(match.group(1).split()) if match else ""
    return text.strip().strip("\"'").replace("\\\"", "\"").replace("|", "\\|")


def kebab(title):
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")


def _playbook_summary(path):
    if not path.is_file():
        return ""
    paragraphs = []
    current = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped:
            current.append(stripped)
        elif current:
            paragraphs.append(" ".join(current))
            current = []
    if current:
        paragraphs.append(" ".join(current))
    heading = ""
    for index, paragraph in enumerate(paragraphs):
        if index == 0 and paragraph.startswith("#"):
            heading = paragraph.lstrip("#").strip()
            continue
        if paragraph.startswith(MODES_POINTER):
            continue
        if LIST_OR_HEADING.match(paragraph):
            break
        bold = BOLD_LEAD.match(paragraph)
        return bold.group(1).strip() if bold else paragraph.strip()
    return heading


def _scan_playbooks(text):
    entries = []
    unparsed = []
    in_section = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("## ") and not line.startswith("###"):
            if in_section:
                break
            if line[3:].strip() == "Playbooks":
                in_section = True
            continue
        if not in_section:
            continue
        match = BULLET.fullmatch(line)
        if match:
            title, summary = match.group(1), match.group(2)
            given = GIVEN_FILE.search(summary)
            file = given.group(1) if given else f"playbooks/{kebab(title)}.md"
            entries.append((title, file, GIVEN_FILE.sub("", summary).strip()))
            continue
        if line.startswith(("- ", "* ", "+ ")):
            unparsed.append(
                f'poteto-mode/SKILL.md:{number}: playbook bullet does not match "- **Title.** summary": {line.strip()[:120]}'
            )
            continue
        if COMMA_LIST.fullmatch(line):
            for raw in line.rstrip(".").split(","):
                title = raw.strip()
                entries.append((title, f"playbooks/{kebab(title)}.md", None))
    return entries, unparsed


def playbook_entries(skills):
    """Return one (title, file, summary) tuple per playbook in poteto-mode's Playbooks section, in list order.

    The section starts on the line after the first level-two heading whose text is Playbooks and ends before the next level-two heading. A level-three heading does not end it.
    A "- **Title.** summary" bullet resolves to the skill-relative path GIVEN_FILE finds at its end, otherwise playbooks/<kebab(title)>.md, and keeps its text with the GIVEN_FILE match removed.
    A comma-separated line of two or more capitalized titles (letters, digits, spaces, hyphens, optional trailing period) emits each title in order with playbooks/<kebab(title)>.md and a summary read from that file's leading bold lead, first intro paragraph, opening heading fallback, or empty string when missing.
    """
    return [
        (title, file, _playbook_summary(skills / "poteto-mode" / file) if summary is None else summary)
        for title, file, summary in _scan_playbooks((skills / "poteto-mode/SKILL.md").read_text())[0]
    ]


def playbooks(skills):
    return [f"| [{title}](../skills/poteto-mode/{file}) | {summary.replace('|', '/')} |" for title, file, summary in playbook_entries(skills)]


def missing_playbooks(skills):
    entries, unparsed = _scan_playbooks((skills / "poteto-mode/SKILL.md").read_text())
    missing = [
        f'poteto-mode/SKILL.md: playbook "{title}" resolves to {file}, which does not exist'
        for title, file, _ in entries
        if not (skills / "poteto-mode" / file).is_file()
    ]
    named = {file for _, file, _ in entries}
    playbooks_dir = skills / "poteto-mode/playbooks"
    unnamed = (
        [
            f"poteto-mode/playbooks/{path.name}: playbook file is not listed under ## Playbooks in poteto-mode/SKILL.md"
            for path in sorted(playbooks_dir.glob("*.md"))
            if f"playbooks/{path.name}" not in named
        ]
        if playbooks_dir.is_dir()
        else []
    )
    return unparsed + missing + unnamed


def render(skills):
    names = sorted(p.parent.name for p in skills.glob("*/SKILL.md"))
    lines = [
        "# Skills",
        "",
        "Every skill pstack-t3 installs. Generated by `scripts/catalog.py` from each skill's own description, so it never drifts.",
        "",
        "Invoke any skill in a T3 composer with `$name`. `$poteto-mode` calls most of the others for you as its playbook steps need them.",
        "",
    ]
    placed = set()
    for title, member in GROUPS:
        group = [name for name in names if member(name) and name not in placed]
        if not group:
            continue
        placed.update(group)
        lines += [f"## {title}", "", "| Skill | What it does |", "| --- | --- |"]
        lines += [f"| [`{name}`](../skills/{name}/SKILL.md) | {description(skills / name / 'SKILL.md')} |" for name in group]
        lines.append("")
    rest = [name for name in names if name not in placed]
    if rest:
        lines += ["## Other", "", "| Skill | What it does |", "| --- | --- |"]
        lines += [f"| [`{name}`](../skills/{name}/SKILL.md) | {description(skills / name / 'SKILL.md')} |" for name in rest]
        lines.append("")
    rows = playbooks(skills)
    lines += ["## poteto-mode playbooks", "", "`$poteto-mode` matches your request to one of these and follows its steps. Opening a PR runs at the end of the others.", "",
              "| Playbook | For |", "| --- | --- |", *rows, ""]
    return "\n".join(lines)


def write(skills=ROOT / "skills", out=ROOT / "docs/skills.md"):
    out.parent.mkdir(exist_ok=True)
    out.write_text(render(skills))
    return out


if __name__ == "__main__":
    print(f"wrote {write(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'skills')}")
