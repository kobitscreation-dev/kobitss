@echo off
setlocal enabledelayedexpansion

echo ===================================================
echo   Kobits - Deploy Monochrome UI Redesign to Live
echo   Live Domain: https://kobits.space
echo ===================================================
echo.

echo [1/3] Staging all files for deployment...
git add .
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git add failed.
    pause
    exit /b %ERRORLEVEL%
)

echo [2/3] Creating commit...
git commit -m "feat(ui): monochrome 3d wireframe redesign and animated agent indicators"
if %ERRORLEVEL% NEQ 0 (
    echo [INFO] No new changes to commit or commit created.
)

echo [3/3] Pushing to GitHub (origin/main)...
echo (Render will automatically trigger deployment on push)
git push origin main
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Git push failed. Please check your GitHub credentials or connection.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo ===================================================
echo [SUCCESS] Pushed to GitHub!
echo Render is now building the new Docker container.
echo In 2-3 minutes, your live site will be updated at:
echo   https://kobits.space
echo ===================================================
echo.
pause
