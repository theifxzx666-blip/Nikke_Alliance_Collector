"""外部识别 CLI 桥：把「认不准的那一张裁图」交给外部命令去认。

为什么要有这一层
================
本地 RapidOCR 用的是 PP-OCRv6 **small** 模型，徽标那种 14px 字高的小字，
稳定极限就在那儿（详见 README 第 4 条）。阿卡（ArcanaHelper）之所以稳，
是因为它把文字全部上传云端识别 —— 本地做不到，但可以**接一个更强的后端来兜底**。

设计原则
--------
1. **只在存疑时调用**。主链路仍是本地识别，票数够了就不碰外部命令 ——
   一次滚采几十行，如果每行都调外部，耗时和成本都不划算。
2. **命令模板化，后端无关**。Umi-OCR、PaddleOCR、本地 VLM、云 VLM 都只是
   「输入一张 PNG，输出一行文本」，配置里换 `cmd` 就行，代码不用改。
3. **失败必须静默降级**。外部命令超时/报错/输出垃圾，一律当作「没认出来」，
   绝不把异常抛进采集循环。

配置：`预设/识别后端.json`
-------------------------
    {
      "enabled": true,
      "cmd": ["D:/Tools/Umi-OCR/UmiOCR.exe", "--path", "{png}"],
      "timeout": 15
    }

`cmd` 里任意一项可以含 `{png}` 占位符，运行时替换成临时 PNG 的绝对路径。
程序把 stdout 当识别结果（多行会拼成一行）。`{png}` 缺省时自动追加到末尾。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

CFG_NAME = "识别后端.json"

# 进程内缓存：同一张图不重复调外部命令（滚采时同一行会被读多次）
_CACHE: dict[str, str] = {}
_CFG_CACHE: dict = {}
_FAILS = 0            # 连续失败次数，达阈值就本次运行内停用，避免每行都等超时
_FAIL_LIMIT = 3


def cfg_path(app_dir: Path) -> Path:
    return Path(app_dir) / "预设" / CFG_NAME


def load_cfg(app_dir: Path) -> dict:
    """读配置。文件不存在/坏掉都返回 {}（= 未启用），不抛异常。"""
    global _CFG_CACHE
    p = cfg_path(app_dir)
    if not p.exists():
        return {}
    try:
        mtime = p.stat().st_mtime
        if _CFG_CACHE.get("_mtime") == mtime:
            return _CFG_CACHE
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        data["_mtime"] = mtime
        _CFG_CACHE = data
        return data
    except Exception:
        return {}


def enabled(app_dir: Path) -> bool:
    cfg = load_cfg(app_dir)
    return bool(cfg.get("enabled")) and bool(cfg.get("cmd")) and _FAILS < _FAIL_LIMIT


def resolve_cmd(cfg: dict, png: Path) -> list:
    """把 `{png}` 替换成实际路径；没有占位符就追加到末尾。"""
    cmd = cfg.get("cmd") or []
    if isinstance(cmd, str):
        cmd = [cmd]
    cmd = [str(x) for x in cmd]
    hit = False
    out = []
    for part in cmd:
        if "{png}" in part:
            hit = True
            out.append(part.replace("{png}", str(png)))
        else:
            out.append(part)
    if not hit:
        out.append(str(png))
    return out


def _run(cfg: dict, png: Path) -> str:
    cmd = resolve_cmd(cfg, png)
    exe = cmd[0]
    # 绝对路径的 exe 不存在就直接认命（不抛），相对命令交给 PATH
    if (os.path.isabs(exe) or "/" in exe or "\\" in exe) and not Path(exe).exists():
        return ""
    timeout = float(cfg.get("timeout") or 15)
    flags = 0
    if os.name == "nt":
        # 不弹黑框
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           creationflags=flags,
                           cwd=str(cfg.get("cwd") or png.parent))
    except Exception:
        return ""
    txt = ""
    for stream in (r.stdout, r.stderr):
        if stream:
            try:
                txt += stream.decode("utf-8", "ignore")
            except Exception:
                txt += stream.decode("mbcs", "ignore")
    return txt.strip()


def read_text(app_dir: Path, png_bytes: bytes, suffix: str = ".png") -> str:
    """把一张图的字节交给外部命令，返回它认出的文本（不认识就返回 ""）。

    用临时文件而不是 stdin：绝大多数 OCR CLI 只接受路径参数。
    """
    global _FAILS
    cfg = load_cfg(app_dir)
    if not (cfg.get("enabled") and cfg.get("cmd")) or _FAILS >= _FAIL_LIMIT:
        return ""
    key = hashlib.sha1(png_bytes).hexdigest()
    if key in _CACHE:
        return _CACHE[key]

    tmpdir = cfg.get("tmpdir") or tempfile.gettempdir()
    fd, name = tempfile.mkstemp(suffix=suffix, dir=tmpdir, prefix="raidcli_")
    os.close(fd)
    p = Path(name)
    try:
        p.write_bytes(png_bytes)
        txt = _run(cfg, p)
    finally:
        try:
            p.unlink()
        except Exception:
            pass

    if txt:
        _FAILS = 0
        # 多行输出拼成一行（BOSS 名/等级都在同一行里）
        flat = " ".join(line.strip() for line in txt.splitlines() if line.strip())
        _CACHE[key] = flat
        return flat
    _FAILS += 1
    return ""


def reset():
    """清缓存与失败计数（切换配置/重跑时用）。"""
    global _FAILS
    _CACHE.clear()
    _CFG_CACHE.clear()
    _FAILS = 0


def probe(app_dir: Path) -> dict:
    """自检：命令是否找得到、能不能跑通。给窗口里的【测后端】按钮用。"""
    cfg = load_cfg(app_dir)
    info = {"path": str(cfg_path(app_dir)), "exists": cfg_path(app_dir).exists(),
            "enabled": bool(cfg.get("enabled")), "cmd": cfg.get("cmd"),
            "found": False, "note": ""}
    if not cfg.get("cmd"):
        info["note"] = "没有配置 cmd"
        return info
    cmd = cfg.get("cmd")
    exe = (cmd if isinstance(cmd, list) else [cmd])[0]
    exe = str(exe)
    if os.path.isabs(exe):
        info["found"] = Path(exe).exists()
    else:
        info["found"] = bool(shutil.which(exe))
    if not info["found"]:
        info["note"] = f"找不到可执行文件：{exe}"
    return info
