"""昵称库回归：历次采集的结果能不能真的提高下一轮的识别率。

用法：
    .venv\\Scripts\\python.exe roottest.py

四组断言：
  1. roster_fix —— 读歪的字能归回库里的正确拼写；两字名/有歧义/字形系统不同的一律不动
  2. load_roster / save_roster —— 库文件不存在、内容损坏都不能挡住采集
  3. _commit_roster —— 新增/未见/连续两轮才判退会/没采全不许判退会
  4. 真实数据回放 —— 拿 191029 那轮的 32 人当库，重跑 184352 那轮的 27 屏：
     不带库会多出 3 个幽灵，带库直接精准复原成 32 人
  5. 库不许影响相似名称判定 —— 同屏近名、露过两次以上、位置证据在、轮数不够都不动
  6. 库当"字库"用 —— 易混字对的可读性提示，以及同屏双疑似/孤行可疑只点名不改名单
"""
import json
import os
import sys
import tempfile
from pathlib import Path

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


class Fake:
    """只带合并/复核/入库需要的字段，UI 相关的一律空实现。"""

    def __init__(self):
        self.names = {}
        self._hits = {}
        self._seen = {}
        self._links = set()
        self._prev_ordered = []
        self._roster = {"version": 1, "rounds": 0, "names": {}}
        self._roster_prev = {}
        self._fix_cnt = {}
        self._gate = {}
        self._twin = {}
        self._total_hint = None        # 页面人数：入库时记进轮次记录

    def _refresh_rows(self):
        pass

    def _refresh_ledger(self, total=None):
        pass

    def _refresh(self):
        pass

    def apply(self, cur, score=1.0):
        now = "x"
        for n in cur:
            self._hits[n] = self._hits.get(n, 0) + 1
            seen = self._seen.get(n)
            if seen is None:
                self._seen[n] = (score, now)
            elif score > seen[0]:
                self._seen[n] = (score, seen[1])
            if n not in self.names:
                self.names[n] = self._seen[n]
            elif score > self.names[n][0]:
                self.names[n] = (score, self.names[n][1])

    _merge_misreads = T.App._merge_misreads
    _reconcile = T.App._reconcile
    _commit_roster = T.App._commit_roster
    _note_roster_cand = T.App._note_roster_cand
    _roster_apply = T.App._roster_apply
    _report_twins = T.App._report_twins
    _report_doubt = T.App._report_doubt
    _alias_map = T.App._alias_map


def t_roster_fix():
    print("--- 1. 库纠错本身 ---")
    lib = {"非洲晒太阳": {}, "命OVO": {}, "村里响当当": {}, "博麗霊夢": {},
           "萧白": {}, "英白": {}}
    check("非洲吧太阳 → 非洲晒太阳", T.roster_fix("非洲吧太阳", lib)[0] == "非洲晒太阳")
    check("村果响当当 → 村里响当当", T.roster_fix("村果响当当", lib)[0] == "村里响当当")
    check("博麗需夢 → 博麗霊夢", T.roster_fix("博麗需夢", lib)[0] == "博麗霊夢")
    # 命UVU 对 命OVO 只有 0.50（4 个字错 2 个）：这个区间靠字面分不清"同一个字
    # 读歪"和"两个近名真人"，所以库不自动归位，只提示。名额照留。
    check("相似 0.50 不再自动归位（分不清读歪还是近名真人）",
          T.roster_fix("命UVU", lib)[0] == "命UVU")
    check("字面完全命中的不动", T.roster_fix("非洲晒太阳", lib) == ("非洲晒太阳", None))
    check("两字名不碰（空本 不能被猜成 空木）", T.roster_fix("空本", lib)[0] == "空本")
    check("有声明的近名不碰（库里有 萧白+英白 时 大白 原样保留）",
          T.roster_fix("大白", lib)[0] == "大白")
    check("书写系统不同不碰（DORO 不会被猜成 命OVO）",
          T.roster_fix("DORO", lib)[0] == "DORO")
    check("完全不像的不碰",
          T.roster_fix("星空下的神话", lib)[0] == "星空下的神话")
    check("别名表优先（人工坐实的，不受相似度门槛限制）",
          T.roster_fix("命UVU", lib, {"命UVU": "命OVO"}) == ("命OVO", "命OVO"))
    check("别名指向库里没有的名字时回退到模糊比对",
          T.roster_fix("非洲吧太阳", lib, {"非洲吧太阳": "早就不在了"})[0] == "非洲晒太阳")
    check("空库时原样返回", T.roster_fix("命UVU", {}) == ("命UVU", None))
    check("库里两个近名都在 → 歧义，谁都不归",
          T.roster_fix("小楼听风两", {"小楼听风雨": {}, "小楼听风两": {}})[0] == "小楼听风两")


