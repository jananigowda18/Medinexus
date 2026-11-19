"""
STEP 5 - Outbreak and demand-spike detection (Person 3 - AI/ML)

Two layers:

 1. SERIES LAYER - flags a facility x medicine when its recent demand is far above
    its own normal level.
      * remove the weekly pattern (Sundays are quiet, so a quiet Sunday is not a dip)
      * baseline = median of days t-35..t-8 (robust to past spikes), noise = MAD
      * flag when the 3-day average is > 4 robust standard errors AND > 1.3x baseline
    The baseline stops 8 days back so a spike that lasts a few weeks does not
    "become normal" and switch itself off. A seasonal-drift correction raises the
    baseline when demand has been rising slowly, so the monsoon season is not
    mistaken for an outbreak.

 2. DISTRICT LAYER - one spike is noise, an outbreak is a pattern. A district
    alert needs >= 2 different outbreak-sensitive medicines (fever, ORS, fluids,
    antimalarials, cough) flagged at the same time, at >= 2 facilities if the
    district has more than one.

The same district rule is applied to a "manual" static threshold (daily demand
> 2x the 30-day average) so the detection delay can be compared fairly.

Outputs
    outputs/spike_events.csv           one row per detected outbreak event
    outputs/spike_signals_latest.csv   current ratio / flag for every facility x medicine

Run from the project root:
    python src/spikes.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"

BASE_LAG, BASE_WIN = 8, 28
Z_MIN, RATIO_MIN = 4.0, 1.3
MAX_DRIFT = 0.25           # max seasonal-drift correction to the baseline
EVENT_GAP_DAYS = 3         # alert days closer than this belong to the same event


# --------------------------------------------------------------------------
# Layer 1: per-series signals
# --------------------------------------------------------------------------
def series_signals(g):
    g = g.sort_values("date").reset_index(drop=True)
    y = g["units_dispensed"].to_numpy(float)
    dow = g["date"].dt.dayofweek.to_numpy()

    # weekday factor: typical demand on that weekday relative to the average weekday
    med_by_dow = pd.Series(y).groupby(dow).median()
    factor = (med_by_dow / med_by_dow.mean()).reindex(range(7)).to_numpy()
    x = pd.Series(y / np.maximum(factor[dow], 0.05))

    lagged = x.shift(BASE_LAG).rolling(BASE_WIN)
    base = lagged.median()
    mad = lagged.apply(lambda a: np.median(np.abs(a - np.median(a))), raw=True)

    # Seasonal-drift correction: if demand has been creeping up slowly (e.g. monsoon
    # season), the baseline is raised by the drift expected by today, capped at +25%.
    # An outbreak is far too fast and recent to be absorbed by this correction.
    xs = x.shift(BASE_LAG)
    first_half = xs.rolling(BASE_WIN).apply(lambda a: np.median(a[:BASE_WIN // 2]), raw=True)
    second_half = xs.rolling(BASE_WIN).apply(lambda a: np.median(a[BASE_WIN // 2:]), raw=True)
    slope = (second_half - first_half) / (BASE_WIN / 2)          # units per day
    base = base + (slope * (BASE_LAG + BASE_WIN // 4)).clip(lower=0, upper=MAX_DRIFT * base)
    sigma = np.maximum.reduce([1.4826 * mad, np.sqrt(base.clip(lower=1)), 0.10 * base])

    mean3 = x.rolling(3).mean()
    z3 = (mean3 - base) / (sigma / np.sqrt(3))
    ratio3 = mean3 / base
    flag_robust = (z3 > Z_MIN) & (ratio3 > RATIO_MIN)

    # naive "manual" rule: today's raw demand more than double the 30-day average
    flag_manual = pd.Series(y) > 2 * pd.Series(y).shift(1).rolling(30).mean()

    g["ratio3"], g["z3"] = ratio3, z3
    g["flag_robust"] = flag_robust.fillna(False)
    g["flag_manual"] = flag_manual.fillna(False)
    return g


# --------------------------------------------------------------------------
# Layer 2: district alerts -> events
# --------------------------------------------------------------------------
def district_events(panel, flag_col, fac_per_district):
    hits = panel[panel[flag_col] & panel["outbreak_sensitive"]]
    daily = (hits.groupby(["district", "date"])
             .agg(meds=("medicine_id", "nunique"), facs=("facility_id", "nunique"))
             .reset_index())
    daily["need_facs"] = daily["district"].map(lambda d: min(2, fac_per_district[d]))
    alert_days = daily[(daily.meds >= 2) & (daily.facs >= daily.need_facs)]

    events = []
    for district, g in alert_days.groupby("district"):
        dates = sorted(g["date"])
        start = prev = dates[0]
        for d in dates[1:] + [None]:
            if d is None or (d - prev).days > EVENT_GAP_DAYS:
                events.append(dict(district=district, detected_at=start, last_alert=prev))
                start = d
            prev = d if d is not None else prev
    return pd.DataFrame(events)


def main():
    OUT.mkdir(exist_ok=True)
    daily = pd.read_csv(RAW / "daily_demand.csv", parse_dates=["date"])
    meds = pd.read_csv(RAW / "medicines.csv").set_index("medicine_id")
    facs = pd.read_csv(RAW / "facilities.csv").set_index("facility_id")
    truth = pd.read_csv(RAW / "ground_truth.csv", parse_dates=["start_date"])
    last_day = daily["date"].max()

    panel = pd.concat([series_signals(g) for _, g in daily.groupby(["facility_id", "medicine_id"])],
                      ignore_index=True)
    panel["district"] = panel["facility_id"].map(facs["district"])
    panel["outbreak_sensitive"] = panel["medicine_id"].map(meds["outbreak_sensitive"]).astype(bool)
    fac_per_district = facs.groupby("district").size().to_dict()

    # ---------------- events (robust detector) ----------------
    ev = district_events(panel, "flag_robust", fac_per_district)
    rows = []
    for _, e in ev.iterrows():
        win = panel[(panel.district == e.district) & (panel.date >= e.detected_at)
                    & (panel.date <= e.last_alert + pd.Timedelta(days=3)) & panel.flag_robust]
        peak = float(win["ratio3"].max())
        sev = "HIGH" if peak >= 2.5 else "MEDIUM" if peak >= 1.8 else "LOW"
        affected = win[win.outbreak_sensitive]
        rows.append(dict(
            region=e.district,
            detected_at=e.detected_at.date(),
            last_alert=e.last_alert.date(),
            status="ACTIVE" if (last_day - e.last_alert).days <= 1 else "ENDED",
            severity=sev,
            peak_demand_ratio=round(peak, 2),
            affected_medicines=",".join(sorted(affected.medicine_id.unique())),
            affected_medicine_names="; ".join(meds.loc[sorted(affected.medicine_id.unique()), "medicine_name"]),
            affected_facilities=",".join(sorted(affected.facility_id.unique())),
        ))
    events = pd.DataFrame(rows).sort_values("detected_at").reset_index(drop=True)
    events.to_csv(OUT / "spike_events.csv", index=False)

    # ---------------- latest signals per series ----------------
    latest = panel[panel.date == last_day].copy()
    latest["spike_flag"] = latest["flag_robust"]
    latest["spike_multiplier"] = np.where(latest.spike_flag, latest.ratio3.clip(lower=1).round(2), 1.0)
    latest = latest[["facility_id", "medicine_id", "district", "ratio3", "z3", "spike_flag", "spike_multiplier"]]
    latest[["ratio3", "z3"]] = latest[["ratio3", "z3"]].round(2)
    latest.to_csv(OUT / "spike_signals_latest.csv", index=False)

    print(f"spike_events.csv: {len(events)} events")
    print(events[["region", "detected_at", "status", "severity", "peak_demand_ratio",
                  "affected_medicines", "affected_facilities"]].to_string(index=False))
    print(f"\nSeries flagged on {last_day.date()}: {int(latest.spike_flag.sum())} of {len(latest)}")

    # ---------------- evaluation vs injected outbreaks ----------------
    manual = district_events(panel, "flag_manual", fac_per_district)
    print("\nEvaluation vs injected outbreaks (days after true start; lower is better):")
    print(f"{'outbreak':<28}{'true start':<13}{'manual 2x rule':<17}{'our detector':<13}")
    for _, t in truth[truth.event_type.str.startswith("outbreak")].iterrows():
        def delay(evdf):
            m = evdf[(evdf.district == t.district)
                     & (evdf.detected_at >= t.start_date - pd.Timedelta(days=3))
                     & (evdf.detected_at <= t.start_date + pd.Timedelta(days=30))]
            return f"{(m.detected_at.min() - t.start_date).days} days" if len(m) else "missed"
        print(f"{t.event_type + ' / ' + t.district:<28}{t.start_date.date()!s:<13}{delay(manual):<17}{delay(ev):<13}")

    # false alerts = events that do not overlap any injected outbreak window (start .. +45 days)
    def false_alerts(evdf):
        n = 0
        for _, e in evdf.iterrows():
            ok = ((truth.district == e.district)
                  & (e.detected_at >= truth.start_date - pd.Timedelta(days=3))
                  & (e.detected_at <= truth.start_date + pd.Timedelta(days=45))).any()
            n += 0 if ok else 1
        return n
    print(f"\nFalse alert events over the year: manual rule = {false_alerts(manual)}, "
          f"our detector = {false_alerts(ev)}")


if __name__ == "__main__":
    main()
    