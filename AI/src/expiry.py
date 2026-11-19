"""
STEP 4 - Expiry and wastage detection (Person 3 - AI/ML)

For every batch we ask: "how much of this will we realistically sell before it expires?"

  * Demand until expiry = the 14-day forecast, then the facility's recent long-run
    average (last 90 days) for anything beyond the forecast horizon. The long-run
    average is used on purpose: a short outbreak surge should not make us
    think near-expiry stock will all be sold.
  * Batches of the same medicine at the same facility are consumed FEFO
    (first-expiry-first-out), so earlier-expiring batches get sold first.
  * expected_waste = quantity - expected units sold before expiry
  * expiry_risk    = expected_waste / quantity

Inputs : data/raw/{batches,medicines,facilities,daily_demand}.csv, outputs/forecasts.csv
Output : outputs/expiry_risk.csv

Run from the project root (after forecast.py):
    python src/expiry.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"


def level(risk, days_left):
    if risk >= 0.50:
        return "HIGH"
    if risk >= 0.20:
        return "MEDIUM"
    return "LOW"


def main():
    batches = pd.read_csv(RAW / "batches.csv", parse_dates=["expiry_date"])
    medicines = pd.read_csv(RAW / "medicines.csv").set_index("medicine_id")
    facilities = pd.read_csv(RAW / "facilities.csv").set_index("facility_id")
    daily = pd.read_csv(RAW / "daily_demand.csv", parse_dates=["date"])
    fc = pd.read_csv(OUT / "forecasts.csv", parse_dates=["date"])

    today = daily["date"].max()
    long_run = (daily[daily["date"] > today - pd.Timedelta(days=90)]
                .groupby(["facility_id", "medicine_id"]).units_dispensed.mean())

    rows = []
    for (fid, mid), grp in batches.groupby(["facility_id", "medicine_id"]):
        p50 = fc[(fc.facility_id == fid) & (fc.medicine_id == mid)].sort_values("date")["p50"].to_numpy()
        cum14 = np.concatenate([[0.0], np.cumsum(p50)])        # cum14[d] = demand in first d days
        horizon = len(p50)
        rate = long_run.get((fid, mid), p50.mean())

        def demand_until(days):
            if days <= horizon:
                return float(np.interp(days, np.arange(horizon + 1), cum14))
            return float(cum14[-1] + (days - horizon) * rate)

        consumed = 0   # units of earlier-expiring batches that FEFO sells first
        for _, b in grp.sort_values("expiry_date").iterrows():
            days_left = int((b.expiry_date - today).days)
            available_demand = demand_until(days_left) - consumed
            sold = float(np.clip(available_demand, 0, b.quantity_on_hand))
            waste = b.quantity_on_hand - sold
            consumed += b.quantity_on_hand

            risk = waste / b.quantity_on_hand if b.quantity_on_hand else 0.0
            rows.append(dict(
                batch_id=b.batch_id, batch_number=b.batch_number,
                facility_id=fid, medicine_id=mid,
                facility_name=facilities.loc[fid, "facility_name"],
                medicine_name=medicines.loc[mid, "medicine_name"],
                quantity_on_hand=int(b.quantity_on_hand),
                expiry_date=b.expiry_date.date(),
                days_to_expiry=days_left,
                expected_sold=int(round(sold)),
                expected_waste_units=int(round(waste)),
                expiry_risk=round(risk, 3),
                waste_value_inr=round(waste * medicines.loc[mid, "unit_cost_inr"]),
                risk_level=level(risk, days_left),
            ))

    out = (pd.DataFrame(rows)
           .sort_values(["waste_value_inr"], ascending=False)
           .reset_index(drop=True))
    out.to_csv(OUT / "expiry_risk.csv", index=False)

    print(f"expiry_risk.csv: {len(out)} batches")
    print(out["risk_level"].value_counts().reindex(["HIGH", "MEDIUM", "LOW"]).fillna(0).astype(int).to_string())
    print(f"Total expected waste value: INR {out.waste_value_inr.sum():,.0f}")
    print("\nTop 8 batches by waste value:")
    cols = ["facility_name", "medicine_name", "quantity_on_hand", "days_to_expiry",
            "expected_waste_units", "expiry_risk", "waste_value_inr", "risk_level"]
    print(out[cols].head(8).to_string(index=False))

    # --- evaluation against the overstock batches we planted (ground truth, never a model input) ---
    truth = pd.read_csv(RAW / "ground_truth.csv")
    planted = truth[truth.event_type == "planted_overstock"][["facility_id", "medicine_id"]]
    flagged = out[(out.risk_level == "HIGH") & (out.days_to_expiry <= 50)][["facility_id", "medicine_id"]]
    caught = planted.merge(flagged.drop_duplicates(), on=["facility_id", "medicine_id"]).shape[0]
    false_alarms = (out.risk_level == "HIGH").sum() - caught
    print(f"\nEvaluation: caught {caught}/{len(planted)} planted overstock batches; "
          f"other HIGH-risk batches: {false_alarms}")


if __name__ == "__main__":
    main()