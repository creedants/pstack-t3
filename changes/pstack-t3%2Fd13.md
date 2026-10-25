- kind fix
- A guarded process protected only the roots its environment named. A child started with
  `env={"PATH": ...}` inherits no pointer, no token, and no protected-root record, so it made a fresh
  run root and protected only the real home from pwd. The reproduction at `adee1f0a` wrote, truncated,
  and made a directory inside the parent's external XDG root. Only the delete was refused, and only
  because a delete must sit inside the run root.
- `tests/isolation_roots.py` publishes one record per guarded process at
  `<base>/pstack-t3-isolation-<uid>/<pid>.json` under the first of `/tmp`, `/var/tmp`, and `/usr/tmp`
  that is a directory and is outside every protected root, because a child with no environment cannot
  be told where to look. The directory is mode 0700 and the record 0600. A child with no pointer reads
  the record of `os.getppid()` and protects its roots too.
- A record that exists is proof the parent was guarded, so a child that cannot read it stops. A record
  that is not JSON, is not an object, names another uid or pid, or holds a root that is not an absolute
  path raises SystemExit with the module's message prefix.
- `install()` adds the inherited roots to the roots its environment names, `_record()` publishes this
  process's merged roots to its own record after it writes `PSTACK_TEST_PROTECTED_ROOTS`, and `install()`
  removes the records of dead processes only before it arms the guard, since the guard refuses a delete
  outside the run root.
- `os.path.realpath` follows symlinks and not mounts. A bind mount of a protected directory at a name
  inside the run root therefore looked like the run root itself, and a real bind mount at `adee1f0a` let
  a write, a rename, and a delete through and destroyed the protected canaries.
- `tests/isolation_paths.py` reads `/proc/self/mountinfo` at check time, decodes the octal escapes for
  space, tab, newline, and the escape character, and turns a path into the device and on-device path of
  the longest mount point that covers it. One directory then has one identity under every spelling, and
  a bind mount of any part of a protected root produces an identity under that root's identity.
- `_protected_write` and `_owned` refuse a write, delete, truncate, or rename that reaches protected
  storage by that identity as well as by spelling, and they moved from `tests/isolation.py` to
  `tests/isolation_paths.py` so each check asks the mount table at the same module level. The checkout
  exemption stays first and unchanged. macOS has no mount table, so the table is empty there and the
  spelling rules stand alone.
- The two regression modules cover a guarded driver that protects an external XDG state root and starts
  one child per entry point with a minimal environment through `subprocess.run`, `subprocess.Popen`,
  `os.spawnve`, and `os.fork` plus `os.execve`, the fail-closed case of a record replaced with text that
  is not a record, and a bind mount and an answered mount table that each require the write, truncate,
  rename, and delete to be refused with both canaries intact.
- `tests/isolation_tests/test_mount_alias.py` now registers its mounted directory with
  `isolation.protecting` around the four attempts. The guard protects the roots it is given and resolves
  nothing by mount, and the module never told it that directory was protected, so the write and truncate
  had nothing to refuse. No assertion was weakened.
- Not proven is native macOS, where `/proc/self/mountinfo` is absent by design, and the `F_GETPATH`
  branch is unchanged. Multiprocessing gets no test, because a fork child inherits the armed guard and
  the run root in memory and a spawn child inherits the whole environment, so neither drops the guard's
  variables.
