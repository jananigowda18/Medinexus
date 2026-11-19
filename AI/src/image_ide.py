"""
Read a medicine pack photo (OCR) and pull out the batch number, expiry date,
manufacturing date and medicine name. The result can pre-fill the
"Receive stock" screen.

Needs:  pip install pillow pytesseract   AND the Tesseract OCR program installed
        on the computer (Windows: https://github.com/UB-Mannheim/tesseract/wiki)

Run:    python src/image_ide.py path\\to\\photo.jpg
        python src/image_ide.py images          (reads EVERY image in the folder -> images/ocr_results.csv)
Test the text-reading part without any image:   python src/image_ide.py --selftest
"""

import calendar
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDICINES_CSV = ROOT / "data" / "raw" / "medicines.csv"

DATE = (r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"          # 15/03/2027
        r"|\d{1,2}[/\-.]\d{2,4}"                      # 03/2027
        r"|[A-Za-z]{3,9}\.?[\s,/\-]*\d{2,4})")        # MAR 2027

EXP_RE = re.compile(r"(?:EXP(?:IRY|IRES)?)\.?\s*(?:DATE)?\s*[:.\-]?\s*" + DATE, re.I)
MFG_RE = re.compile(r"(?:MFG|MFD|MANUF(?:ACTURED|ACTURING)?)\.?\s*(?:DATE)?\s*[:.\-]?\s*" + DATE, re.I)
BATCH_RE = re.compile(r"(?:BATCH|B\.?\s*NO|LOT)\.?\s*(?:NO|NUMBER|#)?\.?\s*[:.\-#]?\s*([A-Z0-9][A-Z0-9/\-]{2,})", re.I)

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def normalise_date(raw, last_day_if_month_only=False):
    """Turn what was printed on the pack into YYYY-MM-DD (or return it unchanged)."""
    s = raw.strip().rstrip(".")
    parts = re.split(r"[/\-.\s,]+", s)
    try:
        if parts[0].isalpha():                                  # MAR 2027
            month, year = MONTHS[parts[0][:3].lower()], int(parts[1])
            day = None
        elif len(parts) == 3:                                   # 15/03/2027
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        else:                                                   # 03/2027
            day, month, year = None, int(parts[0]), int(parts[1])
        year += 2000 if year < 100 else 0
        if day is None:
            day = calendar.monthrange(year, month)[1] if last_day_if_month_only else 1
        return f"{year:04d}-{month:02d}-{day:02d}"
    except (ValueError, KeyError, IndexError):
        return raw


def load_medicines():
    if not MEDICINES_CSV.exists():
        return []
    import csv
    with open(MEDICINES_CSV, newline="", encoding="utf-8") as f:
        return [(r["medicine_id"], r["medicine_name"]) for r in csv.DictReader(f)]


def match_medicine(text):
    """Best match against our medicine list: full name beats first word."""
    low = text.lower()
    best, score = (None, None), 0
    for mid, name in load_medicines():
        n = name.lower()
        first = n.split()[0]
        s = 1.0 if n in low else 0.8 if len(first) >= 4 and first in low else 0
        if s > score:
            best, score = (mid, name), s
    return best


def parse_label_text(text):
    exp, mfg, batch = EXP_RE.search(text), MFG_RE.search(text), BATCH_RE.search(text)
    mid, name = match_medicine(text)
    return {
        "medicine_id": mid,
        "medicine_name": name,
        "batch_number": batch.group(1).upper() if batch else None,
        "expiry_date": normalise_date(exp.group(1), True) if exp else None,
        "manufacturing_date": normalise_date(mfg.group(1)) if mfg else None,
        "raw_text": text,
    }


def find_tesseract():
    """Look for tesseract.exe in every place the Windows installer may have put it."""
    import os
    import shutil
    candidates = [os.environ.get("TESSERACT_CMD", ""),
                  r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
                  os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe")]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return shutil.which("tesseract")


def extract_text(image_path):
    from PIL import Image, ImageOps
    import pytesseract
    if not Path(image_path).is_file():
        sys.exit(f"File not found: {image_path}\n"
                 "Type the path of a PHOTO, for example  sample_label.png  (not a command).")
    exe = find_tesseract()
    if exe:
        pytesseract.pytesseract.tesseract_cmd = exe
    img = ImageOps.grayscale(Image.open(image_path))
    if img.width < 1200:                                        # small photos read poorly
        img = img.resize((img.width * 2, img.height * 2))
    try:
        return pytesseract.image_to_string(img)
    except pytesseract.TesseractNotFoundError:
        sys.exit("Tesseract OCR was not found. Install it, or set the full path to tesseract.exe in the\n"
                 "environment variable TESSERACT_CMD (see the note at the top of this file).")


def extract_medicine_details(image_path):
    return parse_label_text(extract_text(image_path))


def read_folder(folder):
    """Read every image in a folder and save the results to <folder>/ocr_results.csv."""
    import csv
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
    files = sorted(f for f in Path(folder).iterdir() if f.suffix.lower() in exts)
    if not files:
        sys.exit(f"No images found in {folder}")
    cols = ["file", "medicine_id", "medicine_name", "batch_number", "expiry_date", "manufacturing_date"]
    rows = []
    for f in files:
        r = extract_medicine_details(str(f))
        rows.append({"file": f.name, **{c: r[c] for c in cols[1:]}})
        print(f"{f.name:<22} {str(r['medicine_name']):<20} batch={r['batch_number']}  "
              f"exp={r['expiry_date']}  mfg={r['manufacturing_date']}")
    out = Path(folder) / "ocr_results.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nRead {len(rows)} images. Results saved to {out}")


def selftest():
    samples = [
        "Paracetamol 500mg\nBatch No: M01-261044\nMfg: 03/2025\nExp: 02/2027",
        "Batch No. AB1234\nMFG DATE: 03/2025\nEXP DATE: 02/2027\nArtesunate",
        "B.No. 7788  Mfd 01-2025  Exp. 12-2026  ORS Sachet",
        "LOT NO: L55/2\nMfg Date 05/2025 Expiry Date 04/2028\nInsulin Vial",
        "BATCH: X9Z-77  MFD: MAR 2025  EXPIRES: JAN 2027",
    ]
    for t in samples:
        r = parse_label_text(t)
        print(repr(t.replace("\n", " | ")))
        print("  ->", {k: v for k, v in r.items() if k != "raw_text"})


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit()
    path = sys.argv[1] if len(sys.argv) > 1 else input("Enter medicine image path (or a folder): ").strip().strip('"')
    if Path(path).is_dir():
        read_folder(path)
        sys.exit()
    r = extract_medicine_details(path)
    print("\nExtracted Medicine Information\n" + "-" * 32)
    print("Medicine:           ", r["medicine_name"], f"({r['medicine_id']})" if r["medicine_id"] else "")
    print("Batch number:       ", r["batch_number"])
    print("Expiry date:        ", r["expiry_date"])
    print("Manufacturing date: ", r["manufacturing_date"])
    print("\nRaw OCR text:\n" + r["raw_text"])