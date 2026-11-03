"""音声処理のテスト。実行: python -m unittest discover -s tests -t ."""
import http.server
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from audio.analyzer import compute_levels
from audio.envelope import Envelope, GAIN_MAX_DB, GAIN_MIN_DB
from audio.loader import LoadError, ensure_tools, load_file
from audio.player import Player
from utils import edit_store
from utils.exporter import ExportError, _codec_args, export_audio, export_video

ensure_tools()
SR = 48000


def tone(seconds=1.0, amp=0.1, channels=2):
    t = np.arange(int(SR * seconds)) / SR
    wave = (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.stack([wave] * channels, axis=1)


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)))


class EnvelopeTest(unittest.TestCase):
    def test_flat_gain_is_unity(self):
        self.assertTrue(np.allclose(Envelope(1.0).gain_range(0, 100, SR), 1.0))

    def test_gain_interpolates_in_db(self):
        e = Envelope(10.0)
        e.set_points([(0.0, 0.0), (10.0, -20.0)])
        self.assertAlmostEqual(e.gain_db_at(5.0), -10.0)

    def test_gain_range_matches_position_in_file(self):
        e = Envelope(10.0)
        e.set_points([(0.0, 0.0), (10.0, -20.0)])
        whole = e.gain_range(0, 10 * SR, SR)
        np.testing.assert_allclose(e.gain_range(5 * SR, 1000, SR), whole[5 * SR:5 * SR + 1000])

    def test_drag_does_not_jump_over_neighbor(self):
        e = Envelope(10.0)
        idx = e.add_point(3.0, -10.0)
        e.add_point(5.0, -20.0)
        for t in (4.0, 6.0, 7.0):
            e.move_point(idx, t, -30.0)
        pts = e.get_points()
        self.assertEqual(pts[2], (5.0, -20.0))
        self.assertLess(pts[1][0], 5.0)
        self.assertEqual(pts[1][1], -30.0)

    def test_endpoints_stay_at_both_ends(self):
        e = Envelope(10.0)
        e.move_point(0, 3.0, -5.0)
        e.move_point(1, 3.0, -7.0)
        self.assertEqual(e.get_points(), [(0.0, -5.0), (10.0, -7.0)])

    def test_add_near_endpoint_keeps_anchor(self):
        e = Envelope(10.0)
        e.add_point(0.001, -6.0)
        self.assertEqual(e.get_points(), [(0.0, -6.0), (10.0, 0.0)])

    def test_endpoints_cannot_be_removed(self):
        e = Envelope(10.0)
        e.remove_point(0)
        e.remove_point(1)
        self.assertEqual(len(e.get_points()), 2)

    def test_gain_is_limited(self):
        e = Envelope(10.0)
        e.move_point(0, 0.0, 99.0)
        e.move_point(1, 10.0, -99.0)
        self.assertEqual(e.get_points(), [(0.0, GAIN_MAX_DB), (10.0, GAIN_MIN_DB)])

    def test_set_points_rejects_broken_values(self):
        e = Envelope(10.0)
        e.set_points([(float('nan'), 1.0), (5.0, float('inf')), (20.0, 99.0)])
        pts = e.get_points()
        self.assertEqual((pts[0][0], pts[-1][0]), (0.0, 10.0))
        self.assertTrue(all(np.isfinite(g) for _, g in pts))

    def test_copy_is_independent(self):
        e = Envelope(10.0)
        c = e.copy()
        e.add_point(5.0, -6.0)
        self.assertTrue(c.is_flat())


