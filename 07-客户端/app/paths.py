# -*- coding: utf-8 -*-
"""数据目录与文件命名（v1.18）。

一个数据根 + 五类子目录，文件名带库名与时间戳：

    <根>/伤害统计/S19嗷润吉Q区_伤害统计_20261107_1530.csv
    <根>/成员名单/嗷润吉Q区_成员名单_20261107_1530.csv
    <根>/BOSS名单/S19_BOSS名单_20261107_1530.csv
    <根>/日志/操作日志.txt
    <根>/诊断/诊断_20261107_153015.zip

为什么只让用户选「根」：如果每类都让用户点一次，最后一定有人把伤害和名单指到同一个
目录里，混成一堆。子目录由程序建，用户只决定"放在哪块盘"。

命名规则为什么这么定：`S19嗷润吉Q区` 把 BOSS 库放前面、成员库紧跟，一眼能看出
"哪一期、哪个盟"；时间戳精确到分钟，连续导出只留最后一次（覆盖），不制造一堆副本。
"""
from datetime import datetime
from pathlib import Path

DAMAGE = "伤害统计"
ROSTER = "成员名单"
BOSS = "BOSS名单"
LOG = "日志"
DIAG = "诊断"
KINDS = (DAMAGE, ROSTER, BOSS, LOG, DIAG)

# 随包素材（图标 / 收款码 / 内置背景）。打包后位于 `_internal/assets`，
# 源码态位于 `07-客户端/assets` —— 两种布局下 parent.parent 都对。
ASSETS = Path(__file__).resolve().parent.parent / "assets"

# 出厂内置背景：配置里没有 bg_image 键时才用（用户点「默认」写过空串就不再回它）
BUILTIN_BG = "background.jpg"

# Windows 文件名非法字符。库名是用户自己起的，不能指望它天然合法。
BAD_CHARS = '\\/:*?"<>|'
MAX_NAME = 40
STAMP = "%Y%m%d_%H%M"


def _cfg():
    """延迟导入 store：store 反过来要用本模块，模块级互相 import 会成环。"""
    try:
        from . import store as S
        return S.load_settings()
    except Exception:
        return {}


def default_root() -> Path:
    """默认放在「文档」下。

    不选程序目录：打包后可能装在 Program Files，那里普通权限写不进去，
    用户会看到"导出失败"却不知道为什么。
    """
    return Path.home() / "Documents" / "嗷润吉联盟采集"


def root(cfg=None) -> Path:
    """数据根目录（自动创建）。读 `data_dir`，老配置的 `export_dir` 当根沿用。"""
    cfg = _cfg() if cfg is None else cfg
    for key in ("data_dir", "export_dir"):
        raw = str((cfg or {}).get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            if p.is_dir():
                return p
        except Exception:
            continue
    p = default_root()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def sub(kind: str, cfg=None) -> Path:
    """某一类产出的目录（自动创建）。"""
    p = root(cfg) / kind
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def safe(text, fallback: str = "") -> str:
    """清成合法文件名片段：去非法字符、压空白、剁到 40 字。"""
    s = str(text or "")
    for ch in BAD_CHARS:
        s = s.replace(ch, "")
    s = " ".join(s.split()).strip(" .")
    return s[:MAX_NAME] or fallback


def _file(head: str, kind: str, when=None, ext: str = "csv") -> str:
    when = when or datetime.now()
    parts = [p for p in (head, kind) if p]      # 缺段自动收缩，不留前导下划线
    return f"{'_'.join(parts)}_{when.strftime(STAMP)}.{ext}"


def damage_file(boss_book: str, roster_book: str, when=None) -> str:
    """`S19嗷润吉Q区_伤害统计_20261107_1530.csv`（BOSS 库在前，成员库紧跟）"""
    return _file(safe(boss_book) + safe(roster_book), DAMAGE, when)


def roster_file(roster_book: str, when=None) -> str:
    """`嗷润吉Q区_成员名单_20261107_1530.csv`"""
    return _file(safe(roster_book), ROSTER, when)


def boss_file(boss_book: str, when=None) -> str:
    """`S19_BOSS名单_20261107_1530.csv`"""
    return _file(safe(boss_book), BOSS, when)


def diag_file(when=None) -> str:
    """`诊断_20261107_153015.zip`（诊断要精确到秒，方便和日志对时间）"""
    when = when or datetime.now()
    return f"诊断_{when.strftime('%Y%m%d_%H%M%S')}.zip"


def log_file(cfg=None) -> Path:
    """`<根>/日志/操作日志.txt`"""
    return sub(LOG, cfg) / "操作日志.txt"


def ensure_all(cfg=None) -> Path:
    """把五类子目录都建好，返回数据根。设置页点"打开目录"时会用到。"""
    r = root(cfg)
    for k in KINDS:
        sub(k, cfg)
    return r
