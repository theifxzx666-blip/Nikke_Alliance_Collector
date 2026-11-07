# -*- coding: utf-8 -*-
"""困难模式采集引擎：全部判据与采集流程，**不含任何 UI 依赖**。

为什么单独拆出来：Tk 客户端与 Qt 客户端必须共用同一套判据。
复制一份意味着以后改一处、另一处不同步 —— 这是本项目最忌讳的事。

宿主（客户端）需要提供：
    win         游戏窗口句柄
    rect_rel    框选比例，None = 整屏
    _stop       threading.Event
    _roster     昵称库
    _bosses     本期 BOSS 列表
    say(msg)    写日志
    emit_rows(rows)   数据更新
    emit_done()       收工

判据来源：`raid_core.splice` / `alliance_ocr.measure_vshift_signed` / `raid_hard.read_hard`。
"""
import ctypes
import difflib
import time
from datetime import datetime

import alliance_ocr as A
import raid_core as rc


class CaptureEngine:
    """把 Tk 客户端里那套滚采流程原样搬出来，供两个前端复用。"""

    # 拖动参数**固定**用名单采集实测值 (3px, 28ms)≈107px/s，不做运行时试探。
    # 10:01 那轮教训：试探把"大步 286px/s"选成了最优（188px），但那 188px 是松手
    # 后惯性途中的读数，实际每次滚多少不可控，紧接着就出现"重叠 2/5 对不上"。
    DRAG_STEP, DRAG_GAP = A.DRAG_STEP_PX, A.DRAG_STEP_GAP
    # 每次滚多少行。2.5 → 2.0（v1.36）：5 轮真实样本里 165 人次漏了 7 条，
    # 全落在"步长偏了半行、边界行被两屏各裁一半"这种位置。滚 2 行时重叠 4 行，
    # 边界行在两屏都是完整行。代价是拖动次数 +25%（记录页 26 → 33 次，约 +30 秒）。
    SCROLL_ROWS = 2.0

    # 给调试面板看的现场快照（不算逻辑，只是留档）
    last_pitch = 0.0
    last_splice = None

    def _crop(self, img):
        """只把框选范围交给识别；没框过就整屏。"""
        if not self.rect_rel:
            return img
        w, h = img.size
        x0, y0, x1, y1 = self.rect_rel
        box = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
        if box[2] - box[0] < 60 or box[3] - box[1] < 60:
            return img
        return img.crop(box)

    def _scroll_region(self, region):
        """拖动落点用的区域：框选过就用框，否则整幅有效画面。

        框选之后，**操作和识别都发生在同一块区域里**——起手点按框算，才不会拖到
        框外的标题栏或右侧滚动条上；`wait_settled` 仍比整屏，那是判断"画面停了没"，
        范围大一点更可靠。
        """
        if not self.rect_rel:
            return region
        x0, y0, x1, y1 = region
        w, h = x1 - x0, y1 - y0
        rx0, ry0, rx1, ry1 = self.rect_rel
        return (int(x0 + rx0 * w), int(y0 + ry0 * h),
                int(x0 + rx1 * w), int(y0 + ry1 * h))

    def _drag_raw(self, cx, y_from, y_to, step_px, gap):
        """自己实现一次拖动，步长与步间隔都可配（`A.drag_at` 的间隔是写死的常量）。"""
        u = ctypes.windll.user32
        u.SetCursorPos(int(cx), int(y_from))
        time.sleep(0.05)
        u.mouse_event(0x0002, 0, 0, 0, 0)          # LEFTDOWN
        time.sleep(0.06)
        n = max(2, int(abs(y_to - y_from) // max(1, step_px)))
        for i in range(1, n + 1):
            u.SetCursorPos(int(cx), int(round(y_from + (y_to - y_from) * i / n)))
            time.sleep(gap)
        # 终点压住不动、但持续发事件：游戏把"最后一刻的速度"当甩动
        for k in range(4):
            u.SetCursorPos(int(cx), int(y_to) + (1 if k % 2 else 0))
            time.sleep(0.05)
        u.SetCursorPos(int(cx), int(y_to))
        time.sleep(0.02)
        u.mouse_event(0x0004, 0, 0, 0, 0)          # LEFTUP
        time.sleep(0.18)                            # 松手后别动光标

    def _drag_list(self, region, dist, up=False):
        """在框内固定位置起手拖动，不依赖 scroll_once 的"边缘 1/8"默认点。

        为什么必须自己算起手点：`scroll_once` 反向拖时从**框顶 1/8** 起手，
        而框的上沿常把日期 tab 一起框进来 —— 拖在非列表区上，位移恒为 0
        （实测「位移 0px · 相关 1.00」，和"已到顶部"完全一样，无法分辨）。
        正向拖从**框底 1/8** 起手，也常常压在最后一行下面那片空白或滚动条上，
        实测位移只有 33px（预期 200px）。

        改成压在框内 45%/55% 处：那里一定压在某个卡片上。
        """
        sr = self._scroll_region(region)
        x0, y0, x1, y1 = sr
        cx = (x0 + x1) // 2
        h = max(1, y1 - y0)
        if up:                      # 看更早的记录：从上往下拖
            y_from = int(y0 + h * 0.45)
            y_to = int(min(y1 - 12, y_from + dist))
        else:                       # 看更新的记录：从下往上拖
            y_from = int(y0 + h * 0.55)
            y_to = int(max(y0 + 12, y_from - dist))
        if abs(y_to - y_from) < A.MIN_DRAG_PX // 2:
            return False
        self._drag_raw(cx, y_from, y_to, self.DRAG_STEP, self.DRAG_GAP)
        try:
            A.park_cursor(region, self.win)     # 悬停高亮会冒充"画面在动"
        except Exception:
            pass
        return True

    def _measure(self, region, a, b):
        """只在**框选区域**内量位移，且带方向。

        为什么必须裁到框内：`measure_vshift` 把每行压成一个笔迹强度标量，
        整屏里标题栏 / 日期 tab / 左右面板 / 底部按钮全都是不动的，那些行占多数，
        相关性最高的位移永远落在 0。实测（困难样张，框内内容移动 240px）：
            整屏测 → 0px（相关 0.63）      框内测 → 240px（相关 0.94）
        这正是"用户明明看见画面动了、程序却报 0px"的一半原因。
        另一半是方向：向上滚时内容下移，只搜正位移的函数同样只能给 0。
        """
        return A.measure_vshift_signed(self._crop(a), self._crop(b))

    @staticmethod
    def _same_rec(a, b):
        """两条记录是不是同一条：伤害相同 + 成员名相近。

        伤害是这一页最稳的字段；加成员名是为了不误伤普通模式"不同人打出相同伤害"。
        超短名（≤3 字）放宽到 0.5：11 位伤害相同已是主判据，成员名只是双保险。
        实测 15:30 那轮「蕭白/萧白」同伤害两字名 ratio 恰好 0.5，被 0.6 挡住
        → 同一条记录读出两种写法各留一行，人数也虚报 1。

        **伤害不等也可能同一条**（v1.26）：同一个人在**剩余血量百分比完全相同**的
        前提下，两屏读出的伤害只差千分之一以内 —— 这是同一刀的数字被读错一位。
        实测 21:36 那轮「村里响当当」的 `7,057,526,552` 和 `7,057,526,352`
        差 200，去重认不出，同一条记录占了两行。
        血量百分比相同是关键判据：真打两刀时 BOSS 已经掉血，百分比不可能一样。
        """
        ad, bd = a.get("damage"), b.get("damage")
        if ad is None or bd is None:
            return False
        ma, mb = a.get("member") or "", b.get("member") or ""
        if ad == bd:
            r = difflib.SequenceMatcher(None, ma, mb).ratio()
            if r >= 0.6:
                return True
            return min(len(ma), len(mb)) <= 3 and r >= 0.5
        if not ma or ma != mb:
            return False
        pa, pb = (a.get("pct") or "").strip(), (b.get("pct") or "").strip()
        if pa and pb and pa != pb:
            return False
        # 两端都有血量时必须相同（真打两刀 BOSS 已经掉血，百分比不会一样）。
        # **一端缺失时**（实测 23:01 MAO 那条整行读歪、血量没认出来）改用伤害
        # 近似判断：同一个人对同一个 BOSS、伤害差 0.1% 以内，只能是同一刀。
        big = max(abs(ad), abs(bd))
        return big > 0 and abs(ad - bd) <= big * 0.001

    @classmethod
    def _contains(cls, rows, rec):
        return any(cls._same_rec(r, rec) for r in rows)

    @staticmethod
    def _dedupe(rows):
        """整表去重：同一条记录只留一份，保持出现顺序。返回 (新列表, 合并条数)。

        为什么要**整表**去重，而不是只在新追加时比一次：
        重复不是"多追加了一次"这么简单——实测 12:43 那轮，早先一行读歪了伤害，
        后面又采到正确值，`patch` 把早先那条改成正确伤害之后，才和后面那条撞成一样。
        只在追加时比对，这种"事后撞车"永远发现不了。

        判据：**伤害值相同 + 成员名相近**。
          · 伤害是这一页最稳的字段（实测 6/6 全对），而且同一个人两次出手的伤害
            几乎不可能一模一样（11 位数字）；
          · 加成员名是为了避开普通模式"不同人打出相同伤害"的情形。
        命中时把缺的字段补上、置信度取高的，不新增行。
        """
        out, merged = [], 0
        for r in rows:
            rd = r.get("damage")
            dup = None
            if rd is not None:
                for old in reversed(out[-40:]):     # 只和最近的比，长列表里不会误撞
                    if not CaptureEngine._same_rec(old, r):
                        continue
                    dup = old
                    break
            else:
                # 伤害读残的行（实测 #87：`9,791,211,2` 末尾少了两位 → 解析成 None）：
                # 主判据拿不到伤害，但 成员 + BOSS + 剩余血量 三项全同就是同一条。
                # 不合并的话它会变成这个人的"第 4 条"，触发「超过 3 刀」误报。
                for old in reversed(out[-40:]):
                    if (old.get("damage") is not None
                            and (old.get("member") or "") == (r.get("member") or "")
                            and (old.get("boss") or "") == (r.get("boss") or "")
                            and (old.get("pct") or "")
                            and old.get("pct") == (r.get("pct") or "")):
                        dup = old
                        break
            if dup is None:
                out.append(r)
                continue
            merged += 1
            for k in ("boss", "pct"):
                if not dup.get(k) and r.get(k):
                    dup[k] = r[k]
            if (r.get("conf") or 0) > (dup.get("conf") or 0):
                # 伤害不等却判成同一条时（数字读错一位），谁的分高信谁的。
                # 但**残行的 damage 是 None**，绝不能拿 None 盖掉好读数（实测 #87
                # 的 conf 0.995 比正身的 0.981 高，一盖就把整行变成"缺伤害"）。
                if r.get("damage") is not None and dup.get("damage") != r.get("damage"):
                    dup["damage"] = r.get("damage")
                    dup["damage_raw"] = r.get("damage_raw") or dup.get("damage_raw") or ""
                dup["conf"] = r["conf"]
        return out, merged

    def _extra_read(self, region, eng):
        """到底时补一次**未裁剪**的整幅画面。

        框选的下沿是按"卡片列表"拖的，最后一张卡片的 BOSS 行常常正好落在框外
        （实测末条 `薯KI` 只有伤害没有 BOSS）。整幅画面里那行还在，
        多花一次识别（约 0.5 s）就能补回来 —— 只在到底时做一次。
        """
        try:
            from raid_hard import read_hard
            raw = A.grab(region)
            recs, _loc = read_hard(raw, eng, self._roster, self._bosses)
            return recs or []
        except Exception:
            A.log_exc("hard-extra-read")
            return []

    def _fill_bottom(self, rows, recs):
        """用**最后一屏**的完整读数，补齐缺字段的行。返回补了几个字段。

        为什么不再"往回滚一格"：往回滚是让内容往下走，最后一行只会更靠下、
        更容易被容器下沿压住，方向是反的。
        真实规律（10:05 留存帧实测）：某条记录被采到时正好压在框的下沿，
        BOSS 行落在框外；再滚一屏它就落到画面中部，读得完整——
        最后一帧里 `凛酱 / 铁匠 / 93.6%` 一条不缺。所以直接用最后一屏回填即可。

        匹配只用**伤害值**：同一个人的记录会连着好几条（凛酱连着 3 条），
        靠成员名匹配会串行，把别人的 BOSS 填到自己头上，比留空更糟。
        只有伤害缺失时才退回"成员名在屏内唯一"这个更弱的条件。
        """
        if not rows or not recs:
            return 0
        uniq = {}
        for c in recs:
            uniq[c.get("member")] = uniq.get(c.get("member"), 0) + 1
        filled = 0
        for r in rows:
            if r.get("boss") and r.get("pct"):
                continue                     # 这条不缺东西，不用管
            for c in recs:
                if r.get("damage") is not None and c.get("damage") is not None:
                    same = c["damage"] == r["damage"]
                else:
                    same = bool(c.get("member")) and (
                        c.get("member") == r.get("member")
                        and uniq.get(c.get("member")) == 1)
                if not same:
                    continue
                for k in ("boss", "pct"):
                    if not r.get(k) and c.get(k):
                        r[k] = c[k]
                        filled += 1
                if c.get("conf") and (r.get("conf") is None or c["conf"] < r["conf"]):
                    r["conf"] = c["conf"]
                break
        if filled:
            self.say(f"用最后一屏补全 {filled} 个字段（压在框下沿的那条）")
        return filled

    @staticmethod

    @staticmethod
    def _all_known(recs, rows) -> bool:
        """这一屏认到的记录，是不是已经全在已采列表里了。

        用来区分「真的到底了」和「真的拼不上」。列表到底时拖动不再带来新内容，
        弹窗还可能回弹一格，重叠核对会 0/5 全失败——但内容其实一条都不缺。
        10:05 那轮就是这样：最后一屏是 13~18 行，全是已有的，却报「可能缺行」收工，
        连带把「到底后回填最后一条 BOSS」那一步也跳过了（凛酱的 BOSS 因此为空）。
        """
        if not recs or not rows:
            return False
        have_d = {r.get("damage") for r in rows if r.get("damage") is not None}
        have_mb = {(r.get("member") or "", r.get("boss") or "") for r in rows}
        for r in recs:
            dmg = r.get("damage")
            if dmg is not None:
                # 读到伤害就必须按伤害认：**同一个人可以重复打同一个 BOSS**（特化刀），
                # (成员, BOSS) 不是唯一键，拿它兜底会把新的一刀误判成"已采过"，
                # 于是提前判定到底、后面整段丢掉。
                if dmg not in have_d:
                    return False
                continue
            if (r.get("member") or "", r.get("boss") or "") in have_mb:
                continue
            return False
        return True

    # 拖动参数**固定**用名单采集实测值 (3px, 28ms)≈107px/s，不做运行时试探。
    # 10:01 那轮教训：试探把"大步 286px/s"选成了最优（188px），但那 188px 是松手
    # 后惯性途中的读数，实际每次滚的多少完全不可控，紧接着就出现"重叠 2/5 对不上"。
    # 而 09:11 那轮（用户认可的那次）用的就是这个标准速度，每步稳定 2~3 行。
    DRAG_STEP, DRAG_GAP = A.DRAG_STEP_PX, A.DRAG_STEP_GAP

    def _scroll_to_top(self, region, pitch):
        """开始前把列表拉回第一行。

        程序只会往下滚，所以**从中间或底部开始采，上面的记录永远采不到**——
        实测就从中间起采，只拿到 6+2=8 条就报"到底"。
        判据照名单采集：反向拖到"画面连续两次不动"才算到顶；一次都没移动过时
        不敢判定（可能是本来就在顶部，也可能是拖动被权限拦住，两者画面表现一样）。
        """
        step = int(max(A.MIN_DRAG_PX, pitch * 2 + A.DRAG_SLOP))
        prev = A.grab(region)
        still = moved = 0
        for _i in range(16):
            if self._stop.is_set():
                return
            # **不能用 fast**：fast 把步间隔压到 0.45 倍（约 238px/s），正好落在
            # 名单采集实测的甩动危险区（250px/s 时 5~10 步就甩掉一整屏、两屏零重合）。
            if not self._drag_list(region, step, up=True):
                self.say("框太矮，拖不动 —— 重新框选一个更高的区域", "WARN")
                return
            cur, _s, _st = A.wait_settled(region, prev)
            shift, corr = self._measure(region, prev, cur)
            # 向上拖时内容往下走，位移是**负的**；比大小时必须取绝对值，
            # 否则每次都被当成"没动"——这就是"拉不回顶部 / 一直撞满次数"的根因。
            if corr >= 0.3 and abs(shift or 0) < max(6.0, pitch * 0.35):
                still += 1
                # **连续两次没动就收手，不管之前有没有移动过。**
                # 原来要求 moved>0 才算到顶，于是"一开始就不动"（已经在顶部，
                # 或拖动被权限拦住）会一路撞满 40 次，用户看着像卡死。
                if still >= 2:
                    self.say(f"已到列表顶部（回滚 {moved} 次）" if moved else
                             f"画面一直没动（位移 {shift}px · 相关 {corr:.2f}）—— "
                             f"已在顶部，或拖动被权限拦住（普通权限点不了管理员窗口）",
                             "INFO" if moved else "WARN")
                    return
            else:
                still = 0
                moved += 1
            prev = cur
        self.say(f"回滚 16 次仍未确认到顶（已滚 {moved} 次），按当前位置继续", "WARN")

    def capture(self, region, from_nav: bool = False):
        """滚采整页。`from_nav=True` = 记录页是刚刚由导航点开的，跳过回顶。

        为什么：刚点开的页面本来就在第一行，再拖一次既浪费时间（最多 16 次回滚），
        又会刷出「画面一直没动（位移 0px）—— 已在顶部，或拖动被权限拦住」这条
        误导性 WARN；而用户自己翻到中间再点开始时，**必须**回顶，否则漏采上面的记录。
        """
        from raid_hard import MAX_SCREENS, read_hard    # 不含 UI，打包时不会拖进 tkinter
        try:
            eng = A.get_engine()
            rows = None
            last_rows = None
            box = None
            pitch = 0.0
            last = None
            miss = back = 0
            raw = A.grab(region)
            img = self._crop(raw)
            # 先认一帧拿到行距，再把列表拉回第一行（只往下滚，从中间起采会漏上面）
            try:
                _r0, _l0 = read_hard(img, eng, self._roster, self._bosses)
                if _l0.pitch:
                    self.say(f"行距 {_l0.pitch:.0f}px · 拖动速度固定 "
                             f"{self.DRAG_STEP}px/{int(self.DRAG_GAP * 1000)}ms"
                             f"（≈{self.DRAG_STEP / self.DRAG_GAP:.0f}px/s，"
                             f"就是 09:11 那轮验证过的参数）")
                    if from_nav:
                        self.say("刚由导航进入记录页，列表本来就在第一行 —— 跳过回顶")
                    else:
                        self.say("先拉回列表顶部…")
                        self._scroll_to_top(region, _l0.pitch)
                        raw = A.grab(region)
                        img = self._crop(raw)
            except Exception:
                A.log_exc("hard-top")
            for i in range(MAX_SCREENS):
                if self._stop.is_set():
                    break
                recs, loc = read_hard(img, eng, self._roster, self._bosses)
                if not loc.cards:
                    self.say("这一屏没认到记录，收工")
                    break
                # v1.37：边缘残行被 read_hard 丢了（partial / clipped 缺 BOSS）。
                # 丢了多少必须留痕 —— 丢多了就是"明明 96 刀只采到 93 条"。
                _drop = len(loc.cards) - len(recs)
                if _drop > 0:
                    self._edge_dropped = getattr(self, "_edge_dropped", 0) + _drop
                    A.log("hard", f"边缘残行丢弃 {_drop} 条"
                                  f"（这屏 {len(loc.cards)} 张卡 → {len(recs)} 条）")
                if rows is None:
                    rows, last_rows, box, pitch = recs, recs, loc.box, loc.pitch
                    self.last_pitch = pitch
                    self.say(f"首屏 {len(recs)} 条 · 行距 {pitch:.0f}px")
                else:
                    shift, corr = None, 0.0
                    if last is not None and box:
                        x0, y0, x1, y1 = box
                        # 带方向：splice 只吃"内容上移"的正位移，反向滚时不能骗它
                        shift, corr = A.measure_vshift_signed(last.crop((x0, y0, x1, y1)),
                                                             img.crop((x0, y0, x1, y1)))
                    shift_px = shift
                    if corr < 0.3:
                        shift = None
                    added, info = rc.splice(last_rows, recs, shift, pitch)
                    self.last_splice = dict(info, measured_px=shift_px)
                    if info.get("same"):
                        miss += 1
                        if miss == 1 and box:
                            self.say(f"拖动没挪动（实测位移 {shift_px}px · 相关 {corr:.2f}）"
                                     f"→ 加大步长再试一次", "WARN")
                            self._drag_list(region, int(min(A.DRAG_MAX, max(
                                A.MIN_DRAG_PX, pitch * self.SCROLL_ROWS * 2))))
                            last = img
                            raw, _s, _st = A.wait_settled(region, raw)
                            img = self._crop(raw)
                            continue
                        self.say(f"列表到底了：拖动不再产生位移"
                                 f"（本次 {shift_px}px · 相关 {corr:.2f}）", "WARN")
                        try:
                            self._fill_bottom(rows, recs + self._extra_read(region, eng))
                            self.emit_rows(rows)
                        except Exception:
                            A.log_exc("hard-fill-bottom")
                        break
                    if added is None:
                        back += 1
                        if self._all_known(recs, rows):
                            # 拼不上不是缺行，是列表到底后回弹了一格：这一屏的内容我们全有。
                            # 必须走到底后的回填，否则最后一条的 BOSS 永远空着
                            # （10:05 那轮凛酱的「铁匠」就是这么丢的）。
                            self.say("这一屏的记录已全部采过 → 确认到底", "INFO")
                            try:
                                self._fill_bottom(rows, recs + self._extra_read(region, eng))
                                self.emit_rows(rows)
                            except Exception:
                                A.log_exc("hard-fill-bottom")
                            break
                        if back == 1 and last is not None:
                            # 只在真的拼不上时留两屏原图：下次不用靠猜是位移算错还是 OCR 读歪。
                            try:
                                d = A.OUT_DIR / f"诊断_拼接_{datetime.now():%Y%m%d_%H%M%S}"
                                d.mkdir(parents=True, exist_ok=True)
                                last.save(d / "上一屏.png")      # last 已经是框内图，别再裁一次
                                img.save(d / "这一屏.png")
                                self.say(f"拼接对不上（{info.get('why')}）→ 已存两屏供排查"
                                         f" → {d.name}，往回小步补采", "WARN")
                            except Exception:
                                self.say(f"拼接对不上（{info.get('why')}）→ 往回小步补采", "WARN")
                        if back <= 1 and box:
                            self._drag_list(region, int(max(A.MIN_DRAG_PX, pitch * 0.9)), up=True)
                            last = img
                            raw, _s, _st = A.wait_settled(region, raw)
                            img = self._crop(raw)
                            continue
                        self.say("补采后仍对不上，收工（可能缺行）", "WARN")
                        break
                    miss = back = 0
                    if info.get("patch"):
                        base = len(rows) - len(last_rows)
                        for idx, rec in info["patch"]:
                            if 0 <= base + idx < len(rows):
                                rows[base + idx] = rec
                    rows, merged = self._dedupe(rows + added)
                    if merged:
                        self.say(f"合并 {merged} 条重复（同一伤害只留一条）")
                    # **兜底不变量：这一屏看见的每一条，都必须已经在列表里。**
                    # 位移判偏时 m 会算大，边界行会被整条跳过（实测 12:47 那轮
                    # 24 条里完全没有 `小卡卡`，可它就在留存帧的第一行）。
                    # 漏的直接补进来，重复由 _dedupe 收掉 —— 宁可多一次比对，
                    # 不能让屏幕上明明出现过的记录丢掉。
                    lost = [r for r in recs
                            if r.get("member") and r.get("damage") is not None
                            and not self._contains(rows, r)]
                    if lost:
                        rows, _m2 = self._dedupe(rows + lost)
                        self.say(f"补回 {len(lost)} 条被位移跳过的行")
                    last_rows = recs
                    fixed = len(info.get("patch") or [])
                    self.say(f"拼接 +{info.get('added', 0)} 条（位移 {info.get('shift_rows')} 行，"
                             f"重叠 {info.get('hit')}/{info.get('overlap')}"
                             + (f" · 修正 {fixed} 条边缘读数" if fixed else "") + "）")
                self.emit_rows(rows)

                if self._stop.is_set():
                    break
                dist = int(min(A.DRAG_MAX, max(A.MIN_DRAG_PX, pitch * self.SCROLL_ROWS)))
                self._drag_list(region, dist)
                last = img
                raw, _s, _st = A.wait_settled(region, raw)
                img = self._crop(raw)
            # 收工自检：这两条不拦流程，只把"可能有问题"当场说出来。
            # 以前要等导出后人工翻 CSV 才发现，代价太大。
            _rows = rows or []
            _cnt = {}
            for _r in _rows:
                _m = _r.get("member") or "?"
                _cnt[_m] = _cnt.get(_m, 0) + 1
            _over = {m: n for m, n in _cnt.items() if n > 3}
            if _over:
                self.say("⚠ 有人超过 3 条（可能重复采集）："
                         + "、".join(f"{m} {n} 条" for m, n in list(_over.items())[:4]), "WARN")
            _miss = [i for i, r in enumerate(_rows, 1)
                     if not (r.get("member") and r.get("boss") and r.get("damage") is not None)]
            if _miss:
                self.say(f"⚠ 第 {_miss} 条缺字段（导出时会再点名）", "WARN")
            self.say(f"收工：{len(_rows)} 条 · {len({r['member'] for r in _rows})} 名成员")
            if getattr(self, "_edge_dropped", 0):
                self.say(f"（其中丢弃了 {self._edge_dropped} 条边缘残行 —— "
                         f"这个数偏大说明滚动的边界行裁得太狠）", "WARN")
        except Exception as e:
            self.say(f"采集出错：{e}")
            A.log_exc("hard-run")
        finally:
            self.emit_done()

    # ---------------- 导出
