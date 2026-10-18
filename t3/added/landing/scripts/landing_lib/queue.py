"""Re-exports names from _queue. Patch a name in the _queue module that reads it. Patching it here does not reach callers."""
from ._queue.integration import (cherry_pick_stopped_for_conflict, _tail, Integration)
from ._queue.entries import (submit, remote_trunk, publish, pause, mark_paused, forget_absent, settle_landed, settle_bounced, reconcile, rewound, attempt)
from ._queue.prs import (gh, Outcome, build, build_line, human_branch, publish_absent_branch, pr_adoptable_url, ensure_pr)

__all__ = ['submit', 'cherry_pick_stopped_for_conflict', '_tail', 'Integration', 'remote_trunk', 'publish', 'pause', 'mark_paused', 'forget_absent', 'settle_landed', 'settle_bounced', 'reconcile', 'rewound', 'attempt', 'gh', 'Outcome', 'build', 'build_line', 'human_branch', 'publish_absent_branch', 'pr_adoptable_url', 'ensure_pr']
