"""配布用の zip を解凍し、各形式の読み込みと書き出しを試す。

Homebrew の FFmpeg はテスト用ファイルの作成と結果の確認だけに使い、
配布版は PATH を絞って実行する。実行: python scripts/test_release.py dist/xxx.zip
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

AUDIO = [
    ('a.m4a', ['-c:a', 'aac'], 'o.m4a'),
    ('alac.m4a', ['-c:a', 'alac'], 'alac_o.m4a'),
    ('a.mp3', ['-c:a', 'libmp3lame'], 'o.mp3'),
    ('a.opus', ['-c:a', 'libopus'], 'o.wav'),
    ('a.ogg', ['-c:a', 'vorbis', '-strict', '-2'], 'ogg_o.flac'),
    ('a.flac', [], 'o.flac'),
]
VIDEO = [
    ('h264.mp4', ['-c:v', 'libx264', '-c:a', 'aac']),
    ('hevc.mp4', ['-c:v', 'libx265', '-x265-params', 'log-level=error', '-tag:v', 'hvc1', '-c:a', 'aac']),
    ('av1.mp4', ['-c:v', 'libsvtav1', '-c:a', 'aac']),
    ('h264.mov', ['-c:v', 'libx264', '-c:a', 'aac']),
    ('h264.m4v', ['-c:v', 'libx264', '-c:a', 'aac']),
    ('ac3.mkv', ['-c:v', 'libx264', '-c:a', 'ac3']),
    ('vp9.webm', ['-c:v', 'libvpx-vp9', '-c:a', 'libopus']),
    ('mpeg4.avi', ['-c:v', 'mpeg4', '-c:a', 'libmp3lame']),
]
SINE = ['-f', 'lavfi', '-i', 'sine=f=440:d=3:r=48000']
TESTSRC = ['-f', 'lavfi', '-i', 'testsrc=d=3:s=320x240']


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-y', '-v', 'quiet', *args], check=True)


def video_info(path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-of', 'json', '-select_streams', 'v:0', '-count_packets',
         '-show_entries', 'stream=codec_name,nb_read_packets', str(path)],
        capture_output=True, text=True, check=True)
    return json.loads(result.stdout)['streams'][0]


def rms_db(path):
    raw = subprocess.run(['ffmpeg', '-v', 'quiet', '-i', str(path), '-f', 'f32le', '-ac', '1', '-'],
                         capture_output=True, check=True).stdout
    return 20 * np.log10(np.sqrt(np.mean(np.frombuffer(raw, dtype='<f4')[4800:-4800] ** 2)))


def main():
    zip_path = Path(sys.argv[1]).resolve()
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(['ditto', '-x', '-k', str(zip_path), str(tmp / 'app')], check=True)
        app = next((tmp / 'app').glob('*.app'))
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        exe = next((app / 'Contents' / 'MacOS').iterdir())

        def run(name, output):
            result = subprocess.run(
                [str(exe), '--self-test', str(tmp / name), str(tmp / output)],
                env={'HOME': str(tmp), 'PATH': '/usr/bin:/bin'}, capture_output=True, text=True)
            if 'Frameworks/ffmpeg/ffmpeg' not in result.stdout:
                raise RuntimeError(result.stderr[-200:] or result.stdout[-200:])

        for name, codec, output in AUDIO:
            ffmpeg(*SINE, '-ac', '2', *codec, str(tmp / name))
            try:
                run(name, output)
                change = rms_db(tmp / output) - rms_db(tmp / name)
                ok = abs(change + 6.0) < 0.5
                print(f"{'OK' if ok else 'NG'} {name} -> {output}: {change:+.2f} dB")
            except Exception as e:
                ok = False
                print(f'NG {name}: {e}')
            failed += not ok

        for name, codec in VIDEO:
            output = 'out_' + name
            ffmpeg(*TESTSRC, *SINE, '-shortest', '-pix_fmt', 'yuv420p', *codec, str(tmp / name))
            try:
                run(name, output)
                before, after = video_info(tmp / name), video_info(tmp / output)
                change = rms_db(tmp / output) - rms_db(tmp / name)
                ok = before == after and abs(change + 6.0) < 0.5
                print(f"{'OK' if ok else 'NG'} {name}: {after['codec_name']} "
                      f"{after['nb_read_packets']} packets, {change:+.2f} dB")
            except Exception as e:
                ok = False
                print(f'NG {name}: {e}')
            failed += not ok

    print('失敗', failed, '件')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
