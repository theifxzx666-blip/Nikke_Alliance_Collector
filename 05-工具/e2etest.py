# -*- coding: utf-8 -*-
"""端到端验证：真的让工具自己滚、自己认，看能不能把 12 个人全部采到。

做法：
  1. 启动工具（真实 App，用 mainloop 跑）
  2. 挂一个"假游戏窗口"作为它的 Toplevel：一页 5 行名字，
     按参数决定认滚轮还是认拖动（真实联盟列表只认拖动，且拖动带惯性）
  3. 把 region 指到假窗口，调 toggle_auto() 走真实自动流程
  4. 检查最终名单数量

    .venv\\Scripts\\python.exe e2etest.py          # 假窗口只认滚轮
    .venv\\Scripts\\python.exe e2etest.py 1 drag   # 只认拖动（带 2.0 倍惯性），验证闭环步长
    .venv\\Scripts\\python.exe e2etest.py 6 drag   # 拖一格跨 6 行，验证连续两次对不上才叫停
"""
import importlib.util
import os
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

tool.dpi_aware()

# 配置文件重定向到测试专用，避免污染用户真实记录
tool.CFG = tool.OUT_DIR / "_test_区域.json"
tool.PRESET = tool.OUT_DIR / "_test_预设.json"   # 别把测试结果写成用户的预设
tool.LOG_PATH = tool.OUT_DIR / "_test_操作日志.txt"   # 别把测试记录混进用户日志
tool.ROSTER = tool.OUT_DIR / "_test_昵称库.json"      # 必须重定向：跑完一轮会 _commit_roster，不重定向就写坏真库
tool.ROSTER.unlink(missing_ok=True)
tool.CFG.unlink(missing_ok=True)

# 自动流程里的确认框不好无人值守，直接放行
tool.messagebox.askyesno = lambda *a, **k: True
tool.messagebox.showinfo = lambda *a, **k: None
tool.is_foreground = lambda hwnd: True      # 前台归属由 winlocktest 单独覆盖

NAMES = [
    "DORO", "曼殊颜华", "新来的", "非洲晒太阳", "PPORANGE",
    "夜刃", "NIKKE01", "冷面笑匠", "雪之下", "Zzz",
    "红莲", "灰姑娘",
]
PAGE = 5
ROW_H = 60
# 假窗口必须是真正的 16:9（720×405 = 1.778）：新版坐标基准是「有效画面」，
# 比例不是 16:9 会被直接拒绝；高度还得 >= MIN_CLIENT_H(400)。
# 假窗口的位置。真机上如果正好开着工具本体（或标定浮层是全屏 topmost），
# 它会盖住假窗口，采集会被"采集区被 xxx 挡住了"正确拦下——那是产品该有的行为，
# 不是 bug。这种情况用环境变量把假窗口挪到空处再跑：
#     E2E_X=100 E2E_Y=560 .venv\Scripts\python.exe e2etest.py
WIN_X, WIN_Y, WIN_W, WIN_H = int(os.environ.get("E2E_X", 1200)), \
    int(os.environ.get("E2E_Y", 100)), 720, 405
LIST_TOP, LIST_LEFT = 40, 20
LIST_W = 500
REGION = None

app = tool.App()
app.geometry("960x620+0+0")

game = tk.Toplevel(app)
game.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
game.attributes("-topmost", True)
game.configure(bg="#F7F7F5")
game.title("fake game")

cv = tk.Canvas(game, bg="#F7F7F5", highlightthickness=0)
cv.pack(fill="both", expand=True)

state = {"off": 0, "rolls": 0}


def draw():
    cv.delete("all")
    for i, nm in enumerate(NAMES[state["off"]:state["off"] + PAGE]):
        cv.create_text(LIST_LEFT + 10, LIST_TOP + i * ROW_H + ROW_H // 2,
                       text=nm, anchor="w", fill="#424242",
                       font=("Microsoft YaHei UI", 22, "bold"))


STEP = int(sys.argv[1]) if len(sys.argv) > 1 else 1   # 模拟"一格滚 N 行"
WHICH = sys.argv[2] if len(sys.argv) > 2 else "wheel"  # wheel / drag / both
FLING = 2.0            # 松手惯性倍率：真实列表实测约 1.8~2.2


def move(d):
    if d == 0:
        return
    state["off"] = max(0, min(len(NAMES) - PAGE, state["off"] + d))
    state["rolls"] += 1
    draw()


def on_wheel(e):
    if WHICH not in ("wheel", "both"):
        return                     # 只认拖动的列表，滚轮完全无效
    move(-int(e.delta / 120) * STEP)


# 拖动没法用 Tk 的 <B1-Motion> 事件来模拟：工具的 drag_at() 跑在 Tk 主线程上，
# 拖动期间主循环是阻塞的，事件排到拖动结束才被派发，窗口根本画不出来。
# 真实游戏是独立进程，不受影响。所以这里用独立线程直接读系统鼠标状态，
# 等效于跨进程：由监视线程算出这一拖的位移，再交给 Tk 定时器落到假列表上。
mon = {"down": False, "y0": 0, "dy": 0, "pending": 0}


