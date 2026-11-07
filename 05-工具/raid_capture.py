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

    def _insert_in_order(self, rows, rec, recs, idx, base):
        """把 rec 插回 rows 里它该在的位置（v1.45）。

        优先找"屏幕上它下面那一行"在 rows 里的**实际位置**当锚点 —— 这是精确的。
        v1.43 用的是 `base = len(rows) - len(last_rows)`，可那个基准假设"rows 尾部
        正好是上一屏的 recs"，补回的行一插进来就不成立了，越补越偏
        （实测实机第 3 条 `班长` 被插到工具第 1 条）。

        锚点顺序：下方邻居 → 上方邻居 → 才退回 base+idx（整屏都是新行时没邻居可比）。
        """
        for nxt in recs[idx + 1:]:
            pos = next((j for j, x in enumerate(rows) if self._same_rec(x, nxt)), None)
            if pos is not None:
                # 屏号/坐标跟着锚点走（v1.47）：回扫补进的行原本没有 `_pass`，
                # 不继承的话最终排序会把它们全甩到 9999 那一档的末尾。
                rec.setdefault("_pass", rows[pos].get("_pass", 9999))
                rec["_y"] = int(rows[pos].get("_y") or 0) - 1      # 排在锚点前面
                rec.setdefault("_gy", (int(rows[pos].get("_gy") or 0) or 1 << 29) - 1)
                rows.insert(pos, rec)
                return
        for prv in reversed(recs[:idx]):
            pos = next((j for j, x in enumerate(rows) if self._same_rec(x, prv)), None)
            if pos is not None:
                rec.setdefault("_pass", rows[pos].get("_pass", 9999))
                rec["_y"] = int(rows[pos].get("_y") or 0) + 1      # 排在锚点后面
                rec.setdefault("_gy", (int(rows[pos].get("_gy") or 0) or 1 << 29) + 1)
                rows.insert(pos + 1, rec)
                return
        rows.insert(min(max(0, base + idx), len(rows)), rec)

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
        if not ma or not mb:
            return False
        # 伤害不等时，成员名**不能要求严格相等** —— 残读那一行名字往往也带错字
        # （实测 `小卡卡` 被读成 `小卡卜`，严格相等直接把这对挡在门外）。
        # 用和"伤害相同"分支一致的相似度门槛：短名 0.5、长名 0.6。
        _nr = difflib.SequenceMatcher(None, ma, mb).ratio()
        if _nr < (0.5 if min(len(ma), len(mb)) <= 3 else 0.6):
            return False
        pa, pb = (a.get("pct") or "").strip(), (b.get("pct") or "").strip()
        if pa and pb and pa != pb:
            return False
        # 两端都有血量时必须相同（真打两刀 BOSS 已经掉血，百分比不会一样）。
        # **一端缺失时**（实测 23:01 MAO 那条整行读歪、血量没认出来）改用伤害
        # 近似判断：同一个人对同一个 BOSS、伤害差 0.1% 以内，只能是同一刀。
        big = max(abs(ad), abs(bd))
        if big > 0 and abs(ad - bd) <= big * 0.001:
            return True
        # 相邻残读（v1.47）：**血量完全相同 + 一方 BOSS 没读到** → 同一条。
        #
        # 实测 `小卡卡·医师 6,712,891,076 / 93.3%` 与 `小卡卜 0,712,091,076 / 93.3%`：
        # 名字差一字、伤害读坏两位（首位 6→0、第五位 8→0）、BOSS 空。
        # 伤害数字完全不可比（差 8.4 倍），所以只能靠**血量** —— 真打两刀时
        # BOSS 已经掉血，同一个人对同一个 BOSS 不可能出现两个相同的百分比。
        # 一方 BOSS 空是必要条件：两侧 BOSS 都有值时必须已经相等（上面查过）。
        if pa and pa == pb and (not (a.get("boss") or "").strip()
                                or not (b.get("boss") or "").strip()):
            return True
        return False

    @classmethod
    def _contains(cls, rows, rec):
        return any(cls._same_rec(r, rec) for r in rows)

    last_merges = []          # 最近一次 _dedupe 合并掉的 (保留项, 被并项)

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
        out, merged, merges = [], 0, []
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
            # 留一笔"谁被并到谁"：用户看到条数变少时，能立刻分清
            # 是误合并还是根本没采到（这两种在日志里本来长得一样）。
            merges.append((dup, r))
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
        CaptureEngine.last_merges = merges
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

    # ---------------- v1.54 严格采集：重开再扫 ----------------

    def _reopen_record(self, region, eng):
        """关掉记录弹窗 → 重新点开。返回 (ok, 说明)。

        为什么关掉重开，而不是就地滚回顶部：反向扫那 50 多屏里 `本屏重叠 6/6`
        说明它几乎什么都没做（补进 2 条花 3 分钟）；而重开后列表必然回顶，
        第二遍和第一遍的累计位移都从 0 起算，`_gy` 天然同坐标系 —— 重叠区的
        比对才有意义。

        关弹窗用**点外面**（用户截图实测有效）：弹窗大约占 27%~64% 宽 × 20%~83% 高，
        所以 (8%, 50%) / (92%, 50%) 一定在弹窗外。每点一次都用 `on_record_page`
        复核列表是不是真没了；两次都没关掉，再补一发 ESC。
        """
        import raid_nav as rn
        import raid_ocr as ro

        x0, y0, x1, y1 = region
        w, h = max(1, x1 - x0), max(1, y1 - y0)

        def _gone() -> bool:
            try:
                _img = self._crop(A.grab(region))
                return not rn.RaidNav.on_record_page(ro.tokens(_img, eng), _img)
            except Exception:
                A.log_exc("deep-close")
                return False

        _closed = False
        for rx, ry in ((0.08, 0.50), (0.92, 0.50)):
            if self._stop.is_set():
                return False, "已取消"
            A.click_at(int(x0 + w * rx), int(y0 + h * ry))
            time.sleep(0.55)
            if _gone():
                _closed = True
                break
        if not _closed:
            # 点空白没用 → ESC。可能连突袭页一起关掉，下面的 goto_record 会逐级补回来。
            try:
                A.tap_key(0x1B)
            except Exception:
                A.log_exc("deep-esc")
            time.sleep(0.7)

        hook = getattr(self, "reenter", None)
        if hook is None:
            return False, "没有导航能力（宿主没提供 reenter）"
        try:
            ok, msg = hook(region)
        except Exception as e:
            A.log_exc("deep-reenter")
            return False, f"重开记录页出错：{e}"
        if not ok:
            return False, f"重开没成功（{msg}）"
        # 弹窗没关掉时 goto_record 会直接回"已经在联盟记录页"（ok=True）——
        # 那种情况下列表还停在底部，第二遍会从中间起扫，`_gy` 全偏。
        # Engine 会把它记进 `_entered_by_nav`（只有真的重新点开才是 True）。
        if not getattr(self, "_entered_by_nav", True):
            return False, "记录页没关掉（还停在原页面）→ 无法从头再扫"
        return True, f"已重开记录页（{msg}）"

    def _second_pass(self, region, eng, rows):
        """严格采集第二遍：从列表顶部再扫一遍，只增补不重建。

        和正向那套的区别：
          · 不动 rows 的既有内容，只把"没见过"的行插进来；
          · 不建 last_rows、不做 patch —— 那是"连续两屏"的机制，这一遍独立成段，
            去重全靠 `_contains` + `_dedupe`；
          · `_gy` 用**重叠区反校**，落在第一遍的坐标系里（见下）。

        返回扫过的屏数（日志用）。
        """
        from raid_hard import MAX_SCREENS, read_hard

        _off2 = 0          # 这一遍自己的累计位移（从顶部起算）
        _k = 0
        raw = A.grab(region)
        img = self._crop(raw)
        for _k in range(MAX_SCREENS):
            if self._stop.is_set():
                break
            recs, loc = read_hard(img, eng, self._roster, self._bosses)
            if not loc.cards:
                break
            _pitch = loc.pitch or self.last_pitch or 0
            # 反校：拿"已经在表里"的行比一遍，取中位数修正累计位移。两遍的拖动
            # 次数不一定一样（正向有过"加大步长再试一次"），不校准的话第二遍
            # 补进的行会整体偏移几行 —— 那正是"滑得越多越容易乱"的同一个病。
            _deltas = []
            for _r in recs:
                _old = next((x for x in rows if self._same_rec(x, _r)), None)
                if _old is not None and _old.get("_gy") is not None:
                    _deltas.append(int(_old["_gy"]) - (_off2 + int(_r.get("_y") or 0)))
            if _deltas:
                _deltas.sort()
                _off2 += _deltas[len(_deltas) // 2]
            _new = 0
            for _i, _r in enumerate(recs):
                if self._contains(rows, _r):
                    continue
                # 第二遍新行排在 9000+，同一 `_gy` 时让第一遍的读数优先
                _r.setdefault("_pass", 9000 + _k)
                self._insert_in_order(rows, _r, recs, _i, len(rows))
                # 插入没给 `_gy`（找不到锚点）时，用这一遍的物理位置兜底
                _r.setdefault("_gy", _off2 + int(_r.get("_y") or 0))
                _new += 1
            if _new:
                # **原地换内容**（v1.54）：`_dedupe` 返回的是新列表，如果只把
                # 局部名字重绑过去，调用方（capture）手里还是旧对象 —— 它会拿
                # 一份"没有这一屏新增"的列表继续跑，第二遍等于白扫。
                rows[:] = self._dedupe(rows)[0]
                self.emit_rows(rows)
            # 表格跟着走（和回扫那套一致）
            _sig = getattr(self, "scrollRow", None)
            _pos = next((j for j, x in enumerate(rows)
                         if any(self._same_rec(x, _r) for _r in recs)), None)
            if _pos is not None and _sig is not None:
                _sig.emit(_pos)
            if _k % 5 == 0:
                _hit = sum(1 for x in recs if self._contains(rows, x))
                self.say(f"严格第二遍 {_k} 屏 · 累计 {len(rows)} 条 · "
                         f"本屏重叠 {_hit}/{len(recs)}"
                         + (f" · 新增 {_new} 条" if _new else ""))
            if not self._drag_list(region, int(min(A.DRAG_MAX, max(
                    A.MIN_DRAG_PX, _pitch * self.SCROLL_ROWS)))):
                break
            _prev = raw
            raw, _s, _st = A.wait_settled(region, raw)
            _sh, _co = self._measure(region, _prev, raw)
            img = self._crop(raw)
            if _sh is not None and _co >= 0.3:
                _off2 += abs(int(_sh))
            if _sh is not None and abs(_sh) < 5:
                self.say(f"第二遍到底（位移 {_sh}px）· 共 {_k + 1} 屏")
                break
        return _k + 1

    # ---------------- v1.54 成员额度锚定 ----------------

    @classmethod
    def _name_sim(cls, u: str, n: str) -> float:
        """两个昵称的相似度；**不做书写系统逐位校验**。

        逐屏的 `same_row` 要求书写系统逐位一致，`小卡ト`（末位是片假名）就被
        那一道挡在门外——相似度 0.67 也没用。收工时的锚定有额度兜底，可以松掉
        这一道：改错了会让人变成 4 刀，试算当场就能发现。
        """
        if not u or not n:
            return 0.0
        return difflib.SequenceMatcher(None, u, n).ratio()

    def _quota_after(self, rows, target: str) -> int:
        """把 target 的行按 `_same_rec` 去一遍重之后还剩几条（额度试算）。

        为什么不能直接 `cnt[target] + cnt[u]`：残读那条**往往就是某一刀的重复**
        （血量和 BOSS 都对得上），改名后会被去重吃掉，总数不变。
        实测 `小卡ト`(1 条) → `小卡卡`(已 3 条)：直接相加得 4 会误判"满额"，
        可按 `_same_rec` 去重后仍然是 3 —— 这一条本来就该并进去。
        """
        sub = [dict(r) for r in rows if (r.get("member") or "") == target]
        kept, _m = self._dedupe(sub)
        return len(kept)

    def _reconcile_partial(self, rows, pitch: float = 0.0):
        """收工整理：把"缺 成员 / BOSS / 伤害"的残行并掉或丢弃。

        实测 03:54 那轮，两轮严格采集跑完仍留着三条：

            #34  (空)   (空)   11,000,000,000   (空)    conf 0.859
            #35  空木   邪魔   11,096,802,360   11.1%   conf 0.999   ← 干净读数
            #36  空本   (空)   11 000 902 260   (空)    conf 0.692

        `空木` 已有 3 条，96 条真数据全在，98 = 96 + 这两条多余。
        **它们不是"没读到"，是同一条记录被读成了三行** —— `_same_rec` 要求伤害
        相对差 <= 0.1%，差 0.86% 就认不出，`_contains` 也就不认，三行全留在表里。

        为什么不靠"再扫一遍"：两轮的几何**完全一样**（同一框、同一步长、同一落点），
        同一物理行每次都落在同一个 y 上，读歪的方式一模一样 —— 两次相同的观察
        互相救不了。严格采集只治"漏采"，治不了"读歪后多出一行"。

        三条证据（只处理不完整的行，完整行之间一律不动）：
          · 名字：相似度 >= 0.5 且唯一最优（次优差 0.15 以上）—— 主判据
          · 位置：`_gy` 差 <= 0.6 个行距 —— 残行没名字时的唯一依据，
            并要求对方那个人已经满 3 刀（多出来的这条必然是重复）
          · 数值：伤害相对差 <= 5%（有名）/ 3%（无名）—— 否决权

        只允许"残行 -> 完整行"这一个方向。返回 (并掉几条, 丢弃几条, 说明串)。
        """
        if not rows:
            return 0, 0, ""
        pitch = float(pitch or self.last_pitch or 0) or 86.0

        def _cnt() -> dict:
            c = {}
            for r in rows:
                m = r.get("member") or ""
                if m:
                    c[m] = c.get(m, 0) + 1
            return c

        merged = dropped = 0
        notes, gone = [], []
        i = 0
        while i < len(rows):
            bad = rows[i]
            miss = [k for k in ("member", "boss") if not (bad.get(k) or "").strip()]
            if bad.get("damage") is None:
                miss.append("damage")
            if not miss:
                i += 1
                continue
            bd = bad.get("damage")
            bname = (bad.get("member") or "").strip()
            bgy = bad.get("_gy")
            cnt = _cnt()

            scored = []
            for j, r in enumerate(rows):
                if j == i:
                    continue
                if not (r.get("member") and r.get("boss") and r.get("damage") is not None):
                    continue
                rel = None
                if bd is not None and r.get("damage"):
                    rel = abs(bd - r["damage"]) / max(abs(bd), abs(r["damage"]), 1)
                pos = None
                if bgy is not None and r.get("_gy") is not None:
                    pos = abs(int(bgy) - int(r["_gy"])) / pitch
                sim = self._name_sim(bname, r["member"]) if bname else 0.0
                scored.append((r, rel, pos, sim))

            target, why = None, ""
            if bname:
                pick = sorted(((sim, rel, r) for r, rel, pos, sim in scored
                               if sim >= 0.5 and rel is not None and rel <= 0.05),
                              key=lambda t: -t[0])
                if pick and (len(pick) == 1 or pick[0][0] - pick[1][0] >= 0.15):
                    target = pick[0][2]
                    why = f"名字像 {pick[0][0]:.2f} · 伤害差 {pick[0][1]:.2%}"
            else:
                # 没名字就只能靠位置；再加一条"那个人已经满 3 刀"——
                # 满额的人不可能再多一刀，眼前这条必然是重复，并掉是安全的。
                pick = sorted(((pos, rel, r) for r, rel, pos, sim in scored
                               if pos is not None and pos <= 0.6
                               and rel is not None and rel <= 0.03
                               and cnt.get(r["member"], 0) >= 3),
                              key=lambda t: t[0])
                if pick and (len(pick) == 1 or pick[1][0] - pick[0][0] >= 0.25):
                    target = pick[0][2]
                    why = f"同位置（差 {pick[0][0]:.2f} 行）· 伤害差 {pick[0][1]:.2%}"

            if target is not None:
                _n = target.get("member_note") or ""
                target["member_note"] = ((_n + " · ") if _n else "") + \
                    f"并掉 1 条残读（{bname or '无名字'}，{why}）"
                notes.append(f"{bname or '无名字'}→{target['member']}（{why}）")
                rows.pop(i)
                merged += 1
                continue
            if not bname and not (bad.get("boss") or "").strip():
                # 既没成员又没 BOSS：无法归属到任何人，留着只是让用户手工删。
                gone.append(f"伤害 {bad.get('damage_raw') or bad.get('damage') or '?'} · "
                            f"conf {float(bad.get('conf') or 0):.2f}")
                rows.pop(i)
                dropped += 1
                continue
            i += 1

        msg = ""
        if merged:
            msg = f"残行归并：{merged} 条（" + "；".join(notes[:3]) + \
                  ("…" if len(notes) > 3 else "") + "）"
        if dropped:
            msg += ("；" if msg else "") + \
                f"丢弃 {dropped} 条无名无 BOSS 的残行（" + "；".join(gone[:2]) + \
                "）—— 无法归属到任何人；如果总数比实机少，就是它"
        return merged, dropped, msg

    def _anchor_members(self, rows, limit: int = 3):
        """收工前的成员锚定：用「库闭集 + 3 刀额度」把读歪的名字归位。

        逐屏归位（`read_hard` 里的 `roster_fix`）只有字面证据，而且要求
        `same_row` 的书写系统逐位一致 —— `小卡ト`（末位片假名）就被那道挡住，
        相似度 0.67 也用不上。收工时多了一样逐屏没有的东西：**账本**。
        一个人一轮最多 3 刀，所以"改名"是可以验算的。

        条件（全部成立才改）：
          1) 长度 >= 3（两字名不做模糊归档：`大白`/`萧白` 是两个真人）
          2) 等长 + 相似度 >= 0.55，或 长度差 1 + 相似度 >= 0.65
          3) 候选**恰好一个**
          4) 试算额度：改完 + 去重后不超过 3 条

        改不动的只给建议、不改字 —— 改错了就再也看不出来，不改至少复核还能靠
        「不在库」提醒人。返回 (改了个人名数, 说明串)。
        """
        roster = self._roster or {}
        entries = roster.get("names") if isinstance(roster, dict) else None
        names = list(entries or {})
        if not names or not rows:
            return 0, ""

        cnt = {}
        for r in rows:
            m = r.get("member") or ""
            if m:
                cnt[m] = cnt.get(m, 0) + 1
        pool = set(names)
        unknown = [m for m in cnt if m not in pool]
        if not unknown:
            return 0, ""

        fixed, tips = [], []
        _tipped = set()      # 已经在 tips 里说过的名字，别再补一句"未改"
        # 先处理出现次数多的：额度试算更稳（只露一次的多半是残读）
        for u in sorted(unknown, key=lambda n: -cnt[n]):
            if len(u) < 3:
                continue
            cands = []
            for n in names:
                _d = abs(len(u) - len(n))
                _r = self._name_sim(u, n)
                if (_d == 0 and _r >= 0.55) or (_d == 1 and _r >= 0.65):
                    cands.append((_r, n))
            cands.sort(reverse=True)
            if len(cands) != 1:
                if cands:
                    tips.append(f"{u} 有 {len(cands)} 个候选，不敢改")
                    _tipped.add(u)
                continue
            _r, target = cands[0]
            # 试算：先按 target 算一遍额度，超了就原样退回。
            _bak = [(rec, rec.get("member")) for rec in rows
                    if (rec.get("member") or "") == u]
            for rec, _ in _bak:
                rec["member"] = target
            _after = self._quota_after(rows, target)
            if _after > limit:
                for rec, _old in _bak:
                    rec["member"] = _old
                tips.append(f"{u}≈{target}（{_r:.2f}）但算下来 {_after} 刀，不归")
                _tipped.add(u)
                continue
            for rec, _old in _bak:
                _note = rec.get("member_note") or ""
                rec["member_note"] = ((_note + " · ") if _note else "") + \
                    f"额度锚定 {u}→{target}（相似 {_r:.2f}，去重后 {_after}/3 刀）"
            fixed.append(f"{u}→{target} {cnt[u]} 条（去重后 {_after}/3 刀）")
            cnt[target] = cnt.get(target, 0) + cnt[u]
            cnt.pop(u, None)

        for u in unknown:
            if u not in cnt or u in _tipped:
                continue
            best, br = None, 0.0
            for n in names:
                _r = self._name_sim(u, n)
                if _r > br:
                    best, br = n, _r
            if best and br >= 0.25:
                tips.append(f"{u}≈{best}（{br:.2f}）未改")

        msg = ""
        if fixed:
            msg = "成员锚定：" + " · ".join(fixed)
        if tips:
            # 只报前 4 条：一屏日志塞 17 个"未改"没人看，反而把真正改掉的挤没了。
            _head = "；".join(tips[:4])
            _more = f"；另有 {len(tips) - 4} 个" if len(tips) > 4 else ""
            msg += ("；" if msg else "成员锚定：") + _head + _more
        if msg:
            msg += " —— 改不动的请人工核（复核列会点名）"
        return len(fixed), msg

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
                    # 导航进入时页面确实在第一行，但**第一行不一定在框里**：
                    # 框的上沿是按卡片列表拖的，顶部日期栏（第1天/第2天）压着
                    # 第一条卡片的上半截 —— 实测那条从头到尾没进过 CSV（v1.44）。
                    # 所以两种入口都回顶，代价约 +2 秒。
                    if from_nav:
                        self.say("刚由导航进入记录页，先顶一下（防止第一行被框上沿切掉）…")
                    else:
                        self.say("先拉回列表顶部…")
                    self._scroll_to_top(region, _l0.pitch)
                    raw = A.grab(region)
                    img = self._crop(raw)
            except Exception:
                A.log_exc("hard-top")
            # v1.52：从列表顶部起累计滚过的像素。每条记录的全局位置 = 这个 + 屏内 y，
            # 排序只认物理坐标，不再依赖"第几屏第一次看到"（那个会因漏行而偏）。
            _offset = 0
            for i in range(MAX_SCREENS):
                if self._stop.is_set():
                    break
                recs, loc = read_hard(img, eng, self._roster, self._bosses)
                # v1.52：全局物理位置 = 从顶部起累计滚动量 + 屏内纵坐标。
                # 这一行滚到哪一屏，这个值都不变 —— 所以排序不会被"第几屏才看到"带偏。
                for _r in recs:
                    _r["_gy"] = _offset + int(_r.get("_y") or 0)
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
                    # v1.48：首屏这 6 条**必须也打屏号**。之前漏了这一步，
                    # 它们在最终 sort 里全是默认的 9999，被后面每一屏的记录挤到末尾 ——
                    # 实测 02:32 那轮实机第 1 条 `大魔王·重装火神R` 直接排没了。
                    for _r in rows:
                        _r.setdefault("_pass", i)
                    self.last_pitch = pitch
                    # 带上名字：首屏丢了哪一条、读成什么，只有这里能看出来
                    self.say(f"首屏 {len(recs)} 条 · 行距 {pitch:.0f}px · "
                             + "、".join((r.get("member") or "?") for r in recs[:6]))
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
                    for _r in added:
                        _r.setdefault("_pass", i)
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
                        # 上一屏如果发生过合并，len(rows) 会比 len(last_rows) 还小，
                        # 这条减法就地变负数，`0 <= base+idx` 全不成立 —— patch 一条都
                        # 打不上（v1.48）。取非负，并在日志里报出来便于核对。
                        base = max(0, len(rows) - len(last_rows))
                        for idx, rec in info["patch"]:
                            if 0 <= base + idx < len(rows):
                                rows[base + idx] = rec
                    rows, merged = self._dedupe(rows + added)
                    if merged:
                        _d = "；".join(
                            f"{a_.get('member') or '?'}·{a_.get('boss') or '?'} "
                            f"{a_.get('damage_raw') or a_.get('damage')} ← "
                            f"{b_.get('damage_raw') or b_.get('damage') or '缺伤害'}"
                            for a_, b_ in CaptureEngine.last_merges[:3])
                        self.say(f"合并 {merged} 条重复：{_d}")
                    # **兜底不变量：这一屏看见的每一条，都必须已经在列表里。**
                    # 位移判偏时 m 会算大，边界行会被整条跳过（实测 12:47 那轮
                    # 24 条里完全没有 `小卡卡`，可它就在留存帧的第一行）。
                    # 漏的直接补进来，重复由 _dedupe 收掉 —— 宁可多一次比对，
                    # 不能让屏幕上明明出现过的记录丢掉。
                    lost = [r for r in recs
                            if r.get("member") and r.get("damage") is not None
                            and not self._contains(rows, r)]
                    if lost:
                        # 按这一屏里的原始下标插回去，**不能 append**：追加会让本该在
                        # 第 3 条位置的行跑到列表末尾，和实机的顺序对不上（v1.43）。
                        # 基准沿用 patch 那套 `base = len(rows) - len(last_rows)`。
                        base = len(rows) - len(last_rows)
                        for r in lost:
                            idx = next((i for i, x in enumerate(recs) if x is r), 0)
                            self._insert_in_order(rows, r, recs, idx, base)
                        rows, _m2 = self._dedupe(rows)
                        self.say(f"补回 {len(lost)} 条被位移跳过的行（已插回原位）")
                    last_rows = recs
                    fixed = len(info.get("patch") or [])
                    if shift:
                        _offset += abs(int(shift))       # v1.52 累计滚过的像素
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
            # ---- v1.54 严格采集：重开记录页，从头再扫一遍 ----
            #
            # v1.40~1.53 是"从底部反向扫一遍"。实测那 50 多屏里 `本屏重叠 6/6`
            # 说明它几乎什么都没做（补进 2 条，花 3 分钟）—— 反向时累计位移的
            # 坐标系和正向不一致，插入只能靠锚点，锚点一错就错一串。
            # 改成"关弹窗 → 重开 → 从上往下"：两遍都从顶部起算，`_gy` 同坐标。
            if getattr(self, "deep", False) and rows and not self._stop.is_set():
                _before_all = len(rows)
                _ok_re, _how_re = self._reopen_record(region, eng)
                self.say(f"严格采集：{_how_re}", "INFO" if _ok_re else "WARN")
                if not _ok_re:
                    # 重开失败就退回"拉回顶部"：功能等价，只是少了重新加载。
                    try:
                        self._scroll_to_top(region, pitch)
                    except Exception:
                        A.log_exc("deep-top")
                _p2 = self._second_pass(region, eng, rows)
                _added = len(rows) - _before_all
                if _added:
                    self.say(f"严格采集完成：第二遍 {_p2} 屏 · 补进 {_added} 条 "
                             f"→ 正向确实漏了，结算前建议都开着")
                else:
                    self.say(f"严格采集完成：第二遍 {_p2} 屏 · 一条没补 "
                             f"→ 正向已采全，这次严格模式白跑一趟")
            # v1.54：成员额度锚定放在最终排序**之前** —— 归位后可能撞成同一条，
            # 还能被下面那次去重收掉；报告也要早于"超过 3 条"，否则用户看到
            # 某人 4 条会先怀疑是重复采集。
            if rows:
                # v1.55：先把"缺字段的残行"并掉/丢掉，再归位成员名。
                # 顺序不能反：残行往往带着一个读歪的名字，先归位的话它会跟
                # 干净行抢同一个候选（两边都像 `空木`），反而更难判。
                try:
                    _rm, _rd, _rmsg = self._reconcile_partial(rows, pitch)
                    if _rmsg:
                        self.say(_rmsg)
                    if _rm or _rd:
                        self.emit_rows(rows)
                except Exception:
                    A.log_exc("hard-reconcile")
                try:
                    _na, _nmsg = self._anchor_members(rows)
                    if _nmsg:
                        self.say(_nmsg)
                    if _na:
                        rows, _mn = self._dedupe(rows)
                        self.emit_rows(rows)
                except Exception:
                    A.log_exc("hard-anchor")

            # 收工自检：这两条不拦流程，只把"可能有问题"当场说出来。
            # 以前要等导出后人工翻 CSV 才发现，代价太大。
            # v1.47 最终排序：**按"首次出现的屏号 + 屏内 y"**，不再依赖任何
            # "插到第几个"的推算。滚 2 行、重叠 4 行 → 每条首次出现的屏号单调递增；
            # 同屏内 OCR 直接给的顺序最可靠。补回/回扫怎么插都不影响最终结果。
            if rows:
                # v1.52：按**全局物理位置**排，不再按屏号。屏号会因漏行而偏，
                # 滑得越多偏得越狠（用户："越滑越容易出现排序错误"）。
                rows.sort(key=lambda r: (int(r.get("_gy") or 1 << 30),
                                         int(r.get("_pass", 9999)),
                                         int(r.get("_y") or 0)))
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
            _people = len({r["member"] for r in _rows if r.get("member")})
            self.say(f"收工：{len(_rows)} 条 · {_people} 名成员")
            # v1.55：条数对不上就当场说。一个人一轮 3 刀，差 2 条以上就不正常
            # （1 条可能是有人只打了 2 刀，见 `浩然菌` 那种），不然用户只能手工数。
            _short = 3 * _people - len(_rows)
            if _people and _short >= 2:
                self.say(f"⚠ {len(_rows)} 条 / {_people} 人 —— 满额应是 {3 * _people} 条，"
                         f"少 {_short} 条（可能漏采，也可能有人没打完）", "WARN")
            if getattr(self, "_edge_dropped", 0):
                self.say(f"（其中丢弃了 {self._edge_dropped} 条边缘残行 —— "
                         f"这个数偏大说明滚动的边界行裁得太狠）", "WARN")
        except Exception as e:
            self.say(f"采集出错：{e}")
            A.log_exc("hard-run")
        finally:
            self.emit_done()

    # ---------------- 导出
