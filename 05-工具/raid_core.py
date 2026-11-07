# -*- coding: utf-8 -*-
"""联盟突袭 · 伤害记录解析内核

只做"把 OCR 结果变成记录"这一件事：不碰 GUI、不加载模型、不截图。
这样所有判据都能在 `raidtest.py` 里用真实截图和合成数据回放，改一行跑一次。

数据形态（一屏）：

    [LEVEL 10] HANSON          2,158,171,750   [Q]
               ⚔ 古铁            0.0%
    [LEVEL 10] HANSON          1,538,157,240   [Q]
               ⚔ 重装火神R        0.0%

一行 = 一次出击记录：成员 / 打的是哪个 BOSS / BOSS 等级 / 伤害 / 百分比。

几个刻意的取舍（都有实测依据，别随手改）：

1. **BOSS 名必须单独抠图重认**。全屏检测（det）会把它整条漏掉——实测 6 行里
   漏 3 行（古铁 / 铁匠 / 医师），因为白字压在深色卡面上、左边还挨着 BOSS 小图标。
   而按卡片 ROI 只跑识别（use_det=False）6/6 全中，置信度 0.98~1.00。
   所以流程是：det 只负责"找行"，字段一律 ROI 重认。

2. **伤害数字要做格式校验**。实测同一块 ROI，up=1 读对、up=2/3/4 反而读成
   `2,158,17,750`（少一位、分组变 2 位），置信度却高达 0.997 —— 光看置信度
   会被骗。所以 `normalize_damage()` 只认"首位 1~3 位、其后每组恰好 3 位"，
   不合格就换倍率重读，仍不合格标存疑，绝不硬凑一个数。

3. **跨屏合并只认位置证据**。同一成员对同一个 BOSS 打出同一个伤害是完全正常的
   （实测 HANSON 两次都是 2,158,171,750），所以"内容相同"不能当去重判据，
   必须靠像素位移算出"这一屏往上走了几行"，再用重叠区做一次内容核对。
   位移量不准、或重叠区对不上，就停下来报错，不猜。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

# 伤害：首位 1~3 位，其后每组恰好 3 位（1,234 / 12,345,678 / 1,234,567,890）
DAMAGE_OK = re.compile(r"^\d{1,3}(?:,\d{3})+$")
# 宽容版：只要"去掉逗号后全是数字且 ≥6 位"就算候选，交给 normalize 判定
DAMAGE_CAND = re.compile(r"^[\d,\s]{6,}$")
LEVEL_RE = re.compile(r"LEVEL\s*([0-9]{1,2})", re.I)
# 匿名成员：昵称被游戏打成 `******` / `•••`（v1.32）
ANON_RE = re.compile(r"^[*＊✱✳•·・]{2,}$")
DAY_RE = re.compile(r"^第\s*([0-9]{1,2})\s*天$")
PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")

# 明显不是昵称的 UI 词（含它们就跳过，避免把标签当成员名）
UI_WORDS = ("联盟记录", "BOSS", "今天", "搜索", "排名", "伤害", "剩余", "回合")


def _cy(t):
    return (t["y0"] + t["y1"]) / 2.0


def _cx(t):
    return (t["x0"] + t["x1"]) / 2.0


def _h(t):
    return max(1.0, t["y1"] - t["y0"])


def norm_text(s: str) -> str:
    """OCR 文本归一：去空白、全角转半角、去掉逗号空格差异。"""
    s = (s or "").strip()
    s = s.replace("\u3000", " ").replace("，", ",").replace("．", ".")
    return re.sub(r"\s+", " ", s)


def normalize_damage(text: str):
    """把 OCR 出的伤害串判成整数。返回 (值|None, 说明)。

    只接受两种形态：
      · 标准千分位：首位 1~3 位，其余每组恰好 3 位
      · 纯数字且 ≥7 位（OCR 把逗号全丢了，但位数够，数值唯一）
    `2,158,17,750` 这种（实测真实会读出来）一律不接受——它缺了一位，
    补哪一位都是猜，宁可标存疑让用户看一眼。
    少于 6 位的一律不当伤害：这个界面上出现的短数字是等级/序号，不是伤害。
    """
    t = norm_text(text).replace(" ", "")
    if not t:
        return None, "空"
    if not DAMAGE_CAND.match(t):
        return None, "非数字或位数不足"
    if t.isdigit():
        return (int(t), "") if len(t) >= 7 else (None, "位数不足")
    if not DAMAGE_OK.match(t):
        return None, "分组异常"
    try:
        return int(t.replace(",", "")), ""
    except ValueError:
        return None, "转换失败"


def fmt_damage(v) -> str:
    return f"{int(v):,}" if isinstance(v, int) else ""


# 字模最容易混的几个：把字母当数字用（只作用在 "LEVEL" 之后那段）
_LV_DIGIT = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "|": "1",
             "B": "8", "Z": "2", "G": "6", "Y": "9"}


def parse_level(text: str):
    """从 "LEVEL 10" 这类串里取出 BOSS 等级。

    真机的徽标只有 85×45 px，字高约 14px，模型经常把 `1` 和 `0` 粘成一个字形，
    读出来是 `IEVEL 1O` / `LEVEL 1O0` / `LEVEL IO` 这些。所以清洗分两步：

      1. **先整段剥掉开头的 "LEVEL"**（可能被读成 IEVEL/LEVEl/LEVE1），
         只留下数字那段 —— 直接在整串上做字母→数字映射会把 LEVEL 自己变成
         数字（L→1），这是踩过的坑；
      2. 再对剩下的字符做 O/D/Q→0、I/L/|→1、B→8、Z→2、G→6，只保留数字，
         取前两位（`1O0` → 100 → `10`，末尾那个 0 是噪声）。

    `S`（`LEVEL S` 这种）**故意不映射**：它既可能是 5 也可能是 9，猜不得，
    宁可返回 None 交给上层标记存疑。
    """
    t = norm_text(text).upper().replace(" ", "")
    if not t:
        return None
    # 等级不会带小数点和千分位。带分隔符的串一律是徽标被切歪后的残形：
    # 实测 `3E.3` / `.3` / `50.3` 都在真机出现过，兜底剥离会把 `.3` 洗成 `3`、
    # 把 `3E.3` 洗成 `33`，然后在投票里压过唯一正确的 `8`。
    if "." in t or "," in t:
        return None
    # 剥掉开头的 LEVEL（可能就是 IEVEL/LEVEl 这类 L↔I 的混淆），必须精确 5 个字符：
    # 用 ^[A-Z]{4,6} 贪婪匹配会把 `LEVEL IO` 里的 `I` 一起吃掉（→ 只剩 `O` → 0）。
    m = re.match(r"^[LI1|][E3]V[E3][LI1|]", t)      # 完整的 LEVEL（含 L↔I、E↔3 混淆）
    if m:
        body = t[m.end():]
    else:
        # 兜底：把**第一个数字之前的一切**都当词尾剥掉。
        # 小窗口下 LEVEL 会被读成各种残形：`L9`、`IF9`、`LENE.9`、`LEVE.9`
        # （0.85 缩放实测），不剥就会把 9 读成 19 —— 这些前缀一定是 "LEVEL" 的残骸，
        # 不可能是数字的一部分。
        body = re.sub(r"^[^0-9]*", "", t)
    digits = ""
    for ch in body:
        if ch.isdigit():
            digits += ch
        elif ch in _LV_DIGIT:
            digits += _LV_DIGIT[ch]
        else:
            # 出现既不是数字、也不是已知混淆字形的字符 → 这根本不是等级串。
            # 实测 `3E.3`（LEVEL 徽标被切歪后把笔画读成 3E.3）原先会被静默跳过
            # 字母和点号、只剩 `33`，还在投票里以两票压过唯一正确的 `8`。
            # 等级不会带小数点或千分位，一律判无效。
            return None
    digits = digits[:2]
    if not digits:
        return None
    v = int(digits)
    # BOSS 等级只有 1~10（8/9/10 是常见档位）。读到 11~19 时，十位那个 `1`
    # 几乎都是徽标开头 `L` 被粘进了数字：`LEVEL 9`→`19`、`LEVEL 8`→`18`、
    # `LEVEL 1`→`11`。2026-11-06 两轮真机这三类全出现过（含 f 族 0.58 那一路
    # 稳定读出 18 的坑），统一在解析层剥掉，比逐个调裁切偏移干净。
    if 11 <= v <= 19:
        v -= 10
    return v if 1 <= v <= 99 else None


def is_member_text(s: str) -> bool:
    """能不能当成员名用。昵称允许中英日韩 + 数字 + 少量符号，2~14 字。

    匿名成员的昵称被游戏打成 `******`（v1.32）：没有中文也没有字母，后面
    "至少一个可识别字符"那道关会把它判成噪声，整条记录跟着被丢 ——
    伤害记录里少一个人，名单里人数也对不上。所以先放它过。
    """
    raw = str(s or "").strip()
    if ANON_RE.match(raw):
        return True
    t = norm_text(s)
    if len(t) < 2 or len(t) > 14:
        return False
    if any(w in t for w in UI_WORDS):
        return False
    if LEVEL_RE.search(t) or DAY_RE.match(t) or PCT_RE.match(t):
        return False
    if normalize_damage(t)[0] is not None:
        return False
    if not re.search(r"[\u4e00-\u9fffA-Za-z\u3040-\u30ff\uac00-\ud7af]", t):
        return False
    return True


# 一张记录卡里五个字段的相对位置（以「用户圈出来的那张卡」为 100%）。
# 数值来自真机截图实测框：等级徽标 / 成员名 / BOSS 名 / 伤害 / 百分比。
# 存成比例而不是像素——换分辨率只要重画一次框，其余自动换算。
CARD_FRACTIONS = {
    "level":  (0.000, 0.020, 0.175, 0.460),
    "member": (0.165, 0.220, 0.400, 0.610),
    "boss":   (0.150, 0.620, 0.520, 0.970),
    "damage": (0.750, 0.200, 1.000, 0.680),
    "pct":    (0.850, 0.620, 1.000, 0.970),
}


def card_fields(card_rect):
    """把「一张卡的框」切成五个字段框。card_rect = (x0, y0, x1, y1)。"""
    if not card_rect:
        return {}
    x0, y0, x1, y1 = [float(v) for v in card_rect]
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    return {k: (x0 + a * w, y0 + b * h, x0 + c * w, y0 + d * h)
            for k, (a, b, c, d) in CARD_FRACTIONS.items()}


class Locate:
    """一屏的定位结果：行距、日标记、每张卡片的框与已读字段。"""

    def __init__(self):
        self.pitch = 0.0
        self.days = []          # [(y, 天)]
        self.cards = []         # [dict]
        self.damage_raw = []    # 没通过格式校验的原始串，用于日志
        self.notes = []

    @property
    def box(self):
        """所有卡片的外接框（滚动时用这块区域，别滚到标题栏上）。"""
        # 没有成员名的行（单行兜底/识别失败）不参与外接框计算，否则这里会 None 下标崩
        rows = [c for c in self.cards if c.get("member_box")]
        if not rows:
            return None
        xs0 = [c["member_box"][0] for c in rows] + [c["level_box"][0] for c in rows if c.get("level_box")]
        xs1 = [c["damage_box"][2] for c in rows if c.get("damage_box")] + \
              [c["pct_box"][2] for c in rows if c.get("pct_box")]
        ys0 = [c["member_box"][1] for c in rows]
        ys1 = [max(c["boss_roi"][3] if c.get("boss_roi") else 0,
                   c["pct_box"][3] if c.get("pct_box") else c["member_box"][3]) for c in rows]
        if not xs0 or not xs1:
            return None
        return (int(min(xs0)), int(min(ys0)), int(max(xs1)), int(max(ys1)))

    def rows(self):
        return len(self.cards)


# 字段框的默认值：全部以「成员名框」为锚，单位 = 成员名字高 h。
#   格式 (dx0, dy0, dx1, dy1)，相对成员名框的左上角 (x0, y0)。
# 这些数是 2320×1305 真机帧逐行量出来的；用户可以在窗口里【标定字段】重画一套存进预设，
# 换分辨率/换 UI 缩放时不用改代码 —— 这就是照阿卡「素材框」那套做的本地版。
DEFAULT_FIELDS = {
    "level": (-3.15, -0.60, -0.05, 1.05),   # 红色 LEVEL 徽标（要紧，宽了会带进上一行）
    "boss": (-0.21, 1.07, 5.80, 2.80),      # BOSS 名（可以松，里面有墨迹投影自动定位文字行）
    "pct": (10.80, 1.32, 14.60, 3.46),      # 百分比（在伤害数字下面）
}


def field_box(m_box, rel):
    """把 (dx0,dy0,dx1,dy1)×h 的偏移换算成像素框。"""
    x0, y0, x1, y1 = m_box
    h = max(1.0, y1 - y0)
    return (int(x0 + rel[0] * h), int(y0 + rel[1] * h),
            int(x0 + rel[2] * h), int(y0 + rel[3] * h))


def locate(tokens, fields=None) -> Locate:
    """从一屏的 OCR token 里找出所有伤害记录行。

    token 形态：{"x0","y0","x1","y1","text","score"}（图片坐标）。
    BOSS 名这里**不读**——det 会漏，留给调用方按 boss_roi 抠图重认。

    fields：字段标定表（单位 = 成员名字高 h），键为 level/boss/pct。
    传 None 或某个键缺失 → 用 DEFAULT_FIELDS 里对应的默认值（与历史行为一致）。
    """
    res = Locate()
    f = dict(fields or {})
    toks = [dict(t) for t in tokens if norm_text(t.get("text"))]

    # 日标记（第 1 天 / 第2天）
    for t in toks:
        m = DAY_RE.match(norm_text(t["text"]))
        if m:
            res.days.append((_cy(t), int(m.group(1))))
    res.days.sort()

    # 伤害锚点：右侧那一列大数字
    anchors = []
    for t in toks:
        txt = norm_text(t["text"])
        if not DAMAGE_CAND.match(txt.replace(" ", "")):
            continue
        val, why = normalize_damage(txt)
        if val is None:
            res.damage_raw.append(txt)
            # 仍然当锚点用（位置是对的），只是数值存疑
        anchors.append((t, val, why))
    if not anchors:
        return res
    anchors.sort(key=lambda a: _cy(a[0]))

    # 行距：相邻锚点中心距的中位数。只有一行时用卡高估值。
    ys = [_cy(a[0]) for a in anchors]
    if len(ys) >= 2:
        diffs = sorted(b - a for a, b in zip(ys, ys[1:]) if b - a > 8)
        if diffs:
            res.pitch = float(diffs[len(diffs) // 2])
    h0 = _h(anchors[0][0])
    if res.pitch <= 0:
        res.pitch = h0 * 3.2          # 单行兜底：实测卡高 ≈ 行距/3

    pitch = res.pitch
    tol = max(10.0, pitch * 0.28)     # 同一行允许的 y 偏差

    for tok, val, why in anchors:
        cy = _cy(tok)
        dmg_box = (tok["x0"], tok["y0"], tok["x1"], tok["y1"])

        # ---- 成员名：伤害左侧、同一行、最靠右的那个文本
        cand = [t for t in toks
                if t["x1"] <= tok["x0"] - 4 and abs(_cy(t) - cy) <= tol
                and is_member_text(t["text"])]
        member = max(cand, key=lambda t: t["x1"]) if cand else None

        # ---- BOSS 等级：成员名左边的 LEVEL N
        level = None
        if member:
            lv = [t for t in toks
                  if LEVEL_RE.search(norm_text(t["text"]))
                  and t["x1"] <= member["x0"] + 2
                  and abs(_cy(t) - cy) <= max(14.0, pitch * 0.6)]
            level = max(lv, key=lambda t: t["x1"]) if lv else None
        lv_val = None
        if level:
            m = LEVEL_RE.search(norm_text(level["text"]))
            if m:
                lv_val = int(m.group(1))

        # ---- 百分比：伤害正下方
        pct = None
        p = [t for t in toks
             if PCT_RE.match(norm_text(t["text"]))
             and 0 <= _cy(t) - cy <= pitch * 0.7
             and _cx(t) >= tok["x0"] - 20]
        if p:
            pct = min(p, key=lambda t: _cy(t))

        m_box = (member["x0"], member["y0"], member["x1"], member["y1"]) if member else None
        # ---- BOSS ROI：成员名正下方一条横带。
        #      右边界取到伤害框左侧 8px：BOSS 名最长见过「重装火神R」，比成员名宽，不能用成员名宽度截。
        if m_box:
            h = _h(member)
            # 右边界必须收窄：伤害左边的 x~330 处有一个图标，det 会把它读成「双/众」，
            # 真机导出过「感医师思」这种把图标吃进来的 BOSS 名。按字符高度估算
            # 「最多 5~6 个字符宽」，既容得下「重装火神R」，又碰不到图标。
            boss_rel = f.get("boss") or DEFAULT_FIELDS["boss"]
            boss_cal = field_box(m_box, boss_rel)
            # 标定框的右边界也要钳到伤害列左侧，否则会吃进伤害数字
            boss_x1 = min(tok["x0"] - 6, boss_cal[2])
            boss_y0 = boss_cal[1]
            boss_y1 = max(boss_cal[3], boss_y0 + int(h * 1.2))
            # 左边界给三个值：框边切到图标时 OCR 会凭空多吐一个字（实测「惠医师」），
            # 多读两次取多数就能把那个不稳定字筛掉。
            boss_left = boss_cal[0]
            boss_rois = [(int(boss_left + off), boss_y0, int(boss_x1), boss_y1)
                         for off in (0, 6, 12)]
            boss_roi = boss_rois[0]
            lv_rel = f.get("level") or DEFAULT_FIELDS["level"]
            level_roi = field_box(m_box, lv_rel)
            # 备用框：在标定框基础上放宽一档（左再多 1.2h、下再放 0.3h）
            level_roi_wide = (int(level_roi[0] - 1.2 * h), int(level_roi[1] - 0.3 * h),
                              int(level_roi[2] + 0.2 * h), int(level_roi[3] + 0.3 * h))
            if f.get("pct"):
                pct_roi = field_box(m_box, f["pct"])
            else:
                # 没标定就锚在伤害框上（历史行为）。高度必须收紧：一放宽就吃到下一行，
                # 实测把 "0.0%" 读成 "00%"。
                pct_roi = (int(tok["x0"] - 6), int(tok["y1"] + 2),
                           int(tok["x1"] + 2), int(tok["y1"] + min(pitch * 0.55, _h(tok) * 2.2)))
        else:
            boss_roi = level_roi = level_roi_wide = pct_roi = None
            boss_rois = []

        res.cards.append({
            "cy": cy,
            "member": norm_text(member["text"]) if member else "",
            "member_box": m_box,
            # 置信度：det 给的 token 自带 score，用来算整行可信度（不参与任何判定）
            "member_score": float(member.get("score") or 0.0) if member else None,
            "level": lv_val,
            "level_box": (level["x0"], level["y0"], level["x1"], level["y1"]) if level else None,
            "damage": val,
            "damage_raw": norm_text(tok["text"]),
            "damage_why": why,
            "damage_box": dmg_box,
            "damage_score": float(tok.get("score") or 0.0),
            "pct": norm_text(pct["text"]) if pct else "",
            "pct_box": (pct["x0"], pct["y0"], pct["x1"], pct["y1"]) if pct else None,
            "pct_score": float(pct.get("score") or 0.0) if pct else None,
            "boss": "",
            "boss_roi": boss_roi,
            "boss_rois": boss_rois,
            "level_roi": level_roi,
            "level_roi_wide": level_roi_wide,
            "pct_roi": pct_roi,
        })

    res.cards.sort(key=lambda c: c["cy"])
    _mark_partial(res.cards, res.box, res.pitch)
    if any(not c["member"] for c in res.cards):
        res.notes.append("有行没认出成员名")
    return res


def _mark_partial(cards, box=None, pitch=None):
    """标出被列表上下边切掉的行。

    拖动式滚动不会对齐到整行，截图里最上面/最下面那行经常只露半截：
    成员名还能认，但下面那行 BOSS 名已经被裁掉 —— 真机就出过 BOSS=`_`、
    `本铁匠` 这种残字。这种行在上/下一屏里一定完整出现过，所以直接丢掉，
    让拼接去处理，别把半行数据算进来。
    """
    hs = sorted((c["member_box"][3] - c["member_box"][1]) for c in cards if c.get("member_box"))
    if len(hs) < 3:
        return
    med = hs[len(hs) // 2]
    for c in cards:
        mb = c.get("member_box")
        h = (mb[3] - mb[1]) if mb else 0
        # 判据一：成员名字高明显矮一截 → 整行只露了半截。
        # 阈值 0.65 → 0.50（v1.38）：实测 00:17 那轮 4 个人（I一IXX / 哥别打我了我错了 /
        # 拉毗PI / 数据线）前 7 轮全是 3 条，那一轮集体变成 2 条 —— 被这条误伤了。
        # 成员名的框高会随字数浮动（3 字 vs 8 字），0.65 把正常的短名也判成半截。
        # 0.50 只留真正"只剩半个身子"的行。
        c["partial"] = bool(mb) and h < med * 0.50
        # 判据二：BOSS 行压到列表下边（或上边）→ 只是"可能被切"，
        # **不能直接丢**（230×1305 那屏最后一行几何上也越界，但内容是完整的）。
        # 所以只打标记，等读完字段再看：几何越界 **且** BOSS 读不出来，才丢。
        c["clipped"] = bool(box and c.get("boss_roi")
                            and c["boss_roi"][3] > box[3] - 2)


def vote_text(vals):
    """多读几次取多数；全都不一样时取置信度最高的。

    **不做"去多余前缀"那类聪明事**：试过按"短串是长串后缀就取短串"，但
    `噬魂者` 与 `魂者` 完全符合同一个形状（真名 vs 裁掉首字），结构上分不开。
    真机上那个 `惠医师` 由"先用 det 读"解决（残片不会被检测当字），
    这一层只负责最后的兜底投票，保持可预测。

    vals = [(文本, 置信度), ...]
    """
    vals = [(norm_text(t).replace(" ", ""), float(s or 0)) for t, s in (vals or []) if t]
    vals = [(t, s) for t, s in vals if t]
    if not vals:
        return "", 0.0
    cnt = {}
    for t, _s in vals:
        cnt[t] = cnt.get(t, 0) + 1
    top, n = max(cnt.items(), key=lambda kv: (kv[1], len(kv[0])))
    if n >= 2:
        return top, max(s for t, s in vals if t == top)
    return max(vals, key=lambda v: v[1])


def _boss_key(t: str) -> str:
    """BOSS 名归一：去空白与标点、英文小写。"""
    t = norm_text(t).replace(" ", "").lower()
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", t)


def match_boss(text, bosses):
    """把 OCR 出来的 BOSS 名归一到「本期 5 个 BOSS」里的标准名。

    这是最可靠的一道闸，因为**一期盟战只有 5 个固定 BOSS**（用户提供的国服 boss 表）。
    之前那些 `本铁匠` / `惠医师` / `感医师思`，本质都是"名字旁边多认了一个字"，
    用闭集一比对就能无歧义地修回来 —— 比调几何、投票都稳。

    返回 (标准名 | None, 依据)，依据 ∈ exact / 包含 / 相似 / 无。
    """
    cands = [str(b) for b in (bosses or []) if b]
    key = _boss_key(text)
    if not key or not cands:
        return None, "无"
    kmap = {_boss_key(b): b for b in cands}
    if key in kmap:
        return kmap[key], "exact"
    # 包含关系：OCR 多认/少认一两个字时最常见（本铁匠 / 感医师思）
    for k, name in kmap.items():
        if not k:
            continue
        if k in key and len(key) - len(k) <= 2:
            return name, "包含"
        if key in k and len(k) - len(key) <= 2:
            return name, "包含"
    # 等长且只差一个字：两字 BOSS（邪魔 / 医师 / 铁匠 / 古铁）错一个字时
    # 相似度只有 0.5，永远过不了下面 0.75 的线 —— 但闭集里唯一的这个候选
    # 本身就足以定人。必须放在相似度之前，否则短名的错字永远修不回来。
    if len(key) >= 2:
        near = [name for k, name in kmap.items()
                if k and len(k) == len(key)
                and sum(a != b for a, b in zip(k, key)) == 1]
        if len(near) == 1:
            return near[0], "形近"
    # 兜底：相似度（字形相近、错一两个字）
    import difflib
    best, score = None, 0.0
    for k, name in kmap.items():
        if not k or abs(len(k) - len(key)) > 2:
            continue
        r = difflib.SequenceMatcher(None, key, k).ratio()
        if r > score:
            best, score = name, r
    if best and score >= 0.75:
        return best, "相似"
    return None, "无"


def attach_days(cards, days):
    """把「第 N 天」按 y 顺序分配给其下方的记录。"""
    out = []
    for c in cards:
        d = None
        for y, n in days:
            if y < c["cy"]:
                d = n
        c = dict(c)
        c["day"] = d
        out.append(c)
    return out


def fill_days(records):
    """滚动采集时日期标记会滚出画面 → 用"上一个已知的天"往后补。

    补的同时记一笔：如果开头的记录没有日期，说明是从中间开始采的，
    这批数据的"天"不可信，要告警（宁可说清，不给个猜的数）。
    """
    out = []
    cur = None
    missing_head = 0
    for r in records:
        d = r.get("day")
        if d is not None:
            cur = d
        else:
            if cur is None:
                missing_head += 1
            r = dict(r)
            r["day"] = cur
        out.append(r)
    return out, missing_head


def same_record(a, b) -> bool:
    """同一条记录？成员 + BOSS 相似，且**两边的伤害都对得上**。

    两个方向都踩过坑，缺一不可：
      · 只看伤害不行：普通难度一击必杀、同档同 BOSS 血量固定 → **多人伤害完全相同**
        （实测古铁三条都是 2,158,171,750），拿它当身份会认错人。
      · 只看「成员 + BOSS」也不行：**每人 3 刀，带特化刀时可以重复打同一个 BOSS**
        （用户 2026-11-07 更正），所以 (成员, BOSS) 不是唯一键；同一人的两条
        同 BOSS 记录只要伤害不同，就必须算两行，否则合并时会把一条吞掉。
    结论：成员、BOSS 相似 **且** 伤害相等（两边都读到才算，一边缺失就放行）——
    两个条件各挡一半的错，单独用任何一个都会出错。
    """
    if not a or not b:
        return False
    import difflib
    for k in ("member", "boss"):
        x, y = (a.get(k) or ""), (b.get(k) or "")
        if x == y:
            continue
        if not x or not y:
            return False
        if difflib.SequenceMatcher(None, x, y).ratio() < 0.6:
            return False
    da, db = a.get("damage"), b.get("damage")
    if da is not None and db is not None and da != db:
        return False
    return True


def _needs_fill(rec) -> bool:
    """这条记录缺不缺关键字段——缺了才值得用同行的另一屏读数回填。

    首卡被「第 N 天」标题栏压住时，等级、甚至整个 BOSS 行都读不到；
    滚过一次后同一行落到画面中部，重读就是完整的。

    `level` / `pct` 是**可选**字段：困难模式只采三列、记录里根本没有这两个键，
    不能因为它们缺席就判定"要回填"（否则每条都会触发，等于无条件覆盖）。
    """
    if not rec:
        return True
    if not rec.get("member") or not rec.get("boss"):
        return True
    if rec.get("damage") is None:
        return True
    for k in ("level", "pct"):
        if k in rec and not rec.get(k):
            return True
    return False


def splice(prev, cur, shift_px, pitch, allow_adjust: int = 1):
    """算出新一屏要**追加**哪些行。返回 (待追加行, 说明)；不可信时 (None, 说明)。

    参数 prev 是**上一屏的行**，不是累计列表——这是踩过的坑：拿累计列表去比，
    第一屏之后列表比一屏长，位移 k 行的对应关系就错位，第三屏必然判"对不上"
    （合成三屏联测时复现：屏 1 通过、屏 2 报 0/5）。

    方向：列表往下翻 = 旧的第 k+1 行变成新的第 1 行。
      核对 `prev[k:]` ↔ `cur[:m]`，其中 m = len(prev) - k（再受 len(cur) 限制）
      追加 `cur[m:]`

    为什么允许 ±1 行修正：行距相等时像素位移会混叠（实测 272px 被量成 182px），
    所以位移只给候选，定案靠重叠区内容核对；k0 对不上就试 k0±1。
    """
    if not prev:
        return list(cur), {"shift_rows": 0, "overlap": 0, "added": len(cur), "fresh": True}
    # **k=0 特例：画面只挪了不到一行（或压根没挪，只是最后一行被重读）。**
    # 实测两次：
    #   · 12:47 —— 拖动只上移 55px（行距 90，0.6 行），行序列不变，下边界多露一条；
    #   · 12:51 —— 列表到底后画面没动，但上一屏末行读废（`渲奖 … 0 767 106 202`），
    #     同一位置这一屏读对了（`凛酱 / 铁匠`）。
    # 两种情况整数位移扫描都是 0/N，会被判"拼不上"→ 补采 → 收工，尾部整段丢掉。
    # 判据：前 m0 行里最多容忍 2 行对不上（和整数位移那边的容忍度一致）；
    #   新屏更长 → 多出来的是刚露出来的行，追加；
    #   对不上的位置 → 当作同一行被重读，用新读数替换（patch）。
    m0 = min(len(prev), len(cur))
    if m0 >= 1:
        hits0 = [i for i in range(m0) if same_record(prev[i], cur[i])]
        if len(hits0) >= max(1, m0 - 2):
            added = list(cur[len(prev):]) if len(cur) > len(prev) else []
            patch = [(i, cur[i]) for i in range(m0) if i not in hits0
                     and cur[i].get("member") and cur[i].get("damage") is not None]
            if added or patch:
                return added, {"shift_rows": 0, "overlap": m0, "hit": len(hits0),
                               "added": len(added), "adjusted": True, "same": False,
                               "patch": patch}
            return [], {"shift_rows": 0, "overlap": m0, "added": 0,
                        "adjusted": True, "same": True}

    if not pitch or shift_px is None:
        return None, {"why": "量不到位移（画面变化不是整体平移）"}
    k0 = int(round(float(shift_px) / float(pitch)))
    # **k0 <= 0 不能直接判"没挪动"**：实测这个弹窗拖 200px，像素位移只量到 35px
    # （效率 17%），round(35/100)=0，于是"滚动生效"被当成"没动"→ 直接报到底。
    # 位移只当候选，真没动还是量不准，交给下面的全 k 扫描用**内容**定案。
    tiny = abs(float(shift_px)) < 1.0   # 只有真正的 0 才算「没动」
    if k0 < 1:
        k0 = 1
    if k0 >= len(prev):
        return None, {"why": f"位移 {k0} 行 ≥ 上一屏 {len(prev)} 行，中间可能漏行"}

    # 位移只当**候选**，不当结论：行距相等时像素位移会混叠（实测 272px 被量成
    # 182px，差两行以上），原来只试 k0±1 救不回来——判"位移不可信"→ 换滚动方式
    # → 最后误报"到底"，中间那几行就永久丢了（实测三人各少一条）。
    # 这里把所有 k 扫一遍，取重叠最多的那个；重叠数相同时优先像素位移给的 k。
    best = None
    best_hit = None          # (hit, m)：最接近达标的一次，用来生成可读的失败原因
    for k in range(1, len(prev)):
        m = min(len(prev) - k, len(cur))
        if m < 1:
            continue
        a = prev[k:k + m]
        b = cur[:m]
        hit = sum(1 for x, y in zip(a, b) if same_record(x, y))
        if best_hit is None or (hit, m) > best_hit:
            best_hit = (hit, m)
        # **锚点**：位移必须由内容确定，不能只看"条数对得上"。
        # 但**不能只认第一行**：框的上沿同样会切掉第一行的字，
        # 实测两次失败都是 cur[0] 读废（`OAOE` / `班长` 缺 BOSS），
        # 而 k+1 那一对完全正确（3/4 命中）。所以放宽为"前两行至少一行对得上"，
        # 仍然能挡住位移错 2 行以上的情况（那时前两行都对不上）。
        anchor_ok = same_record(prev[k], cur[0])
        if not anchor_ok and m >= 2:
            anchor_ok = same_record(prev[k + 1], cur[1])
        if not anchor_ok:
            continue
        # 允许重叠区里有 2 行对不上，但**不允许 0 行**。
        # 为什么放宽：每屏最后 1~2 行常压在列表下边界，读出来是垃圾
        # （实测 12:15 那轮上一屏末两行读成 `LEVEEL]` / `AN`，而同一批行滚上来后
        #  是 `小卡卡 / 医师 / 6,712,891,076`）。按 80% 硬卡 → 判"拼不上" → 补采
        # 一次仍失败 → 直接收工，后面整段数据丢掉（23 条 / 13 人就停在这）。
        if hit < max(1, m - 2):
            continue
        score = (hit, -abs(k - k0))
        if best is None or score > best[0]:
            best = (score, k, m, hit)
    if best is not None:
        _s, k, m, hit = best
        added = list(cur[m:])
        # 重叠区回填：同一条记录在上一屏可能落在边缘（首卡被「第 N 天」标题栏压住），
        # 等级/伤害读不到；滚过一次后它落到画面中部，同一行重读就完整了。
        # 只在"旧行缺字段、新行补齐了"时覆盖——绝不拿可能更差的读数盖掉好读数。
        # 下标用 **j（新屏内的位置）**，不是 k+j：宿主按
        # `len(rows) - len(prev) + j` 定位，写成 k+j 会重复加一次 k，把好行盖成坏行。
        tail_from = len(prev) - 2          # 上一屏最后两行 = 贴在列表下边界的脆弱行
        patch = []
        for j in range(m):
            old, new = prev[k + j], cur[j]
            oc, nc = (old.get("conf") or 0), (new.get("conf") or 0)
            if _needs_fill(old) and not _needs_fill(new):
                better = True
            elif (k + j) >= tail_from and new.get("member") and nc > oc + 0.05:
                # 边缘行被裁过：新读数来自画面中部，明显更可信就换掉
                better = True
            elif nc > oc + 0.25 and new.get("member"):
                # 其它位置：只有差距很大（说明旧行确实读歪了）才覆盖
                better = True
            else:
                better = False
            if better:
                patch.append((j, new))
        return added, {"shift_rows": k, "overlap": m, "hit": hit,
                       "added": len(added), "adjusted": k != k0,
                       "same": not added, "patch": patch}
    # 全 k 都失败：位移也接近 0 才是真的没动（到底），否则是拼接失败
    if tiny:
        return [], {"shift_rows": 0, "overlap": 0, "added": 0, "same": True}
    why = (f"重叠区只有 {best_hit[0]}/{best_hit[1]} 条对得上"
           if best_hit else f"位移 {k0} 行")
    return None, {"why": f"{why}，位移不可信"}


def summarize(records):
    """按成员 / 按 BOSS / 按天聚合。伤害求和，次数计数。"""
    by_member, by_boss, by_day = {}, {}, {}
    for r in records:
        dmg = r.get("damage") or 0
        m = r.get("member") or "?"
        b = r.get("boss") or "?"
        by_member.setdefault(m, {"count": 0, "damage": 0, "bosses": set()})
        by_member[m]["count"] += 1
        by_member[m]["damage"] += dmg
        by_member[m]["bosses"].add(b)
        by_boss.setdefault(b, {"count": 0, "damage": 0, "members": set()})
        by_boss[b]["count"] += 1
        by_boss[b]["damage"] += dmg
        by_boss[b]["members"].add(m)
        d = r.get("day")
        by_day.setdefault(d, {"count": 0, "damage": 0})
        by_day[d]["count"] += 1
        by_day[d]["damage"] += dmg
    for v in by_member.values():
        v["bosses"] = sorted(v["bosses"])
    for v in by_boss.values():
        v["members"] = sorted(v["members"])
    return {"members": by_member, "bosses": by_boss, "days": by_day,
            "total": sum((r.get("damage") or 0) for r in records), "count": len(records)}


COLS = ("day", "member", "boss", "level", "damage", "pct")


def write_csv(records, path) -> Path:
    """明细 CSV（UTF-8 BOM，Excel 直接开）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["序号", "天", "成员", "BOSS", "BOSS等级", "伤害", "占比"])
        for i, r in enumerate(records, 1):
            w.writerow([i, r.get("day") if r.get("day") is not None else "",
                        r.get("member", ""), r.get("boss", ""),
                        r.get("level") if r.get("level") is not None else "",
                        fmt_damage(r.get("damage")), r.get("pct", "")])
    return p


def write_summary_csv(records, path) -> Path:
    """汇总 CSV：一段按成员，一段按 BOSS。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    s = summarize(records)
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["汇总", "次数", "总伤害", "对象"])
        for m, v in sorted(s["members"].items(), key=lambda kv: -kv[1]["damage"]):
            w.writerow([m, v["count"], fmt_damage(v["damage"]), "/".join(v["bosses"])])
        w.writerow([])
        w.writerow(["BOSS", "被击次数", "总伤害", "出手成员数"])
        for b, v in sorted(s["bosses"].items(), key=lambda kv: -kv[1]["damage"]):
            w.writerow([b, v["count"], fmt_damage(v["damage"]), len(v["members"])])
    return p