def watch_mouse():
    u = tool.ctypes.windll.user32
    pt = tool._POINT()
    while True:
        down = bool(u.GetAsyncKeyState(0x01) & 0x8000)
        u.GetCursorPos(tool.ctypes.byref(pt))
        if down and not mon["down"]:
            mon["down"], mon["y0"], mon["dy"] = True, pt.y, 0
        elif down and mon["down"]:
            mon["dy"] = mon["y0"] - pt.y
        elif not down and mon["down"]:
            mon["down"] = False
            mon["pending"] = mon["dy"]
        time.sleep(0.008)


def apply_pending():
    d = mon["pending"]
    if d:
        mon["pending"] = 0
        if abs(d) >= 8:
            # 拖动 + 松手惯性：内容位移 = 手指位移 × FLING
            move(int(round(d * STEP * FLING / ROW_H)))
    app.after(50, apply_pending)


game.bind("<MouseWheel>", on_wheel)
if WHICH in ("drag", "both"):
    threading.Thread(target=watch_mouse, daemon=True).start()
    app.after(50, apply_pending)
draw()
game.focus_force()
app.update()          # 让窗口真正 map，才能取到客户区坐标

cx0, cy0 = game.winfo_rootx() + LIST_LEFT, game.winfo_rooty() + LIST_TOP
REGION = (cx0, cy0, cx0 + LIST_W, cy0 + PAGE * ROW_H)

print(f"假游戏窗口 {WIN_W}x{WIN_H} @({WIN_X},{WIN_Y}) · 一页 {PAGE} 行 · 共 {len(NAMES)} 人"
      f" · 只认{ {'wheel': '滚轮', 'drag': '拖动', 'both': '拖动和滚轮'}[WHICH] }"
      f" · 一格 {STEP} 行 · 惯性 {FLING}")
print(f"客户区原点 ({game.winfo_rootx()},{game.winfo_rooty()}) · region = {REGION}")

# 等价于用户点【① 锁定窗口】+【② 点选昵称列】：先锁窗口，再把标定结果换算成相对比例
_hwnd = int(tool.ctypes.windll.user32.GetAncestor(game.winfo_id(), tool.GA_ROOT))
_win = tool.content_rect(_hwnd)
app._adopt(_win)
app.region_rel = (
    (REGION[0] - _win.x) / _win.w, (REGION[1] - _win.y) / _win.h,
    (REGION[2] - _win.x) / _win.w, (REGION[3] - _win.y) / _win.h,
)
_pitch, _rows = tool._measure_pitch(tool.grab(REGION))
app._calib_pitch, app._calib_rows = _pitch, _rows
print(f"已锁定 {_win.describe()} · 区域占有效画面 "
      f"{app.region_rel[2]-app.region_rel[0]:.1%} × {app.region_rel[3]-app.region_rel[1]:.1%}"
      f" · 行距 {_pitch} · 每屏 {_rows} 行")

t0 = time.time()


def start():
    app.toggle_auto()


def watch():
    if app._continuous and time.time() - t0 < 120:
        # 顺手验证"边跑边看日志"：采集期间日志区必须撑开、并且一直停在最新一行。
        # 只测组件本身不够——真正跑起来时是子线程在写，这里才是真实链路。
        try:
            samples.append((bool(app.logpane._follow),
                            float(app.logpane.text.yview()[1]),
                            int(app.logpane.rows)))
        except Exception:
            pass
        app.after(200, watch)
    else:
        app.quit()


samples = []


app.after(1200, start)      # 等模型预热完
app.after(1400, watch)
app.mainloop()

got = set(app.names)
missing = [n for n in NAMES if n not in got]
extra = [n for n in got if n not in NAMES]
elapsed = time.time() - t0

print(f"\n滚轮实际响应 {state['rolls']} 次 · 耗时 {elapsed:.1f}s")
print(f"状态栏：{app.status.cget('text')}")
print(f"采到 {len(got)}/{len(NAMES)} 人")

follow_ok = all(s[0] for s in samples)
bottom_ok = all(s[1] >= 0.999 for s in samples)
tall = max((s[2] for s in samples), default=0)
print(f"日志区：采样 {len(samples)} 次 · 全程跟随={'是' if follow_ok else '否'}"
      f" · 始终停在最新={'是' if bottom_ok else '否'} · 采集时高度 {tall} 行")
print("名单：", " ".join(sorted(got)))
if missing:
    print("漏采：", missing)
if extra:
    print("误采：", extra)

app.destroy()

print("\n=== 结果 ===")
ok = (len(got) == len(NAMES) and not extra and samples
      and follow_ok and bottom_ok and tall == tool.LOG_ROWS_RUN)
print("全部采齐，无漏无误，日志全程跟到最新" if ok else "未通过")
