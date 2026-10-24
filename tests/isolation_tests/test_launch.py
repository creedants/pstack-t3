"""Each common way to start a test arms the guard before the first test body runs.

Each case starts test_environment in a fresh process from the checkout. The four unittest cases drop the
pointer and the token, as a new shell would, so the child must make its own run root.
"""

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
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / "tests" / "isolation_tests"
PROBE = "test_environment"

ENTRIES = {
    "runner": [sys.executable, "scripts/run_tests.py", "-s", "tests/isolation_tests", "-j", "1"],
    "discover in the area": [sys.executable, "-m", "unittest", "discover", "-s", "tests/isolation_tests", "-p", f"{PROBE}.py"],
    "discover from the checkout": [sys.executable, "-m", "unittest", "discover", "-p", f"{PROBE}.py"],
    "named module": [sys.executable, "-m", "unittest", f"tests.isolation_tests.{PROBE}"],
    "direct file": [sys.executable, f"tests/isolation_tests/{PROBE}.py"],
}


def within(path, base):
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


class LaunchTest(unittest.TestCase):
    def test_every_entry_point_runs_the_probe_inside_a_guarded_run_root_of_its_own(self):
        for name, argv in ENTRIES.items():
            with self.subTest(name):
                if name == "runner" and os.environ.get("PSTACK_RUN_TESTS") == str(AREA):
                    # The runner started on this area refuses to start itself again, and the run already proves this case.
                    continue
                scratch = Path(tempfile.mkdtemp(prefix="launch-"))
                self.addCleanup(shutil.rmtree, scratch, True)
                tmp = scratch / "tmp"
                tmp.mkdir()
                report = scratch / "report.jsonl"
                env = {key: value for key, value in os.environ.items() if key not in (isolation.POINTER, isolation.TOKEN)}
                env.update(TMPDIR=str(tmp), PSTACK_GUARD_PROBE_REPORT=str(report))
                done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                records = [json.loads(line) for line in report.read_text().splitlines()]
                self.assertEqual(len(records), 1, records)
                record = records[0]
                self.assertTrue(within(record["root"], os.path.realpath(tmp)), record)
                self.assertTrue(record["refused"])
                self.assertEqual(sorted(record["values"]), sorted(isolation.layout(record["root"])))
                for key, value in record["values"].items():
                    self.assertTrue(within(value, record["root"]), f"{key}={value}")


if __name__ == "__main__":
    unittest.main()
