#!/usr/bin/env python3
"""
Core video editing pipeline: transcribe -> trim dead air -> cut stumbles ->
join in order -> caption -> quality-check -> deliver.

Runs entirely offline after the one-time model download. Never modifies or
deletes anything in the raw footage folder — all work happens in a temp
directory; only finished files are written to the output folder.
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = REPO_DIR / "settings.json"
STATE_DIR = REPO_DIR / ".state"
STATE_FILE = STATE_DIR / "processed.json"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


def load_settings():
    with open(SETTINGS_PATH) as f:
        return json.load(f)


def expand(path_str):
    return Path(os.path.expanduser(path_str)).resolve()


# ---------------------------------------------------------------------------
# State tracking (so re-running / watching never reprocesses the same clip)
# ---------------------------------------------------------------------------

def load_state():
    STATE_DIR.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def file_fingerprint(path: Path):
    st = path.stat()
    return f"{st.st_size}:{int(st.st_mtime)}"


# ---------------------------------------------------------------------------
# ffprobe / ffmpeg helpers
# ---------------------------------------------------------------------------

def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def run_ffmpeg(cmd, **kwargs):
    """Like run(), but raises with the real ffmpeg error instead of letting
    a silently-failed (missing/empty) output file confuse a later step."""
    result = run(cmd, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result


def probe_duration(path: Path) -> float:
    result = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(result.stdout.strip())


def detect_silences(path: Path, threshold_db: float, min_duration: float):
    """Returns list of (start, end) silence intervals in seconds."""
    result = run([
        "ffmpeg", "-i", str(path), "-af",
        f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])
    log = result.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\-0-9.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\-0-9.]+)", log)]
    # ffmpeg always pairs these in order; if a silence runs to EOF there may
    # be an unmatched start, which we can safely ignore (trailing silence is
    # already handled by trimming to the last spoken word).
    return list(zip(starts, ends[: len(starts)]))


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

@dataclass
class Word:
    start: float
    end: float
    text: str


_MODEL_CACHE = {}


def get_model(model_name: str):
    if model_name not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        _MODEL_CACHE[model_name] = WhisperModel(model_name, device="cpu", compute_type="int8")
    return _MODEL_CACHE[model_name]


def transcribe_words(path: Path, model_name: str):
    model = get_model(model_name)
    segments, _info = model.transcribe(str(path), word_timestamps=True, vad_filter=False)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append(Word(start=w.start, end=w.end, text=w.word.strip()))
    return words


# ---------------------------------------------------------------------------
# Deciding what to cut: dead air, filler words, stutter repeats
# ---------------------------------------------------------------------------

def _norm(word_text: str) -> str:
    return re.sub(r"[^a-z']", "", word_text.lower())


def find_filler_spans(words, filler_words):
    fillers = {w.lower() for w in filler_words}
    spans = []
    for w in words:
        if _norm(w.text) in fillers:
            spans.append((w.start, w.end))
    return spans


def find_stutter_spans(words):
    """Detect immediate word repeats (e.g. 'the the', 'I I was') and mark the
    earlier occurrence(s) for removal, keeping the final, completed one."""
    spans = []
    i = 0
    while i < len(words) - 1:
        run_start = i
        while i < len(words) - 1 and _norm(words[i].text) == _norm(words[i + 1].text) and _norm(words[i].text):
            i += 1
        if i > run_start:
            # words[run_start .. i] are identical repeats; keep only the last
            for w in words[run_start:i]:
                spans.append((w.start, w.end))
        i += 1
    return spans


def merge_spans(spans, pad):
    if not spans:
        return []
    padded = sorted((max(0.0, s - pad), e + pad) for s, e in spans)
    merged = [list(padded[0])]
    for s, e in padded[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def compute_keep_segments(duration, silences, words, settings):
    ed = settings["editing"]
    cut_spans = []

    for s, e in silences:
        if e - s >= ed["min_silence_to_cut_seconds"]:
            cut_spans.append((s, e))

    if ed.get("remove_filler_words"):
        cut_spans.extend(find_filler_spans(words, ed["filler_words"]))

    if ed.get("remove_stutter_repeats"):
        cut_spans.extend(find_stutter_spans(words))

    cut_spans = merge_spans(cut_spans, ed["keep_padding_seconds"])

    keep = []
    cursor = 0.0
    for s, e in sorted(cut_spans):
        s, e = max(0.0, s), min(duration, e)
        if s > cursor:
            keep.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        keep.append((cursor, duration))

    # Drop slivers too short to be meaningful (avoids ffmpeg edge artifacts).
    keep = [(s, e) for s, e in keep if e - s > 0.08]
    return keep


def remap_words_to_keep_segments(words, keep_segments):
    """Returns words with timestamps rewritten onto the new, cut timeline,
    dropping any word that falls entirely inside a removed span."""
    remapped = []
    cumulative = 0.0
    for seg_start, seg_end in keep_segments:
        seg_len = seg_end - seg_start
        for w in words:
            overlap_start = max(w.start, seg_start)
            overlap_end = min(w.end, seg_end)
            if overlap_end - overlap_start > (w.end - w.start) * 0.5:
                new_start = cumulative + max(0.0, w.start - seg_start)
                new_end = cumulative + min(seg_len, w.end - seg_start)
                remapped.append(Word(start=new_start, end=new_end, text=w.text))
        cumulative += seg_len
    return remapped


# ---------------------------------------------------------------------------
# Cutting and joining with ffmpeg
# ---------------------------------------------------------------------------

def cut_segments_and_concat(src: Path, keep_segments, out_path: Path, workdir: Path):
    part_files = []
    for idx, (s, e) in enumerate(keep_segments):
        part = workdir / f"part_{idx:04d}.mp4"
        run_ffmpeg([
            "ffmpeg", "-y", "-i", str(src), "-ss", f"{s:.3f}", "-to", f"{e:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", str(part),
        ])
        part_files.append(part)

    list_file = workdir / "concat_list.txt"
    with open(list_file, "w") as f:
        for p in part_files:
            f.write(f"file '{p.as_posix()}'\n")

    run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", str(out_path),
    ])
    return part_files


def concat_final_clips(clip_paths, out_path: Path, workdir: Path):
    if len(clip_paths) == 1:
        shutil.copy(clip_paths[0], out_path)
        return
    list_file = workdir / "final_concat_list.txt"
    with open(list_file, "w") as f:
        for p in clip_paths:
            f.write(f"file '{Path(p).resolve().as_posix()}'\n")
    run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", str(out_path),
    ])


# ---------------------------------------------------------------------------
# Spelling correction against the user's protected word list
# ---------------------------------------------------------------------------

def correct_spelling(text: str, must_spell):
    for phrase in must_spell:
        phrase_words = phrase.split()
        n = len(phrase_words)
        tokens = text.split()
        out = []
        i = 0
        while i < len(tokens):
            window = tokens[i:i + n]
            window_norm = " ".join(_norm(t) for t in window)
            phrase_norm = " ".join(_norm(t) for t in phrase_words)
            if len(window) == n and window_norm and difflib.SequenceMatcher(
                None, window_norm, phrase_norm
            ).ratio() > 0.72:
                out.append(phrase)
                i += n
            else:
                out.append(tokens[i])
                i += 1
        text = " ".join(out)
    return text


# ---------------------------------------------------------------------------
# Captions: chunk words into cues, apply spelling fixes, write ASS, burn in
# ---------------------------------------------------------------------------

@dataclass
class Cue:
    start: float
    end: float
    text: str


def build_cues(words, max_words, must_spell):
    cues = []
    chunk = []
    for w in words:
        chunk.append(w)
        ends_sentence = w.text.strip().endswith((".", "!", "?"))
        if len(chunk) >= max_words or ends_sentence:
            text = correct_spelling(" ".join(w.text for w in chunk), must_spell)
            cues.append(Cue(chunk[0].start, chunk[-1].end, text))
            chunk = []
    if chunk:
        text = correct_spelling(" ".join(w.text for w in chunk), must_spell)
        cues.append(Cue(chunk[0].start, chunk[-1].end, text))
    return cues


def _ass_time(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def write_ass(cues, settings, out_path: Path, video_w: int, video_h: int):
    cap = settings["captions"]
    alignment = {"bottom_third": 2, "top_third": 8, "center": 5}[cap["position"]]
    margin_v = int(video_h * 0.12) if cap["position"] != "center" else 0
    color_map = {"white": "&H00FFFFFF", "yellow": "&H0000FFFF", "black": "&H00000000"}
    primary = color_map.get(cap["text_color"], "&H00FFFFFF")
    outline = color_map.get(cap["outline_color"], "&H00000000")
    font_size = cap["font_size_portrait"]

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cap["font"]},{font_size},{primary},{primary},{outline},&H00000000,1,0,0,0,100,100,0,0,1,{cap["outline_width"]},0,{alignment},40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for c in cues:
        lines.append(
            f"Dialogue: 0,{_ass_time(c.start)},{_ass_time(c.end)},Default,,0,0,0,,{c.text}"
        )
    out_path.write_text(header + "\n".join(lines))


def probe_dimensions(path: Path):
    result = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", str(path),
    ])
    # Some ffprobe builds emit a trailing separator with no value after it
    # (e.g. "1920x1080x") — only the first two fields are ever meaningful.
    w, h = result.stdout.strip().split("x")[:2]
    return int(w), int(h)


def burn_captions(src: Path, ass_path: Path, out_path: Path):
    # A drive-letter colon (e.g. "C:") in the path breaks the ass filter's
    # own colon-separated option parsing no matter how it's escaped, so we
    # sidestep it entirely: run with cwd set to the file's own folder and
    # reference it by bare filename, which never contains a colon.
    run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src), "-vf", f"ass={ass_path.name}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy", str(out_path),
    ], cwd=str(ass_path.parent))


# ---------------------------------------------------------------------------
# Quality check
# ---------------------------------------------------------------------------

def verify_plays_cleanly(path: Path):
    result = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"])
    return result.stderr.strip() == "", result.stderr.strip()


def diff_ratio(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def quality_check(final_path: Path, expected_duration: float, caption_text: str,
                   settings, workdir: Path):
    report = {}

    actual_duration = probe_duration(final_path)
    drift = abs(actual_duration - expected_duration)
    report["duration_expected"] = round(expected_duration, 3)
    report["duration_actual"] = round(actual_duration, 3)
    report["duration_drift_seconds"] = round(drift, 3)
    report["duration_ok"] = drift <= settings["quality_check"]["max_duration_drift_seconds"]

    plays_clean, playback_errors = verify_plays_cleanly(final_path)
    report["plays_back_cleanly"] = plays_clean
    if not plays_clean:
        report["playback_errors"] = playback_errors

    if settings["quality_check"]["verify_captions_against_audio"]:
        audio_path = workdir / "final_audio_check.wav"
        run_ffmpeg(["ffmpeg", "-y", "-i", str(final_path), "-ac", "1", "-ar", "16000", str(audio_path)])
        recheck_words = transcribe_words(audio_path, settings["whisper_model"])
        recheck_text = " ".join(w.text for w in recheck_words)
        ratio = diff_ratio(caption_text, recheck_text)
        report["caption_audio_match_ratio"] = round(ratio, 4)
        report["captions_match_audio"] = ratio >= 0.90
    else:
        report["captions_match_audio"] = None

    report["all_checks_passed"] = bool(
        report["duration_ok"] and report["plays_back_cleanly"] and report["captions_match_audio"] is not False
    )
    return report


# ---------------------------------------------------------------------------
# Per-clip and per-batch processing
# ---------------------------------------------------------------------------

@dataclass
class ProcessedClip:
    cleaned_path: Path
    keep_segments: list
    words: list  # remapped onto this clip's own cleaned timeline


def process_single_clip(src: Path, settings, workdir: Path) -> ProcessedClip:
    duration = probe_duration(src)
    ed = settings["editing"]
    silences = detect_silences(src, ed["silence_threshold_db"], ed["min_silence_to_cut_seconds"])
    words = transcribe_words(src, settings["whisper_model"])
    keep_segments = compute_keep_segments(duration, silences, words, settings)
    if not keep_segments:
        keep_segments = [(0.0, duration)]

    cleaned_path = workdir / f"cleaned_{src.stem}.mp4"
    cut_segments_and_concat(src, keep_segments, cleaned_path, workdir)
    remapped_words = remap_words_to_keep_segments(words, keep_segments)
    return ProcessedClip(cleaned_path, keep_segments, remapped_words)


def process_batch(raw_files, settings):
    """raw_files: list of Paths, sorted in the order they should appear in
    the finished video (filename order — name your clips 01_, 02_, ... if
    you film one line at a time)."""
    output_dir = expand(settings["folders"]["output"])
    transcripts_dir = expand(settings["folders"]["transcripts"])
    output_dir.mkdir(parents=True, exist_ok=True)
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    workdir = Path(tempfile.mkdtemp(prefix="video_ads_"))
    try:
        processed = [process_single_clip(f, settings, workdir) for f in raw_files]

        # Join cleaned clips in order, offsetting word timestamps to match.
        all_words = []
        offset = 0.0
        for clip in processed:
            for w in clip.words:
                all_words.append(Word(w.start + offset, w.end + offset, w.text))
            offset += probe_duration(clip.cleaned_path)

        final_pre_captions = workdir / "final_no_captions.mp4"
        concat_final_clips([c.cleaned_path for c in processed], final_pre_captions, workdir)

        must_spell = settings["spelling"]["must_spell_correctly"]
        cues = build_cues(all_words, settings["captions"]["max_words_per_screen"], must_spell)
        caption_text = " ".join(c.text for c in cues)

        w, h = probe_dimensions(final_pre_captions)
        ass_path = workdir / "captions.ass"
        write_ass(cues, settings, ass_path, w, h)

        now = datetime.now()
        base_name = raw_files[0].stem if len(raw_files) == 1 else f"{raw_files[0].stem}_batch"
        naming = settings["output_naming"]
        final_name = naming["pattern"].format(
            basename=base_name, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H%M%S")
        )
        transcript_name = naming["transcript_pattern"].format(
            basename=base_name, date=now.strftime("%Y-%m-%d"), time=now.strftime("%H%M%S")
        )

        final_path = output_dir / final_name
        burn_captions(final_pre_captions, ass_path, final_path)

        expected_duration = offset
        report = quality_check(final_path, expected_duration, caption_text, settings, workdir)

        transcript_path = transcripts_dir / transcript_name
        with open(transcript_path, "w") as f:
            f.write(f"Transcript for: {final_name}\n")
            f.write(f"Source clip(s): {', '.join(p.name for p in raw_files)}\n\n")
            f.write("=== QUALITY CHECK ===\n")
            for k, v in report.items():
                f.write(f"{k}: {v}\n")
            if not report["all_checks_passed"]:
                f.write(
                    "\n*** ATTENTION: one or more checks failed. Please review the finished "
                    "video yourself before publishing. ***\n"
                )
            f.write("\n=== TRANSCRIPT (as captioned) ===\n")
            for c in cues:
                f.write(f"[{c.start:6.2f} - {c.end:6.2f}] {c.text}\n")

        print(f"Done: {final_path}")
        print(f"Transcript: {transcript_path}")
        if not report["all_checks_passed"]:
            print("WARNING: quality check flagged an issue — see transcript file for details.")
        return final_path, transcript_path, report
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# CLI: process whatever is currently in the raw folder, once
# ---------------------------------------------------------------------------

def find_unprocessed_files(raw_dir: Path, state: dict):
    files = sorted(
        p for p in raw_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    )
    return [f for f in files if state.get(str(f)) != file_fingerprint(f)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process current folder contents and exit.")
    args = parser.parse_args()

    settings = load_settings()
    raw_dir = expand(settings["folders"]["raw"])

    if not raw_dir.exists():
        print(f"Raw folder does not exist yet: {raw_dir}")
        sys.exit(1)

    state = load_state()
    new_files = find_unprocessed_files(raw_dir, state)
    if not new_files:
        print("Nothing new to process.")
        return

    print(f"Found {len(new_files)} new clip(s): {', '.join(f.name for f in new_files)}")
    process_batch(new_files, settings)

    for f in new_files:
        state[str(f)] = file_fingerprint(f)
    save_state(state)


if __name__ == "__main__":
    main()
