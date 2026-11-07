"""联盟突袭的导航：找字 → 点字 → 校验页面。

为什么不写死坐标
================
用户的原话是「每次窗口都不一样的」——窗口位置、大小都可能变，写死像素必错。
阿里阿卡（ArcanaHelper）的做法是 3840 基准画布 + 比例换算；它成立的前提是
**页面布局不变**。但这里连窗口尺寸都在变，比例也会跟着失真。

所以改成**文字锚点**：现场截图 → OCR 找出「联盟突袭」「联盟记录」这几个字 →
点它们的中心。字号、位置、缩放全都不用管，因为坐标是从当前这一帧里读出来的。

v1.1 补的两段（用户实测：站在大厅点开始，直接报"当前页面既没有…"）
------------------------------------------------------------------------
1. **大厅 → 联盟概览**：大厅右侧竖排图标里有「联盟」，坐标白名单与实测锚点
   直接沿用名单采集验证过的 `NAV_SPEC[0]`，不新造。
2. **其它页 → 大厅**：按 ESC 逐级回退，**每按一次都重新判页面指纹**，
   一认出来就停（多按会退过头）。

代价与守卫
----------
OCR 会认错，所以定了几条硬规矩（延续项目「不盲点」铁律）：
  · 只认分数 ≥ 0.9 且文本完全匹配的 token；
  · 「找到词」和「允许点」是同一件事（必须落在页面白名单区域内）；
  · 同一页出现多个候选时，只取**页面下半部**那个（上半部可能是标题）；
  · 每个页面切换都**轮询校验**，超时就停，并告诉用户停在哪一步；
  · 任何一步失败都不继续点下去，避免在错的页面上乱点。
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

import raid_core as rc

# 导航用的文字锚点。左边是「要找的字」，右边是「找到后当作什么」
ANCHOR_RAID = ("联盟突袭", "联盟突袭入口")
ANCHOR_RECORD = ("联盟记录", "联盟记录入口")
ANCHOR_GUILD = "联盟"

# 页指纹词表：直接取自 `alliance_ocr.NAV_SPEC`（名单采集真机跑通的那一组），
# 目的不是"再发明一套"，而是让两个工具对"这是哪一页"有同一个答案。
HOME_PROBE = ("方舟", "大厅", "物品栏", "队员招募", "作战出击", "MISSION PASS")
GUILD_PROBE = ("日志", "联盟公告", "联盟功能", "联盟射击场", "联盟商店")
HOME_NEED, GUILD_NEED = 3, 2

# 大厅右侧竖排图标里「联盟」的白名单与实测锚点（= NAV_SPEC[0].area / preset）。
# 上界 0.230 是硬要求：再往上就是 MISSION PASS 横幅，框进来就等于允许点它。
GUILD_AREA = (0.935, 0.230, 1.000, 0.560)
GUILD_PRESET = (0.970, 0.418)

PAGE_CN = {"record": "联盟记录页", "raid": "联盟突袭主页",
           "guild": "联盟概览", "members": "联盟成员页", "home": "大厅"}

# 联盟界面顶部的子页签。成员页和概览页都有这两个词，所以**不能**只靠它们
# 区分两页 —— 必须先用 GUILD_PROBE 把概览认走（v1.16）。
TAB_MAIN = "主选单"
TAB_MEMBERS = "队员"
VK_ESCAPE = 0x1B


def _norm(s: str) -> str:
    return rc.norm_text(s).replace(" ", "")


def _hits(toks: Iterable[dict], words) -> int:
    """这一屏命中了几个"该页独有"的词。"""
    pool = [_norm(t.get("text", "")) for t in toks]
    pool = [p for p in pool if p]
    n = 0
    for w in words:
        k = _norm(w)
        if k and any(k in p for p in pool):
            n += 1
    return n


def find_text(toks: Iterable[dict], keyword: str, min_score: float = 0.90,
              lower_half: bool = False, img_h: float | None = None):
    """在 token 里找完全等于 keyword 的文本，返回候选列表（按 y 排序）。

    lower_half=True 时只保留位于画面下半部分的候选 —— 联盟页的「联盟突袭」既是
    卡片标签（下半部，可点）也可能被标题区误认（上半部），靠这条区分。
    """
    key = _norm(keyword)
    out = []
    for t in toks:
        if _norm(t.get("text", "")) != key:
            continue
        if float(t.get("score") or 0) < min_score:
            continue
        if lower_half and img_h:
            if (t["y0"] + t["y1"]) / 2 < img_h * 0.45:
                continue
        out.append(t)
    out.sort(key=lambda t: t["y0"])
    return out


def _center(t):
    return (t["x0"] + t["x1"]) / 2.0, (t["y0"] + t["y1"]) / 2.0


class RaidNav:
    """把「大厅 → 联盟概览 → 联盟突袭 → 联盟记录」这条链路点完。

    调用方提供：
      region   游戏客户区在屏幕上的矩形 (x0, y0, x1, y1)
      grab     截这个区 → PIL Image（一般是 alliance_ocr.grab）
      click    在**屏幕坐标**点一下（alliance_ocr.click_at）
      tokens   一屏 → token 列表（raid_ocr.tokens）
      say      写日志（只收一个字符串参数）
      stop     外部要求中止（threading.Event）
      esc      发一次 ESC（一般是 alliance_ocr.tap_key 的包装）；None = 不许回退
    """

    def __init__(self, region, grab, click, tokens, say: Callable, stop=None, esc=None):
        self.last_entered = False     # 记录页是不是这次点开的（v1.17）
        self.region = region
        self.grab = grab
        self.click = click
        self.tokens = tokens
        self.say = say
        self.stop = stop
        self.esc = esc

    # ---------------- 基础动作 ----------------
    def _shot(self):
        """截一屏 → token。tokens 由调用方包好引擎（`lambda im: ro.tokens(im, eng)`）。"""
        img = self.grab(self.region)
        return img, self.tokens(img)

    def _click_token(self, t):
        cx, cy = _center(t)
        self.click(self.region[0] + cx, self.region[1] + cy)

    def _wait_for(self, pred, timeout=8.0, interval=0.35):
        """反复截图直到 pred(toks, img) 成立；返回 (ok, toks, img)。"""
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.stop is not None and self.stop.is_set():
                return False, [], None
            img, toks = self._shot()
            if pred(toks, img):
                return True, toks, img
            time.sleep(interval)
        img, toks = self._shot()
        return bool(pred(toks, img)), toks, img

    def _esc_once(self) -> bool:
        if self.esc is None:
            return False
        try:
            self.esc(VK_ESCAPE)
            return True
        except Exception:
            return False

    # ---------------- 页面判定 ----------------
    @staticmethod
    def on_record_page(toks, img=None):
        """已经在伤害记录列表：**至少两条标准千分位伤害数字**，或两条 LEVEL 徽标。

        为什么不用 `DAMAGE_CAND`（更宽松的那个）：联盟页上「0/30」「2ND」这类短数字
        会被它命中，于是把联盟页误判成记录页、直接跳过导航（实测过）。
        真伤害一定是 `1,538,157,240` 这种严格千分位，用 `normalize_damage` 过滤才对。
        """
        # 联盟子页（概览 / 成员）顶部有「主选单 / 队员」页签，而记录页和突袭主页没有
        # ——2026-11-07 用真实截图核过（记录页无页签）。成员页的贡献值是 7 位数字
        # （实测 1586750、1126490），会通过 normalize_damage 的"纯数字 ≥7 位"被判成
        # 伤害，两行一凑就把成员页认成记录页，`goto_record` 直接 return True，
        # 于是"在成员页点开始采集"什么都不点就开跑（用户 19:09 的日志）。
        if find_text(toks, TAB_MAIN) and find_text(toks, TAB_MEMBERS):
            return False
        n_dmg = n_lv = 0
        for t in toks:
            txt = _norm(t.get("text", ""))
            if rc.LEVEL_RE.search(txt):
                n_lv += 1
            elif rc.normalize_damage(txt.replace(" ", ""))[0] is not None:
                n_dmg += 1
            if n_dmg >= 2 or n_lv >= 2:
                return True
        return False

    @classmethod
    def which_page(cls, toks, img=None):
        """record / raid / guild / home / None（None = 认不出，调用方不许猜）。

        raid = 联盟突袭主页：见「联盟记录」按钮但没有记录列表。以前只认
        record/guild/home，用户停在突袭主页点开始会被当"认不出"按 ESC 瞎回退
        ——实测（2026-11-07 用户反馈）就是"进入联盟突袭主页之后识别不到，
        一直要求回主页"的坑。概览页没有「联盟记录」按钮（真实截图核过），
        所以这个指纹不会把概览误判成突袭主页。
        """
        # 顺序是硬要求：概览页也有「主选单+队员」页签，必须先用 GUILD_PROBE 认走；
        # 而记录页/突袭页**没有**页签，所以 members 排在 record 前面是安全的
        # （v1.16 起用真实截图验证：记录页无页签、成员页三个页签齐全）。
        if _hits(toks, GUILD_PROBE) >= GUILD_NEED:
            return "guild"
        if find_text(toks, TAB_MAIN) and find_text(toks, TAB_MEMBERS):
            return "members"
        if cls.on_record_page(toks, img):
            return "record"
        if find_text(toks, ANCHOR_RECORD[0]):
            return "raid"
        if _hits(toks, HOME_PROBE) >= HOME_NEED:
            return "home"
        return None

    # ---------------- 回大厅 ----------------
    def goto_home(self, max_esc: int = 5):
        """ESC 逐级回退，直到认领大厅或联盟概览。返回 (ok, 说明)。

        为什么"认出来就停"：多按一次就会从联盟概览退回大厅，白绕一圈；
        更糟的是在弹窗上按 ESC 会把弹窗关掉，状态更难判。
        """
        if self.esc is None:
            return False, "拿不到 ESC 能力，无法自动回大厅 —— 请手动进入联盟页"
        for i in range(max_esc):
            if self.stop is not None and self.stop.is_set():
                return False, "已取消"
            if not self._esc_once():
                return False, "ESC 发不出去 —— 检查是否以管理员运行"
            time.sleep(1.05)
            img, toks = self._shot()
            page = self.which_page(toks, img)
            if page == "home":
                self.say(f"按 ESC 回退 {i + 1} 次 → 已回到大厅")
                return True, "已回到大厅"
            if page == "guild":
                self.say(f"按 ESC 回退 {i + 1} 次 → 已到联盟概览")
                return True, "已到联盟概览"
            if page == "raid":
                # 突袭主页不再当"认不出"傻按：明说在哪，下一按 ESC 就到概览
                self.say(f"按 ESC 回退 {i + 1} 次 → 已到联盟突袭主页（再退一步到概览）")
            elif page == "members":
                # 这里只是"说明位置"：正常路径是先点「主选单」，别一上来就 ESC
                self.say(f"按 ESC 回退 {i + 1} 次 → 在联盟成员页（再退一步到概览）")
            else:
                self.say(f"按 ESC 回退（{i + 1}/{max_esc}）→ 还没到大厅")
        return False, (f"ESC 回退 {max_esc} 次仍没回到大厅 —— 可能卡在战斗/剧情里，"
                       f"请手动回到大厅后点开始")

    # ---------------- 大厅 → 联盟概览 ----------------
    def _click_guild(self, img, toks):
        """点大厅右侧竖排的「联盟」。返回 (ok, 说明)。

        先找词、再查白名单；认不出才退回实测锚点。**锚点也必须在白名单内**，
        否则宁可不点 —— 点错一个像素就可能进商店或弹公告。
        """
        w, h = img.size
        best = None
        for t in toks:
            if _norm(t.get("text", "")) != _norm(ANCHOR_GUILD):
                continue
            cx = (t["x0"] + t["x1"]) / 2.0 / w
            cy = (t["y0"] + t["y1"]) / 2.0 / h
            if not (GUILD_AREA[0] <= cx <= GUILD_AREA[2]
                    and GUILD_AREA[1] <= cy <= GUILD_AREA[3]):
                continue
            sc = float(t.get("score") or 0)
            if best is None or sc > best[0]:
                best = (sc, cx, cy)
        if best is not None:
            sc, cx, cy = best
            self.click(self.region[0] + cx * w, self.region[1] + cy * h)
            return True, f"点「联盟」@({cx:.3f},{cy:.3f}) 置信 {sc:.2f}"
        cx, cy = GUILD_PRESET
        if not (GUILD_AREA[0] <= cx <= GUILD_AREA[2]
                and GUILD_AREA[1] <= cy <= GUILD_AREA[3]):
            return False, "「联盟」锚点落在白名单外，拒绝点击"
        self.click(self.region[0] + cx * w, self.region[1] + cy * h)
        return True, f"OCR 没认出「联盟」，退回实测锚点 ({cx:.3f},{cy:.3f})"

    # ---------------- 主流程 ----------------
    def goto_record(self, max_step_timeout: float = 8.0):
        """确保停在联盟记录页。返回 (ok, 说明)。"""
        img, toks = self._shot()
        page = self.which_page(toks, img)
        self.say(f"当前页面：{PAGE_CN.get(page) or '认不出'}")

        # 记录页是不是"这次点出来的"：一开始就在 → False（列表位置是用户自己滚的，
        # 采集前必须回顶）；其余路径都要先点按钮才到 → True（页面刚打开，本就在第一行）。
        self.last_entered = page != "record"

        if page == "record":
            return True, "已经在联盟记录页"

        # 已经站在联盟突袭主页 → 不回退，直达「联盟记录」（v1.2 的坑就是这里
        # 被当"认不出"按 ESC 瞎回退，绕一圈大厅还进不去）
        if page == "raid":
            return self._enter_record(toks, img, max_step_timeout)

        # 站在联盟成员页（名单采集刚收工就是这页）→ 点「主选单」回概览。
        # 不走 ESC：联盟界面里 ESC 往往直接关掉整个联盟回大厅，还得再点一次「联盟」。
        if page == "members":
            ok_m, toks_m, img_m = self._leave_members(toks, img, max_step_timeout)
            if ok_m:
                toks, img, page = toks_m, img_m, "guild"
                self.say("已回到联盟概览")
                # 概览页有加载动画，等一拍再找「联盟突袭」卡片（同 v1.2 的坑）
                for _ in range(6):
                    if self.stop is not None and self.stop.is_set():
                        return False, "已取消"
                    time.sleep(0.1)
            else:
                self.say("「主选单」没点动 → 退回按 ESC 逐级返回")
                ok_h, msg_h = self.goto_home()
                self.say(msg_h)
                if not ok_h:
                    return False, msg_h
                img, toks = self._shot()
                page = self.which_page(toks, img)

        # 认不出当前页 → 先回退到大厅/概览，再往下走
        if page is None:
            ok, msg = self.goto_home()
            self.say(msg)
            if not ok:
                return False, msg
            img, toks = self._shot()
            page = self.which_page(toks, img)

        # 大厅 → 联盟概览
        if page == "home":
            ok, msg = self._click_guild(img, toks)
            self.say(msg)
            if not ok:
                return False, msg
            ok, toks, img = self._wait_for(
                lambda tk, im: self.which_page(tk, im) == "guild",
                timeout=max_step_timeout)
            if not ok:
                return False, "点了「联盟」但没进联盟概览（可能弹了公告/别的窗口）"
            page = "guild"
            self.say("已进入联盟概览")
            # 刚认出概览页就立刻找「联盟突袭」，页面还在加载动画里必然找不到
            # （实测 15:27 那轮 1ms 内就报了"没看到"），白按一次 ESC 还多等 2 s。
            # 等一拍再找；等的时候尊重停止请求。
            for _ in range(10):
                if self.stop is not None and self.stop.is_set():
                    return False, "已取消"
                time.sleep(0.1)

        if page != "guild":
            return False, (f"停在「{PAGE_CN.get(page) or '未知页面'}」，"
                           f"请手动开到联盟页再点开始")

        # 联盟概览 → 点「联盟突袭」卡片
        cands = find_text(toks, ANCHOR_RAID[0], lower_half=True, img_h=img.height)
        if not cands:
            # 可能站在联盟的子页（成员 / 日志 / 商店）：ESC 一次回概览
            for _i in range(2):
                if self.stop is not None and self.stop.is_set():
                    return False, "已取消"
                self.say("联盟页上没看到「联盟突袭」→ 按 ESC 回概览重试")
                if not self._esc_once():
                    break
                time.sleep(1.0)
                img, toks = self._shot()
                cands = find_text(toks, ANCHOR_RAID[0], lower_half=True, img_h=img.height)
                if cands:
                    break
        if cands:
            t = cands[-1]                     # 卡片标签在最下面那一排
            self.say(f"点「{ANCHOR_RAID[0]}」（{t['text']} @ "
                     f"{int(_center(t)[0])},{int(_center(t)[1])}）")
            self._click_token(t)
            ok, toks, img = self._wait_for(
                lambda tk, im: bool(find_text(tk, ANCHOR_RECORD[0])) or self.on_record_page(tk, im),
                timeout=max_step_timeout)
            if not ok:
                return False, "点了「联盟突袭」但没进到突袭页（没看到「联盟记录」）"
        elif not find_text(toks, ANCHOR_RECORD[0]):
            return False, "联盟概览上没找到「联盟突袭」卡片，也没看到「联盟记录」"

        # 突袭页 → 点「联盟记录」
        return self._enter_record(toks, img, max_step_timeout)

    def _leave_members(self, toks, img, timeout: float = 4.0):
        """联盟成员页 → 点顶部「主选单」回联盟概览。返回 (ok, toks, img)。

        为什么优先点页签而不是 ESC：联盟界面里 ESC 的语义是"关掉整个联盟"，
        按下去直接回大厅（有时先弹确认框），还得从大厅重新点「联盟」——
        而「主选单」和「队员」是同一排页签，点它是确定动作，一步到位。
        """
        cands = find_text(toks, TAB_MAIN)
        if not cands:
            return False, toks, img
        t = cands[0]
        cx, cy = _center(t)
        self.say(f"成员页 → 点「{TAB_MAIN}」回联盟概览（@ {int(cx)},{int(cy)}）")
        self._click_token(t)
        # 只要不再是成员页就算成功：万一 OCR 把「联盟公告」等词漏了、概览被认成
        # members，这里的判据也不会把"其实已经到概览"误判成失败。
        ok, toks2, img2 = self._wait_for(
            lambda tk, im: self.which_page(tk, im) in ("guild", "record", "raid"),
            timeout=timeout)
        if not ok:
            return False, toks2 or toks, img2 or img
        return True, toks2, img2

    def _enter_record(self, toks, img, max_step_timeout: float = 8.0):
        """从联盟突袭主页进记录页：认出列表就算到，否则点「联盟记录」再等。"""
        if not self.on_record_page(toks, img):
            cands2 = find_text(toks, ANCHOR_RECORD[0])
            if not cands2:
                return False, "突袭页上没找到「联盟记录」按钮"
            t2 = cands2[0]
            self.say(f"点「{ANCHOR_RECORD[0]}」（@ {int(_center(t2)[0])},{int(_center(t2)[1])}）")
            self._click_token(t2)
            ok2, toks, img = self._wait_for(lambda tk, im: self.on_record_page(tk, im),
                                           timeout=max_step_timeout)
            if not ok2:
                return False, "点了「联盟记录」但没出现记录列表（可能需要先打一刀）"

        return True, "已进入联盟记录页"
