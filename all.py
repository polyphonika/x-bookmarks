"""Run the full pipeline: pull → classify run → push.

Skips `classify.py discover` — that's a one-shot setup with manual
review of topics.json. Each step exits non-zero on failure and the
orchestrator stops there.
"""
import subprocess
import sys

STEPS = [
    ("pull.py", []),
    ("classify.py", ["run"]),
    ("push.py", []),
]


def main() -> None:
    for i, (script, args) in enumerate(STEPS, 1):
        header = f"[{i}/{len(STEPS)}] {script} {' '.join(args)}".rstrip()
        print(f"\n{'=' * 60}\n{header}\n{'=' * 60}", flush=True)
        result = subprocess.run([sys.executable, script, *args])
        if result.returncode != 0:
            print(
                f"\n{script} failed (exit {result.returncode}). Stopping.",
                flush=True,
            )
            sys.exit(result.returncode)
    print("\nAll steps complete.", flush=True)


if __name__ == "__main__":
    main()
