# ==============================================================================
# Kobits Terminal Engine Installer (Windows PowerShell)
# Installs Kobits CLI globally into %LOCALAPPDATA%\kobits
# ==============================================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "   ╦╔═ ╔═╗ ╔╗  ╦ ╔╦╗ ╔═╗" -ForegroundColor Cyan
Write-Host "   ╠╩╗ ║ ║ ╠╩╗ ║  ║  ╚═╗" -ForegroundColor Cyan
Write-Host "   ╩ ╩ ╚═╝ ╚═╝ ╩  ╩  ╚═╝" -ForegroundColor Cyan
Write-Host "   Autonomous Software Engineering CLI" -ForegroundColor Gray
Write-Host "   ─────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

# 1. Detect Python Installation (supports Python 3.10, 3.11, 3.12+)
Write-Host "[1/4] Detecting Python environment..." -ForegroundColor Yellow

$pythonCmd = $null
$pythonPrefix = @()

# Check Windows 'py' launcher first
$testVersions = @("-3.12", "-3.11", "-3.10", "")
foreach ($ver in $testVersions) {
    try {
        $checkArgs = if ($ver) { @($ver, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])") } else { @("-c", "import sys; print(sys.version_info[0], sys.version_info[1])") }
        $proc = Start-Process -FilePath "py" -ArgumentList $checkArgs -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$env:TEMP\py_check.txt" -RedirectStandardError "$env:TEMP\py_err.txt"
        if ($proc.ExitCode -eq 0) {
            $verOut = (Get-Content "$env:TEMP\py_check.txt" -Raw).Trim()
            $major, $minor = $verOut.Split(" ")
            if ([int]$major -eq 3 -and [int]$minor -ge 10) {
                $pythonCmd = "py"
                if ($ver) { $pythonPrefix = @($ver) }
                Write-Host "      Found Python $major.$minor via py launcher" -ForegroundColor Green
                break
            }
        }
    } catch {}
}

# Fallback to direct 'python' command
if (-not $pythonCmd) {
    try {
        $checkArgs = @("-c", "import sys; print(sys.version_info[0], sys.version_info[1])")
        $proc = Start-Process -FilePath "python" -ArgumentList $checkArgs -NoNewWindow -Wait -PassThru -RedirectStandardOutput "$env:TEMP\py_check.txt" -RedirectStandardError "$env:TEMP\py_err.txt"
        if ($proc.ExitCode -eq 0) {
            $verOut = (Get-Content "$env:TEMP\py_check.txt" -Raw).Trim()
            $major, $minor = $verOut.Split(" ")
            if ([int]$major -eq 3 -and [int]$minor -ge 10) {
                $pythonCmd = "python"
                $pythonPrefix = @()
                Write-Host "      Found Python $major.$minor via python on PATH" -ForegroundColor Green
            }
        }
    } catch {}
}

if (-not $pythonCmd) {
    Write-Host ""
    Write-Host "   [!] Python 3.10+ was not found on your system." -ForegroundColor Red
    Write-Host "       Please install Python from https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "       (Make sure to check 'Add Python to PATH' during installation!)" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

# 2. Setup Installation Directory
$InstallDir = "$env:LOCALAPPDATA\kobits"
$BinDir = "$InstallDir\bin"
Write-Host "[2/4] Setting up directory: $InstallDir..." -ForegroundColor Yellow

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

# 3. Download Latest Kobits Engine
Write-Host "[3/4] Downloading latest Kobits engine from GitHub..." -ForegroundColor Yellow
$ZipPath = "$env:TEMP\kobits_latest.zip"
$ZipUrl = "https://github.com/kobitscreation-dev/kobitss/archive/refs/heads/main.zip"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath -UseBasicParsing
    
    $ExtractTemp = "$env:TEMP\kobits_extract"
    if (Test-Path $ExtractTemp) { Remove-Item -Recurse -Force $ExtractTemp }
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractTemp -Force
    
    $SourceFolder = "$ExtractTemp\kobitss-main"
    if (-not (Test-Path $SourceFolder)) {
        # Some archives extract directly without -main suffix
        $SourceFolder = (Get-ChildItem -Path $ExtractTemp -Directory | Select-Object -First 1).FullName
    }

    # Copy files into InstallDir (preserve existing local .env or config if present)
    Get-ChildItem -Path $SourceFolder | ForEach-Object {
        $targetFile = Join-Path $InstallDir $_.Name
        if ($_.Name -eq ".env" -and (Test-Path $targetFile)) {
            # Don't overwrite existing user .env
        } else {
            Copy-Item -Path $_.FullName -Destination $InstallDir -Recurse -Force
        }
    }

    # Clean up temp
    Remove-Item -Force $ZipPath -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $ExtractTemp -ErrorAction SilentlyContinue
    Write-Host "      Downloaded and unpacked successfully." -ForegroundColor Green
} catch {
    Write-Host "      Download error: $_" -ForegroundColor Red
    exit 1
}

# 4. Install Dependencies
Write-Host "[4/4] Verifying dependencies..." -ForegroundColor Yellow
$reqFile = "$InstallDir\requirements.txt"
if (Test-Path $reqFile) {
    $pipArgs = $pythonPrefix + @("-m", "pip", "install", "-r", $reqFile, "--quiet", "--disable-pip-version-check")
    & $pythonCmd @pipArgs
}

# 5. Create Command Wrappers in BinDir
$cmdContent = @"
@echo off
setlocal
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    py -c "import sys; exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if %ERRORLEVEL% equ 0 (
        py "$InstallDir\kobits_cli.py" %*
        exit /b %ERRORLEVEL%
    )
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    python "$InstallDir\kobits_cli.py" %*
    exit /b %ERRORLEVEL%
)
echo [Kobits] Error: Python 3.10+ not found on PATH.
exit /b 1
"@
Set-Content -Path "$BinDir\kobits.cmd" -Value $cmdContent -Encoding ASCII

$ps1Content = @"
#!/usr/bin/env pwsh
`$ScriptDir = Split-Path -Parent `$MyInvocation.MyCommand.Definition
`$EngineScript = "$InstallDir\kobits_cli.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py "`$EngineScript" @args
    exit `$LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python "`$EngineScript" @args
    exit `$LASTEXITCODE
}
Write-Error "[Kobits] Error: Python 3.10+ not found on PATH."
exit 1
"@
Set-Content -Path "$BinDir\kobits.ps1" -Value $ps1Content -Encoding UTF8

# 6. Add BinDir to PATH (Current session + Permanent User PATH)
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -split ';' -notcontains $BinDir) {
    $newPath = "$currentPath;$BinDir"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
}
if ($env:PATH -split ';' -notcontains $BinDir) {
    $env:PATH = "$env:PATH;$BinDir"
}

Write-Host ""
Write-Host "   =======================================================" -ForegroundColor Green
Write-Host "   [OK] KOBITS INSTALLED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "   =======================================================" -ForegroundColor Green
Write-Host ""
Write-Host "   You can now run Kobits anywhere on your computer:" -ForegroundColor Gray
Write-Host '   PS > kobits run "Build a modern school admission website"' -ForegroundColor Cyan
Write-Host ""
Write-Host "   Or start the interactive terminal session:" -ForegroundColor Gray
Write-Host '   PS > kobits' -ForegroundColor Cyan
Write-Host ""
