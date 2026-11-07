# -*- coding: utf-8 -*-
"""自测：用 verify 样本验证识别链路（不需要开游戏）。

用法：
    .venv\\Scripts\\python.exe selftest.py
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import importlib.util

spec = importlib.util.spec_from_file_location("tool", HERE / "alliance_ocr.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

SAMPLE = ROOT / "04-复用方案" / "verify" / "alliance" / "alliance_list_5rows.png"
GT = ["DORO", "曼殊颜华", "新来的", "非洲晒太阳", "PPORANGE"]


def run(label, img, expect_warn=False):
    found, bands, passed = tool.scan(img)
    got = [n for _y, n, _s in found]
    hit = sum(1 for g in GT if g in got)
    print(f"\n[{label}] 尺寸 {img.width}x{img.height} · 候选带 {len(bands)} · 过色 {passed} · 识别 {len(found)} · 命中 {hit}/{len(GT)}")
    for y, name, score in found:
        mark = "✔" if name in GT else "✘"
        print(f"   {mark} y={y:4}  {score:.3f}  {name}")
    if expect_warn:
        # 负例：框太宽时，程序应当察觉异常并提示重框（UI 里同一条判据）
        # 判据必须跟 alliance_ocr._apply 里的 bad 一致：看"过色行里认出来几行"，
        # 不看候选带条数（玩家横幅也会各占一条带，条数已经不代表能容纳几行）
        warn = (passed >= 2 and len(found) <= 1) or \
               (passed >= 4 and len(found) < passed * 0.5)
        print(f"   → 应触发「框选异常」提示：{'是' if warn else '否'}")
        return warn
    return hit == len(GT) and len(found) == len(GT)


def main():
    tool.dpi_aware()
    base = Image.open(SAMPLE).convert("RGB")
    # 正例：框住「昵称这一列」
    col = base.crop((110, 95, 310, 447))
    ok = []
    ok.append(run("正例 单列 1x", col))
    ok.append(run("正例 单列 2x", col.resize((col.width * 2, col.height * 2), Image.Resampling.LANCZOS)))
    # 负例：框宽了（含头像和右侧数字），应当被识别为异常而不是默默出错
    wide = base.crop((60, 95, 400, 447))
    ok.append(run("负例 宽框（含头像）", wide, expect_warn=True))

    print("\n=== 结果 ===")
    print("全部通过" if all(ok) else f"有 {ok.count(False)} 项未通过，需调参")


if __name__ == "__main__":
    main()
