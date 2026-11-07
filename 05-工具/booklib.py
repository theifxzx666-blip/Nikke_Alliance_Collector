# -*- coding: utf-8 -*-
"""多套库：成员名单库（昵称库）与 BOSS 库的存储、索引、迁移、活动套切换。

**为什么要有这个模块**（客户端设计-v1.md §13）：一个联盟可以有好几个盟
（Q 区 / V 区…），每个盟的人不一样、人数也不一定 32。原来只有一份
`预设/昵称库.json`、一份 `盟战boss表.json`，采第二个盟就把第一个覆盖了。
所以改成"目录里每套一个文件 + 一份索引记活动套"：

```
预设/库索引.json        {"active_roster": "嗷润吉-Q区", "active_boss": "S19",
                        "rosters": {名字: {people,updated,note}}, "bosses": {...}}
预设/成员库/<名字>.json  与原昵称库同结构（version/rounds/names/history）
预设/BOSS库/<名字>.json  {"version":1,"name":…,"bosses":[…],"note":…,"updated":…}
```

**为什么不 import alliance_ocr**：alliance_ocr 要反过来用这里的 `roster_path()`，
相互 import 会成环。所以本模块自带一份 `_app_dir()`（逻辑与 alliance_ocr 的一致：
冻结态用 exe 所在目录，源码态用本文件目录）。

**迁移只做一次且不删原文件**：旧 `昵称库.json` / `盟战boss表.json` 原样保留，
出问题能立刻回滚。
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import appdir


def _app_dir() -> Path:
    """工具数据目录。规则与 `alliance_ocr._app_dir` 同源（`appdir.py`），
    不能各写一份 —— 两份一旦漂移，预设和库就分家了。"""
    return appdir.app_dir()


APP_DIR = _app_dir()
PRESET = APP_DIR / "预设"
INDEX = PRESET / "库索引.json"
ROSTER_DIR = PRESET / "成员库"
BOSS_DIR = PRESET / "BOSS库"

# 旧路径：只用于迁移与回落，不再写入
LEGACY_ROSTER = PRESET / "昵称库.json"
LEGACY_BOSS = PRESET / "盟战boss表.json"

BAD_CHARS = re.compile(r'[\\/:*?"<>|]')
MAX_NAME = 40


def valid_name(name: str) -> tuple:
    """库名校验：返回 (清理后的名字, 错误说明)。错误时名字为空串。"""
    s = (name or "").strip()
    if not s:
        return "", "名字不能为空"
    if BAD_CHARS.search(s):
        return "", '名字不能含 \\ / : * ? " < > | 这些字符'
    if len(s) > MAX_NAME:
        return "", f"名字最多 {MAX_NAME} 个字"
    if s.startswith(".") or s in ("_索引",):
        return "", "这个名字是保留名，换一个"
    return s, ""


# ---------------------------------------------------------------- 索引

def _default_index() -> dict:
    return {"version": 1, "active_roster": "", "active_boss": "",
            "rosters": {}, "bosses": {}}


def load_index() -> dict:
    try:
        d = json.loads(INDEX.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            out = _default_index()
            out.update(d)
            for k in ("rosters", "bosses"):
                if not isinstance(out.get(k), dict):
                    out[k] = {}
            return out
    except Exception:
        pass
    return _default_index()


def save_index(idx: dict) -> bool:
    try:
        INDEX.parent.mkdir(parents=True, exist_ok=True)
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, INDEX)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 路径

def roster_path(name: str | None = None) -> Path:
    """某个成员库的文件路径；name 省略时取活动套（没有活动套就用"默认"）。"""
    idx = load_index()
    n = (name if name is not None else idx.get("active_roster") or "").strip()
    if not n:
        n = "默认"
    return ROSTER_DIR / f"{n}.json"


def boss_path(name: str | None = None) -> Path:
    idx = load_index()
    n = (name if name is not None else idx.get("active_boss") or "").strip()
    if not n:
        # 没有活动套：给一个必然不存在的路径，调用方据此回落旧表
        return BOSS_DIR / "__none__.json"
    return BOSS_DIR / f"{n}.json"


def active_roster() -> str:
    return (load_index().get("active_roster") or "").strip()


def active_boss() -> str:
    return (load_index().get("active_boss") or "").strip()


def set_active_roster(name: str) -> bool:
    idx = load_index()
    if name not in idx["rosters"]:
        return False
    idx["active_roster"] = name
    return save_index(idx)


def clear_active_roster() -> bool:
    """取消活动成员库（= 伤害采集不做名字归位，只靠位置合并 + BOSS 归一）。"""
    idx = load_index()
    idx["active_roster"] = ""
    return save_index(idx)


def set_active_boss(name: str) -> bool:
    idx = load_index()
    if name not in idx["bosses"]:
        return False
    idx["active_boss"] = name
    return save_index(idx)


# ---------------------------------------------------------------- 列目录

def list_rosters() -> list:
    """[{name, people, updated, active}]，按名片数从多到少。"""
    idx = load_index()
    act = active_roster()
    out = []
    for n, e in (idx.get("rosters") or {}).items():
        out.append({"name": n,
                    "people": int((e or {}).get("people") or 0),
                    "updated": str((e or {}).get("updated") or ""),
                    "active": n == act})
    out.sort(key=lambda r: (-r["people"], r["name"]))
    return out


def list_bosses() -> list:
    """[{name, count, updated, active}]。"""
    idx = load_index()
    act = active_boss()
    out = []
    for n, e in (idx.get("bosses") or {}).items():
        out.append({"name": n,
                    "count": int((e or {}).get("count") or 0),
                    "updated": str((e or {}).get("updated") or ""),
                    "active": n == act})
    out.sort(key=lambda r: r["name"])
    return out


# ---------------------------------------------------------------- 成员库读写

def read_roster(name: str | None = None) -> dict:
    """读一套成员库；不存在/坏了给空库（绝不让它挡住采集）。"""
    p = roster_path(name)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("names"), dict):
            return d
    except Exception:
        pass
    return {"version": 1, "rounds": 0, "names": {}}


def _touch_roster(idx: dict, name: str, data: dict):
    idx["rosters"][name] = {
        "people": len(data.get("names") or {}),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "note": str(data.get("note") or ""),
    }


def write_roster(name: str, data: dict, activate: bool = True) -> bool:
    """写一套成员库并更新索引（activate=True 时设为活动套）。"""
    n, err = valid_name(name)
    if err:
        return False
    try:
        ROSTER_DIR.mkdir(parents=True, exist_ok=True)
        data = dict(data or {})
        data["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        tmp = roster_path(n).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, roster_path(n))
    except Exception:
        return False
    idx = load_index()
    _touch_roster(idx, n, data)
    if activate:
        idx["active_roster"] = n
    return save_index(idx)


def save_roster_as(name: str, data: dict | None = None) -> tuple:
    """另存当前成员库为新的一套。data 省略时复制活动套。返回 (ok, 说明)。"""
    n, err = valid_name(name)
    if err:
        return False, err
    src = data if data is not None else read_roster()
    if write_roster(n, src):
        return True, f"已保存成员库「{n}」· {len(src.get('names') or {})} 人"
    return False, "写入失败（目录权限？）"


def rename_roster(old: str, new: str) -> tuple:
    n, err = valid_name(new)
    if err:
        return False, err
    idx = load_index()
    if old not in idx["rosters"]:
        return False, "原库不存在"
    if n in idx["rosters"] and n != old:
        return False, f"「{n}」已经有一套了"
    try:
        os.replace(roster_path(old), roster_path(n))
    except Exception as e:
        return False, f"改名失败：{e}"
    idx["rosters"][n] = idx["rosters"].pop(old)
    if idx.get("active_roster") == old:
        idx["active_roster"] = n
    save_index(idx)
    return True, f"已改名为「{n}」"


def delete_roster(name: str) -> tuple:
    idx = load_index()
    if name not in idx["rosters"]:
        return False, "库不存在"
    if idx.get("active_roster") == name:
        return False, "这是当前活动库，先切到别的再删"
    if len(idx["rosters"]) <= 1:
        return False, "至少留一套成员库"
    try:
        p = roster_path(name)
        if p.exists():
            p.unlink()
    except Exception as e:
        return False, f"删除失败：{e}"
    idx["rosters"].pop(name, None)
    save_index(idx)
    return True, f"已删除「{name}」"


# ---------------------------------------------------------------- BOSS 库读写

def read_boss(name: str | None = None) -> dict:
    p = boss_path(name)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("bosses"), list):
            return d
    except Exception:
        pass
    return {"version": 1, "name": name or "", "bosses": []}


def write_boss(name: str, bosses, note: str = "", activate: bool = True) -> bool:
    n, err = valid_name(name)
    if err:
        return False
    bs = [str(b).strip() for b in (bosses or []) if str(b).strip()]
    try:
        BOSS_DIR.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "name": n, "bosses": bs, "note": note,
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
        tmp = boss_path(n).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, boss_path(n))
    except Exception:
        return False
    idx = load_index()
    idx["bosses"][n] = {"count": len(bs),
                        "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if activate:
        idx["active_boss"] = n
    return save_index(idx)


def save_boss_as(name: str, bosses=None) -> tuple:
    n, err = valid_name(name)
    if err:
        return False, err
    bs = bosses if bosses is not None else list(read_boss().get("bosses") or [])
    if not bs:
        return False, "BOSS 名单是空的"
    if write_boss(n, bs):
        return True, f"已保存 BOSS 库「{n}」· {len(bs)} 个"
    return False, "写入失败（目录权限？）"


def rename_boss(old: str, new: str) -> tuple:
    n, err = valid_name(new)
    if err:
        return False, err
    idx = load_index()
    if old not in idx["bosses"]:
        return False, "原库不存在"
    if n in idx["bosses"] and n != old:
        return False, f"「{n}」已经有一套了"
    try:
        os.replace(boss_path(old), boss_path(n))
    except Exception as e:
        return False, f"改名失败：{e}"
    idx["bosses"][n] = idx["bosses"].pop(old)
    if idx.get("active_boss") == old:
        idx["active_boss"] = n
    save_index(idx)
    return True, f"已改名为「{n}」"


def delete_boss(name: str) -> tuple:
    idx = load_index()
    if name not in idx["bosses"]:
        return False, "库不存在"
    if idx.get("active_boss") == name:
        return False, "这是当前活动库，先切到别的再删"
    if len(idx["bosses"]) <= 1:
        return False, "至少留一套 BOSS 库"
    try:
        p = boss_path(name)
        if p.exists():
            p.unlink()
    except Exception as e:
        return False, f"删除失败：{e}"
    idx["bosses"].pop(name, None)
    save_index(idx)
    return True, f"已删除「{name}」"


# ---------------------------------------------------------------- 迁移

def ensure_migrated() -> str:
    """首次运行把旧单库搬进目录结构。幂等：有索引就直接返回 ""。

    返回一句给人看的说明（迁移了什么），没事可做时返回空串。
    """
    if INDEX.exists():
        return ""
    idx = _default_index()
    notes = []

    # 成员库：旧昵称库 → 成员库/默认.json（原文件不删）
    if LEGACY_ROSTER.exists():
        try:
            d = json.loads(LEGACY_ROSTER.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("names"), dict):
                ROSTER_DIR.mkdir(parents=True, exist_ok=True)
                target = ROSTER_DIR / "默认.json"
                if not target.exists():
                    target.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
                _touch_roster(idx, "默认", d)
                idx["active_roster"] = "默认"
                notes.append(f"昵称库 {len(d.get('names') or {})} 人 → 成员库「默认」")
        except Exception:
            pass

    # BOSS 库：旧赛季表 → 每期一套（活动套 = 表里当前 season）
    if LEGACY_BOSS.exists():
        try:
            t = json.loads(LEGACY_BOSS.read_text(encoding="utf-8"))
            seasons = t.get("seasons") if isinstance(t, dict) else None
            if isinstance(seasons, dict):
                BOSS_DIR.mkdir(parents=True, exist_ok=True)
                for s, names in seasons.items():
                    bs = [str(x).strip() for x in (names or []) if str(x).strip()]
                    if not bs:
                        continue
                    p = BOSS_DIR / f"{s}.json"
                    if not p.exists():
                        p.write_text(json.dumps(
                            {"version": 1, "name": s, "bosses": bs,
                             "note": "从旧盟战boss表迁移", "updated": ""},
                            ensure_ascii=False, indent=1), encoding="utf-8")
                    idx["bosses"][s] = {"count": len(bs), "updated": ""}
                cur = str(t.get("season") or "")
                if cur in idx["bosses"]:
                    idx["active_boss"] = cur
                elif idx["bosses"]:
                    idx["active_boss"] = sorted(idx["bosses"])[-1]
                notes.append(f"BOSS 表 {len(idx['bosses'])} 期 → BOSS库/")
        except Exception:
            pass

    if not idx["rosters"]:
        # 什么都没有：建一套空"默认"，至少界面有东西可选
        write_roster("默认", {"version": 1, "rounds": 0, "names": {}},
                     activate=True)
        idx = load_index()
        notes.append("新建空成员库「默认」")

    save_index(idx)
    return "；".join(notes)


def describe() -> str:
    """给日志/状态卡用的一行摘要。"""
    idx = load_index()
    r = idx.get("active_roster") or ""
    b = idx.get("active_boss") or "（无）"
    rp = int((idx["rosters"].get(r) or {}).get("people") or 0)
    bc = int((idx["bosses"].get(b) or {}).get("count") or 0)
    rt = f"成员库 {r} · {rp} 人" if r else "不使用成员库"
    return f"{rt} ｜ BOSS 库 {b} · {bc} 个"
