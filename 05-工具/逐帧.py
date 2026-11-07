"""逐帧探针：把每一步 scan 的原始输出（y / 名字 / 得分）和 ROI 原图都留下来。

为什么要它
----------
拖动标定里出现了自相矛盾的数据：像素互相关说内容只上移了 50px（不到一行），
可 5 行名字却一个都不重合。这两件事不可能同时为真，所以不能再拿 judge_roll
的结论推，得把 scan 每一步的原始输出摊开看：
  · 是 scan 漏行 / 拆行 / 排错序？
  · 还是列表真的在换内容（自动刷新重排）？
  · 还是 ROI 根本框错了列？

每帧存一张 ROI 图，看图和看数字能对上，就能定性。

用法
----
    .venv\\Scripts\\python 逐帧.py [距离] [次数]
"""

import json
import sys
import time

import alliance_ocr as T

DIST = int(sys.argv[1]) if len(sys.argv) > 1 else 70
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 6

T._fix_streams()
T.dpi_aware()
T.install_crash_hooks()
T.log_start()

OUT = T.OUT_DIR / f"逐帧_{time.strftime('%H%M%S')}"
OUT.mkdir(parents=True, exist_ok=True)

win, _c = T.find_game_window()
if win is None:
    print("没找到游戏窗口"); sys.exit(1)
info = T.content_rect(win.hwnd)
preset = T.load_preset()
l, t, r, b = preset["region_rel"]
region = (info.x + round(l * info.w), info.y + round(t * info.h),
          info.x + round(r * info.w), info.y + round(b * info.h))
pitch0 = (preset["pitch_rel"] * info.h) or 70.0
T.focus_window(win.hwnd)
time.sleep(0.5)

print(f"窗口 {info.w}×{info.h} @({info.x},{info.y}) · ROI {region} "
      f"(高 {region[3]-region[1]}) · 行距 {pitch0:.1f}")
print(f"ROI 能装 {((region[3]-region[1]) / pitch0):.2f} 行\n")


def snap():
    img = T.grab(region)
    rows, band, p = T.scan(img)
    return img, rows, p


def dump(tag, img, rows, p, shift=None):
    path = OUT / f"{tag}.png"
    img.save(path)
    print(f"--- {tag} · ROI {img.width}×{img.height} · scan 行距 {p} ---")
    for y, name, score in rows:
        print(f"    y={y:>3}  {score:.2f}  {name}")
    if shift is not None:
        print(f"    像素位移（相对上一帧）{shift[0]}px  相关 {shift[1]:.2f}")


# 回顶：这一步用像素位移判断，不依赖名字序列
print("=== 回顶（判据：连续两次像素位移≈0）===")
prev = T.grab(region)
zero = 0
for i in range(1, 41):
    T.scroll_once("drag", region, 1, int(round(pitch0)), up=True)
    T.park_cursor(region, info)
    time.sleep(0.25)
    cur = T.grab(region)
    sh, corr = T.measure_vshift(prev, cur)
    print(f"  回顶 #{i:>2} 位移 {sh:>3}px r={corr:.2f}")
    if sh is None or sh <= 3:
        zero += 1
        if zero >= 2:
            print(f"  → 到顶（{i} 次）")
            break
    else:
        zero = 0
    prev = cur

print("\n=== 开跑：逐帧原始输出 ===")
img, rows, p = snap()
dump("00_顶部", img, rows, p)
prev_img = img
for k in range(1, REPS + 1):
    T.scroll_once("drag", region, 1, DIST)
    T.park_cursor(region, info)
    cur, _t, ok = T.wait_settled(region, None)
    img, rows, p = snap()
    sh, corr = T.measure_vshift(prev_img, img)
    dump(f"{k:02d}_拖{DIST}px", img, rows, p, (sh, corr))
    prev_img = img

(OUT / "明细.json").write_text(json.dumps(
    {"region": list(region), "pitch": pitch0, "dist": DIST}, ensure_ascii=False),
    encoding="utf-8")
print(f"\n图都在 {OUT}")
