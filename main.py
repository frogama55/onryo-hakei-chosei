import faulthandler
import sys
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from app_info import APP_NAME
from audio.envelope import Envelope
from audio.loader import ensure_tools, load_file, tool_path
from ui.main_window import MainWindow
from utils.exporter import export_audio, export_video


def report_exception(kind, value, tb):
    # PyQt はスロット内の未処理例外でアプリごと終了するので、ここで受け止める
    traceback.print_exception(kind, value, tb)
    QMessageBox.critical(None, 'エラー', f'予期しないエラーが発生しました:\n{value}')


def self_test(source: str, output: str) -> None:
    """配布版の確認用。画面を出さず、同梱の FFmpeg で読み込みと書き出しを行う。"""
    audio, sr, source_type = load_file(source)
    envelope = Envelope(audio.shape[0] / sr)
    envelope.set_points([(0.0, -6.0), (envelope.duration, -6.0)])
    if source_type == 'video':
        export_video(source, audio, sr, envelope, output)
    else:
        export_audio(audio, sr, envelope, output, source)
    print(tool_path('ffmpeg'), source_type, audio.shape, sr, flush=True)


def main():
    faulthandler.enable()
    ensure_tools()
    if sys.argv[1:2] == ['--self-test']:
        self_test(sys.argv[2], sys.argv[3])
        return

    sys.excepthook = report_exception

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    window = MainWindow()
    window.show()
    if len(sys.argv) > 1:
        window.load_path(sys.argv[1])

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
