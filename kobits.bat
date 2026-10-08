@echo off
setlocal
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py "%~dp0kobits_cli.py" %*
    exit /b %ERRORLEVEL%
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "%~dp0kobits_cli.py" %*
    exit /b %ERRORLEVEL%
)
echo [Kobits] Error: Python not found on PATH.
exit /b 1
