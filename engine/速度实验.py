"""速度实验：同一段距离，改拖动速度，看还会不会甩出一整屏。

背景
----
日志里"一步跨掉整屏"（两屏零重合）的落点每次都一模一样，而且正好是 5 行 =
ROI 里能看到的行数。说明那不是距离算错，是松手瞬间的指针速度被游戏当成甩动，
惯性把列表滑满一屏。要验证就一件事：**把拖动放慢，跳步会不会消失**。

三档速度对比：
    fast  每步 3px / 12ms ≈ 250px/s（现在程序用的）
    mid   每步 3px / 25ms ≈ 120px/s
    slow  每步 2px / 30ms ≈  67px/s

用法
----
    .venv\\Scripts\\python 速度实验.py 104 3px 12 12     # 距离 每步 间隔(ms) 次数
"""

import sys
import time

import alliance_ocr as T

DIST = int(sys.argv[1]) if len(sys.argv) > 1 else 104
STEP_PX = int((sys.argv[2] or "3px").replace("px", "")) if len(sys.argv) > 2 else 3
GAP_MS = int(sys.argv[3]) if len(sys.argv) > 3 else 12
REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 10

T._fix_streams()
T.dpi_aware()
T.install_crash_hooks()
T.log_start()

win, _c = T.find_game_window()
if win is None:
    print("没找到游戏窗口"); sys.exit(1)
info = T.content_rect(win.hwnd)
preset = T.load_preset()
l, t, r, b = preset["region_rel"]
region = (info.x + round(l * info.w), info.y + round(t * info.h),
          info.x + round(r * info.w), info.y + round(b * info.h))
pitch0 = (preset["pitch_rel"] * info.h) or 71.0
T.focus_window(win.hwnd)
time.sleep(0.5)

u = T.ctypes.windll.user32


def slow_drag(dist, step_px, gap_ms):
    """自己写一份拖动，只改速度，其余和 drag_at 完全一致（含松手前压速度）。"""
    x = (region[0] + region[2]) // 2
    y1 = region[3]
    y_from = y1 - max(24, (region[3] - region[1]) // 8)
    y_to = max(region[1] + 12, y_from - dist)
    u.SetCursorPos(x, y_from)
    time.sleep(0.05)
    u.mouse_event(T.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.06)
    n = max(4, abs(y_from - y_to) // max(1, step_px))
    for i in range(1, n + 1):
        u.SetCursorPos(x, int(round(y_from + (y_to - y_from) * i / n)))
        time.sleep(gap_ms / 1000.0)
    # 终点原地抖 ±1px 停住，让游戏采样到的速度归零后再松手
    for k in range(4):
        u.SetCursorPos(x, y_to + (1 if k % 2 else 0))
        time.sleep(0.05)
    u.SetCursorPos(x, y_to)
    time.sleep(0.02)
    u.mouse_event(T.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def snap():
    img = T.grab(region)
    rows, _b, _p = T.scan(img)
    return img, [n for _y, n, _s in rows]


print(f"距离 {DIST}px · 每步 {STEP_PX}px / {GAP_MS}ms "
      f"≈ {STEP_PX / (GAP_MS / 1000):.0f}px/s · 跑 {REPS} 次")

# 回顶（用现成实现，速度不是本次变量）
prev = None
still = 0
for i in range(1, 41):
    T.scroll_once("drag", region, 1, int(round(pitch0 * 2 + 15)), up=True)
    T.park_cursor(region, info)
    time.sleep(0.25)
    _i, names = snap()
    if prev is not None:
        _r, _o, v, _n = T.judge_roll(names, prev)
        if v == "stuck":
            still += 1
            if still >= 2:
                print(f"回顶 {i} 次到顶 · 首屏 {names}")
                break
            prev = names
            continue
        still = 0
    prev = names

prev_img, prev_names = snap()
print(f"起步首屏 {prev_names}\n")

rows_hist = []
jumps = 0
for k in range(1, REPS + 1):
    slow_drag(DIST, STEP_PX, GAP_MS)
    T.park_cursor(region, info)
    T.wait_settled(region, None)
    img, names = snap()
    rows, ov, verdict, _n = T.judge_roll(prev_names, names)
    sh, corr = T.measure_vshift(prev_img, img)
    rows_hist.append(rows)
    if verdict == "jump":
        jumps += 1
    print(f"  #{k:>2} 行数 {str(rows):>4} 重合 {ov} {verdict:<5} "
          f"位移 {str(sh):>3}px r={corr:.2f} · {names}")
    if verdict == "stuck":
        print("  → 到底了")
        break
    prev_img, prev_names = img, names

print(f"\n结果：{len(rows_hist)} 步 · 行数分布 "
      f"{ {r: rows_hist.count(r) for r in set(rows_hist)} } · 跳步（零重合）{jumps} 次")
