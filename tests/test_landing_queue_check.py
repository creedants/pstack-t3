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
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "verification" / "landing-queue" / "check.py"


def load_check():
    spec = importlib.util.spec_from_file_location("landing_queue_check", CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trace_output(states=12):
    lines = [
        "Error: Invariant NoDoubleLanding is violated.",
        "Error: The behavior up to this point is:",
    ]
    for step in range(1, states + 1):
        lines.append(f"State {step}: <BoundNext line 186, col 14 to line 186, col 56 of module LandingQueue>")
        lines.extend([
            f'/\\ checkedAt = [E1 |-> "c{step}"]',
            '/\\ leaseState = <<"submitted">>',
            '/\\ leasePaths = <<{"a"}>>',
            f"/\\ steps = {step - 1}",
            "/\\ paused = FALSE",
            "/\\ floor = 1",
            f'/\\ landedAs = [E1 |-> "c{step}"]',
            "/\\ entryLease = [E1 |-> 1]",
            f'/\\ candidate = [E1 |-> "c{step}"]',
            "/\\ seenEarlier = [E1 |-> FALSE]",
            "/\\ clock = 0",
            "/\\ uncheckedMerge = FALSE",
            '/\\ leaseHolder = <<"docs/A">>',
            "/\\ leaseExpiry = <<1>>",
            "/\\ droppedCoverage = FALSE",
            "/\\ trunkRewound = FALSE",
            "/\\ armed = [E1 |-> FALSE]",
            "/\\ prStored = [E1 |-> FALSE]",
            "/\\ prOpen = [E1 |-> FALSE]",
            f'/\\ tip = "c{step}"',
            "",
        ])
    lines.extend([
        "9000 states generated, 400 distinct states found, 0 states left on queue.",
        "The depth of the complete state graph search is 13.",
    ])
    return "\n".join(lines) + "\n"


def parse_failure_output():
    lines = ["***Parse Error***", "Encountered end of file while parsing module LandingQueue."]
    lines.extend(f"line {step}: Parse Error" for step in range(200))
    return "\n".join(lines) + "\n"


class FakeRun:

    def __init__(self, output):
        self.output = output
        self.configs = []

    def __call__(self, java, jar, config, parent):
        self.configs.append(config)
        return 12, self.output, 0.1


class LandingQueueCheckOutputTest(unittest.TestCase):
    def check(self, output, config_text):
        module = load_check()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        here = Path(directory.name)
        (here / "one.cfg").write_text(config_text)
        module.HERE = here
        module.tools = lambda: ("java", "tla2tools.jar")
        module.run = FakeRun(output)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code = module.main(["--scratch-parent", directory.name])
        return code, printed.getvalue()

    def test_missed_variant_prints_the_whole_trace_not_just_its_tail(self):
        output = trace_output()
        self.assertGreater(len(output), 4000)
        code, printed = self.check(output, "\\* EXPECT: NoOverlappingLeases\n")
        self.assertEqual(code, 1)
        self.assertIn("Error: Invariant NoDoubleLanding is violated.", printed)
        self.assertIn("State 1:", printed)
        self.assertIn('/\\ tip = "c12"', printed)

    def test_parse_failure_prints_tlc_whole_output_not_just_its_tail(self):
        output = parse_failure_output()
        self.assertGreater(len(output), 4000)
        code, printed = self.check(output, "CONSTANTS\n")
        self.assertEqual(code, 1)
        self.assertIn("***Parse Error***", printed)
