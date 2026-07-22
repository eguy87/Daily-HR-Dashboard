param(
    [int]$Port = 8000,
    [switch]$SkipBuild,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
} else {
    throw "Python 3.10+ is required. Install Python, then run .\setup.ps1."
}

Push-Location $ProjectRoot
try {
    if (-not $SkipBuild) {
        & $Python update.py
        if ($LASTEXITCODE -ne 0) { throw "Dashboard build failed." }
    }

    $ServeArgs = @("serve.py", "--port", $Port)
    if (-not $NoBrowser) { $ServeArgs += "--open" }
    & $Python @ServeArgs
} finally {
    Pop-Location
}

