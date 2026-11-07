"""拖动标定：在真实客户端上量「拖 N 像素 = 列表走几行」，并且带像素位移做交叉验证。

为什么要单独一个工具
--------------------
日志里出现了互相矛盾的现象：同一份代码，同样拖 70px，一次走 1 行，下一次直接
走 5 行（两屏零重合）。步长明明是常量、每步 3px 速度也是常量，那就说明
「距离→行数」并不是稳定的单值，靠推理是量不出来的，只能上机打表：

    对每个距离连续拖 N 次，每次记三样：名字序列量出的行数、像素互相关量出的
    位移、两者是否一致。

名字序列是"真值"（人到底走过去几行），像素位移是"补充"（哪怕零重合也能看出
内容其实移动了 350px 还是 70px）。两个一起看，才能分清：
  · 像素位移 ≈ 70、行数 = 1      → 正常
  · 像素位移 ≈ 350、行数 = 5     → 松手后真的甩出去了（惯性）
  · 像素位移 ≈ 70、行数 = 5      → 不是甩动，是行距估错了或列表自己吸附
  · 像素位移 ≈ 0、行数 = 0       → 输入根本没生效

会真的操作游戏窗口。跑的时候不要动鼠标。

用法
----
    .venv\\Scripts\\python 拖动标定.py 60,70,80 5      # 三个距离各 5 次
    .venv\\Scripts\\python 拖动标定.py 70 10          # 只量 70px，10 次
"""

import json
import sys
import time

import alliance_ocr as T

DISTS = [int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else "70").split(",")]
REPS = int(sys.argv[2]) if len(sys.argv) > 2 else 5

# 回顶用小步、多给几次机会：回顶本身这次也在被观察的对象里，
# 一旦它把列表推到奇怪的态（比如停在顶部下拉），后面的标定就全废了
TOP_MAX = 45


def say(msg):
    print(f"[标定] {msg}", flush=True)


T._fix_streams()
T.dpi_aware()
T.install_crash_hooks()
T.log_start()

win, cands = T.find_game_window()
if win is None:
    say(f"没找到游戏窗口，候选 {len(cands)} 个：{[c.title for c in cands][:5]}")
    sys.exit(1)

info = T.content_rect(win.hwnd)
if info is None:
    say("拿到窗口但取不到有效画面（可能已最小化）")
    sys.exit(1)
say(f"窗口「{win.title}」有效画面 {info.w}×{info.h} @({info.x},{info.y}) "
    f"比例 {info.aspect:.3f}")

preset = T.load_preset()
if not preset:
    say("没有预设（预设/预设.json），先在主程序里标定一次昵称列")
    sys.exit(1)
l, t, r, b = preset["region_rel"]
region = (info.x + round(l * info.w), info.y + round(t * info.h),
          info.x + round(r * info.w), info.y + round(b * info.h))
pitch0 = (preset["pitch_rel"] * info.h) or 70.0
say(f"采集区 {region} · 预设行距 {pitch0:.1f}px · 预设每屏 {preset['rows']} 行")

if not T.focus_window(win.hwnd):
    say("⚠ 抢前台失败，后面的拖动可能全都不生效")
time.sleep(0.6)


def snap():
    img = T.grab(region)
    rows, _band, _p = T.scan(img)
    return img, [n for _y, n, _s in rows]


def stage0():
    """先确认此刻到底是什么画面——之前几次诊断都是因为没确认就下结论。"""
    say("—— 阶段 0：全屏识图（不过滤，看看到底在哪一页）——")
    full = T.grab(None)
    shot = T.OUT_DIR / "标定_全屏.png"
    full.save(shot)
    blocks = T.read_screen(full, min_score=0.3, rel=True)
    say(f"全屏路径 {shot}")
    for txt, box, score in sorted(blocks, key=lambda x: (x[1][1], x[1][0])):
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        say(f"   ({cx:.2f},{cy:.2f}) {score:.2f}  {txt}")
    say("—— 阶段 0 结束 ——")


