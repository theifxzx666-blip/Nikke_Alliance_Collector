# -*- coding: utf-8 -*-
"""Qt 侧的引擎适配：把 `raid_capture.CaptureEngine` 接到 Qt 的信号上。

只做三件事：找窗口/换算区域、把回调转成信号、在后台线程跑采集。
判据一律在引擎里，本文件不写任何"怎么认、怎么滚"。
"""
import threading
import time

from PySide6.QtCore import QObject, Signal

import alliance_ocr as A
import booklib as BK
from raid_capture import CaptureEngine

import raid_ocr as ro
import raid_nav as rn
from raid_hard import boss_norm, load_bosses, read_hard


class Engine(QObject, CaptureEngine):
    rowsChanged = Signal(list)
    finished = Signal()
    logged = Signal(str)
    attached = Signal(bool, str, str, str)   # ok, 标题, 分辨率, 徽标

    def __init__(self):
        QObject.__init__(self)
        self.win = None
        self.rect_rel = None
        self._stop = threading.Event()
        self._entered_by_nav = False     # 记录页是不是这次导航点开的（v1.17）
        self._roster = {}
        self._bosses = []
        self._thread = None
        self._eng = None

    # ---------------- 宿主接口（引擎只认这几个） ----------------
    def say(self, msg, level="INFO"):
        self.logged.emit(str(msg))

    def emit_rows(self, rows):
        self.rowsChanged.emit([dict(r) for r in rows])

    def emit_done(self):
        self.finished.emit()

    # ---------------- 基础设施 ----------------
    def engine(self):
        if self._eng is None:
            self._eng = A.get_engine()
        return self._eng

    def attach(self):
        """自动找回游戏窗口；只认「进程名 + 标题」命中的那个。"""
        try:
            info, _others = A.find_game_window()
        except Exception as e:
            self.say(f"找窗口出错：{e}")
            self.attached.emit(False, "", "", "未连接")
            return False
        if info is None:
            # 旧实现只会说"没找到"，但失败可能是三种：没有、候选太多、比例不对。
            # 不把差别说清楚，用户只能猜。
            if _others:
                # 候选里会混进我们自己的窗口（标题也含 NIKKE），列出来只会让人更迷惑
                names = "、".join(sorted({o.title for o in _others
                                         if o.title and "联盟采集" not in o.title}))[:70]
                self.say(f"找到 {len(_others)} 个候选窗口，无法确定是哪个：{names}")
                self.say("请只保留游戏窗口，或稍等再按 F2")
            else:
                self.say("没找到游戏窗口 —— 确认游戏已打开、没有最小化；"
                         "若游戏就在眼前，点一下游戏窗口再按 F2")
            self.attached.emit(False, "", "", "未连接")
            return False
        self.win = info
        self._roster = A.load_roster()
        self._bosses = load_bosses()
        bad = abs(info.aspect - A.TARGET_ASPECT) / A.TARGET_ASPECT > A.ASPECT_TOL
        self.attached.emit(True, info.title, f"{info.w} × {info.h}",
                           "比例不对" if bad else "16:9 ✓")
        self.say(f"已连接：{info.title} {info.w}×{info.h}")
        self.say(f"昵称库 {len((self._roster or {}).get('names') or {})} 人 · "
                 f"本期 BOSS {len(self._bosses)} 个")
        return True

    def region(self):
        if self.win is None:
            raise RuntimeError(f"还没接上游戏窗口（{KEYS.current('attach')}）")
        info = A.content_rect(self.win.hwnd)
        if info is None:
            raise RuntimeError("游戏窗口找不到了")
        if abs(info.aspect - A.TARGET_ASPECT) / A.TARGET_ASPECT > A.ASPECT_TOL:
            raise RuntimeError(f"有效画面 {info.w}×{info.h} 不是 16:9 —— 先修正窗口比例")
        return (info.x, info.y, info.x + info.w, info.y + info.h)

    # ---------------- 三个动作 ----------------
    def shot(self):
        """只认当前这一屏，不滚动。"""
        try:
            region = self.region()
            img = self._crop(A.grab(region))
            recs, loc = read_hard(img, self.engine(), self._roster, self._bosses)
            if not loc.cards:
                self.say("这一屏没认到记录 —— 确认「联盟记录」已打开")
                return
            self.emit_rows(recs)
            miss = sum(1 for r in recs
                       if not (r["member"] and r["boss"] and r["damage"] is not None))
            self.say(f"本屏 {len(recs)} 条 · 行距 {loc.pitch:.0f}px · 缺字段 {miss} 条")
        except Exception as e:
            self.say(f"采集本屏失败：{e}")
            A.log_exc("client-shot")

    def start(self):
        """一键：进记录页 → 拉回顶部 → 滚采。"""
        if self.win is None:
            self.say(f"先接窗口（{KEYS.current('attach')}）")
            return False
        try:
            region = self.region()
        except Exception as e:
            self.say(str(e))
            return False
        # 每次开跑前重读活动库（v1.3）：用户在设置里切了 Q 区/V 区，下一次开始
        # 就该用新库核对；采集中途不换，避免半程换判据。
        # 成员库非必选（v1.4）：选「不使用」时传 None，read_hard 跳过名字归位，
        # 只靠位置合并 + BOSS 归一——库只在有把握时才是帮手。
        self._roster = A.load_roster() if BK.active_roster() else None
        self._bosses = load_bosses()
        self.say(f"核对用：{BK.describe()}")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_all, args=(region,), daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._stop.set()

    def _run_all(self, region):
        try:
            # 先把游戏拉到前台：导航要按 ESC 回退，ESC 是发给**当前前台窗口**的。
            # 用户刚点完客户端按钮时焦点还在客户端，不切前台的话 ESC 会被客户端自己吃掉。
            if self.win is not None and not A.focus_window(self.win.hwnd):
                self.say("切不到游戏前台 —— 请手动点一下游戏窗口再开始", "WARN")
                self.emit_done()
                return
            time.sleep(0.25)
            ok, msg = self._goto_record(region)
            self.say(msg)
            if not ok:
                self.emit_done()
                return
            # 记录页是导航刚点开的就让 capture 跳过回顶（v1.17）；
            # 用户自己早就在记录页时 last_entered=False，仍会拉回第一行
            self.capture(region, from_nav=self._entered_by_nav)
        except Exception as e:
            self.say(f"采集出错：{e}")
            A.log_exc("client-run")
            self.emit_done()

    def _goto_record(self, region):
        """复用 RaidNav：大厅 → 联盟 → 突袭 → 记录；已在记录页就直接通过。"""
        nav = rn.RaidNav(region, A.grab, A.click_at,
                         lambda im: ro.tokens(im, self.engine()), self.say, self._stop,
                         esc=A.tap_key)
        ok, msg = nav.goto_record()
        self._entered_by_nav = bool(getattr(nav, "last_entered", False))
        return ok, msg

    # 客户端比旧工具多一步：把 BOSS 名按本期表归一
    def _norm_rows(self, rows):
        for r in rows:
            raw = r.get("boss") or ""
            r["boss"] = boss_norm(raw, self._bosses)
        return rows
