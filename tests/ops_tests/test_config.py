if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import re
import unittest
from .support import ARCHIVE, ARCHIVE_CASES, CREDENTIAL, EXAMPLE, OpsTest, setUpModule

ACCEPTED, NO_OWN_KEY = object(), object()
READER_CASES = (
    ("base", lambda base: base, ACCEPTED),
    ("crlf", lambda base: base.replace("\n", "\r\n"), ACCEPTED),
    ("no final newline", lambda base: base.rstrip("\n"), ACCEPTED),
    ("comment and blank", lambda base: "# c\n\n   \t\n  # d\n" + base, ACCEPTED),
    ("leading blanks", lambda base: base.replace("ARCHIVE_T3_HOME", " \tARCHIVE_T3_HOME"), ACCEPTED),
    ("other key", lambda base: base + "SOMETHING_ELSE=1 2 = 3\n", ACCEPTED),
    ("empty other value", lambda base: base + "X=\n", ACCEPTED),
    ("number sign inside a value", lambda base: base + "X=1 # not a comment\n", ACCEPTED),
    ("lone carriage return inside", lambda base: base + "X=1\rY=2\n", ACCEPTED),
    ("comment that is not UTF-8", lambda base: base.encode() + b"# \xff\xfe\n", ACCEPTED),
    ("space before equals", lambda base: base + "X =1\n", "line 2 is not KEY=value"),
    ("no equals", lambda base: base + "just words\n", "line 2 is not KEY=value"),
    ("lowercase key", lambda base: base + "x=1\n", "line 2 is not KEY=value"),
    ("digit first", lambda base: base + "1X=1\n", "line 2 is not KEY=value"),
    ("empty key", lambda base: base + "=1\n", "line 2 is not KEY=value"),
    ("export prefix", lambda base: base + "export X=1\n", "line 2 is not KEY=value"),
    ("key that is not UTF-8", lambda base: base.encode() + b"K\xff=1\n", "line 2 is not KEY=value"),
    ("other key twice", lambda base: base + "X=1\nX=1\n", "line 3 sets X a second time"),
    ("archive key twice", lambda base: base + "ARCHIVE_T3_HOME=/a/t3\n", "line 2 sets ARCHIVE_T3_HOME a second time"),
    ("nightly apps dir kept", lambda base: base + "NIGHTLY_APPS_DIR=/a/apps\n", ACCEPTED),
    ("nightly app name kept", lambda base: base + "NIGHTLY_APP_NAME=t3code\n", ACCEPTED),
    ("nightly restart env kept", lambda base: base + "NIGHTLY_RESTART_ENV=/a/restart.env\n", ACCEPTED),
    ("nightly mise tools kept", lambda base: base + "NIGHTLY_MISE_TOOLS=alpha\n", ACCEPTED),
    ("nightly version commands kept", lambda base: base + "NIGHTLY_VERSION_COMMANDS=alpha\n", ACCEPTED),
    ("nightly key twice", lambda base: base + "NIGHTLY_APP_NAME=t3code\nNIGHTLY_APP_NAME=t3code\n",
     "line 3 sets NIGHTLY_APP_NAME a second time"),
    ("empty file", lambda base: "", NO_OWN_KEY),
)


