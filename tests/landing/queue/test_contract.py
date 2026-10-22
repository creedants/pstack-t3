from ..support import (
    setUpModule,
    LandingTest as LandingTestBase,
    ast,
    json,
    land,
    mock,
    os,
    ProcessReason,
    real_process,
    REVIEWER,
    SCRIPT,
    sh,
    sys,
)
from .base import QueueBase

class LandingTest(QueueBase, LandingTestBase):
    def test_status_refuses_a_q_number(self):
        self.init()
        self.assertEqual(self.queue_one(), "E1")
        self.assertIn("'Q1' is not an entry id such as E3", self.land("status", "Q1", ok=False))
        named = self.land("status", "E1")
        self.assertEqual(self.land("status", "1"), named)
        self.assertTrue(named.startswith("E1 queued"), named)

    def test_a_first_init_without_the_flag_leaves_the_submitted_body_unchanged(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            self.assertEqual(self.stored_pr_item_line(), "false")
            self.store_item("w1", "polish", "G2")
            sh("git", "checkout", "-q", "-b", "w1", "origin/main", cwd=self.work)
            (self.work / "a.txt").write_text("agent\n")
            sha = self.commit("w1")
            lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease",
                      lease, "--reviewer", REVIEWER)
            self.land("land")
            create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
            self.assertEqual(create[create.index("--body") + 1], "")

    def test_a_rerun_without_the_flag_keeps_pr_item_line_on(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "on")
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--batch", "1")
        self.assertEqual(self.stored_pr_item_line(), "true")
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.store_item("w1", "polish", "G2")
            sh("git", "checkout", "-q", "-b", "w1", "origin/main", cwd=self.work)
            (self.work / "a.txt").write_text("agent\n")
            sha = self.commit("w1")
            lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease",
                      lease, "--reviewer", REVIEWER)
            self.land("land")
            create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
            self.assertEqual(create[create.index("--body") + 1], "Kind: polish. Goal: rules in the tool")

    def test_an_unknown_pr_item_line_value_is_refused_and_leaves_the_contract_unchanged(self):
        with self.fake_gh():
            self.init(mode="human")
            before = self.queue_rows()
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "bogus", ok=False)
        self.assertEqual(self.queue_rows(), before)

    def test_a_deleted_pr_item_line_key_reads_as_off(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "on")
        with self.raw_db() as db:
            db.execute("DELETE FROM contract WHERE key = 'prItemLine'")
            db.commit()
        self.assertIsNone(self.stored_pr_item_line())
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.store_item("w1", "polish", "G2")
            sh("git", "checkout", "-q", "-b", "w1", "origin/main", cwd=self.work)
            (self.work / "a.txt").write_text("agent\n")
            sha = self.commit("w1")
            lease = self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease",
                      lease, "--reviewer", REVIEWER)
            self.land("land")
            create = ast.literal_eval([line for line in log.read_text().splitlines() if "'create'" in line][-1])
            self.assertEqual(create[create.index("--body") + 1], "")

    def test_repo_status_names_pr_item_line_only_when_on(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "on")
            self.assertIn("pr item line: on", self.land("status"))
            self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                      "--pr-item-line", "off")
        self.assertNotIn("pr item line", self.land("status"))
        with self.raw_db() as db:
            db.execute("DELETE FROM contract WHERE key = 'prItemLine'")
            db.commit()
        self.assertNotIn("pr item line", self.land("status"))

    def test_a_first_init_stores_the_heartbeat_defaults_and_status_omits_the_heartbeat(self):
        self.init(mode="push")
        self.assertEqual(self.stored_heartbeat_ms(), "3600000")
        self.assertEqual(self.stored_merge_heartbeat_ms(), "900000")
        self.assertNotIn("heartbeat", self.land("status"))

    def test_a_rerun_without_the_heartbeat_flags_keeps_the_stored_intervals(self):
        with self.fake_gh():
            self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                      "--heartbeat-ms", "7200000", "--merge-heartbeat-ms", "1800000")
            self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1")
        self.assertEqual(self.stored_heartbeat_ms(), "7200000")
        self.assertEqual(self.stored_merge_heartbeat_ms(), "1800000")
        self.assertIn("heartbeat everyMs 7200000 merge wait 1800000", self.land("status"))

    def test_a_heartbeat_below_the_floor_is_refused_and_writes_no_database(self):
        err = self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                        "--heartbeat-ms", "3599999", ok=False)
        self.assertIn("--heartbeat-ms", err)
        self.assertEqual(list((self.base / "state").glob("pstack-t3/landing/*/land.db")), [])
        err = self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                        "--merge-heartbeat-ms", "899999", ok=False)
        self.assertIn("--merge-heartbeat-ms", err)
        self.assertEqual(list((self.base / "state").glob("pstack-t3/landing/*/land.db")), [])

    def test_a_heartbeat_rerun_below_the_floor_keeps_the_stored_queue(self):
        self.init(mode="push")
        self.queue_one()
        before = self.queue_rows()
        err = self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                        "--heartbeat-ms", "3599999", ok=False)
        self.assertIn("--heartbeat-ms", err)
        self.assertEqual(self.queue_rows(), before)
        err = self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                        "--merge-heartbeat-ms", "899999", ok=False)
        self.assertIn("--merge-heartbeat-ms", err)
        self.assertEqual(self.queue_rows(), before)

    def test_a_non_integer_heartbeat_is_refused_before_the_contract_changes(self):
        for flag in ("heartbeat_ms", "merge_heartbeat_ms"):
            with self.subTest(flag=flag):
                with self.assertRaises(land.LandError):
                    land.init(str(self.work), "main", "push", "origin", "", None, None, 1, 1800,
                              **{flag: True})
        self.assertEqual(list((self.base / "state").glob("pstack-t3/landing/*/land.db")), [])

    def test_deleted_heartbeat_keys_leave_status_without_a_heartbeat(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                  "--heartbeat-ms", "7200000", "--merge-heartbeat-ms", "1800000")
        with self.raw_db() as db:
            db.execute("DELETE FROM contract WHERE key IN ('heartbeatMs', 'mergeHeartbeatMs')")
            db.commit()
        self.assertNotIn("heartbeat", self.land("status"))

    def test_setting_only_the_fallback_still_names_both_intervals(self):
        self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh", "--batch", "1",
                  "--heartbeat-ms", "7200000")
        self.assertIn("heartbeat everyMs 7200000 merge wait 900000", self.land("status"))

    def use_gh_auth_by_arguments(self, github, other):
        """Wrap the fake gh so auth status answers by its arguments. github is the exit status of the github.com-qualified call, other of an unqualified one."""
        real = os.environ["LAND_GH"]
        wrapper = self.base / "gh-auth-by-arguments"
        wrapper.write_text(f"""#!{sys.executable}
import os, sys
if sys.argv[1:] == ["auth", "status", "--active", "--hostname", "github.com"]:
    sys.exit({github})
if sys.argv[1:3] == ["auth", "status"]:
    sys.exit({other})
os.execv({real!r}, [{real!r}, *sys.argv[1:]])
""")
        wrapper.chmod(0o755)
        os.environ["LAND_GH"] = str(wrapper)

    def test_init_accepts_a_github_login_beside_a_stale_host(self):
        with self.fake_gh():
            self.use_gh_auth_by_arguments(github=0, other=1)
            self.assertIn("created", self.init(mode="human"))
        self.assertTrue(self.land("status").startswith("human mode onto"), self.land("status"))

    def test_init_refuses_an_enterprise_only_login_and_writes_no_database(self):
        with self.fake_gh():
            self.use_gh_auth_by_arguments(github=1, other=0)
            err = self.land("init", "--trunk", "main", "--mode", "human", "--check", "./check.sh",
                            "--batch", "1", ok=False)
        self.assertIn("land: mode human opens pull requests with gh, and "
                      "gh auth status --active --hostname github.com failed", err)
        self.assertIn("use mode push or mode local", err)
        self.assertEqual(list((self.base / "state").glob("pstack-t3/landing/*/land.db")), [])

    def test_mode_accepts_a_github_login_beside_a_stale_host(self):
        with self.fake_gh():
            self.init(mode="push")
            self.use_gh_auth_by_arguments(github=0, other=1)
            self.assertEqual(self.land("mode", "human"), "landing mode is now human")
        self.assertTrue(self.land("status").startswith("human mode onto"), self.land("status"))

    def test_mode_refuses_an_enterprise_only_login_and_keeps_the_stored_mode(self):
        with self.fake_gh():
            self.init(mode="push")
            self.use_gh_auth_by_arguments(github=1, other=0)
            err = self.land("mode", "human", ok=False)
        self.assertIn("land: mode human opens pull requests with gh, and "
                      "gh auth status --active --hostname github.com failed", err)
        self.assertIn("use mode push or mode local", err)
        self.assertTrue(self.land("status").startswith("push mode onto"), self.land("status"))
