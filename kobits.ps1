#!/usr/bin/env pwsh
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
& py -3.12 "$ScriptDir\kobits_cli.py" @args
exit $LASTEXITCODE
