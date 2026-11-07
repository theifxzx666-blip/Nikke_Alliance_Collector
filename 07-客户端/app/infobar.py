# -*- coding: utf-8 -*-
"""折叠式信息窗（v1.6）：一行看联通状态，点开看详情与两个尺寸动作。

为什么改：原来的状态卡占三行、常驻在最上面，而 90% 的时间用户只需要知道
"连上没连上"。折成一行后，名单/伤害两页都多出两行可视高度。
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from . import theme as T


class _Clickable(QWidget):
    """整条可点（折叠条）。QLabel 不接鼠标事件，所以包一层。"""

    clicked = Signal()

    def mousePressEvent(self, e):        # noqa: N802
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class InfoBar(QFrame):
    """折叠条 + 展开详情。属性名与旧状态卡保持一致（dot/who/who_sub/badge）。"""

    fix_clicked = Signal()               # 修正为 16:9
    suggest_clicked = Signal()           # 设为建议尺寸

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self.setFrameShape(QFrame.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(T.PAD_CARD, 6, T.PAD_CARD, 6)
        outer.setSpacing(6)

        # ---- 折叠条：● 已连接 1756×988 [16:9 ✓] ⌄
        self.head = _Clickable(self)
        h = QHBoxLayout(self.head)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 12px;")
        self.who = QLabel("游戏未启动")
        self.who.setStyleSheet(f"color: {T.TEXT}; font-size: {T.SZ_BODY}px;")
        self.who.setMinimumWidth(40)
        self.who.setTextInteractionFlags(Qt.NoTextInteraction)
        self.badge = QLabel("")
        self.badge.setStyleSheet(
            f"color: {T.WARN_TEXT}; background: rgba(224,160,48,36);"
            f"border: 1px solid rgba(224,160,48,77); border-radius: 5px;"
            f"padding: 2px 7px; font-size: {T.SZ_SUB}px;")
        self.badge.setVisible(False)          # 未连接时不出徽标，别和标题重复
        self.arrow = QLabel("⌄")
        self.arrow.setStyleSheet(f"color: {T.TEXT_DIM}; font-size: 13px;")
        self.arrow.setFixedWidth(14)
        h.addWidget(self.dot, 0)
        h.addWidget(self.who, 1)
        h.addWidget(self.badge, 0, Qt.AlignVCenter)
        h.addWidget(self.arrow, 0)
        self.head.clicked.connect(self.toggle)
        outer.addWidget(self.head)

        # ---- 展开区
        self.detail = QWidget(self)
        d = QVBoxLayout(self.detail)
        d.setContentsMargins(0, 2, 0, 0)
        d.setSpacing(6)

        # 键名由 Shell 注入（v1.20）：控件不自己去读设置文件，避免又一处
        # "写死 F2" 的旧文案在改键后变成错指引
        self.attach_key = "F7"
        self.who_sub = QLabel(f"启动游戏后按 {self.attach_key} 重新连接")
        self.who_sub.setProperty("role", "sub")
        self.who_sub.setWordWrap(True)
        d.addWidget(self.who_sub)

        self.line_region = QLabel("区域：未标定")
        self.line_region.setProperty("role", "sub")
        d.addWidget(self.line_region)

        self.line_book = QLabel("成员库：—")
        self.line_book.setProperty("role", "sub")
        d.addWidget(self.line_book)

        self.line_size = QLabel("建议尺寸：—")
        self.line_size.setProperty("role", "sub")
        d.addWidget(self.line_size)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_fix = QPushButton("修正为 16:9")
        self.btn_fix.setToolTip("把游戏客户区调成 16:9（只缩不放，保留左上角）")
        self.btn_fix.setProperty("role", "sub")
        self.btn_fix.clicked.connect(self.fix_clicked)
        self.btn_suggest = QPushButton("并排并适配尺寸")
        self.btn_suggest.setToolTip(
            "按「屏幕宽 − 客户端宽 − 间距」把游戏缩到 16:9 建议尺寸，再并排（与 F9 同一条路）")
        self.btn_suggest.setProperty("role", "sub")
        self.btn_suggest.clicked.connect(self.suggest_clicked)
        for b in (self.btn_fix, self.btn_suggest):
            row.addWidget(b)
        row.addStretch(1)
        d.addLayout(row)

        self.detail.setVisible(False)
        outer.addWidget(self.detail)

    # ---------------- 折叠 / 展开
    def toggle(self):
        # 用 isHidden 而不是 isVisible：窗口还没 show 时 isVisible 恒 False，
        # 会让"点第二下"又变成展开（apptest 抓到的）
        show = self.detail.isHidden()
        self.detail.setVisible(show)
        self.arrow.setText("⌃" if show else "⌄")

    def set_expanded(self, on: bool):
        self.detail.setVisible(bool(on))
        self.arrow.setText("⌃" if on else "⌄")

    # ---------------- 内容
    def set_region(self, text: str):
        self.line_region.setText(f"区域：{text}")

    def set_book(self, text: str):
        self.line_book.setText(f"成员库：{text}")

    # ---------------- 通联三态（v1.9）
    def set_link(self, state: str, title: str = "", sub: str = "", badge: str = ""):
        """gone / back / ok —— 颜色与文案只在这里定，别处不许自己拼字符串。

        为什么收成一个入口：之前 attach 成功写一套、失败写另一套，窗口关掉后
        没人再更新（一直显示"已连接"）。状态机在 Shell._refresh_link 里，
        这里只负责画。
        """
        if state == "ok":
            color = T.OK
            self.who.setText(title or "已连接")
            self.who_sub.setText(sub or "")
            self.badge.setVisible(bool(badge))
            if badge:
                good = badge.startswith("16:9")
                fg = T.OK_TEXT if good else T.WARN_TEXT
                bg = "rgba(69,176,118,36)" if good else "rgba(224,160,48,36)"
                bd = "rgba(69,176,118,77)" if good else "rgba(224,160,48,77)"
                self.badge.setText(badge)
                self.badge.setStyleSheet(
                    f"color: {fg}; background: {bg}; border: 1px solid {bd};"
                    f"border-radius: 5px; padding: 2px 7px; font-size: {T.SZ_SUB}px;")
        elif state == "back":
            color = T.WARN
            self.who.setText("游戏窗口不在前台")
            self.who_sub.setText(f"点一下游戏窗口即可恢复；或按 {self.attach_key} 重新连接")
            self.badge.setVisible(False)
        else:
            color = T.ERR
            self.who.setText("游戏未启动")
            self.who_sub.setText(f"启动游戏后按 {self.attach_key} 重新连接")
            self.badge.setVisible(False)
        self.dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.state = state

    def set_suggest(self, size):
        self.line_size.setText("建议尺寸：" + (f"{size[0]}×{size[1]}" if size else "屏幕放不下"))