def _tmp_lib():
    """把 booklib 的全部路径指到临时目录（v1.3 多库后，ROSTER 常量不再是真相）。

    返回 (恢复函数, 临时根)。先写一份空索引：否则 load_roster 会触发迁移，
    把真实昵称库复制进临时目录，"文件不存在 → 空库"就测不出来了。
    """
    import booklib as B
    saved = (B.PRESET, B.INDEX, B.ROSTER_DIR, B.BOSS_DIR,
             B.LEGACY_ROSTER, B.LEGACY_BOSS)
    root = Path(tempfile.mkdtemp(prefix="roottest_"))
    B.PRESET = root
    B.INDEX = root / "库索引.json"
    B.ROSTER_DIR = root / "成员库"
    B.BOSS_DIR = root / "BOSS库"
    B.LEGACY_ROSTER = root / "无此文件.json"
    B.LEGACY_BOSS = root / "无此文件.json"
    B.save_index({"version": 1, "active_roster": "测试", "active_boss": "",
                  "rosters": {"测试": {"people": 0}}, "bosses": {}})

    def restore():
        (B.PRESET, B.INDEX, B.ROSTER_DIR, B.BOSS_DIR,
         B.LEGACY_ROSTER, B.LEGACY_BOSS) = saved

    # 防回归（2026-11-07 真事故）：v1.3 把 load/save 挪到 booklib 之后，旧测试还在
    # monkeypatch `T.ROSTER`，结果直接读写真实库、把 62 人覆盖成 3 人。
    # 这里硬断言一次：重定向后所有路径必须在临时根下，否则宁可不跑。
    for p in (B.INDEX, B.ROSTER_DIR, B.BOSS_DIR, B.LEGACY_ROSTER):
        if root not in Path(p).parents and Path(p) != root:
            raise SystemExit(f"测试隔离失败：{p} 不在临时目录 {root} 里")
    return restore, root


def t_storage():
    print("\n--- 2. 库文件的读写 ---")
    restore, root = _tmp_lib()
    try:
        rp = root / "成员库" / "测试.json"
        check("文件不存在 → 空库", T.load_roster()["names"] == {})
        data = {"version": 1, "rounds": 3,
                "names": {"马三一": {"first": "a", "last": "b", "rounds": 3,
                                    "miss": 0, "score": 0.99, "alias": ["马三"]}}}
        check("写盘成功", T.save_roster(data))
        back = T.load_roster()
        check("读回来人数一致", len(back["names"]) == 1)
        check("读回来别名还在", back["names"]["马三一"]["alias"] == ["马三"])
        check("写盘带 updated 时间戳", "updated" in back)
        rp.write_text("这不是 json", encoding="utf-8")
        check("文件损坏 → 空库，不抛异常", T.load_roster()["names"] == {})
    finally:
        restore()


