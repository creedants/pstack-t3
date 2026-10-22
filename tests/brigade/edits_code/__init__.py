# The refusal edits verify prints when the applied change counts more than the cap, with the count filled in.
def cap_refusal(count):
    return ("brigade: the applied change counts {count} lines, and a small code fix counts at most 20; it needs a "
            "second review. The count is each line the edits add or delete from the file at the reviewed commit to that "
            "file with the edits applied, so a replaced line counts two, and a last line with no newline differs from "
            "that line with a newline. A path ending in .md is not counted, and neither is a .py path whose AST is the "
            "same once its docstrings are removed, so a change to a comment, a docstring, or formatting alone is not "
            "counted. A change to a printed message or to code on a test line changes that AST and is counted").format(count=count)
