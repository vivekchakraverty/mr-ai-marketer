"""Local TF-IDF text search over the merged catalog. No LLM, no network calls."""
from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import config
from .catalog import Site


class CatalogSearchIndex:
    def __init__(self, sites: list[Site]):
        self.sites = sites
        self._vectorizer = TfidfVectorizer(
            stop_words="english", ngram_range=(1, 2), max_features=50_000,
        )
        self._matrix = self._vectorizer.fit_transform(s.search_text for s in sites)

    def shortlist(self, topic: str, k: int = config.SHORTLIST_SIZE) -> list[Site]:
        """Top-k sites by cosine similarity to the topic query. No liveness/OPR filtering
        here — that happens downstream, which is why k is generous relative to the
        10-per-page UI (some shortlisted candidates will be filtered out as dead)."""
        if not topic.strip():
            return []
        qv = self._vectorizer.transform([topic])
        sims = cosine_similarity(qv, self._matrix).ravel()
        top_idx = sims.argsort()[::-1][:k]
        return [self.sites[i] for i in top_idx if sims[i] > 0.0]
