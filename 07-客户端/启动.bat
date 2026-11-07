@echo off
rem NIKKE 采集客户端（静默启动）
rem 必须管理员：游戏以管理员运行时，不改权限既动不了它的窗口，也拖不动鼠标
cd /d "D:\Codex\Nikke\Nikke_Skills\OCR采集\07-客户端"
if errorlevel 1 (
  echo 找不到客户端目录：D:\Codex\Nikke\Nikke_Skills\OCR采集\07-客户端
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
start "" "D:\Codex\依赖\venv-client\Scripts\pythonw.exe" -m app
