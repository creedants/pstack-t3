"""Run the bundled mesh board, landing queue, or evidence verifier."""
import sys
from pstack_mesh import __main__ as board
from pstack_mesh import landing, verify


if __name__ == '__main__':
    component = sys.argv.pop(1) if len(sys.argv) > 1 else 'board'
    if component not in ('board', 'landing', 'verify'):
        raise SystemExit('Choose board, landing, or verify')
    raise SystemExit({'board': board.main, 'landing': landing.main, 'verify': verify.main}[component]())
