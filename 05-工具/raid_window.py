# -*- coding: utf-8 -*-
"""联盟突袭 · 伤害记录采集窗口

入口：主窗口工具行的【伤害记录】。独立窗口，不改主界面的任何布局——
名单采集那条链路一行不动，避免"为了加个场景把能用的搞坏"。

能力边界（刻意划死）：
  · **不做自动点击导航**。联盟突袭 → 联盟记录的按钮位置没有实测样本，
    而本项目的铁律是"不盲点"。所以进页面这一步交给人：打开联盟 → 联盟突袭
    → 联盟记录，让列表露出来，再点【开始】。
  · 采集只做三件：截图认字 → 跨屏拼接 → 导出。拼接的证据只有一条——
    像素位移算出的"走了几行" + 重叠区内容核对；对不上就停，不猜。

为什么滚采不用"到底了就停止"这种简单判据：
  记录列表里同一个人对同一个 BOSS 打出同样的伤害是常态（实测 HANSON 两次都是
  2,158,171,750），所以"内容不再变化"不等于到底——可能是滚动没生效。
  这里用位移行数（0 行 = 没动）当唯一判据，连续两次 0 才收工。
"""
from __future__ import annotations

import csv
import json
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk, messagebox

from PIL import Image, ImageDraw, ImageTk

import alliance_ocr as A
import raid_cli
import raid_core as rc
import raid_nav
import raid_ocr as ro
import ui_kit as ui

APP_TITLE = "联盟突袭 · 伤害记录"
MAX_SCREENS = 60
SCROLL_ROWS = 2.5          # 每次往下走几行（留 ≥3 行重叠给拼接核对）


