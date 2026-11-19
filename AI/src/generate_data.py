"""
STEP 1 - Synthetic data generator (Person 3 - AI/ML)

Creates fake-but-realistic data so the models can be built before the real
database exists. Run from the project root:

    python src/generate_data.py

Writes to data/raw/:
    suppliers.csv      supplier reliability + lead times
    medicines.csv      10 medicines (category, shelf life, lead time, flags)
    facilities.csv     9 facilities in 4 districts (with lat/lon)
    daily_demand.csv   365 days x 9 facilities x 10 medicines of units dispensed
    batches.csv        CURRENT stock snapshot, one row per batch (expiry, QR)
    ground_truth.csv   the events we injected (only for evaluating the models;
                       never feed this to a model)
"""

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SEED = 42
N_DAYS = 365
END_DATE = pd.Timestamp("2026-10-18")   # "today" in the demo = last data day

rng = np.random.default_rng(SEED)

# --------------------------------------------------------------------------
# Master data
# --------------------------------------------------------------------------
suppliers = pd.DataFrame([
    # supplier_id, name, avg lead time, lead time std, on-time reliability
    ("S01", "Karnataka Generic Pharma", 4, 1.0, 0.95),
    ("S02", "Sunrise Healthcare Distributors", 5, 1.5, 0.90),
    ("S03", "Medico Bulk Supplies", 7, 2.0, 0.85),
    ("S04", "LifeLine Hospital Supplies", 5, 2.5, 0.78),
    ("S05", "ColdChain Biologicals", 8, 1.5, 0.92),
], columns=["supplier_id", "supplier_name", "avg_lead_time_days",
            "lead_time_std_days", "on_time_rate"])

# medicine_id, name, category, base daily units (at facility scale 1.0),
# shelf life (days), unit cost (INR), supplier, lead time, season, outbreak group,
# cold chain?, criticality (1 low - 3 life-saving)
MED_ROWS = [
    ("M01", "Paracetamol 500mg", "Antipyretic",   120, 730,   0.5, "S01", 5, "none",    True,  False, 2),
    ("M02", "ORS Sachet",        "Rehydration",    60, 540,   8.0, "S01", 4, "monsoon", True,  False, 3),
    ("M03", "Amoxicillin 500mg", "Antibiotic",     80, 540,   4.0, "S02", 6, "winter",  False, False, 2),
    ("M04", "Cough Syrup",       "Respiratory",    40, 365,  35.0, "S02", 5, "winter",  True,  False, 1),
    ("M05", "Metformin 500mg",   "Chronic",       150, 730,   1.2, "S03", 7, "none",    False, False, 2),
    ("M06", "Amlodipine 5mg",    "Chronic",       130, 730,   1.0, "S03", 7, "none",    False, False, 2),
    ("M07", "IV Normal Saline",  "IV Fluid",       50, 540,  25.0, "S04", 4, "monsoon", True,  False, 3),
    ("M08", "Artesunate",        "Antimalarial",   15, 365,  60.0, "S04", 10, "monsoon", True,  False, 3),
    ("M09", "Insulin Vial",      "Cold Chain",     12, 270, 450.0, "S05", 8, "none",    False, True,  3),
    ("M10", "Rabies Vaccine",    "Cold Chain",      5, 365, 300.0, "S05", 10, "none",    False, True,  3),
]
medicines = pd.DataFrame(MED_ROWS, columns=[
    "medicine_id", "medicine_name", "category", "base_daily_demand",
    "shelf_life_days", "unit_cost_inr", "supplier_id", "lead_time_days",
    "season", "outbreak_sensitive", "cold_chain", "criticality"])

