import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "t3/scripts"))
sys.path.append(str(ROOT / "scripts"))

import tempfile
from tests.pstack_t3.support import PLAYBOOK_FILES


class BuildTest(unittest.TestCase):
    def test_replace_tree_recovers_an_interrupted_swap_before_copying(self):
        import build
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "dest.outgoing").mkdir()
            (base / "dest.outgoing/old").write_text("old")
            (base / "src").mkdir()
            with mock.patch.object(build.shutil, "copytree", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    build.replace_tree(base / "src", base / "dest")
            self.assertEqual((base / "dest/old").read_text(), "old")

    def test_check_rejects_invalid_yaml_frontmatter(self):
        import check  # noqa: E402
        if check.yaml is None:
            self.skipTest("PyYAML is not installed, so the fallback frontmatter reader in scripts/check.py cannot reject invalid YAML")
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: [unterminated\n---\n\nbody\n")
            self.assertTrue(any("not valid YAML" in finding for finding in check.check_tree(directory)))

    def test_catalog_lists_every_skill_and_playbook(self):
        import catalog
        text = catalog.render(ROOT / "skills")
        for skill_md in (ROOT / "skills").glob("*/SKILL.md"):
            self.assertIn(f"[`{skill_md.parent.name}`]", text)
        self.assertIn("(../skills/poteto-mode/playbooks/bug-fix.md)", text)

    def test_kebab_lowercases_and_hyphenates_three_playbook_titles(self):
        import catalog
        self.assertEqual(catalog.kebab("Bug fix"), "bug-fix")
        self.assertEqual(catalog.kebab("Autopilot-full"), "autopilot-full")
        self.assertEqual(catalog.kebab("Opening a PR"), "opening-a-pr")

    def test_built_playbook_bullets_resolve_to_the_files_in_playbook_files(self):
        import catalog
        resolved = {title: file for title, file, _ in catalog.playbook_entries(ROOT / "skills")}
        self.assertEqual(resolved, PLAYBOOK_FILES)

    def test_every_built_playbook_bullet_resolves_to_an_existing_file(self):
        import catalog
        for title, file, _ in catalog.playbook_entries(ROOT / "skills"):
            self.assertTrue((ROOT / "skills/poteto-mode" / file).is_file(), title)
        self.assertEqual(catalog.missing_playbooks(ROOT / "skills"), [])

    def test_vendor_playbook_entries_match_playbook_files(self):
        import catalog
        resolved = {title: file for title, file, _ in catalog.playbook_entries(ROOT / "vendor/pstack/skills")}
        self.assertEqual(resolved, PLAYBOOK_FILES)
        self.assertEqual(catalog.missing_playbooks(ROOT / "vendor/pstack/skills"), [])

    def test_a_skill_with_no_playbooks_heading_returns_nothing(self):
        import catalog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "poteto-mode"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: poteto-mode\ndescription: d\n---\n\n# Poteto mode\n")
            self.assertEqual(catalog.playbook_entries(root), [])
            self.assertEqual(catalog.missing_playbooks(root), [])

    def test_playbooks_extra_does_not_open_the_section(self):
        import catalog
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "poteto-mode"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: poteto-mode\ndescription: d\n---\n\n## Playbooks extra\n- **No such playbook** Nothing.\n")
            self.assertEqual(catalog.playbook_entries(root), [])
            self.assertEqual(catalog.missing_playbooks(root), [])

    def test_the_playbooks_section_keeps_catalog_bullets_and_reports_the_other_list_markers(self):
        import check  # noqa: E402
        import catalog
        front = "---\nname: poteto-mode\ndescription: d\n---\n"
        body = [
            "## Before",
            "- **Ignored.** Because this is before Playbooks.",
            "## Playbooks",
            "This is prose.",
            "- **No such playbook** Nothing.",
            "* **Star.** Nothing.",
            "  - **Indented.** Nothing.",
            "- **Empty.**",
            "+ **Plus.** Nothing.",
            "- **Bug fix.** File: `playbooks/bug-fix.md`.",
            "- **No such playbook.** Nothing.",
            "-not a bullet",
            "1. **Ordered.** Nothing.",
            "### Nested",
            "- **Nested title.** File: `playbooks/bug-fix.md`.",
            "## Later",
            "- **No such playbook.** Nothing.",
            "## Playbooks",
            "- **Second.** Nothing.",
        ]
        text = front + "\n".join(body) + "\n"
        lines = text.splitlines()
        bad = [
            "- **No such playbook** Nothing.",
            "* **Star.** Nothing.",
            "- **Empty.**",
            "+ **Plus.** Nothing.",
        ]
        expected = [
            f'poteto-mode/SKILL.md:{lines.index(bullet) + 1}: playbook bullet does not match "- **Title.** summary": {bullet.strip()[:120]}'
            for bullet in bad
        ]
        expected.append('poteto-mode/SKILL.md: playbook "No such playbook" resolves to playbooks/no-such-playbook.md, which does not exist')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "poteto-mode"
            (skill / "playbooks").mkdir(parents=True)
            (skill / "SKILL.md").write_text(text)
            (skill / "playbooks/bug-fix.md").write_text("ok\n")
            self.assertEqual(
                catalog.playbook_entries(root),
                [
                    ("Bug fix", "playbooks/bug-fix.md", ""),
                    ("No such playbook", "playbooks/no-such-playbook.md", "Nothing."),
                    ("Nested title", "playbooks/bug-fix.md", ""),
                ],
            )
            self.assertEqual(catalog.missing_playbooks(root), expected)
            self.assertEqual(check.check_tree(root), expected)

    def test_build_fails_before_the_swap_on_a_playbook_bullet_whose_file_does_not_exist(self):
        import build
        from unittest import mock
        real = build.render

        def render_with_a_stray_bullet(destination):
            real(destination)
            with (destination / "poteto-mode/SKILL.md").open("a") as skill:
                skill.write("- **No such playbook.** Nothing.\n")

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "skills"
            with mock.patch.object(build, "render", render_with_a_stray_bullet):
                with self.assertRaises(SystemExit):
                    build.build(out, skip_lock=True)
            self.assertFalse(out.exists())

    def test_build_accepts_an_indented_note_under_a_playbook_bullet(self):
        import build
        from unittest import mock
        real = build.render
        note = "    - Choose this when the defect is already reproduced.\n"

        def render_with_an_indented_note(destination):
            real(destination)
            with (destination / "poteto-mode/SKILL.md").open("a") as skill:
                skill.write(note)

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "skills"
            with mock.patch.object(build, "render", render_with_an_indented_note):
                self.assertEqual(build.build(out, skip_lock=True), out)
            self.assertIn(note, (out / "poteto-mode/SKILL.md").read_text())

    def test_check_flags_cursor_leftovers(self):
        import check  # noqa: E402
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nSpawn with `subagent_type: generalPurpose`.\n")
            findings = check.check_tree(directory)
        self.assertTrue(any("subagent_type" in finding for finding in findings))

    def test_check_requires_watch_tool_names_on_the_runtime_skill(self):
        import check  # noqa: E402
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "pstack-runtime"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: pstack-runtime\ndescription: d\n---\n\nNo watch here.\n")
            findings = check.check_tree(directory)
            self.assertIn("pstack-runtime/SKILL.md: missing watch_pull_request", findings)
            self.assertIn("pstack-runtime/SKILL.md: missing unwatch_pull_request", findings)
            (skill / "SKILL.md").write_text(
                "---\nname: pstack-runtime\ndescription: d\n---\n\n"
                "Call `watch_pull_request`. Call `unwatch_pull_request`.\n"
            )
            findings = check.check_tree(directory)
        self.assertFalse(any("missing watch" in finding or "missing unwatch" in finding for finding in findings))

    def test_check_requires_the_watch_tool_and_version_on_setup(self):
        import check  # noqa: E402
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "setup-pstack"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: setup-pstack\ndescription: d\n---\n\nNo watch here.\n")
            findings = check.check_tree(directory)
            self.assertIn("setup-pstack/SKILL.md: missing watch_pull_request", findings)
            self.assertIn("setup-pstack/SKILL.md: missing T3 Code 0.1.2 or later", findings)
            (skill / "SKILL.md").write_text(
                "---\nname: setup-pstack\ndescription: d\n---\n\n"
                "Call `watch_pull_request`. T3 Code 0.1.2 or later.\n"
            )
            findings = check.check_tree(directory)
        self.assertFalse(any("missing watch_pull_request" in finding or "missing T3 Code 0.1.2 or later" in finding for finding in findings))

    def test_check_rejects_the_cursor_xhigh_slug(self):
        import check  # noqa: E402
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\n\nUse claude-opus-5-5-xhigh.\n")
            findings = check.check_tree(directory)
        self.assertTrue(any("claude-opus-5-5-xhigh" in finding for finding in findings))

    def test_check_rejects_an_indented_catalog_heredoc_closer(self):
        import check  # noqa: E402
        bad = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "```bash",
            "python3 tool <<'JSON'",
            '{"ok": true}',
            "  JSON",
            "```",
            "",
        ])
        good = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "```bash",
            "python3 tool <<'JSON'",
            '{"ok": true}',
            "JSON",
            "```",
            "",
        ])
        plain = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "  JSON",
            "",
        ])
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(bad)
            findings = check.check_tree(directory)
        self.assertIn(
            "demo/SKILL.md:9: catalog heredoc closer JSON is indented. Put JSON at column 0",
            findings,
        )
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(good)
            findings = check.check_tree(directory)
        self.assertFalse(any("heredoc closer" in finding for finding in findings))
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(plain)
            findings = check.check_tree(directory)
        self.assertFalse(any("heredoc closer" in finding for finding in findings))

    def test_check_scopes_the_catalog_heredoc_closer_to_its_body(self):
        import check  # noqa: E402
        text = "\n".join([
            "---",
            "name: demo",
            "description: d",
            "---",
            "",
            "  JSON",
            "```bash",
            "python3 tool <<'JSON'",
            '{"name": "JSON"}',
            "JSON ",
            "JSON\t",
            "  JSON",
            "JSON",
            "```",
            "",
            "  JSON",
            "",
        ])
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(text)
            findings = check.check_tree(directory)
        self.assertEqual(findings, [
            "demo/SKILL.md:10: catalog heredoc closer JSON has trailing whitespace. The closer is JSON with nothing after it",
            "demo/SKILL.md:11: catalog heredoc closer JSON has trailing whitespace. The closer is JSON with nothing after it",
            "demo/SKILL.md:12: catalog heredoc closer JSON is indented. Put JSON at column 0",
        ])

    def light_findings(self, rel, body):
        import check  # noqa: E402
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / rel
            path.parent.mkdir(parents=True)
            name = rel.split("/", 1)[0]
            path.write_text(f"---\nname: {name}\ndescription: d\n---\n\n{body}\n" if rel.endswith("SKILL.md") else body)
            return [finding for finding in check.check_tree(directory) if "light" in finding]

    def test_check_rejects_a_pasted_waiver_table(self):
        header = "| Playbook | `first` | `fix` and `bounce` |"
        findings = self.light_findings("demo/SKILL.md", "\n".join([
            header,
            "| --- | --- | --- |",
            "| Feature | Arena, Interrogate, Comment Sicko | How, Architect, Arena, Interrogate, Comment Sicko |",
        ]))
        self.assertIn(
            f"demo/SKILL.md:6: light table restated. Link pstack-runtime/SKILL.md#modes instead of pasting it: {header}",
            findings,
        )

    def test_check_rejects_a_census_with_a_light_column(self):
        header = "| Census row | Full | Light | Kind |"
        findings = self.light_findings("demo/SKILL.md", f"{header}\n| --- | --- | --- | --- |")
        self.assertEqual(findings, [
            f"demo/SKILL.md:6: light table restated. Link pstack-runtime/SKILL.md#modes instead of pasting it: {header}",
        ])

    def test_check_rejects_light_mode_prose_without_the_runtime_link(self):
        findings = self.light_findings("demo/SKILL.md", "In light mode, launch at most 3 workers.")
        self.assertEqual(findings, [
            "demo/SKILL.md:6: names light mode without a link to the runtime's Modes section. "
            "Link pstack-runtime/SKILL.md#modes: In light mode, launch at most 3 workers.",
        ])

    def test_check_accepts_the_modes_pointer(self):
        pointer = (
            "[The runtime's Modes section](../pstack-runtime/SKILL.md#modes) sets the mode lines "
            "of every brief this skill writes and how its spawns run in light mode."
        )
        self.assertEqual(self.light_findings("demo/SKILL.md", pointer), [])

    def test_check_lets_the_runtime_hold_the_light_table(self):
        import check  # noqa: E402
        body = "\n".join([
            "Call `watch_pull_request`. Call `unwatch_pull_request`.",
            "",
            "| Spawn | Light behavior |",
            "| --- | --- |",
            "| Feature | Arena, Interrogate, Comment Sicko |",
            "",
            "In light mode, launch at most 3 workers.",
        ])
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "pstack-runtime"
            skill.mkdir()
            (skill / "SKILL.md").write_text(f"---\nname: pstack-runtime\ndescription: d\n---\n\n{body}\n")
            self.assertEqual(check.check_tree(directory), [])

    def test_check_ignores_waiver_names_outside_markdown(self):
        findings = self.light_findings("demo/scripts/x.py", 'ROW = ("Arena", "Interrogate", "Comment Sicko")\n')
        self.assertEqual(findings, [])

    def test_check_accepts_an_unrelated_playbook_table(self):
        import check  # noqa: E402
        with tempfile.TemporaryDirectory() as directory:
            skill = Path(directory) / "demo"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: demo\ndescription: d\n---\n\n| Playbook | Purpose |\n| --- | --- |\n| Feature | New behavior |\n"
            )
            self.assertEqual(check.check_tree(directory), [])
