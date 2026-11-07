# -*- coding: utf-8 -*-
"""诊断包：一份环境快照 + 运行日志，打成一个 zip。

为什么要有这个：用户报「采到一半停了」「名字识别错」，一句话没法复现。
日志里已经有每一步的坐标、判定和耗时，缺的是「他这台机器长什么样」——
同一条判据在 100% 缩放和 150% 缩放、1080p 和 4K 上的表现完全不同。

采集边界（硬约束，改之前先想清楚）：

    采：系统版本与位数、屏幕分辨率/缩放比/显示器数、CPU 型号与线程数、
        物理内存、Python/Qt/OCR 版本、是否管理员、数据目录是否可写、
        库数量与人数
    不采：用户名、计算机名、IP、MAC、磁盘序列号、游戏账号
    不带：屏幕截图 —— 一屏画面就是整份联盟名单

日志本身会写进 `C:\\Users\\<用户名>\\Documents\\...` 这类路径，所以导出前
统一过一遍 `mask()`：宁可少一个字段，也不要把能定位到人的东西发出去。
"""
import ctypes
import json
import os
import platform
import re
import struct
import zipfile
from datetime import datetime
from pathlib import Path

import alliance_ocr as A

from . import paths as P
from . import store as S
from . import version as V


class _MemStat(ctypes.Structure):
    """MEMORYSTATUSEX。要用 physical 总量，wmic 太慢，直接问内核。"""
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


# 兜底擦除：万一某个库把 IP/MAC 打进了日志。版本号是 2~3 段，撞不上 4 段的点分十进制。
_RX_IP = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b")
_RX_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")


def mask(text) -> str:
    """把文本里能定位到人的片段换掉。

    只按 `Users\\<用户名>` 这种带上下文的形态替换，不做裸词替换 ——
    用户名短的时候（比如 `App`）裸替换会把正常单词也吃掉。
    """
    s = str(text)
    home = str(Path.home())
    for h in (home, home.replace("\\", "/")):
        if h:
            s = s.replace(h, "<用户目录>")
    user = os.environ.get("USERNAME") or ""
    if len(user) >= 2:
        s = re.sub(rf"(?i)(users[\\/]+){re.escape(user)}", r"\1<用户>", s)
    host = os.environ.get("COMPUTERNAME") or ""
    if len(host) >= 3:
        s = re.sub(rf"(?i)\b{re.escape(host)}\b", "<计算机>", s)
    s = _RX_IP.sub("<IP>", s)
    s = _RX_MAC.sub("<MAC>", s)
    return s


def _mem_gb() -> float:
    try:
        m = _MemStat()
        m.dwLength = ctypes.sizeof(_MemStat)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullTotalPhys / 1024 ** 3
    except Exception:
        pass
    return 0.0


def _screens() -> list:
    """屏幕：逻辑/物理分辨率 + 缩放比 + 显示器数。

    采集坐标是按物理像素算的，所以物理尺寸必须单列 —— 只报 Qt 的逻辑尺寸
    会让人误判成"他的屏幕怎么这么小"，而这恰恰是坐标全偏的典型原因。
    """
    try:
        from PySide6.QtGui import QGuiApplication
        out = []
        for s in QGuiApplication.screens():
            g = s.geometry()
            dpr = s.devicePixelRatio() or 1.0
            out.append(f"{s.name()} {g.width()}x{g.height()} 逻辑 / "
                       f"{round(g.width() * dpr)}x{round(g.height() * dpr)} 物理 / "
                       f"缩放 {dpr:.2f}x")
        return out or ["（Qt 未就绪）"]
    except Exception as exc:
        return [f"（读取失败 {type(exc).__name__}）"]


def _lang() -> str:
    try:
        return f"{ctypes.windll.kernel32.GetUserDefaultUILanguage()}"
    except Exception:
        return "?"


def _frozen_kind() -> str:
    """单文件 / 目录版 / 源码。PyInstaller 的两种冻结态都设 `_MEIPASS`，
    区别在于单文件解压到临时目录、目录版就是 exe 旁边的 `_internal`。"""
    import sys as _s
    if not getattr(_s, "frozen", False):
        return "源码运行"
    try:
        meipass = Path(_s._MEIPASS).resolve()
        return "目录版" if meipass.parent == Path(_s.executable).resolve().parent \
            else "单文件（每次启动解压）"
    except Exception:
        return "?"


def _writable(p: Path) -> str:
    try:
        p.mkdir(parents=True, exist_ok=True)
        t = p / ".write_probe"
        t.write_text("1", encoding="utf-8")
        t.unlink()
        return "可写"
    except Exception as exc:
        return f"不可写（{type(exc).__name__}）"


