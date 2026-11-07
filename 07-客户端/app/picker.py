# -*- coding: utf-8 -*-
"""框选覆盖层：全屏半透明，拖一个框出来。

为什么不用旧的 Tk 版 RegionPicker：客户端是 Qt，混用两套 UI 会出现
「焦点被另一个工具包抢走」这种怪问题。这里只画一个框，逻辑照样简单。
"""
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget


class RegionPicker(QWidget):
    picked = Signal(QRect)          # 屏幕绝对坐标

    def __init__(self, screen=None):
        super().__init__()
        scr = screen or QGuiApplication.primaryScreen()
        self._geo = scr.geometry()
        self._bg = scr.grabWindow(0)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setGeometry(self._geo)
        self.setCursor(Qt.CrossCursor)
        self._start = None
        self._cur = None

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)
        p.fillRect(self.rect(), QColor(8, 10, 14, 150))

        tip = "拖动框住「卡片列表」：第一张卡上沿 → 最后一张下沿 · 顶部日期栏/滚动条不要框进来 · Esc 取消"
        p.setPen(QColor(255, 224, 102))
        p.drawText(24, 30, tip)

        if self._start and self._cur:
            r = QRect(self._start, self._cur).normalized()
            p.setPen(QPen(QColor(47, 111, 208), 2))
            p.setBrush(QColor(47, 111, 208, 40))
            p.drawRect(r)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r.left() + 6, max(14, r.top() - 6), f"{r.width()} × {r.height()}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._start = e.position().toPoint()
            self._cur = self._start
            self.update()

    def mouseMoveEvent(self, e):
        if self._start is not None:
            self._cur = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._start is None:
            return
        r = QRect(self._start, e.position().toPoint()).normalized()
        self.close()
        if r.width() < 80 or r.height() < 80:
            return                       # 太小当误触，不返回结果
        r.translate(self._geo.topLeft())
        self.picked.emit(r)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