def t_commit():
    print("\n--- 3. 收工入库的判定 ---")
    restore, _root = _tmp_lib()
    try:
        A = ["甲一", "乙二", "丙三", "丁四"]

        # 第一轮：首次建库，不该报"新增 4 人"
        app = Fake()
        app.names = {n: (0.99, "x") for n in A}
        app._roster_prev = {}
        rep = app._commit_roster(complete=True)
        check("首次建库不报新增", rep == "", f"实际 {rep!r}")
        check("库里记下 4 人", len(app._roster["names"]) == 4)

        # 第二轮：名单没变
        app = Fake()
        app._roster = json.loads(json.dumps(T.load_roster()))
        app._roster_prev = json.loads(json.dumps(app._roster["names"]))
        app.names = {n: (0.99, "x") for n in A}
        check("人数没变 → 无变动可报", app._commit_roster(complete=True) == "")
        check("累计轮数 +1", app._roster["rounds"] == 2)

        # 第三轮： newcomers 出现，丁四 不见了
        app = Fake()
        app._roster = json.loads(json.dumps(T.load_roster()))
        app._roster_prev = json.loads(json.dumps(app._roster["names"]))
        app.names = {"甲一": (0.99, "x"), "乙二": (0.99, "x"),
                     "丙三": (0.99, "x"), "戊五": (0.99, "x")}
        rep = app._commit_roster(complete=True)
        check("报出新增 1 人", "新增 1 人" in rep and "戊五" in rep, f"实际 {rep!r}")
        check("报出未见 1 人", "没见到 1 人" in rep and "丁四" in rep, f"实际 {rep!r}")
        check("第一次没见到不删（可能只是这轮没采到）", "丁四" in app._roster["names"])

        # 第四轮：丁四 还是没出现 → 判退会
        app = Fake()
        app._roster = json.loads(json.dumps(T.load_roster()))
        app._roster_prev = json.loads(json.dumps(app._roster["names"]))
        app.names = {"甲一": (0.99, "x"), "乙二": (0.99, "x"),
                     "丙三": (0.99, "x"), "戊五": (0.99, "x")}
        app._commit_roster(complete=True)
        check(f"连续 {T.ROSTER_DROP} 轮没见到才移出库", "丁四" not in app._roster["names"])

        # 没采全的一轮：不许判退会
        app = Fake()
        app._roster = {"version": 1, "rounds": 3,
                       "names": {n: {"rounds": 3, "miss": 0, "score": .99, "alias": []}
                                 for n in A}}
        app._roster_prev = json.loads(json.dumps(app._roster["names"]))
        app.names = {"甲一": (0.99, "x")}
        app._commit_roster(complete=False)
        check("中途停下（没采全）不累加 miss",
              all(e["miss"] == 0 for e in app._roster["names"].values()))

        # 名单几乎空：更不许判退会
        app = Fake()
        app._roster = {"version": 1, "rounds": 3,
                       "names": {n: {"rounds": 3, "miss": 0, "score": .99, "alias": []}
                                 for n in A}}
        app._roster_prev = json.loads(json.dumps(app._roster["names"]))
        app.names = {"甲一": (0.99, "x"), "乙二": (0.99, "x")}
        app._commit_roster(complete=True)
        check("名单不到 3 人时不判退会",
              all(e["miss"] == 0 for e in app._roster["names"].values()))

        # 本轮坐实的别名要记下来
        app = Fake()
        app._roster = {"version": 1, "rounds": 3,
                       "names": {"马三一": {"rounds": 3, "miss": 0,
                                          "score": .99, "alias": []},
                                 "乙二": {"rounds": 3, "miss": 0,
                                        "score": .99, "alias": []},
                                 "丙三": {"rounds": 3, "miss": 0,
                                        "score": .99, "alias": []}}}
        app._roster_prev = json.loads(json.dumps(app._roster["names"]))
        app.names = {n: (0.99, "x") for n in ("马三一", "乙二", "丙三")}
        app._fix_cnt = {("马三", "马三一"): 2}
        rep = app._commit_roster(complete=True)
        check("本轮坐实的误读写法记成别名",
              app._roster["names"]["马三一"]["alias"] == ["马三"], f"实际 {rep!r}")
        check("别名下轮能直接命中",
              T.roster_fix("马三", app._roster["names"],
                           {"马三": "马三一"})[0] == "马三一")
    finally:
        restore()


