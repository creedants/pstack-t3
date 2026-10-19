import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .names import CLAUDE, CODEX, SCRIPT, _owner_words
from .inprocess import call


class EditsFlows:
    @staticmethod
    def edit_block(number, path, old, new, fence="```"):
        return f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n{fence}\n{old}\n{fence}\n\nNew text:\n\n{fence}text\n{new}\n{fence}\n"


    def edits_report(self, *blocks, name="D1-review-1.md", after="## Edits verified\n\nApplied in a scratch copy.\n"):
        body = "Verdict: pass with edits\n\n## Blockers\n\nNone.\n\n## Edits\n\n" + "\n".join(blocks)
        return self.review_file(name, body + ("\n" + after if after else ""))


    def record_edits(self, sha, *extra, ok=True):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass-with-edits",
                            "--author", CODEX, "--verifier", CLAUDE, *extra, ok=ok)


    SOURCE = "helo wrold\nsecond line\n"


    FIXED = "hello world\nsecond line\n"


    BUILD = ('import pathlib, sys\ntext = pathlib.Path("src/greeting.txt").read_text()\nif "unbuildable" in text:\n'
             '    print("build refuses unbuildable")\n    sys.exit(2)\n'
             'pathlib.Path("out").mkdir(exist_ok=True)\npathlib.Path("out/greeting.txt").write_text(text.upper())\n')


    CHECK = ('import pathlib, sys\nif "forbidden" in pathlib.Path("src/greeting.txt").read_text():\n'
             '    print("check saw forbidden")\n    sys.exit(3)\n')


    BUILD_COMMAND = f"{shlex.quote(sys.executable)} tools/build.py"


    CHECK_COMMAND = f"{shlex.quote(sys.executable)} tools/check.py"


    NEEDS_COMMANDS = ("brigade: edits verify needs a build command and a check command; "
                      "run set --build-command <command> --check-command <command>, with none for one the project lacks")


    def commit_files(self, files):
        git = lambda *command: subprocess.run(("git", *command), cwd=self.project, capture_output=True, text=True,
                                              check=True).stdout.strip()
        for name, text in files.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
        return git("rev-parse", "HEAD")


    def head_commit(self, source, out=None, **files):
        return self.commit_files({"src/greeting.txt": source, "out/greeting.txt": source.upper() if out is None else out,
                                  **files})


    def edits_store(self, build=BUILD_COMMAND, check=CHECK_COMMAND, **files):
        """D1 in a store whose project is a git repository with a source file, a build script, and a check script.
        Returns the reviewed commit."""
        self.git_project()
        reviewed = self.commit_files({"src/greeting.txt": self.SOURCE, "out/greeting.txt": self.SOURCE.upper(),
                                      "tools/build.py": self.BUILD, "tools/check.py": self.CHECK, **files})
        self.fired_bug_fix()
        if build is not None:
            self.brigade("set", "--build-command", build, "--check-command", check)
        return reviewed


    def two_edits(self, **kwargs):
        return self.edits_report(self.edit_block("1", "src/greeting.txt", "helo", "hello"),
                                 self.edit_block("2", "src/greeting.txt", "wrold", "world"), **kwargs)


    def verify(self, reviewed, head):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), "edits", "verify", "D1", "--from", reviewed, "--to", head])
        return outcome.code, outcome.stdout_text().strip(), outcome.stderr_text().strip()


    def verified(self, reviewed, head):
        return f"D1: edits verified, {reviewed} to {head}"


    def not_verified(self, reviewed, head):
        return f"brigade: D1: edits not verified, {reviewed} to {head}"


    def edits_rows(self):
        return [line.split("\t")[1:] for line in (self.at / "edits.tsv").read_text().splitlines()[1:]]


    def record_pass_at(self, sha, *extra, ok=True):
        return self.brigade("pass", "record", "D1", "--sha", sha, "--verdict", "pass", "--author", CODEX,
                            "--verifier", CLAUDE, *extra, ok=ok)


    def gate(self, reviewed, sha):
        return (f"brigade: D1's last verdict is pass-with-edits at {reviewed}; run edits verify D1 --from {reviewed} "
                f"--to {sha}, or name a second review's report with --report")


    QUOTED_APPLIED = "hello wrold\nsecond line\n"


    SECTION_APPLIED = "helo world\nsecond line\n"


    def quoting_report(self, opening, closing):
        """A report whose Notes section quotes an Edits section with one edit inside one fence, above an Edits section with another edit."""
        quoted = "## Edits\n" + self.edit_block("8", "src/greeting.txt", "helo", "hello")
        return self.review_file("D1-review-1.md", f"Verdict: pass with edits\n\n## Notes\n\n{opening}\n{quoted}{closing}\n\n## Edits\n"
                                + self.edit_block("1", "src/greeting.txt", "wrold", "world"))


    def assert_the_quoted_edit_is_not_read(self, opening, closing):
        reviewed = self.edits_store()
        self.quoting_report(opening, closing)
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.QUOTED_APPLIED)
        self.assertEqual(self.verify(reviewed, head),
                         (1, "FAIL out/greeting.txt: differs from what the build writes\n"
                             f"FAIL src/greeting.txt: differs from {reviewed} with the edits applied",
                          self.not_verified(reviewed, head)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
        applied = self.head_commit(self.SECTION_APPLIED)
        self.assertEqual(self.verify(reviewed, applied),
                         (0, f"built out/greeting.txt\nedited src/greeting.txt\nok check\n{self.verified(reviewed, applied)}", ""))


    NESTED_FENCE = ("has three backticks or tildes after nothing but indentation, block quote marks, and list marks; "
                    "start each fence at the first column")


    PARTS_REFUSAL = ("brigade: reports/D1-review-1.md: Edit {} must hold, once each and in this order, a File line, "
                     "an Old text line, a fenced block, a New text line, and a fenced block")


    def section(self):
        return "## Edits\n\n" + self.edit_block("1", "src/greeting.txt", "wrold", "world")


    def refused_edits(self, body):
        """What pass record prints when it refuses a pass-with-edits round on a report with this body."""
        path = self.review_file("D1-review-1.md")
        path.write_bytes(body if isinstance(body, bytes) else body.encode())
        refusal = self.record_edits("abc", "--edits", "D1-review-1.md", ok=False)
        self.assertEqual(self.pass_rows(), [])
        return refusal


    BELOW = (" is a File, Old text, or New text line of Edit 1; the line above it must be empty or only spaces and tabs, "
             "the edit's heading, the closing line of a fence, or another of those three lines")


    APART = (" opens a fenced block of Edit 1; every line between an Old text or New text line and the fenced block below "
             "it must be empty or only spaces and tabs")


    MALFORMED = (": line 7 of Edit 1 starts, once its indentation, block quote marks, list marks, and the whitespace at its "
                 "two ends are removed, with File: and a backtick; what follows must be a path that holds no backtick and "
                 "does not start with a space, then a backtick that no backtick follows directly")


    AROUND = (("> example", None), ("- example", None), ('[r]: /url "example', 'end"'), ("Example ``", "``"),
              ("[r]: /url 'example", "end'"), ("[r]: /url (example", "end)"), ("> > example", None), ("- - example", None),
              ("> - example", None), ("+ example", None), ("* example", None), ("1. example", None), ("2) example", None),
              ("> example  ", None), ("> example\\", None), ("- example  ", None), ("example", None),
              ("Example <span", 'title="x">'), ("Example [link", "](/u)"), ("Example *stressed", "words*"), ("[r]: /url", None),
              ("\u00a0", None), ("\u0661. example", None))


    def one_edit_report(self):
        return "Verdict: pass with edits\n\n" + self.section()


    def run_raw(self, *args):
        outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                       *_owner_words(self.at), *(str(arg) for arg in args)])
        return outcome.code, outcome.stdout_text(), outcome.stderr_text()


    def assert_edits_verify_and_pass_record_refuse(self, cases):
        """Each case is (body, refusal), and the refusal finishes the words `brigade: reports/D1-review-1.md`.

        A pass-with-edits round is on record for one_edit_report, and the head is that edit and the rebuilt output.
        With each body as the report, edits verify for that round and pass record for a new round each print that one
        line and nothing else, exit 1, and change no store file. A pass at the head is refused after the last case.
        """
        reviewed = self.edits_store()
        report = self.review_file("D1-review-1.md", self.one_edit_report())
        self.record_edits(reviewed, "--edits", "D1-review-1.md")
        head = self.head_commit(self.SECTION_APPLIED)
        self.brigade("dish", "D1", "--state", "in-progress")
        for body, refusal in cases:
            with self.subTest(body=body):
                report.write_bytes(body.encode())
                before, line = self.store_files(), f"brigade: reports/D1-review-1.md{refusal}\n"
                self.assertEqual(self.run_raw("edits", "verify", "D1", "--from", reviewed, "--to", head), (1, "", line))
                self.assertEqual(self.run_raw("pass", "record", "D1", "--sha", head, "--verdict", "pass-with-edits",
                                              "--author", CODEX, "--verifier", CLAUDE, "--edits", "D1-review-1.md"), (1, "", line))
                self.assertEqual(self.store_files(), before)
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
