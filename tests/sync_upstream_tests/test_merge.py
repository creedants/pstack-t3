if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

import hashlib
import json
import os
import stat
import unittest
from .support import BLOCK, CLASHING, FOOTER, MARKED, MARKERS, MERGED, NEW, NO_ROWS, OLD, OVERRIDDEN, PLUGIN, PORTED, MergeCase, commit, git, held, marker_lines, setUpModule, snapshot, write_tree

class MergeTest(MergeCase):
    def test_a_clean_merge_writes_the_merged_override_and_leaves_the_lock(self):
        locked = self.lock.read_bytes()
        lines = self.merge({"pstack/skills/alpha/SKILL.md": NEW})
        self.assertEqual(
            lines,
            [
                "clean     t3/overrides/alpha/SKILL.md  merged",
                "override drift:",
                "  alpha/SKILL.md: upstream changed since this override was written; review or re-port it, then --update-lock",
                "",
                *FOOTER,
            ],
        )
        self.assertEqual(self.override("alpha/SKILL.md"), MERGED.encode())
        self.assertEqual(self.override("alpha/both.md"), PORTED.encode())
        self.assertEqual(self.lock.read_bytes(), locked)
        self.assertEqual(self.vendored("alpha/SKILL.md"), NEW.encode())
        self.assertEqual(
            sorted(path.name for path in (self.root / "t3").iterdir()),
            ["added", "agents", "overrides", "overrides.lock.json", "removed.txt", "runtime.md", "scripts", "setup.md"],
        )

    def test_a_clean_merge_keeps_the_mode_of_the_override(self):
        (self.root / "t3/overrides/alpha/SKILL.md").chmod(0o755)
        self.merge({"pstack/skills/alpha/SKILL.md": NEW})
        self.assertEqual(self.override("alpha/SKILL.md"), MERGED.encode())
        self.assertEqual(stat.S_IMODE((self.root / "t3/overrides/alpha/SKILL.md").stat().st_mode), 0o755)

    def test_a_changed_persona_merges_and_names_the_t3_agents_file(self):
        locked = self.lock.read_bytes()
        lines = self.merge({"pstack/agents/persona.md": NEW})
        self.assertEqual(
            lines,
            [
                "clean     t3/agents/persona.md  merged",
                "override drift:",
                "  t3/agents/persona.md: upstream changed since this persona was written; review or re-port it, then --update-lock",
                "",
                *FOOTER,
            ],
        )
        self.assertEqual((self.root / "t3/agents/persona.md").read_bytes(), MERGED.encode())
        self.assertEqual((self.root / "vendor/pstack/agents/persona.md").read_bytes(), NEW.encode())
        self.assertEqual(self.lock.read_bytes(), locked)

    def test_a_persona_conflict_leaves_t3_byte_identical_and_names_the_persona(self):
        directory = self.directory / "conflicts"
        before = snapshot(self.root / "t3")
        lines = self.merge({"pstack/agents/persona.md": CLASHING}, "--conflicts", str(directory))
        saved = directory.resolve() / "agents/persona.md"
        self.assertEqual(
            lines[0],
            f"conflict  t3/agents/persona.md  left as is, re-port by hand, git's marked merge is in {saved}",
        )
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual(snapshot(self.root / "t3"), before)
        self.assertEqual(held(directory), {"agents": None, "agents/persona.md": MARKED.encode()})

    def test_a_t3_only_persona_gets_no_row_when_an_unrelated_upstream_file_changes(self):
        write_tree(self.root, {"t3/agents/t3-only.md": "t3 only\n"})
        lines = self.merge({"pstack/README.md": "edited\n"})
        self.assertEqual(lines[0], NO_ROWS)
        self.assertEqual([line for line in lines if "t3-only.md" in line], [])
        self.assertEqual((self.root / "t3/agents/t3-only.md").read_bytes(), b"t3 only\n")

    def test_a_t3_only_persona_whose_vendored_file_appears_gets_a_no_base_row(self):
        write_tree(self.root, {"t3/agents/t3-only.md": "t3 only\n"})
        lines = self.merge({"pstack/agents/t3-only.md": NEW})
        detail = "no old upstream text that matches the lock, left as is, re-port by hand"
        self.assertEqual(lines[0], f"no-base   t3/agents/t3-only.md  {detail}")
        self.assertEqual((self.root / "t3/agents/t3-only.md").read_bytes(), b"t3 only\n")

    def test_a_conflict_leaves_t3_byte_identical_and_names_the_override(self):
        before = snapshot(self.root / "t3")
        lines = self.merge({"pstack/skills/alpha/SKILL.md": CLASHING})
        self.assertEqual(lines[0], "conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand")
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual(self.override("alpha/SKILL.md"), PORTED.encode())
        self.assertEqual(snapshot(self.root / "t3"), before)
        self.assertEqual(self.vendored("alpha/SKILL.md"), CLASHING.encode())

    def test_no_file_under_t3_overrides_holds_a_conflict_marker_after_a_conflict_beside_a_clean_merge(self):
        lines = self.merge({"pstack/skills/alpha/SKILL.md": CLASHING, "pstack/skills/alpha/both.md": NEW})
        self.assertEqual(
            lines[:2],
            [
                "conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand",
                "clean     t3/overrides/alpha/both.md  merged",
            ],
        )
        overrides = self.root / "t3/overrides"
        held = {path.relative_to(overrides).as_posix(): path.read_bytes() for path in overrides.rglob("*") if path.is_file()}
        self.assertEqual(
            held,
            {"alpha/SKILL.md": PORTED.encode(), "alpha/both.md": MERGED.encode(), "gone/kept.md": PORTED.encode()},
        )
        for rel, data in held.items():
            for line in data.splitlines():
                with self.subTest(path=rel, line=line):
                    self.assertFalse(line.startswith((b"<<<<<<<", b">>>>>>>")) or line == b"=======")

    def test_an_upstream_marker_block_that_git_merges_cleanly_is_left_and_named_beside_a_conflict(self):
        before = snapshot(self.root / "t3")
        lines = self.merge({"pstack/skills/alpha/SKILL.md": CLASHING, "pstack/skills/alpha/both.md": NEW + BLOCK})
        self.assertEqual(
            lines[:2],
            [
                "conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand",
                f"markers   t3/overrides/alpha/both.md  {MARKERS}",
            ],
        )
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual([self.override(rel) for rel in OVERRIDDEN], [PORTED.encode()] * 3)
        self.assertEqual(snapshot(self.root / "t3"), before)
        self.assertEqual(self.vendored("alpha/both.md"), (NEW + BLOCK).encode())

    def test_a_dry_run_names_a_merge_result_that_holds_a_marker_line_and_writes_nothing(self):
        head = commit(
            self.upstream, {"pstack/skills/alpha/SKILL.md": CLASHING, "pstack/skills/alpha/both.md": NEW + BLOCK}
        )
        before = snapshot(self.root)
        result = self.sync("--merge", "--dry-run")
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        self.assertEqual(
            result.stdout.splitlines(),
            [
                f"vendor/pstack: {self.pinned[:12]} -> {head[:12]} (1.0.0), dry run, nothing written in the checkout",
                "conflict  t3/overrides/alpha/SKILL.md  left as is, re-port by hand",
                f"markers   t3/overrides/alpha/both.md  {MARKERS}",
                *FOOTER,
            ],
        )
        self.assertEqual(snapshot(self.root), before)

    def test_a_line_that_starts_with_any_of_the_three_marker_shapes_leaves_the_override_and_names_it(self):
        lines = self.merge(
            {
                "pstack/skills/alpha/SKILL.md": NEW + "<<<<<<< left\n",
                "pstack/skills/alpha/both.md": NEW + "||||||| base\n",
                "pstack/skills/gone/kept.md": NEW + ">>>>>>> right\n",
            }
        )
        self.assertEqual(lines[:3], [f"markers   t3/overrides/{rel}  {MARKERS}" for rel in OVERRIDDEN])
        self.assertEqual([self.override(rel) for rel in OVERRIDDEN], [PORTED.encode()] * 3)

    def test_a_line_of_seven_equals_signs_with_no_marker_line_merges(self):
        lines = self.merge({"pstack/skills/alpha/SKILL.md": NEW + "Heading\n=======\n"})
        self.assertEqual(lines[0], "clean     t3/overrides/alpha/SKILL.md  merged")
        self.assertEqual(self.override("alpha/SKILL.md"), (MERGED + "Heading\n=======\n").encode())

    def test_a_marker_line_the_override_already_held_leaves_the_override_and_names_it(self):
        held = PORTED.replace("two\n", "<<<<<<< kept\ntwo\n")
        (self.root / "t3/overrides/alpha/SKILL.md").write_text(held)
        lines = self.merge({"pstack/skills/alpha/SKILL.md": NEW})
        self.assertEqual(lines[0], f"markers   t3/overrides/alpha/SKILL.md  {MARKERS}")
        self.assertEqual(self.override("alpha/SKILL.md"), held.encode())

    def test_an_override_whose_upstream_file_was_deleted_is_left_and_named(self):
        lines = self.merge({"pstack/skills/gone/kept.md": None})
        self.assertEqual(lines[0], "deleted   t3/overrides/gone/kept.md  upstream deleted its file, left as is")
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual(self.override("gone/kept.md"), PORTED.encode())
        self.assertFalse((self.root / "vendor/pstack/skills/gone/kept.md").exists())

    def test_a_new_upstream_file_with_no_override_reaches_vendor_and_gets_no_row(self):
        lines = self.merge({"pstack/skills/beta/new.md": "added\n"})
        self.assertEqual(lines[0], NO_ROWS)
        self.assertEqual([line for line in lines if "beta/new.md" in line], [])
        self.assertEqual(self.vendored("beta/new.md"), b"added\n")

    def test_a_changed_file_that_ships_unchanged_reaches_vendor_and_gets_no_row(self):
        lines = self.merge({"pstack/skills/beta/SKILL.md": "edited\n"})
        self.assertEqual(lines[0], NO_ROWS)
        self.assertEqual([line for line in lines if "beta/SKILL.md" in line], [])
        self.assertEqual(self.vendored("beta/SKILL.md"), b"edited\n")

    def test_a_nul_byte_in_any_of_the_three_texts_leaves_the_override_and_names_it(self):
        nul = "one\0\ntwo\nthree\nfour\nfive\nsix\nseven\n"
        write_tree(self.root, {"t3/overrides/alpha/SKILL.md": nul, "vendor/pstack/skills/gone/kept.md": nul})
        self.write_lock()
        lines = self.merge(
            {"pstack/skills/alpha/SKILL.md": NEW, "pstack/skills/alpha/both.md": nul, "pstack/skills/gone/kept.md": NEW}
        )
        detail = "a NUL byte in one of the three texts, left as is, re-port by hand"
        self.assertEqual(
            lines[:3],
            [
                f"binary    t3/overrides/alpha/SKILL.md  {detail}",
                f"binary    t3/overrides/alpha/both.md  {detail}",
                f"binary    t3/overrides/gone/kept.md  {detail}",
            ],
        )
        self.assertEqual(
            [self.override(rel) for rel in OVERRIDDEN], [nul.encode(), PORTED.encode(), PORTED.encode()]
        )

    def test_a_latin_1_override_with_no_nul_byte_merges_bytewise(self):
        (self.root / "t3/overrides/alpha/SKILL.md").write_bytes(b"caf\xe9 t3\ntwo\nthree\nfour\nfive\nsix\nseven\n")
        lines = self.merge({"pstack/skills/alpha/SKILL.md": NEW})
        self.assertEqual(lines[0], "clean     t3/overrides/alpha/SKILL.md  merged")
        self.assertEqual(self.override("alpha/SKILL.md"), b"caf\xe9 t3\ntwo\nthree\nfour\nfive\nsix\nseven upstream\n")

    def test_old_upstream_text_that_is_absent_or_differs_from_the_lock_leaves_the_override_and_names_it(self):
        write_tree(
            self.root,
            {"vendor/pstack/skills/alpha/SKILL.md": "edited in vendor\n", "vendor/pstack/skills/alpha/both.md": None},
        )
        lines = self.merge({"pstack/skills/alpha/SKILL.md": NEW, "pstack/skills/alpha/both.md": NEW})
        detail = "no old upstream text that matches the lock, left as is, re-port by hand"
        self.assertEqual(
            lines[:2],
            [f"no-base   t3/overrides/alpha/SKILL.md  {detail}", f"no-base   t3/overrides/alpha/both.md  {detail}"],
        )
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual([self.override(rel) for rel in OVERRIDDEN], [PORTED.encode()] * 3)

    def test_a_merge_that_changes_nothing_leaves_the_override_and_says_so(self):
        (self.root / "t3/overrides/alpha/SKILL.md").write_text(MERGED)
        lines = self.merge({"pstack/skills/alpha/SKILL.md": NEW})
        self.assertEqual(lines[0], "same      t3/overrides/alpha/SKILL.md  the merge changes nothing, left as is")
        self.assertEqual(lines[-3:], FOOTER)
        self.assertEqual(self.override("alpha/SKILL.md"), MERGED.encode())

    def test_with_no_changed_override_prints_the_one_line_and_no_footer(self):
        lines = self.merge({"pstack/README.md": "edited\n"})
        self.assertEqual(lines[0], NO_ROWS)
        self.assertEqual([line for line in lines if line in FOOTER], [])

