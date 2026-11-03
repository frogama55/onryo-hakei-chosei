import math

import numpy as np

GAIN_MIN_DB = -60.0
GAIN_MAX_DB = 24.0
MIN_POINT_GAP = 0.01  # 秒


class Envelope:
    """
    Volume envelope defined by control points (time_sec, gain_db).
    Interpolates linearly (in dB) between points.

    再生スレッドが同時に読むので、_points は書き換えず、常に新しいリストへ差し替える。
    """

    def __init__(self, duration: float):
        self.duration = duration
        # Default: flat at 0 dB
        self._points: list[tuple[float, float]] = [(0.0, 0.0), (duration, 0.0)]

    def set_points(self, points: list[tuple[float, float]]) -> None:
        valid = [
            (float(np.clip(t, 0.0, self.duration)), float(np.clip(g, GAIN_MIN_DB, GAIN_MAX_DB)))
            for t, g in points if math.isfinite(t) and math.isfinite(g)
        ]
        valid.sort(key=lambda p: p[0])
        # Always anchor start and end
        if not valid:
            valid = [(0.0, 0.0)]
        if valid[0][0] > 0.0:
            valid.insert(0, (0.0, valid[0][1]))
        if valid[-1][0] < self.duration or len(valid) == 1:
            valid.append((self.duration, valid[-1][1]))
        self._points = valid

    def get_points(self) -> list[tuple[float, float]]:
        return list(self._points)

    def is_flat(self) -> bool:
        return all(g == 0.0 for _, g in self._points)

    def add_point(self, time: float, gain_db: float) -> int:
        """点を追加して、その番号を返す。すぐ近くに点があれば、その点の値を変える。"""
        time = float(np.clip(time, 0.0, self.duration))
        gain_db = float(np.clip(gain_db, GAIN_MIN_DB, GAIN_MAX_DB))
        for i, (t, _) in enumerate(self._points):
            if abs(t - time) < MIN_POINT_GAP:
                self.move_point(i, t, gain_db)
                return i
        pts = sorted(self._points + [(time, gain_db)], key=lambda p: p[0])
        self._points = pts
        return pts.index((time, gain_db))

    def remove_point(self, index: int) -> None:
        if 0 < index < len(self._points) - 1:
            self._points = self._points[:index] + self._points[index + 1:]

    def move_point(self, index: int, time: float, gain_db: float) -> None:
        pts = list(self._points)
        gain_db = float(np.clip(gain_db, GAIN_MIN_DB, GAIN_MAX_DB))
        # Keep first and last on their time positions
        if index == 0:
            time = 0.0
        elif index == len(pts) - 1:
            time = self.duration
        else:
            # 隣の点は越えない（越えると掴んでいる点が入れ替わる）
            time = float(np.clip(time, pts[index - 1][0] + MIN_POINT_GAP,
                                 pts[index + 1][0] - MIN_POINT_GAP))
        pts[index] = (time, gain_db)
        self._points = pts

    def gain_db_at(self, time: float) -> float:
        return float(self.gain_db_array(np.array([time]))[0])

    def gain_db_array(self, times: np.ndarray) -> np.ndarray:
        pts = self._points
        return np.interp(times, [p[0] for p in pts], [p[1] for p in pts])

    def gain_range(self, start: int, n_samples: int, sr: int) -> np.ndarray:
        """start 番目のサンプルから n_samples 個分の線形ゲイン。"""
        times = (start + np.arange(n_samples)) / sr
        return (10.0 ** (self.gain_db_array(times) / 20.0)).astype(np.float32)

    def copy(self) -> 'Envelope':
        other = Envelope(self.duration)
        other._points = self.get_points()
        return other

    def reset(self) -> None:
        self._points = [(0.0, 0.0), (self.duration, 0.0)]