class LevelsTest(unittest.TestCase):
    def test_sine_levels(self):
        times, rms, peak = compute_levels(tone(amp=0.5), SR)
        self.assertAlmostEqual(float(rms[5]), 20 * np.log10(0.5 / np.sqrt(2)), places=1)
        self.assertAlmostEqual(float(peak[5]), 20 * np.log10(0.5), places=1)
        self.assertEqual(len(times), 20)

    def test_short_tail_is_not_lost(self):
        times, _, _ = compute_levels(tone(seconds=1.01), SR)
        self.assertEqual(len(times), 21)
        self.assertLessEqual(times[-1], 1.01)

    def test_shorter_than_one_window(self):
        times, rms, _ = compute_levels(tone(seconds=0.01), SR)
        self.assertEqual(len(times), 1)
        self.assertGreater(float(rms[0]), -80.0)

    def test_silence(self):
        _, rms, peak = compute_levels(np.zeros((SR, 1), dtype=np.float32), SR)
        self.assertEqual(float(rms.min()), -80.0)
        self.assertEqual(float(peak.max()), -80.0)

    def test_block_boundary(self):
        # BLOCK_FRAMES をまたいでも位置がずれない
        audio = np.zeros((2400 * 20010, 1), dtype=np.float32)
        audio[2400 * 20005:2400 * 20006] = 0.5
        _, rms, _ = compute_levels(audio, SR)
        self.assertEqual(int(np.argmax(rms)), 20005)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return str(self.dir / name)

    def make_m4a(self, name='s.m4a'):
        wav = self.path('src.wav')
        sf.write(wav, tone(seconds=2.0, amp=0.2), SR)
        out = self.path(name)
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', wav, '-c:a', 'aac', out], check=True)
        return out

    def make_video(self, name='v.mp4'):
        out = self.path(name)
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=d=2:s=160x120',
                        '-f', 'lavfi', '-i', 'sine=d=2:r=48000', '-shortest', '-pix_fmt', 'yuv420p', out],
                       check=True)
        return out

    def test_load_m4a(self):
        audio, sr, kind = load_file(self.make_m4a())
        self.assertEqual((sr, audio.shape[1], kind), (SR, 2, 'audio'))
        self.assertAlmostEqual(audio.shape[0] / sr, 2.0, delta=0.1)

    def test_load_video(self):
        audio, sr, kind = load_file(self.make_video())
        self.assertEqual(kind, 'video')
        self.assertGreater(audio.shape[0], SR)

    def test_load_mp4_without_video_is_audio(self):
        self.assertEqual(load_file(self.make_m4a('a.mp4'))[2], 'audio')

    def test_load_video_without_audio_explains(self):
        out = self.path('mute.mp4')
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=d=1:s=160x120',
                        '-pix_fmt', 'yuv420p', out], check=True)
        with self.assertRaisesRegex(LoadError, '音声トラックがありません'):
            load_file(out)

    def test_load_broken_file_raises_load_error(self):
        out = self.path('broken.m4a')
        Path(out).write_bytes(b'not audio')
        with self.assertRaises(LoadError):
            load_file(out)

    def test_load_missing_file(self):
        with self.assertRaisesRegex(LoadError, '見つかりません'):
            load_file(self.path('none.wav'))

    def test_missing_ffmpeg_explains(self):
        src = self.make_m4a()
        with mock.patch.dict(os.environ, {'PATH': '/nonexistent'}):
            with self.assertRaisesRegex(LoadError, 'brew install ffmpeg'):
                load_file(src)

    def export(self, name, source='', gain=-6.0, audio=None):
        audio = tone() if audio is None else audio
        e = Envelope(audio.shape[0] / SR)
        e.set_points([(0.0, gain), (e.duration, gain)])
        out = self.path(name)
        export_audio(audio, SR, e, out, source)
        return out, audio

    def test_export_applies_gain(self):
        for name in ('o.wav', 'o.flac', 'o.m4a', 'o.mp3'):
            out, src = self.export(name)
            result = load_file(out)[0]
            self.assertAlmostEqual(rms_db(result) - rms_db(src), -6.0, delta=0.3, msg=name)

    def test_export_uses_only_codecs_of_the_release_build(self):
        # AAC は macOS 内蔵、映像は再符号化しない
        cases = {'.m4a': 'aac_at', '.mp4': 'aac_at', '.mkv': 'aac_at', '.mp3': 'libmp3lame',
                 '.avi': 'libmp3lame', '.webm': 'opus'}
        for ext, codec in cases.items():
            self.assertEqual(_codec_args(ext, SR, 2)[1], codec)

    def test_aac_handles_rates_and_channels(self):
        for sr, channels in [(8000, 1), (44100, 2), (48000, 6), (48000, 8), (96000, 2), (192000, 1)]:
            audio = np.full((sr, channels), 0.1, dtype=np.float32)
            out = self.path(f'{sr}_{channels}.m4a')
            export_audio(audio, sr, Envelope(1.0), out)
            result, result_sr, _ = load_file(out)
            self.assertEqual(result.shape[1], channels, out)
            self.assertLessEqual(result_sr, 48000)

    def test_webm_output_of_surround_is_stereo(self):
        src = self.path('v.webm')
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=d=1:s=160x120',
                        '-f', 'lavfi', '-i', 'sine=d=1:r=48000', '-shortest', '-c:v', 'libvpx-vp9',
                        '-c:a', 'libopus', src], check=True)
        audio = np.zeros((48000, 6), dtype=np.float32)
        out = self.path('out.webm')
        export_video(src, audio, 48000, Envelope(1.0), out)
        self.assertEqual(load_file(out)[0].shape[1], 2)

    def test_crafted_playlist_does_not_connect_outside(self):
        hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_error(404)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_address[1]}/secret.ts'

        # 確認: 対策なしなら FFmpeg は接続しに行く
        out = self.path('crafted.m3u8')
        Path(out).write_text(f'#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1,\n{url}\n#EXT-X-ENDLIST\n')
        subprocess.run(['ffprobe', '-v', 'quiet', '-protocol_whitelist', 'file,http,tcp', out], timeout=20)
        self.assertTrue(hits, '対照実験で接続が起きていない')

        hits.clear()
        with self.assertRaises(LoadError):
            load_file(out)
        self.assertEqual(hits, [])

    def test_relative_name_starting_with_dash(self):
        src = self.dir / '-i.m4a'
        Path(self.make_m4a()).rename(src)
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(load_file('-i.m4a')[1], SR)

    def test_export_can_boost(self):
        out, src = self.export('o.wav', gain=12.0)
        self.assertAlmostEqual(rms_db(load_file(out)[0]) - rms_db(src), 12.0, delta=0.1)

    def test_export_keeps_bit_depth(self):
        src = self.path('s24.wav')
        sf.write(src, tone(), SR, subtype='PCM_24')
        self.assertEqual(sf.info(self.export('o.wav', src)[0]).subtype, 'PCM_24')
        self.assertEqual(sf.info(self.export('o.flac', src)[0]).subtype, 'PCM_24')

    def test_export_of_lossy_source_is_16bit(self):
        self.assertEqual(sf.info(self.export('o.wav', self.make_m4a())[0]).subtype, 'PCM_16')

    def test_export_clips_instead_of_wrapping(self):
        out, _ = self.export('o.wav', gain=24.0, audio=tone(amp=0.5))
        self.assertLessEqual(float(np.abs(load_file(out)[0]).max()), 1.0)

    def test_export_refuses_source_as_output(self):
        src = self.path('s.wav')
        sf.write(src, tone(), SR)
        before = Path(src).read_bytes()
        with self.assertRaisesRegex(ExportError, '上書きできません'):
            export_audio(tone(), SR, Envelope(1.0), src, src)
        self.assertEqual(Path(src).read_bytes(), before)

    def test_export_unknown_format(self):
        with self.assertRaisesRegex(ExportError, 'WAV'):
            export_audio(tone(), SR, Envelope(1.0), self.path('o.xyz'))

    def test_failed_export_keeps_existing_file_and_no_partial(self):
        out = self.path('o.m4a')
        Path(out).write_bytes(b'keep me')
        with mock.patch('utils.exporter.AUDIO_BITRATE', 'invalid'):
            with self.assertRaises(ExportError):
                export_audio(tone(), SR, Envelope(1.0), out)
        self.assertEqual(Path(out).read_bytes(), b'keep me')
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['o.m4a'])

    def test_export_video_changes_only_audio(self):
        src = self.make_video()
        audio, sr, _ = load_file(src)
        e = Envelope(audio.shape[0] / sr)
        e.set_points([(0.0, -12.0), (e.duration, -12.0)])
        out = self.path('out.mp4')
        export_video(src, audio, sr, e, out)

        probe = lambda p: json.loads(subprocess.run(
            ['ffprobe', '-v', 'error', '-of', 'json', '-show_streams', p],
            capture_output=True, text=True).stdout)['streams']
        video = [s for s in probe(out) if s['codec_type'] == 'video'][0]
        original = [s for s in probe(src) if s['codec_type'] == 'video'][0]
        self.assertEqual((video['codec_name'], video['nb_frames']), (original['codec_name'], original['nb_frames']))
        self.assertAlmostEqual(rms_db(load_file(out)[0]) - rms_db(audio), -12.0, delta=0.5)

    def test_export_progress_reaches_end(self):
        seen = []
        export_audio(tone(seconds=30), SR, Envelope(30.0), self.path('o.wav'), progress=seen.append)
        self.assertEqual(seen[-1], 1.0)
        self.assertEqual(seen, sorted(seen))

    def test_edit_store_roundtrip(self):
        src = self.path('s.wav')
        sf.write(src, tone(), SR)
        with mock.patch.object(edit_store, 'STORE_DIR', self.dir / 'store'):
            edit_store.save_points(src, [(0.0, -3.0), (1.0, 0.0)])
            self.assertEqual(edit_store.load_points(src), [(0.0, -3.0), (1.0, 0.0)])

            # 元のファイルが変わったら前の編集を使わない
            sf.write(src, tone(seconds=2.0), SR)
            self.assertIsNone(edit_store.load_points(src))

    def test_edit_store_flat_removes_entry(self):
        src = self.path('s.wav')
        sf.write(src, tone(), SR)
        with mock.patch.object(edit_store, 'STORE_DIR', self.dir / 'store'):
            edit_store.save_points(src, [(0.0, -3.0), (1.0, 0.0)])
            edit_store.save_points(src, [(0.0, 0.0), (1.0, 0.0)])
            self.assertIsNone(edit_store.load_points(src))

    def test_edit_store_ignores_broken_data(self):
        src = self.path('s.wav')
        sf.write(src, tone(), SR)
        with mock.patch.object(edit_store, 'STORE_DIR', self.dir / 'store'):
            edit_store.save_points(src, [(0.0, -3.0), (1.0, 0.0)])
            next((self.dir / 'store').iterdir()).write_text('{broken', 'utf-8')
            self.assertIsNone(edit_store.load_points(src))


