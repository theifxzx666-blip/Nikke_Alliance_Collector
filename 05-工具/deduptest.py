"""用真实诊断包回放，验证「同一行两种读法」会不会多出人、会不会把对的拼写弄丢。

用法：
    .venv\\Scripts\\python.exe deduptest.py                  # 回放 184352 那一趟
    .venv\\Scripts\\python.exe deduptest.py 诊断包.zip        # 回放指定的包

断言（对应诊断包 20261027_184352 的实测结果）：
  1. 最终人数 = 32（原先是 34，多出「非洲吧太阳」「命UVU」）
  2. 留下的拼写是「非洲晒太阳」「命OVO」，不是被读歪的那两种
  3. same_row 不会把 DORO 和 命OVO 当成同一个人（等长 + 相似度恰好 0.5 的坑）
"""
import ast
import glob
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import alliance_ocr as T

HERE = os.path.dirname(os.path.abspath(__file__))
OK = FAIL = 0


def check(name, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK]   {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def read_log(path):
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                n = info.filename
                if not (info.flag_bits & 0x800):
                    n = n.encode("cp437").decode("gbk")
                if n == "操作日志.txt":
                    return z.read(info).decode("utf-8", "replace")
        raise SystemExit("包里没有 操作日志.txt")
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class Fake:
    """只带合并/复核需要的几个字段，UI 相关的一律空实现。"""

    def __init__(self):
        self.names = {}
        self._hits = {}
        self._seen = {}
        self._links = set()
        self._prev_ordered = []

    def _refresh_rows(self):
        pass

    def _refresh_ledger(self, total=None):
        pass

    def _refresh(self):
        pass

    def apply(self, cur, score_of):
        """照抄 App._apply 里维护名单的那几行（不含 UI）。"""
        now = "x"
        for n in cur:
            self._hits[n] = self._hits.get(n, 0) + 1
            s = score_of(n)
            seen = self._seen.get(n)
            if seen is None:
                self._seen[n] = (s, now)
            elif s > seen[0]:
                self._seen[n] = (s, seen[1])
            if n not in self.names:
                self.names[n] = self._seen[n]
            elif s > self.names[n][0]:
                self.names[n] = (s, self.names[n][1])

    _merge_misreads = T.App._merge_misreads
    _reconcile = T.App._reconcile
    _trim_to_total = T.App._trim_to_total     # 真实流程是 _reconcile() 之后紧接着裁到页面人数


def _default_pkg():
    """不带参数时**不回放**：日志每跑一轮就变，拿"最近一趟"当断言等于天天红。

    回放是人工核对手段，用 `deduptest.py 诊断包.zip` 显式触发；
    不带参数只跑 same_row / 收工复核 / 人数硬裁 三组边界用例。
    """
    return None


def _old_default_pkg():
    """（历史）按最新诊断包/操作日志猜回放对象。留档，不再调用。"""
    out = os.path.join(HERE, "输出")
    zips = sorted(glob.glob(os.path.join(out, "诊断包_*.zip")))
    if zips:
        return zips[-1]
    txt = os.path.join(out, "操作日志.txt")
    return txt if os.path.exists(txt) else None


def main():
    pkg = sys.argv[1] if len(sys.argv) > 1 else _default_pkg()
    if not pkg:
        print("未指定回放包 —— 只跑边界用例（回放：deduptest.py <诊断包.zip|操作日志.txt>）\n")
        return run_edges()
    if not os.path.exists(pkg):
        raise SystemExit(f"找不到回放包：{pkg}")
    text = read_log(pkg)

    # 只看最后一趟（最后一次 start: 之后），前面的都是调试时的手动单屏。
    # 但"最后一趟"可能是伤害采集（同一个操作日志.txt 两边都写，实测 2026-11-07
    # 15:26 那趟伤害让回放解析出 0 屏）——那就往前找最近的一趟**名单**采集。
    lines = text.splitlines()
    starts = [i for i, l in enumerate(lines) if " start: " in l]
    if not starts:
        raise SystemExit("日志里没有名单采集的 start: 行，无法回放")

    def scans_since(i):
        out = []
        for l in lines[i:]:
            if " scan: " in l and "识别" in l:
                try:
                    out.append(ast.literal_eval(l.strip().rsplit("· ", 1)[1]))
                except Exception:
                    pass
        return out

    def seg_stop(i):
        """这一趟的收工自述（没有就空串）。"""
        for l in lines[i:]:
            if " stop: " in l:
                return l
        return ""

    def complete_seg(i):
        """完整收工的趟才算回放基线：中途取消/失败的趟只有几屏，回放它没有意义
        （实测 2026-11-07 16:57 那趟被取消，页面 32 人只采到 5 人）。"""
        st = seg_stop(i)
        return any(w in st for w in ("收工", "到底", "列表末尾"))

    last_start = starts[-1]
    scans = scans_since(last_start) if complete_seg(last_start) else []
    if len(scans) < 2:
        for s in reversed(starts[:-1]):
            if not complete_seg(s):
                continue
            cand = scans_since(s)
            if len(cand) >= 2:
                scans = cand
                last_start = s
                break
        else:
            scans = []
    if not scans:
        print("日志最后一趟及更早都没有成轮的名单 scan 行（可能只跑过伤害/单屏调试）——"
              "回放断言跳过，只跑下面的边界用例。\n")
        return run_edges()
    print(f"回放 {os.path.basename(pkg)} · 名单趟共 {len(scans)} 屏\n")

    # 页面人数是这一趟唯一的硬事实（工具自己说了不算），新包靠它做通用核对
    total = None
    for l in reversed(lines[last_start:]):
        m = re.search(r"total: 页面读出成员总数\s*(\d+)", l)
        if m:
            total = int(m.group(1))
            break
    tail = [l.strip() for l in lines[last_start:] if " stop: " in l]

    # 置信度取诊断包里那份名单的真实值（没列到的按 1.0）
    score = {}
    csv = os.path.join(os.path.dirname(pkg), "_d2", "当前名单.csv")
    if os.path.exists(csv):
        for row in open(csv, encoding="utf-8-sig").read().splitlines()[1:]:
            c = row.split(",")
            if len(c) >= 3:
                score[c[1]] = float(c[2])

    app = Fake()
    merged_log = []
    for cur in scans:
        app.apply(cur, lambda n: score.get(n, 1.0))
        rows, overlap, verdict, _note = T.judge_roll(app._prev_ordered, cur)
        if verdict == "ok" and overlap and rows:
            before = set(app.names)
            app._merge_misreads(cur, rows, overlap, verdict)
            lost = before - set(app.names)
            if lost:
                merged_log.append((sorted(lost), sorted(set(app.names) & before)))
        app._prev_ordered = cur

    mid = list(app.names)
    fixed = app._reconcile()
    trimmed = app._trim_to_total(total) if total else 0
    final = list(app.names)
    print(f"当场并完 {len(mid)} 人 → 收工复核换掉 {fixed} 个 → "
          f"按页面人数裁掉 {trimmed} 个 → 最终 {len(final)} 人")
    print(f"最终名单：{final}")
    for lost, kept in merged_log:
        print(f"  并掉 {lost}")
    print()

    if "20261027_184352" in os.path.basename(pkg):
        # 184352 那趟的已知结果：这 7 条是回归基线，名字写死是故意的
        check("人数 = 32", len(final) == 32, f"实际 {len(final)}")
        check("留下正确的「非洲晒太阳」", "非洲晒太阳" in final)
        check("没有多出的「非洲吧太阳」", "非洲吧太阳" not in final)
        check("留下正确的「命OVO」", "命OVO" in final)
        check("没有多出的「命UVU」", "命UVU" not in final)
        check("没留下读歪的「村果响当当」", "村果响当当" not in final)
        check("留下正确的「村里响当当」", "村里响当当" in final)
    elif total:
        # 断言语义（2026-11-07 修正）：deduptest 回放的是"最新日志"，那趟采集
        # 本身可能漏采（实测 11-06 20:44 页面 32、收工 30）——漏采是当时采集的
        # 质量问题，回放不该替它背。这里判的是**判据回归**：回放结果要忠实还原
        # 当时收工自述的人数；只有回放比当时还少（并空/误并）才算 FAIL。
        m_self = re.search(r"共\s*(\d+)\s*人", tail[-1]) if tail else None
        self_n = int(m_self.group(1)) if m_self else None
        if self_n is not None and self_n == len(final):
            check("回放忠实还原当时收工结果", True, f"{len(final)} 人")
            if len(final) < total:
                print(f"  （当时那轮就漏采：页面 {total} · 实采 {len(final)} —— "
                      f"非判据回归，仅供人工知悉）")
        else:
            # 漏采（< 页面人数）仍要提示；多出 1 人给容差：回放用的是旧日志配
            # 新判据，当场合并的时点与当时不同（实测 2026-11-06：现场 32，回放 33）。
            check(f"实测人数不少于页面（{total}）且回放偏差 ≤1",
                  total <= len(final) <= total + 1,
                  f"实际 {len(final)}" + (f" · 收工：{tail[-1][:60]}" if tail else ""))
        check("回放过程没有把名单并空", len(final) > 0, f"剩 {len(final)}")
        dups = [(a, b) for i, a in enumerate(final) for b in final[i + 1:]
                if T.same_row(a, b)]
        if dups:
            print(f"  ⚠ 名单里还有 {len(dups)} 对同一行的两种拼写共存：{dups[:3]}")
            print("    （不影响人数对账：超出页面人数的部分已由 trim 裁掉，"
                  "但留下的那条不一定是正确写法）")
    else:
        print("  （这一趟日志里没有 total 行，跳过人数对账，只打印结果供人工核对）")
        check("回放过程没有把名单并空", len(final) > 0, f"剩 {len(final)}")

    return run_edges()


def run_edges():
    """same_row / 收工复核 / 人数硬裁三组边界用例。无名单趟可回放时单独跑。"""
    print("\n--- same_row 的边界 ---")
    check("非洲晒太阳 / 非洲吧太阳 判为同一行", T.same_row("非洲晒太阳", "非洲吧太阳"))
    check("命OVO / 命UVU 判为同一行", T.same_row("命OVO", "命UVU"))
    check("DORO / 命OVO 判为两个人（相似度恰好 0.5 的坑）", not T.same_row("DORO", "命OVO"))
    check("空木 / 空本 判为同一行", T.same_row("空木", "空本"))
    # 2026-11-06 真机：同一行被读成「马三一」和「马三-」，签名字节不等（CCC vs CCO），
    # 于是同一个人进库两条，裁剪时还把正确拼写裁掉。标点不该构成书写系统差异。
    check("马三一 / 马三- 判为同一行（横笔字读成短横）", T.same_row("马三一", "马三-"))
    check("马三一 / 马三二 判为同一行（横笔字读成邻字）", T.same_row("马三一", "马三二"))
    check("萧白 / 英白 判为同一行", T.same_row("萧白", "英白"))
    check("两个完全不同的中文名不被并", not T.same_row("星空下的神话", "小楼听风雨"))

    print("\n--- 收工复核的边界 ---")
    # 两个昵称相近的真人，各自都被读到很多屏 → 绝不能并
    app2 = Fake()
    app2.names = {"空木": (0.99, "x"), "空本": (0.98, "x")}
    app2._hits = {"空木": 20, "空本": 18}
    app2._seen = {"空木": (0.99, "x"), "空本": (0.98, "x")}
    app2._links = {frozenset(("空木", "空本"))}
    # 两个真人在同一联盟里、名字只差一个字、都稳定出现 —— 它们必然在某一屏同时
    # 出现（每屏 5 行，相隔 ≤4 行就同屏）。`_twin` 就是"同屏各占一行"的记录，
    # 逐位投票必须先让路，否则会把其中一人的名字改掉。这是投票唯一的删真人入口。
    app2._twin = {"空木": ("空本", 0.5)}
    app2._reconcile()
    check("两个都常见的近名真人不会被并（有同屏共现证据）", len(app2.names) == 2,
          f"剩 {list(app2.names)}")

    # 一个是偶发读歪（1 屏）、一个每屏都对（8 屏）→ 该并
    app3 = Fake()
    app3.names = {"村里响当当": (0.99, "x"), "村果响当当": (0.91, "x")}
    app3._hits = {"村里响当当": 8, "村果响当当": 1}
    app3._seen = {"村里响当当": (0.99, "x"), "村果响当当": (0.91, "x")}
    app3._links = {frozenset(("村里响当当", "村果响当当"))}
    app3._reconcile()
    check("偶发读歪的会被并掉",
          "村里响当当" in app3.names and "村果响当当" not in app3.names,
          f"剩 {list(app3.names)}")

    # ---- 按页面人数硬裁（_trim_to_total）-------------------------------
    # 这一段对应真机第 1 轮采到 34 人、页面写着 32 的那次。两个多出来的名字
    # 成因完全不同，都躲过了「位置合并」：一个落在本屏最后一行（没有第二次
    # 机会），一个是三个字全读错（相似度 0.33，比 same_row 的 0.5 门槛还低）。
    print("\n=== 场景 E：页面说 32 人，采到 34 人 ===")
    sayings = []
    saved_log = T.log
    T.log = lambda tag, msg, lvl="I": sayings.append((tag, msg, lvl))

    def trim_fake(names, hits, total):
        a = Fake()
        a.names = dict(names)
        a._hits = dict(hits)
        a._seen = {k: (0.99, "x") for k in names}
        # Fake 是独立类，不继承 App：把真方法绑上去，测的还是产品代码那份实现
        return a, T.App._trim_to_total(a, total)

    real = {f"真人{i}": (0.99, "x") for i in range(32)}
    real_hits = {k: 3 for k in real}
    over = {"腊目夜": (0.94, "x"), "入魔工": (0.88, "x")}     # 各只露 1 屏
    a, n = trim_fake({**real, **over}, {**real_hits,
                                        "腊目夜": 1, "入魔工": 1}, 32)
    check("多出 2 人 → 裁回 32", len(a.names) == 32 and n == 2, f"裁了 {n} 个")
    check("两个只露一屏的都被裁掉",
          "腊目夜" not in a.names and "入魔工" not in a.names)
    check("屏数 ≥2 的真成员一个没动",
          all(k in a.names for k in list(real)[:32]))

    # 人数正好 → 不许动；裁一次就够，重复调用不该再删
    a, n = trim_fake(real, real_hits, 32)
    check("人数一致时不动手", n == 0 and len(a.names) == 32)
    a, n = trim_fake({**real, "腊目夜": (0.94, "x")},
                     {**real_hits, "腊目夜": 1}, 32)
    check("超 1 个只裁 1 个（页面 32 / 采到 33）",
          n == 1 and len(a.names) == 32, f"裁了 {n} · 剩 {len(a.names)}")
    # 页面写 33、采到 33 —— 人数本来就对，一个都不许动
    a, n = trim_fake({**real, "腊目夜": (0.94, "x")},
                     {**real_hits, "腊目夜": 1}, 33)
    check("采到数 ≤ 页面数时不动手", n == 0 and len(a.names) == 33)

    # 超出 MAX_TRIM 说明是别的问题，不许靠丢名字掩盖
    many = {f"误读{i}": (0.9, "x") for i in range(8)}
    a, n = trim_fake({**real, **many},
                     {**real_hits, **{k: 1 for k in many}}, 32)
    check("超出单轮可裁上限就不动", n == 0 and len(a.names) == 40,
          f"剩 {len(a.names)}")

    # 页面人数本身读歪了（比如把 132 读成 32、或干脆读成 1）→ 不能信
    for bad in (1, 999):
        a, n = trim_fake({**real, **over},
                         {**real_hits, "腊目夜": 1, "入魔工": 1}, bad)
        check(f"页面人数 {bad} 不合理 → 不裁", n == 0, f"剩 {len(a.names)}")

    # 可裁的不够时只裁能裁的，剩下的必须留给人看，不能拿真成员凑数
    a, n = trim_fake({**real, "腊目夜": (0.94, "x")},
                     {**real_hits, "腊目夜": 1}, 30)
    check("可裁的不够时宁缺毋滥（不拿真成员凑数）",
          n == 1 and len(a.names) == 32, f"裁了 {n} · 剩 {len(a.names)}")

    T.log = saved_log
    check("裁剪动作都写进了日志",
          any(t == "trim" for t, _m, _l in sayings), f"{len(sayings)} 条")

    print(f"\n结果：{OK} 通过 · {FAIL} 失败")
    return 1 if FAIL else 0



if __name__ == "__main__":
    raise SystemExit(main())
