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

    def test_an_over_limit_file_lands_without_a_contract_limit_or_baseline(self):
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
        self.commit("big")
        self.assertIn("size ok", self.land("size"))
        self.assertTrue((self.work / "limits/sizes.json").is_file())

    def test_a_baseline_filename_without_enabled_marker_still_lands(self):
        self.init(mode="push")
        (self.work / "size-baseline.json").write_text(json.dumps(
            {"maxFileTokens": 5000, "files": {}, "skip": []}))
        self.commit("incidental baseline")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        sha = self.worker("w1", {"big.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "big.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_status_json_without_a_limit_keeps_the_prior_shape(self):
        self.init(mode="push")
        payload = json.loads(self.land("status", "--json"))
        self.assertNotIn("sizeMaxTokens", payload)
        self.assertNotIn("sizeBaseline", payload)
        self.assertEqual(set(payload), {"mode", "trunk", "generated", "rebuild", "map"})
        self.assertNotIn("size limit", self.land("status"))

    def test_a_staged_new_file_does_not_seed_its_own_allowance(self):
        self.init_with_limit()
        (self.work / "big.py").write_text("x" * 20004)
        sh("git", "add", "-A", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("big.py", reported)
        baseline = self.work / "size-baseline.json"
        if baseline.is_file():
            self.assertNotEqual(json.loads(baseline.read_text()).get("files", {}).get("big.py"), 5001)

    def test_a_staged_growth_with_an_unstaged_allowance_increase_still_bounces(self):
        (self.work / "old.py").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        (self.work / "old.py").write_text("x" * 28000)
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["files"]["old.py"] = 7000
        (self.work / "size-baseline.json").write_text(json.dumps(baseline))
        sh("git", "add", "old.py", cwd=self.work)
        reported = self.land("size", "--staged", ok=False)
        self.assertIn("old.py", reported)

    def test_an_allowance_only_entry_followed_by_growth_bounces_both(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.init_with_limit()
        base_raw = json.loads((self.work / "size-baseline.json").read_text())
        raised = dict(base_raw)
        raised_files = dict(base_raw.get("files", {}))
        raised_files["data.txt"] = 7000
        raised["files"] = raised_files
        e1 = self.worker("w1", {"size-baseline.json": json.dumps(raised, indent=2, sort_keys=True) + "\n"})
        e2 = self.worker("w2", {"data.txt": "x" * 28000})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "data.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", e1,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", e2,
                  "--lease", "L2", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1 (r/D1)", reported)
        self.assertIn("bounced E2 (r/D2)", reported)
        self.assertNotIn("landed", reported)

    def test_a_batch_of_allowance_then_growth_lands_neither(self):
        (self.work / "data.txt").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                  "--batch", "2", "--max-file-tokens", "5000")
        base_raw = json.loads((self.work / "size-baseline.json").read_text())
        raised = dict(base_raw)
        raised_files = dict(base_raw.get("files", {}))
        raised_files["data.txt"] = 7000
        raised["files"] = raised_files
        e1 = self.worker("w1", {"size-baseline.json": json.dumps(raised, indent=2, sort_keys=True) + "\n"})
        e2 = self.worker("w2", {"data.txt": "x" * 28000})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "size-baseline.json")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "data.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", e1,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", e2,
                  "--lease", "L2", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1 (r/D1)", reported)
        self.assertIn("bounced E2 (r/D2)", reported)
        self.assertNotIn("landed E1", reported)
        self.assertNotIn("landed E2", reported)

    def test_a_nested_generated_root_lands_over_the_limit(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                  "--generated", "build/output", "--max-file-tokens", "5000")
        sha = self.worker("w1", {"a.txt": "agent\n", "build/output/big.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt,build/output")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_shrink_uses_the_effective_maximum(self):
        (self.work / "old.py").write_text("x" * 24000)
        self.assertIn("size ok", self.land("size"))
        baseline = json.loads((self.work / "size-baseline.json").read_text())
        baseline["maxFileTokens"] = 5000
        baseline["files"] = {"old.py": 6000}
        (self.work / "size-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
        self.commit("seed")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh",
                  "--max-file-tokens", "4000")
        (self.work / "old.py").write_text("x" * 18000)
        self.assertIn("size ok", self.land("size", "--shrink"))
        shrunk = json.loads((self.work / "size-baseline.json").read_text())
        self.assertEqual(shrunk["files"].get("old.py"), 4500)

    def test_an_uppercase_image_svg_and_nested_lockfile_land_over_the_limit(self):
        self.init_with_limit()
        sha = self.worker("w1", {"a.txt": "agent\n",
                                 "photo.PNG": "x" * 20004,
                                 "diagram.svg": "x" * 20004,
                                 "web/package-lock.json": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1",
                  "--paths", "a.txt,photo.PNG,diagram.svg,web/package-lock.json")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed E1 (r/D1)")

    def test_a_project_source_under_skills_still_bounces(self):
        self.init_with_limit()
        sha = self.worker("w1", {"skills/agent.py": "x" * 20004})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "skills/agent.py")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha,
                  "--lease", "L1", "--reviewer", REVIEWER)
        reported = self.land("land")
        self.assertIn("bounced E1 (r/D1)", reported)
        self.assertIn("skills/agent.py", reported)
