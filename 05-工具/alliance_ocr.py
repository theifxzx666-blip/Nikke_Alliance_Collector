# -*- coding: utf-8 -*-
"""NIKKE 联盟成员名单采集工具

用法：
    1) 打开客户端即可，工具会自己接上窗口（标题/进程命中 + 有效画面 16:9），
       后台每 2 秒重扫一次；连续接不上才放出「手动选择窗口」兜底
    2) 标定只在第一次做（一般内置预设已经够用）。点【标定昵称列】后浮层会先自己认一遍：
         · 认出来了    → 画面直接标出 ROI 和每一行，确认就行
         · 认不出来    → 在任意一行昵称上点一下（不必点准，点行间空白也行）
         · 结构不规整  → 拖一个框接管
       确认后的结果会固化成 预设/昵称列预设.json，之后换分辨率也不用来第二次
    3) 点【采集本屏】或【自动滚采】，滚动游戏后自动累加去重
    4) 点【导出 CSV】存盘

为什么坐标基准是「有效画面」而不是「客户区」：
    客户区含游戏自己加的黑边（窗口不是 16:9 时会有），拿它的百分比算坐标会整体
    偏移。有效画面 = 客户区去掉黑边，才是真正渲染 16:9 的那块矩形。
    锁定宽高比后，每轮截图前都能校验——比例变了说明 UI 布局变了，
    此时相对坐标也不再有效，必须停下要求重新标定。

为什么 ROI 是算出来的而不是画出来的：
    手画框不可复现——同一个意图画两次，落点差 10~30px、高度差半行，之后所有
    滚动判据都建在这个随机量上。改成"从画面结构里找出等距成列的文本"后，
    结果只跟画面内容有关，跟点在哪一行的哪个字上无关。

为什么标定结果要固化：
    需要标定的次数应该是一次，不是每次启动一次。预设里存的是「占有效画面的
    比例」和「行距占画面高度的比例」，所以 1220×686 上标出来的东西，换到
    1920×1080 会自动换算，不需要任何人再点一遍。

核心算法依据见 ../04-复用方案/联盟名单采集-可行性.md：
    关掉文字检测(det)只跑识别(rec) + 格点拟合剔噪 + 昵称合法性校验
"""
from __future__ import annotations

import csv
import ctypes
import difflib
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk

import appdir

def _app_dir() -> Path:
    """程序数据目录（`预设/`、`输出/`、日志都挂在这里）。

    冻结态**不再**用 exe 所在目录：单文件版每次启动都在 exe 同级解压，
    再把预设和许可撒在那里 —— 从桌面双击一次，桌面就多三个东西；
    装在 Program Files 时更是直接写不进去。具体规则见 `appdir.py`。

    源码态仍是本文件目录，开发时改预设、看输出都在熟悉的位置。
    """
    return appdir.app_dir()


APP_DIR = _app_dir()
OUT_DIR = APP_DIR / "输出"
try:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass                                  # 目录不可写不该拦启动，后面真写时再报
CFG = OUT_DIR / "区域.json"
# 标定结果一旦确认就固化成「预设」，放在工具目录里跟着走。
# 之后只要有效画面是 16:9，任何分辨率都按比例换算套用，不需要再标定。
PRESET = APP_DIR / "预设" / "昵称列预设.json"
RUN_LOG = OUT_DIR / "运行日志.txt"       # 旧路径，仅用于兼容历史文件
LOG_PATH = OUT_DIR / "操作日志.txt"      # 唯一的日志出口（客户端会改成本地数据目录）
DIAG_DIR = OUT_DIR                       # 诊断包落点（同上，可被宿主覆盖）
LOG_MAX = 2 * 1024 * 1024


def _fix_streams() -> None:
    """pythonw.exe 下 sys.stdout/stderr 是 None，任何 print 都会抛异常并静默退出。

    这里统一接到日志文件，既能兜住 traceback，也方便事后排查启动失败。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception:
        return
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


# 主题与界面组件集中在 ui_kit.py（竖版单列排版，见那边的说明）。
# 这里保留同名常量，下面几百处引用不用动。
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_kit as ui                                   # noqa: E402  (要先补 sys.path)
import sync_client                                    # noqa: E402

FONT = ui.FONT
FONT_SM = ui.FONT_SM
FONT_XS = ui.FONT_XS
BG = ui.BG
PANEL = ui.PANEL
FG = ui.FG
ACCENT = ui.ACCENT

APP_VER = "2.0"
# 流程步骤：与步骤条严格一致，改这里就是改界面
FLOW_STEPS = ["接窗口", "进成员页", "校人数", "滚采", "入库"]
# 日志区高度（行）：一轮滚采约每 1.6 秒写 8 行，空闲 5 行够看，
# 采集时撑到 9 行才够"边跑边看现场"——收工再让回给名单。
LOG_ROWS_IDLE = 5
LOG_ROWS_RUN = 9
# 同步配置：默认 enabled=false，只留档不联网。接后端时只改这个文件，不动代码。
SYNC_CFG = APP_DIR / "预设" / "同步.json"

UPSCALE = 2          # 单行放大倍数，实测 1~4 倍准确率一致、耗时无差异
MIN_SCORE = 0.60


def dpi_aware() -> None:
    """必须在建窗口前调用，否则 tkinter 坐标与截图像素会差一个缩放比。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def logline(msg: str, level: str = "INFO") -> None:
    """把状态栏的每句话写进操作日志。

    pythonw 没有控制台，用户看到的"报错"只显示在状态栏上，关掉就没了。
    """
    log("ui", msg, level)


# ---------------------------------------------------------------- 操作日志
#
# 目标是"用户把日志发过来就能定位，不用来回问细节"，所以记的不只是结果：
#   1) 每次启动先写环境快照（Python/Tk/依赖版本/屏幕/DPI/权限），先排除环境差异
#   2) 每个动作一行，带毫秒时间戳和关键参数（点了什么、算出的坐标是多少）
#   3) 每轮采集记判定依据（候选带、过色、识别、新增、画面差），能反推是哪层出的错
#   4) 未捕获异常（主线程 / 子线程 / Tk 回调）全部落盘
#   5) 超过 LOG_MAX 自动轮转，免得越跑越大发不出来

_LOG_TAG = {"INFO": "I", "WARN": "W", "ERR": "E"}

# 界面日志区的出口。App 建好后把它指到自己的 LogPane 上；纯命令行跑测试时
# 保持 None，日志照旧只落盘——不能让 UI 依赖渗进核心逻辑。
_UI_ECHO = None


def _echo(line: str) -> None:
    """有真控制台时顺手打一遍；pythonw 下 stderr 已被接到日志文件，别再重复写一次。"""
    try:
        if sys.stderr is not None and sys.stderr.isatty():
            print(line, file=sys.stderr)
    except Exception:
        pass
    hook = _UI_ECHO
    if hook is not None:
        try:
            level = "WARN" if " [W] " in line else "ERR" if " [E] " in line else "INFO"
            hook(line, level)
        except Exception:
            pass


def _rotate_log() -> None:
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX:
            bak = OUT_DIR / "操作日志.1.txt"
            if bak.exists():
                bak.unlink()
            LOG_PATH.rename(bak)
    except Exception:
        pass


def _stamp() -> str:
    return f"{datetime.now():%H:%M:%S.%f}"[:-3]


def log(tag: str, msg: str = "", level: str = "INFO") -> None:
    """写一条操作日志。tag 用能一眼看出位置的名字（lock/region/scan/roll…）。"""
    line = f"{_stamp()} [{_LOG_TAG.get(level, '?')}] {tag}: {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    _echo(line)


def log_exc(tag: str, exc: BaseException | None = None) -> None:
    """记完整 traceback。定位问题全靠这个，只留 str(exc) 是没用的。"""
    if exc is None:
        tb = traceback.format_exc()
    else:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{_stamp()} [E] {tag}: 异常\n")
            f.write("".join("    " + x + "\n" for x in tb.rstrip().splitlines()))
    except Exception:
        pass
    _echo(tb)


def log_start(extra: str = "") -> None:
    """启动分隔头 + 环境快照。

    `extra` 给宿主前端补一句自己的版本（客户端是 PySide6，旧工具不需要）。
    """
    _rotate_log()
    vers = []
    for pkg in ("rapidocr", "onnxruntime", "mss", "Pillow", "numpy"):
        try:
            m = __import__({"Pillow": "PIL"}.get(pkg, pkg))
            v = getattr(m, "__version__", None)
            if not v:
                from importlib.metadata import version as _v
                v = _v(pkg)
            vers.append(f"{pkg}={v}")
        except Exception as exc:
            # 冻结态查不到版本是正常的：PyInstaller 不打 .dist-info，而 rapidocr
            # 模块本身也没有 __version__。显示「内置」比显示一串报错类名有用——
            # 后者看着像缺件，实际它就在包里。真缺件的话下面加载模型时会立刻炸。
            vers.append(f"{pkg}={'内置' if getattr(sys, 'frozen', False) else '!' + type(exc).__name__}")
    try:
        scr = (ctypes.windll.user32.GetSystemMetrics(0),
               ctypes.windll.user32.GetSystemMetrics(1))
    except Exception:
        scr = ("?", "?")
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        dpi_txt = f" · DPI {dpi}（{dpi / 96:.2f}x）"
    except Exception:
        dpi_txt = ""
    log("start", "-" * 72)
    try:
        tkv = f" · Tk {tk.TkVersion}"
    except Exception:                 # 裁掉 tkinter 的发行包里也得能写日志
        tkv = ""
    log("start", f"启动 · Python {sys.version.split()[0]}{tkv} · "
                 f"屏幕 {scr[0]}x{scr[1]}{dpi_txt}")
    log("start", f"PID={os.getpid()} 提权={self_elevated()} 目录={APP_DIR}")
    log("start", " · ".join(vers))
    if extra:
        log("start", extra)
    log("start", "-" * 72)


def install_crash_hooks() -> None:
    """未捕获异常也落盘。pythonw 下没控制台，崩了就是"窗口凭空消失"。"""
    def hook(tp, val, tb):
        log("crash", "".join(traceback.format_exception(tp, val, tb)), "ERR")

    sys.excepthook = hook

    def thook(args):
        name = args.thread.name if args.thread else "?"
        log("crash-thread", f"{name}\n" + "".join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        ), "ERR")

    try:
        threading.excepthook = thook
    except Exception:
        pass


# ---------------------------------------------------------------- 模拟滚动

MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
WHEEL_DELTA = 120
VK_NEXT = 0x22
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002
GA_ROOT = 2

MAX_EMPTY = 3            # 连续几轮没有新人就判定到底
MOVE_EPS = 0.30          # 滚动前后画面差低于此值 = 画面没动（注入被丢弃或已到底）

# ---- 等画面停稳（替代"固定延时后截图"）
#
# 为什么不能用固定延时：拖动本身要 0.37s（16 小步 × 12ms + 按下/松开延时），
# 发完 LEFTUP 立刻算起 520ms，真正留给惯性的只有 150ms——而松手后的惯性
# 滑动实测 200~400ms 才停。于是在列表还在滑的中途就截了图，截到的是两个位置
# 叠加的中间态：行被拉花、名字串行，OCR 认出来的序列和上一屏对不上，
# judge_roll 就会误判成"漏人/跨屏"。
#
# 而悬停高亮动画会让画面永远"在变"，纯靠"连续帧相同"会等到超时。
# 所以还要配合把光标挪出采集区（见 park_cursor）——两个问题一起修。
SETTLE_MIN = 300         # 松手后先等这么久（惯性通常在这段时间里最大），再开始探稳定
SETTLE_POLL = 90         # 探稳定轮询间隔
SETTLE_STABLE_N = 2      # 连续几帧帧间差达标才算停稳
# 帧间差阈值（平均灰度差）。按实测量级定：真实游戏背景动效 1.45（Live2D/粒子
# 永远在动，所以不能像旧版那样要求 ≈0），滚动一行 40 以上。5.0 落在中间，
# 离两边都够远。
SETTLE_EPS = 5.0
SETTLE_TIMEOUT = 2200    # 最长等多久。惯性实测 200~400ms，正常 700ms 内就停
SCROLL_GAP = 240         # 本屏 OCR 采完到下一次拖动之间的空档，别紧接着滚
CURSOR_PARK_PAD = 28     # 停光标时离采集区留这么多余量（悬停热区比卡片本身大）

# 让列表往下走的三种方式。联盟成员列表实测不吃滚轮（1 格和 5 格画面差
# 都是 1.3x、名字一个没变），手游式 UI 通常要用"按住往上拖"。
# 顺序按成功率排：先拖动，再滚轮，最后翻页键。
SCROLL_MODES = ("drag", "wheel", "key")
SCROLL_MODE_CN = {"drag": "拖动", "wheel": "滚轮", "key": "翻页键"}
DRAG_PX_PER_STEP = 160   # 一"格"的基础拖动距离（没量到行距时的兜底）
DRAG_MAX = 420           # 单次拖动上限，避免一次拖过一整屏
MIN_DRAG_PX = 40         # 单次拖动下限，再小游戏会当成点击
PROBE_DRAG_PX = 64       # 「测试滚动」试探时拖多远（小步，别一上来就滑满一屏）
# 每次只滚 **1 行多一点**。原来定 2 行，理由是本屏 5 行还剩 3 行重叠、位移好测。
# 但实测这个列表的拖动响应是非线性的，多滚一步省下的时间远远抵不上一次跑飞。
TARGET_ROWS = 1.25
# 起手死区（touch slop）：手势起手的前十几像素游戏只用来区分「点击还是拖动」，
# 这段位移**不产生滚动**。这是本轮实测挖出来的核心事实，前面几轮改步长都没找对病：
#     拖 60px → 内容上移 42px   （少了 18）
#     拖 70px → 内容上移 57px   （少了 13）
#     拖 89px → 内容上移 71px = 正好 1 行
# 所以"拖多少像素就走多少像素"是错的。步长 71px 时真实位移只有 0.80 行，相位会
# 慢慢往后漂；漂到某一步正好跨不过行边界，名字就一个字不变——而旧逻辑把"名字不变"
# 直接当成"列表到底了"，于是 32 人的联盟只采到 6 个人就报"✅ 已到列表末尾"。
# 步长必须补上这段死区，否则"每次一行"根本不成立。
DRAG_SLOP = 15
# 单步拖动距离的硬上限，单位是"几个行距"（不含死区补偿）。
# 实测 143px 以上出现过一步跨掉整屏、两屏零重合，所以上限压在 1.6 行。
STEP_CAP_ROWS = 1.6
# 拖完松手游戏会带惯性再滑一段（实测 160px 的拖动滑出 284~355px 内容，1.8~2.2 倍），
# 且倍率不稳定：同一段距离有时走 4 行、有时 5 行。而每屏只有 5 行，一旦滑满一屏，
# 两屏就没有重合行，位移量只能量到"至少一屏"，漏没漏人在数据上完全分不出来。
# 所以不能猜固定步长，要闭环：拖动距离 = 目标行数 × 实测行距 ÷ 实测惯性倍率。
FLING_INIT = 2.0         # 惯性倍率初值（内容位移 ÷ 拖动距离）
FLING_MIN = 0.55         # 学到的倍率下限（实测最稳的值在 1.0 附近）
FLING_MAX = 1.60         # 学到的倍率上限：再大就是甩动，不能当成常态学进去
# 松手前在终点原地停这么久，让游戏侧采样到的速度归零。
# 这是「突然加速直接拉到底部」的根因：手游列表的惯性来自**松手瞬间的指针速度**，
# 原来移动一停 60ms 就松手，游戏仍把最后几步（约 250px/s）当成甩动，于是同一段
# 48px 的拖动，有时滑 142px、有时滑 284px——倍率在 2.9~5.9 之间乱跳，闭环调参
# 永远追不上，偶尔一步就滑过整屏，两屏没有重合行，中间漏没漏人就无从判断。
# 停够一个速度采样窗口（实测 200ms 足够，游戏侧窗口一般 100~150ms），甩动分量
# 消失，位移就约等于拖动距离，倍率稳定在 1.0 附近，步长才真正可控。
RELEASE_HOLD = 0.20
# 拖动速度：每步移动多少像素、每步之间等多久。**这两个值决定一切**——
# 250px/s（每步 3px/12ms）实测 5~10 步就会甩一次，松手后惯性把列表滑掉整整一屏；
# 降到 107~120px/s 连续跑 14~28 步零跳步，位移稳定在 87~91px。
# 慢的代价是每拖一次多花半秒，换来的是"一步就是一步"，这笔交易很划算。
DRAG_STEP_PX = 3
DRAG_STEP_GAP = 0.028
# 松手（LEFTUP）之后等这么久再允许移动光标。游戏处理抬起要晚一两帧，这期间
# 光标一动就被算成还在拖——把光标从拖动终点请到画面角落那 500 多像素，会被
# 当成一次大甩动，列表直接甩到最底部。实测这个时间窗是 1~2 帧，0.18s 足够。
RELEASE_SETTLE = 0.18
# 几步「两屏完全没有重合行」就停。原来定为 2，以为"到底时最后一屏本来就会换掉，
# 一次不算数"——实战证明这个宽容是错的：实测一次零重合就跨掉了十几个人，而下一步
# 立刻 stuck（到底了），2 这个门槛**根本没机会触发**，于是那份 20 人的残缺名单被当成
# "已到末尾"收工，还顺手判了 13 人退会。零重合本身就是"数据不可信"的信号，
# 停一次重跑的代价，远小于把错名单写进库。
JUMP_TOLERANCE = 1
# 跳步补采：一步跨掉整屏后，先往回走小步把跨过去的人重新读一遍，而不是直接
# 中止。实测这种跳位偶尔只漏 2 个人（落点每次都一样，像是列表自己跳了位置），
# 往回走五六步就能补齐，代价远小于整轮重跑。
#   JUMP_RECOVER_TIMES  一轮最多补采几次（防止来回打转）
#   JUMP_RECOVER_STEPS  一次补采最多往回走几步，走这么多还没接上就认输
JUMP_RECOVER_TIMES = 3
JUMP_RECOVER_STEPS = 12

# 按页面人数硬裁。页面上那个 `联盟成员 (32/32)` 是**唯一不依赖识别质量的硬事实**：
# 不管位置对齐有没有盲区、字面相似度够不够，联盟里就是 32 个人。所以采到 34 个
# 时不能只告警了事——一张"看着完整"的假名单进库，之后每轮都会跟着错。
#   MAX_TRIM         一轮最多裁掉几个。超出说明是别的问题（例如总数本身读歪了），
#                    靠丢名字掩盖只会更糟，宁可告警让人来看
#   TRIM_TOTAL_RANGE 页面人数的合理区间。那个数字也是 OCR 读出来的，一样会歪
MAX_TRIM = 5
TRIM_TOTAL_RANGE = (3, 200)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor_pos() -> tuple[int, int]:
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def set_cursor(x: int, y: int) -> None:
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def screen_rect() -> tuple[int, int, int, int]:
    u = ctypes.windll.user32
    return 0, 0, int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


def in_rect(region, x: int, y: int, pad: int = 0) -> bool:
    x0, y0, x1, y1 = region
    return (x0 - pad) <= x <= (x1 + pad) and (y0 - pad) <= y <= (y1 + pad)


def park_point(region, frame) -> tuple[int, int]:
    """挑一个「不在采集区里」的点来停光标。frame 是游戏有效画面（WinInfo 或 (x,y,w,h)）。

    为什么必须挪：拖动结束时指针正停在列表中央某个成员卡片上。光标停在
    名单上有两个后果——① 卡片悬停高亮动画让像素一直在变，"等画面停稳"永远
    等不到；② 白色箭头本身会压在昵称字上，被 OCR 当笔画认进去，一行名字断成
    两截。两者都不是"多等一会儿"能解决的，只能把光标请出去。

    候选点按「离采集区远、又在游戏窗口内」排。全部落在采集区里时（框得极宽）
    退回屏幕角落——此时不在窗口内也无所谓，因为滚轮/拖动都是先 SetCursorPos
    到目标点再发事件，跟光标当前位置无关。
    """
    if frame is None:
        frame = screen_rect()
    if hasattr(frame, "w"):                       # WinInfo
        fx, fy, fw, fh = frame.x, frame.y, frame.w, frame.h
    else:
        fx, fy, fw, fh = frame
    inset = 10
    cands = [
        (fx + inset, fy + inset),                    # 左上角
        (fx + fw - inset, fy + inset),               # 右上角
        (fx + inset, fy + fh - inset),               # 左下角
        (fx + fw - inset, fy + fh - inset),          # 右下角
        (fx + fw // 2, fy + 6),                      # 顶边中间
        (fx + fw - inset, fy + fh // 2),             # 右边中间
        (fx + inset, fy + fh // 2),                  # 左边中间
    ]
    sx0, sy0, sx1, sy1 = screen_rect()
    cands += [(sx0 + 2, sy0 + 2), (sx1 - 3, sy0 + 2),
              (sx0 + 2, sy1 - 3), (sx1 - 3, sy1 - 3)]
    for cx, cy in cands:
        if not (sx0 <= cx < sx1 and sy0 <= cy < sy1):
            continue
        if in_rect(region, cx, cy, CURSOR_PARK_PAD):
            continue
        return int(cx), int(cy)
    # 理论上到不了这——采集区不可能同时盖住四角和屏幕四角
    return int(sx0 + 2), int(sy0 + 2)


def park_cursor(region, frame) -> tuple[int, int] | None:
    """把光标挪出采集区。返回挪到哪了（原本就在区外则返回 None，不动它）。"""
    if region is None or frame is None:
        return None
    cx, cy = cursor_pos()
    if not in_rect(region, cx, cy, CURSOR_PARK_PAD):
        return None
    tx, ty = park_point(region, frame)
    set_cursor(tx, ty)
    log("cursor", f"光标在采集区内 ({cx},{cy}) → 挪到 ({tx},{ty})")
    return tx, ty


class SettleJudge:
    """「画面停稳了吗」的状态机。主线程和工作线程共用同一份判据。

    为什么不各写一份：判据改在这边忘了那边，会出现"测试全过、线上还是等满
    超时"这种最难查的偏差。抽成一个只吃帧、吐结论的小对象就不会。

    判据只有一条：帧间差 <= SETTLE_EPS。看起来单薄，但阈值是按**实测量级**定的：

        真实游戏背景动效（Live2D / 粒子）    1.45
        滚动一行的位移                        40+

    中间有近 30 倍的空隙，5.0 落在正中，两边都够不着。

    ## 为什么不用「与滚动前的总差不再变化」

    试过，不可行。总差会在"位移超过内容自身尺寸"之后**饱和**：假窗口里白条
    高 40px，位移 70px 和 90px 的总差都是同一个 102.00，于是惯性刚滑出半屏
    就被判成"停稳了"，截到的是中间态。真实列表滚过一屏后画面同样完全不重叠、
    总差同样饱和。饱和是 frame_diff 的固有性质，不能拿来当"停止"的证据。
    """

    def __init__(self, before: Image.Image | None = None):
        self.before = before    # 只用于日志里报"这次滚了多少"
        self.prev = None
        self.seq = 0            # 连续几帧帧间差达标
        self.total = None       # 最新一次「与滚动前的总差」
        self.max_step = 0.0     # 探测期间最大帧间差（超时用它说明画面还在动）

    def feed(self, cur) -> bool:
        """喂一帧，返回是否已停稳。"""
        if cur is not None:
            if self.before is not None:
                try:
                    self.total = frame_diff(self.before, cur)
                except Exception:
                    pass
            if self.prev is not None:
                try:
                    step = frame_diff(self.prev, cur)
                except Exception:
                    step = 0.0
                if step <= SETTLE_EPS:
                    self.seq += 1
                else:
                    self.seq = 0
                    self.max_step = max(self.max_step, step)
            self.prev = cur
        return self.seq >= SETTLE_STABLE_N


def wait_settled(region, before: Image.Image | None = None,
                 min_wait: float = SETTLE_MIN / 1000.0,
                 poll: float = SETTLE_POLL / 1000.0,
                 timeout: float = SETTLE_TIMEOUT / 1000.0):
    """阻塞版「等画面停稳」：返回 (最后一帧, 用时秒, 是否真的停稳)。

    给工作线程用（试滚、离线校验）。主线程不能 sleep，走 _poll_settle 的 after 版。
    """
    time.sleep(min_wait)
    t0 = time.time()
    judge = SettleJudge(before)
    cur = grab(region)
    while time.time() - t0 < timeout:
        time.sleep(poll)
        cur = grab(region)
        if judge.feed(cur):
            return cur, time.time() - t0, True
    return judge.prev if judge.prev is not None else cur, time.time() - t0, False


def window_at(x: int, y: int) -> int:
    """该点所在顶层窗口句柄——用来确认游戏仍在前台，避免把滚轮发到别的窗口。"""
    u = ctypes.windll.user32
    hwnd = u.WindowFromPoint(_POINT(int(x), int(y)))
    if not hwnd:
        return 0
    root = u.GetAncestor(hwnd, GA_ROOT)
    return int(root or hwnd)


def scroll_at(x: int, y: int, clicks: int = 1, up: bool = False) -> None:
    """鼠标移到 (x,y) 后滚动 clicks 格，默认向下（列表内容往上走）。

    up=True 是反向滚（列表往回走），导航收尾"拉回第一行"用得到。

    用 mouse_event 而非 PostMessage：多数游戏只认真实输入队列里的滚轮事件。

    注意：注入的输入要过 UIPI 检查——本进程权限低于游戏时会被**静默丢弃**，
    既不抛异常也不返回错误，表现就是"点了没反应、列表不滚"。
    所以调用方必须自己验证画面有没有动，不能假定调用成功就等于生效。
    """
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.04)
    delta = WHEEL_DELTA if up else -WHEEL_DELTA
    for _ in range(max(1, int(clicks))):
        u.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        time.sleep(0.03)


def drag_at(x: int, y_from: int, y_to: int, steps: int = 16,
            hold: float = RELEASE_HOLD, fast: bool = False) -> None:
    """按住(x,y_from)往上拖到(x,y_to)——手游式列表靠这个滚动。

    必须分很多小步移动：一次跳到终点会被识别成"点击"或直接被丢掉。

    速度是整个滚采里最要命的一个参数。**每步固定 DRAG_STEP_PX、每步间隔
    DRAG_STEP_GAP**，也就是速度恒定（约 107px/s），不跟着距离放大：

      · 每步 3px / 12ms（250px/s，早期版本）→ 实测 5~10 步就会甩一次，
        松手后惯性把列表滑掉整整一屏（两屏零重合，中间的人全丢）。
      · 每步 3px / 25ms（120px/s）  → 连续 14 步零跳步，位移稳定在 87~91px
      · 每步 2px / 30ms（67px/s）   → 连续 14 步零跳步，位移稳定在 88~91px

    到终点还要**原地停 hold 秒再松手**（见 RELEASE_HOLD）：移动一停就松手，
    游戏会把最后一步的速度当成甩动。停手时还要持续发事件——只停手不调
    SetCursorPos，游戏侧最后收到的样本仍是拖动途中那一步，速度依然很高。
    """
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y_from))
    time.sleep(0.05)
    u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    steps = max(2, int(steps))
    gap = DRAG_STEP_GAP * (0.45 if fast else 1.0)
    for i in range(1, steps + 1):
        u.SetCursorPos(int(x), int(round(y_from + (y_to - y_from) * i / steps)))
        time.sleep(gap)
    # 终点压住不动，但要**持续发事件**：原地 ±1px 抖动——像素上看不出位移，
    # 速度却已经掉到接近 0。
    ticks = max(1, int(round(hold / 0.05)))
    for k in range(ticks):
        u.SetCursorPos(int(x), int(y_to) + (1 if k % 2 else 0))
        time.sleep(hold / ticks)
    u.SetCursorPos(int(x), int(y_to))
    time.sleep(0.02)
    u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    # **松手之后必须先停一下，谁都别动光标。** 这是"突然猛的下滑到底部"的真凶：
    # 调用方紧跟着会把光标请出采集区（从拖动终点 (604,631) 瞬间跳到 (63,184)，
    # 横向 540px）。游戏侧处理 LEFTUP 要晚一两帧，这段时间里它仍然认为手指按着，
    # 这 540px 就被当成一次大甩动，列表一路甩到底。
    # 证据：跳步的落点每次都一模一样，而且正好是列表最后 5 行（也就是底部）；
    # 从第 4 行、第 5 行、第 12 行开始跳，落点全都相同——只有一个绝对位置
    # 才会这样，不是距离算错。
    time.sleep(RELEASE_SETTLE)


def press_page_down(times: int = 1) -> None:
    u = ctypes.windll.user32
    for _ in range(max(1, int(times))):
        u.keybd_event(VK_NEXT, 0, 0, 0)
        u.keybd_event(VK_NEXT, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.15)


def click_at(x: int, y: int, settle: float = 0.06) -> None:
    """在屏幕坐标上点一下。

    和拖动不同，点击不需要"归零速度"那一套——按下到抬起之间不移动，
    游戏只会看到一次按下+一次抬起，不会被判成甩动。但按下与抬起之间得
    留几十毫秒，贴得太近有些客户端会当抖动丢掉。
    """
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.06)
    u.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(max(0.02, settle))
    u.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(RELEASE_SETTLE)     # 同 drag_at：抬起后先别动光标


def tap_key(vk: int, times: int = 1, gap: float = 0.25) -> None:
    """敲一个键 times 次。导航里只用它发 ESC 清弹窗/退一层。"""
    u = ctypes.windll.user32
    for _ in range(max(1, int(times))):
        u.keybd_event(vk, 0, 0, 0)
        u.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(gap)


def scroll_once(mode: str, region, clicks: int = 1, dist: int | None = None,
                up: bool = False, fast: bool = False) -> None:
    """按指定方式让列表动一次。region 是屏幕坐标 (x0,y0,x1,y1)。

    dist 是拖动距离（像素）。拖动松手后游戏会带惯性再滑一段，所以"拖多远"
    和"滚几行"不是一回事——调用方按实测行距反推距离，才不会一步滑过整屏。

    up=True 是反向（列表往回走），导航收尾"拉回第一行"用得到。**反向必须走
    和控制列表时同一种输入**：联盟列表实测只吃拖动、不吃滚轮，拿滚轮去把它
    拉回顶部等于什么都没做，而且表现和"已经在顶部"一模一样，完全看不出来。
    """
    x0, y0, x1, y1 = region
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    if mode == "wheel":
        scroll_at(cx, cy, clicks, up=up)
    elif mode == "drag":
        if dist is None:
            dist = DRAG_PX_PER_STEP * max(1, clicks)
        dist = max(MIN_DRAG_PX, min(DRAG_MAX, int(dist)))
        if up:
            # 从上方起手往下拖：内容往回翻。回滚不在乎精度，要的是快，
            # 所以距离上限放到 DRAG_MAX（420px ≈ 6 行），比正常步长大得多
            y_from = y0 + max(24, (y1 - y0) // 8)
            y_to = min(y1 - 12, y_from + dist)
        else:
            # 起手点压在列表靠下位置，往上拖；留边距，别从最边缘起手
            y_from = y1 - max(24, (y1 - y0) // 8)
            y_to = max(y0 + 12, y_from - dist)
        # 步长按距离给：**固定每步 DRAG_STEP_PX**，不跟着距离放大。
        # 速度必须是常量，否则"拖多远"和"走几行"的关系会随距离变化——
        # 早期版本有个 36 步的上限，拖 143px 时被压成每步 4px，速度从 250px/s
        # 涨到 333px/s，正好越过甩动判定线，位移从 1 行变成 5 行以上。
        drag_at(cx, y_from, y_to,
                steps=max(12, int(abs(y_from - y_to) / DRAG_STEP_PX)),
                fast=fast)
    else:
        press_page_down(clicks)



# ---------------------------------------------------------------- 权限 / 前台
#
# 为什么单列一块：
#   游戏若以管理员身份启动、本工具不是，Windows 的 UIPI 会拦住
#   SetForegroundWindow 和 mouse_event。两者都**不报错**，于是外部表现
#   只是"点了没反应"。这种情况必须事前判定（给出明确提示）、事后验证
#   （比对滚动前后画面），否则程序只会误判成"已到列表底部"。

TOKEN_QUERY = 0x0008
TOKEN_ELEVATION = 20
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5

_K32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ADV = ctypes.WinDLL("advapi32", use_last_error=True)

# 句柄必须声明 c_void_p：ctypes 默认按 c_int 处理返回值，64 位下
# GetCurrentProcess() 的伪句柄 -1 会被传成 0x00000000FFFFFFFF，
# OpenProcessToken 直接失败，于是本进程提权状态恒为"未知"。
_K32.GetCurrentProcess.restype = ctypes.c_void_p
_K32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
_K32.OpenProcess.restype = ctypes.c_void_p
_K32.CloseHandle.argtypes = [ctypes.c_void_p]
_K32.CloseHandle.restype = ctypes.c_int
_K32.QueryFullProcessImageNameW.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
    ctypes.POINTER(ctypes.c_ulong),
]
_K32.QueryFullProcessImageNameW.restype = ctypes.c_int

_ADV.OpenProcessToken.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p),
]
_ADV.OpenProcessToken.restype = ctypes.c_int
_ADV.GetTokenInformation.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
    ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
]
_ADV.GetTokenInformation.restype = ctypes.c_int


class _TOKEN_ELEVATION_(ctypes.Structure):
    _fields_ = [("TokenIsElevated", ctypes.c_ulong)]


def _token_elevated(hproc) -> bool | None:
    tok = ctypes.c_void_p()
    if not _ADV.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(tok)):
        return None
    try:
        val = _TOKEN_ELEVATION_()
        size = ctypes.c_ulong()
        ok = _ADV.GetTokenInformation(
            tok, TOKEN_ELEVATION, ctypes.byref(val),
            ctypes.sizeof(val), ctypes.byref(size),
        )
        return bool(val.TokenIsElevated) if ok else None
    finally:
        _K32.CloseHandle(tok)


def self_elevated() -> bool | None:
    return _token_elevated(_K32.GetCurrentProcess())


