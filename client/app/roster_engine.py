# -*- coding: utf-8 -*-
"""名单采集引擎（客户端版）：直接持有 `alliance_ocr.RosterMixin` 的判据，不再开任何旧窗口。

v1.4 之前是"headless 驱动旧 App（tk.Tk）"——那条路会连带 Tk 事件泵、弹窗、
局部变量丢失（`info` 未定义）等问题。现在判据本身抽成了 `RosterMixin`，
这里只是它的一个**宿主**：把 UI 钩子实现成 Qt 信号/日志，仅此而已。

线程模型：`nav_to_members` 自己起工作线程，内部用 `self._post` 回主线程；
这里用一条 Qt 信号（跨线程自动排队）把回调送回主线程执行，绝不在工作线程
里碰 Qt 对象。
"""
import csv
import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

import alliance_ocr as A
from alliance_ocr import RosterMixin

from app import paths as P
from app import store as S


def export_book_csv():
    """把**当前成员库文件**写成 CSV（不需要任何采集结果）。返回 (人数, Path 或错误)。

    v1.21：用户只选了一个已有成员的库、这次没采过，也要能导出。那种情况下
    `self.names` 是空的，能写的只有库文件本身 —— 引擎的 export() 与 Shell 都走这里。
    """
    try:
        import booklib as BK
        book = BK.active_roster()
        names = (BK.read_roster(book).get("names") or {}) if book else {}
    except Exception as e:
        return 0, f"读成员库失败：{e}"
    if not names:
        return 0, "名单为空"
    out = P.sub(P.ROSTER) / P.roster_file(book)
    try:
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["序号", "昵称", "置信度"])
            for i, (n, v) in enumerate(names.items(), 1):
                w.writerow([i, n, f"{float((v or {}).get('score') or 0):.3f}"])
    except Exception as e:
        return 0, str(e)
    S.save_settings(last_export=str(out))
    return len(names), out


