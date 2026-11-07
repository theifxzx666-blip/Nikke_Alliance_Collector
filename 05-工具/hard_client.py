# -*- coding: utf-8 -*-
r"""困难模式 · 联盟记录采集（独立 demo，第二客户端）

只做困难模式这一种界面，只输出「成员 / BOSS / 伤害」三列——
百分比与等级按需求不采（困难模式等级恒为 LEVEL 1，没有信息量）。

和 `raid_window.py` 的关系：**完全独立**，不动那边一行。底层识别复用
`raid_ocr` / `raid_core`（那是验证过的），采集循环按昨晚的教训重写：

  · 只拖动。真机确认这个弹窗不吃滚轮，滚轮/翻页键一次都不试。
  · 「没挪动」→ 放大步长再试一次；「拼不上」→ 往回小步补采。两者不混为一谈。
  · 重叠区回填：旧行缺字段时用这一屏同位置的完整读数补上。
  · 成员名 det 漏检时按**几何 ROI 补认**（今日实测 6 条里漏 2 条，全是这种情况）。

直接跑本文件（不带参数）会拿一张样张做离线自测，不弹窗口：

    .venv\Scripts\python.exe hard_client.py 样张.png
"""
from __future__ import annotations

import csv
import ctypes
import json
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

import alliance_ocr as A
import raid_nav as rn
import raid_ocr as ro
from raid_capture import CaptureEngine

HERE = Path(__file__).resolve().parent
from raid_hard import load_bosses, read_hard

RECT_JSON = A.APP_DIR / "预设" / "困难框选.json"

def _selftest(path):
    from PIL import Image
    eng = A.get_engine()
    img = Image.open(path).convert("RGB")
    recs, loc = read_hard(img, eng, roster=A.load_roster(), bosses=load_bosses())
    print(f"pitch={loc.pitch:.1f} box={loc.box} 锚点={len(loc.cards)} 记录={len(recs)}")
    bad = 0
    for i, r in enumerate(recs, 1):
        flag = "" if (r["member"] and r["boss"] and r["damage"] is not None) else "  ← 缺字段"
        bad += 1 if flag else 0
        print(f"r{i:02d} {r['member']:12s} | {r['boss']:10s} | {r['damage_raw']:>15s}"
              f" | {r['pct']:>6s} | {r['conf']}"
              f" | {r['member_note'] or r['boss_note'] or ''}{flag}")
    print(f"\n完整 {len(recs) - bad}/{len(recs)}")
    return 0 if not bad else 1


