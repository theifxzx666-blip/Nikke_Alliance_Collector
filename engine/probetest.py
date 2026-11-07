# -*- coding: utf-8 -*-
"""验证「试滚一格」量得准：假游戏窗口一格滚 N 行，看它报出来的行数对不对。

    .venv\\Scripts\\python.exe probetest.py       # 一格滚 1 行
    .venv\\Scripts\\python.exe probetest.py 3     # 一格滚 3 行
"""
import importlib.util
import sys
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

tool.dpi_aware()
tool.CFG = tool.OUT_DIR / "_test_区域.json"
tool.PRESET = tool.OUT_DIR / "_test_预设.json"
tool.LOG_PATH = tool.OUT_DIR / "_test_操作日志.txt"   # 别把测试记录混进用户日志
tool.CFG.unlink(missing_ok=True)
tool.messagebox.showwarning = lambda *a, **k: None

NAMES = ["DORO", "曼殊颜华", "新来的", "非洲晒太阳", "PPORANGE",
         "夜刃", "NIKKE01", "冷面笑匠", "雪之下", "Zzz", "红莲", "灰姑娘"]
PAGE, ROW_H = 5, 60
# 假窗口必须是真的 16:9（720×405 = 1.778）：坐标基准是「有效画面」，
# 比例不对会被直接拒绝，测的就成了"能不能拦住"，不是"量得准不准"。
WIN_X, WIN_Y, WIN_W, WIN_H = 1200, 100, 720, 405
LIST_TOP, LIST_LEFT = 80, 20
STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 1

app = tool.App()
app.geometry("960x620+0+0")

game = tk.Toplevel(app)
game.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
game.attributes("-topmost", True)
game.configure(bg="#F7F7F5")
game.title("fake game")

cv = tk.Canvas(game, bg="#F7F7F5", highlightthickness=0)
cv.pack(fill="both", expand=True)
state = {"off": 0}


def draw():
    cv.delete("all")
    for i, nm in enumerate(NAMES[state["off"]:state["off"] + PAGE]):
        cv.create_text(LIST_LEFT + 10, LIST_TOP + i * ROW_H + ROW_H // 2,
                       text=nm, anchor="w", fill="#424242",
                       font=("Microsoft YaHei UI", 22, "bold"))


def on_wheel(e):
    d = -int(e.delta / 120) * STEP
    if d:
        state["off"] = max(0, min(len(NAMES) - PAGE, state["off"] + d))
        draw()


game.bind("<MouseWheel>", on_wheel)
draw()
game.focus_force()
app.update()

x0, y0 = game.winfo_rootx() + LIST_LEFT, game.winfo_rooty() + LIST_TOP
REGION = (x0, y0, x0 + 500, y0 + PAGE * ROW_H)

_hwnd = int(tool.ctypes.windll.user32.GetAncestor(game.winfo_id(), tool.GA_ROOT))
_win = tool.content_rect(_hwnd)
app._adopt(_win)
app.region_rel = ((REGION[0] - _win.x) / _win.w, (REGION[1] - _win.y) / _win.h,
                  (REGION[2] - _win.x) / _win.w, (REGION[3] - _win.y) / _win.h)

print(f"假游戏 一页 {PAGE} 行 · 行距 {ROW_H}px · 一格滚 {STEP} 行 "
      f"→ 期望探测到【滚轮】并报 ≈{STEP} 行")

app.after(1200, app.probe_scroll)
# 试滚依次试 拖动/滚轮/翻页，每种都要"等画面停稳"（最长 2.8s），
# 假窗口不认拖动，所以前两种会走满超时，给足 14s
app.after(14000, app.quit)
app.mainloop()

msg = app.status.cget("text")
print(f"状态栏：{msg}")
app.destroy()

want = f"≈ {STEP} 行"
ok = "【滚轮】" in msg and want in msg
print("\n=== 结果 ===")
print("通过" if ok else "未通过")
