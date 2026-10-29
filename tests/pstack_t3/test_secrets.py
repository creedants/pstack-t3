if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import unittest

from tests.pstack_t3.support import runtime_doc_section, runtime_section


class SecretFileDeliveryDocTest(unittest.TestCase):
    def test_on_demand_table_sends_a_shell_secret_to_the_delivery_rule(self):
        table = runtime_section("## On-demand files")
        self.assertIn(
            "| [secrets.md](secrets.md) | before you ask the user for a secret, "
            "or before a shell command needs a token, deploy key, or password |",
            table,
        )

    def test_delivery_section_sends_a_shell_consumer_to_a_file_and_a_t3_tool_to_secret_ref(self):
        section = runtime_doc_section("secrets.md", "## Delivery")
        for phrase in (
            '`request_secret` with `delivery: "file"`',
            "takes the default delivery",
            "`schedule_task`",
            "`clientRequestId`",
        ):
            self.assertIn(phrase, section)

    def test_read_section_keeps_the_value_inside_the_command_and_bans_echo_cat_set_x_and_export(self):
        section = runtime_doc_section("secrets.md", "## Read it inside the command")
        for phrase in (
            'TOKEN="$(cat <file>)" <cli>',
            "No `echo`.",
            "No `cat` on its own line",
            "No `set -x`.",
            "No export to a long-lived environment.",
            "rm -f <file>",
            "when the host allows it",
            "24 hours",
        ):
            self.assertIn(phrase, section)

    def test_never_section_keeps_the_secret_out_of_chat_and_the_repository(self):
        section = runtime_doc_section("secrets.md", "## Never")
        for phrase in (
            "Never ask for the secret in chat, and never type the value.",
            "Never print, copy, log, or commit the file or its contents.",
        ):
            self.assertIn(phrase, section)

    def test_card_section_says_a_child_cannot_answer_its_own_card(self):
        section = runtime_doc_section("secrets.md", "## Who answers the card")
        for phrase in (
            "calls `request_secret` on its own thread",
            "A child cannot answer its own card.",
            "A parent cannot fill it either",
            "`t3_pending_request` reads questions, not secret cards",
        ):
            self.assertIn(phrase, section)

    def test_host_section_names_the_floor_and_the_fallback_out_of_chat(self):
        section = runtime_doc_section("secrets.md", "## Host without file delivery")
        for phrase in (
            'T3 Code 0.1.3 added `delivery: "file"`',
            "place the secret in a file themselves",
            "Never ask for the value in chat.",
            "delete it only when the user allows it",
        ):
            self.assertIn(phrase, section)
