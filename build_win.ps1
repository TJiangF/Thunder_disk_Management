# Windows pack script: PyInstaller onedir -> dist\ThunderSweeper\
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File build_win.ps1
#
# Artifacts:
#   dist\ThunderSweeper\ThunderSweeper.exe   green folder, run directly
#   dist\ThunderSweeper-<ver>-win64.zip      distributable zip
#
# Verify:
#   dist\ThunderSweeper\ThunderSweeper.exe --version
#   dist\ThunderSweeper\ThunderSweeper.exe selftest

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Py = $null
if (Test-Path ".venv\Scripts\python.exe") { $Py = ".venv\Scripts\python.exe" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $Py = "py -3" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Py = "python" }
if (-not $Py) { throw "Python 3.10+ not found. Install it first." }

Write-Host "==> Using interpreter: $Py"
Write-Host "==> Installing deps"
& $Py -m pip install -q -r requirements.txt
& $Py -m pip show pyinstaller | Out-Null
if ($LASTEXITCODE -ne 0) { & $Py -m pip install -q pyinstaller }

Write-Host "==> Self-test (offline)"
& $Py -m thunder_sweeper selftest

Write-Host "==> PyInstaller onedir build"
& $Py -m PyInstaller packaging\ThunderSweeper.spec --noconfirm --clean `
    --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Ver = & $Py -c "from thunder_sweeper import util; print(util.app_version())"
$Zip = "dist\ThunderSweeper-$Ver-win64.zip"
Remove-Item -LiteralPath $Zip -ErrorAction SilentlyContinue
Write-Host "==> Packing zip: $Zip"
Compress-Archive -Path "dist\ThunderSweeper" -DestinationPath $Zip -Force

Write-Host ""
Write-Host "Done: dist\ThunderSweeper\ThunderSweeper.exe (verify: --version / selftest)"