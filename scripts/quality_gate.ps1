$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $projectRoot ".venv\Scripts"

function Invoke-GateStep {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Executable,
        [string[]]$Arguments = @()
    )

    Write-Host "==> $Name"
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    Invoke-GateStep `
        -Name "Ruff lint" `
        -Executable (Join-Path $toolRoot "ruff.exe") `
        -Arguments @("check", ".")

    Invoke-GateStep `
        -Name "Ruff format" `
        -Executable (Join-Path $toolRoot "ruff.exe") `
        -Arguments @("format", "--check", ".")

    Invoke-GateStep `
        -Name "mypy" `
        -Executable (Join-Path $toolRoot "mypy.exe") `
        -Arguments @("main.py")

    Invoke-GateStep `
        -Name "pytest" `
        -Executable (Join-Path $toolRoot "pytest.exe") `
        -Arguments @()

    Write-Host "==> Secret scan"
    $files = @(
        git ls-files --cached --others --exclude-standard |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    )
    & (Join-Path $toolRoot "detect-secrets-hook.exe") `
        --baseline ".secrets.baseline" --no-verify @files
    if ($LASTEXITCODE -ne 0) {
        throw "Secret scan failed with exit code $LASTEXITCODE"
    }
    Write-Host "Secret scan passed: $($files.Count) files checked."

    Write-Host "Quality gate passed."
}
finally {
    Pop-Location
}
