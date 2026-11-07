# -*- coding: utf-8 -*-
"""困难模式识别层（**不含任何 UI**）：BOSS 表、BOSS 名归一、单屏读取。

为什么单独拆出来：`raid_capture` 需要 `read_hard`，但 `hard_client` 顶部 import 了
tkinter —— 客户端打包时会把整个 Tk 一起拖进包里（多 20~30MB，且完全用不到）。
这一层只依赖 alliance_ocr / raid_core / raid_ocr。
"""
import difflib
import json

import booklib                                # 多套 BOSS 库（v1.3）
import alliance_ocr as A
import raid_core as rc
import raid_ocr as ro

MAX_SCREENS = 60          # 一屏 6 条、滚动 2~3 行，60 屏足够覆盖整期

BOSS_TABLE = A.APP_DIR / "预设" / "盟战boss表.json"   # 旧单表，仅回落用
RECT_JSON = A.APP_DIR / "预设" / "困难框选.json"


def load_bosses() -> list:
    """活动 BOSS 库的 5 个名字（v1.3 多库；是校验名字最硬的依据）。

    回落顺序：活动套缺失 → 旧 `盟战boss表.json` 的当前 season。
    迁移由 `booklib.ensure_migrated()` 完成（load_roster 第一次调用时已做过）。
    """
    try:
        booklib.ensure_migrated()
        p = booklib.boss_path()
        if p.exists():
            t = json.loads(p.read_text(encoding="utf-8"))
            bs = [str(x) for x in (t.get("bosses") or []) if str(x).strip()]
            if bs:
                return bs
    except Exception:
        A.log_exc("bosslib")
    try:
        if BOSS_TABLE.exists():
            t = json.loads(BOSS_TABLE.read_text(encoding="utf-8"))
            season = t.get("season") or "S19"
            return list((t.get("seasons") or {}).get(season) or [])
    except Exception:
        A.log_exc("hard-bosstable")
    return []


def boss_norm(name: str, bosses) -> str:
    """把读出来的 BOSS 名归一到本期标准名。

    实测三种脏法：左边图标被吃进名字（`惠医师`）、少一个字（`火神` 不是本期）、
    末尾汉字被读成字母（`铁E`，`匠` 的右边被当成 E）。
    前两种靠"掐字"和相似度；第三种相似度只有 0.5，过不了 0.6 的门槛——
    但本期 5 个 BOSS 的**首字互不相同**（邪 / 医 / 铁 / 重 / 古），
    首字对得上就足以定人，不需要靠相似度硬凑。
    """
    if not name or not bosses:
        return name
    if name in bosses:
        return name
    for cut in (1, 2):
        if len(name) > cut + 1 and name[cut:] in bosses:
            return name[cut:]
    # 只有首字读歪、后面全对（`失匠`）→ 直接按"尾巴全等"定人
    tail = [b for b in bosses if len(b) == len(name) and b[1:] == name[1:]]
    if len(tail) == 1:
        return tail[0]
    # 只有末尾读歪（`铁E`）→ 按首字定人；首字唯一且长度接近才动手，避免乱套
    if len(name) >= 2:
        head = [b for b in bosses if b[0] == name[0]]
        if len(head) == 1 and len(name) <= len(head[0]) + 1:
            return head[0]
    best = max(bosses, key=lambda b: difflib.SequenceMatcher(None, name, b).ratio())
    r = difflib.SequenceMatcher(None, name, best).ratio()
    return best if r >= 0.6 else name


def _roi_member(left, cy, dmg_x0, pitch):
    """成员名 ROI：伤害数字左侧、与它同一水平线的窄带。

    det 漏检时用它补认。左边界取"本屏已识别成员名的最小 x"，不会凭空猜。
    """
    return (int(left), int(cy - pitch * 0.30), int(dmg_x0 - 12), int(cy + pitch * 0.10))


def _roi_boss(left, cy, dmg_x0, pitch, member_box=None):
    """BOSS 名 ROI：成员名正下方一条横带，右边界收在伤害列左侧。

    右边界必须收：伤害左边有个图标，det 会把它读成「双/众」，历史导出过
    「感医师思」这种把图标吃进来的名字。
    """
    if member_box:
        h = max(8, member_box[3] - member_box[1])
        return (int(member_box[0] - h * 0.3), int(member_box[3] + h * 0.10),
                int(dmg_x0 - 6), int(member_box[3] + h * 1.70))
    return (int(left), int(cy + pitch * 0.18), int(dmg_x0 - 12), int(cy + pitch * 0.55))


