@echo off
title ScreenPush (dev)
setlocal
set "APP=%~dp0src\app.py"

rem ---- find a usable Python ----
rem try common install locations first, then fall back to PATH
set "PY="
if exist "C:\Program Files\Python314\python.exe" set "PY=C:\Program Files\Python314\python.exe"
if exist "C:\Program Files\Python313\python.exe" if not defined PY set "PY=C:\Program Files\Python313\python.exe"
if defined PY goto havepy
where py >nul 2>nul
if not errorlevel 1 set "PY=py"
if defined PY goto havepy
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto nopy

:havepy
if not exist "%APP%" goto noapp
"%PY%" "%APP%" %*
if errorlevel 1 pause
exit /b 0

:nopy
echo [ERROR] Python not found. Install Python 3.10+ and make sure it is on PATH.
pause
exit /b 1

:noapp
echo [ERROR] src\app.py not found next to this script.
pause
exit /b 1
