# Builds the standalone IT-Deck.exe: one PyInstaller onefile build bundling
# the backend, the frontend static assets, and the Windows agent (including
# its bundled agents/windows/tools/SoundVolumeView.exe). Run from anywhere;
# paths below are resolved relative to this script's own location.
#
# Requires Python 3.7-3.12 (comtypes' own supported range -- see
# agents/windows/requirements.txt) and, on first run, downloads/builds a
# throwaway venv under standalone/venv.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $root
$venv = Join-Path $root "venv"

if (-not (Test-Path $venv)) {
    python -m venv $venv
}

$pip = Join-Path $venv "Scripts\pip.exe"
$pyinstaller = Join-Path $venv "Scripts\pyinstaller.exe"

& $pip install --quiet -r (Join-Path $root "requirements.txt")

$iconArg = @()
$iconPath = Join-Path $repoRoot "agents\windows\icon.ico"
if (Test-Path $iconPath) {
    $iconArg = @("--icon", $iconPath)
}

$frontendSrc = Join-Path $repoRoot "frontend"
$soundVolumeViewSrc = Join-Path $repoRoot "agents\windows\tools\SoundVolumeView.exe"

& $pyinstaller `
    --onefile `
    --name ITDeck `
    --console `
    --paths (Join-Path $repoRoot "backend") `
    --paths (Join-Path $repoRoot "agents\windows") `
    --add-data "${frontendSrc};frontend" `
    --add-data "${soundVolumeViewSrc};tools" `
    --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build") `
    --specpath $root `
    @iconArg `
    (Join-Path $root "launcher.py")

Write-Host ""
Write-Host "Built: standalone\dist\ITDeck.exe"
