# -*- coding: utf-8 -*-
"""把竖版界面渲染成一张图，用来核排版（不需要人肉点，也不需要游戏在跑）。

    .venv\\Scripts\\python.exe uishot.py            # 跑一遍演示态并出图
"""
import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import Image

import alliance_ocr as T

HERE = Path(__file__).resolve().parent


def shot(app, out: Path) -> Path:
    """抓窗口。

    优先用屏幕复制（和用户真正看到的完全一致）：PrintWindow 抓 ttk 控件偶尔
    会拿到一份没按 style 重绘的旧缓存，表头会被误判成浅色。
    """
    from PIL import ImageGrab
    app.attributes("-topmost", True)
    app.lift()
    app.update()
    time.sleep(0.45)
    app.update()
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w0, h0 = app.winfo_width(), app.winfo_height()
    if w0 > 10 and h0 > 10:
        ImageGrab.grab((x, y, x + w0, y + h0)).save(out)
        app.attributes("-topmost", False)
        return out
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    h = app.winfo_id()
    h = u.GetAncestor(h, 2)          # 2 = GA_ROOT，取顶层窗口句柄
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    w, ht = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(h)
    mdc = g.CreateCompatibleDC(hdc)
    bmp = g.CreateCompatibleBitmap(hdc, w, ht)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(h, mdc, 2)

    class BI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    bi = BI()
    bi.biSize, bi.biWidth, bi.biHeight = ctypes.sizeof(BI), w, -ht
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * ht * 4)
    g.GetDIBits(mdc, bmp, 0, ht, buf, ctypes.byref(bi), 0)
    Image.frombuffer("RGB", (w, ht), buf, "raw", "BGRX", 0, 1).save(out)
    g.DeleteObject(bmp)
    g.DeleteDC(mdc)
    u.ReleaseDC(h, hdc)
    return out


DEMO = ["HANSON", "空木", "HGOLIVER", "凛酱", "萧白", "怎么还取名", "命OVO",
        "DORO", "小饼干", "夜猫", "Nyah", "灰狼", "青栀", "十六夜", "零号"]


def main():
    T._fix_streams()
    T.dpi_aware()
    app = T.App()
    app.update()

    # 演示态：正好停在"滚采到一半、已采 28 人"的样子，四段信息都有内容可看
    app.names = {n: (0.97 - i * 0.003, "12:0%d:00" % (i % 9)) for i, n in enumerate(DEMO)}
    app._total_hint = 32
    app._jumps, app._recover_walks = 1, 2
    app._refresh()
    app._refresh_ledger()
    for i, st in enumerate(("done", "done", "done", "doing", "idle")):
        app.steps.set(i, st, auto_next=False)
    app.task.set(72, "滚采中 · 第 24 次滚动 · 页面共 32 人 · 已采 28")
    app._sync_run_buttons = lambda: None
    for b in (app.btn_pause, app.btn_resume, app.btn_stop):
        b.config(state="normal", cursor="hand2")
    app.btn_start.config(state="disabled")
    app.disabled_run = True
    # 启动时的几行是走队列来的，先清空，图里只留演示内容（顺序才不会错乱）
    app.logpane.text.configure(state="normal")
    app.logpane.text.delete("1.0", "end")
    app.logpane.text.configure(state="disabled")
    app.logpane._lines = 0
    for line, lv in (
        ("12:14:02.113 [I] nav: 已拉回第一行（拖动 反向 9 次）", "INFO"),
        ("12:14:07.402 [I] roll: 第 1 次 · 拖动 拖 104px（行距 71px · 死区 15px）", "INFO"),
        ("12:14:09.880 [I] apply: auto=True 本屏 5 行 净增 5 累计 5 人", "INFO"),
        ("12:16:41.026 [W] auto: 两屏无重合行（第 1 次）· 先往回补采", "WARN"),
        ("12:16:47.517 [I] auto: ✅ 补采接上了跳步前的位置（用了 2 步）", "INFO"),
        ("12:18:20.330 [I] total: 页面读出成员总数 32（原文「联盟成员 (32/32)」）", "INFO"),
        ("12:18:22.104 [I] roll: 第 17 次 · 拖动 拖 63px（行距 70px · 死区 15px）", "INFO"),
        ("12:18:23.641 [I] scan: 111x353 · 候选带 5 · 过色 5 · 识别 5 · 74ms", "INFO"),
        ("12:18:23.702 [I] auto: 本屏 5 行 · 与上屏重合 3 行 → 这次走了 2 行（拖动）", "INFO"),
        ("12:18:23.703 [I] ui: 自动采集中 · 共 28 人（本屏 +2）", "INFO"),
    ):
        app.logpane.append(line, lv)
    # 采集中的样子：日志区撑开、停在最新一行
    app._log_mode(True)

    out = shot(app, T.OUT_DIR / "界面_竖版.png")
    print("界面截图:", out)

    # 成员变动视图：给一份"最近几轮有人进出"的演示数据，四种行都要出现
    # （无变动 / 有变动 / 未采完 / 移出），排版有没有问题一眼能看出来
    app._roster = {
        "version": 1, "rounds": 82,
        "names": {n: {"first": "2026-10-20 20:00", "last": "2026-10-30 14:18",
                      "rounds": 40, "miss": 0, "score": 1.0, "alias": []}
                  for n in DEMO[:8]},
        "history": [
            {"t": "2026-10-27 21:02", "n": 32, "total": 32, "ok": True,
             "add": [], "gone": [], "drop": [], "alias": 0},
            {"t": "2026-10-28 20:11", "n": 32, "total": 32, "ok": True,
             "add": ["冷面笑匠"], "gone": [], "drop": [], "alias": 1},
            {"t": "2026-10-29 20:04", "n": 18, "total": 32, "ok": False,
             "add": ["冷面笑匠"], "gone": [], "drop": [], "alias": 0},
            {"t": "2026-10-29 21:33", "n": 32, "total": 32, "ok": True,
             "add": [], "gone": ["灰姑娘"], "drop": [], "alias": 2},
            {"t": "2026-10-30 13:55", "n": 32, "total": 32, "ok": True,
             "add": ["新来的", "冷面笑匠"], "gone": ["灰姑娘"], "drop": [], "alias": 3},
            {"t": "2026-10-30 14:18", "n": 32, "total": 32, "ok": True,
             "add": ["冷面笑匠"], "gone": ["灰姑娘"], "drop": [], "alias": 1},
        ],
    }
    app.churn_dialog()
    dlg = [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"][-1]
    dlg.update()
    # 选中"有变动"那一轮，详情区才有内容可看
    from tkinter import ttk
    trees = [c for c in dlg.winfo_children() for c in c.winfo_children()
             if isinstance(c, ttk.Treeview)]
    if trees:
        kids = trees[0].get_children()
        if len(kids) >= 2:
            trees[0].selection_set(kids[1])
    dlg.update()
    out2 = shot(dlg, T.OUT_DIR / "成员变动.png")
    print("变动视图:", out2)
    app.destroy()


if __name__ == "__main__":
    main()
