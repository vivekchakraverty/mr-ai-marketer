#!/usr/bin/env python
"""
Trains the marketing-email CTR (click-through rate) predictor bundled with
the Email Writer tool.

Data: Kaggle "Email CTR Prediction" (sk4467) -- one row per historical email
campaign, target `click_rate` = clicks / delivered (continuous, heavily
right-skewed: median ~1.1%, 75th pct ~3.6%, max ~90%).

Feature selection was decided empirically, not assumed. Two rounds of
elimination:

1. Dropped the opaque `sender`/`category`/`product`/`target_audience` codes
   (no data dictionary exists for what the codes mean, so we can't ask the
   user to pick one meaningfully) and `day_of_week`/`is_weekend`/
   `times_of_day` (would need new UI fields, and contribute <10% combined
   feature importance) -- 5-fold CV R^2 only dropped from 0.51 (full
   21-feature set) to 0.48, not worth the added UI/complexity.

2. Also dropped `body_len` and `mean_paragraph_len` despite being the two
   most important remaining features (28% + 12% importance) -- their scale
   (training median body_len ~12,700 characters, 25th pct ~9,554) is far
   too large to be plain-text content length; it's almost certainly raw
   HTML source length (markup + CSS included). This tool only ever
   generates plain text, never HTML, so every real prediction would land
   far outside the model's actual training range on its single most
   important feature -- a lower-R^2 model (0.32) whose remaining features
   (subject_len, no_of_CTA, mean_CTA_len -- all plausibly comparable in
   scale whether the source is HTML or plain text) are actually
   representative of our real inputs beats a higher-R^2 one built on a
   feature that's systematically out-of-distribution for everything this
   app will ever feed it.

is_urgency/is_discount/is_price/is_emoticons/is_timer were also tested and
found to be ~zero-importance noise even in the full model, so they're
dropped too rather than given fragile text heuristics for no predictive
benefit. See app/services/ctr_predictor.py for how the surviving 6 features
are computed from raw generated email text at inference time.

Target is modeled in log1p space (the raw target is heavily right-skewed);
app/services/ctr_predictor.py applies expm1 to invert this.

Run from backend/: python ml/ctr/train_ctr_model.py
Output: app/ml/ctr_model.joblib (bundled with the app; see backend.spec).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import dump
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import KFold, cross_val_predict, cross_val_score

FEATURES = [
    "subject_len",
    "no_of_CTA",
    "mean_CTA_len",
    "is_image",
    "is_personalised",
    "is_quote",
]

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE / "data" / "train_data.csv"

# The training corpus is not distributed with the app and is not in the repo. The app ships
# the *fitted* model; this data is only needed to refit it, so it lives in a private Hugging
# Face dataset that this script pulls and nothing else reads. Set CTR_TRAINING_REPO to it.
TRAINING_REPO = os.environ.get("CTR_TRAINING_REPO", "").strip()


def _training_csv() -> Path:
    if DATA_PATH.exists():
        return DATA_PATH
    if not TRAINING_REPO:
        raise SystemExit(
            "No training data. It is deliberately not in this repo — set CTR_TRAINING_REPO to "
            "the private Hugging Face dataset holding train_data.csv "
            "(see scripts/hf/publish_assets.py)."
        )
    from huggingface_hub import hf_hub_download

    print(f"Fetching training data from {TRAINING_REPO} ...")
    return Path(hf_hub_download(repo_id=TRAINING_REPO, repo_type="dataset",
                                filename="train_data.csv"))
OUTPUT_PATH = HERE.parent.parent / "app" / "ml" / "ctr_model.joblib"


def main() -> None:
    df = pd.read_csv(_training_csv())
    X = df[FEATURES]
    y = np.log1p(df["click_rate"])

    kf = KFold(n_splits=5, shuffle=True, random_state=42)
    model = RandomForestRegressor(n_estimators=200, max_depth=12, random_state=42, n_jobs=-1)
    scores = cross_val_score(model, X, y, cv=kf, scoring="r2")
    print(f"5-fold CV R^2: {scores.mean():.4f} (+/- {scores.std():.4f})")

    model.fit(X, y)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    dump({"model": model, "features": FEATURES}, OUTPUT_PATH)
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(f"Saved: {OUTPUT_PATH} ({size_kb:.0f} KB)")

    # Bucket thresholds (below average/typical/above average/strong) are
    # calibrated against what THIS reduced 6-feature model actually tends to
    # predict (via out-of-fold CV), NOT the raw dataset's click_rate
    # quartiles. Those differ meaningfully: the dropped features (body_len
    # especially) explained a lot of the raw target's spread, so this
    # model's own output distribution is narrower and shifted from the raw
    # target's. Bucketing against the raw quartiles made nearly every
    # realistic short marketing email look "strong" (unhelpful -- if
    # everything is strong, the label carries no information); bucketing
    # against the model's own CV predictions restores real discrimination.
    cv_preds = np.expm1(cross_val_predict(model, X, y, cv=kf))
    cv_preds = pd.Series(cv_preds).clip(lower=0)

    quantiles = {
        "q25": float(cv_preds.quantile(0.25)),
        "q50": float(cv_preds.quantile(0.50)),
        "q75": float(cv_preds.quantile(0.75)),
        "q95": float(df["click_rate"].quantile(0.95)),  # clip ceiling: real observed data, not model output
        "mean_cta_len_fallback": float(df["mean_CTA_len"].median()),
    }
    quantiles_path = OUTPUT_PATH.parent / "ctr_reference_stats.json"
    quantiles_path.write_text(json.dumps(quantiles, indent=2), encoding="utf-8")
    print(f"Saved: {quantiles_path} -> {quantiles}")


if __name__ == "__main__":
    main()