def pid_of_window(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


def proc_name(pid: int) -> str:
    """进程可执行名——权限对比时给用户一个明确的对比对象。"""
    h = _K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_ulong(1024)
        if _K32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
    finally:
        _K32.CloseHandle(h)
    return ""


def window_elevated(hwnd: int) -> bool | None:
    """目标窗口所属进程是否提权；None = 判不出来（已退出或被保护）。

    OpenProcess 直接 ACCESS_DENIED 本身就是强信号：同权限级别的进程
    通常能拿到 QUERY_LIMITED_INFORMATION。
    """
    pid = pid_of_window(hwnd)
    if not pid:
        return None
    h = _K32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
            return True
        return None
    try:
        return _token_elevated(h)
    finally:
        _K32.CloseHandle(h)


def perm_advice(hwnd: int) -> str:
    """权限不对等时返回一句提示；对等则返回空串。

    只作**归因参考**，绝不能据此阻止采集：实测 mouse_event 这类真实输入
    注入不受 UIPI 限制，低权限进程照样能把滚轮送进高权限游戏。
    真正的判据是滚动前后画面差，不是权限表。
    """
    me = self_elevated()
    tgt = window_elevated(hwnd)
    if tgt is True and me is False:
        name = proc_name(pid_of_window(hwnd)) or "游戏"
        return (
            f"{name} 是管理员权限、本工具是普通权限。"
            f"实测这种组合滚轮仍可注入；若确认不动，可以把本工具也用管理员身份重开一次试试。"
        )
    return ""


def is_foreground(hwnd: int) -> bool:
    """目标窗口是否真的在前台。比对根窗口，游戏可能把前台给了自己的子窗口。"""
    u = ctypes.windll.user32
    fg = u.GetForegroundWindow()
    if not fg:
        return False
    root = u.GetAncestor(fg, GA_ROOT) or fg
    return int(root) == int(hwnd)


def _force_foreground(hwnd: int) -> None:
    """绕过 Windows 的前台锁。

    SetForegroundWindow 有条规则：只有当前前台进程才能切换前台，跨进程
    强切会被静默拒绝（这也是"我以为已经切过去了"的主要来源）。
    AttachThreadInput 把本线程输入队列挂到前台线程上可临时获得切换资格；
    再不行就模拟一次 Alt 键，解锁前台锁。
    """
    u = ctypes.windll.user32
    fg = u.GetForegroundWindow()
    tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = _K32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(u.AttachThreadInput(tid_fg, tid_me, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetFocus(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(tid_fg, tid_me, False)

    if not is_foreground(hwnd):
        VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
        u.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.03)
        u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.06)
        u.SetForegroundWindow(hwnd)


def focus_window(hwnd: int, tries: int = 3) -> bool:
    """把目标窗口提到前台，返回**是否真的成功**。

    用户是点工具按钮启动的，此刻工具窗口在前台、游戏在后台。
    多数游戏失焦时不响应滚轮，所以必须先让游戏窗口回到前台。
    返回值必须检查——SetForegroundWindow 失败是不报错的。
    """
    u = ctypes.windll.user32
    for _ in range(max(1, tries)):
        if is_foreground(hwnd):
            return True
        try:
            u.ShowWindow(hwnd, 9)          # SW_RESTORE
            u.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if is_foreground(hwnd):
            return True
        try:
            _force_foreground(hwnd)
        except Exception:
            pass
        if is_foreground(hwnd):
            return True
        time.sleep(0.12)
    return is_foreground(hwnd)


# ---------------------------------------------------------------- 窗口锁定
#
# 采集区不以「屏幕绝对坐标」保存，而是以「占有效画面的百分比」保存。
# 有效画面 = 客户区再去掉游戏自己加的黑边，也就是真正渲染 16:9 的那块矩形。
# 好处：窗口挪位置、换大小、换显示器比例，只要画面还是 16:9 就不用重标定。
# 前提：有效画面宽高比必须一致——NIKKE 的 UI 锚点跟着画面比例走，
#       比例一变（例如 16:9 拖成 3:2），UI 会重排，百分比坐标就失效了。

ASPECT_TOL = 0.01      # 宽高比容差 ±1%
MIN_CLIENT_H = 400     # 客户区高度下限：再小 UI 缩得过细，单行字高不足 8px
AUTO_LOCK_RETRY = 2000  # 自动接窗口的重试间隔（毫秒）
AUTO_LOCK_GIVEUP = 5    # 连续这么多次接不上，才放出「手动选择」兜底（约 10 秒）
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80

# 黑边（letterbox / pillarbox）检测。
#
# 窗口比例不是 16:9（拉成 16:10、放上 21:9 显示器）时，游戏会把 16:9 画面
# 居中显示，两侧或上下留纯黑。这时拿「客户区百分比」当坐标会整体偏移——
# 画面变窄 1%，昵称列的实际位置就偏 1% × 客户区宽。所以坐标基准必须是
# 有效画面（客户区去掉黑边），不是客户区。
_BAR_MEAN = 14.0       # 黑边列/行的平均亮度上限
_BAR_STD = 7.0         # 且必须"均匀地黑"：UI 深色区通常有渐变和高光
_BAR_SIDE = 0.40       # 单侧最多裁 40%：再多说明不是黑边，是整块深色 UI

TARGET_ASPECT = 16 / 9

# 自动锁窗只认这两个特征：标题像游戏，或进程名像游戏。
# 单靠标题不可行——实测终端标题里带 Nikke 路径也会命中。
_GAME_TITLE = ("胜利女神", "goddess of victory", "nikke")
_GAME_PROC = ("nikke", "胜利女神")

# 这些是系统窗口，混进列表只会干扰选择
_SKIP_TITLES = {
    "Program Manager", "Default IME", "MSCTFIME UI",
    "Windows Input Experience", "Windows 输入体验",
    "Microsoft Text Input Application", "NVIDIA GeForce Overlay",
}


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class WinInfo:
    """一个窗口的客户区快照。x/y 是客户区左上角在屏幕上的物理像素。

    刻意不用 dataclass：测试脚本用 importlib 动态加载本文件时模块不在
    sys.modules 里，dataclass 解析注解会直接抛 AttributeError。
    """

    def __init__(self, hwnd: int, title: str, x: int, y: int, w: int, h: int):
        self.hwnd = hwnd
        self.title = title
        self.x = x
        self.y = y
        self.w = w
        self.h = h

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 0.0

    def describe(self) -> str:
        return f"{self.w}×{self.h} · {self.aspect:.3f}"

    def __repr__(self) -> str:
        return f"WinInfo({self.title!r}, {self.describe()}, @({self.x},{self.y}))"


def window_title(hwnd: int) -> str:
    u = ctypes.windll.user32
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def client_rect(hwnd: int) -> WinInfo | None:
    """取客户区矩形。失败（窗口没了/最小化）返回 None。

    必须用 GetClientRect + ClientToScreen，不能用 GetWindowRect：
    后者含标题栏和边框，实测多出横向 8px、纵向 31px，坐标一偏整列名字就截歪。
    """
    u = ctypes.windll.user32
    if not hwnd or not u.IsWindow(hwnd):
        return None
    r = _RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    w, h = int(r.right - r.left), int(r.bottom - r.top)
    if w <= 0 or h <= 0:
        return None
    pt = _POINT(0, 0)
    u.ClientToScreen(hwnd, ctypes.byref(pt))
    # 最小化时 ClientToScreen 返回 -32000，客户区尺寸变成 0×0
    if pt.x <= -30000 or pt.y <= -30000:
        return None
    return WinInfo(int(hwnd), window_title(hwnd), int(pt.x), int(pt.y), w, h)


def _trim_bars(img: Image.Image, axis: int) -> tuple[int, int]:
    """沿一个方向去掉两侧黑边，返回 (起, 止) 开区间。

    axis=0 看列（左右黑边），axis=1 看行（上下黑边）。
    判据要同时满足"很暗"和"很均匀"：UI 里的深色面板通常有渐变、描边、
    高光，方差远大于 7，不会被当成黑边误裁。
    """
    a = np.asarray(img.convert("L"), dtype=np.float32)
    prof = a if axis == 0 else a.T
    mean = prof.mean(axis=0)
    std = prof.std(axis=0)
    n = len(mean)
    limit = max(2, int(n * _BAR_SIDE))

    lo, hi = 0, n
    while lo < limit and mean[lo] <= _BAR_MEAN and std[lo] <= _BAR_STD:
        lo += 1
    while hi > n - limit and mean[hi - 1] <= _BAR_MEAN and std[hi - 1] <= _BAR_STD:
        hi -= 1
    return lo, hi


def content_box(img: Image.Image) -> tuple[int, int, int, int] | None:
    """找出真正游戏画面的矩形 (x0,y0,x1,y1)。没有黑边返回 None。

    先切左右再切上下：letterbox 的黑条是整宽的，先切列不影响行判定，
    但反过来会——两侧黑边被算进行均值后，本来不认识的黑边也会变"暗"。
    """
    x0, x1 = _trim_bars(img, 0)
    if x1 - x0 < img.width * 0.5:
        return None
    sub = img.crop((x0, 0, x1, img.height))
    y0, y1 = _trim_bars(sub, 1)
    if y1 - y0 < img.height * 0.5:
        return None
    if (x0, y0, x1, y1) == (0, 0, img.width, img.height):
        return None

    # 最后一道闸：裁完必须「更像 16:9」，否则这两条不是黑边。
    # 必要性来自实测——游戏窗口被另一个深色程序挡住时，截图拿到的是遮挡物，
    # 它的深色边框很容易满足"又暗又均匀"，照裁下去坐标就整体偏了。
    # 而真有黑边时，裁掉的比例一定更接近 16:9，这条判据几乎不可能误伤。
    a0 = img.width / img.height
    a1 = (x1 - x0) / (y1 - y0)
    if abs(a1 - TARGET_ASPECT) >= abs(a0 - TARGET_ASPECT):
        return None
    return int(x0), int(y0), int(x1), int(y1)


def content_rect(hwnd: int) -> WinInfo | None:
    """有效画面：客户区去掉黑边后的 16:9 内容区。

    这是全局唯一的坐标基准——region_rel、截图、点击换算全部走它的百分比。
    截图失败（窗口刚最小化等）时退回客户区，不让整条链路断掉；
    真正的比例校验留给调用方，不要在这里静默放过。
    """
    info = client_rect(hwnd)
    if info is None:
        return None
    try:
        box = content_box(grab((info.x, info.y, info.x + info.w, info.y + info.h)))
    except Exception:
        box = None
    if box is None:
        return info
    x0, y0, x1, y1 = box
    return WinInfo(hwnd, info.title, info.x + x0, info.y + y0, x1 - x0, y1 - y0)


def frame_size(hwnd: int) -> tuple[int, int]:
    """窗口外框尺寸（含标题栏和边框）。**只用于显示和诊断，坐标一律不用它。**

    坐标必须走 `client_rect` / `content_rect`：外框比客户区多出标题栏和边框
    （实测横向 +8px、纵向 +31px），拿它换算整列名字都会截歪。
    """
    u = ctypes.windll.user32
    r = _RECT()
    if not u.GetWindowRect(hwnd, ctypes.byref(r)):
        return 0, 0
    return int(r.right - r.left), int(r.bottom - r.top)


# 常见显示比例档位：(叫法, 参考值, 容差)。只用来把 1.778 说成"16:9"，
# 不参与任何判定。21:9 的容差放宽到 ±3%：这个叫法本身就不精确，
# 2560×1080（2.370）、3440×1440（2.389）、2560×1080 全是"21:9"。
_ASPECT_BANDS = (("16:9", 16 / 9, 0.01), ("16:10", 16 / 10, 0.01),
                 ("3:2", 3 / 2, 0.01), ("21:9", 21 / 9, 0.03),
                 ("4:3", 4 / 3, 0.01), ("9:16", 9 / 16, 0.01))


def aspect_label(a: float, tol: float = ASPECT_TOL) -> str:
    """把宽高比说成人话。认不出档位就直接报小数，不硬凑。"""
    if not a:
        return "?"
    for name, ref, band in _ASPECT_BANDS:
        if abs(a - ref) / ref <= max(band, tol):
            return name
    return f"{a:.3f}"


def _rank(info: WinInfo) -> tuple:
    """排序权重。只影响列表顺序，不影响判据——最终由用户点选确认。

    注意不能只按「标题含 nikke」排：实测 `F:\\Codex\\Nikke\\...` 这类终端标题
    也会命中，反而把真游戏挤下去。所以标题像路径的一律降到末尾。
    """
    t = info.title.strip()
    low = t.lower()
    pri = 3
    if "胜利女神" in t or "goddess of victory" in low:
        pri = 0                                  # 国服官方标题
    elif "nikke" in low:
        pri = 1
    if "\\" in t or "/" in t or (len(t) > 2 and t[1] == ":"):
        pri = 4                                  # 路径样标题：终端/编辑器
    return (pri, -info.w * info.h)


def enumerate_windows(skip_pid: int | None = None) -> list[WinInfo]:
    """枚举可采集的顶层窗口，最像游戏的排前面。

    skip_pid 默认排除本进程——否则工具自己的窗口会因为标题含 NIKKE
    混进候选，用户误选后每轮采集都会截到自己。
    """
    u = ctypes.windll.user32
    skip_pid = os.getpid() if skip_pid is None else skip_pid
    found: list[WinInfo] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _lp):
        h = int(hwnd)
        if not u.IsWindowVisible(h) or u.IsIconic(h):
            return True
        if u.GetWindow(h, GW_OWNER):            # 有 owner 的多是对话框
            return True
        if u.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        pid = ctypes.c_ulong()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pid.value == skip_pid:
            return True
        title = window_title(h).strip()
        if not title or title in _SKIP_TITLES:
            return True
        info = client_rect(h)
        if info is None or info.w < 200 or info.h < 200:
            return True
        found.append(info)
        return True

    u.EnumWindows(cb, 0)
    found.sort(key=_rank)
    return found


def game_score(info: WinInfo) -> int:
    """这个窗口像不像游戏：0 表示不像，越大越像。

    进程名比标题可靠（标题可能被改），所以进程命中给更高权重。
    """
    hit = 2 if any(k in info.title.lower() for k in _GAME_TITLE) else 0
    try:
        proc = (proc_name(pid_of_window(info.hwnd)) or "").lower()
    except Exception:
        proc = ""
    if proc and any(k in proc for k in _GAME_PROC):
        hit += 3
    return hit


def find_game_window() -> tuple[WinInfo | None, list[WinInfo]]:
    """自动找游戏窗口。返回 (唯一命中, 候选列表)。

    判据三层，必须同时满足才进"命中"：像游戏（标题/进程）× 有效画面是 16:9
    × 高度够识别。不满足 16:9 的单独收集，用来把"为什么没找到"讲清楚——
    直接静默失败的话，用户只会看到"点锁定窗口"这句废话。

    只有唯一命中才自动锁定：两个都像游戏时自动选错，代价是采出一份
    看着正常、实际全是错的名单，比让人点一下贵得多。
    """
    good, near = [], []
    for w in enumerate_windows():
        if not game_score(w):
            continue
        c = content_rect(w.hwnd)
        if c is None or c.h < MIN_CLIENT_H:
            continue
        if abs(c.aspect - TARGET_ASPECT) / TARGET_ASPECT <= ASPECT_TOL:
            good.append((game_score(w), c.w * c.h, c))
        else:
            near.append(c)

    good.sort(key=lambda t: (-t[0], -t[1]))
    if len(good) == 1:
        return good[0][2], [g[2] for g in good]
    return None, [g[2] for g in good] + near


def obscurer(region: tuple[int, int, int, int], hwnd: int) -> str:
    """采集区被别的窗口盖住时，返回盖住它的窗口标题；没被盖住返回空串。

    窗口句柄有效、客户区矩形正确、截图尺寸正确，截到的内容仍可能是遮挡物
    （输入法候选框、Steam/Discord 覆盖层、置顶聊天窗）。这一步专门拦它。
    """
    x0, y0, x1, y1 = region
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    top = window_at(cx, cy)
    if top == hwnd:
        return ""
    if top == 0:
        return "桌面"
    t = window_title(top).strip()
    return t or f"窗口 0x{top:X}"


# ---------------------------------------------------------------- 截图

def grab(region: tuple[int, int, int, int] | None = None) -> Image.Image:
    """region = (x0, y0, x1, y1)，物理像素。None 表示主屏全屏。"""
    import mss

    with mss.mss() as sct:
        if region is None:
            mon = sct.monitors[1]
        else:
            x0, y0, x1, y1 = region
            mon = {"left": x0, "top": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}
        raw = sct.grab(mon)
        return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def frame_diff(a: Image.Image, b: Image.Image) -> float:
    """两帧的平均灰度绝对差（0~255），用来判断画面到底动没动。

    完全静止时约 0.00；滚过一行昵称通常 3 以上。
    """
    if a.size != b.size:
        return 255.0
    x = np.asarray(a.convert("L"), dtype=np.int16)
    y = np.asarray(b.convert("L"), dtype=np.int16)
    return float(np.abs(x - y).mean())


def match_rows(before, after, tol=0.8):
    """用两屏的名字序列对齐，算这次滚动实际走了几行。

    返回 (滚过的行数, 重合行数)；名字太少判不出来时返回 (None, 0)。

    为什么不用像素位移：列表各行字高一致，位移满一个行距会形成周期性混叠，
    滚 3 行会被量成滚 1 行。名字序列没这个问题——它唯一确定"第几行对第几行"。
    tol 容忍个别行 OCR 认错。
    """
    if len(before) < 2 or len(after) < 2:
        return None, 0
    n = min(len(before), len(after))
    for k in range(n, 0, -1):
        hit = sum(1 for x, y in zip(before[-k:], after[:k]) if x == y)
        if hit >= max(1, int(round(k * tol))):
            return len(before) - k, k
    return len(before), 0


def plan_drag(pitch, slop=None, clicks: int = 1) -> int:
    """算出这一拖该拖多少像素。

    目标行数 × 实测行距 = 希望内容移动的像素；**再加回起手死区**，
    因为前十几像素被游戏当成"区分点击和拖动"丢掉了，不产生滚动。
    拖完松手游戏还会带惯性再滑一段，所以这只是个基准值，靠闭环修。
    """
    p = float(pitch or 71.0)
    s = DRAG_SLOP if slop is None else max(0.0, float(slop))
    want = TARGET_ROWS * p * max(1, int(clicks))
    cap = p * STEP_CAP_ROWS
    # 两件事都不能少：
    # ① 不再除以惯性倍率。松手前已把指针速度压到 0（RELEASE_HOLD），倍率稳定在
    #    1.0 附近，剩下的抖动是纯噪声，除进去反而算出 40px 这种"有时走一行、
    #    有时原地不动"的步长。
    # ② 必须加回死区补偿。少了它步长恒等于行距，真实位移只有 0.8 行，相位往后
    #    漂到某一步就跨不过行边界，表现为"名字不变"→ 被误判成到底、提前收工。
    return max(MIN_DRAG_PX,
               min(DRAG_MAX, int(round(cap)) + int(round(s)),
                   int(round(want)) + int(round(s))))


def judge_roll(prev_ordered, cur_ordered):
    """判定这一滚走到了哪。返回 (滚过行数, 重合行数, 结论, 说明)。

    名字序列是唯一可靠的位移证据：画面差会把鼠标悬停的高亮当成滚动
    （实测 1.354），而拖动又几乎不改画面差。但序列对齐有个天花板——
    两屏没有重合行时，位移量只能量到"至少一屏"，"刚好一屏"和"跳过几行"
    在数据上完全一样，只能停下来把话说清楚。

    结论：
      none   上一屏没有数据，这一滚不参与判定
      ok     量出了精确位移，且滚过的行数 ≤ 本屏行数（两屏是连着的，没漏）
      jump   两屏没有重合行，位移量只能量到"至少一屏" → 连续多次才停
      stuck  名字一个没变（也没新人）→ 或到底了，或该换一种滚动方式
    """
    if not prev_ordered or not cur_ordered:
        return None, 0, "none", ""
    rows, overlap = match_rows(prev_ordered, cur_ordered)
    if rows is None:
        return None, 0, "none", ""
    if rows == 0:
        return 0, overlap, "stuck", "名字一个都没变"
    if overlap == 0:
        # 这里曾经用"本屏行数比上一屏少 ⇒ 确实跨过了中间的人"当证据，是错的：
        # 行数变少至少有三种原因——列表到底了那一屏本来就不满、个别行 OCR 没认
        # 出来（实测「马三一」被横笔间隙切成碎片整行丢掉）、真的跳过了人。
        # 三者从行数上分不出来，所以不能拿它下结论，只如实说"量不到"。
        note = ("两屏没有重合行，位移量只能量到「至少一屏」——"
                "中间有没有漏人，从名字序列上分不出来")
        return rows, 0, "jump", note
    return rows, overlap, "ok", ""


def measure_vshift(a: Image.Image, b: Image.Image):
    """量出两帧之间列表内容纵向移动了多少像素（正数=内容向上移动）。

    frame_diff 只能回答"动没动"，回答不了"动了多少"。这里把每行压成一个
    笔迹强度标量（行内像素标准差，有字的行大、空白行小），得到一维序列，
    再找让两段序列相关性最高的位移。

    返回 (位移像素, 相关性)。相关性低于 0.3 说明画面变化不是整体平移
    （比如列表被换掉了），此时位移值不可信。
    """
    if a.size != b.size:
        return None, 0.0
    ra = np.asarray(a.convert("L"), dtype=np.float32).std(axis=1)
    rb = np.asarray(b.convert("L"), dtype=np.float32).std(axis=1)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    h = len(ra)

    best_s, best_c = 0, -1.0
    for s in range(0, h // 2 + 1):
        # 内容上移 s 像素 ⇒ 后帧的第 i 行约等于前帧的第 i+s 行
        x, y = (ra, rb) if s == 0 else (ra[s:], rb[:-s])
        den = float(np.sqrt((x * x).sum() * (y * y).sum()))
        if den <= 1e-6:
            continue
        c = float((x * y).sum()) / den
        if c > best_c:
            best_s, best_c = s, c
    return best_s, best_c


def measure_vshift_signed(a: Image.Image, b: Image.Image, max_ratio: float = 0.5):
    """同 measure_vshift，但**带方向**：正 = 内容上移，负 = 内容下移。

    上面那个只遍历 s >= 0（内容上移）。向上滚列表时内容往下走，最优解在负半轴，
    永远取不到，只能退化成 0 —— 与"画面没动"完全无法区分。
    实测（困难样张，内容下移 2 行＝240px）：旧函数返回 `0px · 相关 0.62`，
    新函数返回 `-240px · 相关 0.94`。
    """
    if a.size != b.size:
        return None, 0.0
    ra = np.asarray(a.convert("L"), dtype=np.float32).std(axis=1)
    rb = np.asarray(b.convert("L"), dtype=np.float32).std(axis=1)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    h = len(ra)
    lim = max(1, int(h * max_ratio))

    best_s, best_c = 0, -1.0
    for s in range(-lim, lim + 1):
        # 内容上移 s（s>0）⇒ 后帧第 i 行约等于前帧第 i+s 行
        if s > 0:
            x, y = ra[s:], rb[:-s]
        elif s < 0:
            x, y = ra[:h + s], rb[-s:]
        else:
            x, y = ra, rb
        den = float(np.sqrt((x * x).sum() * (y * y).sum()))
        if den <= 1e-6:
            continue
        c = float((x * y).sum()) / den
        if c > best_c:
            best_s, best_c = s, c
    return best_s, best_c


def check_region(region, img=None):
    """框完先体检，把"这个框拍出来会出什么问题"当场说清楚。

    不做自动裁剪：实测把框收窄到文字块反而会让带级文字色被判错（框越宽，
    带内混进的头像暗部越多，颜色中位数越偏），越修越差。框只要保证
    "整列昵称都在框内"，位置大小差几像素不影响识别。

    返回 (识别行数, 提示列表)。
    """
    try:
        if img is None:
            img = grab(region)
        found, bands, passed = scan(img)
    except Exception as exc:
        return 0, [f"试拍失败：{exc}"]

    notes = []
    if len(found) <= 2:
        notes.append(f"只认出 {len(found)} 行昵称，框可能没套住昵称列")
    if len(bands) >= 3 and passed < len(bands) * 0.5:
        notes.append(f"{len(bands)} 个候选带里只有 {passed} 个是昵称色——框里混进了头像或横幅")
    if bands and (bands[0][0] < 3 or bands[-1][1] > img.height - 4):
        notes.append("首行/末行贴着框边，可能被切掉半行")
    if img.width > img.height:
        notes.append("框是横的，昵称列应该是竖长条")

    return len(found), notes


def _measure_pitch(img: Image.Image):
    """从区域截图量出 (行距, 行数)，量不出返回 (None, 0)。

    手动框选这条路没有结构保证，但行距照样能从候选带量出来——
    有了它，自动滚采的步长就有初值，不用每次从"猜 2 倍惯性"开始试。

    注意必须先按文字色过滤：ROI 里除了昵称，还有头像边框、分隔线这些横带。
    它们的间距跟行距毫无关系，一起参与拟合会把中位数拉到行距的一半以下
    （实测 356px 高的五行列表被量成 39px，真实值 71px）。
    """
    try:
        bands = find_bands(img)
    except Exception:
        return None, 0
    rows = []
    for y0, y1 in bands:
        c = row_text_color(img, y0, y1, 0, img.width)
        if c is None or not (TEXT_COLOR[0] <= c <= TEXT_COLOR[1]):
            continue
        rows.append(((y0 + y1) // 2, y0, y1))
    keep = fit_rows(rows)
    if len(keep) < 2:
        return None, len(keep)
    ys = sorted(r[0] for r in keep)
    pitch = float(np.median(np.diff(ys)))
    # 兜底：行距 × 行数 应该大致铺满整块 ROI，差太多说明拟合落到了子格点上
    if img.height and len(keep) >= 3:
        est = img.height / len(keep)
        if est > 0 and abs(pitch - est) / pitch > 0.35:
            pitch = est
    return pitch, len(keep)


# ---------------------------------------------------------------- OCR

_engine = None
_engine_lock = threading.Lock()


def get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR

            _engine = RapidOCR()
        return _engine


def find_bands(img: Image.Image, min_height: int = 6, min_pixels: int = 3,
               merge_gap: int = 5):
    """纵向投影切候选文本带。

    投影只用"昵称笔迹"条件：低饱和(<25)、非纯黑(>=40)、中深灰(<150)。
    这样头像的彩色区和极黑描边不参与投影，框选稍宽也不至于把行间空白吃掉。

    merge_gap：把间隙 ≤ merge_gap 像素的相邻碎片合成一条带。
    为什么必须合并：昵称里「三 / 一 / 二 / 王 / 丰」这类字全由横笔组成，
    投影在两个横笔之间会掉到阈值以下，一行字被切成 2~3px 的碎片，再被
    min_height 整段丢掉。实测「马三一」就是这么被吞的——碎片是
    207-208 / 211-212 / 214-217，每块都不到 6px，整行在日志里彻底消失，
    名单里也就少了这一个人。而行间空白实测 18px 以上，合并 5px 以内的
    间隙不可能把两行连成一条带。
    """
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    ink = ((mx - mn) < 25) & (mx >= 40) & (mx < 150)
    proj = ink.sum(axis=1)
    thr = max(min_pixels, proj.max() * 0.05)

    # 先按阈值切出连续的"有笔迹"段，允许段内因为横笔间隙短暂回落
    raw, start, last = [], None, None
    for y, v in enumerate(proj):
        if v >= thr:
            if start is None:
                start = y
            elif y - last > merge_gap:      # 间隙太大 → 前面那段到此为止
                raw.append((start, last))
                start = y
            last = y
    if start is not None:
        raw.append((start, last))
    return [(s, e) for s, e in raw if e - s + 1 >= min_height]


_NOISE = re.compile(r"(最近登录|小时前|分钟前|刚刚|联盟成员|挑战者|^[\d\s/()×xX.]+$)")
_NICK_OK = re.compile(r"^[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7afA-Za-z0-9_.\-·\s]{2,14}$")


def plausible(text: str) -> bool:
    t = (text or "").strip()
    if not t or _NOISE.search(t):
        return False
    if not _NICK_OK.match(t):
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z]", t):
        return False
    if re.search(r"\d{4,}", t):          # 昵称与右侧积分粘连
        return False
    return True


def script_sig(s: str) -> str:
    """每个字符属于哪种书写系统：C=汉字 K=假名 L=英文字母 D=数字 O=其它。

    用来挡一个真实存在的坑：`命OVO` 和 `DORO` 等长、相似度正好 0.5，
    光看相似度会被当成同一行的两种读法；可它们一个是汉字+字母、一个是纯字母，
    字形系统完全不同，绝不可能是同一个字被读歪的两种结果。
    """
    out = []
    for ch in s:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF:
            out.append("C")
        elif 0x3040 <= o <= 0x30FF:
            out.append("K")
        elif ch.isascii() and ch.isalpha():
            out.append("L")
        elif ch.isdigit():
            out.append("D")
        else:
            out.append("O")
    return "".join(out)


def script_ok(a: str, b: str) -> bool:
    """两个拼写的书写系统是否逐位相容；标点（O）位豁免。

    原先要求签名逐位**完全相等**，于是横笔字被读成短横时（`马三一`→`马三-`，
    CCC vs CCO）同一行被当成两个人：真机一轮里同一个人被拆成
    `马三一`/`马三-`/`马二一`/`马三` 四条，回放后 35 人（页面 32）。
    标点不构成书写系统差异，跳过它再比。

    该挡的照样挡住：`命OVO`(CLLL) 与 `DORO`(LLLL) 首字就是 C vs L。
    """
    for x, y in zip(script_sig(a), script_sig(b)):
        if x == "O" or y == "O":
            continue
        if x != y:
            return False
    return True


def same_row(a: str, b: str, ratio: float = 0.5) -> bool:
    """两个拼写是不是同一条昵称的两种读法。

    昵称会在相邻两屏里各被读一次，字面不一致几乎全是 OCR 抖动：实测
    「非洲晒太阳」被读成「非洲吧太阳」（0.917）、「命OVO」被读成「命UVU」
    （0.775）、「空木」被读成「空本」。如果不认这一点，名单就会凭空多出人。

    调用方的证据是**位置**（同一物理行），这里只是安全阀：万一滚动位移判偏了
    一行，同位置的两个人会被拿来比，相似度不够就分开——宁可名单多一个人，
    也不能把两个真成员并成一个（那是丢数据，比多算严重得多）。

    两道条件：书写系统逐位一致 + 等长 + 相似度 ≥ 阈值。加书写系统那一道是因为
    相似度对短名字不可靠：`命OVO` vs `DORO` 恰好 0.5，`萧白` vs `英白` 也是 0.5，
    前者是两个不同的人，后者是同一行读歪。书写系统能把它俩分开。
    """
    if a == b:
        return True
    if not a or not b or len(a) != len(b):
        return False
    if not script_ok(a, b):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= ratio


def vote_spelling(members, hits, seen):
    """把一组"同一行的多种读法"逐位投票成共识拼写，投不出就返回 None。

    每个位置按"该写法被读到的屏数"加权取多数字符。短读（「马三」把末尾的
    「一」丢了）不投这一位，但它的屏数记成"缺席票"：缺席票压过字符票时说明
    真名确实短，这里直接判"投不出"，交给调用方按老办法处理——宁可不动，也不
    硬补一个字出来。

    实测（2026-11-06 真机，53 屏同一行）：
        马三一 3 屏 · 马三- 3 屏 · 马二一 1 屏 · 马三 1 屏
    取"屏数最多"会平票（马三一 vs 马三-），投票则逐位得 马/三/一 → 正确还原。
    """
    ms = [m for m in members if m]
    if len(ms) < 2:
        return None
    lens = {len(m) for m in ms}
    if max(lens) - min(lens) > 1:        # 长短差超过一个字符，不像同一行的抖动
        return None
    out = []
    for i in range(max(lens)):
        votes, absent = {}, 0
        for m in ms:
            w = max(hits.get(m, 0), 1)
            if i < len(m):
                votes[m[i]] = votes.get(m[i], 0) + w
            else:
                absent += w
        if not votes:
            return None
        top = max(votes.values())
        winners = [c for c, v in votes.items() if v == top]
        if len(winners) > 1:
            # 平票先看书写系统：昵称以标点收尾远少于以汉字/字母收尾，而"横笔字被
            # 读成短横"正是最常见的误读方向（马三一 → 马三-）。只在平票时用，
            # 不平票时不动。
            solid = [c for c in winners if script_sig(c) != "O"]
            if len(solid) == 1:
                winners = solid
        if len(winners) > 1:             # 仍并列，用置信度打破
            score = {c: sum(seen.get(m, (0.0, ""))[0]
                            for m in ms if i < len(m) and m[i] == c) for c in winners}
            hi = max(score.values())
            winners = [c for c, v in score.items() if v == hi]
        if len(winners) > 1 or top < absent:
            return None                  # 并列未决，或被缺席票压过
        out.append(winners[0])
    res = "".join(out)

    # 投票结果必须和组内每个读法都对得上，否则说明这组本来就不是一个人。
    # 短读（「马三」把末尾「一」丢了）长度不等，same_row 会直接否掉，
    # 这里按"是共识串的前缀或后缀"放行——漏读只会发生在两端。
    def ok(m):
        if len(res) == len(m):
            return same_row(res, m)
        return bool(m) and (res.startswith(m) or res.endswith(m))

    return res if all(ok(m) for m in ms) else None


def rarer(a: str, b: str, hits) -> bool:
    """a 被读到的屏数是不是明显少于 b —— 用来判断哪个拼写才是读歪的那次。

    读歪是偶发（实测全部 303 屏里 25 次，1~3%），读对是每屏都对，所以真错的
    那种写法在整趟采集里会差一个量级：「非洲晒太阳」113 屏 vs「非洲吧太阳」1 屏。
    要求"少一半以上"才敢判，是为了防住两个都常常出现的近名真人被并成一个——
    丢一个真成员比多一个假名字严重得多。
    """
    return hits.get(a, 0) * 2 <= hits.get(b, 0)


# ---------------------------------------------------------------- 昵称库
#
# 公会名单有个天然性质：成员几乎不变，一轮下来只有一两个人进出。所以"上次
# 采到的那批名字"对这一次就是最硬的先验——同一个人在同一台机器、同一套字体
# 渲染下，读歪的方式也是固定的（实测「命OVO」偶尔读成「命UVU」、「空木」偶尔
# 读成「空本」、「博麗霊夢」偶尔读成「博麗需夢」）。
#
# 这一层补的是 `_merge_misreads` 的盲区：位置合并要求"同一行在相邻两屏都出现
# 过"，可一屏里最上面那一行往往只被扫到一次（下一滚就出去了），读歪了就没有
# 第二次机会。实测顶行误读率 2.7%，是其余行的近两倍。库比对不需要第二屏，
# 一次就能救。
#
# 安全阀：只在库里**恰好有一个**候选和它对得上时才归。库里同时存在「萧白」
# 和「英白」这种近名真人时，模糊匹配会同时命中两个 → 放弃，原样保留，
# 交回位置合并去处理。删真成员比留假名严重。

import booklib                                # 多套库：成员库/BOSS库/活动套（v1.3）

ROSTER = APP_DIR / "预设" / "昵称库.json"       # 旧单库路径，仅迁移与回落用
ROSTER_DROP = 2          # 连续这么多轮完整采集都没见到，才认为真的退会了
ROSTER_MIN_LEN = 3       # 短于这个长度的昵称不做模糊归档（见 roster_fix）
# 库归位比 same_row 严一档。same_row 判的是"两屏同一物理行上的两个读法"，
# 有位置这个硬证据兜底，0.5 就够；库归位没有位置证据，全靠字面猜，门槛必须更高。
ROSTER_RATIO = 0.62
# 库里这条至少稳定采到过这么多轮，才允许它把别人的读法"吸"过来。
# 只有 1 轮的条目（比如本轮刚加进来的新人）一律不参与——否则两个新名字
# 一撞上，谁吸谁都是猜。
ROSTER_ROUNDS_MIN = 2
# 轮次变动只留最近这么多轮。24 轮 ≈ 公会半年的人员进出，够回答"最近谁走了"，
# 又不至于把 json 撑大（每轮几十字节，一万轮也就几百 KB，但没必要留全史）。
ROSTER_HISTORY_MAX = 24


def load_roster() -> dict:
    """读**活动套**成员库（v1.3 多库）。文件不在 / 坏了都当空库，绝不挡采集。

    第一次调用时顺带做一次迁移：旧 `昵称库.json` → `成员库/默认.json`。
    迁移幂等，索引存在就直接跳过。
    """
    try:
        booklib.ensure_migrated()
        p = booklib.roster_path()
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("names"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "rounds": 0, "names": {}}


def save_roster(data: dict) -> bool:
    """写回**活动套**成员库，并刷新索引里的人数/更新时间。"""
    try:
        booklib.ensure_migrated()
        p = booklib.roster_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        idx = booklib.load_index()
        name = idx.get("active_roster") or "默认"
        idx.setdefault("rosters", {})[name] = {
            "people": len(data.get("names") or {}),
            "updated": data["updated"],
            "note": str(data.get("note") or ""),
        }
        booklib.save_index(idx)
        return True
    except Exception as exc:
        log_exc("roster-save", exc)
        return False


def roster_history(roster: dict) -> list:
    """把库里的轮次变动记录规整成"从新到旧"。

    为什么要规整而不是直接读：这个文件用户可能手改过、也可能是旧版本写的，
    一行脏记录不该让界面打不开。所有字段都给默认值，坏元素直接丢掉。
    """
    raw = roster.get("history")
    if not isinstance(raw, list):
        return []
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        try:
            out.append({
                "t": str(e.get("t") or ""),
                "n": int(e.get("n") or 0),
                "total": int(e.get("total") or 0),
                "ok": bool(e.get("ok")),
                "add": [str(x) for x in (e.get("add") or []) if x],
                "gone": [str(x) for x in (e.get("gone") or []) if x],
                "drop": [str(x) for x in (e.get("drop") or []) if x],
                "alias": int(e.get("alias") or 0),
            })
        except Exception:
            continue
    return out[::-1]                 # 存的是时间顺序，看的是最新在上


def churn_headline(roster: dict) -> dict:
    """退会判定的输入：最近一轮、最近一个完整轮、当前连续没见到的人、改名嫌疑。

    抽成纯函数是为了能脱开界面单测——这条判据一旦出错（把没来的人当成退会，
    或者把退会的人留在库里），界面上是看不出来的。
    """
    rows = roster_history(roster)
    names = roster.get("names") if isinstance(roster.get("names"), dict) else {}
    last_ok = next((r for r in rows if r["ok"]), None)
    # 连续没见到的：只认库里的 miss，且必须完整轮才累加（见 _commit_roster）
    watch = []
    for n, e in names.items():
        if not isinstance(e, dict):
            continue
        m = int(e.get("miss") or 0)
        if m >= 1:
            watch.append((n, m, str(e.get("last") or "")))
    watch.sort(key=lambda x: (-x[1], x[0]))
    add = list((last_ok or {}).get("add") or [])
    gone = list((last_ok or {}).get("gone") or [])
    return {
        "rounds": int(roster.get("rounds") or 0),
        "n": len(names),
        "last": rows[0] if rows else None,
        "last_ok": last_ok,
        "head": rows[:1],
        "rows": rows,
        "add": add,
        "gone": gone,
        "drop": list((last_ok or {}).get("drop") or []),
        "watch": watch,
        # 一进一出恰好各一个：公会里改名远比"真的走一个又来一个"常见，
        # 而库是按名字认人的，改名在库里就是"旧的退了、新的来了"。只提示，不自动合并。
        "rename_hint": (len(add) == 1 and len(gone) == 1
                        and not (last_ok or {}).get("drop")),
    }


def roster_cand(text: str, names, alias=None, floor: float | None = None) -> tuple:
    """查昵称库：这个拼写最可能对应库里哪一条。返回 (候选名 or None, 相似度)。

    **只查、不决定、不改字。** 库是补充，不是判据：
      · 它绝不参与 `same_row` / `rarer` / 位置合并那套"两个相似昵称是不是
        同一个人"的判定——那些只认 OCR 原文和同一物理行的位置证据。
      · 调用方（`App._roster_gate`）还必须先把所有其他证据都排除掉才有资格用
        这里的结果，条件写在那边。

    三道条件，缺一不表态：
      1) 库里没有字面完全相同的条目（有就是命中，不用纠）
      2) 等长 + 书写系统逐位一致 + 相似度 ≥ ROSTER_RATIO（比 same_row 严）
      3) 够格的候选**恰好只有一个** —— 库里真有近名成员时，猜哪个都错

    alias 是历史上确认过的别名表 {读歪的写法: 正确写法}，命中直接给满分，
    优先级高于模糊比对（那是前几轮或人工坐实过的）。
    """
    if not text or not names:
        return None, 0.0
    if alias and text in alias and alias[text] in names:
        return alias[text], 1.0
    if text in names:
        return text, 1.0
    # 两字名不做模糊归档：`空本`/`空木` 是同一行读歪，`大白`/`萧白` 却是两个
    # 真人，相似度都正好 0.5，光凭字面分不开。两字名的误读交给位置合并。
    if len(text) < ROSTER_MIN_LEN:
        return None, 0.0
    floor = ROSTER_RATIO if floor is None else floor
    # list() 拍快照：识别在工作线程里跑，主线程收工时可能正在往库里增删
    cands = []
    for n in list(names):
        if not same_row(text, n):
            continue
        r = difflib.SequenceMatcher(None, text, n).ratio()
        if r >= floor:
            cands.append((r, n))
    if len(cands) == 1:
        return cands[0][1], cands[0][0]
    return None, 0.0


def roster_fix(text: str, names, alias=None) -> tuple:
    """`roster_cand` 的"直接采用"包装：返回 (采用的名字, 库里的原名或 None)。

    注意它**不负责判断该不该采用**——那是 `App._roster_gate` 的事。这里只把
    一个候选翻译成"换成谁"。单独留着是为了查表类调用（诊断、测试）方便。
    """
    c, _r = roster_cand(text, names, alias)
    if c is None or c == text:
        return text, None
    return c, c


def confuse_pairs(items) -> dict:
    """把昵称库当"字库"读：反推这套字体爱把哪个字读成哪个字。

    `items` 可以是 `{名字: 记录}` 也可以是名字的可迭代对象。返回
    `{(读歪的字, 正确的字): 次数}`。

    数据来源只有一处：库里每条记录攒下的 `alias`（历史上坐实的误读写法）。
    能逐位对齐才提取——长度不等或书写系统不同的一律跳过，那种情况下"第 3 个
    字错了"这种说法本身就无意义。

    这是个**只读的旁证**：它不进 `same_row`，不进 `rarer`，不进 `roster_cand`
    的门槛，也不进任何合并/归位决定。它唯一的用途是让提示语能说清楚
    "这个差异像不像本机字体的老毛病"，剩下的交给人判断。
    """
    out = {}
    if isinstance(items, dict):
        seq = list(items.items())
    else:
        seq = [(n, {}) for n in (items or ())]
    for name, e in seq:
        al = (e.get("alias") or ()) if isinstance(e, dict) else ()
        for bad in al:
            if not bad or bad == name or len(bad) != len(name):
                continue
            if script_sig(bad) != script_sig(name):
                continue
            for x, y in zip(bad, name):
                if x != y:
                    out[(x, y)] = out.get((x, y), 0) + 1
    return out


def confuse_delta(a: str, b: str, pairs) -> tuple:
    """逐位比 a 和 b：有几处差异是这台机器见过多次的、有几处是头一回见。

    返回 `(已知处数, 陌生处数)`；没法逐位比（长度或书写系统不同）时返回
    `(0, -1)`。同样只用于措辞，不参与判定。
    """
    if not a or not b or len(a) != len(b) or script_sig(a) != script_sig(b):
        return 0, -1
    known = unknown = 0
    for x, y in zip(a, b):
        if x == y:
            continue
        if (x, y) in pairs or (y, x) in pairs:
            known += 1
        else:
            unknown += 1
    return known, unknown


def confuse_note(a: str, b: str, pairs) -> str:
    """把 `confuse_delta` 说成人话，接在提示语后面（以 " · " 开头，没有内容就返回空串）。

    三档：差异全是老易混 / 全是没见过的 / 一半一半。最后落地成
    "更像本机字体的老毛病"还是"更像两个真人"，但这只是建议措辞，
    名字一个都不动。
    """
    known, unknown = confuse_delta(a, b, pairs)
    if unknown < 0 or not (known or unknown):
        return ""
    if not pairs:
        # 冷启动：库里还没攒下任何误读写法。这时候说"更像两个真人"是误导——
        # 工具手上根本没有这张字表，只是没证据，不是有反证。
        return f" · 有 {unknown} 处差异，但库里还没攒下易混字记录，字面判断不了"
    if not unknown:
        hit = []
        for x, y in zip(a, b):
            if x != y:
                hit.append(f"{y}↔{x}")
        return (f" · 差异全在已知易混字上（{'、'.join(hit)}），"
                f"像这台机器字体的老毛病")
    if not known:
        return f" · 有 {unknown} 处差异从没见过，更像两个真人"
    return f" · {known} 处是已知易混、{unknown} 处没见过，字面说不准"


def read_line(img: Image.Image, box, upscale: int = UPSCALE):
    crop = img.crop(box)
    if upscale != 1:
        crop = crop.resize(
            (crop.width * upscale, crop.height * upscale), Image.Resampling.LANCZOS
        )
    r = get_engine()(crop, use_det=False, use_cls=False, use_rec=True)
    txts = list(r.txts or [])
    scores = list(r.scores or [])
    if not txts:
        return "", 0.0
    return str(txts[0]).strip(), float(scores[0]) if scores else 0.0


def lattice(rows, pitch=(50, 140), tol=4):
    """只保留落在同一等距格点上的行——联盟行高固定，横幅文字不在格点上。"""
    ys = [r[0] for r in rows]
    if len(ys) < 3:
        return rows
    best = []
    cands = sorted({round(b - a) for a, b in zip(ys, ys[1:]) if pitch[0] <= b - a <= pitch[1]})
    for d in cands:
        for y0 in ys:
            chain = [r for r in rows if any(abs(r[0] - (y0 + k * d)) <= tol for k in range(64))]
            if len(chain) > len(best):
                best = chain
    return best if len(best) >= 3 else rows


def name_span(img: Image.Image, y0: int, y1: int, pad_x: int = 6, gap: int = 25):
    """在候选带内定位昵称的实际左右边界。

    依据实测：昵称笔画列"有字列数"占带高 30% 以上且列最暗值落在 40~145；
    头像区笔画稀疏（1~4 列高）、右侧"最近登录"最暗约 155（不满足 <145）。
    框选过宽时靠这一步把 ROI 收回到昵称本身。
    """
    if y1 <= y0:
        return None
    a = np.asarray(img.convert("RGB")).astype(np.int16)[y0:y1 + 1]
    h = a.shape[0]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    ink = ((mx - mn) < 30) & (mx >= 40) & (mx < 145)
    cnt = ink.sum(axis=0)

    idx = np.where(cnt >= max(2, int(h * 0.30)))[0]
    if len(idx) < 3:
        return None

    # 按间隙分组，取最宽的一组（容纳昵称内部的空格）
    groups, cur = [], [idx[0]]
    for prev, cur_i in zip(idx, idx[1:]):
        if cur_i - prev <= gap:
            cur.append(cur_i)
        else:
            groups.append(cur)
            cur = [cur_i]
    groups.append(cur)
    best = max(groups, key=lambda g: g[-1] - g[0])

    x0, x1 = int(best[0]), int(best[-1])
    if x1 - x0 < img.width * 0.08:      # 太窄，判失败，退回原框
        return None
    return max(0, x0 - pad_x), min(img.width, x1 + pad_x)


TEXT_COLOR = (50.0, 115.0)      # 昵称笔迹的灰阶落点，实测 62~84


def band_text_color(img: Image.Image, y0: int, y1: int):
    """估计该行的文字色：取最暗 2% 的低饱和像素中位数。

    实测这个值对昵称极稳定（1x 69~84、2x 62~84），而玩家横幅因为
    白字/黑描边/彩底，算出来是 5、9、15、35 或直接无值。用它做行级过滤，
    比像素密度可靠得多——密度会被缩放插值产生的灰阶污染。
    """
    if y1 <= y0:
        return None
    a = np.asarray(img.convert("RGB")).astype(np.int16)[y0:y1 + 1]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    vals = np.sort(mx[(mx - mn) < 25])
    if len(vals) < 20:
        return None
    return float(np.median(vals[:max(10, len(vals) // 50)]))


def scan(img: Image.Image, debug_dir: Path | None = None, pitch=(50, 140)):
    """返回 ([(y, 昵称, 置信度)...], 候选带列表, 通过文字色过滤的行数)。

    pitch 是格点拟合允许的行距区间。默认值对应 1316×740 左右的窗口；
    大窗口行距会到 130+，标定预览里要按实测值传进来，否则整屏都会被当噪声剔掉。

    **这里只做 OCR，一个字都不改。** 昵称库（`roster_cand`）不参与识别，也不
    参与后续的位置合并——库是补充，一旦在识别阶段就把字改掉，后面所有判定
    （同一行两种读法、两个相似昵称是不是同一个人）都会建立在假证据上。
    归位放到 `App._roster_gate`，那时其他证据都已经判完了。
    """
    bands = find_bands(img)
    rows = []
    passed = 0
    for i, (y0, y1) in enumerate(bands):
        h = y1 - y0 + 1
        # 横幅行的文字色不在昵称区间，直接跳过，不进识别
        c = band_text_color(img, y0, y1)
        if c is None or not (TEXT_COLOR[0] <= c <= TEXT_COLOR[1]):
            continue
        passed += 1
        pad_y = max(2, int(h * 0.35))
        cy0, cy1 = max(0, y0 - pad_y), min(img.height, y1 + pad_y)
        span = name_span(img, y0, y1) or (0, img.width)
        # 横向再放宽 8px。name_span 的笔迹判据是 mx<145，而汉字的收尾横笔
        # （「三一」最后那一横）颜色偏淡、抗锯齿后灰度上到 150+，会被判成
        # 背景从右边界切掉——实测「马三一」正好切在「一」中间，OCR 只吐
        # 「马三」。放宽这一点点，代价是把相邻几个像素的背景带进来，无碍。
        sx0, sx1 = max(0, span[0] - 8), min(img.width, span[1] + 8)
        box = (sx0, cy0, sx1, cy1)
        text, score = read_line(img, box)
        if debug_dir is not None:
            img.crop(box).save(debug_dir / f"band{i:02d}_{y0}-{y1}.png")
        if score < MIN_SCORE or not plausible(text):
            continue
        rows.append(((y0 + y1) // 2, text, round(score, 3)))
    rows.sort()
    return lattice(rows, pitch=pitch), bands, passed


# ---------------------------------------------------------------- 页面导航
#
# 工具只服务一个客户端，导航就不做"通用点击器"，而是把路径写死成状态机：
#
#     主页 —点「联盟」→ 联盟概览 —点「队员」→ 成员列表
#
# 三条铁律（宁可停下来，也绝不盲点）：
#   1. 点之前必须先证明"我现在确实在这一页"——页指纹命中 ≥ NAV_MIN_HITS 才允许点
#   2. 只允许在锚点白名单矩形里点。抽卡、充值、退出这类按钮永远不在白名单里，
#      所以哪怕坐标算错也点不到付费项
#   3. 点完必须验证页面真的变了。没变就停在这一步报出来，绝不"再点一次试试"——
#      在错误的页面上重复点击，是唯一可能造成实际损失的动作
#
# 为什么用 OCR 文字而不是像素模板：改版改一个图标就能让模板全废，而按钮上的
# 「联盟」「队员」这几个字几乎不会变。代价是一次全屏 OCR（0.3~0.8 秒），放在
# 几步导航里完全够用。
#
# 坐标一律是 `content_rect` 的比例（0~1），不是像素：窗口缩放后还能用，也不必
# 为每种分辨率各存一套坐标。

NAV_MIN_HITS = 2         # 页指纹至少命中几个词才认"在这一页"
NAV_WAIT = 0.55          # 点完等界面响应
NAV_ENTER_WAIT = 1.30    # 进联盟有加载动画，多给点时间
NAV_ESC_TRIES = 2        # 页面对不上时按 ESC 清弹窗的次数上限
NAV_MIN_SCORE = 0.45     # 全屏 OCR 置信度下限
NAV_SETTLE_TRIES = 6     # 点完之后最多等几个 0.25s 去看结果
NAV_TOP_TRIES = 30       # 收尾把列表拉回第一行，最多滚几次
NAV_TOP_STILL = 2        # 连续几次"画面没动"算到顶


class NavStep:
    """一步导航：在哪一页、点哪个词、允许点哪里、OCR 认不出时用哪个坐标兜底。

    同 WinInfo，刻意不用 dataclass：测试脚本用 importlib 动态加载本文件时
    模块不在 sys.modules 里，dataclass 解析注解会直接抛 AttributeError。
    """

    def __init__(self, key, name, probe, target, area, preset,
                 wait=NAV_WAIT, need=None, min_hits=NAV_MIN_HITS):
        self.key = key
        self.name = name           # 给用户看的页面名
        self.probe = tuple(probe)  # 这一页必须能认出的词（该页独有的）
        self.target = target       # 要点的按钮/图标上的文字
        self.area = area           # 锚点白名单 (l, t, r, b)，占有效画面比例
        self.preset = preset       # (x, y) 实测锚点，OCR 认不出字时用
        self.wait = wait
        self.need = need           # 点完应该进入的页的指纹（None = 终点，另验）
        self.min_hits = min_hits   # 这一页要命中几个词才算"确实在"

    def __repr__(self):
        return f"NavStep({self.key!r} → 点「{self.target}」)"


# preset / area 的来源：用户 1236×725 的整窗截图（含 31px 标题栏、左右各 4px
# 边框），裁出 1228×694 的有效画面后，直接量红框中心 + 实测 OCR 框中心。
# 实测 OCR 自己认出来的位置和这两个数相差不到 0.02，互为验证。
NAV_SPEC = (
    NavStep("home", "主页",
            # 指纹只用大字：右侧竖排图标下面那行「任务/好友/联盟」实测在
            # 10px 上下，全屏 det 时有时能认有时认不出（这次认出来了，但不能
            # 指望），所以不拿它当指纹。底部栏和几个入口是 16~20px，很稳。
            # 刻意不用「商店」：联盟页有「联盟商店」，会把两页搅在一起。
            probe=("方舟", "大厅", "物品栏", "队员招募", "作战出击", "MISSION PASS"),
            target="联盟",
            # 只框住右侧竖排那一列。上界必须压到 0.23 以下：再往上就是
            # MISSION PASS 横幅（中心 0.928,0.163）和「即将开启」，框进来
            # 就等于允许点它们
            area=(0.935, 0.230, 1.000, 0.560),
            preset=(0.970, 0.418),
            need=("日志", "联盟公告", "联盟功能", "联盟射击场", "联盟商店"),
            # 主页可命中的词多，门槛提到 3：少一个词还能走，乱认三个词的概率很低
            min_hits=3),
    NavStep("guild", "联盟概览",
            # 「主菜单」实测会被读成「主选单」（两者差一个字符，`in` 判不中），
            # 所以不写它。这五个词在这张截图上全部命中，区分度也够。
            probe=("日志", "联盟公告", "联盟功能", "联盟射击场", "联盟商店", "退出"),
            target="队员",
            # 只套「队员」这一个标签。上界 0.23 是硬要求：再往上就把顶端那排
            # 「钻石 1552210」圈进来了（中心 0.610,0.201），那是挂着数量显示的
            # 资源入口。左右也收窄到不含「主选单」「日志」，少一个能点的东西
            # 就少一分误点风险
            area=(0.440, 0.230, 0.560, 0.330),
            preset=(0.500, 0.278),
            wait=NAV_ENTER_WAIT),
)


def text_norm(s: str) -> str:
    """比对用的归一化：全角转半角、去掉所有空白、统一大写。

    OCR 出来的「ＭＩＳＳＩＯＮ　ＰＡＳＳ」和「Mission Pass」在字面上差得远，
    归一化之后才是一回事。只用于比对，绝不写回名单。
    """
    out = []
    for ch in str(s or ""):
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:        # 全角 ASCII 区
            ch = chr(o - 0xFEE0)
        elif o == 0x3000:                # 全角空格
            ch = " "
        if not ch.isspace():
            out.append(ch.upper())
    return "".join(out)


def hit_words(texts, words) -> int:
    """texts 里命中了几个词。词是"该页独有"的，命中越多越确定。"""
    pool = [text_norm(t) for t in (texts or ()) if t]
    n = 0
    for w in words:
        k = text_norm(w)
        if k and any(k in t for t in pool):
            n += 1
    return n


def nav_which(texts, spec=None):
    """判断当前在哪一页。返回 (NavStep 或 None, 命中数)。

    都达不到该页自己的门槛就返回 None——"判断不出来"比"猜一个"安全得多，
    调用方拿到 None 就该停手，而不是找个最近的坐标点下去。
    """
    spec = NAV_SPEC if spec is None else spec
    best, best_n = None, 0
    for st in spec:
        n = hit_words(texts, st.probe)
        if n > best_n:
            best, best_n = st, n
    if best is None or best_n < best.min_hits:
        return None, best_n
    return best, best_n


# 场景打分表：(key, 显示名, 指纹词, 判定门槛)。
# 和 NAV_SPEC 分开写是故意的：NAV_SPEC 管"怎么走过去"，这张表只管"现在在哪"，
# 所以它可以包含没有点击动作的页面（成员页就是终点，不需要 target）。
SCENE_SPEC = (
    ("home", "主页", NAV_SPEC[0].probe, 3),
    ("guild", "联盟概览", NAV_SPEC[1].probe, 3),
    # 成员页没有稳定的大标题（实测整页最稳的字样是「联盟成员」和右上角的 (n/m)），
    # 所以门槛压到 1：命中「联盟成员」即认。
    ("members", "联盟成员页", ("联盟成员",), 1),
)


def classify_scene(texts, spec=None):
    """把"现在在哪一页"从布尔判断升级成打分，返回每页一行判定结果。

    这是照 ArcanaHelper 的 Recognizer.classify 学的：它的日志不是"识别成功/失败"，
    而是逐页列出 `命中率=0.9396/0.5,误差=0.0267,成功=True,原因=…`。好处是出问题时
    能看出"差多少"——只给一个 False，谁也说不清是差一个词还是整页都不对。

    返回 [{key,name,hit,need,ratio,err,ok,reason}, …]，按命中率降序。
    """
    spec = SCENE_SPEC if spec is None else spec
    pool = [text_norm(t) for t in (texts or ()) if t]
    out = []
    for key, name, probe, need in spec:
        hit = sum(1 for w in probe if text_norm(w) in "".join(pool))
        ratio = hit / float(len(probe) or 1)
        ok = hit >= need
        out.append({
            "key": key, "name": name, "hit": hit, "need": need,
            "ratio": ratio, "err": 1.0 - ratio, "ok": ok,
            "reason": "指纹命中率通过" if ok else "指纹命中率不足",
        })
    out.sort(key=lambda d: (-d["ratio"], d["key"]))
    return out


def scene_line(rows) -> str:
    """把 classify 的结果压成一行日志，格式对齐阿卡客户端的写法。"""
    return "；".join(f"{d['name']}:命中率={d['ratio']:.4f}/{d['need']},"
                    f"误差={d['err']:.4f},成功={d['ok']},原因={d['reason']}"
                    for d in rows)


def in_area(x, y, area) -> bool:
    l, t, r, b = area
    return l <= x <= r and t <= y <= b

def pick_target(blocks, word, area):
    """在 OCR 结果里找目标词，且文字中心必须落在白名单内。

    blocks = [(文本, (x0,y0,x1,y1) 比例框, 置信度)]
    返回 (x, y, 置信度) 或 None。**"找到词"和"允许点"是同一件事**，不给
    调用方留"先找到再说、边界另外查"的口子。
    """
    key = text_norm(word)
    if not key:
        return None
    best = None
    for text, (x0, y0, x1, y1), score in blocks:
        if key not in text_norm(text):
            continue
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        if not in_area(cx, cy, area):
            continue
        if best is None or float(score) > best[2]:
            best = (cx, cy, float(score))
    return best


def need_ok(texts, words, spec=None) -> bool:
    """"点完之后该到的那一页，到了吗"。

    门槛用**目标页自己的 min_hits**，不是全局默认值——主页要求 3 个词，
    拿 3 去卡联盟页（它只配了 2）会平白多失败几次。
    """
    if not words:
        return True
    spec = NAV_SPEC if spec is None else spec
    for st in spec:
        if tuple(st.probe) == tuple(words):
            return hit_words(texts, words) >= st.min_hits
    return hit_words(texts, words) >= NAV_MIN_HITS


def nav_skip_step(step, texts) -> bool:
    """这一步是不是"已经过了"，该跳过不重复点。

    必须带 `step.need` 判断：最后一步（成员页前那一跳）没有"点完该到哪"，
    `need_ok(texts, None)` 按定义返回 True。少了这个 and，最后一步会被恒定
    判成"已经过了"直接跳过——表现就是"点了联盟，但没点队员"，而且日志还会
    写"已经过了「联盟概览」这一关"，看着特别合理，非常难查。
    """
    return bool(step.need) and need_ok(texts, step.need)


def nav_decide(step, blocks, texts):
    """决定这一步的落点。返回 (x, y, 依据) 或 (None, None, 拒绝原因)。

    纯函数——不碰鼠标、不碰窗口，所以能直接喂假的 OCR 结果做单测。
    """
    n = hit_words(texts, step.probe)
    if n < step.min_hits:
        return None, None, (f"看不出在「{step.name}」（页指纹只命中 {n} 个词，"
                            f"至少 {step.min_hits} 个才敢点）")
    got = pick_target(blocks, step.target, step.area)
    if got is not None:
        x, y, sc = got
        return x, y, f"OCR 认出「{step.target}」@({x:.3f},{y:.3f}) 置信 {sc:.2f}"
    x, y = step.preset
    if not in_area(x, y, step.area):
        return None, None, (f"锚点 ({x:.3f},{y:.3f}) 落在白名单 "
                            f"{step.area} 之外，拒绝点击")
    return x, y, f"OCR 没认出「{step.target}」，退回实测锚点 ({x:.3f},{y:.3f})"


def read_screen(img: Image.Image, min_score: float = NAV_MIN_SCORE, rel: bool = True):
    """整幅画面 OCR，返回 [(文本, 框, 置信度)]。

    与 scan 的分工：scan 是"先切行、再逐行 rec"，只认昵称那一条竖列；这里是
    det+rec 全图，用来读 UI 上的按钮文字。两者用的同一个引擎实例，不会重复加载。
    """
    r = get_engine()(img, use_det=True, use_cls=True, use_rec=True)
    # 三个返回值可能是 tuple、list 或 numpy 数组，统一转 list：
    # 直接 `or ()` 会在 numpy 数组上炸（truth value of an array is ambiguous）
    def _lst(name):
        v = getattr(r, name, None)
        return list(v) if v is not None else []

    txts, boxes, scores = _lst("txts"), _lst("boxes"), _lst("scores")
    W = float(img.width) or 1.0
    H = float(img.height) or 1.0
    out = []
    for i, t in enumerate(txts):
        if not str(t or "").strip():
            continue
        sc = float(scores[i]) if i < len(scores) else 1.0
        if sc < min_score:
            continue
        if i >= len(boxes):
            continue
        pts = np.asarray(boxes[i], dtype=float).reshape(-1, 2)
        if not pts.size:
            continue
        x0, y0 = float(pts[:, 0].min()), float(pts[:, 1].min())
        x1, y1 = float(pts[:, 0].max()), float(pts[:, 1].max())
        if rel:
            out.append((str(t).strip(), (x0 / W, y0 / H, x1 / W, y1 / H), sc))
        else:
            out.append((str(t).strip(), (x0, y0, x1, y1), sc))
    return out


# ---------------------------------------------------------------- 标定推导
#
# 这是替代「手画框」的那一层。手画框的根本问题不是画得糙，而是**不可复现**：
# 同一个意图画两次，落点差 10~30px、高度差半行，之后所有滚动判据都建在
# 这个随机量上。所以 ROI 不该由人手给，应该由程序从画面结构里量出来：
#
#   1) 用户只在任意一行昵称上点一下 —— 这是唯一定位意图，不需要精度
#   2) 点所在文本带 → 该行精确上下沿（吸附，手抖被吃掉）
#   3) 全画面切候选文本带，用文字色把横幅/标题/按钮滤掉
#   4) 等距格点拟合求行距，剔掉不在格点上的噪声带
#   5) y 范围 = 首行上沿 → 末行下沿；x 范围 = 各名字左右边界的并集
#
# 结果只跟画面内容有关，跟点在哪一行的哪个字上无关 —— 这才叫可复现。


def ink_groups(img: Image.Image, y0: int, y1: int, gap: int = 25):
    """带内按列投影切出连续的「有字列」分组，返回 [(x0,x1), ...]。

    与 name_span 同一套笔迹判据（低饱和、非纯黑、中深灰），区别是这里
    返回全部分组——因为要在整幅画面上找，必须靠点击位置挑出昵称那一组，
    而不能像 name_span 那样直接取"最宽的一组"（整幅画面上最宽的可能是 UI）。
    """
    if y1 <= y0:
        return []
    a = np.asarray(img.convert("RGB")).astype(np.int16)[y0:y1 + 1]
    h = a.shape[0]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    ink = ((mx - mn) < 30) & (mx >= 40) & (mx < 145)
    cnt = ink.sum(axis=0)

    idx = np.where(cnt >= max(2, int(h * 0.30)))[0]
    if len(idx) < 3:
        return []

    groups, cur = [], [int(idx[0])]
    for prev, nxt in zip(idx, idx[1:]):
        if int(nxt) - int(prev) <= gap:
            cur.append(int(nxt))
        else:
            groups.append((cur[0], cur[-1]))
            cur = [int(nxt)]
    groups.append((cur[0], cur[-1]))
    return groups


def fit_rows(rows, tol: float = 6.0):
    """用「行距中位数」做等距拟合，剔掉不在格点上的带。

    为什么不直接复用 lattice：它只在"实际观测到的相邻差值"里挑行距。
    不同昵称的字形上下伸不一样（有 descender 的行高 2px、没有的高 0px），
    相邻差会在 69/71 之间交替；lattice 会锁死在 69 上，误差逐行累加，
    最后一行就被当成噪声剔掉了。取中位数（70）当行距再拟合才稳。
    """
    if len(rows) < 3:
        return rows
    ys = [r[0] for r in rows]
    diffs = [b - a for a, b in zip(ys, ys[1:]) if 20 <= b - a <= 300]
    if not diffs:
        return rows
    d = float(np.median(diffs))
    if d <= 0:
        return rows
    best = []
    for y0 in ys:
        chain = [r for r in rows
                 if abs(r[0] - (y0 + round((r[0] - y0) / d) * d)) <= tol]
        if len(chain) > len(best):
            best = chain
    return best if len(best) >= 3 else rows


def row_text_color(img: Image.Image, y0: int, y1: int, x0: int, x1: int):
    """在「整幅画面」上估某一行的文字色，只看 x0~x1 这一竖条。

    为什么不直接用 band_text_color：它是按"截进来的就是昵称列"设计的，
    取最暗 2% 像素的中位数。标定时喂进去的是整幅游戏画面，暗背景像素
    （灰阶 30~40）占比远超 2%，中位数会直接落到背景色上，结果每行都被
    判成"不是昵称色"。

    这里加了两道约束：只看点击所在的那一竖条（昵称列附近），并且先按
    "可能是笔迹"的灰阶范围（>=40）过滤，把背景排除在统计之外。
    """
    if y1 <= y0 or x1 <= x0:
        return None
    a = np.asarray(img.convert("RGB")).astype(np.int16)[y0:y1 + 1, x0:x1]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    vals = np.sort(mx[((mx - mn) < 25) & (mx >= 40)])
    if len(vals) < 20:
        return None
    return float(np.median(vals[:max(10, len(vals) // 50)]))


def detect_columns(img: Image.Image):
    """把画面里所有「等距成列的文本」找出来，返回候选列，按行数从多到少排。

    这是标定的公共底座。旧版把「点击必须精确命中某一行文字」当成前置条件，
    于是点偏十像素、点在字缝里、点在行间空白，就弹一次「这一下没点到昵称」——
    而真人点一次的成功率本来就不该被要求到像素级。改成先找候选列、再看点击
    落在哪一列附近，点击就从「必须命中」降级成「在一堆候选里指一下」。

    每列返回：
        rect   (x0,y0,x1,y1) 相对截图的像素矩形
        bands  被采纳的行 [(y0,y1), ...]
        pitch  行距（像素）
        rows   行数
        x0,x1  列内笔迹的左右边界
    """
    W, H = img.size
    bands = find_bands(img)
    if not bands:
        return []

    # 1) 每条带里按列投影切出笔迹组，只有「组内文字色落在昵称区间」的才算候选单元。
    #    这一步同时把头像（笔迹稀疏）、右侧"最近登录"（最暗约 155，超出上限）、
    #    横幅标题（颜色不同）全部挡在外面。
    cells = []          # (y_center, y0, y1, gx0, gx1)
    for y0, y1 in bands:
        h = y1 - y0 + 1
        if h < 6 or h > H * 0.12:
            continue
        for gx0, gx1 in ink_groups(img, y0, y1, gap=max(24, int(h * 1.9))):
            if gx1 - gx0 < max(10, h * 0.6):
                continue
            c = row_text_color(img, y0, y1, gx0, gx1 + 1)
            if c is None or not (TEXT_COLOR[0] <= c <= TEXT_COLOR[1]):
                continue
            cells.append(((y0 + y1) // 2, y0, y1, gx0, gx1))
    if not cells:
        return []

    # 2) 按左边界聚类：昵称是左对齐的，同一列各行的 x0 只会差几个像素。
    #    tol 给到画面宽度 2%（1316 宽 → 26px）：字形左侧留白会让 x0 抖动十几
    #    像素（"ゴルフ"这类假名比汉字多空 16px），tol 太紧就会把一个名字当成
    #    另一列，最后整行被漏掉。真实的两列之间至少隔着一个头像，远大于 26。
    tol_x = max(16, int(W * 0.020))
    seeds, seen = [], set()
    for c in cells:
        s = c[3]
        if any(abs(s - t) <= tol_x for t in seeds):
            continue
        seeds.append(s)

    cols = []
    for seed in seeds:
        group = [c for c in cells if abs(c[3] - seed) <= tol_x]
        if len(group) < 3:
            continue
        keep = fit_rows(group)
        if len(keep) < 3:
            continue
        keep.sort()
        ys = [r[0] for r in keep]
        diffs = [b - a for a, b in zip(ys, ys[1:])]
        if not diffs:
            continue
        pitch = float(np.median(diffs))
        if pitch < 12:
            continue
        # 行距必须真的等距，否则把"若干条无关文本恰好左对齐"当成列表
        if max(abs(d - pitch) for d in diffs) > pitch * 0.18:
            continue

        pad_x = max(3, int(pitch * 0.10))
        pad_y = max(3, int(pitch * 0.12))
        rect = (
            max(0, min(r[3] for r in keep) - pad_x),
            max(0, keep[0][1] - pad_y),
            min(W, max(r[4] for r in keep) + pad_x),
            min(H, keep[-1][2] + pad_y),
        )
        if rect[2] - rect[0] < 30 or rect[3] - rect[1] < 20:
            continue
        cols.append({
            "rect": rect,
            "bands": [(r[1], r[2]) for r in keep],
            "pitch": round(pitch, 1),
            "rows": len(keep),
            "x0": min(r[3] for r in keep),
            "x1": max(r[4] for r in keep),
            "y0": keep[0][1],
            "y1": keep[-1][2],
        })

    # 行数多的优先；行数相同则左边界靠左的优先（昵称列在成员卡片左侧）
    cols.sort(key=lambda c: (-c["rows"], c["x0"]))
    return cols


def derive_column(img: Image.Image, click: tuple[int, int] | None = None,
                  log_tag: str = "calib"):
    """推出昵称列的 ROI。给了 click 就用它指认是哪一列，不给就自动挑。

    返回 dict：rect / bands / pitch / rows / x0 / x1（失败时 error）。
    """
    W, H = img.size
    cols = detect_columns(img)
    if not cols:
        return {"error": "画面里没找到成列的昵称。确认游戏停在「联盟成员」列表，"
                         "且昵称文字没被弹窗挡住"}

    chosen, how = cols[0], "自动"
    if click is not None:
        cx, cy = int(click[0]), int(click[1])
        if not (0 <= cx < W and 0 <= cy < H):
            return {"error": "点到画面外面了"}

        def dist(c):
            # 优先「x 落在列内」，其次「y 在列的上下范围内」，两者都不中就退回行数最多
            dx = 0 if c["x0"] - 40 <= cx <= c["x1"] + 60 else min(
                abs(cx - c["x0"]), abs(cx - c["x1"]))
            dy = 0 if c["y0"] - 60 <= cy <= c["y1"] + 60 else min(
                abs(cy - c["y0"]), abs(cy - c["y1"]))
            return (dx, dy, -c["rows"])

        chosen, how = min(cols, key=dist), f"点击({cx},{cy})"
        # 横向离得太远就不要硬套：列表是竖长条，左右偏出一个名字宽度以上，
        # 说明他点的很可能根本不是列表（比如点在了右侧战绩栏）
        if dist(chosen)[0] > W * 0.30:
            return {"error": "点的位置离识别到的昵称列太远，请点在成员昵称上"}

    out = dict(chosen)
    out["click"] = click
    log(log_tag, f"推导 ROI={out['rect']} 行数={out['rows']} 行距={out['pitch']} "
                 f"来源={how} 候选列={len(cols)}")
    return out


# ---------------------------------------------------------------- 预设固化
#
# 标定一次就够了，不该每次启动都来一遍。结果固化成两份：
#   输出/区域.json      本机最近一次的标定（会被覆盖，跟着窗口比例走）
#   预设/昵称列预设.json 随工具走的预设（换分辨率照样能用，因为存的是比例）
#
# 之所以还要第二份：删了 输出/ 目录、或者换台机器、或者重装，只要这个文件
# 在，工具就知道昵称列在哪——不需要任何人再点一遍。


def load_preset():
    """读固化预设。没有、坏了、格式不对都返回 None，不打扰用户。"""
    import json

    try:
        raw = json.loads(PRESET.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    rel = raw.get("region_rel")
    if not (isinstance(rel, list) and len(rel) == 4):
        return None
    try:
        return {
            "region_rel": tuple(max(0.0, min(1.0, float(v))) for v in rel),
            # 行距按「占画面高度的比例」存：1220×686 上量到 70px → 存 0.102，
            # 换到 1920×1080 自动变成 110px，不用重新标定。
            "pitch_rel": float(raw.get("pitch_rel") or 0),
            "rows": int(raw.get("rows_per_screen") or 0),
            "aspect": float(raw.get("aspect") or 0),
            "resolution": str(raw.get("resolution") or ""),
            "saved_at": str(raw.get("saved_at") or ""),
        }
    except Exception:
        return None


def save_preset(region_rel, pitch=None, rows=None, frame=None) -> bool:
    """把标定结果固化成预设。frame 是有效画面（WinInfo），用来算比例。"""
    import json

    if not region_rel:
        return False
    fh = float(getattr(frame, "h", 0) or 0)
    data = {
        "version": 1,
        "saved_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "region_rel": [round(float(v), 6) for v in region_rel],
        "pitch_rel": round(float(pitch) / fh, 6) if (pitch and fh) else 0.0,
        "rows_per_screen": int(rows or 0),
        "aspect": round(float(getattr(frame, "aspect", 0) or 0), 6),
        "resolution": f"{getattr(frame, 'w', 0)}x{getattr(frame, 'h', 0)}",
        "note": "标定一次就固化在这儿。删掉本文件才会要求重新标定。",
    }
    try:
        PRESET.parent.mkdir(parents=True, exist_ok=True)
        PRESET.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        log("preset", f"已固化预设 → {PRESET} · 每屏 {data['rows_per_screen']} 行 "
                      f"· 行距比例 {data['pitch_rel']} · 原分辨率 {data['resolution']}")
        return True
    except Exception as exc:
        log_exc("preset", exc)
        return False


# ---------------------------------------------------------------- 框选

class RegionPicker(tk.Toplevel):
    """全屏半透明层，拖拽框选。返回物理像素 (x0,y0,x1,y1)。

    正常流程不用它——【标定昵称列】点一下就能把 ROI 量出来。
    这里保留是为了兜底：画面被遮挡、行距异常、或者昵称列不是常规排布时，
    手画仍然是最后一招。
    """

    def __init__(self, master, hint: str | None = None):
        super().__init__(master)
        self.result = None
        self.shot = grab()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.sx = self.shot.width / sw      # 截图 → 屏幕 的换算比
        self.sy = self.shot.height / sh

        disp = self.shot if (self.shot.width, self.shot.height) == (sw, sh) else \
            self.shot.resize((sw, sh), Image.Resampling.LANCZOS)
        self.tkimg = ImageTk.PhotoImage(disp)

        self.overrideredirect(True)
        self.geometry(f"{sw}x{sh}+0+0")
        self.attributes("-topmost", True)
        self.configure(bg="black")

        self.cv = tk.Canvas(self, highlightthickness=0, cursor="cross", bg="black")
        self.cv.pack(fill="both", expand=True)
        self.cv.create_image(0, 0, image=self.tkimg, anchor="nw")
        self.cv.create_text(
            sw // 2, 36,
            text=hint or ("手动框选（备用）：拖动框住「昵称这一列」 —— 第一行名字上沿 → 最后一行下沿  ·  Esc 取消"),
            fill="#ffe066", font=("Microsoft YaHei UI", 15, "bold"),
        )

        self._p0 = None
        self._rect = None
        self.cv.bind("<ButtonPress-1>", self._down)
        self.cv.bind("<B1-Motion>", self._drag)
        self.cv.bind("<ButtonRelease-1>", self._up)
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

    def _down(self, e):
        self._p0 = (e.x, e.y)
        if self._rect:
            self.cv.delete(self._rect)
        self._rect = self.cv.create_rectangle(
            e.x, e.y, e.x, e.y, outline="#ff5252", width=3, dash=(6, 3)
        )

    def _drag(self, e):
        if self._p0 and self._rect:
            self.cv.coords(self._rect, self._p0[0], self._p0[1], e.x, e.y)

    def _up(self, e):
        if not self._p0:
            return
        x0, x1 = sorted((self._p0[0], e.x))
        y0, y1 = sorted((self._p0[1], e.y))
        if x1 - x0 < 40 or y1 - y0 < 20:
            self.cv.delete(self._rect)
            self._rect = None
            self._p0 = None
            return
        self.result = (
            int(x0 * self.sx), int(y0 * self.sy),
            int(x1 * self.sx), int(y1 * self.sy),
        )
        self.destroy()


# ---------------------------------------------------------------- 点选标定

def snap_to_rows(img: Image.Image, rect, log_tag: str = "calib-snap"):
    """把框的上下边吸附到真实行边界。认不出行就原样返回，绝不报错。

    框是用户给的"这一片"，但框的上下边几乎不可能正好落在行间空白上——人手
    落点差十几像素是常态，而切掉半行会让 OCR 直接认不出那一行。所以拿到框之后
    借画面自己的结构对齐一次：凡是**被框碰到**的行，都扩成完整的行。

    关键是被切掉的行必须也能看到。只在框内找行是不够的——框把某行切掉半截时，
    框内只看得见它的下半部分，对齐反而会把这一行整行裁掉，越修越差。所以在
    整幅画面上找行，再挑"与框纵向有重叠"的那些。

    x 不动。昵称长短差异很大，自动收窄会切掉长名字；横向框宽一点只会多带进
    空白，识别链路本来就会滤掉。只修真正会看错的那一维。

    返回 (rect, pitch, rows)。量不出时为 (rect, None, 0)。
    """
    x0, y0, x1, y1 = rect
    if img.width < 8 or img.height < 8:
        return tuple(rect), None, 0

    try:
        bands = find_bands(img)
    except Exception:
        return tuple(rect), None, 0
    if not bands:
        return tuple(rect), None, 0

    # 文字色只在框的 x 范围内采样：列表右侧的"最近登录"、顶部横幅都在别处，
    # 用全宽采样会把它们的中位数算进来。
    nick = []
    for a, b in bands:
        c = row_text_color(img, a, b, x0, x1)
        if c is not None and TEXT_COLOR[0] <= c <= TEXT_COLOR[1]:
            nick.append((a, b))
    if len(nick) < 2:
        return tuple(rect), None, 0

    keep = fit_rows([((a + b) // 2, a, b) for a, b in nick])
    if len(keep) < 2:
        return tuple(rect), None, 0
    keep.sort()
    ys = [r[0] for r in keep]
    diffs = [b - a for a, b in zip(ys, ys[1:])]
    pitch = float(np.median(diffs)) if diffs else 0.0
    if pitch <= 0:
        return tuple(rect), None, 0

    # 与框纵向有重叠的行 —— 这些才是用户框住的行
    over = [r for r in keep if r[2] > y0 + 2 and r[1] < y1 - 2]
    if len(over) < 2:
        # 框和这一列错位了（比如框到了头像那一栏的空隙）：退回整列，
        # 至少给出一个结构正确的区域，而不是把一个歪框原样存下去。
        over = keep

    pad = max(3, int(pitch * 0.12))
    ny0 = max(0, over[0][1] - pad)
    ny1 = min(img.height, over[-1][2] + pad)
    if ny1 - ny0 < 20:
        return tuple(rect), None, 0

    out = (x0, ny0, x1, ny1)
    if out != tuple(rect):
        log(log_tag, f"上下边吸附 {tuple(rect)} → {out} "
                     f"（框住 {len(over)} 行 · 行距 {pitch:.1f}）")
    return out, round(pitch, 1), len(over)


def median_samples(samples):
    """把 N 个手画框样本按分量取中位数。samples = [(rect, pitch, rows), ...]。

    取中位数而不是平均：手抖是双边的（有时拖长、有时拖短），均值会被单个
    拖飞了的样本拉偏，中位数不会。这也是"框几次再提交"在统计上真实有效的原因——
    噪声按 √n 收敛，而不是靠人越框越准。

    纯函数，不依赖 Tk，可单测。
    """
    if not samples:
        return None
    arr = np.array([s[0] for s in samples], dtype=float)
    x0, y0, x1, y1 = (int(round(v)) for v in np.median(arr, axis=0))
    if x1 - x0 < 30 or y1 - y0 < 20:
        return None

    pitches = [s[1] for s in samples if s[1]]
    rowss = [s[2] for s in samples if s[2]]
    pitch = float(np.median(pitches)) if pitches else None
    rows = int(round(np.median(rowss))) if rowss else 0

    # 交叉校验：行距 × 行数 应该大致铺满框高。量错一格（比如量成半行距）
    # 时比例会明显不对，直接用 高度÷行数 兜住。只纠正，不拦。
    h = y1 - y0
    if pitch and rows:
        if abs(pitch * rows - h) / h > 0.35:
            pitch = h / rows
    elif pitch and not rows:
        rows = max(1, int(round(h / pitch)))
    elif rows and not pitch:
        pitch = h / rows

    return {
        "rect": (x0, y0, x1, y1),
        "bands": [],
        "pitch": round(pitch, 1) if pitch else None,
        "rows": rows or None,
        "mode": "manual",
        "samples": len(samples),
    }


class CalibWizard(tk.Toplevel):
    """标定浮层：程序先自己认一次，认不出就让人拖框——**框几次都行，提交即保存**。

    这里的原则是「框是输入，不是答案」。框选的唯一作用是告诉程序"昵称在这一片"，
    所以：不校验框里有没有认出昵称，不弹窗要求重来，也不因为只框了一次就拒绝。

    为什么改成多次采样取中位数：手拖同一个意图两次，落点差十到三十像素是正常的，
    这是人手的分辨率，不是错误。单次框 = 一个带噪声的样本；多次框各分量取中位数，
    噪声按 √n 收敛。所以"框几次再提交"在统计上是有效做法，而不是繁琐的仪式。

    三条路径，优先级从低负担到高负担：
        自动    打开就先算一遍，多数情况直接给结果，一次都不用框
        点一下  在任意一行昵称上点，指认是哪一列（点不准也无所谓）
        拖一个框 结构不规整时的兜底；拖几次就采几次样本
    """

    def __init__(self, master, frame: WinInfo, preset_rel=None):
        super().__init__(master)
        self.result = None
        self.frame = frame
        self._preset_rel = preset_rel  # 上次标定的相对位置：自动认不出时先垫上
        self.res = None                 # 自动/点选/预设推导的结果（相对有效画面）
        self._samples = []              # 每次拖框的样本 [(rect, pitch, rows), ...]
        self._mc = None                 # 中位数+吸附的缓存（重算一次要几十毫秒）
        self._p0 = None
        self._dragging = False

        self.shot = grab()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.sx = self.shot.width / sw      # 截图 → 屏幕 的换算比
        self.sy = self.shot.height / sh

        disp = self.shot if (self.shot.width, self.shot.height) == (sw, sh) else \
            self.shot.resize((sw, sh), Image.Resampling.LANCZOS)
        self.tkimg = ImageTk.PhotoImage(disp)

        # 推导只在游戏有效画面里做，坐标相对它的左上角
        self.crop = self.shot.crop(
            (frame.x, frame.y, frame.x + frame.w, frame.y + frame.h))

        self.overrideredirect(True)
        self.geometry(f"{sw}x{sh}+0+0")
        self.attributes("-topmost", True)
        self.configure(bg="black")

        self.cv = tk.Canvas(self, highlightthickness=0, cursor="cross", bg="black")
        self.cv.pack(fill="both", expand=True)
        self.cv.create_image(0, 0, image=self.tkimg, anchor="nw")
        self._hx = self.cv.create_line(0, 0, 0, 0, fill="#ff5252", width=1)
        self._hy = self.cv.create_line(0, 0, 0, 0, fill="#ff5252", width=1)

        self.tip_lbl = tk.Label(
            self, text="正在自动识别…", bg="#16171a", fg="#ffe066",
            font=("Microsoft YaHei UI", 13, "bold"), padx=14, pady=6,
        )
        self.tip_lbl.place(relx=0.5, y=14, anchor="n")

        foot = tk.Frame(self, bg="#16171a", padx=10, pady=8)
        foot.place(relx=0.5, rely=1.0, y=-14, anchor="s")

        def fbtn(text, cmd, primary=False):
            b = tk.Button(
                foot, text=text, command=cmd, font=FONT,
                bg="#3a6fd8" if primary else PANEL,
                fg="white" if primary else FG, relief="flat", bd=0,
                padx=18, pady=6, cursor="hand2",
                activebackground="#4a7fe8" if primary else "#3a3d42",
                activeforeground="white",
            )
            b.pack(side="left", padx=4)
            return b

        fbtn("提交", self._apply, primary=True)
        fbtn("清空重来", self._reset)
        fbtn("取消", self.destroy)

        self.cv.bind("<Motion>", self._move)
        self.cv.bind("<ButtonPress-1>", self._down)
        self.cv.bind("<B1-Motion>", self._b1move)
        self.cv.bind("<ButtonRelease-1>", self._up)
        self.bind("<Return>", lambda e: self._apply())
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_force()

        # 打开就先算一遍——多数情况用户什么都不用做
        self._auto()

    # ---------------- 绘图与提示

    def _tip(self, text, color="#ffe066"):
        self.tip_lbl.config(text=text, fg=color)

    def _to_cv(self, x, y):
        """有效画面内的物理坐标 → 画布坐标。"""
        return ((x + self.frame.x) / self.sx, (y + self.frame.y) / self.sy)

    def _clear(self):
        for tag in ("mk", "man", "sample"):
            self.cv.delete(tag)

    def _draw(self, res, tag="mk", color="#3ad29f", width=3, bands=True):
        x0, y0, x1, y1 = res["rect"]
        cx0, cy0 = self._to_cv(x0, y0)
        cx1, cy1 = self._to_cv(x1, y1)
        self.cv.create_rectangle(cx0, cy0, cx1, cy1, outline=color,
                                 width=width, tags=tag)
        if not bands:
            return
        for by0, by1 in res.get("bands") or []:
            _x, y = self._to_cv(x0, (by0 + by1) // 2)
            self.cv.create_line(cx0, y, cx1, y, fill=color,
                                width=1, dash=(4, 4), tags=tag)

    def _show(self, res, how="自动"):
        self.res = res
        self._clear()
        self._draw(res)
        if self._samples:
            # 自动结果只是参考：一旦有手画样本，以样本为准
            self.cv.itemconfig("mk", outline="#3a6fd8", width=2)
            self._redraw_samples()
            self._tip_sample()
            return
        self._tip(f"{how}认出 {res['rows']} 行 · 行距 {res['pitch']}px · "
                  f"无误点【提交】；想自己框就拖一个框，框几次都行")

    # ---------------- 三条路径

    def _auto(self):
        try:
            res = derive_column(self.crop, None, log_tag="calib-auto")
        except Exception as exc:
            log_exc("calib-auto", exc)
            if not self._show_preset():
                self._tip("自动识别出错 —— 直接拖一个框，拖完点【提交】", "#ffb4b4")
            return
        if res.get("error"):
            # 自动认不出不等于要重来：上次标定过的话，位置就在那儿，
            # 先画出来让他一眼确认，点【提交】就走。—— 这才是"下次直接用"。
            if not self._show_preset():
                self._tip("没自动认出来 —— 直接拖一个框(套住整列昵称)，拖完点【提交】",
                          "#ffb4b4")
            return
        self._show(res, "自动")

    def _show_preset(self) -> bool:
        """把上次标定的位置画出来当默认框。没有可用预设就返回 False。"""
        rel = self._preset_rel
        if not rel or len(rel) != 4:
            return False
        try:
            rect = (int(round(rel[0] * self.frame.w)), int(round(rel[1] * self.frame.h)),
                    int(round(rel[2] * self.frame.w)), int(round(rel[3] * self.frame.h)))
        except Exception:
            return False
        if rect[2] - rect[0] < 30 or rect[3] - rect[1] < 20:
            return False

        res = {"rect": rect, "bands": [], "pitch": None, "rows": None, "mode": "preset"}
        try:
            rect2, pitch, rows = snap_to_rows(self.crop, rect, log_tag="calib-preset")
            res["rect"] = rect2
            if pitch:
                res["pitch"] = pitch
            if rows:
                res["rows"] = rows
        except Exception as exc:
            log_exc("calib-preset", exc)

        self.res = res
        self._clear()
        self._draw(res, color="#3a6fd8")
        got = ""
        if res["rows"]:
            got = f"认出 {res['rows']} 行 · 行距 {res['pitch']}px · "
        self._tip(f"自动识别没成功，先套用了上次标定的位置（{got}"
                  f"对就点【提交】，要改就拖个框）", "#9ad6a8")
        log("calib", f"自动识别失败，套用上次位置 rect={res['rect']} "
                     f"行数={res['rows']} 行距={res['pitch']}")
        return True

    def _point(self, ev):
        lx = int(ev.x * self.sx) - self.frame.x
        ly = int(ev.y * self.sy) - self.frame.y
        try:
            res = derive_column(self.crop, (lx, ly), log_tag="calib-point")
        except Exception as exc:
            log_exc("calib-point", exc)
            self._tip("识别出错 —— 拖一个框更快，拖完点【提交】", "#ffb4b4")
            return
        if res.get("error"):
            # 点不出来不再要求"再点一次"：换拖框，一步到位
            self._tip("⚠ " + res["error"] + " —— 改用拖框：套住整列昵称即可", "#ffb4b4")
            return
        self._show(res, "按你点的位置")

    def _reset(self):
        """清空所有样本，回到"自动识别"的原点。"""
        self._samples.clear()
        self._mc = None
        self.res = None
        self._clear()
        self._tip("已清空 —— 重新拖框，或点一行昵称", "#aeb4ba")
        self._auto()

    # ---------------- 鼠标

    def _move(self, e):
        self.cv.coords(self._hx, 0, e.y, self.cv.winfo_width(), e.y)
        self.cv.coords(self._hy, e.x, 0, e.x, self.cv.winfo_height())

    def _down(self, e):
        self._p0 = (e.x, e.y)
        self._dragging = False

    def _b1move(self, e):
        if self._p0 is None:
            return
        if not self._dragging and max(abs(e.x - self._p0[0]),
                                      abs(e.y - self._p0[1])) > 8:
            self._dragging = True
        if self._dragging:
            self._clear()
            self.cv.create_rectangle(self._p0[0], self._p0[1], e.x, e.y,
                                     outline="#ffb84d", width=2,
                                     dash=(6, 3), tags="man")

    def _up(self, e):
        if self._p0 is None:
            return
        p0, self._p0 = self._p0, None
        if not self._dragging:
            self._point(e)
            return

        self._dragging = False
        x0, x1 = sorted((p0[0], e.x))
        y0, y1 = sorted((p0[1], e.y))
        if x1 - x0 < 40 or y1 - y0 < 20:
            self._clear()
            self._redraw_samples()
            self._tip("框太小了（宽高至少 40×20）—— 重新拖一次", "#ffb4b4")
            return

        # 画布坐标 → 物理屏幕坐标 → 有效画面内坐标
        sx0, sy0 = int(x0 * self.sx), int(y0 * self.sy)
        sx1, sy1 = int(x1 * self.sx), int(y1 * self.sy)
        rel = (sx0 - self.frame.x, sy0 - self.frame.y,
               sx1 - self.frame.x, sy1 - self.frame.y)
        rel = (max(0, rel[0]), max(0, rel[1]),
               min(self.frame.w, rel[2]), min(self.frame.h, rel[3]))
        pitch, rows = _measure_pitch(self.crop.crop(rel))

        # 采样：不判对错、不拦。框就是输入，量不出行距也照样收下——
        # 行距只是滚速初值，没有它照样能跑（步长有闭环）。
        self._samples.append((rel, pitch, rows))
        self._mc = None
        self.res = None
        self._clear()
        self._redraw_samples()
        self._tip_sample()

    # ---------------- 采样：多次取中位数

    def _redraw_samples(self):
        """画所有历史样本（淡黄细线）+ 当前中位数（绿色粗线）。"""
        self.cv.delete("sample")
        for rel, _p, _r in self._samples[:-1]:
            cx0, cy0 = self._to_cv(rel[0], rel[1])
            cx1, cy1 = self._to_cv(rel[2], rel[3])
            self.cv.create_rectangle(cx0, cy0, cx1, cy1, outline="#c8a24a",
                                     width=1, dash=(3, 3), tags="sample")
        if not self._samples:
            return
        cur = self._median_result()
        if cur:
            rel = cur["rect"]
            cx0, cy0 = self._to_cv(rel[0], rel[1])
            cx1, cy1 = self._to_cv(rel[2], rel[3])
            self.cv.create_rectangle(cx0, cy0, cx1, cy1, outline="#ffb84d",
                                     width=2, tags="sample")
            for by0, by1 in cur.get("bands") or []:
                _x, y = self._to_cv(rel[0], (by0 + by1) // 2)
                self.cv.create_line(cx0, y, cx1, y, fill="#ffb84d",
                                    width=1, dash=(4, 4), tags="sample")

    def _tip_sample(self):
        cur = self._median_result()
        n = len(self._samples)
        if not cur:
            self._tip(f"已采 {n} 次 · 算不出中位数，再框一次", "#ffb4b4")
            return
        w = cur["rect"][2] - cur["rect"][0]
        h = cur["rect"][3] - cur["rect"][1]
        desc = f"已采 {n} 次 · 中位数 {w}×{h}px"
        if cur["rows"]:
            desc += f" · {cur['rows']} 行"
        if cur["pitch"]:
            desc += f" · 行距 {cur['pitch']:.0f}px"
        if n == 1:
            desc += " —— 想更稳就再框一次；觉得对了直接点【提交】"
        else:
            desc += " —— 可以继续框，也可以点【提交】"
        self._tip(desc, "#9ad6a8")

    def _median_result(self):
        if self._mc is not None:
            return self._mc
        cur = median_samples(self._samples)
        if not cur:
            return None
        # 中位数解决了"框在哪"，吸附解决"边在哪"。两步分开：
        # 前者消除手抖，后者把边贴到真实行边界，切掉半行的风险归零。
        rect, pitch, rows = snap_to_rows(self.crop, cur["rect"], log_tag="calib-snap")
        cur["rect"] = rect
        if pitch:
            cur["pitch"] = pitch
        if rows:
            cur["rows"] = rows
        self._mc = cur
        return cur

    def _apply(self):
        if self._samples:
            res = self._median_result()
            if not res:
                self._tip("框算不出中位数 —— 再拖一次（大一点，套住整列）", "#ffb4b4")
                return
            self.result = res
        elif self.res:
            out = dict(self.res)
            out["mode"] = self.res.get("mode") or "auto"
            self.result = out
        else:
            self._tip("直接拖一个框：套住整列昵称，拖完点【提交】", "#ffb4b4")
            return
        log("calib", f"提交标定 mode={self.result.get('mode')} "
                     f"rect={self.result['rect']} pitch={self.result.get('pitch')} "
                     f"rows={self.result.get('rows')} 样本={len(self._samples)}")
        self.destroy()


# ---------------------------------------------------------------- 选窗口

class WindowPicker(tk.Toplevel):
    """列出可见窗口，显示有效画面尺寸和宽高比，选中即锁定。

    自动锁定只在「唯一命中」时生效（见 find_game_window），因为自动选错窗口的
    代价是采到一份看起来正常、实际全是错的名单。多个候选时把判断权交回给人：
    显示尺寸和比例，一眼能确认「1316×740 · 1.778」是游戏而不是别的东西。
    """

    def __init__(self, master):
        super().__init__(master)
        self.result: WinInfo | None = None
        self._items: dict[str, WinInfo] = {}

        self.title("选择游戏窗口")
        self.geometry("760x430")
        self.minsize(620, 340)
        self.configure(bg=BG)
        self.transient(master)

        tk.Label(
            self,
            text="选中游戏窗口后点「锁定」。显示的是有效画面（客户区去掉黑边）的尺寸与比例 —— "
                 "挪窗口、改大小都不用重新标定，只有画面比例变了才要重来。",
            bg=BG, fg="#9aa0a6", font=FONT_SM, justify="left", anchor="w",
            wraplength=720, padx=14, pady=10,
        ).pack(fill="x")

        wrap = tk.Frame(self, bg=BG, padx=14)
        wrap.pack(fill="both", expand=True)

        cols = ("title", "size", "aspect")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=12,
                                 selectmode="browse")
        for c, t, w, a in (("title", "窗口标题", 430, "w"),
                           ("size", "有效画面", 130, "center"),
                           ("aspect", "宽高比", 90, "center")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "title"))
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda _e: self._ok())
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._hint())

        self.hint = tk.Label(self, text="", bg=BG, fg="#ffe066", font=FONT_SM,
                             anchor="w", justify="left", padx=14, wraplength=720)
        self.hint.pack(fill="x")

        bar = tk.Frame(self, bg=BG, padx=14, pady=10)
        bar.pack(fill="x")

        def btn(text, cmd, primary=False):
            b = tk.Button(bar, text=text, command=cmd, font=FONT,
                          bg=ACCENT if primary else PANEL,
                          fg="white" if primary else FG,
                          activebackground="#3a6fd8" if primary else "#3a3d42",
                          activeforeground="white", relief="flat", bd=0,
                          padx=16, pady=7, cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            return b

        btn("重新扫描", self._scan)
        btn("取消", self.destroy)
        btn("锁定这个窗口", self._ok, primary=True)

        self._scan()
        self.bind("<Escape>", lambda _e: self.destroy())
        self.tree.focus_set()

    def _scan(self):
        self.tree.delete(*self.tree.get_children())
        self._items.clear()
        # 列表里显示的是「有效画面」尺寸（客户区去掉黑边），不是客户区尺寸——
        # 因为坐标基准就是有效画面，给人看的数字必须和程序用的数字是同一个。
        for w in enumerate_windows():
            c = content_rect(w.hwnd) or w
            iid = str(w.hwnd)
            self._items[iid] = c
            self.tree.insert("", "end", iid=iid,
                             values=(w.title[:60], f"{c.w}×{c.h}", f"{c.aspect:.3f}"))
        items = list(self._items.values())
        if items:
            first = str(items[0].hwnd)
            self.tree.selection_set(first)
            self.tree.focus(first)
            self.tree.see(first)
        self._hint()

    def _selected(self) -> WinInfo | None:
        sel = self.tree.selection()
        return self._items.get(sel[0]) if sel else None

    def _hint(self):
        w = self._selected()
        if w is None:
            self.hint.config(text="列表为空：游戏窗口可能被最小化了，还原后点「重新扫描」")
            return
        msgs = []
        if w.h < MIN_CLIENT_H:
            msgs.append(f"画面只有 {w.h}px 高，字太小识别会不稳，建议放大窗口")
        if abs(w.aspect - TARGET_ASPECT) / TARGET_ASPECT > ASPECT_TOL:
            msgs.append(f"有效画面比例 {aspect_label(w.aspect)} 不是 16:9；"
                        f"UI 会跟着比例重排，请把游戏窗口调成 16:9 再锁定")
        self.hint.config(text="  ".join(msgs) if msgs else
                         f"有效画面 {w.describe()}（{aspect_label(w.aspect)}）—— 比例正常，可以直接锁定")

    def _ok(self):
        w = self._selected()
        if w is None:
            messagebox.showinfo("提示", "请先选中一个窗口", parent=self)
            return
        self.result = w
        self.destroy()


# ---------------------------------------------------------------- 主窗口

def _client_win_h(default: int = 940) -> int:
    """正式客户端持久化的窗口高度（导出设置.json 的 win_h），读不到给 default。

    客户端的 SETTINGS_PATH = alliance_ocr.APP_DIR / "预设" / "导出设置.json"
    （07-客户端/app/store.py 复用本模块的 APP_DIR），所以第一候选就是它；
    再兜一个 07-客户端/预设/。只认 400~3000 的值，别把损坏文件里的 0 当高度用。
    """
    for p in (APP_DIR / "预设" / "导出设置.json",
              APP_DIR.parent / "07-客户端" / "预设" / "导出设置.json",
              APP_DIR / "导出设置.json"):
        try:
            v = int(json.loads(p.read_text(encoding="utf-8")).get("win_h"))
            if 400 <= v <= 3000:
                return v
        except Exception:
            continue
    return default


class RosterMixin:
    """名单采集的判据与流程（**不含任何 UI**）。

    为什么要抽出来：客户端要把名单采集真正搬进去，而这份判据是几轮踩坑换来的
    （行距学习 / 跨屏拼接 / 六道归位闸 / 收工对账），复制一份必然漂移。所以判据
    留在原处，UI 换成宿主钩子：

      self.say(msg)                   日志
      self._task(**kw)                进度条/步骤（可空）
      self._flow(idx, state, pct)     步骤条（可空）
      self._sync_run_buttons()        按钮态（可空）
      self._log_mode(running)         日志区伸缩（可空）
      self._refresh_rows()            名单表格刷新（可空）
      self._refresh_ledger(total=None) 账本（可空）
      self._warn(title, msg)          警告（旧工具弹窗 / 客户端写日志）
      self._ok(title, msg) -> bool    确认（旧工具弹窗 / 客户端恒 True）
      self._roll_gap() -> int         滚轮格数
      self._nav_ui(running)           导航按钮态（可空）
      self.after(ms, fn)              定时器（Tk / QTimer）
      self._post(fn, *args)           回主线程（Tk 队列 / 客户端直调）

    状态字段由 `_init_roster_state()` 初始化，两个宿主都必须调它。
    """


    # ---------------- 定时器 / 线程回传（App 用 Tk，客户端覆盖成 QTimer）
    def after(self, ms, fn=None):          # noqa: A003  （覆盖 Tk 同名方法不冲突）
        raise NotImplementedError("宿主必须提供 after(ms, fn)")

    def _post(self, fn, *args):
        raise NotImplementedError("宿主必须提供 _post(fn, *args)")

    # ---------------- 宿主钩子默认实现（客户端漏写也不会崩）

    def _raise_self(self):
        pass

    def _alive(self):
        return True

    def _status_text(self, msg):
        pass

    def _refresh_info(self):
        pass

    def _show_manual(self):
        pass

    def do_sync(self, complete=None, quiet=False):
        return None

    def _draw_preview(self, img, bands):
        pass

    def _persist_mode(self):
        pass

    def _switch_mode(self):
        return False
    def _refresh_rows(self):
        pass

    def _refresh_ledger(self, total=None):
        pass

    def _warn(self, title, msg):
        self.say(f"{title}：{msg}", "WARN")

    def _ok(self, title, msg):
        return True

    def _roll_gap(self):
        return 1

    def _nav_ui(self, running):
        pass

    def _init_roster_state(self):
        """名单引擎的全部状态字段（原先散落在 App.__init__ 里）。"""
        self.win = None
        self.region = None
        self.region_rel = None
        self._saved = {}
        self.names = {}
        self._hits = {}
        self._seen = {}
        self._links = set()
        self._gate = {}
        self._twin = {}
        self._doubt_said = set()
        self._roster = load_roster()
        self._roster_prev = None
        self._continuous = False
        self._paused = False
        self._headless = False
        self._nav_running = False
        self._nav_cancel = False
        self._nav_top_ok = None
        self._nav_where = None
        self._total_hint = None
        self._still_t0 = 0.0
        self._still_probe = None
        self._prev_ordered = []
        self._pitch = None
        self._last_dist = None
        self._last_img = None
        self._mode_ok = set()
        self._mode_tried = set()
        self._session_t0 = None
        self._fling = FLING_INIT
        self._slop = float(DRAG_SLOP)
        self._step_boost = 1
        self._stuck_retry = 0
        self._roll_count = 0
        self._roll_before = None
        self._last_diff = None
        self._fix_cnt = {}
        self._jumps = 0
        self._recover_target = []
        self._recover_seen = 0
        self._recover_walks = 0
        self._judge = SettleJudge()
        self._cursor_home = None
        self._empty_rounds = 0
        self._prev_names = set()
        self._calib_pitch = None
        self._calib_rows = None
        self._preset_used = False
        self._focused = False
        self._busy = False
        self._last_preview = None
        self._perm_note = ""
        self.scroll_mode = None
        self._persisted_mode = None
        self._roll_clicks = 1
        self._settle_t0 = 0.0
        self._run_dir = None
        self._sync = None
        self._lock_miss = 0

    def _save_cfg(self):
        import json

        if self.win is None or not self.region_rel:
            return
        data = {
            "version": 3,
            "window": {
                "title": self.win.title,
                "w": self.win.w,
                "h": self.win.h,
                "aspect": round(self.win.aspect, 6),
            },
            "region_rel": [round(v, 6) for v in self.region_rel],
            # 行距和每屏行数不只是给界面看的：它们是"这个 UI 还是不是原来那个 UI"
            # 的指纹。下次采集前重算一遍对不上，就说明布局变了、该重标定了。
            "pitch": round(float(self._calib_pitch or 0), 2),
            "rows_per_screen": int(self._calib_rows or 0),
        }
        if self.scroll_mode in SCROLL_MODES:
            data["scroll_mode"] = self.scroll_mode
        try:
            CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            log("config", f"保存区域 {data.get('region_rel')} · 窗口 {data.get('window')} "
                          f"· 行距 {data.get('pitch')} · 每屏 {data.get('rows_per_screen')}")
        except Exception as exc:
            log_exc("config", exc)

    def auto_lock(self):
        """接上游戏窗口。工具只服务一个客户端，所以这件事全程在后台做。

        每 2 秒扫一次，扫到就停，界面上没有"锁定"这个动作。三种情况三种说法，
        都不需要用户动手：
          · 扫到了 → 静默接上（有上次标定 / 内置预设就直接能采）
          · 有候选但有效画面不是 16:9 → 说清差在哪，用户把窗口调成 16:9 后
            下一轮自动扫到（NIKKE 的 UI 锚点跟画面比例走，比例不对坐标就废了）
          · 还没开客户端 → 等着
        只有连续 `AUTO_LOCK_GIVEUP` 次接不上，才把「手动选择」放出来兜底——
        自动选错窗口的代价是采出一份看着正常、实际全错的名单，所以宁可让人点。
        """
        try:
            if not self._alive():      # 窗口已关，别让 after 自循环继续跑
                return
        except Exception:
            return
        if self.win is not None:
            return
        best, cands = find_game_window()
        if best is not None:
            log("lock", f"自动接上 {best.title[:30]} {best.describe()} @{best.x},{best.y}")
            self._adopt(best)
            return
        self._lock_miss = getattr(self, "_lock_miss", 0) + 1
        if cands:
            w = cands[0]
            self._status_text(
                f"画面比例 {w.w}×{w.h}（{aspect_label(w.aspect)}）不是 16:9 —— "
                f"把客户端窗口调成 16:9（如 1600×900 / 1920×1080）后会自动接上")
            if self._lock_miss == 1:            # 只说一次，别每 2 秒刷一遍日志
                log("lock", f"自动接窗放弃：{len(cands)} 个候选，首个画面比例 "
                            f"{w.aspect:.3f}（{aspect_label(w.aspect)}）", "WARN")
        else:
            self._status_text("正在等待游戏窗口…（打开客户端后会自己接上）")
        if self._lock_miss >= AUTO_LOCK_GIVEUP:
            self._show_manual()
            return
        self.after(AUTO_LOCK_RETRY, self.auto_lock)

    def _adopt(self, info: WinInfo):
        """接上窗口。比例和上次一致就自动带出上次的标定，省掉重复标定。"""
        self._lock_miss = 0
        if getattr(self, "_btn_manual", None) is not None:
            self._btn_manual.pack_forget()   # 接上了就把兜底入口收回去
            self._btn_manual = None
        self.win = WinInfo(info.hwnd, info.title, info.x, info.y, info.w, info.h)
        self.region = None
        self.region_rel = None
        self._focused = False

        # 权限对照：游戏是管理员而本工具不是时，滚轮和前台切换都会被拦，
        # 与其等采集中途失败，不如锁定那一刻就把结论写进信息条。
        me, tgt = self_elevated(), window_elevated(info.hwnd)
        if tgt is True and me is False:
            self._perm_note = "权限：游戏管理员·工具普通"
            logline(f"权限不对等 self={me} target={tgt} "
                    f"proc={proc_name(pid_of_window(info.hwnd))}")
        elif tgt is None or me is None:
            self._perm_note = "权限：未知"
        else:
            self._perm_note = "权限：一致"
            logline(f"权限一致 self={me} target={tgt}")

        saved = self._saved
        sa = float(saved.get("aspect") or 0.0)
        if saved.get("region_rel") and sa and self.win.aspect and \
                abs(sa - self.win.aspect) / self.win.aspect <= ASPECT_TOL:
            self.region_rel = saved["region_rel"]
            self._calib_pitch = self._sanity_pitch(
                float(saved.get("pitch") or 0) or None,
                int(saved.get("rows") or 0) or None)
            self._calib_rows = int(saved.get("rows") or 0) or None
            ok, region, _err = self.resolve_region(check_obscure=False)
            if ok:
                self.region = region
                self._refresh_info()
                self.say("已接上客户端并带出上次标定 · 直接点【自动滚采】即可")
                # 老用户手里只有 输出/区域.json，没有预设文件。
                # 第一次跑新版时顺手把现有配置迁成预设，之后就不用再标定了。
                if not PRESET.exists():
                    save_preset(self.region_rel, self._calib_pitch, self._calib_rows,
                                frame=content_rect(self.win.hwnd) or self.win)
                return
            self.region_rel = None
            self._calib_pitch = self._calib_rows = None

        # 本机配置没有（换机器 / 删了 输出/ / 配置版本作废）时，用固化预设接住。
        # 这是"标定一次就够"的兑现点：只要 16:9，任何分辨率都按比例换算。
        preset = load_preset()
        if preset and preset["region_rel"]:
            pa = float(preset.get("aspect") or 0.0)
            if not pa or not self.win.aspect or \
                    abs(pa - self.win.aspect) / self.win.aspect <= ASPECT_TOL:
                self.region_rel = preset["region_rel"]
                self._calib_pitch = (preset["pitch_rel"] * self.win.h) or None
                self._calib_rows = preset.get("rows") or None
                ok, region, _err = self.resolve_region(check_obscure=False)
                if ok:
                    self.region = region
                    self._preset_used = True
                    self._save_cfg()          # 落到本机配置，下次走快路径
                    self._refresh_info()
                    self.say(f"已套用内置预设（{preset['resolution']} 上标定的）"
                             f"· 直接点【自动滚采】即可")
                    log("preset", f"套用预设 每屏 {self._calib_rows} 行 "
                                  f"行距 {self._calib_pitch} 原分辨率 {preset['resolution']}")
                    return
                self.region_rel = None
                self._calib_pitch = self._calib_rows = None

        self._refresh_info()
        self.say("已接上客户端 · 点【标定昵称列】量一次（有内置预设时可直接滚采）")

    def resolve_region(self, check_obscure: bool = True):
        """把相对比例换算成当前屏幕坐标，并做全部前置校验。

        每次截图前都走一遍：窗口可能被挪、被缩、被最小化、被别的窗口盖住。
        基准是「有效画面」而不是客户区——见 content_rect 的说明。
        返回 (是否可用, 绝对坐标, 给用户看的原因)。
        """
        if self.win is None:
            return False, None, "还没接上游戏窗口——打开客户端后会自动接上"
        if not self.region_rel:
            return False, None, "昵称列还没标定 —— 需要先标定一次采集区域"

        info = content_rect(self.win.hwnd)
        if info is None:
            return False, None, "⚠ 游戏窗口找不到了（可能已关闭或最小化）—— 还原窗口后会自动重新接上"

        # 比例是硬约束：UI 锚点跟着画面比例走，比例变了百分比坐标就是错的。
        # 这里不自动改窗口尺寸——动用户的窗口比让他自己拖一下危险得多。
        # 外框尺寸只写日志不写提示：用户要的是"去把窗口调成 16:9"这一个动作，
        # 外框数字是给他发诊断包时我们看的。
        if abs(info.aspect - TARGET_ASPECT) / TARGET_ASPECT > ASPECT_TOL:
            fw, fh = frame_size(info.hwnd)
            log("region", f"画面比例不对：画面 {info.w}×{info.h}"
                          f"（{aspect_label(info.aspect)}）· 外框 {fw}×{fh}"
                          f"（{aspect_label(fw / fh) if fh else '?'}）", "WARN")
            return False, None, (
                f"⚠ 有效画面 {info.w}×{info.h}（{aspect_label(info.aspect)}）不是 16:9 —— "
                f"客户端 UI 会跟着比例重排，把窗口调成 16:9"
                f"（1600×900 / 1920×1080）后会自动接上"
            )
        if info.h < MIN_CLIENT_H:
            return False, None, f"⚠ 有效画面只有 {info.h}px 高，窗口太小，请放大后重试"

        l, t, r, b = self.region_rel
        x0 = info.x + int(round(l * info.w))
        y0 = info.y + int(round(t * info.h))
        x1 = info.x + int(round(r * info.w))
        y1 = info.y + int(round(b * info.h))
        if x1 - x0 < 40 or y1 - y0 < 20:
            return False, None, "⚠ 换算后的区域太小（窗口被缩得过小），请放大窗口或重新标定"

        if check_obscure:
            who = obscurer((x0, y0, x1, y1), self.win.hwnd)
            if who:
                return False, None, f"⚠ 采集区被「{who}」挡住了，请把它移开或最小化再试"

        return True, (x0, y0, x1, y1), ""


    def _sanity_pitch(self, pitch, rows):
        """行距 × 行数 应该大致铺满 ROI 高度；差太远说明这个行距是错的。

        实测踩过：手动框选量出 39px，而同一块 356px 高的五行列表真实行距是 71px
        ——拟合落在了子格点上（头像边框、分隔线的间距正好是行距的一半）。
        错的初值不会采错数据（步长有闭环），但要多试两步才收敛，所以顺手修掉。
        """
        try:
            p, r = float(pitch or 0), int(rows or 0)
        except Exception:
            return pitch
        if not p or not r or not self.region_rel or self.win is None:
            return pitch
        est = (self.region_rel[3] - self.region_rel[1]) * self.win.h / r
        if est > 0 and abs(p - est) / p > 0.35:
            log("calib", f"行距 {p:.0f}px 与「区域高÷行数」推算 {est:.0f}px 不符，"
                         f"改用推算值", "WARN")
            return round(est, 1)
        return pitch


    def _screen_rect(self):
        """当前有效画面的 WinInfo，顺手校验比例。返回 (info, 拒绝原因)。"""
        if self.win is None:
            return None, "还没接上游戏窗口——打开客户端后会自动接上"
        info = content_rect(self.win.hwnd)
        if info is None:
            return None, "游戏窗口找不到了（可能已关闭或最小化）"
        if abs(info.aspect - TARGET_ASPECT) / TARGET_ASPECT > ASPECT_TOL:
            return None, (f"有效画面 {info.w}×{info.h}（{aspect_label(info.aspect)}）"
                          f"不是 16:9，导航锚点会错位，先把窗口调成 16:9")
        return info, ""


    def _nav_focus(self) -> bool:
        """截图前把游戏顶到前台。

        为什么必须是硬前置：工具窗口盖在游戏上时，截回来的是**本工具自己的
        界面**，OCR 一样能认出一堆字，但全是错的——页指纹判不准、也不会抛异常，
        外部表现只是"导航说判断不出在哪一页"。这类静默错误查起来极费劲，
        所以在唯一的入口上把它堵死。
        """
        if self.win is None:
            return False
        if is_foreground(self.win.hwnd):
            return True
        if focus_window(self.win.hwnd):
            time.sleep(0.30)
            return is_foreground(self.win.hwnd)
        return False


    def _look(self, info, path: Path | None = None):
        """拍一屏并读出全图文字。返回 (图, [(文本, 比例框, 置信度)])。"""
        img = grab((info.x, info.y, info.x + info.w, info.y + info.h))
        if path is not None:
            img.save(path)
        return img, read_screen(img)


    def nav_to_members(self):
        """一键从主页走到联盟成员页。

        交互约定：点了就自己走，中途不弹窗问；任何一步判不准就整趟停下并
        说清停在哪。宁可少做一步，也不在没认出来的页面上点一下。
        """
        if self._nav_running:
            self._nav_cancel = True
            self.say("正在取消导航…")
            return
        if self._continuous:
            self.say("先停掉自动滚采再导航")
            return
        info, why = self._screen_rect()
        if info is None:
            self.say(f"⚠ {why}")
            return
        self._nav_running = True
        self._nav_cancel = False
        self._nav_ui(True)
        log("nav", f"开始导航 · 画面 {info.w}×{info.h} @{info.x},{info.y}")

        def work():
            try:
                # 先抢前台再开跑：第一屏就要截图，此刻工具窗口还在最前面
                if not self._nav_focus():
                    log("nav", "抢不到前台，导航取消", "WARN")
                    self._post(self.say, "⚠ 抢不到游戏前台（点一下游戏窗口再试），导航取消")
                    return
                self._nav_run(info)
            except Exception as exc:
                log_exc("nav", exc)
                self._post(self.say, f"导航出错：{exc}")
            finally:
                self._post(self._nav_done)

        threading.Thread(target=work, daemon=True).start()


    def _run_temp(self) -> Path:
        """本次运行的缓存目录 `输出/<时间戳>/temp`。

        照 ArcanaHelper 的做法：一次运行一个目录，里面的截图只服务这次运行。
        好处是"最近一屏"和"失败现场"不会互相覆盖，打包日志时也只需要收一个目录。
        """
        if self._run_dir is None:
            now = datetime.now()
            self._run_dir = OUT_DIR / f"{now:%Y-%m-%d_%H-%M-%S}_{now.microsecond // 1000:03d}"
            log("run", f"本次缓存目录：{self._run_dir / 'temp'}")
        d = self._run_dir / "temp"
        d.mkdir(parents=True, exist_ok=True)
        return d


    def _failure_roi(self, tag, img, rect, info):
        """把"没认出来"的那一帧原图和双坐标一起落盘。

        ArcanaHelper 的 `LocalLog.failure_roi` 是同一个思路：失败只留一行
        "匹配失败"根本没法复盘，必须给出 client_roi（画面内坐标）/ screen_roi
        （屏幕坐标）/ client_size（当时画面多大）和那一帧图——尺寸变了坐标就会漂，
        事后靠回忆补不出来。
        """
        try:
            p = self._run_temp() / f"{datetime.now():%Y%m%d_%H%M%S_%f}_{tag}.png"
            img.save(p)
            log("fail", f"匹配失败 scene={tag} client_roi={tuple(rect) if rect else None} "
                        f"screen_roi={(getattr(info, 'x', '?'), getattr(info, 'y', '?'))} "
                        f"client_size=({getattr(info, 'w', '?')},{getattr(info, 'h', '?')}) "
                        f"file={p}", "WARN")
            return p
        except Exception as exc:
            log_exc("fail", exc)
            return None

    # ---------------- 同步（预留接口）


    def _probe_total(self):
        """读页面上的「联盟成员 (n/m)」，把 m 当成本轮应有的人数。

        为什么非要这个数：滚采期间所有判据只能证明"没跳步"，证明不了"起点真的
        在第一行"。工具自己说了不算，页面上那个 n/m 才是唯一能自证的硬证据
        （实测采到 32 人、页面 32/32，才敢说这轮是完整的）。
        """
        if self.win is None:
            return None
        info = content_rect(self.win.hwnd)
        if info is None:
            return None
        try:
            img = grab((info.x, info.y, info.x + info.w, info.y + info.h))
            blocks = read_screen(img, min_score=0.3, rel=False)
        except Exception as exc:
            log_exc("total", exc)
            return None
        pat = re.compile(r"(\d{1,3})\s*[/／]\s*(\d{1,3})")
        for txt, _b, _s in blocks:
            m = pat.search(txt)
            if not m:
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if 0 < a <= b <= 300:
                log("total", f"页面读出成员总数 {b}（原文「{txt}」）")
                return b
        log("total", "页面上没找到 n/m 形式的人数标记，本轮不核总数", "WARN")
        return None


    def _set_total(self, n):
        """把读到的总数写进台账并推进"校人数"这一步（主线程调用）。"""
        if n:
            self._total_hint = n
            self._flow(2, "done", 40)
            self._task(msg=f"成员页已就位 · 页面显示共 {n} 人")
        self._refresh_ledger()


    def _confirm_scene(self, tries=2):
        """开跑前连续确认"确实在联盟成员页"（对应阿卡的「连续=2/2」）。

        为什么值得多花一秒：滚采的每一条判据都以"列表就在眼前"为前提。
        用户要是没进成员页就点了开始，接下来的拖动会滚别的页面，采到的是一屏
        不相干的字，还可能完全不报错——这类错误比崩溃难查得多。

        返回 (ok, 说明)。**认不出页面时返回 True**：认不出 ≠ 不在成员页，
        拿"认不出来"拦人只会让工具凭空难用。
        """
        info, why = self._screen_rect()
        if info is None:
            return True, why
        hit = 0
        for i in range(max(1, tries)):
            if i:
                time.sleep(0.35)
            try:
                _img, blocks = self._look(info)
            except Exception as exc:
                log_exc("scene", exc)
                return True, "场景识别出错，跳过校验"
            rows = classify_scene([b[0] for b in blocks])
            top = rows[0] if rows else None
            log("scene", f"页面判定 {i + 1}/{tries} · 最终选择="
                         f"{top['name'] if top else '认不出'} · 连续={i + 1}/{tries}。"
                         + scene_line(rows))
            if top is None or not top["ok"]:
                return True, "页面认不出，跳过校验"
            if top["key"] != "members":
                return False, f"当前在「{top['name']}」而不是联盟成员页"
            hit += 1
        return True, f"已在联盟成员页（连续 {hit} 次确认）"


    def _nav_done(self):
        self._nav_running = False
        self._nav_ui(False)
        # _nav_top_ok 只有真正走到"拉回第一行"那一步才会被赋值，用它判导航是否到位
        if self._nav_top_ok is not None and not self._nav_cancel:
            self._flow(1, "done", 26)


    def _nav_wait_until(self, step):
        """点完之后等页面真的换过去。

        只认"目标页的指纹出现"，不认"过了多少秒"——加载快慢取决于机器和网络，
        固定延时要么白等，要么在还没加载完时就判定失败。
        """
        nxt = step.need
        if not nxt:
            return True, ""
        last = ""
        for _ in range(NAV_SETTLE_TRIES):
            if self._nav_cancel:
                return False, "已取消"
            time.sleep(0.25)
            info, why = self._screen_rect()
            if info is None:
                last = why
                continue
            _img, blocks = self._look(info)
            texts = [b[0] for b in blocks]
            if need_ok(texts, nxt):
                return True, ""
            last = (f"只命中 {hit_words(texts, nxt)}/{len(nxt)} 个词："
                    + " ".join(texts)[:60])
        return False, last


    def _nav_at_members(self):
        """试拍昵称列，判断是不是已经站在成员列表上了。

        这是终点唯一可靠的判据：成员页没有稳定的 UI 关键词，但它一定有一列
        昵称。用"能不能扫出 ≥3 行"当指纹——验的就是接下来要干的事。
        没标定过就等于没法验，返回 None（不当作"已在"）。
        """
        if not self.region_rel:
            return None
        try:
            ok, region, _e = self.resolve_region(check_obscure=False)
            if not ok:
                return None
            rows, _bands, _p = scan(grab(region))
            return len(rows)
        except Exception as exc:
            log_exc("nav", exc)
            return None


    def _nav_scroll_top(self, region, trust=None):
        """把名单拉回第一行。返回 (是否确认到顶, 真正滚动的次数)。

        **必须走"控制这个列表时实测有效的那一种输入"。** 联盟列表只吃拖动、
        不吃滚轮。早期版本固定用滚轮向上，结果画面一动不动 → 判"未确认" →
        列表留在底部 → 下一轮自动滚采一步都推不动，报"起步就滚不动"。实测连跑
        三轮全废在这里，而日志上完全看不出异常（滚轮失效和"已在顶部"长得一样）。

        回滚不需要精确，所以拖动距离直接放到 DRAG_MAX（约 6 行），十几次就能回顶。

        两个坑必须避开：
          · 第一次滚动的 diff 不可信——光标刚从别处移进列表，悬停高亮本身就
            制造了差异（实测 1.35，高于 MOVE_EPS）。所以要求"画面连续静止"
            且"至少真的动过一次"，否则一次不动就判到顶。
          · 客户端不吃滚轮时，表现和"已经在顶部"一模一样（都是画面不动）。
            这两者只能靠"以前滚轮管不管用"来区分：存盘的 scroll_mode 是 wheel
            （或本次采集里滚轮确实推动过列表）就信它，一次不动即到顶；否则
            保守报"未确认"，让用户手动拉一下，而不是骗自己。

        trust=None 时自动取上面的历史证据，测试可以显式指定。
        """
        mode = self.scroll_mode or SCROLL_MODES[0]
        if trust is None:
            seen = getattr(self, "_mode_ok", None) or set()
            trust = (mode == "drag") or (mode in seen)
        # 回滚一次走 2 行（2×行距 + 起手死区）。原来只按 1 个行距给距离，而拖动前
        # 十几像素会被游戏当手势死区吃掉，真实位移只有 0.8 行——32 人的盟要按三十
        # 多次才到顶，中途还更容易被死区卡成"名字没变"。2 行 + 死区 = 157px，
        # 远不到"一步跨掉整屏"的那一档，安全性没变。
        step = max(MIN_DRAG_PX,
                   int(round((self._pitch or 71.0) * 2
                             + getattr(self, "_slop", DRAG_SLOP))))
        frame = content_rect(self.win.hwnd) if self.win is not None else None
        prev_names = None
        moved = 0
        ever_moved = False      # 真的移过内容（不是"循环了几次"）
        still = 0               # 连续几次名字没变
        for _i in range(NAV_TOP_TRIES):
            if self._nav_cancel:
                return False, moved
            scroll_once(mode, region, 1, step, up=True, fast=True)
            park_cursor(region, frame)      # 悬停高亮会冒充"画面在动"，先请出去
            # **必须等画面停稳再判位置。** 原来固定 sleep(0.18) 就截图，而反向
            # 拖动带惯性、到顶还有回弹，0.18s 时列表常常还在飞——这时截图截到的
            # 是半格错位的一屏（某一行被切掉一半），名字看起来"变了"，`still`
            # 永远归零，于是怎么滚都判不出到顶。实测因此连滚 30 次仍停在离顶部
            # 7 行的位置，白白漏掉前 7 个人。
            try:
                # min_wait 必须够长（0.5s）。反向拖动是带甩动的，甩到顶会触发
                # 弹性回弹，而**回弹到最高点那一瞬间速度也是 0**——SettleJudge
                # 会把它判成"已停稳"，于是截到的是弹回来两三行的那一帧。
                # 实测表现就是"往上拖一下反而往下走了两行"（#4 HGOLIVER →
                # #5 空木），`still` 永远清零，连滚 30 次判不出到顶。
                img, _t, _still2 = wait_settled(region, None, min_wait=0.50,
                                                timeout=2.2)
                rows, _b, _p = scan(img)
            except Exception as exc:
                log_exc("nav", exc)
                break
            cur = [n for _y, n, _s in rows]
            log("nav", f"回顶 #{_i + 1} · 首行 {cur[:1]} · 共 {len(cur)} 行 · "
                       f"停稳={_still2} · 用时 {_t:.2f}s")
            if prev_names is not None:
                # **到顶的判据必须看名字，不能看画面差。** 列表到顶后再往下拖，
                # 触发的是回弹/下拉动画：画面一直在动（实测连滚 30 次都停不下来），
                # 但内容一个字都没变。名字序列没变才是真的到底了。
                # 参数顺序是反的：回滚时"这一屏的内容更靠前"，所以问的是
                # "上一屏那几行现在排第几"。顺序写反会永远判成 none。
                _r, _ov, verdict, _note = judge_roll(cur, prev_names)
                if verdict == "stuck":
                    still += 1
                    # 连续两次没变才算到顶：单次拖动失手（游戏正忙、回弹未完）
                    # 也会让它"看起来没动"，一次就判会停在半山腰，而且停在那里
                    # 还会被当成"已到顶"——实测因此少采了前面 10 个人。
                    if still >= 2 or (still >= 1 and not ever_moved and trust):
                        if ever_moved:
                            return True, moved
                        # 从头到尾一动没动：本来就在第一行，或这种输入压根推不动
                        return (True, 0) if trust else (False, 0)
                    prev_names = cur
                    moved += 1
                    continue
                still = 0
                if verdict != "none":
                    ever_moved = True
            moved += 1
            prev_names = cur
        log("nav", f"回滚 {moved} 次仍未确认到顶（可能列表很长或拖动未生效）", "WARN")
        return (True, moved) if moved else (False, 0)


    def _nav_top_then(self, rows):
        """到位之后的收尾：把列表拉回第一行，再给结论。

        为什么要拉回顶部：上一轮采集总是停在列表底部，下一轮如果就地从中间
        开始，前面那截人永远采不到——而且日志上完全看不出来，只会表现为
        "这次比上次少了十几个人"。
        """
        ok, region = False, None
        try:
            ok, region, _e = self.resolve_region(check_obscure=False)
        except Exception as exc:
            log_exc("nav", exc)
        if not ok or region is None:
            self._post(self.say, f"✅ 已到联盟成员页（认出 {rows} 行）——"
                                 f"滚到最上面后点【自动滚采】")
            self._post(self._set_total, self._probe_total())
            return
        self._post(self.say, "已到位，正在把列表拉回第一行…")
        mode = self.scroll_mode or SCROLL_MODES[0]   # 与 _nav_scroll_top 内部取值保持一致
        done, n = self._nav_scroll_top(region)
        self._nav_top_ok = True if done else False
        if done:
            log("nav", f"已拉回第一行（{SCROLL_MODE_CN.get(mode, mode)} 反向 {n} 次）")
            self._post(self.say, f"✅ 已到联盟成员页 · 列表已回到第一行（认出 {rows} 行）"
                                 f"——点【自动滚采】开始")
        else:
            log("nav", f"拉回第一行未确认（滚了 {n} 次画面都不动）", "WARN")
            self._post(self.say, f"✅ 已到联盟成员页，但没能确认列表在第一行"
                                 f"（{SCROLL_MODE_CN.get(mode, mode)}往回滚没反应）"
                                 f"——请手动拉到最上面再点【自动滚采】")
        # 到这一步页面一定在成员页，顺手把 (n/m) 读出来：既推进"校人数"这一步，
        # 也给收工对账留一个总数
        self._post(self._set_total, self._probe_total())


    def _nav_run(self, info):
        # 已经在成员页就别折腾了：判据是"昵称列能扫出 ≥3 行"，比任何 UI 关键词
        # 都贴近事实。这一步很重要——用户第二次点「进成员页」时如果已经在目标
        # 页，按原来的逻辑会认不出页面 → 报错中止，白白吓人一次
        rows0 = self._nav_at_members()
        if rows0 is not None and rows0 >= 3:
            log("nav", f"已经在成员页上了（试拍认出 {rows0} 行），不需要导航")
            self._nav_top_then(rows0)
            return

        step_i = 0
        while step_i < len(NAV_SPEC):
            step = NAV_SPEC[step_i]
            if self._nav_cancel:
                log("nav", "用户取消导航")
                self._post(self.say, "已取消导航")
                return
            if not self._nav_focus():
                log("nav", "中途失去前台，导航中止", "WARN")
                self._post(self.say, "⚠ 游戏不在前台了（可能被切走），导航中止")
                return
            img, blocks = self._look(info)
            texts = [b[0] for b in blocks]
            here, hits = nav_which(texts, NAV_SPEC)
            log("nav", f"第 {step_i + 1}/{len(NAV_SPEC)} 步 · 判定在「"
                       f"{here.name if here else '认不出'}」（指纹 {hits} 词）")
            self._nav_where = here.key if here else None

            # 已经站在"点完之后"该到的页：直接跳过这一步，不重复点
            if nav_skip_step(step, texts):
                log("nav", f"已经过了「{step.name}」这一关，跳过")
                step_i += 1
                continue

            x, y, why = nav_decide(step, blocks, texts)
            if x is None:
                # 判不准就先按一次 ESC 清弹窗（公告、签到奖励都会挡页面），
                # 最多 NAV_ESC_TRIES 次；再不行就停——绝不在认不出的页面上点
                for k in range(NAV_ESC_TRIES):
                    if self._nav_cancel:
                        break
                    log("nav", f"「{step.name}」判不准（{why}），按 ESC 清一层（{k + 1}/"
                               f"{NAV_ESC_TRIES}）", "WARN")
                    if not focus_window(self.win.hwnd):
                        self._post(self.say, "⚠ 切不到游戏前台，导航中止")
                        return
                    tap_key(VK_ESCAPE)
                    time.sleep(0.45)
                    info, _why2 = self._screen_rect()
                    if info is None:
                        break
                    img, blocks = self._look(info)
                    texts = [b[0] for b in blocks]
                    x, y, why = nav_decide(step, blocks, texts)
                    if x is not None:
                        break
                if x is None:
                    log("nav", f"第 {step_i + 1} 步放弃：{why}", "WARN")
                    self._failure_roi(f"nav_{step.key}", img, None, info)
                    self._post(self.say, f"⚠ 停在「{step.name}」这一步：{why}")
                    return

            px = info.x + int(round(x * info.w))
            py = info.y + int(round(y * info.h))
            # 落点再验一次：nav_decide 已经保证在白名单里，这里是最后一道闸，
            # 万一以后有人改了坐标换算，也不至于把点击送出去
            if not (info.x <= px <= info.x + info.w and info.y <= py <= info.y + info.h):
                log("nav", f"落点 ({px},{py}) 超出画面，拒绝点击", "WARN")
                self._post(self.say, "⚠ 算出的落点不在画面内，导航中止")
                return
            log("nav", f"点「{step.target}」@({px},{py}) · {why}")
            if not focus_window(self.win.hwnd):
                self._post(self.say, "⚠ 切不到游戏前台，导航中止")
                return
            click_at(px, py)
            self._post(self.say, f"导航：已点「{step.target}」，等界面切换…")
            time.sleep(step.wait)

            if step.need:
                ok, detail = self._nav_wait_until(step)
                if not ok:
                    log("nav", f"点了「{step.target}」但没进到下一页：{detail}", "WARN")
                    self._post(self.say, f"⚠ 点了「{step.target}」但页面没变（{detail}）"
                                         f"——可能被弹窗挡住了，导航中止")
                    return
                log("nav", f"已进入下一步应到的页面")
            step_i += 1

        # 终点：成员列表。没有独立的 UI 关键词，改用"采集区能扫出行"来验——
        # 这比再编一组关键词可靠，因为验的就是接下来要干的那件事
        if self._nav_cancel:
            self._post(self.say, "已取消导航")
            return
        rows = self._nav_at_members()
        if rows is None:
            log("nav", "导航走完，但还没标定昵称列，无法确认是否到了列表", "WARN")
            self._post(self.say, "导航走完了，但昵称列还没标定，确认不了到没到——"
                                 "点【标定昵称列】量一次")
        elif rows >= 3:
            log("nav", f"已到联盟成员页（试拍认出 {rows} 行）")
            self._nav_top_then(rows)
        else:
            log("nav", f"导航走完，但试拍只认出 {rows} 行", "WARN")
            self._post(self.say, f"⚠ 导航走完了，但昵称列只认出 {rows} 行——"
                                 f"确认列表在第一行，或点【标定昵称列】")

    # ---------------- 标定


    def _begin_auto(self):
        """自动滚采的实际启动（**无对话框**）——客户端 headless 桥直接调这里。

        `toggle_auto` 只负责询问与前置检查；真正的状态初始化、抢前台、页面
        确认、第一屏调度都在这里，两处共用同一份，判据不复制。
        """
        log("auto", f"开始自动滚采 · 已有 {len(self.names)} 人 · "
                    f"滚轮 {self._roll_gap()} 格/次 · "
                    f"window={self.win.describe()}@{self.win.x},{self.win.y}")
        self._continuous = True
        self._log_mode(True)              # 先撑开日志区：采集期间要看现场
        self._empty_rounds = 0
        self._prev_names = set()
        self._prev_ordered = []           # 上一屏按 y 排序的名字，用于序列对齐算滚动行数
        self._fling = FLING_INIT          # 每次开跑重新标定：不同列表惯性不一样
        # 标定已经量过行距，直接当步长初值——省掉第一轮"猜 2 倍惯性"的浪费
        self._pitch = self._calib_pitch
        self._last_dist = None
        self._jumps = 0
        self._recover_target = []
        self._recover_seen = 0
        self._recover_walks = 0
        self._slop = float(DRAG_SLOP)     # 死区每轮重新估：不同分辨率不一样
        self._step_boost = 1
        self._stuck_retry = 0
        self._mode_ok = set()
        self._roll_count = 0
        self._last_diff = None
        self._mode_tried = set()
        self._roll_before = None
        self._settle_t0 = 0.0
        self._judge = SettleJudge()
        self._cursor_home = cursor_pos()   # 记下用户的光标位置，停的时候还回去
        # 昵称库的"本轮"从这里起算：开跑时给库拍个快照，收工拿它算新增/未见
        self._roster_prev = json.loads(json.dumps(self._roster.get("names") or {}))
        self._session_t0 = datetime.now()
        self._fix_cnt = {}
        self._gate = {}
        self._twin = {}
        self._doubt_said = set()
        self.region = None
        # 先让游戏回到前台，否则第一屏就会因为被本窗口挡住而判定为遮挡
        if not focus_window(self.win.hwnd):
            self._continuous = False
            log("auto", "focus_window 失败，前台仍未切到游戏", "WARN")
            msg = (
                f"⚠ 切不到前台：「{self.win.title[:20]}」没能拿到焦点，"
                f"滚轮等于发给了别的窗口，列表不会动。\n"
                f"请手动点一下游戏窗口（让它真的在最前面），再点自动滚采。"
            )
            self.say(msg)
            if not getattr(self, "_headless", False):
                self._warn("切不到前台", msg)
            return
        self._focused = True
        log("auto", "已确认游戏在前台")
        self._paused = False
        self._sync_run_buttons()
        self._task(msg="滚采中… 请不要动鼠标", step=3, state="doing", pct=42)
        self.say("自动采集中… 请不要动鼠标")
        # 第一屏之前也要清场：用户点【自动滚采】时鼠标正停在这个按钮上，
        # 但上一轮可能把光标留在了名单里。第一屏若压着光标，整份名单的
        # 起点就是脏的，后面再稳也补不回来。
        # info 原来只是 toggle_auto 的局部变量，提取成方法后必须重新取一次
        # （2026-11-07 真机两次报 name 'info' is not defined 就是这里）
        info = content_rect(self.win.hwnd) if self.win is not None else None
        ok0, region0, _e0 = self.resolve_region(check_obscure=False)
        if ok0 and info is not None:
            park_cursor(region0, info)
        # 开跑前先确认页面。这一步比"多等 0.4 秒画面静止"更靠前：页面不对的话，
        # 后面所有精细的滚动判据都没有意义。
        self._task(msg="开跑前确认页面…", step=2, state="doing", pct=38)
        ok_scene, why_scene = self._confirm_scene()
        if not ok_scene:
            self._continuous = False
            self._sync_run_buttons()
            # 不弹窗：界面本身就是竖版常驻面板，状态栏 + 步骤条变红已经说清楚了，
            # 再弹一个模态框只会挡住用户点【进成员页】，还给脚本化测试添一个死锁点。
            log("scene", f"开跑前页面校验不通过：{why_scene}", "WARN")
            self.say(f"⚠ 现在在「{why_scene}」，不是成员页 —— 先点【进成员页】再开始")
            self._flow(2, "fail")
            return
        self._task(msg=f"✅ {why_scene} · 开始滚采", step=3, state="doing", pct=41)
        self._still_t0 = time.time()
        self._still_probe = None
        self.after(420, self._wait_still)


    def _wait_still(self):
        """开跑前等画面彻底静止，再采第一屏。

        为什么必须等：导航收尾刚把列表拖回顶部，列表往往还在回弹/惯性里。
        这时候立刻拖第一下，残留惯性跟新拖动叠加——实测拖 99px 走掉了 5 行
        以上（平时只走 1 行），一屏就跨过去十几个人，而且当场就判 jump 收工。
        多等一秒，换的是整份名单可信。
        """
        if not self._continuous:
            return
        ok, region, _e = self.resolve_region(check_obscure=False)
        if not ok:
            self._tick()
            return
        try:
            cur = grab(region)
        except Exception:
            self._tick()
            return
        prev = self._still_probe
        if prev is not None and frame_diff(prev, cur) < MOVE_EPS:
            log("auto", "画面已静止，开始采第一屏")
            self._still_probe = None
            self._tick()
            return
        self._still_probe = cur
        if time.time() - self._still_t0 > 2.5:
            log("auto", "等画面静止超时（2.5s），按当前画面开跑", "WARN")
            self._still_probe = None
            self._tick()
            return
        self.after(180, self._wait_still)



    def capture(self, auto: bool = False):
        if self._busy:
            return
        # 每轮都重新换算 + 校验：窗口可能被挪、被缩、被最小化、被别的窗口盖住
        ok, region, err = self.resolve_region()
        if not ok:
            log("capture", f"被前置校验拦下（auto={auto}）：{err}", "WARN")
            if auto:
                self._stop_auto(err)
            else:
                self.say(err)
                self._warn("无法采集", err)
            return
        self.region = region
        self._busy = True
        self.say("识别中…")
        log("capture", f"采集 region={region} auto={auto}")

        def work(r=region, a=auto):
            try:
                t0 = time.time()
                img = grab(r)
                found, bands, passed = scan(img)     # 纯 OCR，不许库插手
                log("scan", f"{img.width}x{img.height} · 候选带 {len(bands)} · 过色 {passed} · "
                            f"识别 {len(found)} · {int((time.time() - t0) * 1000)}ms · "
                            f"{[n for _y, n, _s in found]}")
                self._post(self._apply, img, found, bands, passed, a)
            except Exception as exc:
                log_exc("capture", exc)
                self._post(self.say, f"识别失败：{exc}")
                self._post(setattr, self, "_busy", False)
        threading.Thread(target=work, daemon=True).start()

    def _tick(self):
        """采一屏。采完由 _apply 决定是继续滚还是收工。"""
        if not self._continuous:
            return
        if self._paused:
            self.after(250, self._tick)
            return
        if self._busy:
            self.after(200, self._tick)
            return
        self.capture(auto=True)


    def _plan_dist(self, clicks: int = 1) -> int:
        """这一拖该拖多少像素：目标 1.25 行 × 行距 + 起手死区（再乘重试倍数）。

        为什么是"行距 + 死区"而不是"行距"：拖动前十几像素被游戏当成
        区分点击/拖动的阈值丢掉了，不产生滚动。实测拖 71px 只挪 57px（0.8 行），
        相位会一路往后漂，漂到某一步就跨不过行边界——名字不变，被当成"到底了"。
        """
        return plan_drag(self._pitch, self._slop, clicks) * self._step_boost


    def _learn_step(self, rows, found, dist, img=None, prev_img=None):
        """用这一滚的实测结果校正行距和起手死区。返回本次采样到的死区。

        死区**只能靠像素位移量**，不能靠"走了几行"反推：真实位移 1.0 行和
        1.35 行在名字序列上完全一样（都只跨过一个行边界），用它反推会得到
        34~44px 和 0 交替跳的假数据，再把步长推着来回摆——实测就是这个
        see-saw 把步长从 105px 一路顶到 119px。
        像素互相关量的是真实位移，直接 `dist - 位移` 就是死区。
        """
        ys = sorted(y for y, _n, _s in found)
        if len(ys) >= 2:
            diffs = [b - a for a, b in zip(ys, ys[1:]) if b > a]
            if diffs:
                self._pitch = float(np.median(diffs))
        if not rows or not dist or not self._pitch:
            return None
        shift, corr = (None, 0.0)
        if img is not None and prev_img is not None and img.size == prev_img.size:
            shift, corr = measure_vshift(prev_img, img)
        if not shift or corr < 0.55:
            return None                    # 画面不是整体平移（有动画/高亮），这次不学
        # 相关性高说明内容整体平移了 shift 像素；差出来的那截就是被游戏吃掉的手势死区。
        sample = max(0.0, min(60.0, float(dist) - float(shift)))
        self._slop = 0.7 * self._slop + 0.3 * sample
        return sample


    def _do_scroll(self, up: bool = False):
        """滚动一格，并**验证画面真的动了**，然后再采下一屏。

        滚轮注入可能被静默丢弃（游戏不在前台、或本进程权限低于游戏），
        发出去既不报错也不生效。所以不能发完就当成功——滚动前后各抓一帧比对。

        up=True 只在"跳步补采"时用：往回走几小步，把跳过去的那几个人捡回来。
        """
        if not self._continuous:
            return
        if self._paused:
            # 暂停不撒手：这里自旋等，而不是 return 把链条断掉。
            # 断掉之后"继续"就得重新点火，而重新点火的落点和暂停点不一定一致。
            self.after(200, lambda: self._do_scroll(up))
            return
        if self.win is not None and not is_foreground(self.win.hwnd):
            if not focus_window(self.win.hwnd):
                self._stop_auto(
                    "⚠ 游戏窗口丢了前台焦点，滚轮发不进去。"
                    "采集期间请不要点别的窗口，重新开始即可。"
                )
                return
            self._focused = True
            time.sleep(0.25)

        ok, region, err = self.resolve_region()
        if not ok:
            self._stop_auto(err)
            return
        self.region = region
        x0, y0, x1, y1 = region
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2

        try:
            clicks = max(1, min(20, int(self._roll_gap())))
        except Exception:
            clicks = 1

        # 拖之前先清场：光标若还停在名单上，before 帧里就压着光标和悬停高亮，
        # 拿它当基准比画面差会一路失真。
        frame = content_rect(self.win.hwnd) if self.win is not None else None
        park_cursor(region, frame)

        try:
            self._roll_before = grab(region)
        except Exception as exc:
            self._stop_auto(f"滚动前截图失败：{exc}")
            return

        mode = self.scroll_mode or SCROLL_MODES[0]
        self.scroll_mode = mode          # 记住实际用的方式，换方式时才知道哪些试过了
        dist = self._plan_dist(clicks) if mode == "drag" else None
        self._last_dist = dist
        scroll_once(mode, region, clicks, dist, up=up)
        self._roll_clicks = clicks

        # 松手后指针停在拖动终点（列表中央的卡片上）。先把它请出采集区，
        # 否则悬停高亮动画会让下面"等停稳"一直等不到。
        moved = park_cursor(region, frame)

        how = (f"拖 {dist}px（行距 {self._pitch or '?'}px · 死区 {self._slop:.0f}px"
               + (f" · 重试 ×{self._step_boost}" if self._step_boost != 1 else "") + "）"
               if mode == "drag" else f"{clicks} 格")
        log("roll", f"第 {self._roll_count + 1} 次 · {SCROLL_MODE_CN[mode]} {how}"
                    f"{'（反向补采）' if up else ''} → "
                    f"({cx},{cy}) · 前台={is_foreground(self.win.hwnd)} · "
                    f"该点窗口={window_at(cx, cy) == self.win.hwnd}"
                    + (f" · 光标已挪到 {moved}" if moved else ""))

        # 不再"等一个固定时长就截图"，而是轮询到画面真的停稳（或超时兜底）。
        # 同样在 Tk 主线程上，用 after 让出控制权，界面不僵。
        self._settle_t0 = time.time()
        self._judge = SettleJudge(self._roll_before)
        self.after(SETTLE_MIN, self._poll_settle)


    def _poll_settle(self):
        """轮询到画面停稳再采下一屏。判据本身在 SettleJudge 里，与工作线程共用。"""
        if not self._continuous:
            return
        if self.region is None:
            return
        cur = None
        try:
            cur = grab(self.region)
        except Exception:
            pass

        settled = cur is not None and self._judge.feed(cur)
        waited = int((time.time() - self._settle_t0) * 1000)
        if settled or waited > SETTLE_TIMEOUT:
            total = self._judge.total
            if settled:
                tail = f" · 总差 {total:.2f}" if total is not None else ""
                log("settle", f"画面停稳 · 松手后 {waited}ms{tail}")
            else:
                log("settle", f"⚠ 等了 {waited}ms 画面仍在变化"
                              f"（最大帧间差 {self._judge.max_step:.2f}）——按当前帧继续",
                    "WARN")
            self._roll_before = None
            self._after_scroll(total)
            return
        self.after(SETTLE_POLL, self._poll_settle)


    def _after_scroll(self, diff=None):
        """滚动后判定画面动不动，然后采下一屏或停下。diff 由 _poll_settle 量好传进来。"""
        if not self._continuous:
            return
        self._roll_count += 1
        clicks, mode = self._roll_clicks, self.scroll_mode

        if diff is not None:
            self._last_diff = diff
            fg = self.win is not None and is_foreground(self.win.hwnd)
            cn = SCROLL_MODE_CN.get(mode or "", "?")
            logline(f"{cn} {clicks} 格 · 画面差 {diff:.3f} · 前台={fg}")
            if not fg and self.win is not None and focus_window(self.win.hwnd):
                fg = True
                log("auto", "前台被抢走了，已重新抢回", "WARN")
            if not fg:
                self._stop_auto(
                    "⚠ 游戏窗口丢了前台焦点，输入发不进去。"
                    "采集期间请不要点别的窗口，重新开始即可。"
                )
                return
            # 画面差只能说明"像素变了"，联盟列表不吃滚轮时它也能有 1.3 的
            # 假变化（悬停高亮动画），所以不在这里下结论——滚没滚由名字序列判。
            if diff < MOVE_EPS:
                log("auto", f"{cn} 没让画面变化（{diff:.2f}），等名字序列判定", "WARN")

        self.after(80, self._tick)


    def _stop_auto(self, msg: str, complete: bool = False):
        # 导航没确认"列表在第一行"，这一轮采到的东西必然是残缺的（少了前面的人），
        # 一律不许当完整采集——否则昵称库会把前面那些人记成"这次没见到"，
        # 连续两轮就真把它们移出库了。这个坑实测踩过一次（13 人各记了一笔）。
        if complete and getattr(self, "_nav_top_ok", None) is False:
            complete = False
            log("auto", "导航没确认列表回到第一行 → 本轮不算完整采集，库不记帐", "WARN")
        self._continuous = False
        self._focused = False
        # 采集期间光标被挪到角落避让，停的时候还回去——否则用户会找不到鼠标
        if self._cursor_home is not None:
            try:
                set_cursor(*self._cursor_home)
            except Exception:
                pass
            self._cursor_home = None
        # 证据这时候才齐，先把拼写裁定完再报数。裁定可能少掉一两个名字，
        # 所以调用方拼在提示语里的"共 N 人"要跟着改，否则同一句话里两个数打架
        # （实测出现过"✅ 已到列表末尾 · 共 33 人 · 已采 32 人"）。
        self._reconcile()
        # 页面上的 n/m 是硬事实：人数超了必须在进库**之前**裁到一致。
        # 只告警是不够的——实测「腊目夜」「入魔工」各在库里活了两轮才被移出，
        # 期间每一轮都在教库"这两个是成员"。
        trimmed = 0
        if self._total_hint:
            trimmed = self._trim_to_total(self._total_hint)
        # 位置证据定完，才轮到库对账（库只补位置合并管不到的孤行）
        gated, warned = self._roster_apply()
        if gated:
            log("roster", f"收工对账归位 {gated} 处读歪的写法"
                          f"（另有 {warned} 处只点名不改，见上）", "WARN")
        elif warned:
            # 一处都没归位、但库有话说：这条必须留，否则用户只看到"改成 1 人"却
            # 不知道哪儿来的，也不知道该去核哪个名字
            log("roster", f"有 {warned} 处疑似识别错误，已在上文逐个点名，"
                          f"名单按原样保留（库只提示、不替你改）", "WARN")
        # 名单空着就别碰库：窗口丢了、校验没过这类中断会把 rounds 虚增，
        # 而"连续两轮没见到就移出库"是靠这个计数判的
        report = self._commit_roster(complete) if self.names else ""
        msg = re.sub(r"共 \d+ 人", f"共 {len(self.names)} 人", msg)
        # "校人数"这一关的意义就在这：页面写着 32、采到 33，说明有歪读混进来了；
        # 采到 30 说明漏了。两种都必须当场说出来——一张"看着完整"的名单进库，
        # 后面每轮都会跟着错，比报错难查得多。
        if self._total_hint:
            diff = len(self.names) - self._total_hint
            if diff:
                tip = (f"⚠ 页面共 {self._total_hint} 人，采到 {len(self.names)} 人"
                       f"（{'多' if diff > 0 else '少'} {abs(diff)}）")
                why = "多半是同一个名字被读出两种写法" if diff > 0 else "说明中间漏采了"
                log("stop", f"{tip} · {why}", "WARN")
                msg = f"{msg} · {tip}"
            elif trimmed:
                # 裁过就得说，否则用户看到"32 人一致"会以为一次误读都没有
                tip = (f"✅ 已按页面人数裁到 {self._total_hint} 人"
                       f"（去掉 {trimmed} 个只露一屏的误读，见日志 dedup/trim）")
                log("stop", tip)
                msg = f"{msg} · {tip}"
            else:
                log("stop", f"✅ 人数核对一致：页面 {self._total_hint} / 采集 "
                            f"{len(self.names)}")
        log("stop", f"{msg} · 已采 {len(self.names)} 人 · 滚了 {self._roll_count} 次 · "
                    f"最后画面差 {self._last_diff}", "WARN")
        self._paused = False
        self._sync_run_buttons()
        # 收工即入队留档：配置没打开时不联网，但"这一轮采到了什么"必须留下来，
        # 以后接上后端能直接补投（见 sync_client.Outbox）
        try:
            self.do_sync(complete, quiet=True)
        except Exception as exc:
            log_exc("sync", exc)
        # 最后一步"入库"是个判断题：完整采集才点绿，残缺一律点红——颜色比文案先被看到
        self._flow(4, "done" if complete else "fail",
                   100.0 if complete else None)
        self._refresh_ledger()
        self.say(f"{msg} · 共 {len(self.names)} 人" + (f" · {report}" if report else ""))
        # 采集时游戏被顶到前台，这里把工具窗口拽回来并闪一下，免得用户以为卡死
        try:
            self._raise_self()
        except Exception:
            pass
        # 收工再把日志区让回给名单——但放在最后，刚才那一串收尾结论要先看到
        self._log_mode(False)


    def _merge_misreads(self, ordered, rows, overlap, verdict) -> int:
        """把"同一行被读成两种拼写"的两个名字并回一个。返回并掉了几个。

        为什么会多出人来：每个昵称会在相邻两屏里各被读一次，两次字面不一致时
        （实测「非洲晒太阳」→「非洲吧太阳」0.917、「命OVO」→「命UVU」0.775），
        按文本去重就等于把这一行算成两个人，32 人的联盟最后出 34 个名字。

        为什么用位置而不是模糊匹配当主判据：`match_rows` 刚刚对齐出"本屏第 i 行
        = 上一屏第 i+位移 行"，在这个重合区间内两者讲的是**同一物理行**，
        所以字面不同只可能是同一行的两种读法。模糊匹配只当安全阀（见 same_row），
        防的是位移判偏一行的情况。

        只在 `verdict == "ok"` 时做：jump 时位移量只能量到"至少一屏"，
        位置对齐不成立，宁可多一个人也不乱并。

        留哪个拼写（诊断包 184352 实证）：同一趟采集里「非洲晒太阳」被读到
        113 屏、「非洲吧太阳」只出现在最后那一屏；「命OVO」33 屏、「命UVU」
        1 屏。所以按"读到过多少屏"留，置信度只当屏数相同时的第二道。
        不能反着来——读错的那次照样可能给 0.99。

        但当场能拿到的证据可能还不够：实测「村里响当当」第一次出现时就被读成
        「村果响当当」，两个拼写都只见过一次，谁也说不准哪个对。所以这里并掉的
        同时把关系记进 `self._links`，等整趟跑完由 `_reconcile()` 按最终屏数重判。
        """
        if verdict != "ok" or not overlap or not rows or not self._prev_ordered:
            return 0
        shift = int(rows)
        merged = 0
        for i, name in enumerate(ordered[:overlap]):
            j = shift + i
            if j >= len(self._prev_ordered) or i >= len(self._prev_ordered):
                break
            prev_name = self._prev_ordered[j]
            if prev_name == name:
                continue
            if prev_name not in self.names or name not in self.names:
                continue
            if not same_row(prev_name, name):
                continue
            # 不管当场并成哪个，先把"这两个是同一行"记下来，收工时重新算一遍
            self._links.add(frozenset((prev_name, name)))
            # 留哪个拼写：谁被读到的屏数明显少，谁就是读歪的那次。
            # 不能只看置信度——OCR 读错时照样能给出 0.9 的高分，而同一个昵称在
            # 一趟采集里会被读到几十次，出现次数才是更硬的证据（实测「非洲晒太阳」
            # 113 屏 vs「非洲吧太阳」1 屏）。
            # 两边屏数差不多时**当场不合**：证据不足，硬选等于掷硬币（实测
            # 「村里响当当」第一次出现就被读成「村果响当当」，各 1 屏，选错过一次）。
            # 这种交给 `_reconcile()` 收工时按全程屏数定。
            if rarer(name, prev_name, self._hits):
                drop, keep = name, prev_name
            elif rarer(prev_name, name, self._hits):
                drop, keep = prev_name, name
            else:
                continue
            self.names.pop(drop, None)
            # 位置证据坐实的读歪，顺手记成别名：下一轮 `roster_cand` 靠它直接命中，
            # 不再走模糊比对。这是字库唯一的自动扩表渠道——`_roster_apply` 那条
            # 只肯收 ≥0.62 的，0.5 区间永远不会自己攒下东西。
            fx = self.__dict__.setdefault("_fix_cnt", {})
            fx[(drop, keep)] = fx.get((drop, keep), 0) + 1
            log("dedup", f"同一行两种读法：「{drop}」并进「{keep}」"
                         f"（{self._hits.get(keep, 0)} 屏 vs {self._hits.get(drop, 0)} 屏）", "WARN")
            merged += 1
        return merged


    def _note_roster_cand(self, ordered, cur, overlap, verdict):
        """采集途中只"记账"：哪些行看着像库里的谁。**一个都不改。**

        库是补充，不是判据。采集途中任何一刻的证据都不够——一个名字只在某一屏
        露过一次，没法判断它是"读歪的老成员"还是"只露了个面的新成员"。所以
        这里只把候选记下来，真正的裁定放到收工时（`_roster_apply`），那时
        `_hits` 里已经有"这个写法全程出现过几次"这个硬证据。

        记账阶段就排除掉的（这些行连候选都不留）：
          · 库里已有的字面 → 老成员，不用纠
          · 本屏与上屏对齐区间里的行 → 那几行有位置证据兜底，轮不到库说话
          · 参与过位置证据的（`_links`）→ 位置说了算

        唯一被留下来单独记账的是"同屏双疑似"（`_twin`）：本行和它的候选在同一
        屏里各占一行。这一屏不可能把同一个人读两遍（除非那一行被行切分切成了
        两段），所以位置证据不成立、库也不该动名单——但也不能像以前那样一声不
        吭地丢掉，那是唯一一处"库明明有话说却一个字不提"的地方，留着收工点名。
        """
        names = self._roster.get("names") or {}
        if not names or not ordered:
            return
        alias = self._alias_map()
        cur_set = set(cur)
        twins = self.__dict__.setdefault("_twin", {})   # 兼容测试里的半成品 App
        for n in ordered:
            if n in names or n in self._gate:
                continue
            if verdict == "ok" and overlap and n in ordered[:overlap]:
                continue
            if any(n in p for p in self._links):
                continue
            c, ratio = roster_cand(n, names, alias, floor=0.5)
            if c is None or c == n:
                continue
            if c in cur_set:
                twins[n] = (c, ratio)
                continue
            self._gate[n] = (c, ratio)


    def _roster_apply(self) -> tuple:
        """收工时才按库对账：全程看下来，哪些"孤行"确实是老成员被读歪了。

        为什么一定要放到最后：采集途中看到的一个名字只露过一次，无从判断是
        "读歪的老成员"还是"只露了个面的新成员"；而跑完一整趟，`_hits` 里有
        "这个写法全程出现过几次"——**出现两次以上的一律当真成员，库不许碰**。
        这条是"库不能影响相似名称判定"最硬的一道闸：真人每屏都在，一定 ≥2。

        返回 (归位个数, 只提示未归位个数)。
        """
        names = self._roster.get("names") or {}
        pairs = confuse_pairs(names)
        # 这两类都是"库有话说、但一个字都不许改"的：先点名，再谈归位。
        # 放在前面是因为它们和第 6 道闸（≥2 屏）、和 `_gate` 是否为空都无关。
        warned = self._report_twins(names, pairs) + self._report_doubt(names, pairs)
        if not names or not self._gate:
            return 0, warned
        gated = 0
        alias = self._alias_map()
        for n, (c, ratio) in list(self._gate.items()):
            if n not in self.names or n in names:
                continue
            if self._hits.get(n, 0) >= 2:            # 露过两次以上 = 真成员
                continue
            if any(n in p for p in self._links):     # 位置证据后来才表态
                continue
            e = names.get(c) or {}
            # 别名不受"库里至少稳采 2 轮"限制：那条挡的是"本轮刚进来的新人凭字面
            # 吸别人"，而别名是位置证据（相邻两屏同一物理行）钉死过的写法对，
            # 性质上比模糊比对硬一档。`_hits<2` 那道闸仍然管着它，真成员碰不到。
            if not (ratio >= 1.0 and alias.get(n) == c):
                if int(e.get("rounds", 0)) < ROSTER_ROUNDS_MIN and not e.get("seeded"):
                    continue
            if ratio < ROSTER_RATIO:
                # 字面证据不够硬：说出来，但名单一个都不改
                log("roster", f"「{n}」有点像库里的「{c}」（相似 {ratio:.2f} < "
                              f"{ROSTER_RATIO:.2f}）{confuse_note(n, c, pairs)}"
                              f"——证据不够，没自动归位，"
                              f"名单按原样保留，请你自己核一眼", "WARN")
                warned += 1
                continue
            sc, t = self.names.pop(n)
            if c not in self.names:
                self.names[c] = self._seen.get(c, (sc, t))
            log("roster", f"收工对账：「{n}」按库归到「{c}」（相似 {ratio:.2f} · "
                          f"库里已稳采 {e.get('rounds')} 轮 · 本趟只露过 1 次）", "WARN")
            # **这里不记别名。** 这条路径的证据只有"字面像 + 本趟只露过一次"，
            # 方向可能是反的：实测库里有「命OVO」，某轮把它读成「命OVo」正好只露
            # 一次，于是就学成了"命OVO 是歪读、命OVo 才是正名"，之后每一轮都跟着
            # 错下去，名单里凭空多出一个人。别名只由 _merge_misreads 来记——那里
            # 靠"相邻两屏同一物理行"投票，能定方向（实测 4 屏 vs 1 屏）。
            gated += 1
        self._gate.clear()          # 对完账就清空，导出前再调一次也不会重复记账
        if gated:
            self._refresh_rows()
        return gated, warned


    def _report_twins(self, names, pairs) -> int:
        """点名"同屏双疑似"：两个相似写法在同一屏里各占一行。

        为什么不自动并：同一屏不可能把同一个人读两遍（除非那一行的切分崩了，
        真是那样就该去修行切分，而不是改名单），所以这里八成是两个真人；但字形
        上又只差已知易混字，也可能是切分 bug 的产物。两种可能都真，工具不替人
        拍板——只把证据摆出来，名单一个不动。
        """
        twins = getattr(self, "_twin", None) or {}
        if not twins:
            return 0
        n = 0
        for a, (b, ratio) in list(twins.items()):
            if a not in self.names or b not in self.names:
                continue
            ha = self._hits.get(a, 0)
            if ha <= 1:
                tail = (f"要么同一行被行切分切成了两段，要么本来就是两个人")
            else:
                # 每屏都一起出现：要么真有两个近名真人，要么行切分固定多切了一块。
                # 这两种都不该由名单层去猜，指向行切分更靠谱。
                tail = (f"每屏都一起出现，不像偶发误读——要么是两个近名真人，"
                        f"要么行切分固定多切了一块，请检查截图里这两行是不是同一行")
            log("roster", f"「{a}」和「{b}」同一屏里各占一行（相似 {ratio:.2f} · "
                          f"前者本趟 {ha} 屏{confuse_note(a, b, pairs)}）——"
                          f"库不改名单，请核一眼：{tail}", "WARN")
            n += 1
        twins.clear()
        return n


    def _report_doubt(self, names, pairs, cap: int = 5) -> int:
        """点名"孤行可疑"：只露过一屏的野名字，和某个稳定出现的老成员只差易混字。

        这类是位置合并真正的盲区：两个写法虽然像，却没落在相邻两屏的同一物理行
        上（榜单一滚就错开了），`_merge_misreads` 和 `_reconcile` 都没资格说话，
        于是它就作为一个"新人"留在了名单里。库在这里也只能说"可疑"——不能直接
        并，因为"新人刚进会、只被扫到一屏"也是完全合理的。

        四个条件同时成立才点名：只露 1 屏、对手稳定 ≥3 屏、字面像到 `same_row`
        认、且它自己不在库里（在库里说明是两位老成员，不用管）。对手还必须**在
        库里有据**——两边都是野名字时库无话可说，{a} 完全可能只是刚入会、名字
        恰好相近的真人，报了纯属噪声。
        """
        if not names or not self.names:
            return 0
        links = self._links or set()
        twins = set(getattr(self, "_twin", None) or ())
        lone = [n for n in self.names if self._hits.get(n, 0) == 1 and n not in names]
        settled = [n for n in self.names if self._hits.get(n, 0) >= 3 and n in names]
        if not lone or not settled:
            return 0
        # 收工时会调一次、导出前还会再调一次：同一对名字只点名一次，否则日志重复
        said = self.__dict__.setdefault("_doubt_said", set())
        n = 0
        for a in sorted(lone, key=lambda x: -(self._seen.get(x, (0.0, ""))[0])):
            if n >= cap:
                break
            if a in twins:
                continue
            best = None
            for b in settled:
                if a == b or any({a, b} == set(p) for p in links):
                    continue
                if not same_row(a, b):
                    continue
                r = difflib.SequenceMatcher(None, a, b).ratio()
                if best is None or r > best[1]:
                    best = (b, r)
            if best is None:
                continue
            b, r = best
            if (a, b) in said:
                continue
            said.add((a, b))
            log("roster", f"「{a}」全程只露过 1 屏，和库里的「{b}」"
                          f"（{self._hits.get(b, 0)} 屏）相似 {r:.2f}"
                          f"{confuse_note(a, b, pairs)}——位置证据没对上，没并；"
                          f"如果它是读歪的，请自己删掉", "WARN")
            n += 1
        return n


    def _reconcile(self) -> int:
        """收工前按"整趟采到过多少屏"最终裁定每一组同形拼写，返回换掉的个数。

        为什么要等收工：并的当口常常证据不足。实测「村里响当当」第一次出现就被
        读成「村果响当当」，两个拼写各只见了 1 屏，当场无论怎么选都是掷硬币；
        后面几屏「村里响当当」稳稳定定出现了 4 次，答案才浮出来。
        所以当场只做临时合并（免得界面上的数字一路乱涨），真正的裁定放这里。

        证据取 (屏数, 置信度) 的最大值。屏数是主证据：读歪是偶发，读对是每屏都
        对；置信度只在屏数打平时用，因为 OCR 读错时也会给出 0.9 的高分。
        并且只有"屏数差一倍以上"才动手（见 `rarer`），两个都常见的近名真人
        一律不动。
        """
        if not self._links:
            return 0
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for pair in self._links:
            a, b = tuple(pair)
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        groups = {}
        for n in list(parent):
            groups.setdefault(find(n), []).append(n)

        fixed = 0
        for members in groups.values():
            if len(members) < 2:
                continue
            # 同屏共现（_twin：两个相似写法在同一屏各占一行）说明是两个真人，
            # 一票都不投——这是投票唯一可能造成"删真人"的入口，必须先关掉。
            # 注意 _twin 的结构是 {写法: (另一写法, 相似度)}，是 dict 不是集合。
            twins = getattr(self, "_twin", None) or {}

            def co_occur(x, y):
                v = twins.get(x)
                if v and v[0] == y:
                    return True
                w = twins.get(y)
                return bool(w and w[0] == x)

            cooc = any(co_occur(a, b)
                       for k, a in enumerate(members) for b in members[k + 1:])
            consensus = None if cooc else vote_spelling(
                members, self._hits, self._seen)
            best = consensus or max(members, key=lambda n: (self._hits.get(n, 0),
                                                            self._seen.get(n, (0.0, ""))[0]))
            dropped = False
            for n in members:
                if n == best or n not in self.names:
                    continue
                if consensus:
                    # 投票已经把每一位的多数字符定下来，且结果与组内每个读法都过关；
                    # 这时不再要求"少一半以上"——那正是「马三一」和「马三-」各 3 屏
                    # 时谁也并不掉的原因。
                    pass
                elif not rarer(n, best, self._hits):
                    # 没有共识拼写时才退回老判据：只有"明显读得少"的那个才当读歪。
                    # 两个都常常出现的，是同一联盟里两个昵称相近的真人（或两次不同
                    # 的误读），不能动——删真人比留假名严重得多。
                    continue
                self.names.pop(n, None)
                # 同上：收工复核钉死的读歪也要进别名表，不然字库永远学不到这一对
                fx = self.__dict__.setdefault("_fix_cnt", {})
                fx[(n, best)] = fx.get((n, best), 0) + 1
                log("dedup", f"收工复核：这一行应当是「{best}」"
                             f"（{self._hits.get(best, 0)} 屏 vs "
                             f"「{n}」{self._hits.get(n, 0)} 屏），已去掉读歪的写法", "WARN")
                dropped = True
                fixed += 1
            if dropped and best not in self.names:
                sc, t = self._seen.get(best, (1.0, ""))
                self.names[best] = (sc, t)
        if fixed:
            self._refresh_rows()
        return fixed


    def _trim_to_total(self, total) -> int:
        """采到的名字比页面上写的人数还多时，按"可疑度"把多出来的丢掉。

        页面上的 `(n/m)` 是唯一不依赖识别质量的硬事实：m 就是联盟真实人数。
        多出来的必然是误读，问题只是"哪几个"。这一层刻意**不看字面、不看位置
        对齐**——那两套判据各有盲区，实测放过去的两个：

          · 「腊目夜」把「朧月夜」读歪，恰好落在那一屏的最后一行，位置证据没有
            第二次机会（下一屏它已经滚出去了）；
          · 「入魔工」把「大魔王」读成三个完全不同的字，字面相似度只有 0.33，
            比 `same_row` 的 0.5 门槛还低。

        所以只认一条：**真成员每屏都在，误读只闪一两次。** 整趟 24 屏里平均每个
        人出现 3~4 屏，误读全是 1 屏——这个差距比字面相似度可靠得多。

        三道保护，少一道就会把真人当误读删掉：

          · 超出 MAX_TRIM 个就不动手：那是别的问题（比如总数本身读歪了），
            靠丢名字掩盖只会更糟；
          · 只从"屏数 ≤ 1"里挑：真成员至少出现 2 屏，这是下限不是统计猜测；
          · total 要落在合理区间：页面那个数字也是 OCR 读出来的。
        """
        n = len(self.names)
        if not total or not n or n <= total:
            return 0
        lo, hi = TRIM_TOTAL_RANGE
        if not (lo <= total <= hi):
            log("trim", f"页面人数 {total} 不在合理区间 {lo}~{hi}，"
                        f"不做裁剪（数字本身可能读歪了）", "WARN")
            return 0
        over = n - total
        if over > MAX_TRIM:
            log("trim", f"采到 {n} 人、页面 {total} 人，多 {over} 个超过单轮可裁上限"
                        f" {MAX_TRIM} —— 这个量级说明是别的问题，不做裁剪", "WARN")
            return 0
        # 只挑"只露过一屏"的。真成员在 24 屏里平均出现 3~4 屏，露一屏的只可能是误读
        cands = [k for k in self.names if self._hits.get(k, 0) <= 1]
        if len(cands) < over:
            log("trim", f"多出 {over} 个，但只露面一次的只有 {len(cands)} 个 —— "
                        f"只裁这些，剩下的请人工核（可能是两个真成员名字太像）", "WARN")
            over = len(cands)
        if not over:
            return 0
        # 同为一屏时，置信度低的先走
        cands.sort(key=lambda k: (self._hits.get(k, 0),
                                  self._seen.get(k, (0.0, ""))[0]))
        dropped = []
        for k in cands[:over]:
            self.names.pop(k, None)
            dropped.append(k)
        if dropped:
            log("trim", f"按页面人数裁剪：页面 {total} 人 / 采到 {n} 人，去掉露面最少的 "
                        f"{len(dropped)} 个 —— "
                        + "、".join(f"「{k}」{self._hits.get(k, 0)} 屏" for k in dropped),
                "WARN")
            self._refresh_rows()
        return len(dropped)


    def _commit_roster(self, complete: bool) -> str:
        """把本轮结果并进昵称库，返回一句变动摘要（没变化返回空串）。

        `complete` 表示这一轮是不是完整采到了列表末尾——只有完整采完，"库里有人
        这次没见到"才说明得了问题；中途停下的那轮少人太正常了，不能拿它判退会。

        三件事：
          · 本轮采到的每个人 → 刷新 last / 累计轮数 / 最高置信度，清掉 miss
          · 库里上次有、本轮完整采完却没见到 → miss + 1，连满 ROSTER_DROP 轮才删
          · 本轮坐实的读歪写法 → 记成别名，下轮开局就能直接归（比模糊比对更快更准）
        """
        names = self._roster.setdefault("names", {})
        prev = self._roster_prev or {}
        first_time = int(self._roster.get("rounds", 0)) == 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        cur = set(self.names)

        added = []
        for n in cur:
            sc = self.names[n][0]
            e = names.get(n)
            if e is None:
                names[n] = {"first": now, "last": now, "rounds": 1, "miss": 0,
                            "score": sc, "alias": []}
                # 第一次建库时整张名单都是"新增"，报了没意义
                if not first_time and n not in prev:
                    added.append(n)
            else:
                e["last"] = now
                e["rounds"] = int(e.get("rounds", 0)) + 1
                e["miss"] = 0
                e["score"] = max(float(e.get("score") or 0), sc)

        gone = []
        dropped = []
        # 名单里连 3 个人都不到，说明这轮根本没采到东西，不能当作"别人都退会了"
        if complete and len(cur) >= 3:
            for n, e in list(names.items()):
                if n in cur:
                    continue
                e["miss"] = int(e.get("miss", 0)) + 1
                if e["miss"] >= ROSTER_DROP:
                    names.pop(n, None)
                    dropped.append(n)
                    log("roster", f"连续 {ROSTER_DROP} 轮完整采集都没见到「{n}」，已移出昵称库", "WARN")
                else:
                    gone.append(n)

        alias_new = 0
        for (src, dst), cnt in self._fix_cnt.items():
            e = names.get(dst)
            if e is None:
                continue
            al = e.setdefault("alias", [])
            if src not in al:
                al.append(src)
                alias_new += 1
                log("roster", f"记住别名：「{src}」今后直接归到「{dst}」（本轮读到 {cnt} 屏）")

        self._roster["rounds"] = int(self._roster.get("rounds", 0)) + 1
        self._roster["version"] = 1
        # 轮次留档：库里只留"最后一次见到"和 miss 计数，反推不出"是哪一轮不见的"。
        # 界面要回答"这轮谁走了"，就得每轮写一行——而且必须是**判定之后**写，
        # 否则记下的是变动前的人数。
        hist = self._roster.get("history")
        if not isinstance(hist, list):
            hist = self._roster["history"] = []
        hist.append({
            "t": now, "n": len(cur), "total": int(self._total_hint or 0),
            "ok": bool(complete), "add": list(added), "gone": list(gone),
            "drop": list(dropped), "alias": alias_new,
        })
        if len(hist) > ROSTER_HISTORY_MAX:
            del hist[:-ROSTER_HISTORY_MAX]      # 只留最近 N 轮
        if not save_roster(self._roster):
            log("roster", "昵称库写盘失败，本轮结果不会被记住", "WARN")

        parts = []
        if added:
            parts.append(f"新增 {len(added)} 人（{'、'.join(added[:6])}"
                         f"{'…' if len(added) > 6 else ''}）")
        if gone:
            parts.append(f"上次有这次没见到 {len(gone)} 人（{'、'.join(gone[:4])}"
                         f"{'…' if len(gone) > 4 else ''}）")
        if dropped:
            # 移出库是不可逆的（下次进来会被当新人），必须在收工摘要里点名，
            # 不能只留一行 WARN 埋在日志里
            parts.append(f"移出 {len(dropped)} 人（{'、'.join(dropped[:4])}"
                         f"{'…' if len(dropped) > 4 else ''}）")
        if alias_new:
            parts.append(f"记住 {alias_new} 个误读写法")
        if parts:
            log("roster", f"本轮 {len(cur)} 人 · 库内 {len(names)} 人 · " + " · ".join(parts), "WARN")
        else:
            log("roster", f"本轮 {len(cur)} 人 · 库内 {len(names)} 人 · 与上次一致")
        return " · ".join(parts)


    def _alias_map(self) -> dict:
        """把库里每条记录攒下的别名摊平成 {读歪的写法: 正确写法}。"""
        out = {}
        for name, e in list((self._roster.get("names") or {}).items()):
            for a in e.get("alias") or ():
                out[a] = name
        return out


    def _apply(self, img, found, bands, passed, auto):
        self._busy = False
        prev_img = self._last_img          # 上一屏，用来量像素位移（算死区）
        self._last_img = img                # 留给诊断包，能看出当时到底截到了什么
        now = datetime.now().strftime("%H:%M:%S")
        if self._session_t0 is None:
            self._session_t0 = datetime.now()
            self._roster_prev = json.loads(json.dumps(self._roster.get("names") or {}))
        before = len(self.names)
        # 补采（往回走找跳步接点）那几步只用来对齐位置，**一个名字都不记**：
        # 那几行等会儿往前重新走的时候会再读一遍，现在记进来只会把 OCR 的歪读法
        # （实测命OVO 被读成 命OVo / 命oVo）当成新成员灌进库，一轮就多出三个人。
        recovering = bool(self._recover_target)
        for _y, name, score in (() if recovering else found):
            self._hits[name] = self._hits.get(name, 0) + 1
            seen = self._seen.get(name)
            if seen is None:
                self._seen[name] = (score, now)
            elif score > seen[0]:
                self._seen[name] = (score, seen[1])       # 时间记第一次，置信度取最高
            if name not in self.names:
                self.names[name] = self._seen[name]
            elif score > self.names[name][0]:
                self.names[name] = (score, self.names[name][1])
        new = len(self.names) - before
        self._refresh_rows()
        self._draw_preview(img, bands)
        self._refresh_ledger()
        if auto and self._total_hint:
            # 42% 是"开始滚采"，95% 留给"到底"，中间按已采/总数线性推进
            self._task(pct=min(95.0, 42.0 + 53.0 * len(self.names)
                               / max(1, self._total_hint)))
        log("apply", f"auto={auto} 本屏 {len(found)} 行 净增 {new} 累计 {len(self.names)} 人")

        # 框选异常：有若干行的文字色看着就是昵称，却几乎一个都没认出来——
        # 多半是框太窄把字切掉一半，或者框偏了。
        # 注意不能再拿"候选带条数"当判据：玩家横幅现在也会各占一条带，
        # 条数跟"能容纳几行昵称"已经没关系，用它报警会在正常画面上瞎叫。
        bad = (passed >= 2 and len(found) <= 1) or \
              (passed >= 4 and len(found) < passed * 0.5)
        if bad:
            log("apply", f"框选疑似异常 bands={len(bands)} found={len(found)} passed={passed}", "WARN")
            if auto:
                # 自动滚采时没人盯着，这一帧必须留下来：事后才能分清是 OCR 认错、
                # 框切掉了半个字，还是列表本身滚到了空白处
                self._failure_roi("昵称列", img, self.region,
                                  content_rect(self.win.hwnd) if self.win else None)
        if bad and not auto:
            self.say("⚠ 框选可能包含了头像或右侧文字——请重框，只框「昵称这一列」")
            return
        if passed == 0 and len(bands) >= 3 and not auto:
            self.say("⚠ 没找到昵称笔迹，可能框错了位置，或游戏换了配色主题")
            return

        if auto:
            cur = {n for _y, n, _s in found}
            ordered = [n for _y, n, _s in sorted(found, key=lambda t: t[0])]

            # —— 补采模式：正在往回走，找跳步前那一屏 ——
            # 这期间每一步都是"往回"，方向跟正常滚采相反，所以不能走正常判定
            # （否则反向位移会被当成"走 0 行"或"跳步"）。只做一件事：认名字、
            # 盯重合。一旦和跳步前那一屏接上了，说明中间那几个人都补回来了。
            if self._recover_target:
                # 参数顺序：这一屏在前、跳步前那一屏在后。match_rows 只认"后面的
                # 前移了 k 行"，往回走时新的这一屏才是靠前的那个，写反了永远 0 重合
                # （实测因此死循环走满 12 步还没接上）。
                _r, ov2, _v2, _n2 = judge_roll(self._recover_target, ordered)
                self._recover_seen += 1
                log("auto", f"补采第 {self._recover_seen} 步 · 本屏 {ordered} · "
                            f"与跳步前那屏重合 {ov2} 行")
                if ov2 > 0:
                    log("auto", f"✅ 补采接上了跳步前的位置（用了 {self._recover_seen} 步）· "
                                f"累计 {len(self.names)} 人", "INFO")
                    self._recover_target = []
                    self._recover_seen = 0
                    self._recover_walks += 1
                    # 补回来了就等于没漏过人，把跳步计数清掉——否则收工时会
                    # 因为 complete=(_jumps==0) 把这一轮判成"不算完整采集"，
                    # 白白浪费一轮已经采全的数据。
                    self._jumps = 0
                    self._prev_ordered = ordered
                    self._prev_names = cur
                    self._empty_rounds = 0
                    self.after(SCROLL_GAP, self._do_scroll)
                    return
                if self._recover_seen >= JUMP_RECOVER_STEPS:
                    # 往回走了这么多步还没接上：要么跳得太远，要么列表在乱动。
                    # 再走下去只会把整轮采集拖成一片混乱，停下并如实报告。
                    self._recover_target = []
                    self._recover_seen = 0
                    self._stop_auto(
                        f"⚠ 某一步跳过了至少一整屏，往回找了 {JUMP_RECOVER_STEPS} 步也没能"
                        f"接上原来的位置。已采 {len(self.names)} 人，但这**不算完整采集**，"
                        f"昵称库这一轮不记帐。请把名单拉回顶部重跑一遍"
                    )
                    return
                self.after(SCROLL_GAP, lambda: self._do_scroll(up=True))
                return

            rows, overlap, verdict, note = judge_roll(self._prev_ordered, ordered)

            # 同一行在相邻两屏里被读成两种拼写的话，上一步已经按文本把它们当成了
            # 两个人。这里按"行位置"并回去——名单的人数才是用户真正在意的那个数。
            merged = self._merge_misreads(ordered, rows, overlap, verdict)
            if merged:
                new = len(self.names) - before
                log("auto", f"同一行两种读法 → 并掉 {merged} 个重复（净增 {new} · "
                            f"累计 {len(self.names)} 人）", "WARN")

            # 位置证据到此用完。库只在这之后记一笔候选，一个字都不改——
            # 真正的裁定在 _stop_auto 里的 _roster_apply()，那时才看得到全程屏数。
            self._note_roster_cand(ordered, cur, overlap, verdict)

            if verdict != "none":
                log("auto", f"本屏 {len(ordered)} 行 · 与上屏重合 {overlap} 行 → 这次走了 "
                            f"{rows} 行（{SCROLL_MODE_CN.get(self.scroll_mode or '', '?')}）")
                # 只在真的滚动成功时记住方式：到列表底部时任何方式都不动，
                # 那时写盘会把最后一次失败的"翻页键"存进去，下次开局就错
                if rows:
                    self._mode_ok.add(self.scroll_mode)
                    self._persist_mode()

            # 一屏一个名字都没有：多半是滚出界面/弹出了别的东西，先停下，别继续乱滚
            if not ordered and self._roll_count > 0:
                self._stop_auto(
                    "⚠ 这一屏一个昵称都没认出来——可能弹出了成员详情之类的窗口，"
                    "或游戏画面切走了。请关掉多余窗口后重新开始"
                )
                return

            # 名字一个没变，有三种完全不同的情况：
            #   ① 这种滚动方式本来就推不动这个列表（联盟列表实测不吃滚轮）→ 换下一种
            #   ② 这种方式刚才还在推、现在推不动了 → 到列表末尾了，正常收工
            #   ③ **这一步只是没跨过行边界**：拖动前十几像素被游戏当手势死区吃掉，
            #      步长 71px 的真实位移只有 0.8 行，相位漂到某一步就正好卡在两行
            #      中间——名字一个不变，但离到底还远。实测 32 人的联盟因此只采到
            #      6 个人就报"✅ 已到列表末尾"，这是最坑的一种误判。
            # 光看"没新人"分不出这三者，所以：先记"哪种方式真的推动过"，再在
            # 判到底之前用**放大步长**区分 ② 和 ③——真到底的话放大也推不动，
            # 卡在缝隙里的话一步就跨过去了。
            if verdict == "stuck" and new == 0:
                if self.scroll_mode in self._mode_ok:
                    if self._stuck_retry < 1 and self.scroll_mode == "drag":
                        self._stuck_retry = 1
                        self._step_boost = 2
                        log("auto", "名字一个没变，但可能只是卡在两行中间没跨过去 → "
                                    "步长放大一倍再试一次", "WARN")
                        self.after(SCROLL_GAP, self._do_scroll)
                        return
                    self._stop_auto(
                        f"✅ 已到列表末尾：{SCROLL_MODE_CN.get(self.scroll_mode, '?')}"
                        f"再滚画面不再变化 · 共 {len(self.names)} 人 · "
                        f"滚了 {self._roll_count} 次"
                        + (f" · ⚠ 其中 {self._jumps} 步两屏对不上，理论上可能漏人，"
                           f"建议和游戏内成员数核一下" if self._jumps else ""),
                        complete=(self._jumps == 0),
                    )
                    return
                if self._switch_mode():
                    self._fling = FLING_INIT             # 换方式后重新估
                    self.after(SCROLL_GAP, self._do_scroll)
                    return

            # 到底：连续 MAX_EMPTY 轮没有新人
            if new == 0:
                self._empty_rounds += 1
                log("auto", f"连续第 {self._empty_rounds} 轮无新人", "WARN")
                if self._empty_rounds >= MAX_EMPTY:
                    d = self._last_diff
                    tail = f"（最后画面差 {d:.2f}）" if d is not None else ""
                    why = perm_advice(self.win.hwnd) if self.win else ""
                    if not self._mode_ok:
                        # 本次一次都没推动过列表：这不是"方式选错了"，是列表压根没动。
                        # 最常见的原因就是它本来就停在底部——上一轮采完就停在那儿。
                        # （实测 19:06 那次：重开后直接点自动滚采，三种方式轮着推都
                        #   推不动，白等 6 秒才停；用户手动拉回顶部后就一路顺到末尾。）
                        self._stop_auto(
                            f"⚠ 起步就滚不动{tail}：三种滚动方式都推不动这个列表。"
                            f"最常见的原因是名单已经停在底部（上一轮采完就停在那儿）。"
                            f"请先在游戏里把名单拉回顶部，再点【自动滚采】"
                            + (f"；{why}" if why else "")
                        )
                        return
                    self._stop_auto(
                        f"连续 {MAX_EMPTY} 次没有新内容，已停止{tail}"
                        f" · 拖动/滚轮/翻页键都试过，列表都没动"
                        + (f"；{why}" if why else "")
                        + "。若确实没到底，把这个列表的滚动方式告诉我（或发诊断包）",
                        complete=(self._jumps == 0),
                    )
                    return
            else:
                self._empty_rounds = 0

            # 两屏没有重合行：位移量只能量到"至少一屏"，中间漏没漏人在数据上
            # 分不出来。但偶发一次不代表出事——列表到底时最后一屏本来就会整屏
            # 换掉，而这一滚照样采到了新人。所以先记一笔、把步长压小继续走，
            # 只有连续 JUMP_TOLERANCE 步都对不上才停（那时才是惯性真的失控）。
            if verdict == "jump":
                self._jumps += 1
                log("auto", f"两屏无重合行（第 {self._jumps} 次）· 这一步跨过了至少一整屏，"
                            f"{note}", "WARN")
                # **先别急着宣判。** 列表偶尔会自己跳位（实测落点每次都一样，
                # 而且中间只漏了 2 个人），这时往回慢慢走几步就能把漏掉的
                # 那几个人重新读进来——代价是几秒钟，收益是这一轮不用重跑。
                # 每轮最多补采 JUMP_RECOVER_TIMES 次，防止一直来回打转。
                if self._recover_walks < JUMP_RECOVER_TIMES and ordered:
                    self._recover_target = list(self._prev_ordered)
                    self._recover_seen = 0
                    self._step_boost = 1
                    log("auto", "→ 往回走小步，把这一步跨过的人补回来", "WARN")
                    self.say("某一步跳过了整屏，正在往回补采…")
                    self.after(SCROLL_GAP, lambda: self._do_scroll(up=True))
                    return
                if self._jumps >= JUMP_TOLERANCE:
                    # 注意这里**不带 complete**：跨过一整屏意味着中间的人没采到，
                    # 这份名单一定是残缺的，不能当成"采完了"去判退会——
                    # 否则库里那十几个人会被当成自己退会，连着两轮就真被删掉了。
                    self._stop_auto(
                        f"⚠ 这一步跨过了至少一整屏（前后两屏没有一处重合），中间可能漏人。"
                        f"已采 {len(self.names)} 人，但这**不算完整采集**，"
                        f"昵称库这一轮不记帐。请把名单拉回顶部重跑一遍——"
                        f"步长已限制在 {self._plan_dist()}px 以内，正常不会再跨屏"
                    )
                    return
            else:
                self._jumps = 0

            if verdict == "ok" and self.scroll_mode == "drag":
                sample = self._learn_step(rows, found, self._last_dist,
                                          img=img, prev_img=prev_img)
                if sample is not None:
                    log("auto", f"滚了 {rows} 行 → 实测行距 {self._pitch:.0f}px · "
                                f"起手死区 {sample:.0f}px（均值 {self._slop:.0f}px）· "
                                f"下次拖 {self._plan_dist()}px"
                                f"（目标 {TARGET_ROWS} 行 + 死区）")

            # 这一步真的推动了列表 → 说明刚才的"走 0 行"只是被死区卡住，
            # 把重试状态清干净，下次卡住还能再救一次
            if rows:
                self._step_boost = 1
                self._stuck_retry = 0

            self._prev_names = cur
            self._prev_ordered = ordered

            self.say(f"自动采集中 · 共 {len(self.names)} 人（本屏 +{new}）")
            # 留个空档再滚下一格。OCR 一屏只要两三百毫秒，紧贴着滚会让
            # 游戏侧的悬停/缓动动画和下一次拖动叠在一起，截图容易截到夹层。
            self.after(SCROLL_GAP, self._do_scroll)
        else:
            self.say(f"本屏识别 {len(found)} 行，新增 {new} · 共 {len(self.names)} 人")



class App(tk.Tk, RosterMixin):
    def __init__(self):
        super().__init__()
        self.title("NIKKE 联盟名单采集")
        # 竖版：贴在游戏窗口侧边常驻时不抢横向空间（对齐阿卡客户端 616×839 的形制）。
        # 默认高度跟随正式客户端（07-客户端/导出设置.json 的 win_h，2026-11-07 用户要求）：
        # 两个窗口并排时高度一致；读不到（没装客户端/文件损坏）保持旧值 940。
        self.geometry(f"620x{_client_win_h()}")
        self.minsize(560, 700)
        self.configure(bg=BG)

        self.win: WinInfo | None = None   # 锁定的游戏窗口（有效画面快照）
        self.region_rel = None            # (l,t,r,b) 占有效画面的比例 0~1，存盘存它
        self.region = None                # 由 region_rel 换算出的屏幕坐标，每轮截图前刷新
        self._saved = {}                  # 盘里的上次记录：aspect / region_rel / title
        self.names = {}                   # 昵称 -> (置信度, 采集时间)
        self._hits = {}                   # 昵称 -> 在多少屏里被读到过（同一个人留哪个拼写靠它）
        self._seen = {}                   # 昵称 -> (最高置信度, 首次时间)，连被并掉的拼写也留档
        self._links = set()               # {frozenset({拼写A, 拼写B})} 位置证据说这两个是同一行
        self._roster = load_roster()      # 昵称库：历次采集确认过的成员，用来兜读歪的字
        self._roster_prev = None          # 本轮开始时的库快照，收工拿它算"新增/未见"
        self._fix_cnt = {}                # (读歪的写法, 归到的写法) -> 本屏出现次数，只首见打日志
        self._gate = {}                   # 本趟"看着像库里某人"的孤行：写法 -> (候选, 相似度)
        self._twin = {}                   # 同屏双疑似：写法 -> (相似的另一个写法, 相似度)，只点名不动名单
        self._session_t0 = None           # 本轮开始时间，判断谁是"上次在、这次没见到"
        self._busy = False
        self._continuous = False
        self._focused = False             # 本次滚采是否已把游戏顶到前台
        self._last_preview = None
        self._empty_rounds = 0
        self._prev_names = set()          # 上一屏识别到的名字，用于检测跳行
        self._roll_count = 0              # 本次滚采已滚动次数，用于首滚强判
        self._last_diff = None            # 最近一次滚动前后画面差
        self._roll_before = None          # 滚动前那一帧，滚完拿来比对
        self._roll_clicks = 1
        self._settle_t0 = 0.0             # 本次"等停稳"的计时起点
        self._judge = SettleJudge()       # 停稳判据状态机（见 SettleJudge）
        self._cursor_home = None          # 开始采集前的光标位置，停的时候还回去
        self._perm_note = ""               # 权限对照结论，显示在信息条上
        self._preset_used = False           # 当前区域是不是套用固化预设来的
        self.scroll_mode = None            # 实测能滚动列表的方式（drag/wheel/key），存盘
        self._fling = FLING_INIT           # 实测惯性倍率（只用于日志，不参与步长）
        self._slop = float(DRAG_SLOP)      # 实测起手死区：拖动距离里被游戏吃掉的那截
        self._pitch = None                 # 实测行距（像素）
        self._step_boost = 1               # 临时放大步长的倍数（"走 0 行"重试用）
        self._stuck_retry = 0              # 本次"走 0 行"已经重试过几次
        self._last_dist = None             # 上一次实际拖了多少像素
        self._jumps = 0                    # 本次采集中"两屏没有重合行"的连续次数
        self._recover_target = []          # 补采目标：跳步前那一屏的名字
        self._recover_seen = 0             # 本次补采已经往回走了几步
        self._recover_walks = 0            # 本次采集已经补采过几次
        self._mode_ok = set()              # 本次采集里确实推动过列表的滚动方式
        self._persisted_mode = None        # 已经存盘的方式，避免每轮重复写文件
        self._mode_tried = set()           # 本次滚采已经试过并失败的方式
        self._last_img = None             # 最近一屏原图，导出诊断包时带上
        self._calib_pitch = None           # 标定出的行距（像素）
        self._calib_rows = None            # 标定出的每屏行数
        self._q = queue.Queue()           # 工作线程 -> 主线程 的结果投递队列
        self.info_var = tk.StringVar(value="正在等待游戏窗口…")
        self._infobar = None               # 信息条容器，兜底按钮要往它里面放
        self._btn_manual = None            # 「手动选择窗口」兜底按钮，平时不存在
        self._lock_miss = 0                # 自动接窗连续失败次数，到阈值才放兜底
        self._nav_running = False          # 导航线程在跑
        self._headless = False              # 客户端 headless 桥：跳过所有 messagebox
        self._nav_top_ok = None            # 列表是否确认回到第一行：None 未知 / True 是 / False 否
        self._still_t0 = 0.0               # 开跑前等静止的起点
        self._still_probe = None           # 上一帧，用来判"动完了没有"
        self._nav_cancel = False           # 导航的取消旗标（线程里轮询它）
        self._nav_where = None             # 上一次导航判定出的页面，只用于显示

        self._paused = False               # 用户点【暂停】后的等待态（不打断当前这屏）
        self._total_hint = None            # 页面上显示的总人数（联盟成员 n/m），用来核漏采
        self._run_dir = None               # 本次运行的缓存目录（对齐阿卡客户端的 per-run 目录）
        self._sync = None                  # 同步客户端，第一次用到才建（默认不联网）

        self._build()
        self._fit_size()
        # PyInstaller 单文件（console=False）的主窗口会继承启动器的显示状态，
        # 从无窗口的父进程拉起时会一上来就是最小化——双击后只在任务栏看到图标。
        # 源码直跑不受影响，所以这一行只在 exe 上体现价值。
        try:
            self.state("normal")
            self.deiconify()
            self.lift()
        except Exception:
            pass
        self._load_cfg()
        self._info()
        self._warmup()
        self.after(60, self._pump)
        self.after(400, self.auto_lock)


    # ---------------- RosterMixin 的宿主钩子（Tk 版实现）
    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except Exception:
            return False

    def _status_text(self, msg):
        try:
            self.info_var.set(msg)
        except Exception:
            pass

    def _refresh_info(self):
        self._info()

    def _raise_self(self):
        try:
            self.deiconify()
            self.attributes("-topmost", True)
            self.lift()
            self.focus_force()
            self.after(400, lambda: self.attributes("-topmost", False))
        except Exception:
            pass

    def _refresh_rows(self):
        self._refresh()

    def _warn(self, title, msg):
        messagebox.showwarning(title, msg)

    def _ok(self, title, msg):
        return bool(messagebox.askyesno(title, msg))

    def _roll_gap(self):
        try:
            return int(self.spin_roll.get())
        except Exception:
            return 1

    def _nav_ui(self, running):
        try:
            if running:
                self.btn_nav.config(text="■ 取消", bg="#c0392b")
            else:
                self.btn_nav.config(text="进成员页", bg=PANEL)
        except Exception:
            pass

    def _fit_size(self):
        """按内容实际需要放大窗口，只增不减。

        620×940 是按 100% 缩放定的竖版尺寸。Windows 缩放 150%/200% 时字体变高变宽：
        高度不够会把底部两排按钮压扁重叠（实测 200% 下"格每次"那行只剩 15px），
        宽度不够会把第二排右端（手动框选/清空）直接裁掉。用户自己拉过的尺寸不受影响。
        宽度不取窗口总需求——日志区要按字符数算宽，会把窗口撑得过宽，这里只看其余各行。
        """
        try:
            self.update_idletasks()
            need_w = max([w.winfo_reqwidth() for w in self.winfo_children()
                          if w is not self.logpane] or [0]) + 16
            need_h = self.winfo_reqheight()
            w = max(self.winfo_width(), min(need_w, self.winfo_screenwidth() - 40))
            h = max(self.winfo_height(), min(need_h, self.winfo_screenheight() - 60))
            if (w, h) != (self.winfo_width(), self.winfo_height()):
                self.geometry(f"{w}x{h}")
        except Exception:
            pass

    # ---------------- 线程安全的结果回传

    def _post(self, fn, *args):
        """工作线程只往队列塞任务，绝不直接碰 Tk。

        直接从子线程调 self.after() 会踩「main thread is not in main loop」，
        且关窗时会在后台线程里炸出一串异常。
        """
        self._q.put((fn, args))

    def _pump(self):
        while True:
            try:
                fn, args = self._q.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:
                log_exc("ui-callback")
        try:
            self.after(60, self._pump)
        except Exception:
            pass

    def _load_cfg(self):
        """读上次的记录。

        版本不兼容就作废区域、只留滚动方式：
          v1 存屏幕绝对坐标，反推不出比例；
          v2 存「客户区」百分比，而 v3 起基准换成「有效画面」，两者在有黑边时
             不是同一个坐标系，留着只会静默偏几像素。
        与其猜，不如让人重新标定一次——新版点一下就能出结果，成本很低。
        """
        import json

        try:
            raw = json.loads(CFG.read_text(encoding="utf-8"))
            log("config", f"读到区域配置 v{raw.get('version')} · {(raw or {}).get('window')}")
        except Exception as exc:
            log("config", f"区域配置读不出来（按首次使用处理）：{type(exc).__name__} {exc}", "WARN")
            return
        if not isinstance(raw, dict):
            return

        mode = raw.get("scroll_mode")
        if mode in SCROLL_MODES:
            self.scroll_mode = mode
            self._persisted_mode = mode
            log("config", f"滚动方式 {mode}")

        ver = int(raw.get("version", 1) or 1)
        if ver < 3:
            bak = OUT_DIR / f"区域_旧版v{ver}备份.json"
            try:
                CFG.replace(bak)
            except Exception:
                pass
            log("config", f"v{ver} 区域配置作废（基准已改为有效画面）", "WARN")
            self.say("旧版区域已作废（坐标系换成了有效画面）· 点【标定昵称列】重新标定一次")
            return

        rel = raw.get("region_rel")
        win = raw.get("window") or {}
        if isinstance(rel, list) and len(rel) == 4:
            try:
                self._saved = {
                    "aspect": float(win.get("aspect") or 0.0),
                    "title": str(win.get("title") or ""),
                    "region_rel": tuple(float(v) for v in rel),
                    "pitch": float(raw.get("pitch") or 0),
                    "rows": int(raw.get("rows_per_screen") or 0),
                }
            except Exception:
                self._saved = {}


    # ---------------- 锁定窗口与坐标换算

    def lock_window(self):
        dlg = WindowPicker(self)
        self.wait_window(dlg)
        if dlg.result is not None:
            self._adopt(dlg.result)

    def _show_manual(self):
        """兜底入口：自动接不上时才出现，平时界面上没有这个按钮。"""
        if getattr(self, "_btn_manual", None) is not None or self._infobar is None:
            return
        self._btn_manual = tk.Button(
            self._infobar, text="手动选择窗口", command=self.lock_window,
            font=FONT_SM, bg=PANEL, fg="#e0a030", activebackground="#3a3d42",
            activeforeground="white", relief="flat", bd=0, padx=8, pady=2,
            cursor="hand2")
        self._btn_manual.pack(side="right")
        self.say("自动接不上游戏窗口 · 已放出「手动选择窗口」兜底")

    def _info(self):
        """信息条：连上的是哪个窗口、比例对不对得上、区域占多大。

        工具只服务一个客户端，所以这里不写"请点锁定"这类操作指引——连不上就是
        连不上，等它自己重连即可。比例这一栏分两段说：窗口外框比例和有效画面
        比例，两者不同说明游戏在用黑边居中（超宽屏常见），那是正常的。
        """
        if self.win is None:
            self.info_var.set("正在等待游戏窗口…（打开客户端后会自己接上）")
            return
        ok_aspect = abs(self.win.aspect - TARGET_ASPECT) / TARGET_ASPECT <= ASPECT_TOL
        gap = ""
        fw, fh = frame_size(self.win.hwnd)
        if fw and fh and self.win.h:
            wa = fw / fh
            # 外框比例和画面比例一样时不必提，那是纯噪声。外框多半带着标题栏和
            # 边框，比例很少正好落在已知档位上——认不出档位就只报尺寸，不硬凑小数。
            if abs(wa - self.win.aspect) / max(self.win.aspect, 1e-6) > ASPECT_TOL:
                lab = aspect_label(wa)
                gap = f"窗口 {fw}×{fh}" + (f"（{lab}）" if lab[0].isdigit() and
                                           ":" in lab else "") + " · "
        parts = [f"已连接：{self.win.title[:24]}",
                 f"{gap}画面 {self.win.w}×{self.win.h}"
                 f"（{aspect_label(self.win.aspect)}{' ✓' if ok_aspect else ' ⚠'}）"]
        if self._perm_note:
            parts.append(self._perm_note)
        if self.region_rel:
            l, t, r, b = self.region_rel
            parts.append(f"区域：占画面 {(r - l):.1%} × {(b - t):.1%}")
            if self._calib_pitch:
                parts.append(f"每屏 {self._calib_rows} 行 · 行距 {self._calib_pitch:.0f}px")
            if self._preset_used:
                parts.append("来源：内置预设")
        else:
            parts.append("区域：未标定")
        self.info_var.set("   ｜   ".join(parts))

    # ---------------- UI

    def _build(self):
        """竖版单列布局。

        从上到下就是一次采集的真实顺序：接窗口 → 进成员页 → 校人数 → 滚采 → 入库。
        竖版不是审美偏好：工具要贴在游戏窗口一侧常驻，横版会横向抢位置；而且
        竖着只有一条视线，出问题时"卡在第几步"一眼就能看出来。
        """
        head = tk.Frame(self, bg=BG, padx=14, pady=4)
        head.pack(fill="x", pady=(8, 0))
        tk.Label(head, text="NIKKE 联盟名单采集", bg=BG, fg=FG,
                 font=ui.FONT_TITLE, anchor="w").pack(side="left")
        tk.Label(head, text=f"V{APP_VER}", bg=BG, fg="#6b7075", font=ui.FONT_XS,
                 anchor="e").pack(side="right", pady=(6, 0))

        # 目标窗口条：接上谁、画面多大、区域标没标，全在这一行
        self._infobar = tk.Frame(self, bg=PANEL, padx=10, pady=5)
        self._infobar.pack(fill="x", padx=14, pady=(4, 6))
        tk.Label(self._infobar, textvariable=self.info_var, bg=PANEL, fg=ui.DIM,
                 font=FONT_XS, anchor="w", justify="left",
                 wraplength=430).pack(side="left")

        # 三步之外还多了"校人数"：采完要拿游戏里显示的 n/m 对一下，
        # 这是唯一能自证"没漏人"的硬证据（见 _refresh_ledger）。
        self.steps = ui.StepBar(self, FLOW_STEPS)
        self.steps.pack(fill="x", padx=10, pady=(0, 2))

        prev = ui.Card(self, "本屏预览 · 红框=候选行")
        prev.pack(fill="x", padx=14, pady=(2, 0))
        self.cv = tk.Canvas(prev.body, bg="#16171a", highlightthickness=0, height=132)
        self.cv.pack(fill="x")
        self.cv.create_text(10, 10, anchor="nw", fill="#4a4d52", font=FONT_XS,
                            text="等待第一屏…（点【采集本屏】看单屏，或【开始】直接滚采）")

        self.task = ui.TaskProgress(self)
        self.task.pack(fill="x", padx=14, pady=(8, 2))
        self.status = self.task.txt      # say() 与任务提示共用一行，免得两处文案打架

        lst = ui.Card(self, "采集到的成员 · 双击可删误识别")
        lst.pack(fill="both", expand=True, padx=14, pady=(4, 4))
        sty = ttk.Style(self)
        sty.configure("Treeview", background="#16171a", fieldbackground="#16171a",
                      foreground=FG, rowheight=22, borderwidth=0)
        sty.configure("Treeview.Heading", background=PANEL, foreground=ui.DIM,
                      relief="flat", borderwidth=0)
        sty.map("Treeview.Heading", background=[("active", PANEL)],
                foreground=[("active", FG)])
        sty.map("Treeview", background=[("selected", ui.ACCENT_DK)],
                foreground=[("selected", "white")])
        cols = ("idx", "name", "score")
        self.tree = ttk.Treeview(lst.body, columns=cols, show="headings", height=7)
        for c, t, w, a in (("idx", "#", 40, "center"),
                           ("name", "昵称", 330, "w"),
                           ("score", "置信度", 80, "center")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c == "name"))
        sb = ttk.Scrollbar(lst.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._del_selected)

        self.ledger = ui.Ledger(self, [("待采", ""), ("已识别", ""), ("已跳过", "")])
        self.ledger.pack(fill="x", padx=14, pady=(0, 4))

        # 日志常驻 5 行，够看现场；要全量就点【打开日志】
        self.logpane = ui.LogPane(self, height=LOG_ROWS_IDLE)
        self.logpane.pack(fill="x", padx=14, pady=(0, 4))
        global _UI_ECHO
        # 走 push：日志是从采集线程写的，直接调 Tk 会偶发丢行（见 ui_kit.LogPane）
        _UI_ECHO = self.logpane.push

        # 主操作：与阿卡客户端同款的四键语义（开始 / 暂停 / 继续 / 取消），
        # 比原来"一个键翻来覆去变身"更好认——按错一次就得多跑一整轮。
        bar = tk.Frame(self, bg=BG, padx=14, pady=2)
        bar.pack(fill="x", side="bottom")
        self.btn_start = ui.btn(bar, "开始", self.toggle_auto, primary=True)
        self.btn_pause = ui.btn(bar, "暂停", self.pause_auto)
        self.btn_resume = ui.btn(bar, "继续", self.resume_auto)
        self.btn_stop = ui.btn(bar, "取消", self.cancel_auto)
        for b in (self.btn_start, self.btn_pause, self.btn_resume, self.btn_stop):
            b.pack(side="left", expand=True, fill="x", padx=3)
        ui.disable(self.btn_pause)
        ui.disable(self.btn_resume)
        ui.disable(self.btn_stop)

        # 工具行两排：越往下越常用。注意 pack(side=bottom) 是"后放的在上"，
        # 所以创建顺序要和视觉顺序相反——先建数据行、再建流程行，否则会颠倒。
        r2 = tk.Frame(self, bg=BG, padx=14)
        r2.pack(fill="x", side="bottom", pady=(0, 3))
        for text, cmd in (("导出 CSV", self.export), ("昵称库", self.roster_dialog),
                          ("成员变动", self.churn_dialog),
                          ("伤害记录", self.raid_records),
                          ("打包日志", self.export_diag), ("同步", self.do_sync),
                          ("测试滚动", self.probe_scroll),
                          ("手动框选", self.pick_region), ("清空", self.clear)):
            ui.btn(r2, text, cmd, small=True).pack(side="left", padx=(0, 4))

        r1 = tk.Frame(self, bg=BG, padx=14)
        r1.pack(fill="x", side="bottom", pady=(0, 3))
        self.btn_nav = ui.btn(r1, "进成员页", self.nav_to_members, small=True)
        self.btn_nav.pack(side="left", padx=(0, 4))
        self.btn_cap = ui.btn(r1, "采集本屏", self.capture, small=True)
        self.btn_cap.pack(side="left", padx=(0, 4))
        ui.btn(r1, "标定昵称列", self.calibrate, small=True).pack(side="left", padx=(0, 8))
        tk.Label(r1, text="格/次", bg=BG, fg="#6b7075", font=FONT_XS).pack(side="left")
        self.spin_roll = tk.Spinbox(
            r1, from_=1, to=20, width=3, font=FONT_SM, justify="center",
            bg=PANEL, fg=FG, buttonbackground=PANEL, relief="flat",
            insertbackground=FG,
        )
        self.spin_roll.delete(0, "end")
        self.spin_roll.insert(0, "1")
        self.spin_roll.pack(side="left", padx=(3, 0))


    # ---------------- 流程可视化 / 运行控制

    def _flow(self, idx: int, state: str, pct: float | None = None):
        """推进步骤条。state ∈ idle / doing / done / fail，idx 见 FLOW_STEPS。"""
        try:
            self.steps.set(idx, state)
        except Exception:
            pass
        if pct is not None:
            self.task.set(pct)

    def _task(self, msg=None, pct=None, step=None, state=None):
        """任务提示：文案 + 进度 + 步骤条一起改。

        三者必须同步。只改进度不改步骤条，用户看到的还是"卡在进成员页"，
        这正是阿卡客户端把 `任务提示 running N%` 和场景判定写在同一行日志的原因。
        """
        if step is not None:
            self._flow(step, state or "doing", pct)
        self.task.set(pct, msg)

    def _refresh_ledger(self, total=None):
        """刷新台账：待采 / 已识别 / 已跳过。

        "已跳过"只统计**有证据**的跳过（跳步补偿 + 往回补采的次数），
        不含 OCR 读不出字的行——那种行本来就不进名单，算进来只会让人误以为漏人。
        """
        got = len(self.names)
        if total is not None:
            self._total_hint = total
        total = self._total_hint
        skip = int(self._jumps) + int(self._recover_walks)
        self.ledger.set(**{
            "已识别": f"{got} 人",
            "已跳过": f"{skip} 次" if skip else "0",
            "待采": (f"{max(0, total - got)} 人（共 {total}）" if total
                     else f"未知（已采 {got}，进成员页时自动读总数）"),
        })

    def pause_auto(self):
        """暂停。不打断正在飞的那一屏，只在下一个动作前停——半路撒手会留半屏截断。"""
        if not self._continuous or self._paused:
            return
        self._paused = True
        self._task(msg="⏸ 已暂停 · 点【继续】接着采")
        log("auto", "用户暂停采集")
        self._sync_run_buttons()

    def resume_auto(self):
        if not self._continuous or not self._paused:
            return
        self._paused = False
        self._task(msg="继续采集中…")
        log("auto", "用户继续采集")
        self._sync_run_buttons()

    def cancel_auto(self):
        if not self._continuous:
            return
        self._paused = False
        self._stop_auto("已手动取消 · 本轮不算完整采集，昵称库不记帐", complete=False)

    def _sync_run_buttons(self):
        """四键状态跟运行状态对齐。置灰不只是视觉——tkinter 的 disabled 才真拦点击。"""
        run = self._continuous
        try:
            ui.disable(self.btn_start, run)
            ui.disable(self.btn_pause, not run or self._paused)
            ui.disable(self.btn_resume, not run or not self._paused)
            ui.disable(self.btn_stop, not run)
        except Exception:
            pass

    def _log_mode(self, running: bool):
        """采集时把日志区撑开、并确保它是展开的；收工再让回给名单。

        一年跑下来最常听到的一句是"采集中我想看一眼日志"——5 行高、每 1.6 秒
        换一遍，等于没法看。所以开跑先给 9 行；用户自己拖过高度就不再插手，
        他量过的尺寸比这里的默认值可信。
        """
        try:
            pane = self.logpane
            if running and not pane.is_open:
                pane.toggle()          # 折叠着等于没有日志
            if not pane.user_sized:
                pane.set_rows(LOG_ROWS_RUN if running else LOG_ROWS_IDLE)
            self._fit_size()          # 撑开日志会让内容变高，窗口要跟着长
        except Exception:
            pass

    def say(self, msg):
        self.status.config(text=msg)
        logline(msg)

    # ---------------- 模型预热

    def _ready_msg(self) -> str:
        """预热完成时该说什么，取决于当前进度。

        不能一律说"打开客户端"——接窗口可能已经成功了，那句提示会把更重要的
        话覆盖掉，用户会以为没接上。
        """
        if self.win is None:
            return "模型就绪 · 正在等待游戏窗口（打开客户端会自动接上）"
        if not self.region_rel:
            return f"模型就绪 · 已接上「{self.win.title[:18]}」，点【标定昵称列】量一次"
        if self._preset_used:
            return (f"模型就绪 · 已套用内置预设（每屏 {self._calib_rows or '?'} 行）"
                    f"· 直接点【自动滚采】即可")
        return "模型就绪 · 已带出上次标定，直接点【自动滚采】即可"

    def _warmup(self):
        def work():
            try:
                get_engine()
                dummy = Image.new("RGB", (240, 40), (247, 247, 245))
                read_line(dummy, (0, 0, 240, 40), upscale=1)
                # 文案依赖 UI 状态，必须回主线程再算，不能在预热线程里读窗口信息
                self._post(lambda: self.say(self._ready_msg()))
            except Exception as exc:
                log_exc("warmup", exc)
                self._post(self.say, f"模型加载失败：{exc}")
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 动作

    # ---------------- 导航

    def _sync_client(self):
        """懒建同步客户端。配置默认关闭，所以平常它只往 outbox 里留一份档。"""
        if self._sync is None:
            cfg = sync_client.load_config(SYNC_CFG)
            box = sync_client.Outbox(self._run_temp() / "sync")
            self._sync = sync_client.SyncClient(
                cfg, box, logger=lambda t, m, lv="INFO": log(t, m, lv))
            log("sync", f"同步配置：enabled={cfg.enabled} "
                        f"base_url={cfg.base_url or '未设置'} "
                        f"（改 {SYNC_CFG} 生效，不用改代码）")
        return self._sync

    def sync_payload(self, complete=None):
        """把当前名单整理成待同步的结构。

        字段先按"后端最可能要什么"来定：人数、总数、每人名字和置信度、采集时间、
        这一轮是否完整。字段名以后端为准，改这里一处就行。
        """
        return {
            "count": len(self.names),
            "total": self._total_hint,
            "complete": (self._jumps == 0 and self._nav_top_ok is not False
                         if complete is None else bool(complete)),
            "captured_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "window": self.win.describe() if self.win else None,
            "members": [{"name": n, "score": round(float(s), 3)}
                        for n, (s, _t) in self.names.items()],
        }

    def do_sync(self, complete=None, quiet=False):
        """排一条待同步并尝试重投。默认配置下只留档，不产生任何网络请求。"""
        if not self.names:
            if not quiet:
                self.say("名单还是空的，没有可同步的内容")
            return
        try:
            cli = self._sync_client()
            cli.queue("members", "联盟名单", self.sync_payload(complete))
            r = cli.flush()
            if r.get("skipped"):
                if not quiet:
                    self.say("已留档到 outbox（同步未启用：改 预设/同步.json 打开）")
                return
            if not quiet:
                self.say(f"同步完成 · 成功 {r['sent']} 条 · 失败 {r['failed']} 条")
            if not quiet:
                log("sync", f"本次同步结果 {r}")
        except Exception as exc:
            log_exc("sync", exc)
            if not quiet:
                self.say(f"同步出错：{exc}")

    def calibrate(self):
        """标定昵称列：先自动认，认不出再引导点/拖。"""
        if self.win is None:
            messagebox.showinfo("提示", "还没接上游戏窗口——请先打开客户端，工具会自己接上")
            return
        # 先把主窗口藏起来，等它真正从屏幕消失再截图，否则截图会拍到自己
        self.withdraw()
        self.after(260, self._calibrate)

    def _calibrate(self):
        try:
            content = content_rect(self.win.hwnd)
            if content is None:
                self.say("游戏窗口不见了，标定取消")
                return
            if abs(content.aspect - TARGET_ASPECT) / TARGET_ASPECT > ASPECT_TOL:
                log("calib", f"拒绝标定：有效画面 {content.describe()}", "WARN")
                self.say(f"⚠ 有效画面 {content.w}×{content.h}（{content.aspect:.3f}）"
                         f"不是 16:9 —— 把窗口调成 16:9（如 1600×900）后再点【标定昵称列】")
                return

            # 自动认不出时用它垫底：上次标定过就不用再框一遍
            prel = self.region_rel
            if not prel:
                _p = load_preset()
                prel = (_p or {}).get("region_rel")
            wiz = CalibWizard(self, content, preset_rel=prel)
            self.wait_window(wiz)
            if not wiz.result:
                self.say("已取消标定")
                return

            # 提交就存盘。不再走第二次确认窗：浮层上已经画了框和每一行，
            # 再弹一个"确认吗"只是把手上的动作变成两次点击。
            res = wiz.result
            x0, y0, x1, y1 = res["rect"]
            rel = (x0 / content.w, y0 / content.h, x1 / content.w, y1 / content.h)
            ok = self._commit_rel(rel, res.get("pitch"), res.get("rows"),
                                  frame=content)
            log("calib", f"标定完成 mode={res.get('mode')} rect={res['rect']} "
                         f"pitch={res.get('pitch')} rows={res.get('rows')} "
                         f"样本={res.get('samples', 0)} 画面={content.describe()}")
            if ok:
                self._info()
        except Exception as exc:
            log_exc("calib", exc)
            self.say(f"标定失败：{exc}")
        finally:
            self.deiconify()
            self.lift()

    def _commit_rel(self, rel, pitch=None, rows=None, frame=None):
        """把百分比例落盘并立刻校验一遍。标定和手动框选共用。

        frame 给了说明这次是真人确认过的结果 —— 顺手固化成预设，
        以后换分辨率、删了 输出/ 目录都不用再标定。
        """
        over = max(0.0, -rel[0], -rel[1], rel[2] - 1.0, rel[3] - 1.0)
        if over > 0.02:
            # 这里刻意不弹窗：错的是坐标，不是用户的判断，拦下 + 状态栏说清就够
            log("calib", f"区域越界 {over:.2%} rel={rel}", "WARN")
            self.say(f"⚠ 区域超出有效画面 {over:.1%}，未采用 —— 请重新标定")
            return False

        self.region_rel = (max(0.0, rel[0]), max(0.0, rel[1]),
                           min(1.0, rel[2]), min(1.0, rel[3]))
        self._calib_pitch = pitch
        self._calib_rows = rows if pitch else None
        self._save_cfg()
        self.region = None

        ok, region, err = self.resolve_region(check_obscure=False)
        if ok:
            self.region = region
            w, h = region[2] - region[0], region[3] - region[1]
            msg = f"已标定区域 {w}×{h}"
            if rows and pitch:
                msg += f" · 每屏 {rows} 行 · 行距 {pitch:.0f}px"
            if frame is not None and save_preset(self.region_rel, pitch, rows, frame):
                msg += " · 已固化为预设，以后不用再标定"
            # 存完立刻试拍一次，把"这个框实际能认出几个人"当场说清。
            # 认不出也不撤销——框是用户给的，工具不该因为自己认不出就否定它。
            got, notes = check_region(region)
            log("calib", f"标定体检 · 认出 {got} 行 · " + ("；".join(notes) or "无异常"))
            if got >= 2:
                msg += f" · 试拍认出 {got} 行"
            if notes:
                msg += " · 提示：" + "；".join(notes)
            self.say(msg + " · 点【采集本屏】")
            self._info()
            return True
        # 区域换算不出来（窗口被缩、被挡）时仍然存盘：配置是对的，
        # 只是现在用不了，下次窗口正常了就能直接用。
        self.say(err)
        self._info()
        return False

    # ---------------- 手动框选（备用）

    def pick_region(self):
        """手画矩形。点选标定失效时的退路，坐标基准一样是有效画面。"""
        if self.win is None:
            messagebox.showinfo("提示", "还没接上游戏窗口——请先打开客户端，工具会自己接上")
            return
        if client_rect(self.win.hwnd) is None:
            messagebox.showwarning(
                "提示", "游戏窗口找不到了（可能已关闭或最小化）。\n还原游戏窗口后，工具会自动重新接上。")
            return
        self.withdraw()
        self.after(260, self._pick)

    def _pick(self):
        try:
            info = content_rect(self.win.hwnd)
            if info is None:
                self.say("游戏窗口不见了，框选取消")
                return
            p = RegionPicker(self)
            self.wait_window(p)
            if not p.result:
                self.say("已取消框选")
                return

            x0, y0, x1, y1 = p.result
            rel = (
                (x0 - info.x) / info.w,
                (y0 - info.y) / info.h,
                (x1 - info.x) / info.w,
                (y1 - info.y) / info.h,
            )
            log("region", f"手动框选 {p.result} → 相对 "
                          f"[{rel[0]:.4f},{rel[1]:.4f},{rel[2]:.4f},{rel[3]:.4f}] "
                          f"有效画面 {info.w}x{info.h}@{info.x},{info.y}")
            pitch, rows = _measure_pitch(grab((x0, y0, x1, y1)))
            self._commit_rel(rel, pitch, rows, frame=info)
        except Exception as exc:
            log_exc("pick", exc)
            self.say(f"框选失败：{exc}")
        finally:
            self.deiconify()
            self.lift()

    def toggle_auto(self):
        if self._continuous:
            self._stop_auto("已手动停止")
            return
        info = None
        if self.win is not None:
            info = content_rect(self.win.hwnd)
        if info is None:
            self.say("⚠ 游戏窗口不见了 —— 正在后台自动重连…")
            self.win = None
            self.after(400, self.auto_lock)
            return
        if not self.region_rel:
            self.say("⚠ 还没有采集区域 —— 点【标定昵称列】，一般会自动认出来")
            return
        tip = (
            "自动滚采：从当前这一屏开始，向下逐屏采集，直到列表底部。\n\n"
            "开始前请确认：\n"
            "· 游戏已切到「联盟成员」列表，并滚到第一行\n"
            "· 开始后游戏窗口会自动切到前台，本窗口退到后面（正常现象，结束后会自动回来）\n"
            "· 采集期间不要移动鼠标、不要切换窗口\n"
            "· 滚轮格数保持 1 最稳（列表一页行数少时别调大，会跳行漏采）\n\n"
            f"当前已有 {len(self.names)} 人，可继续追加。是否开始？"
        )
        # 权限不对等只记录、不拦截：实测 mouse_event 注入不受 UIPI 限制，
        # 低权限进程照样能把滚轮送进去。真正的判据是首滚之后的画面差。
        adv = perm_advice(self.win.hwnd)
        if adv:
            logline("权限不对等（不拦截）：" + adv)

        if not messagebox.askyesno("自动滚采", tip):
            log("auto", "用户取消了自动滚采")
            return
        self._begin_auto()

    def _relaunch_as_admin(self):
        """用 runas 重新拉起自己（pythonw + 本文件），成功后关掉当前实例。"""
        try:
            py = Path(sys.executable)
            pyw = py.with_name("pythonw.exe")
            if not pyw.exists():
                pyw = py
            r = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(pyw), f'"{Path(__file__).resolve()}"',
                str(APP_DIR), 1,
            )
            # >32 成功；5 = 用户在 UAC 弹窗上点了取消
            if r > 32:
                logline("已请求提权重启")
                self.destroy()
            else:
                self.say(f"提权未完成（代码 {r}，1223=已取消）")
        except Exception as exc:
            self.say(f"提权失败：{exc}")

    def probe_scroll(self):
        """试一次滚动，把能滚的方式试出来，并量出一次走几行。

        联盟成员列表实测不吃滚轮（1 格和 5 格画面差都是 1.3x、名字一个没变），
        所以不能写死用滚轮。这里按 拖动 → 滚轮 → 翻页键 依次试，
        哪个真的让名字变了就用哪个，并记进配置。
        """
        if self.win is None:
            self.say("先打开客户端，工具会自动接上窗口")
            return
        ok, region, err = self.resolve_region()
        if not ok:
            self.say(err)
            messagebox.showwarning("试滚", err)
            return
        try:
            clicks = max(1, min(20, int(self.spin_roll.get())))
        except Exception:
            clicks = 1
        if not focus_window(self.win.hwnd):
            msg = ("切不到游戏前台，输入会发给别的窗口。\n"
                   "请手动点一下游戏窗口，让它真的在最前面，再点【试滚】。")
            self.say(msg)
            messagebox.showwarning("试滚", msg)
            return
        self.say("测试滚动中…（依次试 拖动/滚轮/翻页，期间别动鼠标）")
        log("probe", f"开始试滚 · {clicks} 格/次 · region={region}")
        frame = content_rect(self.win.hwnd)

        def work():
            try:
                results = []
                for mode in SCROLL_MODES:
                    park_cursor(region, frame)      # 光标压在名单上会遮字 + 触发悬停
                    before = grab(region)
                    names_b = [n for _y, n, _s in scan(before)[0]]
                    scroll_once(mode, region, clicks, PROBE_DRAG_PX)
                    park_cursor(region, frame)
                    # 等画面真停稳再截，否则量到的是惯性中途的位置，行数会偏
                    after, used, ok_settle = wait_settled(region)
                    names_a = [n for _y, n, _s in scan(after)[0]]
                    rows, overlap = match_rows(names_b, names_a)
                    diff = frame_diff(before, after)
                    results.append((mode, rows, overlap, len(names_b), len(names_a), diff))
                    log("probe", f"{SCROLL_MODE_CN[mode]}: 上屏 {len(names_b)} 行 → "
                                 f"本屏 {len(names_a)} 行 · 滚了 {rows} 行 · "
                                 f"重合 {overlap} · 画面差 {diff:.2f} · "
                                 f"等稳定 {int(used * 1000)}ms"
                                 f"{'' if ok_settle else '（超时，画面仍在动）'}")
                    # 判定有效：名字真的换了。重合 0 行也算有效——拖动带惯性，
                    # 1 格就可能滑满一屏，此时位移量量不出精确行数，但"能滚"是确定的。
                    if rows and rows > 0:
                        break
                    # 没滚动的方案可能弹出了别的界面，按 ESC 收掉再试下一个
                    ctypes.windll.user32.keybd_event(0x1B, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(0x1B, 0, KEYEVENTF_KEYUP, 0)
                    time.sleep(0.25)
                self._post(self._probe_done, clicks, results)
            except Exception as exc:
                log_exc("probe", exc)
                self._post(self.say, f"试滚失败：{exc}")

        threading.Thread(target=work, daemon=True).start()

    def _probe_done(self, clicks, results):
        good = [r for r in results if r[1] and r[1] > 0]
        if good:
            mode, rows, overlap, nb, na, diff = good[0]
            self.scroll_mode = mode
            self._persisted_mode = mode
            self._fling = FLING_INIT
            self._save_cfg()
            if overlap > 0:
                self.say(f"这个列表要用【{SCROLL_MODE_CN[mode]}】才能滚："
                         f"一次 {clicks} 格 ≈ {rows} 行（本屏 {nb} 行，重合 {overlap}）")
            else:
                self.say(f"这个列表要用【{SCROLL_MODE_CN[mode]}】才能滚，"
                         f"但 {clicks} 格就跨满了一整屏（量不出精确行数）——"
                         f"自动滚采会自己把步长调小，直接点它就行")
            log("probe", f"选定滚动方式 {mode} · {rows} 行/次", "WARN")
            return
        detail = " / ".join(
            f"{SCROLL_MODE_CN[m]}滚{r[1]}行" if r[1] is not None else SCROLL_MODE_CN[m]
            for r in results
        )
        self.say(f"⚠ 三种方式都没能滚动列表（{detail}）——把 输出/操作日志.txt 发我")
        log("probe", f"三种方式均无效 · {detail}", "ERR")

    def _persist_mode(self):
        """把"实测能滚动的方式"写进配置，只写一次。"""
        if self.scroll_mode and self._persisted_mode != self.scroll_mode:
            self._persisted_mode = self.scroll_mode
            self._save_cfg()

    def _switch_mode(self) -> bool:
        """换一种还没试过的滚动方式。全都试过就返回 False。"""
        if self.scroll_mode in SCROLL_MODES:
            self._mode_tried.add(self.scroll_mode)
        for m in SCROLL_MODES:
            if m not in self._mode_tried:
                self.scroll_mode = m
                log("auto", f"列表没被滚动 → 改用【{SCROLL_MODE_CN[m]}】再试", "WARN")
                return True
        return False

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, (name, (score, _t)) in enumerate(self.names.items(), 1):
            self.tree.insert("", "end", values=(i, name, f"{score:.3f}"))

    def _draw_preview(self, img, bands):
        self.cv.delete("all")
        cw = max(self.cv.winfo_width(), 400)
        ch = max(self.cv.winfo_height(), 300)
        k = min(cw / img.width, ch / img.height, 1.0)
        disp = img.resize((int(img.width * k), int(img.height * k)), Image.Resampling.LANCZOS)
        self._last_preview = ImageTk.PhotoImage(disp)
        ox, oy = (cw - disp.width) // 2, (ch - disp.height) // 2
        self.cv.create_image(ox, oy, image=self._last_preview, anchor="nw")
        for (y0, y1) in bands:
            self.cv.create_rectangle(
                ox, oy + int(y0 * k), ox + disp.width, oy + int(y1 * k),
                outline="#ff5252", width=1,
            )

    def _del_selected(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        name = self.tree.item(sel[0], "values")[1]
        self.names.pop(name, None)
        self._hits.pop(name, None)
        self._seen.pop(name, None)
        # 用户亲手删掉的，就别让收工复核再把它捡回来
        self._links = {p for p in self._links if name not in p}
        # 也别让它在收工时被算成"上次有这次没见到"——那是用户判断，不是漏采
        if self._roster_prev:
            self._roster_prev.pop(name, None)
        self._refresh()
        self.say(f"已删除「{name}」· 共 {len(self.names)} 人")

    def clear(self):
        if not self.names or messagebox.askyesno("确认", "清空当前名单？"):
            self.names.clear()
            self._hits.clear()
            self._seen.clear()
            self._links.clear()
            self._fix_cnt.clear()
            # 名单清零 = 这一轮重新开始，昵称库的对比基准也要重拍
            self._roster_prev = json.loads(json.dumps(self._roster.get("names") or {}))
            self._session_t0 = None
            self._refresh()
            self.cv.delete("all")
            self.say("已清空（昵称库仍然保留，" + f"{len(self._roster.get('names') or {})} 人）")

    def export(self):
        if not self.names:
            messagebox.showinfo("提示", "名单为空")
            return
        self._reconcile()                 # 手动逐屏采的也在这里裁定一次
        self._roster_apply()              # 位置证据定完，库再对一次账
        default = OUT_DIR / f"联盟名单_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(
            title="导出名单", initialdir=str(OUT_DIR),
            initialfile=default.name, defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["序号", "昵称", "置信度"])
            for i, (name, (score, _t)) in enumerate(self.names.items(), 1):
                w.writerow([i, name, f"{score:.3f}"])
        self.say(f"已导出 {len(self.names)} 人到 {path}")
        log("export", f"CSV {path} · {len(self.names)} 人")
        if messagebox.askyesno("完成", f"已导出 {len(self.names)} 人。\n打开所在文件夹？"):
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(Path(path))])

    # ---------------- 诊断

    def open_log(self):
        """打开输出目录并选中操作日志，用户直接把这份发过来就行。"""
        try:
            LOG_PATH.touch(exist_ok=True)
            import subprocess
            subprocess.Popen(["explorer", "/select,", str(LOG_PATH)])
        except Exception as exc:
            log_exc("open_log", exc)
            messagebox.showwarning("打不开", f"手动去这里找：\n{LOG_PATH}")

    def roster_dialog(self):
        """看一眼昵称库里现在有谁、记下了哪些误读写法。"""
        names = self._roster.get("names") or {}
        win = tk.Toplevel(self)
        win.title(f"昵称库 · {len(names)} 人")
        win.configure(bg=BG)
        win.geometry("560x580")
        win.transient(self)

        tk.Label(win, text=f"已记下 {len(names)} 位成员 · 累计采集 {self._roster.get('rounds', 0)} 轮",
                 bg=BG, fg=FG, font=FONT, anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(win,
                 text="每次采完都会自动更新。下轮读歪的字只要能对回这里唯一一条，就直接归位；\n"
                      "连续 2 轮完整采集都没见到的才会被移出（判断退会）。",
                 bg=BG, fg="#9aa0a6", font=FONT_SM, justify="left", anchor="w").pack(fill="x", padx=14)

        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(8, 6))
        tree = ttk.Treeview(wrap, columns=("n", "r", "s", "a"), show="headings", height=16)
        for col, txt, w, anchor in (("n", "昵称", 170, "w"), ("r", "采到轮数", 70, "center"),
                                    ("s", "置信度", 70, "center"), ("a", "记下的误读写法", 210, "w")):
            tree.heading(col, text=txt)
            tree.column(col, width=w, anchor=anchor)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for n, e in sorted(names.items(), key=lambda kv: -int(kv[1].get("rounds", 0))):
            tree.insert("", "end", values=(n, e.get("rounds", 0),
                                           f"{float(e.get('score') or 0):.3f}",
                                           "、".join(e.get("alias") or ()) or "—"))

        # 库同时也是"字库"：把攒下来的误读写法逐位对齐，就是这台机器这套字体
        # 的易混字表。它只用来在提示语里说"这个差异像不像老毛病"，不做判据。
        pairs = confuse_pairs(names)
        top = sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
        mix = ("这台机器常混的字：" + "、".join(
            f"{y}↔{x}({c})" for (x, y), c in top)) if top else \
            "还没攒下误读写法——多跑两轮、读歪几次就有了"
        tk.Label(win, text=mix, bg=BG, fg="#9aa0a6", font=FONT_SM, anchor="w",
                 justify="left", wraplength=520).pack(fill="x", padx=14, pady=(0, 6))

        def open_file():
            try:
                booklib.ROSTER_DIR.mkdir(parents=True, exist_ok=True)
                os.startfile(str(booklib.ROSTER_DIR))
            except Exception as exc:
                messagebox.showwarning("打不开", str(exc))

        def wipe():
            if not messagebox.askyesno("清空昵称库",
                                       f"库里的 {len(names)} 个人会被全部忘掉。\n"
                                       "之后的一轮要重新攒（首次建库那轮不会做拼写纠错）。\n\n确定清空？"):
                return
            self._roster = {"version": 1, "rounds": 0, "names": {}}
            save_roster(self._roster)
            self._roster_prev = {}
            win.destroy()
            self.say("昵称库已清空，下一轮重新建库")

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=14, pady=(0, 12))
        tk.Button(foot, text="打开所在文件夹", command=open_file, bg=PANEL, fg=FG,
                  relief="flat", font=FONT_SM, padx=10, pady=4).pack(side="left")
        tk.Button(foot, text="清空昵称库", command=wipe, bg=PANEL, fg="#e06c6c",
                  relief="flat", font=FONT_SM, padx=10, pady=4).pack(side="left", padx=8)
        tk.Button(foot, text="关闭", command=win.destroy, bg=PANEL, fg=FG,
                  relief="flat", font=FONT_SM, padx=10, pady=4).pack(side="right")

    def churn_dialog(self):
        """成员变动：这一轮谁走了、谁是新来的、哪几轮在动。

        为什么单独一个视图：昵称库那张表是"一个人一行"，回答不了"哪一轮变的"。
        公会人员进出本来就慢（一轮一两个人），靠 last/miss 反推只知道"现在少谁"，
        不知道"从第几轮开始少的"——而这恰恰是判断真退会还是漏采的关键。
        """
        head = churn_headline(self._roster)
        rows = head["rows"]
        win = tk.Toplevel(self)
        win.title(f"成员变动 · 库内 {head['n']} 人")
        win.configure(bg=BG)
        win.geometry("620x620")
        win.minsize(560, 520)
        win.transient(self)

        # 一句话结论：先给结论再给表。看表的人多半只想知道"最近这轮正不正常"
        last = head["last"]
        if last:
            tip = f"最近一轮 {last['t']} · 采到 {last['n']} 人"
            if last["total"]:
                same = "一致" if last["n"] == last["total"] else "不一致"
                tip += f"（页面 {last['total']}，{same}）"
            if not last["ok"]:
                tip += " · 这轮没采完，不计退会"
            tip += f" · 累计 {head['rounds']} 轮"
        else:
            tip = f"还没有轮次记录 · 库内 {head['n']} 人 · 累计 {head['rounds']} 轮"
        tk.Label(win, text=tip, bg=BG, fg=FG, font=FONT, anchor="w").pack(
            fill="x", padx=14, pady=(12, 2))

        stat = tk.Frame(win, bg=BG)
        stat.pack(fill="x", padx=14, pady=(0, 4))
        lk = head["last_ok"]
        for txt, num, col in ((f"新增 {len(lk['add']) if lk else 0}",
                               len(lk["add"]) if lk else 0, ui.OK),
                              (f"未见 {len(lk['gone']) if lk else 0}",
                               len(lk["gone"]) if lk else 0, ui.WARN),
                              (f"移出 {len(lk['drop']) if lk else 0}",
                               len(lk["drop"]) if lk else 0, ui.ERR)):
            # 0 不染色：一排彩色的 0 看着像在报警
            tk.Label(stat, text=txt, bg=BG, fg=(col if num else "#6b7075"),
                     font=FONT_SM, anchor="w").pack(side="left", padx=(0, 14))
        tk.Label(win, text="只有完整采到列表末尾的那一轮才算数；连续 2 轮完整采集都没见到的才会移出库。",
                 bg=BG, fg="#9aa0a6", font=FONT_SM, anchor="w").pack(fill="x", padx=14)

        # 轮次时间线（最新在上）
        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=(8, 4))
        cols = (("t", "时间", 120, "w"), ("n", "采到", 62, "center"),
                ("a", "新增", 62, "center"), ("g", "未见", 62, "center"),
                ("d", "移出", 56, "center"), ("s", "状态", 96, "w"))
        tree = ttk.Treeview(wrap, columns=[c[0] for c in cols], show="headings",
                            height=9, selectmode="browse")
        for col, txt, w, anchor in cols:
            tree.heading(col, text=txt)
            tree.column(col, width=w, anchor=anchor, stretch=(col == "t"))
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # 未采完的轮次整行压暗：它不参与退会判定，混在正常行里会被当成"这轮少人了"
        tree.tag_configure("bad", foreground="#7d838a")
        tree.tag_configure("drop", foreground="#e57b7b")

        for i, r in enumerate(rows):
            if not r["ok"]:
                state, tag = "未采完", ("bad",)
            elif r["drop"]:
                state, tag = f"移出 {len(r['drop'])} 人", ("drop",)
            elif r["add"] or r["gone"]:
                state, tag = "有变动", ()
            else:
                state, tag = "无变动", ()
            tree.insert("", "end", iid=str(i), tags=tag, values=(
                r["t"], r["n"], len(r["add"]) or "—", len(r["gone"]) or "—",
                len(r["drop"]) or "—", state))

        det = tk.Text(win, height=7, bg="#16171a", fg="#b9bdc4",
                      font=("Consolas", 9), relief="flat", bd=0, wrap="char",
                      insertbackground=FG, state="disabled", padx=8, pady=6)
        det.pack(fill="x", padx=14)
        for tag, col in (("a", ui.OK), ("g", ui.WARN), ("d", ui.ERR),
                         ("w", "#e0a03a"), ("k", "#9aa0a6")):
            det.tag_configure(tag, foreground=col)

        def say(parts):
            """parts = [(文字, tag)]。必须先在 normal 态写、写完再 disable——
            Tk 的 Text 在 disabled 下连程序化 insert/delete 都会被静默忽略。
            """
            det.configure(state="normal")
            det.delete("1.0", "end")
            for text, tag in parts:
                det.insert("end", text, tag)
            det.configure(state="disabled")

        def show(i):
            if not (0 <= i < len(rows)):
                return
            r = rows[i]
            out = [(f"{r['t']} · 采到 {r['n']} 人", ""),
                   (f"（页面 {r['total']}）" if r["total"] else "", ""),
                   # 没采完的那轮连"新增"都不可信：可能是漏采导致的重认，也可能是误读
                   ("" if r["ok"] else " · 没采完，人数和新增都不可信，不参与退会判定", "w"),
                   ("\n", "")]
            for label, key, tag in (("新来的", "add", "a"), ("没见到", "gone", "g"),
                                    ("已移出", "drop", "d")):
                out.append((f"{label}：", tag))
                out.append(("、".join(r[key]) if r[key] else "—", tag))
                out.append(("\n", ""))
            if r["alias"]:
                out.append((f"误读归位：记住 {r['alias']} 个读歪的写法\n", "k"))
            if len(r["add"]) == 1 and len(r["gone"]) == 1 and not r["drop"]:
                # 库按名字认人，改名在库里就是"旧的退了、新的来了"。只提示，不自动合并。
                out.append(("⚠ 这轮恰好 1 进 1 出，可能是同一个人改了名\n", "w"))
            say(out)

        if rows:
            tree.selection_set("0")
            show(0)
        else:
            say([("还没有轮次记录。\n", "k"),
                 ("下一轮采集完成就会在这里留下第一行；旧版本攒下的昵称库没有历史，"
                  "但名单本身不受影响。\n", "k")])

        def on_pick(_e=None):
            sel = tree.selection()
            if sel:
                show(int(sel[0]))

        tree.bind("<<TreeviewSelect>>", on_pick)

        # 当前"连续没见到"的人：不依赖选中的是哪一轮，永远看最新状态
        if head["watch"]:
            txt = "当前连续没见到：" + "、".join(
                f"{n}（{m} 轮）" for n, m, _t in head["watch"][:8])
            tk.Label(win, text=txt + ("…" if len(head["watch"]) > 8 else ""),
                     bg=BG, fg=ui.WARN, font=FONT_SM, anchor="w",
                     justify="left", wraplength=580).pack(fill="x", padx=14, pady=(4, 0))
        if head["rename_hint"]:
            tk.Label(win, text=f"⚠ 上一轮 1 进 1 出：{head['add'][0]} ↔ {head['gone'][0]}"
                               f"　可能是改名，不是换人",
                     bg=BG, fg=ui.WARN, font=FONT_SM, anchor="w",
                     wraplength=580, justify="left").pack(fill="x", padx=14, pady=(2, 0))

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=14, pady=(6, 12))
        ui.btn(foot, "昵称库", self.roster_dialog, small=True).pack(side="left")
        ui.btn(foot, "关闭", win.destroy, small=True).pack(side="right")

    def raid_records(self):
        """打开「联盟突袭 · 伤害记录」采集窗口。

        独立窗口，不碰本界面的布局：名单这条链路一行不改，坏也只坏新窗口。
        延迟 import 是为了避免和 raid_window 形成循环引用（它要 import 本模块取
        截图/滚动/窗口那几件工具）。
        """
        try:
            from raid_window import RaidWindow
        except Exception:
            log_exc("raid-open")
            messagebox.showerror("打不开伤害记录",
                                 "raid_window.py 缺失或导入失败，详见操作日志")
            return None
        win = getattr(self, "_raid_win", None)
        if win is not None and win.winfo_exists():
            win.lift()
            win.focus_force()
            return win
        self._raid_win = RaidWindow(self)
        return self._raid_win

    def export_diag(self):
        """把定位问题需要的东西打成 zip：日志 + 配置 + 当时那一屏截图 + 当前名单。

        只带日志不够——"截到了什么"往往是关键，光看统计数字分不清是
        OCR 认错还是框错了位置。
        """
        import zipfile
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out = DIAG_DIR / f"诊断_{ts}.zip"
            env = [
                f"工具目录 {APP_DIR}",
                f"Python {sys.version.split()[0]} · Tk {tk.TkVersion}",
                f"PID {os.getpid()} 提权 {self_elevated()}",
                f"窗口 {self.win.describe() if self.win else '未锁定'}",
                f"权限 {self._perm_note or '未检测'}",
                f"区域相对 {self.region_rel}",
                f"绝对 {self.region}",
                f"名单 {len(self.names)} 人",
                f"昵称库 {len(self._roster.get('names') or {})} 人 · "
                f"累计 {self._roster.get('rounds', 0)} 轮",
            ]
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                if LOG_PATH.exists():
                    z.write(LOG_PATH, "操作日志.txt")
                if CFG.exists():
                    z.write(CFG, "区域.json")
                # 库一起带上：复核名单拼写时，"这一条是原本就有还是本轮新认出" 全靠它
                _rp = booklib.roster_path()      # v1.3：活动套成员库
                if _rp.exists():
                    z.write(_rp, "昵称库.json")
                z.writestr("环境.txt", "\n".join(env) + "\n")
                if self.names:
                    lines = ["序号,昵称,置信度"]
                    lines += [f"{i},{n},{s:.3f}"
                              for i, (n, (s, _t)) in enumerate(self.names.items(), 1)]
                    z.writestr("当前名单.csv", "\ufeff" + "\n".join(lines) + "\n")
                if self._last_img is not None:
                    import io
                    buf = io.BytesIO()
                    self._last_img.save(buf, "PNG")
                    z.writestr("最近一屏.png", buf.getvalue())
                # 本次运行的失败存证（_failure_roi 落的那些帧）：复盘"哪一帧没认出来"
                # 全靠它，比事后重新跑一遍便宜得多
                if self._run_dir is not None:
                    for png in sorted((self._run_dir / "temp").glob("*.png"))[-40:]:
                        z.write(png, f"运行缓存/{png.name}")
            self.say(f"诊断包已生成：{out.name}")
            log("diag", f"导出诊断包 {out}")
            if messagebox.askyesno(
                "诊断包", f"已生成：\n{out}\n\n把这个文件发给我就能定位问题。\n打开所在文件夹？"
            ):
                import subprocess
                subprocess.Popen(["explorer", "/select,", str(out)])
        except Exception as exc:
            log_exc("diag", exc)
            messagebox.showwarning("导出失败", f"{exc}\n\n可以手动打包：{OUT_DIR}")

    def report_callback_exception(self, exc, val, tb):
        """Tk 回调里的异常默认只打到 stderr，pythonw 下等于直接消失。"""
        log("crash-tk", "".join(traceback.format_exception(exc, val, tb)), "ERR")
        try:
            self.say(f"内部错误：{val}（点右下角「打开日志」看详情）")
        except Exception:
            pass


def _seed_presets() -> None:
    """首次运行时把包里的预设文件播种到**程序数据目录**。

    为什么要这一步：`APP_DIR` 在冻结态是用户数据目录（`%APPDATA%\\OrangeHelper`，
    见 `appdir.py`），而随包带进来的预设全在 `_MEIPASS` 那个每次启动都会重建的
    临时目录里——不搬出来，exe 就永远认不到内置的标定结果，用户还得自己重新标一次。

    只播种**缺失**的文件，绝不覆盖：用户自己标过的结果比内置先验更准。
    昵称库不在包里（那是用户数据），所以不会被播种，第一次跑就是空库。

    v1.22 起递归播种：`成员库/`、`BOSS库/` 也在包里，只拷顶层 *.json 会让
    内置的二十六套 BOSS 名单在单文件版里消失（`_MEIPASS` 是临时目录）。

    失败一律忽略：预设拷不过去顶多让用户多点一次【标定昵称列】，不该拦启动。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = Path(getattr(sys, "_MEIPASS", ""))

    # 许可声明也一起搬出去：单文件发行版只有一个 exe，用户没地方看条款
    notice = meipass / "NOTICE.txt"
    if notice.is_file() and not (APP_DIR / "NOTICE.txt").exists():
        try:
            shutil.copy2(notice, APP_DIR / "NOTICE.txt")
        except Exception:
            pass

    src = meipass / "预设"
    if not src.is_dir():
        return
    dst = APP_DIR / "预设"
    try:
        dst.mkdir(exist_ok=True)
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            target = dst / f.relative_to(src)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
    except Exception:
        pass


def main():
    _fix_streams()
    dpi_aware()
    _seed_presets()
    install_crash_hooks()
    log_start()
    try:
        App().mainloop()
    except Exception:
        log_exc("main")
        tb = traceback.format_exc()
        try:
            (APP_DIR / "错误日志.txt").write_text(tb, encoding="utf-8")
        except Exception:
            pass
        try:
            import tkinter.messagebox as mb
            mb.showerror("启动失败", f"详见 输出/操作日志.txt\n\n{tb[-800:]}")
        except Exception:
            pass


if __name__ == "__main__":
    main()
