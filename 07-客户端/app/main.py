# -*- coding: utf-8 -*-
"""客户端入口。

    python -m app            # 正常启动
    python -m app --shot x.png   # 渲染一帧并存图后退出（自检用，不弹窗停留）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

import alliance_ocr as A

from app import paths as P
from app import theme as T
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


def main():
    args = sys.argv[1:]
    shot = None
    if "--shot" in args:
        i = args.index("--shot")
        shot = args[i + 1] if len(args) > i + 1 else "shot.png"

    _bind_storage()          # 先定好落盘位置，后面所有日志都写在这里

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("NIKKE 联盟采集")
    f = QFont(T.FONT_UI)
    f.setPixelSize(T.SZ_BODY)
    app.setFont(f)
    app.setStyleSheet(T.qss())

    w = Shell()
    if shot:
        # 只出图核版式：不自动抢游戏窗口、不跟随、不写设置
        w.autoconnect = False
        w._watch.stop()
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
