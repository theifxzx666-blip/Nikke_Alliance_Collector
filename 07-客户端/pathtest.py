# -*- coding: utf-8 -*-
"""数据目录与命名规则的自检（v1.18）。

    ..\\05-工具\\.venv\\Scripts\\python.exe pathtest.py

只验纯逻辑：不写用户目录（用临时根），不碰游戏。
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from app import paths as P                                    # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        FAIL.append(name)


def t_names():
    print("\n--- 1. 命名规则 ---")
    when = datetime(2026, 11, 7, 15, 30)
    check("伤害统计：BOSS 库在前、成员库紧跟",
          P.damage_file("S19", "嗷润吉Q区", when) == "S19嗷润吉Q区_伤害统计_20261107_1530.csv",
          P.damage_file("S19", "嗷润吉Q区", when))
    check("伤害统计：没选成员库时收缩",
          P.damage_file("S19", "", when) == "S19_伤害统计_20261107_1530.csv",
          P.damage_file("S19", "", when))
    check("伤害统计：两个都没选只剩类型",
          P.damage_file("", "", when) == "伤害统计_20261107_1530.csv",
          P.damage_file("", "", when))
    check("成员名单",
          P.roster_file("嗷润吉Q区", when) == "嗷润吉Q区_成员名单_20261107_1530.csv",
          P.roster_file("嗷润吉Q区", when))
    check("BOSS 名单",
          P.boss_file("S19", when) == "S19_BOSS名单_20261107_1530.csv",
          P.boss_file("S19", when))
    check("诊断包精确到秒",
          P.diag_file(datetime(2026, 11, 7, 15, 30, 15)) == "诊断_20261107_153015.zip",
          P.diag_file(datetime(2026, 11, 7, 15, 30, 15)))


def t_safe():
    print("\n--- 2. 非法字符与长度 ---")
    check("斜杠/冒号被去掉", P.safe('Q区/2026:07') == "Q区202607", P.safe('Q区/2026:07'))
    check("管道与问号被去掉", P.safe('a|b?c*d"e<f>g') == "abcdefg", P.safe('a|b?c*d"e<f>g'))
    check("空白压成一个、首尾空格去掉", P.safe("  嗷润吉   Q区  ") == "嗷润吉 Q区",
          P.safe("  嗷润吉   Q区  "))
    check("末尾的点去掉（Windows 不允许）", P.safe("联盟.") == "联盟", P.safe("联盟."))
    check("超长截到 40 字", len(P.safe("啊" * 80)) == P.MAX_NAME, str(len(P.safe("啊" * 80))))
    check("空名回落为空串", P.safe("") == "" and P.safe(None) == "", repr(P.safe(None)))
    check("清洗后的名字能拼进文件名",
          P.damage_file('S19', 'a/b', datetime(2026, 1, 1, 0, 0))
          == "S19ab_伤害统计_20260101_0000.csv",
          P.damage_file('S19', 'a/b', datetime(2026, 1, 1, 0, 0)))


def t_dirs():
    print("\n--- 3. 目录：只选根、子目录自动建 ---")
    tmp = Path(tempfile.mkdtemp(prefix="pathtest_"))
    root = tmp / "数据根"
    cfg = {"data_dir": str(root)}
    check("根目录按配置解析", P.root(cfg) == root, str(P.root(cfg)))
    check("根目录被建出来", root.is_dir(), "")
    for k in P.KINDS:
        d = P.sub(k, cfg)
        check(f"子目录 {k} 自动创建", d.is_dir() and d.parent == root, str(d))
    check("日志落在 日志/ 下",
          P.log_file(cfg).parent == root / P.LOG, str(P.log_file(cfg)))
    check("ensure_all 会把五类都建好",
          P.ensure_all(cfg) == root
          and all((root / k).is_dir() for k in P.KINDS), "")

    # 老配置：只有 export_dir 时当数据根沿用
    old = tmp / "老导出目录"
    check("老配置 export_dir 当数据根",
          P.root({"export_dir": str(old)}) == old, str(P.root({"export_dir": str(old)})))
    check("data_dir 优先于 export_dir",
          P.root({"data_dir": str(root), "export_dir": str(old)}) == root, "")
    # 脏配置：不存在的盘符 → 回落默认根（不抛异常）
    check("不可用的路径回落默认根",
          P.root({"data_dir": "Z:/不存在的盘/xx"}) == P.default_root(),
          str(P.root({"data_dir": "Z:/不存在的盘/xx"})))


def main():
    t_names()
    t_safe()
    t_dirs()
    print()
    if FAIL:
        print(f"失败 {len(FAIL)} 项：" + "、".join(FAIL))
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
