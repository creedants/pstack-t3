if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from .support import (
    setUpModule,
    check,
    measure_capacity,
    ROOT,
)


class BuildProcessCounterTest(unittest.TestCase):
    def test_a_python_process_running_the_build_script_the_test_runner_or_unittest_counts(self):
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

    def test_a_missing_process_directory_does_not_crash_before_the_header(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "rows.tsv"
            env = dict(os.environ, **{measure_capacity.PROC_ROOT_ENV: str(Path(directory) / "no-proc")})
            with out.open("w") as handle:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "measure_capacity.py"), "--label", "x", "--samples", "1", "--interval", "0"],
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    env=env,
                    timeout=60,
                )
            lines = out.read_text().splitlines()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(lines[0].split("\t"), list(measure_capacity.COLUMNS))
        self.assertEqual(len(lines), 2)
        row = dict(zip(measure_capacity.COLUMNS, lines[1].split("\t")))
        self.assertEqual(row["label"], "x")
        for name in measure_capacity.COLUMNS:
            if name not in ("label", "ts"):
                self.assertEqual(row[name], measure_capacity.UNAVAILABLE, name)
