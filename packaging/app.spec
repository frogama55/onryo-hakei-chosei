# PyInstaller の設定。scripts/build_release.sh から使う
from pathlib import Path
import sys

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from app_info import APP_ID, APP_NAME, COPYRIGHT_HOLDER, VERSION


a = Analysis(
    [str(ROOT / 'main.py')],
    pathex=[str(ROOT)],
    # FFmpeg はここで渡さない。PyInstaller が Homebrew の GPL 版を拾うため、
    # scripts/build_release.sh でビルド後に組み込む
    datas=[(str(ROOT / 'third_party' / 'licenses'), 'licenses')],
    excludes=['tkinter', 'unittest', 'pydoc', 'readline', 'ssl', '_ssl', '_hashlib',
              'lzma', '_lzma', 'bz2', '_bz2', 'psutil', 'charset_normalizer', 'requests'],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=APP_ID, console=False)
coll = COLLECT(exe, a.binaries, a.datas, name=APP_ID)
app = BUNDLE(
    coll,
    name=f'{APP_NAME}.app',
    icon=str(ROOT / 'app_icon.icns'),
    bundle_identifier='io.github.frogama55.onryo-hakei-chosei',
    version=VERSION,
    info_plist={
        'CFBundleName': APP_NAME,
        'CFBundleDisplayName': APP_NAME,
        'NSHumanReadableCopyright': f'Copyright (C) 2026 {COPYRIGHT_HOLDER}. GPL-3.0',
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '15.5',  # 同梱の Python に合わせる
        'LSApplicationCategoryType': 'public.app-category.music',
    },
)
