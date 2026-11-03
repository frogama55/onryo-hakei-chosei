import faulthandler
import sys
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox

from app_info import APP_NAME
from audio.loader import ensure_tools
from ui.main_window import MainWindow


def report_exception(kind, value, tb):
    # PyQt はスロット内の未処理例外でアプリごと終了するので、ここで受け止める
    traceback.print_exception(kind, value, tb)
    QMessageBox.critical(None, 'エラー', f'予期しないエラーが発生しました:\n{value}')


def main():
    faulthandler.enable()
    ensure_tools()
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
