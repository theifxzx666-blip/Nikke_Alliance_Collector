# -*- coding: utf-8 -*-
"""GUI 交互冒烟：验证框选层能正常创建/取消、主窗口能恢复。

不依赖人工点击，跑完自动退出。
    .venv\\Scripts\\python.exe guitest.py
"""
import importlib.util
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

tool.dpi_aware()
app = tool.App()
app.update()

fail = []

# 1) 框选层：构造 -> 取消（等价于用户按 Esc）
try:
    picker = tool.RegionPicker(app)
    app.update()
    assert picker.winfo_exists(), "picker 未创建"
    assert picker.shot.width > 0, "截图失败"
    print(f"[1] 框选层创建 OK · 截图 {picker.shot.width}x{picker.shot.height}")
    picker.destroy()
    app.update()
    print("[2] 取消框选 OK · result =", picker.result)
except Exception:
    fail.append("框选层")
    traceback.print_exc()

# 2) 主窗口恢复（pick_region 先隐藏自己，结束必须能回来）
try:
    app.withdraw()
    app.update()
    assert app.state() == "withdrawn"
    app.deiconify()
    app.update()
    assert app.state() == "normal", app.state()
    print("[3] 隐藏/恢复主窗口 OK")
except Exception:
    fail.append("主窗口恢复")
    traceback.print_exc()

# 3) 预览绘制：给一张真样本，画红框
try:
    from PIL import Image
    img = Image.open(HERE.parent / "04-复用方案" / "verify" / "alliance" / "alliance_list_5rows.png").convert("RGB")
    bands = tool.find_bands(img.crop((110, 95, 310, 447)))
    app._draw_preview(img, bands)
    app.update()
    print(f"[4] 预览绘制 OK · 候选带 {len(bands)}")
except Exception:
    fail.append("预览绘制")
    traceback.print_exc()

app.destroy()
print("\n=== 结果 ===")
print("全部通过" if not fail else f"失败项：{fail}")
sys.exit(1 if fail else 0)
