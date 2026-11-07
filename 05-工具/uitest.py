# -*- coding: utf-8 -*-
"""界面层单测：步骤条状态机、台账数字、运行控制的按钮状态。

    .venv\\Scripts\\python.exe uitest.py

只测"看得见的那一层"，不碰 OCR 和滚动——那些在 navtest / gaptest / looptest 里。
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import alliance_ocr as T                                    # noqa: E402
import ui_kit as ui                                         # noqa: E402

fail = []


def check(name, cond, extra=""):
    print(f"{'✓' if cond else '✗'} {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        fail.append(name)


tmp = Path(tempfile.mkdtemp(prefix="uitest_"))
T.CFG = tmp / "区域.json"
T.LOG_PATH = tmp / "操作日志.txt"
T.dpi_aware()
app = T.App()
app.withdraw()
app.update()

print("=== 1. 步骤条 ===")
sb = app.steps
check("五步与 FLOW_STEPS 一致", list(sb.steps) == T.FLOW_STEPS, " / ".join(sb.steps))
check("初始全 idle", set(sb.state) == {"idle"})
sb.set(1, "done")
sb.set(2, "doing")
check("推进后前面的步骤保留", sb.state[1] == "done")
check("当前步是 doing", sb.state[2] == "doing")
check("后面的步骤被重置", sb.state[3:] == ["idle"] * 2, str(sb.state))
sb.set(4, "fail")
check("失败态可表示", sb.state[4] == "fail")
sb.reset()
check("reset 回到全 idle", set(sb.state) == {"idle"})
check("四种状态都有配色", set(ui.STEP_COLOR) == {"idle", "doing", "done", "fail"})

print("\n=== 2. 台账 ===")
app.names = {f"人{i}": (0.9, "12:00:00") for i in range(7)}
app._jumps, app._recover_walks = 0, 0
app._refresh_ledger()
check("没总数时待采写未知", "未知" in app.ledger.vars["待采"].get(),
      app.ledger.vars["待采"].get())
app._refresh_ledger(32)
check("有总数时算出还差多少人", app.ledger.vars["待采"].get() == "25 人（共 32）",
      app.ledger.vars["待采"].get())
check("已识别等于名单长度", app.ledger.vars["已识别"].get() == "7 人",
      app.ledger.vars["已识别"].get())
check("没跳过时显示 0", app.ledger.vars["已跳过"].get() == "0",
      app.ledger.vars["已跳过"].get())
app._jumps, app._recover_walks = 1, 2
app._refresh_ledger()
check("跳过数 = 跳步 + 补采次数", app.ledger.vars["已跳过"].get() == "3 次",
      app.ledger.vars["已跳过"].get())

print("\n=== 3. 任务提示 ===")
app._task("正在滚采", pct=66, step=3, state="doing")
check("进度条数值跟着走", abs(app.task.var.get() - 66) < 0.01, str(app.task.var.get()))
check("任务提示文案同步", app.task.txt.cget("text") == "正在滚采")
check("say 与任务提示是同一行", app.status is app.task.txt)
check("滚采步被点亮", app.steps.state[3] == "doing", str(app.steps.state))

print("\n=== 4. 运行控制 ===")
app._continuous = False
app._paused = False
app._sync_run_buttons()
check("空闲：开始可点、其余置灰",
      app.btn_start.cget("state") == "normal"
      and all(b.cget("state") == "disabled"
              for b in (app.btn_pause, app.btn_resume, app.btn_stop)))
app._continuous, app._paused = True, False
app._sync_run_buttons()
check("运行中：开始与继续置灰、暂停与取消可点",
      app.btn_start.cget("state") == "disabled"
      and app.btn_pause.cget("state") == "normal"
      and app.btn_stop.cget("state") == "normal"
      and app.btn_resume.cget("state") == "disabled")
app._paused = True
app._sync_run_buttons()
check("暂停后：暂停置灰、继续可点",
      app.btn_pause.cget("state") == "disabled"
      and app.btn_resume.cget("state") == "normal")
app._paused = False

print("\n=== 5. 日志区 ===")
app.logpane.text.configure(state="normal")
app.logpane.text.delete("1.0", "end")
app.logpane.text.configure(state="disabled")
app.logpane._lines = 0
for i in range(420):
    app.logpane.append(f"第 {i} 行", "WARN" if i % 50 == 0 else "INFO")
app.update()
kept = int(app.logpane.text.index("end-1c").split(".")[0])
check("超过 400 行会裁剪", kept <= 400, f"现在 {kept} 行")
check("裁剪后仍保留最新一行", "第 419 行" in app.logpane.text.get("1.0", "end"))
check("WARN 行有独立颜色", app.logpane.text.tag_cget("W", "foreground") == "#e0a03a")

print("\n=== 6. 窗口形制 ===")
check("竖版窗口高大于宽", app.winfo_height() > app.winfo_width(),
      f"{app.winfo_width()}x{app.winfo_height()}")
check("最小尺寸是竖版比例", app.minsize()[1] > app.minsize()[0],
      f"{app.minsize()}")

print("\n=== 7. 日志跟随最新 ===")
# 采集时"边跑边看现场"全靠这一块：新行进来要停在最新，翻历史时不能被拽回去。
# 视图行为要真实几何才算得准，所以这里把窗口放出来。
app.deiconify()
app.update()
lp = app.logpane


def wipe():
    lp.text.configure(state="normal")
    lp.text.delete("1.0", "end")
    lp.text.configure(state="disabled")
    lp._lines = 0
    lp.to_bottom()


wipe()
for i in range(60):
    lp.append(f"行 {i}")
app.update()
check("写满后停在最新一行", lp.text.yview()[1] >= 0.999, f"yview={lp.text.yview()}")
check("末行有高亮标记", bool(lp.text.tag_ranges("LAST")))
check("跟随时标题不带提醒", lp._head.cget("text") == "▾ 运行日志",
      lp._head.cget("text"))

lp.text.event_generate("<MouseWheel>", delta=120)   # 真·滚轮往上一格
app.update()
check("滚轮往上翻就暂停跟随", lp._follow is False)
top_before = lp.text.index("@0,0")            # 按"首行行号"比：占比会随总行数变，行号不会
lp.append("新来的一条")
app.update()
check("暂停时不把用户拽回底部", lp.text.index("@0,0") == top_before,
      f"首行 {top_before} → {lp.text.index('@0,0')}")
check("新行仍落在视图下面", "新来的一条" not in lp.text.get("@0,0", "@0,1000"))
check("角标报出攒了几条", "条新" in lp._chip.cget("text"), lp._chip.cget("text"))
lp.set_rows(8)
app.update()
check("改高度不会被当成在看历史", lp._follow is False and lp.rows == 8,
      f"follow={lp._follow} rows={lp.rows} yview={lp.text.yview()}")
lp.to_bottom()
app.update()
check("回到最新并恢复跟随", lp.text.yview()[1] >= 0.999 and lp._follow)
check("恢复后角标清空", lp._chip.cget("text") == "")

lp.toggle()
for i in range(10):
    lp.append(f"折叠 {i}")
lp.toggle()
app.update()
check("展开后直接看最新", lp.text.yview()[1] >= 0.999
      and "折叠 9" in lp.text.get("1.0", "end"))

lp.set_rows(1)
check("高度不低于下限", lp.rows == lp.MIN_ROWS, str(lp.rows))
lp.set_rows(99, user=True)
check("高度不超过上限", lp.rows == lp.MAX_ROWS, str(lp.rows))
check("拖过高度就记为用户设定", lp.user_sized is True)
check("Text 实际高度同步", int(lp.text.cget("height")) == lp.MAX_ROWS)

print("\n=== 8. 采集线程写日志不丢行 ===")
wipe()
threading.Thread(target=lambda: [lp.push(f"线程 {i}") for i in range(200)],
                 daemon=True).start()
lp.push("先塞一条")                       # 只进队列，不碰 Tk
check("push 只入队、当场不写视图", "先塞一条" not in lp.text.get("1.0", "end")
      and len(lp._q) >= 1, f"队列 {len(lp._q)}")
time.sleep(0.25)                          # 让采集线程跑完
app.update()                              # 主线程按 80ms 节拍抽取
deadline = time.time() + 2.0              # 最后一条可能正好落在节拍之后，轮询到抽干为止
while lp._q and time.time() < deadline:
    time.sleep(0.02)
    app.update()
body = lp.text.get("1.0", "end")
miss = [i for i in range(200) if f"线程 {i}" not in body]
check("子线程的 200 条一条不丢", not miss, f"缺 {len(miss)} 条")
check("队列抽干", len(lp._q) == 0, f"剩 {len(lp._q)}")
check("抽取后仍停在最新", lp.text.yview()[1] >= 0.999, f"yview={lp.text.yview()}")

print("\n=== 9. 采集时撑开日志区 ===")
saved = app.logpane
probe = ui.LogPane(app, height=T.LOG_ROWS_IDLE)
probe.pack()
app.logpane = probe
probe.toggle()                            # 折叠着就等于没有日志
app._log_mode(True)
check("开跑会展开并撑高", probe.is_open and probe.rows == T.LOG_ROWS_RUN,
      f"open={probe.is_open} rows={probe.rows}")
app._log_mode(False)
check("收工收回空闲高度", probe.rows == T.LOG_ROWS_IDLE, str(probe.rows))
probe.set_rows(12, user=True)
app._log_mode(True)
check("用户拖过的高度不再被自动改", probe.rows == 12, str(probe.rows))
app.logpane = saved
probe.destroy()

app.destroy()
print("\n=== 结果 ===")
print("全部通过" if not fail else f"失败项：{fail}")
raise SystemExit(0 if not fail else 1)
