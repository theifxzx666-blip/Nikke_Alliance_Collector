# -*- coding: utf-8 -*-
"""正式客户端打包。

    python build.py              # 单文件 exe（发行用，发出去只有一个文件）
    python build.py --onedir     # 目录版（开发调试用，启动快、免解压）

两种形态共用 `NIKKE联盟采集.spec`，靠环境变量 `NIKKE_ONEFILE` 切换。
裁剪（ffmpeg 解码器 / Qt Quick）已经写进 spec 的清单过滤里 —— 单文件形态
打完只剩一个 exe，外面没东西可删。

用法：
    D:\\Codex\\依赖\\venv-client\\Scripts\\python.exe build.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "NIKKE联盟采集"

# %APPDATA% 下的目录名：从 appdir 取，不在这里抄第二份（抄了必然漂移）
sys.path.insert(0, str(HERE.parent / "engine"))
try:
    from appdir import APP_NAME as APP_FOLDER
except Exception:                      # 只在 engine 缺失时兜底
    APP_FOLDER = "OrangeHelper"
SPEC = HERE / f"{NAME}.spec"
DIST_DIR = HERE / "dist"
DIST = DIST_DIR / NAME                      # 目录版产物目录
EXE = DIST_DIR / f"{NAME}.exe"              # 单文件产物

ONEFILE = "--onedir" not in sys.argv

# 发行版数据源：`发布预设/` 是**唯一**来源，与开发机上的用户数据（engine/预设）
# 彻底隔离。开发目录里有真实联盟名单，直接打包会泄漏给使用者。
RELEASE = HERE / "发布预设"

PRESETS = ("昵称列预设.json", "困难框选.json", "盟战boss表.json", "导出设置.json")
PRESET_DIRS = ("成员库", "BOSS库")
PRESET_FILES = ("库索引.json",)


def size_mb(p: Path) -> float:
    if p.is_file():
        return p.stat().st_size / 1024 / 1024
    total = 0
    for f in p.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total / 1024 / 1024


def build():
    env = dict(os.environ)
    env["NIKKE_ONEFILE"] = "1" if ONEFILE else "0"
    cmd = [
        str(Path(sys.executable).with_name("pyinstaller.exe")),
        "--noconfirm", "--clean",
        "--distpath", str(DIST_DIR),
        "--workpath", str(HERE / "build"),
        str(SPEC),
    ]
    print(f"打包中（{'单文件' if ONEFILE else '目录版'}）… 约 90~200 s")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-3000:])
        raise SystemExit(f"PyInstaller 失败，退出码 {r.returncode}")
    out = EXE if ONEFILE else DIST
    print(f"打包完成 {time.time() - t0:.0f}s · {size_mb(out):.0f} MB")


def copy_presets():
    """目录版：把发行预设铺到 exe 同级。

    单文件版不用这一步 —— 预设已经在 exe 里，`_seed_presets()` 首次运行会把
    缺失的文件写到 exe 旁边（用户数据必须落在 exe 旁边，不能留在临时目录）。
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


