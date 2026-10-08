import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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
    "how explorer": ["grok-4.7"],
    "how explainer": ["claude-opus-5-5"],
    "why investigators": ["grok-4.7"],
    "why synthesizer": ["claude-opus-5-5"],
    "reflect tooling": ["grok-4.7"],
    "reflect judgment, divergent, synthesizer": ["claude-opus-5-5"],
    "swarm workers": ["grok-4.7"],
    "skill tests": ["gpt-6-luna"],
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
        self.assert_check(completed, 1, MISSING_PERSONA + MISSING_PLAYBOOK)

    def test_passing_brief(self):
        brief = f"{agent_body()}\n\nPlaybook: playbooks/refactoring.md\n\nImplement the change described in the plan.\n"
        completed = check_text(brief)
        self.assert_check(completed, 0, "ok playbooks/refactoring.md\n")

    def test_persona_truncated_by_one_sentence(self):
        body = agent_body()
        suffix = " Do not work from memory of the style."
        self.assertTrue(body.endswith(suffix), body[-80:])
        brief = f"{body[:-len(suffix)]}\n\nPlaybook: playbooks/refactoring.md\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_persona_after_other_text(self):
        brief = f"Read this note first.\n\n{agent_body()}\n\nPlaybook: playbooks/refactoring.md\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_frontmatter_paste(self):
        brief = PERSONA_PATH.read_text(encoding="utf-8") + "\nPlaybook: playbooks/refactoring.md\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PERSONA)

    def test_missing_playbook_line(self):
        brief = f"{agent_body()}\n\nImplement the change described in the plan.\n"
        completed = check_text(brief)
        self.assert_check(completed, 1, MISSING_PLAYBOOK)

    def test_unknown_playbook_name(self):
        brief = f"{agent_body()}\n\nPlaybook: playbooks/not-a-playbook.md\n"
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
        )
        completed = check_text(brief)
        self.assert_check(completed, 1, "more than one Playbook line: keep one\n")

    def test_missing_brief_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.md"
            completed = run_check_brief(path)
        self.assert_check(completed, 2, "", f"error: {path}: brief not found\n")


CATALOG = ROOT / "tests/fixtures/catalog.json"
FOUR_PATHS = [
    "**/migrations/**",
    "t3/added/landing/scripts/land.py",
    "t3/added/brigade/scripts/brigade.py",
    "scripts/install.py",
]
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


HAIKU_CAP = (
    "claude-haiku-5-5 is capped at 100000 prompt tokens, and only skill tests may run a capped model"
)


