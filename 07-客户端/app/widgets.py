# -*- coding: utf-8 -*-
"""通用控件 + 布局体检。

控件只有这几个，页面里不再裸造按钮/卡片 —— 保证全客户端长得一样。
"""
from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer,
                            Signal)
from PySide6.QtGui import (QFontMetrics, QPainter, QPainterPath,
                           QPixmap)
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from . import icons as IC
from . import theme as T


class Card(QFrame):
    """圆角卡片。strong=True 用于主操作区（底色更亮一档）。"""

    def __init__(self, strong=False, parent=None):
        super().__init__(parent)
        self.setObjectName("cardStrong" if strong else "card")
        self.setFrameShape(QFrame.NoFrame)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(T.PAD_CARD_STRONG if strong else T.PAD_CARD,
                               T.PAD_CARD, T.PAD_CARD_STRONG if strong else T.PAD_CARD,
                               T.PAD_CARD)
        lay.setSpacing(8)
        self.body = lay


class ElideLabel(QLabel):
    """按宽度省略、并自动带 ToolTip 的标签。

    这是"文本显示不全"的第一道防线：宁可显示省略号 + 悬停看全文，
    也不要让文字被硬生生裁掉半截。
    """

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full = text
        super().setText(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def setText(self, text):
        self._full = text
        self.setToolTip(text)
        self._apply()

    def fullText(self):
        return self._full

    def sizeHint(self):                      # noqa: N802
        """按完整文本算宽度。

        坑：QLabel.sizeHint() 用的是**当前显示的**文本，而我们显示的是省略后的，
        于是「省略 → sizeHint 变小 → 布局给更窄 → 更省略」形成死循环，
        标题只剩"联盟采集·嗷…"。必须回到 _full 来量。
        """
        h = super().sizeHint().height()
        return QSize(self.fontMetrics().horizontalAdvance(self._full), h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()

    def _apply(self):
        if not self._full:
            super().setText("")
            return
        fm = QFontMetrics(self.font())
        # 不扣那 2px：sizeHint 按完整文本算，扣了就永远差两个字符，
        # 明明放得下也显示省略号（标题"…1E-6 …"就是这么来的）。
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(10, self.width())))


class LinkLabel(QLabel):
    """看起来是文字、点了有动作的标签（用于「回到最新」这类低频操作）。

    为什么不用 QPushButton：按钮有边框和最小高度，夹在进度条右边会把行撑高。
    """
    clicked = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


class IconButton(QPushButton):
    """顶栏小圆按钮。给 `icon=` 就用 SVG 图标，否则退回文字字形（兼容旧调用）。"""

    def __init__(self, glyph="", tip="", icon=None, parent=None):
        super().__init__(glyph, parent)
        self.setObjectName("icon")
        if icon:
            self.setIcon(IC.icon(icon, 18))
            self.setIconSize(QSize(18, 18))
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        # checkable 才能用到 QIcon 的 On 变体（图标自己变高亮色），
        # 否则只能靠 QSS 改背景，图标还是灰的 —— "已开启"看起来像"没生效"。
        self.setCheckable(True)

    def setOn(self, on: bool):
        on = bool(on)
        if self.isChecked() != on:
            self.setChecked(on)
        self.setProperty("on", "1" if on else "0")   # 兼容旧 QSS 规则
        self.style().unpolish(self)
        self.style().polish(self)


class Segmented(QFrame):
    """两段式切换（名单 / 伤害）。"""

    changed = Signal(int)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.setObjectName("seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(3)
        self._btns = []
        for i, name in enumerate(items):
            b = QPushButton(name)
            b.setObjectName("segItem")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, idx=i: self.select(idx))
            lay.addWidget(b, 1)
            self._btns.append(b)
        self._cur = -1
        self.select(0)

    def select(self, idx):
        if idx == self._cur:
            return
        self._cur = idx
        for i, b in enumerate(self._btns):
            b.setProperty("on", "1" if i == idx else "0")
            b.style().unpolish(b)
            b.style().polish(b)
        self.changed.emit(idx)

    def current(self):
        return self._cur


