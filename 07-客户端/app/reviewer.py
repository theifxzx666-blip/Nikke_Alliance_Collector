# -*- coding: utf-8 -*-
"""采集结果复核：本地规则 agent。

定位
----
**不替代识别，是在 OCR 之后用"记录之间的一致性"再查一遍。**

单条记录的错往往不是孤立的：伤害读错一位，就会和同一 BOSS 的其它记录差出一大截；
成员认歪，名字就不在库里；血量读错，在同一（成员, BOSS）序列里就不单调。
这些矛盾只有把整表摆在一起才看得见 —— 逐条看的时候它们每一条都"长得挺正常"。

三条约束（2026-11-07 用户确认）
--------------------------------
· **纯本地规则**：离线、零配置，能连同 exe 一起发出去；
· **采集结束自动跑 + 导出前再跑一次**；
· **只标记不改数据** —— 复核是提醒，不是替用户拍板。CSV 里加一列「复核」。

为什么不接模型：本项目的发布原则是离线单文件。把截图和盟员名单传出去，
既违反原则，也解释不清"我的名单传给谁了"。

七条判据见 `客户端设计-v1.md` §35.2。
"""
from collections import Counter, defaultdict

# 可疑标签（顺序即报告里的排列顺序）
CODES = (
    "缺字段",
    "疑似坏行",
    "BOSS 不在本期",
    "血量不单调",
    "伤害异常",
    "重复嫌疑",
    "成员不在库",
    "超过 3 刀",
)