def t_replay():
    print("\n--- 4. 真实数据：库能不能补上这一轮的读歪 ---")
    p = os.path.join(HERE, "测试素材", "分屏序列_184352.json")
    if not os.path.exists(p):
        print(f"  [SKIP] 缺少样本 {p}")
        return
    d = json.load(open(p, encoding="utf-8"))
    lib, scans = set(d["roster"]), d["scans"]
    print(f"  库 = 191029 那轮的 {len(lib)} 人 · 回放 184352 那轮的 {len(scans)} 屏")

    def run(use_roster, frames, lib_rounds=5):
        app = Fake()
        if use_roster:
            app._roster = {"version": 1, "rounds": lib_rounds,
                           "names": {n: {"alias": [], "rounds": lib_rounds} for n in lib}}
        for raw in frames:
            cur = list(raw)
            app.apply(cur)                     # 原文入库，一个字都不改（同 _apply）
            rows, overlap, verdict, _ = T.judge_roll(app._prev_ordered, cur)
            if verdict == "ok" and overlap and rows:
                app._merge_misreads(cur, rows, overlap, verdict)
            # 库只记账，一个字都不改；裁定在下面的 _roster_apply
            app._note_roster_cand(cur, set(cur), overlap, verdict)
            app._prev_ordered = cur
        app._reconcile()
        app._roster_apply()
        return set(app.names)

    no_lib = run(False, scans)
    with_lib = run(True, scans)
    print(f"  完整 27 屏：不带库 {len(no_lib)} 人 · 带库 {len(with_lib)} 人")
    check("带库后人数 = 32", len(with_lib) == 32, f"实际 {len(with_lib)}")
    check("带库后跟上一轮的名单逐字一致", with_lib == lib,
          f"多 {sorted(with_lib - lib)} 少 {sorted(lib - with_lib)}")
    check("带库后幽灵一个都没有",
          not (with_lib & {"非洲吧太阳", "命UVU", "村果响当当"}))
    check("完整回放时两条路都能收干净（位置合并本来就够用，库没帮倒忙）",
          no_lib == lib, f"不带库时多 {sorted(no_lib - lib)}")

    # 位置合并真正救不了的场景：两屏之间跳了行（位移不可确认），而恰好
    # 中间那几行里有一行被读歪。这时没有任何"同一行"证据，只有库认得出来。
    # 这正是 184352 那轮出 34 人的原始形态——跳行 + 误读撞在一起。
    print("\n  位置合并失效时（跳行 + 顶行读歪，无任何重合行）：")
    s1 = ["DORO", "曼殊颜华", "新来的", "非洲晒太阳", "命OVO"]
    s2 = ["非洲吧太阳", "命UVU", "大魔王", "小卡卡", "凛酱"]
    a = run(False, [s1, s2])
    b = run(True, [s1, s2])
    print(f"    不带库 {len(a)} 人 {sorted(a - lib)} ← 多出来的都是同一行的两种读法")
    print(f"    带库   {len(b)} 人 {sorted(b - lib)}")
    check("跳行场景不带库会把 8 人算成 10 人", len(a) == 10, f"实际 {len(a)}")
    check("跳行场景带库救回相似 0.80 的读歪（非洲吧太阳）", "非洲晒太阳" in b)
    # 「命UVU」对「命OVO」只有 0.50：分不清是读歪还是两个近名真人，库按规矩不救。
    # 这就是"库不许影响相似名称判定"的代价——宁可多留一个名字，也不猜。
    check("相似 0.50 的仍然留着（库在这里刻意不动手）", "命UVU" in b,
          f"带库 {len(b)} 人 · 幽灵 {sorted(b - lib)}")
    check("带库后幽灵从 2 个降到 1 个", len(b - lib) == 1, f"实际 {sorted(b - lib)}")


