# -*- coding: utf-8 -*-
"""验证框选体检：拿真实截图当"用户框出来的区域"，看能不能认出行数并报出问题。

    .venv\\Scripts\\python.exe regiontest.py
"""
import importlib.util
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

CASES = [
    ("用户真实区域（应 5 行、无异常）",
     HERE / "输出" / "_diag" / "最近一屏.png", 5, False),
    ("样本昵称列（只有 2 行可认，应提示）",
     HERE.parent / "04-复用方案" / "verify" / "alliance" / "alliance_list_5rows.png", 2, True),
    ("横条图（应提示框是横的）",
     HERE.parent / "04-复用方案" / "verify" / "alliance" / "rows_annotated.png", None, True),
]

ok_all = True
for name, path, want_rows, want_notes in CASES:
    if not path.exists():
        print(f"跳过（文件不存在）：{name} · {path}")
        continue
    im = Image.open(path).convert("RGB")
    rows, notes = tool.check_region((0, 0, im.width, im.height), im)
    good = (want_rows is None or rows == want_rows) and (bool(notes) == want_notes)
    ok_all = ok_all and good
    print(f"{'✓' if good else '✗'} {name}")
    print(f"    {im.size} → 认出 {rows} 行 · 提示 {notes or '无'}")

print("\n=== 结果 ===")
print("通过" if ok_all else "未通过")
