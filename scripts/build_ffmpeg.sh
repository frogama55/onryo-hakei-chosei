#!/bin/bash
# 配布版に同梱する FFmpeg を LGPL の範囲でビルドする。
# FFmpeg 公式のライセンス条件（https://ffmpeg.org/legal.html）に合わせて、
# --enable-gpl と --enable-nonfree は使わず、共有ライブラリとしてビルドする。
set -euo pipefail

FFMPEG_VERSION=8.1
FFMPEG_SHA256=b072aed6871998cce9b36e7774033105ca29e33632be5b6347f3206898e0756a
LAME_VERSION=3.100
LAME_SHA256=ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THIRD="$ROOT/third_party"
SRC="$THIRD/src"
WORK="$THIRD/work"
PREFIX="$THIRD/ffmpeg"
JOBS="$(sysctl -n hw.ncpu)"
# 配布物に作業フォルダのパスが残らないよう、設定には架空の場所を書き、実際の置き場所は環境変数で渡す
INSTALL_ROOT=/opt/onryo-hakei-chosei
export MACOSX_DEPLOYMENT_TARGET=13.0

fetch() {  # url file sha256
    [ -f "$SRC/$2" ] || curl -fsSL -o "$SRC/$2" "$1"
    echo "$3  $SRC/$2" | shasum -a 256 -c -
}

mkdir -p "$SRC"
rm -rf "$WORK" "$PREFIX"
mkdir -p "$WORK"
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" "ffmpeg-$FFMPEG_VERSION.tar.xz" "$FFMPEG_SHA256"
fetch "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz" \
      "lame-$LAME_VERSION.tar.gz" "$LAME_SHA256"

echo "=== LAME ==="
tar -xzf "$SRC/lame-$LAME_VERSION.tar.gz" -C "$WORK"
cd "$WORK/lame-$LAME_VERSION"
# macOS のリンカが存在しないシンボルで失敗するので、公開シンボルの一覧から1行だけ除く
sed -i '' '/^lame_init_old$/d' include/libmp3lame.sym
./configure --prefix="$INSTALL_ROOT" --enable-shared --disable-static --disable-frontend \
            --disable-gtktest >/dev/null
make -j"$JOBS" >/dev/null
make install DESTDIR="$WORK/stage" >/dev/null

echo "=== FFmpeg ==="
tar -xJf "$SRC/ffmpeg-$FFMPEG_VERSION.tar.xz" -C "$WORK"
cd "$WORK/ffmpeg-$FFMPEG_VERSION"

CONFIGURE=(
    --prefix="$INSTALL_ROOT"
    --install-name-dir=@rpath
    --enable-shared --disable-static
    --disable-doc --disable-debug --disable-ffplay
    --disable-network --disable-autodetect --disable-everything
    --disable-avdevice --disable-swscale
    --enable-audiotoolbox --enable-libmp3lame --enable-zlib
    --enable-protocol=file,pipe
    --enable-demuxer=mov,matroska,avi,mp3,aac,flac,ogg,wav,aiff,pcm_f32le
    --enable-muxer=mp4,mov,ipod,matroska,webm,avi,mp3,pcm_f32le
    --enable-decoder=aac_at,ac3_at,eac3_at,alac,flac,mp3float,opus,vorbis
    --enable-decoder=pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_s32be,pcm_f32le,pcm_f32be,pcm_u8
    --enable-encoder=aac_at,libmp3lame,opus,pcm_f32le
    --enable-parser=aac,ac3,av1,flac,h264,hevc,mpeg4video,mpegaudio,opus,vorbis,vp8,vp9
    --enable-bsf=aac_adtstoasc,extract_extradata,vp9_superframe
    --enable-filter=abuffer,abuffersink,aformat,anull,aresample
)
export CPATH="$WORK/stage$INSTALL_ROOT/include"
export LIBRARY_PATH="$WORK/stage$INSTALL_ROOT/lib"
for option in "${CONFIGURE[@]}"; do
    case "$option" in
        --enable-gpl|--enable-nonfree|--enable-version3)
            echo "ライセンス条件に反するオプションです: $option" >&2
            exit 1
            ;;
    esac
done

./configure "${CONFIGURE[@]}" | grep -E '^(License|install prefix|External libraries)' || true
make -j"$JOBS" >/dev/null
make install DESTDIR="$WORK/stage" >/dev/null

# 同じフォルダのライブラリを読むようにする
mkdir -p "$PREFIX"
cp -R "$WORK/stage$INSTALL_ROOT/bin" "$WORK/stage$INSTALL_ROOT/lib" "$PREFIX/"
rm -rf "$PREFIX/lib/pkgconfig" "$PREFIX/lib"/*.la
lame_old="$INSTALL_ROOT/lib/libmp3lame.0.dylib"
install_name_tool -id @rpath/libmp3lame.0.dylib "$PREFIX/lib/libmp3lame.0.dylib"
for f in "$PREFIX"/bin/* "$PREFIX"/lib/*.dylib; do
    [ -L "$f" ] && continue
    install_name_tool -change "$lame_old" @rpath/libmp3lame.0.dylib "$f"
done
for f in "$PREFIX"/bin/*; do
    install_name_tool -add_rpath @loader_path/../lib -add_rpath @loader_path "$f"
done
for f in "$PREFIX"/bin/* "$PREFIX"/lib/*.dylib; do
    [ -L "$f" ] || codesign -f -s - "$f" 2>/dev/null
done
cp "$WORK/ffmpeg-$FFMPEG_VERSION/COPYING.LGPLv2.1" "$PREFIX/COPYING.FFmpeg"
cp "$WORK/lame-$LAME_VERSION/COPYING" "$PREFIX/COPYING.LAME"

# 配布物に添える、ビルド方法の説明（FFmpeg 公式の条件）
{
    echo "FFmpeg $FFMPEG_VERSION is unmodified. LAME $LAME_VERSION: one line (lame_init_old) was removed"
    echo "from include/libmp3lame.sym for macOS. Built by scripts/build_ffmpeg.sh with:"
    echo
    echo "./configure ${CONFIGURE[*]}"
} > "$PREFIX/BUILD_INFO.txt"

"$PREFIX/bin/ffmpeg" -hide_banner -L | sed -n 1,3p
if "$PREFIX/bin/ffmpeg" -hide_banner -buildconf | grep -qE -- '--enable-(gpl|nonfree)'; then
    echo "GPL または nonfree が有効です。中止します。" >&2
    exit 1
fi
echo "完了: $PREFIX"
