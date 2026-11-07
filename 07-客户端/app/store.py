# -*- coding: utf-8 -*-
"""导出与设置持久化。

只做三件事：把记录写成 CSV、记住默认导出目录、记住最近一次导出。
判据与字段口径跟 Tk 工具（`hard_client.export`）完全一致 —— 同一份数据
在两个前端导出，必须逐列相同，否则以后合并两份 CSV 会对不上。
"""
import csv
import json
from pathlib import Path

import alliance_ocr as A

from . import paths as P

SETTINGS_PATH = A.APP_DIR / "预设" / "导出设置.json"

# 列顺序与 Tk 版一致；置信度是「成员/BOSS/伤害三者最低分」，不是平均分。
HEADER = ("序号", "成员", "BOSS", "伤害", "剩余血量", "置信度")


def default_dir() -> Path:
    """数据根（v1.18）：`文档\嗷润吉联盟采集`，里面按类型分子目录。"""
    return P.root()


def load_settings() -> dict:
    try:
        d = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(**kw) -> None:
    d = load_settings()
    d.update({k: v for k, v in kw.items() if v is not None})
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    except Exception:
        # 设置存不下来不影响采集，不能因此中断流程
        pass


def export_dir() -> Path:
    """伤害统计的落盘目录：`<数据根>/伤害统计`（v1.18）。"""
    return P.sub(P.DAMAGE)


def export_rows(rows, boss_book: str = "", roster_book: str = "") -> Path:
    """把记录写成 CSV，返回落盘路径。

    文件名按 v1.18 规范：`{BOSS库}{成员库}_伤害统计_{时间}.csv`
    （例 `S19嗷润吉Q区_伤害统计_20261107_1530.csv`），落在 `<根>/伤害统计/`。

    缺字段的条目**照常导出**并单独返回下标 —— 留空比编一个值安全，
    但必须让人一眼看到该核哪几条。
    """
    if not rows:
        raise ValueError("还没有数据")
    out = P.sub(P.DAMAGE) / P.damage_file(boss_book, roster_book)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for i, r in enumerate(rows, 1):
            w.writerow([i, r.get("member") or "", r.get("boss") or "",
                        r.get("damage_raw") or "", r.get("pct") or "",
                        "" if r.get("conf") is None else r["conf"]])
    save_settings(last_export=str(out))
    return out


def incomplete(rows) -> list:
    """哪几条缺关键字段（成员 / BOSS / 伤害任一为空）。"""
    return [i for i, r in enumerate(rows, 1)
            if not (r.get("member") and r.get("boss")
                    and r.get("damage") is not None)]
