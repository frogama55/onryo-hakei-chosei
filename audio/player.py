import threading

import numpy as np
import sounddevice as sd

from .envelope import Envelope


class PlayerError(Exception):
    """再生デバイスを開けなかったときのエラー。"""


class Player:
    """
    Real-time audio player that applies the envelope on every callback.
    Thread-safe: envelope can be updated while playing.

    再生の終わりは音声スレッドで起きるので、UI は poll_finished() で受け取る。
    """

    CHUNK_FRAMES = 1024

    def __init__(self):
        self._audio: np.ndarray | None = None  # (samples, channels)
        self._sr: int = 44100
        self._envelope: Envelope | None = None

        self._pos: int = 0          # current sample position
        self._playing: bool = False
        self._ended: bool = False
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load(self, audio: np.ndarray, sr: int, envelope: Envelope) -> None:
        self.stop()
        with self._lock:
            self._audio = audio
            self._sr = sr
            self._envelope = envelope
            self._pos = 0

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def play(self) -> None:
        if self._audio is None or self._playing:
            return
        with self._lock:
            if self._pos >= self._audio.shape[0]:
                self._pos = 0
        self._ended = False
        self._playing = True
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sr,
                channels=min(self._audio.shape[1], 2),
                dtype='float32',
                blocksize=self.CHUNK_FRAMES,
                callback=self._callback,
                finished_callback=self._on_stream_finished,
            )
            self._stream.start()
        except Exception as e:
            self._playing = False
            self._close_stream()
            raise PlayerError(str(e))

    def pause(self) -> None:
        self._playing = False
        self._close_stream()

    def stop(self) -> None:
        self.pause()
        with self._lock:
            self._pos = 0

    def seek(self, time_sec: float) -> None:
        with self._lock:
            if self._audio is not None:
                self._pos = int(np.clip(time_sec * self._sr, 0, self._audio.shape[0]))

    def poll_finished(self) -> bool:
        """最後まで再生し終えていれば片付けて True を返す。UI のスレッドから呼ぶ。"""
        if not self._ended:
            return False
        self._ended = False
        self.pause()
        return True

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def position_sec(self) -> float:
        return self._pos / self._sr

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    def _callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        with self._lock:
            start = self._pos
            block = self._audio[start:start + frames]
            n = block.shape[0]
            self._pos = start + n
            applied = block * self._envelope.gain_range(start, n, self._sr)[:, np.newaxis]

        if applied.shape[1] > outdata.shape[1]:
            # 3チャンネル以上はまとめて再生する
            applied = applied.mean(axis=1, keepdims=True)
        outdata[:n] = np.clip(applied, -1.0, 1.0)
        outdata[n:] = 0
        if n < frames:
            raise sd.CallbackStop

    def _on_stream_finished(self) -> None:
        # pause() による停止では _playing が先に False になっている
        if self._playing:
            self._ended = True
