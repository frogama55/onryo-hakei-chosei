from pathlib import Path

import numpy as np
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QPushButton, QButtonGroup, QLabel, QDoubleSpinBox, QGroupBox,
    QFileDialog, QSplitter, QFrame, QMessageBox,
    QStatusBar, QProgressBar, QScrollBar, QListWidget, QListWidgetItem,
)
from PyQt6.QtCore import Qt, QEvent, QTimer, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QKeySequence, QShortcut

from app_info import APP_NAME, COPYRIGHT_HOLDER, FFMPEG_NOTICE, SOURCE_URL, VERSION
from audio.envelope import Envelope
from audio.loader import SUPPORTED_AUDIO, SUPPORTED_VIDEO
from audio.player import Player, PlayerError
from utils import edit_store
from utils.exporter import AUDIO_OUTPUTS
from utils.history import EditHistory
from ui.waveform_widget import MODE_DRAG, MODE_LINE, WaveformWidget
from ui.transport import TransportBar
from ui.workers import ExportWorker, LoadedFile, LoadWorker

CLIP_WARN_SEC = 0.05
HISTORY_MAX = 100

_COMMON_HINTS = (
    '• 上端の赤 → 音割れの位置\n'
    '• ⌘+スクロール、ピンチ\n  → 拡大縮小\n'
    '• スペース → 再生/一時停止'
)
HINTS = {
    MODE_DRAG: (
        '• 波形を上下にドラッグ\n  → その部分の音量を変更\n'
        '• 点をドラッグ → 再調整\n'
        '• クリック → 再生位置へ\n'
        '• 右クリック → 点を削除\n'
        '• 点線 → 調整前の波形\n'
        + _COMMON_HINTS
    ),
    MODE_LINE: (
        '• クリック → 点を追加\n'
        '• 点をドラッグ → 音量を調整\n'
        '• 右クリック → 点を削除\n'
        '• 黄色の線 → 音量の増減\n'
        '• 緑の線 → 調整後の音量\n'
        + _COMMON_HINTS
    ),
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)

        self._file: LoadedFile | None = None
        self._duration: float = 0.0
        self._envelope: Envelope | None = None
        self._player = Player()
        self._worker: LoadWorker | ExportWorker | None = None
        self._history = EditHistory(HISTORY_MAX)
        # 最後に書き出した調整。None は、このファイルをまだ書き出していないこと
        self._exported_points: list[tuple[float, float]] | None = None

        self.setAcceptDrops(True)

        self._build_ui()
        self._connect_signals()

        # Timer to poll playback position
        self._pos_timer = QTimer(self)
        self._pos_timer.setInterval(50)
        self._pos_timer.timeout.connect(self._on_pos_timer)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self._build_menu()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Toolbar
        toolbar = self._build_toolbar()
        root.addWidget(toolbar)

        # Main splitter (waveform | side panel)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        self._waveform = WaveformWidget()
        self._scroll = QScrollBar(Qt.Orientation.Horizontal)
        self._scroll.hide()
        wave_area = QWidget()
        wave_layout = QVBoxLayout(wave_area)
        wave_layout.setContentsMargins(0, 0, 0, 0)
        wave_layout.setSpacing(0)
        wave_layout.addWidget(self._waveform, stretch=1)
        wave_layout.addWidget(self._scroll)
        splitter.addWidget(wave_area)

        side = self._build_side_panel()
        splitter.addWidget(side)
        splitter.setSizes([800, 200])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        root.addWidget(splitter, stretch=1)

        # Transport
        self._transport = TransportBar()
        root.addWidget(self._transport)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._progress = QProgressBar()
        self._progress.setFixedWidth(160)
        self._progress.setTextVisible(False)
        self._progress.hide()
        self._status.addPermanentWidget(self._progress)
        self._status.showMessage('ファイルを開くか、ここへドラッグしてください')

        self._apply_dark_style()

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu('ファイル')

        open_act = QAction('開く...', self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self._open_file)
        file_menu.addAction(open_act)

        file_menu.addSeparator()

        export_act = QAction('書き出し...', self)
        export_act.setShortcut(QKeySequence('Ctrl+E'))
        export_act.triggered.connect(self._export_file)
        file_menu.addAction(export_act)

        file_menu.addSeparator()

        quit_act = QAction('終了', self)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(quit_act)

        edit_menu = menu.addMenu('編集')
        self._undo_act = QAction('取り消す', self)
        self._undo_act.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_act.triggered.connect(self._undo)
        edit_menu.addAction(self._undo_act)

        self._redo_act = QAction('やり直す', self)
        self._redo_act.setShortcut(QKeySequence.StandardKey.Redo)
        self._redo_act.triggered.connect(self._redo)
        edit_menu.addAction(self._redo_act)

        edit_menu.addSeparator()
        reset_act = QAction('音量の調整をリセット', self)
        reset_act.triggered.connect(self._reset_envelope)
        edit_menu.addAction(reset_act)

        view_menu = menu.addMenu('表示')
        for label, key, slot in [
            ('拡大', 'Ctrl+=', lambda: self._zoom(2.0)),
            ('縮小', 'Ctrl+-', lambda: self._zoom(0.5)),
            ('全体を表示', 'Ctrl+0', self._waveform_show_all),
        ]:
            act = QAction(label, self)
            act.setShortcut(QKeySequence(key))
            act.triggered.connect(slot)
            view_menu.addAction(act)

        help_menu = menu.addMenu('ヘルプ')
        about_act = QAction(f'{APP_NAME}について', self)
        about_act.setMenuRole(QAction.MenuRole.AboutRole)
        about_act.triggered.connect(self._show_about)
        help_menu.addAction(about_act)

        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self, self._toggle_play)
        space.setAutoRepeat(False)  # 押し続けても再生と停止を繰り返さない

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName('toolbar')
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bar.setFixedHeight(44)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        open_btn = QPushButton('ファイルを開く')
        open_btn.clicked.connect(self._open_file)

        export_btn = QPushButton('書き出し')
        export_btn.setObjectName('primaryBtn')
        export_btn.clicked.connect(self._export_file)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet('color:#444;')

        self._drag_btn = QPushButton('ドラッグ編集')
        self._drag_btn.setToolTip('波形をつまんで上下に動かし、その部分の音量を変えます')
        self._line_btn = QPushButton('ライン編集')
        self._line_btn.setToolTip('音量の線に点を打ち、点を動かして音量を変えます')
        self._mode_group = QButtonGroup(self)
        # 2つで1組の切り替えに見えるよう、間を詰めて並べる
        modes = QHBoxLayout()
        modes.setSpacing(0)
        for btn, mode, name in [(self._drag_btn, MODE_DRAG, 'segLeft'),
                                (self._line_btn, MODE_LINE, 'segRight')]:
            btn.setObjectName(name)
            btn.setCheckable(True)
            modes.addWidget(btn)
            btn.clicked.connect(lambda _=False, m=mode: self._set_mode(m))
            self._mode_group.addButton(btn)
        self._drag_btn.setChecked(True)

        reset_btn = QPushButton('調整をリセット')
        reset_btn.setToolTip('音量の調整をすべて元に戻します（⌘Z で取り消せます）')
        reset_btn.clicked.connect(self._reset_envelope)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet('color:#444;')

        threshold_tip = '波形の背景の色分けに使う目安です。音は変わりません'
        threshold_label = QLabel('目安')
        threshold_label.setObjectName('groupLabel')
        threshold_label.setToolTip(threshold_tip)
        threshold_label_up = QLabel('上限')
        self._upper_spin = QDoubleSpinBox()
        self._upper_spin.setRange(-64.0, 0.0)
        self._upper_spin.setValue(-6.0)
        self._upper_spin.setSuffix(' dB')
        self._upper_spin.setDecimals(1)
        self._upper_spin.setFixedWidth(90)

        threshold_label_lo = QLabel('下限')
        self._lower_spin = QDoubleSpinBox()
        self._lower_spin.setRange(-64.0, 0.0)
        self._lower_spin.setValue(-40.0)
        self._lower_spin.setSuffix(' dB')
        self._lower_spin.setDecimals(1)
        self._lower_spin.setFixedWidth(90)
        for spin in [self._upper_spin, self._lower_spin]:
            spin.setToolTip(threshold_tip)
            spin.installEventFilter(self)  # 入力欄にカーソルがあってもスペースで再生する

        zoom_btns = []
        for label, tip, slot in [
            ('－', '縮小（⌘-）', lambda: self._zoom(0.5)),
            ('＋', '拡大（⌘=）', lambda: self._zoom(2.0)),
            ('全体', '全体を表示（⌘0）', self._waveform_show_all),
        ]:
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            zoom_btns.append(btn)
        self._zoom_out_btn, self._zoom_in_btn, self._show_all_btn = zoom_btns

        for b in [open_btn, export_btn, self._drag_btn, self._line_btn, reset_btn, *zoom_btns]:
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # スペースキーを再生に使うため

        mode_label = QLabel('編集')
        mode_label.setObjectName('groupLabel')
        view_label = QLabel('表示')
        view_label.setObjectName('groupLabel')

        for w in [open_btn, export_btn, sep, mode_label]:
            layout.addWidget(w)
        layout.addLayout(modes)
        for w in [reset_btn, sep2, threshold_label, threshold_label_up, self._upper_spin,
                  threshold_label_lo, self._lower_spin]:
            layout.addWidget(w)
        layout.addStretch()
        layout.addWidget(view_label)
        for b in zoom_btns:
            layout.addWidget(b)
        self._toolbar = bar
        return bar

    def _build_side_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName('sidePanel')
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        panel.setFixedWidth(210)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        # File info
        info_group = QGroupBox('ファイル情報')
        info_layout = QVBoxLayout(info_group)
        self._info_label = QLabel('—')
        self._info_label.setMinimumHeight(80)  # 5行。履歴の欄に押しつぶされないように
        self._info_label.setStyleSheet('color:#888; font-size:11px; line-height:1.8;')
        info_layout.addWidget(self._info_label)

        # Hint
        hint_group = QGroupBox('操作方法')
        hint_layout = QVBoxLayout(hint_group)
        self._hint = QLabel(HINTS[MODE_DRAG])
        self._hint.setStyleSheet('color:#666; font-size:11px; line-height:1.8;')
        hint_layout.addWidget(self._hint)

        layout.addWidget(info_group)
        layout.addWidget(hint_group)
        layout.addWidget(self._build_history_panel(), stretch=1)
        return panel

    def _build_history_panel(self) -> QWidget:
        group = QGroupBox('編集の履歴')
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 8, 6, 6)
        layout.setSpacing(6)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self._undo_btn = QPushButton('← 戻る')
        self._undo_btn.setToolTip('1つ前の状態へ戻ります（⌘Z）')
        self._undo_btn.clicked.connect(self._undo)
        self._redo_btn = QPushButton('進む →')
        self._redo_btn.setToolTip('戻した操作をやり直します（⇧⌘Z）')
        self._redo_btn.clicked.connect(self._redo)
        for b in [self._undo_btn, self._redo_btn]:
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # スペースキーを再生に使うため
            b.setEnabled(False)
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self._history_list = QListWidget()
        self._history_list.setObjectName('historyList')
        self._history_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._history_list.setMinimumHeight(90)
        self._history_list.setWordWrap(True)
        self._history_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._history_list.setToolTip('項目を押すと、その時点まで戻ります')
        self._history_list.itemClicked.connect(
            lambda item: self._go_to_history(self._history_list.row(item)))
        layout.addWidget(self._history_list)
        return group

    def _connect_signals(self):
        self._waveform.envelope_committed.connect(self._on_envelope_committed)
        self._waveform.seek_requested.connect(self._seek)
        self._waveform.view_changed.connect(self._on_view_changed)
        self._scroll.valueChanged.connect(self._on_scroll)

        self._transport.play_pause_toggled.connect(self._toggle_play)
        self._transport.stop_requested.connect(self._stop)
        self._transport.seek_requested.connect(self._on_transport_seek)

        self._upper_spin.valueChanged.connect(self._on_threshold_changed)
        self._lower_spin.valueChanged.connect(self._on_threshold_changed)


    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _open_file(self):
        exts = sorted(SUPPORTED_AUDIO | SUPPORTED_VIDEO)
        audio_filter = f'Audio/Video Files ({" ".join("*" + e for e in exts)});;All Files (*)'
        path, _ = QFileDialog.getOpenFileName(self, 'ファイルを開く', '', audio_filter)
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> None:
        if self._worker is not None:
            return
        self._stop()
        self._set_busy(f'{Path(path).name} を読み込み中...')
        self._worker = LoadWorker(path, self)
        self._worker.loaded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_load_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    @pyqtSlot(object)
    def _on_loaded(self, file: LoadedFile):
        self._file = file
        self._duration = file.audio.shape[0] / file.sr

        self._envelope = Envelope(self._duration)
        restored = edit_store.load_points(file.path)
        if restored:
            self._envelope.set_points(restored)
        self._exported_points = None
        self._history.start('前回の編集を復元' if restored else 'ファイルを開く',
                            self._envelope.get_points())
        self._update_history_actions()

        self._waveform.load_waveform(file.times, file.rms_db, file.peak_db,
                                     self._duration, self._envelope)
        self._waveform.set_thresholds(self._upper_spin.value(), self._lower_spin.value())

        self._transport.set_duration(self._duration)
        self._player.load(file.audio, file.sr, self._envelope)

        p = Path(file.path)
        peak = float(file.peak_db.max())
        self._info_label.setText(
            f'形式: {p.suffix.upper()[1:]}\n'
            f'チャンネル: {file.audio.shape[1]}\n'
            f'サンプルレート: {file.sr} Hz\n'
            f'長さ: {self._duration:.1f} 秒\n'
            f'ピーク: {peak:.1f} dB'
        )
        self.setWindowTitle(f'{APP_NAME} — {p.name}')
        note = '（前回の編集を復元しました）' if restored else ''
        self._status.showMessage(f'{p.name} を読み込みました{note}')
        self._pos_timer.start()

    @pyqtSlot(str)
    def _on_load_failed(self, message: str):
        QMessageBox.critical(self, 'エラー', f'ファイルを読み込めませんでした:\n{message}')
        self._status.showMessage('読み込み失敗')

    @pyqtSlot()
    def _export_file(self):
        if self._file is None or self._envelope is None or self._worker is not None:
            if self._file is None:
                QMessageBox.information(self, '書き出し', 'まずファイルを読み込んでください。')
            return

        p = Path(self._file.path)
        if self._file.source_type == 'video':
            default_name = p.stem + '_volume_adjusted' + p.suffix
            file_filter = f'Video Files (*{p.suffix});;All Files (*)'
        else:
            ext = p.suffix.lower() if p.suffix.lower() in AUDIO_OUTPUTS else '.wav'
            default_name = p.stem + '_volume_adjusted' + ext
            file_filter = 'Audio Files (*.wav *.flac *.m4a *.mp3);;All Files (*)'

        out_path, _ = QFileDialog.getSaveFileName(
            self, '書き出し先を選択', str(p.parent / default_name), file_filter
        )
        if not out_path:
            return
        if not Path(out_path).suffix:
            out_path += Path(default_name).suffix

        clipped = self._clipped_seconds()
        if clipped >= CLIP_WARN_SEC:
            answer = QMessageBox.question(
                self, '音割れの確認',
                f'約 {clipped:.1f} 秒分で音量が上がりすぎ、音割れします。\n'
                '波形の上端の赤い位置です。このまま書き出しますか？',
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self._set_busy('書き出し中...')
        self._progress.setRange(0, 1000)
        self._worker = ExportWorker(self._file, self._envelope.copy(), out_path, self)
        self._worker.progress.connect(lambda v: self._progress.setValue(int(v * 1000)))
        self._worker.succeeded.connect(lambda path, points=self._envelope.get_points():
                                       self._on_exported(path, points))
        self._worker.failed.connect(self._on_export_failed)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _on_exported(self, path: str, points: list[tuple[float, float]]) -> None:
        self._exported_points = points
        self._status.showMessage(f'書き出し完了: {path}')

    def has_unexported_changes(self) -> bool:
        if self._envelope is None:
            return False
        if self._exported_points is None:
            return not self._envelope.is_flat()
        return self._envelope.get_points() != self._exported_points

    def _confirm_close(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle('確認')
        box.setText('書き出していない調整があります。閉じますか？')
        box.setInformativeText('調整の内容は自動で保存され、次に同じファイルを開くと元に戻ります。'
                               'ただし、音声や動画のファイルは書き出すまで作られません。')
        close_btn = box.addButton('書き出さずに閉じる', QMessageBox.ButtonRole.DestructiveRole)
        cancel_btn = box.addButton('キャンセル', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel_btn)
        box.exec()
        return box.clickedButton() is close_btn

    def _clipped_seconds(self) -> float:
        file = self._file
        over = file.peak_db + self._envelope.gain_db_array(file.times) > 0.0
        return float(np.count_nonzero(over)) * (file.times[1] - file.times[0] if len(file.times) > 1 else 0.0)

    @pyqtSlot(str)
    def _on_export_failed(self, message: str):
        QMessageBox.critical(self, 'エラー', f'書き出しに失敗しました:\n{message}')
        self._status.showMessage('書き出し失敗')

    def _set_busy(self, message: str) -> None:
        self._toolbar.setEnabled(False)
        self._waveform.setEnabled(False)
        self._progress.setRange(0, 0)
        self._progress.show()
        self._status.showMessage(message)

    @pyqtSlot()
    def _on_worker_finished(self):
        self._worker.deleteLater()
        self._worker = None
        self._toolbar.setEnabled(True)
        self._waveform.setEnabled(True)
        self._progress.hide()

    # ------------------------------------------------------------------
    # Edit history
    # ------------------------------------------------------------------

    @pyqtSlot(str)
    def _on_envelope_committed(self, label: str):
        if self._envelope is None:
            return
        if self._history.push(label, self._envelope.get_points()):
            self._update_history_actions()
        edit_store.save_points(self._file.path, self._envelope.get_points())

    def _go_to_history(self, index: int) -> None:
        points = self._history.go_to(index)
        if points is None:
            return
        self._envelope.set_points(points)
        self._waveform.refresh_envelope()
        edit_store.save_points(self._file.path, points)
        self._update_history_actions()

    @pyqtSlot()
    def _undo(self):
        if self._history.can_undo():
            self._go_to_history(self._history.index - 1)

    @pyqtSlot()
    def _redo(self):
        if self._history.can_redo():
            self._go_to_history(self._history.index + 1)

    def _update_history_actions(self) -> None:
        for act, btn, enabled in [(self._undo_act, self._undo_btn, self._history.can_undo()),
                                  (self._redo_act, self._redo_btn, self._history.can_redo())]:
            act.setEnabled(enabled)
            btn.setEnabled(enabled)

        # 今の位置より後ろ（進むで戻れる項目）は薄く表示する
        lst = self._history_list
        lst.clear()
        for i, entry in enumerate(self._history.entries):
            when = f'{entry.time.month}/{entry.time.day} {entry.time:%H:%M:%S}'
            item = QListWidgetItem(f'{entry.label}\n{when}')
            item.setToolTip(f'{entry.label}\n{entry.time:%Y-%m-%d %H:%M:%S}')
            if i > self._history.index:
                item.setForeground(QColor('#5a5a5a'))
            lst.addItem(item)
        if self._history.entries:
            lst.setCurrentRow(self._history.index)
            lst.scrollToItem(lst.currentItem())

    def _set_mode(self, mode: str) -> None:
        self._waveform.set_mode(mode)
        (self._drag_btn if mode == MODE_DRAG else self._line_btn).setChecked(True)
        self._hint.setText(HINTS[mode])

    @pyqtSlot()
    def _reset_envelope(self):
        if self._envelope:
            self._envelope.reset()
            self._waveform.refresh_envelope()
            self._on_envelope_committed('調整をリセット')

    @pyqtSlot()
    def _on_threshold_changed(self):
        upper, lower = self._upper_spin.value(), self._lower_spin.value()
        # 上限は常に下限より上にする
        self._upper_spin.setMinimum(lower + 1.0)
        self._lower_spin.setMaximum(upper - 1.0)
        self._waveform.set_thresholds(upper, lower)

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    def _zoom(self, factor: float) -> None:
        # 再生位置が見えていればそこを中心に、見えていなければ画面の中央を中心にする
        start, end = self._waveform._view_start, self._waveform._view_end
        pos = self._player.position_sec
        self._waveform.zoom(factor, pos if start <= pos <= end else None)

    def _waveform_show_all(self):
        if self._duration > 0:
            self._waveform.show_all()

    @pyqtSlot(float, float)
    def _on_view_changed(self, start: float, end: float):
        span = end - start
        zoomed = span < self._duration - 0.001
        self._scroll.setVisible(zoomed)
        self._scroll.blockSignals(True)
        self._scroll.setRange(0, int((self._duration - span) * 1000))
        self._scroll.setPageStep(int(span * 1000))
        self._scroll.setValue(int(start * 1000))
        self._scroll.blockSignals(False)

    @pyqtSlot(int)
    def _on_scroll(self, value: int):
        self._waveform.set_view(value / 1000.0, self._scroll.pageStep() / 1000.0)

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    @pyqtSlot()
    def _toggle_play(self):
        if self._file is None:
            return
        if self._player.is_playing:
            self._player.pause()
            self._transport.set_playing(False)
            return
        try:
            self._player.play()
        except PlayerError as e:
            QMessageBox.critical(self, 'エラー', f'再生を開始できませんでした:\n{e}')
            return
        self._transport.set_playing(True)

    @pyqtSlot()
    def _stop(self):
        self._player.stop()
        self._transport.set_playing(False)
        self._transport.update_position(0.0)
        self._waveform.set_playhead(0.0)

    def _seek(self, t: float) -> None:
        self._player.seek(t)
        self._transport.update_position(t)
        self._waveform.set_playhead(t)

    @pyqtSlot(float)
    def _on_transport_seek(self, frac: float):
        self._seek(frac * self._duration)

    @pyqtSlot()
    def _on_pos_timer(self):
        if self._player.poll_finished():
            self._transport.set_playing(False)
        if self._player.is_playing:
            pos = self._player.position_sec
            self._transport.update_position(pos)
            self._waveform.set_playhead(pos, follow=True)

    # ------------------------------------------------------------------
    # About
    # ------------------------------------------------------------------

    def about_text(self) -> str:
        lines = [f'<b>{APP_NAME}</b> {VERSION}']
        if COPYRIGHT_HOLDER:
            lines.append(f'&copy; {COPYRIGHT_HOLDER}')
        lines.append('GNU General Public License v3.0 のもとで配布しています。無保証です。')
        lines.append(FFMPEG_NOTICE)
        lines.append('Qt と PyQt6 を使用しています。詳細は同梱の THIRD_PARTY_NOTICES.md を参照してください。')
        if SOURCE_URL:
            lines.append(f'ソースコード: <a href="{SOURCE_URL}">{SOURCE_URL}</a>')
        return '<br><br>'.join(lines)

    @pyqtSlot()
    def _show_about(self):
        QMessageBox.about(self, f'{APP_NAME}について', self.about_text())

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self._worker is None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.load_path(url.toLocalFile())
                break

    def eventFilter(self, obj, event):
        # 数値の入力欄はスペースを自分で使おうとするので、渡さずにショートカットへ回す
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Space
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if self._worker is not None:
            self._status.showMessage('処理が終わるまでお待ちください')
            event.ignore()
            return
        if self.has_unexported_changes() and not self._confirm_close():
            event.ignore()
            return
        self._player.stop()
        event.accept()

    # ------------------------------------------------------------------
    # Style
    # ------------------------------------------------------------------

    def _apply_dark_style(self):
        self.setStyleSheet('''
            QMainWindow, QWidget { background:#1e1e1e; color:#d0d0d0; }
            QMenuBar { background:#252525; color:#ccc; }
            QMenuBar::item:selected { background:#333; }
            QMenu { background:#2a2a2a; color:#ccc; border:1px solid #444; }
            QMenu::item:selected { background:#1a6fbb; }
            QPushButton {
                background:#333; color:#ccc; border:1px solid #444;
                border-radius:4px; padding:4px 12px; font-size:12px;
            }
            QPushButton:hover { background:#4a4a4a; border-color:#9a9a9a; color:white; }
            QPushButton:pressed { background:#262626; }
            QPushButton:disabled { color:#666; border-color:#3a3a3a; }
            QPushButton:checked { background:#1a5c3a; border-color:#2a9a60; color:#a0ffc8; }
            QPushButton:checked:hover { background:#227048; border-color:#80e0a8; }
            QPushButton#segLeft { border-top-right-radius:0; border-bottom-right-radius:0; }
            QPushButton#segRight {
                border-top-left-radius:0; border-bottom-left-radius:0; border-left:none;
            }
            QPushButton#primaryBtn {
                background:#1a6fbb; border-color:#1a6fbb; color:white;
            }
            QPushButton#primaryBtn:hover { background:#3a90dd; border-color:#9ccaf5; }
            QDoubleSpinBox {
                background:#1a1a1a; color:#ccc; border:1px solid #444;
                border-radius:3px; padding:2px 4px;
            }
            QDoubleSpinBox:hover { border-color:#9a9a9a; }
            #toolbar { background:#252525; border-bottom:1px solid #333; }
            #toolbar QLabel, #toolbar QFrame { background:transparent; }
            QLabel#groupLabel { color:#888; font-size:11px; }
            #sidePanel { background:#222; border-left:1px solid #333; }
            #sidePanel QWidget { background:#222; }
            QListWidget#historyList {
                background:#1a1a1a; color:#ccc; border:1px solid #333;
                border-radius:3px; font-size:11px;
            }
            QListWidget#historyList::item { padding:3px 4px; border-bottom:1px solid #262626; }
            QListWidget#historyList::item:hover { background:#333; }
            QListWidget#historyList::item:selected { background:#1a5c3a; color:#a0ffc8; }
            QListWidget#historyList QScrollBar:vertical { background:#1a1a1a; width:8px; }
            QListWidget#historyList QScrollBar::handle:vertical {
                background:#444; border-radius:4px; min-height:20px;
            }
            QListWidget#historyList QScrollBar::add-line, QListWidget#historyList QScrollBar::sub-line {
                height:0;
            }
            QGroupBox {
                color:#888; border:1px solid #333; border-radius:4px;
                margin-top:10px; padding-top:6px; font-size:11px;
            }
            QGroupBox::title { subcontrol-origin:margin; left:8px; }
            QSplitter::handle { background:#333; }
            QLabel { color:#ccc; }
            QStatusBar { background:#252525; color:#888; font-size:11px; }
        ''')
