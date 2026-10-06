import json
import os
import shutil
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


def git_env():
    return {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def sh(*args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=git_env())
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
        self.install_github_remote()

    def install_github_remote(self):
        """Point origin at https://github.com/o/r.git and send transport commands to the bare repo.

        git remote get-url still prints the GitHub URL. push, fetch, ls-remote, and pull
        rewrite that URL to origin.git, so a test never contacts GitHub.
        """
        real = shutil.which("git")
        bindir = self.base / "git-wrap"
        bindir.mkdir()
        origin = str(self.base / "origin.git")
        script = bindir / "git"
        script.write_text(
            "#!" + sys.executable + "\n"
            "import os, subprocess, sys\n"
            f"real = {real!r}\n"
            f"origin = {origin!r}\n"
            "args = sys.argv[1:]\n"
            "if len(args) >= 2 and args[0] == 'remote' and args[1] == 'get-url':\n"
            "    os.execv(real, [real, *args])\n"
            "if args and args[0] in ('push', 'fetch', 'ls-remote', 'pull'):\n"
            "    probed = subprocess.run([real, 'remote', 'get-url', 'origin'], capture_output=True, text=True)\n"
            "    url = probed.stdout.strip()\n"
            "    if probed.returncode == 0 and url.startswith('https://github.com/'):\n"
            "        os.execv(real, [real, '-c', 'url.' + origin + '/.insteadOf=' + url, *args])\n"
            "os.execv(real, [real, *args])\n"
        )
        script.chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
        sh("git", "remote", "set-url", "origin", "https://github.com/o/r.git", cwd=self.work)

    def tearDown(self):
        self.env.stop()
        self.temporary.cleanup()

    def commit(self, message, cwd=None):
        sh("git", "add", "-A", cwd=cwd or self.work)
        sh("git", "commit", "-qm", message, cwd=cwd or self.work)
        return sh("git", "rev-parse", "HEAD", cwd=cwd or self.work)

    def git_raw(self, *args, cwd):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=git_env())

    def land(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(self.work), *args],
                                capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return (result.stdout if ok else result.stderr).strip()

    def init(self, mode="auto", batch=1, **extra):
        args = ["init", "--trunk", extra.get("trunk", "main"), "--mode", mode, "--check", "./check.sh", "--batch", str(batch)]
        if "merge_method" in extra:
            args += ["--merge-method", extra["merge_method"]]
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

    def ref_exists(self, ref, cwd):
        return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd, capture_output=True).returncode == 0

    def install_racing_branch_delete(self):
        """While git push --delete runs, another deleter holds the ref lock and removes the ref.

        git 2.55 then rejects the push with "File exists" and "reference already exists".
        That stderr does not contain "does not exist", and ls-remote shows the ref gone.
        """
        hook = self.base / "origin.git" / "hooks" / "pre-receive"
        marker = self.base / "race-deleted"
        hook.write_text(f"""#!/bin/sh
marker={marker}
while read old new ref; do
  if [ "$new" = "0000000000000000000000000000000000000000" ]; then
    path=$(git rev-parse --git-path "$ref")
    echo x > "$path.lock"
    rm -f "$path"
    echo "$ref" >> "$marker"
  fi
done
exit 0
""")
        hook.chmod(0o755)

    def record_pushes(self):
        """Log each git push while later commands run. The real git stays behind the wrapper."""
        log = self.base / "git-pushes"
        bindir = self.base / "bin"
        bindir.mkdir(exist_ok=True)
        real = subprocess.run(["which", "git"], capture_output=True, text=True, check=True).stdout.strip()
        script = bindir / "git"
        script.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = push ]; then\n"
            f"  printf '%s\\n' \"$*\" >> '{log}'\n"
            "fi\n"
            f"exec '{real}' \"$@\"\n"
        )
        script.chmod(0o755)
        self._push_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(bindir) + os.pathsep + self._push_path
        return log

    def stop_recording_pushes(self):
        os.environ["PATH"] = self._push_path

    def assert_pushes_stay_on_their_refs(self, log):
        lines = log.read_text().splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("push --no-follow-tags "), line)
            self.assertNotIn("--dry-run", line)
            self.assertFalse(any(part.startswith(":") for part in line.split()), line)

    def merged_queue_branch(self):
        """Save the queue head, then mark the PR merged at that head."""
        remote = self.base / "origin.git"
        sha = sh("git", "rev-parse", "refs/heads/landing/q1", cwd=remote)
        (self.base / "pr-state").write_text(
            f"MERGED {sha} 1111111111111111111111111111111111111111")
        return sha

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

    def use_non_english_locale(self):
        """Set LANG and LC_ALL to de_DE.UTF-8 for the rest of this test.

        Returns whether git on this machine prints German under that locale.
        When it does not, git on PATH hides the English conflict strings unless
        that process has LC_ALL=C, and records each cherry-pick environment.
        """
        self.git_locale_log = self.base / "git-locale.log"
        translated = False
        try:
            locpath = self.base / "locales"
            locpath.mkdir()
            compiled = subprocess.run(
                ["localedef", "-f", "UTF-8", "-i", "de_DE", str(locpath / "de_DE.UTF-8")],
                capture_output=True)
        except OSError:
            compiled = None
        if compiled is not None and compiled.returncode == 0:
            os.environ["LOCPATH"] = str(locpath)
            env = os.environ.copy()
            env["LANG"] = "de_DE.UTF-8"
            env["LC_ALL"] = "de_DE.UTF-8"
            probe = subprocess.run(["git", "status"], cwd=self.work, capture_output=True, text=True, env=env)
            translated = "Auf Branch" in probe.stdout
        os.environ["LANG"] = "de_DE.UTF-8"
        os.environ["LC_ALL"] = "de_DE.UTF-8"
        if not translated:
            self.install_c_locale_gate()
        return translated

    def install_c_locale_gate(self):
        """Hide English conflict text unless this git process has LC_ALL=C."""
        current = shutil.which("git")
        bindir = self.base / "locale-gate"
        bindir.mkdir()
        script = bindir / "git"
        script.write_text(
            "#!" + sys.executable + "\n"
            "import os, subprocess, sys\n"
            f"real = {current!r}\n"
            f"log = {str(self.git_locale_log)!r}\n"
            "args = sys.argv[1:]\n"
            "if 'cherry-pick' in args:\n"
            "    with open(log, 'a', encoding='utf-8') as handle:\n"
            "        handle.write('cherry-pick LC_ALL=%s\\n' % os.environ.get('LC_ALL', ''))\n"
            "proc = subprocess.run([real, *args], capture_output=True)\n"
            "out, err = proc.stdout, proc.stderr\n"
            "if os.environ.get('LC_ALL') != 'C':\n"
            "    out = out.replace(b'CONFLICT', b'KONFLIKT').replace(b'could not apply', b'nicht anwenden')\n"
            "    err = err.replace(b'CONFLICT', b'KONFLIKT').replace(b'could not apply', b'nicht anwenden')\n"
            "sys.stdout.buffer.write(out)\n"
            "sys.stderr.buffer.write(err)\n"
            "raise SystemExit(proc.returncode)\n"
        )
        script.chmod(0o755)
        os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]

    def test_a_conflict_under_a_non_english_locale_bounces(self):
        translated = self.use_non_english_locale()
        self.init()
        first = self.worker("w1", {"a.txt": "one\n"})
        second = self.worker("w2", {"a.txt": "two\n"})
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", first, "--lease", "L1", "--reviewer", REVIEWER)
        self.land("land")
        self.land("lease", "claim", "--holder", "r/D2", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D2", "--branch", "w2", "--sha", second, "--lease", "L2", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "bounced Q2 (r/D2): conflict with trunk")
        if not translated:
            recorded = self.git_locale_log.read_text().splitlines()
            self.assertTrue(recorded)
            for line in recorded:
                self.assertEqual(line, "cherry-pick LC_ALL=C")

    def test_a_conflict_rerere_resolved_to_trunk_bounces(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "worker\n"})
        (self.work / "a.txt").write_text("human\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        sh("git", "config", "rerere.enabled", "true", cwd=self.work)
        sh("git", "config", "rerere.autoupdate", "true", cwd=self.work)
        record = self.base / "record"
        sh("git", "worktree", "add", "-q", "--detach", str(record), "origin/main", cwd=self.work)
        conflict = self.git_raw("cherry-pick", sha, cwd=record)
        self.assertNotEqual(conflict.returncode, 0, conflict.stdout + conflict.stderr)
        (record / "a.txt").write_text("human\n")
        sh("git", "rerere", cwd=record)
        sh("git", "add", "a.txt", cwd=record)
        sh("git", "cherry-pick", "--abort", cwd=record)
        replay = self.git_raw("cherry-pick", sha, cwd=record)
        detail = replay.stdout + replay.stderr
        self.assertNotEqual(replay.returncode, 0, detail)
        self.assertIn("CONFLICT", detail)
        self.assertIn("could not apply", detail)
        self.assertEqual(self.git_raw("status", "--porcelain", cwd=record).stdout, "")
        self.assertEqual(self.git_raw("ls-files", "-u", cwd=record).stdout, "")
        sh("git", "cherry-pick", "--abort", cwd=record)
        self.assertEqual(self.land("land"), "bounced Q1 (r/D1): conflict with trunk")
        status = self.land("status", "Q1")
        self.assertIn("Q1 bounced", status)
        self.assertIn("conflict with trunk", status)
        self.assertNotIn("already in trunk", status)
        self.assertEqual(self.land("lease", "list").split(" until ")[0], "L1 active r/D1")
        self.assertEqual(self.origin_log(), ["human", "init"])
        self.assertEqual(sh("git", "show", "main:a.txt", cwd=self.base / "origin.git"), "human")

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

    def test_a_patch_already_on_trunk_lands_without_replaying_it(self):
        self.init()
        sha = self.worker("w1", {"a.txt": "agent\n"})
        (self.work / "a.txt").write_text("agent\n")
        self.commit("human")
        sh("git", "push", "-q", "origin", "main", cwd=self.work)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
        self.assertEqual(self.origin_log(), ["human", "init"])
        self.assertIn("already in trunk", self.land("status", "Q1"))

    def test_a_commit_that_started_empty_pauses_instead_of_landing(self):
        self.init()
        path = self.base / "w1"
        sh("git", "worktree", "add", "-q", "-b", "w1", str(path), "origin/main", cwd=self.work)
        (path / "a.txt").write_text("agent\n")
        self.commit("w1", cwd=path)
        sh("git", "commit", "-q", "--allow-empty", "-m", "empty", cwd=path)
        sha = sh("git", "rev-parse", "HEAD", cwd=path)
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        paused = self.land("land")
        self.assertIn("queue paused", paused)
        self.assertIn("cherry-pick failed", paused)
        self.assertNotIn("landed", paused)
        self.assertEqual(self.origin_log(), ["init"])

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
import json, os, signal, subprocess, sys
from pathlib import Path
base = Path({str(self.base)!r})
args = sys.argv[1:]
with open(base / "gh-calls", "a") as calls:
    print(" ".join(args), file=calls)
if args[:2] == ["repo", "view"]:
    if (base / "repo-view-fails").exists():
        print("repo view failed", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({{"nameWithOwner": "o/r"}}))
    sys.exit(0)
if args[:1] == ["api"] and any("/branches/" in arg for arg in args):
    endpoint = next(arg for arg in args if "/branches/" in arg)
    branch = endpoint.split("/branches/", 1)[1]
    if (base / "forge-says-missing").exists():
        print("branch-status 404", file=open(base / "gh-calls", "a"))
        print("HTTP/2.0 404 Not Found")
        print()
        print(json.dumps({{"message": "Branch not found", "status": "404"}}))
        sys.exit(1)
    if (base / "branch-query-fails").exists():
        print("branch-status 500", file=open(base / "gh-calls", "a"))
        print("HTTP/2.0 500 Internal Server Error")
        print()
        print(json.dumps({{"message": "unavailable"}}))
        sys.exit(1)
    ref = "refs/heads/" + branch
    exists = subprocess.run(
        ["git", "--git-dir", str(base / "origin.git"), "rev-parse", "--verify", "--quiet", ref],
        capture_output=True).returncode == 0
    status = "200" if exists else "404"
    print("branch-status " + status, file=open(base / "gh-calls", "a"))
    print("HTTP/2.0 " + ("200 OK" if exists else "404 Not Found"))
    print()
    if exists:
        print(json.dumps({{"name": branch}}))
        sys.exit(0)
    print(json.dumps({{"message": "Branch not found", "status": "404"}}))
    sys.exit(1)
if args[:2] == ["pr", "create"]:
    (base / "pr-url").write_text("https://github.com/o/r/pull/9")
    if (base / "crash-on-create").exists():
        (base / "crash-on-create").unlink()
        os.kill(os.getppid(), signal.SIGKILL)
    print("https://github.com/o/r/pull/9")
elif args[:2] == ["pr", "merge"]:
    print(" ".join(args), file=open(base / "merge-calls", "a"))
    if "--disable-auto" in args:
        if (base / "disable-auto-fails").exists():
            print((base / "disable-auto-fails").read_text() or "API unavailable", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    if (base / "merge-refused").exists():
        print("GraphQL: At least 1 approving review is required by reviewers with write access.", file=sys.stderr)
        sys.exit(1)
    if "--auto" in args and (base / "auto-merge-disabled").exists():
        print("GraphQL: Auto merge is not allowed for this repository (enablePullRequestAutoMerge)", file=sys.stderr)
        sys.exit(1)
    if "--auto" not in args and (base / "plain-merge-fails").exists():
        print((base / "plain-merge-fails").read_text().strip() or "merge conflict", file=sys.stderr)
        sys.exit(1)
    if "--auto" in args and not (base / "auto-merges-immediately").exists():
        if not (base / "required-checks").exists():
            print("GraphQL: Pull request is in clean status (enablePullRequestAutoMerge)", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    checks_now = (base / "checks").read_text().strip() if (base / "checks").exists() else ""
    if (base / "policy-until-passed").exists() and checks_now != "passed":
        print("X Pull request o/r#9 is not mergeable: the base branch policy prohibits the merge.", file=sys.stderr)
        sys.exit(1)
    import subprocess
    head = subprocess.run(["git", "--git-dir", str(base / "origin.git"), "rev-parse", "refs/heads/landing/q1"],
                          capture_output=True, text=True).stdout.strip()
    (base / "pr-state").write_text(f"MERGED {{head}} 1111111111111111111111111111111111111111")
    if (base / "merge-state-lags").exists():
        (base / "pr-state-after").write_text((base / "pr-state").read_text())
        (base / "pr-state").write_text(f"OPEN {{head}} ")
elif args[:2] == ["pr", "view"] and any("statusCheckRollup" in arg for arg in args):
    if (base / "checks-query-fails").exists():
        print("API unavailable", file=sys.stderr)
        sys.exit(1)
    if (base / "pr-checks.json").exists():
        doc = (base / "pr-checks.json").read_text()
    else:
        kind = (base / "checks").read_text().strip() if (base / "checks").exists() else ""
        review = "REVIEW_REQUIRED" if (base / "merge-refused").exists() else "APPROVED"
        rollup = {{
            "pending": [{{"name": "test", "status": "IN_PROGRESS"}}],
            "failed": [{{"name": "test (3.12)", "status": "COMPLETED", "conclusion": "FAILURE"}}],
            "passed": [
                {{"name": "test (3.10)", "status": "COMPLETED", "conclusion": "SUCCESS"}},
                {{"name": "test (3.12)", "status": "COMPLETED", "conclusion": "SUCCESS"}},
            ],
        }}.get(kind, [])
        doc = json.dumps({{"reviewDecision": review, "statusCheckRollup": rollup}})
    payload = json.loads(doc)
    if "autoMergeRequest" not in payload:
        marker = base / "auto-merge-request.json"
        payload["autoMergeRequest"] = json.loads(marker.read_text()) if marker.exists() else None
        doc = json.dumps(payload)
    query = args[args.index("-q") + 1]
    ran = subprocess.run(["jq", "-r", query], input=doc, capture_output=True, text=True)
    if ran.returncode != 0:
        print(ran.stderr, file=sys.stderr)
        sys.exit(1)
    sys.stdout.write(ran.stdout if ran.stdout.endswith("\\n") else ran.stdout + "\\n")
    flip = base / "checks-flip"
    if flip.exists():
        (base / "checks").write_text(flip.read_text())
        flip.unlink()
        saved = base / "pr-checks.json"
        if saved.exists():
            saved.unlink()
elif args[:2] == ["pr", "view"] and "url" in args:
    if not (base / "pr-url").exists():
        sys.exit(1)
    print((base / "pr-url").read_text())
elif args[:2] == ["pr", "view"]:
    print((base / "pr-state").read_text() if (base / "pr-state").exists() else "OPEN")
    nxt = base / "pr-state-after"
    if nxt.exists():
        (base / "pr-state").write_text(nxt.read_text())
        nxt.unlink()
""")
        fake.chmod(0o755)
        return mock.patch.dict(os.environ, {"LAND_GH": str(fake)})

    def arm_auto_merge(self):
        (self.base / "auto-merge-request.json").write_text(
            '{"authorEmail":null,"commitBody":null,"commitHeadline":null,'
            '"mergeMethod":"SQUASH","enabledAt":"2026-10-05T02:54:42Z",'
            '"enabledBy":{"login":"octocat","id":"U_1","name":"Octo Cat"}}'
        )

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
            self.assertTrue(self.ref_exists("refs/remotes/origin/landing/q1", self.work))
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/q1", self.work))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_merges_its_own_pr_when_there_are_no_checks_to_wait_for(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", self.land("land"))
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             ["pr merge https://github.com/o/r/pull/9 --auto --merge", "pr merge https://github.com/o/r/pull/9 --merge"])
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/q1", self.work))
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

    def test_merge_mode_waits_for_running_checks_then_merges(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: Q1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "Q1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("pr merge https://github.com/o/r/pull/9 --squash", calls)
            self.assertEqual(self.land("land"), "nothing to land")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/q1", self.work))

    def test_merge_mode_bounces_a_pr_whose_required_checks_failed(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))

    def test_merge_mode_does_not_merge_when_auto_would_succeed_with_a_pending_check(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "auto-merges-immediately").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: Q1 (r/D1) https://github.com/o/r/pull/9")
            self.assertIn("waiting for required checks", self.land("status", "Q1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))

    def test_merge_mode_bounces_when_a_check_fails_after_auto_merge_was_enabled(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "opened PRs that merge when their checks pass: Q1 (r/D1) https://github.com/o/r/pull/9")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "Q1"))
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))

    def test_merge_mode_does_not_merge_when_the_check_query_fails(self):
        with self.fake_gh():
            (self.base / "checks").write_text("pending")
            (self.base / "checks-query-fails").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("could not read checks", out)
            self.assertIn("queue paused", out)
            self.assertNotIn("landed Q1", out)
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse((self.base / "merge-calls").exists())

    def absent_marker(self):
        return land.Store.for_repo(self.work).contract.get("absentDrain") or {}

    def test_merge_mode_does_not_merge_a_just_opened_pr_with_zero_posted_checks(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertIn("waiting for required checks", self.land("status", "Q1"))
            self.assertNotIn("Paused", self.land("status"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(),
                             ["pr merge https://github.com/o/r/pull/9 --auto --merge",
                              "pr merge https://github.com/o/r/pull/9 --merge"])
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lands_a_merged_pr_when_a_later_check_fails(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "Q1"))
            candidate = sh("git", "rev-parse", "landing/q1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            (self.base / "checks").write_text("failed")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertIn("Q1 landed", self.land("status", "Q1"))

    def test_merge_mode_pauses_on_a_merge_conflict_when_auto_merge_is_disabled(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "auto-merge-disabled").write_text("")
            (self.base / "plain-merge-fails").write_text("merge conflict")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            paused = self.land("land")
            self.assertIn("queue paused", paused)
            self.assertIn("merge conflict", paused)
            self.assertNotIn("landed", paused)
            self.assertIn("Paused", self.land("status"))
            self.assertIn("merge conflict", self.land("status"))
            calls = [
                "pr merge https://github.com/o/r/pull/9 --auto --merge",
                "pr merge https://github.com/o/r/pull/9 --merge",
            ]
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(), calls)
            self.assertIn("queue paused", self.land("land"))
            self.assertEqual((self.base / "merge-calls").read_text().splitlines(), calls)

    def test_merge_mode_waits_when_auto_merge_is_disabled_and_policy_refuses(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "auto-merge-disabled").write_text("")
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            still = self.land("land")
            self.assertNotIn("queue paused", still)
            self.assertNotIn("landed", still)
            self.assertIn("waiting for required checks", self.land("status", "Q1"))
            self.assertNotIn("Paused", self.land("status"))
            calls = (self.base / "merge-calls").read_text()
            self.assertIn("pr merge https://github.com/o/r/pull/9 --auto --merge", calls)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --merge", calls)
            (self.base / "pr-checks.json").unlink()
            (self.base / "auto-merge-disabled").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")

    def test_merge_mode_waits_when_base_branch_policy_refuses_and_no_check_is_posted(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "policy-until-passed").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertFalse((self.base / "auto-merge-disabled").exists())
            still_empty = self.land("land")
            self.assertNotIn("queue paused", still_empty)
            self.assertNotIn("landed", still_empty)
            self.assertIn("waiting for required checks", self.land("status", "Q1"))
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")

    def test_merge_mode_does_not_merge_when_a_pending_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("pending")
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertNotIn("queue paused", opened)
            self.assertFalse((self.base / "merge-calls").exists())
            self.assertIn("waiting for required checks", self.land("status", "Q1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_does_not_merge_when_a_failed_check_appears_between_reads(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"APPROVED","statusCheckRollup":[]}')
            (self.base / "checks-flip").write_text("failed")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertNotIn("landed", opened)
            self.assertEqual((self.base / "merge-calls").read_text().strip(),
                             "pr merge https://github.com/o/r/pull/9 --disable-auto")
            self.assertIn("bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", opened)
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_drops_the_absent_marker_when_the_entry_lands(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", opened)
            self.assertEqual(self.absent_marker(), {"1": 1})
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertNotIn("1", self.absent_marker())

    def test_merge_mode_drops_the_absent_marker_when_the_entry_bounces(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.absent_marker(), {"1": 1})
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("failed")
            self.assertIn("bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", self.land("land"))
            self.assertNotIn("1", self.absent_marker())
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_a_checked_out_queue_branch_is_named_when_land_cannot_delete_it(self):
        with self.fake_gh():
            self.init(mode="human")
            self.queue_one()
            self.land("land")
            candidate = sh("git", "rev-parse", "landing/q1", cwd=self.base / "origin.git")
            sh("git", "branch", "landing/q1", candidate, cwd=self.work)
            sh("git", "checkout", "-q", "landing/q1", cwd=self.work)
            (self.base / "pr-state").write_text(f"MERGED {candidate} abc123")
            out = self.land("land")
            self.assertIn("landed Q1 (r/D1)", out)
            self.assertIn("left local landing/q1", out)
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.work))
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertIn("Q1 landed", self.land("status", "Q1"))

    def test_merge_mode_lands_when_the_remote_deletes_the_queue_branch_during_push(self):
        with self.fake_gh():
            self.install_racing_branch_delete()
            self.init(mode="merge")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", opened)
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertIn("refs/heads/landing/q1", (self.base / "race-deleted").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/q1", self.work))
            self.assertIn("branch-status 404", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_the_queue_branch_delete_fails(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            sh("git", "config", "receive.denyDeletes", "true", cwd=self.base / "origin.git")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertIn("merge requested by the queue", self.land("status", "Q1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())
            sh("git", "config", "--unset", "receive.denyDeletes", cwd=self.base / "origin.git")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_the_entry_when_a_hidden_ref_refuses_deletion(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "receive.denyDeletes", "true", cwd=remote)
            sh("git", "config", "uploadpack.hideRefs", "refs/heads/landing/q1", cwd=remote)
            listed = subprocess.run(["git", "ls-remote", "origin", "refs/heads/landing/q1"],
                                    cwd=self.work, capture_output=True, text=True)
            self.assertEqual((listed.returncode, listed.stdout.strip()), (0, ""))
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_a_hidden_ref_is_locked(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "uploadpack.hideRefs", "refs/heads/landing/q1", cwd=remote)
            (remote / "refs/heads/landing/q1.lock").write_text("x")
            listed = subprocess.run(["git", "ls-remote", "origin", "refs/heads/landing/q1"],
                                    cwd=self.work, capture_output=True, text=True)
            self.assertEqual((listed.returncode, listed.stdout.strip()), (0, ""))
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_receive_pack_hides_the_queue_branch(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            sh("git", "config", "receive.hideRefs", "refs/heads/landing/q1", cwd=remote)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertIn("branch-status 200", (self.base / "gh-calls").read_text())

    def test_an_annotated_tag_stays_local_when_the_queue_pushes(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            log = self.record_pushes()
            try:
                self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", self.land("land"))
                self.merged_queue_branch()
                self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            finally:
                self.stop_recording_pushes()
            remote = self.base / "origin.git"
            self.assert_pushes_stay_on_their_refs(log)
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertNotIn("branch-status", (self.base / "gh-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_push_mode_keeps_an_annotated_tag_local(self):
        self.init(mode="push")
        self.queue_one()
        sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
        sh("git", "config", "push.followTags", "true", cwd=self.work)
        log = self.record_pushes()
        try:
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
        finally:
            self.stop_recording_pushes()
        self.assert_pushes_stay_on_their_refs(log)
        self.assertFalse(self.ref_exists("refs/tags/private-local-tag", self.base / "origin.git"))

    def test_merge_mode_settles_when_the_forge_reports_the_branch_gone(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/q1", cwd=remote)
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            log = self.record_pushes()
            try:
                self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            finally:
                self.stop_recording_pushes()
            self.assertEqual(
                log.read_text().splitlines(),
                ["push --no-follow-tags origin --delete landing/q1"])
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertIn("branch-status 404", (self.base / "gh-calls").read_text())
            self.assertIn(
                "api --include repos/{owner}/{repo}/branches/landing/q1",
                (self.base / "gh-calls").read_text())
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_leaves_the_entry_when_the_branch_query_fails(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/q1", cwd=remote)
            (self.base / "branch-query-fails").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("branch-status 500", (self.base / "gh-calls").read_text())
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", remote))

    def test_merge_mode_leaves_the_entry_when_another_push_url_dropped_the_branch(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            backup = self.base / "backup.git"
            ref = "refs/heads/landing/q1"
            sha = self.merged_queue_branch()
            sh("git", "checkout", "--detach", "-q", sha, cwd=self.work)
            sh("git", "clone", "--bare", "-q", str(remote), str(backup), cwd=self.base)
            sh("git", "update-ref", "-d", ref, cwd=backup)
            (remote / "refs/heads/landing/q1.lock").write_text("x")
            sh("git", "config", "--add", "remote.origin.pushurl", str(remote), cwd=self.work)
            sh("git", "config", "--add", "remote.origin.pushurl", str(backup), cwd=self.work)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists(ref, remote))
            self.assertFalse(self.ref_exists(ref, backup))
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))

    def test_merge_mode_leaves_the_entry_when_the_other_push_url_keeps_the_branch(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            origin = self.base / "origin.git"
            backup = self.base / "backup.git"
            ref = "refs/heads/landing/q1"
            sha = self.merged_queue_branch()
            sh("git", "checkout", "--detach", "-q", sha, cwd=self.work)
            sh("git", "clone", "--bare", "-q", str(origin), str(backup), cwd=self.base)
            sh("git", "update-ref", "-d", ref, cwd=origin)
            lock = backup / "refs/heads/landing/q1.lock"
            lock.parent.mkdir(parents=True, exist_ok=True)
            lock.write_text("x")
            sh("git", "config", "--add", "remote.origin.pushurl", str(origin), cwd=self.work)
            sh("git", "config", "--add", "remote.origin.pushurl", str(backup), cwd=self.work)
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertFalse(self.ref_exists(ref, origin))
            self.assertTrue(self.ref_exists(ref, backup))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))

    def test_merge_mode_leaves_the_entry_when_the_push_url_names_another_repo(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "remote", "set-url", "origin", "https://github.com/else/where.git", cwd=self.work)
            sh("git", "config", "receive.denyDeletes", "true", cwd=remote)
            (self.base / "forge-says-missing").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())

    def test_merge_mode_leaves_the_entry_when_the_forge_repo_cannot_be_read(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/q1", cwd=remote)
            (self.base / "repo-view-fails").write_text("x")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", remote))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))
            self.assertIn("awaiting-merge", self.land("status", "Q1"))

    def test_push_mode_settles_a_missing_branch_only_from_gits_absent_line(self):
        with self.fake_gh():
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            store = land.Store.for_repo(self.work)
            with store.tx() as db:
                db.execute("INSERT OR REPLACE INTO contract VALUES ('mode', ?)", (json.dumps("push"),))
            store = land.Store.for_repo(self.work)
            self.assertEqual(store.contract["mode"], "push")
            remote = self.base / "origin.git"
            self.merged_queue_branch()
            sh("git", "update-ref", "-d", "refs/heads/landing/q1", cwd=remote)
            sh("git", "tag", "-a", "private-local-tag", "-m", "local tag", cwd=self.work)
            sh("git", "config", "push.followTags", "true", cwd=self.work)
            (self.base / "branch-query-fails").write_text("x")
            entry = store.entries("awaiting-merge")[0]
            log = self.record_pushes()
            try:
                landed, bounced = [], []
                self.assertTrue(land.take_pr(store, entry, landed, bounced))
            finally:
                self.stop_recording_pushes()
            self.assertEqual(landed, [entry["id"]])
            self.assertEqual(bounced, [])
            self.assertEqual(
                log.read_text().splitlines(),
                ["push --no-follow-tags origin --delete landing/q1"])
            self.assertNotIn("branches/", (self.base / "gh-calls").read_text())
            self.assertFalse(self.ref_exists("refs/tags/private-local-tag", remote))
            self.assertEqual(self.land("status", "Q1").splitlines()[0].split(". ")[0], "Q1 landed (r/D1, w1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")

    def test_merge_mode_lands_in_the_run_whose_plain_merge_succeeds(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            (self.base / "merge-state-lags").write_text("")
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            opened = self.land("land")
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", opened)
            self.assertNotIn("landed", opened)
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            self.assertFalse(self.ref_exists("refs/remotes/origin/landing/q1", self.work))
            self.assertIn("pr merge https://github.com/o/r/pull/9 --squash", (self.base / "merge-calls").read_text())

    def test_merge_mode_lands_in_the_run_whose_auto_merge_completes(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text('{"reviewDecision":"","statusCheckRollup":[]}')
            self.init(mode="merge", merge_method="squash")
            self.queue_one()
            self.land("land")
            (self.base / "auto-merges-immediately").write_text("")
            (self.base / "merge-state-lags").write_text("")
            (self.base / "pr-checks.json").unlink()
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))
            calls = (self.base / "merge-calls").read_text().splitlines()
            self.assertEqual(calls[-1], "pr merge https://github.com/o/r/pull/9 --auto --squash")

    def test_merge_mode_reports_a_merge_that_lands_after_the_opening_state_read(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            self.assertIn("opened PRs that merge when their checks pass: Q1 (r/D1)", self.land("land"))
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "Q1"))
            candidate = sh("git", "rev-parse", "landing/q1", cwd=self.base / "origin.git")
            (self.base / "pr-state").write_text(f"OPEN {candidate} ")
            (self.base / "pr-state-after").write_text(f"MERGED {candidate} abc123")
            (self.base / "checks").write_text("passed")
            self.assertEqual(self.land("land"), "landed Q1 (r/D1)")
            self.assertEqual(self.land("lease", "list"), "no leases held")
            self.assertFalse(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))

    def test_merge_mode_pauses_for_review_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"REVIEW_REQUIRED","statusCheckRollup":'
                '[{"name":"test (3.12)","status":"COMPLETED","conclusion":"SUCCESS"}]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("approving review", out)
            self.assertNotIn("landed", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())
            self.assertIn("awaiting-merge", self.land("status", "Q1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 submitted"))

    def test_merge_mode_pauses_when_changes_are_requested_without_calling_auto_merge(self):
        with self.fake_gh():
            (self.base / "pr-checks.json").write_text(
                '{"reviewDecision":"CHANGES_REQUESTED","statusCheckRollup":[]}')
            (self.base / "required-checks").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused", out)
            self.assertIn("https://github.com/o/r/pull/9", out)
            self.assertIn("changes requested", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

    def test_merge_mode_disables_auto_merge_when_it_bounces_an_open_pr(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.assertEqual(self.land("land"), "nothing to land")
            self.assertIn("merge requested by the queue", self.land("status", "Q1"))
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", out)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr comment", (self.base / "gh-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 active r/D1"))
            self.assertTrue(self.ref_exists("refs/heads/landing/q1", self.base / "origin.git"))

    def test_merge_mode_reports_a_failure_to_disable_auto_merge_on_bounce(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            (self.base / "disable-auto-fails").write_text("API unavailable")
            self.arm_auto_merge()
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.land("land")
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", out)
            self.assertIn("auto-merge still enabled: API unavailable", out)
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_skips_disable_auto_when_auto_merge_request_is_null(self):
        with self.fake_gh():
            (self.base / "checks").write_text("failed")
            (self.base / "auto-merge-request.json").write_text("null")
            self.init(mode="merge")
            self.queue_one()
            self.assertEqual(self.land("land"), "bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)")
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--disable-auto", calls)
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())
            self.assertNotIn("auto-merge still enabled", self.land("status", "Q1"))
            self.assertTrue(self.land("lease", "list").startswith("L1 active"))

    def test_merge_mode_reports_disable_auto_failure_when_auto_merge_request_is_set(self):
        with self.fake_gh():
            (self.base / "required-checks").write_text("")
            self.arm_auto_merge()
            (self.base / "disable-auto-fails").write_text("GraphQL: Auto merge is not enabled for this pull request")
            self.init(mode="merge")
            self.queue_one()
            self.land("land")
            self.land("land")
            (self.base / "checks").write_text("failed")
            out = self.land("land")
            self.assertIn("bounced Q1 (r/D1): required checks failed on https://github.com/o/r/pull/9: test (3.12)", out)
            self.assertIn("auto-merge still enabled: GraphQL: Auto merge is not enabled for this pull request", out)
            self.assertIn("pr merge https://github.com/o/r/pull/9 --disable-auto", (self.base / "merge-calls").read_text())
            self.assertNotIn("pr close", (self.base / "gh-calls").read_text())

    def test_merge_mode_pauses_when_github_requires_a_human_approval(self):
        with self.fake_gh():
            (self.base / "merge-refused").write_text("")
            self.init(mode="merge")
            self.queue_one()
            out = self.land("land")
            self.assertIn("queue paused: https://github.com/o/r/pull/9 needs an approving review", out)
            calls = (self.base / "merge-calls").read_text() if (self.base / "merge-calls").exists() else ""
            self.assertNotIn("--auto", calls.split())

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
        # land.py's slot gate reads LAND_SLOT. The queue sets it around this suite, so the
        # subprocesses under test must not inherit it. The nested command still sets it itself.
        env = {key: value for key, value in os.environ.items() if key != "LAND_SLOT"}
        worker = subprocess.Popen([sys.executable, str(SCRIPT), "slot", "--", "sh", "-c", f"echo worker-start >> {marks}; sleep 2; echo worker-end >> {marks}"],
                                  env=env)
        time.sleep(0.5)
        bench = subprocess.run([sys.executable, str(SCRIPT), "slot", "--exclusive", "--", "sh", "-c", f"echo bench >> {marks}"],
                               capture_output=True, text=True, timeout=30, env=env)
        worker.wait(timeout=30)
        self.assertEqual(bench.returncode, 0, bench.stderr)
        self.assertEqual(marks.read_text().split(), ["worker-start", "worker-end", "bench"])
        nested = subprocess.run([sys.executable, str(SCRIPT), "slot", "--", sys.executable, str(SCRIPT), "slot", "--exclusive", "--", "true"],
                                capture_output=True, text=True, timeout=30, env=env)
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
