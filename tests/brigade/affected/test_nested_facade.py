from ..support import setUpModule
from tests.brigade.test_affected import AffectedBase

MESSAGE = "a distinctive facade message that is deliberately longer than forty chars"


class AffectedNestedFacadeTest(AffectedBase):
    def facade_modules(self, path):
        """The module names the picker's output names, once `No affected mapping` is out."""
        out = self.affected("--paths", path)
        self.assertNotIn("No affected mapping", out)
        head = out.index("Affected test modules are ") + len("Affected test modules are ")
        tail = out.index("Run them in the foreground")
        return frozenset(part.strip().strip("`.") for part in out[head:tail].split(","))

    def facade_repo(self):
        self.git_project()
        self.write("src/__init__.py", "")
        self.write("src/governor.py",
                   "from ._governor.deep.admission import slot\n"
                   "MESSAGE = \"" + MESSAGE + "\"\n")
        self.write("src/_governor/__init__.py", "")
        self.write("src/_governor/deep/__init__.py", "")
        self.write("src/_governor/deep/admission.py", "slot = 1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/runner.py",
                   "import importlib\n"
                   "def message():\n"
                   "    return importlib.import_module(\"src.governor\").MESSAGE\n")
        self.write("tests/test_facade.py",
                   "from .runner import message\n"
                   "def test_f():\n"
                   "    assert message() == \"" + MESSAGE + "\"\n")
        self.write("tests/test_member.py",
                   "from src._governor.deep.admission import slot\n"
                   "def test_m():\n"
                   "    assert slot == 1\n")
        self.track()

    def nested_facade_repo(self):
        self.git_project()
        self.write("src/__init__.py", "")
        self.write("src/governor.py",
                   "from ._governor.deep import slot\n"
                   "MESSAGE = \"" + MESSAGE + "\"\n")
        self.write("src/_governor/__init__.py", "")
        self.write("src/_governor/deep.py", "from ._deep.admission import slot\n")
        self.write("src/_governor/_deep/__init__.py", "")
        self.write("src/_governor/_deep/admission.py", "slot = 1\n")
        self.write("tests/__init__.py", "")
        self.write("tests/runner.py",
                   "import importlib\n"
                   "def message():\n"
                   "    return importlib.import_module(\"src.governor\").MESSAGE\n")
        self.write("tests/test_facade.py",
                   "from .runner import message\n"
                   "def test_f():\n"
                   "    assert message() == \"" + MESSAGE + "\"\n")
        self.write("tests/test_member.py",
                   "from src._governor._deep.admission import slot\n"
                   "def test_m():\n"
                   "    assert slot == 1\n")
        self.write("tests/test_nested.py",
                   "from src._governor.deep import slot\n"
                   "def test_n():\n"
                   "    assert slot == 1\n")
        self.track()

    def test_member_below_a_subpackage_gains_the_facade_tests_fails_when_the_union_follows_only_the_immediate_private_directory(self):
        self.facade_repo()
        self.assertEqual(self.facade_modules("src/_governor/deep/admission.py"),
                         {"tests.test_facade", "tests.test_member", "tests.test_pstack_t3"})

    def test_member_gains_facade_tests_that_arrive_after_its_visit_fails_when_the_union_reads_the_facade_tests_once(self):
        self.nested_facade_repo()
        self.assertEqual(self.facade_modules("src/_governor/_deep/admission.py"),
                         {"tests.test_facade", "tests.test_member", "tests.test_nested", "tests.test_pstack_t3"})
