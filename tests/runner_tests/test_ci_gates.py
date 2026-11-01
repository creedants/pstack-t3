if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

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
    GATES,
    GATE_BLOCK_MUTATIONS,
    MACOS_PARTS,
    PARTS,
    dedent_block,
    gate_readers,
    gate_script_status,
    gate_scripts,
    job_body,
    literal_run_blocks,
    macos_part_run,
    mutate_gate_block,
    read_workflow_switch,
    report_failed_part,
    result_block,
    result_block_span,
    workflow_with_gate_mutation,
)


MACOS_MUTATIONS = (
    "drop_macos_312_1_from_needs",
    "skipped_allowance_on_macos_312_1",
    "continue_on_error_on_macos_test_part_1",
    "restore_advisory_comment",
    "or_true_on_macos_312_2",
)

def macos_result_line(job_id: str) -> str:
    return 'test "${{ needs.' + job_id + '.result }}" = "$macos"'

def macos_comment(index: int, job_id: str) -> str:
    return (
        f"# Part {index} of 4. Runs on a push only. A step failure fails this job. "
        f"test (3.12) lists {job_id} in needs and requires success on push and skipped on pull_request."
    )

def assert_macos_blocking(case, text: str) -> None:
    expected_needs = "needs: [part_312_1, part_312_2, part_312_3, part_312_4, part_312_5, part_312_6, part_312_7, part_312_8, browser_linux, generated, browser_macos, " + ", ".join(MACOS_PARTS) + "]"
    case.assertIn(expected_needs, text, "macos needs pins the blocking rule.")
    for job_id in MACOS_PARTS:
        case.assertIn(macos_result_line(job_id), text, "macos result line pins the blocking rule.")
    for phrase in (
        "No macOS part is in a needs list",
        "does not fail the job check",
        "Every step of this part uses continue-on-error",
    ):
        case.assertNotIn(phrase, text, "macos comment pins the blocking rule.")
    case.assertNotIn("continue-on-error", text, "macos continue-on-error pins the blocking rule.")
    for job_id in MACOS_PARTS:
        for line in text.splitlines():
            if job_id in line and "needs." in line:
                case.assertNotIn("skipped", line, "macos no skipped pins the blocking rule.")
                case.assertNotIn("|| true", line, "macos no or true pins the blocking rule.")
    for index, job_id in enumerate(MACOS_PARTS, start=1):
        case.assertIn(macos_comment(index, job_id), text, "macos comment pins the blocking rule.")
    try:
        import yaml
    except ImportError:
        return
    jobs = yaml.safe_load(text)["jobs"]
    case.assertEqual(jobs["test_312"]["needs"], [f"part_312_{index}" for index in range(1, PARTS + 1)] + ["browser_linux", "generated", "browser_macos"] + list(MACOS_PARTS), "macos needs pins the blocking rule.")
    for job_id in MACOS_PARTS:
        steps = jobs[job_id]["steps"]
        case.assertFalse(any("continue-on-error" in step for step in steps), "macos continue-on-error pins the blocking rule.")

def workflow_with_macos_mutation(text: str, name: str) -> str:
    if name == "drop_macos_312_1_from_needs":
        return text.replace(", " + MACOS_PARTS[0], "", 1)
    if name == "skipped_allowance_on_macos_312_1":
        line = macos_result_line(MACOS_PARTS[0])
        return text.replace(line, line + ' || test "${{ needs.' + MACOS_PARTS[0] + '.result }}" = skipped', 1)
    if name == "continue_on_error_on_macos_test_part_1":
        return text.replace(
            "      - name: Test part 1/4\n        run: |",
            "      - name: Test part 1/4\n        continue-on-error: true\n        run: |",
            1,
        )
    if name == "restore_advisory_comment":
        return text.replace(
            macos_comment(1, MACOS_PARTS[0]),
            "# Every step of this part uses continue-on-error, so a step failure does not fail the job check. The part runs part 1 of 4. No macOS part is in a needs list.",
            1,
        )
    if name == "or_true_on_macos_312_2":
        line = macos_result_line(MACOS_PARTS[1])
        return text.replace(line, line + " || true", 1)
    raise AssertionError(name)


