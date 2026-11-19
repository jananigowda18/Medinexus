import datetime as dt
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"

app = Flask(__name__)
write_lock = threading.Lock()
ai_status = {"state": "idle", "started": None, "finished": None, "returncode": None, "log_tail": ""}


@app.after_request
def allow_frontend(resp):
    """Let a website on another port (e.g. the frontend) call this API."""
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def error(message, code=400, **extra):
    return jsonify({"error": message, **extra}), code


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load(path):
    if not path.exists():
        raise FileNotFoundError(f"{path.name} not found. Run  python run_all.py  first.")
    df = pd.read_csv(path)
    for c in df.columns:                      # 447.0 -> 447 when a column only holds whole numbers
        if df[c].dtype == "float64" and (df[c].dropna() % 1 == 0).all():
            df[c] = df[c].astype("Int64")
    return df


def to_records(df):
    return df.astype(object).where(df.notna(), None).to_dict("records")


def apply_filters(df):
    """?column=value filters (case-insensitive) and ?limit=N."""
    for key, value in request.args.items():
        if key in df.columns:
            df = df[df[key].astype(str).str.lower() == value.lower()]
    limit = request.args.get("limit", type=int)
    return df.head(limit) if limit else df


@app.errorhandler(FileNotFoundError)
def missing_file(e):
    return error(str(e), 404)


def make_table_route(route, folder, filename):
    def view():
        return jsonify(to_records(apply_filters(load(folder / filename))))
    view.__name__ = "table_" + route.replace("-", "_")
    app.add_url_rule(f"/api/{route}", view_func=view)


for _route, _folder, _file in [
    ("recommendations", OUT, "recommendations.csv"),
    ("shortage-risk", OUT, "shortage_risk.csv"),
    ("expiry-risk", OUT, "expiry_risk.csv"),
    ("spike-events", OUT, "spike_events.csv"),
    ("spike-signals", OUT, "spike_signals_latest.csv"),
    ("forecasts", OUT, "forecasts.csv"),
    ("forecast-metrics", OUT, "forecast_metrics.csv"),
    ("facilities", RAW, "facilities.csv"),
    ("medicines", RAW, "medicines.csv"),
    ("suppliers", RAW, "suppliers.csv"),
]:
    make_table_route(_route, _folder, _file)


# --------------------------------------------------------------------------
# summary for the dashboard cards
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    output_file = OUT / "recommendations.csv"

    return jsonify({
        "status": "ok",
        "outputs_ready": output_file.exists(),
        "output_path": str(output_file),
        "root_path": str(ROOT)
    })