def _lib_ver(pkg: str, mod: str = "") -> str:
    try:
        m = __import__(mod or pkg)
        v = getattr(m, "__version__", None)
        if v:
            return str(v)
    except Exception:
        pass
    try:
        from importlib.metadata import version as _v
        return _v(pkg)
    except Exception:
        # 冻结态没有 .dist-info，查不到是正常的：它就在包里，缺了会立刻炸
        return "内置"


def env_lines() -> list:
    bits = struct.calcsize("P") * 8
    try:
        elev = A.self_elevated()
        elev_txt = {True: "是（管理员）", False: "否", None: "?"}.get(elev, str(elev))
    except Exception:
        elev_txt = "?"
    root = None
    try:
        root = P.root()
    except Exception:
        pass

    lines = [
        "# 运行环境（导出时自动生成，可直接删除任意一行）",
        f"时间       {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"程序       {V.APP_NAME} {V.full_text()}",
        f"打包形态   {_frozen_kind()}",
        f"系统       {platform.platform()}（{bits} 位）",
        f"CPU        {platform.processor() or '?'} · {os.cpu_count()} 线程",
        f"内存       {_mem_gb():.1f} GB",
        f"界面语言   LCID {_lang()}",
        f"Python     {sys_version()}",
        f"Qt         {_lib_ver('PySide6', 'PySide6.QtCore')}",
        f"OCR        rapidocr {_lib_ver('rapidocr')} · onnxruntime {_lib_ver('onnxruntime')}",
        f"图像       Pillow {_lib_ver('Pillow', 'PIL')} · numpy {_lib_ver('numpy')} · "
        f"opencv {_lib_ver('opencv-python', 'cv2')}",
        f"权限       {elev_txt}",
        f"分辨率     {' | '.join(_screens())}",
        f"数据目录   {mask(root) if root else '?'} · {_writable(root) if root else '?'}",
    ]
    try:
        import booklib as BK
        lines.append(f"库         成员库 {len(BK.list_rosters())} 套 · "
                     f"BOSS 库 {len(BK.list_bosses())} 套")
    except Exception:
        pass
    try:
        lg = Path(A.LOG_PATH)
        if lg.exists():
            lines.append(f"日志       {lg.stat().st_size // 1024} KB")
    except Exception:
        pass
    return lines


def sys_version() -> str:
    import sys as _sys
    return _sys.version.split()[0] + ("" if not getattr(_sys, "frozen", False) else "（内置）")


def _cfg_text() -> str:
    """设置里的绝对路径全部脱敏；背景图只留文件名。"""
    try:
        d = dict(S.load_settings())
    except Exception:
        return "{}"
    for k in ("data_dir", "export_dir", "bg_image"):
        if d.get(k):
            d[k] = mask(d[k]) if k != "bg_image" else Path(str(d[k])).name
    return json.dumps(d, ensure_ascii=False, indent=1)


def export() -> Path:
    """生成诊断包，返回路径。调用方负责提示与打开文件夹。"""
    out = P.sub(P.DIAG) / P.diag_file()
    log = Path(A.LOG_PATH)
    added = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("环境.txt",
                   mask("\n".join(env_lines())) + "\n")
        for src, name in ((log, "操作日志.txt"),
                          (log.with_name("操作日志.1.txt"), "操作日志.1.txt")):
            try:
                if src.exists():
                    # 先读再写：日志可能正被 stdout 占着，交给 zip 直接读会撞共享冲突
                    z.writestr(name, mask(src.read_text(encoding="utf-8",
                                                        errors="replace")))
                    added.append(f"{name}（{src.stat().st_size // 1024} KB）")
            except Exception as exc:
                added.append(f"{name}（读取失败 {type(exc).__name__}）")
        z.writestr("配置.json", mask(_cfg_text()) + "\n")
        z.writestr("说明.txt", _README)
    if not added:
        added.append("操作日志.txt（本次还没有日志）")
    return out


_README = """\
这个包是给作者排查问题用的，内容如下：

  环境.txt      系统/屏幕/CPU/内存/运行库版本、数据目录是否可写
  操作日志.txt  每一步动作的时间戳、坐标、判定结果
  配置.json     界面设置（路径已脱敏）
  说明.txt      本文件

已刻意不包含：用户名、计算机名、IP、MAC、磁盘序列号、游戏账号、屏幕截图。
环境.txt 与 配置.json 里的库名和人数属于你自己的业务数据，发出去之前
可以先用记事本打开看一眼，删掉不想给的部分也完全不影响定位问题。
"""
