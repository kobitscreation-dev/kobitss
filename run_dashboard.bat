@echo off
setlocal
title Kobits Engineering Dashboard
echo ======================================================================
echo           KOBITS AUTONOMOUS ENGINEERING DASHBOARD
echo ======================================================================
echo.

where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=py -3.12
    goto :START
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set PYTHON_CMD=python
    goto :START
)
echo [Kobits] Error: Python 3.10+ not found on PATH.
pause
exit /b 1

:START
echo [1/2] Checking environment and database...
%PYTHON_CMD% kobits_cli.py doctor

echo [2/2] Launching Kobits Server on http://localhost:8000...
start "" "http://localhost:8000/portal.html"
%PYTHON_CMD% main.py
pause
