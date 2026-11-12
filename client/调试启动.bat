@echo off
rem NIKKE 采集客户端（调试启动，保留控制台看报错）
cd /d "D:\Codex\Nikke\02_工位\10_数据管线\OCR采集\07-客户端"
if errorlevel 1 (
  echo 找不到客户端目录：D:\Codex\Nikke\02_工位\10_数据管线\OCR采集\07-客户端
  pause
  exit /b 1
)
net session >nul 2>&1
if errorlevel 1 (
  start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
"D:\Codex\依赖\venv-client\Scripts\python.exe" -m app
echo.
echo ---- 程序已退出，按任意键关闭 ----
pause >nul
