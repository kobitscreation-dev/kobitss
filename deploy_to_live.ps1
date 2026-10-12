# Kobits Live Deployment Script
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host "  Kobits - Deploy Monochrome UI Redesign to Live   " -ForegroundColor Cyan
Write-Host "  Live Domain: https://kobits.space                " -ForegroundColor Cyan
Write-Host "===================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/3] Staging all files for deployment..." -ForegroundColor Yellow
git add .

Write-Host "[2/3] Creating commit..." -ForegroundColor Yellow
git commit -m "feat(ui): monochrome 3d wireframe redesign and animated agent indicators"

Write-Host "[3/3] Pushing to GitHub (origin/main)..." -ForegroundColor Yellow
Write-Host "(Render auto-deploys upon push to main)" -ForegroundColor DarkGray
git push origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "===================================================" -ForegroundColor Green
    Write-Host "[SUCCESS] Pushed to GitHub!" -ForegroundColor Green
    Write-Host "Render auto-deploy is building the new container." -ForegroundColor Green
    Write-Host "Your live site will be updated in 2-3 minutes at:" -ForegroundColor Green
    Write-Host "  https://kobits.space" -ForegroundColor White
    Write-Host "===================================================" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Git push failed. Please check credentials." -ForegroundColor Red
}
