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
import tempfile
import unittest
from tests import in_process
from .support import CATALOG, INHERIT_ENTRY, ROOT, Repo, roles, setUpModule, show


HAIKU_BRIEF = list(roles.HAIKU_BRIEF)


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
