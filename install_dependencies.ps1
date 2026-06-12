# Install GPS Disruption Tracker dependencies
# sqlite3 is included with Python's standard library — no pip install needed.

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Set-Location $ProjectRoot

if (Test-Path ".\.venv\Scripts\python.exe") {
    $Python = ".\.venv\Scripts\python.exe"
    $Pip = ".\.venv\Scripts\pip.exe"
} else {
    $Python = "python"
    $Pip = "pip"
}

Write-Host "Using Python: $Python"
& $Pip install -r requirements.txt
Write-Host ""
Write-Host "Dependencies installed."
Write-Host ""
Write-Host "Run the dashboard with:"
Write-Host "  $Python -m streamlit run app.py"