def t_gate():
    print("\n--- 5. 库不许影响相似名称的判定 ---")
    lib = {"小楼听风雨": {"alias": [], "rounds": 5},
           "非洲晒太阳": {"alias": [], "rounds": 5}}

    def mk(library):
        app = Fake()
        app._roster = {"version": 1, "rounds": 9, "names": library}
        return app

    def note(app, cur, verdict="jump", overlap=0):
        app._note_roster_cand(cur, set(cur), overlap, verdict)

    # 5-1 同一屏两个近名同时出现 → 两个真人，一个都不许动
    app = mk(lib)
    app.apply(["小楼听风雨", "小楼听风两"])
    note(app, ["小楼听风雨", "小楼听风两"])
    g, _w = app._roster_apply()
    check("同屏两个近名都在 → 一个都不动", g == 0 and len(app.names) == 2,
          f"归位 {g} · 名单 {sorted(app.names)}")

    # 5-2 该写法全程露过两次以上 → 真成员，库不许碰
    app = mk(lib)
    for _ in range(2):
        app.apply(["小楼听风两"])
        note(app, ["小楼听风两"])
    g, _w = app._roster_apply()
    check("露过两次以上 → 当真成员，不归位",
          g == 0 and "小楼听风两" in app.names, f"归位 {g}")

    # 5-3 库里那条只采过 1 轮 → 没资格把别人的读法吸过来
    app = mk({"小楼听风雨": {"alias": [], "rounds": 1}})
    app.apply(["小楼听风两"])
    note(app, ["小楼听风两"])
    g, _w = app._roster_apply()
    check("库里条目只采过 1 轮 → 不归位",
          g == 0 and "小楼听风两" in app.names, f"归位 {g}")

    # 5-3b 基准库（人工核对过、标了 seeded）不受"只采过 1 轮"限制
    app = mk({"小楼听风雨": {"alias": [], "rounds": 1, "seeded": True}})
    app.apply(["小楼听风两"])
    note(app, ["小楼听风两"])
    g, _w = app._roster_apply()
    check("标了 seeded 的基准条目照样能归位",
          g == 1 and "小楼听风雨" in app.names, f"归位 {g}")

    # 5-3c 别名命中不受"库里至少稳采 2 轮"限制：别名是位置证据钉死的，比模糊比对硬
    app = mk({"小楼听风雨": {"alias": ["小楼听风两"], "rounds": 1}})
    app.apply(["小楼听风两"])
    note(app, ["小楼听风两"])
    g, _w = app._roster_apply()
    check("别名命中时不受'库条目只采过 1 轮'限制",
          g == 1 and "小楼听风雨" in app.names, f"归位 {g} · {sorted(app.names)}")

    # 5-4 位置证据已经表过态 → 库不抢
    app = mk(lib)
    app.apply(["非洲吧太阳"])
    app._links.add(frozenset(("非洲吧太阳", "非洲晒太阳")))
    note(app, ["非洲吧太阳"], verdict="ok")
    g, _w = app._roster_apply()
    check("位置证据管过的不归库管",
          g == 0 and "非洲吧太阳" in app.names, f"归位 {g}")

    # 5-5 对照组：条件都满足时确实会归——否则上面几条只是"永远不动"
    app = mk(lib)
    app.apply(["非洲吧太阳"])
    note(app, ["非洲吧太阳"])
    g, _w = app._roster_apply()
    check("条件都满足时会归位（对照组）",
          g == 1 and "非洲晒太阳" in app.names and "非洲吧太阳" not in app.names,
          f"归位 {g} · 名单 {sorted(app.names)}")

    # 5-6 判定层对库无感：同一套分屏，空库和满库跑出一样的结果
    def judge_only(library):
        app = Fake()
        app._roster = {"version": 1, "rounds": 9, "names": library}
        for cur in (["星空下的神话", "小楼听风雨", "非洲晒太阳"],
                    ["小楼听风雨", "非洲晒太阳", "命OVO"],
                    ["非洲晒太阳", "命OVO", "马三一"]):
            app.apply(cur)
            rows, overlap, verdict, _ = T.judge_roll(app._prev_ordered, cur)
            if verdict == "ok" and overlap and rows:
                app._merge_misreads(cur, rows, overlap, verdict)
            app._prev_ordered = cur
        app._reconcile()
        return set(app.names)

    check("判定层对库无感（空库 / 满库结果一字不差）",
          judge_only({}) == judge_only({"星空下的神话": {}, "小楼听风雨": {}}),
          f"{sorted(judge_only({}))} vs {sorted(judge_only({'星空下的神话': {}, '小楼听风雨': {}}))}")


