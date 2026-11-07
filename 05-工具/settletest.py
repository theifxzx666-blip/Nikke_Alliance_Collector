# -*- coding: utf-8 -*-
"""验证「等画面停稳」和「把光标挪出采集区」这两件事。

要证的两条：
  1) 列表松手后还会带惯性滑一段，滑动期间截图拿到的是中间态 ——
     旧的固定延时（松手后 150ms）恰好落在这段时间里，截到的行是花的。
     现在的做法是轮询到「连续两帧几乎一样」才截，必须证明它拿到的是终态。
  2) 光标停在名单上会遮字 + 触发悬停高亮，必须被挪到采集区外。

    .venv\\Scripts\\python.exe settletest.py
"""
import importlib.util
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
tool.LOG_PATH = tool.OUT_DIR / "_test_操作日志.txt"

ok_all = []


def check(name, cond, extra=""):
    ok_all.append(bool(cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {extra}" if extra else ""))


# ---------------------------------------------------------------- 假窗口
# 一块 300×200 的纯黑区域，里面一条 40px 高的白条按 offset 上下移动。
# 白条每动 8px，300×200 区域的平均灰度差约 20 —— 远高于 SETTLE_EPS(0.15)，
# 静止时则是 0.000。这一对数量级正负例都够干净。
WIN_X, WIN_Y, WIN_W, WIN_H = 300, 40, 340, 240
BAR_X, BAR_W, BAR_H, AREA_H = 20, 300, 40, 200

root = tk.Tk()
root.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.configure(bg="black")
cv = tk.Canvas(root, bg="black", highlightthickness=0, width=WIN_W, height=WIN_H)
cv.pack()

BAR_ID = None


def draw(px=0):
    """把白条画在 AREA_H 范围内的 px 偏移处。"""
    global BAR_ID
    if BAR_ID is None:
        BAR_ID = cv.create_rectangle(BAR_X, BAR_H + px, BAR_X + BAR_W,
                                     BAR_H + px + BAR_H, fill="white", width=0)
    else:
        cv.coords(BAR_ID, BAR_X, BAR_H + px, BAR_X + BAR_W, BAR_H + px + BAR_H)


root.update()
time.sleep(0.35)
root.update()
REGION = (WIN_X + BAR_X, WIN_Y, WIN_X + BAR_X + BAR_W, WIN_Y + AREA_H)

# 模拟背景动效的小方块：放在采集区底部，避开白条的所有停靠位置
# （白条 px 最大 160 时占 y=200..240，已在采集区外；px<120 时占 y=120..160，
#   所以方块放在 y=168..186 一定不会被白条盖住）
NOISE_ID = cv.create_rectangle(BAR_X + 250, 168, BAR_X + 268, 186,
                               fill="black", width=0)


def fresh(px=0):
    draw(px)
    cv.itemconfig(NOISE_ID, fill="black")   # 参照帧一律在"无闪动"状态拍
    root.update()
    time.sleep(0.12)
    root.update()


def frame_of(px):
    """让画面停在这个 offset，截一帧当参照，然后返回。"""
    fresh(px)
    return tool.grab(REGION)


print(f"假窗口 {WIN_W}x{WIN_H}@{WIN_X},{WIN_Y} · 采集区 {REGION[2]-REGION[0]}×{REGION[3]-REGION[1]}")


# ---------------------------------------------------------------- 1 停稳判定
def run(anim_ms, step_ms, px_step, timeout=None, min_wait=None, poll=None,
        noise=False, with_before=True, loop=False):
    """主线程推动画、工作线程跑 wait_settled。

    Tk 只有主线程能 update，所以动画留在主线程按时间节奏推进；
    工作线程只做 mss 截图，不碰 Tk。

    动画走到 anim_ms 就**停在最后的位置**（不是弹回原位）——这才是惯性的
    真实形态：滑一段然后静止在终点。返回时给出终点 offset，供比对用。

    noise=True 时，惯性走完后画面仍有一小块在持续闪动：模拟真实游戏背景的
    循环动效。实测日志里正是这个让"帧间差≈0"的判据永远不成立，每一步都等到
    2.8s 超时。它必须能靠"总差不再变化"停下来。
    """
    fresh(0)
    steps = max(0, int(anim_ms / step_ms))
    end_px = min(steps * px_step, AREA_H - BAR_H)
    res = {}

    def work():
        # 线上真实调用会传"滚动前那一帧"，总差判据才有参照。
        # 用例 [2] 另外验证不传 before 时帧间差判据仍然有效。
        before = tool.grab(REGION) if with_before else None
        kw = {}
        if timeout is not None:
            kw["timeout"] = timeout
        if min_wait is not None:
            kw["min_wait"] = min_wait
        if poll is not None:
            kw["poll"] = poll
        res["r"] = tool.wait_settled(REGION, before, **kw)

    th = threading.Thread(target=work, daemon=True)
    t0 = time.time()
    th.start()
    last = -1
    flip = 0
    while th.is_alive():
        el = (time.time() - t0) * 1000
        done = steps == 0 or el >= anim_ms
        n = min(int(el / step_ms), steps) if steps > 0 else 0
        if n != last:
            last = n
            # loop=True：滚到底就回绕重来，画面永远在动（"背景动效一直不停"）
            px = (n * px_step) % (AREA_H - BAR_H) if loop \
                else min(n * px_step, AREA_H - BAR_H)
            draw(px)
            root.update()
        elif noise and done:
            # 惯性已走完，但背景动效还在：18×18 的小块黑白交替翻。
            # 300×200 区域上 324 像素变 255 灰 ≈ 帧间差 1.4，正好是实测那个量级。
            flip ^= 1
            cv.itemconfig(NOISE_ID, fill="white" if flip else "black")
            root.update()
        time.sleep(0.008)
    th.join()
    img, used, settled = res["r"]
    return img, used, settled, end_px


print("\n[1] 惯性滑 400ms 后停在终点：必须停在终点再截")
# 惯性段：每 50ms 挪 8px，持续 400ms（8 步 = 64px），之后不动
img, used, settled, final_px = run(anim_ms=400, step_ms=50, px_step=8, timeout=3.0)
expect = frame_of(final_px)
mid = frame_of(final_px // 2)          # 惯性中途的位置，旧版固定延时大概就截在这里
d_final = tool.frame_diff(img, expect)
d_mid = tool.frame_diff(img, mid)
check("判定为已停稳", settled, f"等 {int(used*1000)}ms")
check("截到的就是终点（差 <0.5）", d_final < 0.5, f"与终点差 {d_final:.3f}")
check("不是惯性中途的中间态", d_mid > 3.0 and d_final < d_mid,
      f"与中途差 {d_mid:.1f} vs 与终点差 {d_final:.2f}")
check("等到惯性走完才截（松手后 >=400ms）",
      used * 1000 + tool.SETTLE_MIN >= 400, f"{int(used*1000)}+{tool.SETTLE_MIN}ms")

print("\n[2] 画面本来就静止：不该白等")
img, used, settled, _ = run(anim_ms=0, step_ms=50, px_step=8, timeout=3.0,
                            with_before=False)
check("判定为已停稳", settled)
check("用时接近下限（<900ms）", used < 0.9, f"{int(used*1000)}ms")
check("没有因为静止就返回空图", img is not None and img.size == (REGION[2] - REGION[0],
                                                               REGION[3] - REGION[1]),
      str(None if img is None else img.size))

print("\n[3] 画面一直在动（背景动效不停）：超时兜底，不能卡死")
# loop=True：白条滚出采集区就回绕重来，所以"永远在动"这件事是真的。
# 旧版的假窗口滑到 640ms 就撞底停住了，于是"未停稳"这条断言其实测的是别的东西。
img, used, settled, total = run(anim_ms=100000, step_ms=40, px_step=10,
                                timeout=0.7, min_wait=0.1, loop=True)
check("报告未停稳", not settled)
check("按超时返回（0.6~1.8s）", 0.6 <= used <= 1.8, f"{int(used*1000)}ms")
check("仍然返回了一帧", img is not None)

print("\n[3b] 惯性走完但背景还在闪（真实游戏的样子）：不能被底噪拖到超时")
# 这是线上实测的那个场景：背景噪点让帧间差到不了 0，旧阈值 0.15 每步都等满超时。
# 现在阈值 5.0 高于噪点（约 1.4）、低于滚动信号（40+），所以惯性一停就能收工。
n_img, n_used, n_settled, n_final = run(anim_ms=400, step_ms=50, px_step=8,
                                        timeout=2.5, noise=True)
check("判定为已停稳（不再是超时）", n_settled, f"等 {int(n_used*1000)}ms")
check("明显早于超时（<1.5s）", n_used < 1.5, f"{int(n_used*1000)}ms")
# 停稳时刻必须晚于惯性结束：惯性没走完就截，等于截了个中间态
check("没有在惯性走完之前就交差",
      n_used * 1000 + tool.SETTLE_MIN >= 400, f"{int(n_used*1000)}+{tool.SETTLE_MIN}ms")
n_ref = frame_of(n_final)
d_body = tool.frame_diff(n_img, n_ref)
check("白条位置就是终点（闪动块不计，差 <3）", d_body < 3.0, f"与终点差 {d_body:.2f}")

# ---------------------------------------------------------------- 2 光标挪出采集区
print("\n[4] park_point 选点：永远不落在采集区里")
FRAME = (0, 0, 720, 405)


def park_ok(region, frame, label):
    px, py = tool.park_point(region, frame)
    hit = tool.in_rect(region, px, py, tool.CURSOR_PARK_PAD)
    inside = frame[0] <= px <= frame[0] + frame[2] and frame[1] <= py <= frame[1] + frame[3]
    check(label, not hit, f"→ ({px},{py}) · 在游戏画面内={inside}")
    return px, py


park_ok((40, 60, 260, 340), FRAME, "窄竖条采集区 → 选到旁边")
park_ok((0, 0, 300, 200), FRAME, "采集区贴左上角 → 避开它")
park_ok((600, 300, 719, 404), FRAME, "采集区贴右下角 → 避开它")
park_ok((0, 0, 719, 404), FRAME, "采集区几乎盖满窗口 → 退回屏幕角")
park_ok((10, 10, 700, 395), FRAME, "采集区占满中部 → 仍有落点")

print("\n[5] park_cursor 真挪鼠标，且挪完真的不在区内")
saved = tool.cursor_pos()
try:
    region5 = (WIN_X + BAR_X, WIN_Y, WIN_X + BAR_X + BAR_W, WIN_Y + AREA_H)
    cx, cy = region5[0] + 50, region5[1] + 50
    # 这一项要真的动鼠标，所以最容易被别的东西干扰：真人挪一下鼠标、
    # 或者前台程序抢了指针，set_cursor 立刻就被覆盖。检测到被覆盖就重试，
    # 重试还不行就 SKIP —— 判它 FAIL 只会让人以为代码坏了。
    ok_set = False
    for _ in range(3):
        tool.set_cursor(cx, cy)
        time.sleep(0.1)
        back = tool.cursor_pos()
        if max(abs(back[0] - cx), abs(back[1] - cy)) <= 4:
            ok_set = True
            break
    if not ok_set:
        print(f"  SKIP  光标被外部占用（想放到 {cx},{cy}，实际 {tool.cursor_pos()}）"
              f"—— 别动鼠标再跑一次")
    else:
        moved = tool.park_cursor(region5, (WIN_X, WIN_Y, WIN_W, WIN_H))
        now = tool.cursor_pos()
        check("光标被挪走了", moved is not None, str(moved))
        check("挪完不在采集区内", not tool.in_rect(region5, now[0], now[1],
                                                   tool.CURSOR_PARK_PAD),
              f"现在 ({now[0]},{now[1]})")
        # 已经在区外时不该乱动
        again = tool.park_cursor(region5, (WIN_X, WIN_Y, WIN_W, WIN_H))
        check("已在区外就不动它", again is None)
finally:
    tool.set_cursor(*saved)

root.destroy()

print("\n=== 结果 ===")
n, ok = len(ok_all), sum(ok_all)
print(f"{ok}/{n} 通过")
sys.exit(0 if ok == n else 1)
