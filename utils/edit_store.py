"""ファイルごとの編集内容を自動で保存する。元のファイルの隣には何も作らない。"""
import hashlib
import json
import os
from pathlib import Path

from app_info import APP_ID

STORE_DIR = Path.home() / 'Library' / 'Application Support' / APP_ID / 'edits'


def _store_path(source_path: str) -> Path:
    key = hashlib.sha1(os.path.abspath(source_path).encode('utf-8')).hexdigest()
    return STORE_DIR / f'{key}.json'


def _fingerprint(source_path: str) -> list:
    st = os.stat(source_path)
    return [st.st_size, st.st_mtime_ns]


def load_points(source_path: str) -> list[tuple[float, float]] | None:
    """保存された制御点を返す。元のファイルが変わっていれば使わない。"""
    try:
        data = json.loads(_store_path(source_path).read_text('utf-8'))
        if data['source'] != _fingerprint(source_path):
            return None
        return [(float(t), float(g)) for t, g in data['points']]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_points(source_path: str, points: list[tuple[float, float]]) -> None:
    path = _store_path(source_path)
    try:
        if all(g == 0.0 for _, g in points):
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps({'source': _fingerprint(source_path), 'points': points}), 'utf-8')
        os.replace(tmp, path)
    except OSError as e:
        # 自動保存の失敗で編集を止めない
        print(f'edit_store: {e}', flush=True)
