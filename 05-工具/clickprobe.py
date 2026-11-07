"""坐标采集器：你在游戏里点，它把坐标和点前/点后画面全记下来，再逐张识图。

为什么需要它
------------
导航锚点是从截图反算出来的比例坐标，算错了要跑一次导航才知道。更麻烦的是
「点了但没进下一层」——日志只会说「页面没变」，不会说「队员」这两个字到底
在屏幕哪儿、OCR 有没有把它认出来。这个工具补的就是这一段：你照平常点，
它把每次点击的屏幕坐标、换算后的画面比例、点前/点后两张图、以及全屏 OCR
结果一起记下来，最后出一份报告，直接对出「你点的位置」和「字的位置」差多少。

三件必须做对的事
----------------
1. **不抢焦点**：只有点击发生时游戏在前台才记录。所以你点自己的工具按钮不会
   产生垃圾记录，点游戏也不会因为工具置顶而被吃掉。
2. **点前画面要真的有**：轮询发现点击时画面已经开始变了，所以点前那帧靠环形
   缓冲（每 0.12 秒一帧，留 3 帧，取最老的那张），保证一定早于点击。
3. **不猜**：窗口接不上、画面比例不对、点到画面外，都照实标出来，不替用户圆场。

用法
----
    .venv\\Scripts\\python clickprobe.py

打开后照平常点游戏。点完点【生成报告】，报告里每一步都列出：
屏幕坐标 → 画面比例 → 点后画面里离你最近的那行字（含偏差距离）。
"""

import ctypes
import json
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import alliance_ocr as core

VK_LBUTTON = 0x01
POLL = 0.025        # 鼠标轮询间隔（秒）
SHOT_GAP = 0.12     # 缓冲截图间隔
BUF = 3             # 点前画面留几帧
SETTLE = 0.90       # 点后等多久再截（等动画走完）
DUP_MS = 0.45       # 同一位置这么短时间内重复点击只算一次（双击/抖动）


