#!/usr/bin/env bash
# One-time setup for your local video editing pipeline.
# Everything installed here is free and runs entirely on this computer —
# nothing is uploaded anywhere, ever. Re-run any time; it skips steps
# that are already done.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

confirm() {
    # $1 = plain-English explanation of what's about to happen
    echo ""
    echo "-------------------------------------------------------------"
    echo "$1"
    read -r -p "Proceed? [y/N] " reply
    case "$reply" in
        [yY][eE][sS]|[yY]) return 0 ;;
        *) echo "Skipped." ; return 1 ;;
    esac
}

OS="$(uname -s)"
echo "Setting up your local video editor on $OS."
echo "This script only touches this computer. It will:"
echo "  1. Install ffmpeg (cuts, joins, and captions video)"
echo "  2. Install Python 3 if missing (runs the pipeline)"
echo "  3. Create a private Python environment inside this folder"
echo "  4. Install faster-whisper (free, offline speech-to-text)"
echo "  5. Create your RawClips and Finished-videos folders on the Desktop"
echo "  6. Download the offline transcription model (~500MB, one-time, then works with no internet)"

# 1 + 2: system dependencies
if [ "$OS" = "Darwin" ]; then
    if ! command -v brew >/dev/null 2>&1; then
        if confirm "Homebrew (the standard free package manager for Mac) isn't installed. It's needed to install ffmpeg. Install Homebrew now?"; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        else
            echo "Cannot continue without Homebrew or a manual ffmpeg install. Exiting."
            exit 1
        fi
    fi
    if ! command -v ffmpeg >/dev/null 2>&1; then
        if confirm "Install ffmpeg via Homebrew? (This is the free tool that actually cuts/joins/captions your video files.)"; then
            brew install ffmpeg
        fi
    else
        echo "ffmpeg already installed, skipping."
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        if confirm "Install Python 3 via Homebrew? (Runs the editing scripts.)"; then
            brew install python
        fi
    else
        echo "Python 3 already installed, skipping."
    fi
elif [ "$OS" = "Linux" ]; then
    if ! command -v ffmpeg >/dev/null 2>&1; then
        if confirm "Install ffmpeg via apt? (This is the free tool that actually cuts/joins/captions your video files. You may be asked for your password by sudo.)"; then
            sudo apt-get update && sudo apt-get install -y ffmpeg python3 python3-venv python3-pip
        fi
    else
        echo "ffmpeg already installed, skipping."
    fi
else
    echo "Unrecognized OS ($OS). If you're on Windows, run this inside WSL (Ubuntu), then re-run this script there."
    exit 1
fi

# 3 + 4: Python environment
if confirm "Create a private Python environment in '$REPO_DIR/.venv' and install faster-whisper (offline transcription) into it? Nothing here affects any other Python on your system."; then
    python3 -m venv .venv
    ./.venv/bin/pip install --quiet --upgrade pip
    ./.venv/bin/pip install --quiet -r requirements.txt
    echo "Python environment ready."
fi

# 5: folders from settings.json
RAW_DIR="$(python3 -c "import json,os; print(os.path.expanduser(json.load(open('settings.json'))['folders']['raw']))")"
OUT_DIR="$(python3 -c "import json,os; print(os.path.expanduser(json.load(open('settings.json'))['folders']['output']))")"

if confirm "Create your two working folders?
  Raw footage folder (drop new clips here):  $RAW_DIR
  Finished videos folder (pipeline output):  $OUT_DIR"; then
    mkdir -p "$RAW_DIR" "$OUT_DIR"
    echo "Folders created (or already existed)."
fi

# 6: pre-download the whisper model so first real run isn't slow
if confirm "Download the offline transcription model now (one-time, ~500MB, needs internet just this once — after this, transcription works with no internet at all)?"; then
    MODEL="$(python3 -c "import json; print(json.load(open('settings.json'))['whisper_model'])")"
    ./.venv/bin/python3 -c "from faster_whisper import WhisperModel; WhisperModel('$MODEL')"
    echo "Model downloaded and cached locally."
fi

echo ""
echo "-------------------------------------------------------------"
echo "Setup complete."
echo ""
echo "To start watching your raw footage folder and auto-process anything you drop in it, run:"
echo "  ./.venv/bin/python3 scripts/watch_and_process.py"
echo ""
echo "To process whatever's currently sitting in the folder once, without watching, run:"
echo "  ./.venv/bin/python3 scripts/pipeline.py --once"
