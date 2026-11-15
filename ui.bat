@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 goto nopython
start "" pythonw lol_limit_ui.py
goto done
:nopython
echo.
echo [ERROR] pythonw not found.
echo Install Python 3.8+ from https://www.python.org/downloads/
echo and check "Add Python to PATH" during setup.
pause
:done