class HardClient(tk.Tk, CaptureEngine):
    """困难模式采集窗口。采集判据在 `raid_capture.CaptureEngine`，这里只做界面。"""

    def __init__(self):
        super().__init__()
        self.title("联盟突袭 · 困难模式采集（demo）")
        self.geometry("640x800")
        self.minsize(560, 640)
        self.configure(bg=BG)
        self.win = None
        self.recs = []
        self._stop = threading.Event()
        self._q = queue.Queue()
        self._roster = {}
        self._bosses = []
        self._thread = None
        self.rect_rel = None        # 框选的识别范围（相对有效画面的比例），None = 全屏
        try:
            if RECT_JSON.exists():
                _r = json.loads(RECT_JSON.read_text(encoding="utf-8")).get("rect_rel")
                if _r and len(_r) == 4:
                    self.rect_rel = tuple(float(v) for v in _r)
        except Exception:
            A.log_exc("hard-load-rect")
        self._build()
        self.bind("<Escape>", lambda e: self.stop())     # 采集时按 Esc 随时停
        self.after(80, self._pump)
        self.say("先【接窗口】，再在游戏里开到「联盟记录」页，然后【采集本屏】确认认得对")
        self.say("采集过程中按 Esc 可随时停止")
        if not self.elevated:
            self.say("当前是普通权限。若游戏以管理员运行，Windows 会拦住鼠标注入"
                     "（截图和识别正常，但拖动无效）→ 点【以管理员重启】")

    # ---------------- 界面
    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=10, pady=(10, 4))
        self.lbl = tk.Label(top, text="未接窗口", bg=BG, fg=FG, anchor="w",
                            font=("Microsoft YaHei UI", 10))
        self.lbl.pack(side="left")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=10, pady=4)
        self.btn_attach = self._btn(bar, "接窗口", self.attach, True)
        self.btn_pick = self._btn(bar, "框选", self.pick_rect)
        self.btn_shot = self._btn(bar, "采集本屏", self.shot)
        self.btn_start = self._btn(bar, "开始滚采", self.start, True)
        self.btn_stop = self._btn(bar, "停止", self.stop)
        self.btn_export = self._btn(bar, "导出 CSV", self.export)
        # 游戏以管理员运行时，普通权限进程会被 UIPI 拦住，点击/拖动注入全部失效
        self.elevated = bool(A.self_elevated())
        self.btn_admin = self._btn(bar, "以管理员重启",
                                   self.relaunch_admin, not self.elevated)

        # 第二排只放导航：它要现场找字再点，和采集不是同一步
        bar2 = tk.Frame(self, bg=BG)
        bar2.pack(fill="x", padx=10, pady=(0, 4))
        self.btn_nav = self._btn(bar2, "一键进记录", self.nav_record)
        tk.Label(bar2, text="联盟页 → 联盟突袭 → 联盟记录（按文字锚点找，找不到就停）",
                 bg=BG, fg=FG, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)

        wrap = tk.Frame(self, bg=BG)
        wrap.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        cols = ("member", "boss", "damage", "pct", "conf")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", height=12)
        for c, t, w in (("member", "成员", 165), ("boss", "BOSS", 130),
                        ("damage", "伤害", 160), ("pct", "剩余血量", 80),
                        ("conf", "置信度", 70)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.log = tk.Text(self, height=10, bg="#15161a", fg="#c9ccd2", relief="flat",
                           font=("Consolas", 9), wrap="char", state="disabled")
        self.log.pack(fill="x", padx=10, pady=(0, 10))

    def _btn(self, parent, text, cmd, primary=False):
        b = tk.Button(parent, text=text, command=cmd, relief="flat", bd=0,
                      bg=ACCENT if primary else PANEL, fg="#ffffff" if primary else FG,
                      activebackground=ACCENT, activeforeground="#ffffff",
                      font=("Microsoft YaHei UI", 10), padx=12, pady=5, cursor="hand2")
        b.pack(side="left", padx=(0, 6))
        return b

    def say(self, msg, level="INFO"):
        self._q.put(("log", msg))
        try:
            A.logline(f"hard: {msg}", level)
        except Exception:
            pass

    def _pump(self):
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log.configure(state="normal")
                self.log.insert("end", f"{datetime.now():%H:%M:%S}  {payload}\n")
                self.log.see("end")
                self.log.configure(state="disabled")
            elif kind == "rows":
                self._show(payload)
            elif kind == "shot":
                self.shot()                     # 导航成功后自动校验一屏
            elif kind == "done":
                for b in (self.btn_start, self.btn_shot, self.btn_nav):
                    b.configure(state="normal")
                self.btn_stop.configure(state="disabled")
        self.after(80, self._pump)

    def _show(self, rows):
        self.recs = rows
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", values=(
                r.get("member", ""), r.get("boss", ""), r.get("damage_raw", ""),
                r.get("pct", ""), r.get("conf") if r.get("conf") is not None else ""))

    # ---------------- 接窗口
    def attach(self):
        try:
            info, _others = A.find_game_window()
        except Exception as e:
            self.say(f"找窗口出错：{e}")
            return
        if info is None:
            self.say("没找到游戏窗口 —— 先把客户端打开并显示出来")
            return
        self.win = info
        self.lbl.configure(text=f"已连接：{info.title} {info.w}×{info.h}")
        self._roster = A.load_roster()
        self._bosses = load_bosses()
        self.say(f"昵称库 {len((self._roster or {}).get('names') or {})} 人 · 本期 BOSS {self._bosses}")

    def _region(self):
        if self.win is None:
            raise RuntimeError("还没接上游戏窗口")
        info = A.content_rect(self.win.hwnd)
        if info is None:
            raise RuntimeError("游戏窗口找不到了")
        if abs(info.aspect - A.TARGET_ASPECT) / A.TARGET_ASPECT > A.ASPECT_TOL:
            raise RuntimeError(f"有效画面 {info.w}×{info.h} 不是 16:9")
        x0, y0 = info.x, info.y
        return (x0, y0, x0 + info.w, y0 + info.h)

    # ---------------- 框选识别范围
    def pick_rect(self):
        """手动框住卡片列表（不含顶部日期栏/滚动条）。存的是相对比例，换分辨率自动换算。"""
        if self.win is None:
            self.say("先【接窗口】")
            return
        self.withdraw()
        self.after(260, self._pick_rect)

    def _pick_rect(self):
        try:
            info = A.content_rect(self.win.hwnd)
            if info is None:
                self.say("游戏窗口不见了，框选取消")
                self.deiconify()
                return
            p = A.RegionPicker(
                self, "拖动框住「卡片列表」：第一张卡上沿 → 最后一张卡下沿"
                      "（顶部日期栏、右侧滚动条不要框进来）· Esc 取消")
            self.wait_window(p)
            if not p.result:
                self.say("已取消框选")
                return
            x0, y0, x1, y1 = p.result
            self.rect_rel = ((x0 - info.x) / info.w, (y0 - info.y) / info.h,
                             (x1 - info.x) / info.w, (y1 - info.y) / info.h)
            RECT_JSON.parent.mkdir(parents=True, exist_ok=True)
            RECT_JSON.write_text(
                json.dumps({"rect_rel": [round(v, 4) for v in self.rect_rel]},
                           ensure_ascii=False), encoding="utf-8")
            self.say(f"已框选：相对 {tuple(round(v, 3) for v in self.rect_rel)} · 存于 {RECT_JSON.name}")
        except Exception as e:
            self.say(f"框选出错：{e}")
            A.log_exc("hard-pick-rect")
        finally:
            self.deiconify()


    def relaunch_admin(self):
        """用 runas 重新拉起自己（pythonw + 本文件），成功后关掉当前实例。

        游戏以管理员身份运行时，Windows 的 UIPI 会拦住普通权限进程的鼠标注入：
        **截图、识别都正常，唯独点击和拖动不生效**。表现就是"能采本屏、一滚就不动"，
        很容易被误当成"列表到底"。
        """
        try:
            py = Path(sys.executable)
            pyw = py.with_name("pythonw.exe")
            if not pyw.exists():
                pyw = py
            r = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(pyw), f'"{Path(__file__).resolve()}"', str(HERE), 1)
            if r > 32:                      # >32 成功；1223 = 用户在 UAC 上点了取消
                self.say("已请求提权重启 —— 同意 UAC 后本窗口会关闭，新窗口带管理员权限")
                self.destroy()
            else:
                self.say(f"提权未完成（代码 {r}，1223 = 在 UAC 弹窗上点了取消）")
        except Exception as e:
            self.say(f"提权失败：{e}")
            A.log_exc("hard-relaunch")






    def shot(self):
        try:
            img = self._crop(A.grab(self._region()))
        except Exception as e:
            self.say(f"截图失败：{e}")
            return
        recs, loc = read_hard(img, A.get_engine(), self._roster, self._bosses)
        if not loc.cards:
            self.say("这一屏没认到记录 —— 确认「联盟记录」已打开")
            return
        self._show(recs)
        miss = sum(1 for r in recs if not (r["member"] and r["boss"] and r["damage"] is not None))
        self.say(f"本屏 {len(recs)} 条 · 行距 {loc.pitch:.0f}px · 缺字段 {miss} 条")

    # ---------------- 自动导航
    def nav_record(self):
        """一键从联盟页点到联盟记录页。

        为什么敢自动点：`RaidNav` 每一步都是「先截图 → 认出字 → 才点」，
        只认分数 ≥0.9 且文本完全匹配的 token，点完还要轮询校验页面变了没有；
        任何一步对不上就停并说明卡在哪，绝不在错的页面上继续点。
        """
        if self.win is None:
            self.say("先【接窗口】")
            return
        try:
            region = self._region()
        except Exception as e:
            self.say(str(e))
            return
        # 导航要按 ESC 回退，而 ESC 发给**当前前台窗口**：
        # 不先把游戏提到前台，这一下会发给本工具自己。
        if not A.focus_window(self.win.hwnd):
            self.say("切不到游戏前台 —— 先点一下游戏画面再试", "WARN")
            return
        for b in (self.btn_nav, self.btn_start, self.btn_shot):
            b.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._stop.clear()
        self._thread = threading.Thread(target=self._nav_worker, args=(region,), daemon=True)
        self._thread.start()

    def _nav_worker(self, region):
        try:
            eng = A.get_engine()
            self.say("自动导航：大厅 → 联盟 → 联盟突袭 → 联盟记录…")
            nav = rn.RaidNav(region, A.grab, A.click_at,
                             lambda im: ro.tokens(im, eng), self.say, self._stop,
                             esc=A.tap_key)
            ok, msg = nav.goto_record()
            self.say(msg, "INFO" if ok else "ERR")
            if ok:
                self._q.put(("shot", None))     # 进页面后自动跑一次「采集本屏」做校验
            else:
                self.say("已停下，没有再点任何按钮", "WARN")
        except Exception as e:
            self.say(f"自动导航出错：{e}")
            A.log_exc("hard-nav")
        finally:
            self._q.put(("done", None))

    # ---------------- 滚采
    def start(self):
        if self.win is None:
            self.say("先【接窗口】")
            return
        try:
            region = self._region()
        except Exception as e:
            self.say(str(e))
            return
        self.btn_start.configure(state="disabled")
        self.btn_shot.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._stop.clear()
        self._thread = threading.Thread(target=self.capture, args=(region,), daemon=True)
        self._thread.start()

    def emit_rows(self, rows):
        self._q.put(("rows", list(rows)))

    def emit_done(self):
        self._q.put(("done", None))

    def stop(self):
        if not (self._thread and self._thread.is_alive()):
            return                      # 没在跑就别喊"已请求停止"，容易误以为卡住了
        self._stop.set()
        self.say("已请求停止，等当前这步做完（Esc 同效）")

    def export(self):
        if not self.recs:
            self.say("还没有数据")
            return
        out = A.OUT_DIR / f"困难伤害_{datetime.now():%Y%m%d_%H%M}.csv"
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["序号", "成员", "BOSS", "伤害", "剩余血量", "置信度"])
            for i, r in enumerate(self.recs, 1):
                w.writerow([i, r["member"], r["boss"], r.get("damage_raw") or "",
                            r.get("pct") or "",
                            "" if r.get("conf") is None else r["conf"]])
        miss = [i for i, r in enumerate(self.recs, 1)
                if not (r.get("member") and r.get("boss") and r.get("damage") is not None)]
        if miss:
            self.say(f"⚠ 第 {miss} 条缺字段，已照常导出，请人工核对", "WARN")
        self.say(f"已导出 {len(self.recs)} 条 → {out}")


BG = "#1b1d21"
PANEL = "#2a2d33"
ACCENT = "#2f6fd0"
FG = "#d6d9de"


def main():
    # pythonw.exe 下 sys.stdout/stderr 是 None，而 RapidOCR 启动时会往 stderr 打日志，
    # 任何输出都会抛异常并静默退出（连窗口都不出来）。必须先把它接到日志文件上。
    A._fix_streams()
    A.dpi_aware()
    app = HardClient()
    app.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raise SystemExit(_selftest(sys.argv[1]))
    main()
