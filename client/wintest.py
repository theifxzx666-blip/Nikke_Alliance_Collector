# -*- coding: utf-8 -*-
"""窗口控制（16:9 / 并排 / 跟随判定）的纯逻辑测试：不碰真实窗口，只验数学。

跑法：`python wintest.py`（在 client 目录）
为什么单独放这里：`app/win.py` 是客户端专有逻辑，engine 那套测试不该依赖 Qt。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import win as W  # noqa: E402  （导入 app 包会先执行 __init__，把 engine 挂进 sys.path）

FAIL = []


def check(name, cond, extra=""):
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ---------------- 16:9 方案（用假矩形，避免动真窗口）
_orig_client, _orig_window = W.client_box, W.window_box


def fake_boxes(client, window):
    W.client_box = lambda hwnd: client
    W.window_box = lambda hwnd: window


fake_boxes((100, 50, 1920, 1200), (92, 18, 1936, 1248))
p = W.plan_aspect(1)
check("16:10 保宽度改高度", p and p["target_client"] == (1920, 1080), str(p))
check("窗口尺寸要加上边框", p and p["window_size"] == (1936, 1128), str(p))

fake_boxes((100, 50, 1920, 1080), (92, 18, 1936, 1128))
check("已经是 16:9 → 不出方案", W.plan_aspect(1) is None)

fake_boxes((0, 0, 2560, 1080), (-8, -32, 2576, 1128))
p2 = W.plan_aspect(1)
check("超宽屏 → 保高度缩宽度", p2 and p2["target_client"] == (1920, 1080), str(p2))

fake_boxes((0, 0, 1920, 1080), (0, 0, 1920, 1080))
check("无边框窗口也成立", W.plan_aspect(1) is None)


# ---------------- 并排方案（纯数学）
class Avail:
    def left(self): return 0
    def top(self): return 0
    def right(self): return 1706
    def bottom(self): return 1018
    def width(self): return 1707
    def height(self): return 1019


AV = Avail()
CW, MINH = 468, 560

# 右边放得下：贴游戏右侧，间距 8
p = W.plan_beside((0, 200, 1000, 562), CW, MINH, AV)
check("右侧放得下 → 贴右 + 间距", p["x"] == 1008 and p["side"] == "右侧" and not p["overlap"], str(p))
check("不传高度时沿用游戏客户区高（旧调用兼容）", p["h"] == 562, str(p))

# v1.4：高度归用户 —— 传了 ch 就按 ch，不再被游戏高度顶替
p = W.plan_beside((0, 120, 1000, 562), CW, MINH, AV, ch=820)
check("客户端高度按用户给的值（可高于游戏）",
      p["h"] == 820, str(p))
check("顶部与游戏对齐、左侧贴右缘",
      p["y"] == 120 and p["x"] == 1008, str(p))
p = W.plan_beside((0, 200, 1000, 562), CW, MINH, AV, ch=820)
check("顶部对齐但底部会出屏时上移，仍保顶部优先",
      p["y"] + p["h"] <= AV.bottom() and p["y"] <= 200, str(p))
p = W.plan_beside((0, 200, 1000, 562), CW, MINH, AV, ch=300)
check("低于最小高时抬到 min_h", p["h"] == MINH, str(p))
p = W.plan_beside((0, 700, 1000, 562), CW, MINH, AV, ch=820)
check("底部放不下就上移，仍不出屏幕",
      p["y"] + p["h"] <= AV.bottom(), str(p))

# 右边放不下：默认只允许右侧 → 报 overlap（**不许自己挪到游戏上面**）
p = W.plan_beside((200, 200, 1200, 675), CW, MINH, AV, side="右侧")
check("右侧放不下 → overlap 而不是硬贴", p["overlap"] is True, str(p))

# 自动：右满则换左
p = W.plan_beside((1000, 200, 700, 394), CW, MINH, AV, side="自动")
check("自动模式右满换左", p["side"] == "左侧" and p["x"] == 1000 - CW - 8, str(p))

# 明确选左侧
p = W.plan_beside((600, 200, 900, 506), CW, MINH, AV, side="左侧")
check("左侧坐标", p["x"] == 600 - CW - 8, str(p))

# 不动
p = W.plan_beside((200, 200, 900, 506), CW, MINH, AV, side="不动")
check("不动不改几何", p["x"] == 200 and p["y"] == 200, str(p))

# 游戏很高时客户端高度被工作区夹住
p = W.plan_beside((0, 0, 900, 2000), CW, MINH, AV)
check("客户端高度不超过工作区", p["h"] <= AV.height(), str(p))

# ---------------- 缩游戏（放不下时唯一正确的路）
sz = W.plan_shrink(AV, CW, gap=8)
check("缩游戏能算出方案", sz is not None, str(sz))
check("缩完正好并排放得下",
      sz and sz[0] + 8 + CW <= AV.width() - 2 * W.EDGE, str(sz))
check("缩出来还是 16:9",
      sz and abs(sz[0] / sz[1] - 16 / 9) < 0.01, str(sz))

class Narrow(Avail):
    def right(self): return 1279
    def width(self): return 1280


nsz = W.plan_shrink(Narrow(), CW, gap=8)
check("1280 宽屏放不下 → 明确返回 None（不缩成看不清）", nsz is None, str(nsz))

class Short(Avail):
    def bottom(self): return 719
    def height(self): return 720


ssz = W.plan_shrink(Short(), CW, gap=8)
check("矮屏以高度为准反算", ssz is not None and ssz[1] <= 720 - 2 * W.EDGE, str(ssz))

# ---------------- v1.9 建议尺寸 = 并排反算（唯一真源）
sug = W.plan_suggest_client(AV, CW, 8)
shr = W.plan_shrink(AV, CW, 8)
check("建议尺寸与并排反算完全一致", sug == shr, f"{sug} vs {shr}")
check("建议尺寸给客户端留了位置", sug is not None and sug[0] + CW + 8 <= AV.width(),
      f"{sug} / avail {AV.width()}")
check("建议尺寸是 16:9", sug is not None and abs(sug[0] / sug[1] - 16 / 9) < 0.02, str(sug))
wider = W.plan_suggest_client(AV, CW + 200, 8)
check("客户端拖宽后建议尺寸同步变小",
      wider is not None and wider[0] < sug[0], f"{wider} vs {sug}")

# ---------------- 通联三态
check("没有句柄 → 未启动", W.game_link(0) == "gone", W.game_link(0))
check("空句柄 → 未启动", W.game_link(None) == "gone", str(W.game_link(None)))

# ---------------- 跟随判定
check("同一矩形算没动", W.same_rect((0, 0, 100, 100), (1, 1, 101, 101)) is True)
check("差 3px 算动了", W.same_rect((0, 0, 100, 100), (0, 0, 104, 100)) is False)
check("空矩形不算动（拿不到就别乱挪）", W.same_rect(None, (0, 0, 1, 1)) is False)

W.client_box, W.window_box = _orig_client, _orig_window

print()
if FAIL:
    print(f"失败 {len(FAIL)} 项：" + "、".join(FAIL))
    sys.exit(1)
print("全部通过")
