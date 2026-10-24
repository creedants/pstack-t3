if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import json
import re
from .support import CATALOG, GROK_SEAT, INHERIT_ENTRY, ModeCliCase, OPUS_HIGH, OPUS_MEDIUM, OPUS_SEAT, ROOT, Repo, setUpModule


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


class ModeCliTest(ModeCliCase):
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
        self.assertIn("**/migrations/**", head)
        self.assertIn(
            "Suggest `**/migrations/**` plus the lock, install, and deploy scripts the repository keeps.",
            head,
        )
        for path in (
            "t3/added/landing/scripts/land.py",
            "t3/added/brigade/scripts/brigade.py",
            "scripts/install.py",
        ):
            self.assertNotIn(path, head)
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