class PlayerTest(unittest.TestCase):
    """無音を実際の出力デバイスへ流す（音は出ない）。"""

    def make(self, seconds=0.25, channels=1):
        audio = np.zeros((int(SR * seconds), channels), dtype=np.float32)
        p = Player()
        p.load(audio, SR, Envelope(seconds))
        self.addCleanup(p.pause)
        return p

    def wait_finished(self, p, timeout=2.0):
        limit = time.time() + timeout
        while time.time() < limit:
            if p.poll_finished():
                return True
            time.sleep(0.01)
        return False

    def test_finishes_and_releases_stream(self):
        p = self.make()
        p.play()
        self.assertTrue(self.wait_finished(p))
        self.assertFalse(p.is_playing)
        self.assertIsNone(p._stream)

    def test_replay_after_end_starts_from_beginning(self):
        p = self.make()
        p.play()
        self.assertTrue(self.wait_finished(p))
        self.assertAlmostEqual(p.position_sec, 0.25, delta=0.01)
        p.play()
        self.assertLess(p.position_sec, 0.2)
        self.assertTrue(self.wait_finished(p))

    def test_pause_is_not_reported_as_end(self):
        p = self.make(seconds=2.0)
        p.play()
        time.sleep(0.1)
        p.pause()
        self.assertFalse(self.wait_finished(p, timeout=0.2))
        self.assertLess(p.position_sec, 1.0)

    def test_seek_while_playing_keeps_stream(self):
        p = self.make(seconds=2.0)
        p.play()
        stream = p._stream
        p.seek(1.5)
        self.assertIs(p._stream, stream)
        self.assertGreaterEqual(p.position_sec, 1.5)

    def test_surround_plays_on_stereo_output(self):
        p = self.make(channels=6)
        p.play()
        self.assertTrue(self.wait_finished(p))

    def test_callback_applies_gain_and_clips(self):
        p = Player()
        audio = np.full((4096, 2), 0.5, dtype=np.float32)
        e = Envelope(4096 / SR)
        e.set_points([(0.0, 12.0), (e.duration, 12.0)])
        p.load(audio, SR, e)
        out = np.zeros((1024, 2), dtype=np.float32)
        p._callback(out, 1024, None, None)
        self.assertEqual(float(out.max()), 1.0)
