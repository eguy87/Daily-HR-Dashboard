$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
} else {
    throw "Python 3.10+ is required. Download it from https://www.python.org/downloads/."
}

Push-Location $ProjectRoot
try {
    & $Python -m venv .venv
    & ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
    & ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
    & ".\.venv\Scripts\python.exe" -m playwright install chromium
    Write-Host "Setup complete. Run .\start.ps1 to build and open the dashboard."
} finally {
    Pop-Location
}

