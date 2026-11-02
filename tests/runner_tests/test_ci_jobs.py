if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from tests.activity_tests.browser import SOCKET_LIMIT

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
    BROWSER_LINUX,
    BROWSER_MACOS,
    GENERATED,
    MACOS_PARTS,
    PARTS,
    job_body,
    macos_part_run,
)

MACOS_PRIVATE_ROOTS = ("/tmp", "/var", "/etc")

# 22 run root, 16 browser temp dir, 45 socket suffix (reports/D7.md in the brigade store).
SELECT_BROWSER_SOCKET_OFFSET = 22 + 16 + 45

# 1 + 10 + pid + 1 + 8 scratch name, 5 shard home, 4 worker TMPDIR, 12 browser temp dir, 45 suffix (reports/D7.md in the brigade store).
WORKER_SOCKET_OFFSET = 1 + 10 + 7 + 1 + 8 + 5 + 4 + 12 + 45

GENERATED_RUN = (
    "python3 scripts/build.py\n"
    "git diff --exit-code -- skills docs/skills.md docs/cli\n"
    "test -z \"$(git status --porcelain -- skills docs/skills.md docs/cli)\"\n"
)

JOB_ORDER = (
    tuple(f"part_310_{index}" for index in range(1, PARTS + 1))
    + tuple(f"part_312_{index}" for index in range(1, PARTS + 1))
    + ("test_310", "test_312", GENERATED, BROWSER_LINUX, BROWSER_MACOS)
    + MACOS_PARTS
)


def workflow_environment(text):
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line == "env:"), None)
    values = {}
    if start is None:
        return values
    for line in lines[start + 1:]:
        if line.strip() and not line.startswith(" "):
            break
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        value = value.strip()
        quote = None
        escaped = False
        for index, char in enumerate(value):
            if escaped:
                escaped = False
            elif char == "\\" and quote == '"':
                escaped = True
            elif char == quote == "'" and value[index:index + 2] == "''":
                escaped = True
            elif char in "'\"" and (index == 0 or char == quote):
                quote = None if quote else char
            elif char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
                value = value[:index].rstrip()
                break
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def macos_path_length(path):
    for root in MACOS_PRIVATE_ROOTS:
        if path == root or path.startswith(root + "/"):
            return len(path) + len("/private")
    return len(path)


