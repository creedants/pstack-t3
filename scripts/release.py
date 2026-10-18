#!/usr/bin/env python3
"""Cut a release section in CHANGELOG.md from the fragments in changes/.

Orders the fragments by the commit that added each one, writes their bullets
under a new `## X.Y.Z (YYYY-MM-DD)` heading, and deletes the fragments. It
never stages, commits, tags, pushes, or calls gh. It prints those steps.

When the changelog would pass 5000 tokens it moves older versions into docs/changelog/ and leaves an ## Archive link block. When the new section alone cannot fit it archives the new section too and leaves only the Archive block. Archived copies prefix relative link destinations with ../../. Archived bytes equal the original section except for rebased relative link destinations.

A failed write or delete, or a SIGINT, SIGTERM, or SIGHUP, puts CHANGELOG.md and every fragment back
as the script read them. The script then checks them and prints `nothing changed` only when they match.
When a file differs, it names that file and the undo command. A kill that cannot be caught, or power
loss, can leave a partial run. The same undo command restores it.
"""
import argparse
import datetime
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
HEADING = re.compile(r"## ([0-9]+)\.([0-9]+)\.([0-9]+)(?:\s.*)?")
KINDS = ("feature", "fix", "polish", "upkeep", "study")
GROUPS = (("### New", ("feature",)), ("### Fixed", ("fix",)), ("### Changed", ("upkeep",)), (None, ("polish", "study")))
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
LINK_RE = re.compile(r"(!?\[[^\]]*\]\()([^)\s]+)([^)]*\))")
CODERE = re.compile(r"(`+[^`\n]*?`+)")
UNDO = "git restore --staged --worktree CHANGELOG.md changes/"
LIMIT_TOKENS = 5000
ARCHIVE = "docs/changelog"
ARCHIVE_HEADING = "## Archive"
ARCHIVE_UNDO = "git reset -q -- CHANGELOG.md changes/ docs/changelog/ && git checkout -- CHANGELOG.md changes/ && git clean -fdq -- docs/changelog/"
HELD = tuple(getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name))
COMMIT = "\x01"
LOG = ("log", "--reverse", "--diff-filter=A", "--no-renames", "--no-show-signature", "-z",
 f"--format=%x{ord(COMMIT):02x}", "--name-only", "--", "changes/")
Version = tuple[int, int, int]
@dataclass(frozen=True)
class Original:
 path: Path
 data: bytes
 mode: int
 absent: bool = False
@dataclass(frozen=True)
class Shelved:
 version: Version
 heading: str
 parts: tuple[str, ...]
@dataclass(frozen=True)
class Shelving:
 changelog: str
 created: dict
 moved: tuple
@dataclass(frozen=True)
class Cut:
 section: str
 changelog: str
 before: Original
 fragments: tuple[Original, ...]
 created: tuple[Original, ...] = ()
def git(*args):
 try:
  result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="surrogateescape", env={**os.environ, "LC_ALL": "C"})
 except OSError as error:
  sys.exit(f"git {args[0]} failed: {error}")
 if result.returncode != 0:
  sys.exit(f"git {args[0]} failed: {(result.stderr or result.stdout).strip()}")
 return result.stdout
def parse_version(text):
 match = VERSION.fullmatch(text)
 return tuple(int(part) for part in match.groups()) if match else None
def parse_day(text):
 if not DAY.fullmatch(text):
  return None
 try:
  return datetime.date.fromisoformat(text)
 except ValueError:
  return None
def dotted(version):
 return ".".join(str(part) for part in version)
def bullet_fault(lines):
 open_bullet = False
 for number, line in enumerate(lines, 1):
  bullet = line.startswith("- ") and line[2:].strip()
  continuation = open_bullet and line.startswith((" ", "\t")) and line.strip()
  if "\r" in line or not (bullet or continuation):
   return f"line {number} is not a bullet or a continuation line"
  open_bullet = True
 return None if open_bullet else "holds no bullet"
def fits(text):
 return len(text.encode("utf-8")) // 4 <= LIMIT_TOKENS
