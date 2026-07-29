"""Per-campaign Gaussian Process qualifier with Bayesian active learning.

Faithful to OpenOutreach's design, on scikit-learn (already a dependency):

  * model      Pipeline(StandardScaler, GaussianProcessRegressor(ConstantKernel*RBF)),
               trained on bge-small embeddings; positives=1.0, negatives=0.0.
  * cold start the GP stays unfitted until >=2 labels of BOTH classes exist; until then
               the next lead to label is chosen seed-first, with no ranking.
  * acquisition balance-driven: if negatives outnumber positives, EXPLOIT (highest
               predicted mean); otherwise EXPLORE (highest posterior std — the GP-active-
               learning uncertainty signal, our stand-in for BALD, which does not have a
               closed form for a regressor).
  * consume    once the GP is fitted, unlabeled leads whose predicted mean clears
               MIN_GP_CONFIDENCE advance to email-finding WITHOUT spending an LLM call —
               the efficiency win of the whole scheme.
  * persist    the fitted pipeline is joblib-compressed into campaigns.model_blob and
               warm-started on demand.
"""

from __future__ import annotations

import io
import logging

import joblib
import numpy as np

from .. import db
from . import embedder

log = logging.getLogger(__name__)

MIN_GP_CONFIDENCE = 0.9


class Qualifier:
    """Thin wrapper over the sklearn pipeline, exposing mean + std for acquisition."""

    def __init__(self) -> None:
        self._pipe = None

    @property
    def is_fitted(self) -> bool:
        return self._pipe is not None

    def fit(self, X: np.ndarray, y: np.ndarray) -> "Qualifier":
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import RBF, ConstantKernel
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        kernel = ConstantKernel(1.0) * RBF(length_scale=1.0)
        self._pipe = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "gp",
                    GaussianProcessRegressor(
                        kernel=kernel, alpha=1e-3, normalize_y=True, random_state=0
                    ),
                ),
            ]
        )
        self._pipe.fit(X, y)
        return self

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mean, std). std is 0 when unfitted."""
        if not self.is_fitted or len(X) == 0:
            return np.zeros(len(X)), np.zeros(len(X))
        Xs = self._pipe.named_steps["scaler"].transform(X)
        mean, std = self._pipe.named_steps["gp"].predict(Xs, return_std=True)
        return np.asarray(mean), np.asarray(std)

    def acquisition_scores(self, X: np.ndarray, mode: str) -> np.ndarray:
        mean, std = self.predict(X)
        return std if mode == "explore" else mean

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        joblib.dump(self._pipe, buf, compress=3)
        return buf.getvalue()

    @classmethod
    def from_bytes(cls, blob: bytes) -> "Qualifier":
        q = cls()
        q._pipe = joblib.load(io.BytesIO(blob))
        return q


# ---------------------------------------------------------------------------
# Orchestration over the DB
# ---------------------------------------------------------------------------


def _matrix(leads: list[dict]) -> np.ndarray:
    if not leads:
        return np.zeros((0, embedder.DIM), dtype=np.float32)
    return np.vstack([embedder.from_blob(l["embedding"]) for l in leads])


def load(campaign: dict) -> Qualifier | None:
    blob = campaign.get("model_blob")
    if not blob:
        return None
    try:
        return Qualifier.from_bytes(blob)
    except Exception:  # noqa: BLE001 — a stale/corrupt blob just means "retrain from scratch"
        return None


def train_and_persist(campaign_id: str) -> Qualifier | None:
    """Fit from all labeled leads and store the blob. Returns None (leaves the model
    unfitted) until >=2 labels of both classes exist — the cold-start guard."""
    labeled = db.labeled_leads(campaign_id)
    pos = [l for l in labeled if l["label"] == "positive"]
    neg = [l for l in labeled if l["label"] == "negative"]
    if len(pos) < 2 or len(neg) < 2:
        return None

    X = _matrix(labeled)
    y = np.array([1.0 if l["label"] == "positive" else 0.0 for l in labeled])
    qual = Qualifier().fit(X, y)
    db.save_model_blob(campaign_id, qual.to_bytes())
    log.info("[leadgen] campaign %s GP retrained on %d labels", campaign_id, len(labeled))
    return qual


def select_next_to_qualify(campaign_id: str) -> dict | None:
    """Choose the next unlabeled lead for the LLM to judge (the active-learning step)."""
    unlabeled = db.unlabeled_leads(campaign_id)
    if not unlabeled:
        return None

    campaign = db.get_campaign(campaign_id)
    qual = load(campaign) if campaign else None
    if qual is None or not qual.is_fitted:
        # Cold start: label seed-first, no ranking.
        return sorted(unlabeled, key=lambda l: l["created_at"])[0]

    labeled = db.labeled_leads(campaign_id)
    n_pos = sum(1 for l in labeled if l["label"] == "positive")
    n_neg = sum(1 for l in labeled if l["label"] == "negative")
    mode = "exploit" if n_neg > n_pos else "explore"

    X = _matrix(unlabeled)
    scores = qual.acquisition_scores(X, mode)
    return unlabeled[int(np.argmax(scores))]


def confident_unlabeled(campaign_id: str, threshold: float = MIN_GP_CONFIDENCE) -> list[tuple[dict, float]]:
    """Unlabeled leads the fitted GP is confident are positive — they advance to email-
    finding without spending an LLM qualification call (consume mode)."""
    campaign = db.get_campaign(campaign_id)
    qual = load(campaign) if campaign else None
    if qual is None or not qual.is_fitted:
        return []
    unlabeled = db.unlabeled_leads(campaign_id)
    if not unlabeled:
        return []
    mean, _ = qual.predict(_matrix(unlabeled))
    return [(lead, float(m)) for lead, m in zip(unlabeled, mean) if m >= threshold]
