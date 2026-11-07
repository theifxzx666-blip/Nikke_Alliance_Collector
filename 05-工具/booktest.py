# -*- coding: utf-8 -*-
"""多套库（成员库 / BOSS 库）的离线自检。

跑法：`python booktest.py`（在 05-工具 目录）

全套都在**临时目录**里跑：booklib 的路径常量被重定向，真实预设/一动不动。
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import booklib as B                                    # noqa: E402

OK = FAIL = 0


def check(name, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [OK]   {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def redirect(root: Path):
    """把 booklib 的所有路径常量指到临时根。"""
    B.PRESET = root / "预设"
    B.INDEX = B.PRESET / "库索引.json"
    B.ROSTER_DIR = B.PRESET / "成员库"
    B.BOSS_DIR = B.PRESET / "BOSS库"
    B.LEGACY_ROSTER = B.PRESET / "昵称库.json"
    B.LEGACY_BOSS = B.PRESET / "盟战boss表.json"
    B.PRESET.mkdir(parents=True, exist_ok=True)


def test_names():
    print("--- 库名校验 ---")
    check("空名被拒", B.valid_name("  ")[1] != "")
    check("非法字符被拒", B.valid_name("Q区/A")[1] != "")
    check("超长被拒", B.valid_name("长" * 41)[1] != "")
    check("保留名被拒", B.valid_name("_索引")[1] != "")
    n, e = B.valid_name("  嗷润吉-Q区  ")
    check("正常名去掉首尾空格", n == "嗷润吉-Q区" and e == "", repr(n))


def test_migrate(root: Path):
    print("\n--- 旧单库迁移（首次运行）---")
    redirect(root)
    names = {f"成员{i}": {"rounds": 3, "score": 0.99, "alias": []} for i in range(62)}
    B.PRESET.mkdir(parents=True, exist_ok=True)
    B.LEGACY_ROSTER.write_text(json.dumps(
        {"version": 1, "rounds": 5, "names": names, "history": []},
        ensure_ascii=False), encoding="utf-8")
    B.LEGACY_BOSS.write_text(json.dumps({
        "version": 1, "season": "S19",
        "seasons": {"S18": ["重装惊悚", "普拉特", "岚灾", "手指", "水晶洞穴"],
                    "S19": ["邪魔", "医师", "铁匠", "重装火神R", "古铁"]}},
        ensure_ascii=False), encoding="utf-8")

    msg = B.ensure_migrated()
    check("迁移有说明", "成员库「默认」" in msg and "BOSS" in msg, msg)
    check("活动成员库 = 默认", B.active_roster() == "默认", B.active_roster())
    check("默认库 62 人", len(B.read_roster().get("names") or {}) == 62,
          str(len(B.read_roster().get("names") or {})))
    check("活动 BOSS 库 = S19", B.active_boss() == "S19", B.active_boss())
    check("S19 五个 BOSS 对", B.read_boss().get("bosses") ==
          ["邪魔", "医师", "铁匠", "重装火神R", "古铁"], str(B.read_boss().get("bosses")))
    check("旧 BOSS 表按赛季各成一套（2 套）", len(B.list_bosses()) == 2,
          str([r["name"] for r in B.list_bosses()]))
    check("旧文件保留未删", B.LEGACY_ROSTER.exists() and B.LEGACY_BOSS.exists())

    check("再迁一次是空操作（幂等）", B.ensure_migrated() == "")


def test_switch_and_save(root: Path):
    print("\n--- 活动切换 / 另存 / 两套互不污染 ---")
    ok, msg = B.save_roster_as("嗷润吉-Q区")
    check("另存为 Q 区", ok and "62 人" in msg, msg)
    check("另存后自动设为活动", B.active_roster() == "嗷润吉-Q区", B.active_roster())
    check("两套都在列表里", {r["name"] for r in B.list_rosters()} ==
          {"默认", "嗷润吉-Q区"}, str([r["name"] for r in B.list_rosters()]))

    v = {"version": 1, "rounds": 1,
         "names": {f"V区成员{i}": {"rounds": 1, "score": 1.0} for i in range(20)}}
    B.write_roster("嗷润吉-V区", v)
    check("V 区 20 人（人数不固定 32）",
          len(B.read_roster("嗷润吉-V区").get("names") or {}) == 20)
    check("写 V 区不污染 Q 区",
          len(B.read_roster("嗷润吉-Q区").get("names") or {}) == 62,
          str(len(B.read_roster("嗷润吉-Q区").get("names") or {})))
    check("活动切成 V 区", B.active_roster() == "嗷润吉-V区")

    check("切到不存在的库被拒", B.set_active_roster("不存在") is False)
    ok, msg = B.set_active_roster("嗷润吉-Q区"), "ok"
    check("切回 Q 区", B.active_roster() == "嗷润吉-Q区")

    print("\n--- 重命名 / 删除保护 ---")
    ok, msg = B.rename_roster("嗷润吉-V区", "嗷润吉-V区2")
    check("重命名成功", ok, msg)
    check("旧名不在、新名在",
          {r["name"] for r in B.list_rosters()} == {"默认", "嗷润吉-Q区", "嗷润吉-V区2"},
          str([r["name"] for r in B.list_rosters()]))
    ok, msg = B.delete_roster("嗷润吉-Q区")
    check("活动库不可删", not ok and "活动" in msg, msg)
    ok, msg = B.delete_roster("默认")
    check("非活动库可删", ok, msg)
    ok, msg = B.delete_roster("嗷润吉-V区2")
    check("非活动库删到只剩活动套", ok and len(B.list_rosters()) == 1, msg)
    ok, msg = B.delete_roster("嗷润吉-Q区")
    check("唯一活动套不可删（删无可删时停在活动库）", not ok and "活动" in msg, msg)


def test_boss(root: Path):
    print("\n--- BOSS 库编辑 ---")
    ok, msg = B.save_boss_as("自定义5个", ["A", "B", "C", "D", "E"])
    check("另存 BOSS 库", ok and "5 个" in msg, msg)
    check("活动 BOSS 切到自定义", B.active_boss() == "自定义5个", B.active_boss())
    check("写 BOSS 库不污染 S19",
          B.read_boss("S19").get("bosses") == ["邪魔", "医师", "铁匠", "重装火神R", "古铁"])
    ok = B.write_boss("自定义5个", ["A1", "B1", "C1", "D1", "E1"])
    check("同一套可覆盖编辑", ok and B.read_boss().get("bosses")[0] == "A1",
          str(B.read_boss().get("bosses")))
    ok, msg = B.save_boss_as("空套", [])
    check("空 BOSS 名单被拒", not ok and "空" in msg, msg)
    ok, msg = B.rename_boss("自定义5个", "S19")
    check("与已有库重名被拒", not ok and "已经" in msg, msg)
    ok, msg = B.delete_boss("S19")
    check("非活动 BOSS 库可删", ok, msg)
    check("切到不存在的 BOSS 库被拒", B.set_active_boss("不存在") is False)


def test_broken(root: Path):
    print("\n--- 坏文件兜底 ---")
    p = B.roster_path("嗷润吉-Q区")
    p.write_text("{ 这不是 json", encoding="utf-8")
    check("坏成员库读成空库", B.read_roster("嗷润吉-Q区").get("names") == {})
    B.write_roster("嗷润吉-Q区", {"names": {"好人": {}}})


def main():
    root = Path(tempfile.mkdtemp(prefix="booktest_"))
    print(f"临时根：{root}\n")
    test_names()
    test_migrate(root)
    test_switch_and_save(root)
    test_boss(root)
    test_broken(root)
    print(f"\n结果：{OK} 通过 · {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