def parts(section):
 if fits(section):
  return (section,)
 lines = section.splitlines(keepends=True)
 starts = [i for i, line in enumerate(lines) if line.startswith("- ")]
 if not starts:
  return (section,)
 out = []
 cur = 0
 while cur < len(lines):
  cands = [s for s in starts if s > cur] + [len(lines)]
  nxt = cands[0]
  for cand in cands:
   if fits("".join(lines[cur:cand])):
    nxt = cand
   else:
    break
  out.append("".join(lines[cur:nxt]))
  cur = nxt
 return tuple(out)
def rebase_links(section):
 o = []
 f = False
 for r in section.splitlines(keepends=True):
  s = r.lstrip()
  if s.startswith("```") or s.startswith("~~~"):
   f = not f
   o.append(r)
   continue
  if f:
   o.append(r)
   continue
  p = CODERE.split(r)
  for i in range(0, len(p), 2):
   p[i] = LINK_RE.sub(lambda m: m.group(1) + "../../" + m.group(2) + m.group(3) if m.group(2) and not m.group(2).startswith("../../") and "://" not in m.group(2) and ":" not in m.group(2) and m.group(2)[0] not in "/#<" else m.group(0), p[i])
  o.append("".join(p))
 return "".join(o)
def read_shelf(files):
 grouped: dict = {}
 for rel, text in files.items():
  if not rel.startswith(ARCHIVE + "/") or not rel.endswith(".md"):
   sys.exit(f"{rel} is an orphan")
  base = rel[len(ARCHIVE) + 1:-3]
  if "-" in base:
   stem, suffix = base.rsplit("-", 1)
   ver = parse_version(stem)
   part = int(suffix) if suffix.isdigit() else 0
   if ver is None or part < 2:
    sys.exit(f"{rel} is an orphan")
  else:
   ver = parse_version(base)
   part = 1
   if ver is None:
    sys.exit(f"{rel} is an orphan")
  grouped.setdefault(ver, {})[part] = text
 shelf = {}
 for ver, by_part in grouped.items():
  if 1 not in by_part or sorted(by_part) != list(range(1, max(by_part) + 1)):
   sys.exit(f"{ARCHIVE}/{dotted(ver)}.md is an orphan")
  ordered = tuple(by_part[i] for i in sorted(by_part))
  if not ordered[0].startswith("## "):
   sys.exit(f"{ARCHIVE}/{dotted(ver)}.md is an orphan")
  first = ordered[0].splitlines()[0] if ordered[0].splitlines() else ""
  m = HEADING.fullmatch(first)
  if m is None or tuple(int(p) for p in m.groups()) != ver:
   sys.exit(f"{ARCHIVE}/{dotted(ver)}.md is an orphan")
  shelf[ver] = Shelved(version=ver, heading=first, parts=ordered)
 return shelf
