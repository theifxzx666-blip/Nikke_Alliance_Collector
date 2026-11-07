"""导航回归：从主页走到联盟成员页，每一步都不许盲点。

用法：
    .venv\\Scripts\\python.exe navtest.py

六组断言（前五组是纯逻辑，最后一组回放两张真实截图）：
  1. 页面指纹 —— 主页/联盟页各认各的，不串页；认不出来必须老实说认不出来
  2. 落点决策 —— 认出文字就用文字位置，认不出退实测锚点，两者都必须在白名单里
  3. 白名单红线 —— 付费/商店/退出这类按钮的坐标不在任何一个 area 内
  4. 拒绝优先 —— 指纹不够、锚点越界，一律不点（宁可不做，也不盲点）
  5. 文字归一化 —— 全角/空格/大小写不影响命中
  6. 真实截图走查 —— 两张图串完整流程，确认每步都能走到、且不互相抢词

真实截图素材：测试素材/导航_主页_有效画面.png
              测试素材/导航_联盟页_有效画面.png
    都是从用户 1236×725 的整窗截图上裁掉标题栏(31px)和左右边框(各 4px)
    得到的 1228×694 有效画面，和运行时 content_rect 的坐标系一致。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image

import alliance_ocr as T
import raid_nav as RN

HERE = os.path.dirname(os.path.abspath(__file__))
OK = FAIL = 0
HOME_IMG = "导航_主页_有效画面.png"
GUILD_IMG = "导航_联盟页_有效画面.png"


def check(name, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK]   {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


_cache = {}


def look(name):
    """读一屏并缓存 OCR 结果——引擎每次约 0.3s，六组测试不该重复跑。"""
    if name not in _cache:
        img = Image.open(os.path.join(HERE, "测试素材", name)).convert("RGB")
        _cache[name] = T.read_screen(img)
    return _cache[name]


def texts_of(name):
    return [b[0] for b in look(name)]


# ---------------------------------------------------------------- 1 页面指纹
def t_which():
    print("\n[1] 页面指纹：两页各认各的，认不出必须说认不出")
    home = texts_of(HOME_IMG)
    guild = texts_of(GUILD_IMG)

    st, n = T.nav_which(home)
    check("主页截图判成「主页」", st is not None and st.key == "home",
          f"{st} · 命中 {n}")
    st2, n2 = T.nav_which(guild)
    check("联盟页截图判成「联盟概览」", st2 is not None and st2.key == "guild",
          f"{st2} · 命中 {n2}")

    n_home_on_guild = T.hit_words(guild, T.NAV_SPEC[0].probe)
    check("联盟页不会被认成主页", n_home_on_guild < T.NAV_SPEC[0].min_hits,
          f"联盟页命中主页指纹 {n_home_on_guild} 个（门槛 {T.NAV_SPEC[0].min_hits}）")
    n_guild_on_home = T.hit_words(home, T.NAV_SPEC[1].probe)
    check("主页不会被认成联盟页", n_guild_on_home < T.NAV_SPEC[1].min_hits,
          f"主页命中联盟页指纹 {n_guild_on_home} 个")

    st3, _n3 = T.nav_which(["随便", "什么", "都没有"])
    check("什么都不是就说认不出（不硬猜最近的页）", st3 is None, str(st3))

    st4, n4 = T.nav_which([])
    check("空画面返回 None 而不是抛异常", st4 is None and n4 == 0, str(st4))


# ---------------------------------------------------------------- 2 落点决策
def t_decide():
    print("\n[2] 落点决策：优先用 OCR 认出的位置，退锚点也要在白名单里")
    hb, ht = look(HOME_IMG), texts_of(HOME_IMG)
    x, y, why = T.nav_decide(T.NAV_SPEC[0], hb, ht)
    check("主页能决定点「联盟」", x is not None, why)
    check("落点在白名单内", x is not None and T.in_area(x, y, T.NAV_SPEC[0].area),
          f"({x:.3f},{y:.3f}) vs {T.NAV_SPEC[0].area}")
    check("用的是 OCR 认出的位置而不是硬编码锚点", "OCR 认出" in why, why)
    check("落点贴近实测位置 (0.970, 0.418)",
          x is not None and abs(x - 0.970) < 0.02 and abs(y - 0.418) < 0.02,
          f"({x:.3f},{y:.3f})")

    gb, gt = look(GUILD_IMG), texts_of(GUILD_IMG)
    x2, y2, why2 = T.nav_decide(T.NAV_SPEC[1], gb, gt)
    check("联盟页能决定点「队员」", x2 is not None, why2)
    check("「队员」落点在标签条白名单内",
          x2 is not None and T.in_area(x2, y2, T.NAV_SPEC[1].area), f"({x2:.3f},{y2:.3f})")
    check("「队员」没被底部「队员招募」抢走",
          T.pick_target(hb, "队员", T.NAV_SPEC[1].area) is None,
          "主页的「队员招募」在白名单外，应被过滤")

    # OCR 认不出目标词 → 退实测锚点，且锚点仍须在白名单内
    stripped = [(t, box, sc) for t, box, sc in hb if "联盟" not in t]
    x3, y3, why3 = T.nav_decide(T.NAV_SPEC[0], stripped, ht)
    check("认不出目标词就退实测锚点", x3 is not None and "锚点" in why3, why3)
    check("退锚点也仍在白名单内",
          x3 is not None and T.in_area(x3, y3, T.NAV_SPEC[0].area), f"({x3:.3f},{y3:.3f})")


# ---------------------------------------------------------------- 3 白名单红线
def t_whitelist():
    print("\n[3] 白名单红线：付费类按钮一个都点不到")
    risky = ("付费", "商店", "充值", "钻石", "购买", "礼包", "退出", "设定")
    checked = 0
    for name in (HOME_IMG, GUILD_IMG):
        for text, (x0, y0, x1, y1), _sc in look(name):
            if not any(k in text for k in risky):
                continue
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            hit = [st.key for st in T.NAV_SPEC if T.in_area(cx, cy, st.area)]
            checked += 1
            check(f"「{text}」不在任何点击白名单里", not hit,
                  f"{hit} @({cx:.3f},{cy:.3f})")
    check("确实扫到了这类按钮（不是空跑）", checked >= 4, f"共 {checked} 个")

    # 每一步的兜底锚点本身也必须在自己的白名单里——否则降级路径会点到白名单外
    for st in T.NAV_SPEC:
        check(f"「{st.name}」的兜底锚点在自己白名单内",
              T.in_area(st.preset[0], st.preset[1], st.area), str(st.preset))


# ---------------------------------------------------------------- 4 拒绝优先
def t_refuse():
    print("\n[4] 拒绝优先：判不准就一个都不点")
    x, y, why = T.nav_decide(T.NAV_SPEC[0], [], [])
    check("空画面拒绝点击", x is None and "至少" in why, why)

    x, y, why = T.nav_decide(T.NAV_SPEC[0], [], ["方舟", "大厅"])
    check("主页只命中 2 个词也不点（门槛 3）", x is None, why)

    g = T.NAV_SPEC[1]
    x, y, why = T.nav_decide(g, [], ["日志"])
    check("联盟页只命中 1 个词不点", x is None, why)
    x, y, why = T.nav_decide(g, [], ["日志", "联盟公告"])
    check("联盟页命中 2 个词就够（门槛 2）", x is not None, why)

    # 锚点被挤出白名单 → 必须拒绝，而不是"就近点一下"
    squeezed = type(g)("guild", "联盟概览", g.probe, "队员", (0.30, 0.60, 0.40, 0.70),
                       (0.500, 0.278), wait=0, need=None, min_hits=2)
    x, y, why = T.nav_decide(squeezed, [], ["日志", "联盟公告"])
    check("锚点落在白名单外就拒绝", x is None and "白名单" in why, why)

    # 目标词出现但不在白名单内 → 不能点
    blocks = [("队员", (0.05, 0.90, 0.10, 0.95), 0.99)]
    x, y, why = T.nav_decide(g, blocks, ["日志", "联盟公告"])
    check("目标词在白名单外就退回锚点、不点那处",
          x is not None and abs(x - g.preset[0]) < 1e-9, f"({x},{y})")


# ---------------------------------------------------------------- 5 归一化
def t_norm():
    print("\n[5] 文字归一化：全角/空格/大小写不该影响判定")
    check("全角 → 半角、去空格、统一大写",
          T.text_norm("ＭＩＳＳＩＯＮ　ＰＡＳＳ") == T.text_norm(" mission pass "),
          T.text_norm("ＭＩＳＳＩＯＮ　ＰＡＳＳ"))
    check("归一化后仍命中指纹", T.hit_words(["Mission Pass"], ("MISSION PASS",)) == 1)
    check("空词不误命中", T.hit_words(["任意"], ("",)) == 0)
    check("None / 空串不炸", T.text_norm(None) == "" and T.hit_words([None, ""], ("日志",)) == 0)


# ---------------------------------------------------------------- 6 真实走查
def t_walk():
    print("\n[6] 真实截图走查：两张图串一遍完整导航")
    hb, ht = look(HOME_IMG), texts_of(HOME_IMG)
    gb, gt = look(GUILD_IMG), texts_of(GUILD_IMG)
    home, guild = T.NAV_SPEC

    x, y, why = T.nav_decide(home, hb, ht)
    check("① 在主页决定点「联盟」", x is not None, why)
    check("② 点完「该到联盟页」的判据在联盟页上成立",
          T.need_ok(gt, home.need), f"命中 {T.hit_words(gt, home.need)}")
    check("③ 联盟页上「已经过了主页这关」能认出来（可跳过重复点击）",
          T.need_ok(gt, home.need))
    check("④ 主页上这条判据不成立（不会误跳过）",
          not T.need_ok(ht, home.need), f"命中 {T.hit_words(ht, home.need)}")

    x2, y2, why2 = T.nav_decide(guild, gb, gt)
    check("⑤ 在联盟页决定点「队员」", x2 is not None, why2)
    check("⑥ 两步落点不重合（不是同一个位置）",
          x is not None and x2 is not None and abs(x - x2) > 0.1,
          f"({x:.3f},{y:.3f}) vs ({x2:.3f},{y2:.3f})")

    check("⑦ 每一步的 target 都真的出现在对应页上",
          all(T.hit_words(t, (st.target,)) == 1 for st, t in ((home, ht), (guild, gt))))
    check("⑧ 两步的白名单互不重叠",
          not any(T.in_area(px, py, guild.area)
                  for px, py in [(home.area[0], home.area[1]),
                                 (home.area[2], home.area[3])]),
          f"{home.area} vs {guild.area}")


# ---------------------------------------------------------------- 7 回顶
def t_scroll_top():
    print("\n[7] 拉回第一行：名字没变才到顶，输入失效不许假装成功")
    import types

    class Sim:
        """假列表：at 是当前第一行在名单里的下标，scroll(up) 每次往回 2 行。"""

        def __init__(self, total=20, at=10, works=True):
            self.total, self.at, self.works = total, at, works
            self.rolled = 0
            self.modes = []

        def rows(self, img=None):
            out = [(i * 71.0, f"p{self.at + i}", 0.99)
                   for i in range(5) if self.at + i < self.total]
            return out, None, 71.0

        def scroll(self, mode, region, clicks=1, dist=None, up=False, fast=False):
            self.rolled += 1
            self.modes.append((mode, up))
            if self.works and up and self.at > 0:
                self.at = max(0, self.at - 2)

    class NoSleep:
        def sleep(self, *_a):
            pass

    def run(sim, mode=None, trust=None):
        app = types.SimpleNamespace(_nav_cancel=False, scroll_mode=mode,
                                    _mode_ok=set(), _pitch=71.0, win=None)
        saved = (T.scan, T.scroll_once, T.time, T.park_cursor, T.log,
                 T.wait_settled)
        T.scan = sim.rows
        T.scroll_once = sim.scroll
        T.time = NoSleep()
        T.park_cursor = lambda *a, **k: None
        T.log = lambda *a, **k: None
        # 回顶现在每次都等画面停稳再判位置（不然截图截到半格错位，名字看起来
        # 变了，永远判不出到顶）。测试里没必要真等，直接返回"已停稳"。
        T.wait_settled = lambda *a, **k: (None, 0.0, True)
        try:
            return T.App._nav_scroll_top(app, (0, 0, 100, 100), trust), sim
        finally:
            (T.scan, T.scroll_once, T.time, T.park_cursor, T.log,
             T.wait_settled) = saved

    (done, n), sim = run(Sim(total=20, at=10))
    check("从第 10 行回滚：名字不再变才算到顶", done and n >= 5,
          f"done={done} n={n}")
    check("未知方式时默认走拖动（不能拿滚轮推一个不吃滚轮的列表）",
          all(m == "drag" for m, _u in sim.modes), str(sim.modes[:2]))
    check("每一次都是反向（up=True），方向写反会越拉越到底",
          all(u for _m, u in sim.modes), str(sim.modes[:2]))

    (done, n), sim = run(Sim(total=20, at=0))
    check("本来就在第一行：第一轮就确认到顶，不做无谓拖动", done and n <= 2,
          f"done={done} n={n} 滚了{sim.rolled}次")

    (done, n), sim = run(Sim(total=20, at=10, works=False), mode="wheel")
    check("输入完全失效：明确报未确认，不谎报已到顶",
          not done, f"done={done} n={n}")

    (done, n), sim = run(Sim(total=20, at=10), mode="wheel")
    check("配置存的是滚轮就按滚轮回滚", done and
          all(m == "wheel" for m, _u in sim.modes), str(sim.modes[:2]))

    # 关键回归：到顶后继续拖会触发回弹/下拉动画，画面一直在动。
    # 早期版本用画面差判"还在动"，于是连滚 30 次都停不下来（实测日志）。
    (done, n), sim = run(Sim(total=20, at=10))
    check("到顶后靠名字判定停下，不会一直滚到次数上限", sim.rolled < T.NAV_TOP_TRIES,
          f"滚了 {sim.rolled} 次 / 上限 {T.NAV_TOP_TRIES}")


def t_skip():
    """「这一步过没过」的判据。

    这条是拿真实故障换来的：`need_ok(texts, None)` 按定义返回 True，所以只要
    忘了挡 None，最后一步就会被恒定判成「已经过了」直接跳过。外部表现是
    「点了联盟、但没点队员」，而日志写着「已经过了『联盟概览』这一关」——看着
    完全合理，查起来极费劲。所以必须有一条断言钉住它。
    """
    print("\n[8] 跳过判据：最后一步不许被当成「已经过了」")
    home, guild = T.NAV_SPEC[0], T.NAV_SPEC[1]
    guild_texts = ["日志", "联盟公告", "联盟功能", "联盟射击场", "联盟商店"]

    check("终点步没有 need", guild.need is None, str(guild.need))
    check("站在联盟页时，第一步（主页→联盟）该跳过",
          T.nav_skip_step(home, guild_texts) is True, "")
    check("站在联盟页时，最后一步（→队员）不许跳过",
          T.nav_skip_step(guild, guild_texts) is False,
          "这就是「点了联盟没点队员」的直接原因")
    check("站在主页时，两步都不跳过",
          (T.nav_skip_step(home, ["方舟", "大厅", "物品栏"]) is False
           and T.nav_skip_step(guild, ["方舟", "大厅", "物品栏"]) is False), "")
    check("need_ok 对 None 确实是 True（说明这道挡板不能省）",
          T.need_ok(guild_texts, None) is True, "")

    im = Image.open(os.path.join(HERE, "测试素材", "导航_联盟页_有效画面.png")).convert("RGB")
    texts = [b[0] for b in T.read_screen(im)]
    check("真实联盟页截图：第一步跳过、第二步不跳过",
          T.nav_skip_step(home, texts) and not T.nav_skip_step(guild, texts),
          f"home={T.nav_skip_step(home, texts)} guild={T.nav_skip_step(guild, texts)}")


def t_raid_pages():
    """`raid_nav` 的页判定：记录页 / 联盟概览 / 大厅三分，认不出就必须是 None。

    这一组是 v1.1 补的：以前 `goto_record` 只认「联盟页 → 突袭 → 记录」，
    站在大厅点开始会直接报"当前页面既没有…"。加了回退链路之后，
    **判定错了就会在错的页面上按 ESC**，所以先把判定钉死。
    """
    def tk(*texts):
        return [dict(x0=10 * i, y0=10 * i, x1=10 * i + 50, y1=10 * i + 20,
                     text=t, score=0.95) for i, t in enumerate(texts)]

    cases = [
        ("record", tk("LEVEL 400", "LEVEL 400"), "两条 LEVEL 徽标"),
        ("record", tk("1,538,157,240", "2,000,000,000"), "两条千分位伤害"),
        ("raid", tk("联盟记录"), "联盟记录按钮（突袭主页指纹，v1.2 的坑）"),
        ("raid", tk("联盟记录", "联盟记录入口"), "联盟记录按钮 + 别名"),
        ("record", tk("LEVEL 400", "LEVEL 401", "1,538,157,240", "联盟记录"),
         "记录页优先于突袭主页（有列表就是 record，不再点按钮）"),
        ("home", tk("方舟", "大厅", "物品栏"), "大厅三词"),
        ("home", tk("方舟", "大厅", "队员招募", "作战出击"), "大厅四词"),
        ("members", tk("主选单", "队员", "日志"),
         "成员页（主选单+队员，只命中「日志」1 词，v1.16）"),
        ("guild", tk("主选单", "队员", "日志", "联盟公告"),
         "概览页也有两个页签，但先被 GUILD_PROBE 认走"),
        ("guild", tk("联盟公告", "联盟功能"), "联盟页两词"),
        ("guild", tk("联盟商店", "联盟射击场", "日志"), "联盟页三词"),
        (None, tk("商店", "礼包"), "无关页"),
        (None, tk("方舟", "大厅"), "大厅只命中 2 词 —— 不够就别猜"),
    ]
    for want, toks, why in cases:
        got = RN.RaidNav.which_page(toks)
        check(f"页判定 {why} → {want}", got == want, f"实际 {got}")

    # v1.16：成员页 → 点「主选单」回概览（名单采集刚收工就停在这页）
    nav = object.__new__(RN.RaidNav)
    said, clicked = [], []
    nav.say = lambda m, *a: said.append(m)
    nav._click_token = lambda tok: clicked.append(tok["text"])
    nav._wait_for = lambda pred, timeout=8.0, interval=0.35: (True, [], None)

    ok_mv, _toks, _img = nav._leave_members(tk("主选单", "队员", "日志"), None)
    check("成员页点「主选单」回概览", ok_mv and clicked == ["主选单"],
          f"{ok_mv} / 点了 {clicked}")

    clicked.clear()
    ok_no, _a, _b = nav._leave_members(tk("队员", "日志"), None)
    check("找不到「主选单」时不乱点（交给 ESC 兜底）",
          (not ok_no) and clicked == [], f"{ok_no} / {clicked}")

    check("页名表里有成员页", RN.PAGE_CN.get("members") == "联盟成员页",
          str(RN.PAGE_CN))

    # v1.16 端到端：成员页 → 主选单 → 联盟突袭 → 记录页（全链路打桩，不碰游戏）
    def tk_y(*texts):
        # y 要落在画面下半部分：「联盟突袭」卡片标签的 lower_half 过滤在 45% 处
        return [dict(x0=10 * i, y0=320 + 12 * i, x1=10 * i + 50, y1=320 + 12 * i + 20,
                     text=tx, score=0.95) for i, tx in enumerate(texts)]

    img_fake = type("Img", (), {"height": 600})()
    seq = {
        "members": tk_y("主选单", "队员", "日志"),
        "guild": tk_y("主选单", "队员", "日志", "联盟公告", "联盟突袭"),
        "record": tk_y("LEVEL 400", "LEVEL 401"),
    }
    nav2 = object.__new__(RN.RaidNav)
    nav2.stop = None
    nav2.esc = None
    nav2.region = (0, 0, 100, 100)
    nav2.say = lambda m, *a: None
    nav2._shot = lambda: (img_fake, seq["members"])
    nav2._click_token = lambda tok: None
    _hop = {"n": 0}

    def _fake_wait(pred, timeout=8.0, interval=0.35):
        _hop["n"] += 1
        if _hop["n"] == 1:              # 点「主选单」之后 → 概览
            return True, seq["guild"], img_fake
        return True, seq["record"], img_fake   # 点「联盟突袭」之后 → 记录页

    nav2._wait_for = _fake_wait
    ok_e2e, msg_e2e = nav2.goto_record()
    check("成员页开始采集：主选单 → 联盟突袭 → 记录页 一条链走通",
          ok_e2e and "记录页" in msg_e2e, f"{ok_e2e} / {msg_e2e}")
    check("这条链是导航点出来的 → last_entered=True（采集不必回顶）",
          getattr(nav2, "last_entered", None) is True,
          str(getattr(nav2, "last_entered", None)))

    # 反例：一开始就站在记录页 → 列表位置是用户自己滚的，必须回顶
    nav3 = object.__new__(RN.RaidNav)
    nav3.stop = None
    nav3.region = (0, 0, 100, 100)
    nav3.say = lambda m, *a: None
    nav3._shot = lambda: (img_fake, seq["record"])
    ok3, _m3 = nav3.goto_record()
    check("本来就在记录页 → last_entered=False（要回顶）",
          ok3 and getattr(nav3, "last_entered", None) is False,
          str(getattr(nav3, "last_entered", None)))

    # 联盟入口的白名单：越界的坐标（比如右侧那排图标之外的「联盟」字样）不许点
    check("联盟入口白名单上界压到 0.230（避免命中 MISSION PASS 横幅）",
          RN.GUILD_AREA[1] >= 0.23, str(RN.GUILD_AREA))
    check("实测锚点落在白名单内",
          RN.GUILD_AREA[0] <= RN.GUILD_PRESET[0] <= RN.GUILD_AREA[2]
          and RN.GUILD_AREA[1] <= RN.GUILD_PRESET[1] <= RN.GUILD_AREA[3],
          f"area={RN.GUILD_AREA} preset={RN.GUILD_PRESET}")

    # v1.2：真实联盟概览截图里不许出现「联盟记录」——否则概览会被误判成突袭主页
    im_g = Image.open(os.path.join(HERE, "测试素材", "导航_联盟页_有效画面.png")).convert("RGB")
    g_toks = [{"x0": 0, "y0": i * 10, "x1": 60, "y1": i * 10 + 18,
               "text": b[0], "score": 0.95}
              for i, b in enumerate(T.read_screen(im_g))]
    check("真实联盟概览截图不误判为突袭主页",
          RN.RaidNav.which_page(g_toks) == "guild",
          str(RN.RaidNav.which_page(g_toks)))

    # v1.16.1：真实成员页（用户 2026-11-07 19:09 误判的原始画面）——
    # 贡献值是 7 位数字，会被 normalize_damage 当伤害，两行凑够就误判成记录页
    def _toks_of(img_path):
        img = Image.open(img_path).convert("RGB")
        return [{"x0": b[1][0], "y0": b[1][1], "x1": b[1][2], "y1": b[1][3],
                 "text": b[0], "score": 0.95} for b in T.read_screen(img)], img

    m_toks, m_img = _toks_of(os.path.join(HERE, "测试素材", "raid", "导航_成员页.jpg"))
    check("真实成员页 → members（不再误判成记录页）",
          RN.RaidNav.which_page(m_toks, m_img) == "members",
          str(RN.RaidNav.which_page(m_toks, m_img)))
    check("真实成员页不算记录页（7 位贡献值不能当伤害）",
          RN.RaidNav.on_record_page(m_toks, m_img) is False, "")
    check("成员页有主选单/队员两个页签可点",
          bool(RN.find_text(m_toks, RN.TAB_MAIN))
          and bool(RN.find_text(m_toks, RN.TAB_MEMBERS)),
          str(RN.find_text(m_toks, RN.TAB_MAIN)))

    r_toks, r_img = _toks_of(os.path.join(HERE, "测试素材", "raid", "记录_第1天_6条.jpg"))
    check("真实记录页仍认得出（改动没把它弄坏）",
          RN.RaidNav.which_page(r_toks, r_img) == "record",
          str(RN.RaidNav.which_page(r_toks, r_img)))
    a_toks, a_img = _toks_of(os.path.join(HERE, "测试素材", "raid", "导航_突袭页.jpg"))
    check("真实突袭主页仍是 raid（它没有联盟页签）",
          RN.RaidNav.which_page(a_toks, a_img) == "raid",
          str(RN.RaidNav.which_page(a_toks, a_img)))


def main():
    t_which()
    t_decide()
    t_whitelist()
    t_refuse()
    t_norm()
    t_walk()
    t_scroll_top()
    t_skip()
    t_raid_pages()
    print(f"\n结果：{OK} 通过 · {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