def _selftest(exe: Path, cwd: Path, min_presets: int, marker: bytes = b"",
              env: dict | None = None) -> bool:
    """跑一次 `--shot`：出图 + 退出码 0，才能说明 Qt/OCR 都打包进去了。

    `marker` 给单文件版用：外面没有 `预设/` 可数，只能在 exe 字节里确认
    随包文件确实进去了（否则打出来的是个能启动但没预设的空壳）。
    `env` 用来把 `APPDATA` 指到临时目录 —— 单文件版把数据放在用户目录，
    不隔离的话自检会往真实 `%APPDATA%` 里写东西。

    返回 True 表示**真的跑起来了**（False = 退化成静态校验）。
    """
    out = HERE / "dist" / "_selftest.png"
    out.unlink(missing_ok=True)
    print("自检：运行 exe --shot …")
    t0 = time.time()
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    try:
        r = subprocess.run([str(exe), "--shot", str(out)],
                           cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300,
                           env=run_env)
    except OSError as e:
        if getattr(e, "winerror", None) != 740:
            raise
        print("exe 需要管理员权限（清单生效）—— 改用静态校验")
        raw = exe.read_bytes()
        has_admin = b"requireAdministrator" in raw
        presets = sorted(x.name for x in (cwd / "预设").glob("*.json"))
        print(f"清单 requireAdministrator：{'有' if has_admin else '缺'}")
        if min_presets:
            print(f"预设 {len(presets)} 个：{'、'.join(presets) or '无'}")
        # 单文件版外面没有 预设/ 可数，改成在 exe 字节里找随包文件的文件名
        embedded = (marker in raw) if marker else True
        if marker:
            print(f"内嵌预设标记 {'库索引.json'}：{'有' if embedded else '缺'}")
        print(f"体积 {size_mb(exe):.0f} MB")
        # 136 MB 是单文件的实际体积（PyInstaller 的 CArchive 带压缩）；
        # 目录版解压后约 310 MB。低于 100 基本就是打漏了。
        bad = not has_admin or not embedded or size_mb(exe) < 100
        if min_presets and len(presets) < min_presets:
            bad = True
        if bad:
            raise SystemExit("静态校验未通过")
        print("静态校验通过（动态自检请在管理员终端里重跑 build.py）")
        return False

    ok = out.exists() and out.stat().st_size > 5000
    print(f"退出码 {r.returncode} · 出图 {'有' if ok else '无'} "
          f"({out.stat().st_size // 1024 if out.exists() else 0} KB) · "
          f"耗时 {time.time() - t0:.1f}s")
    if not ok:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        raise SystemExit("自检失败：exe 没有正常出图")
    print("自检通过")
    return True


def verify():
    """目录版：直接跑产物目录里的 exe。"""
    _selftest(DIST / f"{NAME}.exe", DIST, 3)


def verify_onefile():
    """单文件版：在**空目录**里跑，确认两件事。

    1. 预设与许可播到了 `%APPDATA%\\OrangeHelper`，不是撒在 exe 旁边；
    2. exe 所在目录跑完之后**仍然只有 exe 一个文件**。

    不能直接在 dist/ 里跑 —— 那里已经有一份 exe 和 `预设/`，播种走没走通
    就看不出来了。`APPDATA` 指到临时目录，避免污染真实的用户目录。
    """
    tmp = DIST_DIR / "_onefile_test"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    run_dir = tmp / "exe"
    data_dir = tmp / "appdata"
    run_dir.mkdir(parents=True)
    data_dir.mkdir()
    exe = run_dir / f"{NAME}.exe"
    shutil.copy2(EXE, exe)
    try:
        ran = _selftest(exe, run_dir, 0, marker="库索引.json".encode("utf-8"),
                        env={"APPDATA": str(data_dir)})
        if not ran:
            print("（未提权，动态播种检查跳过；预设已按内嵌标记确认在包里）")
            return
        appdata = data_dir / APP_FOLDER
        seeded = sorted(x.name for x in (appdata / "预设").rglob("*") if x.is_file())
        print(f"首次运行播种 {len(seeded)} 个文件："
              f"{'、'.join(seeded[:6])}{' …' if len(seeded) > 6 else ''}")
        # 发布预设是 32 个（4 个顶层 json + 库索引 + 26 套 BOSS + 示例成员库）
        if len(seeded) < 30:
            raise SystemExit(f"播种不完整：只有 {len(seeded)} 个，应有 32 个")
        if not (appdata / "NOTICE.txt").exists():
            raise SystemExit("许可声明没有播到用户数据目录")
        left = sorted(p.name for p in run_dir.iterdir() if p.name != exe.name)
        if left:
            raise SystemExit(f"exe 旁边不该有东西，实际多了：{'、'.join(left)}")
        print(f"数据落在 {appdata}")
        print("exe 所在目录跑完仍然只有它自己")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    check_release_presets()
    if ONEFILE:
        try:
            EXE.unlink(missing_ok=True)
        except PermissionError:
            # Windows 上运行中的 exe 不能被删除或覆盖，报 WinError 5
            raise SystemExit(f"{EXE.name} 正在运行 —— 先关掉客户端再打包")
        if DIST.exists():
            shutil.rmtree(DIST, ignore_errors=True)   # 别让旧目录版混在一起
    build()
    if ONEFILE:
        verify_onefile()
        print(f"\n产物：{EXE}")
        print(f"体积：{size_mb(EXE):.0f} MB（单文件，发给别人只发这一个）")
    else:
        copy_presets()
        verify()
        print(f"\n产物：{DIST}")
        print(f"体积：{size_mb(DIST):.0f} MB")


if __name__ == "__main__":
    main()