def shelve(new, changelog, shelf):
 lines = changelog.splitlines(keepends=True)
 idxs = [i for i, line in enumerate(lines) if line.startswith("## ")]
 if not idxs:
  header, secs, ab = changelog, [], None
 else:
  header = "".join(lines[:idxs[0]])
  at = next((k for k, i in enumerate(idxs) if lines[i].strip() == ARCHIVE_HEADING), None)
  if at is None:
   secs = ["".join(lines[idxs[k]:idxs[k + 1] if k + 1 < len(idxs) else len(lines)]) for k in range(len(idxs))]
   ab = None
  else:
   secs = ["".join(lines[idxs[k]:idxs[k + 1]]) for k in range(at)]
   ab = "".join(lines[idxs[at]:])
 def render(full):
  r = [ARCHIVE_HEADING, ""]
  for v in sorted(full, reverse=True):
   for i in range(len(full[v].parts)):
    fn = f"{dotted(v)}.md" if i == 0 else f"{dotted(v)}-{i + 1}.md"
    r.append(f"- [{fn[:-3]}]({ARCHIVE}/{fn})")
  return "\n".join(r) + "\n"
 if (render(shelf) if shelf else None) != ab:
  sys.exit("CHANGELOG.md Archive block does not match docs/changelog/")
 dup = set(released(changelog)) & set(shelf)
 if dup:
  sys.exit(f"{ARCHIVE}/{dotted(sorted(dup)[0])}.md is an orphan")
 if new is not None:
  spliced = splice(changelog, new)
  if fits(spliced):
   return Shelving(spliced, {}, ())
  todo = secs
 else:
  if fits(changelog):
   return Shelving(changelog, {}, ())
  todo = secs
 ordered = []
 for s in todo:
  f = s.splitlines()[0]
  ordered.append((tuple(int(p) for p in HEADING.fullmatch(f).groups()), s))
 splits = [(v, s, parts(rebase_links(s))) for v, s in ordered]
 for keep in range(len(splits), -1, -1):
  rest = splits[keep:]
  fresh = [Shelved(v, s.splitlines()[0], p) for v, s, p in rest]
  full = dict(shelf)
  for she in fresh:
   full[she.version] = she
  block = render(full)
  head = header.rstrip("\n") + "\n\n" if header.strip() else ""
  kt = (new + "\n" + "".join(s for _, s, _ in splits[:keep])) if new is not None else "".join(s for _, s, _ in splits[:keep])
  body = kt.rstrip("\n") + "\n\n" if kt.strip() else ""
  final = head + body + block
  if fits(final):
   created = {}
   for she in fresh:
    for i, part in enumerate(she.parts):
     fn = f"{dotted(she.version)}.md" if i == 0 else f"{dotted(she.version)}-{i + 1}.md"
     created[f"{ARCHIVE}/{fn}"] = part
   return Shelving(final, created, tuple(fresh))
 if new is not None:
  h = new.splitlines()[0]
  v = tuple(int(p) for p in HEADING.fullmatch(h).groups())
  np = parts(rebase_links(new))
  ns = Shelved(v, h, np)
  full[ns.version] = ns
  block = render(full)
  final = head + block
  if fits(final):
   created = {}
   for she in list(fresh) + [ns]:
    for i, part in enumerate(she.parts):
     fn = f"{dotted(she.version)}.md" if i == 0 else f"{dotted(she.version)}-{i + 1}.md"
     created[f"{ARCHIVE}/{fn}"] = part
   return Shelving(final, created, tuple(fresh) + (ns,))
 sys.exit("CHANGELOG.md cannot fit under 5000 tokens")
def added_at(log):
 at, commit = {}, 0
 for token in log.split("\0"):
  if token == COMMIT:
   commit += 1
   continue
  path = token[1:] if token.startswith("\n") else token
  if not path:
   continue
  if not commit or not path.startswith("changes/"):
   raise ValueError(token)
  if path.count("/") == 1:
   at[path[len("changes/"):]] = commit
 return at
def released(changelog):
 matches = (HEADING.fullmatch(line) for line in changelog.splitlines())
 return [tuple(int(part) for part in match.groups()) for match in matches if match]
def splice(changelog, section):
 lines = changelog.splitlines(keepends=True)
 at = next((index for index, line in enumerate(lines) if line.startswith("## ")), len(lines))
 head, tail = "".join(lines[:at]).rstrip("\n"), "".join(lines[at:])
 return (head + "\n\n" if head else "") + section + ("\n" + tail if tail else "")
def remaining_steps(version, archived=False):
 first = "git add CHANGELOG.md changes/ docs/changelog/" if archived else "git add CHANGELOG.md changes/"
 return [first, f'git commit -m "release {version}"',
   f'git tag -a v{version} -m "pstack-t3 {version}"    # on the commit that lands on main',
   f"git push origin v{version}", f"gh release create v{version} --notes-file <release notes file>"]
def read_utf8(path):
 data = path.read_bytes()
 try:
  data.decode("utf-8")
 except UnicodeDecodeError:
  sys.exit(f"{path.relative_to(ROOT).as_posix()} is not UTF-8; fix it")
 return Original(path, data, stat.S_IMODE(path.stat().st_mode))
def refuse_unfinished_release():
 dirty = git("status", "--porcelain", "--untracked-files=all", "--", "CHANGELOG.md", "changes/", ARCHIVE)
 if dirty:
  undo = ARCHIVE_UNDO if ARCHIVE in dirty else UNDO
  scope = "CHANGELOG.md, changes/, or docs/changelog/" if ARCHIVE in dirty else "CHANGELOG.md or changes/"
  sys.exit(f"{scope} has uncommitted changes; commit them, "
    f"or run {undo} to undo an unfinished release:\n{dirty.rstrip()}")
def take_kind(name, lines):
 if not lines or not lines[0].startswith("- "):
  return "upkeep", lines
 text = lines[0][2:].strip()
 if text in [f"kind {kind}" for kind in KINDS]:
  return text[len("kind "):], lines[1:]
 if text.startswith("kind"):
  named = ", ".join(KINDS[:-1]) + f", or {KINDS[-1]}"
  sys.exit(f"changes/{name} line 1 names a kind that is not {named}; fix the fragment, commit it, and rerun")
 return "upkeep", lines