# facility_id, name, type, district, scale (size), lat, lon
facilities = pd.DataFrame([
    ("F01", "City District Hospital",   "District Hospital", "Bengaluru Urban", 1.8, 12.9716, 77.5946),
    ("F02", "Yelahanka CHC",            "CHC",               "Bengaluru Urban", 1.0, 13.1007, 77.5963),
    ("F03", "Jayanagar PHC",            "PHC",               "Bengaluru Urban", 0.7, 12.9308, 77.5838),
    ("F04", "Mysuru District Hospital", "District Hospital", "Mysuru",          1.5, 12.2958, 76.6394),
    ("F05", "Tumakuru CHC",             "CHC",               "Tumakuru",        0.9, 13.3392, 77.1140),
    # Mangaluru (coastal Karnataka). Names are for demo only; coordinates are approximate.
    ("F06", "Wenlock District Hospital", "District Hospital", "Dakshina Kannada", 1.7, 12.8700, 74.8430),
    ("F07", "Lady Goschen Hospital",     "Maternity Hospital", "Dakshina Kannada", 0.8, 12.8680, 74.8420),
    ("F08", "Surathkal CHC",             "CHC",               "Dakshina Kannada", 0.9, 12.9810, 74.7950),
    ("F09", "Ullal PHC",                 "PHC",               "Dakshina Kannada", 0.6, 12.8000, 74.8430),
], columns=["facility_id", "facility_name", "facility_type", "district",
            "size_scale", "lat", "lon"])

# --------------------------------------------------------------------------
# Injected outbreaks (ground truth for testing the spike detector)
# --------------------------------------------------------------------------
# shape: ramp up -> plateau -> decay.  plateau/decay = None means "still ongoing"
OUTBREAKS = [
    dict(name="Dengue", district="Bengaluru Urban", start="2026-10-04",
         ramp=7, plateau=10_000, decay=1,
         peaks={"M01": 2.6, "M02": 2.2, "M07": 2.8, "M08": 3.0}),
    dict(name="Influenza", district="Mysuru", start="2026-01-10",
         ramp=6, plateau=8, decay=14,
         peaks={"M04": 2.5, "M03": 1.8, "M01": 1.7}),
]


def outbreak_multiplier(day_index, start_index, ramp, plateau, decay, peak):
    """Demand multiplier for one outbreak at one day (vectorised)."""
    t = day_index - start_index
    m = np.ones_like(t, dtype=float)
    up = (t >= 0) & (t < ramp)
    top = (t >= ramp) & (t < ramp + plateau)
    down = (t >= ramp + plateau) & (t < ramp + plateau + decay)
    m[up] = 1 + (peak - 1) * t[up] / ramp
    m[top] = peak
    m[down] = peak - (peak - 1) * (t[down] - ramp - plateau) / decay
    return m


# --------------------------------------------------------------------------
# Daily demand
# --------------------------------------------------------------------------
dates = pd.date_range(end=END_DATE, periods=N_DAYS, freq="D")
day_idx = np.arange(N_DAYS)
dow = dates.dayofweek.values
doy = dates.dayofyear.values

# OPD pattern: busy weekdays, quieter Saturday, very quiet Sunday
weekly = np.select([dow <= 4, dow == 5], [1.0, 0.8], default=0.5)

SEASON_PEAK = {"monsoon": (230, 0.35), "winter": (15, 0.30)}  # (peak day-of-year, amplitude)


def seasonal_factor(season):
    if season == "none":
        return np.ones(N_DAYS)
    peak, amp = SEASON_PEAK[season]
    return 1 + amp * np.cos(2 * np.pi * (doy - peak) / 365)


rows = []
for _, f in facilities.iterrows():
    for _, m in medicines.iterrows():
        mean = m.base_daily_demand * f.size_scale * weekly * seasonal_factor(m.season)

        # outbreak effect (only in the district + medicines it applies to)
        ob_mult = np.ones(N_DAYS)
        ob_flag = np.zeros(N_DAYS, dtype=bool)
        for ob in OUTBREAKS:
            if f.district == ob["district"] and m.medicine_id in ob["peaks"]:
                start_idx = int((pd.Timestamp(ob["start"]) - dates[0]).days)
                mult = outbreak_multiplier(day_idx, start_idx, ob["ramp"],
                                           ob["plateau"], ob["decay"],
                                           ob["peaks"][m.medicine_id])
                ob_mult *= mult
                ob_flag |= mult > 1.0

        lam = mean * ob_mult * rng.gamma(shape=30, scale=1 / 30, size=N_DAYS)  # day-to-day noise
        units = rng.poisson(lam)

        rows.append(pd.DataFrame({
            "date": dates,
            "facility_id": f.facility_id,
            "medicine_id": m.medicine_id,
            "units_dispensed": units,
            "day_of_week": dow,
            "month": dates.month,
        }))

daily = pd.concat(rows, ignore_index=True)

