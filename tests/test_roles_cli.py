import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "t3/scripts"))
import roles  # noqa: E402

GROK_SEAT = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "xhigh"}}
OPUS_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "xhigh"}}
GROK_MEDIUM = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}}
OPUS_MEDIUM = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}}
HAIKU_5_MEDIUM = {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5", "options": {"effort": "medium"}}
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


def show(*role_names):
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "XDG_CONFIG_HOME": directory}
        argv = [
            sys.executable,
            str(ROOT / "t3/scripts/roles.py"),
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
        return subprocess.run(argv, env=env, capture_output=True, text=True)


class RolesCliTest(unittest.TestCase):
    def test_one_role_resolves_that_role(self):
        completed = show("bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])

    def test_two_roles_resolve_both(self):
        completed = show("bug-fix", "judgment and prose")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix", "judgment and prose"])
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])
        self.assertEqual(payload["roles"]["judgment and prose"]["seats"], [OPUS_SEAT])

    def test_repeated_identical_role_resolves_once(self):
        completed = show("bug-fix", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])


D25_BRIEF = """\
Act as the implementation sub-agent for this task.

Implement the pstack-t3 change described in /tmp/d25-architect/IMPL.md. Read that file, /tmp/d25-architect/SYNTHESIS.md, and /tmp/d25-architect/c3-sol/SKETCH.md before you edit. The synthesis wins where the sketch differs.

Work only in /home/marcus/.t3/worktrees/pstack-t3/pstack-t3-d25 on branch pstack-t3/d25. Do not create a worktree. Do not merge, rebase, push, or open a pull request. Do not edit README.md or CHANGELOG.md. Do not hand-edit skills/ or vendor/pstack. Do not spawn agents.

Two green commits, in order. First the roles defaults and the setup watch gate, with tests. Then scripts/sync_upstream.py and the override ports. Run tests and builds only through python3 /home/marcus/Projects/pstack-t3/skills/landing/scripts/land.py slot -- <command>.

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
    return subprocess.run(
        [sys.executable, str(ROOT / "t3/scripts/roles.py"), "check-brief", str(path)],
        capture_output=True,
        text=True,
    )


def mode_output(*args):
    with tempfile.TemporaryDirectory() as directory:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "t3/scripts/roles.py"), "mode", "--cwd", directory, *args],
            env={**os.environ, "XDG_CONFIG_HOME": directory},
            capture_output=True,
            text=True,
        )
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
        suffix = " Do not work from memory of the style."
        self.assertTrue(body.endswith(suffix), body[-80:])
        brief = f"{body[:-len(suffix)]}\n\nPlaybook: playbooks/refactoring.md\nMode: full\n"
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
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "t3/scripts/roles.py"),
                            "mode",
                            "--brief-mode",
                            "light",
                            "--playbook",
                            stem,
                            "--attempt",
                            attempt,
                            "--cwd",
                            directory,
                        ],
                        env={**os.environ, "XDG_CONFIG_HOME": directory},
                        capture_output=True,
                        text=True,
                    )
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
        return subprocess.run(
            [sys.executable, str(ROOT / "t3/scripts/roles.py"), *args, "--cwd", str(self.directory)],
            env=env,
            capture_output=True,
            text=True,
        )

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


def bug_fix_show(mode, mode_source, escalate, budget="default", seat=GROK_SEAT):
    return {
        "budget": budget,
        "mode": mode,
        "modeSource": mode_source,
        "escalate": escalate,
        "catalog": True,
        "roles": {"bug-fix": {"source": "default", "seats": [seat]}},
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

    def test_missing_mode_defaults_to_full(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload, bug_fix_show("full", "default", None))
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])

    def test_project_without_mode_uses_the_user_mode(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            bug_fix_show("light", str(repo.user), None, seat=GROK_MEDIUM),
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
                            "seats": [{
                                "providerInstanceId": "grok",
                                "model": "grok-4.7",
                                "options": {"reasoningEffort": "xhigh"},
                            }],
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
                    completed = repo.write("--project")
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
                completed = subprocess.run(
                    [sys.executable, str(ROOT / "t3/scripts/roles.py"), command, "--help"],
                    env={**os.environ, "COLUMNS": "200"},
                    capture_output=True,
                    text=True,
                )
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

    def test_light_mode_caps_bug_fix_seat(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload, bug_fix_show("light", str(repo.user), None, seat=GROK_MEDIUM))

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
            second = repo.write("--project", "--set", "bug-fix=inherit")
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

    def test_user_write_without_mode_resets_to_full(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            first = repo.write("--mode", "light")
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(repo.user.read_text())["mode"], "light")
            second = repo.write("--budget", "small")
            self.assertEqual(second.returncode, 0, second.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(list(document), ["version", "roles", "budget", "mode"])
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "small", "mode": "full"})

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

    def test_write_without_keep_replaces_invalid_json(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("{not json", encoding="utf-8")
            completed = repo.write("--budget", "small")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "small", "mode": "full"})

    def test_keep_on_invalid_json_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("{not json", encoding="utf-8")
            before = repo.user.read_text()
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(repo.user.read_text(), before)

    def test_write_without_keep_replaces_a_non_object(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("[]\n", encoding="utf-8")
            completed = repo.write("--mode", "light")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
        self.assertEqual(document, {"version": 1, "roles": {}, "budget": "default", "mode": "light"})

    def test_keep_on_a_non_object_exits_2(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.user.parent.mkdir(parents=True)
            repo.user.write_text("[]\n", encoding="utf-8")
            completed = repo.write("--keep", "--budget", "small")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(repo.user.read_text(), "[]\n")

    def test_user_keep_drops_a_stored_escalate_list(self):
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
        self.assertNotIn("escalate", document)
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

    def test_mode_full_repairs_stored_turbo_without_keep(self):
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
        self.assertIn("full — recommended when no provider is near its limit", head)
        self.assertIn("- `light`", head)
        self.assertLess(
            head.index("full — recommended when no provider is near its limit"),
            head.index("- `light`"),
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
        self.assertIn("A user write without `--mode` stores `full`.", text)
        self.assertIn("A project write without `--mode` omits the key.", text)
        self.assertIn("Omitting `--escalate` leaves a stored project list in place.", text)
        self.assertIn("Tell the user which file was written, the budget, the mode,", text)

    def test_coordinator_mode_light_caps_a_configured_verifier(self):
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
        self.assertEqual(payload["roles"]["verifiers"]["seats"], [OPUS_MEDIUM, GROK_MEDIUM])
        self.assertEqual(completed.stderr, "")

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
            repo.put(repo.project, {"mode": "full"})
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
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_MEDIUM])

    def test_session_mode_full_over_project_light_lifts_the_cap(self):
        role = "bug-fix"
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full"})
            full = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", role,
            )
            repo.put(repo.project, {"mode": "light"})
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
        self.assertEqual(lifted_payload["roles"][role]["seats"], [GROK_SEAT])
        self.assertEqual(lifted_payload["roles"][role]["seats"], full_seats)
        self.assertEqual(capped_seats, [GROK_MEDIUM])
        self.assertNotEqual(capped_seats, lifted_payload["roles"][role]["seats"])

    def test_brief_mode_full_over_session_mode_light(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.project, {"mode": "full"})
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
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])

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
            repo.put(repo.user, {"budget": "small"})
            completed = repo.show_bug_fix()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["roles"]["bug-fix"]["seats"], [GROK_MEDIUM])
        self.assertEqual(payload["mode"], "full")

    def test_light_without_parent_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_PARENT_INFO)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], [GROK_MEDIUM])

    def test_light_without_catalog_prints_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run("show", "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, NO_CATALOG_INFO)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], "catalog-required")

    def test_full_without_parent_prints_no_info(self):
        with self.open_repo() as directory:
            repo = Repo(directory)
            completed = repo.run("show", "--catalog", str(CATALOG), "--role", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")

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
                return subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "t3/scripts/roles.py"),
                        "show",
                        "--cwd", str(worktree),
                        "--catalog", str(CATALOG),
                        "--parent", "claudeAgent/claude-opus-5-5",
                        "--role", "bug-fix",
                        *extra,
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                )

            capped = invoke("--brief-mode", "light")
            plain = invoke()
        self.assertEqual(capped.returncode, 0, capped.stderr)
        capped_payload = json.loads(capped.stdout)
        self.assertEqual(capped_payload["mode"], "light")
        self.assertEqual(capped_payload["modeSource"], "brief")
        self.assertEqual(capped_payload["roles"]["bug-fix"]["seats"], [GROK_MEDIUM])
        self.assertEqual(plain.returncode, 0, plain.stderr)
        plain_payload = json.loads(plain.stdout)
        self.assertEqual(plain_payload["mode"], "full")
        self.assertEqual(plain_payload["modeSource"], "default")
        self.assertEqual(plain_payload["roles"]["bug-fix"]["seats"], [GROK_SEAT])

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



EXCLUDED_RULE = roles.EXCLUDED_RULE
HAIKU_BRIEF = list(roles.HAIKU_BRIEF)
HAIKU_5_HIGH = {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5", "options": {"effort": "high"}}


class Haiku55CliTest(unittest.TestCase):
    def test_show_skill_tests_default_haiku_high(self):
        completed = show("skill tests")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_5_HIGH])
        self.assertEqual(entry["haikuBrief"], HAIKU_BRIEF)

    def test_show_haiku_reading_roles_medium(self):
        for role in ("how explorer", "why investigators"):
            with self.subTest(role=role):
                completed = show(role)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                entry = json.loads(completed.stdout)["roles"][role]
                self.assertEqual(entry["seats"], [HAIKU_5_MEDIUM])
                self.assertEqual(entry["haikuBrief"], HAIKU_BRIEF)

    def test_bounded_seat_command_unknown(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "t3/scripts/roles.py"), "bounded-seat"],
            capture_output=True,
            text=True,
        )
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

    def test_haiku_45_excluded_ids(self):
        cases = (
            ("claude-haiku-4-5", True),
            ("claude-haiku-4-5-20251001", True),
            ("anthropic/claude-haiku-4.5", True),
            ("claude_haiku_4_5", True),
            ("claude-haiku-5-5", False),
            ("claude-haiku-4-6", False),
            ("claude-sonnet-4-5", False),
        )
        for model_id, expected in cases:
            self.assertIs(roles.excluded_id(model_id), expected, model_id)

    def test_only_haiku_45_and_opus_never_picks_haiku_45(self):
        effort = [{"id": "effort", "type": "select", "options": [{"id": "high", "isDefault": True}]}]
        catalog = {
            "inheritedProviderInstanceId": "claudeAgent",
            "inheritedModel": "claude-opus-5-5",
            "providers": [{
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [
                    {"id": "claude-haiku-5-5", "options": [{"id": "effort", "type": "select", "options": [{"id": "high", "isDefault": True}, {"id": "medium"}]}]},
                    {"id": "claude-opus-5-5", "options": effort},
                ],
            }],
        }
        for role in roles.SINGLE_ROLES:
            entry = roles.resolve({"budget": "default", "roles": {}, "sources": {}}, catalog, [role])["roles"][role]
            seats = entry["seats"]
            if isinstance(seats, list):
                for seat in seats:
                    if isinstance(seat, dict):
                        self.assertNotEqual(roles.normalized_bare(seat["model"]), "claude-haiku-4-5", role)

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

    def test_launches_seats_default_haiku_high(self):
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
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_5_HIGH])

    def test_budget_and_light_levels_for_haiku_roles(self):
        cases = (
            ({"budget": "default"}, "skill tests", "high"),
            ({"budget": "small"}, "skill tests", "medium"),
            ({"budget": "large"}, "skill tests", "high"),
            ({"budget": "unlimited"}, "skill tests", "high"),
            ({"mode": "light", "budget": "default"}, "skill tests", "medium"),
            ({"budget": "default"}, "how explorer", "medium"),
        )
        for config, role, level in cases:
            with self.subTest(config=config, role=role):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.user, config)
                    completed = repo.run(
                        "show",
                        "--catalog",
                        str(CATALOG),
                        "--parent",
                        "claudeAgent/claude-opus-5-5",
                        "--role",
                        role,
                        *(
                            ["--brief-mode", "light", "--session-mode", "light"]
                            if config.get("mode") == "light"
                            else []
                        ),
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                seat = json.loads(completed.stdout)["roles"][role]["seats"][0]
                self.assertEqual(seat["options"]["effort"], level)

    def test_non_haiku_defaults_unchanged_on_fixture(self):
        opus_max = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "max"}}
        expectations = {
            "default": {
                "bug-fix": {"source": "default", "seats": [GROK_SEAT]},
                "judgment and prose": {"source": "default", "seats": [OPUS_SEAT]},
                "arena runners": {"source": "default", "seats": [OPUS_SEAT, GROK_SEAT]},
            },
            "unlimited": {
                "bug-fix": {"source": "default", "seats": [GROK_SEAT]},
                "judgment and prose": {"source": "default", "seats": [opus_max]},
                "arena runners": {"source": "default", "seats": [opus_max, GROK_SEAT]},
            },
        }
        for budget, entries in expectations.items():
            with self.subTest(budget=budget):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.user, {"version": 1, "roles": {}, "budget": budget})
                    completed = repo.run(
                        "show",
                        "--catalog",
                        str(CATALOG),
                        "--parent",
                        "claudeAgent/claude-opus-5-5",
                        *[arg for role in entries for arg in ("--role", role)],
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["roles"], entries)

    def test_launches_seats_ignores_a_configured_cursor_skill_tests_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"version": 1, "roles": {
                "skill tests": [{"providerInstanceId": "cursor", "model": "claude-haiku-5-5"}],
            }})
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
        self.assertEqual(json.loads(completed.stdout)["roles"]["skill tests"], {
            "source": str(repo.user),
            "seats": [HAIKU_5_HIGH],
            "haikuBrief": HAIKU_BRIEF,
        })


def reasoning_select(default="high"):
    return {"id": "reasoningEffort", "type": "select", "options": [
        {"id": "low"},
        {"id": "medium", **({"isDefault": True} if default == "medium" else {})},
        {"id": "high", **({"isDefault": True} if default == "high" else {})},
        {"id": "xhigh"},
    ]}


def grok_claude_fast_catalog():
    effort = reasoning_select()
    return {"providers": [
        {
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "grok-4.7", "options": [effort]},
                {"id": "grok-4.7-build-fast", "options": [effort]},
                {"id": "grok-build", "options": []},
            ],
        },
        {
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "claude-opus-5-5", "options": []},
                {"id": "claude-haiku-5-5", "options": [{"id": "effort", "type": "select", "options": [{"id": "high", "isDefault": True}, {"id": "medium"}]}]},
            ],
        },
    ]}


def cursor_grok_catalog():
    return {"providers": [
        {
            "providerInstanceId": "grok",
            "canRunChildTask": False,
            "constraints": ["blocked"],
            "models": [{"id": "grok-4.7", "options": []}],
        },
        {
            "providerInstanceId": "cursor",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7", "options": [
                {"id": "reasoning_effort", "type": "select", "options": [
                    {"id": "low"}, {"id": "medium"}, {"id": "high"}, {"id": "xhigh"},
                ]},
                {"id": "fastMode", "type": "boolean"},
            ]}],
        },
    ]}


def user_shape_catalog():
    medium = reasoning_select("medium")
    return {"providers": [
        {
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "grok-4.7", "options": [reasoning_select()]},
                {"id": "grok-4.7-build-fast", "options": []},
            ],
        },
        {
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "claude-opus-5-5", "options": []},
                {"id": "claude-haiku-5-5", "options": [{"id": "effort", "type": "select", "options": [{"id": "high", "isDefault": True}, {"id": "medium"}]}]},
            ],
        },
        {
            "providerInstanceId": "codex",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "gpt-6-astra", "options": [medium]},
                {"id": "gpt-6-luna", "options": [medium]},
            ],
        },
        {
            "providerInstanceId": "cursor",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "gemini-3.8-flash", "options": []}],
        },
    ]}


def single_role_split(grok_seat, claude_seat, reflect=None):
    code = (
        "feature, refactoring",
        "bug-fix",
        "perf-issue",
        "hillclimb",
        "how explorer",
        "why investigators",
        "swarm workers",
    )
    judgment = (
        "judgment and prose",
        "hardest tasks",
        "how explainer",
        "why synthesizer",
        "reflect judgment, divergent, synthesizer",
    )
    document = {name: [grok_seat] for name in code}
    document.update({name: [claude_seat] for name in judgment})
    document["reflect tooling"] = [reflect or grok_seat]
    return {"roles": document}


SHORT_ESTIMATE = {
    "overheadTokens": 41000,
    "briefBytes": 12,
    "readBytes": 0,
    "tokens": 41003,
    "target": None,
}
FAST_RULE = roles.EXCLUDED_RULE


class FastGrokCliTest(unittest.TestCase):
    def show(self, repo, catalog, role, parent="claudeAgent/claude-opus-5-5", brief_mode=None):
        path = repo.directory / "catalog.json"
        repo.put(path, catalog)
        args = ["show", "--catalog", str(path), "--parent", parent, "--role", role]
        if brief_mode is not None:
            args.extend(["--brief-mode", brief_mode])
        return repo.run(*args), path

    def test_fast_grok_truth_table(self):
        cases = (
            ("grok-4.7-build-fast", True),
            ("x-ai/grok-code-fast-1", True),
            ("Grok-4-Fast-Reasoning", True),
            ("grok-build", False),
            ("grok-4.7", False),
            ("gpt-6.1-fast", False),
        )
        for model_id, expected in cases:
            self.assertIs(roles.fast_grok(model_id), expected, model_id)

    def test_launches_seats_skips_grok_build_fast(self):
        grok = {"providerInstanceId": "grok", "model": "grok-4.7"}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {name: [grok] for name in roles.SINGLE_ROLES}})
            path = repo.directory / "catalog.json"
            repo.put(path, grok_claude_fast_catalog())
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "skill tests",
                "--launches-seats",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [{"providerInstanceId": "grok", "model": "grok-4.7"}])

    def test_skill_tests_skips_grok_build_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed, _path = self.show(repo, grok_claude_fast_catalog(), "skill tests")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_5_HIGH])
        self.assertEqual(entry["haikuBrief"], HAIKU_BRIEF)

    def test_swarm_cursor_grok_pins_fast_mode_false(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed, _path = self.show(repo, cursor_grok_catalog(), "swarm workers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["swarm workers"]
        self.assertEqual(entry["seats"], [{
            "providerInstanceId": "cursor",
            "model": "grok-4.7",
            "options": {"reasoning_effort": "xhigh", "fastMode": False},
        }])

    def test_no_preferred_default_sets_fast_mode_true(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, cursor_grok_catalog())
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(set(payload["roles"]), set(roles.ROLES))
        for name, entry in payload["roles"].items():
            for seat in entry["seats"]:
                if isinstance(seat, dict):
                    self.assertIsNot(seat.get("options", {}).get("fastMode"), True, name)

    def test_same_family_fallback_skips_a_fast_id(self):
        catalog = {"providers": [{
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [
                {"id": "grok-4.7-build-fast", "options": []},
                {"id": "grok-4.6", "options": [reasoning_select()]},
            ],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed, _path = self.show(repo, catalog, "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{
            "providerInstanceId": "grok",
            "model": "grok-4.6",
            "options": {"reasoningEffort": "xhigh"},
        }])

    def test_verifier_skips_a_leading_fast_id(self):
        catalog = {"providers": [
            {
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "claude-opus-5-5", "options": []}],
            },
            {
                "providerInstanceId": "grok",
                "canRunChildTask": True,
                "constraints": [],
                "models": [
                    {"id": "grok-4.7-build-fast", "options": []},
                    {"id": "grok-4.6", "options": []},
                ],
            },
        ]}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed, _path = self.show(repo, catalog, "verifiers")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["verifiers"]
        self.assertEqual(entry["seats"], [
            "inherit",
            {"providerInstanceId": "grok", "model": "grok-4.6"},
        ])

    def test_configured_fast_id_is_skipped_and_refused(self):
        line = (
            "bug-fix: grok/grok-4.7-build-fast: "
            f"grok-4.7-build-fast is a fast Grok variant, and {FAST_RULE}"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [
                {"providerInstanceId": "grok", "model": "grok-4.7-build-fast"},
            ]}})
            completed, path = self.show(repo, grok_claude_fast_catalog(), "bug-fix")
            checked = repo.run(
                "validate",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], {
            "source": "default",
            "seats": [{
                "providerInstanceId": "grok",
                "model": "grok-4.7",
                "options": {"reasoningEffort": "xhigh"},
            }],
            "notes": [
                "skipped configured seat grok/grok-4.7-build-fast: "
                f"grok-4.7-build-fast is a fast Grok variant, and {FAST_RULE}",
            ],
        })
        self.assertEqual(checked.returncode, 1)
        self.assertEqual(checked.stdout, line + "\n")
        self.assertEqual(checked.stderr, "")
        refusal = "error: refusing to write, even with --force:\n" + line + "\n"
        for force in ((), ("--force",)):
            with self.subTest(force=force):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    written = repo.run(
                        "write",
                        "--catalog", str(CATALOG),
                        "--parent", "claudeAgent/claude-opus-5-5",
                        *force,
                        "--set", "bug-fix=grok/grok-4.7-build-fast",
                    )
                    self.assertFalse(repo.user.exists())
                self.assertEqual(written.returncode, 2)
                self.assertEqual(written.stdout, "")
                self.assertEqual(written.stderr, refusal)

    def test_configured_fast_mode_true_is_skipped(self):
        line = (
            "bug-fix: cursor/grok-4.7: "
            f"fastMode=true runs grok-4.7 fast, and {FAST_RULE}"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [{
                "providerInstanceId": "cursor",
                "model": "grok-4.7",
                "options": {"fastMode": True},
            }]}})
            completed, path = self.show(repo, cursor_grok_catalog(), "bug-fix")
            checked = repo.run(
                "validate",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], {
            "source": "default",
            "seats": [{
                "providerInstanceId": "cursor",
                "model": "grok-4.7",
                "options": {"reasoning_effort": "xhigh", "fastMode": False},
            }],
            "notes": [
                "skipped configured seat cursor/grok-4.7: "
                f"fastMode=true runs grok-4.7 fast, and {FAST_RULE}",
            ],
        })
        self.assertEqual(checked.returncode, 1)
        self.assertEqual(checked.stdout, line + "\n")
        self.assertEqual(checked.stderr, "")
        refusal = "error: refusing to write, even with --force:\n" + line + "\n"
        for force in ((), ("--force",)):
            with self.subTest(force=force):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    written = repo.run(
                        "write",
                        "--catalog", str(CATALOG),
                        "--parent", "claudeAgent/claude-opus-5-5",
                        *force,
                        "--set", "bug-fix=cursor/grok-4.7?fastMode=true",
                    )
                    self.assertFalse(repo.user.exists())
                self.assertEqual(written.returncode, 2)
                self.assertEqual(written.stdout, "")
                self.assertEqual(written.stderr, refusal)

    def test_configured_fast_mode_false_has_no_note(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [{
                "providerInstanceId": "cursor",
                "model": "grok-4.7",
                "options": {"fastMode": False},
            }]}})
            completed, _path = self.show(repo, cursor_grok_catalog(), "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry["seats"], [{
            "providerInstanceId": "cursor",
            "model": "grok-4.7",
            "options": {"fastMode": False},
        }])
        self.assertNotIn("notes", entry)

    def test_missing_model_drops_carried_fast_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [{
                "providerInstanceId": "cursor",
                "model": "grok-9",
                "options": {"fastMode": True},
            }]}})
            completed, _path = self.show(repo, cursor_grok_catalog(), "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["bug-fix"]
        self.assertEqual(entry, {
            "source": "default",
            "seats": [{
                "providerInstanceId": "cursor",
                "model": "grok-4.7",
                "options": {"reasoning_effort": "xhigh", "fastMode": False},
            }],
            "notes": [
                "skipped configured seat cursor/grok-9: "
                f"fastMode=true runs grok-9 fast, and {FAST_RULE}",
            ],
        })

    def test_launches_seats_skips_a_free_model_outside_default_families(self):
        cursor = {"providerInstanceId": "cursor", "model": "gemini-3.8-flash"}
        catalog = {"providers": [
            {
                "providerInstanceId": "opencode",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "opencode/ling-3.0-flash-fin-free", "options": []}],
            },
            {
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "claude-haiku-5-5", "options": [{"id": "effort", "type": "select", "options": [{"id": "high", "isDefault": True}]}]}],
            },
        ]}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, single_role_split(cursor, cursor))
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "skill tests",
                "--launches-seats",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_5_HIGH])

    def test_launches_seats_keeps_the_configured_family_split(self):
        grok = {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "high"}}
        claude = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "high"}}
        reflect = {"providerInstanceId": "codex", "model": "gpt-6-astra"}
        document = single_role_split(grok, claude, reflect)
        self.assertEqual(set(document["roles"]), set(roles.SINGLE_ROLES) - {"skill tests"})
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, document)
            path = repo.directory / "catalog.json"
            repo.put(path, user_shape_catalog())
            def launch(parent):
                return repo.run(
                    "show",
                    "--catalog", str(path),
                    "--parent", parent,
                    "--role", "skill tests",
                    "--launches-seats",
                )

            claude_parent = launch("claudeAgent/claude-opus-5-5")
            grok_parent = launch("grok/grok-4.7")
        self.assertEqual(claude_parent.returncode, 0, claude_parent.stderr)
        self.assertEqual(grok_parent.returncode, 0, grok_parent.stderr)
        self.assertEqual(
            json.loads(claude_parent.stdout)["roles"]["skill tests"]["seats"],
            [HAIKU_5_HIGH],
        )
        self.assertEqual(
            json.loads(grok_parent.stdout)["roles"]["skill tests"]["seats"],
            [HAIKU_5_HIGH],
        )

    def test_fast_parent_verifiers_use_the_providers_safe_model(self):
        catalog = json.loads(CATALOG.read_text())
        for provider in catalog["providers"]:
            if provider["providerInstanceId"] == "grok":
                provider["models"].insert(0, {"id": "grok-4.7-build-fast", "options": []})
        note = (
            "skipped inherit of grok/grok-4.7-build-fast: "
            f"grok-4.7-build-fast is a fast Grok variant, and {FAST_RULE}"
        )
        expected = {
            "full": {
                "source": "default",
                "seats": [
                    {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                    {"providerInstanceId": "grok", "model": "grok-4.7"},
                ],
                "notes": [note],
            },
            "light": {
                "source": "default",
                "seats": [
                    {"providerInstanceId": "codex", "model": "gpt-6.1-sol", "options": {"reasoningEffort": "medium"}},
                    {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5", "options": {"effort": "medium"}},
                    {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"reasoningEffort": "medium"}},
                ],
                "notes": [note],
            },
        }
        for mode, entry in expected.items():
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    completed, _path = self.show(
                        repo, catalog, "verifiers", parent="grok/grok-4.7-build-fast", brief_mode=mode,
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(json.loads(completed.stdout)["roles"]["verifiers"], entry)

    def test_fast_mode_parent_verifiers_are_explicit(self):
        expected = {
            "full": (
                {"providerInstanceId": "cursor", "model": "grok-4.7", "options": {"fastMode": False}},
                "inherit made explicit as cursor/grok-4.7 so fastMode stays false",
            ),
            "light": (
                {
                    "providerInstanceId": "cursor",
                    "model": "grok-4.7",
                    "options": {"fastMode": False, "reasoning_effort": "medium"},
                },
                "inherit made explicit as cursor/grok-4.7 so the small budget applies and fastMode stays false",
            ),
        }
        for mode, (seat, info) in expected.items():
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    completed, _path = self.show(
                        repo, cursor_grok_catalog(), "verifiers", parent="cursor/grok-4.7", brief_mode=mode,
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(json.loads(completed.stdout)["roles"]["verifiers"], {
                    "source": "default",
                    "seats": [seat, seat, seat],
                    "info": [info, info, info],
                })

    def test_safe_parents_keep_inherit(self):
        expected = {
            "claudeAgent/claude-opus-5-5": [
                "inherit",
                {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                {"providerInstanceId": "grok", "model": "grok-4.7"},
            ],
            "grok/grok-4.7": [
                "inherit",
                {"providerInstanceId": "codex", "model": "gpt-6.1-sol"},
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
            ],
            "codex/gpt-6.1-sol": [
                "inherit",
                {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"},
                {"providerInstanceId": "grok", "model": "grok-4.7"},
            ],
        }
        catalog = json.loads(CATALOG.read_text())
        for parent, seats in expected.items():
            with self.subTest(parent=parent):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    completed, _path = self.show(
                        repo, catalog, "verifiers", parent=parent, brief_mode="full",
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertEqual(json.loads(completed.stdout)["roles"]["verifiers"], {
                    "source": "default",
                    "seats": seats,
                })

    def test_launches_seats_refuses_a_fast_only_parent(self):
        catalog = {"providers": [{
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7-build-fast", "options": []}],
        }]}
        stderr = (
            "error: role 'skill tests' has no seat: every runnable model in the catalog is excluded "
            f"(grok-4.7-build-fast), and {FAST_RULE}\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "grok/grok-4.7-build-fast",
                "--role", "skill tests",
                "--launches-seats",
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, stderr)

    def test_launches_seats_empty_pool_uses_the_parent_safe_model(self):
        catalog = {"providers": [
            {
                "providerInstanceId": "acme",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "grok-4.7-build-fast", "options": []}],
            },
            {
                "providerInstanceId": "grok",
                "canRunChildTask": True,
                "constraints": [],
                "models": [
                    {"id": "grok-4.7-build-fast", "options": []},
                    {"id": "grok-4.7", "options": [reasoning_select()]},
                ],
            },
        ]}
        replaced = (
            "inherit replaced by grok/grok-4.7: "
            f"grok-4.7-build-fast is a fast Grok variant, and {EXCLUDED_RULE}"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"skill tests": ["inherit"]}})
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "grok/grok-4.7-build-fast",
                "--role", "skill tests",
                "--launches-seats",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [{
            "providerInstanceId": "grok",
            "model": "grok-4.7",
        }])
        self.assertTrue(any(replaced in note for note in entry.get("notes", [])))

    def test_unrunnable_provider_with_a_fast_parent_uses_the_safe_model(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [
                {"providerInstanceId": "pi", "model": "default"},
            ]}})
            completed, _path = self.show(
                repo, json.loads(CATALOG.read_text()), "bug-fix", parent="grok/grok-4.7-build-fast",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"], {
            "source": str(repo.user),
            "seats": [{"providerInstanceId": "grok", "model": "grok-4.7"}],
            "notes": [
                "pi is not runnable (not in catalog); seat inherits the parent",
                "inherit replaced by grok/grok-4.7: "
                f"grok-4.7-build-fast is a fast Grok variant, and {FAST_RULE}",
            ],
        })

    def test_show_skips_a_configured_fast_skill_tests_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"skill tests": [
                {"providerInstanceId": "grok", "model": "grok-4.7-build-fast"},
            ]}})
            path = repo.directory / "catalog.json"
            repo.put(path, grok_claude_fast_catalog())
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "skill tests",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_5_HIGH])
        self.assertIn(
            "skipped configured seat grok/grok-4.7-build-fast",
            entry.get("notes", [""])[0],
        )

    def test_no_catalog_fast_parent_needs_the_catalog(self):
        note = (
            "the parent grok/grok-4.7-build-fast is a fast Grok variant, so inherit needs the catalog: "
            "call orchestrator_capabilities and rerun roles.py show --catalog, "
            f"and {FAST_RULE}"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            verifiers = repo.run("show", "--parent", "grok/grok-4.7-build-fast", "--role", "verifiers")
            skill_tests = repo.run("show", "--parent", "grok/grok-4.7-build-fast", "--role", "skill tests")
            bug_fix = repo.run("show", "--parent", "grok/grok-4.7-build-fast", "--role", "bug-fix")
        for completed in (verifiers, skill_tests, bug_fix):
            self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(verifiers.stdout)["roles"]["verifiers"], {
            "source": "default",
            "seats": "catalog-required",
            "note": note,
        })
        self.assertEqual(json.loads(skill_tests.stdout)["roles"]["skill tests"], {
            "source": "default",
            "seats": "catalog-required",
            "note": "call orchestrator_capabilities and rerun roles.py show --catalog",
        })
        self.assertEqual(json.loads(bug_fix.stdout)["roles"]["bug-fix"], {
            "source": "default",
            "seats": "catalog-required",
            "note": "call orchestrator_capabilities and rerun roles.py show --catalog",
        })

    def test_provider_with_only_fast_models_names_the_rule(self):
        catalog = {"providers": [{
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7-build-fast", "options": []}],
        }]}
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": [
                {"providerInstanceId": "grok", "model": "grok-9"},
            ]}})
            completed, _path = self.show(repo, catalog, "bug-fix")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: role 'bug-fix' cannot use grok/grok-4.7-build-fast: "
            f"grok-4.7-build-fast is a fast Grok variant, and {FAST_RULE}\n",
        )

    def test_fast_only_catalog_names_the_rule(self):
        fast_only = {"providers": [{
            "providerInstanceId": "grok",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "grok-4.7-build-fast", "options": []}],
        }]}
        mixed = {"providers": [
            {
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "claude-haiku-4-5", "options": []}],
            },
            {
                "providerInstanceId": "grok",
                "canRunChildTask": True,
                "constraints": [],
                "models": [{"id": "grok-4.7-build-fast", "options": []}],
            },
        ]}
        cases = {
            "fast": (
                fast_only,
                "error: role 'bug-fix' has no seat: every runnable model in the catalog is excluded "
                f"(grok-4.7-build-fast), and {FAST_RULE}\n",
            ),
            "mixed": (
                mixed,
                "error: role 'bug-fix' has no seat: every runnable model in the catalog is excluded "
                f"(claude-haiku-4-5, grok-4.7-build-fast), and {FAST_RULE}\n",
            ),
        }
        for name, (catalog, stderr) in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    completed, _path = self.show(repo, catalog, "bug-fix")
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, stderr)


def blocked_fast_option_provider(provider_id="cursor"):
    return {
        "providerInstanceId": provider_id,
        "canRunChildTask": False,
        "constraints": ["Provider is not authenticated."],
        "models": [{"id": "grok-4.7", "options": [
            {"id": "fastMode", "type": "boolean", "currentValue": True},
        ]}],
    }


def blocked_fast_option_refusal(role, provider_id="cursor"):
    return (
        f"error: role {role!r} cannot inherit {provider_id}/grok-4.7: "
        f"{provider_id} is not runnable (Provider is not authenticated.), so fastMode cannot be pinned false, "
        f"and {FAST_RULE}\n"
    )


class BlockedFastOptionParentCliTest(unittest.TestCase):
    """A parent whose Grok model declares fastMode is never inherited bare, even when its provider cannot run."""

    def catalog(self):
        catalog = json.loads(CATALOG.read_text())
        catalog["providers"] = [
            provider for provider in catalog["providers"] if provider["providerInstanceId"] != "cursor"
        ] + [blocked_fast_option_provider()]
        return catalog

    def show(self, configured):
        expected = blocked_fast_option_refusal("bug-fix")
        for mode in ("full", "light"):
            with self.subTest(mode=mode):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.user, {"roles": {"bug-fix": [configured]}})
                    path = repo.directory / "catalog.json"
                    repo.put(path, self.catalog())
                    completed = repo.run(
                        "show",
                        "--catalog", str(path),
                        "--parent", "cursor/grok-4.7",
                        "--role", "bug-fix",
                        "--brief-mode", mode,
                    )
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, expected)

    def launch_show(self, provider_id):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, {"providers": [blocked_fast_option_provider(provider_id)]})
            return repo.run(
                "show",
                "--catalog", str(path),
                "--parent", f"{provider_id}/grok-4.7",
                "--role", "skill tests",
                "--launches-seats",
            )

    def test_configured_inherit_is_refused(self):
        self.show("inherit")

    def test_unavailable_seat_fallback_is_refused(self):
        self.show({"providerInstanceId": "pi", "model": "default"})

    def test_launches_seats_empty_pool_is_refused(self):
        completed = self.launch_show("cursor")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("cannot launch seats", completed.stderr)

    def test_launches_seats_empty_pool_on_acme_is_refused(self):
        expected = {
            "cursor": (
                "error: role 'skill tests' has no seat for a child that launches seats: "
                "cursor cannot launch seats, and no single-role seat in roles.json, "
                "built-in default family, or parent names another provider\n"
            ),
            "acme": blocked_fast_option_refusal("skill tests", "acme"),
        }
        for provider_id, stderr in expected.items():
            with self.subTest(provider=provider_id):
                completed = self.launch_show(provider_id)
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                if provider_id == "acme":
                    self.assertIn("no provider in the catalog can run child tasks", completed.stderr)
                else:
                    self.assertEqual(completed.stderr, stderr)
