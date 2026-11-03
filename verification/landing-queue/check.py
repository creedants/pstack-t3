#!/usr/bin/env python3
"""Check the landing queue's TLA+ model with TLC.

Runs every config beside this file, then every config under variants/. A base
config must hold every invariant. A variant must report the invariant its
EXPECT line names. Exits 0 when every run answered as expected, 1 otherwise.

One command. No arguments. Run it through the slot so a model check never
crowds out a build, as the landing skill's Local command capacity says.

TLC keeps its state in a new directory under ~/.cache/pstack-t3/scratch for each
run. The run deletes that directory when TLC exits.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = "LandingQueue.tla"

# The versions this model was last checked with. Pinning both is what makes a
# pass reproducible. Update them together with the wrapper, never alone.
TLC_TOOL = (
    "http:tla2tools["
    "url=https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar,"
    "checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"
    "]@1.7.4"
)
JAVA_TOOL = "java@temurin-25"

# Use one worker and fingerprint polynomial index 0.
# Fingerprinting does not eliminate collision risk. These configs check
# invariants, not temporal liveness properties. Disable deadlock checking
# because the bound in BoundNext leaves no enabled action at Depth.
FLAGS = ["-workers", "1", "-fp", "0", "-lncheck", "final", "-nowarning", "-deadlock"]

VIOLATED = re.compile(r"^Error: Invariant (\w+) is violated\.", re.MULTILINE)
STATES = re.compile(r"(\d+) states generated, (\d+) distinct states found")
LEVELS = re.compile(r"The depth of the complete state graph search is (\d+)\.")


def mise(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["mise", *args], capture_output=True, text=True,
                          env={**os.environ, "MISE_CEILING_PATHS": str(HERE)})


def tools() -> tuple[str, str]:
    """The java binary and the tla2tools.jar, or a refusal naming what to set."""
    jar, java = os.environ.get("TLA2TOOLS"), os.environ.get("JAVA")
    if jar and java:
        return java, jar
    if not shutil.which("mise"):
        raise SystemExit(
            "Cannot find TLC. Set TLA2TOOLS to tla2tools.jar and JAVA to a java binary, "
            "or install mise so this check can fetch both."
        )
    found = [mise("where", TLC_TOOL), mise("which", "java", "--tool", JAVA_TOOL)]
    if any(result.returncode != 0 for result in found):
        print(f"Installing {JAVA_TOOL} and {TLC_TOOL} with mise.", flush=True)
        installed = mise("install", JAVA_TOOL, TLC_TOOL)
        if installed.returncode != 0:
            raise SystemExit(
                "Cannot find TLC: mise install failed. "
                f"{(installed.stderr or installed.stdout).strip()}"
            )
        found = [mise("where", TLC_TOOL), mise("which", "java", "--tool", JAVA_TOOL)]
    return found[1].stdout.strip(), str(Path(found[0].stdout.strip()) / "tla2tools.jar")


def expected(config: Path) -> str:
    for line in config.read_text().splitlines():
        match = re.match(r"\\\*\s*EXPECT:\s*(\w+)\s*$", line)
        if match:
            return match.group(1)
    return ""


def run(java: str, jar: str, config: Path) -> tuple[int, str, float]:
    started = time.monotonic()
    scratch = Path(os.path.expanduser("~")) / ".cache" / "pstack-t3" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    meta = tempfile.mkdtemp(prefix="landing-queue-tlc-", dir=scratch)
    try:
        result = subprocess.run(
            [java, "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC", *FLAGS,
             "-metadir", meta, "-config", str(config), SPEC],
            cwd=HERE, capture_output=True, text=True,
        )
    finally:
        shutil.rmtree(meta)
    return result.returncode, result.stdout + result.stderr, time.monotonic() - started


def main() -> int:
    java, jar = tools()
    configs = sorted(HERE.glob("*.cfg")) + sorted((HERE / "variants").glob("*.cfg"))
    failures = 0
    print(f"{'config':<42} {'result':<28} {'states':>9} {'distinct':>9} {'deepest':>7} {'seconds':>8}")
    for config in configs:
        code, output, seconds = run(java, jar, config)
        wanted = expected(config)
        broken = VIOLATED.findall(output)
        counts = STATES.search(output)
        generated = counts.group(1) if counts else "?"
        distinct = counts.group(2) if counts else "?"
        # TLC counts the initial state as level 1. On a violation this is how far the search had got.
        levels = LEVELS.search(output)
        deepest = int(levels.group(1)) - 1 if levels else "-"
        if wanted:
            ok = wanted in broken
            verdict = f"breaks {wanted}" if ok else f"MISSED {wanted}"
        else:
            ok = code == 0 and not broken
            verdict = "holds" if ok else f"BREAKS {(broken or ['error'])[0]}"
        if not ok:
            failures += 1
            print(f"\n--- {config.relative_to(HERE)}: {verdict}\n{output.strip()[-4000:]}\n")
        name = str(config.relative_to(HERE))
        print(f"{name:<42} {verdict:<28} {generated:>9} {distinct:>9} {deepest:>7} {seconds:>8.1f}")
    print()
    if failures:
        print(f"{failures} of {len(configs)} runs answered differently than the model says.")
        return 1
    print(f"{len(configs)} runs answered as the model says.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
