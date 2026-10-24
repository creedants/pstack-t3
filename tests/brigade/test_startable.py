if __package__ in (None, ""):
    import sys
    from pathlib import Path

    _repo_root = Path(__file__).resolve().parent
    while _repo_root != _repo_root.parent and not (_repo_root / "tests" / "isolation.py").is_file():
        _repo_root = _repo_root.parent
    sys.path.insert(0, str(_repo_root))
from tests import isolation

isolation.install()

from .support import BrigadeTest, setUpModule
from .startable_tickets.test_listing import ListingMixin
from .startable_tickets.test_low_reserve import LowReserveMixin
from .startable_tickets.test_spend_controls import SpendStandingMixin
from .startable_tickets.test_order import OrderAgreementMixin


class StartableTest(BrigadeTest, ListingMixin, LowReserveMixin, SpendStandingMixin, OrderAgreementMixin):
    def vendor_layout(self):
        self.init_landing("--generated", "skills,docs/cli,docs/skills.md", "--generated-map", ",".join([
            "t3/added/**=skills/**",
            "t3/overrides/**=skills/**",
            "t3/agents/**=skills/pstack-runtime/agents/**",
            "t3/runtime.md=skills/pstack-runtime/SKILL.md",
            "t3/scripts/roles.py=skills/pstack-runtime/scripts/roles.py",
            "t3/setup.md=skills/setup-pstack/SKILL.md",
            "t3/added/brigade/scripts/activity.py=docs/cli/activity.md!",
            "t3/added/brigade/scripts/brigade.py=docs/cli/brigade.md!",
            "t3/added/landing/scripts/land.py=docs/cli/land.md!",
            "t3/scripts/roles.py=docs/cli/roles.md!",
            "t3/runtime.md=docs/skills.md!^",
            "t3/setup.md=docs/skills.md!^",
            "t3/removed.txt=docs/skills.md!^",
            "t3=docs/skills.md!^",
            "t3/overrides=docs/skills.md!^",
            "t3/added=docs/skills.md!^",
            "vendor/pstack=docs/skills.md!^",
            "vendor/pstack/skills=docs/skills.md!^",
            "t3/overrides/*=docs/skills.md!^",
            "t3/added/*=docs/skills.md!^",
            "vendor/pstack/skills/*=docs/skills.md!^",
            "t3/overrides/*/SKILL.md=docs/skills.md!^",
            "t3/added/*/SKILL.md=docs/skills.md!^",
            "vendor/pstack/skills/*/SKILL.md=docs/skills.md!^",
        ]))

    def note_lease_fixture(self):
        """A lease on one activity path, one test path, and the next dish's default change note.

        The lease's test path and the ticket's first path are siblings under `tests/brigade/`.
        """
        self.git_project()
        self.vendor_layout()
        self.init_landing()
        self.landing_env()
        self.open()
        self.brigade("set", "--change-note-dir", "changes")
        self.land("lease", "claim", "--holder", "engine/D1",
                  "--paths", "t3/added/brigade/scripts/activity.py,tests/brigade/test_context_cap.py,changes/perf%2Fd1.md")
        self.filed("Fix the cap", "--paths", "tests/brigade/support.py,tests/brigade/test_startable.py")
