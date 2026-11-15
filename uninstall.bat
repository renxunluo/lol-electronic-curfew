@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto trypy
python lol_limit.py uninstall
goto done
:trypy
where py >nul 2>nul
if errorlevel 1 goto nopython
py lol_limit.py uninstall
goto done
:nopython
echo.
echo [ERROR] Python not found.
echo Install Python 3.8+ from https://www.python.org/downloads/
echo and check "Add Python to PATH" during setup.
:done
echo.
pause
