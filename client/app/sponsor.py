# -*- coding: utf-8 -*-
"""赞助弹窗：两张收款码（卡片式，可点放大）+ 一句套话。

图片随包分发（`assets/`），不联网。v1.10 重做视觉：头部头像+文案、细分割线、
两张码装进圆角卡片（hover 亮边、整卡可点），底部统一提示 + 关闭。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea,
                               QVBoxLayout)

from . import theme as T
from .widgets import round_pixmap

from .paths import ASSETS


def _full(name: str) -> QPixmap:
    """原图（不缩放）——点击放大必须用它，否则放大的是一张缩略图。"""
    p = ASSETS / name
    return QPixmap(str(p)) if p.exists() else QPixmap()


def _dpr() -> float:
    """屏幕缩放比：位图按它多出像素，高分屏上才不糊。"""
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        return max(1.0, float(app.primaryScreen().devicePixelRatio() or 1.0))
    return 1.0


def _card_pix(name: str, width: int = 232, radius: int = 10) -> QPixmap:
    """收款码用的缩略图：一次缩放到目标逻辑宽 → 裁圆角 → 标记 dpr。

    两次插值是上一版"糊"的根因，这里只缩一次；按 devicePixelRatio 出图，
    150%/200% 缩放下二维码边缘也不会发毛。
    """
    p = ASSETS / name
    if not p.exists():
        return QPixmap()
    pm = QPixmap(str(p))
    if pm.isNull():
        return pm
    k = _dpr()
    tw = int(width * k)
    th = int(round(tw * pm.height() / max(1, pm.width())))
    scaled = pm.scaled(tw, th, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = round_pixmap(scaled, int(radius * k))
    out.setDevicePixelRatio(k)
    return out


class ImageViewer(QDialog):
    """收款码大图查看器：按屏幕 85% 显示，滚轮缩放（0.4~4×），双击复位，Esc 关闭。"""

    def __init__(self, pm: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("收款码 · 点击放大")
        self.setStyleSheet(T.qss())
        self._src = pm
        self._scale = 1.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)
        lay.addWidget(self.scroll)

        # 初始尺寸：屏幕 85%（长边）
        scr = self.screen().availableGeometry() if self.screen() else None
        if scr is not None and not pm.isNull():
            ratio = min(scr.width() * 0.85 / pm.width(), scr.height() * 0.85 / pm.height())
            self.resize(int(pm.width() * ratio) + 24, int(pm.height() * ratio) + 24)
        else:
            self.resize(560, 760)
        self._apply()

    def _apply(self):
        if self._src.isNull():
            self.label.setText("图片读不出来")
            return
        # 屏幕 85% 与缩放系数的较小者：默认"尽量大且不超屏"
        scr = self.screen().availableGeometry() if self.screen() else None
        base = 1.0
        if scr is not None:
            base = min(scr.width() * 0.85 / self._src.width(),
                       scr.height() * 0.85 / self._src.height(), 4.0)
        k = base * self._scale
        w = max(80, int(self._src.width() * k))
        h = max(80, int(self._src.height() * k))
        pm = self._src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label.setPixmap(pm)
        # scroll 里 widgetResizable=False 时，label 必须自己撑到图片大小，
        # 否则它保持 0 尺寸 → 整块黑（第一次截图就是这样）
        self.label.setFixedSize(pm.size())

    def wheelEvent(self, e):                     # noqa: N802
        step = 1.1 if e.angleDelta().y() > 0 else 1 / 1.1
        self._scale = max(0.4, min(4.0, self._scale * step))
        self._apply()

    def mouseDoubleClickEvent(self, e):          # noqa: N802
        self._scale = 1.0
        self._apply()


class SponsorDialog(QDialog):
    """赞助弹窗（v1.10 美化）：深色卡片 + 两张可点的收款码。

    上一版是把两张原始海报直接并排贴在弹窗里，白底/彩底在深色界面上很扎眼，
    也没有"这张可以点"的暗示。现在：
      · 头部一行（圆角头像 + 主文案 + 副文案），下面一条细分割线；
      · 两张码各自装进 `QFrame#payCard`，圆角裁图 + hover 亮边，整卡可点；
      · 底部统一提示"点击卡片可放大"，不再每张图各写一遍。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("赞助开发者")
        self.setMinimumWidth(580)
        self.setStyleSheet(T.qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(14)

        # ---- 头部
        head = QHBoxLayout()
        head.setSpacing(14)
        emo = QLabel()
        epm = _card_pix("aorunji.jpg", 72, radius=16)
        if not epm.isNull():
            emo.setPixmap(epm)
            emo.setFixedSize(72, 72)
            head.addWidget(emo, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(5)
        title = QLabel("开发不易，赞助开发者吃个烧鹅腿~")
        title.setStyleSheet(
            f"color: {T.TEXT}; font-size: {T.SZ_TITLE + 2}px; font-weight: 600;")
        title.setWordWrap(True)
        sub = QLabel("纯自愿，不影响任何功能 · 扫码后请以实际收款页为准")
        sub.setProperty("role", "sub")
        sub.setWordWrap(True)
        col.addWidget(title)
        col.addWidget(sub)
        col.addStretch(1)
        head.addLayout(col, 1)
        outer.addLayout(head)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,18);")
        outer.addWidget(sep)

        # ---- 两张码卡片
        row = QHBoxLayout()
        row.setSpacing(14)
        for name, cap, color in (("wx_pay.jpg", "微信支付", "#07C160"),
                                 ("alipay.jpg", "支付宝", "#1677FF")):
            row.addWidget(self._pay_card(name, cap, color), 1)
        outer.addLayout(row)

        # ---- 底部
        bottom = QHBoxLayout()
        hint = QLabel("点击卡片可放大 · 滚轮缩放，双击复位")
        hint.setProperty("role", "sub")
        bottom.addWidget(hint, 1)
        close = QPushButton("关闭")
        close.setFixedWidth(84)
        close.clicked.connect(self.accept)
        bottom.addWidget(close, 0)
        outer.addLayout(bottom)

    def _pay_card(self, name: str, cap: str, color: str) -> QFrame:
        """一张收款码卡片：圆角图 + 平台色小标签，整卡可点放大。"""
        card = QFrame()
        card.setObjectName("payCard")
        card.setCursor(Qt.PointingHandCursor)
        card.setToolTip("点击放大查看")
        card.setAttribute(Qt.WA_Hover, True)      # QSS 的 :hover 需要它
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 9)
        lay.setSpacing(8)

        lab = QLabel()
        lab.setAlignment(Qt.AlignCenter)
        pm = _card_pix(name, 232)
        if pm.isNull():
            lab.setText("（未找到收款码图片）")
            lab.setProperty("role", "sub")
        else:
            lab.setPixmap(pm)
            k = max(1.0, pm.devicePixelRatio())
            lab.setFixedSize(int(pm.width() / k), int(pm.height() / k))
        lay.addWidget(lab, 0, Qt.AlignHCenter)

        foot = QHBoxLayout()
        foot.setSpacing(6)
        foot.addStretch(1)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 9px;")
        nm = QLabel(cap)
        nm.setStyleSheet(f"color: {T.TEXT}; font-size: {T.SZ_BODY}px;")
        foot.addWidget(dot, 0, Qt.AlignVCenter)
        foot.addWidget(nm, 0, Qt.AlignVCenter)
        foot.addStretch(1)
        lay.addLayout(foot)

        src = _full(name)
        card.mousePressEvent = (
            lambda e, _pm=src: ImageViewer(_pm, self).exec()
            if e.button() == Qt.LeftButton else None)
        return card
