"""
STEP 3 - Early shortage prediction (Person 3 - AI/ML)

For every facility x medicine:
  1. days_of_cover        = how long current stock lasts under the forecast
  2. predicted_stockout   = the date stock hits zero
  3. risk_score (0-1)     = probability stock runs out BEFORE a new delivery can
                            arrive (Monte Carlo over forecast uncertainty and
                            supplier lead-time uncertainty)
  4. reorder_qty          = how much to order to cover lead time + 14 days + safety stock

Inputs : data/raw/{batches,medicines,suppliers,facilities}.csv, outputs/forecasts.csv
Output : outputs/shortage_risk.csv

Run from the project root (after forecast.py):
    python src/shortage.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"

Z80 = 1.2816          # P10-P90 interval -> standard deviation
N_SIMS = 5000
MAX_LEAD = 14         # forecast horizon: lead times are capped here
DELAY_DAYS = 3        # extra days when a supplier delivers late
BUFFER_DAYS = 14      # reorder enough to last this long after delivery
SERVICE_Z = 1.28      # ~90% service level for safety stock

rng = np.random.default_rng(42)


def risk_level(risk, days_cover, lead):
    if risk >= 0.70 or days_cover < lead:
        return "CRITICAL"
    if risk >= 0.40:
        return "HIGH"
    if risk >= 0.15:
        return "MEDIUM"
    return "LOW"


def main():
    fc = pd.read_csv(OUT / "forecasts.csv", parse_dates=["date"])
    batches = pd.read_csv(RAW / "batches.csv")
    medicines = pd.read_csv(RAW / "medicines.csv").set_index("medicine_id")
    suppliers = pd.read_csv(RAW / "suppliers.csv").set_index("supplier_id")
    facilities = pd.read_csv(RAW / "facilities.csv").set_index("facility_id")

    stock = batches.groupby(["facility_id", "medicine_id"]).quantity_on_hand.sum()
    start = fc["date"].min() - pd.Timedelta(days=1)   # = the "today" of the stock snapshot

    rows = []
    for (fid, mid), g in fc.groupby(["facility_id", "medicine_id"]):
        g = g.sort_values("date")
        p50 = g["p50"].to_numpy()
        sigma = ((g["p90"] - g["p10"]) / (2 * Z80)).to_numpy()
        s = float(stock.get((fid, mid), 0))
        med = medicines.loc[mid]
        sup = suppliers.loc[med.supplier_id]
        lead = int(med.lead_time_days)

        # 1) days of cover from the median forecast
        cum = np.cumsum(p50)
        if cum[-1] >= s:
            i = int(np.argmax(cum >= s))
            prev = cum[i - 1] if i > 0 else 0.0
            days_cover = i + ((s - prev) / p50[i] if p50[i] > 0 else 0.0)
        else:
            mean_d = p50.mean()
            days_cover = s / mean_d if mean_d > 0 else 999.0   # extrapolate past the horizon
        stockout = (start + pd.Timedelta(days=int(np.ceil(days_cover)))).date() if days_cover <= 60 else None

        # 2) Monte Carlo risk: does cumulative demand exceed stock before delivery lands?
        #    Forecast errors are partly persistent (an outbreak keeps going), so half of
        #    the variance is shared across all days and half is day-specific.
        z_common = rng.standard_normal((N_SIMS, 1))
        z_day = rng.standard_normal((N_SIMS, len(p50)))
        demand = np.clip(p50 + sigma * (np.sqrt(0.5) * z_common + np.sqrt(0.5) * z_day), 0, None)
        cum_sims = np.cumsum(demand, axis=1)

        lead_draw = rng.normal(med.lead_time_days, sup.lead_time_std_days, N_SIMS)
        lead_draw += (rng.random(N_SIMS) > sup.on_time_rate) * DELAY_DAYS
        lead_draw = np.clip(np.round(lead_draw), 1, MAX_LEAD).astype(int)
        demand_until_delivery = cum_sims[np.arange(N_SIMS), lead_draw - 1]
        risk = float((demand_until_delivery > s).mean())

        # 3) reorder quantity: cover lead time + buffer, plus safety stock, minus what we have
        daily = p50.mean()
        daily_sigma = sigma.mean()
        safety = SERVICE_Z * daily_sigma * np.sqrt(lead)
        reorder = max(0, int(np.ceil(daily * (lead + BUFFER_DAYS) + safety - s)))

        rows.append(dict(
            facility_id=fid, medicine_id=mid,
            facility_name=facilities.loc[fid, "facility_name"],
            medicine_name=med.medicine_name,
            current_stock=int(s),
            forecast_daily_demand=round(daily, 1),
            days_of_cover=round(min(days_cover, 999), 1),
            lead_time_days=lead,
            predicted_stockout_date=stockout,
            risk_score=round(risk, 3),
            risk_level=risk_level(risk, days_cover, lead),
            reorder_qty=reorder,
            criticality=int(med.criticality),
        ))

    out = (pd.DataFrame(rows)
           .sort_values(["risk_score", "criticality"], ascending=[False, False])
           .reset_index(drop=True))
    out.to_csv(OUT / "shortage_risk.csv", index=False)

    print(f"shortage_risk.csv: {len(out)} rows")
    print(out["risk_level"].value_counts().reindex(["CRITICAL", "HIGH", "MEDIUM", "LOW"]).fillna(0).astype(int).to_string())
    print("\nTop 10 at-risk items:")
    cols = ["facility_name", "medicine_name", "current_stock", "days_of_cover",
            "lead_time_days", "predicted_stockout_date", "risk_score", "risk_level", "reorder_qty"]
    print(out[cols].head(10).to_string(index=False))


if __name__ == "__main__":
    main()