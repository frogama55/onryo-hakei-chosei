import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SUPPORTED_AUDIO = {'.wav', '.mp3', '.flac', '.ogg', '.aac', '.m4a', '.aif', '.aiff', '.opus'}
SUPPORTED_VIDEO = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v'}

FFMPEG_MISSING = 'FFmpeg が見つかりません。ターミナルで brew install ffmpeg を実行してください。'

# 細工されたファイルが FFmpeg を通じて外部へ接続しないよう、ローカルのファイルとパイプに限る
SAFE_INPUT = ['-protocol_whitelist', 'file,pipe']


class LoadError(Exception):
    """利用者に見せるメッセージを持つ読み込みエラー。"""


def tool_path(name: str) -> str:
    """配布版は同梱の FFmpeg だけを使う。開発時は PATH から探す。"""
    if getattr(sys, 'frozen', False):
        return str(Path(sys._MEIPASS) / 'ffmpeg' / name)
    return name


def ensure_tools() -> None:
    """Finder から起動すると PATH に Homebrew が含まれないので補う。"""
    path = os.environ.get('PATH', '').split(os.pathsep)
    for d in ('/usr/local/bin', '/opt/homebrew/bin'):
        if d not in path and os.path.isdir(d):
            path.insert(0, d)
    os.environ['PATH'] = os.pathsep.join(path)


def load_file(path: str) -> tuple[np.ndarray, int, str]:
    """
    Returns (audio_data, sample_rate, source_type).
    audio_data shape: (samples, channels), float32.
    source_type: 'audio' or 'video'
    """
    if not os.path.isfile(path):
        raise LoadError(f'ファイルが見つかりません: {path}')
    path = os.path.abspath(path)  # '-' で始まる名前をオプションと誤認させない

    ext = Path(path).suffix.lower()
    if ext in SUPPORTED_VIDEO:
        audio, sr = _decode_with_ffmpeg(path)
        return audio, sr, 'video' if _has_video(path) else 'audio'

    audio, sr = _load_audio(path)
    if audio.shape[0] == 0:
        raise LoadError('音声が空です。')
    return audio, sr, 'audio'


def _load_audio(path: str) -> tuple[np.ndarray, int]:
    try:
        return sf.read(path, always_2d=True, dtype='float32')
    except sf.LibsndfileError:
        # m4a など libsndfile が読めない形式
        return _decode_with_ffmpeg(path)


def _probe(path: str, *args: str) -> dict:
    try:
        result = subprocess.run(
            [tool_path('ffprobe'), '-v', 'error', '-of', 'json', *SAFE_INPUT, *args, path],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise LoadError(FFMPEG_MISSING)
    if result.returncode != 0:
        raise LoadError(f'ファイルを解析できませんでした:\n{result.stderr.strip()}')
    return json.loads(result.stdout)


def _has_video(path: str) -> bool:
    info = _probe(path, '-select_streams', 'v:0', '-show_entries', 'stream=codec_type')
    return bool(info.get('streams'))


def _decode_with_ffmpeg(path: str) -> tuple[np.ndarray, int]:
    info = _probe(path, '-select_streams', 'a:0', '-show_entries', 'stream=sample_rate,channels')
    streams = info.get('streams')
    if not streams:
        raise LoadError('音声トラックがありません。')
    sr = int(streams[0]['sample_rate'])
    channels = int(streams[0]['channels'])

    try:
        result = subprocess.run(
            [tool_path('ffmpeg'), '-v', 'error', *SAFE_INPUT, '-i', path, '-map', '0:a:0',
             '-f', 'f32le', '-c:a', 'pcm_f32le', '-ar', str(sr), '-ac', str(channels), 'pipe:1'],
            capture_output=True,
        )
    except FileNotFoundError:
        raise LoadError(FFMPEG_MISSING)
    if result.returncode != 0:
        message = result.stderr.decode('utf-8', 'replace').strip()
        raise LoadError(f'音声を取り出せませんでした:\n{message}')

    audio = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, channels)
    if audio.shape[0] == 0:
        raise LoadError('音声が空です。')
    return audio, sr
