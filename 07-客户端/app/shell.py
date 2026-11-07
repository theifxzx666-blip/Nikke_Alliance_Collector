# -*- coding: utf-8 -*-
"""客户端壳层：无边框窗口 + 毛玻璃底 + 标题栏 + 两条产线的页面骨架。

v1.1（用户真机实测 7 条反馈）改了这些：
  · 窗口能自己拖大拖小（8 方向边缘热区，尺寸记进设置）
  · **页签决定流程**：伤害页才走 HARD；名单页诚实占位并负责拉起旧名单工具
  · F 区快捷键走 `RegisterHotKey`，焦点在游戏里也能按
  · 表格列宽按字体实测、表头与内容共用一张对齐表
  · 「对齐」= **并排不重叠**（空间不够先缩游戏），并跟随游戏窗口
"""
import csv
import ctypes
import json
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRect, Qt, QTimer
from PySide6.QtGui import (QColor, QGuiApplication, QIcon, QImage, QPainter,
                           QPainterPath, QPixmap)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QStackedLayout,
    QStyleOptionViewItem, QTableView,
    QVBoxLayout, QWidget,
)

import alliance_ocr as A
import booklib as BK

from . import icons as IC
from . import paths as P
from . import keys as KEYS
from . import store as S
from . import theme as T
from . import version as V
from . import win as W
from .about import AboutDialog
from .bookdlg import BookDialog
from .infobar import InfoBar
from .sponsor import SponsorDialog
from .engine import Engine
from .escwatch import EscWatch
from .hotkey import Hotkeys
from .picker import RegionPicker
from .roster_engine import RosterEngine
from .widgets import (Card, ElideLabel, FitComboBox, IconButton, LinkLabel,
                      LogPane, Section, Segmented, Toast, audit_layout,
                      rounded_pixmap)

try:                                   # 后台抓图只为毛玻璃底，失败就退回纯色
    from PIL import Image, ImageFilter, ImageGrab
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# 旧设置里"游戏右侧"这类写法保留兼容：改名的只是文案，别让老配置失效
_SIDE_ALIAS = {"游戏右侧": "右侧", "游戏左侧": "左侧", "覆盖右下": "自动"}


def _side(name) -> str:
    name = str(name or "右侧")
    name = _SIDE_ALIAS.get(name, name)
    return name if name in ("右侧", "左侧", "自动", "不动") else "右侧"


class TableModel(QAbstractTableModel):
    HEADERS = ("#", "成员", "BOSS", "伤害", "剩余", "置信")
    KEYS = ("idx", "member", "boss", "damage", "pct", "conf")
    # 表头与内容**共用这一张对齐表**。以前表头走 QSS 默认、内容走 data()，
    # 两边各排各的，于是"表头跟内容列不在一条竖线上"。现在只有一个来源。
    ALIGN = Qt.AlignLeft | Qt.AlignVCenter
    # 列宽下限：表头 + 已知最长样本（真实数据实测，不按平均估）
    MIN_W = (44, 92, 76, 116, 60, 48)
    # 列宽上限：不设上限的话，一条读歪的 17 位"伤害"就能把表格撑出横向滚动条。
    # 前五列上限合计 436 < 462（496 窗口的视口宽），常用尺寸下不出现横向滚动。
    CAP_W = (44, 108, 88, 130, 66, 56)

    # 名单形态（v1.2 场景A并入）：列同旧工具 CSV（序号/昵称/置信度）。
    # 昵称列给宽下限——「星空下的神话」这类 6 字名要完整露出。
    ROSTER_HEADERS = ("#", "昵称", "置信")
    ROSTER_KEYS = ("idx", "member", "conf")
    ROSTER_MIN_W = (44, 240, 56)
    ROSTER_CAP_W = (44, 999, 120)

    def __init__(self):
        super().__init__()
        self.rows = []
        # v1.7 新增行淡入：从 flash_from 起的行铺一层淡蓝，alpha 由 shell 逐帧降。
        # 用户原话"一次录入多条时是闪现出来的"——亮一下再沉下去，眼睛跟得住。
        self.flash_from = -1
        self.flash_alpha = 0.0
        # 页签切列靠实例属性遮蔽类属性（data()/headerData 读的都是 self.X）。
        # 默认伤害形态；_on_mode 里 set_mode 切换。
        self.HEADERS = TableModel.HEADERS
        self.KEYS = TableModel.KEYS
        self.MIN_W = TableModel.MIN_W
        self.CAP_W = TableModel.CAP_W

    def set_mode(self, roster: bool):
        """切表格列形态：名单 3 列 / 伤害 6 列。"""
        self.HEADERS = self.ROSTER_HEADERS if roster else TableModel.HEADERS
        self.KEYS = self.ROSTER_KEYS if roster else TableModel.KEYS
        self.MIN_W = self.ROSTER_MIN_W if roster else TableModel.MIN_W
        self.CAP_W = self.ROSTER_CAP_W if roster else TableModel.CAP_W
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, sec, orient, role=Qt.DisplayRole):
        if orient != Qt.Horizontal:
            return None
        if role == Qt.DisplayRole:
            return self.HEADERS[sec]
        if role == Qt.TextAlignmentRole:
            return self.ALIGN
        return None

    def begin_flash(self, start_row: int):
        self.flash_from = max(0, int(start_row))
        self.flash_alpha = 1.0

    def set_flash_alpha(self, a: float):
        self.flash_alpha = max(0.0, float(a))
        if self.flash_from >= 0 and self.rows:
            self.dataChanged.emit(self.index(self.flash_from, 0),
                                  self.index(len(self.rows) - 1, len(self.HEADERS) - 1))

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        if role == Qt.TextAlignmentRole:
            return self.ALIGN
        if (role == Qt.BackgroundRole and self.flash_from >= 0
                and idx.row() >= self.flash_from and self.flash_alpha > 0.01):
            return QColor(70, 150, 255, int(86 * self.flash_alpha))
        if role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        v = self.rows[idx.row()].get(self.KEYS[idx.column()], "")
        return "" if v is None else str(v)

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()


