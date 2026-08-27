#!/usr/bin/env python3
"""
Watches your raw footage folder and automatically runs the editing pipeline
on anything new you drop in it. Leave this running in a terminal window
while you work; press Ctrl+C to stop it. It never touches your originals.
"""
import time

import pipeline as p

POLL_SECONDS = 5
DEBOUNCE_SECONDS = 8  # wait this long after the last new file appears before
                       # processing, so a batch of clips dropped together get
                       # joined into one video instead of processed one by one.


def sizes_stable(files):
    before = {f: f.stat().st_size for f in files}
    time.sleep(2)
    after = {f: f.stat().st_size for f in files}
    return before == after


def main():
    settings = p.load_settings()
    raw_dir = p.expand(settings["folders"]["raw"])
    output_dir = p.expand(settings["folders"]["output"])
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    print(f"Watching {raw_dir} for new clips. Press Ctrl+C to stop.")
    state = p.load_state()
    pending = {}  # path -> first-seen timestamp

    while True:
        try:
            candidates = p.find_unprocessed_files(raw_dir, state)
            now = time.time()

            for f in candidates:
                pending.setdefault(f, now)
            for f in list(pending):
                if f not in candidates:
                    del pending[f]

            ready = [f for f, first_seen in pending.items() if now - first_seen >= DEBOUNCE_SECONDS]

            if ready and sizes_stable(ready):
                ready.sort()
                print(f"\nProcessing {len(ready)} new clip(s): {', '.join(f.name for f in ready)}")
                try:
                    p.process_batch(ready, settings, output_dir)
                    for f in ready:
                        state[str(f)] = p.file_fingerprint(f)
                    p.save_state(state)
                except Exception as exc:
                    print(f"ERROR processing batch: {exc}")
                    print("Files left untouched; will retry next cycle unless you fix the issue.")
                for f in ready:
                    pending.pop(f, None)

            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("\nStopped watching.")
            break


if __name__ == "__main__":
    main()
