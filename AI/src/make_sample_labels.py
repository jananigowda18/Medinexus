"""
Makes a practice set of 6 medicine-label images in  images/  and (optionally) checks
how well image_ide.py reads them.

    python src/make_sample_labels.py          # create images/label_01.png ... label_06.png
    python src/make_sample_labels.py --test   # create them, then run the OCR on each and score it
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "images"

# (lines on the label, expected result, difficulty 0=clean 1=tilt+blur+noise)
LABELS = [
    (["Paracetamol 500mg", "Batch No: M01-261044", "Mfg: 03/2025", "Exp: 02/2027"],
     dict(medicine_id="M01", batch_number="M01-261044", expiry_date="2027-02-28", manufacturing_date="2025-03-01"), 0),
    (["ORS Sachet", "B.No. 7788", "Mfd 01-2025", "Exp. 12-2026"],
     dict(medicine_id="M02", batch_number="7788", expiry_date="2026-12-31", manufacturing_date="2025-01-01"), 0),
    (["Artesunate 60mg", "LOT NO: L55/2", "MFG DATE: 05/2025", "EXPIRY DATE: 04/2028"],
     dict(medicine_id="M08", batch_number="L55/2", expiry_date="2028-04-30", manufacturing_date="2025-05-01"), 0),
    (["Insulin Vial", "BATCH: X9Z-77", "MFD: MAR 2025", "EXPIRES: JAN 2027"],
     dict(medicine_id="M09", batch_number="X9Z-77", expiry_date="2027-01-31", manufacturing_date="2025-03-01"), 1),
    (["Amoxicillin 500mg", "Batch No. AB1234", "Mfg. Date 06/2025", "Exp. Date 05/2027"],
     dict(medicine_id="M03", batch_number="AB1234", expiry_date="2027-05-31", manufacturing_date="2025-06-01"), 1),
    (["Metformin 500mg", "B. No: MF-90321", "MFG: 04/2025", "EXP: 03/2028"],
     dict(medicine_id="M05", batch_number="MF-90321", expiry_date="2028-03-31", manufacturing_date="2025-04-01"), 1),
]


def get_font(size):
    for name in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_label(lines, hard, seed):
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (800, 340), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 791, 331], outline="black", width=3)
    font = get_font(40)
    for i, t in enumerate(lines):
        d.text((40, 28 + i * 75), t, fill="black", font=font)
    if hard:
        img = img.rotate(float(rng.uniform(-3, 3)), expand=True, fillcolor="white")
        img = img.filter(ImageFilter.GaussianBlur(0.9))
        arr = np.asarray(img).astype(float) + rng.normal(0, 9, (img.height, img.width, 1))
        img = Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
    return img


def main():
    OUT.mkdir(exist_ok=True)
    for i, (lines, _, hard) in enumerate(LABELS, 1):
        make_label(lines, hard, seed=i).save(OUT / f"label_{i:02d}.png")
    print(f"Saved {len(LABELS)} images to {OUT}")

    if "--test" in sys.argv:
        sys.path.insert(0, str(Path(__file__).parent))
        from image_ide import extract_medicine_details
        fields = ["medicine_id", "batch_number", "expiry_date", "manufacturing_date"]
        right = total = 0
        print("\nOCR check (field correct?):")
        for i, (_, truth, hard) in enumerate(LABELS, 1):
            got = extract_medicine_details(str(OUT / f"label_{i:02d}.png"))
            marks = []
            for f in fields:
                ok = got[f] == truth[f]
                right += ok
                total += 1
                marks.append(f"{f.split('_')[0]}:{'ok' if ok else 'WRONG (' + str(got[f]) + ')'}")
            print(f"  label_{i:02d} {'(noisy)' if hard else '(clean)'}  " + "  ".join(marks))
        print(f"\nFields read correctly: {right}/{total} ({100 * right / total:.0f}%)")


if __name__ == "__main__":
    main()