def t_confuse():
    print("\n--- 6. 库当字库用：易混字对只是说法，不是判据 ---")
    lib = {"命OVO": {"alias": ["命UVU"], "rounds": 6},
           "空木": {"alias": ["空本"], "rounds": 6},
           "非洲晒太阳": {"alias": ["非洲吧太阳"], "rounds": 6}}
    pairs = T.confuse_pairs(lib)
    check("从别名里提出 O↔U 易混", pairs.get(("U", "O"), 0) >= 2, str(pairs))
    check("从别名里提出 木↔本 易混", ("本", "木") in pairs, str(pairs))
    check("长度不等 / 书写系统不同的别名不参与提取",
          T.confuse_pairs({"命OVO": {"alias": ["DORO", "命OV"]}}) == {})
    check("空库/无别名 → 空表", T.confuse_pairs({}) == {} and
          T.confuse_pairs({"甲一": {}}) == {})

    check("差异全在已知易混 → 判为老毛病",
          "老毛病" in T.confuse_note("命UVU", "命OVO", pairs))
    check("差异从没见过 → 判为更像两个真人",
          "更像两个真人" in T.confuse_note("命XYZ", "命OVO", pairs))
    check("冷启动（库还没攒下别名）时不瞎下结论",
          "判断不了" in T.confuse_note("命UVU", "命OVO", {}))
    check("长度不同不硬凑说法", T.confuse_note("命OV", "命OVO", pairs) == "")
    check("完全一样不说废话", T.confuse_note("命OVO", "命OVO", pairs) == "")

    # 6-1 同屏双疑似：进 _twin，不进 _gate，名单一个不动
    def mk(library):
        app = Fake()
        app._roster = {"version": 1, "rounds": 9, "names": library}
        return app

    app = mk(lib)
    app.apply(["命OVO", "命UVU"])
    app._note_roster_cand(["命OVO", "命UVU"], {"命OVO", "命UVU"}, 0, "jump")
    check("同屏双疑似被记下来（不再静默丢掉）", "命UVU" in app._twin, str(app._twin))
    check("同屏双疑似不进归位候选", "命UVU" not in app._gate, str(app._gate))
    g, w = app._roster_apply()
    check("同屏双疑似不改名单",
          g == 0 and "命UVU" in app.names and "命OVO" in app.names,
          f"归位 {g} · 名单 {sorted(app.names)}")
    check("同屏双疑似要计入'需人工核'", w == 1, f"warned={w}")
    check("点过名就清账，导前再调不会重复报", app._twin == {},
          str(app._twin))

    # 6-2 孤行可疑：只露 1 屏的野名字 vs 稳定出现的老成员
    app = mk(lib)
    for _ in range(4):
        app.apply(["命OVO"])
    app.apply(["命UVU"])            # 只露 1 屏
    w = app._report_doubt(app._roster["names"], T.confuse_pairs(lib))
    check("抓到孤行可疑（1 屏野名字 + 4 屏老成员）", w == 1, f"w={w}")
    check("孤行可疑只报不改", "命UVU" in app.names, sorted(app.names))

    # 6-3 两个都稳定出现 → 不许报，那是两个真人
    app = mk(lib)
    for _ in range(3):
        app.apply(["命OVO", "命UVU"])
    w = app._report_doubt(app._roster["names"], T.confuse_pairs(lib))
    check("两个都稳定出现 → 不报可疑", w == 0, f"w={w}")

    # 6-4 库不认识的名字：没有旁证就不许乱报
    app = mk(lib)
    for _ in range(4):
        app.apply(["马三一"])
    app.apply(["马三二"])
    w = app._report_doubt(app._roster["names"], T.confuse_pairs(lib))
    check("野名字对野名字（都不在库）→ 不报", w == 0, f"w={w}")

    # 6-5 哨兵：点名逻辑（twins / doubt）跑一轮，名单必须一个字不动。
    # 这里故意用 alias 为空的库，免得"别名归位"这条正经改动混进来干扰断言。
    app = mk({"命OVO": {"alias": [], "rounds": 6}})
    app.apply(["命OVO", "命UVU"])
    app._note_roster_cand(["命OVO", "命UVU"], {"命OVO", "命UVU"}, 0, "jump")
    before = set(app.names)
    lib2 = app._roster["names"]
    pr = T.confuse_pairs(lib2)
    w1 = app._report_twins(lib2, pr) + app._report_doubt(lib2, pr)
    check("点名逻辑有话说", w1 > 0, f"w={w1}")
    check("点名逻辑一个字都不改名单", set(app.names) == before,
          f"{sorted(before)} → {sorted(app.names)}")


def main():
    t_roster_fix()
    t_storage()
    t_commit()
    t_replay()
    t_gate()
    t_confuse()
    # ---------------- v1.32：匿名成员（昵称被游戏打成 ******）
    check("匿名昵称 `******` 当名字放行（否则整行被丢、人数永远少）",
          T.plausible("******"), "")
    check("匿名昵称：全角星号也认", T.plausible("＊＊＊＊"), "")
    check("匿名昵称：圆点占位也认", T.plausible("•••"), "")
    check("匿名判定不误伤纯数字", not T.plausible("12345"), "")
    check("匿名判定不误伤昵称与积分的粘连", not T.plausible("****** 24460"), "")
    import raid_core as _rc
    check("伤害记录侧同样放行匿名", _rc.is_member_text("******"), "")
    check("伤害记录侧：BOSS 这类 UI 词仍被挡", not _rc.is_member_text("BOSS"), "")

    print(f"\n结果：{OK} 通过 · {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
