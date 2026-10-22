import json
import unittest
from .support import BADGE_LINE_1_0_0, PINNED_TREE, PLUGIN, README_AT_1_0_0, README_AT_1_1_0, REFUSAL, CheckoutCase, commit, setUpModule, snapshot

class SyncTest(CheckoutCase):
    def test_without_check_the_sync_still_replaces_vendor_and_moves_the_pin(self):
        head = commit(
            self.upstream,
            {
                PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n',
                "pstack/skills/beta/SKILL.md": "edited\n",
                "pstack/skills/beta/old.md": None,
            },
        )
        result = self.sync()
        self.assertEqual(result.stdout.splitlines()[0], f"vendor/pstack: {self.pinned[:12]} -> {head[:12]} (1.1.0)")
        self.assertEqual(result.returncode, 1)
        self.assertIn("override drift", result.stdout)
        meta = json.loads((self.root / "upstream.json").read_text())
        self.assertEqual(
            meta, {"repository": str(self.upstream), "commit": head, "path": "pstack", "excluded": ["harness/pstack"], "version": "1.1.0"}
        )
        expected = {name[len("pstack/") :]: text for name, text in PINNED_TREE.items() if name.startswith("pstack/")}
        expected[".cursor-plugin/plugin.json"] = '{"name": "pstack", "version": "1.1.0"}\n'
        expected["skills/beta/SKILL.md"] = "edited\n"
        del expected["skills/beta/old.md"]
        vendor = self.root / "vendor/pstack"
        self.assertEqual(
            {path.relative_to(vendor).as_posix(): path.read_text() for path in vendor.rglob("*") if path.is_file()},
            expected,
        )


class BadgeSyncTest(CheckoutCase):
    def test_a_newer_upstream_rewrites_the_badge_and_leaves_every_other_readme_byte(self):
        head = commit(self.upstream, {PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n'})
        endings = (
            ("lf", README_AT_1_0_0, README_AT_1_1_0),
            ("crlf", README_AT_1_0_0.replace("\n", "\r\n"), README_AT_1_1_0.replace("\n", "\r\n")),
            ("mixed", README_AT_1_0_0.replace("\n", "\r\n", 1), README_AT_1_1_0.replace("\n", "\r\n", 1)),
            ("no final newline", README_AT_1_0_0[:-1], README_AT_1_1_0[:-1]),
        )
        for flags in ((), ("--ref", head), ("--merge",), ("--merge", "--ref", head)):
            for name, before, after in endings:
                with self.subTest(flags=flags, endings=name):
                    (self.root / "README.md").write_bytes(before.encode())
                    result = self.sync(*flags)
                    self.assertEqual(result.stderr, "")
                    self.assertEqual((self.root / "README.md").read_bytes(), after.encode())
                    self.assertEqual(
                        json.loads((self.root / "upstream.json").read_text()),
                        {"repository": str(self.upstream), "commit": head, "path": "pstack", "excluded": ["harness/pstack"], "version": "1.1.0"},
                    )

    def test_a_missing_badge_exits_before_the_pin_write(self):
        commit(self.upstream, {PLUGIN: '{"name": "pstack", "version": "1.1.0"}\n'})
        duplicated = README_AT_1_0_0.replace(BADGE_LINE_1_0_0, BADGE_LINE_1_0_0 + "\n" + BADGE_LINE_1_0_0, 1)
        split = README_AT_1_0_0.replace(
            BADGE_LINE_1_0_0, BADGE_LINE_1_0_0.replace('"><img', '">\n<img', 1), 1
        )
        for flags in ((), ("--merge",)):
            for name, text in (
                ("deleted", None),
                ("not a badge", "not a badge\n"),
                ("duplicated", duplicated),
                ("split", split),
            ):
                with self.subTest(flags=flags, readme=name):
                    readme = self.root / "README.md"
                    if text is None:
                        readme.unlink(missing_ok=True)
                    else:
                        readme.write_text(text)
                    before = snapshot(self.root)
                    result = self.sync(*flags)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", REFUSAL))
                    self.assertEqual(snapshot(self.root), before)
                    if text is None:
                        self.assertFalse(readme.exists())
                    else:
                        self.assertEqual(readme.read_text(), text)

    def test_a_prerelease_version_exits_with_its_own_message_and_writes_nothing(self):
        commit(self.upstream, {PLUGIN: '{"name": "pstack", "version": "1.0.0-rc1"}\n'})
        expected = "upstream version 1.0.0-rc1 cannot be carried by the README upstream badge\n"
        for flags in ((), ("--merge",)):
            with self.subTest(flags=flags):
                (self.root / "README.md").write_text(README_AT_1_0_0)
                before = snapshot(self.root)
                result = self.sync(*flags)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (1, "", expected))
                self.assertEqual(snapshot(self.root), before)
                self.assertEqual((self.root / "README.md").read_text(), README_AT_1_0_0)


