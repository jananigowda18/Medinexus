"""
STEP 2a - Feature table (Person 3 - AI/ML)

Builds one row per (facility, medicine, forecast origin, horizon h):
  "standing at day t, predict demand on day t+h"  for h = 1..14.

Every feature uses ONLY data up to and including the origin day, so there is
no leakage. Demand features are divided by `scale` (the 28-day average) so one
model can learn from big hospitals and tiny PHCs alike.

Run from the project root:
    python src/features.py        # writes data/processed/features.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"

MAX_H = 14       # forecast horizon in days
MIN_HIST = 28    # days of history needed before the first origin

FEATURE_COLS = [
    "h", "target_dow", "target_month", "target_doy_sin", "target_doy_cos",
    "log_scale", "last_ratio", "r7", "r14", "std14_ratio",
    "same1_ratio", "same4_ratio", "trend_7_28",
    "size_scale", "medicine_code", "facility_code",
]


def load_raw():
    daily = pd.read_csv(RAW / "daily_demand.csv", parse_dates=["date"])
    facilities = pd.read_csv(RAW / "facilities.csv")
    medicines = pd.read_csv(RAW / "medicines.csv")
    return daily, facilities, medicines


def build_features(daily, facilities, max_h=MAX_H):
    size = facilities.set_index("facility_id").size_scale
    frames = []

    for (fid, mid), g in daily.sort_values("date").groupby(["facility_id", "medicine_id"]):
        y = g.units_dispensed.to_numpy(float)
        dates = g.date.to_numpy()
        n = len(y)
        s = pd.Series(y)
        roll7 = s.rolling(7).mean().to_numpy()
        roll14 = s.rolling(14).mean().to_numpy()
        roll28 = s.rolling(28).mean().to_numpy()
        std14 = s.rolling(14).std().to_numpy()

        origins = np.arange(MIN_HIST - 1, n)
        scale = roll28[origins] + 1.0           # +1 avoids divide-by-zero on tiny series

        for h in range(1, max_h + 1):
            k = int(np.ceil(h / 7))
            j = origins + h - 7 * k            # latest day with same weekday as target that is already known
            same1 = y[j]
            same4 = (y[j] + y[j - 7] + y[j - 14] + y[j - 21]) / 4

            tgt_idx = origins + h
            target = np.where(tgt_idx < n, y[np.minimum(tgt_idx, n - 1)], np.nan)

            frames.append(pd.DataFrame({
                "facility_id": fid, "medicine_id": mid,
                "origin_date": dates[origins], "h": h,
                "target": target, "scale": scale,
                "last_ratio": y[origins] / scale,
                "r7": roll7[origins] / scale,
                "r14": roll14[origins] / scale,
                "std14_ratio": std14[origins] / scale,
                "same1_ratio": same1 / scale,
                "same4_ratio": same4 / scale,
                "trend_7_28": (roll7[origins] + 1) / (roll28[origins] + 1),
                "size_scale": size[fid],
                "medicine_code": int(mid[1:]), "facility_code": int(fid[1:]),
            }))

    df = pd.concat(frames, ignore_index=True)
    df["origin_date"] = pd.to_datetime(df["origin_date"])
    df["target_date"] = df["origin_date"] + pd.to_timedelta(df["h"], unit="D")
    df["target_dow"] = df["target_date"].dt.dayofweek
    df["target_month"] = df["target_date"].dt.month
    doy = df["target_date"].dt.dayofyear
    df["target_doy_sin"] = np.sin(2 * np.pi * doy / 365)
    df["target_doy_cos"] = np.cos(2 * np.pi * doy / 365)
    df["log_scale"] = np.log(df["scale"])
    df["target_ratio"] = df["target"] / df["scale"]

    # simple baselines (kept in the table so forecast.py can compare fairly)
    df["base_ma7"] = df["r7"] * df["scale"]            # flat 7-day moving average
    df["base_snaive4"] = df["same4_ratio"] * df["scale"]  # same weekday, last 4 weeks
    return df


if __name__ == "__main__":
    daily, facilities, _ = load_raw()
    feats = build_features(daily, facilities)
    PROC.mkdir(parents=True, exist_ok=True)
    feats.to_csv(PROC / "features.csv", index=False, float_format="%.4f")
    labelled = feats["target"].notna().sum()
    print(f"features.csv: {len(feats):,} rows ({labelled:,} with known target, "
          f"{len(feats) - labelled:,} future rows)")
    print(f"feature columns used by the model: {len(FEATURE_COLS)}")