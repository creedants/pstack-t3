import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from tests import in_process

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/sync_upstream.py"
sys.path.append(str(ROOT / "scripts"))

import build  # noqa: E402
import sync_upstream  # noqa: E402
_scripts = str(ROOT / "scripts")
while _scripts in sys.path:
    sys.path.remove(_scripts)
sys.path.append(_scripts)

GIT_ENV = {
    **{name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "LC_ALL": "C",
    # git commit starts a detached "git maintenance run --auto" that holds .git/objects/maintenance.lock
    # while it runs, and a snapshot of a checkout can catch it.
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "maintenance.auto",
    "GIT_CONFIG_VALUE_0": "false",
}
PLUGIN = "pstack/.cursor-plugin/plugin.json"
SKILL = "---\nname: fixture\ndescription: A fixture skill.\n---\n\n# Fixture\n\n"
PINNED_TREE = {
    "harness/pstack/README.md": "upstream harness README.md\n",
    "harness/pstack/claude-code/skills/alpha/SKILL.md": "upstream harness skill\n",
    "other/notes.md": "upstream other/notes.md\n",
    PLUGIN: '{"name": "pstack", "version": "1.0.0"}\n',
    "pstack/README.md": "upstream README.md\n",
    "pstack/agents/persona.md": "upstream agents/persona.md\n",
    "pstack/automations/nightly/skills/job.md": "upstream automations job.md\n",
    "pstack/skills/alpha/SKILL.md": SKILL + "upstream alpha/SKILL.md\n",
    "pstack/skills/alpha/both.md": "upstream alpha/both.md\n",
    "pstack/skills/beta/SKILL.md": SKILL + "upstream beta/SKILL.md\n",
    "pstack/skills/beta/old.md": "upstream beta/old.md\n",
    "pstack/skills/gone/deep/file.md": "upstream gone/deep/file.md\n",
    "pstack/skills/gone/kept.md": "upstream gone/kept.md\n",
    "pstack/skills/setup-pstack/SKILL.md": SKILL + "upstream setup-pstack/SKILL.md\n",
}
T3_TREE = {
    "t3/added/alpha/both.md": "t3 added alpha/both.md\n",
    "t3/agents/persona.md": "t3 agents/persona.md\n",
    "t3/overrides/alpha/SKILL.md": SKILL + "t3 overrides alpha/SKILL.md\n",
    "t3/overrides/alpha/both.md": "t3 overrides alpha/both.md\n",
    "t3/overrides/gone/kept.md": "t3 overrides gone/kept.md\n",
    "t3/removed.txt": "# Upstream paths pstack-t3 does not ship.\ngone\n",
    "t3/runtime.md": SKILL + "t3 runtime.md\n",
    "t3/scripts/roles.py": "# t3 scripts/roles.py\n",
    "t3/setup.md": SKILL + "t3 setup.md\n",
}
RENDERED_FROM = {
    "alpha/SKILL.md": "t3/overrides/alpha/SKILL.md",
    "alpha/both.md": "t3/added/alpha/both.md",
    "beta/SKILL.md": "vendor/pstack/skills/beta/SKILL.md",
    "beta/old.md": "vendor/pstack/skills/beta/old.md",
    "gone/deep/file.md": None,
    "gone/kept.md": "t3/overrides/gone/kept.md",
    "setup-pstack/SKILL.md": "t3/setup.md",
}
LAYERS = sync_upstream.Layers(
    hand_ported={"agents/persona.md": "t3/agents/persona.md", "skills/setup-pstack/SKILL.md": "t3/setup.md"},
    added=frozenset({"alpha/both.md", "setup-pstack/SKILL.md"}),
    overrides=frozenset({"alpha/SKILL.md", "alpha/both.md", "gone/kept.md"}),
    removed=frozenset({"gone", "beta/old.md"}),
)
CLASSIFIED = (
    ("pstack/skills/alpha/SKILL.md", ("overridden", "t3/overrides/alpha/SKILL.md")),
    ("pstack/skills/alpha/both.md", ("overridden", "t3/added/alpha/both.md")),
    ("pstack/skills/gone/kept.md", ("overridden", "t3/overrides/gone/kept.md")),
    ("pstack/skills/setup-pstack/SKILL.md", ("overridden", "t3/setup.md")),
    ("pstack/agents/persona.md", ("overridden", "t3/agents/persona.md")),
    ("pstack/skills/gone/deep/file.md", ("dropped", None)),
    ("pstack/skills/beta/old.md", ("dropped", None)),
    ("pstack/skills/beta/SKILL.md", ("unchanged", None)),
    ("pstack/skills/gone-later/file.md", ("unchanged", None)),
    ("pstack/skills/top-level.md", ("unchanged", None)),
    ("pstack/agents/unported.md", ("not-shipped", None)),
    ("pstack/README.md", ("not-shipped", None)),
    ("pstack/automations/nightly/skills/alpha/SKILL.md", ("not-shipped", None)),
    ("pstack/skills/alpha/__pycache__/SKILL.md", ("not-shipped", None)),
    ("pstack/skills", ("not-shipped", None)),
)


OVERRIDDEN = ("alpha/SKILL.md", "alpha/both.md", "gone/kept.md")
OLD = "one\ntwo\nthree\nfour\nfive\nsix\nseven\n"
PORTED = "one t3\ntwo\nthree\nfour\nfive\nsix\nseven\n"
NEW = "one\ntwo\nthree\nfour\nfive\nsix\nseven upstream\n"
MERGED = "one t3\ntwo\nthree\nfour\nfive\nsix\nseven upstream\n"
CLASHING = "one upstream\ntwo\nthree\nfour\nfive\nsix\nseven\n"
BLOCK = "<<<<<<< left\nleft\n=======\nright\n>>>>>>> right\n"
MARKED = "<<<<<<< override\none t3\n=======\none upstream\n>>>>>>> new upstream\ntwo\nthree\nfour\nfive\nsix\nseven\n"
NO_ROWS = "no override's upstream file changed"
MARKERS = "the merge result holds a line starting with <<<<<<<, >>>>>>>, or |||||||, left as is, re-port by hand"
FOOTER = [
    "t3/overrides.lock.json is not refreshed.",
    "A clean merge is not a reviewed port, because upstream's new lines can hold a Cursor mechanism.",
    "Once the merges are written, review each merged override or persona and re-port each override or persona whose row says so. "
    "Then run python3 scripts/build.py --update-lock, which runs the build's Cursor-leftover check.",
]
README_AT_1_0_0 = (
    "Preamble keeps 1.0.0 and upstream pstack.\n"
    '  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license MIT"></a>\n'
    '  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-1.0.0-555.svg" alt="upstream pstack 1.0.0"></a>\n'
    "Postscript keeps 1.0.0.\n"
)
README_AT_1_1_0 = (
    "Preamble keeps 1.0.0 and upstream pstack.\n"
    '  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="license MIT"></a>\n'
    '  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-1.1.0-555.svg" alt="upstream pstack 1.1.0"></a>\n'
    "Postscript keeps 1.0.0.\n"
)
BADGE_LINE_1_0_0 = '  <a href="upstream.json"><img src="https://img.shields.io/badge/upstream%20pstack-1.0.0-555.svg" alt="upstream pstack 1.0.0"></a>'
REFUSAL = "README.md has no upstream badge in the expected form\n"


def fence_git_environment():
    """Remove every GIT_* variable from os.environ and point GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM at the null device.

    A module cleanup restores os.environ to what it held at this call.
    """
    saved = dict(os.environ)

    def restore():
        os.environ.clear()
        os.environ.update(saved)

    unittest.addModuleCleanup(restore)
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        del os.environ[name]
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull


def setUpModule():
    fence_git_environment()


def git(directory, *arguments):
    return subprocess.run(
        ("git", *arguments), cwd=directory, env=GIT_ENV, check=True, capture_output=True, text=True
    ).stdout.strip()


def write_tree(base, files):
    for name, text in files.items():
        target = base / name
        if text is None:
            target.unlink()
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)


