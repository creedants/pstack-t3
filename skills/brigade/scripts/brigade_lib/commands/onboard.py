import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from brigade_lib import seam
from brigade_lib.commands.onboard_contract import _read_cap, _read_contract_checks, _read_generated, _read_landing
from brigade_lib.commands.setup import validate_settings
from brigade_lib.compact import effective_cap, effective_provider
from brigade_lib.leases import require_workers
from brigade_lib.shared import BrigadeError, command_words, slug
from brigade_lib.store import ADMIN_DIR, AUTOFIRE, REPORTING, _read_meta, change_note_dir, liveness_of, low_reserve_of, roles_script, round_budget_of, write_atomic

COMMANDS = ("onboard",)

def _text(value):
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    made = str(value)
    return made if made.strip() else "unset"

class _Ctx:
    def __init__(self, root, directory):
        self.root = root
        self.directory = directory
        self.meta = None
        self.meta_state = "missing"
        self.validate_error = ""
        self.roles_payload = None
        self.roles_error = ""
        self._remote = None
        self._branch = None

def _meta(ctx):
    return ctx.meta if isinstance(ctx.meta, dict) else {}

def _read_restaurant(ctx):
    if ctx.meta_state == "missing":
        return ("missing", "no restaurant.json")
    if ctx.meta_state == "invalid":
        return ("invalid", "restaurant.json is not valid JSON")
    if ctx.validate_error:
        return ("invalid", ctx.validate_error)
    return ("configured", ctx.meta.get("restaurant") or ctx.directory.name)

def _read_mode(ctx):
    value = _meta(ctx).get("mode")
    if value in (None, ""):
        return ("missing", "")
    if value not in ("full", "light"):
        return ("invalid", "mode is not full or light")
    return ("configured", value)

def _read_intake(ctx):
    value = _meta(ctx).get("intake")
    if value is None:
        return ("missing", [])
    if not isinstance(value, list):
        return ("invalid", "intake is not a list")
    return ("configured", value) if value else ("missing", [])

def _user_roles_path():
    value = os.environ.get("XDG_CONFIG_HOME")
    if value and os.path.isabs(value):
        return Path(value) / "pstack-t3" / "roles.json"
    return Path.home() / ".config" / "pstack-t3" / "roles.json"

def _project_roles_path(root):
    current = Path(root)
    try:
        current = current.resolve()
    except (OSError, ValueError):
        pass
    for directory in [current, *current.parents]:
        try:
            if (directory / ".pstack" / "t3-roles.json").is_file():
                return directory / ".pstack" / "t3-roles.json"
            if (directory / ".git").exists():
                return directory / ".pstack" / "t3-roles.json"
        except (OSError, ValueError):
            continue
    return current / ".pstack" / "t3-roles.json"

def _read_models(ctx):
    try:
        user_file = _user_roles_path()
        user_exists = user_file.is_file()
    except (OSError, ValueError):
        user_exists = False
    try:
        project_exists = _project_roles_path(ctx.root).is_file()
    except (OSError, ValueError):
        project_exists = False
    if not user_exists and not project_exists:
        return ("missing", "")
    return ("configured", "present")

def _read_file(root, *parts):
    try:
        return root.joinpath(*parts).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""

def _manifest(root):
    test, build = "", ""
    try:
        scripts = json.loads(_read_file(root, "package.json")).get("scripts") or {}
    except ValueError:
        scripts = {}
    if not isinstance(scripts, dict):
        scripts = {}
    if not test and scripts.get("test"):
        test = "npm test"
    if not build and scripts.get("build"):
        build = "npm run build"
    made = _read_file(root, "Makefile").splitlines()
    heads = [line.split("#")[0].strip() for line in made]
    if not test and any(head == "test:" or head.startswith("test:") for head in heads):
        test = "make test"
    if not build and any(head == "build:" or head.startswith("build:") for head in heads):
        build = "make build"
    if not test and root.joinpath("Cargo.toml").is_file():
        test = "cargo test"
    if not build and root.joinpath("Cargo.toml").is_file():
        build = "cargo build"
    if not test and root.joinpath("go.mod").is_file():
        test = "go test ./..."
    if not build and root.joinpath("go.mod").is_file():
        build = "go build ./..."
    if not test and (root.joinpath("pyproject.toml").is_file() or root.joinpath("tox.ini").is_file()):
        test = "pytest" if root.joinpath("pyproject.toml").is_file() else "tox"
    if not build and root.joinpath("scripts", "build.py").is_file():
        build = "python3 scripts/build.py"
    return test, build

