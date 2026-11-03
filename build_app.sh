#!/bin/bash
# 開発用の .app を生成するスクリプト（この Mac でのみ動く）

set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
BUNDLE_NAME="音量波形調整.app"
DEST="${SRC_DIR}/${BUNDLE_NAME}"

echo "=== 音量波形調整.app を構築します ==="

# PyQt6 が入っている python を探す
PYTHON_PATH=""
for PY in python python3 /usr/local/bin/python3 "$HOME/.pyenv/shims/python"; do
    if command -v "$PY" &>/dev/null && "$PY" -c "import PyQt6" 2>/dev/null; then
        PYTHON_PATH="$(command -v "$PY")"
        # pyenv shim の場合は実体パスに解決
        if [[ "$PYTHON_PATH" == *"pyenv/shims"* ]]; then
            PYTHON_PATH="$("$PY" -c 'import sys; print(sys.executable)')"
        fi
        break
    fi
done

if [[ -z "$PYTHON_PATH" ]]; then
    echo "エラー: PyQt6 が入った python が見つかりません"
    exit 1
fi
echo "▶ 使用する Python: ${PYTHON_PATH}"

# アイコン生成
echo "▶ アイコンを生成中..."
cd "$SRC_DIR"
"$PYTHON_PATH" make_icon.py

# バンドルディレクトリを作り直す
rm -rf "$DEST"
mkdir -p "$DEST/Contents/MacOS"
mkdir -p "$DEST/Contents/Resources/app"

# ソースコードをバンドル内にコピー
echo "▶ ソースをバンドルにコピー中..."
/bin/cp -R "$SRC_DIR/audio"   "$DEST/Contents/Resources/app/"
/bin/cp -R "$SRC_DIR/ui"      "$DEST/Contents/Resources/app/"
/bin/cp -R "$SRC_DIR/utils"   "$DEST/Contents/Resources/app/"
/bin/cp    "$SRC_DIR/main.py" "$SRC_DIR/app_info.py" "$DEST/Contents/Resources/app/"
find "$DEST/Contents/Resources/app" -name __pycache__ -prune -exec rm -rf {} +

# アイコンをコピー
/bin/cp "$SRC_DIR/app_icon.icns" "$DEST/Contents/Resources/"

# 実行スクリプト（ランチャー）
echo "▶ ランチャーを生成中 (python: ${PYTHON_PATH})..."
cat > "$DEST/Contents/MacOS/launcher" << LAUNCHER
#!/bin/bash
APP_DIR="\$(cd "\$(dirname "\$0")/../Resources/app" && pwd)"
LOG="\$HOME/Library/Logs/onryo-hakei-chosei.log"
cd "\$APP_DIR"
if [ ! -x "${PYTHON_PATH}" ]; then
    osascript -e 'display alert "音量波形調整 を起動できません" message "Python が見つかりません: ${PYTHON_PATH}\nbuild_app.sh を実行し直してください。"'
    exit 1
fi
exec "${PYTHON_PATH}" main.py >> "\$LOG" 2>&1
LAUNCHER
chmod +x "$DEST/Contents/MacOS/launcher"

# Info.plist
cat > "$DEST/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>音量波形調整</string>
    <key>CFBundleDisplayName</key>
    <string>音量波形調整</string>
    <key>CFBundleIdentifier</key>
    <string>io.github.frogama55.onryo-hakei-chosei</string>
    <key>CFBundleVersion</key>
    <string>1.2.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.2.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleIconFile</key>
    <string>app_icon</string>
    <key>CFBundleIconName</key>
    <string>app_icon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHumanReadableCopyright</key>
    <string>Copyright (C) 2026 かねごん. GPL-3.0</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.music</string>
</dict>
</plist>
PLIST

# 隔離属性を削除（Gatekeeper のダイアログを抑制）
xattr -cr "$DEST" 2>/dev/null || true

echo ""
echo "✓ ${DEST} を生成しました"
echo ""
echo "次の手順で使えます:"
echo "  1. Finder で 音量波形調整.app をダブルクリック"
echo "  2. または Applications フォルダに移動:"
echo "     cp -r 音量波形調整.app /Applications/"
echo ""
echo "ログは ~/Library/Logs/onryo-hakei-chosei.log に書き出されます"
