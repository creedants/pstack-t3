"""JSON command interface. Use --data @file to avoid shell quoting issues."""
import argparse
import json
from pathlib import Path
from pstack_mesh.board import Board


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('action', choices=['register','peers','add','claim','heartbeat','finish','recover','tasks','record','events','message','delivered','messages'])
    parser.add_argument('--data', default='{}')
    args = parser.parse_args()
    try:
        raw = Path(args.data[1:]).read_text(encoding='utf-8-sig') if args.data.startswith('@') else args.data
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError('--data must contain a JSON object')
        result = getattr(Board(args.db), args.action)(**data)
    except (ValueError, TypeError, OSError) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
