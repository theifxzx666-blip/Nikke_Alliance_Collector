# 嗷润吉·联盟采集

NIKKE（胜利女神：新的希望）联盟数据采集桌面客户端。通过窗口截图与 OCR 识别联盟成员名单、联盟突袭（HARD）伤害记录，替代人工逐行抄录。

当前版本：**v1.22 Beta**

## 功能

- **联盟成员采集**：从大厅自动导航至联盟成员页，滚动截图、跨屏拼接、昵称库归位，支持多套成员库（一盟一库）。
- **联盟突袭伤害采集**：自动进入联盟记录页，按行采集伤害、BOSS、剩余血量百分比，支持多期 BOSS 库。
- **并排与跟随**：客户端停靠游戏窗口侧边，跟随窗口移动与缩放；支持一键将游戏调整为 16:9 建议尺寸。
- **多库管理**：成员库与 BOSS 库分离存储，可在界面内新建、改名、删除、导出。
- **数据导出**：按「库名 + 时间戳」自动命名，分目录存放。
- **可配置快捷键**：默认 F7 连接 / F3 名单 / F4 伤害 / F8 导出 / F9 跟随 / F10 设置。

## 环境要求

- Windows 10 / 11，x64
- 游戏以 16:9 窗口运行（1280×720 及以上）
- 客户端需**管理员权限**运行：截图与鼠标注入要求与游戏窗口同级权限

## 运行

### 使用发行版

运行 `NIKKE联盟采集.exe`（单文件，无需安装），首次启动会请求管理员权限。
首次运行会在 exe 旁边生成 `预设/` 与 `NOTICE.txt` —— 用户配置与许可声明都留在本地，
数据本身写到「文档\嗷润吉联盟采集」。

### 从源码运行

```bat
D:\Codex\依赖\venv-client\Scripts\python.exe -m app
```

依赖：PySide6、RapidOCR、onnxruntime、OpenCV、NumPy、Pillow。

### 打包

```bat
python build.py            :: 单文件，产物 dist/NIKKE联盟采集.exe
python build.py --onedir   :: 目录版，产物 dist/NIKKE联盟采集/（调试用，免解压）
```

打包前会校验 `发布预设/` 中不含任何成员数据。单文件形态启动时要先把内置依赖
解压到临时目录，首次启动比目录版慢几秒。

## 使用流程

1. 启动客户端，确认顶部状态条显示「已连接」；未连接时按 F7 重查。
2. **名单页**：选择或新建成员库，按 F3 开始采集。客户端会自动导航至成员页并连续滚采。
3. **伤害页**：选择成员库与 BOSS 库，按 F4 开始采集，客户端自动进入联盟记录页并逐屏拼接。
4. 按 F8 导出当前页数据，文件落在设置中的「数据目录」。

## 目录结构

```
05-工具/            采集判据与识别引擎（与界面无关）
  alliance_ocr.py   名单采集状态机（RosterMixin）
  raid_nav.py       页面识别与导航
  raid_capture.py   伤害滚采与拼接
  booklib.py        多套库的存取与迁移
07-客户端/          桌面客户端
  app/              PySide6 界面层
  assets/           图标、收款码、内置背景
  发布预设/          发行版数据模板（不含任何成员数据）
  build.py          PyInstaller 打包脚本
核心目录/agent/      开发记录与判据说明
```

## 数据存放

运行数据（预设、库、配置、许可）放在 `%APPDATA%\OrangeHelper\`，**exe 旁边不留任何文件**：

```
预设\      导出设置.json / 昵称列预设.json / 库索引.json / 成员库\ / BOSS库\
输出\      标定结果与运行缓存
NOTICE.txt 许可声明
```

> 便携模式：如果 exe 旁边**已经存在** `预设\` 目录，就用它而不用 `%APPDATA%` ——
> 想"整个文件夹拷走"时先建一个空 `预设\` 即可。

导出的 CSV 默认落在 `文档\嗷润吉联盟采集`，可在设置中修改：

```
伤害统计/   S19嗷润吉Q区_伤害统计_20261107_1530.csv
成员名单/   嗷润吉Q区_成员名单_20261107_1530.csv
BOSS名单/   S19_BOSS名单_20261107_1530.csv
日志/       操作日志.txt
诊断/       诊断_20261107_153015.zip
```

## 测试

```bat
cd 05-工具 && .venv\Scripts\python.exe roottest.py    :: 昵称库判据
cd 05-工具 && .venv\Scripts\python.exe navtest.py     :: 页面识别与导航
cd 05-工具 && .venv\Scripts\python.exe hardtest.py    :: 伤害解析与去重
cd 05-工具 && .venv\Scripts\python.exe booktest.py    :: 多库存储
cd 07-客户端 && python apptest.py                     :: 客户端离线自检
cd 07-客户端 && python pathtest.py                    :: 存储与命名
```

## 声明

本项目为个人非商业作品。未经作者书面许可，禁止以任何方式（含使用人工智能模型对程序、字节码或运行过程进行分析、训练、推理）逆向、提取或再分发源代码。完整条款见 [NOTICE.txt](07-客户端/NOTICE.txt)。

采集判据与「并排 + 自动导航」的交互参考了阿卡团队（Arcana）的练度采集工具，感谢其对 NIKKE 社区的贡献。

友链：<https://pinkuro.top:9984/arcana/web?k=>

## 第三方组件

PySide6 (LGPL v3)、RapidOCR / ONNX Runtime (Apache 2.0)、OpenCV (Apache 2.0)、NumPy (BSD 3-Clause)、Pillow (HPND)。
