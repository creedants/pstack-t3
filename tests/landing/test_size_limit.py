import json

from .support import (
    setUpModule,
    LandingTest as LandingTestBase,
    REVIEWER,
    sh,
)


class LandingTest(LandingTestBase):
    def init_with_limit(self, maximum=5000, extra=()):
        args = ["init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                "--max-file-tokens", str(maximum), *extra]
        return self.land(*args)

    def test_an_over_limit_file_lands_while_the_contract_sets_no_limit(self):
        self.init(mode="push")
        sha = self.worker("w1", {"big.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "big.py")
        self.assertEqual(self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                                   "--lease", "L1", "--reviewer", REVIEWER), "E1")
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")
        self.assertNotIn("size limit", self.land("status"))

    def test_a_new_file_over_the_limit_bounces_at_land_time(self):
        self.init_with_limit()
        sha = self.worker("w1", {"big.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "big.py")
        self.assertEqual(self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                                   "--lease", "L1", "--reviewer", REVIEWER), "E1")
        self.assertEqual(self.land("land"),
                         "bounced E1 (r/D1): big.py: 5001 tokens over allowance 5000 for code; "
                         "code moves one responsibility to a sibling module")

    def test_a_file_grown_past_its_baseline_allowance_bounces(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        grown = self.worker("w1", {"data.txt": "x" * 28000})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "data.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", grown, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"),
                         "bounced E1 (r/D1): data.txt: 7000 tokens over allowance 6000 for text; "
                         "skill and doc text moves rarely needed sections to an on-demand file with a pointer saying when to read it")

    def test_a_file_shrunk_below_its_allowance_lands(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.land("size")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        shrunk = self.worker("w1", {"data.txt": "x" * 20000})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "data.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", shrunk, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_generated_vendored_image_and_lockfile_paths_land_over_the_limit(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                  "--generated", "gen", "--max-file-tokens", "5000")
        sha = self.worker("w1", {"a.txt": "agent\n",
                                 "gen/out.txt": "x" * 20004,
                                 "vendor/v.py": "x" * 20004,
                                 "pic.png": "x" * 20004,
                                 "package-lock.json": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1",
                  "--paths", "a.txt,gen,vendor,pic.png,package-lock.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_staged_size_judges_the_index(self):
        self.land("size")
        (self.work / "big.py").write_text("x" * 20004)
        self.assertIn("size ok", self.land("size", "--staged"))
        sh("git", "add", "-A", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("big.py: 5001 tokens over allowance 5000 for code", reported)
        self.assertIn("code moves one responsibility to a sibling module", reported)

    def test_size_names_the_test_and_text_split_steps(self):
        self.land("size")
        (self.work / "notes.md").write_text("x" * 20004)
        (self.work / "test_big.py").write_text("x" * 20004)
        sh("git", "add", "-A", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("notes.md: 5001 tokens over allowance 5000 for text", reported)
        self.assertIn("rarely needed sections to an on-demand file", reported)
        self.assertIn("test_big.py: 5001 tokens over allowance 5000 for test", reported)
        self.assertIn("tests start a new file for the feature and share helpers", reported)

    def test_shrink_lowers_a_fallen_allowance_and_never_raises_one(self):
        (self.work / "data.txt").write_text("x" * 24000)
        (self.work / "other.txt").write_text("x" * 24000)
        self.land("size")
        (self.work / "data.txt").write_text("x" * 20000)
        (self.work / "other.txt").write_text("x" * 28000)
        reported = self.land("size", "--shrink", ok=False)
        self.assertIn("other.txt: 7000 tokens over allowance 6000", reported)
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        self.assertNotIn("data.txt", baseline["files"])
        self.assertEqual(baseline["files"]["other.txt"], 6000)
        reported = self.land("size", ok=False)
        self.assertIn("other.txt: 7000 tokens over allowance 6000", reported)

    def test_a_size_init_rerun_without_flags_keeps_the_limit(self):
        self.init_with_limit()
        self.assertIn("size limit 5000 tokens via size-baseline.json", self.land("status"))
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh")
        self.assertIn("size limit 5000 tokens via size-baseline.json", self.land("status"))
        payload = json.loads(self.land("status", "--json"))
        self.assertEqual(payload["sizeMaxTokens"], 5000)
        self.assertEqual(payload["sizeBaseline"], None)

    def test_a_size_baseline_flag_names_another_file(self):
        self.init_with_limit(extra=("--size-baseline", "limits/sizes.json"))
        payload = json.loads(self.land("status", "--json"))
        self.assertEqual(payload["sizeBaseline"], "limits/sizes.json")
        self.assertIn("size limit 5000 tokens via limits/sizes.json", self.land("status"))
        (self.work / "big.py").write_text("x" * 20004)
        self.assertIn("size ok", self.land("size"))
        self.assertTrue((self.work / "limits/sizes.json").is_file())
