import json
from ..support import land

class MergeBase:

    def contract(self):
        with land.Store.for_repo(self.work) as store:
            return dict(store.contract)

    def age_absence(self, *idents):
        """Backdate these entries' absent markers past the grace."""
        with land.Store.for_repo(self.work) as store:
            with store.tx() as db:
                row = db.execute("SELECT value FROM contract WHERE key = 'absentDrain'").fetchone()
                raw = json.loads(row["value"]) if row else {}
                aged = land.stamp(land.now() - land.ABSENT_CHECKS_GRACE - land.timedelta(seconds=1))
                for ident in idents:
                    candidate = db.execute("SELECT candidate FROM entry WHERE id = ?", (ident,)).fetchone()["candidate"]
                    raw[str(ident)] = {"since": aged, "candidate": candidate}
                db.execute("INSERT OR REPLACE INTO contract VALUES ('absentDrain', ?)", (json.dumps(raw),))

    def assert_absent(self, *idents):
        """These entries hold absent markers for their stored candidates."""
        marker = self.absent_marker()
        self.assertEqual(set(marker), {str(ident) for ident in idents})
        for ident in idents:
            self.assertEqual(set(marker[str(ident)]), {"since", "candidate"})
            self.assertEqual(marker[str(ident)]["candidate"], self.entry_fields(ident, "candidate")[0])

    def open_pr_with_a_merge_requested(self):
        """One merge-mode entry noted merge requested by the queue, with its PR open.

        This one ages the absence before the second land."""
        (self.base / "required-checks").write_text("")
        self.init(mode="merge")
        self.queue_one()
        self.land("land")
        self.age_absence(1)
        self.assertEqual(self.land("land"), "nothing to land")
        self.assertIn("merge requested by the queue", self.land("status", "E1"))
