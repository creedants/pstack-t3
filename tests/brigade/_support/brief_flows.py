import json
import os
import runpy
import subprocess
import sys
import time
from pathlib import Path

from .names import ROOT, SCRIPT, SUPPORT, _owner_words, _shown_root
from .inprocess import ProcessReason, call, real_popen
from .race_child import _race_args, _wait_for_path


class BriefFlows:
    def literal_brief_fields(self):
        return {
            "goal": "Run `$B status` and `$B close` before $(touch sentinel).",
            "acceptance": ["Median cold start below 400 ms"],
            "contract": "A cold start never reads `$HOME/.cache`.",
            "verify": "npm run perf",
            "paths": "src/boot.ts",
            "lease": "L4",
            "base": "origin/main",
            "context": ["notes/startup.md"],
        }


    def brief_from_flags(self, fields):
        return self.brigade("brief", "D1", "--contract", fields["contract"], "--goal", fields["goal"], "--acceptance", fields["acceptance"][0],
                            "--verify", fields["verify"], "--paths", fields["paths"], "--lease", fields["lease"],
                            "--base", fields["base"], "--context", fields["context"][0])


    NEEDS_CONTRACT = ("brigade: brief needs a contract; pass --contract <text> or the contract key in --fields, "
                      "and write none when the item has no safety, privacy, or integrity property")


    FLAGS = ("--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/boot.ts", "--lease", "L4",
             "--base", "origin/main")


    def briefable(self):
        self.open()
        self.fire_one()
        self.brigade("set", "--thread", "thread-coord")


    VERBATIM = "\n  A refused write changes no table.  \n\n  - `log.tsv` gains no row.\nVERIFY: is not a heading here. \n"


    def contract_fields(self, contract):
        fields = self.at / "brief-fields.json"
        fields.write_text(json.dumps({"goal": "g", "acceptance": ["a"], "verify": "v", "base": "origin/main",
                                      "paths": "src/boot.ts", "lease": "L4", "contract": contract}), encoding="utf-8")
        return ("--fields", str(fields))


    def mode_rows(self):
        return [line.split("\t")[1:] for line in (self.at / "log.tsv").read_text().splitlines()[1:]
                if line.split("\t")[1] == "mode"]


    SEAT_RULE = ("Seat rule. Copy the Mode value above into --brief-mode on every roles.py mode and roles.py show call you "
                 "make, and pass no other mode flag. Never pass --session-mode. Mode source names where your launcher's "
                 "decision came from. It does not make this thread a session.")


    WAIT = ("- Never end your turn while a child task you started with delegate_task is still open, per step 5 of the "
            "pstack-runtime skill's Delegation section. This overrides delegate_task's text that says to end the turn and "
            "wait for a notification. A completion wakes you only when the child's run ends, and a stalled child or a run "
            "left open never ends. Wait on each open child task with t3_thread_wait on its childThreadId and timeoutMs "
            "300000, then read it with task_status. A child whose run is still open and whose last message holds the result "
            "is stalled after two minutes with no new activity, per step 5. If that wait returns at once while workState is "
            "still working or waiting_for_children, the child's run ended with its task open. Then wait between task_status "
            "checks with t3_thread_wait and timeoutMs 120000 on your own thread, the parentThreadId from "
            "orchestrator_capabilities, with runId set to its activeRunId from t3_thread_read, never a shell sleep, and "
            "count ten minutes from its last activity item. "
            "A child task is open while its workState is working or "
            "waiting_for_children, whatever hasPendingChildRuns says. Cancel a child task with task_cancel when it runs "
            "past its budget or stalls, per the runtime's Failure handling. A thread you launched with t3_thread_launch "
            "has no parent, so its finished turn never wakes you. While it stays healthy, repeat t3_thread_wait on its "
            "threadId with timeoutMs 300000 and read its activity with t3_thread_read. A timeout alone never stops it. "
            "Stop it with t3_thread_interrupt and then a terminal t3_thread_wait only when it stalls, with no new activity "
            "item for ten minutes per step 5 of the runtime's Delegation section, or runs past its budget. "
            "A long-lived owner your playbook supervises, such as an Orchestrate PR owner, follows the runtime's Top-level "
            "threads section and its playbook instead, and does not hold your report. Write the report only once every "
            "child task and every other thread you launched is terminal.")


    CONTESTED = ("- If you find the design contested, do not run interrogate. Stop at a verifiable point, commit, and write "
                 "Contested: <one-line reason> under the status line. The coordinator moves the work to full mode and gives "
                 "your report to a fresh worker.")


    REPEAT = ("- Under the status line, repeat this brief's Mode: line, and its Waived by mode: line when it has one. "
              "A step that line names is not a deviation.")


    BRIEF = ("brief", "D1", "--contract", "none", "--goal", "g", "--acceptance", "a", "--verify", "v", "--paths", "src/a.py", "--lease", "L1",
             "--base", "origin/main")


    def coordinator(self, station="feature", mode="light", *fire):
        self.open()
        (self.at / "menu.md").write_text("## Purpose\n\nFast.\n")
        self.brigade("set", "--thread", "c1")
        if mode:
            self.brigade("set", "--mode", mode)
        self.brigade("ticket", "add", "--kind", "fix", "--summary", "one")
        self.brigade("fire", "--tickets", "T1", "--station", station, "--summary", "s", "--branch", "perf/d1",
                     "--thread", "w1", *fire)


    def head(self, *lines, station="feature"):
        return "\n".join([f"Use the poteto-mode skill and its `{station}` playbook.", *lines, "Gate: brigade", self.SEAT_RULE])


    def paused_brief(self, case):
        """The child waits in `roles_mode`. The patch is installed before `main`. The test does not overlap this child with another started process. The test acts while the child is stopped, and `call` cannot return that status."""
        case.mkdir()
        support = SUPPORT
        tail = _race_args("brief-pause", case, ["--store", str(self.store), "--at", str(self.at),
                                                *_owner_words(self.at), *self.BRIEF])
        return real_popen(
            ProcessReason.STOPPED,
            [sys.executable, str(support), *tail],
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            text=True)
