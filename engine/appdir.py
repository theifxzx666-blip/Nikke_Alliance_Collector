# -*- coding: utf-8 -*-
"""程序数据目录：运行时文件该放哪。

规范（与 ArcanaHelper 一致）：

    %APPDATA%\\OrangeHelper\\
    ├── 预设\\              导出设置 / 昵称列预设 / 库索引 / 成员库 / BOSS库
    ├── 输出\\              旧工具的 区域.json 与运行缓存
    └── NOTICE.txt

目录名用英文 `OrangeHelper`（与 `NIKKE联盟采集.exe` 这个中文程序名对应），
`%APPDATA%` 下用英文名可以避免某些工具按 ANSI 代码页解析路径时乱码。

**为什么不放 exe 旁边**：单文件版每次启动都会在 exe 同级解压出几百个文件，
再把 `预设/`、`NOTICE.txt` 撒在那里 —— 用户从桌面双击一次，桌面就多三个东西；
放在 `Program Files` 时更是直接写不进去。放到用户数据目录后，exe 可以放在任何
地方（桌面 / U 盘 / Program Files），旁边永远是干净的。

**便携模式**：exe 旁边**已经**存在 `预设/` 时仍用它。这样老用户升级后数据和
配置原地不动，想"整文件夹拷走"的人也只要先建一个空 `预设/`。

为什么单独一个文件：`alliance_ocr` 和 `booklib` 都需要它，而这两个模块互相
import（booklib 用 alliance_ocr 的库判据，alliance_ocr 用 booklib 的路径），
谁也不能去 import 对方拿这个函数 —— 抽出来两边用同一份，路径不会分家。
"""
import os
import sys
from pathlib import Path

APP_NAME = "OrangeHelper"


def app_dir(frozen=None, exe=None) -> Path:
    """程序数据目录。两个参数只为自检能造场景，正常调用都不传。"""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if not frozen:
        return Path(__file__).resolve().parent
    exe_dir = Path(exe or sys.executable).resolve().parent
    if (exe_dir / "预设").is_dir():
        return exe_dir                     # 便携模式：用户自己要"文件夹带走"
    for env in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        d = Path(base) / APP_NAME
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            continue
    # 环境变量都没有（极简系统）：退到用户主目录，至少比 exe 旁边可写
    d = Path.home() / APP_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d
