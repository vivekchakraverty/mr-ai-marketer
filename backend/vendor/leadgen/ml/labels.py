"""Label mapping for the Gaussian Process training signal.

The primary signal is the LLM qualification decision (good/bad fit), set on each lead when
it is qualified. OpenOutreach additionally folds *deal outcomes* back into the labels; we
expose that mapping too so a campaign that runs long enough learns from what actually
converted, not just from the up-front qualification.
"""

from __future__ import annotations

from .. import db

POSITIVE = "positive"
NEGATIVE = "negative"
SKIPPED = "skipped"


def from_qualification(fit: str) -> str:
    """LLM 'good'/'bad' -> GP label."""
    return POSITIVE if str(fit).strip().lower() == "good" else NEGATIVE


def from_deal_outcome(state: str, outcome: str | None) -> str | None:
    """OpenOutreach's outcome->label rule: non-FAILED -> positive; FAILED + wrong_fit ->
    negative; other FAILED (e.g. 'no email') -> skipped (ignored by the ML labeler).

    Returns None when the outcome carries no training signal yet.
    """
    if state == db.STATE_FAILED:
        if (outcome or "").strip().lower() in ("wrong_fit", "not_interested"):
            return NEGATIVE
        return SKIPPED
    if state in (db.STATE_EMAILED, db.STATE_REPLIED, db.STATE_COMPLETED):
        return POSITIVE
    return None
