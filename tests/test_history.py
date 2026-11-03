"""編集の履歴のテスト。"""
import unittest

from utils.history import EditHistory

FLAT = [(0.0, 0.0), (10.0, 0.0)]


def pts(gain):
    return [(0.0, 0.0), (5.0, gain), (10.0, 0.0)]


class EditHistoryTest(unittest.TestCase):
    def setUp(self):
        self.h = EditHistory(max_entries=5)
        self.h.start('ファイルを開く', FLAT)

    def labels(self):
        return [e.label for e in self.h.entries]

    def test_push_and_undo_redo(self):
        self.h.push('a', pts(1))
        self.h.push('b', pts(2))
        self.assertEqual(self.h.go_to(self.h.index - 1), pts(1))
        self.assertTrue(self.h.can_redo())
        self.assertEqual(self.h.go_to(self.h.index + 1), pts(2))
        self.assertFalse(self.h.can_redo())

    def test_unchanged_is_not_added(self):
        self.assertFalse(self.h.push('同じ', FLAT))
        self.assertEqual(self.labels(), ['ファイルを開く'])

    def test_jump_keeps_later_entries_until_new_edit(self):
        for g in [1, 2, 3]:
            self.h.push(f'e{g}', pts(g))
        self.assertEqual(self.h.go_to(1), pts(1))
        self.assertEqual(len(self.h.entries), 4)  # 戻っただけでは消えない
        self.assertEqual(self.h.go_to(3), pts(3))
        self.h.go_to(1)
        self.h.push('new', pts(9))
        self.assertEqual(self.labels(), ['ファイルを開く', 'e1', 'new'])
        self.assertEqual(self.h.index, 2)

    def test_go_to_out_of_range_or_current(self):
        self.assertIsNone(self.h.go_to(0))
        self.assertIsNone(self.h.go_to(5))
        self.assertIsNone(self.h.go_to(-1))

    def test_oldest_entries_are_dropped(self):
        for g in range(1, 9):
            self.h.push(f'e{g}', pts(g))
        self.assertEqual(self.labels(), ['e4', 'e5', 'e6', 'e7', 'e8'])
        self.assertEqual(self.h.index, 4)

    def test_returned_points_are_copies(self):
        self.h.push('a', pts(1))
        got = self.h.go_to(0)
        got.append((1.0, 1.0))
        self.assertEqual(self.h.entries[0].points, FLAT)


if __name__ == '__main__':
    unittest.main()
