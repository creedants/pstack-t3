import contextlib
import os
import stat
from pathlib import Path
from landing_lib import seam

def cherry_pick_stopped_for_conflict(result):
    """True when this cherry-pick stopped on a conflict.

    rerere.autoupdate can stage a resolution that matches HEAD. The index is then
    clean and the exit status is nonzero, the same as a commit that became empty.
    A conflict prints CONFLICT or "could not apply". An empty commit does not.
    """
    detail = result.stderr + result.stdout
    return "CONFLICT" in detail or "could not apply" in detail

def _tail(output):
    lines = output.strip().splitlines()[-3:]
    return " / ".join(lines) if lines else "no output"

class Integration:
    """A detached worktree only the queue uses, restored to a clean base before every attempt."""

    def __init__(self, store):
        self.store = store
        self.path = store.dir / "integration"
        if not (self.path / ".git").exists():
            seam.git("worktree", "add", "--detach", str(self.path), seam.trunk_ref(store.contract), cwd=store.repo)

    def git(self, *args, check=True, exact=False):
        return seam.git(*args, cwd=self.path, check=check, exact=exact)

    def reset(self, commit):
        for operation in ("cherry-pick", "rebase", "merge"):
            self.git(operation, "--abort", check=False)
        self.git("checkout", "--force", "--detach", commit)
        self.git("reset", "--hard", commit)
        self.git("clean", "-ffdx")

    def head(self):
        return self.git("rev-parse", "HEAD").stdout.strip()

    def _unmerged_paths(self):
        out = self.git("diff", "-z", "--name-only", "--diff-filter=U", check=False, exact=True).stdout
        names = [path for path in out.split("\0") if path]
        if names:
            return names
        out = self.git("ls-files", "-u", "-z", check=False, exact=True).stdout
        names = []
        for chunk in out.split("\0"):
            if not chunk:
                continue
            if "\t" in chunk:
                names.append(chunk.split("\t", 1)[-1])
            else:
                names.append(chunk.split()[-1])
        return names

    def _generated(self):
        """The contract's generated paths. An older contract with no key has none."""
        return list(self.store.contract.get("generated") or [])

    def _generated_snapshot(self):
        """Read staged generated paths, modes, blobs, and stages from git ls-files."""
        generated = self._generated()
        if not generated:
            return {}
        result = self.git("ls-files", "-s", "-z", "--", *[f":(literal){root}" for root in generated], check=False, exact=True)
        if result.returncode != 0:
            return None
        entries = {}
        for chunk in result.stdout.split("\0"):
            if not chunk:
                continue
            if "\t" not in chunk:
                return None
            info, path = chunk.split("\t", 1)
            parts = info.split(" ")
            if len(parts) != 3:
                return None
            mode, blob, stage = parts
            entries[path] = (mode, blob, stage)
        return entries

    def _roots_escape(self):
        for root in self._generated():
            cur = Path(self.path)
            for part in Path(root).parts:
                cur = cur / part
                try:
                    st = os.lstat(cur)
                except OSError:
                    break
                if stat.S_ISLNK(st.st_mode):
                    return f"generated path {root} traverses a symlink"
        return None

    def _give_up(self, before, reason):
        self.git("reset", "--hard", before, check=False)
        generated = self._generated()
        if generated and not self._roots_escape():
            self.git("clean", "-ffdx", "--", *[f":(literal){root}" for root in generated], check=False)
        return reason

    def _clear_generated(self):
        generated = self._generated()
        if not generated:
            return None
        escaped = self._roots_escape()
        if escaped:
            return escaped
        cleared = self.git("rm", "-r", "-f", "--ignore-unmatch", "--", *[f":(literal){root}" for root in generated], check=False)
        if cleared.returncode != 0:
            return f"rebuild failed. `git rm -r` for generated paths exited {cleared.returncode}. {_tail(cleared.stderr + cleared.stdout)}"
        cleaned = self.git("clean", "-ffdx", "--", *[f":(literal){root}" for root in generated], check=False)
        if cleaned.returncode != 0:
            return f"rebuild failed. `git clean -ffdx` for generated paths exited {cleaned.returncode}. {_tail(cleaned.stderr + cleaned.stdout)}"
        for path in generated:
            full = Path(self.path) / path
            try:
                if os.path.islink(full) or full.is_file():
                    full.unlink()
            except OSError:
                pass
        return None

    def _resolve_generated(self, before, identity, result):
        while result.returncode != 0 and cherry_pick_stopped_for_conflict(result):
            unmerged = self._unmerged_paths()
            if not unmerged or seam.source_paths(unmerged, self._generated()):
                return result
            picked = self.git("rev-parse", "--verify", "CHERRY_PICK_HEAD", check=False)
            if picked.returncode != 0:
                return result
            if self._roots_escape():
                return result
            restored = []
            for path in unmerged:
                spec = f":(literal){path}"
                in_ours = self.git("rev-parse", "--verify", "--quiet", f"HEAD:{path}", check=False).returncode == 0
                if in_ours:
                    checkout = self.git("checkout", "--ours", "--", spec, check=False)
                    if checkout.returncode != 0:
                        return result
                    restored.append(spec)
                else:
                    removed = self.git("rm", "--", spec, check=False)
                    if removed.returncode != 0:
                        return result
            if restored:
                staged = self.git("add", "--all", "--", *restored, check=False)
                if staged.returncode != 0:
                    return result
            result = self.git(*identity, "-c", "rerere.enabled=false", "cherry-pick", "--continue", check=False)
        return result

    def _dirty_tracked(self):
        names = []
        for args in (("diff", "-z", "--no-renames", "--name-only"), ("diff", "--cached", "-z", "--no-renames", "--name-only")):
            out = self.git(*args, check=False, exact=True).stdout
            names.extend(path for path in out.split("\0") if path)
        return sorted(set(names))

    def _run_build(self, command, timeout):
        return seam.run_build(command, self.path, timeout)

    def _rebuild(self, before):
        command = self.store.contract.get("rebuild")
        generated = self._generated()
        try:
            failed = self._clear_generated()
            if failed:
                return self._give_up(before, failed)
            contract = self.store.contract
            timeout = contract.get("timeout") or 1800
            try:
                slot = seam.slot
            except AttributeError:
                slot = None
            holder = slot("landing", repo=self.path, command=command) if slot else contextlib.nullcontext()
            with holder:
                first = self._run_build(command, timeout)
                if first[0] is None or first[0] != 0:
                    reason = "timed out" if first[0] is None else f"exited {first[0]}"
                    return self._give_up(before, f"rebuild failed. `{command}` {reason}. {_tail(first[1])}")
                dirty = self._dirty_tracked()
                sources = sorted(seam.source_paths(dirty, generated))
                if sources:
                    return self._give_up(before, f"build changed sources {', '.join(sources)}. Rebuild must only change generated paths")
                staged = self.git("add", "--all", "--", *[f":(literal){root}" for root in generated], check=False)
                if staged.returncode != 0:
                    return self._give_up(before, f"rebuild failed. `git add` exited {staged.returncode}. {_tail(staged.stderr + staged.stdout)}")
                snapshot = self._generated_snapshot()
                if snapshot is None:
                    return self._give_up(before, "rebuild failed. `git ls-files` for generated paths failed")
                failed = self._clear_generated()
                if failed:
                    return self._give_up(before, failed)
                second = self._run_build(command, timeout)
                if second[0] is None or second[0] != 0:
                    reason = "timed out" if second[0] is None else f"exited {second[0]}"
                    return self._give_up(before, f"rebuild failed. `{command}` {reason}. {_tail(second[1])}")
                dirty = self._dirty_tracked()
                sources = sorted(seam.source_paths(dirty, generated))
                if sources:
                    return self._give_up(before, f"build changed sources {', '.join(sources)}. Rebuild must only change generated paths")
                restaged = self.git("add", "--all", "--", *[f":(literal){root}" for root in generated], check=False)
                if restaged.returncode != 0:
                    return self._give_up(before, f"rebuild failed. `git add` exited {restaged.returncode}. {_tail(restaged.stderr + restaged.stdout)}")
                again = self._generated_snapshot()
                if again is None:
                    return self._give_up(before, "rebuild failed. `git ls-files` for generated paths failed")
                if again != snapshot:
                    return self._give_up(before, "build did not reproduce generated entries")
            added = self.git("add", "--all", "--", *[f":(literal){root}" for root in generated], check=False)
            if added.returncode != 0:
                return self._give_up(before, f"rebuild failed. `git add` exited {added.returncode}. {_tail(added.stderr + added.stdout)}")
            if self.git("diff", "--cached", "--quiet", check=False).returncode != 0:
                amend = self.git(*seam.committer(self.path), "commit", "--amend", "--no-edit", check=False)
                if amend.returncode != 0:
                    return self._give_up(before, f"rebuild failed. `git commit --amend` exited {amend.returncode}. {_tail(amend.stderr + amend.stdout)}")
            return None
        except BaseException as error:
            self._give_up(before, "")
            if isinstance(error, Exception):
                return f"rebuild failed. `{command}` {type(error).__name__}: {error}"
            raise

    def apply(self, entry, trunk_tip=None):
        """Replay the pinned commits onto HEAD. Returns None, or why the entry cannot land."""
        before = self.head()
        spec = f"{entry['base']}..{seam.pin_ref(entry['sha'])}"
        identity = seam.committer(self.path)
        result = self.git(*identity, "-c", "rerere.enabled=false", "cherry-pick", spec, check=False)
        result = self._resolve_generated(before, identity, result)
        # git 2.43 has no cherry-pick --empty=drop. Git added it in 2.45. Drop a commit that
        # became empty, and stop on a commit that started empty. That matches --empty=drop.
        if result.returncode != 0:
            counted = self.git("rev-list", "--count", spec, check=False)
            remaining = int(counted.stdout.strip() or "0") if counted.returncode == 0 else 0
            while result.returncode != 0 and remaining > 0 and not cherry_pick_stopped_for_conflict(result):
                picked = self.git("rev-parse", "-q", "--verify", "CHERRY_PICK_HEAD", check=False)
                if picked.returncode != 0:
                    break
                sha = picked.stdout.strip()
                parent_tree = self.git("rev-parse", "-q", "--verify", f"{sha}^^{{tree}}", check=False)
                own_tree = self.git("rev-parse", "-q", "--verify", f"{sha}^{{tree}}", check=False)
                started_empty = parent_tree.returncode == 0 and parent_tree.stdout.strip() == own_tree.stdout.strip()
                dirty = (self.git("diff", "--quiet", check=False).returncode != 0
                         or self.git("diff", "--cached", "--quiet", check=False).returncode != 0)
                unmerged = any(chunk for chunk in self.git("ls-files", "-u", "-z", check=False, exact=True).stdout.split("\0"))
                if started_empty or dirty or unmerged:
                    break
                result = self.git(*identity, "-c", "rerere.enabled=false", "cherry-pick", "--skip", check=False)
                remaining -= 1
                result = self._resolve_generated(before, identity, result)
        if result.returncode != 0:
            self.git("cherry-pick", "--abort", check=False)
            if cherry_pick_stopped_for_conflict(result):
                return self._give_up(before, "conflict with trunk")
            detail = (result.stderr + result.stdout).strip()
            self._give_up(before, "")
            raise seam.Infrastructure("cherry-pick failed: " + (detail.splitlines() or ["no output"])[-1])
        changed = seam.changed_paths(self.path, before, self.head())
        generated_paths = self._generated()
        sources = seam.source_paths(changed, generated_paths)
        if not sources:
            self.git("reset", "--hard", before)
            return "already-in-trunk"
        if generated_paths and self.store.contract.get("rebuild"):
            failed = self._rebuild(before)
            if failed:
                return failed
        if seam.fingerprint(self.path, before, self.head(), generated_paths) != entry["fingerprint"]:
            return self._give_up(before, "the rebased change differs from the reviewed one; review it again")
        reason = seam.foreign_author(self.store, entry)
        if reason:
            return self._give_up(before, reason)
        size_base = trunk_tip or before
        reason = seam.check_candidate_size(self.store, size_base, self.head())
        if reason:
            return self._give_up(before, reason)
        return reason

    def check(self):
        """Run setup and checks on the candidate. Returns None, or the failure. Raises when the run left the tree dirty."""
        contract = self.store.contract
        expected = self.head()
        commands = ([contract["setup"]] if contract.get("setup") else []) + contract["checks"]
        with seam.slot("landing", repo=self.path, command=" ".join(commands)):
            for command in commands:
                code, output = seam.run_build(command, self.path, contract.get("timeout") or 1800)
                if code != 0:
                    tail = " / ".join(output.strip().splitlines()[-3:])
                    reason = "timed out" if code is None else f"exited {code}"
                    return f"`{command}` {reason}: {tail}"
        if self.head() != expected or self.git("status", "--porcelain", "--untracked-files=no").stdout.strip():
            raise seam.LandError("a check changed tracked files in the integration worktree; fix the check, then run land again")
        return None
