# -*- coding: utf-8 -*-
"""行切分（find_bands / scan）的回归测试。

    .venv\\Scripts\\python.exe bandtest.py

盯的是一个具体事故：诊断包 20261027_174100 和 _180307 两次采集都少了「马三一」
这个人。原因不在 OCR 也不在滚动，而在**行切分**——

「马三一」三个字全由横笔组成，纵向投影在两个横笔之间会掉到阈值以下，整行被切成
207-208 / 211-212 / 214-217 三块不到 6px 的碎片，再被 min_height 全部丢掉，于是
这一行在识别结果里彻底不存在：本屏本应 4 行只回来 3 行，两屏零重合，采集就被判成
"一步跨了整屏"而叫停。

所以这里锁三件事：
  1. 横笔密集的行不能被切碎（真实样本，来自上面两个诊断包）
  2. 合并碎片不能把相邻两行连成一条（合成图，行间空白 18px）
  3. 真实那一屏必须认出全部 4 / 5 行，且「马三一」完整（不是「马三」）
"""
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ASSETS = HERE / "测试素材"
ok_all = True
n_pass = n_all = 0


def check(title, good, detail=""):
    global ok_all, n_pass, n_all
    n_all += 1
    n_pass += 1 if good else 0
    ok_all = ok_all and good
    print(f"{'✓' if good else '✗'} {title}" + (f"  {detail}" if detail else ""))


def bands_of(path):
    img = Image.open(path).convert("RGB")
    return img, tool.find_bands(img)


# ---------------------------------------------------------------- 1. 真实样本
print("=== 1. 横笔密集的行不能被切碎（真实样本）===")

SAMPLES = [
    ("屏_漏马三一_174100.png", 4),
    ("屏_漏马三一_180307.png", 5),
]
for name, want_rows in SAMPLES:
    p = ASSETS / name
    if not p.exists():
        check(f"{name} 样本存在", False, "缺 测试素材/" + name)
        continue

    img, bands = bands_of(p)
    found, bands2, passed = tool.scan(img)
    names = [n for _y, n, _s in found]

    # 「马三一」那一带必须存在，且高度覆盖整个字（约 11px，不能只剩「马三」的 10px 下限）
    hit = [b for b in bands if b[0] <= 212 <= b[1] or b[0] <= 230 <= b[1]]
    check(f"{name} 认全 {want_rows} 行", len(found) == want_rows,
          f"识别 {len(found)} 行 {names}")
    check(f"{name} 「马三一」在名单里", "马三一" in names, f"{names}")
    check(f"{name} 「马三」不算数（说明右边界被切掉了）", "马三" not in names)
    check(f"{name} 马三一那一带高度 >= 10px", bool(hit) and (hit[0][1] - hit[0][0] + 1) >= 10,
          f"{hit}")


# ---------------------------------------------------------------- 2. 合成图
print("\n=== 2. 合并碎片不能跨行（合成图）===")


def synth(rows, pitch=71, h=14, margin=30, w=101):
    """画一张白底深灰文字条的图，用来控制行距和字高。"""
    im = Image.new("RGB", (w, margin * 2 + pitch * (rows - 1) + h), (246, 246, 246))
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("msyh.ttc", h)
    except Exception:
        font = ImageFont.load_default()
    ys = []
    for i in range(rows):
        y = margin + i * pitch
        d.text((6, y), "测试昵称", fill=(78, 78, 78), font=font)
        ys.append(y)
    return im, ys


# 行间空白 = 71 - 14 = 57px，远大于 merge_gap(5)，必须保持 5 条独立带
im, ys = synth(5, pitch=71, h=14)
bands = tool.find_bands(im)
check("5 行等距文字 → 5 条带，没有被粘成一条", len(bands) == 5, f"{bands}")

# 行间空白压到 18px（真实画面里玩家横幅到下一行昵称就是 18px），仍不能粘
im2, _ = synth(4, pitch=14 + 18, h=14)
bands2 = tool.find_bands(im2)
check("行间空白 18px → 仍是 4 条带", len(bands2) == 4, f"{bands2}")

# 横笔行（只有横线的字）本身投影很弱，但整行必须还在
im3, _ = synth(4, pitch=71, h=14)
d = ImageDraw.Draw(im3)
for i in range(4):
    y = 30 + i * 71
    d.rectangle([6, y + 3, 40, y + 4], fill=(78, 78, 78))   # 一
    d.rectangle([6, y + 8, 40, y + 9], fill=(78, 78, 78))   # 二
bands3 = tool.find_bands(im3)
check("只有横笔的行也能成带（不被 min_height 丢掉）", len(bands3) == 4, f"{bands3}")

# 碎片合并：把一行横笔打散成 2px 一段，间隙 1~2px
im4 = Image.new("RGB", (101, 200), (246, 246, 246))
d4 = ImageDraw.Draw(im4)
for k in range(4):
    yy = 40 + k * 3
    d4.rectangle([10, yy, 70, yy + 1], fill=(78, 78, 78))
bands4 = tool.find_bands(im4)
check("一行被切成 4 段横笔（间隙 1px）→ 合并成 1 条带",
      len(bands4) == 1 and (bands4[0][1] - bands4[0][0] + 1) >= 10, f"{bands4}")

# 单像素噪声不该变成候选带
im5 = Image.new("RGB", (101, 200), (246, 246, 246))
for y in (20, 90, 150):
    im5.putpixel((50, y), (78, 78, 78))
check("零星单像素噪声 → 不成带", len(tool.find_bands(im5)) == 0, f"{tool.find_bands(im5)}")


print(f"\n=== 结果 {n_pass}/{n_all} ===")
print("通过" if ok_all else "未通过")
raise SystemExit(0 if ok_all else 1)
