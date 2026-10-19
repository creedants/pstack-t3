import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import roles  # noqa: E402

CATALOG = json.loads((ROOT / "tests/fixtures/catalog.json").read_text())
PLAYBOOK_FILES = {
    "Investigation": "playbooks/investigation.md",
    "Bug fix": "playbooks/bug-fix.md",
    "Perf issue": "playbooks/perf-issue.md",
    "Hillclimb": "playbooks/hillclimb.md",
    "Runtime forensics": "playbooks/runtime-forensics.md",
    "Trace forensics": "playbooks/trace-forensics.md",
    "Feature": "playbooks/feature.md",
    "Refactoring": "playbooks/refactoring.md",
    "Prototype": "playbooks/prototype.md",
    "Visual parity": "playbooks/visual-parity.md",
    "Authoring or modifying a skill": "playbooks/authoring-a-skill.md",
    "Eval": "playbooks/eval.md",
    "Babysit": "playbooks/babysit.md",
    "Shipping": "playbooks/shipping.md",
    "Autonomous run": "playbooks/autonomous-run.md",
    "Orchestrate": "playbooks/orchestrate.md",
    "Autopilot-full": "playbooks/autopilot-full.md",
    "Autopilot-stack": "playbooks/autopilot-stack.md",
    "Session pickup": "playbooks/session-pickup.md",
    "Pause safely": "playbooks/pause-safely.md",
    "Multi-phase or multi-PR plan": "playbooks/multi-phase-plan.md",
    "Worktree and simulator cleanup": "playbooks/worktree-cleanup.md",
    "Opening a PR": "playbooks/opening-a-pr.md",
}
def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
def config(budget="default", mode=None, **assigned):
    loaded = {"budget": budget, "roles": assigned, "sources": {name: "test" for name in assigned}}
    if mode is not None:
        loaded["mode"] = roles.ModeDecision(mode, "roles.json", "test")
    return loaded
OPUS_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
def pointer_sentence(kind):
    rel = "../pstack-runtime/SKILL.md" if kind == "skill" else "../../pstack-runtime/SKILL.md"
    return (
        f"[The runtime's Modes section]({rel}#modes) sets the mode lines of every brief "
        f"this {kind} writes and how its spawns run in light mode."
    )
def unfenced_lines(text):
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            yield line
def heading_slug(heading):
    kept = "".join(char for char in heading.lower() if char.isalnum() or char in " -")
    return kept.replace(" ", "-")
GATE_CONTRACT = """\
Gate review contract. These rules override the persona above and the tasks below where they differ.
Do not edit files, commit, or push. Do not post on the PR. Return your report to the parent.
Launch no child task, thread, or subagent. Do not run the how, why, architect, or interrogate skill. Do not launch show-me-your-work's trail reviewer.
Read the diff the brief names and the nearby code yourself. You may run git log -L and git blame. Run the named verification commands yourself.
When a claim or finding needs investigation beyond those reads, return send-back. Name the file, the line, the claim, and the question the fix must answer.
Report each comment the persona would delete as a send-back finding with its path and line. Make no edit.
End with pass, send-back, or blocked, the full head SHA, the author, and the verifier.
"""
SEAT_RULE = """\
Seat rule. Copy the Mode value above into --brief-mode on every roles.py mode and roles.py show call you make, and pass no other mode flag. Never pass --session-mode. Mode source names where your launcher's decision came from. It does not make this thread a session.
"""
def gate_contract(runtime):
    start = runtime.index("2. **Brief.**")
    window = runtime[start:].split("\n3. **Verdict.**", 1)[0]
    lines = window.splitlines()
    open_at = next(i for i, line in enumerate(lines) if line.strip() == "```text")
    close_at = next(i for i, line in enumerate(lines) if i > open_at and line.strip() == "```")
    body = "\n".join(lines[open_at + 1:close_at]) + "\n"
    return textwrap.dedent(body)
def seat_rule(runtime):
    start = runtime.index("is not a code delegate")
    window = runtime[start:].split("\n- A read-only leaf", 1)[0]
    lines = window.splitlines()
    open_at = next(i for i, line in enumerate(lines) if line.strip() == "```text")
    close_at = next(i for i, line in enumerate(lines) if i > open_at and line.strip() == "```")
    body = "\n".join(lines[open_at + 1:close_at]) + "\n"
    return textwrap.dedent(body)