class CiGatesMixin:
    def test_every_event_exports_the_real_process_switch_as_1(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            with self.subTest(reader=reader):
                switch = read_workflow_switch(text, reader)
                self.assertEqual(switch.exported("push"), "1")
                self.assertEqual(switch.exported("pull_request"), "1")

    def test_a_gate_exits_nonzero_for_failure_cancelled_skipped_or_an_empty_part_result(self):
        # This starts a process because the check is an exit status.
        cases = (
            (("success",) * PARTS, True),
            (("failure",) + ("success",) * (PARTS - 1), False),
            (("success", "cancelled") + ("success",) * (PARTS - 2), False),
            (("success",) * (PARTS - 1) + ("skipped",), False),
            (("success",) * (PARTS - 1) + ("",), False),
        )
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            for prefix, script in gate_scripts(text, reader):
                for results, ok in cases:
                    with self.subTest(reader=reader, prefix=prefix, results=results):
                        if "macos_312" in script:
                            status = gate_script_status(script, prefix, results, macos=("skipped",) * 4, event="pull_request")
                        else:
                            status = gate_script_status(script, prefix, results, event="pull_request")
                        if ok:
                            self.assertEqual(status, 0)
                        else:
                            self.assertNotEqual(status, 0)

    def test_a_linux_part_result_other_than_the_event_expects_fails_the_gate(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            for prefix, script in gate_scripts(text, reader):
                for event, good, bad in (("push", "skipped", "success"), ("pull_request", "success", "skipped")):
                    with self.subTest(reader=reader, prefix=prefix, event=event):
                        macos = ("success",) * 4 if event == "push" else ("skipped",) * 4
                        if "macos_312" in script:
                            self.assertEqual(gate_script_status(script, prefix, (good,) * PARTS, macos=macos, event=event), 0)
                        else:
                            self.assertEqual(gate_script_status(script, prefix, (good,) * PARTS, event=event), 0)
                        for position in range(PARTS):
                            results = (good,) * position + (bad,) + (good,) * (PARTS - 1 - position)
                            with self.subTest(position=position):
                                if "macos_312" in script:
                                    status = gate_script_status(script, prefix, results, macos=macos, event=event)
                                else:
                                    status = gate_script_status(script, prefix, results, event=event)
                                self.assertNotEqual(status, 0)

    def test_the_gates_exit_nonzero_on_an_unknown_event(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            for prefix, script in gate_scripts(text, reader):
                for macos in (None, ("skipped",) * 4, ("success",) * 4):
                    with self.subTest(reader=reader, prefix=prefix, macos=macos):
                        if "macos_312" in script and macos is None:
                            continue
                        if "macos_312" not in script and macos is not None:
                            continue
                        self.assertNotEqual(
                            gate_script_status(script, prefix, ("success",) * PARTS, macos=macos, event="schedule"),
                            0,
                        )

    def test_the_gates_require_the_browser_and_generated_jobs(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            for prefix, script in gate_scripts(text, reader):
                for event in ("push", "pull_request"):
                    good = "skipped" if event == "push" else "success"
                    macos = ("success",) * 4 if event == "push" else ("skipped",) * 4
                    base = dict(macos=macos) if "macos_312" in script else {}
                    with self.subTest(reader=reader, prefix=prefix, event=event, job="browser_linux"):
                        self.assertNotEqual(
                            gate_script_status(script, prefix, (good,) * PARTS, event=event, browser_linux="failure", **base),
                            0,
                        )
                    with self.subTest(reader=reader, prefix=prefix, event=event, job="generated"):
                        self.assertNotEqual(
                            gate_script_status(script, prefix, (good,) * PARTS, event=event, generated="failure", **base),
                            0,
                        )
        script = dict(gate_scripts(text, gate_readers()[0]))["part_312"]
        for event, macos_good in (("push", "success"), ("pull_request", "skipped")):
            with self.subTest(event=event, job="browser_macos"):
                # This starts a process because the check is an exit status.
                self.assertNotEqual(
                    gate_script_status(script, "part_312", ("success" if event == "pull_request" else "skipped",) * PARTS,
                                       macos=(macos_good,) * 4, event=event, browser_macos="failure"),
                    0,
                )

    def test_switch_appears_only_at_workflow_root_with_value_1_and_in_no_job(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            with self.subTest(reader=reader):
                read_workflow_switch(text, reader)
        lines = text.splitlines(keepends=True)
        start = next(index for index, line in enumerate(lines) if line.strip() == "part_310_1:")
        indent = len(lines[start]) - len(lines[start].lstrip(" "))
        injected = lines[:start + 1] + [" " * (indent + 2) + "env:\n",
                                        " " * (indent + 4) + "PSTACK_TEST_REAL_PROCESS: '1'\n"] + lines[start + 1:]
        for reader in gate_readers():
            with self.subTest(reader=reader, case="part job"):
                with self.assertRaises(AssertionError):
                    read_workflow_switch("".join(injected), reader)
        wrong = text.replace("PSTACK_TEST_REAL_PROCESS: '1'", "PSTACK_TEST_REAL_PROCESS: '0'", 1)
        for reader in gate_readers():
            with self.subTest(reader=reader, case="wrong value"):
                with self.assertRaises(AssertionError):
                    read_workflow_switch(wrong, reader)

    def test_set_plus_e_or_a_false_if_or_or_true_makes_a_failed_part_exit_zero(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for name in GATE_BLOCK_MUTATIONS:
            mutated = workflow_with_gate_mutation(text, name)
            for reader in gate_readers():
                with self.subTest(mutation=name, reader=reader):
                    original = dict(gate_scripts(text, reader))
                    for prefix, script in gate_scripts(mutated, reader):
                        block = result_block(text, prefix)
                        expected = original[prefix].replace(block, mutate_gate_block(name, block), 1)
                        self.assertEqual(script, expected)
                        with self.assertRaises(AssertionError) as caught:
                            report_failed_part(script, prefix)
                        self.assertEqual(str(caught.exception), "gate exited 0 with a failed part")

    def test_a_folded_gate_block_fails_instead_of_running_its_selected_lines(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        lines = text.splitlines(keepends=True)
        for gate, _prefix in GATES:
            header = next(index for index, line in enumerate(lines)
                          if line.rstrip("\n").startswith(" ") and line.rstrip("\n").strip() == gate + ":")
            marker = next(index for index in range(header + 1, len(lines))
                          if lines[index].rstrip("\n").lstrip(" ") == "- run: |")
            lines[marker] = lines[marker].replace("run: |", "run: >", 1)
        folded = "".join(lines)
        with self.assertRaises(AssertionError) as caught:
            gate_scripts(folded, "indent")
        self.assertIn("cannot find the result block", str(caught.exception))

    def test_macos_parts_block_test_312(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        assert_macos_blocking(self, text)

    def test_a_macos_result_other_than_success_fails_the_312_gate_on_push(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            script = dict(gate_scripts(text, reader))["part_312"]
            self.assertEqual(
                gate_script_status(script, "part_312", ("skipped",) * PARTS, macos=("success",) * 4, event="push"), 0
            )
            for position in range(4):
                for bad in ("failure", "cancelled", "skipped", ""):
                    with self.subTest(reader=reader, position=position, bad=bad):
                        macos = ("success",) * position + (bad,) + ("success",) * (3 - position)
                        self.assertNotEqual(
                            gate_script_status(script, "part_312", ("skipped",) * PARTS, macos=macos, event="push"),
                            0,
                        )

    def test_a_macos_result_other_than_skipped_fails_the_312_gate_on_pull_request(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            script = dict(gate_scripts(text, reader))["part_312"]
            self.assertEqual(
                gate_script_status(
                    script, "part_312", ("success",) * PARTS, macos=("skipped",) * 4, event="pull_request"
                ),
                0,
            )
            for position in range(4):
                for bad in ("success", "failure", "cancelled", ""):
                    with self.subTest(reader=reader, position=position, bad=bad):
                        macos = ("skipped",) * position + (bad,) + ("skipped",) * (3 - position)
                        self.assertNotEqual(
                            gate_script_status(
                                script, "part_312", ("success",) * PARTS, macos=macos, event="pull_request"
                            ),
                            0,
                        )

    def test_the_312_gate_exits_nonzero_on_a_schedule_event(self):
        # This starts a process because the check is an exit status.
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        for reader in gate_readers():
            script = dict(gate_scripts(text, reader))["part_312"]
            for macos in (("skipped",) * 4, ("success",) * 4):
                with self.subTest(reader=reader, macos=macos):
                    self.assertNotEqual(
                        gate_script_status(
                            script, "part_312", ("success",) * PARTS, macos=macos, event="schedule"
                        ),
                        0,
                    )

    def test_named_macos_mutations_fail_the_pin_they_target(self):
        text = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
        assert_macos_blocking(self, text)
        markers = {
            "drop_macos_312_1_from_needs": "macos needs",
            "skipped_allowance_on_macos_312_1": "macos no skipped",
            "continue_on_error_on_macos_test_part_1": "macos continue-on-error",
            "restore_advisory_comment": "macos comment",
            "or_true_on_macos_312_2": "macos no or true",
        }
        for name in MACOS_MUTATIONS:
            with self.subTest(mutation=name):
                mutated = workflow_with_macos_mutation(text, name)
                with self.assertRaises(AssertionError) as caught:
                    assert_macos_blocking(self, mutated)
                self.assertIn(markers[name], str(caught.exception))
                if name == "skipped_allowance_on_macos_312_1":
                    for reader in gate_readers():
                        scripts = dict(gate_scripts(mutated, reader))
                        status = gate_script_status(
                            scripts["part_312"],
                            "part_312",
                            ("skipped",) * PARTS,
                            macos=("skipped", "success", "success", "success"),
                            event="push",
                        )
                        self.assertEqual(status, 0)
                if name == "or_true_on_macos_312_2":
                    for reader in gate_readers():
                        scripts = dict(gate_scripts(mutated, reader))
                        status = gate_script_status(
                            scripts["part_312"],
                            "part_312",
                            ("skipped",) * PARTS,
                            macos=("success", "failure", "success", "success"),
                            event="push",
                        )
                        self.assertEqual(status, 0)
