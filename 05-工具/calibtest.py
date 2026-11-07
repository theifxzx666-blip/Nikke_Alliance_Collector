# -*- coding: utf-8 -*-
"""验证标定链路：黑边裁剪 + 点一下推导 ROI 的可复现性。

重点不是"能不能算对一次"，而是"换个点法结果一不一样"——
手画框的病根就是不可复现，这一层必须证明同样的画面必得同样的 ROI。

    .venv\\Scripts\\python.exe calibtest.py
"""
import importlib.util
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

ok_all = True


def check(name, good, detail=""):
    global ok_all
    ok_all = ok_all and bool(good)
    print(f"{'✓' if good else '✗'} {name}" + (f"  {detail}" if detail else ""))


# ---------------------------------------------------------------- 黑边裁剪

def frame(w, h, fill=(100, 105, 120)):
    return Image.new("RGB", (w, h), fill)


print("=== 1. 有效画面（去黑边） ===")

check("纯画面无黑边 → 返回 None", tool.content_box(frame(800, 450)) is None)

# 21:9 画布上居中放 16:9 画面 → 左右各一条黑边
SW, SH = 960, 411
cw = int(SH * 16 / 9)
pillar = frame(SW, SH, (0, 0, 0))
pillar.paste(frame(cw, SH), ((SW - cw) // 2, 0))
box = tool.content_box(pillar)
check("左右黑边（pillarbox）裁对了",
      box == ((SW - cw) // 2, 0, (SW - cw) // 2 + cw, SH), f"→ {box}")

# 16:9 画布上下加黑边
letter = frame(cw, 600, (0, 0, 0))
letter.paste(frame(cw, SH), (0, (600 - SH) // 2))
box = tool.content_box(letter)
check("上下黑边（letterbox）裁对了",
      box == (0, (600 - SH) // 2, cw, (600 - SH) // 2 + SH), f"→ {box}")

# 左侧是一块深色 UI（有纹理/图标），不是黑边：均值低但纵向方差大，不该被裁
grad = frame(800, 450)
rng = np.random.default_rng(7)
panel = np.full((450, 40), 5, dtype=np.uint8)
panel[rng.random((450, 40)) < 0.08] = 90          # 8% 像素是亮的图标/描边
grad.paste(Image.fromarray(panel).convert("RGB"), (0, 0))
check("深色纹理 UI 不被误当黑边", tool.content_box(grad) is None)

# 真的黑边 + 画面里还有深色区：只裁黑边
mixed = frame(cw + 200, SH + 100, (0, 0, 0))
inner = frame(cw, SH, (90, 95, 110))
ImageDraw.Draw(inner).rectangle([0, 0, 80, SH], fill=(18, 18, 22))
mixed.paste(inner, (100, 50))
check("黑边 + 画面内深色区，两个都判对",
      tool.content_box(mixed) == (100, 50, 100 + cw, 50 + SH))


# ---------------------------------------------------------------- 点选推导

NAMES = ["阿尔法测试员", "Bravo_002", "查理斯", "Delta", "Echo玩家", "Foxtrot9", "ゴルフ"]


def _font(size, path="C:/Windows/Fonts/msyh.ttc"):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def make_list(rows=7, pitch=70, top=100, w=1316, h=740):
    """仿联盟列表：浅色卡片 + 深灰昵称 + 彩色头像 + 浅灰右栏文字。

    配色是照实测来的——昵称笔迹落 62~84、右栏"最近登录"约 155、头像高饱和。
    band_text_color / find_bands 的判据正是靠这三点区分"昵称行"和"别的行"。
    """
    img = Image.new("RGB", (w, h), (30, 30, 34))
    d = ImageDraw.Draw(img)
    f = _font(26)
    fs = _font(20)
    for i in range(rows):
        cy = top + i * pitch
        d.rounded_rectangle([20, cy - 30, 700, cy + 34], radius=8, fill=(235, 238, 242))
        d.ellipse([44, cy - 24, 104, cy + 30], fill=(200, 120, 90))
        d.rectangle([116, cy - 10, 148, cy + 12], fill=(90, 160, 210))
        d.text((200, cy), NAMES[i % len(NAMES)], font=f, fill=(72, 72, 72))
        d.text((560, cy + 4), "3小时前", font=fs, fill=(155, 155, 155))
    return img


print("\n=== 2. 点一下推导 ROI ===")

img = make_list()
base = tool.derive_column(img, (240, 112))          # 第 1 行中间
base_rect = base.get("rect")
if base.get("error"):
    check("推导成功", False, base["error"])
else:
    check("推导成功", True,
          f"rect={base['rect']} 行数={base['rows']} 行距={base['pitch']:.1f}")
    check("行数 = 7", base["rows"] == 7, f"→ {base['rows']}")
    check("行距 = 70", abs(base["pitch"] - 70) <= 1.0, f"→ {base['pitch']:.1f}")

    # 换不同的行去点：结果必须一模一样
    same = []
    for i in range(7):
        r = tool.derive_column(img, (240, 100 + i * 70 + 12))
        same.append(r.get("rect"))
    check("点在第 1~7 行的任意一行，ROI 完全相同",
          all(s == base["rect"] for s in same),
          f"{len(set(same))} 种结果")

    # 同一行内手抖 ±6px、横向换位置：也不该改变结果
    jitter = [tool.derive_column(img, (x, 112 + dy)).get("rect")
              for x in (205, 240, 300, 360)
              for dy in (-6, -3, 0, 3, 6)]
    check("同一行内点偏 ±6px / 横向换位置，ROI 不变",
          all(j == base["rect"] for j in jitter),
          f"{len(set(jitter))} 种结果")

    # ROI 必须把整行字包住
    x0, y0, x1, y1 = base["rect"]
    check("ROI 覆盖首行到末行",
          y0 <= 100 and y1 >= 100 + 6 * 70 + 26, f"y {y0}~{y1}")
    check("ROI 没把左侧头像算进去", x0 >= 150, f"x0={x0}")
    check("ROI 没把右侧浅灰文字算进去", x1 < 560, f"x1={x1}")

# 只有 2 行时应该拒绝（连不出等距格点，宁可让人重来也别给个错的框）
thin = make_list(rows=2)
r = tool.derive_column(thin, (240, 112))
check("行数太少时明确报错", "error" in r, r.get("error", ""))

# 点在行间空白：不该报错。旧版这里会弹「这一下没点到昵称」，
# 而"点了两行之间的缝"完全是个正常动作——列已经找到了，照用就是。
r = tool.derive_column(img, (240, 300))
check("点在行间空白处仍然给结果（不弹错）",
      r.get("rect") == base_rect, str(r.get("rect") or r.get("error")))

# 横向离得很远要拒：那不是"点偏了"，是点错了东西
r = tool.derive_column(img, (1290, 700))
check("点得离昵称列很远时明确报错", "error" in r, r.get("error", ""))


print("\n=== 2b. 不给点击也能自动认 ===")

cols = tool.detect_columns(img)
check("候选列只有一列", len(cols) == 1, f"{len(cols)} 列")
auto = tool.derive_column(img, None)
check("自动推导成功", not auto.get("error"), auto.get("error", ""))
if not auto.get("error"):
    check("自动推导 = 点击推导", auto.get("rect") == base_rect,
          f"{auto.get('rect')} vs {base_rect}")
    check("自动推导认出 7 行 / 行距 70",
          auto["rows"] == 7 and abs(auto["pitch"] - 70) <= 1.0,
          f"{auto['rows']} 行 · {auto['pitch']}")


# ---------------------------------------------------------------- 行距测量

print("\n=== 3. 手动框选的行距补测 ===")

tmp = HERE / "输出" / "_test_标定.png"
img.save(tmp)
pitch, rows = tool._measure_pitch(img.crop((190, 60, 420, 620)))
check("从区域截图量出行距", pitch is not None and abs(pitch - 70) <= 1.0,
      f"→ 行距 {pitch} · {rows} 行")

# ROI 里除了昵称还有头像边框、分隔线这类横条。它们的间距跟行距无关，
# 一起参与拟合就会把中位数拉到一半（实测 356px 高、真值 71px 的列表量成了 39px）。
noisy = img.crop((190, 60, 420, 620)).copy()
dd = ImageDraw.Draw(noisy)
for i in range(6):
    y = 45 + i * 70
    dd.rectangle([0, y, 55, y + 7], fill=(120, 120, 120))   # 浅灰横条，不是昵称色
p2, r2 = tool._measure_pitch(noisy)
check("ROI 混进头像边框时行距仍是 70",
      p2 is not None and abs(p2 - 70) <= 2.0, f"→ {p2} · {r2} 行")
try:
    tmp.unlink()
except Exception:
    pass


print("\n=== 3b. 预设固化（标定一次就够） ===")


class _Frame:
    """假的有效画面：预设只关心 w/h/aspect。"""
    w, h = 1316, 740
    aspect = 1316 / 740


saved_path = tool.PRESET
backup = None
if saved_path.exists():
    backup = saved_path.with_name(saved_path.name + ".testbak")
    saved_path.replace(backup)          # 别把用户真在用的预设写坏

try:
    rel = (0.15, 0.13, 0.27, 0.75)
    check("写入预设成功",
          tool.save_preset(rel, 70.0, 7, frame=_Frame) and saved_path.exists())
    p = tool.load_preset()
    check("读回的比例和写进去的一致",
          bool(p) and all(abs(a - b) < 1e-5 for a, b in zip(p["region_rel"], rel)),
          str(p and p["region_rel"]))
    check("行距按「占画面高度」存，不是像素",
          bool(p) and abs(p["pitch_rel"] - 70 / 740) < 1e-4,
          f"{p and p['pitch_rel']}")
    check("每屏行数和原分辨率一并记下",
          bool(p) and p["rows"] == 7 and p["resolution"] == "1316x740",
          f"{p and p['rows']} 行 · {p and p['resolution']}")
    # 这条是"换分辨率不用重标"的全部依据
    check("换到 1920×1080 时行距自动换算成 102px",
          bool(p) and abs(p["pitch_rel"] * 1080 - 102.2) < 1.0,
          f"→ {p['pitch_rel'] * 1080:.1f}px")
    saved_path.write_text("{ 坏文件", encoding="utf-8")
    check("预设文件损坏时返回 None，不抛异常", tool.load_preset() is None)
finally:
    try:
        saved_path.unlink()
    except Exception:
        pass
    if backup is not None:
        backup.replace(saved_path)


print("\n=== 4. 多次框选取中位数（框几次再提交） ===")
# 模拟真人手抖：同一个意图框 5 次，每个分量上下差十几像素
truth = (194, 97, 360, 557)
jitter = [(0, 0, 0, 0), (-9, 6, 8, -5), (7, -8, -6, 9),
          (-4, 3, 5, -7), (3, -2, -3, 4)]
samples = []
for dx0, dy0, dx1, dy1 in jitter:
    r = (truth[0] + dx0, truth[1] + dy0, truth[2] + dx1, truth[3] + dy1)
    samples.append((r, 70.0 + dy1 * 0.1, 7))
m = tool.median_samples(samples)
check("5 次抖动的中位数 = 真实框", bool(m) and m["rect"] == truth,
      f"{m and m['rect']} vs {truth}")


def err(rect):
    return sum(abs(a - b) for a, b in zip(rect, truth))


avg = tuple(int(round(sum(s[0][i] for s in samples) / len(samples))) for i in range(4))
check("中位数比均值更接近真实值（手抖是二值化的，均值会被拖飞的那次拉偏）",
      m and err(m["rect"]) <= err(avg), f"中位数误差 {err(m['rect'])} vs 均值 {err(avg)}")

# 一次拖飞了（框到隔壁栏）：中位数不该被带跑
bad = samples + [((520, 90, 690, 560), 70.0, 7)]
m2 = tool.median_samples(bad)
check("混进一个拖飞了的样本，中位数仍然不乱",
      bool(m2) and err(m2["rect"]) <= 12, f"{m2 and m2['rect']} 误差 {m2 and err(m2['rect'])}")

# 只框一次也要能提交——用户不想框第二次的时候不该逼他
one = tool.median_samples([(truth, 70.0, 7)])
check("只框 1 次也能得到结果", bool(one) and one["rect"] == truth and one["samples"] == 1)

# 量不出行距（画面被遮/对比度低）时照样收下，行距留空由滚速闭环兜
m3 = tool.median_samples([(truth, None, 0)])
check("量不出行距也照样返回区域（不拦人）",
      bool(m3) and m3["rect"] == truth and not m3["pitch"], str(m3 and m3["pitch"]))

# 行距量成半格（真实踩过的 bug：39 vs 71）时，用「高度÷行数」纠正
m4 = tool.median_samples([((194, 97, 360, 557), 39.0, 7)])
check("行距量成半格时按 高度÷行数 纠正",
      bool(m4) and abs(m4["pitch"] - (557 - 97) / 7) < 0.5,
      f"→ {m4 and m4['pitch']}（应为 {(557 - 97) / 7:.1f}）")

check("空样本列表返回 None", tool.median_samples([]) is None)
# 框得太小（误点一下又拖了 5px）不该被当成有效样本
check("太小的框不算有效样本",
      tool.median_samples([((100, 100, 120, 112), None, 0)]) is None)


print("\n=== 5. 框的上下边吸附到真实行边界 ===")
# 真实行边界（find_bands 量出来的）：首行 107~130，末行 530~547
TRUE_TOP, TRUE_BOT = 99, 555      # 含 padding 的期望边界，与 derive_column 一致

r, p, n = tool.snap_to_rows(img, (194, 60, 360, 620))
check("框比整列大很多 → 收紧到真实首末行",
      r == (194, TRUE_TOP, 360, TRUE_BOT) and n == 7, f"→ {r} · {n} 行 · 行距 {p}")

r, p, n = tool.snap_to_rows(img, (194, 110, 360, 545))
check("框把首行和末行各切掉一半 → 两行都完整补回来（不会漏行）",
      r == (194, TRUE_TOP, 360, TRUE_BOT) and n == 7, f"→ {r} · {n} 行")

r, p, n = tool.snap_to_rows(img, (194, 115, 360, 600))
check("框切掉首行上半 → 首行仍被包含",
      r[1] <= 107 and n == 7, f"→ y0={r[1]} · {n} 行")

# 框和这一列错位（套在头像那一条上）：不该硬算出个假区域来
r, p, n = tool.snap_to_rows(img, (30, 90, 110, 560))
check("框错到别的列 → 原样返回，不瞎猜", r == (30, 90, 110, 560) and p is None,
      f"→ {r} · pitch={p}")

# 只有 2 行的小列表也要能用（行数少的列表不该被拒）
small = make_list(rows=3)
r, p, n = tool.snap_to_rows(small, (194, 80, 360, 300))
check("只有 3 行的短列表也能吸附", n == 3 and p and abs(p - 70) <= 2,
      f"→ {r} · {n} 行 · 行距 {p}")
print("通过" if ok_all else "未通过")
raise SystemExit(0 if ok_all else 1)
