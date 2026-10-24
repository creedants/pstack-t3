if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

# Injected git attributes and configuration leave the applied count unchanged.
import os
import subprocess
from pathlib import Path
from unittest import mock

from ..support import BrigadeTest, SCRIPT, _owner_words, call, setUpModule
from . import cap_refusal


HEADER = "\x00BINARY\n"
OLD_ELEVEN = "\n".join(f"old line {number}" for number in range(11))
NEW_ELEVEN = "\n".join(f"new line {number}" if number == 5 else f"old line {number}" for number in range(11))
OLD_BIN_ELEVEN = HEADER + OLD_ELEVEN + "\n"
NEW_BIN_ELEVEN = HEADER + NEW_ELEVEN + "\n"
REPEATED_OLD = "\n".join(f'print("{char}")' for char in "caaabccc")
REPEATED_NEW = "\n".join(f'print("{char}")' for char in "ccbaacca")


def block(number, path, old, new):
    return (f"### Edit {number}\n\nFile: `{path}`\n\nOld text:\n\n```\n{old}\n```\n\n"
            f"New text:\n\n```\n{new}\n```\n")


def report(*blocks):
    return "Verdict: pass with edits\n\n## Edits\n\n" + "\n".join(blocks)


class EditsInjectedGitTest(BrigadeTest):
    def test_a_text_attribute_on_a_binary_path_leaves_the_applied_count_unchanged(self):
        reviewed = self.edits_store(**{"tools/blob.bin": OLD_BIN_ELEVEN, ".gitattributes": "tools/blob.bin text diff\n"})
        self.review_file("D1-review-1.md", report(block("1", "tools/blob.bin", "old line 5", "new line 5")))
        self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md"), "D1 sent-back")
        head = self.head_commit(self.SOURCE, **{"tools/blob.bin": NEW_BIN_ELEVEN,
                                                ".gitattributes": "tools/blob.bin text diff\n"})
        naive = subprocess.run(["git", "-C", str(self.project), "diff", "--numstat", "--no-renames",
                                reviewed, head, "--", "tools/blob.bin"],
                               capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(naive, "1\t1\ttools/blob.bin")
        with mock.patch.dict(os.environ, {"PSTACK_TEST_REAL_PROCESS": "1"}):
            self.assertEqual(self.verify(reviewed, head), (1, "", cap_refusal(24)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))

    def test_a_diff_algorithm_config_leaves_the_max_count_unchanged(self):
        reviewed = self.edits_store(**{f"tools/run{number}.py": REPEATED_OLD for number in range(3)})
        edits = [block(str(number + 1), f"tools/run{number}.py", REPEATED_OLD, REPEATED_NEW) for number in range(3)]
        self.review_file("D1-review-1.md", report(*edits))
        self.assertEqual(self.record_edits(reviewed, "--edits", "D1-review-1.md"), "D1 sent-back")
        head = self.head_commit(self.SOURCE, **{f"tools/run{number}.py": REPEATED_NEW for number in range(3)})
        home_dir = Path(self.temporary.name) / "injected-home"
        home_dir.mkdir(exist_ok=True)
        (home_dir / ".gitconfig").write_text("[diff]\n\talgorithm = myers\n")
        global_path = Path(self.temporary.name) / "injected-global-gitconfig"
        global_path.write_text("[diff]\n\talgorithm = myers\n")
        without_repo_config = dict(os.environ, HOME=str(home_dir))
        without_repo_config.pop("GIT_CONFIG_GLOBAL", None)
        without_repo_config.pop("GIT_CONFIG_SYSTEM", None)
        naive_home = subprocess.run(["git", "-C", str(self.project), "diff", "--numstat", "--no-renames",
                                     reviewed, head, "--", "tools/run0.py"],
                                    capture_output=True, text=True, check=True,
                                    env=without_repo_config).stdout.strip()
        self.assertEqual(naive_home, "3\t3\ttools/run0.py")
        with_global = dict(os.environ, HOME=str(home_dir), GIT_CONFIG_GLOBAL=str(global_path))
        naive_global = subprocess.run(["git", "-C", str(self.project), "diff", "--numstat", "--no-renames",
                                       reviewed, head, "--", "tools/run0.py"],
                                      capture_output=True, text=True, check=True,
                                      env=with_global).stdout.strip()
        self.assertEqual(naive_global, "3\t3\ttools/run0.py")
        subprocess.run(["git", "-C", str(self.project), "config", "diff.algorithm", "myers"], check=True)
        naive_repo = subprocess.run(["git", "-C", str(self.project), "diff", "--numstat", "--no-renames",
                                     reviewed, head, "--", "tools/run0.py"],
                                    capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(naive_repo, "3\t3\ttools/run0.py")
        with mock.patch.dict(os.environ, {"PSTACK_TEST_REAL_PROCESS": "1"}):
            outcome = call(self, SCRIPT, ["--store", str(self.store), "--at", str(self.at),
                                         *_owner_words(self.at), "edits", "verify", "D1",
                                         "--from", reviewed, "--to", head], env=with_global)
        self.assertEqual((outcome.code, outcome.stdout_text().strip(), outcome.stderr_text().strip()),
                         (1, "", cap_refusal(24)))
        self.assertEqual(self.edits_rows(), [])
        self.assertEqual(self.record_pass_at(head, ok=False), self.gate(reviewed, head))
