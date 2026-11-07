"""生成程序图标：深色圆角底 + 三行成员列表，中间那行用强调色高亮。

图形选这个是因为它同时说清了两件事——**这是一份名单**，以及**正在逐行读它**。
不用文字，因为 16×16 的任务栏图标上任何字都糊成一团。

    .venv\\Scripts\\python.exe 做图标.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

S = 256
OUT = Path(__file__).resolve().parent / "图标.ico"

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角底：和界面同色，任务栏里一眼能认出来是同一个东西
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=56, fill="#1e1f22")

# 三行：左边圆点（头像位）+ 右边长条（昵称位）
# 中间那行用强调色 —— 就是界面里"当前正在读的这一行"
for color, y in (("#4a4d52", 84), ("#4c8dff", 128), ("#4a4d52", 172)):
    d.ellipse([50, y - 11, 72, y + 11], fill=color)
    d.rounded_rectangle([84, y - 11, 206, y + 11], radius=11, fill=color)

img.save(OUT, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("已生成", OUT, OUT.stat().st_size, "字节")
