"""
STEP 6 - Recommendation engine (Person 3 - AI/ML)

Combines the four model outputs into a ranked list of concrete actions, each with
a plain-language reason. The numbers all come from the models; the rules here
only decide what to do about them.

Action types
    OUTBREAK_ALERT   an active outbreak pattern in a district
    TRANSFER         move stock from a facility with surplus to one that needs it
                     (purpose = "shortage" or "expiry")
    EXPEDITE_ORDER   supplier's normal lead time is too slow - ask for fast delivery
    REORDER          normal order to restore stock
    EXPIRY_ACTION    batch will expire unsold and nobody nearby can absorb it

Key rules
    * A transfer is only suggested if it arrives before stockout AND before the
      supplier could deliver, and the donor keeps lead time + 21 days of cover.
    * Donors must be low-risk and not themselves in a demand spike.
    * During a spike, order quantities assume demand falls back toward normal
      after the lead time (instead of staying at the peak forever).

Inputs : outputs/{shortage_risk,expiry_risk,spike_events,spike_signals_latest}.csv, data/raw/*.csv
Output : outputs/recommendations.csv

Run from the project root (after all other steps):
    python src/recommend.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "outputs"

DONOR_KEEP_DAYS = 21      # donor keeps lead time + this many days of cover
ROAD_FACTOR = 1.3         # road distance vs straight line
TRUCK_KM_PER_DAY = 400
MIN_TRANSFER = 10
BUFFER_DAYS = 14


def km_between(a, b):
    """Haversine distance in km between two facility rows."""
    lat1, lon1, lat2, lon2 = map(np.radians, [a.lat, a.lon, b.lat, b.lon])
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * np.arcsin(np.sqrt(h))


def level(score):
    return "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM" if score >= 35 else "LOW"


def main():
    sr = pd.read_csv(OUT / "shortage_risk.csv")
    ex = pd.read_csv(OUT / "expiry_risk.csv")
    ev = pd.read_csv(OUT / "spike_events.csv")
    sig = pd.read_csv(OUT / "spike_signals_latest.csv")
    fac = pd.read_csv(RAW / "facilities.csv").set_index("facility_id")
    med = pd.read_csv(RAW / "medicines.csv").set_index("medicine_id")
    sup = pd.read_csv(RAW / "suppliers.csv").set_index("supplier_id")

    sr_idx = sr.set_index(["facility_id", "medicine_id"])
    spike_mult = sig.set_index(["facility_id", "medicine_id"]).spike_multiplier.to_dict()
    spike_flag = sig.set_index(["facility_id", "medicine_id"]).spike_flag.to_dict()

    # distance / transit between facilities
    km = {(a, b): km_between(fac.loc[a], fac.loc[b]) * ROAD_FACTOR
          for a in fac.index for b in fac.index if a != b}
    transit = {k: 0.5 + v / TRUCK_KM_PER_DAY for k, v in km.items()}

    recs = []

    def add(**kw):
        recs.append(kw)

    def cold_note(mid):
        return " Cold-chain transport required." if med.loc[mid, "cold_chain"] else ""

    # ------------------------------------------------------------------
    # 1. Outbreak alerts
    # ------------------------------------------------------------------
    for _, e in ev[ev.status == "ACTIVE"].iterrows():
        n_fac = len(str(e.affected_facilities).split(","))
        add(type="OUTBREAK_ALERT", purpose="outbreak", priority_score=100.0,
            facility_id="", medicine_id=e.affected_medicines,
            reason_text=(f"Outbreak pattern in {e.region} since {e.detected_at}: demand for "
                         f"{e.affected_medicine_names} is rising together at {n_fac} facilities "
                         f"(peak {e.peak_demand_ratio}x normal, severity {e.severity}). "
                         f"Prioritise these medicines for allocation and pre-position stock in the district."))

    # ------------------------------------------------------------------
    # 2. Shortages -> transfers + orders
    # ------------------------------------------------------------------
    sr = sr.copy()
    sr["mult"] = [spike_mult.get((r.facility_id, r.medicine_id), 1.0) for r in sr.itertuples()]
    sr["prio"] = (100 * (0.5 * sr.risk_score + 0.2 * sr.criticality / 3
                         + 0.2 * (sr.days_of_cover < sr.lead_time_days)
                         + 0.1 * np.minimum(sr.mult, 3) / 3)).round(1)

    # what each donor can safely give away
    donor_excess = {}
    for r in sr.itertuples():
        ok = r.risk_level == "LOW" and not spike_flag.get((r.facility_id, r.medicine_id), False)
        keep = r.forecast_daily_demand * (r.lead_time_days + DONOR_KEEP_DAYS)
        donor_excess[(r.facility_id, r.medicine_id)] = max(0, r.current_stock - keep) if ok else 0

    batch_avail = ex.set_index("batch_id").quantity_on_hand.to_dict()
    batches_by_key = {k: g.sort_values("days_to_expiry") for k, g in ex.groupby(["facility_id", "medicine_id"])}
    moved_out = {}                 # batch_id -> units already recommended to move
    received = {}                  # (facility, medicine) -> units already coming in

    for t in sr[sr.risk_score >= 0.15].sort_values("prio", ascending=False).itertuples():
        mid, fid = t.medicine_id, t.facility_id
        m = med.loc[mid]
        daily, stock, lead = t.forecast_daily_demand, t.current_stock, t.lead_time_days
        need = max(0, int(np.ceil(daily * lead * 1.1 - stock)))
        moved = 0
        mult_txt = f" Demand is {t.mult:.1f}x normal." if t.mult > 1.15 else ""

        if t.risk_level in ("CRITICAL", "HIGH") and need > 0:
            donors = sorted((d for d in fac.index if d != fid), key=lambda d: transit[(d, fid)])
            for d in donors:
                if need - moved <= 0:
                    break
                tr = transit[(d, fid)]
                if tr >= lead or tr > t.days_of_cover:      # would not arrive in time
                    continue
                for b in batches_by_key.get((d, mid), pd.DataFrame()).itertuples():
                    exc = donor_excess[(d, mid)]
                    avail = batch_avail[b.batch_id] - moved_out.get(b.batch_id, 0)
                    q = int(min(need - moved, exc, avail))
                    if q < MIN_TRANSFER or b.days_to_expiry < tr + 10:
                        continue
                    d_row = sr_idx.loc[(d, mid)]
                    surplus_txt = (f" It is surplus there ({b.expected_waste_units:,} units expected to expire unused)."
                                   if b.expected_waste_units > 0 else f" Donor keeps {DONOR_KEEP_DAYS}+ days of cover.")
                    add(type="TRANSFER", purpose="shortage", priority_score=t.prio,
                        facility_id=fid, medicine_id=mid, from_facility_id=d, to_facility_id=fid,
                        batch_id=b.batch_id, quantity=q,
                        distance_km=round(km[(d, fid)]), transit_days=round(tr, 1),
                        est_value_inr=round(q * m.unit_cost_inr),
                        reason_text=(f"Move {q:,} units of {m.medicine_name} (batch {b.batch_id}, expires in "
                                     f"{b.days_to_expiry} days) from {fac.loc[d, 'facility_name']} to "
                                     f"{fac.loc[fid, 'facility_name']}, which has {t.days_of_cover:.1f} days of cover "
                                     f"and a {lead}-day supplier lead time (stockout risk {t.risk_score:.0%}).{mult_txt}"
                                     f"{surplus_txt} About {km[(d, fid)]:.0f} km, ~{tr:.1f} days in transit, so it "
                                     f"arrives before stockout.{cold_note(mid)}"))
                    moved += q
                    donor_excess[(d, mid)] -= q
                    moved_out[b.batch_id] = moved_out.get(b.batch_id, 0) + q
                    received[(fid, mid)] = received.get((fid, mid), 0) + q
                    if need - moved <= 0:
                        break

        # order quantity (reduced if the shortage is a temporary spike)
        if t.mult > 1.05:
            normal = daily / t.mult
            safety = 1.28 * 0.25 * daily * np.sqrt(lead)
            total = daily * lead + safety + BUFFER_DAYS * (daily + normal) / 2 - stock
            order_total = max(0, int(np.ceil(total)))
            order_note = " Quantity assumes the spike fades back toward normal after delivery."
        else:
            order_total, order_note = int(t.reorder_qty), ""
        order_qty = max(0, order_total - moved)

        if order_qty > 0:
            slow = (stock + moved) / max(daily, 0.1) < lead
            supplier = sup.loc[m.supplier_id, "supplier_name"]
            if slow:
                txt = (f"Request EXPEDITED delivery of {order_qty:,} units of {m.medicine_name} to "
                       f"{fac.loc[fid, 'facility_name']} from {supplier}: stock lasts "
                       f"{(stock + moved) / max(daily, 0.1):.1f} days but standard delivery takes {lead} days "
                       f"(stockout risk {t.risk_score:.0%}).{mult_txt}{order_note}")
            else:
                txt = (f"Order {order_qty:,} units of {m.medicine_name} for {fac.loc[fid, 'facility_name']} from "
                       f"{supplier}: {t.days_of_cover:.1f} days of cover vs a {lead}-day lead time "
                       f"(stockout risk {t.risk_score:.0%}).{mult_txt}{order_note}")
            add(type="EXPEDITE_ORDER" if slow else "REORDER", purpose="shortage",
                priority_score=t.prio - (3 if not slow else 0), facility_id=fid, medicine_id=mid,
                quantity=order_qty, supplier_id=m.supplier_id,
                est_value_inr=round(order_qty * m.unit_cost_inr), reason_text=txt)

    # ------------------------------------------------------------------
    # 3. Expiring batches -> move them somewhere they will sell, else flag
    # ------------------------------------------------------------------
    for b in ex[ex.risk_level == "HIGH"].sort_values("waste_value_inr", ascending=False).itertuples():
        m = med.loc[b.medicine_id]
        waste_left = b.expected_waste_units - moved_out.get(b.batch_id, 0)
        if waste_left < 20 and waste_left * m.unit_cost_inr < 1000:
            continue
        prio = round(min(60, 20 + b.waste_value_inr / 4000), 1)
        placed = 0
        receivers = sorted((r for r in fac.index if r != b.facility_id),
                           key=lambda r: transit[(b.facility_id, r)])
        for r in receivers:
            tr = transit[(b.facility_id, r)]
            if tr > b.days_to_expiry - 5 or (r, b.medicine_id) not in sr_idx.index:
                continue
            rr = sr_idx.loc[(r, b.medicine_id)]
            absorb = rr.forecast_daily_demand * (b.days_to_expiry - tr - 2) \
                - rr.current_stock - received.get((r, b.medicine_id), 0)
            q = int(min(waste_left - placed, absorb))
            if q < 20:
                continue
            add(type="TRANSFER", purpose="expiry", priority_score=prio,
                facility_id=r, medicine_id=b.medicine_id, from_facility_id=b.facility_id, to_facility_id=r,
                batch_id=b.batch_id, quantity=q, distance_km=round(km[(b.facility_id, r)]),
                transit_days=round(tr, 1), est_value_inr=round(q * m.unit_cost_inr),
                reason_text=(f"Move {q:,} units of {m.medicine_name} (batch {b.batch_id}) from "
                             f"{b.facility_name} to {fac.loc[r, 'facility_name']}: {b.facility_name} will not sell "
                             f"them before expiry in {b.days_to_expiry} days ({b.expected_waste_units:,} units "
                             f"expected waste, INR {b.waste_value_inr:,.0f}), while {fac.loc[r, 'facility_name']} "
                             f"can use them. About {km[(b.facility_id, r)]:.0f} km, ~{tr:.1f} days in "
                             f"transit.{cold_note(b.medicine_id)}"))
            received[(r, b.medicine_id)] = received.get((r, b.medicine_id), 0) + q
            placed += q
            if placed >= waste_left:
                break

        left = waste_left - placed
        if left >= 20 and left >= 0.2 * b.expected_waste_units:
            add(type="EXPIRY_ACTION", purpose="expiry", priority_score=prio,
                facility_id=b.facility_id, medicine_id=b.medicine_id, batch_id=b.batch_id, quantity=left,
                est_value_inr=round(left * m.unit_cost_inr),
                reason_text=(f"{left:,} units of {m.medicine_name} (batch {b.batch_id}) at {b.facility_name} "
                             f"expire in {b.days_to_expiry} days and are not expected to sell; no nearby facility "
                             f"can absorb them. Dispense this batch first, and arrange supplier return or "
                             f"donation before expiry (INR {left * m.unit_cost_inr:,.0f} at risk)."))

    # ------------------------------------------------------------------
    # Assemble output
    # ------------------------------------------------------------------
    cols = ["rec_id", "type", "purpose", "priority_score", "priority_level", "facility_id", "facility_name",
            "medicine_id", "medicine_name", "from_facility_id", "to_facility_id", "batch_id", "quantity",
            "supplier_id", "est_value_inr", "distance_km", "transit_days", "reason_text"]
    out = pd.DataFrame(recs)
    out["priority_level"] = out["priority_score"].map(level)
    out = out.sort_values("priority_score", ascending=False).reset_index(drop=True)
    out["rec_id"] = [f"R{i + 1:03d}" for i in range(len(out))]
    out["facility_name"] = out["facility_id"].map(fac["facility_name"]).fillna("")
    out["medicine_name"] = out["medicine_id"].map(med["medicine_name"]).fillna("")
    for c in cols:
        if c not in out.columns:
            out[c] = ""
    out = out[cols]
    out.to_csv(OUT / "recommendations.csv", index=False)

    print(f"recommendations.csv: {len(out)} actions")
    print(out.groupby(["type", "purpose"]).size().to_string())
    print(f"\nTotal stock value moved by transfers: INR {out.loc[out.type == 'TRANSFER', 'est_value_inr'].replace('', 0).astype(float).sum():,.0f}")
    print("\nTop 6 recommendations:")
    for r in out.head(6).itertuples():
        print(f"\n[{r.rec_id}] {r.priority_level} ({r.priority_score}) {r.type}\n  {r.reason_text}")


if __name__ == "__main__":
    main()