class CiJobsMixin:
    def test_ci_runs_sixteen_linux_parts_two_browser_jobs_generated_and_two_gates(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        try:
            import yaml
        except ImportError:
            self.assertIn("needs: [part_310_1, part_310_2, part_310_3, part_310_4, part_310_5, part_310_6, part_310_7, part_310_8, browser_linux, generated]", text)
            self.assertIn("needs: [part_312_1, part_312_2, part_312_3, part_312_4, part_312_5, part_312_6, part_312_7, part_312_8, browser_linux, generated, browser_macos, macos_312_1, macos_312_2, macos_312_3, macos_312_4]", text)
            self.assertEqual(text.count("if: always()"), 2)
            self.assertIn("PSTACK_TEST_REAL_PROCESS: '1'", text)
            self.assertNotIn("real_312", text)
            self.assertNotIn("--real-process-share", text)
            self.assertIsNone(job_body(text.splitlines(keepends=True), "macos_312"))
            for version, prefix in (("3.10", "part_310"), ("3.12", "part_312")):
                for index in range(1, PARTS + 1):
                    self.assertIn(f"name: part ({version}, {index}/{PARTS})", text)
                    self.assertEqual(text.count(f"python3 scripts/run_tests.py --part {index}/{PARTS}"), 2)
            for index in range(1, 5):
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
                self.assertNotIn("select_browser", joined)
                self.assertNotIn("Generated skills match the sources", joined)
                self.assertIn("grep -E '^Ran [0-9]+ tests? in '", joined)
                self.assertIn("grep -E '^(OK|FAILED)'", joined)
                self.assertIn("grep -E '^(FAIL|ERROR): '", joined)
                self.assertIn('>> "$GITHUB_STEP_SUMMARY"', joined)
                for other in range(1, 5):
                    if other != index:
                        self.assertNotIn(f"--part {other}/4", joined)
            self.assertIn("name: generated files", text)
            self.assertIn("name: browser (linux)", text)
            self.assertIn("name: browser (macOS)", text)
            return
        jobs = yaml.safe_load(text)["jobs"]
        self.assertEqual(list(jobs), list(JOB_ORDER))
        for version, prefix in (("3.10", "part_310"), ("3.12", "part_312")):
            for index in range(1, PARTS + 1):
                job = jobs[f"{prefix}_{index}"]
                self.assertEqual(job["name"], f"part ({version}, {index}/{PARTS})")
                self.assertEqual(job["runs-on"], "ubuntu-latest")
                self.assertEqual(job.get("if"), "github.event_name == 'pull_request'")
                self.assertNotIn("strategy", job)
                self.assertNotIn("needs", job)
                setup = next(step for step in job["steps"] if step.get("uses") == "actions/setup-python@v7")
                self.assertEqual(setup["with"]["python-version"], version)
                self.assertIsInstance(setup["with"]["python-version"], str)
                self.assertEqual(job["steps"][0]["uses"], "actions/checkout@v7")
                runs = [step["run"] for step in job["steps"] if "run" in step]
                self.assertEqual(runs, ["pip install pyyaml", f"python3 scripts/run_tests.py --part {index}/{PARTS}"])
                self.assertEqual(len(job["steps"]), 4)
                self.assertFalse(any(step.get("name") == "Generated skills match the sources" for step in job["steps"]))
                self.assertFalse(any("select_browser" in step.get("run", "") for step in job["steps"]))
        self.assertNotIn("real_312", text)
        self.assertNotIn("--real-process-share", text)
        self.assertNotIn("strategy:", text)
        self.assertNotIn("fail-fast", text)
        generated = jobs[GENERATED]
        self.assertEqual(generated["name"], "generated files")
        self.assertEqual(generated["runs-on"], "ubuntu-latest")
        self.assertNotIn("if", generated)
        self.assertNotIn("needs", generated)
        self.assertEqual(generated["steps"][0]["uses"], "actions/checkout@v7")
        self.assertEqual(generated["steps"][1]["uses"], "actions/setup-python@v7")
        self.assertEqual(generated["steps"][1]["with"]["python-version"], "3.12")
        self.assertEqual(generated["steps"][2]["run"], "pip install pyyaml")
        skill = next(step for step in generated["steps"] if step.get("name") == "Generated skills match the sources")
        self.assertEqual(skill["run"], GENERATED_RUN)
        self.assertEqual(len(generated["steps"]), 4)
        for job_id, runner, event in ((BROWSER_LINUX, "ubuntu-latest", "pull_request"), (BROWSER_MACOS, "macos-latest", "push")):
            job = jobs[job_id]
            self.assertIn("browser", job["name"])
            self.assertEqual(job["runs-on"], runner)
            self.assertEqual(job.get("if"), f"github.event_name == '{event}'")
            self.assertNotIn("needs", job)
            self.assertEqual(job["steps"][0]["uses"], "actions/checkout@v7")
            self.assertEqual(job["steps"][1]["uses"], "actions/setup-python@v7")
            self.assertEqual(job["steps"][1]["with"]["python-version"], "3.12")
            self.assertEqual(job["steps"][2]["run"], "pip install pyyaml")
            probe = [step["run"] for step in job["steps"] if "run" in step and "select_browser" in step["run"]]
            self.assertEqual(len(probe), 1)
            self.assertIn("select_browser(directory, timeout=60)", probe[0])
        probe_jobs = [job_id for job_id, job in jobs.items()
                      if any("select_browser" in step.get("run", "") for step in job.get("steps", []))]
        self.assertEqual(sorted(probe_jobs), sorted([BROWSER_LINUX, BROWSER_MACOS]))
        for version, gate, prefix in (("3.10", "test_310", "part_310"), ("3.12", "test_312", "part_312")):
            job = jobs[gate]
            self.assertEqual(job["name"], f"test ({version})")
            self.assertEqual(job["if"], "always()")
            self.assertEqual(job["runs-on"], "ubuntu-latest")
            self.assertEqual(len(job["steps"]), 1)
            step = job["steps"][0]
            self.assertNotIn("uses", step)
            self.assertNotIn("name", step)
            self.assertNotIn("checkout", step.get("run", ""))
            self.assertNotIn("setup-python", step.get("run", ""))
            self.assertNotIn("pip install", step.get("run", ""))
            self.assertNotIn("scripts/build.py", step.get("run", ""))
            check = step["run"]
            for index in range(1, PARTS + 1):
                self.assertIn(f'test "${{{{ needs.{prefix}_{index}.result }}}}" = "$linux"', check)
            self.assertIn('test "${{ needs.browser_linux.result }}" = "$linux"', check)
            self.assertIn('test "${{ needs.generated.result }}" = success', check)
            self.assertNotIn("real_312", check)
        self.assertEqual(jobs["test_312"]["needs"], [f"part_312_{index}" for index in range(1, PARTS + 1)] + ["browser_linux", "generated", "browser_macos"] + list(MACOS_PARTS))
        self.assertEqual(jobs["test_310"]["needs"], [f"part_310_{index}" for index in range(1, PARTS + 1)] + ["browser_linux", "generated"])
        self.assertIn('test "${{ needs.browser_macos.result }}" = "$macos"', jobs["test_312"]["steps"][0]["run"])
        named = [job_id for job_id, job in jobs.items()
                 if any(step.get("name") == "Generated skills match the sources" for step in job["steps"])]
        self.assertEqual(named, [GENERATED])
        for index in range(1, 5):
            macos = jobs[f"macos_312_{index}"]
            self.assertEqual(macos["name"], f"macOS (3.12, {index}/4)")
            self.assertEqual(macos["runs-on"], "macos-latest")
            self.assertEqual(macos.get("if"), "github.event_name == 'push'", "macos if pins the push-only rule.")
            self.assertNotIn("strategy", macos)
            self.assertNotIn("needs", macos)
            self.assertNotIn("env", macos)
            steps = macos["steps"]
            self.assertEqual(len(steps), 4)
            self.assertEqual(steps[0].get("uses"), "actions/checkout@v7")
            self.assertEqual(steps[1].get("uses"), "actions/setup-python@v7")
            self.assertEqual(steps[1]["with"]["python-version"], "3.12")
            self.assertIsInstance(steps[1]["with"]["python-version"], str)
            self.assertEqual(steps[2].get("run"), "pip install pyyaml")
            self.assertFalse(any("select_browser" in step.get("run", "") for step in steps))
            self.assertFalse(any(step.get("name") == "Generated skills match the sources" for step in steps))
            self.assertEqual(steps[3].get("name"), f"Test part {index}/4")
            self.assertEqual(steps[3]["run"], macos_part_run(index))
            self.assertNotIn("--real-process-share", steps[3]["run"])

    def test_a_job_without_a_timeout_minutes_hangs_for_githubs_six_hour_default(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        part_ids = [f"part_310_{index}" for index in range(1, PARTS + 1)] + [f"part_312_{index}" for index in range(1, PARTS + 1)]
        try:
            import yaml
        except ImportError:
            lines = text.splitlines(keepends=True)
            self.assertEqual(sorted(re.findall(r"^  ([a-z0-9_]+):$", text.split("\njobs:\n", 1)[1], re.MULTILINE)),
                             sorted(JOB_ORDER), "the workflow holds the expected jobs.")
            expected = {job_id: 20 for job_id in part_ids}
            expected.update({job_id: 30 for job_id in MACOS_PARTS})
            for job_id in JOB_ORDER:
                with self.subTest(job=job_id):
                    self.assertIn(f"    timeout-minutes: {expected.get(job_id, 10)}\n",
                                  job_body(lines, job_id) or [], "every job sets its timeout-minutes.")
            return
        jobs = yaml.safe_load(text)["jobs"]
        for job_id, job in jobs.items():
            timeout = job.get("timeout-minutes")
            with self.subTest(job=job_id):
                self.assertIs(type(timeout), int, "every job sets an integer timeout-minutes.")
                self.assertGreater(timeout, 0, "every job sets a positive timeout-minutes.")
                if job_id in part_ids:
                    self.assertEqual(timeout, 20, "a part job fails a hang at 20 minutes.")
                elif job_id in MACOS_PARTS:
                    self.assertEqual(timeout, 30, "a macOS part job fails a hang at 30 minutes.")
                else:
                    self.assertGreaterEqual(timeout, 10, "every other job keeps at least 10 minutes.")

    def test_the_browser_probe_step_is_shared_by_anchor_and_alias(self):
        try:
            import yaml
        except ImportError:
            text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
            self.assertEqual(text.count("&select_browser"), 1)
            self.assertEqual(text.count("*select_browser"), 1)
            return
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        jobs = yaml.safe_load(text)["jobs"]
        linux_probe = [step["run"] for step in jobs[BROWSER_LINUX]["steps"] if "run" in step and "select_browser" in step["run"]]
        macos_probe = [step["run"] for step in jobs[BROWSER_MACOS]["steps"] if "run" in step and "select_browser" in step["run"]]
        self.assertEqual(len(linux_probe), 1)
        self.assertEqual(macos_probe, linux_probe)
        self.assertEqual(text.count("&select_browser"), 1)
        self.assertEqual(text.count("*select_browser"), 1)

    def test_the_browser_step_creates_its_temporary_directory_with_ignore_cleanup_errors(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        needle = "tempfile.TemporaryDirectory(ignore_cleanup_errors=True)"
        try:
            import yaml
        except ImportError:
            self.assertIn(needle, text)
            return
        jobs = yaml.safe_load(text)["jobs"]
        for job_id in (BROWSER_LINUX, BROWSER_MACOS):
            runs = [step["run"] for step in jobs[job_id]["steps"] if "run" in step]
            self.assertIn(needle, runs[-1])

    def test_the_browser_step_tries_select_browser_again_when_the_first_try_returns_no_path(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        try:
            import yaml
        except ImportError:
            self.assertEqual(text.count("select_browser(directory)"), 1)
            self.assertEqual(text.count("select_browser(directory, timeout=60)"), 1)
            return
        jobs = yaml.safe_load(text)["jobs"]
        for job_id in (BROWSER_LINUX, BROWSER_MACOS):
            runs = [step["run"] for step in jobs[job_id]["steps"] if "run" in step]
            probe = next(run for run in runs if "select_browser" in run)
            self.assertEqual(probe.count("select_browser(directory)"), 1)
            self.assertEqual(probe.count("select_browser(directory, timeout=60)"), 1)

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

    def test_the_workflow_env_reader_ignores_comments_after_empty_tmpdir(self):
        for value in ("# x", "'' # x", '"" # x'):
            with self.subTest(value=value):
                self.assertEqual(workflow_environment(f"env:\n  TMPDIR: {value}\n"), {"TMPDIR": ""})

    def test_the_workflow_tmpdir_is_short_enough_for_the_macos_chromium_socket_path(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        env = workflow_environment(text)
        tmpdir = env.get("TMPDIR")
        self.assertTrue(tmpdir, "the workflow env pins a TMPDIR for the macOS socket path.")
        length = macos_path_length(tmpdir)
        self.assertLessEqual(length + SELECT_BROWSER_SOCKET_OFFSET, SOCKET_LIMIT,
                             "the select-browser socket path stays within SOCKET_LIMIT.")
        self.assertLessEqual(length + WORKER_SOCKET_OFFSET, SOCKET_LIMIT,
                             "a run_tests.py worker's socket path stays within SOCKET_LIMIT.")