def _num(v):
    """把 "34.7%" / 34.7 / "1,234" 统一成 float，拿不到就 None。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(",", "").replace("，", "")
    try:
        return float(s)
    except ValueError:
        return None


def _nearest(text, names):
    """库里最像这个名字的一条 + 相似度。只用于给提示，不参与任何判定。"""
    import difflib

    best, score = "", 0.0
    for n in names:
        r = difflib.SequenceMatcher(None, text, n).ratio()
        if r > score:
            best, score = n, r
    return best, score


def _looks_garbage(r, miss) -> bool:
    """整行读歪（多半是模糊帧）：缺 BOSS、血量也没读到、置信度还低。

    实测 2026-11-07 三条：名字读成 `th7` / `DFY` / `可7我7`，伤害串带多余空格或
    前导 0（`0 550 014 075`），BOSS 与剩余血量全空，conf 0.51~0.72。
    正常的一行至少 BOSS 和血量能读到一个 —— 两个都空说明整行没对准。
    """
    if "BOSS" not in miss:
        return False
    if (r.get("pct") or "").strip():
        return False
    conf = r.get("conf")
    return conf is not None and float(conf) < 0.75


def review(rows, bosses=None, roster=None) -> tuple:
    """整表复核。返回 `(findings, summary)`。

    findings : `[{"idx": 序号(1-based), "code": 短标签, "msg": 人话}]`
    summary  : `{"total": 条数, "bad": 可疑条数, "by_code": {标签: 次数}}`

    同一行可能命中多条，按 code 全部记下（用户要看到全部理由，不是只给一个）。
    """
    rows = list(rows or [])
    findings = []

    def add(i, code, msg):
        findings.append({"idx": i, "code": code, "msg": msg})

    bosses = [b for b in (bosses or []) if b]
    bset = set(bosses)

    # ---- 1 缺字段 / 2 BOSS 不在本期
    for i, r in enumerate(rows, 1):
        miss = []
        if not (r.get("member") or "").strip():
            miss.append("成员")
        if not (r.get("boss") or "").strip():
            miss.append("BOSS")
        if r.get("damage") is None:
            miss.append("伤害")
        if miss:
            # 整行读歪（模糊帧）比"缺一个字段"严重得多，分开报 —— 前者要重采，
            # 后者只是那一格没认出来。不自动删：万一是真新成员只露了一次。
            if _looks_garbage(r, miss):
                add(i, "疑似坏行",
                    f"{r.get('member')} · BOSS 和血量都没读到 · conf 只有 "
                    f"{float(r['conf']):.2f} —— 多半是模糊帧，建议重采这一屏")
            else:
                add(i, "缺字段", f"缺 {'/'.join(miss)}")
            # 缺字段的行也要过一眼闭集：实测 23:54 的 #65 成员空、BOSS 位读成 `RT3`，
            # 整行错位了，只报"缺 成员"看不出根因。
            b = (r.get("boss") or "").strip()
            if b and bset and b not in bset:
                add(i, "BOSS 不在本期", f"「{b}」不在本期 —— 这一行多半整体错位")
            continue                      # 缺整行的不再跑后面的判据，避免噪声
        b = r["boss"]
        if bset and b not in bset:
            add(i, "BOSS 不在本期", f"「{b}」不在本期 {'/'.join(bosses)} 里")

    # ---- 3 血量不单调（同一成员 + 同一 BOSS 的序列）
    seq = defaultdict(list)
    for i, r in enumerate(rows, 1):
        if (r.get("member") or "").strip() and r.get("boss"):
            p = _num(r.get("pct"))
            if p is not None:
                seq[(r["member"], r["boss"])].append((i, p))
    for (m, b), items in seq.items():
        if len(items) < 3:
            continue                      # 两条判不出方向
        vals = [p for _i, p in items]
        inc = sum(1 for a, c in zip(vals, vals[1:]) if c > a)
        dec = sum(1 for a, c in zip(vals, vals[1:]) if c < a)
        if inc == 0 or dec == 0:
            continue                      # 严格单调，没事
        # 有反向：把"两边都比它背离"的那个点挑出来（真正的读数异常）
        for k in range(1, len(items) - 1):
            i, p = items[k]
            prev_p, next_p = vals[k - 1], vals[k + 1]
            if (p > prev_p and p > next_p) or (p < prev_p and p < next_p):
                add(i, "血量不单调",
                    f"{m} · {b} 这条血量 {p:.1f}% 夹在 "
                    f"{prev_p:.1f}% 和 {next_p:.1f}% 之间")

    # ---- 4 伤害异常（同一个人对同一个 BOSS，两刀不会差 3 倍）
    # 不能跨成员比：练度不同，同一 BOSS 打出 2 倍差是常态（实测 12.9 亿 vs 7.3 亿）。
    # 但一个人自己对同一个 BOSS，量级是稳定的 —— 差 3 倍基本就是数字读错了一位。
    per = defaultdict(list)
    for i, r in enumerate(rows, 1):
        d = _num(r.get("damage"))
        if d is not None and r.get("boss") and (r.get("member") or "").strip():
            per[((r["member"] or "").strip(), r["boss"])].append((i, d))
    for (m, b), items in per.items():
        if len(items) < 2:
            continue
        lo = min(items, key=lambda x: x[1])
        hi = max(items, key=lambda x: x[1])
        if lo[1] <= 0 or hi[1] / lo[1] <= 3:
            continue
        msg = (f"{m} 对 {b} 的伤害 {lo[1]:,.0f} / {hi[1]:,.0f}"
               f"（差 {hi[1] / lo[1]:.1f} 倍）")
        add(hi[0], "伤害异常", msg)
        add(lo[0], "伤害异常", msg)

    # ---- 5 重复嫌疑（同成员同 BOSS 血量相同、伤害接近但不等）
    for key, items in seq.items():
        m, b = key
        same_pct = defaultdict(list)
        for i, p in items:
            same_pct[round(p, 1)].append(i)
        for p, idxs in same_pct.items():
            if len(idxs) < 2:
                continue
            ds = [(i, _num(rows[i - 1].get("damage"))) for i in idxs]
            ds = [(i, d) for i, d in ds if d]
            for a in range(len(ds)):
                for c in range(a + 1, len(ds)):
                    (i1, d1), (i2, d2) = ds[a], ds[c]
                    big = max(d1, d2)
                    if not big:
                        continue
                    gap = abs(d1 - d2)
                    if gap > big * 0.01:
                        continue
                    # 完全相同也报：那说明 `_dedupe` 没合掉，同样要人看一眼
                    add(i2, "重复嫌疑",
                        f"与第 {i1} 条同为 {m} · {b} · 血量 {p:.1f}%，"
                        f"伤害 {d1:,.0f} / {d2:,.0f}")
                    break

    # ---- 6 成员不在库 / 7 超过 3 刀
    names = set()
    if isinstance(roster, dict):
        names = set(roster.get("names") or {})
    cnt = Counter((r.get("member") or "").strip() for r in rows)
    over_reported = set()
    for i, r in enumerate(rows, 1):
        m = (r.get("member") or "").strip()
        if not m:
            continue
        if names and m not in names:
            # 给一个"最像谁"的建议值 —— 用户要的是能立刻判断，而不是自己一格格比。
            # `UCOLVED` → 最像 `HGOLIVER`（53%）：昵称被游戏列宽截断 + OCR 错位，
            # 这种光靠字符串规则归不了位（长度不等、相似度过不了阈值），
            # 但人一眼就能认。所以**只建议，不改数据**。
            near, sc = _nearest(m, names)
            # 0.5 是底线：再低就是"库里有几十个人，随便挑一个也不止 40%"，
            # 提示反而误导（实测 3 字名「老王」会给出 40% 的「大魔王」）。
            hint = f"，最像「{near}」（{sc:.0%}）" if near and sc >= 0.5 else ""
            add(i, "成员不在库", f"「{m}」不在库里{hint}（新盟员或读歪）")
        # 超 3 刀只在第一次见到这个人时报一次，否则同一句会刷 4 行
        if cnt[m] > 3 and m not in over_reported:
            over_reported.add(m)
            add(i, "超过 3 刀", f"{m} 共 {cnt[m]} 条（一轮最多 3 刀）")

    by_code = Counter(f["code"] for f in findings)
    bad = len({f["idx"] for f in findings})
    return findings, {"total": len(rows), "bad": bad,
                      "by_code": {c: by_code[c] for c in CODES if by_code[c]}}


def summary_line(summary) -> str:
    """给人看的一行摘要，没有可疑项时返回空串。"""
    if not summary or not summary.get("bad"):
        return ""
    parts = "、".join(f"{k} {v}" for k, v in summary["by_code"].items())
    return f"复核：{summary['total']} 条 · {summary['bad']} 条可疑（{parts}）"


def by_row(findings) -> dict:
    """{序号: "原因1；原因2"} —— 导出 CSV 时按行取用。"""
    out = defaultdict(list)
    for f in findings or []:
        out[f["idx"]].append(f["msg"])
    return {k: "；".join(v) for k, v in out.items()}
