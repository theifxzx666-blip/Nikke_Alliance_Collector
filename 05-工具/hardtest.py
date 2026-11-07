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
    # v1.53：认不出就留空。原样保留 `INNNA` 这种只会让 CSV 里出现一个不存在的
    # BOSS，复核也标不出来；留空会被判「缺 BOSS」，人一看就知道要补。
    ("完全无关", "", "认不出就留空（复核会标缺 BOSS）"),
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
    # v1.44：导航进入**不再跳过回顶** —— 页面在第一行 ≠ 第一行在框里，
    # 顶部日期栏压着第一条卡片的上半截，实测那条从头到尾没进过 CSV。
    check("导航刚点进来的页面：也要回顶", cap_nav.calls != [], str(cap_nav.calls))
    check("回顶时日志说明原因",
          any("防止第一行被框上沿切掉" in m for m in cap_nav.logs),
          str(cap_nav.logs[:4]))

    cap_user = _FakeCap()
    cap_user.capture((0, 0, 100, 100))          # 默认 False = 用户自己开页
    check("用户自己停在记录页：仍然回顶",
          cap_user.calls == ["top"], str(cap_user.calls))
    check("回顶时日志写「先拉回列表顶部」",
          any("先拉回列表顶部" in m for m in cap_user.logs), str(cap_user.logs[:4]))
finally:
    A.get_engine, A.grab, RH.read_hard = _orig

# ---------------- v1.54 成员额度锚定 ----------------
#
# 为什么单独测：它会在**收工时改数据**，改错了比不改更糟（复核再也标不出来）。
# 四个条件任何一个失效都必须拒绝 —— 所以每个条件都要各有一个反例。
from raid_capture import CaptureEngine as CE


class _Anchor(CE):
    def __init__(self, names):
        self._roster = {"names": {n: {} for n in names}}
        self._bosses = []


def _rec(member, boss, dmg, pct, conf=0.9):
    return {"member": member, "boss": boss, "damage": dmg,
            "damage_raw": str(dmg), "pct": pct, "conf": conf}


_NAMES = ["小卡卡", "博麗霊夢", "PPORANGE", "马三一"]
_eng = _Anchor(_NAMES)

# 1) 残读 + 那个人还差一刀 → 归位
_rows = [_rec("小卡卡", "医师", 100, "50.0%"), _rec("小卡卡", "铁匠", 200, "40.0%"),
         _rec("小卡ト", "邪魔", 300, "30.0%")]
_n, _m = _eng._anchor_members(_rows)
check("残读归位：小卡ト→小卡卡", _n == 1 and _rows[2]["member"] == "小卡卡", _m)
check("归位记进 member_note", "额度锚定" in (_rows[2].get("member_note") or ""),
      str(_rows[2].get("member_note")))

# 2) 那个人已经满 3 刀，且这条**不是重复** → 拒绝改名（否则变成 4 刀）
_rows = [_rec("小卡卡", "医师", 100, "50.0%"), _rec("小卡卡", "铁匠", 200, "40.0%"),
         _rec("小卡卡", "邪魔", 300, "30.0%"), _rec("小卡ト", "古铁", 400, "20.0%")]
_n, _m = _eng._anchor_members(_rows)
check("满 3 刀就拒绝改名", _n == 0 and _rows[3]["member"] == "小卡ト", _m)
check("拒绝时说清是额度问题", "不归" in _m, _m)

# 3) 满 3 刀但这条**是其中一刀的重复** → 允许改名（去重会吃掉，仍是 3）
_rows = [_rec("小卡卡", "医师", 100, "50.0%"), _rec("小卡卡", "铁匠", 200, "40.0%"),
         _rec("小卡卡", "邪魔", 300, "30.0%"),
         _rec("小卡ト", "邪魔", 300, "30.0%")]
_n, _m = _eng._anchor_members(_rows)
check("重复行改名后仍是 3 刀 → 允许", _n == 1 and _rows[3]["member"] == "小卡卡", _m)
_after, _mm = CE._dedupe(_rows)
check("改完之后去重只剩 3 条", len(_after) == 3 and _mm == 1, f"{len(_after)} 条")

# 4) 等长但相似度不够（博留雪墓 0.25）→ 不改
_rows = [_rec("博留雪墓", "医师", 100, "50.0%")]
_n, _m = _eng._anchor_members(_rows)
check("相似度不到 0.55 不改字", _n == 0 and _rows[0]["member"] == "博留雪墓", _m)
check("但会给建议", "≈" in _m, _m)