def _fmt(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def nearest_block(blocks, rx, ry):
    """离 (rx, ry) 最近的一个文字块。返回 (距离, 文本, 中心x, 中心y, 置信) 或 None。

    报告里最有用的一栏：不用自己算，直接看出点的地方和字差多远。
    """
    if not blocks or rx is None or ry is None:
        return None
    best = None
    for text, box, score in blocks:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        d = ((cx - rx) ** 2 + (cy - ry) ** 2) ** 0.5
        if best is None or d < best[0]:
            best = (d, text, cx, cy, score)
    return best


class Probe:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.win = None                  # 当前接上的 WinInfo
        self.points = []                 # 每次点击一条
        self.buf = deque(maxlen=BUF)     # [(时刻, 图)] 环形缓冲
        self.outdir = None
        self._last_shot = 0.0
        self._was_down = False
        self._recording = True
        self._alive = True
        self._dup = (0, 0, 0.0)
        self._ocr_lock = threading.Lock()   # OCR 引擎不是线程安全的，串行进
        self._collapsed = False
        self._build()
        self.attach()
        threading.Thread(target=self._poll, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # ---------------------------------------------------------------- 界面

    def _build(self):
        r = self.root
        r.title("坐标采集")
        r.configure(bg=core.BG)
        r.geometry("560x380+24+24")
        r.attributes("-topmost", True)

        head = tk.Frame(r, bg=core.BG, padx=12, pady=8)
        head.pack(fill="x")
        self.lab_win = tk.Label(head, text="正在找游戏窗口…", bg=core.BG,
                                fg=core.FG, font=core.FONT_SM, anchor="w")
        self.lab_win.pack(side="left")
        tk.Button(head, text="收起", command=self.collapse, bg=core.PANEL,
                  fg=core.FG, font=core.FONT_SM, relief="flat", bd=0,
                  padx=8).pack(side="right")

        self.body = tk.Frame(r, bg=core.BG, padx=12)
        self.body.pack(fill="both", expand=True)

        bar = tk.Frame(self.body, bg=core.BG)
        bar.pack(fill="x", pady=(0, 6))
        self.lab_state = tk.Label(bar, text="● 记录中（游戏在前台才记）", bg=core.BG,
                                  fg="#6ee7a8", font=core.FONT_SM, anchor="w")
        self.lab_state.pack(side="left")
        self.lab_cnt = tk.Label(bar, text="已记 0 次", bg=core.BG, fg="#9aa0a6",
                                font=core.FONT_SM, anchor="e")
        self.lab_cnt.pack(side="right")

        cols = ("n", "xy", "rel", "near", "diff")
        self.tree = ttk.Treeview(self.body, columns=cols, show="headings", height=9)
        for c, txt, w, anchor in (
            ("n", "#", 34, "center"), ("xy", "屏幕坐标", 100, "center"),
            ("rel", "画面比例", 120, "center"), ("near", "点后最近的字", 170, "w"),
            ("diff", "画面差", 70, "center"),
        ):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "near"))
        self.tree.pack(fill="both", expand=True)

        btns = tk.Frame(self.body, bg=core.BG)
        btns.pack(fill="x", pady=(8, 4))
        self.btn_rec = self._btn(btns, "暂停记录", self.toggle_rec)
        self._btn(btns, "生成报告", self.report, primary=True)
        self._btn(btns, "打开目录", self.open_dir)
        self._btn(btns, "清空", self.clear)
        self._btn(btns, "重接窗口", self.attach)

        self.lab_hint = tk.Label(
            self.body, bg=core.BG, fg="#7d8590", font=core.FONT_SM, anchor="w",
            justify="left", wraplength=520,
            text="照平常点：先在主页点右侧「联盟」，进联盟页后点顶部「队员」。\n"
                 "点完点【生成报告】，会自动识图并对出偏差。首次识别要加载模型，会慢几秒。")
        self.lab_hint.pack(fill="x", pady=(2, 6))

        # 收起后的那一行
        self.strip = tk.Frame(r, bg=core.BG, padx=10, pady=4)
        self.lab_strip = tk.Label(self.strip, text="", bg=core.BG, fg="#6ee7a8",
                                  font=core.FONT_SM, anchor="w")
        self.lab_strip.pack(side="left")
        tk.Button(self.strip, text="展开", command=self.collapse, bg=core.PANEL,
                  fg=core.FG, font=core.FONT_SM, relief="flat", bd=0,
                  padx=8).pack(side="right")

    def _btn(self, parent, text, cmd, primary=False):
        b = tk.Button(parent, text=text, command=cmd,
                      bg=core.ACCENT if primary else core.PANEL,
                      fg="#ffffff" if primary else core.FG,
                      font=core.FONT_SM, relief="flat", bd=0, padx=10, pady=4,
                      activebackground=core.PANEL, activeforeground=core.FG)
        b.pack(side="left", padx=(0, 6))
        return b

    def collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.body.pack_forget()
            self.strip.pack(fill="x")
            self.root.geometry("360x36+24+24")
        else:
            self.strip.pack_forget()
            self.body.pack(fill="both", expand=True)
            self.root.geometry("560x380+24+24")
        self._sync_strip()

    def _sync_strip(self):
        flag = "● 记录中" if self._recording else "⏸ 已暂停"
        self.lab_strip.config(text=f"{flag} · 已记 {len(self.points)} 次")

    # ---------------------------------------------------------------- 接窗口

    def attach(self):
        best, cands = core.find_game_window()
        if best is not None:
            self.win = best
            self.lab_win.config(
                text=f"已接上 {best.title[:26]} · 画面 {best.w}×{best.h}"
                     f"（{core.aspect_label(best.aspect)}）", fg=core.FG)
        elif cands:
            w = cands[0]
            self.win = w
            self.lab_win.config(
                text=f"⚠ 有 {len(cands)} 个疑似窗口，先按 {w.title[:16]} "
                     f"{w.w}×{w.h} 记（比例不是 16:9 的话坐标会错）", fg="#ffb454")
        else:
            self.win = None
            self.lab_win.config(text="⚠ 没找到游戏窗口 —— 打开客户端后点【重接窗口】",
                                fg="#ff6b6b")
        if self.win is not None:
            self.lab_hint.config(
                text=f"照平常点：先在主页点右侧「联盟」，进联盟页后点顶部「队员」。\n"
                     f"点完点【生成报告】，会自动识图并对出偏差。"
                     f"（当前有效画面 {self.win.w}×{self.win.h}）")

    # ---------------------------------------------------------------- 轮询

    def _poll(self):
        while self._alive:
            try:
                self._tick()
            except Exception as exc:
                core.log_exc("probe", exc)
            time.sleep(POLL)

    def _tick(self):
        if self.win is None:
            return
        fg = core.is_foreground(self.win.hwnd)
        now = time.time()

        # 只在游戏在前台时采缓冲：后台时截到的是别的窗口，存下来是垃圾
        if fg and now - self._last_shot >= SHOT_GAP:
            self._shot_once()
            self._last_shot = now

        down = bool(ctypes.windll.user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        rising = down and not self._was_down
        self._was_down = down
        if not (rising and fg and self._recording):
            return

        px, py = core.cursor_pos()
        t = time.time()
        lx, ly, lt = self._dup
        if abs(px - lx) <= 8 and abs(py - ly) <= 8 and (t - lt) < DUP_MS:
            return                     # 双击/抖动，合并成一次
        self._dup = (px, py, t)

        before = self.buf[0][1] if self.buf else None
        self._record(px, py, before)

    def _shot_once(self):
        info = core.content_rect(self.win.hwnd)
        if info is None:
            return
        self.buf.append((time.time(),
                         core.grab((info.x, info.y, info.x + info.w, info.y + info.h))))

    def _record(self, px, py, before):
        info = core.content_rect(self.win.hwnd)
        if info is None:
            return
        inside = (info.x <= px <= info.x + info.w and
                  info.y <= py <= info.y + info.h)
        rec = {
            "n": len(self.points) + 1,
            "x": px, "y": py,
            "rx": (px - info.x) / info.w if inside else None,
            "ry": (py - info.y) / info.h if inside else None,
            "inside": inside,
            "t": datetime.now().strftime("%H:%M:%S"),
            "frame": [info.x, info.y, info.w, info.h],
            "blocks": None, "diff": None, "before": None, "after": None,
        }
        self.points.append(rec)
        self._row(rec)
        self.lab_cnt.config(text=f"已记 {len(self.points)} 次")
        self._sync_strip()
        threading.Thread(target=self._settle, args=(rec, before), daemon=True).start()

    def _settle(self, rec, before):
        """等动画走完再截「点后」，存盘并识图。"""
        time.sleep(SETTLE)
        try:
            if self.win is None:
                return
            info = core.content_rect(self.win.hwnd)
            if info is None:
                return
            after = core.grab((info.x, info.y, info.x + info.w, info.y + info.h))
            d = core.frame_diff(before, after) if before is not None else -1.0
            rec["diff"] = d
            self._save(rec, before, after)

            with self._ocr_lock:
                blocks = core.read_screen(after)
            rec["blocks"] = [[t, list(b), float(s)] for t, b, s in blocks]

            near = nearest_block(blocks, rec["rx"], rec["ry"])
            self.root.after(0, lambda: self._fill(rec, near))
        except Exception as exc:
            core.log_exc("probe", exc)

    def _outdir(self):
        if self.outdir is None:
            self.outdir = core.OUT_DIR / f"坐标采集_{datetime.now():%Y%m%d_%H%M%S}"
            self.outdir.mkdir(parents=True, exist_ok=True)
        return self.outdir

    def _save(self, rec, before, after):
        d = self._outdir()
        n = rec["n"]
        if before is not None:
            rec["before"] = f"{n:02d}_点前.png"
            before.save(str(d / rec["before"]))
        if after is not None:
            rec["after"] = f"{n:02d}_点后.png"
            after.save(str(d / rec["after"]))
        (d / "points.json").write_text(
            json.dumps({"frame": rec["frame"], "points": self.points},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

    # ---------------------------------------------------------------- 列表

    def _row(self, rec):
        rel = (f"{_fmt(rec['rx'])},{_fmt(rec['ry'])}" if rec["inside"]
               else "画面外")
        self.tree.insert("", "end", iid=str(rec["n"]),
                         values=(rec["n"], f"{rec['x']},{rec['y']}", rel, "识别中…", "…"))

    def _fill(self, rec, near):
        rel = (f"{_fmt(rec['rx'])},{_fmt(rec['ry'])}" if rec["inside"]
               else "画面外")
        near_txt = "（什么都没认出）" if near is None else near[1]
        d = rec["diff"]
        diff = "—" if d is None or d < 0 else f"{d:.1f}"
        try:
            self.tree.item(str(rec["n"]), values=(rec["n"], f"{rec['x']},{rec['y']}",
                                                 rel, near_txt, diff))
        except Exception:
            pass

    # ---------------------------------------------------------------- 操作

    def toggle_rec(self):
        self._recording = not self._recording
        if self._recording:
            self.lab_state.config(text="● 记录中（游戏在前台才记）", fg="#6ee7a8")
            self.btn_rec.config(text="暂停记录")
        else:
            self.lab_state.config(text="⏸ 已暂停", fg="#ffb454")
            self.btn_rec.config(text="继续记录")
        self._sync_strip()

    def clear(self):
        if self.points and not messagebox.askyesno("清空", "把已记录的点击全丢掉？"):
            return
        self.points.clear()
        for i in self.tree.get_children():
            self.tree.delete(i)
        self.outdir = None
        self.lab_cnt.config(text="已记 0 次")
        self._sync_strip()

    def open_dir(self):
        if self.outdir is None or not self.outdir.exists():
            messagebox.showinfo("提示", "还没有生成任何东西——先点几次游戏再点【生成报告】")
            return
        import os
        os.startfile(str(self.outdir))

    def report(self):
        if not self.points:
            messagebox.showinfo("提示", "还没记到点击。先在游戏里点几下。")
            return
        text = self._report_text()
        d = self._outdir()
        p = d / "报告.txt"
        p.write_text(text, encoding="utf-8")
        self._show(text, p)

    def _report_text(self) -> str:
        f = self.points[0]["frame"]
        lines = [
            f"=== 坐标采集报告  {datetime.now():%Y-%m-%d %H:%M:%S} ===",
            f"有效画面 {f[2]}×{f[3]} @ ({f[0]},{f[1]})  "
            f"{core.aspect_label(f[2] / max(f[3], 1))}",
            f"共 {len(self.points)} 次点击",
            "",
        ]
        for r in self.points:
            lines.append(f"#{r['n']}  {r['t']}  屏幕 ({r['x']},{r['y']})"
                         f"  画面比例 ({_fmt(r['rx'])},{_fmt(r['ry'])})"
                         f"{'' if r['inside'] else '  ← 点在画面外'}")
            d = r["diff"]
            if d is None:
                lines.append("    点后画面：还没截到")
            elif d < 0:
                lines.append("    点后画面：没有点前画面可比")
            else:
                verdict = "画面变了" if d >= 2.0 else "画面基本没变"
                lines.append(f"    点前→点后差 {d:.1f}（{verdict}）")
            near = nearest_block(r["blocks"], r["rx"], r["ry"]) if r["blocks"] else None
            if r["blocks"] is None:
                lines.append("    识图：还没跑")
            elif not r["blocks"]:
                lines.append("    识图：整屏一个字都没认出")
            else:
                lines.append(f"    识图 {len(r['blocks'])} 块，"
                             f"离点击点最近的：")
                for text, box, sc in _rank_near(r["blocks"], r["rx"], r["ry"], top=4):
                    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                    gap = (((cx - r["rx"]) ** 2 + (cy - r["ry"]) ** 2) ** 0.5
                           if r["rx"] is not None else -1)
                    lines.append(f"      「{text}」  中心 ({_fmt(cx)},{_fmt(cy)})"
                                 f"  置信 {sc:.2f}  距点击 {_fmt(gap)}")
            if r["after"]:
                lines.append(f"    图：{r['after']}"
                             + (f" / {r['before']}" if r["before"] else ""))
            lines.append("")
        return "\n".join(lines)

    def _show(self, text, path):
        w = tk.Toplevel(self.root)
        w.title(f"报告 · {path.name}")
        w.configure(bg=core.BG)
        w.geometry("900x560")
        w.attributes("-topmost", True)
        box = tk.Text(w, bg=core.PANEL, fg=core.FG, font=("Consolas", 9),
                      relief="flat", wrap="none", padx=10, pady=8)
        box.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        box.insert("1.0", text)
        box.config(state="disabled")
        bar = tk.Frame(w, bg=core.BG, padx=8, pady=8)
        bar.pack(fill="x")
        tk.Label(bar, text=str(path), bg=core.BG, fg="#9aa0a6",
                 font=core.FONT_SM, anchor="w").pack(side="left")
        tk.Button(bar, text="复制全文", bg=core.PANEL, fg=core.FG, relief="flat",
                  font=core.FONT_SM, padx=10,
                  command=lambda: (self.root.clipboard_clear(),
                                   self.root.clipboard_append(text),
                                   messagebox.showinfo("已复制", "报告全文已在剪贴板"))
                  ).pack(side="right")

    def close(self):
        self._alive = False
        try:
            if self.points:
                self._outdir()
        except Exception:
            pass
        self.root.destroy()


def _rank_near(blocks, rx, ry, top=4):
    """按离点击点的距离排序，取最近几个。rx/ry 为 None 时按原始顺序。"""
    if rx is None or ry is None:
        return list(blocks)[:top]
    out = []
    for text, box, score in blocks:
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        out.append(((((cx - rx) ** 2 + (cy - ry) ** 2) ** 0.5), text, box, score))
    out.sort(key=lambda t: t[0])
    return [(t[1], t[2], t[3]) for t in out[:top]]


def main():
    root = tk.Tk()
    Probe(root)
    root.mainloop()


if __name__ == "__main__":
    main()
