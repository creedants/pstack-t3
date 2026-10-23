"""A worker without the prctl path exits after its parent dies."""

import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from .support import MARKS, RUNNER, RunnerCase, setUpModule


def wait_gone(pid: int, limit: float = 10.0) -> bool:
    """Return whether pid is gone within limit seconds. A zombie counts as gone."""
    deadline = time.monotonic() + limit
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1][0]
            if state == "Z":
                return True
        except (FileNotFoundError, IndexError):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


class ParentExitTest(RunnerCase, unittest.TestCase):
    def test_a_worker_without_prctl_exits_after_its_parent_dies(self):
        self.write("marks.py", MARKS)
        self.write("test_slow.py", """
            import os
            import time
            import unittest

            import marks

            class SlowTest(unittest.TestCase):
                def test_slow(self):
                    marks.mark("worker", os.getpid())
                    time.sleep(60)
        """)
        plan_path = self.scratch / "plan.json"
        listed = subprocess.run([sys.executable, str(RUNNER), "-s", str(self.tests),
                                 "--list", str(plan_path)],
                                capture_output=True, text=True)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        entries = json.loads(plan_path.read_text())
        seqs = [index for index, entry in enumerate(entries) if entry["id"].endswith("test_slow")]
        self.assertEqual(len(seqs), 1)
        home = self.scratch / "whome"
        home.mkdir()
        (home / "output").write_bytes(b"")
        spec = {"start": str(self.tests), "results": str(home / "results.jsonl"),
                "plan": str(plan_path), "seqs": seqs}
        spec_path = self.scratch / "spec.json"
        spec_path.write_text(json.dumps(spec))
        launcher_path = self.scratch / "launcher.py"
        launcher_path.write_text(
            "import subprocess\n"
            "import sys\n"
            "import time\n"
            f"runner = {str(RUNNER)!r}\n"
            "spec = sys.argv[1]\n"
            "output = sys.argv[2]\n"
            "cwd = sys.argv[3]\n"
            "proc = subprocess.Popen([sys.executable, runner, '--worker', spec], cwd=cwd,\n"
            "                        stdout=open(output, 'wb'), stderr=subprocess.STDOUT,\n"
            "                        stdin=subprocess.DEVNULL, start_new_session=True)\n"
            "time.sleep(60)\n"
        )
        launcher = subprocess.Popen([sys.executable, str(launcher_path), str(spec_path),
                                     str(home / "output"), str(self.proj)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True)
        self.addCleanup(lambda: self.end(launcher.pid))
        worker = self.pid_from("worker")
        self.addCleanup(lambda: self.end(worker))
        os.kill(launcher.pid, signal.SIGKILL)
        launcher.wait()
        self.assertTrue(wait_gone(worker, 10.0), f"worker {worker} is still there after its parent died")


if __name__ == "__main__":
    unittest.main()
