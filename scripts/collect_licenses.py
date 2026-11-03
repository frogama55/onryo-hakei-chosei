"""配布版に同梱するライセンス文書を third_party/licenses に集める。"""
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'third_party' / 'licenses'
SITE = Path(distribution('numpy').locate_file(''))
FFMPEG = ROOT / 'third_party' / 'ffmpeg'


def copy(source: Path, name: str) -> None:
    shutil.copyfile(source, OUT / name)


def package_license(package: str, name: str) -> None:
    files = [f for f in distribution(package).files
             if f.name.upper().startswith(('LICENSE', 'COPYING')) and 'dist-info' in str(f)]
    if not files:
        raise SystemExit(f'{package} のライセンス文書が見つかりません')
    copy(Path(files[0].locate()), name)


shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
copy(ROOT / 'LICENSE', 'LICENSE.txt')
copy(ROOT / 'THIRD_PARTY_NOTICES.md', 'THIRD_PARTY_NOTICES.md')
copy(FFMPEG / 'COPYING.FFmpeg', 'FFmpeg-LGPL-2.1.txt')
copy(FFMPEG / 'COPYING.LAME', 'LAME-LGPL.txt')
copy(FFMPEG / 'COPYING.FFmpeg', 'gettext-libintl-LGPL-2.1.txt')  # 同じ LGPL 2.1
copy(FFMPEG / 'BUILD_INFO.txt', 'FFmpeg-BUILD_INFO.txt')
copy(SITE / '_soundfile_data' / 'COPYING', 'libsndfile.txt')
copy(SITE / '_sounddevice_data' / 'portaudio-binaries' / 'README.md', 'PortAudio.md')
copy(Path(sys.base_prefix) / 'lib' / 'python3.12' / 'LICENSE.txt', 'Python.txt')
for package, name in [('PyQt6', 'PyQt6-GPL-3.0.txt'), ('PyQt6-Qt6', 'Qt-LGPL.txt'),
                      ('PyQt6-sip', 'PyQt6-sip.txt'), ('numpy', 'NumPy.txt'),
                      ('soundfile', 'python-soundfile.txt'), ('sounddevice', 'python-sounddevice.txt'),
                      ('cffi', 'CFFI.txt'), ('pyinstaller', 'PyInstaller.txt')]:
    package_license(package, name)
print(f'{len(list(OUT.iterdir()))} 件を {OUT} に集めました')
