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
)


def planted_entry():
    return next(label for label in check.REQUIRED_SCAN if label.endswith("t3-archive-threads.py"))


class CheckoutRuleTest(unittest.TestCase):
    def _directory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def _ops_repo(self, planted, doc):
        root = self._directory()
        ops = root / "ops"
        ops.mkdir()
        for label in check.REQUIRED_SCAN:
            if label.startswith("ops/"):
                (ops / Path(label).name).write_text(planted if label.endswith("t3-archive-threads.py") else "echo ok\n")
        document = root / "docs" / "operations.md"
        document.parent.mkdir()
        document.write_text(doc)
        return ops, document

    def test_machine_values_are_quiet_on_benign_text(self):
        who = check.Identity("/opt/plant-home", "planter", "plantbox")
        ops, document = self._ops_repo("echo ok\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_machine_values_report_a_planted_home_directory(self):
        who = check.Identity("/opt/plant-home", "planter", "plantbox")
        ops, document = self._ops_repo("APPS=/opt/plant-home/apps\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's home directory",
        ])

    def test_dev_null_does_not_report_the_user_dev(self):
        who = check.Identity("/home/dev", "dev", "docs")
        ops, document = self._ops_repo("</dev/null\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_a_tilde_dev_reports_the_user(self):
        who = check.Identity("/home/dev", "dev", "docs")
        ops, document = self._ops_repo("see ~dev/notes\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's user name",
        ])

    def test_a_longer_root_path_reports_the_home_and_not_the_user(self):
        who = check.Identity("/root", "root", "x")
        ops, document = self._ops_repo("cd /root/foo\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's home directory",
        ])

    def test_a_quoted_ops_conf_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "ops")
        ops, document = self._ops_repo('echo "ops.conf"\n', "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_ping_ops_lan_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "ops")
        ops, document = self._ops_repo("ping ops.lan\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
        ])

    def test_config_example_after_one_slash_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "config")
        ops, document = self._ops_repo("cp ops/config.example dest\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_config_after_two_slashes_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "config")
        ops, document = self._ops_repo("open //config/share\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
        ])

    def test_dollar_app_prev_does_not_report_the_host(self):
        who = check.Identity("/home/alice", "alice", "app")
        ops, document = self._ops_repo("cp $APP.prev dest\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [])

    def test_app_before_a_port_reports_the_host(self):
        who = check.Identity("/home/alice", "alice", "app")
        ops, document = self._ops_repo("curl app:3773/x\n", "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
        ])

    def test_a_quoted_dotted_host_reports_the_host(self):
        who = check.Identity("/home/zed", "zed", "box7.lan")
        text = 'ssh "box7.lan"\nHOST="box7.lan"\nserver = "box7.lan"\nssh "BOX7.LAN"\nssh "box7.lan:2222"\n'
        ops, document = self._ops_repo(text, "ok\n")
        hit = f"{planted_entry()}:%d: holds this machine's host name"
        self.assertEqual(check.machine_value_findings(ops, document, who), [hit % n for n in range(1, 6)])

    def test_a_dotted_host_in_a_slash_path_reports_the_host(self):
        who = check.Identity("/home/zed", "zed", "box7.lan")
        text = "export BACKUPS=/mnt/box7.lan/archives\nscp backup:/box7.lan/data dest\n"
        ops, document = self._ops_repo(text, "ok\n")
        self.assertEqual(check.machine_value_findings(ops, document, who), [
            f"{planted_entry()}:1: holds this machine's host name",
            f"{planted_entry()}:2: holds this machine's host name",
        ])

    def _pair(self, roles, install):
        root = self._directory()
        roles_path = root / "roles.py"
        install_path = root / "install.py"
        roles_path.write_text(roles)
        install_path.write_text(install)
        return roles_path, install_path

    def test_shared_source_matches_when_the_three_names_are_the_same(self):
        body = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        roles_path, install_path = self._pair(body, body)
        self.assertEqual(check.shared_source_findings(roles_path, install_path), [])

    def test_shared_source_reports_a_comment_and_a_missing_name(self):
        install = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        commented = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    # kept\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        roles_path, install_path = self._pair(commented, install)
        self.assertEqual(check.shared_source_findings(roles_path, install_path), [
            "t3/scripts/roles.py: config_home differs from the one in scripts/install.py. Make the source text the same",
        ])
        missing = (
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        roles_path, install_path = self._pair(missing, install)
        self.assertEqual(check.shared_source_findings(roles_path, install_path), [
            "t3/scripts/roles.py: has no IGNORED. Define it with the same source text as scripts/install.py",
        ])

    def test_shared_source_reports_a_decorator_a_trailing_comment_or_an_unresolved_binding(self):
        same = (
            'IGNORED = "same"\n'
            "def config_home(environ, default):\n"
            "    return environ\n"
            "def new_file(directory):\n"
            "    return directory\n"
        )
        differs = "t3/scripts/roles.py: config_home differs from the one in scripts/install.py. Make the source text the same"
        cases = (
            (
                "a decorator on one function",
                'IGNORED = "same"\n'
                "def review_identity(function):\n"
                "    return function\n"
                "\n"
                "@review_identity\n"
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n",
                [differs],
            ),
            (
                "a trailing inline comment",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ  # review comment\n"
                "def new_file(directory):\n"
                "    return directory\n",
                [differs],
            ),
            (
                "an indented trailing comment",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "    # review comment\n"
                "def new_file(directory):\n"
                "    return directory\n",
                [differs],
            ),
            (
                "a later definition inside if",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                "if True:\n"
                "    def config_home(environ, default):\n"
                "        return environ\n",
                ["t3/scripts/roles.py:7: config_home is bound where the static comparison cannot resolve it"],
            ),
            (
                "a later definition inside try",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                "try:\n"
                "    def new_file(directory):\n"
                "        return directory\n"
                "except Exception:\n"
                "    pass\n",
                ["t3/scripts/roles.py:7: new_file is bound where the static comparison cannot resolve it"],
            ),
            (
                "a conditional assignment to IGNORED",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                "if True:\n"
                '    IGNORED = "different"\n',
                ["t3/scripts/roles.py:7: IGNORED is bound where the static comparison cannot resolve it"],
            ),
            (
                "IGNORED +=",
                'IGNORED = "same"\n'
                "def config_home(environ, default):\n"
                "    return environ\n"
                "def new_file(directory):\n"
                "    return directory\n"
                'IGNORED += " different"\n',
                ["t3/scripts/roles.py:6: IGNORED is bound where the static comparison cannot resolve it"],
            ),
        )
        for label, roles, expected in cases:
            with self.subTest(label):
                roles_path, install_path = self._pair(roles, same)
                self.assertEqual(check.shared_source_findings(roles_path, install_path), expected)
