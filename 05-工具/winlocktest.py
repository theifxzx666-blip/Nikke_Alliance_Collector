# -*- coding: utf-8 -*-
"""窗口锁定机制验证：锁定 → 框选 → 挪窗口 / 缩放 / 比例变化 / 最小化 / 遮挡。

不碰真实游戏，用一个可控的「假游戏窗口」当靶子，走 App 的真实方法：
  capture() 会跑 resolve_region()，所以我们能验到真实的校验链路。

    .venv\\Scripts\\python.exe winlocktest.py
"""
import importlib.util
import traceback
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
tool.dpi_aware()

# 配置文件重定向到测试专用，避免污染用户真实记录
tool.CFG = tool.OUT_DIR / "_test_区域.json"
tool.PRESET = tool.OUT_DIR / "_test_预设.json"
tool.CFG.unlink(missing_ok=True)

# 无人值守：确认框放行，警告收集起来供断言
tool.messagebox.askyesno = lambda *a, **k: True
tool.messagebox.showinfo = lambda *a, **k: None
warnings: list[str] = []
tool.messagebox.showwarning = lambda title, msg, **k: warnings.append(msg)

NAMES = ["DORO", "曼殊颜华", "新来的", "非洲晒太阳", "PPORANGE", "夜刃", "NIKKE01"]
PAGE, ROW_H = 5, 60
LIST_TOP, LIST_LEFT = 40, 20
# 假窗口必须是真正的 16:9：新版的坐标基准是「有效画面」，而有效画面比例
# 不是 16:9 时会被直接拒绝（UI 会跟着比例重排）。720×405 = 1.778，
# 高度还得 >= MIN_CLIENT_H(400)，否则会被"窗口太小"拦下。
WIN_W, WIN_H = 720, 405
LIST_W = 500

app = tool.App()
app.geometry("960x620+0+0")

game = tk.Toplevel(app)
game.geometry(f"{WIN_W}x{WIN_H}+1200+100")
game.configure(bg="#F7F7F5")
game.title("fake game")
game.attributes("-topmost", True)

cv = tk.Canvas(game, bg="#F7F7F5", highlightthickness=0)
cv.pack(fill="both", expand=True)
state = {"off": 0}


def draw():
    cv.delete("all")
    for i, nm in enumerate(NAMES[state["off"]:state["off"] + PAGE]):
        cv.create_text(LIST_LEFT + 10, LIST_TOP + i * ROW_H + ROW_H // 2,
                       text=nm, anchor="w", fill="#424242",
                       font=("Microsoft YaHei UI", 22, "bold"))


game.bind("<MouseWheel>", lambda e: None)   # 本测试手动改 off，不靠滚轮
draw()
game.focus_force()
app.update()

results: list[tuple[str, bool, str]] = []


