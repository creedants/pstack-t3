import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import in_process

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "t3/scripts"))
import roles  # noqa: E402

GROK_SEAT = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}
OPUS_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
GROK_MEDIUM = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}}
OPUS_MEDIUM = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}
OPUS_HIGH = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}}
INHERIT_ENTRY = {"source": "default", "seats": "inherit", "note": roles.UNSET_ROLE_NOTE}
NO_CATALOG_INFO = (
    "info: light mode caps reasoning at medium, but show had no catalog, "
    "so no seat was capped. Rerun with --catalog and --parent.\n"
)
NO_PARENT_INFO = (
    "info: light mode caps reasoning at medium, but show had no --parent, "
    "so an inherit seat may keep the parent's reasoning. Rerun with --parent.\n"
)
LAUNCH_MODELS = {
    "feature, refactoring": ["grok-4.7"],
    "bug-fix": ["grok-4.7"],
    "perf-issue": ["grok-4.7"],
    "hillclimb": ["grok-4.7"],
    "judgment and prose": ["claude-opus-5-5"],
    "hardest tasks": ["claude-opus-5-5"],
    "how explorer": ["claude-haiku-5-5"],
    "how explainer": ["claude-opus-5-5"],
    "why investigators": ["claude-haiku-5-5"],
    "why synthesizer": ["claude-opus-5-5"],
    "reflect tooling": ["grok-4.7"],
    "reflect judgment, divergent, synthesizer": ["claude-opus-5-5"],
    "swarm workers": ["grok-4.7"],
    "skill tests": ["claude-haiku-5-5"],
    "arena runners": ["claude-opus-5-5", "grok-4.7"],
    "arena cross-judge pool": ["claude-opus-5-5", "grok-4.7"],
    "architect runners": ["claude-opus-5-5", "grok-4.7"],
    "interrogate reviewers": ["claude-opus-5-5", "grok-4.7"],
    "verifiers": ["claude-opus-5-5", "gpt-6.1-sol", "grok-4.7"],
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


def setUpModule():
    fence_git_environment()


def show(*role_names):
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "XDG_CONFIG_HOME": directory}
        argv = [
            "show",
            "--cwd",
            directory,
            "--catalog",
            str(ROOT / "tests/fixtures/catalog.json"),
            "--parent",
            "claudeAgent/claude-opus-5-5",
        ]
        for name in role_names:
            argv.extend(["--role", name])
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=tuple(argv),
            env=env,
        ))


class RolesCliTest(unittest.TestCase):
    def test_one_role_resolves_that_role(self):
        completed = show("bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)

    def test_two_roles_resolve_both(self):
        completed = show("bug-fix", "judgment and prose")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix", "judgment and prose"])
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)
        self.assertEqual(payload["roles"]["judgment and prose"], INHERIT_ENTRY)

    def test_repeated_identical_role_resolves_once(self):
        completed = show("bug-fix", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)


D25_BRIEF = """\
Act as the implementation sub-agent for this task.

Implement the pstack-t3 change described in /tmp/d25-architect/IMPL.md. Read that file, /tmp/d25-architect/SYNTHESIS.md, and /tmp/d25-architect/c3-sol/SKETCH.md before you edit. The synthesis wins where the sketch differs.

Work only in /tmp/pstack-work/pstack-t3-d25 on branch pstack-t3/d25. Do not create a worktree. Do not merge, rebase, push, or open a pull request. Do not edit README.md or CHANGELOG.md. Do not hand-edit skills/ or vendor/pstack. Do not spawn agents.

Two green commits, in order. First the roles defaults and the setup watch gate, with tests. Then scripts/sync_upstream.py and the override ports. Run tests and builds only through python3 /tmp/pstack-work/skills/landing/scripts/land.py slot -- <command>.

Commit messages have no Co-Authored-By trailer, no Generated-with footer, and no mention of an agent or a tool.

When both commits are green, write /tmp/d25-impl-notes.md as IMPL.md describes, including the changelog sentences you did not add."""

PLAYBOOK_NAMES = ", ".join((
    "authoring-a-skill",
    "autonomous-run",
    "autopilot-full",
    "autopilot-stack",
    "babysit",
    "bug-fix",
    "eval",
    "feature",
    "hillclimb",
    "investigation",
    "multi-phase-plan",
    "opening-a-pr",
    "orchestrate",
    "pause-safely",
    "perf-issue",
    "prototype",
    "refactoring",
    "runtime-forensics",
    "session-pickup",
    "shipping",
    "trace-forensics",
    "visual-parity",
    "worktree-cleanup",
))
PERSONA_PATH = ROOT / "t3" / "agents" / "poteto-agent.md"
MISSING_PERSONA = (
    f"missing persona: open the brief with the body of {PERSONA_PATH}, without its frontmatter\n"
)
MISSING_PLAYBOOK = (
    "missing playbook: add one line 'Playbook: playbooks/<name>.md', "
    f"where <name> is one of: {PLAYBOOK_NAMES}\n"
)
MISSING_MODE = (
    "missing Mode: paste the lines 'roles.py mode --playbook <name> --attempt <kind>' prints, "
    "which include one line 'Mode: full' or 'Mode: light'\n"
)


def agent_body():
    text = PERSONA_PATH.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    fences = [index for index, line in enumerate(lines) if line.strip() == "---"]
    return "".join(lines[fences[1] + 1:]).strip()


def run_check_brief(path):
    return in_process.invoke(in_process.Invocation(
        program=ROOT / "t3/scripts/roles.py",
        argv=("check-brief", str(path)),
    ))


def mode_output(*args):
    with tempfile.TemporaryDirectory() as directory:
        completed = in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("mode", "--cwd", directory, *args),
            env={**os.environ, "XDG_CONFIG_HOME": directory},
        ))
    assert completed.returncode == 0 and completed.stderr == "", completed.stderr
    return completed.stdout


def check_text(text):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "brief.md"
        path.write_text(text, encoding="utf-8")
        return run_check_brief(path)


class CheckBriefCliTest(unittest.TestCase):
    def assert_check(self, completed, code, stdout, stderr=""):
        self.assertEqual(completed.returncode, code, completed.stderr)
        self.assertEqual(completed.stdout, stdout)
        self.assertEqual(completed.stderr, stderr)

    def test_d25_brief(self):
        completed = check_text(D25_BRIEF)
        self.assert_check(completed, 1, MISSING_PERSONA + MISSING_PLAYBOOK + MISSING_MODE)

    def test_passing_brief(self):
        brief = (
            f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n\n"
            "Implement the change described in the plan.\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_persona_truncated_by_one_sentence(self):
        body = agent_body()
        split = body.rfind(". ")
        self.assertGreater(split, 0)
        sentence = body[split + 2:]
        self.assertTrue(sentence.endswith("."))
        self.assertLess(len(sentence), len(body))
        brief = f"{body[:-len(sentence)].rstrip()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_persona_after_other_text(self):
        brief = f"Read this note first.\n\n{agent_body()}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_frontmatter_paste(self):
        brief = PERSONA_PATH.read_text(encoding="utf-8") + "\nPlaybook: playbooks/refactoring.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_missing_playbook_line(self):
        brief = f"{agent_body()}\n\nMode: full\n\nImplement the change described in the plan.\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PLAYBOOK)

    def test_unknown_playbook_name(self):
        brief = f"{agent_body()}\n\nPlaybook: playbooks/not-a-playbook.md\nMode: full\n"
        completed = check_text(brief)
        self.assert_check(
            completed,
            1,
            f"unknown playbook 'playbooks/not-a-playbook.md': expected one of: {PLAYBOOK_NAMES}\n",
        )

    def test_two_playbook_lines(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: full\n"
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, "more than one Playbook line: keep one\n")

    def test_missing_brief_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.md"
            completed = run_check_brief(path)
        self.assert_check(completed, 2, "", f"error: {path}: brief not found\n")


class CheckBriefModeCliTest(unittest.TestCase):
    def assert_check(self, completed, code, stdout, stderr=""):
        self.assertEqual(completed.returncode, code, completed.stderr)
        self.assertEqual(completed.stdout, stdout)
        self.assertEqual(completed.stderr, stderr)

    def test_light_feature_first_passes(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
        )
        self.assert_check(check_text(brief), 0, "ok playbooks/feature.md\n")

    def test_two_mode_lines(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Mode: light\n"
            "Attempt: retry\n"
        )
        self.assert_check(check_text(brief), 1, "more than one Mode line: keep one\n")

    def test_mode_with_trailing_words(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light (from restaurant.json)\n"
            "Attempt: retry\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "bad Mode value 'light (from restaurant.json)': expected full or light\n",
        )

    def test_waiver_under_full(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: full\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "unexpected Waived by mode line: Mode: full waives nothing, remove it\n",
        )

    def test_wrong_waivers_feature_first(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "wrong Waived by mode line: expected 'Waived by mode: Arena, Interrogate, Comment Sicko'\n",
        )

    def test_light_without_attempt(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "missing Attempt: Mode: light needs one line 'Attempt: <kind>', "
            "where <kind> is one of: first, fix, bounce\n",
        )

    def test_code_delegate_without_mode_fails(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/refactoring.md\n"
            "Attempt: nonsense\n"
            "Waived by mode: How\n"
            "Waived by mode: Arena, Interrogate, Comment Sicko\n"
            "\nImplement the change described in the plan.\n"
        )
        self.assert_check(check_text(brief), 1, MISSING_MODE)

    def test_persona_and_playbook_only_fails(self):
        brief = f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\n"
        self.assert_check(check_text(brief), 1, MISSING_MODE)

    def test_pasted_light_session_output_passes(self):
        lines = mode_output("--playbook", "feature", "--attempt", "first", "--session-mode", "light")
        self.assert_check(check_text(f"{agent_body()}\n\n{lines}"), 0, "ok playbooks/feature.md\n")

    def test_pasted_default_output_passes(self):
        lines = mode_output("--playbook", "refactoring", "--attempt", "first")
        self.assert_check(check_text(f"{agent_body()}\n\n{lines}"), 0, "ok playbooks/refactoring.md\n")

    def test_own_playbook_line_beside_pasted_output_fails(self):
        lines = mode_output("--playbook", "feature", "--attempt", "first", "--session-mode", "light")
        brief = f"{agent_body()}\n\nPlaybook: playbooks/feature.md\n{lines}"
        self.assert_check(check_text(brief), 1, "more than one Playbook line: keep one\n")

    def test_bad_attempt_no_traceback(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/feature.md\n"
            "Mode: light\n"
            "Attempt: retry\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            "bad Attempt value 'retry': expected one of: first, fix, bounce\n",
        )

    def test_unknown_playbook_skips_waivers(self):
        brief = (
            f"{agent_body()}\n\n"
            "Playbook: playbooks/nope.md\n"
            "Mode: light\n"
            "Attempt: first\n"
            "Waived by mode: How\n"
        )
        self.assert_check(
            check_text(brief),
            1,
            f"unknown playbook 'playbooks/nope.md': expected one of: {PLAYBOOK_NAMES}\n",
        )

    def test_mode_output_round_trips(self):
        rows = (
            ("feature", "first"),
            ("feature", "fix"),
            ("feature", "bounce"),
            ("bug-fix", "first"),
            ("bug-fix", "fix"),
            ("bug-fix", "bounce"),
            ("refactoring", "first"),
            ("refactoring", "fix"),
            ("refactoring", "bounce"),
            ("perf-issue", "first"),
            ("perf-issue", "fix"),
            ("perf-issue", "bounce"),
            ("hillclimb", "first"),
            ("hillclimb", "fix"),
            ("hillclimb", "bounce"),
            ("authoring-a-skill", "first"),
            ("authoring-a-skill", "fix"),
            ("authoring-a-skill", "bounce"),
            ("investigation", "first"),
        )
        body = agent_body()
        for stem, attempt in rows:
            with self.subTest(stem=stem, attempt=attempt):
                with tempfile.TemporaryDirectory() as directory:
                    completed = in_process.invoke(in_process.Invocation(
                        program=ROOT / "t3/scripts/roles.py",
                        argv=(
                            "mode",
                            "--brief-mode",
                            "light",
                            "--playbook",
                            stem,
                            "--attempt",
                            attempt,
                            "--cwd",
                            directory,
                        ),
                        env={**os.environ, "XDG_CONFIG_HOME": directory},
                    ))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                checked = check_text(f"{body}\n\n{completed.stdout}")
                self.assert_check(checked, 0, f"ok playbooks/{stem}.md\n")

    def test_problem_order_persona_playbook_mode(self):
        brief = "Playbook: playbooks/not-a-playbook.md\nMode: light (from restaurant.json)\n"
        self.assert_check(
            check_text(brief),
            1,
            MISSING_PERSONA
            + f"unknown playbook 'playbooks/not-a-playbook.md': expected one of: {PLAYBOOK_NAMES}\n"
            + "bad Mode value 'light (from restaurant.json)': expected full or light\n",
        )


CATALOG = ROOT / "tests/fixtures/catalog.json"
FOUR_PATHS = [
    "**/migrations/**",
    "t3/added/landing/scripts/land.py",
    "t3/added/brigade/scripts/brigade.py",
    "scripts/install.py",
]
ROOT_ALIASES = (".", "/", "a/..", " ", "//")
SET_EXAMPLES = [
    "judgment and prose=claudeAgent/claude-opus-5-5?effort=xhigh",
    "swarm workers=grok/grok-4.7?reasoningEffort=xhigh",
    "interrogate reviewers=claudeAgent/claude-opus-5-5?effort=xhigh;grok/grok-4.7?reasoningEffort=xhigh",
]
SETUP_DESCRIPTION = (
    'Configure which T3 providers and models pstack uses per role, at what reasoning budget, '
    'and whether mode is full or light. Reads the live T3 catalog and writes a roles file that '
    'every pstack skill reads. Use for /setup-pstack, "configure pstack models", "pstack budget", '
    '"pstack mode", or changing pstack\'s model choices.'
)


class Repo:
    def __init__(self, directory):
        self.directory = Path(directory)
        (self.directory / ".git").mkdir()
        self.user = self.directory / "pstack-t3" / "roles.json"
        self.project = self.directory.resolve() / ".pstack" / "t3-roles.json"

    def put(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def run(self, *args):
        env = {**os.environ, "XDG_CONFIG_HOME": str(self.directory)}
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(*args, "--cwd", str(self.directory)),
            env=env,
        ))

    def show_bug_fix(self):
        return self.run(
            "show",
            "--catalog",
            str(CATALOG),
            "--parent",
            "claudeAgent/claude-opus-5-5",
            "--role",
            "bug-fix",
        )

    def write(self, *args):
        return self.run("write", "--catalog", str(CATALOG), *args)


def bug_fix_show(mode, mode_source, escalate, budget="default"):
    return {
        "budget": budget,
        "mode": mode,
        "modeSource": mode_source,
        "escalate": escalate,
        "catalog": True,
        "roles": {"bug-fix": INHERIT_ENTRY},
    }


