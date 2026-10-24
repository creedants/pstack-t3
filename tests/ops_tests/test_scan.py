if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import tempfile
import unittest
from pathlib import Path
from .support import DOC, ROOT, Identity, REQUIRED_SCAN, OpsTest, machine_value_findings, setUpModule

class ScanTest(OpsTest):
    def repo(self, doc):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        ops = root / "ops"
        ops.mkdir()
        for label in REQUIRED_SCAN:
            if label.startswith("ops/"):
                (ops / Path(label).name).write_text("echo ok\n")
        document = root / "docs" / "operations.md"
        document.parent.mkdir()
        document.write_text(doc)
        return ops, document

    def test_the_scan_is_silent_for_the_ci_runner_and_a_root_container(self):
        for identity in (Identity("/home/runner", "runner", "localhost"), Identity("/root", "root", "localhost")):
            with self.subTest(user=identity.user):
                self.assertEqual(machine_value_findings(ROOT / "ops", DOC, identity), [])

    def test_the_scan_reports_planted_values_and_ignores_longer_names(self):
        who = Identity("/home/zed", "zed", "box7.lan")
        planted = [f"APPS={who.home}/apps", f"see ~{who.user}/notes", f"mail {who.user}@example.org", f"ssh {who.user}@box7",
                   "http://Box7.tail.example:3773", f"cd /srv/{who.user}", "ping BOX7.LAN", "curl box7:3773/x", "open //box7/share"]
        benign = ["rm -rf squashfs-zed", "def zed(tid):", "top = zed(tid)", "the zed is fast", "/home/zedekiah/x", "/home/zed.bak",
                  "ssh box7", "it runs on box7.", "box70.lan", "unbox7.lan", "@box7x", "box7: a label"]
        ops, document = self.repo("\n".join(planted + benign) + "\n")
        self.assertEqual(machine_value_findings(ops, document, who), [
            "docs/operations.md:1: holds this machine's home directory",
            "docs/operations.md:1: holds this machine's user name",
            "docs/operations.md:2: holds this machine's user name",
            "docs/operations.md:3: holds this machine's user name",
            "docs/operations.md:4: holds this machine's user name",
            "docs/operations.md:4: holds this machine's host name",
            "docs/operations.md:5: holds this machine's host name",
            "docs/operations.md:6: holds this machine's user name",
            "docs/operations.md:7: holds this machine's host name",
            "docs/operations.md:8: holds this machine's host name",
            "docs/operations.md:9: holds this machine's host name",
        ])
        container = Identity("/root", "root", "x")
        ops, document = self.repo('rm -rf "$APPS/squashfs-root" "$NEW"\ntop = root(tid)\n/rootfs\n')
        self.assertEqual(machine_value_findings(ops, document, container), [])
        ops, document = self.repo("cd /root\nls ~root\n")
        self.assertEqual(machine_value_findings(ops, document, container), [
            "docs/operations.md:1: holds this machine's home directory",
            "docs/operations.md:1: holds this machine's user name",
            "docs/operations.md:2: holds this machine's user name",
        ])
