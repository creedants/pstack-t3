import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "t3/added/landing/scripts/land.py"
sys.path.insert(0, str(SCRIPT.parent))

import land  # noqa: E402

REVIEWER = "codex/gpt-6.1-sol"


def sh(*args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    if result.returncode != 0:
        raise AssertionError(f"{args} failed: {result.stderr}")
    return result.stdout.strip()


class LandingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.base / "state")})
        self.env.start()
        sh("git", "init", "-q", "--bare", "-b", "main", "origin.git", cwd=self.base)
        sh("git", "clone", "-q", "origin.git", "work", cwd=self.base)
        self.work = self.base / "work"
        sh("git", "checkout", "-q", "-b", "main", cwd=self.work)
        for name in ("a.txt", "b.txt", "lib/x.py"):
            (self.work / name).parent.mkdir(exist_ok=True)
            (self.work / name).write_text(f"{name}\n")
        (self.work / "check.sh").write_text("#!/bin/sh\n! grep -rq BROKEN --include=*.txt .\n")
        (self.work / "check.sh").chmod(0o755)
        self.commit("init")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def commit(self, message, cwd=None):
        sh("git", "add", "-A", cwd=cwd or self.work)
        sh("git", "commit", "-qm", message, cwd=cwd or self.work)
        return sh("git", "rev-parse", "HEAD", cwd=cwd or self.work)

    def land(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.work), *args],
                                capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def init(self, mode="auto", batch=1, **extra):
        args = ["init", "--trunk", extra.get("trunk", "main"), "--mode", mode, "--check", "./check.sh", "--batch", str(batch)]
        if "base" in extra:
            args += ["--base", extra["base"]]
        return self.land(*args)

    def worker(self, name, edits, base="origin/main"):
        """A worker branch in its own worktree with one commit. Returns its SHA."""
        path = self.base / name
        sh("git", "worktree", "add", "-q", "-b", name, str(path), base, cwd=self.work)
        for file, text in edits.items():
            (path / file).parent.mkdir(parents=True, exist_ok=True)
            (path / file).write_text(text)
        return self.commit(name, cwd=path)

    def origin_log(self):
        return sh("git", "log", "--format=%s", "main", cwd=self.base / "origin.git").splitlines()

    def test_leases_refuse_overlap_including_aliases_and_the_whole_repo(self):
        self.init()
        self.assertEqual(self.land("lease", "claim", "--holder", "perf/D1", "--paths", "lib"), "L1")
        self.assertIn("L1 held by perf/D1 on lib", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "a.txt/../lib/x.py", ok=False))
        self.assertIn("L1 held by perf/D1", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", ".", ok=False))
        self.assertIn("leaves the repository", self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "../etc", ok=False))
        self.assertEqual(self.land("lease", "claim", "--holder", "bugs/D1", "--paths", "lib2,a.txt"), "L2")

    def test_submit_requires_the_holders_own_lease_covering_every_changed_path(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "a2\n", "b.txt": "b2\n"})
        self.land("lease", "claim", "--holder", "perf/D1", "--paths", "a.txt")
        self.land("lease", "claim", "--holder", "perf/D2", "--paths", "b.txt")
        self.assertIn("not an active lease held by bugs/D9",
                      self.land("submit", "--holder", "bugs/D9", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False))
        self.assertIn("outside L1: b.txt",
                      self.land("submit", "--holder", "perf/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False))

    def test_a_batch_lands_in_one_push_and_resubmitting_is_idempotent(self):
        self.init(batch=4)
        shas = [self.worker(f"w{n}", {f: f"{f} v{n}\n"}) for n, f in enumerate(["a.txt", "b.txt", "lib/x.py"], 1)]
        for n, (sha, path) in enumerate(zip(shas, ["a.txt", "b.txt", "lib"]), 1):
            self.land("lease", "claim", "--holder", f"r/D{n}", "--paths", path)
            self.assertEqual(self.land("submit", "--holder", f"r/D{n}", "--branch", f"w{n}", "--sha", sha, "--lease", f"L{n}", "--reviewer", REVIEWER), f"Q{n}")
        self.assertEqual(self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", shas[0], "--lease", "L1", "--reviewer", REVIEWER), "Q1 already queued")
        self.assertEqual(self.land("land"), "landed Q1 (r/D1), Q2 (r/D2), Q3 (r/D3)")
        self.assertEqual(self.origin_log(), ["w3", "w2", "w1", "init"])
        self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_a_failing_batch_lands_the_good_entries_alone_and_bounces_the_bad_one(self):
        self.init(batch=3)
        good1 = self.worker("w1", {"a.txt": "fine\n"})
        bad = self.worker("w2", {"b.txt": "BROKEN\n"})
        good2 = self.worker("w3", {"lib/x.py": "fine\n"})
        for n, (sha, path) in enumerate([(good1, "a.txt"), (bad, "b.txt"), (good2, "lib")], 1):
            self.land("lease", "claim", "--holder", f"r/D{n}", "--paths", path)
            self.land("submit", "--holder", f"r/D{n}", "--branch", f"w{n}", "--sha", sha, "--lease", f"L{n}", "--reviewer", REVIEWER)
        out = self.land("land")
        self.assertIn("landed Q1 (r/D1), Q3 (r/D3)", out)
        self.assertIn("bounced Q2 (r/D2): checks failed: `./check.sh` exited 1", out)
        self.assertEqual(self.origin_log(), ["w3", "w1", "init"])
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L2 active r/D2")

    def test_a_conflict_bounces_and_the_fixed_resubmission_lands(self):
        self.init()
        first = self.worker("w1", {"a.txt": "one\n"})
        second = self.worker("w2", {"a.txt": "two\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", first, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("land")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", second, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "bounced Q2 (r/D2): conflict with trunk")
        path = self.base / "w2"
        sh("git", "fetch", "-q", "origin", cwd=path)
        sh("git", "reset", "-q", "--hard", "origin/main", cwd=path)
        (path / "a.txt").write_text("one\ntwo\n")
        fixed = self.commit("w2 fixed", cwd=path)
        self.assertEqual(self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", fixed, "--lease", "L2", "--reviewer", REVIEWER), "Q3")
        self.assertEqual(self.land("land"), "landed Q3 (r/D2)")

    def test_landing_uses_the_pinned_sha_even_when_the_branch_moves_on(self):
        self.init()
        reviewed = self.worker("w1", {"a.txt": "reviewed\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", reviewed, "--lease", "L1", "--reviewer", REVIEWER)
        (self.base / "w1/a.txt").write_text("unreviewed\n")
        self.commit("sneaky", cwd=self.base / "w1")
        self.land("land")
        self.assertEqual(self.origin_log(), ["w1", "init"])
        self.assertEqual(sh("git", "show", "main:a.txt", cwd=self.base / "origin.git"), "reviewed")

    def test_trunk_moved_by_a_human_is_rebased_onto_and_a_rewind_pauses_the_queue(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        (self.work / "b.txt").write_text("human\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
        self.assertEqual(self.origin_log(), ["w1", "human", "init"])
        sh("git", "push", "-q", "--force", "origin", "HEAD~1:main", cwd=self.work)
        other = self.worker("w2", {"b.txt": "agent2\n"})
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "b.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", other, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))
        self.assertIn("queue paused", self.land("land"))
        self.land("resume")
        self.assertEqual(self.land("land"), "landed Q2 (r/D2)")

    def test_a_crash_after_the_push_is_recovered_as_landed_without_pushing_again(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        store = land.Store.for_repo(self.work)
        real = land.publish

        def publish_then_crash(*args):
            real(*args)
            raise KeyboardInterrupt

        with mock.patch.object(land, "publish", publish_then_crash), self.assertRaises(KeyboardInterrupt):
            land.land(store)
        self.assertEqual(self.land("status", "Q1").split(" (")[0], "Q1 landing")
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertIn("Q1 landed", self.land("status", "Q1"))
        self.assertIn("recovered after an interrupted run", self.land("status", "Q1"))
        self.assertEqual(self.origin_log(), ["w1", "init"])

    def test_a_held_queue_lock_reports_busy(self):
        self.init()
        store = land.Store.for_repo(self.work)
        import fcntl
        with open(store.dir / ".queue.lock", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            self.assertIn("queue busy", self.land("land"))

    def test_local_mode_lands_on_a_queue_owned_ref_and_never_moves_a_branch(self):
        self.init(mode="local", trunk="perf", base="main")
        sha = self.worker("w1", {"a.txt": "fast\n"}, base="main")
        self.land("lease", "claim", "--holder", "perf/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "perf/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        main_before = sh("git", "rev-parse", "main", cwd=self.work)
        self.assertEqual(self.land("land"), "landed Q1 (perf/D1)")
        self.assertEqual(sh("git", "show", "refs/landing/perf:a.txt", cwd=self.work), "fast")
        self.assertEqual(sh("git", "rev-parse", "main", cwd=self.work), main_before)
        self.assertEqual(sh("git", "status", "--porcelain", cwd=self.work), "")

    def fake_gh(self):
        """A gh stand-in. PR url lives in pr-url, the poll answer in pr-state. crash-on-create kills land after creating."""
        fake = self.base / "gh"
        fake.write_text(f"""#!{sys.executable}
import os, signal, sys
from pathlib import Path
base = Path({str(self.base)!r})
args = sys.argv[1:]
if args[:2] == ["pr", "create"]:
    (base / "pr-url").write_text("https://github.com/o/r/pull/9")
    if (base / "crash-on-create").exists():
        (base / "crash-on-create").unlink()
        os.kill(os.getppid(), signal.SIGKILL)
    print("https://github.com/o/r/pull/9")
elif args[:2] == ["pr", "merge"]:
    print(" ".join(args), file=open(base / "merge-calls", "a"))
    if (base / "merge-refused").exists():
        print("GraphQL: At least 1 approving review is required by reviewers with write access.", file=sys.stderr)
        sys.exit(1)
    if "--auto" in args:
        if not (base / "required-checks").exists():
            print("GraphQL: Pull request is in clean status (enablePullRequestAutoMerge)", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    import subprocess
    head = subprocess.run(["git", "--git-dir", str(base / "origin.git"), "rev-parse", "refs/heads/landing/q1"],
                          capture_output=True, text=True).stdout.strip()
    (base / "pr-state").write_text(f"MERGED {{head}} 1111111111111111111111111111111111111111")
elif args[:2] == ["pr", "view"] and "url" in args:
    if not (base / "pr-url").exists():
        sys.exit(1)
    print((base / "pr-url").read_text())
elif args[:2] == ["pr", "view"]:
    print((base / "pr-state").read_text() if (base / "pr-state").exists() else "OPEN")
""")
        fake.chmod(0o755)
        return mock.patch.dict(os.environ, {"LAND_GH": str(fake)})

    def queue_one(self, path="a.txt", text="agent\n", name="w1", holder="r/D1"):
        sha = self.worker(name, {path: text})
        lease = self.land("lease", "claim", "--holder", holder, "--paths", path)
        return self.land("submit", "--holder", holder, "--branch", name, "--sha", sha, "--lease", lease, "--reviewer", REVIEWER)

    def test_human_mode_opens_a_pr_and_marks_landed_when_it_merges(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs for Q1 (r/D1) https://github.com/o/r/pull/9")
            candidate = sh("git", "rev-parse", "landing/q1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_merges_its_own_pr_when_there_are_no_checks_to_wait_for(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             ["pr merge https://github.com/o/r/pull/9 --auto --merge", "pr merge https://github.com/o/r/pull/9 --merge"])
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lets_github_merge_after_required_checks_and_asks_once(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: Q1 (r/D1) https://github.com/o/r/pull/9")
            self.assertEqual(self.land("land"), "nothing to land")
            candidate = sh("git", "rev-parse", "landing/q1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual(len((self.base / "merge-calls").read_text().splitlines()), 1)

    def test_merge_mode_pauses_when_github_requires_a_human_approval(self):
        with self.fake_gh():
            (self.base / "merge-refused").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused: GitHub refused to merge https://github.com/o/r/pull/9", out)
            self.assertIn("approving review is required", out)

    def test_the_mode_changes_only_while_nothing_is_in_flight(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            self.assertIn("awaiting merge; change the mode when the queue is empty", self.land("mode", "merge", ok=False))
            candidate = sh("git", "rev-parse", "landing/q1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.land("land")
            self.assertEqual(self.land("mode", "merge", "--merge-method", "squash"), "landing mode is now merge, merging with --squash")
            self.assertTrue(self.land("status").startswith("merge mode onto"))
            self.assertIn("needs a new contract", self.land("mode", "local", ok=False))

    def test_a_contract_written_with_the_old_auto_mode_reads_as_push(self):
        self.init(mode="auto")
        self.assertTrue(self.land("status").startswith("push mode onto"))
        self.assertEqual(self.land("init", "--trunk", "main", "--mode", "push", "--check", "./check.sh").split()[0], "updated")

    def test_human_mode_adopts_the_pr_a_crashed_run_created(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            (self.base / "crash-on-create").write_text("")
            result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.work), "land"], capture_output=True, text=True, env=os.environ.copy())
            self.assertEqual(result.returncode, -9)
            self.land("land")
            self.assertIn("awaiting-merge (r/D1, w1). https://github.com/o/r/pull/9", self.land("status", "Q1"))

    def test_human_mode_bounces_a_pr_merged_with_a_head_the_queue_did_not_check(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            (self.base / "pr-state").write_text("MERGED 0123456789abcdef deadbeef")
            self.assertIn("bounced Q1 (r/D1): merged at deadbeef with head 0123456789ab", self.land("land"))

    def test_human_mode_pauses_when_trunk_loses_a_landed_commit(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            sh("git", "fetch", "-q", "origin", cwd=self.work)
            merged = self.commit_on_origin("human merge")
            (self.base / "pr-state").write_text(f"MERGED {sh('git', 'rev-parse', 'landing/q1', cwd=self.base / 'origin.git')} {merged}")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            sh("git", "push", "-q", "--force", "origin", "HEAD:main", cwd=self.work)
            self.assertIn("queue paused: trunk no longer contains the last landed commit", self.land("land"))

    def commit_on_origin(self, message):
        """Simulate GitHub merging: push a new commit to origin main. Returns its SHA."""
        clone = self.base / "merger"
        if not clone.exists():
            sh("git", "clone", "-q", "origin.git", "merger", cwd=self.base)
        sh("git", "pull", "-q", "origin", "main", cwd=clone)
        (clone / "merged.txt").write_text(message)
        sha = self.commit(message, cwd=clone)
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=clone)
        return sha

    def test_a_failed_pin_leaves_nothing_queued(self):
        self.init()
        wrapper = self.base / "bin"
        wrapper.mkdir()
        real = sh("sh", "-c", "command -v git", cwd=self.base)
        (wrapper / "git").write_text(f'#!/bin/sh\ncase "$*" in *refs/landing/pins/*) exit 1;; esac\nexec {real} "$@"\n')
        (wrapper / "git").chmod(0o755)
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        with mock.patch.dict(os.environ, {"PATH": f"{wrapper}:{os.environ['PATH']}"}):
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER, ok=False)
        self.assertEqual(self.land("status"), "push mode onto refs/remotes/origin/main. leases held: 1.")

    def test_a_policy_rejection_pauses_the_queue_after_one_check_run(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", f"echo run >> {self.base}/runs")
        hook = self.base / "origin.git/hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho rejected by policy >&2\nexit 1\n")
        hook.chmod(0o755)
        self.queue_one()
        self.assertIn("queue paused: push rejected: remote: rejected by policy", self.land("land"))
        self.assertEqual((self.base / "runs").read_text(), "run\n")
        self.assertIn("Q1 queued", self.land("status", "Q1"))

    def test_a_check_that_escapes_its_group_cannot_hold_the_queue(self):
        script = self.base / "escape.py"
        script.write_text("import os, time\nos.setsid()\ntime.sleep(20)\n")
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", f"{sys.executable} {script}; true", "--timeout", "1")
        self.queue_one()
        started = __import__("time").monotonic()
        self.assertIn("timed out", self.land("land"))
        self.assertLess(__import__("time").monotonic() - started, 12)

    def test_non_utf8_check_output_and_file_contents_land(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", "printf '\\377'")
        self.worker("w1", {"seed.txt": "seed\n"})
        (self.base / "w1/latin.txt").write_bytes(b"caf\xe9\n")
        sha = self.commit("latin", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "seed.txt,latin.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed Q1 (r/D1)")

    def test_non_ascii_file_names_match_their_lease(self):
        self.init()
        self.assertEqual(self.queue_one(path="café.txt"), "Q1")
        self.assertEqual(self.land("land"), "landed Q1 (r/D1)")

    def test_landing_works_without_a_git_identity(self):
        self.init()
        self.queue_one()
        empty = self.base / "home"
        empty.mkdir()
        bare_env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        bare_env.update({"HOME": str(empty), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.work), "land"], capture_output=True, text=True, env=bare_env)
        self.assertEqual(result.stdout.strip(), "landed Q1 (r/D1)", result.stderr)

    def test_fingerprint_sees_whitespace_and_ignores_line_numbers(self):
        repo = self.work
        base = sh("git", "rev-parse", "HEAD", cwd=repo)
        (repo / "lib/x.py").write_text("lib/x.py\n    return 1\n")
        indented = self.commit("indent")
        sh("git", "reset", "-q", "--hard", base, cwd=repo)
        (repo / "lib/x.py").write_text("lib/x.py\n  return 1\n")
        other = self.commit("indent 2")
        self.assertNotEqual(land.fingerprint(repo, base, indented), land.fingerprint(repo, base, other))
        sh("git", "reset", "-q", "--hard", base, cwd=repo)
        (repo / "lib/x.py").write_text("header\nlib/x.py\n")
        moved = self.commit("header")
        (repo / "lib/x.py").write_text("header\nlib/x.py\n    return 1\n")
        indented_after_header = self.commit("indent after header")
        self.assertEqual(land.fingerprint(repo, base, indented), land.fingerprint(repo, moved, indented_after_header))

    def test_a_check_that_hangs_is_killed_at_the_timeout(self):
        self.land("init", "--trunk", "main", "--mode", "auto", "--check", "sleep 30", "--timeout", "1")
        sha = self.worker("w1", {"a.txt": "agent\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertIn("bounced Q1 (r/D1): checks failed: `sleep 30` timed out", self.land("land"))

    def test_the_contract_refuses_a_conflicting_init_and_other_clones(self):
        self.init()
        self.assertIn("already sets trunk='main'", self.land("init", "--trunk", "dev", "--mode", "auto", ok=False))
        sh("git", "clone", "-q", "origin.git", "other", cwd=self.base)
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.base / "other"), "status"],
                                capture_output=True, text=True, env=os.environ.copy())
        self.assertIn("has no landing contract", result.stderr)

    def test_an_exclusive_slot_waits_for_every_other_slot_and_refuses_nesting(self):
        import time
        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 2}))
        marks = self.base / "marks"
        worker = subprocess.Popen([sys.executable, str(SCRIPT), "slot", "--", "sh", "-c", f"echo worker-start >> {marks}; sleep 2; echo worker-end >> {marks}"],
                                  env=os.environ.copy())
        time.sleep(0.5)
        bench = subprocess.run([sys.executable, str(SCRIPT), "slot", "--exclusive", "--", "sh", "-c", f"echo bench >> {marks}"],
                               capture_output=True, text=True, timeout=30, env=os.environ.copy())
        worker.wait(timeout=30)
        self.assertEqual(bench.returncode, 0, bench.stderr)
        self.assertEqual(marks.read_text().split(), ["worker-start", "worker-end", "bench"])
        nested = subprocess.run([sys.executable, str(SCRIPT), "slot", "--", sys.executable, str(SCRIPT), "slot", "--exclusive", "--", "true"],
                                capture_output=True, text=True, timeout=30, env=os.environ.copy())
        self.assertNotEqual(nested.returncode, 0)
        self.assertIn("must be the outermost slot", nested.stderr)

    def test_human_mode_uses_the_submitted_pr_title_and_body(self):
        log = self.base / "gh-log"
        with self.fake_gh():
            fake = self.base / "gh"
            fake.write_text(fake.read_text().replace('if args[:2] == ["pr", "create"]:',
                                                     f'open({str(log)!r}, "a").write(repr(args) + "\\n")\nif args[:2] == ["pr", "create"]:'))
            self.init(mode="human")
            sha = self.worker("w1", {"a.txt": "agent\n"})
            self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
            body = self.base / "body.md"
            body.write_text("Cuts load time by 300 ms.\n")
            self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER,
                      "--title", "Faster load", "--body-file", str(body))
            self.land("land")
        create = [line for line in log.read_text().splitlines() if "'create'" in line][0]
        self.assertIn("'--title', 'Faster load'", create)
        self.assertIn("Cuts load time by 300 ms.", create)
        self.assertIn("Reviewed by codex/gpt-6.1-sol", create)

    def test_a_store_from_before_pr_text_columns_is_migrated(self):
        self.init()
        store = land.Store.for_repo(self.work)
        store.db.execute("DROP TABLE entry")
        store.db.executescript(land.SCHEMA)
        self.assertNotIn("title", {row["name"] for row in store.db.execute("PRAGMA table_info(entry)")})
        store.db.close()
        self.assertEqual(self.queue_one(), "Q1")
        self.assertEqual(self.land("land"), "landed Q1 (r/D1)")

    def test_nested_slots_do_not_deadlock_with_one_slot(self):
        governor = self.base / "state/pstack-t3/governor"
        governor.mkdir(parents=True)
        (governor / "governor.json").write_text(json.dumps({"slots": 1}))
        result = subprocess.run([sys.executable, str(SCRIPT), "slot", "--", sys.executable, str(SCRIPT), "slot", "--", "true"],
                                capture_output=True, text=True, timeout=20, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