@app.get("/api/summary")
def summary():
    sr, ex = load(OUT / "shortage_risk.csv"), load(OUT / "expiry_risk.csv")
    ev, rec = load(OUT / "spike_events.csv"), load(OUT / "recommendations.csv")
    fc = load(OUT / "forecasts.csv")
    as_of = (pd.to_datetime(fc["date"]).min() - pd.Timedelta(days=1)).date().isoformat()
    top = rec.iloc[0] if len(rec) else None
    return jsonify({
        "as_of": as_of,
        "shortages": {lvl.lower(): int((sr.risk_level == lvl).sum()) for lvl in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
        "active_outbreaks": int((ev.status == "ACTIVE").sum()),
        "expiry": {"high_risk_batches": int((ex.risk_level == "HIGH").sum()),
                   "expected_waste_value_inr": int(ex.waste_value_inr.sum())},
        "recommendations": {"total": int(len(rec)),
                            "critical": int((rec.priority_level == "CRITICAL").sum()),
                            "transfers": int((rec.type == "TRANSFER").sum())},
        "top_recommendation": None if top is None else {"rec_id": top.rec_id, "type": top.type,
                                                         "reason_text": top.reason_text},
    })


# --------------------------------------------------------------------------
# delivery receipts -> stock
# --------------------------------------------------------------------------
@app.post("/api/receipts")
def save_receipt():
    """
    JSON body:
    {"receipt_number": "LL-20418", "facility_id": "F02", "supplier_id": "S04",
     "delivery_date": "2026-10-19",            (optional, default today)
     "run_ai": true,                           (optional: re-run the AI after saving)
     "items": [{"medicine_id": "M08", "batch_number": "M08-264771",
                "expiry_date": "2027-11-28", "quantity": 400,
                "manufacturing_date": "2026-06-01",      (optional)
                "arrival_temp": 5.2}]}                   (required for cold-chain medicines)
    """
    body = request.get_json(silent=True) or {}
    receipt_no = str(body.get("receipt_number", "")).strip()
    fid, sid = body.get("facility_id"), body.get("supplier_id")
    items = body.get("items") or []
    if not receipt_no:
        return error("receipt_number is required")
    if not items:
        return error("items is empty: add at least one line from the receipt")

    facilities = load(RAW / "facilities.csv").set_index("facility_id")
    medicines = load(RAW / "medicines.csv").set_index("medicine_id")
    suppliers = load(RAW / "suppliers.csv")
    if fid not in facilities.index:
        return error(f"unknown facility_id: {fid}")
    if sid not in set(suppliers.supplier_id):
        return error(f"unknown supplier_id: {sid}")
    try:
        delivered = dt.date.fromisoformat(body.get("delivery_date") or dt.date.today().isoformat())
    except ValueError:
        return error("delivery_date must look like 2026-10-19")

    accepted, held, problems = [], [], []
    for n, it in enumerate(items, 1):
        mid, batch = it.get("medicine_id"), str(it.get("batch_number", "")).strip()
        try:
            qty = int(it.get("quantity"))
            expiry = dt.date.fromisoformat(str(it.get("expiry_date")))
        except (TypeError, ValueError):
            problems.append(f"item {n}: quantity must be a whole number and expiry_date like 2027-11-28")
            continue
        if mid not in medicines.index:
            problems.append(f"item {n}: unknown medicine_id {mid}")
        elif not batch:
            problems.append(f"item {n}: batch_number is required")
        elif qty <= 0:
            problems.append(f"item {n}: quantity must be greater than 0")
        elif expiry <= delivered:
            problems.append(f"item {n} ({mid} {batch}): already expired or expires on delivery date. Do not accept.")
        elif medicines.loc[mid, "cold_chain"]:
            temp = it.get("arrival_temp")
            if temp is None:
                problems.append(f"item {n} ({mid}): arrival_temp is required for cold-chain medicines")
            elif not 2 <= float(temp) <= 8:
                held.append({"medicine_id": mid, "batch_number": batch, "quantity": qty,
                             "reason": f"arrival temperature {temp}C is outside 2-8C: quarantine and raise a cold-chain alert"})
            else:
                accepted.append((it, mid, batch, qty, expiry))
        else:
            accepted.append((it, mid, batch, qty, expiry))

    if problems:                               # all-or-nothing: fix the receipt and send it again
        return error("receipt not saved", 422, problems=problems)

    with write_lock:
        batches = pd.read_csv(RAW / "batches.csv")
        next_id = int(batches.batch_id.str[1:].astype(int).max()) + 1
        for it, mid, batch, qty, expiry in accepted:
            same = (batches.facility_id == fid) & (batches.medicine_id == mid) & (batches.batch_number == batch)
            if same.any():
                batches.loc[same, "quantity_on_hand"] += qty
            else:
                batches.loc[len(batches)] = {
                    "batch_id": f"B{next_id:04d}", "batch_number": batch,
                    "qr_code": "QR-" + hashlib.sha1(batch.encode()).hexdigest()[:12].upper(),
                    "facility_id": fid, "medicine_id": mid, "supplier_id": sid,
                    "manufacture_date": it.get("manufacturing_date") or "",
                    "expiry_date": expiry.isoformat(), "quantity_on_hand": qty}
                next_id += 1
        batches.to_csv(RAW / "batches.csv", index=False)

        log = pd.DataFrame([{
            "receipt_number": receipt_no, "facility_id": fid, "supplier_id": sid,
            "delivery_date": delivered.isoformat(), "medicine_id": mid, "batch_number": batch,
            "expiry_date": expiry.isoformat(), "quantity": qty, "status": "ACCEPTED",
            "saved_at": dt.datetime.now().isoformat(timespec="seconds")}
            for _, mid, batch, qty, expiry in accepted] + [{
            "receipt_number": receipt_no, "facility_id": fid, "supplier_id": sid,
            "delivery_date": delivered.isoformat(), "medicine_id": h["medicine_id"],
            "batch_number": h["batch_number"], "expiry_date": "", "quantity": h["quantity"],
            "status": "HELD", "saved_at": dt.datetime.now().isoformat(timespec="seconds")} for h in held])
        log_path = RAW / "receipts.csv"
        log.to_csv(log_path, mode="a", header=not log_path.exists(), index=False)

    started = start_ai() if body.get("run_ai") else False
    return jsonify({"saved": True, "receipt_number": receipt_no, "items_added": len(accepted),
                    "units_added": sum(a[3] for a in accepted), "held": held, "ai_rerun_started": started}), 201


# --------------------------------------------------------------------------
# re-run the AI
# --------------------------------------------------------------------------
def start_ai():
    if ai_status["state"] == "running":
        return False

    def job():
        ai_status.update(state="running", started=dt.datetime.now().isoformat(timespec="seconds"),
                         finished=None, returncode=None, log_tail="")
        r = subprocess.run([sys.executable, str(ROOT / "run_all.py"), "--skip-generate"],
                           cwd=ROOT, capture_output=True, text=True)
        ai_status.update(state="done" if r.returncode == 0 else "failed", returncode=r.returncode,
                         finished=dt.datetime.now().isoformat(timespec="seconds"),
                         log_tail=(r.stdout + r.stderr)[-600:])

    ai_status["state"] = "running"
    threading.Thread(target=job, daemon=True).start()
    return True


@app.post("/api/run-ai")
def run_ai():
    if not start_ai():
        return error("the AI is already running", 409, **ai_status)
    return jsonify({"started": True}), 202


@app.get("/api/run-ai/status")
def run_ai_status():
    return jsonify(ai_status)


# --------------------------------------------------------------------------
# OCR: photo of a medicine pack -> fields
# --------------------------------------------------------------------------
@app.post("/api/ocr")
def ocr():
    f = request.files.get("image")
    if f is None:
        return error('send the photo as form field "image"')
    sys.path.insert(0, str(ROOT / "src"))
    from image_ide import extract_medicine_details
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(f.filename or "x.png").suffix or ".png") as tmp:
        f.save(tmp)
    try:
        r = extract_medicine_details(tmp.name)
    except SystemExit as e:
        return error(str(e), 500)
    finally:
        os.unlink(tmp.name)
    r["needs_review"] = any(r[k] is None for k in ("medicine_id", "batch_number", "expiry_date"))
    return jsonify(r)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, threaded=True)