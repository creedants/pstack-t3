- kind fix
- The landing group-signal tests count a process the kernel reaps while the test reads its
  `/proc/<pid>/stat` entry as not a zombie, next to a process whose entry is already gone, so a reap
  race no longer raises `ProcessLookupError` out of either test. Both tests share one helper.
