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


class CaptureEngine:
    """把 Tk 客户端里那套滚采流程原样搬出来，供两个前端复用。"""

    # 拖动**步长**固定 3px（名单采集实测值），**步间隔**三档自适应（v1.59）。
    # 单屏 3.32s 里拖动占 2.12s，速度是唯一大杠杆：107px/s → 176px/s。
    # 10:01 那轮"试用大步 286px/s"之所以失败，是因为当时相关峰恒为 0，
    # 测到的 188px 是松手后惯性途中的假读数。现在 `_offset_from_content`
    # 能给出真实位移（重叠行反算），闭环校验才成立 —— 前提变了，结论才敢变。
    DRAG_STEP = A.DRAG_STEP_PX
    GAPS = (A.DRAG_STEP_GAP, 0.022, 0.017)     # 107 / 136 / 176 px/s
    DRAG_GAP = GAPS[0]                          # 起始档 = v1.58 原值，兼容旧引用
    # 每次滚多少行。2.5 → 2.0（v1.36）：5 轮真实样本里 165 人次漏了 7 条，
    # 全落在"步长偏了半行、边界行被两屏各裁一半"这种位置。滚 2 行时重叠 4 行，
    # 边界行在两屏都是完整行。代价是拖动次数 +25%（记录页 26 → 33 次，约 +30 秒）。
    # v1.59.3：3.0 → 2.0 定稿。3.0 试过一轮，**不划算**（真机 05:40）：
    #   · 单屏 3.28s vs 2.0 的 2.83s —— 拖 258px 本身多花 0.49s，抵消掉大半收益
    #   · 屏数 35 vs 48，净省只有 21s（115s vs 136s）
    #   · 代价是 1 条 conf 0.53 的坏行 + 两次「重叠 2/5」（票数掉到下限）
    #   · 计划 258px 实际滚出 2 行的情况出现 3 次，边界轻微露头 → det 检出碎片
    # 2.0 是"收益/风险"的拐点：重叠 4 条、单屏 2.83s、96/32 已验证。
    # ⚠ 永远不要用 2.5 / 3.5 这种非整数倍（05:27 实测出过 2 条半行残片）。
    SCROLL_ROWS = 2.0
    SCROLL_ROWS_MIN = 2.0                       # 重叠变薄 / 框太矮时的退守值

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

    def _drag_raw(self, cx, y_from, y_to, step_px, gap=None):
        """自己实现一次拖动，步长与步间隔都可配（`A.drag_at` 的间隔是写死的常量）。

        gap=None 时用当前速度档 `self._gap`（v1.59 闭环调速会改它）。
        """
        if gap is None:
            gap = getattr(self, "_gap", self.DRAG_GAP)
        u = ctypes.windll.user32
        u.SetCursorPos(int(cx), int(y_from))
        time.sleep(0.05)
        u.mouse_event(0x0002, 0, 0, 0, 0)          # LEFTDOWN
        time.sleep(0.06)
        n = max(2, int(abs(y_to - y_from) // max(1, step_px)))
        for i in range(1, n + 1):
            u.SetCursorPos(int(cx), int(round(y_from + (y_to - y_from) * i / n)))
            time.sleep(gap)
        # 终点压住不动、但持续发事件：游戏把"最后一刻的速度"当甩动。
        # v1.59：4×50ms → 3×45ms，松手后静默 180ms → 120ms（共省 0.125s/屏）。
        # 抖动期间光标基本不动，速度早已归零，次数减一次不影响防甩。
        for k in range(3):
            u.SetCursorPos(int(cx), int(y_to) + (1 if k % 2 else 0))
            time.sleep(0.045)
        u.SetCursorPos(int(cx), int(y_to))
        time.sleep(0.02)
        u.mouse_event(0x0004, 0, 0, 0, 0)          # LEFTUP
        time.sleep(0.12)                            # 松手后别动光标

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
        # 实际能拖多远（框矮时够不到计划距离）。v1.59：调速与坐标预测都必须用它，
        # 否则"框太矮只拖了 150px"会被当成"计划 215px 却只走了 150px"→ 误判甩动、一路降档。
        self._last_drag_px = abs(y_to - y_from)
        self._drag_raw(cx, y_from, y_to, self.DRAG_STEP)
        try:
            A.park_cursor(region, self.win)     # 悬停高亮会冒充"画面在动"
        except Exception:
            pass
        return True

    @classmethod
    def _rows_for_view(cls, view_h, pitch, want):
        """这个框高实际能整行滚几行。够不到就退回整数下限。

        为什么必须算这一步：`_drag_list` 的落点是
        `y_from = y0 + h*0.55` → `y_to = max(y0+12, y_from - dist)`，
        框矮时 `dist` 会被 12px 上边距截短。截出来的距离往往不是行距整数倍
        —— 那就又变成"卡片停在半行位置"，和 2.5 行一样的残片来源。

        框高 550 / 行距 86：可达 290px → 3 行 ✓
        框高 480 / 行距 86：可达 252px → 只够 2 行，退回 2.0
        """
        try:
            max_px = float(view_h) * 0.55 - 12.0
            rows = int(max_px // float(pitch)) if pitch else 2
        except Exception:
            rows = 2
        return float(max(2, min(int(want), rows)))

    @classmethod
    def _pick_rows(cls, n_recs, n_hit, good):
        """下一屏滚几行。返回 (行数, 新的连续达标计数)。

        认全了（≥5 条）且重叠 ≥3 才敢用上限 —— 重叠是 `_offset_from_content`
        的票源，票不够就会"坐标漂半行 → 同一条记录裂成两条"。而且要连续两屏
        达标才升，单屏达标就升会在漏字屏上来回抖。

        ⚠ 2026-11-08：`SCROLL_ROWS` 现在是 2.0（= 下限），所以这道闸门暂时是
        恒等的。**不是死代码** —— 它存在的理由是把"能不能多滚"的判断集中在一处。
        真要试 3.0 行时只改常量，闸门和测试都跟着生效；但别再试 2.5/2.5 行距
        这种非整数倍（见 `SCROLL_ROWS` 的注释）。
        """
        if n_recs >= 5 and n_hit >= 3:
            good += 1
        else:
            good = 0
        return (cls.SCROLL_ROWS if good >= 2 else cls.SCROLL_ROWS_MIN), good

    def _tune_speed(self, shift, dist):
        """按实测位移调拖动速度档位（0=107 / 1=136 / 2=176 px/s，v1.59）。

        入参的 `shift` 是**内容反算**出来的位移（`_offset` 的两屏差），不是
        模板匹配的即时结果 —— 后者在模糊帧上会给偏差值，05:27 那轮因此升降档
        横跳 6 次，而日志里 2.5 行一直很稳。内容反算晚一屏，但投票得出、可信。

        甩动判据是**相对误差**：只要实测和拖出距离对得上就是没甩。
        `shift is None`（量不出来）时**保持当前档不动** —— 不知道有没有甩，
        就不许加速。
        """
        top = len(self.GAPS) - 1
        lvl = getattr(self, "_lvl", 0)
        self._bad = getattr(self, "_bad", 0)
        self._good = getattr(self, "_good", 0)
        if not dist:
            return lvl
        if shift is None:
            self._bad += 1
        else:
            s, d = abs(int(shift)), int(dist)
            err = abs(s - d) / float(max(1, d))
            if err <= 0.15:
                self._good += 1
                self._bad = 0
            else:
                self._good = 0
                # 甩动（多滚）或没拖够（少滚）都比噪声严重，直接记两次。
                # 阈值留 15% 余量：起手死区 + 惯性尾巴本来就吃掉 10px 上下，
                # 卡太紧会把正常拖动判成甩动，一路降档反而更慢。
                self._bad += 2 if (err > 0.25 or s < 5) else 1
        if self._bad >= 2 and lvl > 0:
            lvl -= 1
            self._gap = self.GAPS[lvl]
            self._bad = self._good = 0
            self.say(f"位移不稳 → 拖动降到 {self.DRAG_STEP / self._gap:.0f}px/s"
                     f"（{self.DRAG_STEP}px/{int(self._gap * 1000)}ms）", "WARN")
        elif self._good >= 2 and lvl < top:
            lvl += 1
            self._gap = self.GAPS[lvl]
            self._bad = self._good = 0
            self.say(f"位移稳定 → 拖动升到 {self.DRAG_STEP / self._gap:.0f}px/s"
                     f"（{self.DRAG_STEP}px/{int(self._gap * 1000)}ms）")
        self._lvl = lvl
        return lvl

    def _shift_by_template(self, prev_raw, cur_raw):
        """用模板匹配求当前帧相对上一帧的位移。返回 (px, 峰值)。正数 = 内容上移。

        为什么不用"整帧相关"（旧的 `A.measure_vshift_signed`）：那个把每行压成
        一个笔迹强度标量再找相关峰。这个画面只有名字/数字在变、布局完全不动，
        大部分行压出来完全相同 —— 相关性最高的位移恒为 0（v1.56 整页崩掉、
        以及"回顶"把没到顶误判成到顶，都是它）。
        换成 ScrollUnroll / image-stitch 那一派的做法：从当前帧取**一条横带**，
        到上一帧里找它。只要那条带上有文字，位置就是唯一的。
        """
        try:
            import cv2
            import numpy as np
            a = np.asarray(self._crop(prev_raw).convert("L"), dtype=np.float32)
            b = np.asarray(self._crop(cur_raw).convert("L"), dtype=np.float32)
            h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
            if h < 80 or w < 80:
                return None, 0.0
            a, b = a[:h, :w], b[:h, :w]
            bh = max(24, h // 8)
            y0 = max(0, min(h - bh, h // 3))      # 避开被裁的上下边
            band = b[y0:y0 + bh, :]
            # 纯色/近乎纯色的横带，matchTemplate 会给一个假峰（常数区域相关系数
            # 没有定义，OpenCV 直接返回 1.0）—— 必须先看方差，没有纹理就不表态。
            if float(band.std()) < 8.0:
                return None, 0.0
            res = cv2.matchTemplate(a, band, cv2.TM_CCOEFF_NORMED)
            _mn, mx, _ml, loc = cv2.minMaxLoc(res)
            if mx < 0.55:                          # 没认出来，别硬报一个位移
                return None, float(mx)
            return int(loc[1] - y0), float(mx)
        except Exception:
            A.log_exc("hard-shift")
            return None, 0.0

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
            shift, corr = self._shift_by_template(prev, cur)
            # 向上拖时内容往下走，位移是**负的**；比大小时必须取绝对值，
            # 否则每次都被当成"没动"——这就是"拉不回顶部 / 一直撞满次数"的根因。
            if corr >= 0.55 and abs(shift or 0) < max(6.0, pitch * 0.35):
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

    # ---------------- v1.56 观测归并：物理位置是记录的唯一身份 ----------------

    @staticmethod
    def _compatible(a, b) -> bool:
        """两份观测能不能是同一条记录。

        位置相同一般是同一条，但**校准万一偏了一行**，相邻两条记录会被判成
        同一条 —— 那就是永久丢数据。所以再加一道"内容对得上"的门：
        同 BOSS / 同剩余血量 / 伤害差 20% 内 / 名字相似 0.5 —— 任一条成立。
        一条都不成立就当成两条（宁可多一条看得见的坏行，也不能少一条）。
        """
        ba, bb = (a.get("boss") or ""), (b.get("boss") or "")
        if ba and ba == bb:
            return True
        pa, pb = (a.get("pct") or ""), (b.get("pct") or "")
        if pa and pa == pb:
            return True
        ad, bd = a.get("damage"), b.get("damage")
        if ad and bd and abs(ad - bd) <= max(abs(ad), abs(bd)) * 0.2:
            return True
        ma, mb = (a.get("member") or ""), (b.get("member") or "")
        if ma and mb and CaptureEngine._name_sim(ma, mb) >= 0.5:
            return True
        return False

    _OBS_FIELDS = ("member", "boss", "pct", "damage")

    @classmethod
    def _merge_obs(cls, old, new) -> None:
        """同一位置的多份观测**逐字段投票**：多数胜，平票才看置信度。

        为什么投票而不是"取分最高"：同一行被相邻 3 屏各读一次，某个字段
        错 1 次、对 2 次时，取最高分只会在两份里挑一份（可能挑错），而投票
        能直接把错的那一份投掉。
        这就是"多识别几次"的正确形态 —— **次数本来就够，以前没投票**。
        """
        def _snap(d):
            return {"member": d.get("member"), "boss": d.get("boss"),
                    "pct": d.get("pct"), "damage": d.get("damage"),
                    "damage_raw": d.get("damage_raw"), "conf": d.get("conf")}

        obs = old.get("_obs")
        if not obs:
            obs = [_snap(old)]
            old["_obs"] = obs
        obs.append(_snap(new))

        for k in cls._OBS_FIELDS:
            votes = {}
            for o in obs:
                v = o.get(k)
                if v in (None, ""):
                    continue          # 缺字段不投票，别把"空"投成多数
                votes.setdefault(v, []).append(o.get("conf") or 0)
            if not votes:
                continue
            win = max(votes, key=lambda v: (len(votes[v]), max(votes[v])))
            if old.get(k) != win:
                old[k] = win
                if k == "damage":
                    src = max((o for o in obs if o.get("damage") == win),
                              key=lambda o: o.get("conf") or 0)
                    old["damage_raw"] = src.get("damage_raw") or ""
        if (new.get("conf") or 0) > (old.get("conf") or 0):
            old["conf"] = new["conf"]
        for k in ("member_note", "boss_note"):
            if not (old.get(k) or "") and (new.get(k) or ""):
                old[k] = new[k]

    def _absorb(self, rows, rec, pitch) -> str:
        """把一份新观测并进 rows（按 `_gy` 定位）。返回 'new' / 'upd'。

        `rows` 永远按 `_gy` 升序 —— 顺序就是实机顺序，不依赖任何"插到第几个"。
        """
        gy = rec.get("_gy")
        if gy is None:
            rows.append(rec)
            return "new"
        gy = int(gy)
        lo, hi = 0, len(rows)
        while lo < hi:                      # 二分找插入点
            mid = (lo + hi) // 2
            if int(rows[mid].get("_gy") or 0) < gy:
                lo = mid + 1
            else:
                hi = mid
        for j in (lo - 1, lo):              # 只看左右两个邻居
            if not (0 <= j < len(rows)):
                continue
            old = rows[j]
            if abs(int(old.get("_gy") or 0) - gy) <= pitch * 0.45 and self._compatible(old, rec):
                self._merge_obs(old, rec)
                return "upd"
        rows.insert(lo, rec)
        return "new"

    def _absorb_all(self, rows, recs, pitch):
        """整屏观测并进 rows。返回 (新增几条, 更新几条)。"""
        add = upd = 0
        for r in recs:
            if self._absorb(rows, r, pitch) == "new":
                add += 1
            else:
                upd += 1
        return add, upd

    @staticmethod
    def _same_physical(a, b) -> bool:
        """够硬的身份证据 —— **专门用来算坐标**，不能用 `_compatible` 那种宽松判据。

        `_compatible` 允许"伤害差 20%"，是为了"同一位置的两份读数能不能并"
        （宁可并错也不能多一行）。但拿它配对算坐标就太松了：11 位伤害差 20%
        可以是两个完全不同的人，配错了坐标整屏都会歪。
        """
        ad, bd = a.get("damage"), b.get("damage")
        if ad is not None and bd is not None and ad == bd:
            return True
        ma, mb = (a.get("member") or ""), (b.get("member") or "")
        if not ma or ma != mb:
            return False
        pa, pb = (a.get("pct") or ""), (b.get("pct") or "")
        return bool(pa) and pa == pb and (a.get("boss") or "") == (b.get("boss") or "")

    def _offset_from_content(self, rows, recs, fallback, pitch):
        """从"重叠区里认出来的行"直接反算这一屏的**绝对**坐标。返回 (offset, 票数)。

        为什么不用增量：`offset += 位移` 每屏都有 ±px 噪声，26 屏攒得出两行；
        更要命的是像素相关在这类画面上经常量成 0（只有名字/数字在变、布局完全
        不动，相关性最高的位移就是 0 —— 04:37 那轮整页崩掉就是这个）。
        只要有两行被认出来，`旧行._gy − 观测.y` 就是绝对坐标，和历史无关。
        """
        cands = []
        for r in recs:
            y = r.get("_y")
            if y is None:
                continue
            for x in rows:
                gx = x.get("_gy")
                if gx is None:
                    continue
                if self._same_physical(x, r):
                    cands.append(int(gx) - int(y))
        if len(cands) < 2:
            return fallback, len(cands)
        cands.sort()
        mid = cands[len(cands) // 2]
        near = [c for c in cands if abs(c - mid) <= pitch * 0.6]
        if len(near) < 2:
            return fallback, len(near)
        near.sort()
        return near[len(near) // 2], len(near)

    def capture(self, region, from_nav: bool = False):
        """滚采整页。`from_nav=True` = 记录页是刚刚由导航点开的。

        v1.56 起核心只有一条规则：**物理位置是记录的唯一身份**。
        OCR 读到的每一行都不是"一条新记录"，而是"某个位置上的一份观测"——
        同一行在相邻 3 屏里各被读一次，那就是同一条记录的 3 份观测。

        于是三样东西一起消失：
          · `patch`（按下标改行）—— 新观测直接更新那个位置的那条
          · 补回（按锚点插行）—— 位置就是 `_gy`，插到该去的地方
          · 靠伤害数字判等 —— 位置相同就是同一条

        `rows` 永远按 `_gy` 有序，所以顺序不再依赖任何"插到第几个"的推算。
        """
        from raid_hard import MAX_SCREENS, read_hard    # 不含 UI，打包时不会拖进 tkinter
        _t_all = time.time()          # v1.59：整轮计时（含回顶），收工时报账
        try:
            eng = A.get_engine()
            rows = None
            pitch = 0.0
            # v1.59 调速状态：起始档 = v1.58 原值，靠 `_tune_speed` 逐屏往上爬
            self._lvl = 0
            self._gap = self.GAPS[0]
            self._bad = self._good = 0
            self._rows_good = 0
            self._last_reach = 0          # 上一屏实际拖出的像素，供调速做基准
            raw = A.grab(region)
            img = self._crop(raw)
            # 先认一帧拿到行距，再把列表拉回第一行（只往下滚，从中间起采会漏上面）
            try:
                _r0, _l0 = read_hard(img, eng, self._roster, self._bosses)
                if _l0.pitch:
                    self.say(f"行距 {_l0.pitch:.0f}px · 拖动 "
                             f"{self.DRAG_STEP}px/{int(self.GAPS[0] * 1000)}ms 起步"
                             f"（≈{self.DRAG_STEP / self.GAPS[0]:.0f}px/s，"
                             f"最多爬到 {self.DRAG_STEP / self.GAPS[-1]:.0f}px/s）")
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

            _offset = 0.0        # 这一屏的绝对坐标（从内容反算，不累加）
            _last_off = 0
            _moved = 0
            _still = 0
            _k = -1
            # 本屏计划滚几行。步长闸门（`_pick_rows`）仍在，只是当前上下限都是 2.0
            # —— 2.5 行会让卡片停在半行位置，上下沿各露一张残片，真机已验证。
            _rows_now = self.SCROLL_ROWS_MIN
            for _k in range(MAX_SCREENS):
                if self._stop.is_set():
                    break
                _t_s = time.time()
                recs, loc = read_hard(img, eng, self._roster, self._bosses)
                if not loc.cards:
                    self.say("这一屏没认到记录，收工" if rows is None else "这一屏没有卡片 → 到底")
                    break
                pitch = loc.pitch or pitch or 86.0
                self.last_pitch = pitch
                _drop = len(loc.cards) - len(recs)
                if _drop > 0:
                    self._edge_dropped = getattr(self, "_edge_dropped", 0) + _drop
                    A.log("hard", f"边缘残行丢弃 {_drop} 条"
                                  f"（这屏 {len(loc.cards)} 张卡 → {len(recs)} 条）")
                # ---- 打位置：**从重叠区的内容反算绝对坐标**，不累加、不靠像素
                _delta = 0
                _ncal = 0
                if rows:
                    _offset, _ncal = self._offset_from_content(rows, recs, _offset, pitch)
                    _delta = int(_offset - _last_off)
                    # 保留一位小数：滚 2 行出头时被 round 成 2 会掩盖真实位移
                    # （用户就是靠"位移几行"判断的，日志不能说谎）。
                    _moved = round(_delta / pitch, 1) if pitch else 0
                for _r in recs:
                    _r["_gy"] = int(_offset + int(_r.get("_y") or 0))
                if rows is None:
                    rows = []
                    self._absorb_all(rows, recs, pitch)
                    _add = len(recs)
                    self.say(f"首屏 {len(recs)} 条 · 行距 {pitch:.0f}px · "
                             + "、".join((r.get("member") or "?") for r in recs[:6]))
                else:
                    _add, _upd = self._absorb_all(rows, recs, pitch)
                    _hit = len(recs) - _add
                    _rows_now, self._rows_good = self._pick_rows(
                        len(recs), _hit, self._rows_good)
                    # 第二道闸：框高够不到整数倍时退回 2.0，别让 dist 被 12px
                    # 上边距截成非整数行（那等于又滚了个 2.7 行 = 半张卡）。
                    _sr = self._scroll_region(region)
                    _rows_now = self._rows_for_view(_sr[3] - _sr[1], pitch, _rows_now)
                    self.last_splice = {"added": _add, "hit": _hit,
                                        "overlap": len(recs), "patch": [],
                                        "shift_rows": _moved}
                    self.say(f"拼接 +{_add} 条（位移 {_moved:g} 行，"
                             f"重叠 {_hit}/{len(recs)}"
                             + (f" · 更新 {_upd} 条读数" if _upd else "")
                             + f" · {time.time() - _t_s:.1f}s）")
                # v1.59.1：调速改看**内容反算**的位移。
                # 原来用 `_shift_by_template` 的即时结果，但它在模糊帧上会给出偏差值
                # ——05:27 那轮因此升降档来回横跳 6 次（日志里 2.5 行明明稳定）。
                # 内容反算是重叠行投票出来的中位数，晚一屏才拿到，但可信。
                # 只在"这屏有新记录 + 至少 2 票"时调：到底那屏必然位移 0，不算数。
                if _ncal >= 2 and _add > 0 and getattr(self, "_last_reach", 0):
                    self._tune_speed(_delta, self._last_reach)
                _last_off = _offset
                self.emit_rows(rows)
                if self._stop.is_set():
                    break

                # ---- 到底判定：**连续两屏都没有新记录**。
                # 不用像素位移判：这类画面（只有名字/数字在变、布局完全不动）
                # 的像素相关经常量成 0，用它判到底会半路收工（04:37 那轮）。
                if _add == 0:
                    _still += 1
                    if _still == 1:
                        self.say("这一屏没有新记录（拖动可能没生效）→ 加大步长再试一次", "WARN")
                        if not self._drag_list(region, int(min(A.DRAG_MAX, max(
                                A.MIN_DRAG_PX, pitch * self.SCROLL_ROWS * 2)))):
                            break
                        raw, _s, _st = A.wait_settled(region, raw)
                        img = self._crop(raw)
                        continue
                    self.say("列表到底了：连续两屏没有新记录", "WARN")
                    try:
                        self._fill_bottom(rows, recs + self._extra_read(region, eng))
                        self.emit_rows(rows)
                    except Exception:
                        A.log_exc("hard-fill-bottom")
                    break
                _still = 0

                # ---- 滚一屏。位移只用来**预测**下一屏的起算点，下一屏会被
                # 内容反算覆盖掉；量不出来就按计划步长推进，保证坐标一定在走。
                dist = int(min(A.DRAG_MAX, max(A.MIN_DRAG_PX, pitch * _rows_now)))
                if not self._drag_list(region, dist):
                    self.say("拖动出不了手（框太小）→ 收工", "WARN")
                    break
                _prev = raw
                # v1.59：稳定等待保持默认 300ms。试过压到 240ms，只省 0.06s/屏，
                # 却让 176px/s 的惯性尾巴还没停就抓帧 —— 05:27 那轮两条 conf
                # 0.35/0.65 的模糊行就是从这儿来的。**这 60ms 不能省。**
                raw, _s, _st = A.wait_settled(region, raw)
                img = self._crop(raw)
                _sh, _co = self._shift_by_template(_prev, raw)
                # 实际拖出去的距离，留给下一屏调速当基准。
                # **不减 DRAG_SLOP**：05:27 的日志证明拖动 172px 内容就正好位移 2 行
                # （86×2），是 1:1 的；再减 15px 等于人为造出 9.5% 的恒定误差，
                # 叠加帧噪声就会频繁越过降档线。真有死区的机器上差 8.7%，
                # 仍在 15% 容差内，不会误判。
                _reach = max(1, getattr(self, "_last_drag_px", dist))
                self._last_reach = _reach
                if _sh is not None and abs(_sh) >= 5:
                    _offset += abs(int(_sh))
                else:
                    _offset += _reach
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
            # v1.59：把速度账当场报出来 —— 提速方案是否生效，看这一行就够
            _el = time.time() - _t_all
            _n_screen = max(0, _k + 1)
            _gap = getattr(self, "_gap", self.GAPS[0])
            self.say(f"收工：{len(_rows)} 条 · {_people} 名成员 · {_el:.0f}s"
                     f"（{_n_screen} 屏 · 平均 {_el / max(1, _n_screen):.2f}s/屏 · "
                     f"拖动 {self.DRAG_STEP / max(0.001, _gap):.0f}px/s）")
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
