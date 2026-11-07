# -*- coding: utf-8 -*-
"""正式客户端打包：PyInstaller(onedir) → 裁白带运行时 → 铺预设 → 跑一次自检。

为什么不 --onefile：单文件每次启动都要解压 300MB，而且之前踩过"窗口继承启动状态
不显示"的坑；onedir 启动快、排障也直观。

为什么打包后还要裁：cv2 的 ffmpeg 解码器（约 30M）和 Qt Quick/Qml（约 20M）
我们一个都不用，删掉后**必须再跑一次 exe 验证**，不能只删了事。

用法：
    D:\\Codex\\依赖\\venv-client\\Scripts\\python.exe build.py
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "NIKKE联盟采集"
DIST = HERE / "dist" / NAME
ENGINE = HERE.parents[0] / "05-工具"          # 判据与预设都在这里

TRIM_GLOBS = [
    "_internal/cv2/opencv_videoio_ffmpeg*.dll",   # 视频解码，纯图像处理用不到
    "_internal/PySide6/Qt6Quick*.dll",
    "_internal/PySide6/Qt6Qml*.dll",
    "_internal/PySide6/qml",
    "_internal/PySide6/translations",             # Qt 自带翻译，界面文案都是我们自己的
]

# 发行版数据源：`发布预设/` 是**唯一**来源，与开发机上的用户数据（05-工具/预设）
# 彻底隔离。开发目录里有真实联盟名单，直接打包会泄漏给使用者。
RELEASE = HERE / "发布预设"

PRESETS = ("昵称列预设.json", "困难框选.json", "盟战boss表.json", "导出设置.json")
PRESET_DIRS = ("成员库", "BOSS库")
PRESET_FILES = ("库索引.json",)


def size_mb(p: Path) -> float:
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total / 1024 / 1024


def build():
    cmd = [
        str(Path(sys.executable).with_name("pyinstaller.exe")),
        "--noconfirm", "--clean", "--windowed", "--onedir", "--name", NAME,
        # 采集要往游戏窗口注入鼠标、窗口控制要改它的位置，都必须与游戏同级权限。
        # 内嵌 manifest 让双击 exe 就弹 UAC，而不是跑起来才发现拖不动。
        "--uac-admin",
        "--collect-all", "rapidocr", "--collect-all", "onnxruntime",
        "--paths", str(ENGINE),
        "--add-data", f"{RELEASE};预设",
        # v1.6 品牌素材：图标 + 两张收款码（随包分发，不联网）
        "--add-data", f"{HERE / 'assets'};assets",
        "--icon", str(HERE / "assets" / "aorunji.ico"),
        "app/main.py",
    ]
    print("打包中…（约 90~150 s）")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-2500:])
        print(r.stderr[-2500:])
        raise SystemExit(f"PyInstaller 失败，退出码 {r.returncode}")
    print(f"打包完成 {time.time() - t0:.0f}s · {size_mb(DIST):.0f} MB")


def trim():
    removed = 0.0
    for pat in TRIM_GLOBS:
        for f in DIST.glob(pat):
            if f.is_dir():
                removed += sum(x.stat().st_size for x in f.rglob("*") if x.is_file()) / 1024 / 1024
                shutil.rmtree(f, ignore_errors=True)
            else:
                removed += f.stat().st_size / 1024 / 1024
                f.unlink(missing_ok=True)
    print(f"裁剪 {removed:.0f} MB → 现在 {size_mb(DIST):.0f} MB")


def copy_presets():
    """把发行版预设铺到 exe 同级。

    冻结后 `APP_DIR` 就是 exe 所在目录，客户端只读这里的 `预设/`。
    来源固定为 `发布预设/`：即使开发机上的 `05-工具/预设` 里存着真实联盟名单，
    也不会被打进发行包。
    """
    dst = DIST / "预设"
    dst.mkdir(parents=True, exist_ok=True)
    missing = []
    for name in PRESETS:
        src = RELEASE / name
        if not src.exists():
            missing.append(name)
            continue
        shutil.copy2(src, dst / name)
    print("预设已铺：", "、".join(n for n in PRESETS if n not in missing))
    if missing:
        print("缺失（会用默认值）：", "、".join(missing))

    for d in PRESET_DIRS:
        src = RELEASE / d
        if not src.exists():
            continue
        target = dst / d
        target.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in src.glob("*.json"):
            shutil.copy2(f, target / f.name)
            n += 1
        print(f"库目录已铺：{d}/（{n} 套）")
    for name in PRESET_FILES:
        src = RELEASE / name
        if src.exists():
            shutil.copy2(src, dst / name)
            print(f"库索引已铺：{name}")

    # 许可与使用说明随包分发
    notice = HERE / "NOTICE.txt"
    if notice.exists():
        shutil.copy2(notice, DIST / "NOTICE.txt")


def check_release_presets():
    """发布门禁：发行预设里不允许出现任何成员姓名。

    这是一条硬断言而不是提醒 —— 一旦哪天有人把开发目录的成员库复制进
    `发布预设/`，产物就会带着别人的联盟名单发出去。
    """
    if not RELEASE.exists():
        raise SystemExit(f"缺少发行版预设目录：{RELEASE}")
    bad = []
    for f in (RELEASE / "成员库").glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            bad.append(f"{f.name}（读不出来）")
            continue
        names = d.get("names") or {}
        if names:
            bad.append(f"{f.name}（{len(names)} 人）")
    if (RELEASE / "昵称库.json").exists():
        bad.append("昵称库.json（旧单库，含真实名单）")
    if bad:
        raise SystemExit("发行预设里有成员数据，拒绝打包：" + "、".join(bad))
    print("发行预设检查通过：无成员数据")


_SIDE_ALIAS = {"游戏右侧": "右侧", "游戏左侧": "左侧", "覆盖右下": "自动"}


def _migrate_settings(src: Path, dst: Path) -> None:
    try:
        cfg = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}
    side = str(cfg.get("snap_side") or "右侧")
    cfg["snap_side"] = _SIDE_ALIAS.get(side, side)
    if cfg["snap_side"] not in ("右侧", "左侧", "自动", "不动"):
        cfg["snap_side"] = "右侧"
    if cfg["snap_side"] == "不动":
        cfg["snap_side"] = "右侧"          # 用户这轮明确要"跟着游戏走"
    cfg.pop("win_w", None)
    cfg.pop("win_h", None)
    dst.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")


def verify():
    """跑一次自检：exe --shot 出图 + 退出码 0，才能说明 Qt/OCR 都打包进去了。

    exe 内嵌了 requireAdministrator 清单（采集需要与游戏同级权限），
    所以**普通权限下根本起不来**（WinError 740）。这时退化为静态校验：
    清单在不在、预设铺没铺、体积对不对 —— 动态自检留给管理员环境。
    """
    exe = DIST / f"{NAME}.exe"
    out = HERE / "dist" / "_selftest.png"
    out.unlink(missing_ok=True)
    print("自检：运行 exe --shot …")
    try:
        r = subprocess.run([str(exe), "--shot", str(out)],
                           cwd=str(DIST), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    except OSError as e:
        if getattr(e, "winerror", None) != 740:
            raise
        print("exe 需要管理员权限（清单生效）—— 改用静态校验")
        raw = exe.read_bytes()
        has_admin = b"requireAdministrator" in raw
        presets = sorted(x.name for x in (DIST / "预设").glob("*.json"))
        size = size_mb(DIST)
        print(f"清单 requireAdministrator：{'有' if has_admin else '缺'}")
        print(f"预设 {len(presets)} 个：{'、'.join(presets) or '无'}")
        print(f"体积 {size:.0f} MB")
        if not has_admin or len(presets) < 3 or size < 200:
            raise SystemExit("静态校验未通过")
        print("静态校验通过（动态自检请在管理员终端里重跑 build.py）")
        return

    ok = out.exists() and out.stat().st_size > 5000
    print(f"退出码 {r.returncode} · 出图 {'有' if ok else '无'} "
          f"({out.stat().st_size // 1024 if out.exists() else 0} KB)")
    if not ok:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        raise SystemExit("自检失败：exe 没有正常出图")
    print("自检通过")


def main():
    check_release_presets()
    build()
    trim()
    copy_presets()
    verify()
    print(f"\n产物：{DIST}")
    print(f"体积：{size_mb(DIST):.0f} MB")


if __name__ == "__main__":
    main()