def brief_paragraph(runtime):
    start = runtime.index("2. **Brief.**")
    window = runtime[start:].split("\n3. **Verdict.**", 1)[0]
    prose = []
    for line in window.splitlines():
        if line.strip().startswith("```"):
            break
        if prose and not line.strip():
            break
        if line.strip():
            prose.append(line.strip())
    return " ".join(prose)
def light_behavior_rows(runtime):
    section = runtime.split("# Light mode reference", 1)[1].split("## Light behavior", 1)[0]
    rows = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        if not stripped.replace("|", "").replace(":", "").replace("-", "").strip():
            continue
        if stripped.strip("|").split("|", 1)[0].strip() == "Spawn":
            continue
        rows.append(stripped)
    return rows
def light_row(runtime, spawn):
    matches = [row for row in light_behavior_rows(runtime) if row.split("|", 2)[1].strip() == spawn]
    if len(matches) != 1:
        raise AssertionError(f"{spawn}: expected 1 light row, found {len(matches)}")
    return matches[0]
def fragment_name(branch):
    return branch.replace("%", "%25").replace("/", "%2F") + ".md"
def fragment_holds_bullets(lines):
    open_bullet = False
    for line in lines:
        if line.startswith("- ") and line[2:].strip():
            open_bullet = True
            continue
        if open_bullet and (line.startswith(" ") or line.startswith("\t")) and line.strip():
            continue
        return False
    return open_bullet
def changelog_findings(root):
    findings = []
    changelog = root / "CHANGELOG.md"
    if changelog.is_file():
        for line in changelog.read_text().splitlines():
            if line.strip() == "## Unreleased":
                findings.append(
                    "CHANGELOG.md has a ## Unreleased heading. Write the bullet under changes/."
                )
                break
    changes = root / "changes"
    if not changes.is_dir():
        return findings
    for path in sorted(p for p in changes.rglob("*") if p.is_file()):
        relative = path.relative_to(changes).as_posix()
        if "/" in relative:
            findings.append(f"changes/{relative} sits in a subdirectory. changes/ is flat.")
            continue
        if not fragment_holds_bullets(path.read_text().splitlines()):
            findings.append(f"changes/{relative} holds a line that is not a bullet.")
    return findings
def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "LC_ALL": "C",
        },
    ).stdout
def release_bullets(repo):
    log = _git(
        repo,
        "log",
        "--reverse",
        "--diff-filter=A",
        "--format=",
        "--name-only",
        "--",
        "changes/",
    )
    order = []
    for line in log.splitlines():
        if not line.startswith("changes/") or line.count("/") != 1 or not (repo / line).is_file():
            continue
        if line in order:
            order.remove(line)
        order.append(line)
    bullets = []
    for relative in order:
        bullets.extend((repo / relative).read_text().splitlines())
    return bullets
RUNTIME_DOCS = ("SKILL.md", "threads.md", "host.md", "verification.md", "roles.md", "failures.md", "light.md", "gate-review.md", "delegation.md", "modes.md", "isolation.md", "watching.md")
def runtime_docs():
    """The RUNTIME_DOCS members joined in order, read from the built skills tree."""
    return "\n".join((ROOT / "skills/pstack-runtime" / name).read_text() for name in RUNTIME_DOCS)
def runtime_doc_section(name, start, end="\n## "):
    """A section from one RUNTIME_DOCS member, read from the built skills tree."""
    text = (ROOT / "skills/pstack-runtime" / name).read_text()
    return text.split("\n" + start, 1)[1].split(end, 1)[0]
def runtime_section(start, end="\n## "):
    text = (ROOT / "t3/runtime.md").read_text()
    return text.split(start, 1)[1].split(end, 1)[0]
def runtime_docs_section(start, end="\n## "):
    return runtime_docs().split("\n" + start, 1)[1].split(end, 1)[0]
def runtime_bullet(section, prefix):
    return next(line for line in section.splitlines() if line.startswith(prefix))
def override_text(path):
    return (ROOT / "t3/overrides" / path).read_text()
def _context_catalog(provider_id, model_options):
    return {
        "providers": [{
            "providerInstanceId": provider_id,
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": model_options}],
        }],
    }
_EFFORT_OPTION = {
    "id": "effort",
    "type": "select",
    "options": [{"id": "low"}, {"id": "medium"}, {"id": "high"}, {"id": "xhigh"}, {"id": "max"}],
}
_WINDOW_OPTION = {
    "id": "contextWindow",
    "type": "select",
    "options": [{"id": "300k"}, {"id": "1m"}],
}
PREVIEW_TOOLS = (
    "preview_hover",
    "preview_drag",
    "preview_select",
    "preview_upload",
    "preview_dialog",
)
