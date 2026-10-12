# Kobits CLI Installer (Windows PowerShell)
$ErrorActionPreference = "Stop"

Write-Host "⚡ Installing Kobits CLI (Autonomous Multi-Agent Engineering Engine)..." -ForegroundColor Cyan

if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3.12 -m pip install --upgrade "git+https://github.com/kobitscreation-dev/kobitss.git"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    python -m pip install --upgrade "git+https://github.com/kobitscreation-dev/kobitss.git"
} else {
    Write-Error "Python is required to install Kobits. Please install Python 3.11+."
}

Write-Host "✅ Kobits installed successfully!" -ForegroundColor Green
Write-Host "Run 'kobits --help' or 'kobits run `"your objective`"' to start." -ForegroundColor Yellow