# --------------------------------------------------------------------------
# Current stock snapshot as batches
# --------------------------------------------------------------------------
# "normal" demand level = average of days 60..20 before END_DATE (before dengue starts)
ref_start, ref_end = END_DATE - pd.Timedelta(days=60), END_DATE - pd.Timedelta(days=20)
ref = (daily[(daily.date >= ref_start) & (daily.date <= ref_end)]
       .groupby(["facility_id", "medicine_id"]).units_dispensed.mean()
       .rename("normal_daily_demand"))

med_idx = medicines.set_index("medicine_id")
fac_idx = facilities.set_index("facility_id")
dengue_meds = set(OUTBREAKS[0]["peaks"])

batch_rows, truth_rows = [], []
batch_counter = 0
for (fid, mid), normal in ref.items():
    m = med_idx.loc[mid]
    in_dengue_zone = fac_idx.loc[fid, "district"] == "Bengaluru Urban" and mid in dengue_meds

    # days of cover on hand: Bengaluru dengue medicines are deliberately thin
    cover = rng.uniform(5, 14) if in_dengue_zone else rng.uniform(8, 45)
    total_stock = max(5, int(normal * cover))

    n_batches = int(rng.integers(1, 4))
    shares = rng.dirichlet(np.ones(n_batches))
    qtys = np.maximum(1, (shares * total_stock).astype(int))
    days_left = rng.uniform(20, m.shelf_life_days * 0.9, size=n_batches).astype(int)

    # plant overstocked, near-expiry batches (waste scenario), not in dengue zone
    if not in_dengue_zone and not m.cold_chain and rng.random() < 0.18:
        extra_qty = int(normal * rng.uniform(70, 130))
        extra_days = int(rng.integers(20, 50))
        qtys = np.append(qtys, extra_qty)
        days_left = np.append(days_left, extra_days)
        truth_rows.append(dict(event_type="planted_overstock", facility_id=fid,
                               medicine_id=mid, start_date="", district="",
                               notes=f"{extra_qty} units expiring in {extra_days} days"))

    for q, d in zip(qtys, days_left):
        batch_counter += 1
        expiry = END_DATE + pd.Timedelta(days=int(d))
        batch_no = f"{mid}-{expiry.year % 100}{rng.integers(1000, 9999)}"
        batch_rows.append(dict(
            batch_id=f"B{batch_counter:04d}",
            batch_number=batch_no,
            qr_code="QR-" + hashlib.sha1(batch_no.encode()).hexdigest()[:12].upper(),
            facility_id=fid,
            medicine_id=mid,
            supplier_id=m.supplier_id,
            manufacture_date=(expiry - pd.Timedelta(days=int(m.shelf_life_days))).date(),
            expiry_date=expiry.date(),
            quantity_on_hand=int(q),
        ))

batches = pd.DataFrame(batch_rows)

for ob in OUTBREAKS:
    truth_rows.append(dict(event_type=f"outbreak_{ob['name'].lower()}", facility_id="",
                           medicine_id=",".join(ob["peaks"]), start_date=ob["start"],
                           district=ob["district"],
                           notes=f"ramp {ob['ramp']}d, peaks {ob['peaks']}"))
ground_truth = pd.DataFrame(truth_rows)

# --------------------------------------------------------------------------
# Save
# --------------------------------------------------------------------------
RAW.mkdir(parents=True, exist_ok=True)
suppliers.to_csv(RAW / "suppliers.csv", index=False)
medicines.to_csv(RAW / "medicines.csv", index=False)
facilities.to_csv(RAW / "facilities.csv", index=False)
daily.to_csv(RAW / "daily_demand.csv", index=False)
batches.to_csv(RAW / "batches.csv", index=False)
ground_truth.to_csv(RAW / "ground_truth.csv", index=False)

print(f"Saved to {RAW}")
print(f"  facilities      : {len(facilities)}")
print(f"  medicines       : {len(medicines)}")
print(f"  daily_demand    : {len(daily):,} rows  ({dates[0].date()} -> {dates[-1].date()})")
print(f"  batches         : {len(batches)} rows, {batches.quantity_on_hand.sum():,} units")
print(f"  planted overstock batches: {(ground_truth.event_type == 'planted_overstock').sum()}")
print(f"  injected outbreaks: {[o['name'] + ' @ ' + o['district'] + ' from ' + o['start'] for o in OUTBREAKS]}")