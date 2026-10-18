from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, _WakeRace, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone

class _Helpers:
    def _remote_trunk(self):
        from contextlib import closing
        with closing(self.landing_db()) as db, db:
            db.execute("UPDATE contract SET value = ? WHERE key = 'mode'", (json.dumps("merge"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'trunk'", (json.dumps("main"),))
            db.execute("UPDATE contract SET value = ? WHERE key = 'remote'", (json.dumps("origin"),))
        subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/repo.git"],
                       cwd=self.project, capture_output=True)
        subprocess.run(["git", "remote", "set-url", "origin", "https://github.com/example/repo.git"],
                       cwd=self.project, capture_output=True)
    def _gh_dir(self, payload=None, fail=False, with_gh=True):
        from pathlib import Path as _Path
        import shutil as _shutil
        import stat as _stat
        bindir = _Path(self.temporary.name) / f"fakebin-{time.time_ns()}"
        bindir.mkdir()
        git = _shutil.which("git")
        if git:
            (bindir / "git").symlink_to(git)
        if with_gh:
            script = bindir / "gh"
            if fail:
                script.write_text("#!/bin/sh\necho fake gh failed >&2\nexit 1\n")
            else:
                script.write_text("#!/bin/sh\ncat <<'EOF'\n" + (payload or "[]") + "\nEOF\n")
            script.chmod(0o755)
        return bindir
    def _gh_dir_recording(self, payload, args_file):
        from pathlib import Path as _Path
        import shutil as _shutil
        bindir = _Path(self.temporary.name) / f"fakebin-{time.time_ns()}"
        bindir.mkdir()
        git = _shutil.which("git")
        if git:
            (bindir / "git").symlink_to(git)
        script = bindir / "gh"
        payload_path = bindir / "payload.json"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        payload_path.write_text(text)
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, json\n"
            "args = sys.argv[1:]\n"
            f"open({str(args_file)!r}, 'w').write(' '.join(args))\n"
            "limit = 100\n"
            "if '--limit' in args:\n"
            "    try:\n"
            "        limit = int(args[args.index('--limit') + 1])\n"
            "    except Exception:\n"
            "        pass\n"
            f"rows = json.loads(open({str(payload_path)!r}).read())\n"
            "print(json.dumps(rows[:limit]))\n"
        )
        script.chmod(0o755)
        return bindir
    def _valid_red_payload(self, sha="aaa111", workflow="CI", url="https://example.com/run/2",
                            since="2026-10-02T00:00:00Z"):
        return json.dumps([{"status": "completed", "conclusion": "failure", "headSha": sha,
                            "createdAt": since, "url": url, "workflowName": workflow}])
    def _assert_no_red_cues(self):
        liveness = self.brigade("next", "liveness")
        self.assertNotIn("Read the failing jobs", liveness)
        self.assertNotIn("urgent fix", liveness)
        self.assertNotIn("red-trunk", liveness)
        merge = self.brigade("next", "merge", "D1")
        self.assertNotIn("Read the failing jobs", merge)
        self.assertNotIn("urgent fix", merge)
        self.assertNotIn("red-trunk", merge)
