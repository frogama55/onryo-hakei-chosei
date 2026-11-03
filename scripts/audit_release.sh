#!/bin/bash
# 配布版がライセンス条件を守っているかを検査する。問題があれば失敗する。
set -uo pipefail

APP="$1"
FRAMEWORKS="$APP/Contents/Frameworks"
FFMPEG="$FRAMEWORKS/ffmpeg/ffmpeg"
FAILED=0
fail() { echo "NG: $*" >&2; FAILED=1; }

# 1. GPL / nonfree のコーデックと、余分なライブラリが入っていない
BAD="$(find "$APP" \( -type f -o -type l \) | grep -iE 'x26[45]|xvid|fdk|svtav1|dav1d|libvpx|readline' || true)"
[ -z "$BAD" ] || fail "同梱してはいけないライブラリ: $BAD"

# 2. FFmpeg は同梱のものだけで、ffmpeg フォルダの外に libav* がない
OUTSIDE="$(find "$FRAMEWORKS" -maxdepth 1 -name 'libav*' -o -maxdepth 1 -name 'libsw*' -o -maxdepth 1 -name 'libmp3lame*')"
[ -z "$OUTSIDE" ] || fail "ffmpeg フォルダの外にある FFmpeg のライブラリ: $OUTSIDE"

# 3. FFmpeg のビルド設定
CONF="$("$FFMPEG" -hide_banner -buildconf 2>&1)"
echo "$CONF" | grep -q -- '--disable-everything' || fail "想定した FFmpeg ではありません"
if echo "$CONF" | grep -qE -- '--enable-(gpl|nonfree|version3)'; then fail "FFmpeg に GPL / nonfree が有効"; fi
"$FFMPEG" -hide_banner -L 2>&1 | grep -q 'GNU Lesser General Public' || fail "FFmpeg が LGPL ではありません"

# 4. 配布物の外を参照していない
while IFS= read -r -d '' f; do
    file -b "$f" | grep -q 'Mach-O' || continue
    REFS="$(otool -L "$f" 2>/dev/null | awk 'NR>1{print $1}' | grep -vE '^(@|/usr/lib/|/System/)' || true)"
    [ -z "$REFS" ] || fail "$(basename "$f") が外部を参照: $REFS"
done < <(find "$APP" -type f -perm -u+x -print0; find "$APP" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)

# 5. 個人のパスが FFmpeg に残っていない
if grep -rlaE '/Users/[a-z]' "$FRAMEWORKS/ffmpeg" >/dev/null 2>&1; then fail "FFmpeg に個人のパスが残っています"; fi

# 6. ライセンス文書
for name in LICENSE.txt THIRD_PARTY_NOTICES.md FFmpeg-LGPL-2.1.txt LAME-LGPL.txt Qt-LGPL.txt FFmpeg-BUILD_INFO.txt; do
    [ -s "$APP/Contents/Resources/licenses/$name" ] || fail "ライセンス文書がありません: $name"
done

[ "$FAILED" = 0 ] || { echo "監査に失敗しました。配布してはいけません。" >&2; exit 1; }
echo "監査に合格しました"
