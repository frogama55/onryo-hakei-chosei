import numpy as np
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QEvent, QPointF, pyqtSignal, QRectF
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QLinearGradient,
    QPainterPath, QFont, QPixmap,
)

from audio.envelope import Envelope, GAIN_MIN_DB, GAIN_MAX_DB

DB_MIN = -64.0
DB_MAX = 0.0
CTRL_POINT_RADIUS = 7
HIT_RADIUS = 10
MIN_VIEW_SEC = 2.0
GAIN_SNAP_DB = 0.5
GAIN_TICKS = [24, 12, 6, 0, -6, -12, -24, -40, -60]
TIME_STEPS = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]

MODE_DRAG = 'drag'   # 波形そのものをつまんで上下に動かす
MODE_LINE = 'line'   # 音量の線に点を打って動かす
DRAG_START_PX = 3    # 縦にこれだけ動かすまではクリック（再生位置の移動）として扱う
ANCHOR_PX = 60       # ドラッグ編集で、つまんだ位置の左右に置く固定の点までの距離


def format_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def snap_gain(gain: float) -> float:
    if abs(gain) < GAIN_SNAP_DB:
        return 0.0
    return round(float(np.clip(gain, GAIN_MIN_DB, GAIN_MAX_DB)), 1)


class WaveformWidget(QWidget):
    envelope_committed = pyqtSignal()   # 1回の操作が確定したとき
    seek_requested = pyqtSignal(float)  # time in seconds
    view_changed = pyqtSignal(float, float)  # 表示範囲（秒）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(200)

        # Data
        self._times: np.ndarray | None = None       # waveform time axis
        self._rms_db: np.ndarray | None = None      # waveform dB values
        self._peak_db: np.ndarray | None = None
        self._duration: float = 0.0
        self._envelope: Envelope | None = None

        # Display
        self._upper_threshold: float = -6.0
        self._lower_threshold: float = -40.0
        self._playhead: float = 0.0     # seconds
        self._view_start: float = 0.0
        self._view_end: float = 0.0

        # 再生中は毎秒20回描き直すので、重い部分は作り置く
        self._background: QPixmap | None = None
        self._applied: tuple[np.ndarray, QPainterPath, list[float]] | None = None

        # Interaction state
        self._mode: str = MODE_DRAG
        self._dragging_idx: int = -1
        self._hover_idx: int = -1
        # ドラッグ編集の1回の操作
        self._press_pos: QPointF | None = None
        self._press_gain: float = 0.0
        self._before_press: list[tuple[float, float]] = []
        self._drag_moved: bool = False

        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_waveform(self, times: np.ndarray, rms_db: np.ndarray, peak_db: np.ndarray,
                      duration: float, envelope: Envelope) -> None:
        self._times = times
        self._rms_db = rms_db
        self._peak_db = peak_db
        self._duration = duration
        self._envelope = envelope
        self._playhead = 0.0
        self._end_press()
        self.show_all()

    def refresh_envelope(self) -> None:
        self._applied = None
        self.update()

    def set_playhead(self, time_sec: float, follow: bool = False) -> None:
        self._playhead = time_sec
        if follow and not self._view_start <= time_sec <= self._view_end:
            # 再生位置が表示範囲を出たら、次の範囲へ送る
            span = self._view_end - self._view_start
            self.set_view(time_sec - span * 0.05, span)
        self.update()

    def set_thresholds(self, upper: float, lower: float) -> None:
        self._upper_threshold = upper
        self._lower_threshold = lower
        self._invalidate()

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        self._end_press()
        self._hover_idx = -1
        self.setCursor(Qt.CursorShape.SizeVerCursor if mode == MODE_DRAG
                       else Qt.CursorShape.CrossCursor)
        self._invalidate()  # 波形の描き方がモードで変わる

    def set_view(self, start: float, span: float) -> None:
        span = float(np.clip(span, min(MIN_VIEW_SEC, self._duration), self._duration))
        self._view_start = float(np.clip(start, 0.0, self._duration - span))
        self._view_end = self._view_start + span
        self._invalidate()
        self.view_changed.emit(self._view_start, self._view_end)

    def show_all(self) -> None:
        self.set_view(0.0, self._duration)

    def zoom(self, factor: float, center: float | None = None) -> None:
        """factor > 1 で拡大。center の時刻が画面上で動かないようにする。"""
        if self._duration <= 0:
            return
        span = self._view_end - self._view_start
        if center is None:
            center = self._view_start + span / 2
        new_span = float(np.clip(span / factor, min(MIN_VIEW_SEC, self._duration), self._duration))
        self.set_view(center - (center - self._view_start) * new_span / span, new_span)

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _db_to_y(self, db):
        frac = (DB_MAX - db) / (DB_MAX - DB_MIN)
        return frac * self.height()

    def _gain_to_y(self, gain_db: float) -> float:
        frac = (GAIN_MAX_DB - gain_db) / (GAIN_MAX_DB - GAIN_MIN_DB)
        return frac * self.height()

    def _y_to_gain(self, y: float) -> float:
        frac = y / self.height()
        return snap_gain(GAIN_MAX_DB - frac * (GAIN_MAX_DB - GAIN_MIN_DB))

    def _time_to_x(self, t: float) -> float:
        span = self._view_end - self._view_start
        if span <= 0:
            return 0.0
        return (t - self._view_start) / span * self.width()

    def _x_to_time(self, x: float) -> float:
        span = self._view_end - self._view_start
        if span <= 0:
            return 0.0
        return float(np.clip(self._view_start + x / self.width() * span, 0.0, self._duration))

    def _columns(self, W: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """画面の横幅に合わせて間引く。何時間の音声でも描画の量は横幅で決まる。"""
        centers = self._view_start + (np.arange(W) + 0.5) / W * (self._view_end - self._view_start)
        edges = np.searchsorted(self._times, np.linspace(self._view_start, self._view_end, W + 1))
        edges = np.minimum(edges[:-1], len(self._times) - 1)
        if np.all(np.diff(edges) > 0):
            # 1ピクセルに複数フレーム: 山が消えないよう最大値を取る
            return (centers, np.maximum.reduceat(self._rms_db, edges),
                    np.maximum.reduceat(self._peak_db, edges))
        return (centers, np.interp(centers, self._times, self._rms_db),
                np.interp(centers, self._times, self._peak_db))

    def _invalidate(self) -> None:
        self._background = None
        self._applied = None
        self.update()

    def _adjusted(self) -> tuple[np.ndarray, QPainterPath, list[float]]:
        """調整後の音量（画面の列ごとの y）、その線、音割れする列を求める。"""
        if self._applied is None:
            W = self.width()
            centers, rms, peak = self._columns(W)
            gain = self._envelope.gain_db_array(centers)
            ys = self._db_to_y(np.clip(rms + gain, DB_MIN, DB_MAX))
            self._applied = (ys, self._polyline(ys),
                             [float(x) for x in np.nonzero(peak + gain > 0.0)[0]])
        return self._applied

    def _point_pos(self, t: float, gain: float) -> QPointF:
        """点を描く位置。ドラッグ編集では調整後の波形の上、ライン編集では音量の線の上。"""
        x = self._time_to_x(t)
        if self._mode == MODE_LINE:
            return QPointF(x, self._gain_to_y(gain))
        ys = self._adjusted()[0]
        return QPointF(x, float(ys[int(np.clip(x, 0, len(ys) - 1))]))

    # ------------------------------------------------------------------
    # Paint
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        W, H = self.width(), self.height()
        if self._background is None or self._background.size() != self.size() * self.devicePixelRatio():
            self._background = self._build_background(W, H)

        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._rms_db is not None and self._envelope is not None and self._view_end > self._view_start:
            if self._mode == MODE_DRAG:
                self._draw_adjusted_waveform(painter, W, H)
            else:
                if not self._envelope.is_flat():
                    self._draw_applied(painter, W, H)
                self._draw_gain_scale(painter, W, H)
                self._draw_envelope_line(painter)
            self._draw_points(painter, W)
        self._draw_playhead(painter, W, H)

    def _build_background(self, W: int, H: int) -> QPixmap:
        ratio = self.devicePixelRatio()
        pixmap = QPixmap(int(W * ratio), int(H * ratio))
        pixmap.setDevicePixelRatio(ratio)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._draw_background(p, W, H)
        self._draw_grid(p, W, H)
        self._draw_threshold_zones(p, W, H)
        if self._rms_db is not None and self._view_end > self._view_start:
            if self._mode == MODE_DRAG:
                self._draw_original_outline(p, W)
            else:
                self._draw_waveform(p, W, H)
            self._draw_time_axis(p, W, H)
        self._draw_threshold_lines(p, W, H)
        p.end()
        return pixmap

    def _draw_background(self, p: QPainter, W: int, H: int):
        p.fillRect(0, 0, W, H, QColor('#1a1a1a'))

    def _draw_grid(self, p: QPainter, W: int, H: int):
        p.setFont(QFont('Helvetica', 9))
        for db in [-6, -12, -18, -24, -32, -40, -48, -56]:
            y = int(self._db_to_y(db))
            p.setPen(QPen(QColor('#2a2a2a')))
            p.drawLine(0, y, W, y)
            p.setPen(QColor('#666'))
            p.drawText(QRectF(W - 44, y - 13, 40, 12), Qt.AlignmentFlag.AlignRight, f'{db}')

    def _draw_time_axis(self, p: QPainter, W: int, H: int):
        span = self._view_end - self._view_start
        step = next((s for s in TIME_STEPS if s >= span / 8), TIME_STEPS[-1])
        p.setFont(QFont('Helvetica', 9))
        t = np.ceil(self._view_start / step) * step
        while t <= self._view_end:
            x = self._time_to_x(t)
            p.setPen(QPen(QColor('#2a2a2a')))
            p.drawLine(QPointF(x, 0), QPointF(x, H))
            p.setPen(QColor('#888'))
            p.drawText(QRectF(x + 3, H - 15, 70, 13), Qt.AlignmentFlag.AlignLeft, format_time(t))
            t += step

    def _draw_threshold_zones(self, p: QPainter, W: int, H: int):
        y_upper = self._db_to_y(self._upper_threshold)
        y_lower = self._db_to_y(self._lower_threshold)

        # Above upper: red
        p.fillRect(QRectF(0, 0, W, y_upper), QColor(231, 76, 60, 35))
        # Between thresholds: green
        p.fillRect(QRectF(0, y_upper, W, y_lower - y_upper), QColor(39, 174, 96, 20))
        # Below lower: blue
        p.fillRect(QRectF(0, y_lower, W, H - y_lower), QColor(52, 152, 219, 20))

    @staticmethod
    def _polyline(ys) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(0.0, float(ys[0]))
        for x, y in enumerate(ys):
            path.lineTo(float(x), float(y))
        return path

    @staticmethod
    def _fill_waveform(p: QPainter, outline: QPainterPath, W: int, H: int):
        fill = QPainterPath(outline)
        fill.lineTo(float(W), float(H))
        fill.lineTo(0.0, float(H))
        fill.closeSubpath()

        grad = QLinearGradient(0, 0, 0, H)
        grad.setColorAt(0.0, QColor(26, 111, 187, 220))
        grad.setColorAt(0.5, QColor(26, 111, 187, 140))
        grad.setColorAt(1.0, QColor(26, 111, 187, 30))
        p.fillPath(fill, QBrush(grad))

        pen = QPen(QColor(100, 160, 230, 200))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawPath(outline)

    def _draw_waveform(self, p: QPainter, W: int, H: int):
        _, rms, _ = self._columns(W)
        self._fill_waveform(p, self._polyline(self._db_to_y(rms)), W, H)

    def _draw_original_outline(self, p: QPainter, W: int):
        """ドラッグ編集で、調整前の波形を薄い線で残す。"""
        _, rms, _ = self._columns(W)
        pen = QPen(QColor(200, 200, 200, 90))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawPath(self._polyline(self._db_to_y(rms)))

    def _draw_adjusted_waveform(self, p: QPainter, W: int, H: int):
        _, outline, clipped = self._adjusted()
        self._fill_waveform(p, outline, W, H)
        self._draw_clipping(p, clipped)

    def _draw_applied(self, p: QPainter, W: int, H: int):
        _, path, clipped = self._adjusted()
        pen = QPen(QColor(120, 230, 150, 220))
        pen.setWidthF(1.2)
        p.setPen(pen)
        p.drawPath(path)
        self._draw_clipping(p, clipped)

    @staticmethod
    def _draw_clipping(p: QPainter, clipped: list[float]):
        # 音割れする位置は上端に赤で示す
        for x in clipped:
            p.fillRect(QRectF(x, 0, 1, 7), QColor('#ff3b30'))

    def _draw_threshold_lines(self, p: QPainter, W: int, H: int):
        font = QFont('Helvetica', 9)
        p.setFont(font)

        for db, color_hex, label in [
            (self._upper_threshold, '#e74c3c', f'上限 {self._upper_threshold:.0f} dB'),
            (self._lower_threshold, '#e67e22', f'下限 {self._lower_threshold:.0f} dB'),
        ]:
            y = self._db_to_y(db)
            pen = QPen(QColor(color_hex))
            pen.setWidthF(1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(0, y), QPointF(W, y))

            p.setPen(QPen(QColor(color_hex)))
            p.drawText(QRectF(36, y - 16, 150, 14), Qt.AlignmentFlag.AlignLeft, label)

    def _draw_gain_scale(self, p: QPainter, W: int, H: int):
        # 増減なし（0 dB）の基準線とゲインの目盛り
        p.setFont(QFont('Helvetica', 9))
        for gain in GAIN_TICKS:
            y = min(max(self._gain_to_y(gain), 7.0), H - 21.0)  # 上下の端で切れないように
            p.setPen(QColor(255, 220, 50, 150))
            text = f'{gain:+d}' if gain else '±0'
            p.drawText(QRectF(4, y - 7, 30, 14), Qt.AlignmentFlag.AlignLeft, text)
        pen = QPen(QColor(255, 220, 50, 90))
        pen.setStyle(Qt.PenStyle.DotLine)
        p.setPen(pen)
        y0 = self._gain_to_y(0.0)
        p.drawLine(QPointF(0, y0), QPointF(W, y0))

    def _draw_envelope_line(self, p: QPainter):
        path = QPainterPath()
        for i, (t, gain) in enumerate(self._envelope.get_points()):
            pos = QPointF(self._time_to_x(t), self._gain_to_y(gain))
            if i == 0:
                path.moveTo(pos)
            else:
                path.lineTo(pos)
        pen = QPen(QColor(255, 220, 50, 220))
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    def _draw_points(self, p: QPainter, W: int):
        p.setFont(QFont('Helvetica', 9))
        for i, (t, gain) in enumerate(self._envelope.get_points()):
            pos = self._point_pos(t, gain)
            x, y = pos.x(), pos.y()
            if x < -HIT_RADIUS or x > W + HIT_RADIUS:
                continue
            is_hover = (i == self._hover_idx)
            is_drag = (i == self._dragging_idx)

            r = CTRL_POINT_RADIUS + (2 if (is_hover or is_drag) else 0)
            p.setBrush(QBrush(QColor(255, 215, 0) if not is_drag else QColor(255, 255, 100)))
            p.setPen(QPen(QColor('white'), 1.5))
            p.drawEllipse(pos, r, r)

            if is_hover or is_drag:
                p.setPen(QColor('white'))
                p.drawText(QRectF(min(x + 12, W - 110), max(y - 22, 2), 110, 14),
                           Qt.AlignmentFlag.AlignLeft, f'{gain:+.1f} dB  {format_time(t)}')

    def _draw_playhead(self, p: QPainter, W: int, H: int):
        x = self._time_to_x(self._playhead)
        pen = QPen(QColor(255, 255, 255, 200))
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawLine(QPointF(x, 0), QPointF(x, H))

        # Triangle handle at top
        p.setBrush(QBrush(QColor('white')))
        p.setPen(Qt.PenStyle.NoPen)
        tri = QPainterPath()
        tri.moveTo(x - 5, 0)
        tri.lineTo(x + 5, 0)
        tri.lineTo(x, 9)
        tri.closeSubpath()
        p.fillPath(tri, QColor('white'))

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if self._envelope is None:
            return
        pos = event.position()

        if event.button() == Qt.MouseButton.RightButton:
            self._try_remove_point(pos)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._mode == MODE_DRAG:
            # 点はまだ作らない。縦に動かしたら作り、動かさずに離したら再生位置の移動にする
            self._press_pos = pos
            self._drag_moved = False
            self._before_press = self._envelope.get_points()
            self._dragging_idx = self._find_point_at(pos)
            if self._dragging_idx >= 0:
                self._press_gain = self._before_press[self._dragging_idx][1]
            self.update()
            return

        idx = self._find_point_at(pos)
        if idx < 0:
            idx = self._envelope.add_point(self._x_to_time(pos.x()), self._y_to_gain(pos.y()))
            self._applied = None
        self._dragging_idx = idx
        self.update()

    def mouseMoveEvent(self, event):
        if self._envelope is None:
            return
        pos = event.position()

        if self._press_pos is not None:
            self._drag_waveform(pos)
        elif self._mode == MODE_LINE and self._dragging_idx >= 0:
            self._envelope.move_point(self._dragging_idx, self._x_to_time(pos.x()),
                                      self._y_to_gain(pos.y()))
            self.refresh_envelope()
        else:
            new_hover = self._find_point_at(pos)
            if new_hover != self._hover_idx:
                self._hover_idx = new_hover
                if self._mode == MODE_LINE:
                    self.setCursor(Qt.CursorShape.SizeAllCursor if new_hover >= 0
                                   else Qt.CursorShape.CrossCursor)
                self.update()

    def mouseReleaseEvent(self, event):
        if self._press_pos is not None:
            if not self._drag_moved:
                self.seek_requested.emit(self._x_to_time(self._press_pos.x()))
            elif self._envelope.get_points()[self._dragging_idx][1] == self._press_gain:
                # 動かして元の高さへ戻した: 作った点も残さない
                self._envelope.set_points(self._before_press)
                self.refresh_envelope()
            else:
                self.envelope_committed.emit()
            self._end_press()
            self.update()
        elif self._dragging_idx >= 0:
            self._dragging_idx = -1
            self.envelope_committed.emit()
            self.update()

    def _drag_waveform(self, pos: QPointF) -> None:
        """ドラッグ編集: つまんだ所の波形がマウスに付いてくるようにゲインを変える。"""
        dy = self._press_pos.y() - pos.y()
        if not self._drag_moved:
            if abs(dy) < DRAG_START_PX:
                return
            self._drag_moved = True
            if self._dragging_idx < 0:
                self._dragging_idx = self._add_grab_point(self._x_to_time(self._press_pos.x()))
        db_per_px = (DB_MAX - DB_MIN) / self.height()
        t = self._envelope.get_points()[self._dragging_idx][0]
        self._envelope.move_point(self._dragging_idx, t, snap_gain(self._press_gain + dy * db_per_px))
        self.refresh_envelope()

    def _add_grab_point(self, t: float) -> int:
        """つまんだ位置に点を置く。離れた所の音量が変わらないよう、左右にも点を置く。"""
        span = self._view_end - self._view_start
        w = ANCHOR_PX / self.width() * span
        times = [p[0] for p in self._envelope.get_points()]
        for anchor, lo, hi in [(t - w, t - w, t), (t + w, t, t + w)]:
            inside = any(lo < pt < hi for pt in times)
            if 0.0 < anchor < self._duration and not inside:
                self._envelope.add_point(anchor, self._envelope.gain_db_at(anchor))
        self._press_gain = self._envelope.gain_db_at(t)
        return self._envelope.add_point(t, self._press_gain)

    def _end_press(self) -> None:
        self._press_pos = None
        self._drag_moved = False
        self._dragging_idx = -1

    def wheelEvent(self, event):
        if self._duration <= 0:
            return
        delta = event.angleDelta()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:  # macOS では ⌘
            self.zoom(1.002 ** delta.y(), self._x_to_time(event.position().x()))
        else:
            span = self._view_end - self._view_start
            amount = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
            self.set_view(self._view_start - amount / 1200 * span, span)

    def event(self, event):
        # トラックパッドのピンチで拡大縮小
        if (event.type() == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
            self.zoom(max(0.1, 1.0 + event.value()), self._x_to_time(event.position().x()))
            return True
        return super().event(event)

    def _find_point_at(self, pos: QPointF) -> int:
        if self._envelope is None or self._view_end <= self._view_start:
            return -1
        best, best_dist = -1, HIT_RADIUS
        for i, (t, gain) in enumerate(self._envelope.get_points()):
            pt = self._point_pos(t, gain)
            dist = ((pos.x() - pt.x()) ** 2 + (pos.y() - pt.y()) ** 2) ** 0.5
            if dist <= best_dist:
                best, best_dist = i, dist
        return best

    def _try_remove_point(self, pos: QPointF) -> None:
        idx = self._find_point_at(pos)
        if idx >= 0:
            self._envelope.remove_point(idx)
            self._hover_idx = -1
            self.envelope_committed.emit()
            self.refresh_envelope()
