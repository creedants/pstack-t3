import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .names import DOC_SETTINGS, ROOT, SCRIPT, _owner_words
from .inprocess import call


class SheetFlows:
    def next_sheet(self, *args):
        text = self.brigade("next", *args)
        lines = text.splitlines()
        self.assertEqual([line for line in lines if line.startswith("reply:")], [lines[-1]])
        self.assertFalse(any(line.startswith("fire") for line in lines))
        for line in lines:
            if line.startswith("run "):
                self.assertNotIn(shlex.split(line[4:])[0], ("fire", "watch", "startable", "land.py"))
        return text


    def fresh(self):
        if self.at.exists():
            shutil.rmtree(self.at)
        self.fired_bug_fix()


    def conditional_runs(self, sheet):
        runs = []
        for line in sheet.splitlines():
            if line.startswith("if ") and ": run " in line:
                runs.append(shlex.split(line.split(": run ", 1)[1]))
        return runs


    def run_printed(self, directory, words):
        if words[0] == "land.py":
            result = subprocess.run(
                [sys.executable, str(ROOT / "t3/added/landing/scripts/land.py"), "--repo", str(self.project), *words[1:]],
                capture_output=True, text=True, env=self.land_env())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return
        self.run_at(directory, *words)


    def execute_commands(self, commands):
        self.assertTrue(commands)
        ran = []
        for words in commands:
            self.assertNotIn(words[0], ("fire", "watch", "startable"))
            copy = Path(self.temporary.name) / f"alone-{time.time_ns()}"
            shutil.copytree(self.at, copy)
            self.run_printed(copy, words)
            ran.append(" ".join(words))
        ordered = Path(self.temporary.name) / f"ordered-{time.time_ns()}"
        shutil.copytree(self.at, ordered)
        for words in commands:
            self.run_printed(ordered, words)
        self.assertEqual(ran, [" ".join(words) for words in commands])
        return ran


    def execute_sheet(self, sheet):
        commands = []
        for line in sheet.splitlines():
            if line.startswith("run "):
                words = shlex.split(line[4:])
                self.assertNotIn(words[0], ("fire", "watch", "startable", "land.py"))
                commands.append(words)
        self.execute_commands(commands)
        conditionals = self.conditional_runs(sheet)
        if conditionals:
            self.execute_commands(conditionals)


    def execute_conditionals(self, sheet):
        return self.execute_commands(self.conditional_runs(sheet))


    def assert_one_close_file(self, sheet):
        self.assertEqual([line for line in sheet.splitlines() if line == "run close --to-file"], ["run close --to-file"])


    SHARED_DOCS = ("README.md", "docs/guide.md", "t3/runtime.md", "t3/setup.md", "t3/added/brigade/SKILL.md",
                   "t3/overrides/brigade/SKILL.md", "skills/brigade/SKILL.md")


    def doc_tail(self, count):
        return ("drop it from --paths and give the sentences as follow-ups, or pass --doc-item" if count == 1
                else "drop them from --paths and give the sentences as follow-ups, or pass --doc-item")


    def doc_line(self, docs, timebox=None, count=None, minutes=90):
        docs = tuple(sorted(docs))
        if len(docs) == 1:
            head = f"{docs[0]} is a shared document"
        elif len(docs) == 2:
            head = f"{docs[0]} and {docs[1]} are shared documents"
        else:
            head = f"{', '.join(docs[:-1])}, and {docs[-1]} are shared documents"
        reasons = []
        if timebox is not None:
            reasons.append(f"--timebox is {timebox}, over {minutes} minutes")
        if count is not None:
            reasons.append(f"--tickets names {count} tickets")
        return f"brigade: {head} and {', and '.join(reasons)}; {self.doc_tail(len(docs))}"


    def seed_doc_settings(self, directory=None, **overrides):
        """Write DOC_SETTINGS into this store's restaurant.json, plus any overrides.

        A None override removes that key, so a test can exercise a default.
        """
        path = (self.at if directory is None else directory) / "restaurant.json"
        meta = json.loads(path.read_text())
        meta.update(DOC_SETTINGS)
        for key, value in overrides.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        path.write_text(json.dumps(meta, indent=2) + "\n")


    def doc_log_note(self, directory, dish):
        for line in (directory / "log.tsv").read_text().splitlines()[1:]:
            kind, ident, state, note = line.split("\t")[1:5]
            if kind == "doc" and ident == dish and state == "item":
                return note
        self.fail(f"no doc item row for {dish}")
