@echo off
rem (c) manju-vidu-workflow - MJVID:d2664faa3e - provenance marker, please keep
chcp 65001 >nul
title Init - MANJU Skill (Vidu channel) - one time
cd /d "%~dp0"
setlocal

echo ============================================================
echo  MANJU creation workflow  -  Vidu channel
echo  one-time environment setup
echo  (run ONCE per machine; later runs pass in seconds)
echo ============================================================
echo.

rem ---- locate Python: py launcher - then python - then python3 ----
set "RT="
for %%P in (py.exe) do if not defined RT set "RT=%%~$PATH:P"
for %%P in (python.exe) do if not defined RT set "RT=%%~$PATH:P"
for %%P in (python3.exe) do if not defined RT set "RT=%%~$PATH:P"
if defined RT goto ready

echo [1/2] Python not found on this machine - installing...
echo.
set "WINGET="
for %%P in (winget.exe) do if not defined WINGET set "WINGET=%%~$PATH:P"
if not defined WINGET goto manual

"%WINGET%" install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
goto recheck

:recheck
set "RT="
for %%P in (py.exe) do if not defined RT set "RT=%%~$PATH:P"
for %%P in (python.exe) do if not defined RT set "RT=%%~$PATH:P"
for %%P in (python3.exe) do if not defined RT set "RT=%%~$PATH:P"
if defined RT goto ready

echo.
echo     Python was installed, but THIS window cannot see it yet.
echo     That is normal Windows PATH behaviour.
echo     Please CLOSE this window, then run the init script again.
echo.
pause
exit /b 0

:manual
echo.
echo     winget is not available on this machine.
echo     Please install Python 3 manually, then run this file again.
echo     The official download page will now open in your browser.
echo.
echo       IMPORTANT: tick "Add python.exe to PATH" during install.
echo.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:ready
echo [OK] Python found: %RT%
"%RT%" --version
echo.
echo ============================================================
echo  [2/2] checking dependencies, Edge, login state, shortcuts
echo ============================================================
echo.
"%RT%" "scripts\init_env.py" %*
echo.
echo Done. This window can be closed.
pause
exit /b 0