class LogPane(QPlainTextEdit):
    """只读日志：跟随最新，但用户上翻时不自作主张滚回底部。"""

    def __init__(self, lines=3, parent=None):
        super().__init__(parent)
        self.setObjectName("log")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMaximumBlockCount(500)
        self.document().setDocumentMargin(0)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        fm = self.fontMetrics()
        self.setFixedHeight(fm.lineSpacing() * lines + 24)
        self._follow = True
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _on_scroll(self, v):
        sb = self.verticalScrollBar()
        self._follow = v >= sb.maximum() - 2

    def add(self, text):
        self.appendPlainText(text)
        if self._follow:
            sb = self.verticalScrollBar()
            sb.setValue(sb.maximum())


def _name(w: QWidget) -> str:
    t = w.text() if hasattr(w, "text") else ""
    t = (t or "").replace("\n", " ")[:14]
    cls = w.__class__.__name__
    return f"{cls}({t})" if t else cls


# 按钮 QSS 内边距（左右合计，含边框）——体检测文字有没有被裁时要用
_PAD_BY_NAME = {"icon": 0, "sponsorLink": 18, "betaTag": 16}


def audit_layout(root: QWidget, padding_guess=None):
    """启动时全量体检：找出"宽度不够、文字被裁"的控件。

    为什么要有这个：GM 客户端出过"组件显示不全 / 文本显示不全"，靠人眼检查不可靠。
    这里主动对比 sizeHint 与实际宽度，把问题写进日志，而不是等用户截图来报。
    """
    issues = []
    for w in root.findChildren(QWidget):
        if not w.isVisible() or w.property("noaudit"):
            continue
        try:
            need = w.minimumSizeHint().width()
            if need > 0 and w.width() < need - 1:
                issues.append(f"{_name(w)} 宽 {w.width()} < 需要 {need}")
                continue
            if isinstance(w, (QLabel, QPushButton)) and hasattr(w, "text") and w.text():
                if isinstance(w, QLabel) and w.wordWrap():
                    continue
                # 内边距按控件类型估：标签没有内边距；图标按钮 QSS 里 padding:0；
                # 普通按钮左右各 12。估错会把"刚好放下"误报成裁切。
                if isinstance(w, QPushButton):
                    # 每种按钮的 QSS 内边距不同，估值必须跟着，否则"刚好放下"会误报
                    pad = _PAD_BY_NAME.get(w.objectName(), 24)
                else:
                    pad = 0
                avail = w.contentsRect().width() - pad
                want = w.fontMetrics().horizontalAdvance(w.text())
                if want > avail + 2:
                    issues.append(f"{_name(w)} 文本 {want}px > 可用 {avail}px")
        except Exception:
            continue
    return issues