def back_to_top():
    """反向拖到顶。判据只看名字，不看画面差（到顶后回弹动画一直在动）。"""
    say("—— 阶段 1：反向拖回顶部 ——")
    prev = None
    still = 0
    moved = 0
    for i in range(1, TOP_MAX + 1):
        T.scroll_once("drag", region, 1, int(round(pitch0)), up=True)
        T.park_cursor(region, info)
        time.sleep(0.25)
        _img, names = snap()
        if prev is not None:
            rows, ov, verdict, _n = T.judge_roll(names, prev)
            if verdict == "stuck":
                still += 1
                say(f"  #{i:>2} 名字没变（第 {still} 次）· 当前首行 {names[:1]}")
                if still >= 2:
                    say(f"  连续 2 次没变 → 认为已到顶 · 共回滚 {moved} 次")
                    return True, moved, names
                prev = names
                continue
            still = 0
            moved += 1
            say(f"  #{i:>2} 走了 {rows} 行 · 首行 {names[:1]}")
        else:
            say(f"  #{i:>2} 首屏 {names}")
        prev = names
    say(f"  ⚠ 回滚 {TOP_MAX} 次仍没判出到顶 · 首行 {prev[:1]}")
    return False, moved, prev


def measure(dists, reps):
    say("—— 阶段 2：同一距离连拖，量每次真实行数 ——")
    data = []
    prev_img, prev_names = snap()      # 基线：回顶后这一屏
    for dist in dists:
        for k in range(reps):
            T.scroll_once("drag", region, 1, dist)
            T.park_cursor(region, info)
            img, _t, stilled = T.wait_settled(region, None)
            img, names = snap()
            # 顺序必须是 (上一屏, 这一屏)。写反了永远对不上，会报出"走 5 行、
            # 零重合"这种自相矛盾的数据——第一版就是这么被骗过去的。
            rows, ov, verdict, note = T.judge_roll(prev_names, names)
            shift, corr = (0, 0.0)
            if prev_img is not None and prev_img.size == img.size:
                shift, corr = T.measure_vshift(prev_img, img)
            rec = {"dist": dist, "k": k + 1, "rows": rows, "overlap": ov,
                   "verdict": verdict, "shift": shift, "corr": round(corr, 2),
                   "stilled": stilled, "head": names[:1], "n": len(names)}
            data.append(rec)
            say(f"  拖 {dist:>3}px #{k+1} · 行数 {str(rows):>4} · 重合 {ov} · "
                f"{verdict:<5} · 像素位移 {str(shift):>3}(r={corr:.2f}) · "
                f"首行 {names[:1]}")
            if verdict == "stuck":
                say("  ↑ 已经拖不动（到底，或输入失效）· 本距离的后续次数跳过，换下一档")
                break
            prev_img, prev_names = img, names
        if data and data[-1]["verdict"] == "stuck":
            # 到底了就别继续往下测了，后面的距离会全是 stuck
            break
    return data


def report(data):
    say("—— 汇总：每个距离的位移分布 ——")
    from collections import Counter
    by = {}
    for d in data:
        by.setdefault(d["dist"], []).append(d)
    for dist, rows in by.items():
        cnt = Counter([str(x["rows"]) for x in rows])
        shifts = [x["shift"] for x in rows if x["shift"]]
        avg = sum(shifts) / len(shifts) if shifts else 0
        say(f"  拖 {dist:>3}px × {len(rows)} 次 → 行数分布 {dict(cnt)} · "
            f"像素位移 {shifts} · 均值 {avg:.0f}px")
        if avg:
            say(f"        ⇒ 起手死区 ≈ {dist - avg:.0f}px（拖动距离减掉它才是真实位移）")
    # 用两档以上就能拟合「位移 = 距离 - slop」，给出推荐步长
    pts = [(d, sum(x["shift"] for x in rows if x["shift"]) /
            max(1, len([x for x in rows if x["shift"]]))) for d, rows in by.items()]
    pts = [(d, a) for d, a in pts if a]
    if len(pts) >= 1:
        slops = [d - a for d, a in pts]
        slop = sum(slops) / len(slops)
        say(f"  拟合：内容位移 = 拖动距离 - {slop:.0f}px")
        say(f"  要走满 1 个行距（{pitch0:.0f}px）应拖 {pitch0 + slop:.0f}px")
    out = T.OUT_DIR / f"拖动标定_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps({"region": list(region), "pitch": pitch0,
                               "frame": [info.w, info.h], "data": data},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"明细已存 {out}")


if __name__ == "__main__":
    stage0()
    ok, n_top, head = back_to_top()
    if not ok:
        say("⚠ 回顶没成功，仍然继续量——但这一轮的行数可能从中间开始")
    data = measure(DISTS, REPS)
    report(data)