def undo_for(cut):
 return ARCHIVE_UNDO if cut.created else UNDO
def plan(version_text, date_text):
 version = parse_version(version_text)
 if version is None:
  sys.exit(f"version {version_text} is not X.Y.Z; pass three numbers such as 0.3.0")
 day = datetime.date.today() if date_text is None else parse_day(date_text)
 if day is None:
  sys.exit(f"date {date_text} is not YYYY-MM-DD; pass a real date such as 2026-10-09")
 if git("rev-parse", "--is-shallow-repository").strip() == "true":
  sys.exit("this clone is shallow, so git cannot tell when each fragment was added; "
    "run git fetch --unshallow and rerun")
 refuse_unfinished_release()
 changelog_path = ROOT / "CHANGELOG.md"
 if changelog_path.is_symlink():
  sys.exit("CHANGELOG.md is a symlink; replace it with the file before you release")
 if not changelog_path.is_file():
  sys.exit("CHANGELOG.md is missing; this script adds a section to an existing changelog")
 before = read_utf8(changelog_path)
 changelog = before.data.decode("utf-8")
 archive_dir = ROOT / ARCHIVE
 files = {}
 if archive_dir.is_dir():
  for entry in sorted(archive_dir.iterdir()):
   rel = entry.relative_to(ROOT).as_posix()
   if entry.is_symlink() or not entry.is_file():
    sys.exit(f"{rel} is an orphan")
   try:
    files[rel] = entry.read_bytes().decode("utf-8")
   except UnicodeDecodeError:
    sys.exit(f"{rel} is not UTF-8; fix it")
 shelf = read_shelf(files)
 versions = released(changelog)
 if version in versions:
  sys.exit(f"CHANGELOG.md already has a ## {dotted(version)} heading; pick the next version")
 if version in shelf:
  sys.exit(f"docs/changelog already has a ## {dotted(version)} heading; pick the next version")
 every = versions + sorted(shelf)
 if every and version < max(every):
  where = "CHANGELOG.md and docs/changelog/" if shelf else "CHANGELOG.md"
  sys.exit(f"{dotted(version)} is not above {dotted(max(every))}, the latest version in {where}; pick a higher version")
 changes = ROOT / "changes"
 entries = sorted(changes.iterdir()) if changes.is_dir() else []
 if not entries:
  sys.exit("changes/ holds no fragments; there is nothing to release")
 for entry in entries:
  if entry.is_symlink() or not entry.is_file() or not entry.name.endswith(".md"):
   sys.exit(f"changes/{entry.name} is not a fragment; changes/ holds only .md files, so move or delete it")
 originals = {entry.name: read_utf8(entry) for entry in entries}
 fragments = {}
 for entry in entries:
  lines = originals[entry.name].data.decode("utf-8").split("\n")
  lines = lines[:-1] if lines[-1] == "" else lines
  kind, lines = take_kind(entry.name, lines)
  fault = bullet_fault(lines)
  if fault:
   sys.exit(f"changes/{entry.name} {fault}; fix the fragment, commit it, and rerun")
  fragments[entry.name] = (kind, lines)
 if every:
  latest = max(every)
  wanted = ((latest[0], latest[1] + 1, 0) if any(fragment[0] == "feature" for fragment in fragments.values())
    else (latest[0], latest[1], latest[2] + 1))
  if version != wanted:
   sys.exit(f"{dotted(version)} is not {dotted(wanted)}, the version the kinds give; pass {dotted(wanted)}")
 try:
  at = added_at(git(*LOG))
 except ValueError as error:
  sys.exit(f"git log printed {str(error)!r}, which is not a commit marker or a path under changes/; "
    "look in git config for a setting that adds output to git log")
 for entry in entries:
  if entry.name not in at:
   sys.exit(f"git log shows no commit that added changes/{entry.name}; commit the fragment and rerun")
 ordered = sorted(entries, key=lambda entry: (at[entry.name], entry.name))
 groups = []
 for heading, kinds in GROUPS:
  bullets = [line for entry in ordered if fragments[entry.name][0] in kinds for line in fragments[entry.name][1]]
  if bullets:
   groups.append((heading, bullets))
 blocks = [f"## {dotted(version)} ({day.isoformat()})"]
 blocks += [("\n".join([heading, "", *bullets]) if heading else "\n".join(bullets)) for heading, bullets in groups]
 section = "\n\n".join(blocks) + "\n"
 shelving = shelve(section, changelog, shelf)
 made = tuple(Original(ROOT / rel, text.encode("utf-8"), 0o644, True) for rel, text in shelving.created.items())
 return Cut(section=section, changelog=shelving.changelog, before=before,
   fragments=tuple(originals[entry.name] for entry in ordered), created=made)
