"""Run one local check under the portable machine-wide capacity lock."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def capacity_lock(state_root=None):
    from pstack_mesh.landing import repository_lock
    root = Path(state_root or os.environ.get('PSTACK_MESH_CAPACITY_STATE') or Path.home() / '.pstack-t3/capacity').resolve()
    root.mkdir(parents=True, exist_ok=True)
    return repository_lock(root / 'check.lock')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('Supply a check command after --')
    try:
        with capacity_lock(args.state_root):
            return subprocess.call(command, shell=False)
    except ValueError as error:
        print('Check capacity slot is busy: ' + str(error), file=sys.stderr)
        return 75
    except OSError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