class PromptCapCliTest(unittest.TestCase):
    def test_write_refuses_a_capped_bug_fix_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.write("--set", "bug-fix=claudeAgent/claude-haiku-5-5")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                completed.stderr,
                f"error: {repo.user}: role 'bug-fix' cannot use claudeAgent/claude-haiku-5-5: {HAIKU_CAP}\n",
            )
            self.assertFalse(repo.user.exists())

    def test_write_force_still_refuses_a_capped_bug_fix_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.write("--force", "--set", "bug-fix=claudeAgent/claude-haiku-5-5")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                completed.stderr,
                f"error: {repo.user}: role 'bug-fix' cannot use claudeAgent/claude-haiku-5-5: {HAIKU_CAP}\n",
            )
            self.assertFalse(repo.user.exists())

    def test_write_refuses_a_capped_seat_in_a_panel(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.write("--set", "verifiers=claudeAgent/claude-haiku-5-5;grok/grok-4.7")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                completed.stderr,
                f"error: {repo.user}: role 'verifiers' cannot use claudeAgent/claude-haiku-5-5: {HAIKU_CAP}\n",
            )
            self.assertFalse(repo.user.exists())

    def test_write_accepts_a_capped_model_for_skill_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.write("--set", "skill tests=claudeAgent/claude-haiku-5-5")
            self.assertEqual(completed.returncode, 0, completed.stderr)
            document = json.loads(repo.user.read_text())
            self.assertEqual(document["roles"]["skill tests"], [
                {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5"},
            ])

    def test_show_refuses_a_stored_capped_architect_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"architect runners": [
                {"providerInstanceId": "cursor", "model": "claude-haiku-5-5"},
            ]}})
            completed = repo.run("show", "--role", "bug-fix")
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(
                completed.stderr,
                f"error: {repo.user}: role 'architect runners' cannot use cursor/claude-haiku-5-5: {HAIKU_CAP}\n",
            )

    def show_inherit(self, repo, *extra):
        return repo.run("show", "--role", "bug-fix", *extra)

    def test_no_catalog_haiku_parent_refuses_inherit(self):
        cases = (
            {"roles": {"bug-fix": ["inherit"]}},
            {"mode": "light", "roles": {"bug-fix": ["inherit"]}},
            {"budget": "unlimited", "roles": {"bug-fix": ["inherit"]}},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.user, payload)
                    completed = self.show_inherit(repo, "--parent", "claudeAgent/claude-haiku-5-5")
                self.assertEqual(completed.returncode, 2)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, f"error: {INHERIT_CAP}\n")

    def test_malformed_parent_exits_before_the_catalog(self):
        samples = ("claude-haiku-5-5", "claudeAgent/", "/claude-haiku-5-5", "a b/c", "")
        for text in samples:
            for catalog in (None, CATALOG):
                with self.subTest(text=text, catalog=catalog):
                    with tempfile.TemporaryDirectory() as directory:
                        repo = Repo(directory)
                        args = ["show", "--role", "bug-fix", "--parent", text]
                        if catalog is None:
                            args.extend(["--catalog", str(Path(directory) / "missing-catalog.json")])
                        else:
                            args.extend(["--catalog", str(catalog)])
                        completed = repo.run(*args)
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, parent_error(text))
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": ["inherit"]}})
            completed = self.show_inherit(repo, "--parent", "opencode/opencode/big-pickle")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], ["inherit"])
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            for command in ("validate", "write"):
                completed = repo.run(command, "--catalog", str(CATALOG), "--parent", "claude-haiku-5-5")
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertEqual(completed.stderr, parent_error("claude-haiku-5-5"))
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(Path(directory) / "missing-catalog.json"),
                "--parent", "",
                "--brief", str(Path(directory) / "missing-brief.txt"),
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, parent_error(""))

    def test_snapshot_without_a_parent_keeps_inherit(self):
        catalog = runnable_haiku_catalog()
        for pass_path in (False, True):
            with self.subTest(pass_path=pass_path):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    snapshot = repo.directory / "pstack-t3" / "catalog.json"
                    repo.put(snapshot, catalog)
                    repo.put(repo.user, {"roles": {"bug-fix": ["inherit"]}})
                    args = ["--role", "bug-fix"]
                    if pass_path:
                        args.extend(["--catalog", str(snapshot)])
                    completed = repo.run("show", *args)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], ["inherit"])

    def test_panels_refuse_a_haiku_parent_without_a_catalog(self):
        grok = {"providerInstanceId": "grok", "model": "grok-4.7"}
        cases = (
            ("verifiers", None, ()),
            ("interrogate reviewers", {"roles": {"interrogate reviewers": ["inherit", grok]}}, ()),
            ("verifiers", {"mode": "light", "roles": {"verifiers": ["inherit"]}}, ()),
        )
        for role, payload, extra in cases:
            with self.subTest(role=role, payload=payload):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    if payload is not None:
                        repo.put(repo.user, payload)
                    completed = repo.run("show", "--role", role, "--parent", "claudeAgent/claude-haiku-5-5", *extra)
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertEqual(completed.stdout, "")
                self.assertIn(f"role {role!r} cannot inherit claudeAgent/claude-haiku-5-5", completed.stderr)
                self.assertIn(HAIKU_CAP, completed.stderr)

    def test_uncapped_parent_keeps_inherit_without_a_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"bug-fix": ["inherit"]}})
            completed = self.show_inherit(repo, "--parent", "grok/grok-4.7")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["catalog"], False)
        self.assertEqual(body["roles"]["bug-fix"]["seats"], ["inherit"])

    def test_show_skill_tests_returns_the_uncapped_haiku(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            completed = repo.run(
                "show", "--catalog", str(CATALOG), "--parent", "grok/grok-4.7", "--role", "skill tests",
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            entry = json.loads(completed.stdout)["roles"]["skill tests"]
            self.assertEqual(entry["seats"], [HAIKU_4_SEAT])
            self.assertEqual(entry["note"], SKILL_TESTS_NOTE)
            repo.put(repo.user, {"roles": {"skill tests": [
                {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5"},
            ]}})
            configured = repo.run(
                "show", "--catalog", str(CATALOG), "--parent", "grok/grok-4.7", "--role", "skill tests",
            )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        entry = json.loads(configured.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_4_SEAT])
        self.assertEqual(entry["note"], SKILL_TESTS_NOTE)

    def test_bounded_seat_accepts_a_small_read(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            brief = repo.directory / "brief.txt"
            read = repo.directory / "read.txt"
            brief.write_bytes(b"y" * 1000)
            read.write_bytes(b"x" * 2000)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
                "--read", str(read),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
        self.assertEqual(body["capped"], True)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["seat"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5"})
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 1000,
            "readBytes": 2000,
            "tokens": 41750,
            "target": 100000,
        })

    def test_bounded_seat_falls_back_when_the_estimate_is_over_the_target(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            brief = repo.directory / "brief.txt"
            read = repo.directory / "read.txt"
            brief.write_bytes(b"y" * 1000)
            read.write_bytes(b"z" * 235004)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
                "--read", str(read),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["capped"], False)
        self.assertEqual(body["seat"], HAIKU_4_SEAT)
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["reason"], "estimate 100001 tokens is over the 100000-token target for claude-haiku-5-5")
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 1000,
            "readBytes": 235004,
            "tokens": 100001,
            "target": 100000,
        })

    def test_bounded_seat_runs_cursor_haiku_under_the_target(self):
        catalog = {
            "providers": [
                {
                    "providerInstanceId": "cursor",
                    "canRunChildTask": True,
                    "constraints": [],
                    "models": [{"id": "claude-haiku-5-5", "options": [
                        {"id": "contextWindow", "type": "select", "options": [{"id": "1m"}, {"id": "300k"}]},
                    ]}],
                },
                {
                    "providerInstanceId": "claudeAgent",
                    "canRunChildTask": True,
                    "constraints": [],
                    "models": [{"id": "claude-haiku-4-5", "options": [{"id": "thinking", "type": "boolean"}]}],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "cursor-catalog.json"
            repo.put(path, catalog)
            brief = repo.directory / "brief.txt"
            brief.write_bytes(b"short brief\n")
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(path),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["capped"], True)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["seat"], {
            "providerInstanceId": "cursor",
            "model": "claude-haiku-5-5",
            "options": {"contextWindow": "300k"},
        })
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 12,
            "readBytes": 0,
            "tokens": 41003,
            "target": 100000,
        })

    def test_bounded_seat_prints_an_uncapped_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"skill tests": [
                {"providerInstanceId": "grok", "model": "grok-4.7"},
            ]}})
            brief = repo.directory / "brief.txt"
            brief.write_bytes(b"y" * 1000)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["capped"], False)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["seat"], {"providerInstanceId": "grok", "model": "grok-4.7"})
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 1000,
            "readBytes": 0,
            "tokens": 41250,
            "target": None,
        })

    def test_bounded_seat_rejects_a_missing_read(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            brief = repo.directory / "brief.txt"
            brief.write_bytes(b"brief\n")
            missing = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
                "--read", "/x",
            )
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stdout, "")
        self.assertEqual(missing.stderr, "error: --read /x: not a file\n")

    def test_all_capped_catalog_names_the_prompt_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "only-haiku.json"
            repo.put(path, runnable_haiku_catalog())
            for role in ("bug-fix", "verifiers"):
                completed = repo.run(
                    "show", "--catalog", str(path), "--parent", "grok/grok-4.7", "--role", role,
                )
                self.assertEqual(completed.returncode, 2, completed.stdout)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    f"error: role {role!r} has no seat: every runnable model in the catalog is capped "
                    "(claude-haiku-5-5 at 100000 prompt tokens), and only skill tests may run a capped model\n",
                )

    def test_bounded_seat_picks_the_later_lower_effort_haiku(self):
        catalog = {
            "providers": [{
                "providerInstanceId": "claudeAgent",
                "canRunChildTask": True,
                "constraints": [],
                "models": [
                    {"id": "claude-haiku-5-5", "options": [
                        {"id": "effort", "type": "select", "options": [{"id": "high", "isDefault": True}, {"id": "low"}]},
                        {"id": "contextWindow", "type": "select", "options": [{"id": "100k"}, {"id": "200k"}]},
                    ]},
                    {"id": "anthropic/claude-haiku-5-5", "options": [
                        {"id": "effort", "type": "select", "options": [{"id": "low", "isDefault": True}, {"id": "high"}]},
                        {"id": "contextWindow", "type": "select", "options": [{"id": "1m"}, {"id": "300k"}]},
                    ]},
                ],
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "tie.json"
            brief = repo.directory / "brief.txt"
            repo.put(path, catalog)
            brief.write_bytes(b"tiny\n")
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(path),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["capped"], True)
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["seat"], {
            "providerInstanceId": "claudeAgent",
            "model": "anthropic/claude-haiku-5-5",
            "options": {"contextWindow": "300k"},
        })

    def test_bounded_seat_refuses_an_over_target_haiku_parent_without_an_uncapped_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = repo.directory / "cap-only.json"
            brief = repo.directory / "brief.txt"
            read = repo.directory / "read.txt"
            repo.put(catalog, cap_only_catalog())
            brief.write_bytes(b"y" * 86)
            read.write_bytes(b"x" * 600000)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(catalog),
                "--parent", "claudeAgent/claude-haiku-5-5",
                "--brief", str(brief),
                "--read", str(read),
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "error: role 'skill tests' has no uncapped seat for this test: "
            "estimate 191022 tokens is over the 100000-token target for claude-haiku-5-5, "
            "no runnable model in the catalog is uncapped, "
            "and the parent claudeAgent/claude-haiku-5-5 is capped\n",
        )

    def test_bounded_seat_inherits_an_uncapped_parent_when_every_runnable_model_is_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = repo.directory / "cap-only.json"
            brief = repo.directory / "brief.txt"
            read = repo.directory / "read.txt"
            repo.put(catalog, cap_only_catalog())
            brief.write_bytes(b"y" * 86)
            read.write_bytes(b"x" * 600000)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(catalog),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
                "--read", str(read),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        body = json.loads(completed.stdout)
        self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
        self.assertEqual(body["seat"], "inherit")
        self.assertEqual(body["capped"], False)
        self.assertEqual(body["reason"], "estimate 191022 tokens is over the 100000-token target for claude-haiku-5-5")
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 86,
            "readBytes": 600000,
            "tokens": 191022,
            "target": 100000,
        })

    def test_bounded_seat_resolves_a_missing_configured_model(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"skill tests": [
                {"providerInstanceId": "claudeAgent", "model": "claude-haiku-old"},
            ]}})
            brief = repo.directory / "brief.txt"
            brief.write_bytes(b"y" * 86)
            show = repo.run(
                "show", "--catalog", str(CATALOG), "--parent", "grok/grok-4.7", "--role", "skill tests",
            )
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
            )
        self.assertEqual(show.returncode, 0, show.stderr)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        shown = json.loads(show.stdout)["roles"]["skill tests"]
        body = json.loads(completed.stdout)
        seat = {"providerInstanceId": "claudeAgent", "model": "claude-opus-5-5"}
        self.assertEqual(shown["seats"], [seat])
        self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
        self.assertEqual(body["seat"], seat)
        self.assertEqual(body["seat"], shown["seats"][0])
        self.assertEqual(body["capped"], False)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["notes"], [
            "claudeAgent/claude-haiku-old is not in the catalog; using claude-opus-5-5",
        ])
        self.assertEqual(body["notes"], shown["notes"])
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 86,
            "readBytes": 0,
            "tokens": 41022,
            "target": None,
        })

    def test_bounded_seat_inherits_for_an_unrunnable_configured_provider(self):
        cases = (
            (
                {"providerInstanceId": "cursor", "model": "claude-haiku-5-5"},
                "cursor is not runnable (Provider is not authenticated.); seat inherits the parent",
            ),
            (
                {"providerInstanceId": "disabled-provider", "model": "claude-haiku-5-5"},
                "disabled-provider is not runnable (not in catalog); seat inherits the parent",
            ),
        )
        for configured, note in cases:
            with self.subTest(provider=configured["providerInstanceId"]):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.user, {"roles": {"skill tests": [configured]}})
                    brief = repo.directory / "brief.txt"
                    brief.write_bytes(b"y" * 86)
                    completed = repo.run(
                        "bounded-seat",
                        "--catalog", str(CATALOG),
                        "--parent", "grok/grok-4.7",
                        "--brief", str(brief),
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                body = json.loads(completed.stdout)
                self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
                self.assertEqual(body["seat"], "inherit")
                self.assertEqual(body["capped"], False)
                self.assertIsNone(body["reason"])
                self.assertEqual(body["notes"], [note])
                self.assertEqual(body["estimate"], {
                    "overheadTokens": 41000,
                    "briefBytes": 86,
                    "readBytes": 0,
                    "tokens": 41022,
                    "target": None,
                })

    def test_bounded_seat_gates_a_configured_inherit_on_a_haiku_parent(self):
        cases = (
            (2000, "inherit", True, None, 41750),
            (
                235004,
                {"providerInstanceId": "codex", "model": "gpt-6-luna"},
                False,
                "estimate 100001 tokens is over the 100000-token target for claude-haiku-5-5",
                100001,
            ),
        )
        for read_bytes, seat, capped, reason, tokens in cases:
            with self.subTest(tokens=tokens):
                with tempfile.TemporaryDirectory() as directory:
                    repo = Repo(directory)
                    repo.put(repo.user, {"roles": {"skill tests": ["inherit"]}})
                    brief = repo.directory / "brief.txt"
                    read = repo.directory / "read.txt"
                    brief.write_bytes(b"y" * 1000)
                    read.write_bytes(b"z" * read_bytes)
                    completed = repo.run(
                        "bounded-seat",
                        "--catalog", str(CATALOG),
                        "--parent", "claudeAgent/claude-haiku-5-5",
                        "--brief", str(brief),
                        "--read", str(read),
                    )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                body = json.loads(completed.stdout)
                self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
                self.assertEqual(body["seat"], seat)
                self.assertEqual(body["capped"], capped)
                self.assertEqual(body["reason"], reason)
                self.assertEqual(body["notes"], [])
                self.assertEqual(body["estimate"], {
                    "overheadTokens": 41000,
                    "briefBytes": 1000,
                    "readBytes": read_bytes,
                    "tokens": tokens,
                    "target": 100000,
                })

    def test_bounded_seat_keeps_under_target_haiku_when_every_model_is_capped(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            catalog = repo.directory / "cap-only.json"
            brief = repo.directory / "brief.txt"
            repo.put(catalog, cap_only_catalog())
            brief.write_bytes(b"y" * 86)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(catalog),
                "--parent", "claudeAgent/claude-haiku-5-5",
                "--brief", str(brief),
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        body = json.loads(completed.stdout)
        self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
        self.assertEqual(body["seat"], {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5"})
        self.assertEqual(body["capped"], True)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["notes"], [])
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 86,
            "readBytes": 0,
            "tokens": 41022,
            "target": 100000,
        })

    def test_bounded_seat_drops_an_unknown_option(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"roles": {"skill tests": [
                {"providerInstanceId": "grok", "model": "grok-4.7", "options": {"nope": "x"}},
            ]}})
            brief = repo.directory / "brief.txt"
            brief.write_bytes(b"y" * 86)
            show = repo.run(
                "show", "--catalog", str(CATALOG), "--parent", "grok/grok-4.7", "--role", "skill tests",
            )
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
            )
        self.assertEqual(show.returncode, 0, show.stderr)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        shown = json.loads(show.stdout)["roles"]["skill tests"]
        body = json.loads(completed.stdout)
        seat = {"providerInstanceId": "grok", "model": "grok-4.7"}
        self.assertEqual(shown["seats"], [seat])
        self.assertEqual(list(body), ["seat", "capped", "estimate", "reason", "notes"])
        self.assertEqual(body["seat"], seat)
        self.assertEqual(body["seat"], shown["seats"][0])
        self.assertEqual(body["capped"], False)
        self.assertIsNone(body["reason"])
        self.assertEqual(body["notes"], ["dropped unknown options nope"])
        self.assertEqual(body["notes"], shown["notes"])
        self.assertEqual(body["estimate"], {
            "overheadTokens": 41000,
            "briefBytes": 86,
            "readBytes": 0,
            "tokens": 41022,
            "target": None,
        })

    def test_light_mode_keeps_every_launch_model(self):
        def launch_models(payload):
            found = {}
            for name, entry in payload["roles"].items():
                models = []
                for seat in entry["seats"]:
                    models.append("claude-opus-5-5" if seat == "inherit" else seat["model"])
                found[name] = models
            return found

        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            full = repo.run("show", "--catalog", str(CATALOG), "--parent", "claudeAgent/claude-opus-5-5")
            repo.put(repo.user, {"mode": "light"})
            light = repo.run("show", "--catalog", str(CATALOG), "--parent", "claudeAgent/claude-opus-5-5")
        for completed in (full, light):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(launch_models(json.loads(completed.stdout)), LAUNCH_MODELS)
            self.assertNotIn("claude-haiku-5-5", completed.stdout)

    def test_light_mode_refuses_a_haiku_parent_for_bug_fix(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "roles": {"bug-fix": ["inherit"]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "claudeAgent/claude-haiku-5-5",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, f"error: {INHERIT_CAP}\n")

    def test_light_skill_tests_show_replaces_haiku_5_5(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light", "roles": {"skill tests": [{
                "providerInstanceId": "claudeAgent",
                "model": "claude-haiku-5-5",
            }]}})
            completed = repo.run(
                "show",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--role", "skill tests",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        entry = json.loads(completed.stdout)["roles"]["skill tests"]
        self.assertEqual(entry["seats"], [HAIKU_4_SEAT])
        self.assertEqual(entry["note"], SKILL_TESTS_NOTE)

    def test_bounded_seat_caps_a_light_haiku(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            brief = repo.directory / "brief.txt"
            read = repo.directory / "read.txt"
            brief.write_bytes(b"y" * 1000)
            read.write_bytes(b"x" * 2000)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
                "--read", str(read),
                "--session-mode", "light",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        body = json.loads(completed.stdout)
        self.assertEqual(body["seat"], HAIKU_5_MEDIUM)
        self.assertEqual(body["capped"], True)
        self.assertEqual(body["estimate"]["tokens"], 41750)
        self.assertEqual(body["estimate"]["target"], 100000)

    def test_bounded_seat_brief_mode_full_keeps_the_bare_seat(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            repo.put(repo.user, {"mode": "light"})
            brief = repo.directory / "brief.txt"
            brief.write_bytes(b"y" * 1000)
            completed = repo.run(
                "bounded-seat",
                "--catalog", str(CATALOG),
                "--parent", "grok/grok-4.7",
                "--brief", str(brief),
                "--brief-mode", "full",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout)["seat"],
            {"providerInstanceId": "claudeAgent", "model": "claude-haiku-5-5"},
        )

    def test_light_show_keeps_an_uncapped_model_when_grok_is_absent(self):
        catalog = json.loads(CATALOG.read_text())
        catalog["providers"] = [
            provider for provider in catalog["providers"] if provider["providerInstanceId"] != "grok"
        ]
        with tempfile.TemporaryDirectory() as directory:
            repo = Repo(directory)
            path = repo.directory / "catalog.json"
            repo.put(path, catalog)
            repo.put(repo.user, {"mode": "light"})
            completed = repo.run(
                "show",
                "--catalog", str(path),
                "--parent", "claudeAgent/claude-opus-5-5",
                "--role", "bug-fix",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["roles"]["bug-fix"]["seats"], [{
            "providerInstanceId": "claudeAgent",
            "model": "claude-opus-5-5",
            "options": {"effort": "medium"},
        }])


INHERIT_CAP = (
    "role 'bug-fix' cannot inherit claudeAgent/claude-haiku-5-5: "
    "claude-haiku-5-5 is capped at 100000 prompt tokens, and only skill tests may run a capped model"
)
SKILL_TESTS_NOTE = (
    "claude-haiku-5-5 is capped; roles.py bounded-seat launches it when the whole prompt fits"
)
HAIKU_4_SEAT = {"providerInstanceId": "claudeAgent", "model": "claude-haiku-4-5"}


def parent_error(text):
    return (
        f"error: --parent {text!r}: expected "
        "'<inheritedProviderInstanceId>/<inheritedModel>' from orchestrator_capabilities\n"
    )


def runnable_haiku_catalog():
    return {
        "inheritedProviderInstanceId": "claudeAgent",
        "inheritedModel": "claude-haiku-5-5",
        "providers": [{
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "constraints": [],
            "models": [{"id": "claude-haiku-5-5", "options": []}],
        }],
    }


def cap_only_catalog():
    return {
        "providers": [{
            "providerInstanceId": "claudeAgent",
            "canRunChildTask": True,
            "models": [{"id": "claude-haiku-5-5", "options": []}],
        }],
    }
