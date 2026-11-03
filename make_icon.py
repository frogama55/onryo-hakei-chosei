"""
アイコン生成スクリプト。
PyQt6 で各サイズの PNG を描画し、macOS の iconutil で .icns に変換する。
"""
import os
import subprocess
import sys
import math

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import (
    QImage, QPainter, QColor, QPainterPath,
    QLinearGradient, QPen, QBrush, QFont,
)
from PyQt6.QtCore import Qt, QRectF, QPointF

app = QApplication(sys.argv)

ICONSET = 'app_icon.iconset'
os.makedirs(ICONSET, exist_ok=True)

# iconutil が要求するサイズ一覧 (論理サイズ, 実ピクセルサイズ, ファイル名)
SIZES = [
    (16,   16,   'icon_16x16.png'),
    (16,   32,   'icon_16x16@2x.png'),
    (32,   32,   'icon_32x32.png'),
    (32,   64,   'icon_32x32@2x.png'),
    (128,  128,  'icon_128x128.png'),
    (128,  256,  'icon_128x128@2x.png'),
    (256,  256,  'icon_256x256.png'),
    (256,  512,  'icon_256x256@2x.png'),
    (512,  512,  'icon_512x512.png'),
    (512,  1024, 'icon_512x512@2x.png'),
]


def draw_icon(px: int) -> QImage:
    img = QImage(px, px, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 角丸背景
    radius = px * 0.22
    bg_rect = QRectF(0, 0, px, px)
    bg_grad = QLinearGradient(0, 0, 0, px)
    bg_grad.setColorAt(0.0, QColor('#2a2a2e'))
    bg_grad.setColorAt(1.0, QColor('#18181b'))
    p.setBrush(QBrush(bg_grad))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(bg_rect, radius, radius)

    # 波形バーを描画
    n_bars = 11
    margin_x = px * 0.14
    margin_y = px * 0.18
    bar_area_w = px - margin_x * 2
    bar_area_h = px - margin_y * 2
    bar_w = bar_area_w / n_bars * 0.55
    bar_gap = bar_area_w / n_bars * 0.45
    bar_step = bar_area_w / n_bars

    # バーの高さ比率 (音量波形風)
    heights = [0.30, 0.50, 0.65, 0.80, 0.95, 1.00, 0.90, 0.72, 0.55, 0.38, 0.22]

    for i, h_ratio in enumerate(heights):
        x = margin_x + i * bar_step
        bar_h = bar_area_h * h_ratio
        y = margin_y + (bar_area_h - bar_h) / 2

        bar_rect = QRectF(x, y, bar_w, bar_h)
        bar_grad = QLinearGradient(x, y, x, y + bar_h)
        bar_grad.setColorAt(0.0, QColor('#64aaef'))
        bar_grad.setColorAt(0.5, QColor('#1a6fbb'))
        bar_grad.setColorAt(1.0, QColor('#0d4a80'))
        p.setBrush(QBrush(bar_grad))
        p.setPen(Qt.PenStyle.NoPen)
        corner = bar_w * 0.35
        p.drawRoundedRect(bar_rect, corner, corner)

    # 上限ラインをうっすら赤で
    if px >= 64:
        line_y = margin_y + bar_area_h * 0.15
        pen = QPen(QColor(231, 76, 60, 180))
        pen.setWidthF(max(1.0, px * 0.012))
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(margin_x, line_y), QPointF(px - margin_x, line_y))

    p.end()
    return img


for _logical, pixel, fname in SIZES:
    img = draw_icon(pixel)
    out_path = os.path.join(ICONSET, fname)
    img.save(out_path)
    print(f'  {out_path}')

print('iconutil で .icns を生成中...')
result = subprocess.run(
    ['iconutil', '-c', 'icns', ICONSET, '-o', 'app_icon.icns'],
    capture_output=True, text=True
)
if result.returncode != 0:
    print(f'iconutil エラー: {result.stderr}')
    sys.exit(1)

print('app_icon.icns を生成しました')
