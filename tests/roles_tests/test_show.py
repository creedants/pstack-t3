import json
import unittest
from .support import INHERIT_ENTRY, setUpModule, show


class RolesCliTest(unittest.TestCase):
    def test_one_role_resolves_that_role(self):
        completed = show("bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)

    def test_two_roles_resolve_both(self):
        completed = show("bug-fix", "judgment and prose")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix", "judgment and prose"])
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)
        self.assertEqual(payload["roles"]["judgment and prose"], INHERIT_ENTRY)

    def test_repeated_identical_role_resolves_once(self):
        completed = show("bug-fix", "bug-fix")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(list(payload["roles"]), ["bug-fix"])
        self.assertEqual(payload["roles"]["bug-fix"], INHERIT_ENTRY)