# 5) 两级门槛：等长 0.55 / 差一字 0.65。FrURAINGE↔PPORANGE 是 0.59 差一字 → 不改
_rows = [_rec("FrURAINGE", "医师", 100, "50.0%")]
_n, _m = _eng._anchor_members(_rows)
check("差一字但相似度只 0.59 → 不改", _n == 0, _m)

# 6) 两字名不做模糊归档（大白/萧白 是两个真人）
_rows = [_rec("萧白", "医师", 100, "50.0%"), _rec("大白", "医师", 200, "40.0%")]
_n, _m = _eng._anchor_members(_rows)
check("两字名不归位", _n == 0 and _rows[1]["member"] == "大白", _m)

# 7) 有多个候选 → 不敢改（库里真有近名成员时，猜哪个都错）
_eng2 = _Anchor(["小卡卡", "小卡七", "马三一"])
_rows = [_rec("小卡ト", "医师", 100, "50.0%")]
_n, _m = _eng2._anchor_members(_rows)
check("多个候选就拒绝", _n == 0 and _rows[0]["member"] == "小卡ト", _m)
check("拒绝理由写「不敢改」", "不敢改" in _m, _m)

# 8) 库为空 / 全部在库 → 直接返回，不动数据
_eng3 = _Anchor([])
_rows = [_rec("谁的", "医师", 100, "50.0%")]
check("没有库就什么都不做", _eng3._anchor_members(_rows) == (0, ""), "")

# 9) 两个底层判据各自的边界
check("_name_sim 不看书写系统（小卡ト 0.67）",
      round(CE._name_sim("小卡ト", "小卡卡"), 2) == 0.67,
      str(round(CE._name_sim("小卡ト", "小卡卡"), 2)))
check("_quota_after 会先去重",
      _Anchor(_NAMES)._quota_after(
          [_rec("小卡卡", "医师", 100, "50.0%"), _rec("小卡卡", "医师", 100, "50.0%")],
          "小卡卡") == 1, "")

# ---------------- v1.56 滚动采集：位置就是身份 ----------------
#
# 这一段仿真整条 `capture`（12 条 / 每屏 6 条 / 每次滚 2 条），故意制造三件事：
#   · 位移读数每次都偏 8px  —— 考 `_calibrate` 能不能把误差当屏吃掉
#   · 某屏把 `人4` 读成 `人4x` —— 同一个位置的两份观测必须合成一条
#   · 某屏少读一条           —— 那一屏缺的位置不能把别人挤歪
# 断言：最终 12 条、顺序完全正确、每条 `_gy` 都落在真值 ±1 行内。
import threading

_ALL = [{"member": f"人{i}", "boss": "古铁", "damage": 1000 + i,
         "damage_raw": str(1000 + i), "pct": f"{i}%", "conf": 0.9} for i in range(12)]
_PITCH = 100.0
_STEP = 200.0                        # 每次滚 2 行


class _Img:
    def __init__(self, screen):
        self.screen = screen

    def crop(self, box):
        return self


class _Loc:
    cards = [1] * 6
    box = (0, 0, 600, 600)

    def __init__(self):
        self.pitch = _PITCH


class _Scroll(CE):
    """假宿主：只实现 capture 用到的那几个出口。"""

    def __init__(self):
        self._stop = threading.Event()
        self._roster = None
        self._bosses = []
        self.last_pitch = _PITCH
        self.screen = 0
        self.said = []
        self.got = []
        self._offset = 0.0

    def _crop(self, img):
        return img

    def say(self, msg, level="INFO"):
        self.said.append(msg)

    def emit_rows(self, rows):
        self.got = rows            # 抓最后一次快照（capture 传的是同一个 list）

    def emit_done(self):
        pass

    def _scroll_to_top(self, region, pitch):
        return None                # 仿真里画面本来就在顶部

    def _drag_list(self, region, dist, up=False):
        self.screen += 1
        return True

    def _measure(self, region, a, b):
        # **恒为 0**：复刻 04:37 那轮的现场 —— 画面明明动了，像素相关量成 0。
        # 现在定位改用内容反算，所以这个值只影响"预测下一屏起算点"，不影响顺序。
        return (0, 0.9)


