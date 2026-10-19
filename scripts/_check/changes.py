import re
from pathlib import Path

from .siblings import _release

def _at_file_line(name, fault, offset):
    match = re.match(r"line (\d+)(.*)", fault)
    if match is None:
        return f"changes/{name} {fault}"
    return f"changes/{name} line {int(match.group(1)) + offset}{match.group(2)}"


def change_findings(changes: Path) -> list[str]:
    release = _release()
    directory = Path(changes)
    if not directory.is_dir():
        return []
    findings = []
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".md"):
            findings.append(f"changes/{entry.name} is not a fragment")
            continue
        try:
            text = entry.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            findings.append(f"changes/{entry.name} is not UTF-8")
            continue
        lines = text.split("\n")
        lines = lines[:-1] if lines and lines[-1] == "" else lines
        try:
            _kind, rest = release.take_kind(entry.name, lines)
        except SystemExit as error:
            findings.append(str(error.code).split("; ")[0])
            continue
        fault = release.bullet_fault(rest)
        if fault:
            findings.append(_at_file_line(entry.name, fault, len(lines) - len(rest)))
    return findings
