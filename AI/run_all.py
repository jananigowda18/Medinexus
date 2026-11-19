"""
Runs the whole AI pipeline in the right order with one command.

    python run_all.py                  # regenerate the fake data, then run everything
    python run_all.py --skip-generate  # keep existing data/raw, just re-run the models
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent if HERE.name == "src" else HERE   # works from the project root or from src/
STEPS = [
    ("generate_data.py", "Generate synthetic data"),
    ("features.py",      "Build feature table"),
    ("forecast.py",      "Demand forecast"),
    ("shortage.py",      "Shortage risk"),
    ("expiry.py",        "Expiry / wastage risk"),
    ("spikes.py",        "Outbreak & spike detection"),
    ("recommend.py",     "Recommendations"),
]

skip_generate = "--skip-generate" in sys.argv
start = time.time()
for script, label in STEPS:
    if skip_generate and script == "generate_data.py":
        print(f"-- skipped: {label}")
        continue
    print(f"\n=== {label} ({script}) ===", flush=True)
    t = time.time()
    result = subprocess.run([sys.executable, str(ROOT / "src" / script)], cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f"\nFAILED at {script}. Fix it and run again.")
    print(f"   done in {time.time() - t:.1f}s")

print(f"\nAll steps finished in {time.time() - start:.0f}s. Hand these to the team:")
for f in sorted((ROOT / "outputs").glob("*.csv")):
    print(f"   outputs/{f.name}")