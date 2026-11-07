"""实测滚采：自动跑 N 轮「导航 → 拉回顶部 → 滚采」，对比每轮人数是否一致。

为什么要它
----------
步长和判定的问题只在真实客户端上暴露：日志能事后追，但改完是否真的不再跳步，
只有跑一遍才知道。这个脚本把一轮完整流程串成可重复的动作，跑完直接告诉你
「三轮分别是多少人」——人数一致 = 没有跳步漏采；不一致 = 哪一轮缺了谁。

会真的操作游戏窗口：自动点「联盟」「队员」、自动拖动列表。跑的时候不要动鼠标。

用法
----
    .venv\\Scripts\\python 实测滚采.py [轮数]      # 默认 3 轮
"""

import json
import sys
import time
import tkinter as tk

import alliance_ocr as T

ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
NAV_TIMEOUT = 90       # 导航最多等这么久，超时跳过本轮
AUTO_TIMEOUT = 320     # 单轮滚采最多等这么久（含导航与开跑前场景确认）

# 全自动跑，不能让人在中间点确认框
T.messagebox.askyesno = lambda *a, **k: True
# 任何模态框都会把无人值守的实测卡死，一律自动确认
T.messagebox.showwarning = lambda *a, **k: None
T.messagebox.showerror = lambda *a, **k: None
T.messagebox.showinfo = lambda *a, **k: None
T.messagebox.showwarning = lambda *a, **k: None
T.messagebox.showerror = lambda *a, **k: None

results = []
st = {"phase": "boot", "t0": time.time(), "round": 0}

# App 本身就是 tk.Tk 的子类，并且主程序启动时那四步（编码流/DPI/崩溃钩子/日志头）
# 必须照做——少了 dpi_aware 会把 1220×686 的画面按 125% 缩放截成 1525×857，
# 坐标全错；少了 log_start 则这次实测的日志没头，事后没法对时间。
T._fix_streams()
T.dpi_aware()
T.install_crash_hooks()
T.log_start()
app = T.App()
root = app


def say(msg):
    print(f"[实测] {msg}", flush=True)


_orig_stop = app._stop_auto


def stop_hook(msg, complete=False):
    _orig_stop(msg, complete)
    if st["phase"] != "auto":
        # 不是"开采中"被停的（例如开跑前场景校验没过）：这一轮什么都没采到，
        # 但不能就这么把流程挂住——否则后面的轮次永远等不到触发点，看起来像"超时"。
        if st["phase"] != "idle":
            st["phase"] = "idle"
            root.after(1500, next_round)
        return
    st["phase"] = "idle"
    names = list(app.names)
    results.append({"round": st["round"], "n": len(names), "complete": complete,
                    "msg": msg, "names": names})
    say(f"第 {st['round']} 轮结束 · {len(names)} 人 · complete={complete}")
    say("  " + msg.replace("\n", " ")[:140])
    root.after(1500, next_round)


app._stop_auto = stop_hook


def next_round():
    if st["round"] >= ROUNDS:
        finish()
        return
    st["round"] += 1
    say(f"=== 第 {st['round']}/{ROUNDS} 轮 ===")
    # 轮与轮之间必须把运行态清干净：上一轮可能是被"开跑前校验"或超时打断的，
    # 残留的 _continuous=True 会让下一次 toggle_auto 直接走"停止"分支，
    # 表现为"这一轮 0 次滚动、0 个人"，而且日志上看不出原因。
    app._continuous = False
    app._paused = False
    app.clear()
    root.update()
    # 上一轮结束时游戏可能已经被别的窗口挤到后台，先把前台抢回来，
    # 否则导航第一屏截到的就是别的窗口，页指纹全错
    if app.win is not None:
        T.focus_window(app.win.hwnd)
    st["phase"] = "nav"
    st["t0"] = time.time()
    app.nav_to_members()
    root.after(600, poll_nav)


def poll_nav():
    if st["phase"] != "nav":
        return
    if time.time() - st["t0"] > NAV_TIMEOUT:
        say("⚠ 导航超时，跳过本轮")
        st["phase"] = "idle"
        root.after(800, next_round)
        return
    if not app._nav_running:
        st["phase"] = "auto"
        st["t0"] = time.time()
        st["t_auto"] = time.time()
        say("导航完成，1.2s 后开始滚采")
        root.after(1200, lambda: (app.toggle_auto(), root.after(800, poll_auto)))
        return
    root.after(400, poll_nav)


def poll_auto():
    if st["phase"] != "auto":
        return
    if time.time() - st.get("t_auto", st["t0"]) > AUTO_TIMEOUT:
        say("⚠ 滚采超时，强制停")
        st["phase"] = "idle"          # 先改状态，避免 stop_hook 再记一条重复结果
        try:
            app._continuous = False
            app._stop_auto("实测超时，强制停止")
        except Exception as exc:
            say(f"强制停也失败了：{exc}")
        root.after(1200, next_round)
        return
    root.after(500, poll_auto)


def finish():
    print()
    print("=" * 64)
    print("实测结果")
    print("=" * 64)
    for r in results:
        flag = "完整" if r["complete"] else "不完整/中止"
        print(f"  第 {r['round']} 轮 · {r['n']:>3} 人 · {flag}")
    counts = [r["n"] for r in results]
    if counts:
        same = len(set(counts)) == 1
        print(f"\n  人数 {counts} · {'✅ 每轮一致' if same else '❌ 轮次之间不一致'}")
        if not same:
            base = max(counts)
            ref = next(r for r in results if r["n"] == base)
            ref_names = set(ref["names"])
            for r in results:
                miss = ref_names - set(r["names"])
                if miss:
                    print(f"  第 {r['round']} 轮少了 {len(miss)} 人: {sorted(miss)}")
    out = T.OUT_DIR / f"实测_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  明细已存：{out}")
    root.after(400, root.destroy)


# 模型加载要几秒，给够时间再开第一轮
root.after(9000, next_round)
root.mainloop()
