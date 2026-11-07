# -*- coding: utf-8 -*-
"""顶栏图标集（v1.9）。

为什么不用字符符号：`⇧ / ⤢ / ⚙ / ×` 这类字形在不同字体下粗细、基线都不一致，
在 16px 的按钮里容易歪、也认不出是什么。这里用内嵌 SVG 画线条图标，
两个颜色态（常态 / 悬停）各渲染一次，交给 QIcon 的 Normal / Active 模式。

渲染按 2× 出图（devicePixelRatio=2），125%~200% 缩放下也不会发虚。
"""
from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme as T

_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

# 线条风，统一 24 栅格；全部无填充，靠描边
_BODIES = {
    # 图钉：帽 + 针
    "pin": '<path d="M9.2 3h5.6l-.9 5.1 3.4 3.3v1.9H6.7v-1.9l3.4-3.3L9.2 3z"/>'
           '<path d="M12 13.3V21"/>',
    # 并排：整窗 + 中线（Windows 分屏就是这个形状；两块空矩形在 18px 下会糊成"00"）
    "split": '<rect x="2.6" y="5" width="18.8" height="14" rx="2.2"/>'
             '<path d="M12 5v14"/>',
    # 设置：三滑块（Material 的 tune）。比齿轮在 18px 下更容易认出来 ——
    # 齿轮的齿在这个尺寸会和中心圆糊成一团"太阳"。
    "gear": '<path d="M3 7h6M15 7h6M3 12h11M19 12h2M3 17h3M11 17h10"/>'
            '<circle cx="12" cy="7" r="2.2"/><circle cx="17" cy="12" r="2.2"/>'
            '<circle cx="8.5" cy="17" r="2.2"/>',
    # 最小化：一条横线
    "minimize": '<path d="M5 12h14"/>',
    # 关闭：叉
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    # 分组折叠箭头：展开（朝下）/ 收起（朝右）。不用 ⌄ › 字符 —— 不同字体基线不一
    "chev_down": '<path d="M6 9.5l6 6 6-6"/>',
    "chev_right": '<path d="M9.5 6l6 6-6 6"/>',
}

_CACHE = {}


def pixmap(name: str, color: str, size: int = 18, ratio: float = 2.0) -> QPixmap:
    """单色图标位图。同 (name, color, size, ratio) 复用，避免每次重建。"""
    key = (name, color, size, ratio)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    body = _BODIES.get(name)
    if body is None:
        return QPixmap()
    svg = _SVG.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(ratio)
    _CACHE[key] = pm
    return pm


def icon(name: str, size: int = 18) -> QIcon:
    """Normal = 次要色，Active（悬停/按下）= 主文字色。"""
    ic = QIcon()
    ic.addPixmap(pixmap(name, T.TEXT_DIM, size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, T.TEXT, size), QIcon.Active, QIcon.Off)
    ic.addPixmap(pixmap(name, T.TEXT, size), QIcon.Active, QIcon.On)
    # 开启态用亮蓝（ACCENT 在深色底上偏暗，看着不像"已启用"）
    ic.addPixmap(pixmap(name, "#8AB4F8", size), QIcon.Normal, QIcon.On)
    return ic