class ConfigTest(OpsTest):
    def probe(self, world, config=True, **env):
        return world.sandbox.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=config, **env)

    def assertRan(self, done):
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, ARCHIVE_CASES[0][1] + "\n", ""))

    def test_no_config_prints_one_line_naming_the_file_and_changes_nothing(self):
        world = self.world()
        box = world.sandbox
        before = box.tree()
        for arguments in ((), ("--help",), ("--project", "fixture project"),
                          ("--project", "fixture project", "--top-level-settled")):
            with self.subTest(arguments=arguments):
                done = box.run(ARCHIVE, *arguments, config=False, T3_CODE_MCP_AUTHORIZATION=CREDENTIAL)
                self.assertRefused(done, ARCHIVE, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                                                  "Copy ops/config.example to that path and set every key.")
                self.assertEqual(box.tree(), before)
                self.assertEqual(world.stub.requests, [])

    def test_xdg_config_home_is_used_only_when_it_is_absolute(self):
        world = self.world()
        box = world.sandbox
        box.write_config(box.root / "xdg/pstack-t3/ops.conf")
        self.assertRan(self.probe(world, config=False, XDG_CONFIG_HOME=str(box.root / "xdg")))
        self.assertRefused(self.probe(world, config=False, XDG_CONFIG_HOME="xdg"),
                           ARCHIVE, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                           "Copy ops/config.example to that path and set every key.")

    def test_environment_variable_names_the_file(self):
        world = self.world()
        box = world.sandbox
        named = box.root / "named.conf"
        self.assertRefused(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=""),
                           ARCHIVE, f"no config file at {box.home}/.config/pstack-t3/ops.conf. "
                           "Copy ops/config.example to that path and set every key.")
        box.write_config(box.home / ".config/pstack-t3/ops.conf")
        self.assertRan(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=""))
        self.assertRefused(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=str(named)),
                           ARCHIVE, f"no config file at {named}. Copy ops/config.example to that path and set every key.")
        box.write_config(named)
        (box.home / ".config/pstack-t3/ops.conf").unlink()
        self.assertRan(self.probe(world, config=False, PSTACK_T3_OPS_CONFIG=str(named)))

    def test_flag_wins_and_a_missing_flag_file_is_not_replaced(self):
        world = self.world()
        box = world.sandbox
        box.write_config(box.home / ".config/pstack-t3/ops.conf")
        named = box.write_config(box.root / "named.conf")
        flagged = box.root / "flagged.conf"
        self.assertRefused(self.probe(world, config=flagged, PSTACK_T3_OPS_CONFIG=str(named)),
                           ARCHIVE, f"no config file at {flagged}. Copy ops/config.example to that path and set every key.")
        box.write_config(flagged)
        named.write_text("not a config\n")
        (box.home / ".config/pstack-t3/ops.conf").write_text("not a config\n")
        self.assertRan(self.probe(world, config=flagged, PSTACK_T3_OPS_CONFIG=str(named)))

    def test_config_flag_with_no_file_exits_2(self):
        world = self.world()
        done = world.sandbox.run(ARCHIVE, "--config", config=False)
        self.assertEqual((done.returncode, done.stdout, done.stderr), (2, "", f"{ARCHIVE.name}: --config needs a file\n"))

    def test_a_config_path_that_is_a_directory_is_refused_and_a_dangling_link_is_no_config(self):
        world = self.world()
        link = world.sandbox.root / "link.conf"
        link.symlink_to(world.sandbox.root / "absent.conf")
        self.assertRefused(self.probe(world, config=world.sandbox.home),
                           ARCHIVE, f"config file {world.sandbox.home} is not a readable file")
        self.assertRefused(self.probe(world, config=link),
                           ARCHIVE, f"no config file at {link}. Copy ops/config.example to that path and set every key.")

    def test_the_reader_accepts_and_refuses_each_file(self):
        world = self.world()
        box = world.sandbox
        for name, build, expected in READER_CASES:
            with self.subTest(case=name):
                file = box.write_config(text=build(box.config_text()))
                done = self.probe(world)
                if expected is ACCEPTED:
                    self.assertRan(done)
                else:
                    problem = "does not set ARCHIVE_T3_HOME" if expected is NO_OWN_KEY else expected
                    self.assertRefused(done, ARCHIVE, f"config file {file} {problem}")

    def test_a_script_refuses_an_unknown_key_with_its_prefix_and_ignores_other_keys(self):
        world = self.world()
        box = world.sandbox
        file = box.write_config(text=box.config_text() + "ARCHIVE_DB=/x\n")
        with self.subTest(line="ARCHIVE_DB=/x"):
            self.assertRefused(self.probe(world),
                               ARCHIVE, f"config file {file} line 2 sets ARCHIVE_DB, and this script has no such key")
        for line in ("NIGHTLY_APPS_DIR=/a/apps", "NIGHTLY_APP_NAME=t3code", "NIGHTLY_RESTART_ENV=/a/restart.env",
                     "NIGHTLY_MISE_TOOLS=alpha", "NIGHTLY_VERSION_COMMANDS=alpha",
                     "NIGHTLY_LOG=/x", "OTHER=1"):
            with self.subTest(line=line):
                box.write_config(text=box.config_text() + line + "\n")
                self.assertRan(self.probe(world))

    def test_the_example_holds_the_one_required_key(self):
        box = self.world().sandbox
        example = EXAMPLE.read_text()
        keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", example, re.MULTILINE)
        self.assertEqual(keys, ["ARCHIVE_T3_HOME"])
        archive = box.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=EXAMPLE)
        self.assertEqual((archive.returncode, archive.stdout), (1, ""))
        self.assertIn("sqlite3.OperationalError", archive.stderr)
        file = box.write_config(text="".join(line for line in example.splitlines(True) if not line.startswith("ARCHIVE_T3_HOME=")))
        self.assertRefused(box.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=file),
                           ARCHIVE, f"config file {file} does not set ARCHIVE_T3_HOME")
        legacy = (("NIGHTLY_APPS_DIR", "/a/apps"), ("NIGHTLY_APP_NAME", "t3code"),
                  ("NIGHTLY_RESTART_ENV", "/a/restart.env"), ("NIGHTLY_MISE_TOOLS", "alpha"),
                  ("NIGHTLY_VERSION_COMMANDS", "alpha"))
        full = example + "".join(f"{key}={value}\n" for key, value in legacy)
        full_file = box.write_config(text=full)
        full_run = box.run(ARCHIVE, "--project", "fixture project", "--dry-run", config=full_file)
        self.assertEqual((full_run.returncode, full_run.stdout), (1, ""))
        self.assertIn("sqlite3.OperationalError", full_run.stderr)
        for key, _ in legacy:
            with self.subTest(key=key):
                omitted = "".join(line for line in full.splitlines(True) if not line.startswith(key + "="))
                done = box.run(ARCHIVE, "--project", "fixture project", "--dry-run",
                               config=box.write_config(text=omitted))
                self.assertEqual((done.returncode, done.stdout), (1, ""))
                self.assertIn("sqlite3.OperationalError", done.stderr)

    def test_archive_refuses_a_t3_home_that_is_relative_or_holds_a_uri_character(self):
        world = self.world()
        for value in ("t3-home", "/a/t3?mode=rw", "/a/t3#x", "/a/t 3", ""):
            with self.subTest(value=value):
                file = world.sandbox.write_config(ARCHIVE_T3_HOME=value)
                self.assertRefused(world.sandbox.run(ARCHIVE, "--project", "fixture project"), ARCHIVE,
                                   f"config file {file} puts '{value}' in ARCHIVE_T3_HOME. "
                                   "It must start with / and use only the characters A-Za-z0-9._/-.")
                self.assertEqual(world.stub.requests, [])

    def test_blanks_around_the_value_are_dropped(self):
        world = self.world()
        box = world.sandbox
        box.write_config(text=box.config_text(ARCHIVE_T3_HOME=f"  {box.home}/t3-home \t").replace("\n", "\r\n"))
        self.assertRan(self.probe(world))