class Toast(QLabel):
    """轻量提示条（v1.6）：贴在窗口底部居中，2.4 s 自己消失。

    为什么不用 QMessageBox：库覆盖提示属于"说一句就够"的信息，模态框会打断
    用户点「重新采集」的节奏；这里也刻意做成客户端自己的深色圆角样式，
    不和系统对话框混在一起。字号取 SZ_BODY（13px）保证在 100% 缩放下也清楚。
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setVisible(False)
        self.setWordWrap(True)
        self.setTextFormat(Qt.PlainText)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background: rgba(24, 27, 33, 242); color: {T.TEXT};"
            f"border: 1px solid rgba(255,255,255,28); border-radius: 10px;"
            f"padding: 9px 14px; font-size: {T.SZ_BODY + 1}px;")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_text(self, text: str, msec: int = 2400):
        self.setText(text)
        self.adjustSize()
        par = self.parentWidget()
        if par is not None:
            w = min(max(200, self.width()), max(200, par.width() - 32))
            self.setFixedWidth(w)
            self.adjustSize()
            x = (par.width() - self.width()) // 2
            y = par.height() - self.height() - 18
            self.move(max(8, x), max(8, y))
        self.raise_()
        self.setVisible(True)
        self._timer.start(msec)

class Section(QFrame):
    """可折叠的设置分组（v1.19）。

    为什么需要它：设置项一多，一路平铺就分不清"哪几行是一类的"，用户要从头
    扫到尾才能找到某一项；而快捷键这种装好就不动的项又占了近一半高度。
    分组卡片 + 折叠把「分类」和「层级」都画出来了。

    结构：圆角卡片里装两行 —— 标题条（标题 + 摘要 + 箭头）与内容区。
    点标题条任意位置切换；展开/收起用 maximumHeight 动画，不是直接 show/hide
    （直接切换会让下面的内容"跳一下"）。
    """

    toggled = Signal(bool)

    def __init__(self, title: str, sub: str = "", expanded: bool = True,
                 key: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("section")
        self._key = key
        self._expanded = bool(expanded)
        self._anim = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---- 标题条（整条可点）
        self.head = QWidget()
        self.head.setObjectName("secHead")
        self.head.setAttribute(Qt.WA_StyledBackground, True)   # QSS 背景对 QWidget 要开这个
        self.head.setAttribute(Qt.WA_Hover, True)              # :hover 同理
        self.head.setCursor(Qt.PointingHandCursor)
        self.head.setFixedHeight(34)
        hl = QHBoxLayout(self.head)
        hl.setContentsMargins(12, 0, 10, 0)
        hl.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("secTitle")
        hl.addWidget(self.title, 0, Qt.AlignVCenter)
        hl.addStretch(1)
        self.summary = QLabel(sub)
        self.summary.setObjectName("secSub")
        hl.addWidget(self.summary, 0, Qt.AlignVCenter)
        self.arrow = QLabel()
        self.arrow.setFixedSize(14, 14)
        self.arrow.setPixmap(IC.pixmap("chev_down", T.TEXT_DIM, 14))
        hl.addWidget(self.arrow, 0, Qt.AlignVCenter)
        outer.addWidget(self.head)

        # ---- 内容区
        self.panel = QWidget()
        self.panel.setObjectName("secBody")
        self.lay = QVBoxLayout(self.panel)
        self.lay.setContentsMargins(12, 0, 12, 12)
        self.lay.setSpacing(10)
        outer.addWidget(self.panel)

        self.head.mousePressEvent = self._on_head_click
        self._apply(animate=False)

    # ---------------- 交互
    def _on_head_click(self, e):
        if e.button() == Qt.LeftButton:
            self.set_expanded(not self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool, animate: bool = True):
        on = bool(on)
        if on == self._expanded and animate:
            return
        self._expanded = on
        self._apply(animate=animate)
        self.toggled.emit(on)

    def set_summary(self, text: str):
        self.summary.setText(text or "")
        self.summary.setVisible(bool(text))

    def _body_height(self) -> int:
        """内容区的目标高度：用 layout 的 sizeHint，未 show 时也拿得到。"""
        h = self.panel.layout().sizeHint().height()
        return max(0, int(h))

    def _apply(self, animate: bool = True):
        self.arrow.setPixmap(IC.pixmap("chev_down" if self._expanded else "chev_right",
                                       T.TEXT_DIM, 14))
        self.panel.setVisible(True)
        target = self._body_height() if self._expanded else 0
        if not animate:
            self.panel.setMaximumHeight(16777215 if self._expanded else 0)
            self.panel.setVisible(self._expanded)
            return
        if self._anim is not None:
            self._anim.stop()
        self.panel.setMaximumHeight(self.panel.height() if self._expanded else target)
        anim = QPropertyAnimation(self.panel, b"maximumHeight", self)
        anim.setDuration(120)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(0 if self._expanded else self._body_height())
        anim.setEndValue(target)
        if self._expanded:
            # 展开收尾要放开上限，否则窗口拉大时内容被截在动画终点高度
            anim.finished.connect(lambda: self.panel.setMaximumHeight(16777215))
        else:
            anim.finished.connect(lambda: self.panel.setVisible(False))
        anim.start()
        self._anim = anim


class FitComboBox(QComboBox):
    """弹出列表按最长项自适应宽度，并把 popup 夹回宿主窗口内（v1.21）。

    为什么需要它：
      1. QComboBox 默认弹窗宽度 = 控件宽度，长文本（BOSS 名一长串）在弹窗里
         照样被裁，用户还是看不全 —— "选的时候能看清"这件事得自己实现；
      2. popup 是顶层窗口，Qt 只保证不超出**屏幕**，不保证不超出我们这个小客户端：
         贴边并排时弹出列表会飘到游戏窗口上面。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup_w = 0

    def showPopup(self):                       # noqa: N802（Qt 命名）
        self._fit_width()
        super().showPopup()
        # popup 在 show 之后才真正 map 出来，singleShot(0) 抓到的还是旧 geometry
        # （实测那时 setGeometry 会被随后的首帧布局覆盖），所以晚一点再夹。
        QTimer.singleShot(16, self._clamp_to_host)

    def _fit_width(self) -> int:
        fm = self.fontMetrics()
        widest = 0
        for i in range(self.count()):
            widest = max(widest, fm.horizontalAdvance(self.itemText(i)))
        widest += 56                           # 左右内边距 + 滚动条余量
        host = self.window()
        cap = (host.width() - 16) if host is not None else widest
        w = max(self.width(), min(widest, max(120, cap)))
        self._popup_w = w
        if self.view() is not None:
            self.view().setMinimumWidth(w)
        return w

    def _clamp_to_host(self):
        view = self.view()
        win = view.window() if view is not None else None
        host = self.window()
        if win is None or host is None or win is host:
            return
        hg = host.rect()
        w = min(self._popup_w, hg.width() - 16)
        h = min(win.height(), hg.height() - 16)
        origin = host.mapToGlobal(hg.topLeft())
        # 横向：优先与控件左缘对齐，越界就左移，保证整块落在客户端里
        pos = self.mapTo(host, self.rect().topLeft())
        x = origin.x() + max(8, min(pos.x(), hg.width() - w - 8))
        # 纵向：优先在控件下方；下方不够就翻到上方；再不行就顶到可用区
        y = win.y()
        bottom = origin.y() + hg.height() - 8
        if y + h > bottom:
            above = self.mapToGlobal(self.rect().topLeft()).y() - h - 4
            y = max(origin.y() + 8, min(above, bottom - h))
        win.setGeometry(x, y, w, h)


def round_pixmap(pm, radius: int = 10):
    """把任意尺寸的 QPixmap 裁成圆角（不改尺寸）。

    QLabel 上的 `border-radius` 只裁背景，裁不到 pixmap 本身——所以要在
    QPixmap 上开 PainterPath 裁一次。收款码卡片、标题栏头像都走这里。
    """
    if pm.isNull():
        return pm
    w, h = pm.width(), pm.height()
    r = max(0, min(int(radius), min(w, h) // 2))
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path_ = QPainterPath()
    path_.addRoundedRect(0, 0, w, h, r, r)
    p.setClipPath(path_)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def rounded_pixmap(path, size: int = 20, radius: int = 5):
    """正方形圆角小图（标题栏头像 / 图标用）。找不到文件返回空 QPixmap。"""
    pm = QPixmap(str(path))
    if pm.isNull():
        return pm
    pm = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    if pm.width() > size or pm.height() > size:
        x = max(0, (pm.width() - size) // 2)
        y = max(0, (pm.height() - size) // 2)
        pm = pm.copy(x, y, size, size)
    return round_pixmap(pm, radius)

