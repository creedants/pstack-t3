- kind fix
- The home-isolation guard refused a delete whose name was relative to a directory descriptor, and a
  write on an open descriptor, whenever it could not read `/proc/self/fd`. macOS has no `/proc`, so
  `_at` and `_fd_target` returned `None` and the guard refused. CI run 40128615052 on macOS shows 82
  `PermissionError`s from that cause, 79 of them reported as `because its directory descriptor cannot
  be resolved` and 3 as an `os.truncate` refusal.
- The root cause is the descriptor path lookup, not `/tmp`. The run root is a real path and both sides
  of every comparison are real paths, so the symlinked temporary directory a macOS test sees changes
  nothing.
- `tests/isolation_paths.py` now has `_fd_path`, which reads `/proc/self/fd` first and then, only where
  `fcntl` offers `F_GETPATH`, asks the kernel for the descriptor's path and accepts it only when the
  path is absolute and a stat of it names the same device and inode as the descriptor. `_at` and
  `_fd_target` call it, with the same signatures and the same `None` and `""` answers.
- `_deny_delete` refuses a delete, and an `os.rename` source, whose directory descriptor resolves
  through neither. There is no fallback proof. A name reached through a descriptor with no path is
  judged by neither ancestry nor device and inode, because a mount beneath the run root defeats both.
- `_deny_write` requires a target that names its path through a descriptor to sit inside the run root
  as well as outside every protected root, the rule deletes already used. `_placed` names that
  condition, a descriptor itself or a relative name with a directory descriptor, and `_at` uses it too.
  The final resolved path is judged, so a relative name that climbs out is refused. A path-named write
  keeps the existing protected-root rule and its checkout exemption. A rename whose destination cannot
  be resolved refuses, even when it would replace an existing entry.
- Each wrapper converts its path argument once with `os.fspath` at the boundary and hands that same
  value to the guard and the syscall. `os.mkfifo` and `os.mknod` raise no audit event, so the
  conversion has to happen at the wrapper. Without it a stateful path object reads a permitted name
  in the guard and a protected name in the syscall, and a fifo or node lands in a protected root.
- Linux through `/proc` is tightened the same way, because a descriptor tells `_deny_write` nothing
  about which resolver answered. The base allowed a descriptor write outside the run root there, and no
  suite caller makes one. The 3 `os.truncate` refusals in that run reach `_fd_target`, which now asks
  `_fd_path`, so macOS resolves them through `F_GETPATH`, and those writes are inside the run root.
- `tests/isolation_tests/test_descriptor_paths.py` runs each case but the `/proc` case in a child that
  hides `/proc/self/fd` with a patched `os.readlink`, records the real path of every descriptor
  `os.open` returns, and answers `F_GETPATH` from that record in a 1024-byte buffer, so the guard's
  stat and fstat identity check still runs. The `/proc` case keeps `/proc` visible. It covers a
  delete inside the run root through a resolved directory descriptor, a
  delete inside a protected root inside the run root that is refused with its canary intact, a delete
  through a descriptor with no readable path that is refused, and a truncate and a mkdir placed outside
  the run root that are refused with their canaries intact, plus the same outsides through the `/proc`
  branch, a truncate inside the run root that still passes, and a fifo and a node whose path object
  changes after its first read and creates only the permitted entry.
- macOS itself was not run locally. The Linux reproduction with `/proc` hidden and a simulated kernel
  answer covers the resolved descriptor and the one that resolves through neither, and the `F_GETPATH`
  call itself runs only on macOS. The four macOS workflow jobs run only on push to main, not on a pull
  request.