def check(label: str, cond: bool, detail: str = ""):
    results.append((label, bool(cond), detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


def game_info() -> "tool.WinInfo":
    """按 tkinter 报告的客户区造一个 WinInfo，再让 client_rect 校正 hwnd。"""
    hwnd = int(tool.ctypes.windll.user32.GetAncestor(game.winfo_id(), tool.GA_ROOT))
    info = tool.client_rect(hwnd)
    assert info is not None, "假窗口客户区取不到"
    return info


def rel_of(info, box):
    x0, y0, x1, y1 = box
    return ((x0 - info.x) / info.w, (y0 - info.y) / info.h,
            (x1 - info.x) / info.w, (y1 - info.y) / info.h)


# ------------- 各步骤

def s_lock():
    print("\n[1] 锁定窗口 + 模拟标定")
    info = game_info()
    app._adopt(info)
    box = (info.x + LIST_LEFT, info.y + LIST_TOP,
           info.x + LIST_LEFT + LIST_W, info.y + LIST_TOP + PAGE * ROW_H)
    app.region_rel = rel_of(info, box)
    ok, region, err = app.resolve_region(check_obscure=False)
    check("锁定后客户区可读", info.w > 0 and info.h > 0, info.describe())
    check("比例换算可用", ok, err)
    if ok:
        check(f"换算后尺寸 = {LIST_W}×{PAGE * ROW_H}",
              (region[2] - region[0], region[3] - region[1]) == (LIST_W, PAGE * ROW_H),
              str(region))
        # 顺带验一遍行距测量：它会被存进配置，自动滚采拿它当步长初值
        pitch, rows = tool._measure_pitch(tool.grab(region))
        app._calib_pitch, app._calib_rows = pitch, rows
        check(f"量出 {PAGE} 行 · 行距 {ROW_H}",
              rows == PAGE and pitch is not None and abs(pitch - ROW_H) <= 1,
              f"pitch={pitch} rows={rows}")


def s_capture1():
    print("\n[2] 首次采集")
    app.capture()


def s_check1():
    got = set(app.names)
    expect = set(NAMES[:PAGE])
    check("首屏采到 5 人", got == expect, f"{len(got)} 人：{' '.join(sorted(got))}")


def s_move():
    print("\n[3] 把游戏窗口挪到别的位置（不重框）")
    before = app.resolve_region(check_obscure=False)[1]
    game.geometry(f"{WIN_W}x{WIN_H}+300+200")
    app.update()
    ok, region, err = app.resolve_region(check_obscure=False)
    check("挪窗口后仍可用", ok, err)
    if ok and before:
        dx, dy = region[0] - before[0], region[1] - before[1]
        check("区域随窗口平移", (dx, dy) != (0, 0),
              f"区域平移 ({dx:+d},{dy:+d})，窗口从 +1200+100 移到 +300+200")
        check("区域尺寸不变",
              (region[2] - region[0], region[3] - region[1]) == (LIST_W, PAGE * ROW_H))
    state["off"] = 2
    draw()
    app.update()


def s_capture2():
    print("\n[4] 挪完窗口继续采（滚了 2 行）")
    app.capture()


def s_check2():
    got = set(app.names)
    check("新增 2 人，累计 7", got == set(NAMES), f"{len(got)} 人：{' '.join(sorted(got))}")


def s_resize_same():
    print("\n[5] 改窗口大小但比例不变（720×405 → 1440×810，2 倍）")
    game.geometry("1440x810+300+200")
    app.update()
    ok, region, err = app.resolve_region(check_obscure=False)
    check("允许继续用", ok, err)
    if ok:
        w, h = region[2] - region[0], region[3] - region[1]
        check("区域按比例放大", (w, h) == (LIST_W * 2, PAGE * ROW_H * 2), f"{w}×{h}")


def s_resize_diff():
    print("\n[6] 改窗口比例（1440×810 → 1440×900，1.6 不是 16:9）")
    game.geometry("1440x900+300+200")
    app.update()
    ok, region, err = app.resolve_region(check_obscure=False)
    check("拒绝采集", not ok, err)
    check("提示里写明比例不对", "16:9" in err)


def s_minimize():
    print("\n[7] 最小化游戏窗口")
    tool.ctypes.windll.user32.ShowWindow(game_info().hwnd, 6)   # SW_MINIMIZE
    app.update()
    ok, _r, err = app.resolve_region(check_obscure=False)
    check("拒绝采集", not ok, err)
    check("提示指向窗口/最小化", "找不到" in err or "最小化" in err)


def s_restore():
    print("\n[8] 还原窗口，测遮挡")
    hwnd = int(tool.ctypes.windll.user32.GetAncestor(game.winfo_id(), tool.GA_ROOT))
    tool.ctypes.windll.user32.ShowWindow(hwnd, 9)      # SW_RESTORE
    game.geometry(f"{WIN_W}x{WIN_H}+200+150")
    game.attributes("-topmost", False)
    app.update()
    app.lift()
    app.focus_force()
    app.update()


def s_obscure():
    ok, region, err = app.resolve_region(check_obscure=True)
    check("被遮挡时拒绝采集", not ok, err)
    check("提示里点名遮挡者", "挡住" in err)


def s_capture_blocked():
    print("\n[9] 遮挡状态下点采集，应被拦下且不写入错误数据")
    before = len(app.names)
    app.capture(auto=False)
    app.update()
    check("名单未被污染", len(app.names) == before, f"{before} → {len(app.names)}")
    check("弹出了警告", len(warnings) > 0, warnings[-1].splitlines()[0] if warnings else "")


def s_selfcheck():
    print("\n[10] 配置结构")
    app._save_cfg()
    import json
    raw = json.loads(tool.CFG.read_text(encoding="utf-8"))
    check("版本号为 3", raw.get("version") == 3)
    check("存的是相对比例", "region_rel" in raw and "region" not in raw, str(raw.get("region_rel")))
    check("记录了窗口比例", "aspect" in (raw.get("window") or {}), str(raw.get("window")))
    check("记录了行距/每屏行数",
          raw.get("rows_per_screen") == PAGE and abs(float(raw.get("pitch") or 0) - ROW_H) <= 1,
          f"pitch={raw.get('pitch')} rows={raw.get('rows_per_screen')}")
    rel = raw["region_rel"]
    check("比例在 0~1 之间", all(0.0 <= v <= 1.0 for v in rel), str(rel))


def s_ui():
    """工具只服务一个客户端：接窗口是后台的事，界面上不该出现"锁定"这一步。"""
    print("\n[11] 界面：接窗口不在前台，比例看得出档位")
    texts = []

    def walk(w):
        try:
            texts.append(str(w.cget("text")))
        except Exception:
            pass
        for c in w.winfo_children():
            walk(c)

    walk(app)
    joined = " | ".join(texts)
    check("界面上没有「锁定窗口」按钮", "锁定窗口" not in joined, joined[:120])
    check("主按钮是「开始」", app.btn_start.cget("text") == "开始",
          app.btn_start.cget("text"))
    check("开始键是强调色（说明它是主操作）", str(app.btn_start.cget("bg")).lower()
          not in ("", "panel", "systembuttonface"))
    # 竖版把运行控制拆成四键（开始/暂停/继续/取消），未开跑时后三个必须点不动，
    # 否则用户会在空闲状态按"取消"然后以为出了什么问题
    check("空闲时暂停/继续/取消都置灰",
          all(str(b.cget("state")) == "disabled"
              for b in (app.btn_pause, app.btn_resume, app.btn_stop)),
          " / ".join(str(b.cget("state"))
                     for b in (app.btn_pause, app.btn_resume, app.btn_stop)))
    check("步骤条有五步", list(app.steps.steps) == tool.FLOW_STEPS,
          " / ".join(app.steps.steps))
    check("兜底按钮平时不存在", app._btn_manual is None, str(app._btn_manual))
    check("比例档位可读", (tool.aspect_label(16 / 9), tool.aspect_label(16 / 10),
                          tool.aspect_label(2560 / 1080))
          == ("16:9", "16:10", "21:9"), str(tool.aspect_label(2560 / 1080)))
    check("怪比例不硬凑档位", tool.aspect_label(1.85) == "1.850", tool.aspect_label(1.85))
    check("比例命中的画面在信息条上带 ✓", "✓" in app.info_var.get()
          or app.win is None, app.info_var.get()[:80])


STEPS = [
    (s_lock, 700),
    (s_capture1, 2600),
    (s_check1, 500),
    (s_move, 800),
    (s_capture2, 2600),
    (s_check2, 500),
    (s_resize_same, 800),
    (s_resize_diff, 800),
    (s_minimize, 800),
    (s_restore, 900),
    (s_obscure, 400),
    (s_capture_blocked, 900),
    (s_selfcheck, 400),
    (s_ui, 400),
]


def runner(i=0):
    if i >= len(STEPS):
        app.quit()
        return
    fn, wait = STEPS[i]
    try:
        fn()
    except Exception:
        traceback.print_exc()
        check(f"{fn.__name__} 抛异常", False)
    app.after(wait, lambda: runner(i + 1))


app.after(1200, runner)    # 等模型预热
app.mainloop()

bad = [r for r in results if not r[1]]
print(f"\n=== 结果 {len(results) - len(bad)}/{len(results)} 通过 ===")
for label, _ok, detail in bad:
    print(f"  未通过：{label}  {detail}")
app.destroy()
