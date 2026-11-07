# -*- coding: utf-8 -*-
"""自动滚采状态机的回放测试（不上游戏、不动鼠标）。

    .venv\\Scripts\\python.exe looptest.py

把两次真实运行的**分屏序列**重放给 App._apply()，验证跟"名单人数对不对"
直接相关的几件事：

  场景 A（诊断包 20261027_180307）
    1. 滚到底以后正常收工，而不是把"最后一屏"误当成"漏人了"叫停
    2. 名单人数与游戏内一致——含曾经被整行丢掉的「马三一」

  场景 B（诊断包 20261027_184352）
    3. 同一行在相邻两屏被读成两种拼写时（非洲晒太阳/非洲吧太阳、命OVO/命UVU），
       不能当成两个人，名单不能凭空多出人来

分屏序列按"每屏 5 行、每次走 2 行、到底后夹在末尾 5 行"生成，跟真实运行日志里
观察到的推进方式一致（逐像素复刻在 bandtest 里）。
"""
import importlib.util
import shutil
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ok_all = True
n_pass = n_all = 0


def check(title, good, detail=""):
    global ok_all, n_pass, n_all
    n_all += 1
    n_pass += 1 if good else 0
    ok_all = ok_all and good
    print(f"{'✓' if good else '✗'} {title}" + (f"  {detail}" if detail else ""))


# 游戏里的真实成员顺序（从诊断包的名单反推，把被漏掉的「马三一」补回它实际
# 所在的位置：红莲的少号横幅之后、阿兔之前；「英白」「非洲吧太阳」「命UVU」
# 都是同一行认错的另一种拼写，不算独立成员）
ROSTER = [
    "DORO", "曼殊颜华", "新来的", "非洲晒太阳", "PPORANGE",
    "村里响当当", "大魔王", "HGOLIVER", "NING", "AESTHTIC",
    "博麗霊夢", "薯KI", "知古", "小卡卡", "朧月夜",
    "HANSON", "空木", "命OVO", "班长", "点墨为霜",
    "巧乐兹", "凛酱", "青椒椒", "蓝斯洛特", "萧白",
    "星空下的神话", "小楼听风雨", "霞雨瑶", "怎么还取名", "九月八",
    "马三一", "阿兔",
]
CAP = 5          # 每屏能看到几行（区域 334px ÷ 行距 71px ≈ 4.7，取 5）
STEP = 2         # 每次滚动走 2 行


def screens(misread=None):
    """生成分屏序列。misread = (第几屏, 把谁, 读成什么)。

    到底后要留**三屏**一模一样的画面，不是两屏：
      第 1 屏  正常滚到底
      第 2 屏  再滚，名字一个没变 → 程序先当成"可能卡在两行中间"，放大步长重试
      第 3 屏  重试还是不动 → 这才判定到列表末尾
    少留一屏，重试就会把序列走空，测试会误报"没有停"。
    """
    seq, i = [], 0
    while True:
        if i + CAP > len(ROSTER):
            i = len(ROSTER) - CAP          # 夹到底
        seq.append(ROSTER[i:i + CAP])
        if i >= len(ROSTER) - CAP:
            seq.append(ROSTER[i:i + CAP])
            seq.append(ROSTER[i:i + CAP])  # 重试那一步
            break
        i += STEP
    if misread:
        idx, src, dst = misread
        seq[idx] = [dst if n == src else n for n in seq[idx]]
    return seq


