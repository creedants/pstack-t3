# The refusal edits verify prints when the applied change counts more than the cap, with the count filled in.
def cap_refusal(count):
    return ("brigade: the applied change counts {count} lines, and a small code fix counts at most 20; it needs a "
            "second review. The count is, for each changed file, the largest added plus deleted total git diff "
            "--numstat reports between the file at the reviewed commit and that file with the edits applied under "
            "myers, minimal, patience, and histogram, and every line of both files when git calls either one binary. "
            "A path ending in .md is not counted, and neither is a .py path whose AST is the same once its docstrings "
            "are removed, so in a .py path a change to a comment, a docstring, or formatting alone is not counted. A "
            "change to a printed message or to code on a test line changes that AST and is counted").format(count=count)
