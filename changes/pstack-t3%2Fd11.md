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
- `tests/isolation_paths.py` also has `_owned_leaf`, a delete-only proof used when no path can be read.
  It refuses a name that is empty, absolute, `.`, `..`, or compound, refuses when any protected root is
  the run root, inside it, or above it, duplicates the directory descriptor, and walks up with `..` at
  most 256 levels until a level's device and inode are the run root's. It answers `False` for every
  failure and closes only the descriptors it opened.
- `_deny_delete` uses that proof in one place, and only when `_at` returned `None`. Every other refusal
  keeps its behavior, including on macOS. A write through a `dir_fd` that cannot be resolved still
  refuses, and a rename whose new destination cannot be resolved still refuses. No observed failure
  needed those allowances. The 3 `os.truncate` refusals in that run reach `_fd_target`, which now asks
  `_fd_path`, so macOS resolves them through `F_GETPATH`.
- `tests/isolation_tests/test_descriptor_paths.py` reproduces both sides of the delete rule in a child
  that hides `/proc/self/fd` with a patched `os.readlink` and runs under a `TMPDIR` that is a symlink.
  The child writes and removes inside its run root through a symlink alias, then is refused a delete
  beside its run root through a directory descriptor and a write in the protected real home through an
  alias of it.
- macOS itself was not run locally. The macOS branch of `_fd_path` is covered by reasoning about
  `F_GETPATH` and by the Linux reproduction with `/proc` hidden, which raises the same `OSError` the
  guard sees on macOS.
