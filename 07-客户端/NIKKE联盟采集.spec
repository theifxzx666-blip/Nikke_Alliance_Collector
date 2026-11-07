# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 配置。两种形态共用这一份，靠环境变量切换：

    NIKKE_ONEFILE=1   单文件（发行用：发出去只有一个 exe）
    不设              目录版（开发调试用：改完直接跑，不用等解压）

为什么要在这里裁而不是打完再删：单文件形态把 `_internal` 整个塞进 exe 里，
打完之后外面什么都看不到，也没法删。所以只在 Analysis 出来的清单里摘掉。

裁掉的东西（都在 onedir 上验证过可删）：
    opencv_videoio_ffmpeg        只做静态图像处理，视频解码器用不到（约 30 MB）
    Qt6Quick / Qt6Qml / qml / translations  Quick 界面与 Qt 自带翻译（约 20 MB）
"""
import os
import re

from PyInstaller.utils.hooks import collect_all

HERE = os.path.dirname(os.path.abspath(SPEC))
ONE_FILE = os.environ.get("NIKKE_ONEFILE") == "1"
# 调试用：不打 admin 清单，双击不弹 UAC，能直接 `--shot` 出图核版式。
# 这种包不能发 —— 采集要向游戏窗口注入鼠标，没有同级权限必失败。
NOADMIN = os.environ.get("NIKKE_NOADMIN") == "1"
NAME = "NIKKE联盟采集" + ("_dev" if NOADMIN else "")
ENGINE = os.path.join(os.path.dirname(HERE), "05-工具")

_DROP = re.compile(
    r"(opencv_videoio_ffmpeg|Qt6Quick|Qt6Qml|[\\/]qml[\\/]|[\\/]translations[\\/])",
    re.I,
)

datas = [
    (os.path.join(HERE, "发布预设"), "预设"),
    (os.path.join(HERE, "assets"), "assets"),
    # 单文件发行版只有一个 exe，许可声明必须进包（首次运行会播到 exe 旁边）
    (os.path.join(HERE, "NOTICE.txt"), "."),
]
binaries = []
hiddenimports = []
for pkg in ("rapidocr", "onnxruntime"):
    tmp = collect_all(pkg)
    datas += tmp[0]
    binaries += tmp[1]
    hiddenimports += tmp[2]

a = Analysis(
    [os.path.join(HERE, "app", "main.py")],
    pathex=[ENGINE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "pydoc_data"],
    noarchive=False,
    optimize=0,
)

# onnxruntime 的 examples/transformers 目录混在 collect_all 结果里，几百个 py，
# 运行时一次都不会被 import，白白拖慢启动时的解压。
a.binaries = [x for x in a.binaries if not _DROP.search(x[0])]
a.datas = [x for x in a.datas
           if not _DROP.search(x[0])
           and "onnxruntime/transformers" not in x[0].replace("\\", "/")]

pyz = PYZ(a.pure)

COMMON = dict(
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # onnxruntime 的 dll 被 UPX 压过会加载失败
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=not NOADMIN,      # 要向游戏窗口注入鼠标、改它的位置，必须同级权限
    icon=[os.path.join(HERE, "assets", "aorunji.ico")],
)

if ONE_FILE:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **COMMON)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **COMMON)
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
                   upx_exclude=[], name=NAME)
