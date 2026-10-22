from .support import (
    setUpModule,
    BrigadeTest,
    CLAUDE,
    CODEX,
    Path,
    ROOT,
    ProcessReason,
    SCRIPT,
    _owner_words,
    real_popen,
    _shown_root,
    _wait_for_path,
    json,
    os,
    runpy,
    subprocess,
    sys,
    time,
)
from .worker_briefs.test_fields_and_contract import FieldsContractMixin
from .worker_briefs.test_fire_leases import FireLeasesMixin
from .worker_briefs.test_mode_and_rounds import ModeRoundsMixin
from .worker_briefs.test_standing_rules import StandingRulesMixin
from .worker_briefs.test_verify_command import VerifyCommandMixin
from .worker_briefs.test_wait_and_threads import WaitThreadsMixin


class BriefsTest(
    BrigadeTest,
    StandingRulesMixin,
    FieldsContractMixin,
    FireLeasesMixin,
    WaitThreadsMixin,
    ModeRoundsMixin,
    VerifyCommandMixin,
):
    LAYOUT = {
        "always": "tests.test_pstack_t3",
        "workflowModule": "tests.test_run_tests",
        "entryPoints": ["t3/added/brigade/scripts/brigade.py", "t3/added/landing/scripts/land.py"],
        "helpers": ["tests/brigade/support.py", "tests/landing/support.py"],
        "suite": "python3 scripts/run_tests.py -j 8",
        "python": "/usr/local/bin/python3.10",
    }

    def record_layout(self, value=None):
        """Write affectedTests into this store's restaurant.json, using the pstack-t3 layout by default."""
        meta = json.loads((self.at / "restaurant.json").read_text())
        meta["affectedTests"] = self.LAYOUT if value is None else value
        (self.at / "restaurant.json").write_text(json.dumps(meta))
