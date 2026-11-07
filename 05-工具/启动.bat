@echo off
rem Launch NIKKE alliance-member OCR tool
rem NOTE: keep this file pure ASCII. cmd.exe reads .bat as ANSI(GBK) on a
rem Chinese Windows, so any UTF-8 Chinese text here breaks the whole script.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto NOENV

start "" ".venv\Scripts\pythonw.exe" "alliance_ocr.py"
exit /b 0

:NOENV
echo.
echo  [ERROR] Runtime not found: .venv\Scripts\pythonw.exe
echo  Please double-click install_env.bat first.
echo.
pause
exit /b 1
