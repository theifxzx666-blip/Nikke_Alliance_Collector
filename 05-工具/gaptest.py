# -*- coding: utf-8 -*-
"""滚动判定与步长规划的回归测试（纯计算，不开界面、不碰游戏）。

    .venv\\Scripts\\python.exe gaptest.py

用例序列全部取自真实运行日志（诊断包 20261027_163156），所以这里过就等于
真实那次的判定逻辑是对的。
"""
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ROW = ["DORO", "曼殊颜华", "新来的", "非洲晒太阳", "PPORANGE",
       "村里响当当", "大魔王", "HGOLIVER", "NING", "薯KI",
       "知古", "小卡卡", "朧月夜", "HANSON", "夜刃"]

ok_all = True


def case(title, prev, cur, want_verdict, want_rows=None, note_kw=None):
    global ok_all
    rows, overlap, verdict, note = tool.judge_roll(prev, cur)
    good = verdict == want_verdict
    if want_rows is not None:
        good = good and rows == want_rows
    if note_kw:
        good = good and note_kw in note
    ok_all = ok_all and good
    print(f"{'✓' if good else '✗'} {title}")
    print(f"    结论 {verdict} · 走了 {rows} 行 · 重合 {overlap}")
    if note:
        print(f"    说明 {note}")


print("=== judge_roll：这一滚有没有漏人 ===")

# 真实日志第 1 次滚动：上屏 1-5 行，本屏 5-9 行（重合 PPORANGE 一行）
case("真实·正常小步（重合 1 行）", ROW[0:5], ROW[4:9], "ok", want_rows=4)

# 真实日志第 2 次滚动：上屏 5-9 行，本屏 10-14 行（刚好换了一整屏）
# 实际没漏人（1..14 连续），但两屏没有任何重合名字，数据上无法证明，
# 只能停下并把步长压小 —— 关键是文案不能断定"漏人了"。
case("真实·刚好换了一整屏", ROW[4:9], ROW[9:14], "jump", note_kw="分不出来")

# 上屏 5 行、本屏 4 行且全不重合。旧版据此断定"行数变少 ⇒ 确实跨过了中间的人"，
# 这个推论是错的，实测被它误叫停过两次（诊断包 174100 / 180307，都是列表到底那
# 一步）：本屏行数少还有另外两个来源——到底了那一屏本来就不满、个别行 OCR 没认
# 出来（「马三一」被横笔间隙切成碎片整行丢掉）。所以现在只如实说"量不到"。
case("本屏行数变少（不等于漏人）", ROW[0:5], ROW[8:12], "jump", note_kw="分不出来")

case("完全没动", ROW[0:5], ROW[0:5], "stuck", want_rows=0)

case("上一屏没数据", [], ROW[0:5], "none")

case("本屏没数据（弹出别的窗口）", ROW[0:5], [], "none")

# OCR 认错一行不该被当成漏行：上屏 3-7 行，本屏 5-9 行，第 3 行认错了
case("容忍单行认错", ROW[2:7], [ROW[4], ROW[5], "XXX", ROW[7], ROW[8]], "ok", want_rows=2)

# 一行不重合、本屏还比上屏短：同样只能量到"至少一屏"，不能断言漏人
case("上屏 6 行本屏 5 行全换", ROW[0:6], ROW[7:12], "jump", note_kw="分不出来")

# 真实那次漏人的现场：上屏 4 行，本屏本来该 4 行但只认出 3 行（「马三一」丢了），
# 于是 0 重合。这条用来钉住"不能因为 0 重合就判死刑"——真正的处置在 App 里：
# 连续 JUMP_TOLERANCE 步才对不上才停。
case("真实漏认（上屏 4 行/本屏 3 行）", ROW[0:4], ROW[4:7], "jump", note_kw="分不出来")


print("\n=== plan_drag：这一拖该拖多远 ===")
#
# 核心事实（本轮真实客户端实测）：拖动前十几像素会被游戏当成"区分点击和拖动"
# 的手势死区吃掉，不产生滚动。
#     拖 60px → 内容上移 42px（少 18）      拖 70px → 内容上移 57px（少 13）
# 所以 "拖多少像素就走多少像素" 是错的：步长只给一个行距时，真实位移只有 0.8 行，
# 相位会一路往后漂；漂到某一步正好跨不过行边界，名字就一个字不变——旧逻辑把它
# 当成"到列表末尾"，32 人的联盟只采到 6 人。步长必须补上这段死区。
#
# 期望值 = 行距 × TARGET_ROWS + 死区（受 STEP_CAP_ROWS 封顶）

SLOP = tool.DRAG_SLOP
PLANS = [
    (71, None, 1, 71 * tool.TARGET_ROWS + SLOP, "行距 71 · 死区用默认值"),
    (71, 0, 1, 71 * tool.TARGET_ROWS, "死区 0（不补偿）→ 少一个死区的距离"),
    (71, 20, 1, 71 * tool.TARGET_ROWS + 20, "死区 20 → 步长比默认多 5px"),
    (71, 3, 1, 71 * tool.TARGET_ROWS + 3, "死区很小也照加"),
    (71, None, 3, 71 * tool.STEP_CAP_ROWS + SLOP, "格数 3 被单步上限压住"),
    (40, None, 1, 40 * tool.TARGET_ROWS + SLOP, "行距 40"),
    (120, None, 1, 120 * tool.TARGET_ROWS + SLOP, "行距 120"),
    (20, None, 1, None, "行距 20 也是正常算，但不得低于 MIN_DRAG_PX"),
]
for pitch, slop, clicks, want, desc in PLANS:
    got = tool.plan_drag(pitch, slop, clicks)
    if want is None:
        good = got >= tool.MIN_DRAG_PX
    else:
        good = abs(got - want) <= 2
    ok_all = ok_all and good
    print(f"{'✓' if good else '✗'} {desc}：算出 {got}px（期望 ≈{want}）")


print("\n=== 死区模型自检：算出的步长扣掉死区，真实位移是否稳定落在 1 行以上 ===")
# 只要真实位移 > 1 个行距，每一步就必跨过至少一行边界，绝不会出现
# "名字不变 → 误判到底"。实测误差 ±5px，这里按最坏情况验证。
for pitch, true_slop in [(71, 13), (71, 18), (71, 25), (110, 15), (55, 12)]:
    dist = tool.plan_drag(pitch, SLOP, 1)
    lo = (dist - (true_slop + 5)) / pitch
    hi = (dist - (true_slop - 5)) / pitch
    good = lo >= 1.0 and hi <= 5.0        # 至少 1 行、远不到一屏
    ok_all = ok_all and good
    print(f"{'✓' if good else '✗'} 行距 {pitch} 真实死区 {true_slop} → 拖 {dist}px "
          f"→ 真实位移 {lo:.2f}~{hi:.2f} 行（要求 ≥1 行且 ≪ 一屏）")

# 旧行为对照：步长只给一个行距、又不补死区，真实位移会掉到 1 行以下——
# 这就是"名字不变 → 误判到底 → 只采到 6 人"的根源。这里直接按实测数字算，
# 不走 plan_drag（那条路已经是新公式了）。
old_rows = (71 - 15) / 71
good = old_rows < 1.0
ok_all = ok_all and good
print(f"{'✓' if good else '✗'} 对照·旧步长 71px 且不补死区 → 真实只走 {old_rows:.2f} 行 "
      f"（一步跨不过一行，相位漂到那儿就误判到底）")

print("\n=== 结果 ===")
print("通过" if ok_all else "未通过")
raise SystemExit(0 if ok_all else 1)
