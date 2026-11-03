"""編集の履歴。調整の状態を順に並べ、今どこにいるかを持つ。"""
from dataclasses import dataclass
from datetime import datetime

Points = list[tuple[float, float]]


@dataclass(frozen=True)
class HistoryEntry:
    label: str
    time: datetime
    points: Points


class EditHistory:
    """先頭は開いた時の状態。戻っても後ろの項目は残し、新しい編集をした時に消す。"""

    def __init__(self, max_entries: int = 100):
        self.max_entries = max_entries
        self.entries: list[HistoryEntry] = []
        self.index = -1

    def start(self, label: str, points: Points) -> None:
        self.entries = [HistoryEntry(label, datetime.now(), list(points))]
        self.index = 0

    @property
    def current(self) -> HistoryEntry:
        return self.entries[self.index]

    def push(self, label: str, points: Points) -> bool:
        """編集を加える。何も変わっていなければ加えずに False を返す。"""
        if not self.entries or points == self.current.points:
            return False
        del self.entries[self.index + 1:]
        self.entries.append(HistoryEntry(label, datetime.now(), list(points)))
        # 古いものから消す。先頭が開いた時の状態でなくなるが、戻れる回数を優先する
        del self.entries[:-self.max_entries]
        self.index = len(self.entries) - 1
        return True

    def go_to(self, index: int) -> Points | None:
        """その時点へ移り、その時の調整を返す。範囲外や今の位置なら None。"""
        if not 0 <= index < len(self.entries) or index == self.index:
            return None
        self.index = index
        return list(self.current.points)

    def can_undo(self) -> bool:
        return self.index > 0

    def can_redo(self) -> bool:
        return self.index < len(self.entries) - 1
