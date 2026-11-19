"""
STEP 2b - Demand forecasting (Person 3 - AI/ML)

1. BACKTEST on a period the model never saw (time-based split, no shuffling)
   and compare LightGBM against two simple baselines.
2. Retrain on ALL history and forecast the next 14 days from the last data day.

Outputs
    outputs/forecasts.csv         facility_id, medicine_id, date, p10, p50, p90
    outputs/forecast_metrics.csv  accuracy table for your slides
    models/demand_model.pkl       trained model

Run from the project root:
    python src/forecast.py
"""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from features import FEATURE_COLS, build_features, load_raw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
MODELS = ROOT / "models"
Z80 = 1.2816  # z-score for a P10-P90 interval


def make_model():
    """LightGBM if installed, otherwise scikit-learn's equivalent."""
    try:
        import lightgbm as lgb
        return lgb.LGBMRegressor(
            n_estimators=400, learning_rate=0.05, num_leaves=31,
            min_child_samples=40, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, random_state=42, verbose=-1), "LightGBM"
    except ImportError:
        from sklearn.ensemble import HistGradientBoostingRegressor
        return HistGradientBoostingRegressor(
            max_iter=300, learning_rate=0.05, random_state=42), "HistGradientBoosting (sklearn fallback)"


def wape(actual, pred):
    """Weighted absolute percentage error: sum|error| / sum(actual)."""
    return np.abs(actual - pred).sum() / actual.sum()


def smape(actual, pred):
    denom = (np.abs(actual) + np.abs(pred)) / 2
    return np.mean(np.where(denom == 0, 0, np.abs(actual - pred) / denom))


def main():
    OUT.mkdir(exist_ok=True)
    MODELS.mkdir(exist_ok=True)

    daily, facilities, _ = load_raw()
    feats = build_features(daily, facilities)
    last_day = daily["date"].max()
    labelled = feats[feats["target"].notna()].copy()

    # ------------------------------------------------------------------
    # 1. Backtest: hold out the 2 origins that sit 35 and 28 days before the end.
    #    Their 14-day targets end before the dengue outbreak starts, so this
    #    measures normal-time accuracy. (Outbreaks are the spike detector's job.)
    # ------------------------------------------------------------------
    test_origins = [last_day - pd.Timedelta(days=35), last_day - pd.Timedelta(days=28)]
    cutoff = test_origins[0]
    train = labelled[labelled["target_date"] <= cutoff]     # nothing after the first test origin
    test = labelled[labelled["origin_date"].isin(test_origins)].copy()

    model, model_name = make_model()
    model.fit(train[FEATURE_COLS], train["target_ratio"])
    test["pred"] = np.clip(model.predict(test[FEATURE_COLS]), 0, None) * test["scale"]

    results = []
    for name, col in [("Moving average (7d)", "base_ma7"),
                      ("Seasonal naive (4-wk same weekday)", "base_snaive4"),
                      (f"{model_name}", "pred")]:
        for label, mask in [("All horizons (1-14d)", test["h"] > 0),
                            ("Short (1-7d)", test["h"] <= 7),
                            ("Long (8-14d)", test["h"] > 7)]:
            a, p = test.loc[mask, "target"], test.loc[mask, col]
            results.append(dict(model=name, horizon=label,
                                WAPE_pct=round(100 * wape(a, p), 2),
                                sMAPE_pct=round(100 * smape(a, p), 2)))
    metrics = pd.DataFrame(results)
    metrics.to_csv(OUT / "forecast_metrics.csv", index=False)

    print(f"Model: {model_name}")
    print(f"Train rows: {len(train):,}   Test rows: {len(test):,}  "
          f"(origins {[d.date().isoformat() for d in test_origins]})\n")
    print(metrics[metrics.horizon == "All horizons (1-14d)"].to_string(index=False))

    # interval width per medicine from backtest residuals (relative to series scale)
    test["resid_ratio"] = (test["target"] - test["pred"]) / test["scale"]
    sigma_by_med = test.groupby("medicine_id")["resid_ratio"].std().fillna(0.2)

    # ------------------------------------------------------------------
    # 2. Final model on all history -> forecast the next 14 days
    # ------------------------------------------------------------------
    final_model, _ = make_model()
    final_model.fit(labelled[FEATURE_COLS], labelled["target_ratio"])
    with open(MODELS / "demand_model.pkl", "wb") as f:
        pickle.dump(final_model, f)

    future = feats[feats["origin_date"] == last_day].copy()
    future["p50"] = np.clip(final_model.predict(future[FEATURE_COLS]), 0, None) * future["scale"]
    sigma = future["medicine_id"].map(sigma_by_med) * future["scale"]
    future["p10"] = np.clip(future["p50"] - Z80 * sigma, 0, None)
    future["p90"] = future["p50"] + Z80 * sigma

    out = future.rename(columns={"target_date": "date"})[
        ["facility_id", "medicine_id", "date", "p10", "p50", "p90"]
    ].sort_values(["facility_id", "medicine_id", "date"])
    out[["p10", "p50", "p90"]] = out[["p10", "p50", "p90"]].round(1)
    out.to_csv(OUT / "forecasts.csv", index=False)
    print(f"\nforecasts.csv: {len(out)} rows  ({out.date.min().date()} -> {out.date.max().date()})")

    # quick look at the live dengue scenario
    chk = out[(out.facility_id == "F01") & (out.medicine_id == "M07")]
    recent = daily[(daily.facility_id == "F01") & (daily.medicine_id == "M07")].tail(7)["units_dispensed"].mean()
    print(f"F01 IV Saline: last-7-day actual avg = {recent:.0f}/day, "
          f"next-14-day forecast avg = {chk.p50.mean():.0f}/day")


if __name__ == "__main__":
    main()