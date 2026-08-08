"""
Predicts a marketing email's click-through rate (CTR) from its generated
text, using a small RandomForest trained on the Kaggle "Email CTR
Prediction" dataset (see ml/ctr/train_ctr_model.py for training + feature
selection rationale).

This is a statistical estimate from ~1,900 historical campaigns, not a
guarantee -- real CTR depends on audience quality, sender reputation,
deliverability, and plenty else this model never sees. Every caller-facing
value from this module should be presented as a prediction, not a fact.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import numpy as np
import pandas as pd
from joblib import load

# The model and its stats are fetched from Hugging Face on first use rather than shipped in
# the build — see services/hf_assets.py. These paths remain as the dev-checkout fallback and
# as the identity of the files; hf_assets prefers them when they exist.
_ML_DIR = Path(__file__).resolve().parent.parent / "ml"
_MODEL_PATH = _ML_DIR / "ctr_model.joblib"
_STATS_PATH = _ML_DIR / "ctr_reference_stats.json"

# Common marketing calls-to-action. Deliberately broad rather than exact --
# no_of_CTA/mean_CTA_len only need to be roughly right, and (per
# train_ctr_model.py) no_of_CTA is the feature the final prediction is most
# sensitive to, so a missed real CTA here does more damage than a slightly
# loose match.
_CTA_PATTERNS = [
    r"shop now", r"buy now", r"get started", r"learn more", r"click here",
    r"sign up", r"subscribe", r"redeem", r"order now", r"book now",
    r"download", r"try (?:it )?free", r"start your \w+", r"get \d+% off",
    r"shop the sale", r"view (?:details|collection)", r"add to cart",
    r"register", r"reserve", r"explore", r"discover", r"continue reading",
    r"activate", r"upgrade", r"unlock", r"get yours", r"shop\b",
    r"grab (?:it|yours|the deal)", r"claim (?:your|this)", r"save now",
    r"get \d+% (?:off|savings)", r"start (?:shopping|now)", r"see (?:more|details)",
    r"check it out", r"take a look", r"find out more", r"read more",
    r"join (?:us|now|today)", r"apply now", r"schedule", r"contact us",
    r"call now", r"visit (?:us|our)", r"browse", r"tap (?:here|to)",
]
_CTA_RE = re.compile("|".join(_CTA_PATTERNS), re.IGNORECASE)

# A generated MARKETING email (this tool's only output, per its training) is
# vanishingly unlikely to genuinely have zero calls to action -- the training
# data's real zero-CTA campaigns cluster in a small, surprisingly-high-CTR
# pocket (see train_ctr_model.py's docstring), almost certainly reflecting a
# different email TYPE (transactional) in the original dataset, not "good
# marketing copy that happens to lack a CTA." A detected 0 here is far more
# likely a regex miss than genuine CTA-less content, so treat it as 1 rather
# than feed the model a signal it will over-interpret.
_MIN_CTA_COUNT = 1

# Bracket/brace placeholders are the standard convention for mail-merge
# personalization in marketing copy (also how this project's own training
# data represents it, e.g. "Hi [First Name]").
_PERSONALIZATION_RE = re.compile(r"\[(?:first\s*name|last\s*name|name|your\s*name)\]|\{\{\s*\w+\s*\}\}", re.IGNORECASE)

# A wrapped quotation of some length, or an em-dash attribution line --
# rough heuristic for a testimonial/quote block.
_QUOTE_RE = re.compile(r'"[^"\n]{15,}"|^\s*[—–]\s*\S+', re.IGNORECASE | re.MULTILINE)


@dataclass
class CtrPrediction:
    predictedClickRate: float  # 0-1
    bucket: str  # "below average" | "typical" | "above average" | "strong"

    LABELS: ClassVar[dict[str, str]] = {
        "below": "below average",
        "typical": "typical",
        "above": "above average",
        "strong": "strong",
    }


def _split_subject_body(email_text: str) -> tuple[str, str]:
    lines = email_text.strip().split("\n")
    first = lines[0].strip()
    match = re.match(r"^subject:\s*(.*)$", first, re.IGNORECASE)
    if match:
        subject = match.group(1).strip()
        body = "\n".join(lines[1:]).strip()
    else:
        subject = first
        body = "\n".join(lines[1:]).strip()
    return subject, body


def extract_features(email_text: str, cta_len_fallback: float) -> dict[str, float]:
    """Compute the 6 model features from raw generated email text. Every
    value here is directly measured from the text except is_image, which is
    always 0 -- this tool only ever generates plain text, never an actual
    embedded image, regardless of what the copy describes. (body_len and
    mean_paragraph_len are deliberately NOT features here -- see
    ml/ctr/train_ctr_model.py for why: the training data's body_len is
    HTML-source-scale, not plain-text-scale, so it doesn't transfer.)"""
    subject, body = _split_subject_body(email_text)
    # findall returns the FIRST group for patterns with groups (e.g. \d+ in
    # "get \d+% off") and the whole match otherwise -- use finditer for
    # accurate span lengths regardless of grouping.
    cta_spans = [m.group(0) for m in _CTA_RE.finditer(body)]

    return {
        "subject_len": float(len(subject)),
        "no_of_CTA": float(max(len(cta_spans), _MIN_CTA_COUNT)),
        "mean_CTA_len": (
            sum(len(c) for c in cta_spans) / len(cta_spans) if cta_spans else cta_len_fallback
        ),
        "is_image": 0.0,
        "is_personalised": 1.0 if _PERSONALIZATION_RE.search(email_text) else 0.0,
        "is_quote": 1.0 if _QUOTE_RE.search(body) else 0.0,
    }


class _Predictor:
    def __init__(self, hf_token: str | None = None) -> None:
        from . import hf_assets

        bundle = load(hf_assets.path_for("ctr-model", token=hf_token))
        self._model = bundle["model"]
        self._feature_order = bundle["features"]
        stats_path = hf_assets.path_for("ctr-stats", token=hf_token)
        self._stats = json.loads(Path(stats_path).read_text(encoding="utf-8"))

    def predict(self, email_text: str) -> CtrPrediction:
        features = extract_features(email_text, self._stats["mean_cta_len_fallback"])
        x = pd.DataFrame([[features[f] for f in self._feature_order]], columns=self._feature_order)
        log_pred = self._model.predict(x)[0]
        # Clip to q95, not just [0, 1] -- a leaf covering only a handful of
        # unusual training rows (see train_ctr_model.py) can return a value
        # that's technically inside the observed range but not a credible
        # single-email estimate; q95 keeps output within what's commonly seen.
        rate = float(np.clip(np.expm1(log_pred), 0.0, self._stats["q95"]))

        if rate < self._stats["q25"]:
            bucket = "below average"
        elif rate < self._stats["q50"]:
            bucket = "typical"
        elif rate < self._stats["q75"]:
            bucket = "above average"
        else:
            bucket = "strong"

        return CtrPrediction(predictedClickRate=rate, bucket=bucket)


_predictor: _Predictor | None = None


def predict_ctr(email_text: str, hf_token: str | None = None) -> CtrPrediction:
    """Score one draft.

    `hf_token` is only needed the first time on a machine that has to fetch the model, and
    only if the model repo is private or gated. Once loaded, the predictor is reused.
    """
    global _predictor
    if _predictor is None:
        _predictor = _Predictor(hf_token=hf_token)
    return _predictor.predict(email_text)
