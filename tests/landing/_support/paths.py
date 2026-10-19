from datetime import datetime, timedelta, timezone
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "t3/added/landing/scripts/land.py"
REVIEWER = "codex/gpt-6.1-sol"
ADMIN = ("--owner", ".admin/@1")


def stamp_in(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def repository_state(repo):
    """Config bytes, HEAD, and every ref of a repository."""
    refs = subprocess.run(["git", "for-each-ref", "--format=%(refname) %(objectname)"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout
    return (repo / ".git/config").read_bytes(), (repo / ".git/HEAD").read_text(), refs
