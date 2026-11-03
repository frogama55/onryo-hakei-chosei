import numpy as np
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QPointF, pyqtSignal, QRectF
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


def format_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


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
        self._applied: tuple[QPainterPath, list[float]] | None = None

        # Interaction state
        self._envelope_mode: bool = False
        self._dragging_idx: int = -1
        self._hover_idx: int = -1

        self.setMouseTracking(True)

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

    def set_envelope_mode(self, enabled: bool) -> None:
        self._envelope_mode = enabled
        self._dragging_idx = -1
        self._hover_idx = -1
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.update()

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

    def _db_to_y(self, db: float) -> float:
        frac = (DB_MAX - db) / (DB_MAX - DB_MIN)
        return frac * self.height()

    def _gain_to_y(self, gain_db: float) -> float:
        frac = (GAIN_MAX_DB - gain_db) / (GAIN_MAX_DB - GAIN_MIN_DB)
        return frac * self.height()

    def _y_to_gain(self, y: float) -> float:
        frac = y / self.height()
        gain = GAIN_MAX_DB - frac * (GAIN_MAX_DB - GAIN_MIN_DB)
        if abs(gain) < GAIN_SNAP_DB:
            return 0.0
        return round(float(np.clip(gain, GAIN_MIN_DB, GAIN_MAX_DB)), 1)

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
        if self._rms_db is not None and self._envelope is not None:
            if not self._envelope.is_flat():
                self._draw_applied(painter, W, H)
            if self._envelope_mode:
                self._draw_envelope(painter, W, H)
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

    def _draw_waveform(self, p: QPainter, W: int, H: int):
        _, rms, _ = self._columns(W)
        ys = [self._db_to_y(float(db)) for db in rms]

        outline = QPainterPath()
        outline.moveTo(0.0, ys[0])
        for x, y in enumerate(ys):
            outline.lineTo(float(x), y)

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

    def _build_applied(self, W: int) -> tuple[QPainterPath, list[float]]:
        """調整後の音量の線と、音割れする位置を求める。"""
        centers, rms, peak = self._columns(W)
        gain = self._envelope.gain_db_array(centers)
        ys = self._db_to_y(np.minimum(rms + gain, DB_MAX))

        path = QPainterPath()
        path.moveTo(0.0, float(ys[0]))
        for x, y in enumerate(ys):
            path.lineTo(float(x), float(y))
        return path, [float(x) for x in np.nonzero(peak + gain > 0.0)[0]]

    def _draw_applied(self, p: QPainter, W: int, H: int):
        if self._applied is None:
            self._applied = self._build_applied(W)
        path, clipped = self._applied

        pen = QPen(QColor(120, 230, 150, 220))
        pen.setWidthF(1.2)
        p.setPen(pen)
        p.drawPath(path)

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

    def _draw_envelope(self, p: QPainter, W: int, H: int):
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

        pts = self._envelope.get_points()

        # Line
        path = QPainterPath()
        for i, (t, gain) in enumerate(pts):
            x = self._time_to_x(t)
            y = self._gain_to_y(gain)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        pen = QPen(QColor(255, 220, 50, 220))
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # Control points
        for i, (t, gain) in enumerate(pts):
            x = self._time_to_x(t)
            if x < -HIT_RADIUS or x > W + HIT_RADIUS:
                continue
            y = self._gain_to_y(gain)
            is_hover = (i == self._hover_idx)
            is_drag = (i == self._dragging_idx)

            r = CTRL_POINT_RADIUS + (2 if (is_hover or is_drag) else 0)
            p.setBrush(QBrush(QColor(255, 215, 0) if not is_drag else QColor(255, 255, 100)))
            p.setPen(QPen(QColor('white'), 1.5))
            p.drawEllipse(QPointF(x, y), r, r)

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
            if self._envelope_mode:
                self._try_remove_point(pos)
            return

        if self._envelope_mode:
            idx = self._find_point_at(pos)
            if idx < 0:
                # Add new control point
                idx = self._envelope.add_point(self._x_to_time(pos.x()), self._y_to_gain(pos.y()))
                self._applied = None
            self._dragging_idx = idx
            self.update()
        else:
            # Seek on click when not in envelope mode
            self.seek_requested.emit(self._x_to_time(pos.x()))

    def mouseMoveEvent(self, event):
        if not self._envelope_mode or self._envelope is None:
            return
        pos = event.position()

        if self._dragging_idx >= 0:
            self._envelope.move_point(self._dragging_idx, self._x_to_time(pos.x()),
                                      self._y_to_gain(pos.y()))
            self.refresh_envelope()
        else:
            new_hover = self._find_point_at(pos)
            if new_hover != self._hover_idx:
                self._hover_idx = new_hover
                self.setCursor(
                    Qt.CursorShape.SizeAllCursor if new_hover >= 0
                    else Qt.CursorShape.CrossCursor
                )
                self.update()

    def mouseReleaseEvent(self, event):
        if self._dragging_idx >= 0:
            self._dragging_idx = -1
            self.envelope_committed.emit()
            self.update()

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

    def _find_point_at(self, pos: QPointF) -> int:
        if self._envelope is None:
            return -1
        for i, (t, gain) in enumerate(self._envelope.get_points()):
            x = self._time_to_x(t)
            y = self._gain_to_y(gain)
            dist = ((pos.x() - x) ** 2 + (pos.y() - y) ** 2) ** 0.5
            if dist <= HIT_RADIUS:
                return i
        return -1

    def _try_remove_point(self, pos: QPointF) -> None:
        idx = self._find_point_at(pos)
        if idx >= 0:
            self._envelope.remove_point(idx)
            self._hover_idx = -1
            self.envelope_committed.emit()
            self.refresh_envelope()