def _stored(meta, key):
    value = meta.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise BrigadeError(f"{key} is not text")
    return value.strip()

def _read_build(ctx):
    if isinstance(ctx.meta, dict):
        kept = _stored(ctx.meta, "buildCommand")
        if kept:
            command_words(kept)
            return ("configured", kept)
    found = _manifest(ctx.root)[1]
    return ("detected", found) if found else ("missing", "")

def _read_check(ctx):
    if isinstance(ctx.meta, dict):
        kept = _stored(ctx.meta, "checkCommand")
        if kept:
            command_words(kept)
            return ("configured", kept)
    found = _manifest(ctx.root)[0]
    return ("detected", found) if found else ("missing", "")

def _read_note(ctx):
    made = change_note_dir(_meta(ctx))
    return ("configured", made) if made else ("missing", "")

def _read_workers(c):
 m=_meta(c);v=m.get("workers")
 if"workers"not in m or v in(None,""):return("configured",2)
 try:require_workers(v)
 except Exception as e:return("invalid",str(e)or 0)
 return("configured",v)
def _read_reporting(c):
 v=_meta(c).get("reporting")
 if v in(None,""):return("configured","milestones")
 if v not in REPORTING:return("invalid",0)
 return("configured",v)
def _read_autofire(c):
 v=_meta(c).get("autofire")
 if v in(None,""):return("configured","normal")
 if v not in AUTOFIRE:return("invalid",0)
 return("configured",v)
def _read_roles(ctx):
    if ctx.roles_error:
        return ("invalid", ctx.roles_error)
    return ("detected", (ctx.roles_payload or {}).get("mode", "unknown"))

