# Your Local Video Editor

A free, fully offline pipeline that turns raw footage dropped into a folder
into a captioned, trimmed, finished video — plus a transcript to check.
Nothing ever leaves your computer, and your original files are never
modified or deleted.

## What it does, per your settings

- **Filming**: one continuous take, portrait (9:16), iPhone.
- **Raw folder**: `Desktop\Recovery Boots AI Editor\01_Raw_Videos` — drop new footage here.
- **Transcripts folder**: `Desktop\Recovery Boots AI Editor\02_Transcripts` — a transcript for every finished video lands here.
- **Finished videos folder**: `Desktop\Recovery Boots AI Editor\03_Final_Videos` — the captioned, trimmed video lands here.
- **Captions**: white text, black outline, lower third, up to 6 words per screen.
- **Always spelled correctly**: "Tim Dyball", "Endurance" — corrected automatically if the transcriber mishears them.
- **Editing**: dead air (silence ≥0.6s) trimmed, filler words (um/uh/erm/hmm) cut, stutter repeats ("the the...") cut, with a small padding buffer so cuts don't feel abrupt.

All of this lives in **`settings.json`** — edit it any time, no code changes needed.

## One-time setup (Windows)

Open PowerShell in this folder and run:

```
.\scripts\install.ps1
```

If it's your first time running scripts in PowerShell, Windows may ask you to
approve running it (or complain about "execution policy") — if so, close and
reopen PowerShell as Administrator once and run:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then try again.

The script explains and asks before installing anything:
1. **ffmpeg** (via `winget`, the package manager built into Windows 10/11) — the free tool that actually cuts, joins, and captions video.
2. **Python 3** (via `winget`), if not already installed — runs the pipeline scripts.
3. A private Python environment inside this folder (doesn't touch anything else on your system).
4. **faster-whisper** — free, offline speech-to-text.
5. Your `01_Raw_Videos`, `02_Transcripts`, and `03_Final_Videos` folders inside `Desktop\Recovery Boots AI Editor\`.
6. The offline transcription model (~500MB, downloaded once — after that, transcription needs no internet at all).

If `winget` isn't available (older Windows builds), install the "App
Installer" from the Microsoft Store first, then re-run the script. The
script never installs anything without asking you to confirm each step
first.

If ffmpeg or Python were just installed for the first time, close and
reopen PowerShell once (so Windows picks up the updated PATH), then run
`.\scripts\install.ps1` again — it skips anything already done and
continues from where it left off.

*(Mac/Linux: use `./scripts/install.sh` instead — same steps, adapted for those systems.)*

## Everyday use

Start the watcher once, and leave it running in a terminal window:

```
.\.venv\Scripts\python.exe scripts\watch_and_process.py
```

Now just drop clips into `Desktop\Recovery Boots AI Editor\01_Raw_Videos`.
A few seconds after you stop copying files in, it will automatically:

1. **Transcribe** the clip (offline, word-for-word with timestamps).
2. **Trim dead air** — silences longer than the threshold in `settings.json`.
3. **Cut stumbles** — filler words and stutter/false-start repeats.
4. **Join in order** — if you drop multiple clips at once, they're joined in filename order (name them `01_...`, `02_...` if order matters).
5. **Caption** them, burned into the video, styled per your settings, with your protected words corrected.
6. **Quality-check itself** before handing anything to you:
   - Plays the finished file back start to finish and confirms it decodes cleanly (no corruption).
   - Confirms the final duration matches the sum of the kept segments.
   - Independently re-transcribes the *finished* audio and diffs it word-for-word against the burned-in captions, to catch any caption/audio mismatch — not just re-checking its own math.
   - If anything fails this check, it's flagged at the top of the transcript file instead of being silently ignored — check the video yourself before publishing.
7. Writes the finished video to `03_Final_Videos` and a transcript (with the QC results and timestamps) to `02_Transcripts`.

To process what's already sitting in the folder once, without leaving a watcher running:

```
.\.venv\Scripts\python.exe scripts\pipeline.py --once
```

## Your original files are never touched

The pipeline only ever *reads* files from `01_Raw_Videos`. All cutting
and rendering happens in a temporary working directory that's deleted after
each run. Nothing is written back to the raw folder, and nothing there is
ever deleted or renamed.

## Honest limitations

- **Stumble detection is a heuristic**, not true understanding of speech: it
  catches filler words (um/uh/erm/hmm) and immediate word-for-word repeats
  (stutters, false restarts). It won't catch every awkward retake — for
  anything more subjective, review the transcript and finished video before
  posting.
- **Spelling correction** only fixes the caption *text* shown on screen; it
  can't change what's audible in your voice recording.
- The first run of each clip is the slowest (loading the transcription
  model into memory); after that it's much faster.

## Changing your settings later

Just edit `settings.json` — for example, to change caption color, add more
protected words, adjust how aggressively filler words are cut, or point
the raw/output folders somewhere else. No need to touch the scripts.
