# One-time setup for your local video editing pipeline, on Windows.
# Everything here is free and runs entirely on this computer — nothing is
# uploaded anywhere, ever. Re-run any time; it skips steps already done.
# Run it by right-clicking this file -> "Run with PowerShell", or from a
# PowerShell window: .\scripts\install.ps1

$ErrorActionPreference = "Stop"
$RepoDir = Split-Path -Parent $PSScriptRoot
Set-Location $RepoDir

function Confirm-Step {
    param([string]$Explanation)
    Write-Host ""
    Write-Host "-------------------------------------------------------------"
    Write-Host $Explanation
    $reply = Read-Host "Proceed? [y/N]"
    return ($reply -match '^(y|yes)$')
}

function Test-PythonWorks {
    # Windows ships a fake 'python' command that just opens the Microsoft
    # Store when real Python isn't installed. Get-Command alone can't tell
    # the difference, so actually run it and check the result.
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) { return $false }
    try {
        $out = & python --version 2>&1
        return ($LASTEXITCODE -eq 0 -and $out -notmatch "Python was not found")
    } catch {
        return $false
    }
}

Write-Host "Setting up your local video editor on Windows."
Write-Host "This script only touches this computer. It will:"
Write-Host "  1. Install ffmpeg (cuts, joins, and captions video) via winget"
Write-Host "  2. Install Python 3 if missing, via winget"
Write-Host "  3. Create a private Python environment inside this folder"
Write-Host "  4. Install faster-whisper (free, offline speech-to-text)"
Write-Host "  5. Create your Raw / Transcripts / Final Videos folders on the Desktop"
Write-Host "  6. Download the offline transcription model (about 500MB, one-time; after this, transcription needs no internet at all)"

$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) {
    Write-Host ""
    Write-Host "winget (Windows Package Manager) was not found. It ships with Windows 10 (2019+) and Windows 11 by default."
    Write-Host "Install it from the Microsoft Store ('App Installer'), then re-run this script."
    Write-Host "Alternatively, install ffmpeg manually from https://ffmpeg.org/download.html and Python from https://python.org, make sure both are on your PATH, then re-run this script."
    exit 1
}

# 1: ffmpeg
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    if (Confirm-Step "Install ffmpeg via winget? (This is the free tool that actually cuts/joins/captions your video files.)") {
        winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
        Write-Host "ffmpeg installed. You may need to close and reopen this PowerShell window for it to be found on PATH."
    }
} else {
    Write-Host "ffmpeg already installed, skipping."
}

# 2: Python 3
$pythonJustInstalled = $false
if (-not (Test-PythonWorks)) {
    if (Confirm-Step "Install Python 3 via winget? (Runs the editing scripts.)") {
        winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
        $pythonJustInstalled = $true
    } else {
        Write-Host "Cannot continue without Python. Exiting."
        exit 1
    }
} else {
    Write-Host "Python 3 already installed, skipping."
}

if ($pythonJustInstalled) {
    Write-Host ""
    Write-Host "-------------------------------------------------------------"
    Write-Host "Python was just installed. Windows needs a fresh PowerShell window to see it on PATH."
    Write-Host "Please close this window, reopen PowerShell in this same folder, and run '.\scripts\install.ps1' again."
    Write-Host "It will skip ffmpeg and Python (already done) and continue from the next step."
    exit 0
}

# 3 + 4: Python environment
if (Confirm-Step "Create a private Python environment in '$RepoDir\.venv' and install faster-whisper (offline transcription) into it? Nothing here affects any other Python on your system.") {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
    Write-Host "Python environment ready."
}

# 5: folders from settings.json
$settings = Get-Content settings.json | ConvertFrom-Json
$rawDir = $settings.folders.raw -replace '^~', $env:USERPROFILE
$transcriptsDir = $settings.folders.transcripts -replace '^~', $env:USERPROFILE
$outputDir = $settings.folders.output -replace '^~', $env:USERPROFILE

if (Confirm-Step "Create your three working folders?
  Raw footage (drop new clips here):  $rawDir
  Transcripts (to check):             $transcriptsDir
  Finished videos:                    $outputDir") {
    New-Item -ItemType Directory -Force -Path $rawDir | Out-Null
    New-Item -ItemType Directory -Force -Path $transcriptsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    Write-Host "Folders created (or already existed)."
}

# 6: pre-download the whisper model so first real run isn't slow
if (Confirm-Step "Download the offline transcription model now (one-time, about 500MB, needs internet just this once)?") {
    $model = $settings.whisper_model
    .\.venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('$model')"
    Write-Host "Model downloaded and cached locally."
}

Write-Host ""
Write-Host "-------------------------------------------------------------"
Write-Host "Setup complete."
Write-Host ""
Write-Host "To watch your raw footage folder and auto-process anything you drop in it, run:"
Write-Host "  .\.venv\Scripts\python.exe scripts\watch_and_process.py"
Write-Host ""
Write-Host "To process whatever's currently sitting in the folder once, without watching, run:"
Write-Host "  .\.venv\Scripts\python.exe scripts\pipeline.py --once"
