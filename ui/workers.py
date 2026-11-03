from dataclasses import dataclass

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from audio.analyzer import compute_levels
from audio.envelope import Envelope
from audio.loader import LoadError, load_file
from utils.exporter import ExportError, export_audio, export_video


@dataclass
class LoadedFile:
    path: str
    audio: np.ndarray
    sr: int
    source_type: str
    times: np.ndarray
    rms_db: np.ndarray
    peak_db: np.ndarray


class LoadWorker(QThread):
    loaded = pyqtSignal(object)  # LoadedFile
    failed = pyqtSignal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            audio, sr, source_type = load_file(self._path)
            times, rms_db, peak_db = compute_levels(audio, sr)
        except LoadError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f'{type(e).__name__}: {e}')
        else:
            self.loaded.emit(LoadedFile(self._path, audio, sr, source_type, times, rms_db, peak_db))


class ExportWorker(QThread):
    progress = pyqtSignal(float)  # 0.0–1.0
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, file: LoadedFile, envelope: Envelope, output_path: str, parent=None):
        super().__init__(parent)
        self._file = file
        self._envelope = envelope
        self._output_path = output_path

    def run(self):
        f = self._file
        try:
            if f.source_type == 'video':
                export_video(f.path, f.audio, f.sr, self._envelope, self._output_path,
                             progress=self.progress.emit)
            else:
                export_audio(f.audio, f.sr, self._envelope, self._output_path, f.path,
                             progress=self.progress.emit)
        except ExportError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f'{type(e).__name__}: {e}')
        else:
            self.succeeded.emit(self._output_path)