class ModeCliTest(unittest.TestCase):
    def open_repo(self):
        return tempfile.TemporaryDirectory()

    def test_project_mode_wins(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            repo.put(repo.project, {"mode": "full", "escalate": FOUR_PATHS})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload), ["budget", "mode", "modeSource", "escalate", "catalog", "roles"])
        self.assertEqual(payload, bug_fix_show("full", str(repo.project), FOUR_PATHS))

    def test_missing_mode_defaults_to_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload, bug_fix_show("light", "default", None))
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)

    def test_project_without_mode_uses_the_user_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            bug_fix_show("light", str(repo.user), None),
        )

    def test_user_escalate_list_is_ignored(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "escalate": ["**/migrations/**"]})
            repo.put(repo.project, {})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["escalate"], None)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], str(repo.user))

    def test_unknown_user_mode_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "turbo"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.user}: mode 'turbo' is not one of full, light\n",
        )

    def test_unknown_project_mode_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "turbo"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: mode 'turbo' is not one of full, light\n",
        )

    def test_escalate_string_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": "**/migrations/**"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate must be a list of strings\n",
        )

    def test_escalate_empty_string_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": ["ok", ""]})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate must be a list of strings\n",
        )

    def test_show_preserves_root_normalized_escalate_patterns(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(repo.project, {"mode": "full", "escalate": [pattern]})
                    completed = repo.show_bug_fix()
                expected = {
                    "budget": "default",
                    "mode": "full",
                    "modeSource": str(repo.project),
                    "escalate": [pattern],
                    "catalog": True,
                    "roles": {
                        "bug-fix": {
                            "source": "default",
                            "seats": "inherit",
                            "note": roles.UNSET_ROLE_NOTE,
                        },
                    },
                }
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, json.dumps(expected, indent=2) + "\n")
                self.assertEqual(completed.stderr, "")

    def test_project_keep_preserves_root_normalized_escalate_patterns(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(
                        repo.project,
                        {"version": 1, "roles": {}, "mode": "light", "escalate": [pattern]},
                    )
                    first = repo.write("--project", "--keep")
                    self.assertEqual(first.returncode, 0)
                    self.assertEqual(first.stdout, f"wrote {repo.project}\n")
                    self.assertEqual(first.stderr, "")
                    self.assertEqual(
                        json.loads(repo.project.read_text()),
                        {"version": 1, "roles": {}, "mode": "light", "escalate": [pattern]},
                    )
                    written = repo.project.read_bytes()
                    second = repo.write("--project", "--keep")
                    self.assertEqual(second.returncode, 0)
                    self.assertEqual(second.stdout, f"wrote {repo.project}\n")
                    self.assertEqual(second.stderr, "")
                    self.assertEqual(repo.project.read_bytes(), written)

    def test_project_rewrite_preserves_root_normalized_escalate_patterns(self):
        for pattern in ROOT_ALIASES:
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(
                        repo.project,
                        {"version": 1, "roles": {}, "mode": "light", "escalate": [pattern]},
                    )
                    completed = repo.write("--project", "--replace")
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, f"wrote {repo.project}\n")
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(
                        json.loads(repo.project.read_text()),
                        {"version": 1, "roles": {}, "escalate": [pattern]},
                    )

    def test_normalized_traversal_stays_rejected(self):
        for pattern in ("a/../../x", "..", " ../x "):
            with self.subTest(pattern=pattern):
                with self.open_repo() as directory:
                    repo = Repo(directory)
                    repo.put(repo.project, {"mode": "light", "escalate": [pattern]})
                    before = repo.project.read_bytes()
                    show = repo.show_bug_fix()
                    write = repo.write("--project", "--keep")
                    mode = repo.run("mode")
                    after = repo.project.read_bytes()
                message = f"error: {repo.project}: escalate pattern {pattern!r} leaves the repository\n"
                for completed in (show, write, mode):
                    self.assertEqual(completed.returncode, 1)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, message)
                self.assertEqual(after, before)

    def test_mode_flags_print_their_help(self):
        mode_flags = (
            ("--brief-mode", "brief mode; overrides session, coordinator, project, and user modes"),
            ("--session-mode", "session mode; used after brief and before coordinator, project, and user modes"),
            ("--coordinator-mode", "coordinator mode; used after brief and session, before project and user modes"),
        )
        mode_only = (
            ("--send-backs", "SEND_BACKS", "send-back count; 2 or more forces full mode"),
            (
                "--escalated",
                "ESCALATED",
                "recorded one-line escalation reason; forces full mode and wins over paths and send-backs",
            ),
        )
        for command in ("show", "mode"):
            with self.subTest(command=command):
                completed = in_process.finished(in_process.Reason.HELP, in_process.Invocation(
                    program=ROOT / "t3/scripts/roles.py",
                    argv=(command, "--help"),
                    env={**os.environ, "COLUMNS": "200"},
                ))
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, "")
                for flag, text in mode_flags:
                    block = f"  {flag} {{full,light}}\n" + (" " * 24) + text + "\n"
                    self.assertIn(block, completed.stdout)
                if command == "mode":
                    for flag, metavar, text in mode_only:
                        block = f"  {flag} {metavar}\n" + (" " * 24) + text + "\n"
                        self.assertIn(block, completed.stdout)

    def test_escalate_non_string_exits_1(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": [1]})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.project}: escalate must be a list of strings\n",
        )

    def test_bad_budget_still_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"budget": "huge"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: {repo.user}: budget 'huge' is not one of default, small, medium, large, unlimited\n",
        )

    def test_bad_mode_flag_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--mode", "bogus")
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(repo.user.exists())
            self.assertFalse(repo.project.exists())

    def test_project_rewrite_drops_mode_and_keeps_escalate(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            first = repo.write("--project", "--mode", "light", "--escalate", "**/migrations/**")
            self.assertEqual(first.returncode, 0, first.stderr)
            second = repo.write("--project", "--replace", "--set", "bug-fix=inherit")
            self.assertEqual(second.returncode, 0, second.stderr)
            document = json.loads(repo.project.read_text())
            self.assertEqual(list(document), ["version", "roles", "escalate"])
            self.assertEqual(document["roles"]["bug-fix"], ["inherit"])
            self.assertEqual(document["escalate"], ["**/migrations/**"])
            self.assertNotIn("mode", document)
            self.assertNotIn("budget", document)
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], str(repo.user))
        self.assertEqual(payload["escalate"], ["**/migrations/**"])

    def test_replace_without_mode_resets_to_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            first = repo.write("--mode", "full")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(repo.user.read_text())["mode"], "full")
            second = repo.write("--replace", "--budget", "small")
            self.assertEqual(second.returncode, 0, second.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(list(document), ["version", "roles", "budget", "mode"])
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "small", "mode": "light"})

    def test_keep_preserves_user_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document["mode"], "light")
        self.assertEqual(document["budget"], "small")

    def test_project_escalate_write_is_idempotent(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            args = ("--project", "--escalate", "**/migrations/**", "--escalate", "scripts/install.py")
            first = repo.write(*args)
            self.assertEqual(first.returncode, 0, first.stderr)
            written = repo.project.read_text()
            second = repo.write(*args)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(repo.project.read_text(), written)
            self.assertEqual(
                json.loads(written)["escalate"],
                ["**/migrations/**", "scripts/install.py"],
            )
            third = repo.write("--project", "--mode", "light")
            self.assertEqual(third.returncode, 0, third.stderr)
            document = json.loads(repo.project.read_text())
        self.assertEqual(document["escalate"], ["**/migrations/**", "scripts/install.py"])
        self.assertEqual(document["mode"], "light")
        self.assertEqual(list(document), ["version", "roles", "mode", "escalate"])

    def test_clear_escalate_is_idempotent(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"version": 1, "roles": {}, "mode": "light", "escalate": ["**/migrations/**"]})
            first = repo.write("--project", "--clear-escalate", "--keep")
            self.assertEqual(first.returncode, 0, first.stderr)
            document = json.loads(repo.project.read_text())
            self.assertNotIn("escalate", document)
            self.assertEqual(document["mode"], "light")
            written = repo.project.read_text()
            second = repo.write("--project", "--clear-escalate", "--keep")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(repo.project.read_text(), written)

    def test_escalate_without_project_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--escalate", "**/migrations/**")
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(repo.user.exists())
            self.assertFalse(repo.project.exists())

    def test_clear_escalate_without_project_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--clear-escalate")
            self.assertEqual(completed.returncode, 2)
            self.assertFalse(repo.user.exists())
            self.assertFalse(repo.project.exists())

    def test_escalate_and_clear_together_exit_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"version": 1, "roles": {"bug-fix": ["inherit"]}})
            before = repo.project.read_text()
            completed = repo.write("--project", "--escalate", "**/migrations/**", "--clear-escalate")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(repo.project.read_text(), before)
            self.assertFalse(repo.user.exists())

    def test_keep_on_invalid_json_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("{not json", encoding="utf-8")
            before = repo.user.read_text()
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(repo.user.read_text(), before)

    def test_replace_rewrites_a_non_object(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("[]\n", encoding="utf-8")
            completed = repo.write("--replace", "--mode", "light")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "default", "mode": "light"})

    def test_a_roles_value_that_is_not_an_object_is_refused_even_with_replace(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text('{"roles": []}\n', encoding="utf-8")
            before = repo.user.read_bytes()
            refused = repo.write("--budget", "small")
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertIn(f"error: {repo.user}: roles must be an object", refused.stderr)
            self.assertEqual(repo.user.read_bytes(), before)
            replaced = repo.write("--replace", "--budget", "small")
            self.assertEqual(replaced.returncode, 2, replaced.stderr)
            self.assertEqual(replaced.stdout, "")
            self.assertIn(f"error: {repo.user}: roles must be an object", replaced.stderr)
            self.assertEqual(repo.user.read_bytes(), before)
            self.assertFalse(repo.user.with_name("roles.json.bak").exists())

    def test_keep_on_a_non_object_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("[]\n", encoding="utf-8")
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(repo.user.read_text(), "[]\n")

    def test_write_keeps_stored_roles_budget_mode_and_an_unknown_key(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": ["inherit"], "hillclimb": ["inherit"]},
                "budget": "large",
                "mode": "light",
                "exclude": [{"model": "grok-4.7-build-fast"}],
            })
            completed = repo.write("--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document["roles"]["hillclimb"], ["inherit"])
        self.assertEqual(document["roles"]["bug-fix"], [{"providerInstanceId": "grok", "model": "grok-4.7"}])
        self.assertEqual(document["budget"], "large")
        self.assertEqual(document["mode"], "light")
        self.assertEqual(document["exclude"], [{"model": "grok-4.7-build-fast"}])

    def test_replace_rewrites_the_file_and_writes_a_backup(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": ["inherit"]},
                "budget": "large",
                "mode": "light",
            })
            before = repo.user.read_bytes()
            completed = repo.write("--replace", "--budget", "small")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
            self.assertEqual(repo.user.with_name("roles.json.bak").read_bytes(), before)
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "small", "mode": "light"})

    def test_write_refuses_invalid_json_and_leaves_the_file(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("{not json", encoding="utf-8")
            before = repo.user.read_bytes()
            plain = repo.write("--budget", "small")
            replaced = repo.write("--replace", "--budget", "small")
            for completed in (plain, replaced):
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn(f"error: {repo.user}: invalid JSON:", completed.stderr)
                self.assertEqual(repo.user.read_bytes(), before)
                self.assertFalse(repo.user.with_name("roles.json.bak").exists())

    def test_write_refuses_a_non_utf8_target_and_leaves_the_file(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_bytes(b"\xff\xfe")
            before = repo.user.read_bytes()
            completed = repo.write("--budget", "small")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertIn(f"error: {repo.user}: invalid JSON:", completed.stderr)
            self.assertEqual(repo.user.read_bytes(), before)
            self.assertFalse(repo.user.with_name("roles.json.bak").exists())

    def test_keep_is_a_no_op(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            seed = {"version": 1, "roles": {"hillclimb": ["inherit"]}, "budget": "large"}
            repo.put(repo.user, seed)
            without = repo.write("--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(without.returncode, 0, without.stderr)
            plain = repo.user.read_bytes()
            repo.put(repo.user, seed)
            kept = repo.write("--keep", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(kept.returncode, 0, kept.stderr)
            with_keep = repo.user.read_bytes()
        self.assertEqual(with_keep, plain)

    def test_user_keep_preserves_a_stored_escalate_list(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": ["inherit"]},
                "budget": "large",
                "mode": "light",
                "escalate": ["**/migrations/**"],
            })
            completed = repo.write("--keep")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document["escalate"], ["**/migrations/**"])
        self.assertEqual(document["mode"], "light")
        self.assertEqual(document["budget"], "large")
        self.assertEqual(document["roles"], {"bug-fix": ["inherit"]})

    def test_project_keep_copies_stored_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {
                "version": 1,
                "roles": {},
                "mode": "light",
                "escalate": ["**/migrations/**"],
            })
            completed = repo.write("--project", "--keep", "--set", "bug-fix=inherit")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.project.read_text())
        self.assertEqual(document["mode"], "light")
        self.assertEqual(document["escalate"], ["**/migrations/**"])
        self.assertEqual(document["roles"]["bug-fix"], ["inherit"])
        self.assertNotIn("budget", document)

    def test_mode_full_repairs_stored_turbo(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "turbo", "roles": {}})
            completed = repo.write("--mode", "full")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(repo.user.read_text())["mode"], "full")

    def test_keep_rejects_stored_turbo(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "turbo"})
            before = repo.user.read_text()
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                completed.stderr,
                f"error: {repo.user}: mode 'turbo' is not one of full, light\n",
            )
            self.assertEqual(repo.user.read_text(), before)

    def test_project_write_rejects_a_stored_bad_escalate_list(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": "nope", "roles": {}})
            before = repo.project.read_text()
            completed = repo.write("--project", "--set", "bug-fix=inherit")
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                completed.stderr,
                f"error: {repo.project}: escalate must be a list of strings\n",
            )
            self.assertEqual(repo.project.read_text(), before)

    def test_escalate_flag_replaces_a_bad_stored_list(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"escalate": "nope"})
            completed = repo.write("--project", "--escalate", "**/migrations/**")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.project.read_text())
        self.assertEqual(document["escalate"], ["**/migrations/**"])
        self.assertNotIn("mode", document)

    def test_escalate_keeps_duplicates(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.write("--project", "--escalate", "**/migrations/**", "--escalate", "**/migrations/**")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(repo.project.read_text())["escalate"],
                ["**/migrations/**", "**/migrations/**"],
            )

    def test_setup_asks_for_mode_before_proposing_roles(self):
        text = (ROOT / "t3/setup.md").read_text(encoding="utf-8")
        propose = text.index("**(b) Propose roles.**")
        head = text[:propose]
        self.assertIn("**(c) Confirm.**", text)
        self.assertIn("- `light` (recommended)", head)
        self.assertIn("- `full`", head)
        self.assertLess(
            head.index("- `light` (recommended)"),
            head.index("- `full`"),
        )
        for path in (
            "**/migrations/**",
            "t3/added/landing/scripts/land.py",
            "t3/added/brigade/scripts/brigade.py",
            "scripts/install.py",
        ):
            self.assertIn(path, head)
        self.assertNotIn("AskQuestion", text)
        self.assertEqual(re.findall(r'--set "([^"]+)"', text), SET_EXAMPLES)
        self.assertIn("--budget large --mode full", text)
        self.assertIn(f"description: {SETUP_DESCRIPTION}", text)
        self.assertIn("A user write with no stored mode and no `--mode` stores `light`.", text)
        self.assertIn("A project write with no stored mode and no `--mode` omits the key.", text)
        self.assertIn("Omitting `--escalate` leaves a stored project list in place.", text)
        self.assertIn("Tell the user which file was written, the budget, the mode,", text)

    def test_setup_says_write_runs_the_validate_checks_on_the_roles_it_stores(self):
        text = (ROOT / "t3/setup.md").read_text(encoding="utf-8")
        self.assertIn(
            "- With no excluded seat, it runs `validate`'s checks on the roles it is about to store. "
            "When they list a problem, it prints each one and writes nothing unless `--force` is passed. "
            "Fix each problem and rerun. Do not pass `--force` unless the user asks. "
            "Write refuses a seat on a metered OpenCode model unless that same file's allow list names the full model id or `--force` is passed. "
            "Pass the allow flag on write with that id and rerun.\n",
            text,
        )
        self.assertNotIn("a seat that does not match the catalog", text)

    def test_coordinator_mode_light_keeps_a_configured_xhigh_verifier_at_xhigh(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {"verifiers": [OPUS_SEAT, GROK_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--coordinator-mode", "light",
                "--role", "verifiers",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], "restaurant.json")
        self.assertEqual(payload["roles"]["verifiers"]["seats"], [OPUS_SEAT, GROK_SEAT])
        self.assertEqual(completed.stderr, "")

    def show_role(self, repo, *args):
        return repo.run("show", "--catalog", str(CATALOG), "--parent", "claudeAgent/claude-opus-5-5", *args)

    def test_brief_mode_light_keeps_a_configured_high_verifier_at_high(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"verifiers": [OPUS_HIGH]}})
            completed = self.show_role(repo, "--brief-mode", "light", "--role", "verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["roles"]["verifiers"]["seats"], [OPUS_HIGH])
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(completed.stderr, "")

    def test_light_with_small_budget_caps_a_high_verifier_at_medium_as_full_does(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"budget": "small", "roles": {"verifiers": [OPUS_HIGH]}})
            light = self.show_role(repo, "--brief-mode", "light", "--role", "verifiers")
            full = self.show_role(repo, "--brief-mode", "full", "--role", "verifiers")
        for completed in (light, full):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["roles"]["verifiers"]["seats"], [OPUS_MEDIUM])

    def test_unset_verifiers_is_inherit_without_a_panel_under_light_and_full(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            light = self.show_role(repo, "--brief-mode", "light", "--role", "verifiers")
            full = self.show_role(repo, "--brief-mode", "full", "--role", "verifiers")
        self.assertEqual(light.returncode, 0, light.stderr)
        self.assertEqual(full.returncode, 0, full.stderr)
        entry = json.loads(light.stdout)["roles"]["verifiers"]
        self.assertEqual(entry, json.loads(full.stdout)["roles"]["verifiers"])
        self.assertEqual(entry, INHERIT_ENTRY)
        self.assertEqual(light.stderr, "")

    def test_light_keeps_a_configured_review_backups_seat_at_xhigh(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"review backups": [OPUS_SEAT, GROK_SEAT]}})
            completed = self.show_role(repo, "--brief-mode", "light", "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["review backups"]["seats"], [OPUS_SEAT, GROK_SEAT])

    def test_brief_mode_light_keeps_a_configured_high_review_backups_seat_at_high(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"review backups": [OPUS_HIGH, GROK_SEAT]}})
            completed = self.show_role(repo, "--brief-mode", "light", "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["roles"]["review backups"]["seats"], [OPUS_HIGH, GROK_SEAT])
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(completed.stderr, "")

    def test_light_with_small_budget_caps_a_high_review_backups_seat_at_medium_as_full_does(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"budget": "small", "roles": {"review backups": [OPUS_HIGH, GROK_SEAT]}})
            light = self.show_role(repo, "--brief-mode", "light", "--role", "review backups")
            full = self.show_role(repo, "--brief-mode", "full", "--role", "review backups")
        for completed in (light, full):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                json.loads(completed.stdout)["roles"]["review backups"]["seats"], [OPUS_MEDIUM, GROK_MEDIUM]
            )

    def test_light_show_of_every_role_caps_bug_fix_and_keeps_a_high_verifier(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [OPUS_SEAT], "verifiers": [OPUS_HIGH]}})
            completed = self.show_role(repo, "--brief-mode", "light")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        shown = json.loads(completed.stdout)["roles"]
        self.assertEqual(shown["bug-fix"]["seats"], [OPUS_MEDIUM])
        self.assertEqual(shown["verifiers"]["seats"], [OPUS_HIGH])

    def test_light_verifiers_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_light_verifiers_without_catalog_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_light_review_backups_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_light_review_backups_without_catalog_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "review backups")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_light_verifiers_and_bug_fix_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "verifiers", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)

    def test_brief_mode_full_keeps_a_configured_verifier(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "light", "roles": {"verifiers": [OPUS_SEAT, GROK_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--brief-mode", "full",
                "--role", "verifiers",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "full")
        self.assertEqual(payload["modeSource"], "brief")
        self.assertEqual(payload["roles"]["verifiers"]["seats"], [OPUS_SEAT, GROK_SEAT])

    def test_session_mode_light_over_project_full(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {"bug-fix": [OPUS_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--session-mode", "light",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "light")
        self.assertEqual(payload["modeSource"], "session")
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [OPUS_MEDIUM])

    def test_session_mode_full_over_project_light_lifts_the_cap(self):
        role = "bug-fix"
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {role: [OPUS_SEAT]}})
            full = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", role,
            )
            repo.put(repo.project, {"mode": "light", "roles": {role: [OPUS_SEAT]}})
            lifted = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--session-mode", "full",
                "--role", role,
            )
            capped = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", role,
            )
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertEqual(lifted.returncode, 0, lifted.stderr)
        self.assertEqual(capped.returncode, 0, capped.stderr)
        full_seats = json.loads(full.stdout)["roles"][role]["seats"]
        lifted_payload = json.loads(lifted.stdout)
        capped_seats = json.loads(capped.stdout)["roles"][role]["seats"]
        self.assertEqual((lifted_payload["mode"], lifted_payload["modeSource"]), ("full", "session"))
        self.assertEqual(lifted_payload["roles"][role]["seats"], [OPUS_SEAT])
        self.assertEqual(lifted_payload["roles"][role]["seats"], full_seats)
        self.assertEqual(capped_seats, [OPUS_MEDIUM])
        self.assertNotEqual(capped_seats, lifted_payload["roles"][role]["seats"])

    def test_brief_mode_full_over_session_mode_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full", "roles": {"bug-fix": [OPUS_SEAT]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--brief-mode", "full",
                "--session-mode", "light",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["mode"], "full")
        self.assertEqual(payload["modeSource"], "brief")
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [OPUS_SEAT])

    def test_show_keeps_its_keys_under_mode_flags(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--session-mode", "light",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload), ["budget", "mode", "modeSource", "escalate", "catalog", "roles"])
        self.assertEqual(payload["budget"], "default")

    def test_light_inherit_becomes_explicit_medium(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "roles": {"judgment and prose": ["inherit"]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "judgment and prose",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["judgment and prose"]
        self.assertEqual(entry["seats"], [OPUS_MEDIUM])
        self.assertEqual(
            entry["info"],
            ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
        )

    def test_light_with_large_budget_keeps_xhigh(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "budget": "large"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), bug_fix_show("light", str(repo.user), None, budget="large"))

    def test_full_with_small_budget_keeps_medium(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "full", "budget": "small", "roles": {"bug-fix": [OPUS_SEAT]}})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [OPUS_MEDIUM])
        self.assertEqual(payload["mode"], "full")

    def test_light_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], INHERIT_ENTRY)

    def test_light_without_catalog_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_CATALOG_INFO)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], INHERIT_ENTRY)

    def test_default_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)

    def test_light_with_large_budget_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "budget": "large"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

    def test_worktree_reads_the_brief_mode(self):
        with self.open_repo() as directory:
            root = Path(directory)
            main = root / "main"
            (main / ".git").mkdir(parents=True)
            project = main / ".pstack" / "t3-roles.json"
            project.parent.mkdir(parents=True)
            project.write_text(json.dumps({"mode": "full"}) + "\n", encoding="utf-8")
            worktree = main / "wt"
            worktree.mkdir()
            (worktree / ".git").write_text("gitdir: ../.git/worktrees/wt\n", encoding="utf-8")
            env = {**os.environ, "XDG_CONFIG_HOME": str(root)}

            def invoke(*extra):
                return in_process.invoke(in_process.Invocation(
                    program=ROOT / "t3/scripts/roles.py",
                    argv=(
                        "show",
                        "--cwd", str(worktree),
                        "--catalog", str(CATALOG),
                        "--parent", "claudeAgent/claude-opus-5-5",
                        "--role", "bug-fix",
                        *extra,
                    ),
                    env=env,
                ))

            capped = invoke("--brief-mode", "light")
            plain = invoke()
        self.assertEqual(capped.returncode, 0, capped.stderr)
        capped_payload = json.loads(capped.stdout)
        self.assertEqual(capped_payload["mode"], "light")
        self.assertEqual(capped_payload["modeSource"], "brief")
        self.assertEqual(capped_payload["roles"]["bug-fix"], INHERIT_ENTRY)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        plain_payload = json.loads(plain.stdout)
        self.assertEqual(plain_payload["mode"], "light")
        self.assertEqual(plain_payload["modeSource"], "default")
        self.assertEqual(plain_payload["roles"]["bug-fix"], INHERIT_ENTRY)

    def test_bad_brief_mode_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run("show", "--brief-mode", "turbo", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")

    def test_light_does_not_enable_fast_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "roles": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"effort": "xhigh", "fastMode": False},
            }]}})
            held = repo.show_bug_fix()
            repo.put(repo.user, {"mode": "light", "roles": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"effort": "xhigh"},
            }]}})
            absent = repo.show_bug_fix()
        self.assertEqual(held.returncode, 0, held.stderr)
        self.assertEqual(absent.returncode, 0, absent.stderr)
        self.assertEqual(json.loads(held.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "claudeAgent",
            "model": "claude-opus-5-5",
            "options": {"effort": "medium", "fastMode": False},
        }])
        self.assertEqual(json.loads(absent.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "claudeAgent",
            "model": "claude-opus-5-5",
            "options": {"effort": "medium"},
        }])



HAIKU_BRIEF = list(roles.HAIKU_BRIEF)


class Haiku55CliTest(unittest.TestCase):
    def test_bounded_seat_command_unknown(self):
        completed = in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("bounded-seat",),
        ))
        self.assertEqual(completed.returncode, 2)

    def test_write_and_show_configured_haiku_skill_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.write("--set", "skill tests=claudeAgent/claude-haiku-5-5")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            shown = repo.run(
                "show",
                "--catalog",
                str(CATALOG),
                "--parent",
                "claudeAgent/claude-opus-5-5",
                "--role",
                "skill tests",
            )
        self.assertEqual(shown.returncode, 0, shown.stderr)
        entry = json.loads(shown.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"][0]["model"], "claude-haiku-5-5")
        self.assertEqual(entry["haikuBrief"], HAIKU_BRIEF)

    def test_show_output_never_mentions_caps(self):
        completed = show("bug-fix", "skill tests", "how explorer")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        lowered = completed.stdout.lower()
        self.assertNotIn("capped", lowered)
        self.assertNotIn("prompt tokens", lowered)

    def test_provider_spellings_map_to_the_canonical_id(self):
        cases = (
            ("claude-haiku-4-5@20251001", "claude-haiku-4-5"),
            ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
            ("ANTHROPIC.CLAUDE_HAIKU_4_5_20251001", "claude-haiku-4-5"),
            ("anthropic/claude-haiku-4.5", "claude-haiku-4-5"),
            ("us.anthropic.claude-haiku-4-5@20251001", "claude-haiku-4-5"),
            ("eu.anthropic.claude-haiku-4-5", "claude-haiku-4-5"),
            ("apac.anthropic.claude-haiku-4-5", "claude-haiku-4-5"),
            ("global.anthropic.claude-haiku-4-5", "claude-haiku-4-5"),
            ("amazon-bedrock/anthropic.claude-haiku-4-5", "claude-haiku-4-5"),
            ("anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("us.anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("eu.anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("apac.anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("global.anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("amazon-bedrock/anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("opencode/amazon-bedrock/anthropic.claude-haiku-5-5", "claude-haiku-5-5"),
            ("claude-haiku-5-5@20251001", "claude-haiku-5-5"),
            ("claude-haiku-5-5-20251001", "claude-haiku-5-5"),
            ("claude-haiku-5-5", "claude-haiku-5-5"),
            ("claude-sonnet-4-5", "claude-sonnet-4-5"),
            ("claude-sonnet-4.5", "claude-sonnet-4-5"),
        )
        for model_id, canonical in cases:
            self.assertEqual(roles.normalized_bare(model_id), canonical, model_id)

    def test_launches_seats_rejects_wrong_role(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.run(
                "show",
                "--catalog",
                str(CATALOG),
                "--parent",
                "claudeAgent/claude-opus-5-5",
                "--role",
                "bug-fix",
                "--launches-seats",
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn('only with exactly one --role "skill tests"', completed.stderr)

    def test_launches_seats_requires_catalog_and_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            no_catalog = repo.run("show", "--role", "skill tests", "--launches-seats")
            self.assertEqual(no_catalog.returncode, 2)
            self.assertIn("requires a catalog", no_catalog.stderr)
            no_parent = repo.run(
                "show",
                "--catalog",
                str(CATALOG),
                "--role",
                "skill tests",
                "--launches-seats",
            )
        self.assertEqual(no_parent.returncode, 2)
        self.assertIn("requires --parent", no_parent.stderr)

    def test_launches_seats_unset_skill_tests_inherits(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.run(
                "show",
                "--catalog",
                str(CATALOG),
                "--parent",
                "claudeAgent/claude-opus-5-5",
                "--role",
                "skill tests",
                "--launches-seats",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["skill tests"], INHERIT_ENTRY)


VERTEX_ID = "claude-haiku-4-5@20251001"
HAIKU_55_SPELLINGS = (
    "anthropic.claude-haiku-5-5",
    "us.anthropic.claude-haiku-5-5",
    "eu.anthropic.claude-haiku-5-5",
    "apac.anthropic.claude-haiku-5-5",
    "global.anthropic.claude-haiku-5-5",
    "amazon-bedrock/anthropic.claude-haiku-5-5",
    "claude-haiku-5-5@20251001",
    "claude-haiku-5-5-20251001",
)


def vertex_only_catalog():
    return {"providers": [{
        "providerInstanceId": "claudeAgent",
        "canRunChildTask": True,
        "constraints": [],
        "models": [{"id": VERTEX_ID, "options": []}],
    }]}


class VertexHaikuCliTest(unittest.TestCase):
    """An exclude model glob catches the Vertex, Bedrock, and dated Haiku 4.5 spellings."""

    EXCLUDE = [{"model": "*claude-haiku-4-5*"}]

    def test_the_exclude_glob_refuses_the_haiku_4_5_spellings_it_lists(self):
        rules = roles.parse_exclude(self.EXCLUDE, "test")
        for model_id in (
            "claude-haiku-4-5",
            "claude-haiku-4-5-20251001",
            VERTEX_ID,
            "anthropic/claude-haiku-4.5",
            "amazon-bedrock/anthropic.claude-haiku-4-5@20251001",
            "us.anthropic.claude-haiku-4-5",
        ):
            with self.subTest(model=model_id):
                self.assertIsNotNone(
                    roles.exclude_reason({"providerInstanceId": "claudeAgent", "model": model_id}, rules), model_id
                )
        for model_id in ("claude-haiku-5-5", "claude-sonnet-4-5", "claude-haiku-4-6"):
            with self.subTest(model=model_id):
                self.assertIsNone(
                    roles.exclude_reason({"providerInstanceId": "claudeAgent", "model": model_id}, rules), model_id
                )

    def test_validate_rejects_a_configured_vertex_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, vertex_only_catalog())
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": VERTEX_ID,
            }]}, "exclude": self.EXCLUDE})
            completed = repo.run(
                "validate",
                "--catalog", str(path),
                "--parent", f"claudeAgent/{VERTEX_ID}",
            )
        self.assertEqual(completed.returncode, 1)
        self.assertIn(f"bug-fix: claudeAgent/{VERTEX_ID}", completed.stdout)
        self.assertIn(roles.EXCLUDE_RULE, completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_write_force_refuses_a_vertex_seat_the_exclude_list_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, vertex_only_catalog())
            repo.put(repo.user, {"version": 1, "roles": {}, "exclude": self.EXCLUDE})
            completed = repo.run(
                "write",
                "--catalog", str(path),
                "--parent", f"claudeAgent/{VERTEX_ID}",
                "--force",
                "--set", f"bug-fix=claudeAgent/{VERTEX_ID}",
            )
            self.assertEqual(json.loads(repo.user.read_text())["roles"], {})
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("refusing to write, even with --force", completed.stderr)
        self.assertIn(roles.EXCLUDE_RULE, completed.stderr)


class ExcludeEntryShapeCliTest(unittest.TestCase):
    """Every exclude entry is an object; a string entry is refused and names the exclude key."""

    def test_a_string_exclude_entry_is_refused_and_names_the_exclude_key(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"version": 1, "roles": {}, "exclude": ["grok-4.7"]})
            completed = repo.show_bug_fix()
            expected = (
                f"error: {repo.user}: exclude entry 'grok-4.7' is a string; "
                "write an object with model, options, provider, or roles\n"
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, expected)


class UnrunnableHaikuParentCliTest(unittest.TestCase):
    def show(self, constraints):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [{
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": False,
                "constraints": constraints,
                "models": [{"id": "claude-haiku-4-5", "options": []}, {"id": "claude-opus-5-5", "options": []}],
            }]})
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": ["inherit"]},
                                 "exclude": [{"model": "*claude-haiku-4-5*"}]})
            return repo.run("show", "--catalog", str(path), "--parent", "claudeAgent/claude-haiku-4-5", "--role", "bug-fix")

    def test_inherit_of_an_excluded_parent_whose_provider_cannot_run_child_tasks_names_the_provider(self):
        reasons = (
            (["Provider is not authenticated.", "Sign in again."], "Provider is not authenticated.; Sign in again."),
            ([], "cannot run child tasks"),
        )
        for constraints, reason in reasons:
            with self.subTest(constraints=constraints):
                completed = self.show(constraints)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertIn("cannot inherit claudeAgent/claude-haiku-4-5", completed.stderr)
                self.assertIn(roles.EXCLUDE_RULE, completed.stderr)
                self.assertIn(f"claudeAgent is not runnable ({reason})", completed.stderr)


class BedrockHaikuBriefCliTest(unittest.TestCase):
    """Bedrock and dated Haiku 5.5 spellings still get haikuBrief."""

    def show(self, model_id, seat):
        catalog = {"providers": [{
            "providerInstanceId": "opencode",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": model_id, "options": [], "cost": 0}],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"version": 1, "roles": {"how explorer": [seat]}})
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            return repo.run(
                "show",
                "--catalog", str(path),
                "--parent", f"opencode/{model_id}",
                "--role", "how explorer",
            )

    def test_explicit_and_inherit_seats_get_haiku_brief(self):
        for model_id in HAIKU_55_SPELLINGS:
            explicit = {"providerInstanceId": "opencode", "model": model_id}
            for seat in (explicit, "inherit"):
                label = "inherit" if seat == "inherit" else "explicit"
                with self.subTest(model=model_id, seat=label):
                    completed = self.show(model_id, seat)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    entry = json.loads(completed.stdout)["roles"]["how explorer"]
                    expected = ["inherit"] if seat == "inherit" else [explicit]
                    self.assertEqual(entry["seats"], expected)
                    self.assertEqual(entry.get("haikuBrief"), HAIKU_BRIEF)


def cursor_only_catalog():
    return {"providers": [
        {
            "providerInstanceId": "cursor",
            "driverKind": "cursor",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7", "options": []}],
        },
        {
            "providerInstanceId": "claudeAgent",
            "driverKind": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": []}],
        },
    ]}


class SkillTestsCursorCliTest(unittest.TestCase):
    """The cursor driver cannot launch seats, so a configured seat that runs on it is refused under --launches-seats."""

    def run_show(self, roles, *extra):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, cursor_only_catalog())
            if roles is not None:
                repo.put(repo.user, {"version": 1, "roles": roles})
            return repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "skill tests",
                *extra,
            )

    def test_show_notes_a_configured_cursor_skill_tests_seat(self):
        completed = self.run_show({"skill tests": [{"providerInstanceId": "cursor", "model": "grok-4.7"}]})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "cursor", "model": "grok-4.7"}])
        self.assertIn("runs on the cursor driver, which cannot launch seats", " ".join(entry.get("notes", [])))

    def test_validate_reports_a_configured_cursor_skill_tests_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, cursor_only_catalog())
            repo.put(repo.user, {"version": 1, "roles": {
                "skill tests": [{"providerInstanceId": "cursor", "model": "grok-4.7"}],
            }})
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 1)
        self.assertIn("runs on the cursor driver, which cannot launch seats", completed.stdout)
        self.assertEqual(completed.stderr, "")

    def test_launches_seats_refuses_a_configured_cursor_skill_tests_seat(self):
        completed = self.run_show(
            {"skill tests": [{"providerInstanceId": "cursor", "model": "grok-4.7"}]},
            "--launches-seats",
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, (
            "error: role 'skill tests' has no seat for a child that launches seats: "
            "a seat that runs on the cursor driver cannot launch seats, and the seat resolved to cursor\n"
        ))

    def test_launches_seats_keeps_a_configured_non_cursor_seat(self):
        completed = self.run_show(
            {"skill tests": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
            "--launches-seats",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}])


def _select(option_id, values):
    return {"id": option_id, "type": "select", "options": [{"id": value} for value in values]}


_CLAUDE_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultracode", "ultrathink")
_CODEX_LEVELS = ("low", "medium", "high", "xhigh", "max", "ultra")
_GROK_LEVELS = ("low", "medium", "high", "xhigh")
_LIMIT = "You've hit your usage limit. Try again later.\n"
MUSE = "opencode/muse-lite-2-free"
STEP = "opencode/step-9-preview-free"
BUNNY = "opencode/bunny-1-free"
MUSE_SEAT = {"providerInstanceId": "opencode", "model": MUSE, "options": {"variant": "high"}}
STEP_SEAT = {"providerInstanceId": "opencode", "model": STEP, "options": {"variant": "high"}}
BUNNY_SEAT = {"providerInstanceId": "opencode", "model": BUNNY, "options": {"variant": "max"}}
REVIEW_BACKUPS = {"roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]}}
OPUS_BACKUP = [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]
SONNET_BACKUP = [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}]
REVIEW_LADDER_BACKUP = [
    {"providerInstanceId": "grok", "model": "grok-4.7"},
    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
]
# The owner's house-rule migration for a roles file whose roles all name seats.
MIGRATION_BACKUPS = {
    "feature, refactoring": OPUS_BACKUP,
    "bug-fix": OPUS_BACKUP,
    "perf-issue": OPUS_BACKUP,
    "hillclimb": OPUS_BACKUP,
    "judgment and prose": OPUS_BACKUP,
    "hardest tasks": OPUS_BACKUP,
    "how explorer": SONNET_BACKUP,
    "how explainer": OPUS_BACKUP,
    "why investigators": SONNET_BACKUP,
    "why synthesizer": OPUS_BACKUP,
    "reflect tooling": OPUS_BACKUP,
    "reflect judgment, divergent, synthesizer": OPUS_BACKUP,
    "swarm workers": OPUS_BACKUP,
    "skill tests": SONNET_BACKUP,
    "arena runners": OPUS_BACKUP,
    "arena cross-judge pool": REVIEW_LADDER_BACKUP,
    "architect runners": OPUS_BACKUP,
    "interrogate reviewers": REVIEW_LADDER_BACKUP,
    "verifiers": REVIEW_LADDER_BACKUP,
}
PANEL_RULE = {"waitForAllTerminal": True, "minimumPasses": 2, "maximumReproducedBlockers": 0}
UNSET_NOTE = roles.UNSET_NOTE
VERIFIER_OUT = (
    "--role", "verifiers",
    "--provider", "codex",
    "--model", "gpt-6.1-sol",
    "--author", "claudeAgent/claude-opus-5-5",
    "--out", "grok",
)
VERIFIER_PARK = {
    "decision": "park",
    "role": "verifiers",
    "failed": "codex/gpt-6.1-sol",
    "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
}


def backup_catalog(*, second_claude=False):
    claude_options = [_select("effort", _CLAUDE_LEVELS)]
    providers = []
    if second_claude:
        providers.append({
            "providerInstanceId": "claudeDesktop",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": claude_options}],
        })
    providers.extend([
        {
            "providerInstanceId": "cursor",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": claude_options}],
        },
        {
            "providerInstanceId": "codex",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "gpt-6.1-sol", "options": [_select("reasoningEffort", _CODEX_LEVELS)]}],
        },
        {
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{
                "id": "grok-4.7",
                "options": [_select("reasoningEffort", _GROK_LEVELS), {"id": "fastMode", "type": "boolean"}],
            }],
        },
        {
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "claude-opus-5-5", "options": claude_options},
                {"id": "claude-sonnet-5-5", "options": claude_options},
            ],
        },
        {
            "providerInstanceId": "opencode",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": model, "options": [_select("variant", ("high", "max"))], "cost": 0}
                for model in (MUSE, STEP, BUNNY)
            ],
        },
    ])
    return {"providers": providers}


BEDROCK_CLAUDE = (
    "anthropic.claude-sonnet-5-5-v1:0",
    "us.anthropic.claude-sonnet-5-5-v1:0",
    "eu.anthropic.claude-sonnet-5-5-v1:0",
    "apac.anthropic.claude-sonnet-5-5-v1:0",
    "global.anthropic.claude-sonnet-5-5-v1:0",
    "amazon-bedrock/us.anthropic.claude-sonnet-5-5-v1:0",
)


def bedrock_catalog():
    catalog = backup_catalog()
    opencode = next(provider for provider in catalog["providers"] if provider["providerInstanceId"] == "opencode")
    opencode["models"].extend({"id": model, "options": []} for model in ("claude-sonnet-5-5", *BEDROCK_CLAUDE))
    return catalog


def run_backup(*args, text=_LIMIT, catalog=None, roles_file=None):
    with tempfile.TemporaryDirectory() as directory:
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, backup_catalog() if catalog is None else catalog)
        if roles_file is not None:
            repo.put(repo.user, roles_file)
        env = {**os.environ, "XDG_CONFIG_HOME": str(repo.directory)}
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(
                "backup",
                "--cwd", str(repo.directory),
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                *args,
            ),
            env=env,
            stdin=text,
        ))


class BackupCliTest(unittest.TestCase):
    def backup(self, *args, text=_LIMIT, catalog=None, roles_file=None):
        document = {"backups": MIGRATION_BACKUPS} if roles_file is None else roles_file
        return run_backup(*args, text=text, catalog=catalog, roles_file=document)

    def assert_backup(self, completed, expected):
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), expected)

    def test_codex_worker_at_xhigh_relaunches_on_opus(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--options", '[{"id": "reasoningEffort", "value": "xhigh"}]',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_grok_worker_relaunches_on_opus_at_the_light_cap(self):
        completed = self.backup(
            "--role", "feature, refactoring",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '[{"id": "reasoningEffort", "value": "high"}]',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "feature, refactoring",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "feature, refactoring: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_how_explorer_relaunches_on_sonnet(self):
        completed = self.backup(
            "--role", "how explorer",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "medium"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "how explorer",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5", "options": {"effort": "medium"}},
            "report": "how explorer: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-sonnet-5-5 at medium",
        })

    def test_light_mode_caps_the_level_at_medium(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--options", '[{"id": "reasoningEffort", "value": "xhigh"}]',
            "--brief-mode", "light",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    HIGH_VERIFIER = (
        "--role", "verifiers",
        "--provider", "codex",
        "--model", "gpt-6.1-sol",
        "--options", '{"reasoningEffort": "high"}',
        "--brief-mode", "light",
    )

    def grok_relaunch(self, level):
        return {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": level},
            },
            "report": f"verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on grok/grok-4.7 at {level}",
        }

    def test_light_mode_relaunches_a_high_verifier_at_high(self):
        completed = self.backup(*self.HIGH_VERIFIER, "--author", "claude-opus-5-5")
        self.assert_backup(completed, self.grok_relaunch("high"))

    def test_light_mode_with_small_budget_relaunches_a_high_verifier_at_medium(self):
        completed = self.backup(*self.HIGH_VERIFIER, "--author", "claude-opus-5-5", roles_file={"budget": "small", "backups": MIGRATION_BACKUPS})
        self.assert_backup(completed, self.grok_relaunch("medium"))

    def test_light_mode_resumes_a_high_verifier_at_high(self):
        completed = self.backup(*self.HIGH_VERIFIER, "--author", "claude-opus-5-5", "--resume")
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "high"}},
            "report": "verifiers: codex/gpt-6.1-sol resumed on codex/gpt-6.1-sol at high after the reset",
        })

    def test_light_mode_keeps_each_effort_seat_of_the_verifiers_backup_panel_at_its_level(self):
        catalog = backup_catalog()
        catalog["providers"].extend(
            {
                "providerInstanceId": provider,
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": model, "options": [_select("reasoningEffort", _GROK_LEVELS)]}],
            }
            for provider, model in (("kilo", "kilo-1"), ("openrouter", "muse-lite-2"))
        )
        completed = self.backup(
            *VERIFIER_OUT,
            "--brief-mode", "light",
            catalog=catalog,
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "kilo", "model": "kilo-1", "options": {"reasoningEffort": "xhigh"}},
                {"providerInstanceId": "openrouter", "model": "muse-lite-2", "options": {"reasoningEffort": "high"}},
            ]}},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "panel")
        self.assertEqual(payload["seats"], [
            {"providerInstanceId": "kilo", "model": "kilo-1", "options": {"reasoningEffort": "xhigh"}},
            {"providerInstanceId": "openrouter", "model": "muse-lite-2", "options": {"reasoningEffort": "high"}},
        ])

    def test_codex_ultra_lands_on_medium_under_the_light_default(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--options", '{"reasoningEffort": "ultra"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_a_variant_level_maps_onto_the_backup_effort(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"variant": "high"}',
            "--brief-mode", "full",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at high",
        })

    def test_a_seat_with_no_level_gets_medium_under_the_light_default(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_a_claude_worker_parks_and_names_neither_codex_nor_cursor(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "claudeAgent",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "bug-fix",
            "failed": "claudeAgent/claude-opus-5-5",
            "report": "bug-fix: claudeAgent/claude-opus-5-5 hit its usage limit; no backup seat, so the work waits for the reset",
        })
        self.assertNotIn("codex", completed.stdout)
        self.assertNotIn("cursor", completed.stdout)

    def test_codex_verifier_with_a_claude_author_relaunches_on_grok(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claude-opus-5-5",
            "--options", '[{"id": "reasoningEffort", "value": "xhigh"}]',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "xhigh"},
            },
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on grok/grok-4.7 at xhigh",
        })

    def test_codex_verifier_with_a_grok_author_relaunches_on_opus(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at xhigh",
        })

    def test_codex_verifier_parks_when_grok_authored_and_claude_is_out(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok-4.7",
            "--out", "claudeAgent",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })

    def test_options_array_and_object_parse_to_the_same_seat(self):
        expected = {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        }
        array = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '[{"id": "reasoningEffort", "value": "high"}]',
        )
        obj = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
        )
        self.assert_backup(array, expected)
        self.assert_backup(obj, expected)

    def test_two_author_families_park_a_reviewer(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claude-opus-5-5",
            "--author", "grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })

    def test_provider_and_model_authors_exclude_every_family(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--author", "grok/grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })

    def test_a_provider_and_model_author_skips_that_family_on_claude_agent(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok/grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at xhigh",
        })

    def test_cursor_claude_relaunches_on_claude_agent(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "cursor",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "cursor/claude-opus-5-5",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: cursor/claude-opus-5-5 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_worker_backup_uses_the_configured_claude_agent_seat(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_cursor_light_role_relaunches_on_claude_agent_sonnet(self):
        completed = self.backup(
            "--role", "how explorer",
            "--provider", "cursor",
            "--model", "claude-haiku-5-5",
            "--options", '{"effort": "medium"}',
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "how explorer",
            "failed": "cursor/claude-haiku-5-5",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5", "options": {"effort": "medium"}},
            "report": "how explorer: cursor/claude-haiku-5-5 hit its usage limit; relaunched on claudeAgent/claude-sonnet-5-5 at medium",
        })

    def test_cursor_claude_parks_when_claude_agent_is_out(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "cursor",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            "--out", "claudeAgent",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "bug-fix",
            "failed": "cursor/claude-opus-5-5",
            "report": "bug-fix: cursor/claude-opus-5-5 hit its usage limit; no backup seat, so the work waits for the reset",
        })

    def test_resume_returns_the_original_claude_agent_seat(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "claudeAgent",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "claudeAgent/claude-opus-5-5",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: claudeAgent/claude-opus-5-5 resumed on claudeAgent/claude-opus-5-5 at medium after the reset",
        })

    def test_resume_parks_while_claude_agent_stays_out(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "claudeAgent",
            "--model", "claude-opus-5-5",
            "--options", '{"effort": "xhigh"}',
            "--out", "grok",
            "--out", "claudeAgent",
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "bug-fix",
            "failed": "claudeAgent/claude-opus-5-5",
            "report": "bug-fix: claudeAgent/claude-opus-5-5 is still out after the reset; no backup seat, so the work waits for the reset",
        })

    def test_resume_returns_the_original_grok_seat_when_grok_is_back(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--resume",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "medium"},
            },
            "report": "bug-fix: grok/grok-4.7 resumed on grok/grok-4.7 at medium after the reset",
        })

    def test_resume_uses_claude_agent_when_the_original_provider_stays_out(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--out", "grok",
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 resumed on claudeAgent/claude-opus-5-5 at medium after the reset",
        })

    def test_resume_of_a_reviewer_does_not_return_an_author_family(self):
        parked = self.backup(
            "--role", "verifiers",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--author", "grok/grok-4.7",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--resume",
        )
        self.assert_backup(parked, {
            "decision": "park",
            "role": "verifiers",
            "failed": "grok/grok-4.7",
            "report": "verifiers: grok/grok-4.7 is still out after the reset; no backup seat, so the work waits for the reset",
        })
        moved = self.backup(
            "--role", "verifiers",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--author", "grok/grok-4.7",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(moved, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}},
            "report": "verifiers: grok/grok-4.7 resumed on claudeAgent/claude-opus-5-5 at xhigh after the reset",
        })

    def test_resume_of_a_namespaced_reviewer_does_not_return_its_author(self):
        muse = self.backup(
            "--role", "verifiers",
            "--provider", "opencode",
            "--model", MUSE,
            "--author", "opencode/" + MUSE,
            "--out", "grok",
            "--out", "claudeAgent",
            "--resume",
        )
        self.assert_backup(muse, {
            "decision": "park",
            "role": "verifiers",
            "failed": "opencode/opencode/muse-lite-2-free",
            "report": (
                "verifiers: opencode/opencode/muse-lite-2-free is still out after the reset; "
                "no backup seat, so the work waits for the reset"
            ),
        })
        catalog = backup_catalog()
        opencode = next(p for p in catalog["providers"] if p["providerInstanceId"] == "opencode")
        opencode["models"].append({"id": "anthropic/claude-sonnet-5-5", "options": []})
        claude = self.backup(
            "--role", "verifiers",
            "--provider", "opencode",
            "--model", "anthropic/claude-sonnet-5-5",
            "--author", "claudeAgent/claude-opus-5-5",
            "--resume",
            catalog=catalog,
        )
        self.assert_backup(claude, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "opencode/anthropic/claude-sonnet-5-5",
            "seat": {"providerInstanceId": "grok", "model": "grok-4.7"},
            "report": "verifiers: opencode/anthropic/claude-sonnet-5-5 resumed on grok/grok-4.7 after the reset",
        })

    def test_resume_returns_a_reviewer_whose_family_wrote_nothing(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok/grok-4.7",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--resume",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "xhigh"}},
            "report": "verifiers: codex/gpt-6.1-sol resumed on codex/gpt-6.1-sol at xhigh after the reset",
        })

    def test_resume_of_a_light_role_uses_sonnet_when_the_provider_stays_out(self):
        completed = self.backup(
            "--role", "how explorer",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "medium"}',
            "--out", "grok",
            "--resume",
            catalog=backup_catalog(second_claude=True),
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "how explorer",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5", "options": {"effort": "medium"}},
            "report": "how explorer: grok/grok-4.7 resumed on claudeAgent/claude-sonnet-5-5 at medium after the reset",
        })

    def test_grok_usage_balance_exhausted_is_a_usage_limit(self):
        completed = self.backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            text="API error (status 402 Payment Required): Grok Build usage balance exhausted\n",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_a_cursor_grok_reviewer_moves_to_the_grok_provider(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "cursor",
            "--model", "grok-4.7",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "high"}',
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "cursor/grok-4.7",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "high"},
            },
            "report": "verifiers: cursor/grok-4.7 hit its usage limit; relaunched on grok/grok-4.7 at high",
        })

    def test_resume_of_a_reviewer_still_out_uses_the_configured_backup(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            "--out", "codex",
            "--resume",
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "xhigh"},
            },
            "report": "verifiers: codex/gpt-6.1-sol resumed on grok/grok-4.7 at xhigh after the reset",
        })

    def test_rate_limit_overload_and_429_are_not_a_usage_limit(self):
        expected = {
            "decision": "not-usage-limit",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "report": "bug-fix: grok/grok-4.7 failed without a usage limit; respawn per Failure handling",
        }
        for text in ("rate_limit_error", "overloaded", "429", "usage limit", "model not found"):
            with self.subTest(text=text):
                completed = self.backup(
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                    "--options", '{"reasoningEffort": "high"}',
                    text=text,
                )
                self.assert_backup(completed, expected)

    def test_listed_limit_phrases_relaunch(self):
        expected = {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "grok/grok-4.7",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: grok/grok-4.7 hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        }
        phrases = (
            "You've hit your usage limit. Try again later.",
            "You've hit your limit",
            "usage limit reached",
            "reached your usage limit",
            "usage limit exceeded",
            "out of usage",
            "quota exceeded",
            "exceeded your quota",
            "insufficient_quota",
        )
        for text in phrases:
            with self.subTest(text=text):
                completed = self.backup(
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                    "--options", '{"reasoningEffort": "high"}',
                    text=text,
                )
                self.assert_backup(completed, expected)

    def test_inherit_missing_author_and_catalog_stdin_exit_2(self):
        inherit = self.backup(
            "--role", "bug-fix",
            "--provider", "inherit",
            "--model", "claude-opus-5-5",
        )
        self.assertEqual(inherit.returncode, 2)
        self.assertEqual(inherit.stdout, "")
        self.assertIn("inherit", inherit.stderr)

        missing_author = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
        )
        self.assertEqual(missing_author.returncode, 2)
        self.assertEqual(missing_author.stdout, "")
        self.assertIn("--author", missing_author.stderr)

        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            env = {**os.environ, "XDG_CONFIG_HOME": str(repo.directory)}
            stdin_catalog = in_process.invoke(in_process.Invocation(
                program=ROOT / "t3/scripts/roles.py",
                argv=(
                    "backup",
                    "--cwd", str(repo.directory),
                    "--catalog", "-",
                    "--parent", "claudeAgent/claude-opus-5-5",
                    "--role", "bug-fix",
                    "--provider", "grok",
                    "--model", "grok-4.7",
                ),
                env=env,
                stdin=_LIMIT,
            ))
        self.assertEqual(stdin_catalog.returncode, 2)
        self.assertEqual(stdin_catalog.stdout, "")
        self.assertIn("--catalog -", stdin_catalog.stderr)

    def test_unset_review_backups_keeps_the_verifier_park(self):
        self.assert_backup(self.backup(*VERIFIER_OUT), VERIFIER_PARK)

    def test_review_backups_turn_a_verifier_park_into_a_panel(self):
        completed = self.backup(*VERIFIER_OUT, roles_file=REVIEW_BACKUPS)
        self.assert_backup(completed, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seats": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                "so review backups runs 3 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })

    def test_a_configured_verifiers_backup_wins_over_the_panel(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--options", '{"reasoningEffort": "xhigh"}',
            roles_file={
                "roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]},
                "backups": {"verifiers": REVIEW_LADDER_BACKUP},
            },
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seat": {
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "xhigh"},
            },
            "report": "verifiers: codex/gpt-6.1-sol hit its usage limit; relaunched on grok/grok-4.7 at xhigh",
        })

    def test_resume_with_every_configured_backup_out_runs_the_panel(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--out", "codex",
            "--out", "grok",
            "--out", "claudeAgent",
            "--resume",
            text="",
            roles_file={
                "roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]},
                "backups": {"verifiers": REVIEW_LADDER_BACKUP},
            },
        )
        self.assert_backup(completed, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seats": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "report": (
                "verifiers: codex/gpt-6.1-sol is still out after the reset; every paid reviewer backup is out, "
                "so review backups runs 3 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })

    def test_only_verifiers_get_a_panel(self):
        both_keys = {
            "roles": {"review backups": [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT]},
            "backups": {"interrogate reviewers": REVIEW_LADDER_BACKUP, "bug-fix": OPUS_BACKUP},
        }
        interrogate = self.backup(
            "--role", "interrogate reviewers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "claudeAgent/claude-opus-5-5",
            "--out", "grok",
            roles_file=both_keys,
        )
        self.assert_backup(interrogate, {
            "decision": "park",
            "role": "interrogate reviewers",
            "failed": "codex/gpt-6.1-sol",
            "report": "interrogate reviewers: codex/gpt-6.1-sol hit its usage limit; no backup seat, so the work waits for the reset",
        })
        worker = self.backup(
            "--role", "bug-fix",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--out", "grok",
            roles_file=both_keys,
        )
        self.assert_backup(worker, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "codex/gpt-6.1-sol",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5 at medium",
        })

    def test_panel_drops_blocked_author_missing_and_repeated_seats(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--author", "opencode/" + MUSE,
            "--out", "claudeAgent",
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                MUSE_SEAT,
                {"providerInstanceId": "opencode", "model": "opencode/nope-free"},
                STEP_SEAT,
                BUNNY_SEAT,
                {"providerInstanceId": "opencode", "model": BUNNY, "options": {"variant": "high"}},
                {"providerInstanceId": "grok", "model": "grok-4.7"},
            ]}},
        )
        self.assert_backup(completed, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "grok/grok-4.7",
            "seats": [STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "notes": [
                "dropped claudeAgent/claude-opus-5-5: claudeAgent is out",
                "dropped opencode/opencode/muse-lite-2-free: muse wrote the diff",
                "dropped opencode/opencode/nope-free: not runnable or not in the catalog",
                "dropped opencode/opencode/bunny-1-free: family bunny already seated",
                "dropped grok/grok-4.7: grok is out",
            ],
            "report": (
                "verifiers: grok/grok-4.7 hit its usage limit; every paid reviewer backup is out, "
                "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })

    def test_one_usable_seat_parks(self):
        completed = self.backup(
            *VERIFIER_OUT,
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "opencode", "model": "opencode/nope-free"},
                STEP_SEAT,
            ]}},
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
                "so the work waits for the reset "
                "(dropped opencode/opencode/nope-free: not runnable or not in the catalog)"
            ),
        })

    def test_a_panel_member_limit_parks_without_a_backup(self):
        member = ("--role", "review backups", "--provider", "opencode", "--model", STEP)
        self.assert_backup(self.backup(*member, roles_file=REVIEW_BACKUPS), {
            "decision": "park",
            "role": "review backups",
            "failed": "opencode/opencode/step-9-preview-free",
            "report": (
                "review backups: opencode/opencode/step-9-preview-free hit its usage limit; "
                "a review backups seat has no backup, so it counts as no pass"
            ),
        })
        self.assert_backup(self.backup(*member, text="connection reset\n", roles_file=REVIEW_BACKUPS), {
            "decision": "not-usage-limit",
            "role": "review backups",
            "failed": "opencode/opencode/step-9-preview-free",
            "report": (
                "review backups: opencode/opencode/step-9-preview-free failed without a usage limit; "
                "respawn per Failure handling"
            ),
        })

    def test_a_bedrock_claude_author_skips_the_claude_agent_backup(self):
        for model in BEDROCK_CLAUDE:
            with self.subTest(model=model):
                completed = self.backup(
                    "--role", "verifiers",
                    "--provider", "codex",
                    "--model", "gpt-6.1-sol",
                    "--author", "opencode/" + model,
                    "--out", "grok",
                    catalog=bedrock_catalog(),
                )
                self.assert_backup(completed, VERIFIER_PARK)

    def test_resume_of_a_bedrock_claude_reviewer_does_not_return_a_claude_author(self):
        for model in BEDROCK_CLAUDE:
            with self.subTest(model=model):
                completed = self.backup(
                    "--role", "verifiers",
                    "--provider", "opencode",
                    "--model", model,
                    "--author", "claudeAgent/claude-opus-5-5",
                    "--out", "grok",
                    "--out", "claudeAgent",
                    "--resume",
                    text="",
                    catalog=bedrock_catalog(),
                )
                self.assert_backup(completed, {
                    "decision": "park",
                    "role": "verifiers",
                    "failed": "opencode/" + model,
                    "report": (
                        f"verifiers: opencode/{model} is still out after the reset; "
                        "no backup seat, so the work waits for the reset"
                    ),
                })

    def test_panel_drops_a_bedrock_claude_seat_for_a_claude_author(self):
        for model in BEDROCK_CLAUDE:
            with self.subTest(model=model):
                completed = self.backup(
                    *VERIFIER_OUT,
                    catalog=bedrock_catalog(),
                    roles_file={"roles": {"review backups": [
                        {"providerInstanceId": "opencode", "model": model},
                        STEP_SEAT,
                        BUNNY_SEAT,
                    ]}},
                )
                self.assert_backup(completed, {
                    "decision": "panel",
                    "role": "verifiers",
                    "failed": "codex/gpt-6.1-sol",
                    "seats": [STEP_SEAT, BUNNY_SEAT],
                    "rule": PANEL_RULE,
                    "notes": [f"dropped opencode/{model}: claude wrote the diff"],
                    "report": (
                        "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                        "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
                    ),
                })

    def test_a_bedrock_form_of_a_seated_family_parks_a_two_seat_panel(self):
        completed = self.backup(
            "--role", "verifiers",
            "--provider", "codex",
            "--model", "gpt-6.1-sol",
            "--author", "grok/grok-4.7",
            "--out", "grok",
            "--out", "claudeAgent",
            catalog=bedrock_catalog(),
            roles_file={"roles": {"review backups": [
                {"providerInstanceId": "opencode", "model": "claude-sonnet-5-5"},
                {"providerInstanceId": "opencode", "model": "us.anthropic.claude-sonnet-5-5-v1:0"},
            ]}},
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
                "so the work waits for the reset "
                "(dropped opencode/us.anthropic.claude-sonnet-5-5-v1:0: family claude already seated)"
            ),
        })

    def test_resume_of_a_panel_member_in_an_author_family_parks(self):
        completed = self.backup(
            "--role", "review backups",
            "--provider", "opencode",
            "--model", MUSE,
            "--author", "opencode/" + MUSE,
            "--resume",
            text="",
            roles_file=REVIEW_BACKUPS,
        )
        self.assert_backup(completed, {
            "decision": "park",
            "role": "review backups",
            "failed": "opencode/opencode/muse-lite-2-free",
            "report": (
                "review backups: opencode/opencode/muse-lite-2-free is in an author's family, "
                "so it does not resume and counts as no pass"
            ),
        })

    def test_resume_of_a_panel_member_returns_its_seat_for_another_author(self):
        completed = self.backup(
            "--role", "review backups",
            "--provider", "opencode",
            "--model", MUSE,
            "--author", "claudeAgent/claude-opus-5-5",
            "--resume",
            text="",
            roles_file=REVIEW_BACKUPS,
        )
        self.assert_backup(completed, {
            "decision": "relaunch",
            "role": "review backups",
            "failed": "opencode/opencode/muse-lite-2-free",
            "seat": {"providerInstanceId": "opencode", "model": MUSE},
            "report": (
                "review backups: opencode/opencode/muse-lite-2-free resumed on "
                "opencode/opencode/muse-lite-2-free after the reset"
            ),
        })

    def test_resume_of_a_panel_member_needs_an_author(self):
        completed = self.backup(
            "--role", "review backups",
            "--provider", "opencode",
            "--model", MUSE,
            "--resume",
            text="",
            roles_file=REVIEW_BACKUPS,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: role 'review backups' needs --author with --resume, the model whose work it judges\n",
        )

    def test_light_mode_keeps_a_variant_seat(self):
        completed = self.backup(*VERIFIER_OUT, "--brief-mode", "light", roles_file=REVIEW_BACKUPS)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "panel")
        self.assertEqual(payload["seats"], [MUSE_SEAT, STEP_SEAT, BUNNY_SEAT])


class ReviewBackupsRoleCliTest(unittest.TestCase):
    def test_show_reports_unset_with_and_without_a_catalog(self):
        expected = {"source": "default", "seats": "unset", "note": UNSET_NOTE}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            parent = ("--parent", "claudeAgent/claude-opus-5-5", "--role", "review backups")
            plain = repo.run("show", *parent)
            with_catalog = repo.run("show", "--catalog", str(CATALOG), *parent)
        for completed in (plain, with_catalog):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["roles"], {"review backups": expected})

    def test_write_and_validate_refuse_inherit(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            written = repo.write("--set", "review backups=inherit;grok/grok-4.7")
            repo.put(repo.user, {"roles": {"review backups": ["inherit"]}})
            validated = repo.run("validate", "--catalog", str(CATALOG))
            user = str(repo.user)
        refusal = "role 'review backups' refuses inherit, because the parent can be the author\n"
        self.assertEqual(written.returncode, 2)
        self.assertEqual(written.stderr, f"error: {user}: {refusal}")
        self.assertEqual(validated.returncode, 2)
        self.assertEqual(validated.stderr, f"error: {user}: {refusal}")

    PANEL_CONFIG = {"roles": {"review backups": [
        MUSE_SEAT,
        {"providerInstanceId": "kilo", "model": "kilo-1"},
        {"providerInstanceId": "opencode", "model": "opencode/nope-free"},
        STEP_SEAT,
        {"providerInstanceId": "openrouter", "model": "muse-lite-2"},
    ]}}
    PANEL_SET = (
        "review backups=opencode/opencode/muse-lite-2-free?variant=high;kilo/kilo-1;"
        "opencode/opencode/nope-free;opencode/opencode/step-9-preview-free?variant=high;"
        "openrouter/muse-lite-2"
    )
    PANEL_NOTES = [
        "dropped kilo/kilo-1: not runnable or not in the catalog",
        "dropped opencode/opencode/nope-free: not runnable or not in the catalog",
        "dropped openrouter/muse-lite-2: family muse already seated",
    ]
    PANEL_PROBLEMS = "".join(f"review backups: {note}\n" for note in PANEL_NOTES)

    def panel_repo(self, directory, roles_file=None):
        repo = Repo(directory)
        catalog = backup_catalog()
        catalog["providers"].append(plain_provider("kilo", "kilo-1", runs=False))
        catalog["providers"].append(plain_provider("openrouter", "muse-lite-2"))
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        if roles_file is not None:
            repo.put(repo.user, roles_file)
        return repo, str(path)

    def test_show_and_backup_print_the_panel_literals_for_one_config_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.PANEL_CONFIG)
            shown = repo.run("show", "--catalog", catalog, "--role", "review backups")
            backed = in_process.invoke(in_process.Invocation(
                program=ROOT / "t3/scripts/roles.py",
                argv=("backup", "--cwd", str(repo.directory), "--catalog", catalog, *VERIFIER_OUT),
                env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
                stdin=_LIMIT,
            ))
            user = str(repo.user)
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": [MUSE_SEAT, STEP_SEAT],
            "notes": self.PANEL_NOTES + [metered_line("review backups", "opencode", "opencode/nope-free")],
        }})
        self.assertEqual(backed.returncode, 0, backed.stderr)
        payload = json.loads(backed.stdout)
        self.assertEqual(payload["decision"], "panel")
        self.assertEqual(payload["seats"], [MUSE_SEAT, STEP_SEAT])
        self.assertEqual(payload["notes"], self.PANEL_NOTES)

    def test_validate_prints_a_problem_for_each_dropped_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.PANEL_CONFIG)
            completed = repo.run("validate", "--catalog", catalog)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stdout,
            metered_line("review backups", "opencode", "opencode/nope-free") + "\n" + self.PANEL_PROBLEMS,
        )
        self.assertEqual(completed.stderr, "")

    def test_write_refuses_dropped_seats_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory)
            refused = repo.run("write", "--catalog", catalog, "--set", self.PANEL_SET)
            written_before_force = repo.user.exists()
            forced = repo.run("write", "--catalog", catalog, "--set", self.PANEL_SET, "--force")
            stored = json.loads(repo.user.read_text())
            user = str(repo.user)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(
            refused.stderr,
            "error: refusing to write without --force:\n"
            + self.PANEL_PROBLEMS
            + metered_line("review backups", "opencode", "opencode/nope-free")
            + "\n",
        )
        self.assertFalse(written_before_force)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(forced.stdout, f"wrote {user}\n")
        self.assertEqual(stored["roles"], self.PANEL_CONFIG["roles"])

    def test_show_without_a_catalog_reports_catalog_required(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, _catalog = self.panel_repo(directory, self.PANEL_CONFIG)
            completed = repo.run("show", "--role", "review backups")
            user = str(repo.user)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": "catalog-required",
            "note": (
                "review backups drops seats by the catalog: "
                "call orchestrator_capabilities and rerun roles.py show --catalog"
            ),
            "notes": [
                metered_line("review backups", "opencode", MUSE),
                metered_line("review backups", "opencode", "opencode/nope-free"),
                metered_line("review backups", "opencode", STEP),
            ],
        }})

    def test_validate_reports_an_unknown_option_on_a_kept_seat(self):
        unknown = {**MUSE_SEAT, "options": {"variant": "high", "bogus": "1"}}
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, {"roles": {"review backups": [unknown, STEP_SEAT]}})
            completed = repo.run("validate", "--catalog", catalog)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(
            completed.stdout,
            "review backups: opencode/opencode/muse-lite-2-free: dropped unknown options bogus\n",
        )

    ONE_SEAT = {"roles": {"review backups": [MUSE_SEAT]}}
    SHORTFALL = "review backups: has 1 usable seat and needs 2\n"

    def test_validate_reports_one_usable_seat_and_passes_two(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.ONE_SEAT)
            one = repo.run("validate", "--catalog", catalog)
            repo.put(repo.user, {"roles": {"review backups": [MUSE_SEAT, STEP_SEAT]}})
            two = repo.run("validate", "--catalog", catalog)
        self.assertEqual(one.returncode, 1)
        self.assertEqual(one.stdout, self.SHORTFALL)
        self.assertEqual(one.stderr, "")
        self.assertEqual(two.returncode, 0, two.stderr)
        self.assertEqual(two.stdout, "ok\n")

    def test_validate_and_backup_count_one_usable_seat_for_one_config_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.ONE_SEAT)
            validated = repo.run("validate", "--catalog", catalog)
            backed = in_process.invoke(in_process.Invocation(
                program=ROOT / "t3/scripts/roles.py",
                argv=("backup", "--cwd", str(repo.directory), "--catalog", catalog, *VERIFIER_OUT),
                env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
                stdin=_LIMIT,
            ))
        self.assertEqual(validated.returncode, 1)
        self.assertEqual(validated.stdout, self.SHORTFALL)
        self.assertEqual(backed.returncode, 0, backed.stderr)
        payload = json.loads(backed.stdout)
        self.assertEqual(payload["decision"], "park")
        self.assertEqual(
            payload["report"],
            "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
            "so the work waits for the reset",
        )

    def test_write_refuses_one_usable_seat_without_force(self):
        one_seat = "review backups=opencode/opencode/muse-lite-2-free?variant=high"
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory)
            refused = repo.run("write", "--catalog", catalog, "--set", one_seat)
            written_before_force = repo.user.exists()
            forced = repo.run("write", "--catalog", catalog, "--set", one_seat, "--force")
            stored = json.loads(repo.user.read_text())
            user = str(repo.user)
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, "")
        self.assertEqual(
            refused.stderr,
            "error: refusing to write without --force:\n" + self.SHORTFALL,
        )
        self.assertFalse(written_before_force)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertEqual(forced.stdout, f"wrote {user}\n")
        self.assertEqual(stored["roles"], self.ONE_SEAT["roles"])

    def test_show_notes_one_usable_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, self.ONE_SEAT)
            completed = repo.run("show", "--catalog", catalog, "--role", "review backups")
            user = str(repo.user)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": [MUSE_SEAT],
            "notes": ["has 1 usable seat and needs 2"],
        }})

    def test_show_reports_a_role_set_to_one_excluded_seat_as_set(self):
        excluded = {"providerInstanceId": "grok", "model": "grok-4.7"}
        skipped = [
            "skipped configured seat grok/grok-4.7: grok-4.7 matches exclude entry 1 (grok-4.7), "
            f"and {roles.EXCLUDE_RULE}",
        ]
        excluded_file = {"roles": {"review backups": [excluded]}, "exclude": [{"model": "grok-4.7"}]}
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(directory, excluded_file)
            with_catalog = repo.run("show", "--catalog", catalog, "--role", "review backups")
            plain = repo.run("show", "--role", "review backups")
            user = str(repo.user)
        self.assertEqual(with_catalog.returncode, 0, with_catalog.stderr)
        self.assertEqual(json.loads(with_catalog.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": [],
            "notes": skipped + ["has 0 usable seats and needs 2"],
        }})
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(json.loads(plain.stdout)["roles"], {"review backups": {
            "source": user,
            "seats": "catalog-required",
            "note": (
                "review backups drops seats by the catalog: "
                "call orchestrator_capabilities and rerun roles.py show --catalog"
            ),
            "notes": skipped,
        }})

    def test_validate_reports_a_role_set_to_one_excluded_seat_as_excluded_and_short(self):
        excluded = {"providerInstanceId": "grok", "model": "grok-4.7"}
        excluded_line = (
            "review backups: grok/grok-4.7: grok-4.7 matches exclude entry 1 (grok-4.7), "
            f"and {roles.EXCLUDE_RULE}"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.panel_repo(
                directory, {"roles": {"review backups": [excluded]}, "exclude": [{"model": "grok-4.7"}]}
            )
            validated = repo.run("validate", "--catalog", catalog)
        self.assertEqual(validated.returncode, 1)
        self.assertEqual(validated.stdout, f"{excluded_line}\nreview backups: has 0 usable seats and needs 2\n")
        self.assertEqual(validated.stderr, "")


def plain_provider(provider_id, *model_ids, runs=True):
    return {
        "providerInstanceId": provider_id,
        "canRunChildTask": runs,
        "constraints": [],
        "models": [{"id": model_id, "options": []} for model_id in model_ids],
    }


MUSE_DRIVER_SEAT = {"providerInstanceId": "muse", "model": "muse-spark-1"}


def muse_driver_provider(provider_id="muse", driver="muse"):
    return {
        "providerInstanceId": provider_id,
        "driverKind": driver,
        "canRunChildTask": True,
        "constraints": [],
        "models": [{"id": "muse-spark-1", "options": [], "cost": 0}],
    }


def lacks_mode_note(mode, provider_id="muse"):
    return (
        f"{provider_id} is not runnable (the muse driver lacks runtime mode {mode}); "
        "seat inherits the parent"
    )


class RuntimeModeCliTest(unittest.TestCase):
    def show(self, role, roles_file=None, mode=None, provider=None, catalog_mode=None,
             parent="claudeAgent/claude-opus-5-5"):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = json.loads(CATALOG.read_text())
            catalog["providers"].append(provider or muse_driver_provider())
            if catalog_mode is not None:
                catalog["runtimeMode"] = catalog_mode
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            if roles_file is not None:
                repo.put(repo.user, roles_file)
            flag = () if mode is None else ("--runtime-mode", mode)
            completed = repo.run(
                "show", "--catalog", str(path), "--parent", parent, "--role", role, *flag,
            )
            return completed, str(repo.user)

    def entry(self, role, **kwargs):
        completed, user = self.show(role, **kwargs)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)["roles"][role], user

    BUG_FIX = {"roles": {"bug-fix": [MUSE_DRIVER_SEAT]}}

    def test_auto_replaces_a_configured_seat_on_a_muse_driver_with_inherit(self):
        entry, user = self.entry("bug-fix", roles_file=self.BUG_FIX, mode="auto")
        self.assertEqual(entry, {
            "source": user,
            "seats": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}],
            "info": ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
            "notes": ["muse is not runnable (the muse driver lacks runtime mode auto); seat inherits the parent"],
        })

    def test_auto_accept_edits_and_an_unlisted_mode_replace_the_seat_and_name_the_mode(self):
        for mode in ("auto-accept-edits", "yolo"):
            with self.subTest(mode=mode):
                entry, user = self.entry("bug-fix", roles_file=self.BUG_FIX, mode=mode)
                self.assertEqual(entry, {
                    "source": user,
                    "seats": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}],
                    "info": ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
                    "notes": [lacks_mode_note(mode)],
                })

    def test_supported_modes_and_a_catalog_file_mode_keep_the_seat_without_a_note(self):
        cases = (
            {"mode": "full-access"},
            {"mode": "approval-required"},
            {"catalog_mode": "auto"},
        )
        for case in cases:
            with self.subTest(**case):
                entry, user = self.entry("bug-fix", roles_file=self.BUG_FIX, **case)
                self.assertEqual(entry, {"source": user, "seats": [MUSE_DRIVER_SEAT]})

    def test_the_match_is_on_driver_kind_not_on_the_provider_id(self):
        work_seat = {"providerInstanceId": "work", "model": "muse-spark-1"}
        replaced, user = self.entry(
            "bug-fix",
            roles_file={"roles": {"bug-fix": [work_seat]}},
            mode="auto",
            provider=muse_driver_provider("work", "muse"),
        )
        kept, _user = self.entry(
            "bug-fix",
            roles_file=self.BUG_FIX,
            mode="auto",
            provider=muse_driver_provider("muse", "opencode"),
        )
        self.assertEqual(replaced, {
            "source": user,
            "seats": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}],
            "info": ["inherit made explicit as claudeAgent/claude-opus-5-5 so the small budget applies"],
            "notes": ["work is not runnable (the muse driver lacks runtime mode auto); seat inherits the parent"],
        })
        self.assertEqual(kept["seats"], [MUSE_DRIVER_SEAT])
        self.assertNotIn("notes", kept)

    def test_auto_replaces_the_muse_seat_of_a_panel_in_place(self):
        opus = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        entry, _user = self.entry(
            "arena runners",
            roles_file={"roles": {"arena runners": [MUSE_DRIVER_SEAT, opus]}, "mode": "full"},
            mode="auto",
        )
        self.assertEqual(entry["seats"], ["inherit", opus])

    def test_auto_drops_a_review_backups_seat_on_a_muse_driver(self):
        opus = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        grok = {"providerInstanceId": "grok", "model": "grok-4.7"}
        entry, user = self.entry(
            "review backups",
            roles_file={"roles": {"review backups": [MUSE_DRIVER_SEAT, opus, grok]}},
            mode="auto",
        )
        self.assertEqual(entry, {
            "source": user,
            "seats": [opus, grok],
            "notes": ["dropped muse/muse-spark-1: not runnable or not in the catalog"],
        })

    def test_an_empty_mode_and_a_mode_with_whitespace_exit_2(self):
        for text in ("", "a b"):
            with self.subTest(text=text):
                completed, _user = self.show("bug-fix", roles_file=self.BUG_FIX, mode=text)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"error: --runtime-mode {text!r}: expected runtimeMode from orchestrator_capabilities\n",
                )

    def test_validate_and_write_do_not_take_the_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            for command in ("validate", "write"):
                with self.subTest(command=command):
                    completed = repo.run(command, "--catalog", str(CATALOG), "--runtime-mode", "auto")
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn("--runtime-mode", completed.stderr)
                    self.assertFalse(repo.user.exists())

    MUSE_PARENT = "muse/muse-spark-1"
    WRITTEN_INHERIT = {"roles": {"judgment and prose": ["inherit"]}}

    def assert_refused(self, role, roles_file):
        completed, _user = self.show(role, roles_file=roles_file, mode="auto", parent=self.MUSE_PARENT)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            f"error: role {role!r} cannot inherit muse/muse-spark-1: "
            "muse is not runnable (the muse driver lacks runtime mode auto)\n",
        )

    def test_auto_refuses_a_written_inherit_on_a_muse_driver_parent(self):
        self.assert_refused("judgment and prose", self.WRITTEN_INHERIT)

    def test_auto_refuses_a_configured_muse_driver_seat_on_a_muse_driver_parent(self):
        self.assert_refused("bug-fix", self.BUG_FIX)

    def test_auto_refuses_a_panel_that_holds_inherit_on_a_muse_driver_parent(self):
        opus = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        self.assert_refused("arena runners", {"roles": {"arena runners": ["inherit", opus]}})

    def test_supported_modes_and_a_catalog_file_mode_keep_a_written_inherit_on_a_muse_driver_parent(self):
        cases = (
            {"mode": "full-access"},
            {"mode": "approval-required"},
            {"catalog_mode": "auto"},
        )
        for case in cases:
            with self.subTest(**case):
                entry, user = self.entry(
                    "judgment and prose", roles_file=self.WRITTEN_INHERIT, parent=self.MUSE_PARENT, **case,
                )
                self.assertEqual(entry, {"source": user, "seats": ["inherit"]})

    def test_auto_keeps_a_written_inherit_on_a_claude_parent(self):
        entry, user = self.entry(
            "judgment and prose",
            roles_file={**self.WRITTEN_INHERIT, "mode": "full"},
            mode="auto",
        )
        self.assertEqual(entry, {"source": user, "seats": ["inherit"]})

    def test_validate_accepts_a_written_inherit_on_a_muse_driver_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = json.loads(CATALOG.read_text())
            catalog["providers"].append(muse_driver_provider())
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            repo.put(repo.user, self.WRITTEN_INHERIT)
            completed = repo.run("validate", "--catalog", str(path), "--parent", self.MUSE_PARENT)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "ok\n")


class RuntimeModeBackupCliTest(unittest.TestCase):
    PANEL = {"roles": {"review backups": [MUSE_DRIVER_SEAT, STEP_SEAT, BUNNY_SEAT]}}

    def backup(self, *args, roles_file=None):
        catalog = backup_catalog()
        catalog["providers"].append(muse_driver_provider())
        return run_backup(*args, catalog=catalog, roles_file=roles_file)

    def payload(self, *args, roles_file=None):
        completed = self.backup(*args, roles_file=roles_file)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_auto_drops_a_muse_driver_seat_from_a_three_seat_panel(self):
        payload = self.payload(*VERIFIER_OUT, "--runtime-mode", "auto", roles_file=self.PANEL)
        self.assertEqual(payload, {
            "decision": "panel",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "seats": [STEP_SEAT, BUNNY_SEAT],
            "rule": PANEL_RULE,
            "notes": ["dropped muse/muse-spark-1: not runnable or not in the catalog"],
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
            ),
        })

    def test_auto_parks_a_two_seat_panel_with_a_muse_driver_seat(self):
        payload = self.payload(
            *VERIFIER_OUT, "--runtime-mode", "auto",
            roles_file={"roles": {"review backups": [MUSE_DRIVER_SEAT, STEP_SEAT]}},
        )
        self.assertEqual(payload, {
            "decision": "park",
            "role": "verifiers",
            "failed": "codex/gpt-6.1-sol",
            "report": (
                "verifiers: codex/gpt-6.1-sol hit its usage limit; review backups has 1 usable seat and needs 2, "
                "so the work waits for the reset (dropped muse/muse-spark-1: not runnable or not in the catalog)"
            ),
        })

    def test_auto_resumes_a_bug_fix_seat_on_a_muse_driver_on_opus(self):
        payload = self.payload(
            "--role", "bug-fix", "--provider", "muse", "--model", "muse-spark-1", "--resume",
            "--runtime-mode", "auto",
            roles_file={"backups": {"bug-fix": OPUS_BACKUP}},
        )
        self.assertEqual(payload, {
            "decision": "relaunch",
            "role": "bug-fix",
            "failed": "muse/muse-spark-1",
            "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            "report": "bug-fix: muse/muse-spark-1 resumed on claudeAgent/claude-opus-5-5 at medium after the reset",
        })

    def test_an_empty_mode_and_a_mode_with_whitespace_exit_2(self):
        for text in ("", "a b"):
            with self.subTest(text=text):
                completed = self.backup(*VERIFIER_OUT, "--runtime-mode", text, roles_file=self.PANEL)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"error: --runtime-mode {text!r}: expected runtimeMode from orchestrator_capabilities\n",
                )

    def test_full_access_and_no_flag_keep_the_muse_driver_seat_first_in_a_three_seat_panel(self):
        for flag in (("--runtime-mode", "full-access"), ()):
            with self.subTest(flag=flag):
                payload = self.payload(*VERIFIER_OUT, *flag, roles_file=self.PANEL)
                self.assertEqual(payload, {
                    "decision": "panel",
                    "role": "verifiers",
                    "failed": "codex/gpt-6.1-sol",
                    "seats": [MUSE_DRIVER_SEAT, STEP_SEAT, BUNNY_SEAT],
                    "rule": PANEL_RULE,
                    "report": (
                        "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
                        "so review backups runs 3 seats; land only if no reviewer reproduces a blocker and at least two pass"
                    ),
                })


class RelativeConfigHomeCliTest(unittest.TestCase):
    OPUS = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
    GROK = {"providerInstanceId": "grok", "model": "grok-4.7"}

    def setUp(self):
        wrapper = tempfile.TemporaryDirectory()
        self.addCleanup(wrapper.cleanup)
        root = Path(os.path.realpath(wrapper.name))
        self.home, self.work = root / "home", root / "work"
        self.home.mkdir()
        (self.work / ".git").mkdir(parents=True)
        self.default = self.home / ".config" / "pstack-t3"
        self.earlier = self.work / ".config" / "pstack-t3"
        self.ignored = (
            "XDG_CONFIG_HOME='.config' is not an absolute path, so it is ignored "
            f"and the config home is {str(self.home / '.config')!r}\n"
        )

    def put(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    def run_roles(self, *args):
        env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": ".config"}
        invocation = in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=tuple(args),
            env=env,
            cwd=self.work,
        )
        if args == ("show", "--help"):
            return in_process.finished(in_process.Reason.HELP, invocation)
        return in_process.invoke(invocation)

    def test_show_under_a_relative_xdg_config_home_reads_the_default_roles_file_and_prints_the_ignored_line_once(self):
        self.put(self.default / "roles.json", {"version": 1, "roles": {"bug-fix": [self.OPUS]}})
        self.put(self.default / "catalog.json", json.loads(CATALOG.read_text()))
        self.put(self.earlier / "roles.json", {"version": 1, "roles": {"bug-fix": [self.GROK]}})
        completed = self.run_roles("show", "--cwd", str(self.work), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, self.ignored + NO_PARENT_INFO)
        self.assertEqual(json.loads(completed.stdout), {
            "budget": "default",
            "mode": "light",
            "modeSource": "default",
            "escalate": None,
            "catalog": True,
            "roles": {"bug-fix": {"source": str(self.default / "roles.json"), "seats": [
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            ]}},
        })

    def test_write_under_a_relative_xdg_config_home_writes_under_the_default_config_home_and_leaves_the_working_directory_unchanged(self):
        self.put(self.earlier / "roles.json", {"version": 1, "roles": {"bug-fix": [self.OPUS]}})
        self.put(self.earlier / "catalog.json", {"providers": []})
        kept = {name: (self.earlier / name).read_bytes() for name in ("catalog.json", "roles.json")}
        completed = self.run_roles("write", "--cwd", str(self.work), "--catalog", str(CATALOG), "--set", "bug-fix=grok/grok-4.7")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, f"wrote {self.default / 'roles.json'}\n")
        self.assertEqual(completed.stderr, self.ignored)
        self.assertEqual(
            json.loads((self.default / "roles.json").read_text()),
            {"version": 1, "roles": {"bug-fix": [self.GROK]}, "budget": "default", "mode": "light"},
        )
        self.assertEqual(json.loads((self.default / "catalog.json").read_text()), json.loads(CATALOG.read_text()))
        self.assertEqual(sorted(os.listdir(self.work)), [".config", ".git"])
        self.assertEqual(os.listdir(self.work / ".config"), ["pstack-t3"])
        self.assertEqual({name: (self.earlier / name).read_bytes() for name in sorted(os.listdir(self.earlier))}, kept)

    def test_a_roles_command_that_reads_no_user_config_home_prints_no_ignored_line(self):
        brief = self.work / "brief.md"
        brief.write_text("x\n", encoding="utf-8")
        roles_file = self.work / "roles.json"
        self.put(roles_file, {"version": 1, "roles": {}, "mode": "light"})
        runs = (
            (("check-brief", str(brief)), 1),
            (("show", "--help"), 0),
            (("mode", "--cwd", str(self.work), "--config", str(roles_file)), 0),
        )
        for args, status in runs:
            with self.subTest(args=args):
                completed = self.run_roles(*args)
                self.assertEqual(completed.returncode, status, completed.stdout + completed.stderr)
                self.assertEqual(completed.stderr, "")

    def test_a_refused_show_under_a_relative_xdg_config_home_prints_the_ignored_line_then_the_error_and_exits_2(self):
        file = self.default / "roles.json"
        file.parent.mkdir(parents=True)
        file.write_text("{", encoding="utf-8")
        completed = self.run_roles("show", "--cwd", str(self.work), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, (
            self.ignored
            + f"error: {file}: invalid JSON: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)\n"
        ))


class WriteFilesCliTest(unittest.TestCase):
    def setUp(self):
        wrapper = tempfile.TemporaryDirectory()
        self.addCleanup(wrapper.cleanup)
        self.home = Path(os.path.realpath(wrapper.name))
        (self.home / ".git").mkdir()

    def write(self, config_home):
        env = {**os.environ, "HOME": str(self.home), "XDG_CONFIG_HOME": config_home}
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("write", "--cwd", str(self.home), "--catalog", str(CATALOG), "--set", "bug-fix=grok/grok-4.7"),
            env=env,
            cwd=self.home,
        ))

    def temporary_files(self):
        return sorted(str(path.relative_to(self.home)) for path in self.home.rglob("tmp*"))

    def test_write_under_a_config_home_whose_dot_dot_follows_a_symlink_writes_both_files_in_the_directory_the_system_reaches(self):
        (self.home / "else" / "sub").mkdir(parents=True)
        os.symlink(self.home / "else" / "sub", self.home / "x")
        given = f"{self.home}/x/../.config"
        completed = self.write(given)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, f"wrote {given}/pstack-t3/roles.json\n")
        self.assertEqual(completed.stderr, "")
        reached = self.home / "else" / ".config" / "pstack-t3"
        self.assertEqual(sorted(os.listdir(reached)), ["catalog.json", "roles.json"])
        self.assertEqual(
            json.loads((reached / "roles.json").read_text()),
            {"version": 1, "roles": {"bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}]}, "budget": "default", "mode": "light"},
        )
        self.assertEqual(json.loads((reached / "catalog.json").read_text()), json.loads(CATALOG.read_text()))
        self.assertEqual(self.temporary_files(), [])
        self.assertFalse(os.path.lexists(self.home / ".config"))

    def test_a_write_whose_catalog_json_is_a_directory_leaves_no_temporary_file(self):
        state = self.home / ".config" / "pstack-t3"
        (state / "catalog.json").mkdir(parents=True)
        completed = self.write(str(self.home / ".config"))
        self.assertNotEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(os.listdir(state / "catalog.json"), [])
        self.assertEqual(self.temporary_files(), [])


PICKLE = "opencode/big-pickle"
SPACE_BUNNY = "opencode/space-bunny-free"
GO_STEP = "opencode-go/step-5-preview-free"
DEEPSEEK = "opencode-go/deepseek-v4.1-flash"


def catalog_model(model_id, **fields):
    return {"id": model_id, "options": [], **fields}


def driver_kind_provider(provider_id, driver, model_id, **fields):
    return {
        "providerInstanceId": provider_id,
        "driverKind": driver,
        "canRunChildTask": True,
        "constraints": [],
        "models": [catalog_model(model_id, **fields)],
    }


def bare_seat(provider, model):
    return {"providerInstanceId": provider, "model": model}


def metered_line(role, provider, model):
    return (
        f"{role}: {provider}/{model} is a metered OpenCode model and the allow list does not name {model}. "
        f"Add it with --allow-opencode {model}"
    )


def refusal(role, provider, model):
    return "error: refusing to write without --force:\n" + metered_line(role, provider, model) + "\n"


def dumped(payload):
    return json.dumps(payload, indent=2) + "\n"


def user_document(roles, allow=None, budget="default"):
    document = {"version": 1, "roles": roles, "budget": budget, "mode": "light"}
    if allow is not None:
        document["opencodeAllow"] = allow
    return document


def shown_bug_fix(source, seat, notes=None, mode_source="default"):
    entry = {"source": source, "seats": [seat]}
    if notes is not None:
        entry["notes"] = notes
    return {
        "budget": "default",
        "mode": "light",
        "modeSource": mode_source,
        "escalate": None,
        "catalog": True,
        "roles": {"bug-fix": entry},
    }


def relaunch_bug_fix(notes=None):
    payload = {
        "decision": "relaunch",
        "role": "bug-fix",
        "failed": "codex/gpt-6.1-sol",
        "seat": {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
    }
    if notes is not None:
        payload["notes"] = notes
    payload["report"] = (
        "bug-fix: codex/gpt-6.1-sol hit its usage limit; relaunched on claudeAgent/claude-opus-5-5"
    )
    return payload


def verifier_panel(notes=None):
    payload = {
        "decision": "panel",
        "role": "verifiers",
        "failed": "codex/gpt-6.1-sol",
        "seats": [bare_seat("opencode", PICKLE), bare_seat("opencode-go", GO_STEP)],
        "rule": PANEL_RULE,
    }
    if notes is not None:
        payload["notes"] = notes
    payload["report"] = (
        "verifiers: codex/gpt-6.1-sol hit its usage limit; every paid reviewer backup is out, "
        "so review backups runs 2 seats; land only if no reviewer reproduces a blocker and at least two pass"
    )
    return payload


class OpenCodeAllowCliTest(unittest.TestCase):
    def prepare(self, directory, models, extra=()):
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, {"providers": [
            {
                "providerInstanceId": "opencode",
                "canRunChildTask": True,
                "constraints": [],
                "models": models,
            },
            {
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "claude-opus-5-5", "options": []}],
            },
            {
                "providerInstanceId": "codex",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "gpt-6.1-sol", "options": []}],
            },
            {
                "providerInstanceId": "grok",
                "canRunChildTask": True,
                "constraints": [],
                "models": [
                    {"id": "grok-4.7", "options": []},
                    {"id": "grok-opencode-1", "options": []},
                ],
            },
            *extra,
        ]})
        return repo, str(path)

    def write_bug_fix(self, repo, catalog, provider, model, *flags):
        return repo.run("write", "--catalog", catalog, "--set", f"bug-fix={provider}/{model}", *flags)

    def backup_bug_fix(self, repo, catalog):
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(
                "backup",
                "--cwd", str(repo.directory), "--catalog", catalog,
                "--role", "bug-fix", "--provider", "codex", "--model", "gpt-6.1-sol",
            ),
            env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
            stdin=_LIMIT,
        ))

    def test_write_refuses_unnamed_metered_opencode_seat(self):
        both = [catalog_model(PICKLE), catalog_model(SPACE_BUNNY)]
        cases = (
            ("opencode", PICKLE, both, ()),
            ("opencode", SPACE_BUNNY, both, ()),
            ("muse", "muse-spark-1", [catalog_model(PICKLE)], (driver_kind_provider("muse", "opencode", "muse-spark-1"),)),
        )
        for provider, model, models, extra in cases:
            with self.subTest(provider=provider, model=model):
                with tempfile.TemporaryDirectory() as directory:
                    repo, catalog = self.prepare(directory, models, extra)
                    absent = self.write_bug_fix(repo, catalog, provider, model)
                    self.assertEqual(absent.returncode, 2)
                    self.assertEqual(absent.stdout, "")
                    self.assertEqual(absent.stderr, refusal("bug-fix", provider, model))
                    self.assertFalse(repo.user.exists())
                    prior = b'{"version": 1, "roles": {}}\n'
                    repo.user.parent.mkdir(parents=True, exist_ok=True)
                    repo.user.write_bytes(prior)
                    unchanged = self.write_bug_fix(repo, catalog, provider, model)
                    self.assertEqual(unchanged.returncode, 2)
                    self.assertEqual(unchanged.stdout, "")
                    self.assertEqual(unchanged.stderr, refusal("bug-fix", provider, model))
                    self.assertEqual(repo.user.read_bytes(), prior)
                    forced = self.write_bug_fix(repo, catalog, provider, model, "--force")
                    self.assertEqual(forced.returncode, 0, forced.stderr)
                    self.assertEqual(forced.stdout, f"wrote {repo.user}\n")
                    self.assertEqual(forced.stderr, "")
                    self.assertEqual(repo.user.read_text(), dumped(user_document({
                        "bug-fix": [bare_seat(provider, model)],
                    })))
                    self.assertNotIn("opencodeAllow", repo.user.read_text())

    def test_write_accepts_catalog_free_opencode_model_without_an_allow_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [
                catalog_model(PICKLE, cost=0),
                catalog_model(SPACE_BUNNY, cost=0),
            ])
            completed = repo.run(
                "write", "--catalog", catalog, "--set",
                f"verifiers=opencode/{PICKLE};opencode/{SPACE_BUNNY}",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "verifiers": [bare_seat("opencode", PICKLE), bare_seat("opencode", SPACE_BUNNY)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        accepted = (
            ("float zero", catalog_model(PICKLE, cost=0.0)),
            ("pricing free", catalog_model(PICKLE, pricing="free")),
            ("every present marker free", catalog_model(PICKLE, cost=0, pricing="free", tier="free")),
        )
        for label, entry in accepted:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    repo, catalog = self.prepare(directory, [entry])
                    completed = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(repo.user.read_text(), dumped(user_document({
                        "bug-fix": [bare_seat("opencode", PICKLE)],
                    })))
                    self.assertNotIn("opencodeAllow", repo.user.read_text())
        refused = (
            ("paid tier", catalog_model(PICKLE, cost=0, tier="paid")),
            ("string zero", catalog_model(PICKLE, cost="0")),
            ("capital free", catalog_model(PICKLE, cost="Free")),
            ("false", catalog_model(PICKLE, cost=False)),
        )
        for label, entry in refused:
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    repo, catalog = self.prepare(directory, [entry])
                    completed = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
                    self.assertEqual(completed.returncode, 2)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, refusal("bug-fix", "opencode", PICKLE))
                    self.assertFalse(repo.user.exists())

    def test_allow_list_changes_only_through_allow_flags(self):
        completed = in_process.finished(in_process.Reason.HELP, in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=("write", "--help"),
            env={**os.environ, "COLUMNS": "200"},
        ))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, "")
        self.assertIn(
            "  --allow-opencode ALLOW_OPENCODE\n"
            + (" " * 24)
            + "Add this full OpenCode model id to the allow list in the file being written.\n",
            completed.stdout,
        )
        self.assertIn(
            "  --deny-opencode DENY_OPENCODE\n"
            + (" " * 24)
            + "Remove this full OpenCode model id from the allow list in the file being written.\n",
            completed.stdout,
        )
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE), catalog_model(SPACE_BUNNY)])
            added = repo.run(
                "write", "--catalog", catalog,
                "--set", f"verifiers=opencode/{PICKLE}",
                "--allow-opencode", SPACE_BUNNY,
                "--allow-opencode", PICKLE,
                "--allow-opencode", PICKLE,
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            self.assertEqual(added.stdout, f"wrote {repo.user}\n")
            self.assertEqual(added.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"verifiers": [bare_seat("opencode", PICKLE)]},
                [PICKLE, SPACE_BUNNY],
            )))
            dropped = repo.run("write", "--catalog", catalog, "--replace", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(dropped.returncode, 0, dropped.stderr)
            self.assertEqual(dropped.stdout, f"wrote {repo.user}\n")
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE, SPACE_BUNNY],
            )))
            denied = repo.run("write", "--catalog", catalog, "--keep", "--deny-opencode", SPACE_BUNNY)
            self.assertEqual(denied.returncode, 0, denied.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE],
            )))
            kept = repo.run("write", "--catalog", catalog, "--keep", "--budget", "small")
            self.assertEqual(kept.returncode, 0, kept.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE],
                budget="small",
            )))
            restored = repo.run(
                "write", "--catalog", catalog, "--keep",
                "--deny-opencode", PICKLE, "--allow-opencode", PICKLE,
            )
            self.assertEqual(restored.returncode, 0, restored.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE],
                budget="small",
            )))
            cleared = repo.run("write", "--catalog", catalog, "--keep", "--deny-opencode", PICKLE)
            self.assertEqual(cleared.returncode, 0, cleared.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                budget="small",
            )))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {
                "version": 1,
                "roles": {"nope": ["inherit"]},
                "opencodeAllow": [SPACE_BUNNY, PICKLE, PICKLE],
            })
            copied = repo.run("write", "--catalog", catalog, "--replace", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(copied.returncode, 0, copied.stderr)
            self.assertEqual(copied.stdout, f"wrote {repo.user}\n")
            self.assertEqual(repo.user.read_text(), dumped(user_document(
                {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                [PICKLE, SPACE_BUNNY],
            )))
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": [bare_seat("grok", "grok-4.7")]},
                "opencodeAllow": ["has space"],
            })
            before = repo.user.read_bytes()
            shape = repo.run("write", "--catalog", catalog, "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(shape.returncode, 2)
            self.assertEqual(shape.stdout, "")
            self.assertEqual(
                shape.stderr,
                f"error: {repo.user}: opencodeAllow must be a list of non-empty strings with no spaces\n",
            )
            self.assertEqual(repo.user.read_bytes(), before)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            stored = self.write_bug_fix(repo, catalog, "opencode", PICKLE, "--allow-opencode", PICKLE)
            self.assertEqual(stored.returncode, 0, stored.stderr)
            before = repo.user.read_bytes()
            refused = repo.run("write", "--catalog", catalog, "--keep", "--deny-opencode", PICKLE)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode", PICKLE))
            self.assertEqual(repo.user.read_bytes(), before)
            forced = repo.run(
                "write", "--catalog", catalog, "--keep", "--deny-opencode", PICKLE, "--force",
            )
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(forced.stdout, f"wrote {repo.user}\n")
            self.assertEqual(forced.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode", PICKLE)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.project, {"version": 1, "roles": {}, "opencodeAllow": [PICKLE]})
            project_bytes = repo.project.read_bytes()
            refused = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode", PICKLE))
            self.assertFalse(repo.user.exists())
            self.assertEqual(repo.project.read_bytes(), project_bytes)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {"version": 1, "roles": {}, "opencodeAllow": [PICKLE]})
            user_bytes = repo.user.read_bytes()
            refused = repo.run(
                "write", "--project", "--catalog", catalog, "--set", f"bug-fix=opencode/{PICKLE}",
            )
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode", PICKLE))
            self.assertFalse(repo.project.exists())
            self.assertEqual(repo.user.read_bytes(), user_bytes)

    def test_readers_note_a_metered_opencode_seat_and_do_not_rewrite(self):
        line = metered_line("bug-fix", "opencode", PICKLE)
        seat = bare_seat("opencode", PICKLE)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE), catalog_model(SPACE_BUNNY)])
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [seat]}, "backups": {"bug-fix": OPUS_BACKUP}})
            before = repo.user.read_bytes()
            shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(shown.stderr, NO_PARENT_INFO)
            self.assertEqual(shown.stdout, dumped(shown_bug_fix(str(repo.user), seat, [line])))
            self.assertEqual(repo.user.read_bytes(), before)
            backed = self.backup_bug_fix(repo, catalog)
            self.assertEqual(backed.returncode, 0, backed.stderr)
            self.assertEqual(backed.stderr, "")
            self.assertEqual(backed.stdout, dumped(relaunch_bug_fix([line])))
            self.assertEqual(repo.user.read_bytes(), before)
            validated = repo.run("validate", "--catalog", catalog)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(validated.stderr, "")
            self.assertEqual(validated.stdout, line + "\nok\n")
            self.assertEqual(repo.user.read_bytes(), before)
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [bare_seat("grok", "grok-4.7")]}, "backups": {"bug-fix": OPUS_BACKUP}})
            grok_before = repo.user.read_bytes()
            grok_backup = self.backup_bug_fix(repo, catalog)
            self.assertEqual(grok_backup.returncode, 0, grok_backup.stderr)
            self.assertEqual(grok_backup.stderr, "")
            self.assertEqual(grok_backup.stdout, dumped(relaunch_bug_fix()))
            self.assertNotIn("\n  \"notes\"", grok_backup.stdout)
            self.assertEqual(repo.user.read_bytes(), grok_before)
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [seat]}})
            repo.put(repo.project, {"version": 1, "opencodeAllow": [PICKLE]})
            user_noted = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(user_noted.returncode, 0, user_noted.stderr)
            self.assertEqual(user_noted.stdout, dumped(shown_bug_fix(str(repo.user), seat, [line])))
            repo.put(repo.user, {"version": 1, "roles": {}})
            repo.put(repo.project, {
                "version": 1,
                "roles": {"bug-fix": [seat]},
                "opencodeAllow": [PICKLE],
            })
            project_silent = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(project_silent.returncode, 0, project_silent.stderr)
            self.assertEqual(project_silent.stdout, dumped(shown_bug_fix(str(repo.project), seat)))

    def test_missing_opencode_allow_loads_as_an_empty_list(self):
        line = metered_line("bug-fix", "opencode", PICKLE)
        seat = bare_seat("opencode", PICKLE)
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": [seat]}, "kept": "old"})
            shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(shown.stderr, NO_PARENT_INFO)
            self.assertEqual(shown.stdout, dumped(shown_bug_fix(str(repo.user), seat, [line])))

    def test_non_opencode_write_and_show_stay_the_same(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.prepare(directory, [catalog_model(PICKLE)])
            for model_id in ("grok-4.7", "grok-opencode-1"):
                with self.subTest(model=model_id):
                    completed = self.write_bug_fix(repo, catalog, "grok", model_id)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(repo.user.read_text(), dumped(user_document({
                        "bug-fix": [bare_seat("grok", model_id)],
                    })))
                    self.assertNotIn("opencodeAllow", repo.user.read_text())
                    shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
                    self.assertEqual(shown.returncode, 0, shown.stderr)
                    self.assertEqual(shown.stderr, NO_PARENT_INFO)
                    self.assertEqual(shown.stdout, dumped(shown_bug_fix(
                        str(repo.user), bare_seat("grok", model_id), mode_source=str(repo.user),
                    )))
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [driver_kind_provider("opencode", "muse", PICKLE)]})
            catalog = str(path)
            completed = self.write_bug_fix(repo, catalog, "opencode", PICKLE)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode", PICKLE)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
            shown = repo.run("show", "--catalog", catalog, "--role", "bug-fix")
            self.assertEqual(shown.returncode, 0, shown.stderr)
            self.assertEqual(shown.stderr, NO_PARENT_INFO)
            self.assertEqual(shown.stdout, dumped(shown_bug_fix(
                str(repo.user), bare_seat("opencode", PICKLE), mode_source=str(repo.user),
            )))

    def open_panel_catalog(self, directory, extra=()):
        repo = Repo(directory)
        path = repo.directory / "catalog.json"
        repo.put(path, {"providers": [
            {
                "providerInstanceId": "opencode",
                "driverKind": "opencode",
                "canRunChildTask": True,
                "models": [catalog_model(PICKLE)],
            },
            {
                "providerInstanceId": "opencode-go",
                "driverKind": "opencode",
                "canRunChildTask": True,
                "models": [catalog_model(GO_STEP)],
            },
            *extra,
        ]})
        return repo, str(path)

    def backup_verifiers(self, repo, catalog):
        return in_process.invoke(in_process.Invocation(
            program=ROOT / "t3/scripts/roles.py",
            argv=(
                "backup",
                "--cwd", str(repo.directory), "--catalog", catalog,
                "--role", "verifiers", "--provider", "codex", "--model", "gpt-6.1-sol",
                "--author", "claudeAgent/claude-opus-5-5",
                "--out", "grok", "--out", "claudeAgent",
                "--brief-mode", "light",
            ),
            env={**os.environ, "XDG_CONFIG_HOME": str(repo.directory)},
            stdin=_LIMIT,
        ))

    def test_an_unlisted_metered_panel_gets_the_allow_notes(self):
        seats = [bare_seat("opencode", PICKLE), bare_seat("opencode-go", GO_STEP)]
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.open_panel_catalog(directory)
            repo.put(repo.user, {"version": 1, "roles": {"review backups": seats}})
            repo.put(repo.project, {
                "version": 1,
                "roles": {"verifiers": seats},
                "opencodeAllow": [PICKLE, GO_STEP],
            })
            user_before = repo.user.read_bytes()
            project_before = repo.project.read_bytes()
            completed = self.backup_verifiers(repo, catalog)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout, dumped(verifier_panel([
                metered_line("review backups", "opencode", PICKLE),
                metered_line("review backups", "opencode-go", GO_STEP),
            ])))
            self.assertEqual(repo.user.read_bytes(), user_before)
            self.assertEqual(repo.project.read_bytes(), project_before)

    def test_a_panel_allowed_by_the_file_that_supplied_it_gets_no_notes(self):
        seats = [bare_seat("opencode", PICKLE), bare_seat("opencode-go", GO_STEP)]
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.open_panel_catalog(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {"review backups": seats},
                "opencodeAllow": [PICKLE, GO_STEP],
            })
            repo.put(repo.project, {"version": 1, "roles": {"verifiers": ["inherit"]}})
            user_before = repo.user.read_bytes()
            project_before = repo.project.read_bytes()
            completed = self.backup_verifiers(repo, catalog)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout, dumped(verifier_panel()))
            self.assertNotIn("\n  \"notes\"", completed.stdout)
            self.assertEqual(repo.user.read_bytes(), user_before)
            self.assertEqual(repo.project.read_bytes(), project_before)

    def test_a_worker_backup_does_not_gain_panel_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, catalog = self.open_panel_catalog(directory, (
                {
                    "providerInstanceId": "claudeAgent",
                    "canRunChildTask": True,
                    "models": [catalog_model("claude-opus-5-5")],
                },
                {
                    "providerInstanceId": "codex",
                    "canRunChildTask": True,
                    "models": [catalog_model("gpt-6.1-sol")],
                },
            ))
            repo.put(repo.user, {
                "version": 1,
                "roles": {
                    "review backups": [
                        bare_seat("opencode", PICKLE),
                        bare_seat("opencode-go", GO_STEP),
                    ],
                    "bug-fix": [bare_seat("grok", "grok-4.7")],
                },
                "backups": {"bug-fix": OPUS_BACKUP},
            })
            user_before = repo.user.read_bytes()
            self.assertFalse(repo.project.exists())
            completed = self.backup_bug_fix(repo, catalog)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            self.assertEqual(completed.stdout, dumped(relaunch_bug_fix()))
            self.assertEqual(repo.user.read_bytes(), user_before)
            self.assertFalse(repo.project.exists())

    def test_write_refuses_opencode_go_when_driver_kind_is_missing(self):
        provider = {
            "providerInstanceId": "opencode-go",
            "canRunChildTask": True,
            "models": [catalog_model(DEEPSEEK)],
        }
        seat = f"bug-fix=opencode-go/{DEEPSEEK}"
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            refused = self.write_bug_fix(repo, catalog, "opencode-go", DEEPSEEK)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertFalse(repo.user.exists())
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            refused = repo.run("write", "--project", "--catalog", catalog, "--set", seat)
            self.assertEqual(refused.returncode, 2)
            self.assertEqual(refused.stdout, "")
            self.assertEqual(refused.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertFalse(repo.project.exists())
            self.assertFalse(repo.user.exists())
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            seeded = self.write_bug_fix(repo, catalog, "opencode-go", DEEPSEEK, "--force")
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode-go", DEEPSEEK)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
            before = repo.user.read_bytes()
            kept = repo.run("write", "--catalog", catalog, "--keep", "--budget", "small")
            self.assertEqual(kept.returncode, 2)
            self.assertEqual(kept.stdout, "")
            self.assertEqual(kept.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertEqual(repo.user.read_bytes(), before)
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [provider]})
            catalog = str(path)
            seeded = repo.run("write", "--project", "--catalog", catalog, "--set", seat, "--force")
            self.assertEqual(seeded.returncode, 0, seeded.stderr)
            self.assertEqual(repo.project.read_text(), dumped({
                "version": 1,
                "roles": {"bug-fix": [bare_seat("opencode-go", DEEPSEEK)]},
            }))
            self.assertNotIn("opencodeAllow", repo.project.read_text())
            before = repo.project.read_bytes()
            kept = repo.run(
                "write", "--project", "--catalog", catalog, "--keep", "--budget", "small",
            )
            self.assertEqual(kept.returncode, 2)
            self.assertEqual(kept.stdout, "")
            self.assertEqual(kept.stderr, refusal("bug-fix", "opencode-go", DEEPSEEK))
            self.assertEqual(repo.project.read_bytes(), before)
            self.assertFalse(repo.user.exists())

    def test_a_missing_driver_kind_on_another_id_writes_without_an_allow_list(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [{
                "providerInstanceId": "other",
                "canRunChildTask": True,
                "models": [catalog_model("other/paid")],
            }]})
            completed = self.write_bug_fix(repo, str(path), "other", "other/paid")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("other", "other/paid")],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [{
                "providerInstanceId": "opencode-go",
                "driverKind": "grok",
                "canRunChildTask": True,
                "models": [catalog_model(DEEPSEEK)],
            }]})
            completed = self.write_bug_fix(repo, str(path), "opencode-go", DEEPSEEK)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, f"wrote {repo.user}\n")
            self.assertEqual(completed.stderr, "")
            self.assertEqual(repo.user.read_text(), dumped(user_document({
                "bug-fix": [bare_seat("opencode-go", DEEPSEEK)],
            })))
            self.assertNotIn("opencodeAllow", repo.user.read_text())


class WritePreservationTest(unittest.TestCase):
    def targets(self, repo):
        return (
            (repo.user, ()),
            (repo.directory / "custom.json", ("--config", str(repo.directory / "custom.json"))),
            (repo.project, ("--project",)),
        )

    def test_overlay_keeps_unnamed_settings_in_any_project(self):
        for target_index in range(3):
            for budget_flags in ((), ("--budget", "small")):
                for keep_flags in ((), ("--keep",)):
                    with self.subTest(target=target_index, budget=budget_flags, keep=keep_flags):
                        with tempfile.TemporaryDirectory() as directory:
                            repo = Repo(directory)
                            target, target_flags = self.targets(repo)[target_index]
                            seed = {
                                "version": 1,
                                "roles": {
                                    "bug-fix": ["inherit"],
                                    "hillclimb": ["inherit"],
                                    "arena runners": ["inherit", "inherit"],
                                },
                                "budget": "large",
                                "mode": "light",
                                "escalate": ["**/migrations/**"],
                                "opencodeAllow": [],
                                "custom": {"nested": [None, False, "seat"]},
                            }
                            repo.put(target, seed)
                            completed = repo.write(
                                *target_flags, *budget_flags, *keep_flags,
                                "--set", "bug-fix=grok/grok-4.7",
                                "--set", "arena runners=claudeAgent/claude-opus-5-5",
                            )
                            self.assertEqual(completed.returncode, 0, completed.stderr)
                            expected = {**seed, "roles": {**seed["roles"],
                                "bug-fix": [{"providerInstanceId": "grok", "model": "grok-4.7"}],
                                "arena runners": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
                            }}
                            if budget_flags:
                                expected["budget"] = "small"
                            self.assertEqual(json.loads(target.read_text()), expected)
                            self.assertFalse(target.with_name(target.name + ".bak").exists())
                            snapshot = repo.user.with_name("catalog.json")
                            self.assertEqual(snapshot.exists(), target_index == 0)
                            if snapshot.exists():
                                self.assertEqual(json.loads(snapshot.read_text()), json.loads(CATALOG.read_text()))

    def test_null_settings_are_refused_until_a_flag_names_them(self):
        for target_index in range(3):
            for key, valid, exit_code in (("budget", "large", 2), ("mode", "light", 1)):
                with self.subTest(target=target_index, key=key):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        target, target_flags = self.targets(repo)[target_index]
                        repo.put(target, {"roles": {}, key: None})
                        before = target.read_bytes()
                        completed = repo.write(*target_flags)
                        self.assertEqual(completed.returncode, exit_code, completed.stderr)
                        self.assertIn(str(target), completed.stderr)
                        self.assertEqual(target.read_bytes(), before)
                        self.assertFalse(target.with_name(target.name + ".bak").exists())
                        repaired = repo.write(*target_flags, "--" + key, valid)
                        self.assertEqual(repaired.returncode, 0, repaired.stderr)
                        self.assertEqual(json.loads(target.read_text())[key], valid)

    def test_malformed_roles_are_refused_even_with_replace(self):
        for target_index in range(3):
            for value in ([], None, "seat", 1, True):
                for flags in ((), ("--keep",), ("--replace",), ("--replace", "--force")):
                    with self.subTest(target=target_index, value=value, flags=flags):
                        with tempfile.TemporaryDirectory() as directory:
                            repo = Repo(directory)
                            target, target_flags = self.targets(repo)[target_index]
                            repo.put(target, {"roles": value, "custom": "keep"})
                            before = target.read_bytes()
                            completed = repo.write(*target_flags, *flags)
                            self.assertEqual(completed.returncode, 2, completed.stderr)
                            self.assertIn(f"{target}: roles must be an object", completed.stderr)
                            self.assertEqual(target.read_bytes(), before)
                            self.assertFalse(target.with_name(target.name + ".bak").exists())
                            self.assertFalse(repo.user.with_name("catalog.json").exists())

    def test_unreadable_json_is_refused_for_every_target_and_replace(self):
        for target_index in range(3):
            for content in (b"{not json", b"\xff\xfe"):
                for flags in ((), ("--replace",)):
                    with self.subTest(target=target_index, content=content, flags=flags):
                        with tempfile.TemporaryDirectory() as directory:
                            repo = Repo(directory)
                            target, target_flags = self.targets(repo)[target_index]
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(content)
                            completed = repo.write(*target_flags, *flags)
                            self.assertEqual(completed.returncode, 2, completed.stderr)
                            self.assertIn(f"{target}: invalid JSON:", completed.stderr)
                            self.assertEqual(target.read_bytes(), content)
                            self.assertFalse(target.with_name(target.name + ".bak").exists())

    def test_backup_only_for_replace_and_only_for_existing_targets(self):
        for target_index in range(3):
            for flags in ((), ("--keep",), ("--replace",)):
                with self.subTest(target=target_index, flags=flags):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        target, target_flags = self.targets(repo)[target_index]
                        first = repo.write(*target_flags, *flags)
                        self.assertEqual(first.returncode, 0, first.stderr)
                        backup = target.with_name(target.name + ".bak")
                        self.assertFalse(backup.exists())
                        before = target.read_bytes()
                        second = repo.write(*target_flags, *flags, "--budget", "small")
                        self.assertEqual(second.returncode, 0, second.stderr)
                        self.assertEqual(backup.exists(), "--replace" in flags)
                        if backup.exists():
                            self.assertEqual(backup.read_bytes(), before)


def fixture_catalog():
    return json.loads(CATALOG.read_text())


class ExcludedParentCliTest(unittest.TestCase):
    """B1. An unset role, and a role whose every seat is excluded, settle a refused parent against the catalog."""

    def show(self, roles_file, *, catalog=None, parent="claudeAgent/claude-opus-5-5"):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog() if catalog is None else catalog)
            repo.put(repo.user, roles_file)
            return repo.run("show", "--catalog", str(path), "--parent", parent, "--role", "bug-fix")

    def test_an_unset_role_replaces_an_excluded_parent_with_the_first_allowed_model(self):
        completed = self.show({"version": 1, "roles": {}, "exclude": [{"model": "claude-opus-5-5"}]})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"})
        self.assertNotIn("note", entry)
        self.assertTrue(
            any(note.startswith("inherit replaced by claudeAgent/claude-haiku-4-5") for note in entry.get("notes", [])),
            entry,
        )

    def test_a_role_whose_every_seat_is_excluded_replaces_the_refused_parent(self):
        completed = self.show({
            "version": 1,
            "roles": {"bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
            "exclude": [{"model": "claude-opus-5-5"}],
        })
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertNotEqual(entry["seats"], "inherit")
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"})
        notes = entry.get("notes", [])
        self.assertTrue(any(note.startswith("skipped configured seat claudeAgent/claude-opus-5-5") for note in notes), entry)
        self.assertTrue(
            any(note.startswith("inherit replaced by claudeAgent/claude-haiku-4-5") for note in notes),
            entry,
        )

    def test_a_provider_with_no_allowed_model_refuses_the_role(self):
        catalog = {"providers": [{
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-opus-5-5", "options": []}],
        }]}
        completed = self.show(
            {"version": 1, "roles": {}, "exclude": [{"model": "claude-opus-5-5"}]},
            catalog=catalog,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("has no model the exclude list allows", completed.stderr)
        self.assertIn(roles.EXCLUDE_RULE, completed.stderr)


B2_CONFIG = {
    "version": 1,
    "budget": "small",
    "roles": {"bug-fix": [{
        "providerInstanceId": "claudeAgent",
        "model": "claude-opus-5-5",
        "options": {"effort": "xhigh"},
    }]},
    "exclude": [{"options": {"effort": "medium"}}],
}


class ExcludeFinalTargetCliTest(unittest.TestCase):
    """B2. The exclude list is checked on the final target, after option mapping and the budget cap. The accepted `--resume` original-seat path is the exception."""

    def show(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, B2_CONFIG)
            completed = repo.run(
                "show", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5", "--role", "bug-fix",
            )
        return completed

    def test_show_refuses_a_budget_capped_seat_the_exclude_list_names(self):
        completed = self.show()
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("exclude", completed.stderr)
        self.assertIn(roles.EXCLUDE_RULE, completed.stderr)

    def test_validate_lists_a_budget_capped_seat_the_exclude_list_names(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, B2_CONFIG)
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stderr, "")
        self.assertIn("bug-fix: claudeAgent/claude-opus-5-5", completed.stdout)
        self.assertIn(roles.EXCLUDE_RULE, completed.stdout)


def b2_next_candidate_catalog():
    """backup_catalog with claude-sonnet-5-5 declaring no effort option."""
    catalog = backup_catalog()
    claude = next(provider for provider in catalog["providers"] if provider["providerInstanceId"] == "claudeAgent")
    for model in claude["models"]:
        if model["id"] == "claude-sonnet-5-5":
            model["options"] = []
    return catalog


class ExcludeBackupTargetCliTest(unittest.TestCase):
    """B2. A refused backup candidate lets the loop try the next one."""

    def test_backup_parks_when_the_final_target_the_failed_level_produces_is_refused(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--brief-mode", "full",
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
                "exclude": [{"options": {"effort": "high"}}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "park")
        self.assertNotIn("seat", payload)

    def test_backup_tries_the_next_candidate_when_the_first_is_refused(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            "--brief-mode", "full",
            catalog=b2_next_candidate_catalog(),
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [
                    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                    {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"},
                ]},
                "exclude": [{"options": {"effort": "high"}}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(payload["seat"], {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"})

    def test_backup_skips_an_excluded_first_candidate_and_relaunches_on_an_allowed_second(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [
                    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                    {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                ]},
                "exclude": [{"model": "claude-opus-5-5"}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(
            payload["seat"],
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
        )


class BackupUnavailableCandidateCliTest(unittest.TestCase):
    """T951. The loop skips a backups candidate the catalog lacks, and validate still names it."""

    def test_backup_skips_a_first_candidate_the_catalog_lacks_and_relaunches_on_the_second(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            roles_file={"version": 1, "backups": {"bug-fix": [
                {"providerInstanceId": "kilo", "model": "kilo-1"},
                {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
            ]}},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(
            payload["seat"],
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
        )

    def test_validate_names_a_backups_seat_the_catalog_lacks(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, backup_catalog())
            repo.put(repo.user, {"version": 1, "roles": {}, "backups": {"bug-fix": [
                {"providerInstanceId": "kilo", "model": "kilo-1"},
            ]}})
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("backups.bug-fix: kilo/kilo-1: not runnable or not in the catalog", completed.stdout)


MALFORMED_BACKUP_SEATS = (
    {"providerInstanceId": ["claudeAgent"], "model": "claude-opus-5-5"},
    {"providerInstanceId": "claudeAgent", "model": ["claude-opus-5-5"]},
    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": ["effort"]},
    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": ["low"]}},
)


class BackupSeatShapeCliTest(unittest.TestCase):
    """B3. A malformed backups seat is refused by name, never with a traceback."""

    def test_each_malformed_backups_seat_shape_in_the_table_is_refused_without_a_traceback(self):
        for command in ("validate", "show"):
            for seat in MALFORMED_BACKUP_SEATS:
                with self.subTest(command=command, seat=seat):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        path = repo.directory / "catalog.json"
                        repo.put(path, fixture_catalog())
                        repo.put(repo.user, {"version": 1, "roles": {}, "backups": {"bug-fix": [seat]}})
                        completed = repo.run(
                            command, "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5",
                        )
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertIn("backups.bug-fix", completed.stderr)
                    self.assertNotIn("Traceback", completed.stdout)
                    self.assertNotIn("Traceback", completed.stderr)

    def test_write_force_refuses_a_malformed_backups_seat_and_leaves_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {
                "version": 1,
                "roles": {},
                "backups": {"bug-fix": [{"providerInstanceId": ["claudeAgent"], "model": "claude-opus-5-5"}]},
            })
            before = repo.user.read_bytes()
            completed = repo.write("--force", "--set", "bug-fix=grok/grok-4.7")
            self.assertEqual(repo.user.read_bytes(), before)
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertIn("backups.bug-fix", completed.stderr)

    def test_validate_names_a_backups_option_the_catalog_does_not_offer(self):
        for label, options, named in (
            ("unknown id", {"typo": "xhigh"}, "typo"),
            ("bad select value", {"effort": "banana"}, "effort"),
        ):
            with self.subTest(label=label):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    path = repo.directory / "catalog.json"
                    repo.put(path, fixture_catalog())
                    repo.put(repo.user, {"version": 1, "roles": {}, "backups": {"bug-fix": [{
                        "providerInstanceId": "claudeAgent",
                        "model": "claude-opus-5-5",
                        "options": options,
                    }]}})
                    completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
                self.assertEqual(completed.returncode, 1, completed.stderr)
                line = next(
                    (line for line in completed.stdout.splitlines() if line.startswith("backups.bug-fix:")), None,
                )
                self.assertIsNotNone(line, completed.stdout)
                self.assertIn(named, line)

    def test_backup_refuses_when_its_own_backups_entry_has_a_bad_option(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "grok", "--model", "grok-4.7",
            roles_file={"version": 1, "backups": {"bug-fix": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-opus-5-5",
                "options": {"typo": "xhigh"},
            }]}},
        )
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertEqual(completed.stdout, "")
        self.assertIn("backups.bug-fix", completed.stderr)


class ExcludeOptionCliTest(unittest.TestCase):
    """B4. A validate-only check reports an options pair no named catalog model can accept."""

    def validate(self, rule):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, {"version": 1, "roles": {}, "exclude": [rule]})
            return repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")

    def test_validate_reports_an_exclude_options_pair_the_catalog_cannot_accept(self):
        for label, rule, named in (
            ("unknown id", {"options": {"typo": "high"}}, "typo"),
            ("bad select value", {"options": {"effort": "banana"}}, "effort"),
            ("wrong type", {"model": "claude-opus-5-5", "options": {"effort": True}}, "effort"),
        ):
            with self.subTest(label=label):
                completed = self.validate(rule)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                line = next(
                    (line for line in completed.stdout.splitlines() if "exclude entry 1" in line), None,
                )
                self.assertIsNotNone(line, completed.stdout)
                self.assertIn(named, line)

    def test_a_rule_whose_provider_glob_matches_no_catalog_model_is_not_checked(self):
        completed = self.validate({"provider": "gemini*", "options": {"effort": "high"}})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("ok", completed.stdout)


class ExcludeNarrowingCliTest(unittest.TestCase):
    """B6. A provider glob and a roles list narrow a rule and AND with its model and options."""

    CODEX = {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}
    GROK = {"providerInstanceId": "grok", "model": "grok-4.7"}

    def show(self, roles_file, role):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, roles_file)
            return repo.run("show", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5", "--role", role)

    def test_a_roles_narrowed_rule_refuses_only_the_named_role(self):
        roles_file = {
            "version": 1,
            "roles": {"review backups": [self.CODEX], "bug-fix": [self.CODEX]},
            "exclude": [{"provider": "codex", "roles": ["review backups"]}],
        }
        panel = self.show(roles_file, "review backups")
        worker = self.show(roles_file, "bug-fix")
        self.assertEqual(panel.returncode, 0, panel.stderr)
        panel_entry = json.loads(panel.stdout)["roles"]["review backups"]
        self.assertEqual(panel_entry["seats"], [])
        self.assertTrue(any("codex/gpt-6.1-sol" in note for note in panel_entry.get("notes", [])), panel_entry)
        self.assertEqual(worker.returncode, 0, worker.stderr)
        self.assertEqual(json.loads(worker.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "codex",
            "model": "gpt-6.1-sol",
            "options": {"reasoningEffort": "medium"},
        }])

    def test_a_provider_glob_refuses_a_seat_in_every_role(self):
        roles_file = {
            "version": 1,
            "roles": {"bug-fix": [self.CODEX], "review backups": [self.GROK, self.CODEX]},
            "exclude": [{"provider": "codex*"}],
        }
        worker = self.show(roles_file, "bug-fix")
        panel = self.show(roles_file, "review backups")
        self.assertEqual(worker.returncode, 0, worker.stderr)
        worker_entry = json.loads(worker.stdout)["roles"]["bug-fix"]
        self.assertEqual(worker_entry["seats"], "inherit")
        self.assertTrue(
            any("skipped configured seat codex/gpt-6.1-sol" in note for note in worker_entry.get("notes", [])),
            worker_entry,
        )
        self.assertEqual(panel.returncode, 0, panel.stderr)
        panel_entry = json.loads(panel.stdout)["roles"]["review backups"]
        self.assertEqual(panel_entry["seats"], [self.GROK])
        self.assertTrue(any("codex/gpt-6.1-sol" in note for note in panel_entry.get("notes", [])), panel_entry)


class BackupCandidateOptionsCliTest(unittest.TestCase):
    """B5. A backup candidate keeps its declared options and takes the failed level only when it names no effort level."""

    def test_a_candidate_keeps_its_options_and_a_bare_candidate_takes_the_failed_level(self):
        cases = (
            (
                "declared options",
                ("--provider", "claudeAgent", "--model", "claude-opus-5-5", "--options", '{"effort": "xhigh"}'),
                {"providerInstanceId": "grok", "model": "grok-4.7",
                 "options": {"reasoningEffort": "low", "fastMode": False}},
                {"providerInstanceId": "grok", "model": "grok-4.7",
                 "options": {"reasoningEffort": "low", "fastMode": False}},
            ),
            (
                "no options",
                ("--provider", "grok", "--model", "grok-4.7", "--options", '{"reasoningEffort": "medium"}'),
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
            ),
            (
                "declared special effort",
                ("--provider", "grok", "--model", "grok-4.7", "--options", '{"reasoningEffort": "high"}'),
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
                 "options": {"effort": "ultrathink"}},
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
                 "options": {"effort": "medium"}},
            ),
        )
        for label, failed_flags, candidate, expected in cases:
            with self.subTest(label=label):
                completed = run_backup(
                    "--role", "bug-fix", *failed_flags,
                    roles_file={"version": 1, "backups": {"bug-fix": [candidate]}},
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["decision"], "relaunch")
                self.assertEqual(payload["seat"], expected)


MIGRATION_JSON = {
    "exclude": [
        {"model": "grok*fast*"},
        {"model": "grok*", "options": {"fastMode": True}},
        {"model": "*claude-haiku-4-5*"},
        {"provider": "codex", "roles": ["review backups"]},
    ],
    "backups": {
        "feature, refactoring": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "perf-issue": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "hillclimb": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "judgment and prose": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "hardest tasks": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "how explorer": [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}],
        "how explainer": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "why investigators": [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}],
        "why synthesizer": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "reflect tooling": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "reflect judgment, divergent, synthesizer": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "swarm workers": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "skill tests": [{"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"}],
        "arena runners": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "arena cross-judge pool": [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "architect runners": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "interrogate reviewers": [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
        "verifiers": [{"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}],
    },
}
LIMIT = "You've hit your usage limit. Try again later.\n"
MIGRATION_CATALOG = {"providers": [
    plain_provider("claudeAgent", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"),
    {
        "providerInstanceId": "grok",
        "canRunChildTask": True,
        "constraints": [],
        "models": [
            {"id": "grok-4.7", "options": [{"id": "fastMode", "type": "boolean"}]},
            {"id": "grok-4.7-build-fast", "options": []},
        ],
    },
    plain_provider("codex", "gpt-6.1-sol"),
    {"providerInstanceId": "cursor", "driverKind": "cursor", "canRunChildTask": True, "constraints": [],
     "models": [{"id": "default", "options": []}]},
]}


class HouseRuleMigrationCliTest(unittest.TestCase):
    """The owner's house-rule JSON reproduces the four picks, the three refusals, and the CLI paths this test names."""

    def setUp(self):
        self.exclude = roles.parse_exclude(MIGRATION_JSON["exclude"], "test")
        self.backups = roles.parse_backups(MIGRATION_JSON["backups"], "test")

    def show(self, *args, roles_file):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, MIGRATION_CATALOG)
            repo.put(repo.user, roles_file)
            return repo.run("show", "--catalog", str(path), *args)

    def validate(self, roles_file):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, MIGRATION_CATALOG)
            repo.put(repo.user, roles_file)
            return repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")

    def exclude_roles_file(self, roles):
        return {"version": 1, "roles": roles, "exclude": MIGRATION_JSON["exclude"]}

    def backup(self, role, failed, authors=()):
        return roles.backup_seat(
            role, failed, LIMIT, MIGRATION_CATALOG, "default", [], list(authors),
            backups=self.backups, exclude=self.exclude, panel_budget="default",
        )

    def test_the_exclude_and_backups_keys_reproduce_the_removed_builtin_picks(self):
        worker = self.backup("bug-fix", {"provider": "codex", "model": "gpt-6.1-sol"})
        self.assertEqual(worker.decision, "relaunch")
        self.assertEqual(worker.seat, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})

        tests = self.backup("skill tests", {"provider": "grok", "model": "grok-4.7"})
        self.assertEqual(tests.decision, "relaunch")
        self.assertEqual(tests.seat, {"providerInstanceId": "claudeAgent", "model": "claude-sonnet-5-5"})

        grok_author = self.backup(
            "verifiers", {"provider": "codex", "model": "gpt-6.1-sol"}, authors=("grok/grok-4.7",)
        )
        self.assertEqual(grok_author.decision, "relaunch")
        self.assertEqual(grok_author.seat, {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})

        claude_author = self.backup(
            "verifiers", {"provider": "codex", "model": "gpt-6.1-sol"}, authors=("claudeAgent/claude-opus-5-5",)
        )
        self.assertEqual(claude_author.decision, "relaunch")
        self.assertEqual(
            claude_author.seat,
            {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}},
        )

        for seat in (worker.seat, tests.seat, grok_author.seat, claude_author.seat):
            self.assertNotIn(seat["providerInstanceId"], ("codex", "cursor"))

        for seat in (
            {"providerInstanceId": "grok", "model": "grok-4.7-build-fast"},
            {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": True}},
            {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"},
        ):
            with self.subTest(seat=seat):
                self.assertIsNotNone(roles.exclude_reason(seat, self.exclude))

    def test_the_three_reviewer_backup_lists_carry_the_grok_false_pin(self):
        expected = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}
        for role in ("arena cross-judge pool", "interrogate reviewers", "verifiers"):
            with self.subTest(role=role):
                result = self.backup(
                    role, {"provider": "codex", "model": "gpt-6.1-sol"},
                    authors=("claudeAgent/claude-opus-5-5",),
                )
                self.assertEqual(result.decision, "relaunch")
                self.assertEqual(result.seat, expected)

    def test_show_keeps_a_grok_seat_that_declares_fast_mode_false(self):
        seat = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}
        completed = self.show(
            "--parent", "claudeAgent/claude-opus-5-5", "--role", "bug-fix",
            roles_file=self.exclude_roles_file({"bug-fix": [seat]}),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], [seat])

    def test_show_keeps_a_claude_seat_that_declares_fast_mode_true(self):
        seat = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"fastMode": True}}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, self.exclude_roles_file({"bug-fix": [seat]}))
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["roles"]["bug-fix"]["seats"],
            [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5",
              "options": {"fastMode": True, "effort": "medium"}}],
        )

    def test_show_drops_only_the_codex_review_backups_seat(self):
        codex = {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}
        grok = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"fastMode": False}}
        claude = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        completed = self.show(
            "--parent", "claudeAgent/claude-opus-5-5", "--role", "review backups",
            roles_file=self.exclude_roles_file({"review backups": [codex, grok, claude]}),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["review backups"]
        self.assertEqual(entry["seats"], [grok, claude])
        self.assertTrue(any("codex/gpt-6.1-sol" in note for note in entry.get("notes", [])), entry)

    def test_validate_lists_the_codex_review_backups_seat(self):
        codex = {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}
        completed = self.validate(self.exclude_roles_file({"review backups": [codex]}))
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertTrue(
            any(
                line.startswith("review backups: codex/gpt-6.1-sol:") and roles.EXCLUDE_RULE in line
                for line in completed.stdout.splitlines()
            ),
            completed.stdout,
        )

    def test_launches_seats_still_refuses_a_seat_that_resolves_to_a_cursor_parent(self):
        completed = self.show(
            "--parent", "cursor/default", "--role", "skill tests", "--launches-seats",
            roles_file=self.exclude_roles_file({"skill tests": ["inherit"]}),
        )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("cannot launch seats", completed.stderr)

    def test_show_replaces_a_haiku_4_5_parent_the_exclude_list_refuses(self):
        completed = self.show(
            "--parent", "claudeAgent/claude-haiku-4-5", "--role", "bug-fix",
            roles_file=self.exclude_roles_file({}),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})
        self.assertTrue(
            any(note.startswith("inherit replaced by claudeAgent/claude-opus-5-5") for note in entry.get("notes", [])),
            entry,
        )

    def test_backup_relaunches_on_the_backups_list_not_on_codex(self):
        completed = run_backup(
            "--role", "bug-fix", "--provider", "codex", "--model", "gpt-6.1-sol",
            catalog=MIGRATION_CATALOG,
            roles_file={"version": 1, "backups": MIGRATION_JSON["backups"], "exclude": MIGRATION_JSON["exclude"]},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertEqual(payload["seat"], {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"})


class ExcludeRefusalOwnerCliTest(unittest.TestCase):
    """One owner decides the exclude refusal, so resolve, inherit, panel, backup, and validate refuse the same seats.

    The clause is "<bare id> matches exclude entry <n> (<label>), and <EXCLUDE_RULE>".
    resolve, inherit, panel, and validate report it. backup skips a refused candidate and relaunches on the next allowed one.
    """

    OPUS = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
    CLAUSE = f"claude-opus-5-5 matches exclude entry 1 (claude-opus-5-5), and {roles.EXCLUDE_RULE}"

    def show(self, roles_file, *extra):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, roles_file)
            return repo.run(
                "show", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5", *extra,
            )

    def validate(self, roles_file):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, roles_file)
            return repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")

    def test_resolve_refuses_a_final_target_with_the_owner_clause(self):
        completed = self.show({
            "version": 1,
            "budget": "small",
            "roles": {"bug-fix": [{**self.OPUS, "options": {"effort": "xhigh"}}]},
            "exclude": [{"options": {"effort": "medium"}}],
        }, "--role", "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: claudeAgent/claude-opus-5-5: "
            f"claude-opus-5-5 matches exclude entry 1 (effort=medium), and {roles.EXCLUDE_RULE}\n",
        )

    def test_inherit_reports_the_replacement_with_the_owner_clause(self):
        completed = self.show({
            "version": 1,
            "roles": {},
            "exclude": [{"model": "claude-opus-5-5"}],
        }, "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"})
        self.assertEqual(entry["notes"], [f"inherit replaced by claudeAgent/claude-haiku-4-5: {self.CLAUSE}"])

    def test_panel_drops_a_refused_backups_seat_with_the_owner_clause(self):
        exclude = roles.parse_exclude([{"model": "claude-opus-5-5"}], "test")
        verdicts = roles.panel_verdicts([self.OPUS], fixture_catalog(), "default", exclude=exclude)
        self.assertEqual(verdicts[0].reason, self.CLAUSE)
        self.assertEqual(verdicts[0].notes, (f"dropped claudeAgent/claude-opus-5-5: {self.CLAUSE}",))

    def test_backup_skips_an_excluded_first_candidate_and_relaunches_on_the_allowed_second(self):
        completed = run_backup(
            "--role", "bug-fix",
            "--provider", "grok",
            "--model", "grok-4.7",
            "--options", '{"reasoningEffort": "high"}',
            roles_file={
                "version": 1,
                "backups": {"bug-fix": [self.OPUS, {"providerInstanceId": "codex", "model": "gpt-6.1-sol"}]},
                "exclude": [{"model": "claude-opus-5-5"}],
            },
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["decision"], "relaunch")
        self.assertNotEqual(payload["seat"]["model"], "claude-opus-5-5")
        self.assertEqual(
            payload["seat"],
            {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
        )

    def test_validate_lists_a_refused_backups_seat_with_the_owner_clause(self):
        completed = self.validate({
            "version": 1,
            "roles": {"bug-fix": [{"providerInstanceId": "codex", "model": "gpt-6.1-sol"}]},
            "backups": {"bug-fix": [self.OPUS]},
            "exclude": [{"model": "claude-opus-5-5"}],
        })
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(
            completed.stdout,
            f"backups.bug-fix: claudeAgent/claude-opus-5-5: {self.CLAUSE}\n",
        )

    def test_validate_lists_a_refused_configured_seat_with_the_owner_clause(self):
        completed = self.validate({
            "version": 1,
            "roles": {"bug-fix": [self.OPUS]},
            "exclude": [{"model": "claude-opus-5-5"}],
        })
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stdout, f"bug-fix: claudeAgent/claude-opus-5-5: {self.CLAUSE}\n")

    def test_settle_without_a_catalog_names_the_owner_clause_for_a_refused_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"version": 1, "roles": {"bug-fix": ["inherit"]}, "exclude": [{"model": "claude-opus-5-5"}]})
            completed = repo.run("show", "--parent", "claudeAgent/claude-opus-5-5", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["roles"]["bug-fix"]["note"],
            "the parent claudeAgent/claude-opus-5-5 claude-opus-5-5 matches exclude entry 1 (claude-opus-5-5), "
            f"and {roles.EXCLUDE_RULE}, so inherit needs the catalog: "
            "call orchestrator_capabilities and rerun roles.py show --catalog",
        )


class ExcludeUnionCliTest(unittest.TestCase):
    """T967. The merged exclude list is the union of the user and project lists."""

    def test_user_exclude_stays_excluded_when_project_has_own_exclude_list(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, fixture_catalog())
            repo.put(repo.user, {
                "version": 1,
                "roles": {"bug-fix": [{"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}]},
                "exclude": [{"model": "claude-opus-5-5"}],
            })
            repo.put(repo.project, {"exclude": [{"model": "grok-4.7"}]})
            config = roles.merged_config(str(repo.directory), user_path=repo.user, project_path=repo.project)
            completed = repo.run("validate", "--catalog", str(path), "--parent", "claudeAgent/claude-opus-5-5")
        self.assertEqual(config["exclude"], ({"model": "claude-opus-5-5"}, {"model": "grok-4.7"}))
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            "bug-fix: claudeAgent/claude-opus-5-5: "
            f"claude-opus-5-5 matches exclude entry 1 (claude-opus-5-5), and {roles.EXCLUDE_RULE}\n",
        )
