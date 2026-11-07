# -*- coding: utf-8 -*-
"""竖版界面组件：主题、按钮工厂、步骤条、进度条、台账、日志区。

为什么要单独一层：主程序 `alliance_ocr.py` 已经五千多行，再把排版细节塞进去，
以后调个颜色都要翻半天。这里只管"长什么样"，不碰采集逻辑。

设计基线取自 ArcanaHelper V1.1.2 的竖版窗口（616×839）：深灰底、蓝色主操作、
自上而下一条竖线——标题 → 流程 → 进度 → 结果 → 台账 → 操作键。
纯 tkinter，不引第三方 UI 库（工具要能塞进单文件 exe）。
"""
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from tkinter import ttk

# ---- 主题（与主程序原有配色保持一致，只补了状态色）
FONT = ("Microsoft YaHei UI", 10)
FONT_SM = ("Microsoft YaHei UI", 9)
FONT_XS = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 14, "bold")

BG = "#1e1f22"
PANEL = "#2b2d31"
INPUT = "#36393d"
FG = "#e6e6e6"
DIM = "#9aa0a6"
ACCENT = "#4c8dff"
ACCENT_DK = "#3a6fd8"
OK = "#3ba55d"
WARN = "#e0a03a"
ERR = "#d64545"

# 步骤条四种状态：没轮到的、正在做的、做完的、失败的
STEP_COLOR = {"idle": "#4a4d52", "doing": ACCENT, "done": OK, "fail": ERR}


def btn(parent, text, cmd, primary=False, small=False, **kw):
    """统一按钮。primary 用于"当前该点的那个键"，其余一律灰底。"""
    return tk.Button(
        parent, text=text, command=cmd, font=FONT_SM if small else FONT,
        bg=ACCENT if primary else PANEL, fg="white" if primary else FG,
        activebackground=ACCENT_DK if primary else INPUT,
        activeforeground="white", relief="flat", bd=0, cursor="hand2",
        padx=10 if small else 14, pady=2 if small else 6, **kw)


def disable(widget, on=True):
    """按钮置灰。灰掉的按钮在 tkinter 里仍会响应 command，所以状态要一起改。"""
    try:
        widget.config(state="disabled" if on else "normal",
                      cursor="" if on else "hand2")
    except Exception:
        pass


class Card(tk.Frame):
    """带小标题的面板。竖版每一段都要能一眼看出"这块是干什么的"。"""

    def __init__(self, master, title, **kw):
        super().__init__(master, bg=PANEL, **kw)
        self.head = tk.Label(self, text=title, bg=PANEL, fg=DIM, font=FONT_XS,
                             anchor="w", padx=8, pady=3)
        self.head.pack(fill="x")
        self.body = tk.Frame(self, bg=PANEL)
        self.body.pack(fill="both", expand=True, padx=8, pady=(0, 6))

    def title(self, text):
        self.head.config(text=text)