tmp = Path(tempfile.mkdtemp(prefix="looptest_"))
app = None
stops = []
try:
    # 隔离配置与日志，别动用户正在用的 输出/区域.json 和 操作日志.txt
    tool.CFG = tmp / "区域.json"
    tool.LOG_PATH = tmp / "log.txt"
    tool.PRESET = tmp / "预设.json"
    tool.ROSTER = tmp / "昵称库.json"

    app = tool.App()
    app.withdraw()

    dummy = Image.new("RGB", (103, 334), (40, 40, 40))

    def reset():
        app.names.clear()
        app._prev_ordered = []
        app._prev_names = set()
        app._roll_count = 1
        app._empty_rounds = 0
        app._jumps = 0
        app._mode_ok = set()
        app.scroll_mode = "drag"
        app._pitch = 71.0
        app._fling = 1.05
        app._last_dist = 136
        app._continuous = True
        stops.clear()

    def play(seq):
        for k, names in enumerate(seq):
            found = [(n_i * 71 + 30, n, 0.99) for n_i, n in enumerate(names)]
            bands = [(y - 5, y + 5) for y, _n, _s in found]
            app._apply(dummy, found, bands, len(found), True)
            if stops:
                return k + 1
        return len(seq)

    app._do_scroll = lambda: None
    app._persist_mode = lambda: None
    app._stop_auto = lambda msg, complete=False: stops.append(msg)

    # ---------------------------------------------------------------- 场景 A
    print("=== 场景 A：正常跑完（含被整行丢掉的马三一）===")
    reset()
    play(screens())
    names_got = list(app.names)
    print(f"已采 {len(names_got)} 人 · 停止理由 {stops[0] if stops else '（没有停）'}")
    check("滚到底后正常收工（不是被判成漏人）",
          bool(stops) and "已到列表末尾" in stops[0], stops[0][:50] if stops else "没停")
    check("名单人数与游戏内一致", len(names_got) == len(ROSTER), f"{len(names_got)} vs {len(ROSTER)}")
    check("「马三一」在名单里（漏行事故的主角）", "马三一" in names_got)
    check("最后一位是「阿兔」，没有停在中间", bool(names_got) and names_got[-1] == "阿兔")

    # ---------------------------------------------------------------- 场景 B
    print("\n=== 场景 B：同一行读成两种拼写（多人的事故）===")
    for title, mis, ghost in [
        ("非洲晒太阳 → 非洲吧太阳（第 3 屏）", (3, "非洲晒太阳", "非洲吧太阳"), "非洲吧太阳"),
        ("命OVO → 命UVU（第 10 屏）", (10, "命OVO", "命UVU"), "命UVU"),
    ]:
        reset()
        play(screens(mis))
        got = list(app.names)
        check(f"{title}：不会多出「{ghost}」", ghost not in got)
        check(f"{title}：人数仍是 {len(ROSTER)}", len(got) == len(ROSTER), f"{len(got)} 人")
        check(f"{title}：正确拼写还在", mis[1] in got)

    # 负例：位移偏一行时，同位置对上的其实是两个不同的人，绝不能并
    print("\n=== 场景 B 负例：位移判偏，同位置是两个不同成员 ===")
    reset()
    app.names = {"小明": (1.0, ""), "小红": (1.0, "")}
    app._prev_ordered = ["小明", "路人甲"]
    merged = app._merge_misreads(["小红"], 1, 1, "ok")
    check("近名不会被误并（宁可多一个，不能少一个）",
          merged == 0 and len(app.names) == 2, f"merged={merged} · {list(app.names)}")

    # ---------------------------------------------------------------- 场景 C
    # 真实事故现场：第 11 步一步跨了 7 行，两屏零重合，中间 2 个人被跳过。
    # 旧逻辑直接中止整轮（实测三轮里两轮这么废掉）。现在应该往回走小步把
    # 那两个人捡回来，然后继续往前，最后照样采全。
    print("\n=== 场景 C：一步跳过整屏 → 往回补采，不许整轮作废 ===")
    reset()
    app.names.clear()
    app._recover_target = []
    app._recover_seen = 0
    app._recover_walks = 0

    def scr(i):
        i = max(0, min(i, len(ROSTER) - CAP))
        return ROSTER[i:i + CAP]

    # 第 20 行正常往前，下一步本该到 22，实际跳到了 27（漏掉 25、26 两个位置）
    seq = [scr(i) for i in range(0, 21, STEP)] + [scr(27)]
    # 补采：往回走 25 → 23。到 23 这一屏已经和跳步前的 scr(20) 重合 3 行，接上了
    seq += [scr(25), scr(23)]
    # 接上以后继续往前
    seq += [scr(25), scr(27), scr(27), scr(27)]
    play(seq)
    got = list(app.names)
    missed = [n for n in ROSTER if n not in got]
    check("跳步后没有整轮作废，最终正常收工",
          bool(stops) and "已到列表末尾" in stops[0],
          (stops[0][:46] if stops else "没停"))
    check("跳过去的 2 个人被补采捡回来了", len(got) == len(ROSTER),
          f"{len(got)}/{len(ROSTER)}" + (f" · 缺 {missed}" if missed else ""))
    check("补采次数被记下来了（便于事后核对）", app._recover_walks == 1,
          f"walks={app._recover_walks}")
finally:
    try:
        app.destroy()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n=== 结果 {n_pass}/{n_all} ===")
print("通过" if ok_all else "未通过")
raise SystemExit(0 if ok_all else 1)
