"""一条命令出成品：清干净 → 打包 → 附带使用说明。

    .venv\\Scripts\\python.exe 打包.py

产物在 `dist/` 一个 exe + 一份说明，拷给别人双击就能用，不需要装 Python。

为什么要有这个脚本而不是让人记 PyInstaller 命令：打包前必须 **先删 dist 和
build**，否则 PyInstaller 会复用上一轮的缓存，`--noconfirm` 又不会提醒——
改过的代码和旧图标可能一起留下来，测出来的东西和发出去的东西不是一回事。
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"
EXE = DIST / "NIKKE联盟采集.exe"

# 给拿到 exe 的人看的话。写在这里而不是单独维护一个文件，是为了让它跟着代码
# 一起改：界面改了而说明没改，比没有说明更误导。
README = """NIKKE 联盟名单采集  V2.0
================================================================

## 怎么用

1. 双击 NIKKE联盟采集.exe
2. 打开游戏，进到「联盟 → 队员」页面（或让它自己开过去）
3. 点【开始】

它会自己滚完整个列表、逐行认名字，采完报出人数。

## 它保证什么

- **不会跳步**：每步只走一行，走完立刻核对前后两屏的重合行。
- **不会盲点**：认不出页面就停手，绝不瞎猜。
- **人数对不上会说**：页面右上角写着 (32/32)，采到 34 会当场点名多出来的是哪几个。

## 文件都在哪

    输出\\操作日志.txt      出问题先看这个，一行一步都有记录
    输出\\区域.json          标定结果（换了分辨率会自动按比例换算）
    预设\\昵称列预设.json    内置标定，开箱就能用
    预设\\昵称库.json        采到过的成员（第一次跑是空的）
    预设\\同步.json          数据同步配置，默认关闭，不会联网

## 第一次用

内置预设是 1220×686 / 16:9 上标的。如果你的窗口比例也是 16:9，直接就能跑；
不是的话点一下【标定昵称列】，跟着提示在昵称那一列点两下即可。

## 采集时想看日志

采集期间日志区会自己撑高，并且**永远停在最新一条**（末行带淡底）。

- 想往回翻：直接滚轮往上滚，它会停住不跟，右下角显示「↓ N 条新」
- 想继续跟：点一下那个「↓ N 条新」，或者把滚动条拖回最下面
- 想更高：拖标题右侧的 ⇕，拖过之后就按你定的高度，不再自动改
- 要全量日志：点【打包日志】拿 zip，或者直接开 输出\\操作日志.txt

## 谁走了、谁是新来的

点【成员变动】按轮次倒着列：每轮采到几人、新增几个、没见到几个。

- 只有**完整采到列表末尾**的那一轮才算数；中途停下的那轮会整行压暗，不参与判定
- 要**连续 2 轮**完整采集都没见到，才会把人移出昵称库（移出会在收工那句里点名）
- 上一轮恰好 1 进 1 出时，会提示「可能是改名」——只提示，工具不会自动合并

## 注意

- 跑的时候别动鼠标，它会用到你的光标。
- 采集区被别的窗口挡住会拒绝开跑——把挡住的窗口挪开或最小化。
- 游戏窗口比例必须是 16:9，否则导航锚点会错位，工具会直接告诉你。
"""


def main() -> int:
    # 先删干净。顺序不能反：exe 还在跑的时候删不掉 dist，这里要明确报出来，
    # 而不是让 PyInstaller 用旧文件糊过去
    for d in (DIST, HERE / "build"):
        if d.exists():
            try:
                shutil.rmtree(d)
            except PermissionError:
                print(f"✗ 删不掉 {d} —— 先把正在运行的 NIKKE联盟采集.exe 关掉")
                return 1

    t0 = time.time()
    r = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "采集器.spec", "--noconfirm"],
        cwd=HERE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    if r.returncode or not EXE.exists():
        print("✗ 打包失败，最后 20 行：")
        print("\n".join((r.stdout or "").splitlines()[-20:]))
        return 1

    (DIST / "使用说明.txt").write_text(README, encoding="utf-8-sig")
    mb = EXE.stat().st_size / 1024 / 1024
    print(f"✓ 打包完成，用时 {time.time() - t0:.0f}s")
    print(f"  {EXE}  （{mb:.0f} MB）")
    print(f"  {DIST / '使用说明.txt'}")
    print("  把整个 dist 文件夹拷走即可；预设和输出会在首次运行时自动生成在 exe 旁边。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
