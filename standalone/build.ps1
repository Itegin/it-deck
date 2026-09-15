# Builds the standalone IT-Deck.exe: one PyInstaller onefile build bundling
# the backend, the frontend static assets, and the Windows agent (including
# its bundled agents/windows/tools/SoundVolumeView.exe). Run from anywhere;
# paths below are resolved relative to this script's own location.
#
# Needs Python 3.7-3.12 (comtypes' own supported range -- see
# agents/windows/requirements.txt) to BUILD with -- installed automatically
# below via winget if nothing suitable is found. The resulting ITDeck.exe
# itself needs nothing installed on the PC that runs it.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $root
$venv = Join-Path $root "venv"

function Resolve-BuildPython {
    # Already on PATH and in comtypes' supported range?
    try {
        $verOutput = & python --version 2>&1
        if ($verOutput -match "Python 3\.([7-9]|1[0-2])\.") {
            return "python"
        }
        Write-Host "Found $verOutput on PATH, but it's outside comtypes' 3.7-3.12 range."
    } catch {
        Write-Host "No Python found on PATH."
    }

    # winget/python.org's installer puts a per-user install here by default;
    # check before reinstalling in case a prior run already did this.
    $candidate = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $candidate) {
        return $candidate
    }

    Write-Host "Installing Python 3.12 via winget (needs winget -- ships with modern Windows 10/11)..."
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
    if (-not (Test-Path $candidate)) {
        throw "winget install finished but $candidate wasn't found. Install Python 3.7-3.12 manually and re-run this script."
    }
    return $candidate
}

$buildPython = Resolve-BuildPython

if (-not (Test-Path $venv)) {
    & $buildPython -m venv $venv
}

$pip = Join-Path $venv "Scripts\pip.exe"
$python = Join-Path $venv "Scripts\python.exe"
$pyinstaller = Join-Path $venv "Scripts\pyinstaller.exe"

& $pip install --quiet -r (Join-Path $root "requirements.txt")

# Generate icon.ico if it's missing (it's gitignored, so a fresh clone has
# none yet) rather than silently shipping PyInstaller's generic default --
# make_icon.py is idempotent and its only dependency, Pillow, is already
# pulled in above via agents/windows/requirements.txt.
& $python (Join-Path $repoRoot "agents\windows\tools\make_icon.py")

$iconArg = @()
$iconDataArg = @()
$iconPath = Join-Path $repoRoot "agents\windows\icon.ico"
if (Test-Path $iconPath) {
    $iconArg = @("--icon", $iconPath)
    # Baking the icon into the exe (--icon above) only sets its file/taskbar
    # icon -- it isn't a path the running program can open. Bundled again
    # here as plain data so show_info_window() can load it for the popup's
    # own title-bar icon at runtime.
    $iconDataArg = @("--add-data", "${iconPath};.")
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
    @iconDataArg `
    (Join-Path $root "launcher.py")

Write-Host ""
Write-Host "Built: standalone\dist\ITDeck.exe"
