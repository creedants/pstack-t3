- kind fix
- Before this fix, a Python child started with `env={"PATH": ...}` inherited no pointer, no token,
  and no protected-root record, so it made a fresh
  run root and protected only the real home from pwd. The reproduction at `adee1f0a` wrote, truncated,
  and made a directory inside the parent's external XDG root. Only the delete was refused, and only
  because a delete must sit inside the run root.
- `tests/isolation_roots.py` publishes one record per guarded process at
  `<base>/pstack-t3-isolation-<uid>/<pid>.json` under the first of `/tmp`, `/var/tmp`, and `/usr/tmp`
  that is a directory and is outside every protected root, because a child with no environment cannot
  be told where to look. The directory is made with mode 0700 and must have no group or other write
  bit. New record files request mode 0600. A child with no pointer reads the records of its own pid,
  which covers a process that replaced itself with a stripped environment, and of `os.getppid()`,
  which covers a fresh child of a guarded process.
- A child stops when a record it finds cannot be read. A
  live record that is not JSON, is not an object, names another uid or pid, or holds a root that is not an
  absolute path raises SystemExit with the module's message prefix.
- `install()` with no pointer adds the inherited roots to the roots its environment names, `_record()`
  publishes this process's active protection to its own record after it writes
  `PSTACK_TEST_PROTECTED_ROOTS`, and `install()` removes the records of dead processes only before it
  arms the guard, since the guard refuses a delete outside the run root.
- Round 2 closes five gaps the review reproduced. A record is read from every candidate directory, not
  the first one that holds the pid, so a stale or forged first record cannot hide the genuine later
  one. A candidate directory must be a directory this user owns with no group or other write bit, and a
  record file must be a private single-link regular file this user owns; either one that fails stops
  the child. `publish()` writes a fresh temporary file and renames it over the record name through a
  descriptor of the verified directory, so a symlink or a shared file at that name is replaced and
  never written through, and `prune()` lists and deletes only through the same descriptor. The record
  directory itself must sit outside every protected root, and a candidate that does not is skipped. A
  record carries the process start time from `/proc/<pid>/stat` where that file exists, so on Linux a
  record a dead process left under a reused pid is ignored.
- A guarded process publishes its active protection again when it forks and when `protecting()` adds a
  root, so a minimal-environment child of a fork child, and a child started inside a `protecting()`
  block, protect the same roots. The fork publish runs in an `after_in_child` callback, and a child
  that cannot publish exits instead of running with fewer roots. `protecting()` publishes the roots
  it adds on entry and republishes the restored set on exit, so the record follows the active set.
- `os.path.realpath` follows symlinks and not mounts. A bind mount of a protected directory at a name
  inside the run root therefore looked like the run root itself, and a real bind mount at `adee1f0a` let
  a write, a rename, and a delete through and destroyed the protected canaries.
- `tests/isolation_mounts.py` reads `/proc/self/mountinfo` at check time, decodes the octal escapes for
  space, tab, newline, and the escape character, and gives a path the device and on-device path of
  every mount point that covers it, not only the longest. A mount that a later mount hides stays in the
  table and still names storage an alias can reach, so `_reaches` compares every candidate the target
  has with every candidate each protected root has. A bind mount of any part of a protected root then
  produces an identity under that root's identity, and a stacked pair that hides the protected mount
  from its own path is refused through the hidden entry and the visible entry alike. On Linux a table
  that cannot be read, is empty, or holds a malformed entry stops the process. A non-Linux platform,
  which includes macOS, has no table, and the spelling rules stand alone there.
- `_protected_write` and `_owned` refuse a write, delete, truncate, or rename that reaches protected
  storage by an identity as well as by spelling. The checkout exemption stays first and unchanged. The
  mount functions moved from `tests/isolation_paths.py` to `tests/isolation_mounts.py`, and the SQLite
  URI parsing moved the other way, so `tests/isolation.py` and `tests/isolation_paths.py` both stay
  under 20,000 bytes.
- The regression modules cover a guarded driver that protects an external XDG state root and starts
  one child per entry point with a minimal environment through `subprocess.run`, `subprocess.Popen`,
  `os.spawnve`, and `os.fork` plus `os.execve`, the fail-closed case of a record replaced with text that
  is not a record, and a bind mount and an answered mount table that each require the write, truncate,
  rename, and delete to be refused with both canaries intact. Round 2 adds a stacked mount that hides
  the protected mount, a mount table that is missing and one that is malformed, a stale first record,
  a forged record in a world-writable directory, a record name that is a symlink, a record directory
  swapped for a symlink at the listing boundary, a record directory that is itself protected, a fork
  child that starts a minimal-environment child, and a `protecting()` block that starts one.
- `tests/isolation_tests/test_mount_alias.py` now registers its mounted directory with
  `isolation.protecting` around the four attempts. This supplies the protected root used by the
  mount-identity check. No assertion was weakened.
- Not proven is native macOS, where `/proc/self/mountinfo` is absent by design, and the `F_GETPATH`
  branch is unchanged. Multiprocessing's fork start method takes the same `after_in_child` path as
  `os.fork`, which the fork regression exercises through `os.fork`, and a spawn child inherits the
  whole environment, so it drops no guarded variable.
- On macOS `os.mkfifo` and `os.mknod` take no `dir_fd`, so the descriptor regressions for a fifo and a
  node whose path object changes after its first read raised `NotImplementedError` there, in this module
  and in the nested run `tests/isolation_tests/test_launch.py` starts. Each case now skips when the
  platform's own `os.mkfifo` or `os.mknod` is absent from `os.supports_dir_fd`, which is the function the
  guard captured before it wrapped them. Both still run on Linux.
- Round 3 closes the anchored-descriptor leak. `after_fork` clears the anchored set before it
  republishes, so a fork child keeps none of the parent's descriptor numbers as anchors. An anchored
  descriptor is honored only for a single-component relative name, a rename needs both of its
  descriptors anchored, and the unused truncate allowance is gone, so the allowance is open only
  inside an `anchored()` block. Three regressions cover a fork child, an absolute and a multi-component
  name, and a rename with one anchored descriptor.
