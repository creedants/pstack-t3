WATCH = "workers: 0 of 2 running, 2 idle, auto-start: normal\nno work in progress"


def size_warning(size):
    return (
        "brigade: warning: "
        f"handoff.md is {size} bytes. Rewrite its current-state section in place and move the old text to an archive file.\n"
    )
