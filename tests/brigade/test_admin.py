if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import (
    setUpModule,
    BrigadeTest,
    StoresTest,
    _AdminRace,
    json,
)
from .executive_admin.test_store_open import StoreOpenMixin
from .executive_admin.test_refs_and_rulings import RefsRulingsMixin
from .executive_admin.test_requests import RequestsMixin
from .executive_admin.test_sync import SyncMixin
from .executive_admin.test_recovery import RecoveryMixin
from .executive_admin.test_replacement_order import ReplacementOrderMixin


class AdminTest(StoresTest, StoreOpenMixin, RefsRulingsMixin, RequestsMixin, SyncMixin, RecoveryMixin, ReplacementOrderMixin, _AdminRace):
    """The executive admin at `app/.admin`, beside the coordinators on the same root."""

    assert_refused_choice = BrigadeTest.assert_refused_choice
    STAMP = "2026-10-06T00:00:00Z"

    def admin(self, *args, ok=True):
        return self.brigade(".admin", *args, ok=ok)

    def open_admin(self, *extra, ok=True):
        return self.brigade(".admin", "open", "--admin", "--project-root", str(self.project), *extra, ok=ok)

    def meta(self, name):
        return json.loads((self.dir(name) / "restaurant.json").read_text())

    def relays(self):
        """The admin's relay rows as (store@offset, the copied row)."""
        return [(row[2], json.loads(row[4])) for row in self.rows(".admin", "log.tsv") if row[1] == "relay"]

    def ruling_events(self):
        """Ruling log rows as (id, state, note)."""
        return [tuple(row[2:5]) for row in self.rows(".admin", "log.tsv") if row[1] == "ruling"]

    def cursor(self, name):
        return self.meta(".admin").get("cursors", {}).get(f"app/{name}", 0)

    def size(self, name, table="log.tsv"):
        return len((self.dir(name) / table).read_bytes())

    def finish(self, proc):
        out, err = proc.communicate(timeout=30)
        return proc.returncode, out.strip(), err.strip()

    def with_thread(self, thread="t1"):
        """docs, engine, and the admin, with the admin's thread recorded at generation 1."""
        self.open("docs")
        self.open("engine")
        self.open_admin()
        self.admin("set", "--thread", thread)

    def _ruling_section(self, *lines):
        return "\n".join(["## Rulings", "", *lines])
