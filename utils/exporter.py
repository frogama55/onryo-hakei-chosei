import os
import subprocess
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import soundfile as sf

from audio.envelope import Envelope
from audio.loader import FFMPEG_MISSING, SAFE_INPUT, tool_path

CHUNK_FRAMES = 1 << 20  # メモリを増やさないよう、この単位で処理する
AUDIO_BITRATE = '192k'

PCM_FORMATS = {'.wav': 'WAV', '.flac': 'FLAC'}
ENCODED_FORMATS = {'.m4a', '.mp3'}
AUDIO_OUTPUTS = {'.wav', '.flac', '.m4a', '.mp3'}

Progress = Callable[[float], None] | None


class ExportError(Exception):
    """利用者に見せるメッセージを持つ書き出しエラー。"""


def export_audio(
    audio: np.ndarray,
    sr: int,
    envelope: Envelope,
    output_path: str,
    source_path: str = '',
    progress: Progress = None,
) -> None:
    ext = Path(output_path).suffix.lower()
    if ext not in AUDIO_OUTPUTS:
        raise ExportError(f'{ext or "拡張子なし"} には書き出せません。WAV、FLAC、M4A、MP3 から選んでください。')

    with _atomic_output(output_path, source_path) as tmp:
        if ext in ENCODED_FORMATS:
            _encode(_input_args(ext, sr, audio.shape[1]) + _codec_args(ext, sr, audio.shape[1]) + [tmp],
                    audio, sr, envelope, progress)
        else:
            subtype = _pcm_subtype(source_path, PCM_FORMATS[ext])
            with sf.SoundFile(tmp, 'w', sr, audio.shape[1], subtype) as f:
                for block in _render(audio, sr, envelope, progress):
                    f.write(block)


def export_video(
    original_video_path: str,
    audio: np.ndarray,
    sr: int,
    envelope: Envelope,
    output_path: str,
    progress: Progress = None,
) -> None:
    """Replace audio in the original video with the envelope-applied version."""
    with _atomic_output(output_path, original_video_path) as tmp:
        ext = Path(output_path).suffix.lower()
        args = [*SAFE_INPUT, '-i', original_video_path, *_input_args(ext, sr, audio.shape[1]),
                '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy',
                *_codec_args(ext, sr, audio.shape[1]), tmp]
        _encode(args, audio, sr, envelope, progress)


def _render(audio: np.ndarray, sr: int, envelope: Envelope, progress: Progress) -> Iterator[np.ndarray]:
    total = audio.shape[0]
    for start in range(0, total, CHUNK_FRAMES):
        block = audio[start:start + CHUNK_FRAMES]
        gain = envelope.gain_range(start, block.shape[0], sr)
        yield np.clip(block * gain[:, np.newaxis], -1.0, 1.0)
        if progress:
            progress(min(1.0, (start + block.shape[0]) / total))


def _pcm_subtype(source_path: str, fmt: str) -> str:
    """元のファイルのビット深度を保つ。保てない形式なら16bitにする。"""
    try:
        subtype = sf.info(source_path).subtype
    except (sf.LibsndfileError, TypeError, OSError):
        return 'PCM_16'
    return subtype if sf.check_format(fmt, subtype) else 'PCM_16'


def _input_args(ext: str, sr: int, channels: int) -> list[str]:
    layout = ['-ac', str(channels)]
    if channels == 8 and ext not in ('.mp3', '.avi', '.webm'):
        # aac_at は標準の 7.1 配置だと7チャンネルに減らしてしまう
        layout = ['-ch_layout', '7.1(wide)']
    return [*SAFE_INPUT, '-f', 'f32le', '-ar', str(sr), *layout, '-i', 'pipe:0']


def _codec_args(ext: str, sr: int, channels: int) -> list[str]:
    """配布版の FFmpeg にある符号化方式だけを使う。AAC は macOS 内蔵のものを使う。"""
    if ext in ('.mp3', '.avi'):
        return ['-c:a', 'libmp3lame', '-b:a', AUDIO_BITRATE]
    if ext == '.webm':
        layout = ['-ac', '2'] if channels > 2 else []
        return ['-c:a', 'opus', '-strict', '-2', '-b:a', AUDIO_BITRATE, *layout]
    # aac_at は 48kHz までしか受け付けない
    rate = ['-ar', '48000'] if sr > 48000 else []
    return ['-c:a', 'aac_at', '-b:a', AUDIO_BITRATE, *rate]


def _encode(args: list[str], audio: np.ndarray, sr: int, envelope: Envelope, progress: Progress) -> None:
    # stderr をパイプにすると、FFmpeg の出力が詰まって止まることがあるのでファイルで受ける
    with tempfile.TemporaryFile() as log:
        try:
            proc = subprocess.Popen(
                [tool_path('ffmpeg'), '-y', '-v', 'error', *args],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log,
            )
        except FileNotFoundError:
            raise ExportError(FFMPEG_MISSING)
        try:
            for block in _render(audio, sr, envelope, progress):
                proc.stdin.write(block.astype('<f4').tobytes())
            proc.stdin.close()
        except BrokenPipeError:
            # FFmpeg が先に終了した。原因は下の終了コードで判定する
            with suppress(BrokenPipeError):
                proc.stdin.close()
        if proc.wait() != 0:
            log.seek(0)
            raise ExportError('FFmpeg が失敗しました:\n' + log.read().decode('utf-8', 'replace')[-400:])


@contextmanager
def _atomic_output(output_path: str, source_path: str) -> Iterator[str]:
    """途中で失敗しても、既存のファイルを壊さず、書きかけも残さない。"""
    if source_path and os.path.exists(output_path) and os.path.samefile(output_path, source_path):
        raise ExportError('元のファイルには上書きできません。別の名前を指定してください。')
    out = Path(output_path)
    tmp = out.with_name(f'{out.stem}.partial{out.suffix}')
    try:
        yield str(tmp)
        os.replace(tmp, out)
    except OSError as e:
        raise ExportError(f'ファイルを書き込めませんでした:\n{e}')
    finally:
        tmp.unlink(missing_ok=True)