class StepBar(tk.Canvas):
    """横向五步流程条：圆点 + 连线 + 名称，当前步高亮。

    流程可视化是这次改造的重点：采集出问题时用户最先想知道"卡在第几步"，
    而不是去读日志。所以每一步只有 4 种状态，颜色直接回答"到哪了"。
    """

    H = 58

    def __init__(self, master, steps, **kw):
        super().__init__(master, height=self.H, bg=BG, highlightthickness=0, **kw)
        self.steps = list(steps)
        self.state = ["idle"] * len(self.steps)
        self.cur = 0
        self.bind("<Configure>", lambda _e: self.redraw())

    def set(self, idx, state, auto_next=True):
        """把第 idx 步设成某状态；auto_next 时把后面的步骤重置回 idle。"""
        if not 0 <= idx < len(self.state):
            return
        self.state[idx] = state
        self.cur = idx
        if auto_next and state in ("doing", "done"):
            for i in range(idx + 1, len(self.state)):
                self.state[i] = "idle"
        self.redraw()

    def reset(self, cur=0):
        self.state = ["idle"] * len(self.steps)
        self.cur = cur
        self.redraw()

    def redraw(self):
        self.delete("all")
        n = len(self.steps)
        if not n:
            return
        w = max(self.winfo_width(), 200)
        pad = 30
        gap = (w - pad * 2) / max(1, n - 1)
        cy = 17
        for i, name in enumerate(self.steps):
            cx = pad + gap * i
            st = self.state[i]
            col = STEP_COLOR.get(st, STEP_COLOR["idle"])
            if i:
                px = pad + gap * (i - 1)
                # 连线的颜色跟着"右边那个点"走：走完了才把线染绿
                lc = col if st in ("done", "doing") else "#3a3d42"
                self.create_line(px + 10, cy, cx - 10, cy, fill=lc, width=2)
            r = 10 if st == "doing" else 8
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=col, outline=col)
            if st == "done":
                self.create_line(cx - 4, cy, cx - 1, cy + 3, cx + 4, cy - 3,
                                 fill="white", width=2)
            elif st == "fail":
                self.create_line(cx - 3, cy - 3, cx + 3, cy + 3, fill="white", width=2)
                self.create_line(cx - 3, cy + 3, cx + 3, cy - 3, fill="white", width=2)
            elif st == "doing":
                self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="white",
                                 outline="white")
            fg = FG if st in ("doing", "done", "fail") else DIM
            self.create_text(cx, cy + 22, text=name, fill=fg, font=FONT_XS)


class TaskProgress(tk.Frame):
    """进度条 + 一行"任务提示"。对应 ArcanaHelper 的 `任务提示 running N%：…`。"""

    def __init__(self, master, **kw):
        super().__init__(master, bg=BG, **kw)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Task.Horizontal.TProgressbar", troughcolor=INPUT,
                        background=ACCENT, bordercolor=BG, lightcolor=ACCENT,
                        darkcolor=ACCENT, thickness=8)
        self.var = tk.DoubleVar(value=0.0)
        self.pb = ttk.Progressbar(self, orient="horizontal", mode="determinate",
                                  maximum=100.0, variable=self.var,
                                  style="Task.Horizontal.TProgressbar")
        self.pb.pack(fill="x", padx=2, pady=(2, 3))
        self.txt = tk.Label(self, text="等待开始", bg=BG, fg=DIM, font=FONT_SM,
                            anchor="w")
        self.txt.pack(fill="x", padx=2)

    def set(self, pct=None, msg=None):
        if pct is not None:
            try:
                self.var.set(max(0.0, min(100.0, float(pct))))
            except Exception:
                pass
        if msg is not None:
            self.txt.config(text=msg)


class Ledger(tk.Frame):
    """三行台账：待采 / 已识别 / 已跳过。ArcanaHelper 底部那块小表格的同款。"""

    def __init__(self, master, keys, **kw):
        super().__init__(master, bg=PANEL, **kw)
        self.vars = {}
        for i, (k, tip) in enumerate(keys):
            tk.Label(self, text=k + "：", bg=PANEL, fg=DIM, font=FONT_XS,
                     anchor="w", width=7).grid(row=i // 3, column=(i % 3) * 2,
                                               sticky="w", padx=(8, 0), pady=1)
            v = tk.StringVar(value="—")
            self.vars[k] = v
            tk.Label(self, textvariable=v, bg=PANEL, fg=FG, font=FONT_SM,
                     anchor="w").grid(row=i // 3, column=(i % 3) * 2 + 1,
                                      sticky="w", padx=(0, 10), pady=1)

    def set(self, **kw):
        for k, v in kw.items():
            if k in self.vars:
                self.vars[k].set("—" if v is None else str(v))


