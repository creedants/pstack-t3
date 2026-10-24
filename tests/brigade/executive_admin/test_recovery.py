if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from ..support import LAND_SCRIPT, Path, ProcessReason, SCRIPT, call, json, os, real_popen, real_process, runpy, subprocess, sys


class RecoveryMixin:
    def test_snapshot_under_another_stores_lock_raises_and_returns_nothing(self):
        self.open("docs")
        self.open_admin()
        glob = runpy.run_path(str(SCRIPT))["run"].__globals__
        admin = glob["Restaurant"](self.dir(".admin"))
        docs = glob["Restaurant"](self.dir("docs"))
        log = docs.dir / "log.tsv"
        start = log.stat().st_size
        tail = b"2026-10-06T00:00:00Z\tticket\tT6\twaiting\tfro"
        log.write_bytes(log.read_bytes() + tail)
        fabricated = b"2026-10-06T00:00:00Z\tticket\tT6\twaiting\tfrom app/engine/T7\n"
        reads = [0]

        def chunk(fd):
            data = os.read(fd, 16)
            reads[0] += len(data)
            if reads[0] == 42 and data:
                proc = real_process(
                    ProcessReason.EXIT_STATUS,
                    "-B",
                    ["-c",
                     "import runpy,sys; g=runpy.run_path(sys.argv[1]); r=g['Restaurant'](sys.argv[2]); "
                     "r.append('log.tsv',{'at':'2026-10-06T00:00:01Z','kind':'ticket','id':'T7',"
                     "'state':'waiting','note':'from app/engine/T7'})",
                     str(SCRIPT), str(docs.dir)],
                    timeout=3)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            return data

        original_read = glob["read_chunk"]
        glob["read_chunk"] = chunk
        copied = b""
        with admin.locked():
            try:
                copied = docs.snapshot("log.tsv", start)
            except glob["BrigadeError"] as error:
                self.assertEqual(str(error),
                                 f"cannot read {docs.dir} while holding {admin.dir.resolve()}'s lock; "
                                 "read other stores before taking a lock")
            else:
                self.fail(f"snapshot returned {copied!r}")
        self.assertEqual(copied, b"")
        self.assertNotEqual(log.read_bytes()[start:], fabricated)
        glob["read_chunk"] = original_read
        with docs.locked():
            self.assertEqual(docs.snapshot("log.tsv", start), tail)

    def test_two_recoveries_racing_leave_one_claim(self):
        self.with_thread()
        procs = [real_popen(
            ProcessReason.RACE,
            [sys.executable, str(SCRIPT), "--store", str(self.store), "--at", str(self.dir(".admin")),
             "set", "--thread", f"recovering:{name}", "--replace", "--expect", "t1"],
            text=True) for name in ("a", "b")]
        results = [(proc.communicate(timeout=30), proc.returncode) for proc in procs]
        self.assertEqual(sorted(code for _, code in results), [0, 1])
        meta = self.meta(".admin")
        self.assertIn(meta["thread"], ("recovering:a", "recovering:b"))
        self.assertEqual((meta["generation"], meta["previousThread"]), (2, "t1"))
        loser = next(err for (_, err), code in results if code == 1)
        self.assertEqual(loser.strip(), f"brigade: thread is {meta['thread']}, not t1; nothing replaced")

    def test_recovery_needs_a_stopped_run_and_retirement_raises_the_generation(self):
        self.with_thread()
        self.admin("set", "--schedule", "intake=s-1")
        self.assertEqual(self.admin("set", "--thread", "t9", "--replace", ok=False),
                         "brigade: the executive admin's thread changes only with --replace --expect <old>")
        claim = json.loads(self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1"))
        self.assertEqual((claim["thread"], claim["generation"], claim["previousThread"], claim["schedules"]),
                         ("recovering:t9", 2, "t1", {"intake": "s-1"}))
        before = (self.dir(".admin") / "restaurant.json").read_bytes()
        self.assertEqual(self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", ok=False),
                         "brigade: the old run has not been confirmed stopped; wait for it with t3_thread_wait, then pass --stopped <run id>")
        self.assertEqual((self.dir(".admin") / "restaurant.json").read_bytes(), before)
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(self.admin("status").splitlines()[0], "thread t2")
        self.assertEqual(self.admin("status").splitlines()[-1], "owner t2@3")
        self.assertEqual(self.meta(".admin")["previousThread"], "t1")
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[1] == "thread"], [
            ["thread", "t1@1", "recorded", "first thread"],
            ["thread", "recovering:t9@2", "recorded", "replaced t1"],
            ["thread", "t2@3", "recorded", "replaced recovering:t9; stopped r1"],
        ])
        self.admin("set", "--thread", "", "--replace", "--expect", "t2")
        self.assertEqual([row[2:] for row in self.rows(".admin", "log.tsv") if row[1] == "thread" and row[2] == "@4"],
                         [["@4", "recorded", "replaced t2"]])
        self.assertEqual(self.admin("--owner", "t2@3", "status").splitlines()[0], "thread not recorded")
        self.assertEqual(self.admin("--owner", "t2@3", "ticket", "add", "--kind", "fix", "--summary", "late", ok=False),
                         "brigade: owner t2@3 is stale; this store is owned by @4")
        self.admin("set", "--thread", "recovering:t5", "--replace", "--expect", "")
        self.admin("set", "--thread", "t6", "--replace", "--expect", "recovering:t5", "--stopped", "gone")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("t6", 6, "t2"))

    def test_a_replacement_killed_before_the_metadata_write_retries_with_one_row(self):
        self.with_thread()
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        died = self.child("die-before-thread", ".admin", "set", "--thread", "t2", "--replace",
                          "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(died.returncode, 9, died.stderr)
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"]), ("recovering:t9", 2))
        self.assertEqual([row[1:] for row in self.rows(".admin", "log.tsv") if row[2] == "t2@3"], [
            ["thread", "t2@3", "recorded", "replaced recovering:t9; stopped r1"],
        ])
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("t2", 3, "t1"))
        self.assertEqual([row[2] for row in self.rows(".admin", "log.tsv") if row[1] == "thread" and row[2] == "t2@3"],
                         ["t2@3"])

    def test_a_replacement_killed_inside_the_log_leaves_the_old_thread(self):
        self.with_thread()
        self.admin("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")
        before = (self.dir(".admin") / "restaurant.json").read_bytes()
        died = self.child("die-before-log", ".admin", "set", "--thread", "t2", "--replace",
                          "--expect", "recovering:t9", "--stopped", "r1")
        self.assertEqual(died.returncode, 9, died.stderr)
        self.assertEqual((self.dir(".admin") / "restaurant.json").read_bytes(), before)
        self.assertNotIn("stopped r1", (self.dir(".admin") / "log.tsv").read_text())
        self.admin("set", "--thread", "t2", "--replace", "--expect", "recovering:t9", "--stopped", "r1")
        meta = self.meta(".admin")
        self.assertEqual((meta["thread"], meta["generation"], meta["previousThread"]), ("t2", 3, "t1"))
        self.assertEqual([row[2:] for row in self.rows(".admin", "log.tsv") if "stopped r1" in row[4]],
                         [["t2@3", "recorded", "replaced recovering:t9; stopped r1"]])

    def test_the_claim_raises_the_admin_floor_in_the_landing_store(self):
        env = dict(os.environ, XDG_STATE_HOME=str(Path(self.temporary.name) / "state"))
        git = lambda *command: subprocess.run(command, cwd=self.project, capture_output=True, text=True, check=True)
        git("git", "init", "-q", "-b", "main")
        (self.project / "a.txt").write_text("a\n")
        git("git", "add", "-A")
        git("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
        land = lambda *args: subprocess.run([sys.executable, str(LAND_SCRIPT), "--repo", str(self.project), *args],
                                            capture_output=True, text=True, env=env)
        self.assertEqual(land("init", "--trunk", "lane", "--mode", "local", "--base", "main").returncode, 0)
        self.open_admin()
        for args in (("set", "--thread", "t1"), ("set", "--thread", "recovering:t9", "--replace", "--expect", "t1")):
            argv = ["--store", str(self.store), "--at", str(self.dir(".admin")), *args]
            command = [sys.executable, str(SCRIPT), *argv]
            result = call(self, SCRIPT, argv, env=env).completed(command)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(land("owner", "--prefix", ".admin/", "--generation", "1").stderr.strip(),
                         "land: .admin/ is at generation 2; a floor never lowers")

    def test_expect_works_only_in_the_admin_store(self):
        self.open("docs")
        self.brigade("docs", "set", "--thread", "t1")
        self.assertEqual(self.brigade("docs", "set", "--thread", "t2", "--replace", "--expect", "t1", ok=False),
                         "brigade: --expect works only in the executive admin's store")
        self.assertEqual(self.meta("docs")["thread"], "t1")