def commit(repository, files):
    write_tree(repository, files)
    git(repository, "add", "--all")
    git(repository, "commit", "--quiet", "--message", "change")
    return git(repository, "rev-parse", "HEAD")


def new_upstream(directory):
    repository = Path(directory) / "upstream"
    repository.mkdir()
    git(repository, "init", "--quiet", "--initial-branch", "main")
    return repository, commit(repository, PINNED_TREE)


def new_checkout(directory, repository, pinned):
    root = Path(directory) / "checkout"
    shutil.copytree(ROOT / "scripts/_check", root / "scripts/_check", ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("sync_upstream", "build", "check"):
        shutil.copy(ROOT / f"scripts/{name}.py", root / "scripts")
    write_tree(root, T3_TREE)
    write_tree(root / "vendor", {name: text for name, text in PINNED_TREE.items() if name.startswith("pstack/")})
    meta = {"repository": str(repository), "commit": pinned, "path": "pstack", "excluded": ["harness/pstack"], "version": "1.0.0"}
    (root / "upstream.json").write_text(json.dumps(meta, indent=2) + "\n")
    (root / "README.md").write_text(README_AT_1_0_0)
    return root


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): (
            stat.S_IMODE(path.lstat().st_mode),
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
        )
        for path in root.rglob("*")
    }