class Shell(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = S.load_settings()
        # ---- 状态字段先建好：_build() 里会立刻回调 _on_mode / _set_running ----
        self._pin = bool(self.cfg.get("pin", False))   # v1.9：默认不置顶
        self._keys = KEYS.load(self.cfg)   # 可配置快捷键（v1.12）
        self._follow_win = bool(self.cfg.get("follow_win", True))   # 窗口跟随（v1.14 开关）
        # 自定义背景（v1.15）：空 = 抓桌面做毛玻璃；文件没了就静默回落
        self._bg_path = str(self.cfg.get("bg_image") or "")
        if self._bg_path and not Path(self._bg_path).exists():
            self._bg_path = ""
        if not self._bg_path and "bg_image" not in self.cfg:
            # 出厂带一张背景：首次启动就有画面。用户点「默认」会写入空串，
            # 之后就不再回这张图（"默认"的语义必须稳定 = 毛玻璃）。
            _builtin = P.ASSETS / P.BUILTIN_BG
            if _builtin.exists():
                self._bg_path = str(_builtin)
        self._mode = 1
        self._running = False
        self._rows = []              # 当前页签视图对应的行（导出/按钮复用）
        self._rows_hard = []         # 伤害采集的行（真源）
        self._rows_roster = []       # 名单库视图的行（真源）
        self._follow = bool(self.cfg.get("follow", True))
        self._seen = 0
        self._restoring = False
        self._auto_fixed = False
        self._attach_tries = 0
        self._last_export = None
        self._bg = None
        self._drag_from = None
        self._resizing = None            # (边, 起始全局点, 起始几何)
        self._col_w = list(TableModel.MIN_W)
        self._game_rect = None           # 上次看到的游戏客户区（物理），跟随用
        self._last_align_warn = None     # 上一条并排失败提示（节流用）
        self._bridge = None              # 名单引擎（懒建：首次进名单页采集才预热模型）
        self._flash_timer = QTimer(self)  # 新增行高亮的淡出计时
        self._flash_timer.setInterval(16)
        self._flash_timer.timeout.connect(self._flash_step)
        self._flash_alpha = 0.0
        self._hot = Hotkeys()
        # 全局热键的作用域（v1.24）：只有游戏或本工具在前台时才注册，
        # 否则 RegisterHotKey 会独占按键，在浏览器里按 F3/F4 也会触发采集。
        self._hot_scope = None
        self._scope_timer = QTimer(self)
        self._scope_timer.setInterval(300)
        self._scope_timer.timeout.connect(self._refresh_hot_scope)

        self.setWindowTitle("联盟采集 · 嗷润吉-1E-6 制作")
        _ico = Path(__file__).resolve().parent.parent / "assets" / "aorunji.ico"
        if _ico.exists():
            self.setWindowIcon(QIcon(str(_ico)))
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint)
        if self._pin:                    # 置顶要在窗口显示前设好，后设会闪一下
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        # 窗口自己拉伸：不再 setFixedWidth。上下限防拖出屏幕 / 拖成一条缝
        self.setMinimumSize(T.WINDOW_MIN_W, T.WINDOW_MIN_H)
        self.setMaximumSize(T.WINDOW_MAX_W, T.WINDOW_MAX_H)
        w = int(self.cfg.get("win_w") or T.WINDOW_W)
        h = int(self.cfg.get("win_h") or T.WINDOW_H)
        self.resize(max(T.WINDOW_MIN_W, min(w, T.WINDOW_MAX_W)),
                    max(T.WINDOW_MIN_H, min(h, T.WINDOW_MAX_H)))

        self._build()
        self._shortcuts()

        self.engine = Engine()
        self.engine.rect_rel = self._load_rect()
        self.engine.logged.connect(self.say)
        self.engine.rowsChanged.connect(self._on_rows)
        self.engine.finished.connect(self._on_done)
        self.engine.attached.connect(self._on_attached)

        # 跟随游戏窗口：游戏动 → 客户端跟。只移动客户端，不抢焦点
        self._watch = QTimer(self)
        self._watch.setInterval(600)
        self._watch.timeout.connect(self._watch_game)
        # place=False：窗口还没接上，这时 align 只会打印"先按 F2"
        QTimer.singleShot(0, lambda: self._set_follow(self._follow_win, place=False))

        # 通联三态：定时判一次（窗口关掉 / 最小化 / 切走都能立刻反映到界面）
        self._link = QTimer(self)
        self._link.setInterval(1200)
        self._link.timeout.connect(self._refresh_link)
        self._link.start()
        self._refresh_link()              # 先画一次，别让头 1.2 秒停在默认灰点上

        # 运行期才启用的全局 Esc（被动读键，不劫持系统 Esc）
        self._esc = EscWatch(self)
        self._esc.pressed.connect(self._on_esc_pressed)

        self._reload_books()              # 多套库：下拉填好，活动项选中
        QTimer.singleShot(400, self._auto_attach)

    # ---------------- 背景 ----------------
    def prepare_background(self):
        """显示**之前**抓一次窗口背后的桌面，缩到 1/8 做高斯 —— Win10 也能用。

        只在 show 之前调。窗口已经显示后再抓会把自己拍进去。
        拉伸时不再重抓，直接按新矩形拉伸这张模糊底图（本来就糊，看不出）。
        """
        if self._bg_path:                 # 有自定义背景：直接用它，别抓桌面
            self._rebuild_bg()
            return
        if not _HAS_PIL:
            return
        dpr = self.devicePixelRatioF()
        g = self.geometry()
        box = (int(g.x() * dpr), int(g.y() * dpr),
               int((g.x() + self.width()) * dpr), int((g.y() + self.height()) * dpr))
        try:
            img = ImageGrab.grab(bbox=box).convert("RGB")
            small = img.resize((max(1, img.width // 8), max(1, img.height // 8)), Image.BILINEAR)
            small = small.filter(ImageFilter.GaussianBlur(2.4))
            img = small.resize((self.width(), self.height()), Image.BICUBIC)
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
            self._bg = QPixmap.fromImage(qimg.copy())
        except Exception:
            self._bg = None

    # ---------------- 自定义背景（v1.15）
    def _bg_title(self) -> str:
        return Path(self._bg_path).name if self._bg_path else "毛玻璃"

    def _bg_row(self):
        """设置里那一行：状态 + 更换 + 默认。"""
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.bg_name = ElideLabel(self._bg_title())
        self.bg_name.setProperty("role", "sub")
        self.bg_name.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.bg_name.setMaximumWidth(176)   # 104 只够放「嗷润吉.jpg」，长文件名全被省略
        # ElideLabel 默认是 Ignored 策略（专给"吃剩余空间"的行内提示用），
        # 放在右对齐的位置上只会分到 minimumWidth(40) → 显示成 "aoru…"。这里要
        # Preferred：按完整文件名量宽度，超长才由 maxWidth + 省略号收尾。
        self.bg_name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.bg_name.setMinimumWidth(60)
        self.bg_name.setToolTip("当前背景")
        pick = QPushButton("更换")
        pick.setFixedWidth(54)
        pick.setToolTip("选一张图片作为背景（立即生效）")
        pick.clicked.connect(self._pick_bg_image)
        reset = QPushButton("默认")
        reset.setFixedWidth(54)
        reset.setToolTip("回到毛玻璃（抓取窗口背后的桌面做模糊）")
        reset.clicked.connect(self.clear_bg_image)
        h.addWidget(self.bg_name, 0, Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(pick, 0)
        h.addWidget(reset, 0)
        return box

    def _sync_bg_row(self):
        if hasattr(self, "bg_name"):
            self.bg_name.setText(self._bg_title())
            # 缩略宽度下必然显示省略号，全名与目录放 tooltip 里才对得住用户
            self.bg_name.setToolTip(self._bg_path or "默认：抓取窗口背后的桌面做模糊")

    def _pick_bg_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", str(Path.home() / "Pictures"),
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if path:
            self.set_bg_image(path)

    def set_bg_image(self, path):
        """换背景图：立刻生效并记进设置。图坏了就报错，**不覆盖现有背景**。"""
        p = Path(str(path)).resolve()      # 存绝对路径：换工作目录也能找到
        if not p.exists():
            self.say(f"背景图片不存在：{p.name}", "WARN")
            return False
        if QPixmap(str(p)).isNull():
            self.say(f"这张图读不出来（换 png/jpg 试试）：{p.name}", "WARN")
            return False
        self._bg_path = str(p)
        S.save_settings(bg_image=self._bg_path)
        self._rebuild_bg()
        self._sync_bg_row()
        self.update()
        self.say(f"背景已换成：{p.name}")
        return True

    def clear_bg_image(self):
        """恢复默认毛玻璃。"""
        self._bg_path = ""
        S.save_settings(bg_image="")
        self._bg = None
        self._sync_bg_row()
        self.prepare_background()      # 重新抓桌面
        self.update()
        self.say("背景已恢复默认（毛玻璃）")

    def _rebuild_bg_and_repaint(self):
        self._rebuild_bg()
        self.update()

    def _rebuild_bg(self):
        """把自定义图按窗口尺寸做成 cover + 轻模糊 + 压暗。

        为什么不用 paintEvent 的 scrim 一次性压暗：默认毛玻璃配的是 82%，
        套在照片上等于把图糊成一片黑；这里先压到 ~58%，paintEvent 再用更松的
        `BG_SCRIM_CUSTOM`，图能看见、字也读得清。
        """
        if not self._bg_path:
            return
        w, h = max(1, self.width()), max(1, self.height())
        dpr = float(self.devicePixelRatioF() or 1.0)
        tw, th = int(w * dpr), int(h * dpr)
        if _HAS_PIL:
            try:
                img = Image.open(self._bg_path).convert("RGB")
                scale = max(tw / img.width, th / img.height)
                nw = max(tw, int(round(img.width * scale)))
                nh = max(th, int(round(img.height * scale)))
                img = img.resize((nw, nh), Image.LANCZOS)
                x, y = (nw - tw) // 2, (nh - th) // 2
                img = img.crop((x, y, x + tw, y + th))
                img = img.filter(ImageFilter.GaussianBlur(2.4))
                img = Image.eval(img, lambda v: int(v * 0.46))
                data = img.tobytes("raw", "RGB")
                qimg = QImage(data, tw, th, tw * 3, QImage.Format_RGB888)
                pm = QPixmap.fromImage(qimg.copy())
                pm.setDevicePixelRatio(dpr)
                self._bg = pm
                return
            except Exception as e:
                A.log_exc("bg-image", e)
        # 没 PIL / 处理失败：退回直接 cover（scrim 会帮忙压暗）
        src = QPixmap(self._bg_path)
        if src.isNull():
            self._bg = None
            return
        pm = src.scaled(tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (pm.width() - tw) // 2)
        y = max(0, (pm.height() - th) // 2)
        pm = pm.copy(x, y, min(tw, pm.width()), min(th, pm.height()))
        pm.setDevicePixelRatio(dpr)
        self._bg = pm

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), T.RADIUS_WIN, T.RADIUS_WIN)
        p.setClipPath(path)
        if self._bg is not None:
            p.drawPixmap(self.rect(), self._bg)     # 底图跟着窗口缩放
        else:
            p.fillRect(self.rect(), QColor(24, 27, 33))
        # 自定义图在 _rebuild_bg 里已经压过一档，这里用更松的 scrim，否则图看不见
        p.fillRect(self.rect(), QColor(*(T.BG_SCRIM_CUSTOM if self._bg_path
                                        else T.BG_SCRIM)))

    # ---------------- 界面 ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._title_bar())

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(T.GAP)
        lay.addWidget(self._status_card())
        lay.addWidget(self._seg_row())
        lay.addWidget(self._main_card())

        self.stack = QStackedLayout()
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.table_card = self._table_card()
        self.empty = QLabel(f"还没有数据 · 按 {self._k('hard')} 开始采集")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("role", "sub")
        holder = QWidget()
        self.stack.addWidget(self.empty)
        self.stack.addWidget(self.table_card)
        holder.setLayout(self.stack)
        lay.addWidget(holder, 1)

        lay.addLayout(self._progress_row())
        lay.addLayout(self._recent_row())
        self.log = LogPane(3)
        lay.addWidget(self.log)

        # 调试面板：默认不出现，F11 才展开。显示的是"现场快照"，不额外算东西。
        self.debug = QFrame()
        self.debug.setObjectName("debugPanel")
        _dl = QVBoxLayout(self.debug)
        _dl.setContentsMargins(8, 6, 8, 6)
        self.debug_text = QLabel("")
        self.debug_text.setObjectName("debugText")
        self.debug_text.setWordWrap(True)      # 宁可折行，也不让诊断信息被裁掉
        _dl.addWidget(self.debug_text)
        self.debug.setVisible(False)
        lay.addWidget(self.debug)

        self._audit_text = "布局体检：未运行"
        self._debug_timer = QTimer(self)
        self._debug_timer.setInterval(500)
        self._debug_timer.timeout.connect(self._update_debug)

        root.addWidget(body, 1)
        self.drawer = self._drawer(self)
        self.drawer.setVisible(False)

        # 页签 = 流程。启动落回上次那一页，不再硬编码"伤害"
        self.seg.changed.connect(self._on_mode)
        want = 1 if int(self.cfg.get("mode", 1) or 0) else 0
        self.seg.select(want)
        self._on_mode(self.seg.current())

    def _title_bar(self):
        bar = QWidget()
        bar.setFixedHeight(T.H_TITLE)
        bar.setObjectName("titlebar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(6)

        logo = QLabel()
        logo.setFixedSize(18, 18)
        _avatar = rounded_pixmap(Path(__file__).resolve().parent.parent
                                 / "assets" / "aorunji.jpg", 18, 5)
        if not _avatar.isNull():
            logo.setPixmap(_avatar)
            logo.setToolTip("嗷润吉")
        else:                                     # 素材缺失时退回色块，别留空洞
            logo.setStyleSheet(f"background: {T.ACCENT}; border-radius: 4px;")
        lay.addWidget(logo)

        # 用 ElideLabel 而不是 QLabel：标题栏塞进 Beta 徽标后，496 宽下只剩一百多像素，
        # 普通 QLabel 会硬裁成"…1E-"，省略号至少让人知道后面还有字（悬停看全文）。
        # 策略必须是 Preferred：ElideLabel 默认的 Ignored 会让布局根本不给它宽度
        # （实测只剩"联盟"两个字），Ignored 只适合"吃剩余空间"的行内提示。
        name = ElideLabel(V.APP_NAME)
        name.setProperty("role", "title")
        name.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        name.setMinimumWidth(90)
        lay.addWidget(name)

        # 版本徽标：点开关于窗口（版本号/致谢/友链）
        self.beta = QPushButton(V.CHANNEL)
        self.beta.setObjectName("betaTag")
        self.beta.setCursor(Qt.PointingHandCursor)
        self.beta.setFocusPolicy(Qt.NoFocus)
        self.beta.setToolTip(f"版本信息 · {V.full_text()}")
        self.beta.clicked.connect(self.show_about)
        lay.addWidget(self.beta, 0, Qt.AlignVCenter)
        lay.addStretch(1)

        # v1.9：字符字形（⇧⤢⚙）在不同字体下会歪、也认不出，统一换成 SVG 线条图标
        self.btn_pin = IconButton("", f"置顶 {KEYS.LOCAL['pin']}", icon="pin")
        self.btn_pin.setOn(self._pin)
        # v1.14：从"摆一次"改成"窗口跟随开关"（持续性状态，点开高亮）
        self.btn_align = IconButton("", "窗口跟随", icon="split")
        self.btn_set = IconButton("", f"设置 {self._k('settings')}", icon="gear")
        self.btn_min = IconButton("", "最小化", icon="minimize")
        self.btn_close = IconButton("", "关闭", icon="close")
        # 赞助做成胶囊标签（不是纯文字，也不是图标）：一眼能看出是个可点的按钮
        self.btn_sponsor = QPushButton("赞助一下")
        self.btn_sponsor.setObjectName("sponsorLink")
        self.btn_sponsor.setToolTip("赞助开发者")
        self.btn_sponsor.setCursor(Qt.PointingHandCursor)
        self.btn_sponsor.setFocusPolicy(Qt.NoFocus)
        self.btn_sponsor.setMinimumWidth(
            self.btn_sponsor.fontMetrics().horizontalAdvance("赞助一下") + 18)
        # 赞助排在置顶**前面**（用户要求）——它是一次性动作，不该挤在窗口控制键中间
        for b in (self.btn_sponsor, self.btn_pin, self.btn_align, self.btn_set,
                  self.btn_min, self.btn_close):
            lay.addWidget(b, 0, Qt.AlignVCenter)
            if b is self.btn_sponsor:      # 标签和窗口键之间留一口气，分组更清楚
                lay.addSpacing(5)
        self.btn_pin.clicked.connect(lambda: self.toggle_pin())
        self.btn_sponsor.clicked.connect(self.sponsor)
        self.btn_set.clicked.connect(lambda: self.toggle_drawer())
        self.btn_min.clicked.connect(self.minimize)
        self.btn_close.clicked.connect(self.close)
        self.btn_align.clicked.connect(self.toggle_follow)
        return bar

    def _status_card(self):
        """折叠式信息窗（v1.6）：一行看连通状态，点开看详情与尺寸动作。"""
        bar = InfoBar()
        self.info = bar
        # 属性映射：下面几处老代码（_on_attached / _set_badge / fix_aspect_now）
        # 仍按旧名字访问，映射住就不用改那些逻辑
        self.dot, self.who = bar.dot, bar.who
        self.who_sub, self.badge = bar.who_sub, bar.badge
        bar.attach_key = self._k("attach")       # 状态条的"按 X 重连"跟着键位走
        self.btn_fix = bar.btn_fix
        # v1.9 修：这里一直漏映射 btn_suggest，而 _on_attached 里直接 self.btn_suggest.xxx
        # → 连上窗口时抛 AttributeError，被 Qt 信号栈吞掉，界面上什么都不显示。
        self.btn_suggest = bar.btn_suggest
        bar.fix_clicked.connect(self.fix_aspect_now)
        bar.suggest_clicked.connect(self.suggest_size)
        return bar


    def _seg_row(self):
        self.seg = Segmented(("名单", "伤害"))
        return self.seg

    def _book_bar(self):
        """伤害页的库选择条（v1.4）：成员库 / BOSS 库 两个下拉，直接放在卡片下面。

        为什么放这儿而不是只藏在设置里：采集前最常改的就是"这次用哪个盟的名单"，
        藏两层等于每次都要开抽屉。成员库允许「不使用」（只靠位置合并 + BOSS 归一）。
        """
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for text, attr in (("成员库", "sel_roster"), ("BOSS 库", "sel_boss")):
            lb = QLabel(text)
            lb.setProperty("role", "sub")
            lb.setFixedWidth(44)
            # FitComboBox：弹出列表按最长项自适应，并夹在客户端窗口内（v1.21）
            cb = FitComboBox()
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
            setattr(self, attr, cb)
            h.addWidget(lb, 0)
            # BOSS 库要装「库名 + 数量 + 3 个 BOSS 名」，比成员库更长，多分一档宽度
            h.addWidget(cb, 4 if attr == "sel_boss" else 3)
        self.sel_roster.currentIndexChanged.connect(self._on_roster_book)
        self.sel_boss.currentIndexChanged.connect(self._on_boss_book)
        return bar

    def _roster_bar(self):
        """名单页的库行：下拉 + 新建 + 管理（高频入口，不藏设置里）。"""
        bar = QWidget()
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        lb = QLabel("成员库")
        lb.setProperty("role", "sub")
        lb.setFixedWidth(44)
        self.sel_rlib = QComboBox()
        self.sel_rlib.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sel_rlib.setMinimumContentsLength(8)
        self.sel_rlib.currentIndexChanged.connect(self._on_roster_book)
        btn_new = QPushButton("新建…")
        btn_new.setProperty("role", "sub")
        btn_new.setToolTip("建一套新成员库（例如 嗷润吉-新区）")
        btn_new.clicked.connect(self._new_roster_book)
        btn_mgr = QPushButton("管理…")
        btn_mgr.setProperty("role", "sub")
        btn_mgr.setToolTip("另存 / 重命名 / 删除 / 切换活动库")
        btn_mgr.clicked.connect(lambda: self._open_book("roster"))
        h.addWidget(lb, 0)
        h.addWidget(self.sel_rlib, 1)
        h.addWidget(btn_new, 0)
        h.addWidget(btn_mgr, 0)
        return bar

    def _roster_btn_row(self):
        """名单页的按钮行：导出名单 / 打开目录 / 删除选中。

        伤害页的「本屏 / 框选」在名单页没有意义，所以单独一行而不是复用。
        """
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.btn_r_exp = QPushButton(f"导出名单 {self._k('export')}")
        self.btn_r_exp.clicked.connect(self.export)
        self.btn_open2 = QPushButton("打开目录")
        self.btn_open2.clicked.connect(self.open_export_dir)
        self.btn_del = QPushButton("删除选中")
        self.btn_del.setToolTip("删掉表格里选中的成员（只改库文件，下次采集生效）")
        self.btn_del.clicked.connect(self._del_roster_row)
        for b in (self.btn_r_exp, self.btn_open2, self.btn_del):
            row.addWidget(b)
        row.addStretch(1)
        return box

    def _main_card(self):
        card = Card(strong=True)
        left = QVBoxLayout()
        left.setSpacing(2)
        self.card_title = QLabel("联盟突袭 · HARD")
        self.card_title.setProperty("role", "title")
        self.card_sub = QLabel("已采 0 条 · 0 人")
        self.card_sub.setProperty("role", "sub")
        left.addWidget(self.card_title)
        left.addWidget(self.card_sub)
        card.body.addLayout(left, 1)

        self.btn_primary = QPushButton(f"开始采集  {self._k('hard')}")
        self.btn_primary.setObjectName("primary")
        self.btn_primary.setMinimumWidth(148)
        self.btn_primary.clicked.connect(self._toggle_run)
        card.body.addWidget(self.btn_primary, 0, Qt.AlignVCenter)

        # 次按钮单独一行，避免和主按钮抢宽度（包成 widget 才能整行显隐）
        row = QHBoxLayout()
        row.setSpacing(8)
        self.row_hard = QWidget()
        self.row_hard.setLayout(row)
        self.btn_shot = QPushButton(f"本屏 {KEYS.LOCAL['shot']}")
        self.btn_export = QPushButton(f"导出 {self._k('export')}")
        self.btn_pick = QPushButton(f"框选 {KEYS.LOCAL['pick']}")
        self.btn_shot.clicked.connect(lambda: self.engine.shot())
        self.btn_pick.clicked.connect(lambda: self.pick_rect())
        self.btn_export.clicked.connect(self.export)
        for b in (self.btn_shot, self.btn_export, self.btn_pick):
            row.addWidget(b)
        row.addStretch(1)

        # 伤害页的库行与名单页的库行各自独立（同一控件的两处副本，互相同步）：
        # 名单页那行多「新建… / 管理…」——7 个联盟的人每天都在切库，不能藏进设置。
        self.bar_hard = self._book_bar()
        self.bar_roster = self._roster_bar()

        wrap = QVBoxLayout()
        wrap.setSpacing(8)
        wrap.addWidget(card)
        wrap.addWidget(self.bar_hard)
        wrap.addWidget(self.bar_roster)
        wrap.addWidget(self.row_hard)
        self.row_roster = self._roster_btn_row()
        wrap.addWidget(self.row_roster)
        holder = QWidget()
        holder.setLayout(wrap)
        return holder

    def _table_card(self):
        card = Card()
        inner = QVBoxLayout()
        inner.setContentsMargins(0, 0, 0, 0)
        self.model = TableModel()
        self.table = QTableView()
        self.table.doubleClicked.connect(self._on_table_dblclick)
        self.table.setModel(self.model)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setDefaultSectionSize(T.H_ROW)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setStretchLastSection(True)
        # 表头对齐**显式指定**，跟 data() 用同一个常量；不写就各排各的
        hh.setDefaultAlignment(TableModel.ALIGN)
        for i, w in enumerate(TableModel.MIN_W):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, w)
        hh.setSectionResizeMode(len(TableModel.MIN_W) - 1, QHeaderView.Stretch)
        inner.addWidget(self.table)
        self.table.verticalScrollBar().valueChanged.connect(self._on_table_scrolled)
        card.body.addLayout(inner)
        card.body.setContentsMargins(0, 0, 0, 0)
        return card

    def _progress_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1)
        self.prog.setValue(0)
        self.prog.setTextVisible(False)
        row.addWidget(self.prog, 1)
        self.prog_lbl = QLabel("0 / 0")
        self.prog_lbl.setProperty("role", "sub")
        row.addWidget(self.prog_lbl)
        self.follow = LinkLabel("↓ 已跟随最新")
        self.follow.setStyleSheet(f"color: #8AB4F8; font-size: {T.SZ_SUB}px;")
        self.follow.clicked.connect(self.back_to_latest)
        self.follow.setToolTip("采集时自动滚到最新一行；你手动上滚会暂停跟随，点这里恢复")
        row.addWidget(self.follow)
        return row

    def _drawer(self, parent):
        d = QFrame(parent)
        d.setObjectName("drawer")
        d.setGeometry(0, T.H_TITLE, self.width(), self.height() - T.H_TITLE)
        lay = QVBoxLayout(d)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        head = QHBoxLayout()
        t = QLabel("设置")
        t.setProperty("role", "title")
        head.addWidget(t)
        hint = QLabel(self._k("settings"))
        self._hint_key = hint
        hint.setProperty("role", "sub")
        head.addWidget(hint)
        head.addStretch(1)
        close = IconButton("", "关闭设置", icon="close")
        close.setObjectName("drawerClose")      # 独立样式：胶囊底 + hover 变红
        close.clicked.connect(lambda: self.toggle_drawer(False))
        head.addWidget(close)
        lay.addLayout(head)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(f"background: {T.BORDER}; max-height: 1px; border: none;")
        lay.addWidget(line)

        # v1.12：设置项变多了（新增快捷键 7 行），矮窗口下会顶出屏幕，套一层滚动
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # viewport 默认 autoFillBackground=True，会填系统 Base 色（Win10 浅色 = 白）。
        # 这里必须有真底色：整片半透明白压在白底上等于白屏（v1.23）。
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 2, 4, 0)
        body.setSpacing(8)          # 分组之间的间距（组内行距在 Section 里）
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        # ---------------- 分组（v1.19）：按用户动线分，不按代码结构。
        # 每组一张卡片、标题条整条可点；展开状态存进配置，下次打开保持原样。
        self._sections = {}

        # ---- 窗口与并排（默认展开：连上窗口最先要调的就是这几项）
        sec = self._add_section(body, "win", "窗口与并排", "绑定 · 比例 · 位置 · 跟随", True)
        bind = QLabel("自动 · 按进程名")
        bind.setProperty("role", "sub")       # 只读信息，弱一档，别和可改项抢注意力
        self._row_setting(sec.lay, "窗口绑定", bind)

        self.opt_force = self._switch(bool(self.cfg.get("force_aspect", True)))
        self.opt_force.toggled.connect(self._on_force_toggled)
        self._row_setting(sec.lay, "强制 16:9", self.opt_force)

        self.opt_snap = self._combo(["右侧", "左侧", "自动", "不动"])
        self.opt_snap.setCurrentText(_side(self.cfg.get("snap_side")))
        self.opt_snap.currentTextChanged.connect(self._on_snap_changed)
        self._row_setting(sec.lay, "并排位置", self.opt_snap)

        self.opt_gap = self._spin(int(self.cfg.get("gap", 8)), 0, 40, " px")
        self.opt_gap.valueChanged.connect(self._on_gap_changed)
        self._row_setting(sec.lay, "贴边间距", self.opt_gap)

        self.opt_follow_win = self._switch(bool(self.cfg.get("follow_win", True)))
        self.opt_follow_win.toggled.connect(self._on_follow_win_toggled)
        self._row_setting(sec.lay, "窗口跟随", self.opt_follow_win)

        self.opt_pin = self._switch(bool(self.cfg.get("pin", False)))
        self.opt_pin.toggled.connect(self._on_pin_toggled)
        self._row_setting(sec.lay, "窗口置顶", self.opt_pin)

        # ---- 数据与导出（默认展开）
        sec = self._add_section(body, "data", "数据与导出", "存放位置 · 名单导出", True)
        self._row_setting(sec.lay, "数据目录", self._path_edit())

        btn_boss_exp = QPushButton("导出 CSV")
        btn_boss_exp.setProperty("role", "sub")
        btn_boss_exp.setToolTip("把当前 BOSS 库导成 CSV（落在 数据目录/BOSS名单/）")
        btn_boss_exp.clicked.connect(self.export_boss_book)
        self._row_setting(sec.lay, "BOSS 名单", btn_boss_exp)
        self._row_setting(sec.lay, "日志与诊断", self._open_dir_row())

        # ---- 外观（默认折叠）
        sec = self._add_section(body, "look", "外观与显示", "背景 · 表格滚动", False)
        self._row_setting(sec.lay, "背景图片", self._bg_row())

        self.opt_follow = self._switch(bool(self.cfg.get("follow", True)))
        self.opt_follow.toggled.connect(self._on_follow_toggled)
        self._row_setting(sec.lay, "表格跟随最新", self.opt_follow)

        # ---- 快捷键（默认折叠：装好就不动的项，不该占常用项的位置）
        sec = self._add_section(body, "keys", "快捷键",
                                f"{len(KEYS.ORDER)} 个动作可改", False)
        self.opt_hot = self._switch(bool(self.cfg.get("hotkeys", True)))
        self.opt_hot.setToolTip("只在游戏窗口或本客户端处于前台时生效 ——\n"
                                "切到浏览器等其它程序会自动让出按键，不会误触发")
        self.opt_hot.toggled.connect(self._on_hot_toggled)
        self._row_setting(sec.lay, "全局热键", self.opt_hot)

        self._key_combos = {}
        for action, label in KEYS.ORDER:
            cb = QComboBox()
            cb.addItems(KEYS.CHOICES)
            cb.setCurrentText(self._keys.get(action) or KEYS.DEFAULTS[action])
            cb.setFixedWidth(96)
            cb.currentTextChanged.connect(
                lambda key, a=action: self._on_key_changed(a, key))
            self._key_combos[action] = cb
            self._row_setting(sec.lay, "　" + label, cb)

        reset_key = LinkLabel("恢复默认键位")
        reset_key.setProperty("role", "sub")
        reset_key.setToolTip("全部改回 F7 / F3 / F4 / F8 / F9 / F10 / F11")
        reset_key.clicked.connect(self._reset_keys)
        rh = QHBoxLayout()
        rh.setContentsMargins(0, 0, 0, 0)
        rh.addStretch(1)
        rh.addWidget(reset_key, 0)
        sec.lay.addLayout(rh)

        # ---- 高级（默认折叠）
        sec = self._add_section(body, "adv", "高级", "调试面板", False)
        self.opt_debug = self._switch(False)
        self.opt_debug.toggled.connect(self._on_debug_toggled)
        self._row_setting(sec.lay, "调试面板", self.opt_debug)

        body.addStretch(1)
        tip = QLabel(f"调试项默认不出现；{self._k('debug')} 或 --debug 才展开")
        self._tip_debug = tip
        tip.setProperty("role", "sub")
        tip.setWordWrap(True)
        body.addWidget(tip)
        return d

    # ---------------- 设置分组（v1.19）
    def _add_section(self, body, key: str, title: str, sub: str = "",
                     default: bool = True):
        """建一个可折叠分组，展开状态按上次记住的来。

        状态存在 `导出设置.json` 的 `sections` 里：设置页是"配一次就不再进来"的地方，
        每次打开都回到统一默认反而更烦（想改的那组总要再展开一次）。
        """
        saved = self.cfg.get("sections") or {}
        on = bool(saved.get(key, default))
        sec = Section(title, sub=sub, expanded=on, key=key)
        sec.toggled.connect(lambda v, k=key: self._on_section(k, v))
        body.addWidget(sec)
        self._sections[key] = sec
        return sec

    def _on_section(self, key: str, on: bool):
        store = dict(self.cfg.get("sections") or {})
        store[key] = bool(on)
        self.cfg["sections"] = store
        S.save_settings(sections=store)

    def _book_row(self, combo, kind):
        """「成员库 / BOSS 库」这一行：下拉 + 管理按钮。"""
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(combo)
        more = QPushButton("…")
        more.setFixedWidth(34)
        more.setToolTip("管理库：新建 / 另存 / 重命名 / 删除")
        more.clicked.connect(lambda: self._open_book(kind))
        h.addWidget(more)
        return box

    @staticmethod
    def _row_setting(lay, name, ctrl):
        row = QHBoxLayout()
        row.setSpacing(10)
        label = QLabel(name)
        label.setMinimumWidth(96)
        row.addWidget(label, 1)
        row.addWidget(ctrl, 0, Qt.AlignRight)
        lay.addLayout(row)

    @staticmethod
    def _switch(on: bool):
        cb = QCheckBox()
        cb.setChecked(on)
        cb.setFixedWidth(34)
        return cb

    @staticmethod
    def _combo(items):
        c = QComboBox()
        c.addItems(items)
        c.setFixedWidth(150)
        return c

    @staticmethod
    def _spin(val, lo, hi, suffix):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        s.setSuffix(suffix)
        s.setFixedWidth(104)
        return s

    def _path_edit(self):
        """默认导出目录：可直接改，也可以点「…」挑。

        目录存 `预设/导出设置.json`，改完立刻生效（不需要重启）。
        """
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        cur = str(S.default_dir())          # v1.18：这里是「数据根」，不是某一类子目录
        self.dir_edit = QLineEdit(cur)
        self.dir_edit.setFixedWidth(220)    # 窄了只看得到路径尾巴，看不出在哪块盘
        self.dir_edit.setCursorPosition(0)  # 失焦时显示开头（盘符）而不是末尾
        self.dir_edit.setToolTip(cur)
        self.dir_edit.editingFinished.connect(self._on_dir_edited)
        h.addWidget(self.dir_edit)
        op = QPushButton("打开")
        op.setFixedWidth(52)                # 48 时两个字贴边，体检报"宽 < 需要"
        op.setToolTip("在资源管理器里打开数据目录")
        op.clicked.connect(lambda: self._open_dir("root"))
        h.addWidget(op)
        more = QPushButton("…")
        more.setFixedWidth(38)
        more.setToolTip("选择目录")
        more.clicked.connect(self._pick_dir)
        h.addWidget(more)
        return box

    # ---------------- 快捷键 ----------------
    def _shortcuts(self):
        """F 区快捷键：先抢全局，抢不到再退回窗口内。

        为什么必须全局：用户按 F4 时焦点在游戏里，`QShortcut`（WindowShortcut）
        那一刻根本收不到键。不改成 RegisterHotKey，快捷键就等于没有。
        """
        from PySide6.QtGui import QKeySequence, QShortcut

        # 键位从设置读（v1.12），不再写死。默认：F7 连接 / F3 名单 / F4 伤害 /
        # F8 导出 / F9 并排 / F10 设置 / F11 调试。
        K = self._keys
        global_binds = [
            (K["attach"], self.attach),
            (K["roster"], lambda: self._hotkey_mode(0)),
            (K["hard"], lambda: self._hotkey_mode(1)),
            (K["export"], self.export),
            (K["align"], self.toggle_follow),
            (K["settings"], lambda: self.toggle_drawer()),
            (K["debug"], lambda: self.toggle_debug()),
        ]
        # 窗口内快捷键：只在客户端有焦点时有意义，不参与全局注册。
        # Esc 绝不能全局：会劫持整个系统的 Esc，游戏里就没法用了。
        local = {
            KEYS.LOCAL["shot"]: lambda: self.engine.shot(),
            KEYS.LOCAL["pick"]: lambda: self.pick_rect(),
            KEYS.LOCAL["pin"]: lambda: self.toggle_pin(),
            "Esc": lambda: self._stop_current(),
        }
        ok, bad = [], []
        if bool(self.cfg.get("hotkeys", True)):
            ok, bad = self._hot.install(global_binds)
            if ok:
                self.say(f"全局热键已注册：{'/'.join(ok)}")
            if bad:
                self.say(f"⚠ 全局热键被占用：{'/'.join(bad)} —— 退到窗口内（需先点一下客户端）。"
                         f"若任务管理器里有多个「NIKKE联盟采集」，关掉多余的再开", "WARN")
            if not self._scope_timer.isActive():
                self._scope_timer.start()     # 开始盯着前台窗口
        else:
            bad = [k for k, _ in global_binds]
            self._scope_timer.stop()
        table = dict(global_binds)
        self._bind_map = dict(global_binds)      # 供自检查询"某个键到底绑了谁"
        for name in bad:
            local[name] = table[name]
        self._local_keys = {}
        for key, fn in local.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)
            self._local_keys[key] = sc

    def _refresh_hot_scope(self):
        """每 300 ms 判一次前台：只有游戏 / 本工具在用时才占用全局热键。

        为什么要判：`RegisterHotKey` 是**独占**的 —— 注册期间浏览器、聊天框里按
        F3/F4 收不到，而且照样触发本工具的采集。用户的要求是"只在游戏的客户端和
        工具的客户端内响应"，所以离开范围就注销，把按键还给系统。

        300 ms 的延迟换来的是「点一下游戏就能按」——比固定只在窗口内响应好得多，
        比永久全局占用干净。
        """
        try:
            win = getattr(getattr(self, "engine", None), "win", None)
            ok = W.in_scope(getattr(win, "hwnd", 0) if win is not None else 0)
        except Exception:
            ok = True                       # 判断失败就别把功能关死
        if ok == self._hot_scope:
            return
        self._hot_scope = ok
        bad = self._hot.set_scope(ok)
        if not ok:
            A.log("hotkey", "前台不是游戏/本工具 → 注销全局热键，按键还给系统")
            return
        A.log("hotkey", f"回到作用域 → 重新注册：{'/'.join(self._hot.names())}")
        if bad:
            self.say(f"⚠ 热键重新注册失败：{'/'.join(bad)}（多半被别的程序抢了）", "WARN")

    def _rebuild_shortcuts(self):
        """拆掉旧快捷键重建。切换「全局热键」开关时两条路必须有且只有一条生效。"""
        for sc in getattr(self, "_local_keys", {}).values():
            try:
                sc.setEnabled(False)
                sc.deleteLater()
            except Exception:
                pass
        self._local_keys = {}
        self._shortcuts()

    def _hotkey_mode(self, idx):
        """F3/F4：先切到对应页，再执行该页的动作（切换式）。"""
        if self._mode != idx:
            self.seg.select(idx)
        self._toggle_run()

    # ---------------- 模式 ----------------
    def _on_mode(self, idx):
        hard = idx == 1
        changed = idx != getattr(self, "_mode", None)
        self._mode = idx
        self.card_title.setText("联盟突袭 · HARD" if hard else "联盟成员名单")
        self.model.set_mode(not hard)            # 表格列跟着页签切形态
        self._col_w = list(self.model.MIN_W)
        self._refresh_book_sub()
        # 两套库行/按钮行按页签切换：伤害页有 BOSS 库与本屏/框选，名单页有库管理
        self.bar_hard.setVisible(hard)
        self.row_hard.setVisible(hard)
        self.bar_roster.setVisible(not hard)
        self.row_roster.setVisible(not hard)
        if not hard:
            self._load_roster_table()            # 选中库就显示名单，不采集也能看
        else:
            self._restore_hard_view()            # 伤害页：用伤害真源重建，别留名单的行
        self._sync_primary()
        self.btn_shot.setEnabled(hard and not self._running)
        self.btn_pick.setEnabled(hard and not self._running)
        if self._rows:
            self._fit_columns()
        self.stack_placeholder()
        self._sync_recent_row()          # 底部那行跟着页签换语义（v1.21）
        S.save_settings(mode=int(idx))
        if changed:
            self.say(f"切到{'伤害' if hard else '名单'}页")

    def stack_placeholder(self):
        """没有数据时，空态文案跟着页签走 —— 否则名单页写着"按 F4 开始采集"更误导。"""
        if not hasattr(self, "empty") or self._rows:
            return
        self.empty.setText(
            f"还没有数据 · 按 {self._k('hard')} 开始采集" if self._mode == 1
            else f"还没有数据 · 按 {self._k('roster')} 开始采集名单")

    def _primary_text(self):
        if self._running:
            return "停止  Esc"
        if self._mode == 1:
            return f"开始采集  {self._k('hard')}"
        # v1.20：空库时不再写「开始采集名单」——页签已经写着"名单"了，重复
        return (f"重新采集  {self._k('roster')}" if self._lib_people()
                else f"开始采集  {self._k('roster')}")

    def _sync_primary(self):
        """主按钮的文案 + 两套配色属性，唯一刷新入口。

        两个属性：
          run=1  停止态（红）—— 采集中
          warn=1 重采态（琥珀橙）—— 名单页 + 库里有成员，点了会覆盖原名单
        以前文案在六处各自 setText、配色只在 _set_running 里改，改文案时必然漏改配色。
        """
        if not hasattr(self, "btn_primary"):
            return
        self.btn_primary.setText(self._primary_text())
        run = "1" if self._running else "0"
        warn = "1" if (self._mode == 0 and not self._running and self._lib_people()) else "0"
        b = self.btn_primary
        if b.property("run") != run or b.property("warn") != warn:
            b.setProperty("run", run)
            b.setProperty("warn", warn)
            b.style().unpolish(b)
            b.style().polish(b)

    def _lib_people(self) -> int:
        """当前成员库的库存人数（决定按钮说"开始"还是"重新"）。"""
        name = BK.active_roster()
        if not name:
            return 0
        return len((BK.read_roster(name).get("names") or {}))

    def toast(self, text: str, msec: int = 2400):
        if getattr(self, "_toast", None) is None:
            self._toast = Toast(self)
        self._toast.show_text(text, msec)

    def _toggle_run(self):
        if self._running:
            self._stop_current()
            return
        if self._mode != 1:
            self._toggle_roster()
            return
        if not self.engine.start():
            return
        self._set_running(True)

    def _on_esc_pressed(self):
        """全局 Esc：只在运行中触发，走与按钮完全相同的停止路径。

        为什么不是 QShortcut：采集时焦点在游戏里，客户端的 WindowShortcut
        收不到 Esc（用户实测"采集时 Esc 中断不了"）。这里是 GetAsyncKeyState
        被动轮询，不拦系统按键。
        """
        if not (self._running or bool(getattr(self._bridge, "_continuous", False))):
            self._esc.set_active(False)
            return
        self._stop_current()

    def _stop_current(self):
        """Esc / 主按钮的停止：按页签分派给伤害引擎或名单引擎。

        文案交给各自引擎说 —— 名单可能在导航、也可能在滚采，两种情况的话不一样；
        在外面再补一句只会撞车。
        """
        if self._mode != 1:
            if self._bridge is not None:
                self._bridge.stop_capture()
            return
        self.engine.stop()
        self.say("已请求停止，等当前这步做完")

    # ---------------- 多套库（v1.3）

    def _book_line(self) -> str:
        """状态卡用的一行库摘要。"""
        try:
            return BK.describe()
        except Exception:
            return ""

    def _reload_books(self):
        """把两套库列表灌进下拉；活动项选中。blockSignals 防触发切换回调。"""
        try:
            BK.ensure_migrated()
        except Exception as e:
            self.say(f"库迁移失败：{e}", "WARN")
        # 成员库三处同步（伤害页 / 名单页），BOSS 库只有伤害页
        rosters, act_r = BK.list_rosters(), BK.active_roster()
        bosses, act_b = BK.list_bosses(), BK.active_boss()
        for combo in (self.sel_roster, self.sel_rlib):
            combo.blockSignals(True)
            combo.clear()
            if combo is self.sel_roster:
                # 只有伤害采集允许"不用库"；名单采集必须落到具体一套库
                combo.addItem("不使用成员库", "")
            for e in rosters:
                combo.addItem(f"{e['name']} · {e['people']} 人", e["name"])
            i = combo.findData(act_r)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)
        # BOSS 库：名称也要能看见（v1.20）。下拉收起时只能显示一行，所以列前 3 个，
        # 完整清单放 tooltip；弹出列表给足宽度，别把名字裁掉。
        combo = self.sel_boss
        combo.blockSignals(True)
        combo.clear()
        for e in bosses:
            try:
                names = list((BK.read_boss(e["name"]) or {}).get("bosses") or [])
            except Exception:
                names = []
            brief = ""
            if names:
                # 全部名称都拼进去：收起态放不下由控件自己省略（tooltip 有全量），
                # 弹出列表由 FitComboBox 按最长项撑开 —— 用户"选的时候"必须看全
                brief = " · " + "/".join(names)
            # 数量写成 (5) 而不是「5 个」：下拉只有半个窗口宽，省下 3 个字正好
            # 够放第三个 BOSS 名（实测「5 个」会把铁匠挤掉）
            combo.addItem(f"{e['name']} ({e['count']}){brief}", e["name"])
            combo.setItemData(combo.count() - 1,
                              "\n".join(names) or "（这套库还没有 BOSS）",
                              Qt.ToolTipRole)
        combo.setToolTip("下拉里列出每套 BOSS 库的前 3 个 BOSS；悬停看完整清单")
        combo.view().setMinimumWidth(360)
        i = combo.findData(act_b)
        combo.setCurrentIndex(i if i >= 0 else 0)
        combo.blockSignals(False)

    def _on_roster_book(self, idx):
        src = self.sender()
        name = src.itemData(idx) if src is not None else ""
        name = name or ""
        if name == (BK.active_roster() or ""):
            return
        if name:
            ok = BK.set_active_roster(name)
        else:
            ok = BK.clear_active_roster()
        if ok:
            self.say(f"成员库 → {'「' + name + '」' if name else '不使用'}（下次开始采集生效）")
        for c in (self.sel_roster, self.sel_rlib):
            if c is not src:
                c.blockSignals(True)
                j = c.findData(name)
                c.setCurrentIndex(j if j >= 0 else 0)
                c.blockSignals(False)
        self._refresh_book_sub()
        if self._mode == 0:
            self._load_roster_table()
            self._sync_primary()

    def _on_boss_book(self, idx):
        src = self.sender()
        name = src.itemData(idx) if src is not None else ""
        if not name or name == BK.active_boss():
            return
        if BK.set_active_boss(name):
            self.say(f"BOSS 库 → 「{name}」（下次开始采集生效）")
        for c in (self.sel_boss,):
            if c is not src:
                c.blockSignals(True)
                j = c.findData(name)
                c.setCurrentIndex(j if j >= 0 else 0)
                c.blockSignals(False)
        self._refresh_book_sub()

    def _open_book(self, kind):
        dlg = BookDialog(self, kind)
        dlg.exec()
        if dlg.changed:
            self._reload_books()
            self._refresh_book_sub()
            self.say(f"库已更新：{self._book_line()}")

    def _ensure_roster_active(self):
        """名单页必须有落库目标：伤害页选过「不使用」时，进名单页自动认领第一套。

        实测（2026-11-07 17:13 截图）：活动库为空时下拉仍显示第一项，表格却是空的，
        用户会以为库丢了。这里把它钉死。
        """
        if BK.active_roster():
            return True
        items = BK.list_rosters()
        if items and BK.set_active_roster(items[0]["name"]):
            self._reload_books()
            return True
        return False

    def _load_roster_table(self):
        """名单页表格 = 当前库的成员（选中库就能看，不必采集）。"""
        if self._mode != 0 or self._running:
            return
        self._ensure_roster_active()
        name = BK.active_roster()
        names = (BK.read_roster(name).get("names") or {}) if name else {}
        disp = [{"idx": i, "member": n,
                 "conf": f"{float((v or {}).get('score') or 0):.3f}"}
                for i, (n, v) in enumerate(names.items(), 1)]
        self._rows_roster = [{"member": r["member"], "conf": float(r["conf"])}
                             for r in disp]
        self._rows = list(self._rows_roster)
        self._restoring = True
        try:
            self.model.set_rows(disp)
        finally:
            self._restoring = False
        self._fit_columns()
        self.stack.setCurrentIndex(1 if disp else 0)
        self.prog.setRange(0, max(1, len(disp)))
        self.prog.setValue(len(disp))
        self.prog_lbl.setText(f"{len(disp)} 人")
        self.empty.setText(f"这个库还是空的 · 按 {self._k('roster')} 采集，或点「新建…」换一套")
        self.card_sub.setText((f"{name} · 库存 {len(disp)} 人" if name
                               else "还没有成员库 · 点「新建…」建一套"))
        if hasattr(self, "info"):
            self.info.set_book(f"{name} · {len(disp)} 人" if name else "—")
        self._sync_primary()
        self.btn_export.setEnabled(bool(disp))
        if hasattr(self, "btn_del"):
            self.btn_del.setEnabled(bool(disp))

    def _new_roster_book(self):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "新建成员库", "库名（例如：嗷润吉-Q区）：")
        if not ok:
            return
        n, err = BK.valid_name(name)
        if err:
            self.say(f"库名不行：{err}", "WARN")
            return
        if not BK.write_roster(n, {"version": 1, "rounds": 0, "names": {}}):
            self.say("建库失败（目录权限？）", "WARN")
            return
        self._reload_books()
        self._load_roster_table()
        self._sync_primary()
        self.say(f"已新建成员库「{n}」并切为当前库")

    def _roster_name_at(self, row):
        try:
            it = self.model.rows[row]
            return (it.get("member") or "").strip()
        except Exception:
            return ""

    def _rename_roster_row(self, *a):
        """双击改名：写回当前库（只改库文件，下次采集生效）。"""
        from PySide6.QtWidgets import QInputDialog
        row = self.table.currentIndex().row()
        old = self._roster_name_at(row)
        name = BK.active_roster()
        if not old or not name:
            return
        data = BK.read_roster(name)
        names = data.get("names") or {}
        new, ok = QInputDialog.getText(self, "改昵称", f"「{old}」改成：", text=old)
        if not ok:
            return
        new = (new or "").strip()
        if not new or new == old:
            return
        if not BK.valid_name(new)[0]:
            self.say("新昵称不合法（空名/超长/含特殊字符）", "WARN")
            return
        if new in names:
            self.say(f"库里已经有「{new}」了", "WARN")
            return
        names[new] = names.pop(old)
        data["names"] = names
        if BK.write_roster(name, data, activate=False):
            self._load_roster_table()
            self.say(f"已把「{old}」改名为「{new}」（库：{name}）")

    def _del_roster_row(self):
        """删除选中成员（写回库，二次确认）。"""
        from PySide6.QtWidgets import QMessageBox
        row = self.table.currentIndex().row()
        old = self._roster_name_at(row)
        name = BK.active_roster()
        if not old or not name:
            self.say("先在表格里选中一位成员")
            return
        if QMessageBox.question(self, "删除成员",
                                f"从「{name}」里删掉「{old}」？\n"
                                f"只改库文件，不影响已导出的名单。") != QMessageBox.Yes:
            return
        data = BK.read_roster(name)
        names = data.get("names") or {}
        if old in names:
            names.pop(old)
            data["names"] = names
            BK.write_roster(name, data, activate=False)
            self._load_roster_table()
            self.say(f"已从「{name}」删除「{old}」")

    def _refresh_book_sub(self):
        """状态卡副标题跟上库信息（伤害页才提 BOSS/条数）。"""
        if self._mode == 1:
            if self._running:
                return
            n = len(self._rows_hard)
            people = len({r.get("member") for r in self._rows_hard if r.get("member")})
            self.card_sub.setText(f"已采 {n} 条 · {people} 人" if n
                                  else "已采 0 条 · 0 人")
        else:
            # 库名不往副标题塞：496 宽的卡片里主按钮会把它压掉（v1.4 截图实测）。
            # 当前库就在下面那行下拉里，副标题只说进度。
            n = len(self._rows)
            self.card_sub.setText(f"已采 {n} 人 · 名单随采集自动入库" if n
                                  else f"按 {self._k('roster')} 开始 · 先导航到联盟成员页")
        # 库信息变了，底部那行（当前成员表 / 人数）也要跟着变
        self._sync_recent_row()

    # ---------------- 名单（场景A并入：headless 驱动旧引擎，判据不改）

    def _bridge_ready(self) -> bool:
        if self._bridge is not None:
            return True
        try:
            self._bridge = RosterEngine(self)
        except Exception as e:
            self.say(f"名单引擎初始化失败：{e}", "WARN")
            return False
        self._bridge.log_sig.connect(lambda m, lv: self.say(m, lv))
        self._bridge.rows_sig.connect(self._on_roster_rows)
        self._bridge.state_sig.connect(self._set_running)
        return True

    def _toggle_roster(self):
        if not self._bridge_ready():
            return
        people = self._lib_people()
        if people and not getattr(self._bridge, "_continuous", False):
            # 覆盖提示：不拦，但必须让人知道这次会重写这套库
            self.toast(f"「{BK.active_roster()}」已有 {people} 人，本次采集会覆盖原有名单")
        if (getattr(self._bridge, "_continuous", False)
                or getattr(self._bridge, "_nav_running", False)):
            # ⚠ 不能判 `_busy`：那是旧工具 `toggle_auto` 里的旗标，客户端走 headless
            # 路径（直接调 `_begin_auto`），`_busy` 永远是 False —— 判它等于
            # 采集途中再点主按钮还是"开始"，什么都不会发生。
            self._bridge.stop_capture()
            return
        self.say("启动名单引擎（首次要预热识别模型，稍等两三秒）…")
        if not self._bridge.start_capture():
            return
        # 状态由 state_sig 广播（App 真跑起来才亮"停止"），这里不抢跑

    def _on_roster_rows(self, disp):
        """名单引擎推来的行：落名单真源；不在名单页就不动表格。"""
        self._rows_roster = [{"member": r["member"], "conf": float(r["conf"] or 0)}
                             for r in disp]
        if self._mode != 0:
            return
        self._rows = list(self._rows_roster)
        self._restoring = True
        try:
            self.model.set_rows(disp)
        finally:
            self._restoring = False
        self._fit_columns()
        self.stack.setCurrentIndex(1 if disp else 0)
        n = len(disp)
        self.prog.setRange(0, max(1, n))
        self.prog.setValue(n)
        self.prog_lbl.setText(f"{n} 人")
        self._refresh_book_sub()
        self.btn_export.setEnabled(bool(n))
        if self._follow:
            self.table.scrollToBottom()

    def _on_table_dblclick(self, idx):
        """伤害页双击不做事（它是采集结果，不能手改）；名单页双击改名。"""
        if self._mode == 0 and not self._running:
            self._rename_roster_row()

    def _set_running(self, run: bool):
        was = self._running
        self._running = run
        self._sync_primary()          # 文案 + run/warn 两个配色属性一起刷
        hard = self._mode == 1
        for b in (self.btn_shot, self.btn_pick):
            b.setEnabled(hard and not run)
        cur = self._rows_hard if hard else self._rows_roster
        # 名单页：库里有名字就该能导（不要求这次采过）
        self.btn_export.setEnabled(bool(cur) or (not hard and self._lib_people() > 0))
        if hasattr(self, "btn_del"):
            self.btn_del.setEnabled(bool(self._rows_roster) and not run)
        # Esc 只在运行中才监听：平时按 Esc 不该有任何动静。
        # 「运行中」必须包含**导航阶段** —— 那十几秒里界面看着是空闲的，
        # 但它正在自动点页面；不监听就等于按了停止没反应（v1.23 真机）。
        esc = getattr(self, "_esc", None)
        if esc is not None:
            esc.set_active(bool(run)
                           or bool(getattr(self._bridge, "_nav_running", False))
                           or bool(getattr(self._bridge, "_continuous", False)))
        if was and not run and self._mode == 0:
            # 名单收工：先刷新库（下拉人数 + 表格 + 副标题），再说存到哪。
            # 一行都没采到（导航被取消 / 开跑前校验没过）就别说"已存入"，
            # 那句话在空库上看着像采集成功了。
            self._reload_books()
            self._load_roster_table()
            if self._rows_roster:
                n = self._lib_people()
                self.say(f"名单已存入「{BK.active_roster() or '默认'}」（现在 {n} 人）· "
                         f"要按盟分开存：成员库「管理…」→ 另存当前", "INFO")

    # ---------------- 通联状态机（v1.9）
    def _refresh_link(self):
        """每 1.2 s 判一次三态：未启动 / 不在前台 / 已连接。

        以前只在 attach 那一刻写一次状态，游戏关了、最小化了，界面还挂着
        "已连接" —— 用户说的"连接状态不会刷新"就是这个。
        """
        win = self.engine.win
        st = W.game_link(win.hwnd if win is not None else 0)
        if st == "gone":
            if win is not None:
                # 窗口没了：句柄留着只会让后续截图拿到空图，日志一片"找不到了"
                self.engine.win = None
                self._game_rect = None
                self._auto_fixed = False
                self.say(f"游戏窗口已关闭或最小化 —— 恢复后按 {self._k('attach')} 重新连接",
                         "WARN")
            self.info.set_link("gone")
            self.btn_fix.setEnabled(False)
            self.btn_suggest.setEnabled(False)
            self._update_suggest()
            return
        if st == "back":
            self.info.set_link("back")
            self.btn_suggest.setEnabled(True)
            self._update_suggest()
            return
        c = W.client_box(win.hwnd)
        if c is None:
            self.info.set_link("back")
            return
        dpr = W.dpi_scale(win.hwnd) or 1.0
        w, h = int(c[2] / dpr), int(c[3] / dpr)
        good = W.aspect_ok(w, h)
        self.info.set_link(
            "ok", win.title,
            f"{w} × {h} · " + ("16:9" if good else "比例不是 16:9"),
            "16:9 ✓" if good else "比例不是 16:9")
        self.btn_fix.setEnabled(not good)
        self.btn_suggest.setEnabled(True)
        self._update_suggest()

    def _update_suggest(self):
        """展开区那句「建议尺寸」：与并排同源，点之前就知道会缩成多大。"""
        scr = self.screen()
        avail = scr.availableGeometry() if scr is not None else self.geometry()
        try:
            size = W.plan_suggest_client(avail, self.width(), int(self.cfg.get("gap", 8)))
        except Exception:
            size = None
        self.info.set_suggest(size)

    def minimize(self):
        """最小化。无边框窗口在个别环境下 showMinimized 不生效，补一次 Win32。"""
        self.showMinimized()
        if not self.isMinimized():
            try:
                ctypes.windll.user32.ShowWindow(int(self.winId()), 6)   # SW_MINIMIZE
            except Exception:
                pass

    def closeEvent(self, e):                     # noqa: N802
        for tm in (self._watch, self._link, self._flash_timer):
            try:
                tm.stop()
            except Exception:
                pass
        if getattr(self, "_esc", None) is not None:
            self._esc.set_active(False)
        try:
            self._hot.uninstall()
        except Exception:
            pass
        super().closeEvent(e)

    def show_about(self):
        """标题旁 Beta 徽标的动作（v1.12）。"""
        AboutDialog(self).exec()

    # ---------------- 快捷键配置（v1.12）
    def _k(self, action: str) -> str:
        """某个动作当前的键名。取不到就给默认值，绝不返回空串。"""
        return (self._keys or {}).get(action) or KEYS.DEFAULTS.get(action, "")

    def _on_key_changed(self, action: str, key: str):
        if not key or key == self._keys.get(action):
            return
        trial = dict(self._keys)
        trial[action] = key
        dup = KEYS.conflicts(trial)
        if dup:
            self.say(f"⚠ {dup[0]} 已被别的动作占用 —— 先给它换一个", "WARN")
            self._refresh_key_combos()          # 下拉回到真实值，不留假选项
            return
        self._keys = trial
        S.save_settings(keys=dict(self._keys))
        self._shortcuts()                       # 重新注册
        self._relabel_keys()
        self.say(f"快捷键 {KEYS.LABELS.get(action, action)} → {key}")

    def _refresh_key_combos(self):
        for a, cb in getattr(self, "_key_combos", {}).items():
            cb.blockSignals(True)
            cb.setCurrentText(self._keys.get(a) or KEYS.DEFAULTS[a])
            cb.blockSignals(False)

    def _reset_keys(self):
        self._keys = dict(KEYS.DEFAULTS)
        S.save_settings(keys=dict(self._keys))
        self._refresh_key_combos()
        self._shortcuts()
        self._relabel_keys()
        self.say("快捷键已恢复默认")

    def _relabel_keys(self):
        """键位变了之后重刷所有带键名的文案。

        这些文案散在按钮、空态、工具提示里十几处，改键必然漏掉几处 ——
        集中重刷一次，漏了也只是少改一处，不会出现"提示写 F4、实际按 F8"的错位。
        """
        if not hasattr(self, "btn_pin"):
            return
        self.info.attach_key = self._k("attach")
        self.btn_pin.setToolTip(f"置顶 {KEYS.LOCAL['pin']}")
        self._sync_follow_tip()
        self.btn_set.setToolTip(f"设置 {self._k('settings')}")
        self.btn_shot.setText(f"本屏 {KEYS.LOCAL['shot']}")
        self.btn_pick.setText(f"框选 {KEYS.LOCAL['pick']}")
        self.btn_export.setText(f"导出 {self._k('export')}")
        if hasattr(self, "btn_r_exp"):
            self.btn_r_exp.setText(f"导出名单 {self._k('export')}")
        if hasattr(self, "btn_primary"):
            self._sync_primary()
        if hasattr(self, "_hint_key"):
            self._hint_key.setText(self._k("settings"))
        if hasattr(self, "_tip_debug"):
            self._tip_debug.setText(
                f"调试项默认不出现；{self._k('debug')} 或 --debug 才展开")
        self.stack_placeholder()

    def toggle_pin(self):
        self._pin = not self._pin
        self.btn_pin.setOn(self._pin)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self._pin)
        self.show()
        self.say(f"置顶 {'开' if self._pin else '关'}")

    def toggle_drawer(self, show=None):
        show = (not self.drawer.isVisible()) if show is None else show
        self.drawer.setVisible(show)
        self.drawer.raise_()
        self.btn_set.setOn(show)

    def toggle_debug(self, show=None):
        vis = (not self.debug.isVisible()) if show is None else bool(show)
        self.debug.setVisible(vis)
        if hasattr(self, "opt_debug"):
            self.opt_debug.blockSignals(True)
            self.opt_debug.setChecked(vis)
            self.opt_debug.blockSignals(False)
        if vis:
            issues = audit_layout(self) or []
            self._audit_text = ("布局体检：0 处异常" if not issues
                                else f"布局体检：{len(issues)} 处异常（{issues[0][:36]}）")
            self._update_debug()
            self._debug_timer.start()
            self.say("调试面板开（F11）")
        else:
            self._debug_timer.stop()
            self.say("调试面板关（F11）")

    def _on_debug_toggled(self, v):
        S.save_settings(show_debug=bool(v))
        self.toggle_debug(bool(v))

    def _update_debug(self):
        """刷新调试面板：窗口 / 画面 / 采集现场 / 环境，各一行。"""
        if not self.debug.isVisible():
            return
        eng = self.engine
        win = eng.win
        lines = []
        if win is not None:
            cb = W.client_box(win.hwnd)
            size = f"{cb[2]}×{cb[3]}" if cb else "?"
            lines.append(f"窗口  {win.title[:14]} · hwnd {win.hwnd} · 客户区 {size}"
                         f" · DPI {W.dpi_scale(win.hwnd):.2f}")
        else:
            lines.append("窗口  未连接")
        try:
            r = eng.region()
            lines.append(f"画面  region=({r[0]},{r[1]},{r[2]},{r[3]}) · 框选={eng.rect_rel}")
        except Exception as e:
            lines.append(f"画面  {e}")
        sp = getattr(eng, "last_splice", None) or {}
        extra = ""
        if sp:
            extra = (f" · 上次拼接 k={sp.get('shift_rows')} 重叠 {sp.get('hit')}/{sp.get('overlap')}"
                     f" 追加 {sp.get('added')} 修正 {len(sp.get('patch') or [])}")
        lines.append(f"采集  {len(self._rows)} 条 · 行距 {getattr(eng, 'last_pitch', 0):.0f}"
                     f" · 拖动 {eng.DRAG_STEP}px/{int(eng.DRAG_GAP * 1000)}ms{extra}")
        lines.append(f"窗口位  {self.width()}×{self.height()}"
                     f" · 并排 {_side(self.cfg.get('snap_side'))}"
                     f" · 跟随 {'开' if self._watch.isActive() else '关'}")
        lines.append(f"环境  {'管理员' if A.self_elevated() else '普通权限'}"
                     f" · 置顶 {'开' if self._pin else '关'}"
                     f" · 热键 {'/'.join(self._hot.names()) or '无'}"
                     f" · 导出 {S.export_dir().name}")
        lines.append(self._audit_text)
        self.debug_text.setText("\n".join(lines))

    # ---------------- 与引擎对接
    def _load_rect(self):
        try:
            p = A.APP_DIR / "预设" / "困难框选.json"
            if p.exists():
                r = json.loads(p.read_text(encoding="utf-8")).get("rect_rel")
                if r and len(r) == 4:
                    return tuple(float(v) for v in r)
        except Exception:
            pass
        return None

    def _save_rect(self, rel):
        try:
            p = A.APP_DIR / "预设" / "困难框选.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({"rect_rel": list(rel)}, ensure_ascii=False),
                         encoding="utf-8")
        except Exception as e:
            self.say(f"框选保存失败：{e}")

    def _auto_attach(self):
        """启动时自动绑定，失败就重试几次 —— 刚开游戏时窗口常还没就绪。"""
        if not getattr(self, "autoconnect", True):
            return                          # `--shot` 自检时不碰用户的游戏窗口
        if self.engine.win is not None:
            return
        if self.engine.attach():
            self._attach_tries = 0
            return
        self._attach_tries += 1
        if self._attach_tries <= 5:
            QTimer.singleShot(2000, self._auto_attach)
        else:
            self.say("5 次自动绑定都没成功 —— 把游戏窗口恢复到前台后按 F2")

    def attach(self):
        self.engine.attach()

    def _on_attached(self, ok, title, size, badge):
        """连上/没连上都交给 `_refresh_link` 统一画 —— 两套文案必然漂移。"""
        if hasattr(self, "btn_fix"):
            # 展开区里按钮常驻，只按状态禁用：隐藏会让整块布局跳动
            self.btn_fix.setEnabled(bool(ok) and badge != "16:9 ✓")
            self.btn_suggest.setEnabled(bool(ok))
            if hasattr(self, "info"):
                self.info.set_region("已标定" if self.engine.rect_rel else "未标定")
        if ok:
            good = badge == "16:9 ✓"
            if not good and self.opt_force.isChecked() and not self._auto_fixed:
                self._auto_fixed = True
                QTimer.singleShot(300, self.fix_aspect_now)
            elif good:
                self._auto_fixed = False
                # 自动并排**不缩游戏**：刚连上就把人家的窗口改了很突兀。
                # 真想并排（含缩游戏）由用户按 F9 / 点分栏图标。
                if _side(self.cfg.get("snap_side")) != "不动":
                    QTimer.singleShot(120, lambda: self.align(quiet=True, shrink=False))
        self._refresh_link()

    def _recent_row(self):
        """底部信息行（v1.21）：伤害页看「最近导出」，名单页看「当前成员表」。

        为什么分开：在名单页显示"最近导出"是答非所问 —— 用户那一刻关心的是
        "我这套库的名单在哪、有多少人"，不是"上一次导出的是什么文件"。
        """
        row = QHBoxLayout()
        row.setSpacing(8)
        self.recent = ElideLabel("最近导出：—")
        self.recent.setProperty("role", "sub")
        row.addWidget(self.recent, 1)
        self.btn_open = QPushButton("打开目录")
        self.btn_open.setFixedWidth(96)
        self.btn_open.clicked.connect(self._on_recent_action)
        row.addWidget(self.btn_open)
        self._restore_recent()
        return row

    def _restore_recent(self):
        """启动时把上次导出的路径显示出来，不用再翻文件夹找。"""
        last = S.load_settings().get("last_export")
        self._last_export = Path(str(last)) if last else None
        self._sync_recent_row()

    def _sync_recent_row(self):
        """底部那行的文案与按钮，跟着页签/库走。"""
        if not hasattr(self, "btn_open"):
            return
        if self._mode == 0:
            name = BK.active_roster()
            n = self._lib_people()
            self.recent.setText(
                f"当前成员表：{name} · {n} 人" if name else "当前成员表：—（还没选库）")
            p = self._latest_roster_csv(name) if name else None
            self.recent.setToolTip(
                str(p) if p else "还没有导出过这套库的成员表 —— 点右边会自动导一份")
            self.btn_open.setText("打开成员表")
            self.btn_open.setToolTip("在资源管理器里定位当前成员库的 CSV（没有就先导出一份）")
            return
        self.btn_open.setText("打开目录")
        self.btn_open.setToolTip("在资源管理器里打开刚才导出 CSV 的目录")
        if self._last_export:
            self.recent.setText(f"最近导出：{self._last_export.name}")
            self.recent.setToolTip(str(self._last_export))
        else:
            self.recent.setText("最近导出：—")
            self.recent.setToolTip("还没有导出过")

    def _on_recent_action(self):
        if self._mode == 0:
            self._open_roster_csv()
        else:
            self.open_export_dir()

    @staticmethod
    def _latest_roster_csv(name: str):
        """这套库最近一次的成员表 CSV（没有就 None）。"""
        if not name:
            return None
        try:
            files = sorted(P.sub(P.ROSTER).glob(f"{P.safe(name)}_成员名单_*.csv"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
        except Exception:
            return None
        return files[0] if files else None

    def _open_roster_csv(self):
        """打开当前成员库的 CSV：没有就先按库导一份，再定位。"""
        name = BK.active_roster()
        if not name:
            self.say("还没有选成员库", "WARN")
            return
        p = self._latest_roster_csv(name)
        if p is None:
            from .roster_engine import export_book_csv
            n, out = export_book_csv()
            if not isinstance(out, Path):
                self.say(f"没有可打开的成员表：{out}", "WARN")
                return
            p = out
            self.say(f"已按当前库导出 {n} 人 → {out.name}")
        self._reveal(p)
        self._sync_recent_row()

    def _reveal(self, p: Path):
        """在资源管理器里选中这个文件；不行就退到打开所在目录。"""
        try:
            subprocess.Popen(["explorer", "/select," + str(p)])
            return
        except Exception:
            pass
        try:
            os.startfile(str(p.parent))
        except Exception as e:
            self.say(f"打不开：{e}", "WARN")

    def _set_recent(self, path):
        self._last_export = Path(path) if path else None
        self._sync_recent_row()

    def _on_table_scrolled(self, value):
        """用户手动上滚 → 暂停跟随；滚回底部 → 自动恢复。"""
        if self._restoring:
            return
        bar = self.table.verticalScrollBar()
        at_bottom = value >= bar.maximum() - 2
        if at_bottom == self._follow:
            return
        self._follow = at_bottom
        if at_bottom:
            self.follow.setText("↓ 已跟随最新")
        else:
            self.follow.setText(f"↓ 回到最新（{max(0, len(self._rows) - self._seen)} 条新）")

    def back_to_latest(self):
        self._follow = True
        self.table.scrollToBottom()
        self.follow.setText("↓ 已跟随最新")

    def _set_badge(self, text, good):
        """设置状态徽标文字，并**按字体实测**给足最小宽度。

        体检抓到过：`16:9 ✓` 需要 59px 却只分到 55px，被压掉 4px ——
        肉眼看不太出来，正是"组件显示不全"最典型的样子。
        """
        self.badge.setText(text)
        pad = 18                                  # 左右 padding + 边框
        need = self.badge.fontMetrics().horizontalAdvance(text) + pad
        self.badge.setMinimumWidth(max(56, need))
        if good is None:
            return
        fg = T.OK_TEXT if good else T.WARN_TEXT
        bg = "rgba(69,176,118,36)" if good else "rgba(224,160,48,36)"
        bd = "rgba(69,176,118,77)" if good else "rgba(224,160,48,77)"
        self.badge.setStyleSheet(
            f"color: {fg}; background: {bg}; border: 1px solid {bd};"
            f"border-radius: 5px; padding: 2px 7px; font-size: {T.SZ_SUB}px;")

    # ---------------- 表格 ----------------
    def _fit_columns(self):
        """列宽 = max(表头 sectionSizeHint, 每个单元格的 delegate sizeHint + QSS padding)。

        为什么不用 `fontMetrics().horizontalAdvance()` 手算：单元格实际占的宽度
        还包含 QSS padding 与样式自带的 textMargin，手算差 1~2px 就会触发省略号
        （「重装火神R」→「重装火…」、「100.0%」→「100.0…」都是这么来的）。
        `delegate.sizeHint()` 是 Qt 自己算的"刚好够"，直接问它最准。

        窗口被拖窄时按顺序压「成员 → BOSS → 伤害」这几列（它们有省略号 + ToolTip），
        **不压序号与剩余/置信** —— 数字被省略等于读不出数据，比排版难看严重得多。
        """
        m = self.model
        hh = self.table.horizontalHeader()
        dg = self.table.itemDelegate()
        opt = QStyleOptionViewItem()
        widths = []
        rows = min(m.rowCount(), 240)
        for i in range(len(m.HEADERS)):
            best = max(m.MIN_W[i], hh.sectionSizeHint(i))
            for r in range(rows):
                need = dg.sizeHint(opt, m.index(r, i)).width() + T.COL_PAD
                if need > best:
                    best = need
            widths.append(min(best, m.CAP_W[i]))

        view = self.table.viewport().width()
        if view > 200 and len(widths) >= 6:      # 视口压缩是伤害 6 列形态的规则
            over = sum(widths[:5]) - (view - m.MIN_W[5])
            for i, floor in ((1, 70), (2, 66), (3, 92)):
                if over <= 0 or i >= len(widths):
                    break
                cut = min(over, max(0, widths[i] - floor))
                widths[i] -= cut
                over -= cut

        changed = False
        for i in range(len(widths) - 1):        # 最后一列 Stretch，不设宽
            if i >= len(self._col_w):
                self._col_w.append(widths[i])
            if widths[i] != self._col_w[i]:
                self._col_w[i] = widths[i]
                hh.setSectionResizeMode(i, QHeaderView.Fixed)
                self.table.setColumnWidth(i, widths[i])
                changed = True
        return changed

    def _start_flash(self, from_row: int):
        """新追加的行亮一下（500 ms 淡到透明）。"""
        self.model.begin_flash(from_row)
        self._flash_alpha = 1.0
        self._flash_timer.start()

    def _flash_step(self):
        self._flash_alpha -= 1.0 / 30.0          # 16ms × 30 帧 ≈ 480ms
        if self._flash_alpha <= 0:
            self._flash_timer.stop()
            self.model.flash_from = -1
            self.model.set_flash_alpha(0.0)
            return
        self.model.set_flash_alpha(self._flash_alpha)

    def _restore_hard_view(self):
        """切回伤害页：把伤害真源铺回表格，并跟到最新一行。"""
        rows = self._rows_hard
        disp = self._disp_hard(rows)
        self._rows = list(rows)
        self._restoring = True
        self.model.set_mode(False)       # False = 伤害形态（6 列），参数别写反
        try:
            self.model.set_rows(disp)
        finally:
            self._restoring = False
        self._fit_columns()
        self.stack.setCurrentIndex(1 if disp else 0)
        n = len(disp)
        people = len({r.get("member") for r in rows if r.get("member")})
        self.prog.setRange(0, max(1, n))
        self.prog.setValue(n)
        self.prog_lbl.setText(f"{n} 条 · {people} 人")
        self.card_sub.setText(f"已采 {n} 条 · {people} 人" if n else "已采 0 条 · 0 人")
        self.btn_export.setEnabled(bool(n))
        if n and self._follow:
            self.table.scrollToBottom()
        self._seen = n

    def _disp_hard(self, rows):
        return [{"idx": i,
                 "member": r.get("member") or "",
                 "boss": r.get("boss") or "",
                 "damage": r.get("damage_raw") or "",
                 "pct": r.get("pct") or "",
                 "conf": r.get("conf")}
                for i, r in enumerate(rows, 1)]

    def _on_rows(self, rows):
        """伤害采集推来的行：**永远先落真源**，只有当前在伤害页才渲染。

        以前不分页，名单页的行会留在同一个 model 里，切回伤害页时内容对不上、
        滚动基准也被污染（用户报的"记录不滚动"就是它）。
        """
        self._rows_hard = list(rows)
        if self._mode != 1:
            return
        prev_n = len(self._rows)
        disp = self._disp_hard(rows)
        self._rows = list(rows)
        self._restoring = True
        try:
            self.model.set_rows(disp)
        finally:
            self._restoring = False
        self._fit_columns()
        self.stack.setCurrentIndex(1 if disp else 0)
        if len(disp) > prev_n:
            self._start_flash(prev_n)
        n = len(disp)
        people = len({r["member"] for r in rows if r.get("member")})
        self.prog.setRange(0, max(1, n))
        self.prog.setValue(n)
        self.prog_lbl.setText(f"{n} 条 · {people} 人")
        self._refresh_book_sub()
        self.btn_export.setEnabled(bool(n))
        if self._follow:
            self.table.scrollToBottom()
            self.follow.setText("↓ 已跟随最新")
        else:
            new = max(0, n - self._seen)
            self.follow.setText(f"↓ 回到最新（{new} 条新）")
        self._seen = n

    def _on_done(self):
        self._set_running(False)

    def pick_rect(self):
        if self._mode != 1:
            self.say("名单走自动标定 —— 开始采集时按内置预设自动认列；要手动框请在设置里打开旧窗口")
            return
        scr = (QGuiApplication.screenAt(self.geometry().center())
               or QGuiApplication.primaryScreen())
        self.picker = RegionPicker(scr)
        self.picker.picked.connect(self._on_picked)
        self.picker.show()
        self.say("框选：拖出卡片列表范围（顶部日期栏/滚动条不要框进来 · Esc 取消）")

    def _on_picked(self, rect):
        try:
            x0, y0, x1, y1 = self.engine.region()
        except Exception as e:
            self.say(str(e))
            return
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        rel = ((rect.x() - x0) / w, (rect.y() - y0) / h,
               (rect.right() - x0) / w, (rect.bottom() - y0) / h)
        rel = tuple(round(min(1.0, max(0.0, v)), 4) for v in rel)
        if rel[2] - rel[0] < 0.05 or rel[3] - rel[1] < 0.05:
            self.say("框太小了，忽略这次框选")
            return
        self.engine.rect_rel = rel
        self._save_rect(rel)
        self.say(f"已框选：相对 ({rel[0]:.2f}, {rel[1]:.2f}, {rel[2]:.2f}, {rel[3]:.2f})")

    # ---------------- 窗口：比例 / 并排 / 跟随
    def fix_aspect_now(self):
        """把游戏客户区修正成 16:9。先打印方案，再改，改完重新接窗口刷状态。"""
        win = self.engine.win
        if win is None:
            self.say("还没接上游戏窗口 —— 先按 F2")
            return
        plan = W.plan_aspect(win.hwnd)
        if plan is None:
            self.say("游戏客户区已经是 16:9，不用改")
            self.btn_fix.setVisible(False)
            return
        cw, ch = plan["client"]
        tw, th = plan["target_client"]
        self.say(f"修正 16:9：客户区 {cw}×{ch}（{plan['ratio']}）→ {tw}×{th}")
        changed, msg = W.fix_aspect(win.hwnd)
        self.say(msg, "INFO" if changed else "WARN")
        if changed:
            QTimer.singleShot(700, self.attach)     # 重新读尺寸，状态卡与并排一起刷新

    def align(self, quiet=False, shrink=True):
        """把客户端摆到游戏**旁边**（不重叠）。shrink=True 时允许为了腾地方缩游戏。"""
        win = self.engine.win
        if win is None:
            if not quiet:
                self.say("还没接上游戏窗口 —— 先按 F2")
            return
        ok, msg = W.place_beside(self, win.hwnd, _side(self.cfg.get("snap_side")),
                                 int(self.cfg.get("gap", 8)), shrink=shrink)
        if ok:
            self._game_rect = W.client_box(win.hwnd)
            self._last_align_warn = None
            if not quiet:
                self.say(msg)
        else:
            # 失败提示节流：跟随轮询/连按会在同一窗口状态下反复触发 align，
            # 同一句 WARN 只报一次，状态有变化（成功过/换原因）才再报。
            if msg != getattr(self, "_last_align_warn", None):
                self.say(msg, "WARN")
                self._last_align_warn = msg

    def _watch_game(self):
        """游戏窗口动了就跟着走。只移动客户端，不抢焦点。"""
        win = self.engine.win
        if win is None or self._resizing is not None:
            return
        cur = W.client_box(win.hwnd)
        if cur is None:
            return
        if self._game_rect is None:
            self._game_rect = cur
            return
        if W.same_rect(cur, self._game_rect, 3):
            return
        self._game_rect = cur
        if _side(self.cfg.get("snap_side")) == "不动":
            return
        if not bool(self.cfg.get("follow_win", True)):
            return
        QTimer.singleShot(320, lambda: self.align(quiet=True, shrink=False))

    def _on_force_toggled(self, v):
        self.cfg["force_aspect"] = bool(v)
        S.save_settings(force_aspect=bool(v))
        if v and self.engine.win is not None:
            self.fix_aspect_now()

    def _on_snap_changed(self, text):
        self.cfg["snap_side"] = text
        S.save_settings(snap_side=text)
        if self.engine.win is not None:
            self.align(shrink=True)

    def _on_gap_changed(self, v):
        self.cfg["gap"] = int(v)
        S.save_settings(gap=int(v))
        if self.engine.win is not None and _side(self.cfg.get("snap_side")) != "不动":
            self.align(quiet=True, shrink=False)

    def _on_follow_win_toggled(self, v):
        """设置抽屉里的开关：与顶栏按钮是同一个状态，别维护第二份。"""
        self._set_follow(v, place=bool(v), by_user=True)

    # ---------------- 窗口跟随（v1.14：持续性状态，不是一次性动作）
    def toggle_follow(self):
        """顶栏分屏图标 / F9：开→高亮并持续跟随，再点→取消并变暗。"""
        self._set_follow(not self._follow_win, place=True, by_user=True)

    def _set_follow(self, on: bool, place: bool = False, by_user: bool = False):
        """开关状态、图标高亮、轮询、设置同步，都从这里走。

        place=True 时立刻摆一次：只开轮询不摆，会出现"开关打开了但位置还是错的"。
        启动恢复用 place=False —— 那时还没接上游戏窗口，摆也是白摆。
        """
        on = bool(on)
        self._follow_win = on
        self.cfg["follow_win"] = on
        S.save_settings(follow_win=on)

        if hasattr(self, "btn_align"):
            self.btn_align.setOn(on)
            self._sync_follow_tip()
        opt = getattr(self, "opt_follow_win", None)      # 设置抽屉里的同一个开关
        if opt is not None and opt.isChecked() != on:
            opt.blockSignals(True)
            opt.setChecked(on)
            opt.blockSignals(False)

        if on:
            # 记下"当前的游戏矩形"，否则下一拍会误判成"窗口动了"
            self._game_rect = (W.client_box(self.engine.win.hwnd)
                               if self.engine.win is not None else None)
            self._watch.start()
            if place:
                self.align(shrink=True)
            if by_user:
                self.say("窗口跟随：开 —— 拖动游戏窗口，客户端会跟着走")
        else:
            self._watch.stop()
            if by_user:
                self.say("窗口跟随：关（客户端停在当前位置）")

    def _sync_follow_tip(self):
        k = self._k("align")
        if self._follow_win:
            self.btn_align.setToolTip(f"窗口跟随 · 已开启（再点取消 · {k}）")
        else:
            self.btn_align.setToolTip(f"窗口跟随 · 点击开启 · {k}")

    def _on_hot_toggled(self, v):
        self.cfg["hotkeys"] = bool(v)
        S.save_settings(hotkeys=bool(v))
        # 关掉全局后必须把 F 键**补成窗口内**快捷键，否则等于全部失效；
        # 打开时则要把窗口内那份拆掉，避免同一次按键执行两遍。
        self._rebuild_shortcuts()
        self.say(f"全局热键{'开（游戏里也能按）' if v else '关（需先点一下客户端）'}")

    def _on_pin_toggled(self, v):
        self.cfg["pin"] = bool(v)
        S.save_settings(pin=bool(v))
        if bool(v) != self._pin:
            self.toggle_pin()

    def _on_follow_toggled(self, v):
        self.cfg["follow"] = bool(v)
        S.save_settings(follow=bool(v))
        self._follow = bool(v)
        if v:
            self.back_to_latest()

    # ---------------- 导出
    def export(self):
        if self._mode != 1:
            self._export_roster()
            return
        rows = self._rows_hard
        if not rows:
            self.say("还没有数据 —— 先【采集本屏】或【开始采集】")
            return
        miss = S.incomplete(rows)
        if miss:
            # 不拦着导出：留空比编一个值安全，但必须点名让人去核
            self.say(f"⚠ 第 {miss} 条缺字段，已照常导出，请人工核对", "WARN")
        try:
            out = S.export_rows(rows, BK.active_boss(), BK.active_roster())
        except Exception as e:
            self.say(f"导出失败：{e}")
            return
        self._last_export = out
        self._set_recent(out)
        # 只写文件名：完整路径会把三行日志挤成一行（路径长的机器上尤其明显）。
        # 全路径在「最近导出」那行与它的 tooltip 里。
        self.say(f"已导出 {len(rows)} 条 → {out.name}")

    def _export_roster(self):
        """名单导出（v1.21）：有本次采集结果就先对账写库，否则直接导当前库。

        以前必须先采集过才能导出 —— 用户只是选了一套已有成员的库、想导一份看看，
        却被拦下来。现在只要库里有名字就能导。
        """
        if not BK.active_roster():
            self.say("还没有选成员库 —— 先在库下拉里选一套", "WARN")
            return
        from .roster_engine import export_book_csv
        if self._bridge is not None:
            n, out = self._bridge.export()      # 内部按有无采集结果自行分派
        else:
            n, out = export_book_csv()
        if not isinstance(out, Path):
            self.say(f"导出失败：{out}", "WARN")
            return
        self._last_export = out
        self._set_recent(out)
        self.say(f"已导出 {n} 人 → {out.name}")
        self._load_roster_table()          # 导出前对过账，库人数可能变了

    def suggest_size(self):
        """「并排并适配尺寸」：与 F9 同一条路 —— 空间不够就缩游戏再并排。

        v1.9 修正：原先这里把游戏改成屏幕能容纳的 1920×1080，**没算客户端要占的
        宽度**，改完照样并排放不下（用户：「建议尺寸明显不对，功能跟并排差不多」）。
        现在尺寸只有 `plan_shrink` 一个真源，直接复用 `align(shrink=True)`。
        """
        win = self.engine.win
        if win is None:
            self.say("还没接上游戏窗口 —— 先按 F2")
            return
        self._update_suggest()
        scr = self.screen()
        avail = scr.availableGeometry() if scr is not None else self.geometry()
        tgt = W.plan_suggest_client(avail, self.width(), int(self.cfg.get("gap", 8)))
        if tgt:
            self.say(f"目标尺寸 {tgt[0]}×{tgt[1]}（16:9，已为客户端留出位置）")
        self.align(shrink=True)

    def sponsor(self):
        SponsorDialog(self).exec()

    def export_boss_book(self):
        """把当前 BOSS 库导成 CSV，落在 `<根>/BOSS名单/`（v1.18）。"""
        try:
            name = BK.active_boss()
            bosses = list((BK.read_boss() or {}).get("bosses") or [])
        except Exception as e:
            self.say(f"读 BOSS 库失败：{e}", "WARN")
            return
        if not bosses:
            self.say("当前 BOSS 库是空的 —— 先到「管理…」里录一期", "WARN")
            return
        out = P.sub(P.BOSS) / P.boss_file(name)
        try:
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["序号", "BOSS", "库"])
                for i, b in enumerate(bosses, 1):
                    w.writerow([i, b, name])
        except Exception as e:
            self.say(f"导出失败：{e}", "WARN")
            return
        self._last_export = out
        self._set_recent(out)
        self.say(f"已导出 BOSS 名单 {len(bosses)} 个 → {out.name}")

    def open_export_dir(self):
        p = self._last_export or S.default_dir()
        d = p if p.is_dir() else p.parent
        try:
            os.startfile(str(d))
        except Exception as e:
            self.say(f"打不开目录：{e}")

    def _open_dir(self, kind: str):
        """在资源管理器里打开数据根或某一类子目录（v1.20）。

        目录可能被用户删掉，所以先 mkdir 再开；失败走日志提示，不弹模态框
        （设置页连续操作时弹框会打断）。
        """
        try:
            p = P.root() if kind == "root" else P.sub(kind)
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))
        except Exception as e:
            self.say(f"打不开目录：{e}", "WARN")

    def _open_dir_row(self):
        """「日志与诊断」这一行：开日志目录 + 导出诊断包。"""
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        b = QPushButton("打开日志")
        b.setProperty("role", "sub")
        b.setToolTip("在资源管理器里打开 日志/")
        b.clicked.connect(lambda _=False: self._open_dir(P.LOG))
        h.addWidget(b)

        b = QPushButton("导出诊断包")
        b.setProperty("role", "sub")
        b.setToolTip("环境信息 + 运行日志打成一个 zip；不含用户名、计算机名、"
                     "IP 与截图，发出去前可以自己解压看")
        b.clicked.connect(self.export_diag)
        h.addWidget(b)
        return box

    def export_diag(self):
        """导出一个 zip 给作者排查（v1.23）。

        失败不弹模态框：设置页的操作被打断很难受，日志里记了 traceback。
        """
        try:
            from . import diag as D
            out = D.export()
        except Exception as exc:
            A.log_exc("diag", exc)
            self.say(f"导出诊断包失败：{exc}", "WARN")
            self.toast("导出失败，详见日志")
            return
        self.say(f"诊断包已生成：{out.name}")
        self.toast("诊断包已生成")
        try:
            subprocess.Popen(["explorer", "/select,", str(out)])
        except Exception:
            pass

    def _on_dir_edited(self):
        """数据目录：只存根，五类子目录由 paths 自动建（v1.18）。"""
        raw = self.dir_edit.text().strip()
        if not raw:
            self.dir_edit.setText(str(S.default_dir()))
            return
        p = Path(raw).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.say(f"目录不可用：{e}")
            self.dir_edit.setText(str(S.default_dir()))
            return
        if not p.is_dir():
            self.say(f"不是目录：{p}")
            self.dir_edit.setText(str(S.default_dir()))
            return
        S.save_settings(data_dir=str(p))
        P.ensure_all()                      # 立刻把五类子目录建好，用户点进去就能看到
        self.dir_edit.setToolTip(str(p))
        self.say(f"数据目录：{p}（伤害统计 / 成员名单 / BOSS名单 / 日志 / 诊断）")

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择数据目录", str(S.default_dir()))
        if d:
            self.dir_edit.setText(d)
            self._on_dir_edited()

    def say(self, text, level="INFO"):
        from datetime import datetime
        self.log.add(f"{datetime.now():%H:%M:%S}  {text}")
        # 同时写进共用操作日志：界面只留最后三行，关掉就没了。
        # 12:05 那轮真机没落盘，出问题时只能靠猜（日志里只剩 cursor 记录）。
        try:
            A.logline(f"client: {text}", level)
        except Exception:
            pass

    # ---------------- 无边框窗口：拖动 + 边缘拉伸
    def _edge_at(self, pos) -> str:
        """鼠标在哪个边缘上。返回 "l/r/t/b/lt/rt/lb/rb"，不在边缘返回 ""。"""
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        H = T.HOT
        left, right = x <= H, x >= w - H
        top, bottom = y <= H, y >= h - H
        if left and top:
            return "lt"
        if right and top:
            return "rt"
        if left and bottom:
            return "lb"
        if right and bottom:
            return "rb"
        if left:
            return "l"
        if right:
            return "r"
        if top:
            return "t"
        if bottom:
            return "b"
        return ""

    _CURSOR = {"l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
               "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
               "lt": Qt.SizeFDiagCursor, "rb": Qt.SizeFDiagCursor,
               "rt": Qt.SizeBDiagCursor, "lb": Qt.SizeBDiagCursor}

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        edge = self._edge_at(e.position().toPoint())
        if edge:
            self._resizing = (edge, e.globalPosition().toPoint(), QRect(self.geometry()))
            return
        if e.position().y() <= T.H_TITLE:
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._resizing is not None:
            edge, start, geo = self._resizing
            gp = e.globalPosition().toPoint()
            dx, dy = gp.x() - start.x(), gp.y() - start.y()
            g = QRect(geo)
            if "l" in edge:
                g.setLeft(min(geo.left() + dx, geo.right() - T.WINDOW_MIN_W + 1))
            if "r" in edge:
                g.setRight(max(geo.right() + dx, geo.left() + T.WINDOW_MIN_W - 1))
            if "t" in edge:
                g.setTop(min(geo.top() + dy, geo.bottom() - T.WINDOW_MIN_H + 1))
            if "b" in edge:
                g.setBottom(max(geo.bottom() + dy, geo.top() + T.WINDOW_MIN_H - 1))
            if g.width() > T.WINDOW_MAX_W or g.height() > T.WINDOW_MAX_H:
                return
            self.setGeometry(g)
            return
        if self._drag_from is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            return
        edge = self._edge_at(e.position().toPoint())
        self.setCursor(self._CURSOR.get(edge, Qt.ArrowCursor))

    def mouseReleaseEvent(self, _e):
        if self._resizing is not None:
            self._resizing = None
            S.save_settings(win_w=self.width(), win_h=self.height())
        self._drag_from = None
        self.setCursor(Qt.ArrowCursor)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "drawer"):
            self.drawer.setGeometry(0, T.H_TITLE, self.width(), self.height() - T.H_TITLE)
        # 尺寸变化很频繁（拖边缘时每一帧都来），落盘要节流
        if getattr(self, "_size_timer", None) is None:
            self._size_timer = QTimer(self)
            self._size_timer.setSingleShot(True)
            self._size_timer.setInterval(500)
            self._size_timer.timeout.connect(self._on_size_settled)
        self._size_timer.start()

        if self._bg_path:                 # 自定义背景要按新尺寸重做 cover
            if getattr(self, "_bg_timer", None) is None:
                self._bg_timer = QTimer(self)
                self._bg_timer.setSingleShot(True)
                self._bg_timer.setInterval(160)   # 拖边缘每帧重建高清图会卡
                self._bg_timer.timeout.connect(self._rebuild_bg_and_repaint)
            self._bg_timer.start()

    def _on_size_settled(self):
        S.save_settings(win_w=self.width(), win_h=self.height())
        if hasattr(self, "model"):
            self._fit_columns()       # 视口宽变了，列宽按新宽度重排
