# -*- coding: utf-8 -*-
"""成员变动单测：轮次留档、退会判定、改名提示、变动视图。

    .venv\\Scripts\\python.exe churntest.py

只碰昵称库那一层（`_commit_roster` / `roster_history` / `churn_headline`）
和变动视图，不跑 OCR、不碰游戏窗口。
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import alliance_ocr as T                                    # noqa: E402

fail = []


def check(name, cond, extra=""):
    print(f"{'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        fail.append(name)


tmp = Path(tempfile.mkdtemp(prefix="churntest_"))
T.CFG = tmp / "区域.json"
T.LOG_PATH = tmp / "操作日志.txt"
T.ROSTER = tmp / "昵称库.json"        # 必须重定向，否则会写坏真库
T.dpi_aware()
app = T.App()
app.withdraw()
app.update()


def reset(roster=None, names=None, prev=None):
    """把 App 摆到"刚采完一轮"的现场。"""
    app._roster = roster if roster is not None else {
        "version": 1, "rounds": 5,
        "names": {n: {"first": "2026-10-01 10:00", "last": "2026-10-01 10:00",
                      "rounds": 5, "miss": 0, "score": 1.0, "alias": []}
                  for n in (names or [])},
    }
    app.names = {n: (1.0, "10:00:00") for n in (names or [])}
    app._roster_prev = json.loads(json.dumps(app._roster.get("names") or {})) \
        if prev is None else prev
    app._fix_cnt = {}
    app._total_hint = len(names or [])


print("=== 1. 一轮完整采集会留档 ===")
reset(names=["甲", "乙", "丙", "丁"])
r = app._commit_roster(True)
hist = app._roster.get("history")
check("history 是列表", isinstance(hist, list), type(hist).__name__)
check("写了一条", len(hist) == 1, str(hist))
e = hist[0] if hist else {}
check("记下人数", e.get("n") == 4, str(e.get("n")))
check("记下页面人数", e.get("total") == 4, str(e.get("total")))
check("标记为采完", e.get("ok") is True, str(e.get("ok")))
check("有时间戳", bool(e.get("t")), str(e.get("t")))
check("四个人都在库里", len(app._roster["names"]) == 4)
check("没有新增（都是老面孔）", e.get("add") == [], str(e.get("add")))
check("没有未见", e.get("gone") == [], str(e.get("gone")))
check("摘要为空（没变化不报）", r == "", r)

print("\n=== 2. 首轮建库不把整张名单当新人 ===")
reset(roster={"version": 1, "rounds": 0, "names": {}}, names=["甲", "乙", "丙"], prev={})
app._commit_roster(True)
e = (app._roster.get("history") or [{}])[-1]
check("首轮 add 为空", e.get("add") == [], str(e.get("add")))
check("首轮库里三人", len(app._roster["names"]) == 3)

print("\n=== 3. 有人进来 ===")
reset(names=["甲", "乙", "丙"], prev={n: {} for n in ["甲", "乙", "丙"]})
# 库里三人，本轮多一个「新来的」
app._roster["names"] = {n: {"first": "x", "last": "x", "rounds": 3, "miss": 0,
                            "score": 1.0, "alias": []} for n in ["甲", "乙", "丙"]}
app._roster_prev = json.loads(json.dumps(app._roster["names"]))
app.names = {n: (1.0, "10:00:00") for n in ["甲", "乙", "丙", "新来的"]}
app._total_hint = 4
r = app._commit_roster(True)
e = (app._roster.get("history") or [{}])[-1]
check("新人被记入 add", e.get("add") == ["新来的"], str(e.get("add")))
check("摘要里点名新人", "新增 1 人" in r and "新来的" in r, r)

print("\n=== 4. 没采完的轮次不判退会 ===")
reset(names=["甲", "乙", "丙"], prev={n: {"rounds": 3, "miss": 0} for n in ["甲", "乙", "丙"]})
app._roster["names"] = {n: {"first": "x", "last": "x", "rounds": 3, "miss": 0,
                            "score": 1.0, "alias": []} for n in ["甲", "乙", "丙", "丁"]}
app._roster_prev = json.loads(json.dumps(app._roster["names"]))
app.names = {n: (1.0, "10:00:00") for n in ["甲", "乙", "丙"]}      # 丁没采到
app._total_hint = 4
app._commit_roster(False)
e = (app._roster.get("history") or [{}])[-1]
check("标记未采完", e.get("ok") is False, str(e.get("ok")))
check("未采完不记未见", e.get("gone") == [], str(e.get("gone")))
check("丁的 miss 没涨", int(app._roster["names"]["丁"]["miss"]) == 0,
      str(app._roster["names"]["丁"]["miss"]))

print("\n=== 5. 连续两轮没见到才移出 ===")
base = {n: {"first": "x", "last": "x", "rounds": 5, "miss": 0,
            "score": 1.0, "alias": []} for n in ["甲", "乙", "丙", "丁"]}
app._roster = {"version": 1, "rounds": 5, "names": json.loads(json.dumps(base))}
app._roster_prev = json.loads(json.dumps(base))
app.names = {n: (1.0, "10:00:00") for n in ["甲", "乙", "丙"]}
app._total_hint = 3
r1 = app._commit_roster(True)
e1 = (app._roster.get("history") or [{}])[-1]
check("第一轮：记未见", e1.get("gone") == ["丁"], str(e1.get("gone")))
check("第一轮：人还在库里", "丁" in app._roster["names"])
check("第一轮：miss=1", app._roster["names"]["丁"]["miss"] == 1)
app._roster_prev = json.loads(json.dumps(app._roster["names"]))
app.names = {n: (1.0, "10:00:00") for n in ["甲", "乙", "丙"]}
r2 = app._commit_roster(True)
e2 = (app._roster.get("history") or [{}])[-1]
check("第二轮：记移出", e2.get("drop") == ["丁"], str(e2.get("drop")))
check("第二轮：人已离开库", "丁" not in app._roster["names"])
check("第二轮：不再算未见", e2.get("gone") == [], str(e2.get("gone")))
check("移出写进了摘要", "移出 1 人" in r2, r2)
check("记录了两轮", len(app._roster["history"]) == 2)

print("\n=== 6. 轮次记录有上限，留最新的 ===")
app._roster["history"] = [{"t": f"t{i}", "n": 3, "ok": True, "add": [], "gone": [],
                           "drop": [], "alias": 0} for i in range(T.ROSTER_HISTORY_MAX + 6)]
app._roster_prev = json.loads(json.dumps(app._roster["names"]))
app.names = {n: (1.0, "10:00:00") for n in ["甲", "乙", "丙"]}
app._total_hint = 3
app._commit_roster(True)
h = app._roster["history"]
check("裁到上限", len(h) == T.ROSTER_HISTORY_MAX, str(len(h)))
check("最新那条在末尾", h[-1].get("ok") is True and h[-1].get("t") != "t0", str(h[-1].get("t")))
check("不会越裁越少", len(h) >= T.ROSTER_HISTORY_MAX - 1)

print("\n=== 7. 脏数据不炸 ===")
check("history 不是列表 → 空", T.roster_history({"history": "坏"}) == [])
check("元素是字符串 → 跳过", T.roster_history({"history": ["坏", {"n": 3}]})[0]["n"] == 3)
check("字段缺失 → 有默认值", T.roster_history({"history": [{}]})[0]["add"] == [])
check("names 不是字典 → 当作空",
      T.churn_headline({"names": "坏"})["n"] == 0)
check("空库 → 没有最近一轮", T.churn_headline({})["last"] is None)

print("\n=== 8. 视图输入：顺序与判定 ===")
ros = {"rounds": 9, "names": {},
       "history": [
           {"t": "10-01", "n": 30, "ok": True, "add": ["A"], "gone": [], "drop": [], "alias": 0},
           {"t": "10-02", "n": 12, "ok": False, "add": ["B"], "gone": [], "drop": [], "alias": 0},
           {"t": "10-03", "n": 31, "ok": True, "add": ["C"], "gone": ["D"], "drop": [], "alias": 2},
       ]}
hd = T.churn_headline(ros)
check("最新在前", hd["rows"][0]["t"] == "10-03", hd["rows"][0]["t"])
check("last 是最新一条", hd["last"]["t"] == "10-03")
check("last_ok 跳过没采完的", hd["last_ok"]["t"] == "10-03")
check("add 来自完整轮", hd["add"] == ["C"], str(hd["add"]))
check("一进一出 → 提示改名", hd["rename_hint"] is True)
ros["history"][-1]["add"] = ["C", "E"]
check("两进一出 → 不提示改名", T.churn_headline(ros)["rename_hint"] is False)
ros["history"][-1]["drop"] = ["F"]
check("有移出 → 不提示改名", T.churn_headline(ros)["rename_hint"] is False)
ros2 = {"names": {"甲": {"miss": 0}, "乙": {"miss": 2}, "丙": {"miss": 1}},
        "history": [{"t": "10-04", "n": 3, "ok": False}]}
check("last_ok 全是没采完 → None", T.churn_headline(ros2)["last_ok"] is None)
w = T.churn_headline(ros2)["watch"]
check("待观察只挑 miss>=1", [x[0] for x in w] == ["乙", "丙"], str(w))
check("待观察按 miss 降序", w[0][1] == 2)

print("\n=== 9. 变动视图 ===")
app._roster = {"rounds": 9, "names": {"甲": {"rounds": 3, "miss": 0},
                                      "乙": {"rounds": 3, "miss": 1}},
               "history": [
                   {"t": "10-01", "n": 30, "ok": True, "add": ["A"], "gone": [],
                    "drop": [], "alias": 0},
                   {"t": "10-02", "n": 12, "ok": False, "add": ["B"], "gone": [],
                    "drop": [], "alias": 0},
                   {"t": "10-03", "n": 31, "ok": True, "add": ["C"], "gone": ["D"],
                    "drop": [], "alias": 2},
               ]}
tk = app.churn_dialog()
tops = [w for w in app.winfo_children() if isinstance(w, __import__("tkinter").Toplevel)]
win = tops[-1]
win.update()
from tkinter import ttk                                  # noqa: E402


def trees(w):
    out = []
    for c in w.winfo_children():
        if isinstance(c, ttk.Treeview):
            out.append(c)
        out += trees(c)
    return out


tv = trees(win)
check("有轮次表格", len(tv) == 1, str(len(tv)))
rows = tv[0].get_children() if tv else ()
check("行数 = 记录数", len(rows) == 3, str(len(rows)))
vals = tv[0].item(rows[0], "values") if rows else ()
check("第一行是最新一轮", vals and vals[0] == "10-03", str(vals))
check("未采完那行被压暗", "bad" in tv[0].item(rows[1], "tags"),
      str(tv[0].item(rows[1], "tags")))
check("有无变动列", vals and vals[-1] in ("有变动", "无变动", "未采完"), str(vals))
tv[0].selection_set(rows[1])
win.update()
det = [c for c in win.winfo_children() if c.winfo_class() == "Text"]
check("有详情区", len(det) == 1, str(len(det)))
body = det[0].get("1.0", "end") if det else ""
check("详情跟着选中行走", "10-02" in body, body.strip()[:40])
check("未采完在详情里说明", "不参与退会判定" in body, body.strip()[:60])
win.destroy()

app._roster = {"version": 1, "rounds": 0, "names": {}}
app.churn_dialog()
win = [w for w in app.winfo_children()
       if isinstance(w, __import__("tkinter").Toplevel)][-1]
win.update()
tv = trees(win)
check("空库也能打开", len(tv) == 1)
det = [c for c in win.winfo_children() if c.winfo_class() == "Text"]
check("空库给的是提示不是空白",
      "还没有轮次记录" in det[0].get("1.0", "end"), det[0].get("1.0", "end").strip()[:20])
win.destroy()

print("\n=== 10. 落盘 ===")
app._roster = {"version": 1, "rounds": 3, "names": {"甲": {"rounds": 1, "miss": 0}},
               "history": [{"t": "10-01", "n": 1, "ok": True}]}
app._roster_prev = json.loads(json.dumps(app._roster["names"]))
app.names = {"甲": (1.0, "10:00:00")}
app._total_hint = 1
app._commit_roster(True)
disk = json.loads(T.ROSTER.read_text(encoding="utf-8"))
check("history 写进了文件", isinstance(disk.get("history"), list) and len(disk["history"]) >= 2,
      str(len(disk.get("history") or [])))
check("written 时间戳在", bool(disk.get("updated")), str(disk.get("updated")))

app.destroy()

print("\n=== 结果 ===")
if fail:
    print(f"{len(fail)} 项未通过：")
    for f in fail:
        print("  ·", f)
    sys.exit(1)
print("全部通过")
