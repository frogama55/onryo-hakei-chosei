"""画面のテスト。実際のマウスイベントを送り、処理の結果まで確認する。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import soundfile as sf
from PyQt6.QtCore import QEventLoop, QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PyQt6.QtGui import QDropEvent, QNativeGestureEvent, QPointingDevice, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton

from app_info import APP_NAME
from audio.loader import ensure_tools, load_file
from ui.main_window import HINTS, MainWindow
from ui.waveform_widget import DB_MAX, DB_MIN, MODE_DRAG, MODE_LINE
from utils import edit_store

ensure_tools()
app = QApplication.instance() or QApplication([])
SR = 48000
LEFT = Qt.MouseButton.LeftButton
REAL_CONFIRM_CLOSE = MainWindow._confirm_close
REAL_SHOW_EXPORTED = MainWindow._show_exported
RIGHT = Qt.MouseButton.RightButton


def wait_until(condition, timeout=20000):
    remaining = timeout
    while not condition() and remaining > 0:
        loop = QEventLoop()
        QTimer.singleShot(10, loop.quit)
        loop.exec()
        remaining -= 10
    return condition()


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patcher = mock.patch.object(edit_store, 'STORE_DIR', self.dir / 'store')
        patcher.start()
        self.addCleanup(patcher.stop)
        # 後片付けで閉じるときに確認のダイアログで止まらないようにする
        confirm = mock.patch.object(MainWindow, '_confirm_close', return_value=True)
        confirm.start()
        self.addCleanup(confirm.stop)
        exported = mock.patch.object(MainWindow, '_show_exported')
        self.show_exported = exported.start()
        self.addCleanup(exported.stop)

        self.src = str(self.dir / 'test.wav')
        t = np.arange(SR * 10) / SR
        sf.write(self.src, (0.1 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), SR, subtype='PCM_24')

        self.win = MainWindow()
        self.win.resize(1000, 600)
        self.win.show()
        self.addCleanup(self.win.close)
        self.win.load_path(self.src)
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        self.wave = self.win._waveform

    def point(self, t, gain):
        return QPoint(int(self.wave._time_to_x(t)), int(self.wave._gain_to_y(gain)))

    def edit_mode(self):
        QTest.mouseClick(self.win._line_btn, LEFT)
        self.assertEqual(self.wave.mode(), MODE_LINE)

    def add_point(self, t, gain):
        QTest.mouseClick(self.wave, LEFT, pos=self.point(t, gain))

    def drag(self, start, end):
        QTest.mousePress(self.wave, LEFT, pos=start)
        QTest.mouseMove(self.wave, end)
        QTest.mouseRelease(self.wave, LEFT, pos=end)

    def env(self):
        return self.win._envelope.get_points()

    def test_about_has_required_notices(self):
        text = self.win.about_text()
        self.assertIn('This software uses libraries from the FFmpeg project under the LGPLv2.1', text)
        self.assertIn('General Public License', text)
        self.assertIn(APP_NAME, text)
        self.assertIn('かねごん', text)
        self.assertIn('https://github.com/frogama55/onryo-hakei-chosei', text)

    def test_loaded_state(self):
        self.assertIn('test.wav', self.win.windowTitle())
        self.assertIn(APP_NAME, self.win.windowTitle())
        self.assertAlmostEqual(self.win._duration, 10.0)
        self.assertTrue(self.win._toolbar.isEnabled())

    def test_paint_before_and_after_load(self):
        self.assertFalse(MainWindow().grab().isNull())
        self.assertFalse(self.win.grab().isNull())

    def test_click_adds_point_above_zero(self):
        self.edit_mode()
        self.add_point(5.0, 12.0)
        self.assertEqual(len(self.env()), 3)
        self.assertAlmostEqual(self.env()[1][1], 12.0, delta=0.6)

    def test_click_in_drag_mode_seeks(self):
        QTest.mouseClick(self.wave, LEFT, pos=self.point(5.0, 0.0))
        self.assertAlmostEqual(self.win._player.position_sec, 5.0, delta=0.1)
        self.assertEqual(len(self.env()), 2)

    def test_drag_point_and_undo_redo(self):
        self.edit_mode()
        self.add_point(5.0, 6.0)
        self.drag(self.point(5.0, self.env()[1][1]), self.point(6.0, -12.0))
        moved = self.env()
        self.assertAlmostEqual(moved[1][0], 6.0, delta=0.05)
        self.assertAlmostEqual(moved[1][1], -12.0, delta=0.6)

        self.win._undo()
        self.assertAlmostEqual(self.env()[1][0], 5.0, delta=0.05)
        self.win._undo()
        self.assertEqual(len(self.env()), 2)
        self.win._redo()
        self.win._redo()
        self.assertEqual(self.env(), moved)

    def test_right_click_removes_point(self):
        self.edit_mode()
        self.add_point(5.0, 6.0)
        QTest.mouseClick(self.wave, RIGHT, pos=self.point(5.0, self.env()[1][1]))
        self.assertEqual(len(self.env()), 2)

    def test_reset_can_be_undone(self):
        self.edit_mode()
        self.add_point(5.0, 6.0)
        self.win._reset_envelope()
        self.assertTrue(self.win._envelope.is_flat())
        self.win._undo()
        self.assertEqual(len(self.env()), 3)

    def test_edit_is_restored_on_reopen(self):
        self.edit_mode()
        self.add_point(5.0, 6.0)
        points = self.env()

        other = MainWindow()
        self.addCleanup(other.close)
        other.load_path(self.src)
        self.assertTrue(wait_until(lambda: other._worker is None))
        self.assertEqual(other._envelope.get_points(), points)
        self.assertIn('復元', other._status.currentMessage())

    # ------------------------------------------------------------------
    # ドラッグ編集
    # ------------------------------------------------------------------

    def db_px(self, db):
        return round(db / (DB_MAX - DB_MIN) * self.wave.height())

    def on_wave(self, t):
        """調整後の波形の上の位置。"""
        pos = self.wave._point_pos(t, 0.0)
        return QPoint(int(pos.x()), int(pos.y()))

    def test_drag_mode_is_ready_after_load(self):
        self.assertEqual(self.wave.mode(), MODE_DRAG)
        self.assertTrue(self.win._drag_btn.isChecked())
        self.assertEqual(self.win._hint.text(), HINTS[MODE_DRAG])

    def test_drag_waveform_up_raises_that_part(self):
        start = self.on_wave(5.0)
        end = start - QPoint(0, self.db_px(6.0))
        self.drag(start, end)
        env = self.win._envelope
        self.assertAlmostEqual(env.gain_db_at(5.0), 6.0, delta=0.3)
        # 離れた所は変わらない
        self.assertEqual(env.gain_db_at(1.0), 0.0)
        self.assertEqual(env.gain_db_at(9.0), 0.0)
        self.assertEqual(len(self.env()), 5)  # 両端、つまんだ点、左右の固定点
        # 点は波形の上にあり、マウスに付いてきている
        idx = [t for t, _ in self.env()].index(min((t for t, _ in self.env()), key=lambda t: abs(t - 5.0)))
        pos = self.wave._point_pos(*self.env()[idx])
        self.assertAlmostEqual(pos.y(), end.y(), delta=2)

    def test_drag_waveform_is_one_undo_step(self):
        start = self.on_wave(5.0)
        self.drag(start, start + QPoint(0, self.db_px(10.0)))
        self.assertAlmostEqual(self.win._envelope.gain_db_at(5.0), -10.0, delta=0.3)
        self.win._undo()
        self.assertEqual(len(self.env()), 2)
        self.assertTrue(self.win._envelope.is_flat())

    def test_drag_existing_point_again(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        count = len(self.env())
        again = self.on_wave(5.0)
        self.drag(again, again - QPoint(0, self.db_px(4.0)))
        self.assertEqual(len(self.env()), count)
        self.assertAlmostEqual(self.win._envelope.gain_db_at(5.0), 10.0, delta=0.4)

    def test_drag_back_to_start_leaves_nothing(self):
        start = self.on_wave(5.0)
        QTest.mousePress(self.wave, LEFT, pos=start)
        QTest.mouseMove(self.wave, start - QPoint(0, 40))
        QTest.mouseMove(self.wave, start)
        QTest.mouseRelease(self.wave, LEFT, pos=start)
        self.assertEqual(len(self.env()), 2)
        self.assertFalse(self.win._undo_act.isEnabled())

    def test_small_wobble_is_a_click(self):
        start = self.on_wave(5.0)
        self.drag(start, start + QPoint(8, 1))
        self.assertEqual(len(self.env()), 2)
        self.assertAlmostEqual(self.win._player.position_sec, 5.0, delta=0.1)

    def test_second_grab_nearby_does_not_pin_inside_bump(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        count = len(self.env())
        # 右の固定点との間をつまむ。間に点があるので、新しい固定点は置かない
        t = 5.0 + self.wave._x_to_time(30) - self.wave._x_to_time(0)
        nearby = self.on_wave(t)
        self.drag(nearby, nearby - QPoint(0, self.db_px(3.0)))
        self.assertEqual(len(self.env()), count + 1)

    def test_right_click_removes_point_in_drag_mode(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        count = len(self.env())
        QTest.mouseClick(self.wave, RIGHT, pos=self.on_wave(5.0))
        self.assertEqual(len(self.env()), count - 1)

    def test_switching_mode_keeps_points(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        points = self.env()
        self.edit_mode()
        self.assertEqual(self.env(), points)
        self.assertEqual(self.win._hint.text(), HINTS[MODE_LINE])
        self.assertFalse(self.win.grab().isNull())
        QTest.mouseClick(self.win._drag_btn, LEFT)
        self.assertEqual(self.wave.mode(), MODE_DRAG)

    def test_drag_when_zoomed_uses_visible_time(self):
        self.wave.set_view(6.0, 2.0)
        start = self.on_wave(7.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        env = self.win._envelope
        self.assertAlmostEqual(env.gain_db_at(7.0), 6.0, delta=0.3)
        self.assertEqual(env.gain_db_at(5.0), 0.0)

    def test_zoom_buttons(self):
        QTest.mouseClick(self.win._zoom_in_btn, LEFT)
        self.assertAlmostEqual(self.wave._view_end - self.wave._view_start, 5.0)
        QTest.mouseClick(self.win._zoom_out_btn, LEFT)
        self.assertAlmostEqual(self.wave._view_end - self.wave._view_start, 10.0)
        QTest.mouseClick(self.win._zoom_in_btn, LEFT)
        QTest.mouseClick(self.win._show_all_btn, LEFT)
        self.assertAlmostEqual(self.wave._view_end - self.wave._view_start, 10.0)

    def test_pinch_zooms(self):
        before = self.wave._x_to_time(250.0)
        event = QNativeGestureEvent(Qt.NativeGestureType.ZoomNativeGesture, QPointingDevice.primaryPointingDevice(),
                                    2, QPointF(250, 100), QPointF(250, 100), QPointF(250, 100), 0.5, QPointF(0, 0))
        QApplication.sendEvent(self.wave, event)
        self.assertAlmostEqual(self.wave._view_end - self.wave._view_start, 10.0 / 1.5, delta=0.01)
        self.assertAlmostEqual(self.wave._x_to_time(250.0), before, delta=0.02)

    def test_zoom_keeps_time_under_cursor(self):
        before = self.wave._x_to_time(250.0)
        event = QWheelEvent(QPointF(250, 100), QPointF(250, 100), QPoint(0, 0), QPoint(0, 600), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self.wave.wheelEvent(event)
        self.assertLess(self.wave._view_end - self.wave._view_start, 10.0)
        self.assertAlmostEqual(self.wave._x_to_time(250.0), before, delta=0.02)
        self.assertTrue(self.win._scroll.isVisible())

    def test_scrollbar_moves_view(self):
        self.wave.zoom(4.0, 0.0)
        self.win._scroll.setValue(5000)
        self.assertAlmostEqual(self.wave._view_start, 5.0, delta=0.01)
        self.wave.show_all()
        self.assertFalse(self.win._scroll.isVisible())

    def test_zoom_limit(self):
        self.wave.zoom(1e6, 5.0)
        self.assertAlmostEqual(self.wave._view_end - self.wave._view_start, 2.0)
        self.assertFalse(self.wave.grab().isNull())

    def test_click_when_zoomed_uses_visible_time(self):
        self.wave.set_view(6.0, 2.0)
        QTest.mouseClick(self.wave, LEFT, pos=QPoint(self.wave.width() // 2, 100))
        self.assertAlmostEqual(self.win._player.position_sec, 7.0, delta=0.05)

    def test_playhead_follows_when_leaving_view(self):
        self.wave.set_view(0.0, 2.0)
        self.wave.set_playhead(3.0, follow=True)
        self.assertLessEqual(self.wave._view_start, 3.0)
        self.assertGreaterEqual(self.wave._view_end, 3.0)

    def test_thresholds_cannot_cross(self):
        self.win._lower_spin.setValue(-3.0)
        self.assertGreater(self.win._upper_spin.value(), self.win._lower_spin.value())

    def export_to(self, name):
        out = str(self.dir / name)
        with mock.patch.object(QFileDialog, 'getSaveFileName', return_value=(out, '')):
            self.win._export_file()
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        return out

    def test_export_through_window(self):
        self.edit_mode()
        self.win._envelope.set_points([(0.0, -6.0), (10.0, -6.0)])
        out = self.export_to('out.wav')
        self.assertEqual(sf.info(out).subtype, 'PCM_24')
        self.assertLess(np.abs(load_file(out)[0]).max(), 0.06)
        self.assertIn('書き出し完了', self.win._status.currentMessage())
        self.assertFalse(self.win._progress.isVisible())

    def test_export_warns_about_clipping(self):
        self.win._envelope.set_points([(0.0, 24.0), (10.0, 24.0)])
        with mock.patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No) as q:
            out = self.export_to('no.wav')
        q.assert_called_once()
        self.assertFalse(Path(out).exists())

    def test_export_without_clipping_does_not_ask(self):
        with mock.patch.object(QMessageBox, 'question') as q:
            self.export_to('fine.wav')
        q.assert_not_called()

    def test_export_error_is_shown(self):
        with mock.patch.object(QMessageBox, 'critical') as c:
            out = str(self.dir / 'x.wav')
            with mock.patch.object(QFileDialog, 'getSaveFileName', return_value=(self.src, '')):
                self.win._export_file()
            self.assertTrue(wait_until(lambda: self.win._worker is None))
        c.assert_called_once()
        self.assertIn('上書きできません', c.call_args[0][2])
        self.assertFalse(Path(out).exists())

    def test_load_error_is_shown(self):
        bad = self.dir / 'bad.m4a'
        bad.write_bytes(b'nope')
        with mock.patch.object(QMessageBox, 'critical') as c:
            self.win.load_path(str(bad))
            self.assertTrue(wait_until(lambda: self.win._worker is None))
        c.assert_called_once()
        self.assertTrue(self.win._toolbar.isEnabled())
        self.assertIn('test.wav', self.win.windowTitle())

    def test_drop_opens_file(self):
        other = str(self.dir / 'dropped.wav')
        sf.write(other, np.zeros(SR, dtype=np.float32), SR)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(other)])
        self.win.dropEvent(QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                                      LEFT, Qt.KeyboardModifier.NoModifier))
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        self.assertIn('dropped.wav', self.win.windowTitle())

    def test_space_key_toggles_play(self):
        with mock.patch.object(self.win._player, 'play') as play:
            QTest.keyClick(self.win, Qt.Key.Key_Space)
        play.assert_called_once()

    def test_space_key_plays_wherever_focus_is(self):
        for widget in [self.win._upper_spin, self.win._lower_spin, self.wave]:
            widget.setFocus()
            with mock.patch.object(self.win._player, 'play') as play:
                QTest.keyClick(widget, Qt.Key.Key_Space)
            play.assert_called_once()
        self.assertEqual(self.win._upper_spin.value(), -6.0)

    def close_asks(self, answer):
        with mock.patch.object(MainWindow, '_confirm_close', return_value=answer) as ask:
            closed = self.win.close()
        return ask.call_count, closed

    def test_close_without_changes_does_not_ask(self):
        self.assertEqual(self.close_asks(False), (0, True))

    def test_close_with_unexported_changes_asks(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        self.assertEqual(self.close_asks(False), (1, False))
        self.assertTrue(self.win.isVisible())
        self.assertEqual(self.close_asks(True), (1, True))

    def test_close_after_export_does_not_ask(self):
        self.win._envelope.set_points([(0.0, -6.0), (10.0, -6.0)])
        self.export_to('done.wav')
        self.assertEqual(self.close_asks(False), (0, True))

    def test_change_after_export_asks_again(self):
        self.win._envelope.set_points([(0.0, -6.0), (10.0, -6.0)])
        self.export_to('done.wav')
        self.win._envelope.set_points([(0.0, -3.0), (10.0, -6.0)])
        self.assertEqual(self.close_asks(False), (1, False))

    def test_undo_back_to_original_does_not_ask(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        self.win._undo()
        self.assertEqual(self.close_asks(False), (0, True))

    def test_restored_edit_counts_as_unexported(self):
        start = self.on_wave(5.0)
        self.drag(start, start - QPoint(0, self.db_px(6.0)))
        other = MainWindow()
        other.show()
        self.addCleanup(other.close)
        other.load_path(self.src)
        self.assertTrue(wait_until(lambda: other._worker is None))
        self.assertTrue(other.has_unexported_changes())

    def test_close_dialog_buttons(self):
        def answer(button_text):
            def run():
                box = QApplication.activeModalWidget()
                next(b for b in box.buttons() if b.text() == button_text).click()
            return run
        self.win._envelope.set_points([(0.0, -6.0), (10.0, -6.0)])
        QTimer.singleShot(50, answer('キャンセル'))
        self.assertFalse(REAL_CONFIRM_CLOSE(self.win))
        QTimer.singleShot(50, answer('書き出さずに閉じる'))
        self.assertTrue(REAL_CONFIRM_CLOSE(self.win))

    # ------------------------------------------------------------------
    # 編集の履歴
    # ------------------------------------------------------------------

    def history_texts(self):
        lst = self.win._history_list
        return [lst.item(i).text().split('\n')[0] for i in range(lst.count())]

    def raise_at(self, t, db):
        start = self.on_wave(t)
        self.drag(start, start - QPoint(0, self.db_px(db)))

    def click_history(self, row):
        lst = self.win._history_list
        QTest.mouseClick(lst.viewport(), LEFT, pos=lst.visualItemRect(lst.item(row)).center())

    def test_history_lists_edits_with_time(self):
        self.raise_at(3.0, 6.0)
        self.raise_at(7.0, -6.0)
        texts = self.history_texts()
        self.assertEqual(texts[0], 'ファイルを開く')
        self.assertTrue(texts[1].startswith('音量を変更 +6.'), texts[1])
        self.assertIn('（0:03）', texts[1])
        self.assertTrue(texts[2].startswith('音量を変更 -6.'), texts[2])
        second_line = self.win._history_list.item(1).text().split('\n')[1]
        self.assertRegex(second_line, r'^\d{1,2}/\d{1,2} \d\d:\d\d:\d\d$')
        self.assertEqual(self.win._history_list.currentRow(), 2)

    def test_history_labels_for_each_operation(self):
        self.raise_at(5.0, 6.0)
        again = self.on_wave(5.0)
        self.drag(again, again - QPoint(0, self.db_px(2.0)))
        QTest.mouseClick(self.wave, RIGHT, pos=self.on_wave(5.0))
        self.edit_mode()
        self.add_point(2.0, 6.0)
        self.win._reset_envelope()
        labels = [t.split(' ')[0].split('（')[0] for t in self.history_texts()]
        self.assertEqual(labels, ['ファイルを開く', '音量を変更', '点を動かす', '点を削除', '点を追加',
                                  '調整をリセット'])

    def test_click_history_item_rewinds_and_keeps_later_items(self):
        self.raise_at(3.0, 6.0)
        after_first = self.env()
        self.raise_at(7.0, 6.0)
        self.click_history(1)
        self.assertEqual(self.env(), after_first)
        self.assertEqual(self.win._history_list.count(), 3)
        self.assertEqual(self.win._history_list.currentRow(), 1)
        self.assertTrue(self.win._redo_btn.isEnabled())
        self.click_history(0)
        self.assertTrue(self.win._envelope.is_flat())
        self.click_history(2)
        self.assertAlmostEqual(self.win._envelope.gain_db_at(7.0), 6.0, delta=0.3)

    def test_new_edit_after_rewind_clears_later_items(self):
        self.raise_at(3.0, 6.0)
        self.raise_at(7.0, 6.0)
        self.click_history(1)
        self.raise_at(5.0, -3.0)
        self.assertEqual(len(self.history_texts()), 3)
        self.assertTrue(self.history_texts()[2].startswith('音量を変更 -3.'))
        self.assertFalse(self.win._redo_btn.isEnabled())

    def test_back_and_forward_buttons(self):
        self.assertFalse(self.win._undo_btn.isEnabled())
        self.raise_at(5.0, 6.0)
        changed = self.env()
        QTest.mouseClick(self.win._undo_btn, LEFT)
        self.assertTrue(self.win._envelope.is_flat())
        self.assertFalse(self.win._undo_btn.isEnabled())
        QTest.mouseClick(self.win._redo_btn, LEFT)
        self.assertEqual(self.env(), changed)

    def test_command_z_goes_back(self):
        self.raise_at(5.0, 6.0)
        QTest.keyClick(self.win, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)  # macOS では ⌘Z
        self.assertTrue(self.win._envelope.is_flat())
        self.assertEqual(self.win._history_list.currentRow(), 0)

    def test_rewind_is_saved_for_reopen(self):
        self.raise_at(5.0, 6.0)
        self.click_history(0)
        self.assertEqual(edit_store.load_points(self.src), None)

    def test_history_starts_over_with_restore_label(self):
        self.raise_at(5.0, 6.0)
        self.win.load_path(self.src)
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        self.assertEqual(self.history_texts(), ['前回の編集を復元'])

    # ------------------------------------------------------------------
    # 保存の状態
    # ------------------------------------------------------------------

    def status(self):
        return {name: value for name, value, _ in self.win.save_status()}

    def test_save_status_after_open(self):
        self.assertEqual(self.status(), {'元のファイル': '変更しません', '編集内容': '調整なし', '書き出し': '—'})
        self.assertIn('変更しません', self.win._save_label.text())
        self.assertIn('元のファイルは変更しません', self.win._status.currentMessage())

    def test_save_status_before_open(self):
        other = MainWindow()
        self.addCleanup(other.close)
        self.assertEqual({v for _, v, _ in other.save_status()}, {'—'})

    def test_save_status_follows_edit_and_export(self):
        self.raise_at(5.0, 6.0)
        self.assertTrue(self.status()['編集内容'].startswith('自動保存済み '))
        self.assertEqual(self.status()['書き出し'], 'まだです')
        out = self.export_to('done.wav')
        self.assertEqual(self.status()['書き出し'], '済み')
        self.show_exported.assert_called_once_with(out)
        self.assertIn(out, self.win._save_label.toolTip())
        self.raise_at(2.0, -6.0)
        self.assertEqual(self.status()['書き出し'], 'その後に変更あり')
        self.win._undo()
        self.assertEqual(self.status()['書き出し'], '済み')

    def test_save_status_when_restored(self):
        self.raise_at(5.0, 6.0)
        self.win.load_path(self.src)
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        self.assertEqual(self.status()['編集内容'], '保存済み（前回の内容）')
        self.assertEqual(self.status()['書き出し'], 'まだです')
        self.assertIn('前回の編集内容を復元', self.win._status.currentMessage())

    def test_save_status_shows_autosave_failure(self):
        with mock.patch.object(edit_store, 'save_points', return_value=False):
            self.raise_at(5.0, 6.0)
        self.assertEqual(self.status()['編集内容'], '保存できません')
        self.raise_at(2.0, 3.0)
        self.assertTrue(self.status()['編集内容'].startswith('自動保存済み '))

    def test_autosave_failure_is_reported_by_store(self):
        with mock.patch.object(edit_store, 'STORE_DIR', Path('/dev/null/none')):
            self.assertFalse(edit_store.save_points(self.src, [(0.0, 3.0), (10.0, 3.0)]))
        self.assertTrue(edit_store.save_points(self.src, [(0.0, 3.0), (10.0, 3.0)]))

    def test_command_s_explains_autosave(self):
        self.raise_at(5.0, 6.0)
        QTest.keyClick(self.win, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)  # macOS では ⌘S
        self.assertIn('自動で保存されています', self.win._status.currentMessage())
        self.assertIn('新しいファイルに書き出す', self.win._status.currentMessage())

    def test_names_say_a_new_file_is_made(self):
        texts = [b.text() for b in self.win._toolbar.findChildren(QPushButton)]
        self.assertIn('新しいファイルに書き出す', texts)
        title, body = self.win.save_help_text()
        self.assertIn('元のファイルを書き換えません', title)
        for word in ['自動で保存', 'アプリ専用の場所', '新しいファイル', '音声のファイルではありません']:
            self.assertIn(word, body)

    def test_export_dialog_can_reveal_in_finder(self):
        def click(text):
            def run():
                box = QApplication.activeModalWidget()
                self.assertIn('元のファイルはそのまま', box.informativeText())
                next(b for b in box.buttons() if b.text() == text).click()
            return run
        with mock.patch('ui.main_window.subprocess.run') as run:
            QTimer.singleShot(50, click('OK'))
            REAL_SHOW_EXPORTED(self.win, '/tmp/x.wav')
            run.assert_not_called()
            QTimer.singleShot(50, click('Finder で表示'))
            REAL_SHOW_EXPORTED(self.win, '/tmp/x.wav')
            run.assert_called_once_with(['open', '-R', '/tmp/x.wav'], check=False)

    def test_close_is_refused_during_work(self):
        self.win.load_path(self.src)
        self.assertFalse(self.win.close())
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        wait_until(lambda: self.win._toolbar.isEnabled())

    def test_silent_audio_plays_to_end_and_button_returns(self):
        quiet = str(self.dir / 'quiet.wav')
        sf.write(quiet, np.zeros(SR // 4, dtype=np.float32), SR)
        self.win.load_path(quiet)
        self.assertTrue(wait_until(lambda: self.win._worker is None))
        self.win._toggle_play()
        self.assertEqual(self.win._transport._play_btn.text(), '⏸')
        self.assertTrue(wait_until(lambda: not self.win._player.is_playing, 3000))
        self.assertEqual(self.win._transport._play_btn.text(), '▶')
