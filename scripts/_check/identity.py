import dataclasses
import os
import pwd
import re
import socket
from pathlib import Path

REQUIRED_SCAN = (
    "ops/t3-archive-threads.py",
    "ops/config.example",
    "docs/operations.md",
)


@dataclasses.dataclass(frozen=True)
class Identity:
    """home, user, and host compared as a path or an address in ops and the operations doc."""

    home: str
    user: str
    host: str

def _machine_value_hits(text, identity):
    name, word_body = "A-Za-z0-9._-", "A-Za-z0-9_"
    word = word_body + "-"
    home, user, first_host_label = (re.escape(value) for value in (identity.home, identity.user, identity.host.split(".")[0]))
    patterns = []
    if len(identity.home) > 1:
        as_a_whole_path_prefix = rf"(?<![{name}]){home}(?![{name}])"
        patterns.append(("home directory", as_a_whole_path_prefix, 0))
    if len(identity.user) > 1:
        after_a_tilde = rf"(?<=~){user}(?![{name}])"
        before_an_at_sign = rf"(?<![{name}]){user}(?=@)"
        after_a_name_and_a_slash = rf"(?<=[{name}]/){user}(?![{name}])"
        as_the_only_absolute_segment = rf"(?<![{name}])/{user}(?![{name}])(?!/)"
        patterns.append(("user name", "|".join((
            after_a_tilde, before_an_at_sign, after_a_name_and_a_slash, as_the_only_absolute_segment,
        )), 0))
    if len(identity.host.split(".")[0]) > 1:
        after_an_at_sign_or_two_slashes = rf"(?:(?<=@)|(?<=//)){first_host_label}(?![{word}])"
        before_a_port = rf"(?<![{word}]){first_host_label}(?=:[0-9])"
        before_a_domain = rf'(?<![{word_body}/$"-]){first_host_label}(?=\.[A-Za-z0-9])'
        host_forms = [after_an_at_sign_or_two_slashes, before_a_port, before_a_domain]
        if len(identity.host.split(".")) > 1:
            as_the_whole_host = rf'(?<![{word_body}$-]){re.escape(identity.host)}(?![{word}])'
            host_forms.append(as_the_whole_host)
        patterns.append(("host name", "|".join(host_forms), re.IGNORECASE))
    return [(kind, number, line) for number, line in enumerate(text.splitlines(), 1)
            for kind, pattern, flags in patterns if re.search(pattern, line, flags)]


def machine_value_findings(ops_dir, operations_doc, identity):
    """Report the given identity's home directory, user name, or host name at the positions below.

    Scan every file under ops_dir, sorted, then operations_doc. Read text with
    errors='replace'. Skip a pattern when the home, the user name, or the
    first host label has length 1 or less.
    The home matches as a whole path prefix when each side is absent or outside
    the name class A-Za-z0-9._- (letters, digits, dot, underscore, and hyphen).
    A user name matches after a tilde, or after a name-class character and a
    slash, when the next character is outside that class. It matches as the
    only segment of an absolute path when the slash has no name-class character
    before it and the next character is neither a name-class character nor a
    slash. It matches before an at sign when the previous character is outside
    that class. It does not match as the first segment of a longer absolute
    path, so </dev/null is quiet for the user dev and cd /root/foo reports the
    home /root without the user root.
    A host label matches after an at sign or two slashes when the next
    character is outside the word class A-Za-z0-9_- (letters, digits, underscore,
    and hyphen). It matches before a colon and a digit when the previous character
    is outside that class. It matches before a dot and an alphanumeric when
    the previous character is outside that class and is not a slash, a dollar
    sign, or a double quote. These three matches use the first label. Every
    host match is case insensitive. When the host name has more than one label,
    the whole host name also matches when the previous character is absent or
    outside the word class and is not a dollar sign, and the next character is
    absent or outside the word class. For the label ops, "ops.conf" does not
    match and ping ops.lan does. For the label app, $APP.prev does not match
    and curl app:3773/x does. For the label config, ops/config.example does not
    match, and //config/share and see config.json match. For the host box7.lan,
    ssh "box7.lan", HOST="box7.lan", and /mnt/box7.lan/archives match, and
    $box7.lan does not.
    Each hit becomes
    f"{rel}:{number}: holds this machine's {kind}"
    with kind one of 'home directory', 'user name', or 'host name', in that
    pattern order for a line. rel is the path relative to ops_dir.parent.
    When one of REQUIRED_SCAN is missing, append
    f"{rel}: is missing, so this machine's home directory, user name, and host name were not scanned"
    and do not invent a scan of that path. An empty list means the required
    files exist and none of the scanned text matched.
    """
    ops_dir = Path(ops_dir)
    operations_doc = Path(operations_doc)
    repo = ops_dir.parent
    findings = []
    scanned = []
    if ops_dir.is_dir():
        scanned.extend(sorted(path for path in ops_dir.rglob("*") if path.is_file()))
    if operations_doc.is_file() and all(path.resolve() != operations_doc.resolve() for path in scanned):
        scanned.append(operations_doc)
    for path in scanned:
        rel = path.relative_to(repo).as_posix()
        text = path.read_text(errors="replace")
        for kind, number, _line in _machine_value_hits(text, identity):
            findings.append(f"{rel}:{number}: holds this machine's {kind}")
    for label in REQUIRED_SCAN:
        path = operations_doc if label == "docs/operations.md" else ops_dir / Path(label).name
        if not path.is_file():
            findings.append(
                f"{label}: is missing, so this machine's home directory, user name, and host name were not scanned"
            )
    return findings

def _machine_identity():
    """Identity(pwd.getpwuid(os.getuid()).pw_dir, pw_name, socket.gethostname())."""
    entry = pwd.getpwuid(os.getuid())
    return Identity(entry.pw_dir, entry.pw_name, socket.gethostname())
