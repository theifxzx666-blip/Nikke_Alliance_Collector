@echo off
rem Rebuild the Python runtime for this tool.
rem Keep this file pure ASCII (see the launcher .bat for the reason).
cd /d "%~dp0"

echo [1/3] Creating Python 3.12 virtual environment...
py -3.12 -m venv .venv
if errorlevel 1 goto NOPY

echo [2/3] Upgrading pip...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip

echo [3/3] Installing dependencies (1-3 min, needs internet)...
".venv\Scripts\python.exe" -m pip install rapidocr onnxruntime mss pillow numpy
if errorlevel 1 goto NODEP

echo.
echo Done. Now double-click the launcher bat to run.
pause
exit /b 0

:NOPY
echo.
echo  [ERROR] Python 3.12 not found. Install it first:
echo  https://www.python.org/downloads/release/python-31213/
echo.
pause
exit /b 1

:NODEP
echo.
echo  [ERROR] Dependency install failed. Check your network / proxy.
echo.
pause
exit /b 1