class LogPane(tk.Frame):
    """运行日志区：永远停在最新一行，可折叠、可拖高、翻历史时不被拽回来。

    为什么值得单独做成组件，而不是几行 tk.Text：

    1) **一定要跟到最新**。一次滚采大约每 1.6 秒写 8 行，5 行高的框子 1.2 秒
       就换一遍——想"边跑边看现场"，就必须保证新行一进来视图就到底，而且
       最新那条要能被眼睛一眼找到（末行加淡底）。
    2) **写入必须线程安全**。日志是从采集线程写的，直接在子线程里调 Tk 会偶发
       抛 "main thread is not in main loop"；而 log() 外围有 except 兜底，结果
       是**静默丢行**——现场偏偏缺了最关键那一条。所以外部只用 push()，它只往
       队列里塞；主线程上有一个 80ms 的抽取节拍统一入库。
    3) **翻历史不能被打断**。用户往上翻看刚才那行时，新日志不能把他拽回底部；
       翻回底部（或点右下角"↓ N 条新"）才恢复跟随。判据就是滚动条位置本身。
    """

    MIN_ROWS = 3
    MAX_ROWS = 24
    KEEP = 400          # 界面只留最近的现场；全量在日志文件里
    TICK = 80           # 队列抽取节拍（ms）

    def __init__(self, master, height=5, **kw):
        super().__init__(master, bg=PANEL, **kw)
        self._rows = max(self.MIN_ROWS, min(self.MAX_ROWS, int(height)))
        self._open = True
        self._follow = True          # 跟随最新
        self._pending = 0            # 停在历史期间攒了多少条
        self._lines = 0
        self._guard = 0              # 自己滚动视图时不让 yview 回调改跟随状态
        self._alive = True
        self._q = deque()            # 工作线程 → 主线程的唯一通道
        self._drag_y = None
        self._drag_rows = self._rows
        self.user_sized = False      # 用户亲手拖过高度，之后不再自动改

        head = tk.Frame(self, bg=PANEL)
        head.pack(fill="x")
        self._head = tk.Label(head, text="▾ 运行日志", bg=PANEL, fg=DIM,
                              font=FONT_XS, anchor="w", padx=8, pady=3,
                              cursor="hand2")
        self._head.pack(side="left")
        self._head.bind("<Button-1>", lambda _e: self.toggle())
        # 右下角：停在历史时显示"↓ N 条新"，点一下回到底部继续跟
        self._chip = tk.Label(head, text="", bg=PANEL, fg=ACCENT, font=FONT_XS,
                              anchor="e", padx=8, pady=3, cursor="hand2")
        self._chip.pack(side="right")
        self._chip.bind("<Button-1>", lambda _e: self.to_bottom())
        # 抓手上下一拖就改高度：日志要读就得撑得开，不能只有 5 行
        self._grip = tk.Label(head, text="⇕", bg=PANEL, fg="#6b7075",
                              font=FONT_XS, padx=6, pady=3)
        try:
            # X11 的 fsep 在 Windows 上不认；size_ns 两边都有，认不出就算了
            self._grip.configure(cursor="size_ns")
        except Exception:
            pass
        self._grip.pack(side="right")
        self._grip.bind("<ButtonPress-1>", self._grip_press)
        self._grip.bind("<B1-Motion>", self._grip_move)
        self._grip.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_drag_y", None))

        self._wrap = tk.Frame(self, bg=PANEL)
        self._wrap.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        self.text = tk.Text(self._wrap, height=self._rows, bg="#16171a",
                            fg="#b9bdc4", font=("Consolas", 9), relief="flat",
                            bd=0, insertbackground=FG, wrap="char",
                            state="disabled")
        self._sb = tk.Scrollbar(self._wrap, command=self.text.yview, width=10,
                                bg=PANEL, troughcolor="#16171a", relief="flat", bd=0)
        self.text.configure(yscrollcommand=self._on_yview)
        self._sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, col in (("I", "#b9bdc4"), ("W", "#e0a03a"), ("E", "#e57b7b")):
            self.text.tag_configure(tag, foreground=col)
        # 末行淡底：一屏七八行飞快滚过时，"最新那条"要能被眼睛直接抓住
        self.text.tag_configure("LAST", background="#282d35")

        # 用户自己滚动的证据：滚轮 / 键盘翻页 / 拖滚动条。用 add="+" 不抢原有绑定。
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<KeyPress-Up>",
                   "<KeyPress-Down>", "<KeyPress-Prior>", "<KeyPress-Next>",
                   "<KeyPress-Home>", "<KeyPress-End>"):
            self.text.bind(ev, self._on_user_scroll, add="+")
        for ev in ("<ButtonPress-1>", "<B1-Motion>", "<ButtonRelease-1>"):
            self._sb.bind(ev, self._on_user_scroll, add="+")
        self.bind("<Configure>", self._on_configure, add="+")

        self.bind("<Destroy>", self._on_destroy)
        self._tick()

    # ---------------- 写入

    def push(self, line, level="INFO"):
        """线程安全入口：只往队列里放，绝不碰 Tk。工作线程一律走这里。"""
        try:
            self._q.append((str(line), level))
        except Exception:
            pass

    def append(self, line, level="INFO"):
        """主线程直接写（测试、或已知在 UI 线程时）。"""
        self._emit([(str(line).replace("\r", ""), self._tag(level))])

    @staticmethod
    def _tag(level):
        return {"INFO": "I", "WARN": "W", "ERR": "E"}.get(level, "I")

    def _tick(self):
        """队列抽取节拍。跨线程只碰 deque，Tk 调用全部发生在主线程。"""
        if not self._alive:
            return
        try:
            if self._q:
                batch = []
                while self._q:
                    line, lvl = self._q.popleft()
                    batch.append((line.replace("\r", ""), self._tag(lvl)))
                if len(batch) > self.KEEP:
                    batch = batch[-self.KEEP:]
                self._emit(batch)
        except Exception:
            pass
        finally:
            if self._alive:
                try:
                    self.after(self.TICK, self._tick)
                except Exception:
                    self._alive = False

    def _emit(self, batch):
        if not batch:
            return
        self._guard += 1
        try:
            # 注意顺序：Tk 8.6 的 Text 在 state="disabled" 下连程序化的 delete
            # 都会被**静默忽略**（不报错、也不删）。所以裁剪和打标必须在恢复
            # 只读之前做完——实测踩过：先 disable 再 trim，日志会一路涨到 400 行以上。
            self.text.configure(state="normal")
            for line, tag in batch:
                self.text.insert("end", line + "\n", tag)
            self._trim()
            self._mark_last()
            if self._follow:
                self.text.see("end")
            self.text.configure(state="disabled")
        finally:
            self._guard -= 1
        if not self._follow:
            self._pending += len(batch)
        self._sync_head()

    def _count(self):
        try:
            return int(self.text.index("end-1c").split(".")[0])
        except Exception:
            return self._lines

    def _trim(self):
        """只留最后 KEEP 行。停在历史时把视图按删掉的屏数一起挪，否则内容会跳。"""
        n = self._count()
        if n <= self.KEEP:
            self._lines = n
            return
        drop = min(n - self.KEEP + 100, n - 1)
        top = 1
        if not self._follow:
            try:
                top = int(self.text.index("@0,0").split(".")[0])
            except Exception:
                top = 1
        self.text.delete("1.0", f"{drop + 1}.0")
        self._lines = self._count()
        if not self._follow:
            try:
                self.text.yview(str(max(1, top - drop)))
            except Exception:
                pass

    def _mark_last(self):
        try:
            self.text.tag_remove("LAST", "1.0", "end")
            if self._lines:
                self.text.tag_add("LAST", "end-2l linestart", "end-1c")
        except Exception:
            pass

    # ---------------- 跟随 / 滚动位置
    #
    # 判据分两个方向，各用各的证据：
    #   · "离开底部"只认**用户输入**（滚轮/滚条），不认 yview 的变化。因为窗口
    #     变高变矮、日志裁剪都会让 yview 抖一下，那不算用户想看历史，误判会让
    #     界面一直挂着"已停在历史"。
    #   · "回到底部"可以认 yview：到底就是到底，不会冤枉人。

    def _on_user_scroll(self, _ev=None):
        """用户动了滚轮或滚动条——等这次滚动真正生效后再判断他在哪。"""
        try:
            self.after(0, self._settle_follow)
        except Exception:
            pass

    def _settle_follow(self):
        try:
            last = self.text.yview()[1]
        except Exception:
            return
        if last >= 0.999:
            if not self._follow:
                self._follow, self._pending = True, 0
        elif self._follow:
            self._follow = False
        self._sync_head()

    def _on_yview(self, first, last):
        """只同步滚动条，不判定跟随状态。

        原先这里在 last>=0.999 时自动恢复跟随，但"视图够到末尾"并不等于
        "用户滚回了末尾"——把日志区从 5 行撑到 8 行时，Tk 会把视图重新贴到
        末尾，于是正在翻历史的人被硬拽回最新一行（200% DPI 下必现）。
        恢复跟随只认用户输入，统一交给 _settle_follow。
        """
        try:
            self._sb.set(first, last)
        except Exception:
            pass

    def _on_configure(self, _ev=None):
        """窗口/日志区被拉大拉小时，跟随着就重新贴到最新一行。"""
        if not (self._alive and self._open and self._follow):
            return
        self._guard += 1
        try:
            self.text.see("end")
        except Exception:
            pass
        finally:
            self._guard -= 1

    def to_bottom(self):
        """回到最新一行，并恢复跟随。"""
        self._follow, self._pending = True, 0
        self._guard += 1
        try:
            self.text.see("end")
        except Exception:
            pass
        finally:
            self._guard -= 1
        self._sync_head()

    def _head_text(self):
        if not self._open:
            return "▸ 运行日志"
        if not self._follow:
            return "▾ 运行日志 · 已停在历史"
        return "▾ 运行日志"

    def _sync_head(self):
        try:
            self._head.config(text=self._head_text(), fg=ACCENT if not self._follow else DIM)
            if self._follow:
                self._chip.config(text="")
            else:
                self._chip.config(text=f"↓ {self._pending} 条新" if self._pending
                                  else "↓ 最新")
        except Exception:
            pass

    # ---------------- 高度

    def _line_h(self):
        try:
            return max(1, tkfont.Font(font=self.text["font"]).metrics("linespace"))
        except Exception:
            return 15

    def _grip_press(self, ev):
        self._drag_y = ev.y_root
        self._drag_rows = self._rows

    def _grip_move(self, ev):
        if self._drag_y is None:
            return
        dy = self._drag_y - ev.y_root          # 往上拖 = 变高
        self.set_rows(self._drag_rows + int(round(dy / self._line_h())), user=True)

    def set_rows(self, rows, user=False):
        rows = max(self.MIN_ROWS, min(self.MAX_ROWS, int(rows)))
        self._rows = rows
        if user:
            self.user_sized = True
        if not self._open:
            return                  # 折叠着先记下，展开时生效
        try:
            self.text.configure(height=rows)
        except Exception:
            return
        if self._follow:
            self.to_bottom()

    @property
    def rows(self):
        return self._rows

    # ---------------- 折叠

    def toggle(self):
        self._open = not self._open
        if not self._open:
            self._wrap.pack_forget()
        else:
            self._wrap.pack(fill="both", expand=True, padx=8, pady=(0, 6))
            try:
                self.text.configure(height=self._rows)
            except Exception:
                pass
            self.to_bottom()        # 收起期间的日志，展开就该直接看最新的
        self._sync_head()

    @property
    def is_open(self):
        return self._open

    def _on_destroy(self, ev):
        if ev.widget is self:
            self._alive = False
