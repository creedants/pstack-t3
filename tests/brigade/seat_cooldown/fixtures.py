from pathlib import Path

from ..support import CLAUDE, CODEX, ROOT, SCRIPT, _owner_words, call_in_process, json, os, run_globals


NOW = "2026-02-01T00:00:00+00:00"

FAILED = CLAUDE

BACKUP = CODEX

SPARE = "grok/grok-4.7"

PARENT = CLAUDE

LIMIT_CLOCK = "You've hit your usage limit. Your limit will reset at 12:30 UTC."

LIMIT_SPAN = "You've hit your usage limit. Try again in 30 minutes."

NOT_A_LIMIT = "the provider returned a malformed response"


class _CooldownHelpers:
    def freeze(self, moment=NOW):
        """Pin the loaded command's clock in this process, so every cooldown stamp and window is exact."""
        self.moment = moment
        run_globals(self, SCRIPT)["now"] = lambda: self.moment
        return moment

    def outcome_in_process(self, *args, stdin=None):
        """The captured streams of one command run in this process, so the clock `freeze` pinned is the one the command reads."""
        argv = ["--store", str(self.store), "--at", str(self.at), *_owner_words(self.at), *args]
        return call_in_process(self, SCRIPT, [str(arg) for arg in argv], stdin=stdin)

    def command_in_process(self, *args, ok=True, stdin=None):
        """Run one command in this process, so the clock `freeze` pinned is the one the command reads."""
        outcome = self.outcome_in_process(*args, stdin=stdin)
        self.assertEqual(outcome.code == 0, ok, outcome.stdout_text() + outcome.stderr_text())
        return (outcome.stdout_text() if ok else outcome.stderr_text()).strip()

    def cooldown_store(self, moment=NOW, workers=None):
        """A store whose D1 is in progress, with a roles file whose backups are BACKUP then SPARE."""
        self.freeze(moment)
        extra = () if workers is None else ("--workers", str(workers))
        self.brigade("open", "--project-root", str(self.project), "--name", "Perf", *extra)
        self.fire_one()
        self.write_roles(BACKUP, SPARE)

    def named(self, seat, options=None):
        provider, _, model = seat.partition("/")
        target = {"providerInstanceId": provider, "model": model}
        if options is not None:
            target["options"] = options
        return target

    def write_roles(self, *seats, role="perf-issue"):
        """The user roles file, with each seat as one of the role's backups in order."""
        return self.write_roles_file({role: [self.named(FAILED)]},
                                     {role: [self.named(seat) for seat in seats]})

    def write_roles_file(self, roles, backups):
        """The user roles file, with these roles and these backups."""
        directory = Path(os.environ["XDG_CONFIG_HOME"]) / "pstack-t3"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "roles.json"
        path.write_text(json.dumps({"version": 1, "budget": "default", "roles": roles, "backups": backups}))
        return path

    def catalog(self):
        """A copy of the fixture catalog under this test's temporary directory."""
        path = Path(self.temporary.name) / "catalog.json"
        path.write_bytes((ROOT / "tests/fixtures/catalog.json").read_bytes())
        return path

    def usage_limit(self, text, dish="D1", role="perf-issue", seat=FAILED, *extra, ok=True):
        """Run the dish usage-limit command in this process, so the frozen clock reaches the command.

        A separate process would read the real clock.
        """
        return self.command_in_process("dish", dish, "--usage-limit", seat, "--role", role, "--error", "-",
                                       "--catalog", str(self.catalog()), "--parent", PARENT, *extra, ok=ok,
                                       stdin=text)

    def fire_another(self, number, branch, thread):
        """Fire one more dish on the same station, so several dishes share the store's cooldown rows."""
        self.brigade("ticket", "add", "--kind", "fix", "--summary", f"Work {number}")
        return self.brigade("fire", "--tickets", f"T{number}", "--station", "perf-issue", "--summary", f"Work {number}",
                            "--branch", branch, "--thread", thread)

    def write_cooldown_rows(self, rows, start=NOW, dish="D1", attempt="1"):
        """Write one cooldown row per `(seat, end)` pair into threads.tsv, replacing any cooldown row already there."""
        path = self.at / "threads.tsv"
        lines = [line for line in path.read_text().splitlines() if line]
        keys = lines[0].split("\t")
        kept = [dict(zip(keys, line.split("\t"))) for line in lines[1:]]
        kept = [row for row in kept if row["role"] != "cooldown"]
        for seat, end in rows:
            kept.append({"at": start, "dish": dish, "attempt": attempt, "role": "cooldown", "thread": "",
                         "seat": seat, "start": start, "end": end})
        path.write_text("\n".join([lines[0]] + ["\t".join(row.get(key, "") for key in keys) for row in kept]) + "\n")
        return kept[-1]

    def write_cooldown_row(self, seat=FAILED, end=NOW, dish="D1", attempt="1", start=NOW):
        """Write one cooldown row into threads.tsv, replacing any cooldown row already there."""
        return self.write_cooldown_rows([(seat, end)], start=start, dish=dish, attempt=attempt)

    def cooldown_rows(self):
        lines = [line for line in (self.at / "threads.tsv").read_text().splitlines() if line]
        keys = lines[0].split("\t")
        found = [dict(zip(keys, line.split("\t"))) for line in lines[1:]]
        return [row for row in found if row["role"] == "cooldown"]

    def cooldown_events(self):
        return [json.loads(row["note"]) | {"attempt": row["state"]}
                for row in self.log_rows() if row["kind"] == "cooldown"]
