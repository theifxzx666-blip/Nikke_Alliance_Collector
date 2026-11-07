# -*- coding: utf-8 -*-
"""联盟突袭伤害记录 · 解析测试（L2：会加载 OCR 模型，但不弹窗、不碰光标）

跑法：
    .venv\\Scripts\\python.exe raidtest.py          # 全跑
    .venv\\Scripts\\python.exe raidtest.py core     # 只跑纯逻辑（不起模型，1 秒）

覆盖：
    T1 真实截图 → 6 条记录逐字段比对（成员/BOSS/等级/伤害/占比）
    T2 伤害数字格式校验（含实测出现过的 `2,158,17,750`）
    T3 locate 边界：日标记、单行、缺 BOSS、LEVEL 无空格
    T4 splice：正常位移 / 没动 / 跳屏 / 重叠对不上 / 无位移可量
    T5 汇总与 CSV 落盘
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import raid_core as rc

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "测试素材" / "raid" / "记录_第1天_6条.jpg"

_fail = []


def ok(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        _fail.append(name)


def eq(name, got, want):
    ok(name, got == want, f"got={got!r} want={want!r}")


# ---------------------------------------------------------------- T2 纯逻辑
def test_damage():
    print("[T2] 伤害数字格式")
    eq("标准千分位", rc.normalize_damage("2,158,171,750")[0], 2158171750)
    eq("短数字不当伤害（等级/序号）", rc.normalize_damage("1,234")[0], None)
    eq("无逗号长数字", rc.normalize_damage("2158171750")[0], 2158171750)
    eq("实测错误分组被拒", rc.normalize_damage("2,158,17,750")[0], None)
    eq("空格容错", rc.normalize_damage(" 1,538,157,240 ")[0], 1538157240)
    eq("% 不是伤害", rc.normalize_damage("0.0%")[0], None)
    eq("LEVEL 不是伤害", rc.normalize_damage("LEVEL 10")[0], None)
    eq("千分位输出", rc.fmt_damage(2158171750), "2,158,171,750")
    ok("成员名校验：LEVEL 排除", not rc.is_member_text("LEVEL 10"))
    ok("成员名校验：纯数字排除", not rc.is_member_text("1234"))
    ok("成员名校验：UI 词排除", not rc.is_member_text("联盟记录"))
    ok("成员名校验：正常通过", rc.is_member_text("点墨为霜") and rc.is_member_text("HANSON"))
    eq("等级：标准", rc.parse_level("LEVEL 10"), 10)
    eq("等级：无空格", rc.parse_level("LEVEL9"), 9)
    eq("等级：O 混成 0（LEVEL 1O0）", rc.parse_level("LEVEL 1O0"), 10)
    eq("等级：小写 o", rc.parse_level("Level 1o"), 10)
    eq("等级：只有数字", rc.parse_level("10"), 10)
    eq("等级：认不出不瞎猜", rc.parse_level("LEVEL"), None)


# ---------------------------------------------------------------- T3 locate
def _tok(x0, y0, x1, y1, text, score=0.99):
    return dict(x0=x0, y0=y0, x1=x1, y1=y1, text=text, score=score)


def test_locate():
    print("[T3] locate 边界")
    # 两行，第一行有日标记，第二行 LEVEL 没空格
    toks = [
        _tok(46, 118, 102, 146, "第1天"),
        _tok(72, 172, 132, 197, "LEVEL 10"),
        _tok(134, 185, 206, 207, "HANSON"),
        _tok(383, 185, 459, 212, "2,158,171,750"),
        _tok(420, 213, 459, 230, "0.0%"),
        _tok(72, 264, 122, 287, "LEVEL9"),
        _tok(134, 276, 206, 297, "DORO"),
        _tok(383, 276, 459, 302, "1,538,157,240"),
        _tok(420, 304, 459, 321, "0.0%"),
        _tok(266, 17, 350, 45, "联盟记录"),
    ]
    loc = rc.locate(toks)
    eq("行数", loc.rows(), 2)
    ok("行距 ≈ 90", abs(loc.pitch - 90.5) < 3, f"pitch={loc.pitch}")
    eq("成员 1", loc.cards[0]["member"], "HANSON")
    eq("等级 1", loc.cards[0]["level"], 10)
    eq("伤害 1", loc.cards[0]["damage"], 2158171750)
    eq("占比 1", loc.cards[0]["pct"], "0.0%")
    eq("等级无空格", loc.cards[1]["level"], 9)
    ok("BOSS ROI 落在成员名下方", loc.cards[0]["boss_roi"][1] > loc.cards[0]["member_box"][3])
    ok("BOSS ROI 不越到下一行", loc.cards[0]["boss_roi"][3] < 264,
       f"roi_y1={loc.cards[0]['boss_roi'][3]}")
    recs = rc.attach_days(loc.cards, loc.days)
    eq("日标记分配", [r["day"] for r in recs], [1, 1])

    # 单行：没有行距可量，也不能崩
    one = rc.locate([_tok(134, 185, 206, 207, "HANSON"),
                     _tok(383, 185, 459, 212, "1,000,000"),
                     _tok(420, 213, 459, 230, "0.0%")])
    eq("单行也能出记录", one.rows(), 1)
    ok("单行兜底行距 > 0", one.pitch > 0)

    # 缺成员名：不能编一个出来
    bare = rc.locate([_tok(383, 185, 459, 212, "1,000,000")])
    eq("缺成员名 → 空串", bare.cards[0]["member"], "")


# ---------------------------------------------------------------- T4 splice
def _rec(day, member, boss, dmg):
    return {"day": day, "member": member, "boss": boss, "damage": dmg,
            "level": 10, "pct": "0.0%"}


def test_splice():
    print("[T4] 跨屏拼接（返回要追加的行）")
    # 往下翻 2 行：旧的第 3 行变成新的第 1 行
    a = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200),
         _rec(1, "C", "铁匠", 300), _rec(1, "D", "铁匠", 400)]
    b = [_rec(1, "C", "铁匠", 300), _rec(1, "D", "铁匠", 400),
         _rec(1, "E", "医师", 500), _rec(1, "F", "医师", 600)]
    added, info = rc.splice(a, b, 2, 1.0)
    ok("正常位移可拼接", added is not None, str(info))
    eq("只追加 2 条", len(added or []), 2)
    eq("追加的是 E/F", [r["member"] for r in added or []], ["E", "F"])
    eq("拼完 6 条", len(a + (added or [])), 6)
    eq("位移行数", info.get("shift_rows"), 2)
    eq("重叠核对满额", info.get("hit"), info.get("overlap"))

    # 位移量出 0，但内容对得上「滚了 2 行」→ 按内容定案。
    # 实测这个弹窗拖 200px 只量到 35px，round(35/100)=0；旧实现在这里直接判
    # 「没动」，于是每次滚动都被当成到底（2026-11-07 用户报的"效果特别差"就是这个）。
    added2, info2 = rc.splice(a, b, 0, 1.0)
    ok("位移 0 但内容对得上 → 按内容定案",
       added2 is not None and info2.get("shift_rows") == 2, str(info2))
    eq("按内容定案后追加 2 条", len(added2 or []), 2)

    # 真的没动：位移 0 且两屏内容一模一样
    added2b, info2b = rc.splice(a, a, 0, 1.0)
    ok("位移 0 且内容相同 → 没动", added2b == [] and info2b.get("same") is True,
       str(info2b))

    added3, info3 = rc.splice(a, b, None, 1.0)
    ok("量不到位移 → 拒绝", added3 is None and "位移" in str(info3.get("why", "")))

    added4, info4 = rc.splice(a, b, 9, 1.0)
    ok("位移 ≥ 上屏行数 → 拒绝", added4 is None, str(info4))

    bad = [_rec(1, "X", "医师", 999), _rec(1, "Y", "医师", 998),
           _rec(1, "E", "医师", 500), _rec(1, "F", "医师", 600)]
    added5, info5 = rc.splice(a, bad, 2, 1.0)
    ok("重叠区对不上 → 拒绝", added5 is None and "对得上" in str(info5.get("why", "")), str(info5))

    # 位移混叠：真实走 3 行，像素量成 2 行 → 靠重叠区内容修正到 3
    p6 = [_rec(1, c, "古铁", 10 * i) for i, c in enumerate("ABCDEF")]
    c6 = [_rec(1, c, "古铁", 10 * (i + 3)) for i, c in enumerate("DEFGHI")]
    added8, info8 = rc.splice(p6, c6, 2, 1.0)
    ok("位移混叠可按内容修正", added8 is not None and info8.get("shift_rows") == 3, str(info8))
    eq("修正后追加 3 条", len(added8 or []), 3)
    eq("修正后顺序", "".join(r["member"] for r in p6 + (added8 or [])), "ABCDEFGHI")

    # 混叠 2 行（真实走 4、像素量成 2）：原来的 k0±1 救不回来，会判"位移不可信"
    # → 换滚动方式 → 最后误报到底，中间整段丢行。实测 2026-11-06 23:46 那轮
    # 三个人各少一条就是这条路径造成的。
    p9 = [_rec(1, c, "古铁", 10 * i) for i, c in enumerate("ABCDEF")]
    c9 = [_rec(1, c, "古铁", 10 * (i + 4)) for i, c in enumerate("EFGHIJ")]
    added9, info9 = rc.splice(p9, c9, 2, 1.0)
    ok("混叠 2 行也能按内容修正", added9 is not None and info9.get("shift_rows") == 4, str(info9))
    eq("混叠 2 行后追加 4 条（重叠 EF 两条）", len(added9 or []), 4)
    eq("混叠 2 行后顺序", "".join(r["member"] for r in p9 + (added9 or [])), "ABCDEFGHIJ")

    added6, info6 = rc.splice([], b, None, 1.0)
    ok("首屏直接接受", len(added6 or []) == 4 and info6.get("fresh"))

    # 重叠区回填：某条落在画面边缘时字段读不全（如首卡被「第 N 天」标题栏压住），
    # 滚过一次后同一行落到画面中部、重读完整，应当把缺的字段补回来。
    # 注意首卡本身滚出去就补不到了——这里测的是"仍在重叠区内的边缘行"。
    p10 = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200), _rec(1, "C", "古铁", 300)]
    p10[1]["pct"] = ""          # 第 2 条占比没读到
    c10 = [_rec(1, "B", "古铁", 200), _rec(1, "C", "古铁", 300), _rec(1, "D", "古铁", 400)]
    _added10, info10 = rc.splice(p10, c10, 1, 1.0)
    patch = info10.get("patch") or []
    # 下标语义：patch 给的是「新屏内的位置 j」，宿主按 len(rows)-len(prev)+j 定位。
    # 曾经错写成 k+j，宿主再补一次 k → 回填落到错误行（k>0 时必错，会覆盖好行）。
    # 这里连宿主那段映射一起验，避免只验一半。
    rows10 = list(p10) + list(_added10 or [])
    base10 = len(rows10) - len(p10)
    for idx, rec in patch:
        rows10[base10 + idx] = rec
    ok("重叠区缺字段会被回填", bool(patch) and patch[0][0] == 0
       and patch[0][1].get("pct") == "0.0%", str(patch))
    ok("回填落到正确的那一行（不是重复加 k）",
       [r["member"] for r in rows10] == ["A", "B", "C", "D"]
       and rows10[1].get("pct") == "0.0%", str([(r["member"], r.get("pct")) for r in rows10]))
    # 真实事故（12:15 那轮）：上一屏最后两行压在列表下边界，读成 `LEVEEL]` / `AN`，
    # 而同一批行滚上来后是完整读数。按 80% 硬卡会判"拼不上"→ 补采一次仍失败→收工丢数据。
    # 判据改为：锚点成立 + 重叠区最多容忍 2 行对不上。
    pd = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200),
          _rec(1, "C", "古铁", 300), _rec(1, "D", "古铁", 400),
          _rec(1, "LEVEEL]", "医师", 6712891076), _rec(1, "AN", "", 7400071404)]
    cd = [_rec(1, "C", "古铁", 300), _rec(1, "D", "古铁", 400),
          _rec(1, "E", "医师", 6712891076), _rec(1, "F", "重装火神R", 7408871434),
          _rec(1, "G", "古铁", 700), _rec(1, "H", "古铁", 800)]
    # 真实读数带置信度：废行 0.53 / 0.59，滚上来后的好行 0.998 / 0.989。
    # 覆盖判据必须靠置信度差，否则"看起来齐全的废行"永远盖不掉。
    pd[4]["conf"], pd[5]["conf"] = 0.534, 0.588
    cd[2]["conf"], cd[3]["conf"] = 0.998, 0.989
    addedD, infoD = rc.splice(pd, cd, 2, 1.0)
    ok("末两行读废时仍能拼上（真事故回归）",
       addedD is not None and infoD.get("shift_rows") == 2 and infoD.get("hit") == 2,
       str(infoD))
    ok("拼上后只追加后半段", [r["member"] for r in (addedD or [])] == ["G", "H"],
       str([r["member"] for r in (addedD or [])]))
    ok("两行废读数被新读数替换",
       [j for j, _ in (infoD.get("patch") or [])] == [2, 3], str(infoD.get("patch")))

    # 真事故 2（12:32 那轮两次卡住）：新屏**第一行**被框上沿切掉，
    # 实测读成 `OAOE` / `班长`（缺 BOSS），而 k+1 那一对完全正确。
    # 锚点只认第一行 → 3/4 命中却判"位移不可信"→ 补采 → 白跑一趟。
    pe = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200),
          _rec(1, "C", "古铁", 300), _rec(1, "D", "古铁", 400),
          _rec(1, "E", "古铁", 500), _rec(1, "F", "古铁", 600)]
    ce = [_rec(1, "OAOE", "", 300), _rec(1, "D", "古铁", 400),
          _rec(1, "E", "古铁", 500), _rec(1, "F", "古铁", 600),
          _rec(1, "G", "古铁", 700), _rec(1, "H", "古铁", 800)]
    addedE, infoE = rc.splice(pe, ce, 2, 1.0)
    ok("首行读废时靠第二行锚定位移（真事故回归）",
       addedE is not None and infoE.get("shift_rows") == 2, str(infoE))
    ok("首行读废时只追加后半段", [r["member"] for r in (addedE or [])] == ["G", "H"],
       str([r["member"] for r in (addedE or [])]))

    # 真事故 3（12:51 那轮）：列表到底、画面没动，上一屏末行读废
    # （`渲奖 / 0 767 106 202`），同一位置这一屏读对了（`凛酱 / 铁匠`）。
    # 整数位移扫描全是 0/N → 判"拼不上"→ 收工丢尾。应判"没挪动但末行可替换"。
    pf = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200),
          _rec(1, "C", "古铁", 300), _rec(1, "D", "古铁", 400),
          _rec(1, "E", "古铁", 500), _rec(1, "渲奖", "", 767106202)]
    cf = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200),
          _rec(1, "C", "古铁", 300), _rec(1, "D", "古铁", 400),
          _rec(1, "E", "古铁", 500), _rec(1, "F", "铁匠", 9767186202)]
    pf[5]["conf"], cf[5]["conf"] = 0.683, 0.996
    addedF, infoF = rc.splice(pf, cf, 3 * 100, 100)
    ok("画面没动但末行读好了 → 不追加、给回填",
       addedF == [] and infoF.get("same") is False
       and [j for j, _ in (infoF.get("patch") or [])] == [5], str(infoF))
    ok("回填的是新屏那一行", (infoF.get("patch") or [(None, {})])[0][1].get("member") == "F",
       str(infoF.get("patch")))

    # 反过来：旧行字段齐全时，绝不能被新读数覆盖
    p11 = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200)]
    c11 = [_rec(1, "A", "古铁", 100), _rec(1, "B", "古铁", 200), _rec(1, "C", "古铁", 300)]
    c11[0]["level"] = 3
    _added11, info11 = rc.splice(p11, c11, 1, 1.0)
    ok("旧行字段齐全时不覆盖", not (info11.get("patch") or []), str(info11.get("patch")))

    # 日期补全：第二屏没有日标记，沿用上一屏的
    c = [_rec(1, "B", "古铁", 200), _rec(1, "C", "铁匠", 300),
         _rec(1, "D", "铁匠", 400), _rec(None, "E", "医师", 500)]
    added7, _ = rc.splice(a, c, 1, 1.0)
    full, _miss = rc.fill_days(a + (added7 or []))
    eq("日期向后补", (full or [{}])[-1].get("day"), 1)
    eq("补全后条数", len(full or []), 5)


# ---------------------------------------------------------------- T3b 半行 / 墨迹列
def test_partial_and_span():
    print("[T3b] 被切半行 + 墨迹列定位")
    # 4 行里第 1 行被上边切掉一半（字高只有别人一半）
    toks = []
    ys = [60, 160, 260, 360]
    for i, y in enumerate(ys):
        h = 8 if i == 0 else 22
        toks += [_tok(72, y - 14, 132, y - 14 + h, "LEVEL 10"),
                 _tok(134, y - 8, 206, y - 8 + h, "HANSON"),
                 _tok(383, y - 8, 459, y + 19, "2,158,171,750"),
                 _tok(420, y + 20, 459, y + 37, "0.0%")]
    loc = rc.locate(toks)
    eq("4 行都定位到", loc.rows(), 4)
    eq("首行被标为半行", loc.cards[0].get("partial"), True)
    eq("其余行不算半行", [c.get("partial") for c in loc.cards[1:]], [False, False, False])

    # 墨迹列：左边一个孤立小图标 + 右边更宽的文字，应取文字那一组
    import numpy as np
    import raid_ocr as ro
    arr = np.zeros((40, 300, 3), dtype=np.int16)
    arr[10:30, 20:40] = 255          # 图标（窄）
    arr[10:30, 60:200] = 255         # 文字（宽）
    sp = ro.light_span(arr, 5, 35, 0, 300)
    ok("墨迹列取最宽的一组（跳过图标）", sp is not None and sp[0] >= 55,
       str(sp))
    ok("墨迹列右边界在文字内", sp is not None and sp[1] <= 205, str(sp))


# ---------------------------------------------------------------- T4b 圈选范围
def test_range():
    print("[T4b] 圈选范围（只涂黑，不改坐标系）")
    from PIL import Image
    import raid_window as rw
    img = Image.new("RGB", (100, 100), (200, 200, 200))
    m = rw.mask_outside(img, (10, 10, 50, 50))
    eq("尺寸不变", m.size, (100, 100))
    eq("范围外涂黑", m.getpixel((0, 0)), (0, 0, 0))
    eq("范围内保留", m.getpixel((20, 20)), (200, 200, 200))
    eq("范围换算", rw._range_of({"list_rel": [0.1, 0.2, 0.5, 0.6]}, img),
       (10.0, 20.0, 50.0, 60.0))
    eq("没预设就不涂", rw.mask_outside(img, None) is img, True)


# ---------------------------------------------------------------- T4c 多读投票
def test_vote():
    print("[T4c] BOSS 名多读投票")
    eq("两票对一票取多数", rc.vote_text([("铁匠", .99), ("铁匠", .98), ("医师", .95)])[0], "铁匠")
    eq("三票全不同取高分", rc.vote_text([("甲", .5), ("乙", .9), ("丙", .7)])[0], "乙")
    eq("空输入", rc.vote_text([])[0], "")
    # 记录一个已知边界：投票只认"多数"，2:1 时会被带偏——所以 read_boss 的主路径是 det，
    # 这正是 `惠医师` 那一行被修掉的原因（见 raid_ocr.read_boss 的说明）。
    eq("2:1 时服从多数（所以主路径必须是 det）",
       rc.vote_text([("医师", .99), ("惠医师", .91), ("惠医师", .95)])[0], "惠医师")


# ---------------------------------------------------------------- T4d BOSS 读取策略
def test_boss_policy():
    print("[T4d] BOSS 读取策略：det 优先、垃圾读数退回投票")
    from PIL import Image
    import raid_ocr as ro
    img = Image.new("RGB", (10, 10))
    box = [(0, 0, 5, 5)]
    od, ol = ro.read_boss_det, ro.read_line
    try:
        ro.read_boss_det = lambda *a, **k: "医师"
        ro.read_line = lambda *a, **k: ("惠医师", 0.9)
        eq("det 干净 → 用 det", ro.read_boss(img, box, None)[0], "医师")

        ro.read_boss_det = lambda *a, **k: "¥早众"
        ro.read_line = lambda *a, **k: ("古铁", 0.95)
        eq("det 是垃圾串 → 退回投票", ro.read_boss(img, box, None)[0], "古铁")

        ro.read_boss_det = lambda *a, **k: ""
        ro.read_line = lambda *a, **k: ("惠医师", 0.9)
        eq("det 空 → 投票（2:1 服从多数，已知边界）", ro.read_boss(img, box, None)[0], "惠医师")
    finally:
        ro.read_boss_det, ro.read_line = od, ol


# ---------------------------------------------------------------- T4e BOSS 闭集归一
def test_boss_set():
    print("[T4e] BOSS 名归一到本期 5 个固定 BOSS")
    S19 = ["邪魔", "医师", "铁匠", "重装火神R", "古铁"]
    eq("多一个字（本铁匠）", rc.match_boss("本铁匠", S19)[0], "铁匠")
    eq("多一个字（惠医师）", rc.match_boss("惠医师", S19)[0], "医师")
    eq("两头多字（感医师思）", rc.match_boss("感医师思", S19)[0], "医师")
    eq("标准名原样返回", rc.match_boss("重装火神R", S19)[0], "重装火神R")
    eq("空格容错", rc.match_boss("铁 匠", S19)[0], "铁匠")
    eq("异体字（裝）走相似", rc.match_boss("重裝火神R", S19)[0], "重装火神R")
    eq("下划线归不了", rc.match_boss("_", S19)[0], None)
    eq("乱码归不了", rc.match_boss("¥早众", S19)[0], None)
    eq("别的期的 BOSS 不认（本期没有火神）", rc.match_boss("火神", S19)[0], None)
    eq("空名单不报错", rc.match_boss("医师", [])[0], None)


# ---------------------------------------------------------------- T5 汇总 / 导出
def test_export():
    print("[T5] 汇总与导出")
    recs = [_rec(1, "HANSON", "古铁", 100), _rec(1, "HANSON", "古铁", 100),
            _rec(1, "DORO", "铁匠", 50)]
    s = rc.summarize(recs)
    eq("总伤害", s["total"], 250)
    eq("按成员聚合", s["members"]["HANSON"]["damage"], 200)
    eq("按成员次数", s["members"]["HANSON"]["count"], 2)
    eq("按 BOSS 聚合", s["bosses"]["古铁"]["damage"], 200)
    eq("按天聚合", s["days"][1]["count"], 3)

    with tempfile.TemporaryDirectory() as td:
        p1 = rc.write_csv(recs, Path(td) / "d.csv")
        p2 = rc.write_summary_csv(recs, Path(td) / "s.csv")
        t1 = p1.read_text(encoding="utf-8-sig")
        t2 = p2.read_text(encoding="utf-8-sig")
        ok("明细 CSV 有 BOM", p1.read_bytes()[:3] == b"\xef\xbb\xbf")
        ok("明细含成员", "HANSON" in t1)
        ok("汇总含成员段", "汇总" in t2 and "HANSON" in t2)
        ok("汇总含 BOSS 段", "BOSS" in t2 and "古铁" in t2)


# ---------------------------------------------------------------- T1 真实截图
def test_screenshot():
    print("[T1] 真实截图（6 条记录）")
    if not SAMPLE.exists():
        ok("样本存在", False, str(SAMPLE))
        return
    try:
        from PIL import Image
        import raid_ocr as ro
        import alliance_ocr as A
    except Exception as e:
        ok("依赖可用", False, f"{type(e).__name__}: {e}")
        return
    img = Image.open(SAMPLE).convert("RGB")
    loc, recs = ro.read_screen(img, A.get_engine())
    eq("行数", len(recs), 6)
    want = [
        (1, "HANSON", "古铁", 10, 2158171750, "0.0%"),
        (1, "HANSON", "重装火神R", 10, 1538157240, "0.0%"),
        (1, "点墨为霜", "铁匠", 10, 1569579475, "0.0%"),
        (1, "点墨为霜", "医师", 10, 1118659860, "0.0%"),
        (1, "点墨为霜", "邪魔", 10, 1677989700, "0.0%"),
        (1, "DORO", "古铁", 9, 2158171750, "0.0%"),
    ]
    for i, (day, m, b, lv, dmg, pct) in enumerate(want):
        if i >= len(recs):
            ok(f"第 {i+1} 条存在", False)
            continue
        r = recs[i]
        eq(f"第{i+1}条 天", r.get("day"), day)
        eq(f"第{i+1}条 成员", r.get("member"), m)
        eq(f"第{i+1}条 BOSS", r.get("boss"), b)
        eq(f"第{i+1}条 等级", r.get("level"), lv)
        eq(f"第{i+1}条 伤害", r.get("damage"), dmg)
        eq(f"第{i+1}条 占比", r.get("pct"), pct)
    ok("无格式告警", not [r for r in recs if r.get("damage_note")],
       str([(r.get("member"), r.get("damage_note")) for r in recs if r.get("damage_note")]))


# ---------------------------------------------------------------- T6 合成滚动整链
def test_roll():
    """把真实截图裁出一屏，整体上滚一行，走一遍 位移→拼接，验整条链路。"""
    print("[T6] 合成滚动整链")
    if not SAMPLE.exists():
        ok("样本存在", False, str(SAMPLE))
        return
    import numpy as np
    from PIL import Image
    import raid_ocr as ro
    import alliance_ocr as A

    img = Image.open(SAMPLE).convert("RGB")
    eng = A.get_engine()
    loc1, r1 = ro.read_screen(img, eng)
    eq("首屏 6 条", len(r1), 6)
    if not loc1.cards:
        return
    step = int(round(loc1.pitch))

    arr = np.asarray(img).copy()
    sub = arr[185:700, 60:470].copy()
    arr[185:700, 60:470] = np.roll(sub, -step, axis=0)
    img2 = Image.fromarray(arr)
    loc2, r2 = ro.read_screen(img2, eng)
    ok("第二屏仍能认出 6 条（引擎状态没被污染）", len(r2) == 6, f"got={len(r2)}")

    shift, corr = A.measure_vshift(img.crop(loc1.box), img2.crop(loc1.box))
    ok("位移量到 ≈1 行", shift is not None and abs(shift - step) <= 3,
       f"shift={shift} corr={corr:.2f}")

    # 反向滚（内容下移）必须量出**负**位移。真机"拉不回列表顶部"就是这里：
    # 旧函数只搜正位移，反向时最优解在负半轴取不到，只能返回 0，与"没动"同形。
    arr3 = np.asarray(img).copy()
    sub3 = arr3[185:700, 60:470].copy()
    arr3[185:700, 60:470] = np.roll(sub3, step, axis=0)
    img3 = Image.fromarray(arr3)
    neg, ncorr = A.measure_vshift_signed(img.crop(loc1.box), img3.crop(loc1.box))
    ok("反向滚量得负位移", neg is not None and abs(neg + step) <= 3,
       f"shift={neg} corr={ncorr:.2f}")
    old, _oc = A.measure_vshift(img.crop(loc1.box), img3.crop(loc1.box))
    # 旧函数不是"稳定返回 0"，而是只能给 0~几像素的噪声（真位移 120px 量不到）。
    # 判据按它的实际语义写：量不出真位移，就等于"没动"。
    ok("旧函数量不到反向位移（真位移 120px，它只给噪声）",
       old is not None and abs(old) <= 3, f"shift={old}")

    # 两帧完全相同时必须仍是 0，不能因为放宽到负半轴就凭空造出位移
    same, scorr = A.measure_vshift_signed(img, img)
    ok("无位移时仍报 0", same == 0, f"shift={same} corr={scorr:.2f}")
    added, info = rc.splice(r1, r2, shift if corr >= 0.3 else None, loc1.pitch)
    ok("整链可拼接", added is not None, str(info))
    eq("只新增 1 条", len(added or []), 1)
    eq("拼接后 7 条", len(r1 + (added or [])), 7)
    eq("重叠核对满额", (info or {}).get("hit"), (info or {}).get("overlap"))


# ---------------------------------------------------------------- T7 几何兜底
def test_fallback():
    """把 token 里的 LEVEL / 百分比全删掉，模拟真机 det 漏认，看几何兜底能不能救回来。

    真机现场（2026-11-06 22:00 导出）：6 行里 4 行等级是空的，2 行等级正常；
    BOSS 还出现「感医师思」——图标被读成字。这两个问题都靠几何 ROI 收窄/重认解决。
    """
    print("[T7] 几何兜底（模拟 det 漏认等级/百分比）")
    if not SAMPLE.exists():
        ok("样本存在", False, str(SAMPLE))
        return
    from PIL import Image
    import raid_ocr as ro
    import alliance_ocr as A

    img = Image.open(SAMPLE).convert("RGB")
    eng = A.get_engine()
    toks = ro.tokens(img, eng)
    kept = [t for t in toks
            if not rc.LEVEL_RE.search(rc.norm_text(t["text"]))
            and not rc.PCT_RE.match(rc.norm_text(t["text"]))]
    ok("已删掉 LEVEL/百分比 token", len(kept) < len(toks), f"{len(toks)} → {len(kept)}")

    loc, recs = ro.read_screen(img, eng, toks=kept)
    eq("仍然 6 条", len(recs), 6)
    eq("等级全部兜底出来", [r["level"] for r in recs], [10, 10, 10, 10, 10, 9])
    eq("百分比全部兜底出来", [r["pct"] for r in recs], ["0.0%"] * 6)
    eq("BOSS 名不带图标残字", [r["boss"] for r in recs],
       ["古铁", "重装火神R", "铁匠", "医师", "邪魔", "古铁"])


# ---------------------------------------------------------------- T8 红色徽标读等级
def test_badge():
    """等级不靠几何比例，靠红色徽标定位 —— 这是"圈选/比例"体系的替代方案。"""
    print("[T8] 红色徽标定位等级")
    if not SAMPLE.exists():
        ok("样本存在", False, str(SAMPLE))
        return
    from PIL import Image
    import raid_ocr as ro
    import alliance_ocr as A

    img = Image.open(SAMPLE).convert("RGB")
    eng = A.get_engine()
    loc = rc.locate(ro.tokens(img, eng))
    mask = ro.red_mask(img)
    eq("红色像素够多", bool(mask.sum() > 1000), True)
    got, boxes = [], []
    for c in loc.cards:
        box = ro.badge_box(mask, c["member_box"])
        boxes.append(box)
        lv, _raw = ro.read_level_badge(img, mask, c["member_box"], eng)
        got.append(lv)
    ok("每行都找到徽标", all(b is not None for b in boxes), str(boxes))
    eq("等级 6/6", got, [10, 10, 10, 10, 10, 9])
    if all(boxes):
        ok("徽标在成员名左侧", all(b[2] <= c["member_box"][0] + 2
                                   for b, c in zip(boxes, loc.cards)),
           str([(b[2], c["member_box"][0]) for b, c in zip(boxes, loc.cards)]))


# ---------------------------------------------------------------- T9 三屏连续滚采
def test_chain():
    """合成一条 18 行的长列表，按 2 行/屏 截三次，走完整「位移→追加」。

    这条用例抓出过一个真 bug：`splice` 原本拿**累计列表**去和当前屏比对，
    第一屏之后长度对不上，第三屏必定判「0/5 对不上」。正确做法是拿**上一屏**
    去比、只返回要追加的行。
    """
    print("[T9] 三屏连续滚采（追加语义）")
    if not SAMPLE.exists():
        ok("样本存在", False, str(SAMPLE))
        return
    from PIL import Image
    import raid_ocr as ro
    import alliance_ocr as A

    img0 = Image.open(SAMPLE).convert("RGB")
    eng = A.get_engine()
    loc, rows = ro.read_screen(img0, eng)
    eq("首屏 6 条", len(rows), 6)
    pitch = loc.pitch

    block = img0.crop((60, 185, 470, 700))
    W, H = block.size
    tall = Image.new("RGB", (W, H * 3))
    for i in range(3):
        tall.paste(block, (0, i * H))
    step = int(round(2 * pitch))

    prev_img, prev_rows = None, None
    for k in range(3):
        im = tall.crop((0, k * step, W, k * step + H))
        _loc, recs = ro.read_screen(im, eng)
        if k == 0:
            prev_img, prev_rows = im, recs
            acc = list(recs)
            continue
        shift, corr = A.measure_vshift(prev_img, im)
        added, info = rc.splice(prev_rows, recs, shift if corr >= 0.3 else None, pitch)
        ok(f"第 {k+1} 屏可拼接", added is not None, str(info))
        if added is None:
            return
        acc += added
        prev_img, prev_rows = im, recs

    eq("三屏累计条数", len(acc), 10)          # 6 + 2 + 2（每屏前进 2 行）
    eq("无重复首行", acc[0]["member"], "HANSON")
    eq("追加的成员顺序", [r["member"] for r in acc[6:]],
       ["HANSON", "HANSON", "点墨为霜", "点墨为霜"])   # 长图是 6 行一块循环拼的


# ---------------------------------------------------------------- T10 真机帧（主回归）
REAL_2320 = HERE / "测试素材" / "raid" / "真机_2320x1305_首屏.png"
REAL_2092 = HERE / "测试素材" / "raid" / "真机_2092x1177_第二屏.png"


def test_real_frames():
    """真机原图整屏回归 —— 这是唯一能证明"在你机器上对"的用例。

    两张都是 2026-11-06 客户端的诊断帧（工具自己存的 `全屏.png`）：
      · 2320×1305 首屏：6 行完整，等级真值 10/10/10/10/10/9
      · 2092×1177 第二屏：最后一行被列表下边切掉，应丢弃 → 5 行，等级全 9
    之前所有失败（等级空、BOSS 带残字、最后一行 BOSS 读空）都在这两张图上复现过。
    """
    print("[T10] 真机帧整屏回归（2320 / 2092 两种分辨率）")
    import raid_ocr as ro
    import alliance_ocr as A
    from PIL import Image

    S19 = ["邪魔", "医师", "铁匠", "重装火神R", "古铁"]
    eng = A.get_engine()

    if not REAL_2320.exists():
        ok("2320 帧存在", False, str(REAL_2320))
        return
    img = Image.open(REAL_2320).convert("RGB")
    _loc, recs = ro.read_screen(img, eng, bosses=S19)
    eq("2320 行数", len(recs), 6)
    eq("2320 成员", [r["member"] for r in recs],
       ["HANSON", "HANSON", "点墨为霜", "点墨为霜", "点墨为霜", "DORO"])
    eq("2320 BOSS", [r["boss"] for r in recs],
       ["古铁", "重装火神R", "铁匠", "医师", "邪魔", "古铁"])
    eq("2320 等级", [r["level"] for r in recs], [10, 10, 10, 10, 10, 9])
    eq("2320 伤害", [r["damage"] for r in recs],
       [2158171750, 1538157240, 1569579475, 1118659860, 1677989700, 2158171750])
    eq("2320 占比", [r["pct"] for r in recs], ["0.0%"] * 6)
    eq("2320 无缺字段行", sum(1 for r in recs
                          if not (r["boss"] and r["level"] and r["damage"] and r["pct"])), 0)

    if not REAL_2092.exists():
        ok("2092 帧存在", False, str(REAL_2092))
        return
    img2 = Image.open(REAL_2092).convert("RGB")
    loc2, recs2 = ro.read_screen(img2, eng, bosses=S19)
    eq("2092 行数（末行被切应丢弃）", len(recs2), 5)
    ok("2092 说了为什么丢", any("切" in n for n in loc2.notes), str(loc2.notes))
    eq("2092 等级", [r["level"] for r in recs2], [9, 9, 9, 9, 9])
    eq("2092 BOSS", [r["boss"] for r in recs2],
       ["古铁", "重装火神R", "铁匠", "医师", "邪魔"])
    eq("2092 无缺字段行", sum(1 for r in recs2
                          if not (r["boss"] and r["level"] and r["damage"] and r["pct"])), 0)


# ---------------------------------------------------------------- T11 外部识别 CLI
def test_external_cli():
    """外部识别桥：命令模板、真跑一次、缓存、失败静默、熔断。

    用「python 冒充识别 CLI」来测，不依赖任何第三方程序 —— 这样这条用例在
    任何机器上都能跑，而逻辑和真接 Umi-OCR / PaddleOCR 完全一样。
    """
    print("[T11] 外部识别 CLI 兜底")
    import json
    import tempfile
    from pathlib import Path as _P
    import raid_cli
    import raid_ocr as ro
    from PIL import Image, ImageDraw

    eq("占位符替换", raid_cli.resolve_cmd({"cmd": ["x", "{png}", "y"]}, _P("a.png")),
       ["x", "a.png", "y"])
    eq("无占位符则追加", raid_cli.resolve_cmd({"cmd": ["x"]}, _P("a.png")), ["x", "a.png"])

    tmp = _P(tempfile.mkdtemp(prefix="clitest_"))
    (tmp / "预设").mkdir()
    counter = tmp / "calls.txt"
    fake = tmp / "fake_ocr.py"
    fake.write_text(
        "import sys, pathlib\n"
        f"p = pathlib.Path(r'{counter}')\n"
        "p.write_text((p.read_text() if p.exists() else '') + 'x')\n"
        "print('LEVEL 7')\n", encoding="utf-8")
    cfg = {"enabled": True, "cmd": [sys.executable, str(fake), "{png}"], "timeout": 20}
    (tmp / "预设" / "识别后端.json").write_text(json.dumps(cfg), encoding="utf-8")

    raid_cli.reset()
    ok("配置读到且可用", raid_cli.enabled(tmp))
    eq("外部命令返回文本", raid_cli.read_text(tmp, b"b1"), "LEVEL 7")
    raid_cli.read_text(tmp, b"b1")
    eq("同字节只调一次（缓存）", counter.read_text(), "x")

    # 命令不存在 → 静默返回空串，不抛
    bad = {"enabled": True, "cmd": [str(tmp / "nope.exe"), "{png}"], "timeout": 5}
    (tmp / "预设" / "识别后端.json").write_text(json.dumps(bad), encoding="utf-8")
    raid_cli.reset()
    eq("程序不存在时返回空", raid_cli.read_text(tmp, b"b2"), "")
    for _ in range(3):
        raid_cli.read_text(tmp, b"b3")
    ok("连续失败后熔断（本次运行不再调用）", not raid_cli.enabled(tmp))

    # 整条链路：本地读不出的徽标 → 外部补上
    (tmp / "预设" / "识别后端.json").write_text(json.dumps(cfg), encoding="utf-8")
    raid_cli.reset()
    ro.set_app_dir(tmp)
    img = Image.new("RGB", (300, 150), (12, 12, 12))
    ImageDraw.Draw(img).rectangle((20, 30, 180, 120), fill=(200, 30, 30))
    mask = ro.red_mask(img)
    lv, raw = ro.read_level_badge(img, mask, (200, 40, 280, 80), None)
    eq("本地读不出时用外部结果", lv, 7)
    ok("外部读数被记下来", "7" in str(raw), str(raw))

    ro.set_app_dir(None)          # 归还，别影响后面的用例
    raid_cli.reset()


# ---------------------------------------------------------------- T12 字段标定表
def test_field_calib():
    """字段标定：默认值等价于老逻辑、换算自洽、换一套框能真的换切图位置。"""
    print("[T12] 字段标定表")
    from PIL import Image
    import alliance_ocr as A
    import raid_ocr as ro
    import raid_window as rw

    # 1) 默认表 = 老逻辑（同一屏，传不传 DEFAULT_FIELDS 结果完全一致）
    if SAMPLE.exists():
        img = Image.open(SAMPLE).convert("RGB")
        eng = A.get_engine()
        toks = ro.tokens(img, eng)
        a = rc.locate(toks)
        b = rc.locate(toks, fields=rc.DEFAULT_FIELDS)
        eq("默认标定与老逻辑一致（等级框）",
           [c["level_roi"] for c in a.cards], [c["level_roi"] for c in b.cards])
        eq("默认标定与老逻辑一致（BOSS 框）",
           [c["boss_roi"] for c in a.cards], [c["boss_roi"] for c in b.cards])

        # 2) 换一套框 → 切图位置真的跟着变
        moved = dict(rc.DEFAULT_FIELDS)
        moved["level"] = [v + 2.0 if i % 2 == 0 else v
                          for i, v in enumerate(rc.DEFAULT_FIELDS["level"])]
        c = rc.locate(toks, fields=moved)
        h = a.cards[0]["member_box"][3] - a.cards[0]["member_box"][1]
        eq("等级框跟着标定右移 2h",
           c.cards[0]["level_roi"][0] - a.cards[0]["level_roi"][0], int(2 * h))

    # 3) 画布 → 比例的换算自洽：把默认框画一遍，应该还原成默认值
    anchor = (100, 200, 192, 228)          # h = 28
    boxes = [rc.field_box(anchor, rc.DEFAULT_FIELDS[k]) for k in ("level", "boss", "pct")]
    got = rw.fields_from_canvas(anchor, boxes, 1.0, (0, 0))
    for key in ("level", "boss", "pct"):
        want = list(rc.DEFAULT_FIELDS[key])
        # 容差 0.05h：field_box 会取整到像素，±0.5px / 28 ≈ ±0.018h，再留点余量
        ok(f"换算自洽：{key}", all(abs(g - w) <= 0.05 for g, w in zip(got[key], want)),
           f"{got[key]} vs {want}")

    # 4) 视图带偏移时要能加回去
    got2 = rw.fields_from_canvas(anchor, [(x - 50, y - 30, x2 - 50, y2 - 30)
                                          for (x, y, x2, y2) in boxes], 1.0, (50, 30))
    ok("视图偏移会被加回", all(abs(g - w) <= 0.05
                          for key in got2 for g, w in zip(got2[key], got[key])), str(got2))

    # 4b) 缩放不变性：比例是相对「字高 h」的，换窗口大小应当照样对。
    #     这一条是「标定表」的价值所在 —— 只要 h 变、比例不变，字段框就跟着对。
    if REAL_2320.exists():
        src = Image.open(REAL_2320).convert("RGB")
        for sc in (0.7, 0.85):
            im = src.resize((int(src.width * sc), int(src.height * sc)),
                            Image.Resampling.LANCZOS)
            _l, rr = ro.read_screen(im, A.get_engine(),
                                    bosses=["邪魔", "医师", "铁匠", "重装火神R", "古铁"])
            eq(f"缩放 {sc} 行数", len(rr), 6)
            eq(f"缩放 {sc} 等级", [r["level"] for r in rr], [10, 10, 10, 10, 10, 9])
            eq(f"缩放 {sc} 无缺字段", sum(1 for r in rr
                                   if not (r["boss"] and r["level"] and r["damage"] and r["pct"])), 0)

    # 5) 标定后整屏仍然全对（真机帧）
    if REAL_2320.exists():
        img = Image.open(REAL_2320).convert("RGB")
        cal = {"level": got["level"], "boss": got["boss"], "pct": got["pct"]}
        _l, recs = ro.read_screen(img, A.get_engine(),
                                  bosses=["邪魔", "医师", "铁匠", "重装火神R", "古铁"],
                                  fields=cal)
        eq("标定后行数", len(recs), 6)
        eq("标定后等级", [r["level"] for r in recs], [10, 10, 10, 10, 10, 9])
        eq("标定后无缺字段", sum(1 for r in recs
                            if not (r["boss"] and r["level"] and r["damage"] and r["pct"])), 0)


# ---------------------------------------------------------------- T13 导航找字
def test_nav_anchors():
    """导航完全靠「找字」，不写死坐标 —— 所以窗口多大都能点对。

    素材是三张真实截图：联盟页（1920×1200）、联盟页（992×1013，另一个窗口尺寸）、
    突袭页、伤害记录页。这一组用例守的就是"每次窗口都不一样"。
    """
    print("[T13] 导航文字锚点（窗口尺寸无关）")
    import raid_nav as nav
    import raid_ocr as ro
    import alliance_ocr as A
    from PIL import Image

    eng = A.get_engine()
    raid_page_1 = HERE / "测试素材" / "raid" / "导航_联盟页.jpg"
    raid_page_2 = HERE / "测试素材" / "raid" / "联盟页_突袭入口.jpg"
    raid_page_3 = HERE / "测试素材" / "raid" / "联盟页_HARD.jpg"
    boss_page = HERE / "测试素材" / "raid" / "导航_突袭页.jpg"

    for tag, path in (("联盟页 1920×1200", raid_page_1), ("联盟页 992×1013", raid_page_2),
                      ("联盟页 HARD 1920×1151", raid_page_3)):
        if not path.exists():
            ok(f"{tag} 素材存在", False, str(path))
            continue
        img = Image.open(path).convert("RGB")
        toks = ro.tokens(img, eng)
        card = nav.find_text(toks, "联盟突袭", lower_half=True, img_h=img.height)
        eq(f"{tag}：找到一个「联盟突袭」卡片", len(card), 1)
        ok(f"{tag}：卡片在下半屏", bool(card) and (card[0]["y0"] + card[0]["y1"]) / 2 > img.height * 0.45)
        eq(f"{tag}：不在记录页", nav.RaidNav.on_record_page(toks, img), False)

    if boss_page.exists():
        img = Image.open(boss_page).convert("RGB")
        toks = ro.tokens(img, eng)
        rec = nav.find_text(toks, "联盟记录")
        eq("突袭页：找到「联盟记录」按钮", len(rec), 1)
        eq("突袭页：标题里的「联盟突袭」不算卡片",
           len(nav.find_text(toks, "联盟突袭", lower_half=True, img_h=img.height)), 0)
        eq("突袭页：不在记录页", nav.RaidNav.on_record_page(toks, img), False)

    if REAL_2320.exists():
        img = Image.open(REAL_2320).convert("RGB")
        toks = ro.tokens(img, eng)
        eq("记录页：认得出来（免得在记录页又去点导航）",
           nav.RaidNav.on_record_page(toks, img), True)

    # 低分 token 不能当锚点（OCR 认错时宁可停下）
    fake = [{"text": "联盟突袭", "score": 0.55, "x0": 10, "y0": 900, "x1": 60, "y1": 920}]
    eq("低分不采纳", len(nav.find_text(fake, "联盟突袭", lower_half=True, img_h=1000)), 0)
    fake[0]["score"] = 0.99
    eq("高分才采纳", len(nav.find_text(fake, "联盟突袭", lower_half=True, img_h=1000)), 1)
    # 多个候选：取最下面那个（标题在上、卡片在下）
    two = [{"text": "联盟突袭", "score": 0.99, "x0": 10, "y0": 100, "x1": 60, "y1": 120},
           {"text": "联盟突袭", "score": 0.99, "x0": 10, "y0": 900, "x1": 60, "y1": 920}]
    got = nav.find_text(two, "联盟突袭", lower_half=True, img_h=1000)
    eq("多候选只留下半部的那一个", len(got), 1)
    eq("留下的正是卡片", got[0]["y0"], 900)


def main():
    only = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    if only in ("", "core"):
        test_damage()
        test_locate()
        test_splice()
        test_export()
        test_range()
        test_partial_and_span()
        test_boss_set()
        test_vote()
        test_boss_policy()
        test_external_cli()
        test_field_calib()
        test_nav_anchors()
    if only in ("", "ocr"):
        test_screenshot()
        test_roll()
        test_fallback()
        test_badge()
        test_chain()
        test_real_frames()
    print()
    if _fail:
        print(f"失败 {len(_fail)} 项：" + "、".join(_fail))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