def write_diag(img, loc, out_dir, tag="诊断_伤害") -> Path:
    """把一屏的原图 + 每张卡片的框 + 各字段裁图落盘，返回目录。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = Path(out_dir) / f"{tag}_{ts}"
    d.mkdir(parents=True, exist_ok=True)
    img.save(d / "全屏.png")
    meta = {"pitch": loc.pitch, "days": loc.days, "box": loc.box, "cards": []}
    for i, c in enumerate(loc.cards, 1):
        item = {k: c.get(k) for k in
                ("member", "boss", "level", "level_note", "damage", "damage_raw",
                 "damage_note", "pct", "cy")}
        item["boxes"] = {k: c.get(k) for k in
                         ("member_box", "level_box", "level_roi", "level_roi_wide",
                          "boss_roi", "damage_box", "pct_roi")}
        meta["cards"].append(item)
        for tag_name, key in (("等级", "level_roi"), ("BOSS", "boss_roi"),
                              ("伤害", "damage_box"), ("占比", "pct_roi")):
            box = c.get(key)
            if not box:
                continue
            x0, y0, x1, y1 = [int(v) for v in box]
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(img.width, x1), min(img.height, y1)
            if x1 > x0 and y1 > y0:
                img.crop((x0, y0, x1, y1)).save(d / f"r{i:02d}_{tag_name}.png")
    (d / "卡片.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


PRESET_PATH = A.APP_DIR / "预设" / "伤害记录预设.json"
BOSS_TABLE = A.APP_DIR / "预设" / "盟战boss表.json"


def load_boss_table() -> dict:
    """读「国服盟战 BOSS 表」。一期只有 5 个固定 BOSS，这是校验名字最硬的依据。"""
    try:
        if BOSS_TABLE.exists():
            return json.loads(BOSS_TABLE.read_text(encoding="utf-8")) or {}
    except Exception:
        A.log_exc("raid-bosstable-load")
    return {}


def season_bosses(table: dict, season: str):
    seasons = (table or {}).get("seasons") or {}
    return list(seasons.get(season) or [])


def save_season(season: str) -> bool:
    t = load_boss_table()
    if not t:
        return False
    t["season"] = season
    try:
        BOSS_TABLE.write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception:
        A.log_exc("raid-bosstable-save")
        return False


def load_preset() -> dict:
    try:
        if PRESET_PATH.exists():
            return json.loads(PRESET_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        A.log_exc("raid-preset-load")
    return {}


def save_preset(data: dict) -> bool:
    try:
        PRESET_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRESET_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception:
        A.log_exc("raid-preset-save")
        return False


def mask_outside(img, rect):
    """把圈选范围之外的像素涂黑，再送检测。

    为什么不直接裁：裁了坐标系就变了，滚动区域、诊断裁图、拼接口令全要跟着换算，
    错一处就到处漂。涂黑保持尺寸不变，下游一行不用改。
    """
    if not rect:
        return img
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 - x0 < 30 or y1 - y0 < 30:
        return img
    out = Image.new("RGB", img.size, (0, 0, 0))
    out.paste(img.crop((x0, y0, x1, y1)), (x0, y0))
    return out


class RaidCalib(tk.Toplevel):
    """圈选识别范围：在截图上拖一个框，把「记录列表」圈进去。

    只圈**列表这一块**，不要圈单个字段——试过"圈一张卡再按比例切五个字段"，
    实测会把 `2,158,171,750` 切成 `2,58,171,750`、BOSS 名带上图标残字，
    越精确的框越脆。字段定位交给颜色锚点（红色 LEVEL 徽标）和 det，圈选只负责
    "别去看标题栏和按钮"。
    """

    def __init__(self, master, img: Image.Image, on_done):
        super().__init__(master)
        self.title("圈选识别范围")
        self.configure(bg=ui.BG)
        self.img = img
        self.on_done = on_done
        self._p0 = None
        self._rect = None
        self._scale = min(1.0, 560.0 / max(1, img.width))

        w = max(1, int(img.width * self._scale))
        h = max(1, int(img.height * self._scale))
        tk.Label(self, text="把「记录列表」整块框进来（含等级徽标到伤害数字即可，不必贴边）",
                 bg=ui.BG, fg=ui.DIM, font=ui.FONT_XS).pack(anchor="w", padx=10, pady=(8, 4))
        self.cv = tk.Canvas(self, width=w, height=h, bg="#000", highlightthickness=0)
        self.cv.pack(padx=10)
        self._photo = ImageTk.PhotoImage(
            img.resize((w, h), Image.Resampling.LANCZOS))
        self.cv.create_image(0, 0, anchor="nw", image=self._photo)
        self.cv.bind("<ButtonPress-1>", self._down)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._up)

        bar = tk.Frame(self, bg=ui.BG)
        bar.pack(fill="x", padx=10, pady=8)
        ui.btn(bar, "保存", self._ok, primary=True, small=True).pack(side="left")
        ui.btn(bar, "取消", self.destroy, small=True).pack(side="left", padx=6)

    def _down(self, e):
        self._p0 = (e.x, e.y)
        if self._rect:
            self.cv.delete(self._rect)
        self._rect = self.cv.create_rectangle(e.x, e.y, e.x, e.y,
                                              outline=ui.ACCENT, width=2)

    def _move(self, e):
        if self._p0 and self._rect:
            self.cv.coords(self._rect, self._p0[0], self._p0[1], e.x, e.y)

    def _up(self, e):
        if not self._p0:
            return
        x0, y0 = self._p0
        x1, y1 = e.x, e.y
        self._p0 = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._tip()

    def _tip(self):
        if not self._p0:
            return
        x0, y0, x1, y1 = self._p0
        self.title(f"圈选识别范围 · {int(x1 - x0)}×{int(y1 - y0)} px")

    def _ok(self):
        if not self._p0 or self._p0[2] - self._p0[0] < 20 or self._p0[3] - self._p0[1] < 20:
            messagebox.showwarning("范围太小", "请拖一个包含整块记录列表的框")
            return
        s = self._scale
        rel = [self._p0[0] / s / self.img.width, self._p0[1] / s / self.img.height,
               self._p0[2] / s / self.img.width, self._p0[3] / s / self.img.height]
        rel = [round(min(1.0, max(0.0, v)), 6) for v in rel]
        d = load_preset()
        d.update({"version": 1, "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                  "list_rel": rel, "resolution": f"{self.img.width}x{self.img.height}"})
        ok = save_preset(d)
        self.on_done(rel if ok else None)
        self.destroy()


def _range_of(preset, img):
    """把预设里的相对范围换算成当前截图的像素框。"""
    r = (preset or {}).get("list_rel")
    if not r or len(r) != 4:
        return None
    w, h = img.size
    return (r[0] * w, r[1] * h, r[2] * w, r[3] * h)


FIELD_STEPS = (("level", "① 红色 LEVEL 徽标", "贴着成员名左上那小块红底白字，**要贴边画**（画松了会带进上一行）"),
               ("boss", "② BOSS 名称", "成员名下面那行，可以画松一点（里面会自动定位文字行）"),
               ("pct", "③ 百分比", "伤害数字下面那行，比如 0.0%"))


def fields_from_canvas(anchor, boxes, scale, view):
    """把「在放大视图上画的框」换算成以成员名字高 h 为单位、相对成员名框的偏移。

    anchor = 成员名框（原图坐标）；boxes = 画布坐标的框列表；scale = 视图缩放；
    view = 视图左上角在原图中的位置。返回值可直接存进预设的 fields。
    """
    ax0, ay0, ax1, ay1 = anchor
    h = max(1.0, ay1 - ay0)
    vx0, vy0 = view[0], view[1]
    out = {}
    for (key, _n, _hint), (x0, y0, x1, y1) in zip(FIELD_STEPS, boxes):
        ix0, iy0, ix1, iy1 = vx0 + x0 / scale, vy0 + y0 / scale, \
                             vx0 + x1 / scale, vy0 + y1 / scale
        out[key] = [round((ix0 - ax0) / h, 3), round((iy0 - ay0) / h, 3),
                    round((ix1 - ax0) / h, 3), round((iy1 - ay0) / h, 3)]
    return out


class RaidFieldCalib(tk.Toplevel):
    """字段标定：在截图上依次画三个框，换算成「相对成员名字高的比例」存起来。

    为什么按比例而不是按像素：换分辨率/改 UI 缩放后，同一套比例仍然成立，重画一次即可。
    为什么锚在成员名上而不是整屏：列表会滚，行位置是变量；但「等级在名字左上 3 个字高」
    这件事不变。这跟阿卡的 alpha 遮罩是同一个思路，只是它的页面不动，我们的行会动。
    """

    def __init__(self, master, img: Image.Image, anchor_box, existing, on_done, view=None):
        super().__init__(master)
        self.title("标定字段（依次画三个框）")
        self.configure(bg=ui.BG)
        self.img = img
        self.anchor = anchor_box
        self.existing = dict(existing or {})
        self.on_done = on_done
        # 只显示「列表那一块」并放大：整屏缩到 560 宽时徽标只剩 20px，根本点不准。
        # 放进来的框哪怕多画几像素，换算回原图也就差 1~2px。
        if view:
            vx0, vy0, vx1, vy1 = [int(v) for v in view]
        else:
            vx0, vy0, vx1, vy1 = 0, 0, img.width, img.height
        vx0, vy0 = max(0, vx0), max(0, vy0)
        vx1, vy1 = min(img.width, vx1), min(img.height, vy1)
        if vx1 - vx0 < 60 or vy1 - vy0 < 60:
            vx0, vy0, vx1, vy1 = 0, 0, img.width, img.height
        self._view = (vx0, vy0, vx1, vy1)
        vw, vh = vx1 - vx0, vy1 - vy0
        self._scale = min(1.0, 620.0 / max(1, vw), 560.0 / max(1, vh))
        self._boxes = []
        self._p0 = None
        self._drag = None

        w = max(1, int(vw * self._scale))
        h = max(1, int(vh * self._scale))
        self.tip = tk.StringVar(value=self._step_text())
        tk.Label(self, textvariable=self.tip, bg=ui.BG, fg=ui.DIM, font=ui.FONT_XS,
                 wraplength=w - 8, justify="left").pack(anchor="w", padx=10, pady=(8, 4))
        self.cv = tk.Canvas(self, width=w, height=h, bg="#000", highlightthickness=0)
        self.cv.pack(padx=10)
        self._photo = ImageTk.PhotoImage(
            img.crop(self._view).resize((w, h), Image.Resampling.LANCZOS))
        self.cv.create_image(0, 0, anchor="nw", image=self._photo)
        self.cv.bind("<ButtonPress-1>", self._down)
        self.cv.bind("<B1-Motion>", self._move)
        self.cv.bind("<ButtonRelease-1>", self._up)

        bar = tk.Frame(self, bg=ui.BG)
        bar.pack(fill="x", padx=10, pady=8)
        self.btn_save = ui.btn(bar, "保存", self._ok, primary=True, small=True)
        self.btn_save.pack(side="left")
        ui.btn(bar, "重画", self._reset, small=True).pack(side="left", padx=6)
        ui.btn(bar, "取消", self.destroy, small=True).pack(side="left")

    # -- 提示文案
    def _step_text(self) -> str:
        i = len(self._boxes) + len(self._drag or [])
        done = len(self._boxes)
        if done >= len(FIELD_STEPS):
            return "三个框都画好了 —— 点【保存】"
        key, name, hint = FIELD_STEPS[done]
        return f"{name}：{hint}（已画 {done}/3）"

    def _down(self, e):
        self._p0 = (e.x, e.y)
        if self._drag:
            self.cv.delete(self._drag)
        self._drag = self.cv.create_rectangle(e.x, e.y, e.x, e.y, outline=ui.ACCENT, width=2)

    def _move(self, e):
        if self._p0 and self._drag:
            self.cv.coords(self._drag, self._p0[0], self._p0[1], e.x, e.y)

    def _up(self, e):
        if not self._p0 or not self._drag:
            return
        x0, y0 = self._p0
        x1, y1 = e.x, e.y
        box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._p0 = None
        if box[2] - box[0] < 8 or box[3] - box[1] < 6:
            self.cv.delete(self._drag)
            self._drag = None
            self.tip.set("框太小了，重新拖一次：" + FIELD_STEPS[min(len(self._boxes), 2)][1])
            return
        self.cv.itemconfig(self._drag, outline="#33c48d" if len(self._boxes) < 2 else "#e8b339",
                           width=2)
        self._boxes.append(box)
        self._drag = None
        self.tip.set(self._step_text())

    def _reset(self):
        for item in self.cv.find_all():
            if self.cv.type(item) == "rectangle":
                self.cv.delete(item)
        self._boxes = []
        self._drag = None
        self.tip.set(self._step_text())

    def _ok(self):
        if len(self._boxes) < len(FIELD_STEPS):
            messagebox.showwarning("还差框", "要把三个框都画完（等级 / BOSS / 百分比）")
            return
        self.on_done(fields_from_canvas(self.anchor, self._boxes,
                                        self._scale, self._view))
        self.destroy()


class RaidWindow(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title(APP_TITLE)
        self.configure(bg=ui.BG)
        self.minsize(560, 720)
        self.geometry("600x820")

        self.win = None
        self.records = []
        self.screens = 0
        self.doubt = 0
        self._q = queue.Queue()
        self._stop = threading.Event()
        self._worker = None
        self._attaching = False
        self._last = None          # (img, loc) 最近一屏，供【诊断帧】用
        self._auto_diag = False    # 等级认不到时自动存一帧，只存一次
        self._preset = load_preset()
        self._fields = (self._preset or {}).get("fields") or None
        try:
            ro.set_app_dir(A.APP_DIR)          # 外部识别 CLI 要知道「预设/」在哪
        except Exception:
            A.log_exc("raid-cli-setdir")
        self._boss_table = load_boss_table()
        self._season = (self._boss_table or {}).get("season") or "S19"
        self._bosses = season_bosses(self._boss_table, self._season)
        # 名单场景攒下的昵称库：伤害页上的人是同一批联盟成员，成员名读歪时用它归位
        try:
            self._roster = A.load_roster()
        except Exception:
            self._roster = {}
        self._range_rect = None    # 圈选范围（图片坐标），None = 全屏识别

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._tick_attach()
        self.after(120, self._pump)

    def _close(self):
        """关窗口：先让工作线程停，再销毁。否则线程还在往队列里塞，after 回调会炸。"""
        self._stop.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2.0)
        try:
            self.destroy()
        except Exception:
            pass

    # ---------------- 界面 ----------------
    def _build(self):
        head = tk.Frame(self, bg=ui.BG, padx=14, pady=4)
        head.pack(fill="x", pady=(8, 0))
        tk.Label(head, text=APP_TITLE, bg=ui.BG, fg=ui.FG,
                 font=ui.FONT_TITLE, anchor="w").pack(side="left")
        tk.Label(head, text="记录页 · 可自动进入", bg=ui.BG, fg="#6b7075",
                 font=ui.FONT_XS, anchor="e").pack(side="right", pady=(6, 0))

        self._infobar = tk.Frame(self, bg=ui.PANEL, padx=10, pady=5)
        self._infobar.pack(fill="x", padx=14, pady=(4, 6))
        self.info_var = tk.StringVar(value="正在接游戏窗口…")
        tk.Label(self._infobar, textvariable=self.info_var, bg=ui.PANEL, fg=ui.DIM,
                 font=ui.FONT_XS, anchor="w", justify="left",
                 wraplength=560).pack(anchor="w")
        # 第二行放控件：一行塞不下四个（实测 600px 宽时最右边的「期」会被裁掉）
        tools = tk.Frame(self._infobar, bg=ui.PANEL)
        tools.pack(fill="x", pady=(3, 0))
        self._season_var = tk.StringVar(value=self._season)
        seasons = list((self._boss_table or {}).get("seasons") or {})
        cb = ttk.Combobox(tools, textvariable=self._season_var, values=seasons,
                          width=4, state="readonly", font=ui.FONT_XS)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", self._season_changed)
        tk.Label(tools, text="期", bg=ui.PANEL, fg=ui.DIM,
                 font=ui.FONT_XS).pack(side="left", padx=(4, 10))
        ui.btn(tools, "圈选范围", self.pick_range, small=True).pack(side="right")
        ui.btn(tools, "自动进入", self.auto_enter, small=True).pack(side="right", padx=(0, 4))
        ui.btn(tools, "标定字段", self.pick_fields, small=True).pack(side="right", padx=(0, 4))
        ui.btn(tools, "测后端", self.test_backend, small=True).pack(side="right", padx=(0, 4))

        help_card = ui.Card(self, "怎么用")
        help_card.pack(fill="x", padx=14, pady=(0, 4))
        tk.Label(help_card.body,
                 text="停在联盟页或联盟突袭页都行，点【自动进入】——它会找字点进「联盟记录」，"
                      "窗口多大都认（坐标是当场从画面里读的）。\n"
                      "然后点【采集本屏】先看一屏认得对不对，再点【开始】整页滚采。",
                 bg=ui.PANEL, fg=ui.DIM, font=ui.FONT_XS, justify="left",
                 anchor="w").pack(fill="x")

        lst = ui.Card(self, "采集到的记录 · 双击可删误识别")
        lst.pack(fill="both", expand=True, padx=14, pady=(2, 4))
        sty = ttk.Style(self)
        sty.configure("Raid.Treeview", background="#16171a", fieldbackground="#16171a",
                      foreground=ui.FG, rowheight=22, borderwidth=0)
        sty.configure("Raid.Treeview.Heading", background=ui.PANEL, foreground=ui.DIM,
                      relief="flat", borderwidth=0)
        sty.map("Raid.Treeview", background=[("selected", ui.ACCENT_DK)],
                foreground=[("selected", "white")])
        cols = ("day", "member", "boss", "level", "damage", "pct")
        self.tree = ttk.Treeview(lst.body, columns=cols, show="headings",
                                 height=10, style="Raid.Treeview")
        for c, t, w, a in (("day", "天", 36, "center"),
                           ("member", "成员", 110, "w"),
                           ("boss", "BOSS", 112, "w"),
                           ("level", "等级", 40, "center"),
                           ("damage", "伤害", 128, "e"),
                           ("pct", "占比", 56, "center")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor=a, stretch=(c in ("member", "boss", "damage")))
        sb = ttk.Scrollbar(lst.body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self._del_selected)

        self.ledger = ui.Ledger(self, [("屏数", ""), ("记录", ""), ("存疑", "")])
        self.ledger.pack(fill="x", padx=14, pady=(0, 4))

        self.logpane = ui.LogPane(self, height=5)
        self.logpane.pack(fill="x", padx=14, pady=(0, 4))

        bar = tk.Frame(self, bg=ui.BG, padx=14, pady=2)
        bar.pack(fill="x", side="bottom")
        self.btn_start = ui.btn(bar, "开始", self.start_auto, primary=True)
        self.btn_stop = ui.btn(bar, "停止", self.stop_auto)
        self.btn_start.pack(side="left", expand=True, fill="x", padx=3)
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=3)
        ui.disable(self.btn_stop)

        r = tk.Frame(self, bg=ui.BG, padx=14)
        r.pack(fill="x", side="bottom", pady=(0, 4))
        for text, cmd in (("采集本屏", self.capture_one), ("导出明细", self.export_detail),
                          ("导出汇总", self.export_summary), ("诊断帧", self.save_diag),
                          ("清空", self.clear), ("接窗口", self.reattach)):
            ui.btn(r, text, cmd, small=True).pack(side="left", padx=(0, 3))

        self.status = tk.Label(self, text="准备就绪", bg=ui.BG, fg=ui.DIM,
                               font=ui.FONT_XS, anchor="w", padx=14)
        self.status.pack(fill="x", side="bottom", pady=(0, 2))

    # ---------------- 日志 / 状态 ----------------
    def say(self, msg: str, level: str = "INFO"):
        try:
            self.logpane.push(f"{datetime.now():%H:%M:%S}  {msg}", level)
        except Exception:
            pass
        A.log("raid", msg, level)
        try:
            # 状态行只有一行宽：长路径截断，完整内容在日志区里
            self.status.config(text=msg if len(msg) <= 46 else msg[:45] + "…")
        except Exception:
            pass

    def _pump(self):
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "rows":
                    self._set_rows(payload)
                elif kind == "frame":
                    self._last = payload
                elif kind == "doubt":
                    self.doubt += payload
                    self._refresh_ledger()
                elif kind == "done":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _refresh_ledger(self):
        try:
            self.ledger.set(屏数=str(self.screens), 记录=str(len(self.records)),
                            存疑=str(self.doubt))
        except Exception:
            pass

    def _set_rows(self, rows):
        self.records = rows
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            self.tree.insert("", "end", values=(
                r.get("day") if r.get("day") is not None else "?",
                r.get("member", ""), r.get("boss", ""),
                r.get("level") if r.get("level") is not None else "?",
                rc.fmt_damage(r.get("damage")) or f"⚠{r.get('damage_raw','')}",
                r.get("pct", "")))
        self._refresh_ledger()

    # ---------------- 窗口接入 ----------------
    def _tick_attach(self):
        if self.win is None and not self._attaching:
            self._attach_once()
        self.after(2000, self._tick_attach)

    def _attach_once(self):
        self._attaching = True
        try:
            info, _others = A.find_game_window()
            if info is None:
                self.info_var.set("没接上游戏窗口 · 打开客户端后自动接上")
                return
            self.win = info
            self._update_info()
            self.say(f"已接上窗口：{info.title} {info.w}×{info.h}")
        except Exception:
            A.log_exc("raid-attach")
        finally:
            self._attaching = False

    def _update_info(self):
        if self.win is None:
            return
        info = A.content_rect(self.win.hwnd)
        if info is None:
            self.info_var.set("⚠ 游戏窗口找不到了（可能已关闭或最小化）")
            return
        ok = abs(info.aspect - A.TARGET_ASPECT) / A.TARGET_ASPECT <= A.ASPECT_TOL
        if not getattr(self, "_cli_told", False):
            self._cli_told = True
            try:
                info_cli = raid_cli.probe(A.APP_DIR)
                if info_cli.get("enabled") and info_cli.get("found"):
                    self.say(f"外部识别已启用：{info_cli.get('cmd')}")
                elif info_cli.get("enabled") and not info_cli.get("found"):
                    self.say(f"外部识别配置了但找不到程序：{info_cli.get('note')}", "WARN")
            except Exception:
                A.log_exc("raid-cli-probe")
        rng = (self._preset or {}).get("list_rel")
        tip = (f"· 范围 {int((rng[2]-rng[0])*100)}%×{int((rng[3]-rng[1])*100)}%"
               if rng else "· 全屏识别")
        self.info_var.set(
            f"已连接 {info.w}×{info.h} · "
            f"{'16:9 ✓' if ok else '✗ 请调成 16:9'} · {tip}")

    def reattach(self):
        self.win = None
        self._attach_once()

    def _region(self):
        """当前应采集的屏幕矩形（游戏有效画面）。拿不到就抛错，绝不返回半个坐标。"""
        if self.win is None:
            raise RuntimeError("还没接上游戏窗口")
        info = A.content_rect(self.win.hwnd)
        if info is None:
            raise RuntimeError("游戏窗口找不到了（可能已关闭或最小化）")
        if abs(info.aspect - A.TARGET_ASPECT) / A.TARGET_ASPECT > A.ASPECT_TOL:
            raise RuntimeError(f"有效画面 {info.w}×{info.h} 不是 16:9，请调整窗口后重试")
        if info.h < A.MIN_CLIENT_H:
            raise RuntimeError(f"有效画面只有 {info.h}px 高，窗口太小")
        x0, y0 = info.x, info.y
        return (x0, y0, x0 + info.w, y0 + info.h)

    def _season_changed(self, _e=None):
        self._season = self._season_var.get()
        self._bosses = season_bosses(self._boss_table, self._season)
        save_season(self._season)
        self.say(f"本期 {self._season}：{' / '.join(self._bosses) or '（表里没有这一期）'}")

    def _prep(self, img):
        """按圈选范围把范围外的像素涂黑。没圈选就原样返回（全屏识别）。"""
        rect = _range_of(self._preset, img)
        return mask_outside(img, rect) if rect else img

    # ---------------- 自动导航到联盟记录 ----------------
    def auto_enter(self):
        """联盟页 / 突袭页 → 自动点进「联盟记录」。

        不写死坐标：现场截图 → OCR 找「联盟突袭」「联盟记录」这几个字 → 点字的中心。
        窗口大小、位置、UI 缩放都不用管，因为坐标是当场从画面里读出来的。
        每一步都校验页面真的换了，换不过去立刻停，不接着乱点。
        """
        try:
            region = self._region()
        except Exception as e:
            self.say(f"先接上游戏窗口：{e}", "WARN")
            return
        if self._worker and self._worker.is_alive():
            self.say("正在跑，先等这一轮结束", "WARN")
            return
        # 点击前必须确认游戏在前台：否则点下去可能落到别的窗口上（盲点铁律）
        if self.win is None or not A.focus_window(self.win.hwnd):
            self.say("游戏窗口没能提到前台 —— 先点一下游戏画面再试", "ERR")
            return
        self._stop.clear()

        def work():
            try:
                region = self._region()        # 提前台可能挪了位置，重新取一次
            except Exception as e:
                self.say(f"取画面范围失败：{e}", "ERR")
                return
            eng = A.get_engine()
            nav = raid_nav.RaidNav(region, A.grab, A.click_at,
                                   lambda im: ro.tokens(im, eng),
                                   self.say, self._stop, esc=A.tap_key)
            try:
                ok, msg = nav.goto_record()
            except Exception:
                A.log_exc("raid-nav")
                ok, msg = False, "导航过程中出错，详见操作日志"
            self.say(("✓ " if ok else "✗ ") + msg, "INFO" if ok else "ERR")
            if ok:
                self.say("接着点【采集本屏】核对第一屏")

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    # ---------------- 字段标定 ----------------
    def pick_fields(self):
        """截一屏 → 依次画等级/BOSS/百分比三个框 → 存成比例。"""
        try:
            region = self._region()
        except Exception as e:
            self.say(f"先接上游戏窗口：{e}", "WARN")
            return None
        img = A.grab(region)
        eng = A.get_engine()
        loc = rc.locate(ro.tokens(img, eng))
        card = next((c for c in loc.cards if c.get("member_box")), None)
        if not card:
            self.say("这一屏没找到成员名，先把联盟记录列表露出来再标定", "WARN")
            return None
        view = None
        if loc.box:
            x0, y0, x1, y1 = loc.box
            pad_x, pad_y = int((x1 - x0) * 0.08) + 20, int((y1 - y0) * 0.06) + 20
            view = (x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y)
        return RaidFieldCalib(self, img, card["member_box"], self._fields,
                              self._fields_saved, view=view)

    def _fields_saved(self, fields):
        if not fields:
            return
        d = load_preset()
        d.update({"fields": fields,
                  "fields_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        if not save_preset(d):
            self.say("标定保存失败，详见操作日志", "ERR")
            return
        self._preset = load_preset()
        self._fields = fields
        self.say("字段标定已保存：" + "、".join(
            f"{k}{[round(v, 2) for v in fields[k]]}" for k in fields))

    # ---------------- 外部识别后端自检 ----------------
    def test_backend(self):
        """自检外部识别：配置在哪、程序找不找得到、真跑一张能不能出字。

        为什么值得单独做个按钮：这条链路是"可选增强"，配错了不会报错、只会静默不生效，
        光看界面根本不知道是没配、配错路径、还是程序不认识参数。点一下全说清。
        """
        info = raid_cli.probe(A.APP_DIR)
        self.say(f"配置文件：{info['path']}（{'存在' if info['exists'] else '不存在'}）")
        if not info.get("enabled"):
            self.say("外部识别未启用 —— 在配置文件里把 enabled 改成 true 并填 cmd", "WARN")
            return
        if not info.get("found"):
            self.say(f"命令找不到：{info.get('note')}", "ERR")
            return
        self.say(f"命令可用：{info.get('cmd')}")
        if not self._last:
            self.say("还没有采集过，先点【采集本屏】再自检", "WARN")
            return
        img, loc = self._last
        card = next((c for c in loc.cards if c.get("member_box")), None)
        if not card:
            self.say("这一屏没有可用卡片", "WARN")
            return
        try:
            box = ro.badge_box(ro.red_mask(img), card["member_box"])
            txt = ro._cli_text(img, box)
        except Exception:
            A.log_exc("raid-testbackend")
            txt = ""
        if txt:
            lv = rc.parse_level(txt)
            self.say(f"外部识别返回：{txt!r} → 等级 {lv if lv is not None else '解析不出'}")
        else:
            self.say("外部识别没返回内容（命令跑了但没出字，检查它的参数/输出方式）", "ERR")

    # ---------------- 圈选识别范围 ----------------
    def pick_range(self):
        """圈选识别范围。会现截一帧给用户拖框，不在旧截图上画。"""
        try:
            region = self._region()
        except Exception as e:
            self.say(f"先接上游戏窗口：{e}", "WARN")
            return None
        img = A.grab(region)
        return RaidCalib(self, img, self._range_saved)

    def _range_saved(self, rel):
        if not rel:
            self.say("圈选保存失败，详见操作日志", "ERR")
            return
        self._preset = load_preset()
        self._range_rect = None
        self._update_info()
        self.say(f"识别范围已保存：{[round(v, 3) for v in rel]}（换分辨率按比例换算）")


    # ---------------- 采集 ----------------
    def capture_one(self):
        self._launch(1)

    def start_auto(self):
        self._launch(MAX_SCREENS)

    def _launch(self, screens: int):
        if self._worker and self._worker.is_alive():
            self.say("正在跑，先等这一轮结束", "WARN")
            return
        self._stop.clear()
        self.screens = 0
        self.doubt = 0
        self._refresh_ledger()
        ui.disable(self.btn_start)
        ui.disable(self.btn_stop, False)
        self._worker = threading.Thread(target=self._run, args=(screens,), daemon=True)
        self._worker.start()

    def stop_auto(self):
        self._stop.set()
        self.say("已请求停止，等这一步做完", "WARN")

    def _run(self, screens: int):
        try:
            region = self._region()
            eng = A.get_engine()
            rows = None
            last_rows = None
            box = None
            pitch = 0.0
            last = None
            mode = "drag"       # 真机确认这个弹窗只吃拖动，滚轮/翻页键一次都不试
            miss = 0            # 拖动连续"没挪动"的次数
            back = 0            # 拼接失败后往回补采的次数

            img = self._prep(A.grab(region))
            for i in range(max(1, screens)):
                if self._stop.is_set():
                    self.say("已停止")
                    break
                self.screens = i + 1
                loc, recs = ro.read_screen(img, eng, bosses=self._bosses or None,
                                           fields=self._fields,
                                           roster=self._roster)
                if not loc.cards:
                    self.say("这一屏没认到伤害记录 —— 确认「联盟记录」已打开、列表在画面里", "ERR")
                    break
                self._q.put(("frame", (img, loc)))
                if screens == 1:
                    # 【采集本屏】一定要留证据：单屏多半就是"认不准，想看看"时点的
                    try:
                        d = write_diag(img, loc, A.OUT_DIR, "诊断_伤害_单屏")
                        self.say(f"本屏原图与裁图已存 → {d}")
                    except Exception:
                        A.log_exc("raid-diag-single")
                bad = sum(1 for r in recs if not r.get("damage"))
                if bad:
                    self._q.put(("doubt", bad))
                    self.say(f"本屏 {bad} 条伤害数字格式异常，已标 ⚠ 待人工核对", "WARN")
                no_boss = sum(1 for r in recs if not r.get("boss"))
                if no_boss:
                    self._q.put(("doubt", no_boss))
                    self.say(f"本屏 {no_boss} 条没认出 BOSS 名（已留空，不写垃圾串）", "WARN")
                    if not self._auto_diag:
                        self._auto_diag = True
                        try:
                            d = write_diag(img, loc, A.OUT_DIR, "诊断_伤害_自动")
                            self.say(f"已自动存下这一屏供排查 → {d}", "WARN")
                        except Exception:
                            A.log_exc("raid-autodiag")
                no_lv = sum(1 for r in recs if r.get("level") is None)
                if no_lv:
                    self._q.put(("doubt", no_lv))
                    self.say(f"本屏 {no_lv} 条没认到 BOSS 等级（已试过 det + 几何重认）", "WARN")
                    if not self._auto_diag:
                        self._auto_diag = True
                        try:
                            d = write_diag(img, loc, A.OUT_DIR, "诊断_伤害_自动")
                            self.say(f"已自动存下这一屏供排查 → {d}", "WARN")
                        except Exception:
                            A.log_exc("raid-autodiag")

                if rows is None:
                    rows = recs
                    last_rows = recs
                    box = loc.box
                    pitch = loc.pitch
                    self.say(f"首屏 {len(recs)} 条 · 行距 {pitch:.0f}px · "
                             f"日标记 {[d[1] for d in loc.days] or '无'}")
                    if screens > 1 and len(recs) < 2:
                        self.say("本屏只认到 1 条，量不出行距，滚采会拼错 —— "
                                 "先把列表露全一点，或只点【采集本屏】", "ERR")
                        break
                else:
                    shift, corr = (None, 0.0)
                    if last is not None and box:
                        x0, y0, x1, y1 = box
                        prev_c = last.crop((x0, y0, x1, y1))
                        cur_c = img.crop((x0, y0, x1, y1))
                        shift, corr = A.measure_vshift(prev_c, cur_c)
                    if corr < 0.3:
                        shift = None
                    added, info = rc.splice(last_rows, recs, shift, pitch)
                    if info.get("same"):
                        # 画面确实没挪动。名单采集的做法是"走 0 行先放大步长再试
                        # 一次"——真到底时放大也推不动，卡在两行中间则一步就过去。
                        miss += 1
                        why = info.get("why") or "画面没挪动"
                        if miss == 1 and box:
                            self.say(f"拖动没挪动（{why}）→ 加大步长再试一次", "WARN")
                            try:
                                A.scroll_once("drag", self._box_to_screen(box, region), 1,
                                              int(min(A.DRAG_MAX, max(A.MIN_DRAG_PX,
                                                                      pitch * SCROLL_ROWS * 2))))
                                last = img
                                img, _s, _st = A.wait_settled(region, img)
                                img = self._prep(img)
                            except Exception:
                                A.log_exc("raid-scroll-boost")
                            continue
                        self.say("列表到底了：拖动不再产生位移", "ERR")
                        break
                    if added is None:
                        # 位移算得出来、重叠区却对不上 —— 这是**拼接失败，不是到底**。
                        # 名单采集遇到这个会往回小步补采，把跨过去的那几行重新读一遍；
                        # 以前这里直接算"滚动无效"→ 换方式 → 误报到底，中间整段永久丢。
                        back += 1
                        why = info.get("why") or "重叠区对不上"
                        if back <= 1 and box:
                            self.say(f"拼接对不上（{why}）→ 往回小步补采", "WARN")
                            try:
                                A.scroll_once("drag", self._box_to_screen(box, region), 1,
                                              int(max(A.MIN_DRAG_PX, pitch * 0.9)), up=True)
                                last = img
                                img, _s, _st = A.wait_settled(region, img)
                                img = self._prep(img)
                            except Exception:
                                A.log_exc("raid-scroll-back")
                            continue
                        self.say("补采后仍对不上，收工（本段可能缺行，见日志）", "ERR")
                        break
                    else:
                        miss = 0
                        back = 0
                        # 重叠区回填：上一屏落在边缘、缺字段的那几条，用这一屏
                        # 同位置的完整读数补上（首卡被日期栏压住就是这种情况）
                        if info.get("patch"):
                            base = len(rows) - len(last_rows)
                            for _idx, _rec in info["patch"]:
                                if 0 <= base + _idx < len(rows):
                                    rows[base + _idx] = _rec
                        rows = rows + added
                        rows, _miss = rc.fill_days(rows)
                        last_rows = recs
                        adj = "（混叠已修正）" if info.get("adjusted") else ""
                        self.say(f"拼接 +{info.get('added', 0)} 条 "
                                 f"（位移 {info.get('shift_rows')} 行{adj}，"
                                 f"重叠核对 {info.get('hit')}/{info.get('overlap')}）")
                self._q.put(("rows", list(rows)))

                if screens == 1:
                    break

                # 滚一次，等停稳，再取下一帧
                try:
                    dist = int(min(A.DRAG_MAX, max(A.MIN_DRAG_PX, pitch * SCROLL_ROWS)))
                    A.scroll_once(mode, self._box_to_screen(box, region), 1, dist)
                except Exception:
                    A.log_exc("raid-scroll")
                    break
                last = img
                img, secs, settled = A.wait_settled(region, img)
                img = self._prep(img)
                if not settled:
                    self.say("画面一直没停稳，按静止帧继续", "WARN")
        except Exception as e:
            self.say(f"采集出错：{e}", "ERR")
            A.log_exc("raid-run")
        finally:
            self._q.put(("done", None))

    @staticmethod
    def _next_mode(mode: str):
        """换一种还没试过的滚动方式。

        2026-11-06 真机确认：**这个弹窗和成员列表一样不吃滚轮**，
        所以滚轮 / 翻页键只留作最后兜底（万一以后改版），正常路径只有拖动。
        """
        modes = list(getattr(A, "SCROLL_MODES", ("drag", "wheel", "key")))
        if mode in modes:
            i = modes.index(mode)
            if i + 1 < len(modes):
                return modes[i + 1]
        return None

    @staticmethod
    def _box_to_screen(box, region):
        """loc.box 是图片坐标，滚动要屏幕坐标。"""
        if not box:
            return region
        x0, y0, x1, y1 = box
        rx, ry = region[0], region[1]
        return (rx + x0, ry + y0, rx + x1, ry + y1)

    def _finish(self, _payload=None):
        ui.disable(self.btn_start, False)
        ui.disable(self.btn_stop)
        self._refresh_ledger()
        s = rc.summarize(self.records)
        self.say(f"收工：{s['count']} 条 · {len(s['members'])} 名成员 · "
                 f"总伤害 {rc.fmt_damage(s['total'])}")
        # 一期盟战每人每天 3 刀：超过说明重复采到了同一刀
        over = {m: v["count"] for m, v in s["members"].items() if v["count"] > 3}
        if over:
            self.say("⚠ 有成员记录数 >3：" + "、".join(f"{m}×{c}" for m, c in over.items())
                     + "（每人每天 3 刀，可能是重复行）", "WARN")

    # ---------------- 数据操作 ----------------
    def _del_selected(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if 0 <= idx < len(self.records):
            self.records.pop(idx)
            self._set_rows(self.records)
            self.say("已删 1 条")

    def clear(self):
        self.records = []
        self.screens = 0
        self.doubt = 0
        self._set_rows([])
        self.say("已清空")

    def save_diag(self):
        """把最近一屏的原图 + 每张卡片的框 + 各字段裁图存下来。

        为什么要这个：等级这类小字段在别人的分辨率和缩放下认不出时，光看 CSV
        分不清是"框偏了"还是"字太小"。存下原图和框，下一次就能照着图改几何，
        不用让用户反复重跑。
        """
        if not self._last:
            self.say("还没有采集过，先点【采集本屏】", "WARN")
            return None
        img, loc = self._last
        d = write_diag(img, loc, A.OUT_DIR)
        self.say(f"诊断帧已存 → {d}")
        return d

    def export_detail(self):
        if not self.records:
            self.say("还没有记录可导出", "WARN")
            return None
        p = A.OUT_DIR / f"伤害记录_{datetime.now():%Y%m%d_%H%M}.csv"
        rc.write_csv(self.records, p)
        self.say(f"已导出 {len(self.records)} 条 → {p}")
        return p

    def export_summary(self):
        if not self.records:
            self.say("还没有记录可导出", "WARN")
            return None
        p = A.OUT_DIR / f"伤害汇总_{datetime.now():%Y%m%d_%H%M}.csv"
        rc.write_summary_csv(self.records, p)
        self.say(f"已导出汇总 → {p}")
        return p
