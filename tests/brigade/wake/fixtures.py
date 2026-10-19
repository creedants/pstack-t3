from ..support import setUpModule
from ..support import BrigadeTest, CLAUDE, CODEX, HELD_LINE, Path, ROOT, SCRIPT, STILL_OPEN, call, datetime, json, os, runpy, shlex, subprocess, sys, time, timedelta, timezone

class _Helpers:
    def _seed_trunk(self, state, note, ident="main"):
        stamp = datetime.now(timezone.utc).isoformat()
        with open(self.at / "log.tsv", "a") as handle:
            handle.write(f"{stamp}\ttrunk\t{ident}\t{state}\t{note}\n")
