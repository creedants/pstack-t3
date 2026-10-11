"""Behavior checks for the bundled peer mesh."""
import os
from pathlib import Path
import sys

MESH_SCRIPTS = Path(__file__).resolve().parents[1] / 't3/added/mesh/scripts'
sys.path.insert(0, str(MESH_SCRIPTS))
MESH_ENV = {**os.environ, 'PYTHONPATH': str(MESH_SCRIPTS) + os.pathsep + os.environ.get('PYTHONPATH', '')}

import tempfile
import unittest
from pstack_mesh.board import Board


class SeedTest(unittest.TestCase):
    def test_task_can_be_claimed_and_finished(self):
        with tempfile.TemporaryDirectory() as directory:
            board = Board(Path(directory) / 'board.sqlite')
            board.register('a', 'thread-a', Path(directory) / 'a')
            board.add('t', 'Task', 'file')
            claim = board.claim('a')
            self.assertEqual(claim['id'], 't')
            board.finish('t', 'a', claim['token'], {'status': 'PASS'})
            self.assertEqual(board.tasks()[0]['status'], 'done')
            with self.assertRaises(ValueError):
                board.finish('t', 'a', claim['token'], {})


if __name__ == '__main__':
    unittest.main()
