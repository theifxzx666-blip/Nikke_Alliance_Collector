# -*- mode: python ; coding: utf-8 -*-
"""把采集器打成单文件 exe（双击即用，不需要 uv 虚拟环境）。

打包只要一条命令：

    .venv\\Scripts\\python.exe -m PyInstaller 采集器.spec --noconfirm

产物：`dist/NIKKE联盟采集.exe`

两个不能省的细节：

1. **RapidOCR 的模型必须打进去。** 它默认从网络下载 onnx 模型，第一次运行时
   没网就直接崩；而 `rapidocr/models/*.onnx` 不会被静态分析发现（是运行时按
   名字拼路径读的），必须 `collect_data_files` 显式收进来。三份模型合计约
   15MB：det（找字）+ rec（认字）+ cls（判断要不要转 180°）。

2. **数据目录不是 exe 内部。** 主程序 `_app_dir()` 在冻结态返回
   `sys.executable` 的父目录，所以 `预设/`、`输出/` 都落在 exe 旁边，
   跟着 exe 走、卸载不丢数据。这里不需要往包里塞任何用户数据。

排除项是为了体积：这套工具只做截图 + OCR + tkinter，matplotlib/pandas/
scipy/torch 一个都用不上，但 onnxruntime 和 opencv 会顺带把它们的钩子带进来。
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# RapidOCR 的模型 + yaml 配置（运行时按文件名拼路径读，静态分析发现不了）
datas = collect_data_files("rapidocr")

# 内置预设：标定结果 + 同步配置模板。主程序 `_seed_presets()` 会在首次启动时
# 把它们播到 exe 旁边——用户开箱就能直接滚采，不用先标一次。
# **昵称库不能打包**：那是用户数据，打进去会让别人拿到你的成员名单，
# 而且首次播种会把它当成"内置值"覆盖掉用户的空库。
datas += [("预设/昵称列预设.json", "预设")]
datas += [("预设/同步.json", "预设")]
# 盟战 BOSS 表：一期 5 个固定 BOSS，用来把 OCR 出来的名字归一（`本铁匠`→`铁匠`）
datas += [("预设/盟战boss表.json", "预设")]
# 外部识别后端的配置模板（默认 enabled=false，用户自己填 cmd 才生效）
datas += [("预设/识别后端.json", "预设")]

# RapidOCR 的引擎/后端是运行时挑的，子模块也要显式收
hiddenimports = collect_submodules("rapidocr") + [
    "PIL._tkinter_finder",      # Pillow + tkinter 的老坑：少了它 ImageTk 会崩
    "sync_client",              # 同目录模块，跟着 import 走但显式写着更稳
    "ui_kit",
    # 伤害记录窗口是【伤害记录】按钮里延迟 import 的，静态分析看不到，必须显式声明，
    # 否则打出来的 exe 点按钮才报 ModuleNotFoundError。
    "raid_window", "raid_core", "raid_ocr", "raid_cli", "raid_nav",
]

a = Analysis(
    ["alliance_ocr.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib", "pandas", "scipy", "torch", "torchvision",
        "pytest", "IPython", "notebook", "setuptools._distutils",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NIKKE联盟采集",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压 onnxruntime 的 dll 会压坏，别开
    icon="图标.ico",
    console=False,      # 无控制台窗口；程序自己把 stderr 接到日志文件
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
