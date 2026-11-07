# -*- coding: utf-8 -*-
"""关于窗口（v1.12）：版本、致谢、友链。

入口在标题栏标题旁边那个 `Beta` 徽标上 —— 用户要求"标题旁边增加一个信息窗 UI，
点击显示版本信息"。放在设置里会藏得太深：版本号是给人核对"我用的哪一版"的，
应该一眼能看到。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel,
                               QPushButton, QVBoxLayout)

from . import theme as T
from . import version as V
from .widgets import rounded_pixmap

ASSETS = Path(__file__).resolve().parent.parent / "assets"


class AboutDialog(QDialog):
    """版本 / 致谢 / 友链。只读，不需要模态以外的任何交互。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {V.APP_NAME}")
        self.setMinimumWidth(460)
        self.setStyleSheet(T.qss())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 18, 22, 16)
        outer.setSpacing(12)

        # ---- 头部：图标 + 名称 + 版本
        head = QHBoxLayout()
        head.setSpacing(14)
        logo = QLabel()
        pm = rounded_pixmap(ASSETS / "aorunji.jpg", 52, 14)
        if not pm.isNull():
            logo.setPixmap(pm)
            logo.setFixedSize(52, 52)
            head.addWidget(logo, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        title = QLabel(V.APP_NAME)
        title.setStyleSheet(
            f"color: {T.TEXT}; font-size: {T.SZ_TITLE + 2}px; font-weight: 600;")
        ver_row = QHBoxLayout()
        ver_row.setSpacing(8)
        vt = QLabel(V.full_text())
        vt.setProperty("role", "sub")
        tag = QLabel(V.CHANNEL)
        tag.setStyleSheet(
            f"color: {T.WARN_TEXT}; background: rgba(224,160,48,36);"
            f"border: 1px solid rgba(224,160,48,77); border-radius: 5px;"
            f"padding: 1px 7px; font-size: {T.SZ_SUB}px;")
        ver_row.addWidget(vt, 0, Qt.AlignVCenter)
        ver_row.addWidget(tag, 0, Qt.AlignVCenter)
        ver_row.addStretch(1)
        dev = QLabel(V.DEVELOPER)          # 开发者单独一行（用户要求）
        dev.setProperty("role", "sub")
        col.addWidget(title)
        col.addLayout(ver_row)
        col.addWidget(dev)
        col.addStretch(1)
        head.addLayout(col, 1)
        outer.addLayout(head)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,18);")
        outer.addWidget(sep)

        # ---- 致谢
        credit = QLabel(V.CREDIT)
        credit.setWordWrap(True)
        credit.setStyleSheet(f"color: {T.TEXT}; font-size: {T.SZ_BODY}px;")
        outer.addWidget(credit)

        # ---- 友链：用富文本 href，Qt 会交给系统浏览器
        link_row = QHBoxLayout()
        link_row.setSpacing(8)
        who = QLabel("友链")
        who.setProperty("role", "sub")
        a = QLabel(f'<a href="{V.CREDIT_URL}" style="color:#7FB0FF;'
                   f'text-decoration:none;">{V.CREDIT_TITLE}</a>')
        a.setOpenExternalLinks(True)
        a.setTextInteractionFlags(Qt.TextBrowserInteraction)
        a.setCursor(Qt.PointingHandCursor)
        a.setToolTip(V.CREDIT_URL)
        link_row.addWidget(who, 0)
        link_row.addWidget(a, 1)
        outer.addLayout(link_row)

        # ---- 底部：打开链接 + 关闭
        bottom = QHBoxLayout()
        tip = QLabel(V.CREDIT_URL)
        tip.setProperty("role", "sub")
        bottom.addWidget(tip, 1)
        open_btn = QPushButton("打开友链")
        open_btn.setFixedWidth(96)
        open_btn.clicked.connect(self._open_link)
        close = QPushButton("关闭")
        close.setFixedWidth(84)
        close.clicked.connect(self.accept)
        bottom.addWidget(open_btn, 0)
        bottom.addWidget(close, 0)
        outer.addLayout(bottom)

    def _open_link(self):
        QDesktopServices.openUrl(QUrl(V.CREDIT_URL))