def read_hard(img, eng, roster=None, bosses=None):
    """一屏 → (记录列表, Locate)。记录只含 member / boss / damage。

    两条补认路径，都是为了对付"det 在这一屏漏字"：
      1. 成员名漏了 → 按几何 ROI 单独重认（放大 2 倍，单行识别比 det 稳得多）；
      2. BOSS 名漏了 → 在获知的成员框正下方抠一条带重认。
    """
    toks = ro.tokens(img, eng)
    loc = rc.locate(toks)
    if not loc.cards:
        return [], loc

    pitch = loc.pitch or 100.0
    box = loc.box or (0, 0, img.width, img.height)
    m_lefts = [c["member_box"][0] for c in loc.cards if c.get("member_box")]
    left = min(m_lefts) if m_lefts else box[0] + (box[2] - box[0]) * 0.12

    recs = []
    for c in loc.cards:
        cy = c.get("cy") or 0.0
        dbox = c.get("damage_box") or (box[2] - 120, cy - 20, box[2], cy + 20)

        # ---- 成员名
        if not c.get("member"):
            roi = _roi_member(left, cy, dbox[0], pitch)
            t, _s = ro.read_line(img, roi, eng, up=2)
            if t and rc.is_member_text(t):
                c["member"] = rc.norm_text(t)
                c["member_box"] = roi
                c["member_score"] = _s
                c["member_note"] = "几何补认"

        # ---- BOSS 名
        if not c.get("boss"):
            roi = _roi_boss(left, cy, dbox[0], pitch, c.get("member_box"))
            cands, best_sc = [], 0.0
            for dx in (0, 6, 12):        # 左边界切到图标时会凭空多吐一个字，多读两次取多数
                t, _s = ro.read_line(img, (roi[0] + dx, roi[1], roi[2], roi[3]), eng, up=2)
                t = rc.norm_text(t or "")
                if t and ro._boss_ok(t):
                    cands.append(t)
                    best_sc = max(best_sc, float(_s or 0.0))
            if cands:
                voted = max(set(cands), key=cands.count)
                # 投票出来还归不到本期任何一个 BOSS 名下，说明这一小行确实糊：
                # 再放大两档读一次，能用上归一的那版就替换（`铁E` 就是这么来的）。
                if bosses and voted not in bosses and boss_norm(voted, bosses) == voted:
                    for up in (3, 4):
                        t, _s = ro.read_line(img, roi, eng, up=up)
                        t = rc.norm_text(t or "")
                        if t and ro._boss_ok(t) and boss_norm(t, bosses) != t:
                            voted = t
                            best_sc = max(best_sc, float(_s or 0.0))
                            break
                c["boss"] = voted
                c["boss_score"] = best_sc
                c["boss_note"] = "几何补认"

        # ---- 剩余血量百分比：det 经常把 `79.5%` 读成 `%S6L`，所以单独抠图重认
        if not c.get("pct") and c.get("pct_roi"):
            t, _s = ro.read_line(img, c["pct_roi"], eng, up=2)
            t = rc.norm_text(t or "")
            if t and rc.PCT_RE.match(t):
                # 剩余血量不可能超过 100%，读到 795% 就是小数点被丢了 —— 按 /10 还原，
                # 还原后仍不在 0~100 就直接丢掉（不硬凑）。
                try:
                    _v = float(t.rstrip("%"))
                except ValueError:
                    _v = None
                if _v is not None and 100 < _v < 1000:
                    t = f"{_v / 10:.1f}%"
                    c["pct_note"] = "小数点丢失，已 /10 修正"
                if _v is None or _v <= 1000:
                    c["pct"] = t
                    c["pct_score"] = _s

        # ---- 伤害：det 已经给了就用，没给才复核
        dmg = c.get("damage")
        if dmg is None and c.get("damage_box"):
            dmg, raw, why = ro.read_damage(img, c["damage_box"], eng, None, "")
            c["damage"], c["damage_raw"], c["damage_note"] = dmg, raw, why

        # ---- 昵称库归位（名单场景的真名 + 别名表）
        if roster and c.get("member"):
            entries = roster.get("names") if isinstance(roster, dict) else None
            names = set(entries or {})
            alias = {}
            for _n, _e in (entries or {}).items():
                for _a in (_e or {}).get("alias") or ():
                    alias[_a] = _n
            fixed, _orig = A.roster_fix(c["member"], names, alias)
            if fixed != c["member"]:
                c["member_note"] = f"库归位 {c['member']}→{fixed}"
                c["member"] = fixed

        # v1.40：这里曾经按 `partial`/`clipped` 丢掉"被列表上下边切掉的行"。
        # 实测三轮否掉了这个做法（26 屏丢了 52 行，同一轮里残行照样漏进来）——
        # 判据本身依赖几何阈值（字高比例、BOSS 框是否压边），换个窗口尺寸就漂。
        # 丢错了是**永久丢数据**，所以改回全都收：重叠合并负责去重、复核负责标记。
        # 边缘行交给「严格采集」（双向扫描）覆盖，见 §45.3。
        if c.get("member") or c.get("boss") or c.get("damage") is not None:
            # 整行置信度取三个字段里的**最低分**：短板决定这条能不能信。
            # 不用平均分——一个字段 0.4 另一个 1.0，平均出 0.7 会骗人。
            _sc = [x for x in (c.get("member_score"), c.get("boss_score"),
                               c.get("damage_score")) if x]
            _boss_raw = c.get("boss") or ""
            _boss = boss_norm(_boss_raw, bosses)
            _bnote = c.get("boss_note") or ""
            if _boss != _boss_raw:
                # 留一行原文，方便对着截图复核：归一不是"改对了"，是"按规则认出来了"
                _bnote = f"{_bnote} · 归一自「{_boss_raw}」" if _bnote else f"归一自「{_boss_raw}」"
            recs.append({"member": c.get("member") or "",
                         "boss": _boss,
                         "damage": c.get("damage"),
                         "damage_raw": c.get("damage_raw") or "",
                         "pct": c.get("pct") or "",
                         "conf": round(min(_sc), 3) if _sc else None,
                         "member_note": c.get("member_note") or "",
                         "boss_note": _bnote,
                         "damage_note": c.get("damage_note") or "",
                         # v1.47：屏内纵坐标，收工时配合屏号做最终排序
                         "_y": c.get("cy") or 0})
    return recs, loc
