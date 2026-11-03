from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QPushButton, QSlider, QLabel, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer


class TransportBar(QWidget):
    play_pause_toggled = pyqtSignal()
    stop_requested = pyqtSignal()
    seek_requested = pyqtSignal(float)   # fraction 0.0–1.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration: float = 0.0
        self._is_playing: bool = False
        self._seeking: bool = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(10)

        self._play_btn = QPushButton('▶')
        self._play_btn.setFixedSize(32, 32)
        self._play_btn.setStyleSheet(
            'QPushButton { background:#1a6fbb; color:white; border-radius:16px; font-size:14px; border:none; padding:0; }'
            'QPushButton:hover { background:#2080cc; }'
        )
        self._play_btn.clicked.connect(self._on_play_clicked)
        self._play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._stop_btn = QPushButton('■')
        self._stop_btn.setFixedSize(28, 28)
        self._stop_btn.setStyleSheet(
            'QPushButton { background:#333; color:#ccc; border-radius:4px; font-size:12px; border:1px solid #444; padding:0; }'
            'QPushButton:hover { background:#3a3a3a; }'
        )
        self._stop_btn.clicked.connect(self.stop_requested)
        self._stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._time_label = QLabel('0:00.0 / 0:00.0')
        self._time_label.setStyleSheet('color:#aaa; font-family:"Menlo","Monaco","Courier New"; font-size:12px;')
        self._time_label.setMinimumWidth(120)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 10000)
        self._slider.setValue(0)
        self._slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._slider.setStyleSheet(
            'QSlider::groove:horizontal { height:4px; background:#333; border-radius:2px; }'
            'QSlider::handle:horizontal { width:14px; height:14px; background:white; '
            '  border-radius:7px; margin:-5px 0; }'
            'QSlider::sub-page:horizontal { background:#1a6fbb; border-radius:2px; }'
        )
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(self._on_slider_moved)

        layout.addWidget(self._play_btn)
        layout.addWidget(self._stop_btn)
        layout.addWidget(self._time_label)
        layout.addWidget(self._slider)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_duration(self, duration: float) -> None:
        self._duration = duration
        self._update_label(0.0)

    def update_position(self, time_sec: float) -> None:
        if self._seeking:
            return
        self._update_label(time_sec)
        if self._duration > 0:
            val = int(time_sec / self._duration * 10000)
            self._slider.blockSignals(True)
            self._slider.setValue(val)
            self._slider.blockSignals(False)

    def set_playing(self, playing: bool) -> None:
        self._is_playing = playing
        self._play_btn.setText('⏸' if playing else '▶')

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_play_clicked(self):
        self.play_pause_toggled.emit()

    def _on_slider_pressed(self):
        self._seeking = True

    def _on_slider_released(self):
        self._seeking = False
        frac = self._slider.value() / 10000.0
        self.seek_requested.emit(frac)

    def _on_slider_moved(self, value: int):
        frac = value / 10000.0
        t = frac * self._duration
        self._update_label(t)

    def _update_label(self, time_sec: float) -> None:
        def fmt(s):
            m = int(s) // 60
            sec = s % 60
            return f'{m}:{sec:04.1f}'
        self._time_label.setText(f'{fmt(time_sec)} / {fmt(self._duration)}')
