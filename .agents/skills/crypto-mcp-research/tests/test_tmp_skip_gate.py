import unittest


class TmpSkipGate(unittest.TestCase):
    @unittest.skip("deliberate gate check - throwaway, do not merge")
    def test_deliberately_skipped(self):
        pass
