import runpy
import unittest

from .support import SCRIPT, setUpModule


class LoaderTest(unittest.TestCase):
    def test_spend_file_is_one_module_object_under_brigade_lib_name(self):
        load = runpy.run_path(str(SCRIPT))
        self.assertEqual(load["seam"].__name__, "brigade_lib.seam")
        spend_through_reporting = load["report"].__globals__["spend"]
        self.assertEqual(spend_through_reporting.__name__, "brigade_lib.spend")
        self.assertIs(spend_through_reporting.__dict__, load["spend_text"].__globals__)