def _read(img, eng, roster=None, bosses=None):
    off = img.screen * 2
    seg = _ALL[off:off + 6]
    out = []
    for j, r in enumerate(seg):
        d = dict(r)
        d["_y"] = int(100 + j * _PITCH)
        # 第 2 屏把 `人4` 读成 `人4x`：同一个位置、同一个 BOSS、同一伤害
        if img.screen == 2 and d["member"] == "人4":
            d["member"] = "人4x"
            d["conf"] = 0.55
        # 第 3 屏少读一条（模拟边缘被裁）
        if img.screen == 3 and d["member"] == "人7":
            continue
        out.append(d)
    return out, _Loc()


_orig = (A.grab, A.wait_settled, RH.read_hard)
try:
    _sc = _Scroll()
    A.grab = lambda region: _Img(_sc.screen)
    A.wait_settled = lambda region, raw: (_Img(_sc.screen), 0.0, 0)
    RH.read_hard = _read
    _sc.capture((0, 0, 100, 100))
finally:
    A.grab, A.wait_settled, RH.read_hard = _orig

_rows = _sc.got
_order = [r["member"] for r in _rows]
check("滚动采集：条数正好 12（读歪的那条合成一条）", len(_rows) == 12, f"{len(_rows)} 条")
check("滚动采集：顺序完全正确", _order == [f"人{i}" for i in range(12)], str(_order))
check("滚动采集：每条位置落在真值 1 行内",
      all(abs(int(r["_gy"]) - (100 + int(r["member"][1:]) * _PITCH)) <= _PITCH
          for r in _rows),
      str([(r["member"], r["_gy"]) for r in _rows[:4]]))
check("读歪的那份观测被同一位置的好读数盖掉",
      all((r.get("conf") or 0) >= 0.9 for r in _rows),
      str([(r["member"], r["conf"]) for r in _rows if (r.get("conf") or 0) < 0.9]))

# --- 内容定位：像素位移完全不可用时，坐标仍然必须准
_loc_rows = []
_eng2 = _Scroll()
for _k in range(4):
    _recs, _l = _read(_Img(_k), None)
    if _loc_rows:
        _eng2._offset, _n = _eng2._offset_from_content(_loc_rows, _recs,
                                                      _eng2._offset, _PITCH)
    for _r in _recs:
        _r["_gy"] = int(_eng2._offset + int(_r["_y"]))
    _eng2._absorb_all(_loc_rows, _recs, _PITCH)
    _eng2._offset += _STEP          # 预测值；下一屏会被内容反算覆盖
check("内容反算：四屏下来坐标仍落在真值上",
      all(abs(int(r["_gy"]) - (100 + int(r["member"][1:]) * _PITCH)) <= 1
          for r in _loc_rows),
      str([(r["member"], r["_gy"]) for r in _loc_rows[:5]]))
check("内容反算：条数不重复（4 屏覆盖 12 条）", len(_loc_rows) == 12, f"{len(_loc_rows)} 条")

# --- 多份观测投票
_vo = {"member": "空木", "boss": "铁匠", "pct": "50.0%",
       "damage": 100, "damage_raw": "100", "conf": 0.90}
CE._merge_obs(_vo, {"member": "空本", "boss": "铁匠", "pct": "50.0%",
                    "damage": 100, "damage_raw": "100", "conf": 0.95})
CE._merge_obs(_vo, {"member": "空木", "boss": "铁匠", "pct": "50.0%",
                    "damage": 101, "damage_raw": "101", "conf": 0.99})
check("投票：名字 2 票 空木 vs 1 票 空本 → 空木", _vo["member"] == "空木", _vo["member"])
check("投票：伤害 2 票 100 vs 1 票 101 → 100", _vo["damage"] == 100, str(_vo["damage"]))
check("投票：damage_raw 跟着赢家走", _vo["damage_raw"] == "100", _vo["damage_raw"])

_vo2 = {"member": "甲", "boss": "", "pct": "", "damage": 5,
        "damage_raw": "5", "conf": 0.8}
CE._merge_obs(_vo2, {"member": "甲", "boss": "铁匠", "pct": "", "damage": 5,
                     "damage_raw": "5", "conf": 0.7})
check("投票：缺字段不投票（空的不投成多数）", _vo2["boss"] == "铁匠", str(_vo2))

_vo3 = {"member": "甲", "boss": "铁匠", "pct": "", "damage": 5,
        "damage_raw": "5", "conf": 0.80}
CE._merge_obs(_vo3, {"member": "甲", "boss": "医师", "pct": "", "damage": 5,
                     "damage_raw": "5", "conf": 0.95})
check("投票：平票看置信度", _vo3["boss"] == "医师", str(_vo3))

print()
if fail:
    print(f"失败 {len(fail)} 项：" + "、".join(fail))
    sys.exit(1)
print("全部通过")