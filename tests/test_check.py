import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check  # noqa: E402
import measure_capacity  # noqa: E402


def link_findings(*targets):
    body = "\n".join(f"[link]({target})" for target in targets)
    with tempfile.TemporaryDirectory() as directory:
        skill = Path(directory) / "demo"
        skill.mkdir()
        (skill / "other.md").write_text("other\n")
        (skill / "SKILL.md").write_text(f"---\nname: demo\ndescription: d\n---\n\n{body}\n")
        return [finding for finding in check.check_tree(directory) if "broken link" in finding]


class LinkCheckTest(unittest.TestCase):
    def test_a_thread_link_with_an_id_is_not_a_file_link(self):
        self.assertEqual(link_findings("t3-thread://v1/abc-123", "other.md"), [])

    def test_other_targets_still_must_resolve(self):
        self.assertEqual(
            link_findings("missing.md", "t3-thread://v2/abc", "t3-thread://v1/"),
            [
                "demo/SKILL.md: broken link missing.md",
                "demo/SKILL.md: broken link t3-thread://v2/abc",
                "demo/SKILL.md: broken link t3-thread://v1/",
            ],
        )


class FrontmatterWithoutPyYamlTest(unittest.TestCase):
    def test_without_pyyaml_a_malformed_flow_sequence_is_not_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: [unterminated\n---\n\nbody\n")
            with mock.patch.object(check, "yaml", None):
                self.assertEqual(check.check_tree(directory), [])


class BuildProcessCounterTest(unittest.TestCase):
    def test_a_python_process_running_a_repo_script_or_unittest_counts(self):
        counted = [
            ["python3", "scripts/run_tests.py"],
            ["/usr/bin/python3.12", "/work/pstack-t3/scripts/run_tests.py", "--worker", "spec.json"],
            ["python3", "/work/pstack-t3/scripts/build.py"],
            ["python3", "-m", "unittest", "discover", "-s", "tests"],
        ]
        for args in counted:
            self.assertTrue(measure_capacity.is_build(args), args)

    def test_other_processes_do_not_count(self):
        not_counted = [
            ["python3", "scripts/check.py"],
            ["python3", "other/run_tests.py"],
            ["bash", "-c", "python3 scripts/run_tests.py"],
            ["node", "/work/pstack-t3/scripts/run_tests.py"],
            ["python3", "-m", "pytest"],
        ]
        for args in not_counted:
            self.assertFalse(measure_capacity.is_build(args), args)


class SamplerOutputTest(unittest.TestCase):
    def test_a_sampler_stopped_early_keeps_the_rows_it_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "rows.tsv"
            with out.open("w") as handle:
                sampler = subprocess.Popen(
                    [sys.executable, str(ROOT / "scripts" / "measure_capacity.py"), "--label", "x", "--samples", "1000", "--interval", "1"],
                    stdout=handle,
                )
                try:
                    deadline = time.time() + 5
                    while len(out.read_text().splitlines()) < 2 and time.time() < deadline:
                        time.sleep(0.1)
                finally:
                    sampler.terminate()
                    sampler.wait()
            lines = out.read_text().splitlines()
        self.assertEqual(lines[0].split("\t"), list(measure_capacity.COLUMNS))
        self.assertEqual(lines[1].split("\t")[0], "x")


if __name__ == "__main__":
    unittest.main()