def last_line(path):
    return path.read_text().splitlines()[-1]


def held(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes() if path.is_file() else None for path in root.rglob("*")
    }


def marker_lines(root):
    return [
        (path.relative_to(root).as_posix(), line)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        for line in path.read_bytes().splitlines()
        if line.startswith((b"<<<<<<<", b">>>>>>>", b"|||||||")) or line == b"======="
    ]


@contextmanager
def checkout_import(program):
    saved_tempdir = tempfile.tempdir
    tempfile.tempdir = None
    directory = Path(program).resolve().parent
    popped = {}
    try:
        for path in directory.glob("*.py"):
            name = path.stem
            module = sys.modules.get(name)
            loaded = getattr(module, "__file__", None)
            if not name.isidentifier() or loaded is None:
                continue
            if Path(loaded).resolve() == path.resolve():
                continue
            popped[name] = sys.modules.pop(name)
        yield
    finally:
        tempfile.tempdir = saved_tempdir
        sys.modules.update(popped)


class CheckoutCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.upstream, self.pinned = new_upstream(self.directory)
        self.root = new_checkout(self.directory, self.upstream, self.pinned)
        self.scratch = self.directory / "scratch"
        self.scratch.mkdir()

    def sync(self, *flags, **environment):
        # PYTHONDONTWRITEBYTECODE in a developer's shell would hide a script that writes __pycache__.
        inherited = {name: value for name, value in GIT_ENV.items() if not name.startswith("PYTHON")}
        program = self.root / "scripts/sync_upstream.py"
        with checkout_import(program):
            return in_process.invoke(in_process.Invocation(
                program=program,
                argv=flags,
                cwd=self.root,
                env={**inherited, "TMPDIR": str(self.scratch), **environment},
            ))


class MergeCase(CheckoutCase):
    def setUp(self):
        super().setUp()
        files = {f"pstack/skills/{rel}": OLD for rel in OVERRIDDEN}
        files["pstack/agents/persona.md"] = OLD
        self.pinned = commit(self.upstream, files)
        write_tree(self.root, {f"vendor/pstack/skills/{rel}": OLD for rel in OVERRIDDEN})
        write_tree(self.root, {f"t3/overrides/{rel}": PORTED for rel in OVERRIDDEN})
        write_tree(self.root, {"vendor/pstack/agents/persona.md": OLD, "t3/agents/persona.md": PORTED})
        meta = json.loads((self.root / "upstream.json").read_text())
        (self.root / "upstream.json").write_text(json.dumps({**meta, "commit": self.pinned}, indent=2) + "\n")
        self.lock = self.root / "t3/overrides.lock.json"
        self.write_lock()

    def write_lock(self):
        skills = self.root / "vendor/pstack/skills"
        digests = {rel: hashlib.sha256((skills / rel).read_bytes()).hexdigest() for rel in OVERRIDDEN}
        digests["agents/persona.md"] = hashlib.sha256((self.root / "vendor/pstack/agents/persona.md").read_bytes()).hexdigest()
        self.lock.write_text(json.dumps(digests, indent=2, sort_keys=True) + "\n")

    def merge(self, files, *flags):
        head = commit(self.upstream, files)
        result = self.sync("--merge", *flags)
        self.assertEqual(result.stderr, "")
        header, *lines = result.stdout.splitlines()
        self.assertEqual(header, f"vendor/pstack: {self.pinned[:12]} -> {head[:12]} (1.0.0)")
        return lines

    def override(self, rel):
        return (self.root / "t3/overrides" / rel).read_bytes()

    def vendored(self, rel):
        return (self.root / "vendor/pstack/skills" / rel).read_bytes()

