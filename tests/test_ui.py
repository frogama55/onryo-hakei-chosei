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
from PyQt6.QtGui import QDropEvent, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app_info import APP_NAME
from audio.loader import ensure_tools, load_file
from ui.main_window import MainWindow
from utils import edit_store

ensure_tools()
app = QApplication.instance() or QApplication([])
SR = 48000
LEFT = Qt.MouseButton.LeftButton
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
        self.win._mode_btn.setChecked(True)

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

    def test_click_outside_edit_mode_seeks(self):
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
