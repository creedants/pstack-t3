if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from .support import (
    setUpModule,
    check,
)


def link_findings(*targets):
    body = "\n".join(f"[link]({target})" for target in targets)
    with tempfile.TemporaryDirectory() as directory:
        skill = Path(directory) / "demo"
        skill.mkdir()
        (skill / "other.md").write_text("other\n")
        (skill / "SKILL.md").write_text(f"---\nname: demo\ndescription: d\n---\n\n{body}\n")
        return [finding for finding in check.check_tree(directory) if "broken link" in finding]


def fragment_tree_findings(paths, vendor=()):
    """Write a temporary skills tree from tree-relative path to text and return the fragment check's findings."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for rel, text in paths.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        vendored = root / "vendor/pstack/skills"
        vendored.mkdir(parents=True)
        for name in vendor:
            (vendored / name).mkdir()
        with mock.patch.object(check, "VENDOR_SKILLS_PATH", vendored):
            return check.fragment_findings(root)


class LinkCheckTest(unittest.TestCase):
    def test_a_thread_link_with_an_id_is_not_a_file_link(self):
        self.assertEqual(link_findings("t3-thread://v1/abc-123", "other.md"), [])
        self.assertEqual(link_findings("t3-thread://v2/abc-123/def-456", "other.md"), [])

    def test_a_query_bearing_file_link_to_an_existing_file_is_not_broken(self):
        self.assertEqual(link_findings("other.md?raw=1"), [])
        self.assertEqual(link_findings("other.md?raw=1#x"), [])

    def test_other_targets_still_must_resolve(self):
        self.assertEqual(
            link_findings("missing.md", "t3-thread://v1/"),
            [
                "demo/SKILL.md: broken link missing.md",
                "demo/SKILL.md: broken link t3-thread://v1/",
            ],
        )



class FragmentCheckTest(unittest.TestCase):
    def test_a_fragment_link_to_a_missing_skill_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "## Terms\n",
                    "brigade/verdict-protocol.md": "[the script](SKILL.md#the-script)\n",
                }
            ),
            ["brigade/verdict-protocol.md:1: fragment #the-script names no heading in brigade/SKILL.md"],
        )

    def test_a_fragment_link_to_a_present_skill_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "## Terms\n",
                    "brigade/verdict-protocol.md": "[the terms](SKILL.md#terms)\n",
                }
            ),
            [],
        )

    def test_a_fragment_link_to_a_missing_on_demand_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[the protocol](verdict-protocol.md#verdict)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            ["brigade/SKILL.md:1: fragment #verdict names no heading in brigade/verdict-protocol.md"],
        )

    def test_a_fragment_link_to_a_present_on_demand_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[the protocol](verdict-protocol.md#rule)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            [],
        )

    def test_an_in_file_fragment_link_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Modes\n[the roles](#roles)\n"}),
            ["pstack-runtime/SKILL.md:2: fragment #roles names no heading in pstack-runtime/SKILL.md"],
        )

    def test_an_in_file_fragment_link_to_a_present_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Modes\n[the modes](#modes)\n"}),
            [],
        )

    def test_a_fragment_link_into_a_vendor_skill_is_not_checked(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[the persona](../poteto-mode/agents/poteto-agent.md#missing-heading)\n",
                    "poteto-mode/agents/poteto-agent.md": "## Persona\n",
                },
                vendor=("poteto-mode",),
            ),
            [],
        )

    def test_a_fragment_link_inside_a_fence_is_not_checked(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "## Terms\n\n```\n[the rule](verdict-protocol.md#the-script)\n```\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            [],
        )

    def test_a_tilde_fence_hides_a_missing_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "~~~md\n[example](#missing)\n~~~\n"}),
            [],
        )

    def test_a_longer_fence_hides_inner_fence_markers(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "````md\n```md\n[example](#missing)\n```\n````\n"}),
            [],
        )

    def test_a_heading_inside_a_tilde_fence_does_not_count(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "~~~md\n## Missing\n~~~\n[bad](#missing)\n"}),
            ["brigade/SKILL.md:4: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_fragment_link_inside_a_code_span_is_not_checked(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "Use `[example](#missing)` as syntax.\n"}),
            [],
        )

    def test_a_fence_inside_a_blockquote_hides_its_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> ```md\n> [example](#missing)\n> ```\n"}),
            [],
        )

    def test_a_fence_inside_a_list_item_hides_its_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "1. Example\n\n    ```md\n    [example](#missing)\n    ```\n"}),
            [],
        )

    def test_a_fence_ends_at_the_next_list_item(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "1. Example\n\n   ```md\n   sample\n2. [bad](#missing)\n"}),
            ["brigade/SKILL.md:5: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_code_span_with_inner_backticks_leaves_a_later_link_visible(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "Use `` ``` `` to fence [bad](#missing), then `x`.\n"}),
            ["brigade/SKILL.md:1: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_link_title_with_an_escaped_quote_is_checked(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": '[bad](#missing "say \\"hi\\"")\n'}),
            ["brigade/SKILL.md:1: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_titled_fragment_link_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": '## Terms\n[bad](#missing "title")\n'}),
            ["pstack-runtime/SKILL.md:2: fragment #missing names no heading in pstack-runtime/SKILL.md"],
        )

    def test_a_fragment_link_to_a_second_duplicate_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Terms\n## Terms\n[second](#terms-1)\n"}),
            [],
        )

    def test_a_suffix_collision_allocates_the_next_free_anchor(self):
        self.assertEqual(
            fragment_tree_findings({"pstack-runtime/SKILL.md": "## Terms\n## Terms-1\n## Terms\n[third](#terms-2)\n"}),
            [],
        )

    def test_a_percent_encoded_fragment_to_a_present_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "## Caf\u00e9\n[ok](#caf%C3%A9)\n"}),
            [],
        )

    def test_a_percent_encoded_fragment_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "## Terms\n[bad](#caf%C3%A9)\n"}),
            ["brigade/SKILL.md:2: fragment #caf\u00e9 names no heading in brigade/SKILL.md"],
        )

    def test_a_query_bearing_destination_to_a_present_heading_passes(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[ok](verdict-protocol.md?raw=1#rule)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            [],
        )

    def test_a_query_bearing_destination_to_a_missing_heading_is_reported(self):
        self.assertEqual(
            fragment_tree_findings(
                {
                    "brigade/SKILL.md": "[bad](verdict-protocol.md?raw=1#missing)\n",
                    "brigade/verdict-protocol.md": "## Rule\n",
                }
            ),
            ["brigade/SKILL.md:1: fragment #missing names no heading in brigade/verdict-protocol.md"],
        )

    def test_a_quote_marker_inside_a_quoted_fence_stays_in_code(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> ```md\n> > ```\n> [example](#missing)\n> ```\n"}),
            [],
        )

    def test_a_quoted_fence_ends_before_a_reported_link(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> ```md\n> > ```\n> ```\n> [bad](#missing)\n"}),
            ["brigade/SKILL.md:4: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_quote_marker_inside_a_fenced_list_item_stays_in_code(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- ```md\n  > ```\n  [example](#missing)\n  ```\n"}),
            [],
        )

    def test_a_link_outside_a_fenced_list_item_is_still_reported(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- ```md\n>   [bad](#missing)\n"}),
            ["brigade/SKILL.md:2: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_document_fence_after_a_quoted_list_is_not_a_list_fence(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> - item\n\n  ```md\n[example](#missing)\n  ```\n"}),
            [],
        )

    def test_a_list_indent_does_not_reach_into_a_later_quote(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- item\n>   ```md\n> [example](#missing)\n> ```\n"}),
            [],
        )

    def test_an_indented_heading_in_a_quoted_code_block_is_not_a_heading(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- item\n>     ## Missing\n[bad](#missing)\n"}),
            ["brigade/SKILL.md:3: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_link_in_a_nested_quote_after_a_blank_quote_line_is_reported(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "> > ```md\n>\n> > [bad](#missing)\n"}),
            ["brigade/SKILL.md:3: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_a_fence_in_a_parent_list_ends_at_the_parent_boundary(self):
        self.assertEqual(
            fragment_tree_findings({"brigade/SKILL.md": "- - item\n\n  ```md\n[bad](#missing)\n  ```\n"}),
            ["brigade/SKILL.md:4: fragment #missing names no heading in brigade/SKILL.md"],
        )

    def test_check_tree_reports_a_fragment_that_names_no_heading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "brigade"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: brigade\ndescription: d\n---\n\n## Terms\n\n[the rule](verdict-protocol.md#the-script)\n"
            )
            (skill / "verdict-protocol.md").write_text("## Rule\n")
            self.assertIn(
                "brigade/SKILL.md:8: fragment #the-script names no heading in brigade/verdict-protocol.md",
                check.check_tree(root),
            )
