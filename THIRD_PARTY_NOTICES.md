# 第三者ソフトウェア

音量波形調整の本体は [GNU General Public License v3.0](LICENSE) で配布しています。
配布版には、次のソフトウェアが含まれます。各ライセンスの全文は、配布版の中の `licenses` フォルダにあります。

| ソフトウェア | ライセンス | 用途 |
|---|---|---|
| FFmpeg | LGPL 2.1 以降 | 音声と動画の読み書き |
| LAME | LGPL 2 以降 | MP3 の書き出し |
| Qt 6（PyQt6-Qt6） | LGPL 3 | 画面 |
| PyQt6 | GPL 3 | Qt を Python から使う |
| PyQt6-sip | BSD-2-Clause | PyQt6 の一部 |
| NumPy | BSD-3-Clause ほか | 数値計算 |
| python-soundfile | BSD-3-Clause | 音声ファイルの読み書き |
| libsndfile | LGPL 2.1 以降 | 音声ファイルの読み書き |
| python-sounddevice | MIT | 音声の再生 |
| PortAudio | MIT | 音声の再生 |
| CFFI | MIT | ライブラリの呼び出し |
| Python | PSF-2.0 | 実行環境 |
| GNU gettext の libintl 1.0 | LGPL 2.1 以降 | Python が使用 |

## FFmpeg

This software uses libraries from the FFmpeg project under the LGPLv2.1.

FFmpeg は `--enable-gpl` と `--enable-nonfree` を使わずにビルドしています。FFmpeg は改変していません。
使用したソースコード、LAME のソースコード、ビルド手順は、配布版と同じ
GitHub Releases のページに置いています。

FFmpeg は FFmpeg プロジェクトの著作物であり、本ソフトウェアの作者のものではありません。
AAC の処理には macOS に内蔵の機能を使います。映像は再圧縮せず、そのままコピーします。

## Qt

Qt は LGPL 3 のもとで、動的ライブラリのまま同梱しています。
ソースコードは [download.qt.io](https://download.qt.io/official_releases/qt/) から入手できます。

## GNU gettext

libintl は LGPL 2.1 以降のもとで、動的ライブラリのまま同梱しています。
ソースコードは [ftp.gnu.org](https://ftp.gnu.org/gnu/gettext/) から入手できます（版 1.0）。
