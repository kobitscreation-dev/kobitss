#!/usr/bin/env pwsh
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Engine = Join-Path $ScriptDir "kobits_cli.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py $Engine @args
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Engine @args
    exit $LASTEXITCODE
}
Write-Error "[Kobits] Error: Python not found on PATH."
exit 1
