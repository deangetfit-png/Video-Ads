#!/usr/bin/env python3
"""
Read-only diagnostic: shows exactly what the transcriber heard and what
the silence detector flagged for a single clip, without cutting or writing
anything. Usage:
  .\\.venv\\Scripts\\python.exe scripts\\diagnose.py "path\\to\\clip.mov"
"""
import sys
from pathlib import Path

import pipeline as p

def main():
    if len(sys.argv) != 2:
        print("Usage: diagnose.py <path-to-clip>")
        sys.exit(1)

    src = Path(sys.argv[1]).resolve()
    settings = p.load_settings()
    ed = settings["editing"]

    duration = p.probe_duration(src)
    print(f"File: {src}")
    print(f"Duration (ffprobe): {duration:.3f}s")
    print()

    print("Transcribing (this loads the model on first run, may take a bit)...")
    words = p.transcribe_words(src, settings["whisper_model"])
    print(f"\nWords found by transcriber: {len(words)}")
    for w in words:
        print(f"  [{w.start:7.3f} - {w.end:7.3f}] {w.text!r}")

    print()
    silences = p.detect_silences(src, ed["silence_threshold_db"], ed["min_silence_to_cut_seconds"])
    print(f"Silence spans detected (threshold={ed['silence_threshold_db']}dB, min={ed['min_silence_to_cut_seconds']}s):")
    for s, e in silences:
        print(f"  {s:7.3f} - {e:7.3f}  ({e - s:.3f}s)")

    keep = p.compute_keep_segments(duration, silences, words, settings)
    print()
    print("Segments that WOULD be kept after trimming:")
    total_kept = 0.0
    for s, e in keep:
        print(f"  {s:7.3f} - {e:7.3f}  ({e - s:.3f}s)")
        total_kept += e - s
    print(f"\nTotal kept: {total_kept:.3f}s out of {duration:.3f}s original")

if __name__ == "__main__":
    main()
