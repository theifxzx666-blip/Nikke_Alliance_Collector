import importlib.util
import os
from pathlib import Path
spec = importlib.util.spec_from_file_location("tool", "alliance_ocr.py")
t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
t.dpi_aware()
t.CFG = t.OUT_DIR / "_test_区域.json"
t.PRESET = t.OUT_DIR / "_test_预设.json"

app = t.App()
app.geometry("1040x680+0+0")
state = {}

def step1():
    try:
        dlg = t.WindowPicker(app)
        app.update()
        rows = dlg.tree.get_children()
        state["rows"] = len(rows)
        state["sel"] = dlg.tree.selection()
        state["hint"] = dlg.hint.cget("text")
        state["first"] = dlg.tree.item(rows[0], "values") if rows else None
        print(f"列出 {len(rows)} 个窗口")
        for iid in rows[:5]:
            v = dlg.tree.item(iid, "values")
            print(f"   {v[0][:40]:42} {v[1]:>10}  {v[2]}")
        print(f"默认选中: {state['sel']}")
        print(f"提示语: {state['hint']}")
        assert len(rows) >= 1, "窗口列表为空"
        # 判据是"本进程自己的窗口"，不是"标题像本工具"——用户可能同时开着
        # 另一个工具实例，那是正常可采集的普通窗口，不该算漏排除。
        own = [dlg.tree.item(i, "values") for i in rows
               if t.pid_of_window(int(i)) == os.getpid()]
        print(f"含本进程窗口: {own if own else '无（已正确排除）'}")
        assert not own, "本进程自己的窗口不该出现在列表里"
        assert state["sel"], "没有默认选中"
        # 双击选中项 = 确定
        dlg._ok()
        app.update()
        state["result"] = dlg.result
        print(f"锁定结果: {state['result']}")
    except Exception as exc:
        state["err"] = f"{type(exc).__name__}: {exc}"
        print(f"失败：{state['err']}")
    finally:
        app.quit()

app.after(1200, step1)
app.mainloop()
print("\n=== 结果 ===")
ok = state.get("rows", 0) >= 1 and state.get("result") is not None and "err" not in state
print("通过" if ok else "未通过")
app.destroy()
