@echo off
rem Debug launcher: keeps the console open so startup errors are visible.
rem Keep this file pure ASCII (see the launcher .bat for the reason).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto NOENV

echo Starting with console. Close this window to stop the app.
echo.
".venv\Scripts\python.exe" "alliance_ocr.py"
echo.
echo [exit] process returned. See the output folder for logs.
pause
exit /b 0

:NOENV
echo.
echo  [ERROR] Runtime not found: .venv\Scripts\python.exe
echo  Please double-click install_env.bat first.
echo.
pause
exit /b 1