class RosterEngine(QObject, RosterMixin):
    """名单采集：判据来自 RosterMixin，UI 全靠信号。"""

    log_sig = Signal(str, str)      # (消息, 级别 INFO/WARN/ERR)
    rows_sig = Signal(list)         # 名单行 [{idx, member, conf}, …]
    state_sig = Signal(bool)        # 采集中
    _call_sig = Signal(object)      # 工作线程 → 主线程的回调（tuple: fn, args）

    def __init__(self, parent=None):
        QObject.__init__(self, parent)
        self._init_roster_state()
        self._headless = True
        self._busy_reported = None
        self._rows_len = -1
        self._rows_timer = None
        self._call_sig.connect(self._do_call)     # 构造在主线程 → 跨线程自动排队
        self._hb = QTimer(self)                   # 心跳：把 _continuous / names 变化播给界面
        self._hb.setInterval(200)
        self._hb.timeout.connect(self.heartbeat)
        self._hb.start()

    # ---------------- 宿主钩子：日志/进度
    def say(self, msg, level="INFO"):
        try:
            A.logline(msg)                        # 操作日志照常落盘
        except Exception:
            pass
        lv = level if level in ("INFO", "WARN", "ERR") else "INFO"
        if lv == "INFO":
            low = str(msg)
            if "⚠" in low or "没认" in low or "失败" in low:
                lv = "WARN"
        self.log_sig.emit(str(msg), lv)

    def _task(self, msg=None, pct=None, step=None, state=None):
        if msg:
            self.say(msg)

    def _flow(self, idx, state, pct=None):
        pass

    def _sync_run_buttons(self):
        pass

    def _log_mode(self, running):
        pass

    def _refresh_rows(self):
        self.rows_sig.emit(self.names_rows())
        self._rows_len = len(self.getattr_names())

    def getattr_names(self):
        return getattr(self, "names", {}) or {}

    def _refresh_ledger(self, total=None):
        pass

    def _nav_ui(self, running):
        pass

    # ---------------- 定时器 / 跨线程回传
    def after(self, ms, fn=None):              # noqa: A003
        """主线程定时器（Mixin 的 after 链全部在主线程）。"""
        if fn is not None:
            QTimer.singleShot(int(ms), fn)

    def _post(self, fn, *args):
        """工作线程回主线程：走信号排队，绝不在子线程碰 Qt。"""
        self._call_sig.emit((fn, args))

    def _do_call(self, payload):
        fn, args = payload
        try:
            fn(*args)
        except Exception as e:
            self.log_sig.emit(f"回调出错：{e}", "ERR")

    # ---------------- 生命周期
    def prepare(self) -> bool:
        """预热：读活动成员库 + 接窗口。失败也返回 True（后面导航会重试接窗口）。"""
        try:
            self._roster = A.load_roster()
        except Exception as e:
            self.log_sig.emit(f"读成员库失败：{e}", "WARN")
        if not self.region_rel:
            self._load_region_preset()
        return True

    def _load_region_preset(self):
        """带出上次标定：`输出/区域.json`（旧工具写的），没有就用内置预设。

        这段与旧工具 `App._load_cfg` 读的是同一个文件——标定一次两边通用。
        """
        try:
            if A.CFG.exists():
                cfg = json.loads(A.CFG.read_text(encoding="utf-8"))
                self._saved = cfg if isinstance(cfg, dict) else {}
                rel = self._saved.get("region_rel")
                if rel and len(rel) == 4:
                    self.region_rel = tuple(float(x) for x in rel)
                    self._preset_used = True
                self._calib_pitch = self._saved.get("pitch") or None
                self._calib_rows = self._saved.get("rows_per_screen") or None
        except Exception as e:
            self.log_sig.emit(f"读标定失败（会用内置预设）：{e}", "WARN")

    def _apply_builtin_preset(self) -> bool:
        """套用随包的内置预设（`预设/昵称列预设.json`）。

        这是「标定一次就够」的兑现点：预设存的是**相对比例**，只要有效画面还是
        16:9，1280×720 上标的框在任何分辨率都成立。旧工具 `App._load_cfg` 有这个
        回退，客户端 v1.0 起漏掉了 —— 于是内置预设永远用不上，用户每次都被要求
        重新标定，还看到一句"把游戏窗口调成 16:9"的误导提示（v1.23 补）。

        必须在 `self.win` 存在之后调用：换算要用当前窗口的高与宽高比。
        """
        try:
            preset = A.load_preset()
        except Exception as exc:
            self.log_sig.emit(f"读内置预设失败：{exc}", "WARN")
            return False
        if not preset or not preset.get("region_rel"):
            return False
        try:
            pa = float(preset.get("aspect") or 0.0)
            wa = float(getattr(self.win, "aspect", 0) or 0)
            if pa and wa and abs(pa - wa) / wa > A.ASPECT_TOL:
                self.log_sig.emit(
                    f"内置预设是在 {preset.get('resolution') or '16:9'} 上标的，"
                    f"当前窗口宽高比 {wa:.3f} 对不上 —— 需要重新标定一次", "WARN")
                return False
            self.region_rel = tuple(preset["region_rel"])
            self._calib_pitch = (float(preset.get("pitch_rel") or 0)
                                 * self.win.h) or None
            self._calib_rows = preset.get("rows") or None
            self._preset_used = True
            self.log_sig.emit(
                f"已套用内置预设（{preset.get('resolution') or '16:9'} 上标的）"
                f"· 每屏约 {self._calib_rows or '?'} 行，直接开采集即可", "INFO")
            return True
        except Exception as exc:
            self.log_sig.emit(f"套用内置预设失败：{exc}", "WARN")
            return False

    def attach(self) -> bool:
        """锁定游戏窗口（复用旧工具的判据：按进程名 + 有效画面）。"""
        try:
            self.auto_lock()
        except Exception as e:
            self.log_sig.emit(f"接窗口失败：{e}", "ERR")
            return False
        return getattr(self, "win", None) is not None

    def start_capture(self) -> bool:
        """开始：接窗口 → 导航到成员页 → 自动滚采。"""
        self._headless = True
        self._cancelled = False           # 新的一轮：清掉上一轮的取消旗标
        if getattr(self, "_continuous", False):
            self.log_sig.emit("名单采集已经在跑", "WARN")
            return True
        self.prepare()
        if getattr(self, "win", None) is None:
            self.log_sig.emit("正在连接游戏窗口…", "INFO")
            if not self.attach():
                self.log_sig.emit("没接上游戏窗口 —— 确认游戏已打开后重试", "WARN")
                return False
            QTimer.singleShot(600, self._start_now)
            return True
        return self._start_now()

    def _start_now(self) -> bool:
        if getattr(self, "win", None) is None:
            self.log_sig.emit(f"没接上游戏窗口 —— 先按 {KEYS.current('attach')} 接窗口",
                              "WARN")
            return False
        if self.region_rel is None:
            # 接上窗口才算得出比例 —— 内置预设的回退必须放在这一步，不能放 prepare()
            self._apply_builtin_preset()
        if self.region_rel is None:
            self.log_sig.emit(
                "还没有采集区域 —— 点【标定昵称列】量一次"
                "（内置预设只对 16:9 有效，其它宽高比必须量）", "WARN")
            return False
        if getattr(self, "_nav_running", False):
            self.log_sig.emit("导航正在跑，等它做完再开采集", "INFO")
            self._await_nav()
            return True
        self.log_sig.emit("正在导航到联盟成员页…", "INFO")
        try:
            self.nav_to_members()
        except Exception as e:
            self.log_sig.emit(f"导航启动失败：{e}", "ERR")
            return False
        self._await_nav()
        return True

    def _await_nav(self):
        self._nav_tries = 0
        self._nav_timer = QTimer(self)
        self._nav_timer.setInterval(300)
        self._nav_timer.timeout.connect(self._poll_nav)
        self._nav_timer.start()

    def _poll_nav(self):
        # 用户按过停止：导航的取消旗标已经设了，别再自动接着开滚采
        # （否则"按 Esc 停止"反而变成"开始采集"，页面继续动）
        if getattr(self, "_cancelled", False):
            self._nav_timer.stop()
            return
        self._nav_tries += 1
        if getattr(self, "_nav_running", False):
            if self._nav_tries >= 200:            # 60 s
                self._nav_timer.stop()
                self.log_sig.emit("导航超时 —— 停在当前页，没盲点；请手动进成员页再试", "WARN")
            return
        self._nav_timer.stop()
        if getattr(self, "_continuous", False):
            return
        try:
            self._begin_auto()
        except Exception as e:
            self.log_sig.emit(f"启动滚采失败：{e}", "ERR")

    def _nav_done(self):
        """导航收尾：立刻接着判一次，别等 300 ms 轮询。

        中间那一下会让 heartbeat 播一次"不忙"，界面闪一下"名单已存入"，
        看着像采集自己结束了一轮。
        """
        try:
            super()._nav_done()
        except Exception:
            self._nav_running = False
        self._poll_nav()

    def stop_capture(self):
        """停止名单采集：**导航中与滚采中都算**。

        v1.23 之前只处理 `_continuous`，导航阶段按停止等于没按 —— 而且导航一
        结束 `_poll_nav` 还会自动开滚采，用户看到的是"按了停止反而开始动手了"。
        """
        self._cancelled = True
        nav = bool(getattr(self, "_nav_running", False))
        cont = bool(getattr(self, "_continuous", False))
        if nav:
            self._nav_cancel = True               # 导航线程里 5 处检查点会退出
            self.log_sig.emit("正在取消导航…", "INFO")
        tm = getattr(self, "_nav_timer", None)
        if tm is not None and tm.isActive():
            tm.stop()
        if cont:
            self.cancel_auto()
        elif not nav:
            self.log_sig.emit("已停止 —— 当前没有采集在跑", "INFO")

    # ---------------- 数据出口
    def names_rows(self) -> list:
        names = getattr(self, "names", None) or {}
        return [{"idx": i, "member": n, "conf": f"{float(sc):.3f}"}
                for i, (n, (sc, _t)) in enumerate(names.items(), 1)]

    def export(self):
        """收工对账后写 CSV。返回 (人数, Path 或失败原因)。"""
        if not (getattr(self, "names", None) or {}):
            return export_book_csv()      # 没采过：直接导当前库（v1.21）
        # v1.61：**收工时已经提交过一次，这里不能再提交。**
        # 原来的 bug：用户采集完在表格里删掉某人（写库），一按导出，这里又跑
        # `_roster_apply()` 把内存里的采集快照写回库 —— 删掉的人当场复活，
        # 导出自然"不受影响"。现在只在还没提交过时才补一次对账。
        if not getattr(self, "_roster_committed", False):
            try:
                self._reconcile()
                self._roster_apply()
            except Exception as e:
                return 0, f"收工对账失败：{e}"
        # v1.35：对账（这一步才把采集结果写进库）之后，**从库读**再导出。
        # 以前用 `self.names_rows()` —— 那是采集那一刻的快照，用户随后在表格里
        # 加人 / 改名 / 删除，写进去的是库文件，导出却还按旧快照走，等于白改。
        return export_book_csv()
        return len(rows), out

    # ---------------- 状态广播（QTimer 轮询，避免每处手工 emit）
    def heartbeat(self):
        # 导航也算"忙"：否则那 5~20 秒里界面是空闲态，Esc 监听不启用，
        # 用户按停止没有任何反馈，导航跑完还自动开滚采（v1.23 真机）。
        busy = bool(getattr(self, "_continuous", False)
                    or getattr(self, "_nav_running", False))
        if busy != self._busy_reported:
            self._busy_reported = busy
            self.state_sig.emit(busy)
        n = len(getattr(self, "names", None) or {})
        if n != self._rows_len:
            self._rows_len = n
            self.rows_sig.emit(self.names_rows())