def _git(ctx, *args):
    try:
        result = subprocess.run(["git", "-C", str(ctx.root), *args], capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return None, f"git did not run: {type(error).__name__}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return None, detail[0] if detail else f"git exited {result.returncode}"
    return result.stdout.strip(), ""

def _gh(*args):
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return None, f"gh did not run: {type(error).__name__}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return None, detail[0] if detail else f"gh exited {result.returncode}"
    return result.stdout.strip(), ""

def _remote(ctx):
    if ctx._remote is None:
        ctx._remote = _git(ctx, "remote", "get-url", "origin")
    return ctx._remote

def _branch(ctx):
    if ctx._branch is None:
        ctx._branch = _git(ctx, "branch", "--show-current")
    return ctx._branch

def _read_trunk(ctx):
    out, error = _git(ctx, "symbolic-ref", "refs/remotes/origin/HEAD")
    if out:
        return ("detected", out.rsplit("/", 1)[-1])
    if error and "not a git repository" in error:
        return ("none", "no repository")
    branch, _ = _branch(ctx)
    if branch:
        return ("detected", branch)
    return ("unknown", error or "no trunk branch")

def _read_remote(ctx):
    out, error = _remote(ctx)
    if out:
        return ("detected", out)
    lowered = (error or "").lower()
    if "no such remote" in lowered:
        return ("none", "no remote")
    if "not a git repository" in lowered:
        return ("none", "no repository")
    return ("unknown", error or "no remote")

def _github_repo(url):
    text = (url or "").strip()
    for prefix in ("https://github.com/", "git@github.com:"):
        if text.startswith(prefix):
            rest = text[len(prefix):].removesuffix(".git").strip("/")
            parts = rest.split("/")
            if len(parts) == 2 and all(part not in ("", ".", "..") for part in parts):
                return f"{parts[0]}/{parts[1]}"
            return ""
    return ""

def _read_checks(c):
 a,b=_remote(c)
 if not a:return("unknown",b or"no remote")
 r=_github_repo(a)
 if not r:return("unknown","remote is not GitHub")
 t,u=_read_trunk(c)
 if t!="detected":return("unknown",u or"no trunk")
 o,e=_gh("api","repos/"+r+"/branches/"+u+"/protection")
 if e:return("unknown",e)
 try:p=json.loads(o)
 except ValueError:return("unknown",0)
 n=[]
 if isinstance(p,dict):
  q=p.get("required_status_checks")
  if q is None and"contexts"in p:q={"contexts":p.get("contexts"),"checks":[]}
  if isinstance(q,dict):
   for x in q.get("contexts",[])or[]:
    if not isinstance(x,str)or not x:return("unknown",0)
    n.append(x)
   for x in q.get("checks",[])or[]:
    y=x if isinstance(x,str)else x.get("context")if isinstance(x,dict)else None
    if not isinstance(y,str)or not y:return("unknown",0)
    n.append(y)
   return("detected",sorted(set(n)))
  return("detected",[])
 return("unknown",0)
def _read_pr(c):
 a,b=_remote(c)
 if not a:
  if b and"not a git repository"in b:return("none","no repository")
  return("no",b or"no remote")
 if not _github_repo(a):return("unknown","remote is not GitHub")
 _,e=_gh("auth","status")
 if e:return("unknown",e)
 return("yes",a)

SETTINGS = (
    ("restaurant", _read_restaurant, "open --project-root <root> --name <name>", _text),
    ("reporting", _read_reporting, "set --reporting milestones", _text),
    ("workers", _read_workers, "open --workers 2 or set --workers 2", _text),
    ("mode", _read_mode, "open --mode light or set --mode light", _text),
    ("models", _read_models, "run setup-pstack", _text),
    ("intake", _read_intake, "set --intake <sources>", _text),
    ("autofire", _read_autofire, "set --autofire normal", _text),
    ("low_reserve", lambda ctx: ("configured", low_reserve_of(_meta(ctx))), "set --low-reserve 1", _text),
    ("round_budget", lambda ctx: ("configured", round_budget_of(_meta(ctx))), "set --round-budget 3", _text),
    ("liveness", lambda ctx: ("configured", list(liveness_of(_meta(ctx)))), "set --liveness-ms 600000", _text),
    ("context_cap", lambda ctx: ("configured", effective_cap(_meta(ctx))), "set --context-cap 300000", _text),
    ("coordinator_provider", lambda ctx: ("configured", effective_provider(_meta(ctx))), "set --coordinator-provider claude", _text),
    ("build_command", _read_build, "set --build-command <command>", _text),
    ("check_command", _read_check, "set --check-command <command>", _text),
    ("change_note_dir", _read_note, "set --change-note-dir changes", _text),
    ("landing_mode", _read_landing, "land.py init --mode merge or land.py mode merge", _text),
    ("repository_cap", _read_cap, None, _text),
    ("generated", _read_generated, "land.py init --generated-map <MAP>", _text),
    ("roles", _read_roles, "roles.py show, then roles.py write", _text),
    ("trunk", _read_trunk, None, _text),
    ("remote", _read_remote, None, _text),
    ("required_checks", _read_checks, None, _text),
    ("pr_ability", _read_pr, None, _text),
    ("contract_checks", _read_contract_checks, None, _text),
)

def detect(project_root, directory):
    ctx = _Ctx(Path(project_root), Path(directory))
    try:
        ctx.root = Path(project_root).resolve()
    except (OSError, ValueError):
        pass
    try:
        meta, state = _read_meta(ctx.directory / "restaurant.json")
    except (OSError, ValueError):
        meta, state = None, "invalid"
    ctx.meta = meta
    ctx.meta_state = state if state in ("ok", "missing", "invalid") else "invalid"
    if ctx.meta_state == "ok":
        try:
            validate_settings(meta)
        except (BrigadeError, OSError, ValueError) as error:
            ctx.validate_error = str(error) or type(error).__name__
        except Exception as error:
            ctx.validate_error = type(error).__name__
    if seam.HELD_LOCKS:
        ctx.roles_error = (f"roles.py show runs with no store lock held; release {seam.HELD_LOCKS[0]}'s store lock "
                           "before resolving the mode")
    else:
        try:
            script = roles_script()
        except (BrigadeError, OSError, ValueError) as error:
            ctx.roles_error = str(error) or type(error).__name__
        else:
            result, error = _roles_show(script, ctx.root)
            if error:
                ctx.roles_error = error
            else:
                ctx.roles_payload = result
    facts = {}
    for name, reader, fix, _ in SETTINGS:
        try:
            state, value = reader(ctx)
        except (BrigadeError, OSError, ValueError) as error:
            state, value = "invalid", str(error) or type(error).__name__
        except Exception as error:
            state, value = "invalid", type(error).__name__
        facts[name] = {"state": state, "value": value, "fix": fix}
    return facts

def _roles_show(script, root):
    try:
        result = subprocess.run([sys.executable, str(script), "show", "--cwd", str(root)],
                                capture_output=True, timeout=30)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return None, f"roles.py show did not run: {type(error).__name__}"
    try:
        out = result.stdout.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None, "roles.py show printed no JSON object"
    if result.returncode != 0:
        return None, f"roles.py show exited {result.returncode}"
    try:
        payload = json.loads(out)
    except ValueError:
        return None, "roles.py show printed no JSON object"
    return payload if isinstance(payload, dict) else {}, ""

def _lines(facts):
    lines = []
    for name, _, _, show in SETTINGS:
        rendered = str(show(facts[name]["value"]))
        flat = " ".join(rendered.splitlines())
        lines.append(f"{name}: {flat}")
    return lines

def _parse(text):
    found = {}
    for line in text.splitlines():
        if ":" in line:
            name, _, value = line.partition(":")
            found[name.strip()] = value.strip()
        elif line.strip():
            found[line.strip()] = ""
    return found

def register(sub):
    p = sub.add_parser("onboard", help="detect onboarding settings")
    p.add_argument("--project-root", required=True)
    p.add_argument("--name")
    p.add_argument("--admin", action="store_true")
    p.add_argument("--at", default=argparse.SUPPRESS)
    p.add_argument("--detect", action="store_true", help="print facts and missing as JSON and write no brigade files")
    p.add_argument("--summary", action="store_true", help="write onboarding.txt and print the change lines, or with identical bytes print no change to onboarding.txt and skip the write, or with different bytes but unchanged parsed facts print normalized onboarding.txt with no fact changes and rewrite the file, or with no restaurant file print the lines plus not saved and write nothing")

def handle(args, root):
    if seam.READ_ONLY:
        raise BrigadeError("brigade: next is read-only")
    if args.admin and args.name:
        raise BrigadeError("onboard --admin takes no --name")
    if not args.admin and not args.name:
        raise BrigadeError("onboard needs --name, or --admin")
    if args.detect == args.summary:
        raise BrigadeError("onboard needs --detect or --summary")
    project_root = Path(args.project_root)
    if args.at:
        directory = Path(args.at)
    else:
        directory = Path(root) / slug(project_root.resolve().name) / (ADMIN_DIR if args.admin else slug(args.name))
    facts = detect(str(project_root), str(directory))
    missing = [name for name, _, _, _ in SETTINGS if facts[name]["state"] == "missing"]
    if args.detect:
        return json.dumps({"facts": facts, "missing": missing}, indent=2)
    lines = _lines(facts)
    if not (directory / "restaurant.json").is_file():
        return "\n".join([*lines, "not saved; run brigade.py open to create the restaurant"])
    target = directory / "onboarding.txt"
    wanted = "\n".join(lines) + "\n"
    try:
        old_bytes = target.read_bytes()
    except OSError:
        old_bytes = None
    if old_bytes is not None and old_bytes == wanted.encode("utf-8"):
        return "no change to onboarding.txt"
    old = _parse(old_bytes.decode("utf-8", errors="replace")) if old_bytes is not None else {}
    new = _parse(wanted)
    changes = []
    for name in new:
        if name not in old:
            changes.append(f"added {name}: {new[name]}")
        elif old[name] != new[name]:
            changes.append(f"{name}: {old[name]} -> {new[name]}")
    for name in old:
        if name not in new:
            changes.append(f"removed {name}: {old[name]}")
    write_atomic(target, wanted)
    return "\n".join(changes) if changes else "normalized onboarding.txt with no fact changes"
