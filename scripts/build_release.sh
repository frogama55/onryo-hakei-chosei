#!/bin/bash
# 配布用の .app と zip を作る。先に scripts/build_ffmpeg.sh を実行しておく。
# 必要なもの: .venv-release（PyInstaller と requirements.txt の内容）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv-release/bin/python"
cd "$ROOT"

VERSION="$("$PY" -c 'import app_info; print(app_info.VERSION)')"
"$PY" -c 'import app_info, sys; sys.exit(0 if app_info.COPYRIGHT_HOLDER and app_info.SOURCE_URL else "公開情報が未設定です")'
[ -x third_party/ffmpeg/bin/ffmpeg ] || { echo "先に scripts/build_ffmpeg.sh を実行してください" >&2; exit 1; }

"$PY" -m unittest discover -s tests -t .
"$PY" make_icon.py
"$PY" scripts/collect_licenses.py
rm -rf build dist
"$PY" -m PyInstaller --noconfirm --clean --log-level WARN \
    --distpath dist --workpath build packaging/app.spec

APP="dist/音量波形調整.app"
rm -rf "dist/onryo-hakei-chosei"

# LGPL でビルドした FFmpeg を組み込む
FFMPEG_DIR="$APP/Contents/Frameworks/ffmpeg"
mkdir -p "$FFMPEG_DIR"
cp third_party/ffmpeg/bin/ffmpeg third_party/ffmpeg/bin/ffprobe "$FFMPEG_DIR/"
for lib in third_party/ffmpeg/lib/*.dylib; do
    name="$(basename "$lib")"
    # 実行時に参照される名前（libavcodec.62.dylib など）だけを同梱する
    if [ "$(otool -D "$lib" | tail -1)" = "@rpath/$name" ]; then
        cp -L "$lib" "$FFMPEG_DIR/"
    fi
done
codesign --force --deep -s - "$APP"

./scripts/audit_release.sh "$APP"

ditto -c -k --sequesterRsrc --keepParent "$APP" "dist/onryo-hakei-chosei-$VERSION-macos-arm64.zip"
ls -la dist
