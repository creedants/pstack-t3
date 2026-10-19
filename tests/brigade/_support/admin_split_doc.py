import re
import unittest
from pathlib import Path

from .names import BRIGADE_DOCS, ROOT, brigade_docs


class BrigadeAdminSplitDocChecks:
    def setUp(self):
        self.docs = {name: (ROOT / "skills/brigade" / name).read_text() for name in BRIGADE_DOCS}
        self.brigade = brigade_docs()
        self.admin = (ROOT / "skills/brigade-admin/SKILL.md").read_text()

    @classmethod
    def headings(cls, text, prefix="#"):
        from tests.test_pstack_t3 import unfenced_lines
        return [line for line in unfenced_lines(text) if line.startswith(prefix)]

    @classmethod
    def slugs(cls, text):
        from tests.test_pstack_t3 import heading_slug
        return {heading_slug(line.lstrip("#").strip()) for line in cls.headings(text)}

    def test_admin_headings_are_in_brigade_admin_and_not_in_brigade(self):
        admin = self.headings(self.admin, "## ")
        brigade = [line.lstrip("#").strip() for line in self.headings(self.brigade)]
        for heading in (
            "Open an executive admin",
            "Admin first service",
            "Admin service",
            "Admin messages",
            "Rulings",
            "What the admin never does",
            "Admin recovery and retirement",
            "What the user hears from the admin",
        ):
            with self.subTest(heading=heading):
                self.assertIn(f"## {heading}", admin)
                self.assertNotIn(heading, brigade)

    def test_no_line_of_40_characters_is_in_both_skills_but_the_runtime_pointer_and_the_pin_step(self):
        def long_lines(text):
            return {line for line in text.splitlines() if len(line) >= 40}

        self.assertEqual(
            long_lines(self.brigade) & long_lines(self.admin),
            {
                "Read [the pstack-t3 runtime](../pstack-runtime/SKILL.md) before spawning workers, "
                "choosing models, scheduling, or isolating work. It maps those steps onto T3's orchestrator tools.",
                '1. `t3_thread_organize` with `action: "pin"` and no `threadId`, acting on the caller on purpose.',
            },
        )

    def test_reporting_section_links_brigade_admin(self):
        section = self.docs["SKILL.md"].split("\n## Reporting to an executive admin\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("(../brigade-admin/SKILL.md)", section)

    def test_links_between_the_two_skills_and_in_file_links_in_both_name_a_heading(self):
        cross = re.findall(r"\(\.\./brigade/SKILL\.md#([^)\s]+)\)", self.admin)
        self.assertIn("reporting-to-an-executive-admin", cross)
        self.assertEqual([fragment for fragment in cross if fragment not in self.slugs(self.docs["SKILL.md"])], [])
        back = re.findall(r"\(\.\./brigade-admin/SKILL\.md#([^)\s]+)\)", self.brigade)
        self.assertIn("rulings", back)
        self.assertEqual([fragment for fragment in back if fragment not in self.slugs(self.admin)], [])
        for name, text in (("brigade", self.docs["SKILL.md"]), ("brigade-admin", self.admin)):
            with self.subTest(skill=name):
                own = re.findall(r"\]\(#([^)\s]+)\)", text)
                self.assertIn("filing-tracker-work" if name == "brigade" else "rulings", own)
                self.assertEqual([fragment for fragment in own if fragment not in self.slugs(text)], [])
        self.assert_split_doc_links(self.docs)

    def assert_split_doc_links(self, docs):
        from tests.test_pstack_t3 import heading_slug, unfenced_lines
        slugs = {}
        for name, text in docs.items():
            slugs[name] = {heading_slug(line.lstrip("#").strip()) for line in unfenced_lines(text) if line.startswith("#")}
        for name, text in docs.items():
            unfenced = "\n".join(unfenced_lines(text))
            for fragment in re.findall(r"\]\(#([^)\s]+)\)", unfenced):
                self.assertIn(fragment, slugs[name], f"{name}#{fragment}")
            for fragment in re.findall(r"\]\(SKILL\.md#([^)\s]+)\)", unfenced):
                self.assertIn(fragment, slugs["SKILL.md"], f"{name} SKILL.md#{fragment}")
            for fragment in re.findall(r"\]\(admin-reporting\.md#([^)\s]+)\)", unfenced):
                self.assertIn(fragment, slugs["admin-reporting.md"], f"{name} admin-reporting.md#{fragment}")
        skill_links = re.findall(r"\]\(SKILL\.md#([^)\s]+)\)", "\n".join(unfenced_lines(docs["admin-reporting.md"])))
        self.assertGreater(len(skill_links), 0)
        admin_links = re.findall(r"\]\(admin-reporting\.md#([^)\s]+)\)", "\n".join(unfenced_lines(docs["SKILL.md"])))
        self.assertGreater(len(admin_links), 0)

    def test_moved_skill_link_missing_fragment_fails(self):
        docs = dict(self.docs)
        mutated = docs["admin-reporting.md"].replace(
            "(SKILL.md#reporting-to-an-executive-admin)", "(SKILL.md#missing-review-probe)", 1
        )
        self.assertIn("(SKILL.md#missing-review-probe)", mutated)
        docs["admin-reporting.md"] = mutated
        with self.assertRaises(AssertionError):
            self.assert_split_doc_links(docs)

    def test_a_section_of_the_other_skill_is_named_only_as_a_link(self):
        from tests.test_pstack_t3 import heading_slug
        # "Terms", "The script", and "Run a service" also occur as plain words, so they are only required once.
        for section in ("Terms", "The script", "Run a service"):
            with self.subTest(section=section):
                self.assertIn(f"[{section}](../brigade/SKILL.md#{heading_slug(section)})", self.admin)
        for text, target, sections in (
            (
                self.admin,
                "../brigade/SKILL.md",
                (
                    "Open a restaurant",
                    "First service",
                    "Liveness check",
                    "Git and PR housekeeping",
                    "Filing tracker work",
                    "Digest messages",
                    "Reporting to an executive admin",
                ),
            ),
            (
                self.brigade,
                "../brigade-admin/SKILL.md",
                ("Admin service", "Admin messages", "Rulings", "What the admin never does"),
            ),
        ):
            for section in sections:
                with self.subTest(section=section):
                    link = f"[{section}]({target}#{heading_slug(section)})"
                    self.assertGreater(text.count(link), 0)
                    self.assertEqual(text.count(section), text.count(link))

    def test_a_rule_the_coordinator_and_the_admin_share_is_stated_in_one_skill(self):
        skills = {"brigade": self.brigade, "brigade-admin": self.admin}
        for phrase, owner in (
            ("`failed`, `drained`, `report`, `reply`, `contest`, and `appeal`", "brigade"),
            ('with `t3_thread_send` and mode `"queue"`', "brigade"),
            ("until an entry of the first holder lands", "brigade"),
            ("starts no new work on those paths", "brigade"),
            ("it holds and sends `appeal`", "brigade"),
            ("refuses to drop a source while a `waiting` or `assigned` ticket came from it", "brigade"),
            ("The ref stays unchanged through every `ticket move`", "brigade"),
            ("The check runs under the store lock", "brigade"),
            ("A message-only line never appears in `sync`", "brigade-admin"),
            ("Every other holder's claim on those paths is refused", "brigade-admin"),
            ("Direct a coordinator's own work", "brigade-admin"),
            ("An item the other depends on, as a `contest` line stated, lands first", "brigade-admin"),
            ("when no contest covers the two holders", "brigade-admin"),
            ("`ticket <restaurant>: run ticket take`", "brigade"),
            ("`from-user <restaurant>: <the user's words>`", "brigade"),
            ("`answer <restaurant> Q<n>: <answer>`", "brigade"),
            ("`reports-to <restaurant> <thread>`", "brigade"),
            ("`ruling <restaurant> R<n>: <decision>`", "brigade"),
            ("`$B status` prints `thread <id>` first and `owner <thread>@<generation>` last", "brigade"),
            ("End the message with one line that names that path", "brigade"),
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual([name for name, text in skills.items() if phrase in text], [owner])
        for phrase in (
            "`sync` never prints them",
            "The admin's `sync` relays",
            "It never directs this restaurant's own work",
            "and the admin escalates",
            "The admin then rules `dependency`",
            "A reservation on the paths refuses",
            "The admin publishes each request",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.brigade)
        for phrase in (
            "The message-only lines, ",
            'with mode `"queue"`',
            "`land` then holds the other holder's entries",
            "keeps the holder from starting new work",
            "the coordinator holds and has not complied",
            "A ruling binds the coordinators",
            "That is refused while it still has waiting or assigned tickets",
            "`ticket move` in step 4 keeps that ref",
            "Every `brigade.py` write checks the owner token under the store lock",
            "The coordinator's store decides whether",
            "no store row keeps it",
            "It prints `thread <id>` first",
            "asks the holder to move it",
            "End with one line naming the full update",
        ):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.admin)

    def test_admin_names_each_request_line_and_links_brigade_for_its_text(self):
        # Any placeholder counts, so a line written with `<coordinator>` is still a second copy of the format.
        self.assertEqual(re.findall(r"`(?:ticket|from-user|answer|reports-to|ruling) <[^`]*`", self.admin), [])
        messages = self.admin.split("\n## Admin messages\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn(
            "The exact text of each line is in [Reporting to an executive admin]"
            "(../brigade/SKILL.md#reporting-to-an-executive-admin), under Requests.",
            messages,
        )
        self.assertIn(
            "This skill's `<coordinator>`, the directory name of the coordinator the line goes to, "
            "fills the `<restaurant>` field.",
            messages,
        )
        rows = [line.split(" | ", 1)[0] for line in messages.splitlines() if line.startswith("| `")]
        self.assertEqual(rows, ["| `ticket`", "| `from-user`", "| `answer`", "| `reports-to`", "| `ruling`"])
        requests = self.brigade.split("\n**Requests.**", 1)[1].split("\n**A ruling's side.**", 1)[0]
        for line in (
            "ticket <restaurant>: run ticket take",
            "from-user <restaurant>: <the user's words>",
            "answer <restaurant> Q<n>: <answer>",
            "reports-to <restaurant> <thread>",
            "ruling <restaurant> R<n>: <decision>",
        ):
            with self.subTest(line=line):
                self.assertEqual(requests.count(f"`{line}`"), 1)

    def test_admin_links_brigades_script_and_ships_none(self):
        self.assertIn("(../brigade/scripts/brigade.py)", self.admin)
        self.assertTrue((ROOT / "skills/brigade/scripts/brigade.py").is_file())
        self.assertFalse((ROOT / "skills/brigade-admin/scripts").exists())

    def test_brigade_description_sends_the_admin_trigger_to_brigade_admin(self):
        description = next(line for line in self.brigade.splitlines() if line.startswith("description:"))
        self.assertIn(
            "For an executive admin over the coordinators on one repository, use brigade-admin.", description
        )
        self.assertNotIn("an executive admin over the coordinators on one repository'", description)

    def test_neither_description_has_a_mid_sentence_colon(self):
        for name, text in (("brigade", self.brigade), ("brigade-admin", self.admin)):
            with self.subTest(skill=name):
                description = next(line for line in text.splitlines() if line.startswith("description:"))
                self.assertNotIn(": ", description.removeprefix("description: "))
                self.assertIn(". Use for '", description)

    def test_catalog_lists_brigade_admin_under_plan_and_run_long_work(self):
        import catalog
        group = catalog.render(ROOT / "skills").split("\n## Plan and run long work\n", 1)[1].split("\n## ", 1)[0]
        self.assertIn("| [`brigade-admin`](../skills/brigade-admin/SKILL.md) |", group)
