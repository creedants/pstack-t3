from .support import (
    AT_EXIT,
    DURING_THE_TEST,
    ESCAPE,
    GOOD,
    HANG_AT_IMPORT,
    INHERITED,
    LAUNCHER,
    LAUNCHER_CHILD,
    LINUX,
    MARKS,
    NEEDS_LINUX,
    NEVER_AN_EVENT,
    PASSING,
    Path,
    RAN,
    RELEASES_THE_PARENT,
    RESULT_FILE,
    ROOT,
    RUNNER,
    WRITES_TO_ITS_RESULT_FILE,
    json,
    os,
    re,
    run_tests,
    setUpModule,
    signal,
    subprocess,
    sys,
    tempfile,
    textwrap,
    time,
    unittest,
)
from .workflow import (
    MACOS_PARTS,
    job_body,
    macos_part_run,
)


class CiJobsMixin:
    def test_ci_runs_twelve_part_jobs_and_two_gates(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        try:
            import yaml
        except ImportError:
            self.assertIn("needs: [part_310_1, part_310_2, part_310_3, part_310_4]", text)
            self.assertIn("needs: [part_312_1, part_312_2, part_312_3, part_312_4, macos_312_1, macos_312_2, macos_312_3, macos_312_4]", text)
            self.assertEqual(text.count("if: always()"), 2)
            self.assertIn("PSTACK_TEST_REAL_PROCESS: '1'", text)
            self.assertNotIn("real_312", text)
            self.assertNotIn("--real-process-share", text)
            self.assertNotIn("github.event_name == 'pull_request'", text)
            self.assertIsNone(job_body(text.splitlines(keepends=True), "macos_312"))
            for index in range(1, 5):
                self.assertEqual(text.count(f"python3 scripts/run_tests.py --part {index}/4"), 3)
                body = job_body(text.splitlines(keepends=True), f"macos_312_{index}")
                self.assertIsNotNone(body)
                joined = "".join(body)
                self.assertNotIn("strategy", joined)
                self.assertNotIn("env:", joined)
                self.assertNotIn("needs:", joined)
                self.assertIn(f"name: macOS (3.12, {index}/4)", joined)
                self.assertIn("macos-latest", joined)
                self.assertIn("if: github.event_name == 'push'", joined)
                self.assertEqual(joined.count(f"python3 scripts/run_tests.py --part {index}/4"), 1)
                self.assertNotIn("--real-process-share", joined)
                self.assertIn("grep -E '^Ran [0-9]+ tests? in '", joined)
                self.assertIn("grep -E '^(OK|FAILED)'", joined)
                self.assertIn("grep -E '^(FAIL|ERROR): '", joined)
                self.assertIn('>> "$GITHUB_STEP_SUMMARY"', joined)
                for other in range(1, 5):
                    if other != index:
                        self.assertNotIn(f"--part {other}/4", joined)
                if index == 1:
                    self.assertIn("Generated skills match the sources", joined)
                else:
                    self.assertNotIn("Generated skills match the sources", joined)
            return
        jobs = yaml.safe_load(text)["jobs"]
        self.assertEqual(list(jobs), [
            "part_310_1", "part_310_2", "part_310_3", "part_310_4",
            "part_312_1", "part_312_2", "part_312_3", "part_312_4",
            "test_310", "test_312",
            "macos_312_1", "macos_312_2", "macos_312_3", "macos_312_4",
        ])
        for version, prefix in (("3.10", "part_310"), ("3.12", "part_312")):
            for index in range(1, 5):
                job = jobs[f"{prefix}_{index}"]
                self.assertEqual(job["name"], f"part ({version}, {index}/4)")
                self.assertEqual(job["runs-on"], "ubuntu-latest")
                self.assertNotIn("if", job)
                self.assertNotIn("strategy", job)
                setup = next(step for step in job["steps"] if step.get("uses") == "actions/setup-python@v7")
                self.assertEqual(setup["with"]["python-version"], version)
                self.assertIsInstance(setup["with"]["python-version"], str)
                self.assertEqual(job["steps"][0]["uses"], "actions/checkout@v7")
                runs = [step["run"] for step in job["steps"] if "run" in step]
                self.assertEqual(len(runs), 3)
                self.assertEqual(runs[0], "pip install pyyaml")
                self.assertIn("select_browser", runs[1])
                self.assertEqual(runs[2], f"python3 scripts/run_tests.py --part {index}/4")
                self.assertFalse(any(step.get("name") == "Generated skills match the sources" for step in job["steps"]))
        self.assertNotIn("real_312", text)
        self.assertNotIn("--real-process-share", text)
        self.assertNotIn("strategy:", text)
        self.assertNotIn("fail-fast", text)
        for version, gate, prefix in (("3.10", "test_310", "part_310"), ("3.12", "test_312", "part_312")):
            job = jobs[gate]
            self.assertEqual(job["name"], f"test ({version})")
            self.assertEqual(job["if"], "always()")
            if gate == "test_312":
                self.assertEqual(job["needs"], [f"part_312_{index}" for index in range(1, 5)] + list(MACOS_PARTS))
            else:
                self.assertEqual(job["needs"], [f"{prefix}_{index}" for index in range(1, 5)])
            self.assertEqual(job["runs-on"], "ubuntu-latest")
            setup = next(step for step in job["steps"] if step.get("uses") == "actions/setup-python@v7")
            self.assertEqual(setup["with"]["python-version"], version)
            self.assertIsInstance(setup["with"]["python-version"], str)
            check = next(step["run"] for step in job["steps"] if step.get("name") != "Generated skills match the sources" and "needs." in step.get("run", ""))
            for index in range(1, 5):
                self.assertIn(f'test "${{{{ needs.{prefix}_{index}.result }}}}" = success', check)
            self.assertNotIn("real_312", check)
            skill = next(step for step in job["steps"] if step.get("name") == "Generated skills match the sources")
            self.assertEqual(skill["run"], "python3 scripts/build.py\n"
                             "git diff --exit-code -- skills docs/skills.md docs/cli\n"
                             "test -z \"$(git status --porcelain -- skills docs/skills.md docs/cli)\"\n")
        named = [job_id for job_id, job in jobs.items()
                 if any(step.get("name") == "Generated skills match the sources" for step in job["steps"])]
        self.assertEqual(named, ["test_310", "test_312", "macos_312_1"])
        self.assertNotIn("macos_312", jobs)
        for index in range(1, 5):
            macos = jobs[f"macos_312_{index}"]
            self.assertEqual(macos["name"], f"macOS (3.12, {index}/4)")
            self.assertEqual(macos["runs-on"], "macos-latest")
            self.assertEqual(macos.get("if"), "github.event_name == 'push'", "macos if pins the push-only rule.")
            self.assertNotIn("strategy", macos)
            self.assertNotIn("needs", macos)
            self.assertNotIn("env", macos)
            steps = macos["steps"]
            self.assertEqual(len(steps), 6 if index == 1 else 5)
            self.assertEqual(steps[0].get("uses"), "actions/checkout@v7")
            self.assertEqual(steps[1].get("uses"), "actions/setup-python@v7")
            self.assertEqual(steps[1]["with"]["python-version"], "3.12")
            self.assertIsInstance(steps[1]["with"]["python-version"], str)
            self.assertEqual(steps[2].get("run"), "pip install pyyaml")
            self.assertIn("select_browser", steps[3]["run"])
            self.assertEqual(steps[4].get("name"), f"Test part {index}/4")
            self.assertEqual(steps[4]["run"], macos_part_run(index))
            self.assertNotIn("--real-process-share", steps[4]["run"])
            if index == 1:
                self.assertEqual(steps[5].get("name"), "Generated skills match the sources")
                self.assertNotIn("if", steps[5])
                gate_skill = next(step for step in jobs["test_312"]["steps"]
                                  if step.get("name") == "Generated skills match the sources")
                self.assertEqual(steps[5]["run"], gate_skill["run"])

    def test_the_browser_step_creates_its_temporary_directory_with_ignore_cleanup_errors(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        needle = "tempfile.TemporaryDirectory(ignore_cleanup_errors=True)"
        try:
            import yaml
        except ImportError:
            self.assertIn(needle, text)
            return
        jobs = yaml.safe_load(text)["jobs"]
        for prefix in ("part_310", "part_312"):
            for index in range(1, 5):
                runs = [step["run"] for step in jobs[f"{prefix}_{index}"]["steps"] if "run" in step]
                self.assertIn(needle, runs[1])

    def test_the_browser_step_tries_select_browser_again_when_the_first_try_returns_no_path(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        try:
            import yaml
        except ImportError:
            self.assertEqual(text.count("select_browser(directory)"), 1)
            self.assertEqual(text.count("select_browser(directory, timeout=60)"), 1)
            return
        jobs = yaml.safe_load(text)["jobs"]
        for prefix in ("part_310", "part_312"):
            for index in range(1, 5):
                runs = [step["run"] for step in jobs[f"{prefix}_{index}"]["steps"] if "run" in step]
                self.assertEqual(runs[1].count("select_browser(directory)"), 1)
                self.assertEqual(runs[1].count("select_browser(directory, timeout=60)"), 1)

    def test_the_workflow_concurrency_group_is_the_workflow_and_ref_with_cancel_in_progress(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        try:
            import yaml
        except ImportError:
            lines = text.splitlines()
            self.assertIn("concurrency:", lines)
            start = lines.index("concurrency:")
            self.assertEqual(lines[start + 1], "  group: ${{ github.workflow }}-${{ github.ref }}")
            self.assertEqual(lines[start + 2], "  cancel-in-progress: true")
            return
        block = yaml.safe_load(text).get("concurrency")
        self.assertIsNotNone(block, "concurrency block is present.")
        self.assertEqual(block["group"], "${{ github.workflow }}-${{ github.ref }}", "concurrency group pins the workflow and ref.")
        self.assertIs(block["cancel-in-progress"], True, "cancel-in-progress pins true.")

