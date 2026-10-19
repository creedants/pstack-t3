from ..support import REVIEWER, sh


class IdentityBase:

    def init(self, mode="auto", batch=1, author_policy="credited", ok=True, **extra):
        """The helper defaults the policy to credited for tests that name none. None omits the flag."""
        args = ["init", "--trunk", extra.get("trunk", "main"), "--mode", mode, "--check", "./check.sh", "--batch", str(batch)]
        if "merge_method" in extra:
            args += ["--merge-method", extra["merge_method"]]
        if "base" in extra:
            args += ["--base", extra["base"]]
        if "generated" in extra:
            args += ["--generated", extra["generated"]]
        if "rebuild" in extra:
            args += ["--rebuild", extra["rebuild"]]
        if author_policy is not None:
            args += ["--author-policy", author_policy]
        return self.land(*args, ok=ok)

    def stored_author_policy(self):
        """The JSON text under the contract key authorPolicy, or None when the key is absent."""
        with self.raw_db() as db:
            row = db.execute("SELECT value FROM contract WHERE key = 'authorPolicy'").fetchone()
        return row and row[0]

    def queue_one_by(self, author, *message, author_policy="credited"):
        """Init push mode with this policy, then queue one commit by this author. Returns the reviewed commit id."""
        self.init(mode="push", author_policy=author_policy)
        self.configure_owner()
        self.worker("w1", {"a.txt": "agent\n"})
        sh("git", "commit", "-q", "--amend", "--author", author, *message, cwd=self.base / "w1")
        sha = sh("git", "rev-parse", "HEAD", cwd=self.base / "w1")
        self.land("lease", "claim", "--holder", "r/D1", "--paths", "a.txt")
        self.land("submit", "--holder", "r/D1", "--branch", "w1", "--sha", sha, "--lease", "L1", "--reviewer", REVIEWER)
        return sha
