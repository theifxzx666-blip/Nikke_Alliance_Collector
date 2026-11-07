# -*- coding: utf-8 -*-
"""客户端入口。

    python -m app            # 正常启动
    python -m app --shot x.png   # 渲染一帧并存图后退出（自检用，不弹窗停留）
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer, __version__ as QT_VER
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

import alliance_ocr as A

from app import paths as P
from app import theme as T
from app import version as V
from app.shell import Shell
from app.widgets import audit_layout


def _bind_storage():
    """把引擎的日志 / 诊断包出口指到用户选的「数据目录」（v1.18）。

    客户端与旧工具是不同进程，改模块常量互不影响；旧工具仍写它自己的 输出/。
    """
    try:
        A.LOG_PATH = P.log_file()
        A.DIAG_DIR = P.sub(P.DIAG)
    except Exception:
        pass


def _boot_log():
    """打包后没有控制台：把 stdout/stderr 接到日志、装崩溃钩子、写环境快照。

    这三步必须在建窗口之前 —— 建窗口本身就会抛（缺 dll、DPI 设置失败），
    没接住的话用户看到的是「双击没反应」，日志里一个字都没有。
    """
    try:
        A._fix_streams()                   # sys.stdout 为 None 时的兜底，早于任何 print
        A.install_crash_hooks()
        A.log_start(f"客户端 {V.full_text()} · PySide6 {QT_VER}")
    except Exception:
        pass


def main():
    args = sys.argv[1:]
    shot = None
    if "--shot" in args:
        i = args.index("--shot")
        shot = args[i + 1] if len(args) > i + 1 else "shot.png"

    _bind_storage()          # 先定好落盘位置，后面所有日志都写在这里
    # 单文件版的内置预设全在 `_MEIPASS`（每次启动重建的临时目录）里，
    # 必须在 Shell 读库之前搬到程序数据目录（`%APPDATA%\OrangeHelper`），
    # 否则 26 套 BOSS 库等于没打进去。
    try:
        A._seed_presets()
    except Exception:
        pass
    _boot_log()

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("NIKKE 联盟采集")
    # 必须在任何控件创建之前：系统浅色主题下 Qt 内部控件（滚动区 viewport、
    # 弹窗列表、菜单）会用白色底，本项目的半透明配色压上去就是白屏。
    app.setPalette(T.dark_palette())
    f = QFont(T.FONT_UI)
    f.setPixelSize(T.SZ_BODY)
    app.setFont(f)
    app.setStyleSheet(T.qss())

    if "--diag" in args:
        # 窗口起不来时的兜底：命令行导出诊断包，不用先进界面
        from app import diag as D
        try:
            print("诊断包：", D.export())
            return 0
        except Exception:
            traceback.print_exc()
            return 1

    w = Shell()
    if shot:
        # 只出图核版式：不自动抢游戏窗口、不跟随、不写设置
        w.autoconnect = False
        w._watch.stop()
        w._scope_timer.stop()       # 自检不去抢全局热键
    scr = app.primaryScreen().availableGeometry()
    w.move(max(0, scr.right() - w.width() - 40),
           max(0, scr.top() + (scr.height() - w.height()) // 2))
    w.prepare_background()
    w.show()

    if "--debug" in args:
        QTimer.singleShot(120, w.toggle_debug)

    if shot:
        def finish():
            issues = audit_layout(w)
            print("布局体检：" + ("0 处异常" if not issues else f"{len(issues)} 处异常"))
            for s in issues:
                print("  -", s)
            out = Path(shot).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            w.grab().save(str(out))
            print("已保存", out)
            app.quit()

        QTimer.singleShot(500, finish)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