def write_file(path, data, mode):
 handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
 try:
  with os.fdopen(handle, "wb") as temp:
   temp.write(data)
  os.chmod(name, mode)
  os.replace(name, path)
 except BaseException:
  try:
   os.unlink(name)
  except OSError:
   pass
  raise
def found(original):
 try:
  return original.path.read_bytes(), stat.S_IMODE(original.path.stat().st_mode)
 except OSError:
  return None
def differing(cut):
 out = [o for o in (cut.before, *cut.fragments) if found(o) != (o.data, o.mode)]
 return out + [o for o in cut.created if found(o) is not None]
def restore(cut):
 failed = {}
 for original in differing(cut):
  try:
   if original.absent:
    try:
     original.path.unlink()
    except FileNotFoundError:
     pass
   else:
    write_file(original.path, original.data, original.mode)
  except OSError as error:
   failed[original.path] = error
 return failed
def apply(cut):
 caught: list[int] = []
 previous = {}
 try:
  for number in HELD:
   previous[number] = signal.signal(number, lambda signum, frame: caught.append(signum))
  for original in cut.created:
   original.path.parent.mkdir(parents=True, exist_ok=True)
  steps = [*(lambda o=o: write_file(o.path, o.data, o.mode) for o in cut.created),
    lambda: write_file(cut.before.path, cut.changelog.encode("utf-8"), cut.before.mode),
    *(fragment.path.unlink for fragment in cut.fragments)]
  reason = None
  try:
   for step in steps:
    if caught:
     break
    step()
  except (Exception, KeyboardInterrupt) as error:
   reason = "interrupted" if isinstance(error, KeyboardInterrupt) else str(error) or type(error).__name__
  if caught:
   reason = reason or "interrupted"
  if reason is None:
   return
  undo = undo_for(cut)
  failed = restore(cut)
  left = [original.path for original in differing(cut)]
  if left:
   named = ", ".join(
    f"{path.relative_to(ROOT).as_posix()} ({failed[path]})" if path in failed
    else path.relative_to(ROOT).as_posix()
    for path in left)
   sys.exit(f"stopped partway: {reason}; could not put back {named}; run {undo} to undo, then rerun")
  sys.exit(f"stopped partway: {reason}; nothing changed, fix that and rerun")
 finally:
  for number, handler in previous.items():
   signal.signal(number, handler)
def main(argv=None):
 parser = argparse.ArgumentParser(description=__doc__)
 parser.add_argument("version", help="the version to cut, as X.Y.Z")
 parser.add_argument("--dry-run", action="store_true", help="print the section and change nothing")
 parser.add_argument("--date", help="the date in the heading, as YYYY-MM-DD (default: today)")
 args = parser.parse_args(argv)
 cut = plan(args.version, args.date)
 sys.stdout.write(cut.section)
 sys.stdout.flush()
 if not args.dry_run:
  apply(cut)
 archived = bool(cut.created)
 cw, ww, dw = (("would create", "would write", "would delete") if args.dry_run else ("created", "wrote", "deleted"))
 if archived:
  print()
  for original in cut.created:
   print(f"{cw} {original.path.relative_to(ROOT).as_posix()}")
 print(f"\n{ww} this section to CHANGELOG.md" if not archived else f"{ww} this section to CHANGELOG.md")
 for fragment in cut.fragments:
  print(f"{dw} changes/{fragment.path.name}")
 if args.dry_run:
  print("nothing changed")
 print("this script never stages, commits, tags, or pushes. the remaining steps:")
 for step in remaining_steps(args.version, archived):
  print(f"  {step}")
 return 0
if __name__ == "__main__":
 sys.exit(main())
