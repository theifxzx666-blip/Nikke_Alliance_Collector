# -*- coding: utf-8 -*-
"""困难模式采集的纯逻辑单测：到底判定、末尾回填。

    .venv\\Scripts\\python.exe hardtest.py

不跑 OCR、不开窗口、不碰游戏。只用假记录验证两件事：
  1. `_all_known` 能分清"列表到底（内容全采过）"和"真的拼不上"；
  2. `_fill_bottom` 只用伤害值匹配，同一个人的多条记录不会串行填错 BOSS。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import hard_client as H                                     # noqa: E402
from raid_hard import boss_norm                             # noqa: E402

fail = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        fail.append(name)


class Fake:
    """只提供 `_fill_bottom` 需要的 say。"""

    def __init__(self):
        self.lines = []

    def say(self, msg, level="INFO"):
        self.lines.append(msg)


def rec(member, boss, damage, pct="50.0%"):
    return {"member": member, "boss": boss, "damage": damage,
            "damage_raw": f"{damage:,}" if damage is not None else "",
            "pct": pct, "conf": 0.9}


# ---------------------------------------------------------------- 到底判定
rows = [rec("小卡卡", "重装火神R", 10186020028), rec("薯KI", "邪魔", 15829188543),
        rec("DORO", "邪魔", 16053352257), rec("凛酱", "重装火神R", 9713247674),
        rec("凛酱", "古铁", 9038483654), rec("凛酱", "", 9767186202)]

# 最后一屏：6 条都已经是采过的（第 6 条这里读到了完整 BOSS）
last = [rec("小卡卡", "重装火神R", 10186020028), rec("薯KI", "邪魔", 15829188543),
        rec("DORO", "邪魔", 16053352257), rec("凛酱", "重装火神R", 9713247674),
        rec("凛酱", "古铁", 9038483654), rec("凛酱", "铁匠", 9767186202)]

check("整屏都已采过 → 判定到底", H.HardClient._all_known(last, rows) is True)

withnew = last + [rec("新来的", "铁匠", 1)]
check("出现没采过的记录 → 不判到底", H.HardClient._all_known(withnew, rows) is False)

check("空输入不判到底", H.HardClient._all_known([], rows) is False)

# 每人 3 刀、带特化刀时**可以重复打同一个 BOSS**（用户 2026-11-07 更正）。
# 所以"成员 + BOSS 都见过"不等于"这一刀采过"——必须按伤害认，否则会提前收工。
repeat_rows = [rec("凛酱", "铁匠", 111), rec("凛酱", "铁匠", 222)]
check("同一人同 BOSS 的另一刀算新记录",
      H.HardClient._all_known([rec("凛酱", "铁匠", 333)], repeat_rows) is False)
check("同一人同 BOSS 且伤害相同才算采过",
      H.HardClient._all_known([rec("凛酱", "铁匠", 222)], repeat_rows) is True)

# 同一人打同一 BOSS 的两刀，伤害不同 → 不是同一行，合并时不许吞掉一条
import raid_core as rc                                        # noqa: E402

check("同人同 BOSS、伤害不同 → 不是同一行",
      rc.same_record(rec("凛酱", "铁匠", 111), rec("凛酱", "铁匠", 222)) is False)
check("同人同 BOSS、伤害相同 → 是同一行",
      rc.same_record(rec("凛酱", "铁匠", 111), rec("凛酱", "铁匠", 111)) is True)
check("不同人、伤害相同 → 不是同一行",
      rc.same_record(rec("DORO", "铁匠", 111), rec("凛酱", "铁匠", 111)) is False)

# ---------------------------------------------------------------- 末尾回填
n = H.HardClient._fill_bottom(Fake(), rows, last)
check("缺 BOSS 的那条被补上", rows[5]["boss"] == "铁匠", f"实得 {rows[5]['boss']!r}")
check("只补了 1 个字段", n == 1, f"实得 {n}")
check("同一人的前两条没被改", rows[3]["boss"] == "重装火神R" and rows[4]["boss"] == "古铁")

# 反向用例：三条同名人、伤害都对不上 → 一个都不许填
rows2 = [rec("凛酱", "", 111), rec("凛酱", "", 222)]
recs2 = [rec("凛酱", "铁匠", 888), rec("凛酱", "古铁", 999)]
n2 = H.HardClient._fill_bottom(Fake(), rows2, recs2)
check("伤害对不上就不填（不靠成员名猜）",
      n2 == 0 and all(not r["boss"] for r in rows2), f"补了 {n2}")

# 伤害缺失时才允许"成员名在屏内唯一"这个弱条件
rows3 = [rec("独苗", "", None)]
recs3 = [rec("独苗", "医师", 777)]
n3 = H.HardClient._fill_bottom(Fake(), rows3, recs3)
check("伤害缺失时用唯一成员名兜底", n3 == 1 and rows3[0]["boss"] == "医师")

# ---------------------------------------------------------------- BOSS 名归一
# 本期 5 个 BOSS 首字互不相同，这是"首字定人"能成立的前提
BOSSES = ["邪魔", "医师", "铁匠", "重装火神R", "古铁"]
assert len({b[0] for b in BOSSES}) == len(BOSSES)

cases = [
    ("铁E", "铁匠", "末尾汉字被读成字母（10:13 真机）"),
    ("惠医师", "医师", "左边图标被吃进名字"),
    ("失匠", "铁匠", "首字读歪、后面全对"),
    ("重装火神", "重装火神R", "少了尾字母"),
    ("古铁", "古铁", "本来就对，不许动"),
    ("铁匠", "铁匠", "本来就对，不许动"),
    ("完全无关", "完全无关", "对不上就原样留着，不硬凑"),
]
for raw, want, why in cases:
    got = boss_norm(raw, BOSSES)
    check(f"BOSS 归一 {raw!r} → {want!r}（{why}）", got == want, f"实得 {got!r}")

# ---------------------------------------------------------------- 引擎抽取后的自检
# 背景：把采集代码从 Tk 类里搬到 raid_capture 时漏了 ctypes / datetime 的 import，
# 只有真机跑到那一步才会炸（12:01 那轮就是这么挂的）。这里用桩替换鼠标注入，
# 把 _drag_raw 的函数体完整跑一遍 —— 不产生任何真实点击。
import types                                                   # noqa: E402

import raid_capture


class _StubUser32:
    def __init__(self):
        self.moves = 0
        self.events = 0

    def SetCursorPos(self, x, y):
        self.moves += 1
        return 1

    def mouse_event(self, *a):
        self.events += 1


_orig = raid_capture.ctypes
_stub = _StubUser32()
raid_capture.ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(user32=_stub))
try:
    eng = raid_capture.CaptureEngine()
    eng._drag_raw(100, 200, 260, step_px=8, gap=0.001)
    check("_drag_raw 能跑通（鼠标注入已用桩替换）",
          _stub.moves >= 8 and _stub.events == 2,
          f"moves={_stub.moves} events={_stub.events}")
finally:
    raid_capture.ctypes = _orig

check("拖动参数固定为 3px/28ms", (raid_capture.CaptureEngine.DRAG_STEP,
                                round(raid_capture.CaptureEngine.DRAG_GAP, 3)) == (3, 0.028))



# ---------------------------------------------------------------- 重复行合并
# 真实数据（12:34 那轮导出的 CSV）：DORO 邪魔 / DORO 古铁 / NING 铁匠 各出现两次，
# 第二次是位移多估后把已采过的行又追加了一遍。伤害值 + 成员名相同 → 只留一条。
class _Stub:
    def __init__(self):
        self.msg = []

    def say(self, m, level="INFO"):
        self.msg.append(m)


_base = [rec("DORO", "邪魔", 16053352257), rec("DORO", "古铁", 11394763884)]
_add = [rec("DORO", "邪魔", 16053352257), rec("DORO", "古铁", 11394763884)]
_out, _n = raid_capture.CaptureEngine._dedupe(_base + _add)
check("同一伤害+同一成员只留一条", len(_out) == 2 and _n == 2, f"留下 {len(_out)} 合并 {_n}")

# 只有伤害相同、成员不同 → 不是同一条（普通模式一击必杀，多人伤害可能完全相同）
_out2, _n2 = raid_capture.CaptureEngine._dedupe(
    [rec("A", "古铁", 2158171750), rec("B", "古铁", 2158171750)])
check("同伤害不同成员不合并", len(_out2) == 2 and _n2 == 0, f"留下 {len(_out2)}")

# 已有的那条缺 BOSS，新读的有 → 补字段，仍然不新增
_out3, _n3 = raid_capture.CaptureEngine._dedupe(
    [rec("NING", "", 11877507206, pct=""), rec("NING", "铁匠", 11877507206, pct="42.5%")])
check("重复行顺手补齐缺字段", len(_out3) == 1 and _out3[0]["boss"] == "铁匠"
      and _out3[0]["pct"] == "42.5%", str(_out3))

# 事后撞车（12:43 那轮的真实形态）：早先一行读歪了伤害，patch 改对后才和后面那条一样。
# 只在"追加时比一次"是发现不了的，整表去重才行。
_late = [rec("班长", "铁匠", 8018744126), rec("九月八", "医师", 8402382750),
         rec("班长", "铁匠", 8018744126)]
_out4, _n4 = raid_capture.CaptureEngine._dedupe(_late)
check("事后撞车的重复也能合并", len(_out4) == 2 and _n4 == 1, f"留下 {len(_out4)}")

# 15:30 那轮的真实形态：同一行两种写法「蕭白/萧白」（繁简差一字，ratio 恰 0.5），
# 旧判据 0.6 挡住 → 重复行残留 + 人数虚报 1。超短名放宽到 0.5 后应合并。
_xiao = [rec("蕭白", "医师", 12872129022, pct="54.2%"),
         rec("萧白", "医师", 12872129022, pct="54.2%")]
_out5, _n5 = raid_capture.CaptureEngine._dedupe(_xiao)
check("蕭白/萧白 同伤害繁简两种读法合并为一条",
      len(_out5) == 1 and _n5 == 1, f"留下 {len(_out5)} 合并 {_n5}")

# 已知取舍：「大白/萧白」字面上与「蕭白/萧白」无法区分（都是两字差一字 0.5）。
# 放宽后它们同伤害也会合并 —— 但这条判据只用于困难模式（CaptureEngine 仅
# hard_client 使用），11 位伤害全等在两个真人之间不会发生（docstring 论证过
# 连同一人两次出手都几乎不可能），所以护栏让位给消重。
_diff = [rec("大白", "医师", 12872129022), rec("萧白", "医师", 12872129022)]
_out6, _n6 = raid_capture.CaptureEngine._dedupe(_diff)
check("字面同形的短名同伤害按同读法合并（困难模式取舍）",
      len(_out6) == 1, f"留下 {len(_out6)} 合并 {_n6}")

# 21:36 那轮的真实形态：同一条记录被两屏读成 7,057,526,552 / 7,057,526,352（差 200），
# 伤害不等 → 旧判据认不出重复，同一个人多出一条。血量百分比相同是关键判据：
# 真打两刀时 BOSS 已经掉血，百分比不可能一样。
_apx = [rec("村里响当当", "重装火神R", 7057526552, pct="34.7%"),
        rec("村里响当当", "重装火神R", 7057526352, pct="34.7%")]
_apx[0]["conf"], _apx[1]["conf"] = 0.885, 0.734
_out7, _n7 = raid_capture.CaptureEngine._dedupe(_apx)
check("同血量同成员、伤害读错一位 → 合并为一条",
      len(_out7) == 1 and _n7 == 1, f"留下 {len(_out7)} 合并 {_n7}")
check("合并后留置信度高的那个读数",
      _out7 and _out7[0]["damage"] == 7057526552 and _out7[0]["conf"] == 0.885,
      str(_out7[0] if _out7 else {}))

# 反例：血量不同就是真两刀，绝不能合并
_out8, _n8 = raid_capture.CaptureEngine._dedupe(
    [rec("甲", "古铁", 7057526552, pct="34.7%"),
     rec("甲", "古铁", 7057526352, pct="30.1%")])
check("血量不同 → 不同刀，不合并", len(_out8) == 2 and _n8 == 0, f"留下 {len(_out8)}")

# 反例：差 0.8% 不是"读错一位"，是两次不同的输出
_out9, _n9 = raid_capture.CaptureEngine._dedupe(
    [rec("甲", "古铁", 7057526552, pct="34.7%"),
     rec("甲", "古铁", 7000000000, pct="34.7%")])
check("差 0.8% → 不按读错处理", len(_out9) == 2 and _n9 == 0, f"留下 {len(_out9)}")

# v1.36：血量**一端缺失**时，靠伤害近似判重。
# 实测 23:01 MAO 那条整行读歪 —— 伤害 `5 110 350 001`（串里还多空格）、血量空，
# 和紧邻的 `5,113,250,881 / 0.0%` 是同一条。旧判据要求两端血量都非空才比伤害。
_out10, _n10 = raid_capture.CaptureEngine._dedupe(
    [rec("MAO", "古铁", 5113250881, pct="0.0%"), rec("MAO", "古铁", 5110350001, pct="")])
check("血量一端缺失 + 伤害差 0.06% → 同一条",
      len(_out10) == 1 and _n10 == 1, f"留下 {len(_out10)} 合并 {_n10}")

# 反例：两端血量都缺、伤害差 5% —— 没有任何可靠依据，不能合
_out11, _n11 = raid_capture.CaptureEngine._dedupe(
    [rec("甲", "古铁", 1000000, pct=""), rec("甲", "古铁", 950000, pct="")])
check("两端都缺血量、伤害差 5% → 不合并", len(_out11) == 2 and _n11 == 0, f"留下 {len(_out11)}")

# ---------------- v1.17：导航点进来的页面不该再回顶
import threading                                          # noqa: E402
import alliance_ocr as A                                   # noqa: E402
import raid_capture as RC                                  # noqa: E402
import raid_hard as RH                                     # noqa: E402


class _FakeCap(RC.CaptureEngine):
    """只跑 capture 的前半段（认一帧 + 决定要不要回顶），不进滚动循环。"""

    def __init__(self):
        self._stop = threading.Event()
        self._roster = {}
        self._bosses = []
        self.calls = []
        self.logs = []

    def say(self, m, *a):
        self.logs.append(m)

    def _crop(self, raw):
        return raw

    def _scroll_to_top(self, region, pitch):
        self.calls.append("top")

    def emit_done(self):
        pass


class _Loc:
    pitch = 85
    box = None
    cards = []


_orig = (A.get_engine, A.grab, RH.read_hard)
A.get_engine = lambda: object()
A.grab = lambda region: "img"
RH.read_hard = lambda img, eng, roster, bosses: ([], _Loc())
try:
    cap_nav = _FakeCap()
    cap_nav.capture((0, 0, 100, 100), from_nav=True)
    check("导航刚点进来的页面：跳过回顶", cap_nav.calls == [], str(cap_nav.calls))
    check("跳过时日志有说明",
          any("跳过回顶" in m for m in cap_nav.logs), str(cap_nav.logs[:4]))

    cap_user = _FakeCap()
    cap_user.capture((0, 0, 100, 100))          # 默认 False = 用户自己开页
    check("用户自己停在记录页：仍然回顶",
          cap_user.calls == ["top"], str(cap_user.calls))
    check("回顶时日志写「先拉回列表顶部」",
          any("先拉回列表顶部" in m for m in cap_user.logs), str(cap_user.logs[:4]))
finally:
    A.get_engine, A.grab, RH.read_hard = _orig

print()
if fail:
    print(f"失败 {len(fail)} 项：" + "、".join(fail))
    sys.exit(1)
print("全部通过")